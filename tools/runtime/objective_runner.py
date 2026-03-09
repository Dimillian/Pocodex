from __future__ import annotations

from heapq import heappop, heappush
from typing import Any

from .action_executor import ActionExecutor
from .interaction_policy import (
    choose_field_action,
    choose_interaction_action,
    choose_planner_action,
    update_decision_state,
    update_move_strategy,
)
from .objective_inference import (
    find_objective_by_id,
    objective_distance,
    reconcile_objective_interaction_resolution,
    record_objective_selection,
    update_objective_memory,
)
from .runtime_core import RuntimeCore
from .runtime_memory import RuntimeMemory
from .snapshot_service import SnapshotService
from .trace_recorder import TraceRecorder

RuntimeStateBundle = tuple[bytes, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]


class ObjectiveRunner:
    def __init__(
        self,
        *,
        core: RuntimeCore,
        memory: RuntimeMemory,
        snapshot_service: SnapshotService,
        trace_recorder: TraceRecorder,
        action_executor: ActionExecutor,
    ) -> None:
        self.core = core
        self.memory = memory
        self.snapshot_service = snapshot_service
        self.trace_recorder = trace_recorder
        self.action_executor = action_executor

    def follow_objective(self, max_steps: int = 6, *, objective_id: str | None = None) -> dict[str, Any]:
        initial_snapshot = self.snapshot_service.telemetry()
        objective = self._select_objective(initial_snapshot, objective_id)
        if objective is None:
            snapshot = initial_snapshot
            macro_trace = {
                "timestamp": self.trace_recorder.timestamp(),
                "kind": "follow_objective",
                "max_steps": max_steps,
                "objective_id": objective_id,
                "before": self.trace_recorder.trace_state(initial_snapshot),
                "after": self.trace_recorder.trace_state(snapshot),
                "steps": [],
                "error": "No objective candidate is available in the current field state.",
            }
            self.trace_recorder.record_trace(macro_trace)
            snapshot["macro_trace"] = macro_trace
            return snapshot

        snapshot = self.snapshot_service.telemetry()
        steps = self._execute_field_window(
            snapshot,
            strategy="objective",
            max_steps=max_steps,
            objective_id=objective["id"],
            affordance_id=None,
        )
        snapshot = self.snapshot_service.telemetry()
        progress_entry = update_objective_memory(
            self.memory.decision_state,
            before=initial_snapshot,
            after=snapshot,
            objective=objective,
            steps=steps,
        )
        snapshot = self.snapshot_service.telemetry()
        macro_trace = {
            "timestamp": self.trace_recorder.timestamp(),
            "kind": "follow_objective",
            "max_steps": max_steps,
            "objective_id": objective["id"],
            "objective": objective,
            "progress": progress_entry,
            "before": self.trace_recorder.trace_state(initial_snapshot),
            "after": self.trace_recorder.trace_state(snapshot),
            "steps": steps,
        }
        self.trace_recorder.record_trace(macro_trace)
        snapshot["macro_trace"] = macro_trace
        return snapshot

    def follow_target(self, max_steps: int = 6, *, affordance_id: str | None = None) -> dict[str, Any]:
        initial_snapshot = self.snapshot_service.telemetry()
        steps = self._execute_field_window(
            initial_snapshot,
            strategy="target",
            max_steps=max_steps,
            objective_id=None,
            affordance_id=affordance_id,
        )
        snapshot = self.snapshot_service.telemetry()
        macro_trace = {
            "timestamp": self.trace_recorder.timestamp(),
            "kind": "follow_target",
            "max_steps": max_steps,
            "affordance_id": affordance_id,
            "before": self.trace_recorder.trace_state(initial_snapshot),
            "after": self.trace_recorder.trace_state(snapshot),
            "steps": steps,
        }
        self.trace_recorder.record_trace(macro_trace)
        snapshot["macro_trace"] = macro_trace
        return snapshot

    def follow_interaction(self, max_steps: int = 6) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []
        initial_snapshot = self.snapshot_service.telemetry()
        snapshot = initial_snapshot

        for _ in range(max_steps):
            interaction_type = snapshot.get("interaction", {}).get("type")
            if interaction_type in {None, "field"}:
                break

            decision = choose_interaction_action(snapshot, decision_state=self.memory.decision_state)
            snapshot = self.action_executor.execute_decision(decision)
            steps.append(
                {
                    "decision": decision,
                    "after": self.trace_recorder.trace_state(snapshot),
                }
            )
            next_interaction = snapshot.get("interaction", {}).get("type")
            if next_interaction in {None, "field"} or next_interaction != interaction_type:
                break

        interaction_resolution = reconcile_objective_interaction_resolution(
            self.memory.decision_state,
            before=initial_snapshot,
            after=snapshot,
        )

        macro_trace = {
            "timestamp": self.trace_recorder.timestamp(),
            "kind": "follow_interaction",
            "max_steps": max_steps,
            "before": self.trace_recorder.trace_state(initial_snapshot),
            "after": self.trace_recorder.trace_state(snapshot),
            "steps": steps,
            "interaction_resolution": interaction_resolution,
        }
        self.trace_recorder.record_trace(macro_trace)
        snapshot["macro_trace"] = macro_trace
        return snapshot

    def planner_step(self, goal: str = "progress") -> dict[str, Any]:
        snapshot = self.snapshot_service.telemetry()
        update_decision_state(self.memory.decision_state, snapshot)
        decision = choose_planner_action(
            snapshot,
            decision_state=self.memory.decision_state,
            map_catalog=self.core.map_catalog,
            goal=goal,
        )
        result = self.action_executor.execute_decision(decision)
        planner_trace = {
            "timestamp": self.trace_recorder.timestamp(),
            "kind": "planner_step",
            "goal": goal,
            "decision": decision,
            "before": self.trace_recorder.trace_state(snapshot),
            "after": self.trace_recorder.trace_state(result),
            "verification": {
                "passed": snapshot["mode"] != result["mode"]
                or snapshot["dialogue"]["visible_lines"] != result["dialogue"]["visible_lines"]
                or snapshot["menu"]["selected_item_text"] != result["menu"]["selected_item_text"]
                or snapshot["map"]["id"] != result["map"]["id"]
                or (snapshot["map"]["x"], snapshot["map"]["y"]) != (result["map"]["x"], result["map"]["y"]),
            },
        }
        update_decision_state(self.memory.decision_state, result)
        update_move_strategy(self.memory.decision_state, decision, planner_trace["verification"]["passed"])
        self.trace_recorder.record_trace(planner_trace)
        result["planner"] = {"goal": goal, "decision": decision, "trace": planner_trace}
        return result

    def execute_agent_action(
        self,
        action_id: str,
        reason: str | None = None,
        *,
        affordance_id: str | None = None,
        objective_id: str | None = None,
    ) -> dict[str, Any]:
        context = self.snapshot_service.agent_context()
        actions = {action["id"]: action for action in context["allowed_actions"]}
        resolved_action_id = action_id
        if resolved_action_id not in actions:
            resolved_action_id = self._resolve_agent_action_alias(action_id, actions)
        if resolved_action_id not in actions:
            supported = ", ".join(sorted(actions))
            raise ValueError(f"Unsupported agent action '{action_id}'. Expected one of: {supported}")

        action = actions[resolved_action_id]
        action_type = action["type"]
        if action_type == "action":
            result = self.action_executor.tap(action["button"])
        elif action_type == "routine":
            result = self.action_executor.run_routine(action["name"])
        elif action_type == "macro" and action["name"] == "follow_objective":
            result = self.follow_objective(objective_id=objective_id)
        elif action_type == "macro" and action["name"] == "follow_target":
            result = self.follow_target(affordance_id=affordance_id)
        elif action_type == "macro" and action["name"] == "follow_interaction":
            result = self.follow_interaction()
        elif action_type == "tick":
            result = self.action_executor.tick(action["frames"])
        elif action_type == "save_state":
            result = self.action_executor.save_state(action["slot"])
        elif action_type == "load_state":
            result = self.action_executor.load_state(action["slot"])
        else:
            raise ValueError(f"Unsupported agent action type '{action_type}' for '{action_id}'")

        execution_trace = {
            "timestamp": self.trace_recorder.timestamp(),
            "kind": "agent_action",
            "action_id": resolved_action_id,
            "requested_action_id": action_id,
            "action": action,
            "reason": reason,
            "affordance_id": affordance_id,
            "objective_id": objective_id,
            "after": self.trace_recorder.trace_state(result),
        }
        self.trace_recorder.record_trace(execution_trace)
        result["agent_action"] = execution_trace
        return result

    def _execute_field_window(
        self,
        snapshot: dict[str, Any],
        *,
        strategy: str,
        max_steps: int,
        objective_id: str | None,
        affordance_id: str | None,
    ) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        pinned_objective_id = objective_id
        for _ in range(max_steps):
            if snapshot["mode"] != "field":
                break

            if strategy == "objective":
                current_objective = self._resolve_objective(snapshot, pinned_objective_id)
                if current_objective is None:
                    break
            else:
                current_target = snapshot.get("navigation", {}).get("target_affordance")
                if current_target is None and not affordance_id:
                    break

            decision = choose_field_action(
                snapshot,
                decision_state=self.memory.decision_state,
                map_catalog=self.core.map_catalog,
                strategy=strategy,
                objective_id=pinned_objective_id,
                affordance_id=affordance_id,
            )
            snapshot = self.action_executor.execute_decision(decision)
            steps.append(
                {
                    "decision": decision,
                    "after": self.trace_recorder.trace_state(snapshot),
                }
            )
            if snapshot["mode"] != "field":
                break
            if snapshot.get("navigation", {}).get("consecutive_failures", 0) >= 2:
                break
            if strategy == "objective":
                current_objective = self._resolve_objective(snapshot, pinned_objective_id)
                if current_objective is None:
                    break
        return steps

    def _select_objective(self, snapshot: dict[str, Any], objective_id: str | None) -> dict[str, Any] | None:
        objective = self._resolve_objective(snapshot, objective_id)
        if objective is None:
            return None
        record_objective_selection(
            self.memory.decision_state,
            objective=objective,
            frame=snapshot["frame"],
        )
        return objective

    def _resolve_objective(self, snapshot: dict[str, Any], objective_id: str | None) -> dict[str, Any] | None:
        objective = find_objective_by_id(snapshot, objective_id)
        if objective is not None:
            return objective
        navigation = snapshot.get("navigation") or {}
        return navigation.get("active_objective") or navigation.get("objective")

    def _plan_objective_path(self, snapshot: dict[str, Any], objective: dict[str, Any], *, max_depth: int) -> list[str]:
        start_distance = self._objective_distance(snapshot, objective)
        if start_distance is None:
            return []

        start_state = self._capture_runtime_state()
        start_key = self._search_state_key(snapshot)
        frontier: list[tuple[int, int, int, dict[str, Any], RuntimeStateBundle, list[str]]] = []
        best_distance = start_distance
        best_path: list[str] = []
        sequence = 0
        visited = {start_key: 0}

        heappush(frontier, (start_distance, 0, sequence, snapshot, start_state, []))
        sequence += 1

        try:
            while frontier:
                _, depth, _, node_snapshot, node_state, path = heappop(frontier)
                if depth >= max_depth:
                    continue

                preferred = path[0] if path else "up"
                for direction in self._candidate_directions(node_snapshot, objective, preferred=preferred):
                    probe = self._simulate_direction_from_state(node_state, node_snapshot, direction)
                    if probe is None:
                        continue
                    child_snapshot = probe["snapshot"]
                    child_state = probe["state"]

                    score = self._score_objective_probe(node_snapshot, probe, objective)
                    if score is None:
                        continue

                    child_path = path + [direction]
                    if score == 0:
                        return child_path

                    child_key = self._search_state_key(child_snapshot)
                    if visited.get(child_key, max_depth + 1) <= depth + 1:
                        continue
                    visited[child_key] = depth + 1

                    if score < best_distance:
                        best_distance = score
                        best_path = child_path

                    heappush(
                        frontier,
                        (depth + 1 + score, depth + 1, sequence, child_snapshot, child_state, child_path),
                    )
                    sequence += 1
        finally:
            self._restore_runtime_state(start_state)
            self.memory.last_observation = snapshot

        return best_path

    @staticmethod
    def _candidate_directions(snapshot: dict[str, Any], objective: dict[str, Any], *, preferred: str) -> list[str]:
        current_x = snapshot["map"]["x"]
        current_y = snapshot["map"]["y"]
        ordered: list[str] = []

        if objective["kind"] == "trigger_region":
            primary = "up" if objective["axis"] == "y" and current_y > objective["value"] else None
            if objective["axis"] == "y" and current_y < objective["value"]:
                primary = "down"
            if objective["axis"] == "x" and current_x > objective["value"]:
                primary = "left"
            if objective["axis"] == "x" and current_x < objective["value"]:
                primary = "right"
            if primary:
                ordered.append(primary)
        else:
            target = objective.get("target")
            if target:
                delta_x = target["x"] - current_x
                delta_y = target["y"] - current_y
                if delta_y < 0:
                    ordered.append("up")
                elif delta_y > 0:
                    ordered.append("down")
                if delta_x < 0:
                    ordered.append("left")
                elif delta_x > 0:
                    ordered.append("right")

        ordered.append(preferred)
        for direction in ("up", "down", "left", "right"):
            if direction not in ordered:
                ordered.append(direction)
        return ordered

    def _capture_runtime_state(self) -> RuntimeStateBundle:
        return (
            self.core.capture_state_bytes(),
            self.memory.capture_runtime_state(),
        )

    def _restore_runtime_state(self, state: RuntimeStateBundle) -> None:
        state_bytes, memory_state = state
        self.core.restore_state_bytes(state_bytes)
        self.memory.restore_runtime_state(memory_state)

    def _simulate_direction_from_state(
        self,
        base_state: RuntimeStateBundle,
        snapshot: dict[str, Any],
        button: str,
    ) -> dict[str, Any] | None:
        try:
            candidate = self.action_executor.press(button, hold_frames=8, settle_frames=16, record_trace=False)
            return {
                "button": button,
                "snapshot": candidate,
                "state": self._capture_runtime_state(),
            }
        finally:
            self._restore_runtime_state(base_state)
            self.memory.last_observation = snapshot

    def _score_objective_probe(self, before: dict[str, Any], probe: dict[str, Any], objective: dict[str, Any]) -> int | None:
        after = probe["snapshot"]

        before_distance = self._objective_distance(before, objective)
        after_distance = self._objective_distance(after, objective)
        if after_distance is None:
            return None

        if after["mode"] != before["mode"] or after["dialogue"]["active"] != before["dialogue"]["active"]:
            return max(after_distance - 2, 0)

        if after["map"]["id"] != before["map"]["id"]:
            target_map = objective.get("target_map")
            if target_map and target_map != "LAST_MAP" and after["map"].get("const_name") == target_map:
                return 0
            return None

        if before_distance is not None and after_distance > before_distance:
            return None
        if (
            after["map"]["x"] == before["map"]["x"]
            and after["map"]["y"] == before["map"]["y"]
            and after["mode"] == before["mode"]
            and after["dialogue"]["visible_lines"] == before["dialogue"]["visible_lines"]
        ):
            return None
        return after_distance

    @staticmethod
    def _objective_distance(snapshot: dict[str, Any], objective: dict[str, Any]) -> int | None:
        return objective_distance(snapshot, objective)

    @staticmethod
    def _search_state_key(snapshot: dict[str, Any]) -> tuple[Any, ...]:
        return (
            snapshot["mode"],
            snapshot["map"]["id"],
            snapshot["map"]["x"],
            snapshot["map"]["y"],
            snapshot["map"]["script"],
            tuple(snapshot["dialogue"]["visible_lines"]),
            snapshot["menu"]["active"],
            ((snapshot.get("navigation") or {}).get("active_objective") or {}).get("id"),
        )

    @staticmethod
    def _resolve_agent_action_alias(action_id: str, actions: dict[str, dict[str, Any]]) -> str:
        button_aliases = {
            "press_a": "a",
            "interact_a": "a",
            "menu_confirm": "a",
            "battle_confirm": "a",
            "advance_dialogue": "a",
            "confirm": "a",
            "press_b": "b",
            "menu_back": "b",
            "battle_cancel": "b",
            "cancel": "b",
            "press_start": "start",
            "open_menu": "start",
            "press_select": "select",
        }
        routine_aliases = {
            "up": "move_up",
            "down": "move_down",
            "left": "move_left",
            "right": "move_right",
            "menu_up": "move_up",
            "menu_down": "move_down",
            "battle_up": "move_up",
            "battle_down": "move_down",
            "battle_left": "move_left",
            "battle_right": "move_right",
        }

        if action_id in routine_aliases and routine_aliases[action_id] in actions:
            return routine_aliases[action_id]

        button = button_aliases.get(action_id)
        if button is None:
            return action_id

        for candidate_id, action in actions.items():
            if action.get("type") == "action" and action.get("button") == button:
                return candidate_id
        for candidate_id, action in actions.items():
            if action.get("type") == "routine" and action.get("name") == f"move_{action_id.removeprefix('menu_').removeprefix('battle_')}":
                return candidate_id
        return action_id
