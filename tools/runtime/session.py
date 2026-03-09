from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import base64
from heapq import heappop, heappush
import io
import json
import logging
import os
from pathlib import Path
from collections import deque
from threading import RLock
from threading import Event, Thread
import time
import warnings

from .agent_context import build_agent_context
from .controls import normalize_button
from .map_data import load_map_catalog
from .objective_inference import (
    find_objective_by_id,
    fresh_objective_memory,
    objective_distance,
    reconcile_objective_interaction_resolution,
    record_map_history,
    record_objective_selection,
    update_objective_memory,
)
from .navigator import choose_field_action, enrich_snapshot_with_navigation, update_navigation_state
from .progress_memory import capture_progress_memory, fresh_progress_memory, update_progress_memory
from .symbols import load_symbol_table
from .telemetry import TelemetryAddresses, build_telemetry, derive_events

LOGGER = logging.getLogger(__name__)

ROMS = {
    "blue": ("pokeblue.gbc", "pokeblue.sym"),
    "red": ("pokered.gbc", "pokered.sym"),
    "blue-debug": ("pokeblue_debug.gbc", "pokeblue_debug.sym"),
}


class RuntimeSession:
    def __init__(
        self,
        repo_root: Path,
        rom_name: str,
        boot_frames: int = 0,
        auto_run: bool = False,
    ) -> None:
        if rom_name not in ROMS:
            supported = ", ".join(sorted(ROMS))
            raise ValueError(f"Unknown ROM '{rom_name}'. Expected one of: {supported}")

        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        warnings.filterwarnings("ignore", message="Using SDL2 binaries from pysdl2-dll.*")
        logging.getLogger("pyboy").setLevel(logging.ERROR)
        logging.getLogger("pyboy.pyboy").setLevel(logging.ERROR)
        from pyboy import PyBoy

        rom_filename, sym_filename = ROMS[rom_name]
        self.repo_root = repo_root
        self.rom_name = rom_name
        self.rom_path = repo_root / rom_filename
        self.sym_path = repo_root / sym_filename
        self.states_dir = repo_root / ".runtime-state" / rom_name
        self.traces_dir = repo_root / ".runtime-traces" / rom_name
        self.trace_log_path = self.traces_dir / "actions.jsonl"
        self._lock = RLock()
        self._stop_event = Event()
        self._run_event = Event()
        self._last_observation: dict | None = None
        self._event_log: deque[dict] = deque(maxlen=50)
        self._decision_state = self._fresh_decision_state()
        self._navigation_state = self._fresh_navigation_state()
        self._progress_memory = fresh_progress_memory()

        if not self.rom_path.exists():
            raise FileNotFoundError(f"Missing ROM: {self.rom_path}")
        if not self.sym_path.exists():
            raise FileNotFoundError(f"Missing symbol map: {self.sym_path}")
        self.states_dir.mkdir(parents=True, exist_ok=True)
        self.traces_dir.mkdir(parents=True, exist_ok=True)

        self.symbols = load_symbol_table(self.sym_path)
        self.telemetry_addresses = TelemetryAddresses.from_symbols(self.symbols)
        self.map_catalog = load_map_catalog(self.repo_root)
        self.pyboy = PyBoy(
            str(self.rom_path),
            window="null",
            sound_emulated=False,
            symbols=None,
            log_level="ERROR",
        )
        if boot_frames:
            for _ in range(boot_frames):
                self.pyboy.tick()

        self._runner = Thread(target=self._run_loop, name="pokered-runtime", daemon=True)
        self._runner.start()
        if auto_run:
            self.resume()

        LOGGER.info("Booted runtime for %s", self.rom_path.name)

    def stop(self) -> None:
        self._stop_event.set()
        self._run_event.set()
        if self._runner.is_alive():
            self._runner.join(timeout=1)
        with self._lock:
            self.pyboy.stop(save=False)

    @staticmethod
    def _fresh_decision_state() -> dict:
        return {
            "flags": {
                "oak_intro_active": False,
            },
            "exploration": {
                "field_move_index": 0,
            },
            "preferences": {
                "starter_preference": "SQUIRTLE",
                "nickname_policy": "decline",
                "player_name": "RED",
                "rival_name": "BLUE",
            },
            "objective": fresh_objective_memory(),
        }

    def _decision_preferences(self) -> dict[str, str]:
        return self._decision_state["preferences"]

    def _decision_preference(self, key: str, default: str | None = None) -> str | None:
        return self._decision_preferences().get(key, default)

    def _decision_flag(self, key: str) -> bool:
        return bool(self._decision_state["flags"].get(key))

    def _set_decision_flag(self, key: str, value: bool) -> None:
        self._decision_state["flags"][key] = value

    def _field_move_index(self) -> int:
        return int(self._decision_state["exploration"].get("field_move_index", 0))

    def _advance_field_move_index(self) -> None:
        self._decision_state["exploration"]["field_move_index"] = (self._field_move_index() + 1) % 4

    def _run_loop(self) -> None:
        frame_duration = 1 / 60
        next_tick = time.perf_counter()
        while not self._stop_event.is_set():
            if not self._run_event.is_set():
                time.sleep(0.01)
                next_tick = time.perf_counter()
                continue

            now = time.perf_counter()
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.01))
                continue

            with self._lock:
                self.pyboy.tick()
            next_tick += frame_duration

    def pause(self) -> dict:
        self._run_event.clear()
        return self.status()

    def resume(self) -> dict:
        self._run_event.set()
        return self.status()

    def status(self) -> dict:
        with self._lock:
            return {
                "rom": self.rom_name,
                "frame": self.pyboy.frame_count,
                "running": self._run_event.is_set(),
                "states_dir": str(self.states_dir),
                "trace_log_path": str(self.trace_log_path),
                "runtime_memory": {
                    "visited_maps": len(self._progress_memory.get("visited_maps", {})),
                    "recent_targets": len(self._progress_memory.get("recent_targets", [])),
                    "objective_history": len((self._decision_state.get("objective") or {}).get("objective_history", [])),
                },
            }

    def tick(self, frames: int = 1) -> dict:
        with self._lock:
            for _ in range(frames):
                self.pyboy.tick()
            return self._snapshot_unlocked()

    def tap(self, button: str, hold_frames: int = 2, settle_frames: int = 2, *, record_trace: bool = True) -> dict:
        return self.press(button, hold_frames=hold_frames, settle_frames=settle_frames, record_trace=record_trace)

    def press(self, button: str, hold_frames: int = 2, settle_frames: int = 2, *, record_trace: bool = True) -> dict:
        button_name = normalize_button(button)
        with self._lock:
            before = self._build_snapshot_unlocked()
            self.pyboy.button_press(button_name)
            for _ in range(hold_frames):
                self.pyboy.tick()
            self.pyboy.button_release(button_name)
            after = self._settle_after_input_unlocked(
                before=before,
                button_name=button_name,
                minimum_frames=settle_frames,
                reason=f"press:{button_name}",
            )
            self._update_navigation_state(before=before, after=after, payload={"button": button_name})
            enrich_snapshot_with_navigation(
                after,
                map_catalog=self.map_catalog,
                navigation_state=self._navigation_state,
                progress_memory=self._progress_memory,
                decision_state=self._decision_state,
            )
            self._update_progress_memory(before=before, after=after)
            self._update_decision_state(after)
            if record_trace:
                trace = self._build_action_trace(
                    kind="press",
                    payload={
                        "button": button_name,
                        "hold_frames": hold_frames,
                        "settle_frames": settle_frames,
                    },
                    before=before,
                    after=after,
                )
                self._record_trace(trace)
                after["action_trace"] = trace
            return after

    def sequence(self, steps: list[dict]) -> dict:
        traces: list[dict] = []
        with self._lock:
            before_sequence = self._build_snapshot_unlocked()
            for index, step in enumerate(steps):
                button_name = normalize_button(step["button"])
                hold_frames = step.get("hold_frames", 2)
                settle_frames = step.get("settle_frames", 2)
                before = self._build_snapshot_unlocked()
                self.pyboy.button_press(button_name)
                for _ in range(hold_frames):
                    self.pyboy.tick()
                self.pyboy.button_release(button_name)
                after = self._settle_after_input_unlocked(
                    before=before,
                    button_name=button_name,
                    minimum_frames=settle_frames,
                    reason=f"sequence_step:{index}:{button_name}",
                )
                self._update_navigation_state(before=before, after=after, payload={"button": button_name})
                enrich_snapshot_with_navigation(
                    after,
                    map_catalog=self.map_catalog,
                    navigation_state=self._navigation_state,
                    progress_memory=self._progress_memory,
                    decision_state=self._decision_state,
                )
                self._update_progress_memory(before=before, after=after)
                trace = self._build_action_trace(
                    kind="sequence_step",
                    payload={
                        "index": index,
                        "button": button_name,
                        "hold_frames": hold_frames,
                        "settle_frames": settle_frames,
                    },
                    before=before,
                    after=after,
                )
                traces.append(trace)
                self._record_trace(trace)
                self._update_decision_state(after)
            final_snapshot = self._snapshot_unlocked(
                suppress_derive=True,
                extra_events=[
                    {
                        "frame": self.pyboy.frame_count,
                        "type": "sequence_completed",
                        "label": f"Sequence completed ({len(steps)} step{'s' if len(steps) != 1 else ''})",
                    }
                ],
            )
            sequence_trace = {
                "timestamp": self._timestamp(),
                "kind": "sequence",
                "steps": len(steps),
                "before": self._trace_state(before_sequence),
                "after": self._trace_state(final_snapshot),
                "step_results": traces,
            }
            self._record_trace(sequence_trace)
            final_snapshot["sequence_trace"] = sequence_trace
            return final_snapshot

    def run_routine(self, name: str) -> dict:
        routines = {
            "open_menu": [{"button": "start", "hold_frames": 2, "settle_frames": 90}],
            "close_menu": [{"button": "b", "hold_frames": 2, "settle_frames": 40}],
            "advance_dialogue": [{"button": "a", "hold_frames": 2, "settle_frames": 240}],
            "move_up": [{"button": "up", "hold_frames": 8, "settle_frames": 16}],
            "move_down": [{"button": "down", "hold_frames": 8, "settle_frames": 16}],
            "move_left": [{"button": "left", "hold_frames": 8, "settle_frames": 16}],
            "move_right": [{"button": "right", "hold_frames": 8, "settle_frames": 16}],
            "face_up": [{"button": "up", "hold_frames": 1, "settle_frames": 6}],
            "face_down": [{"button": "down", "hold_frames": 1, "settle_frames": 6}],
            "face_left": [{"button": "left", "hold_frames": 1, "settle_frames": 6}],
            "face_right": [{"button": "right", "hold_frames": 1, "settle_frames": 6}],
        }
        if name not in routines:
            supported = ", ".join(sorted(routines))
            raise ValueError(f"Unsupported routine '{name}'. Expected one of: {supported}")
        result = self.sequence(routines[name])
        result["routine"] = {"name": name}
        return result

    def follow_objective(self, max_steps: int = 6, *, objective_id: str | None = None) -> dict:
        initial_snapshot = self.telemetry()
        objective = self._select_objective(initial_snapshot, objective_id)
        if objective is None:
            snapshot = initial_snapshot
            macro_trace = {
                "timestamp": self._timestamp(),
                "kind": "follow_objective",
                "max_steps": max_steps,
                "objective_id": objective_id,
                "before": self._trace_state(initial_snapshot),
                "after": self._trace_state(snapshot),
                "steps": [],
                "error": "No objective candidate is available in the current field state.",
            }
            self._record_trace(macro_trace)
            snapshot["macro_trace"] = macro_trace
            return snapshot

        snapshot = self.telemetry()
        steps = self._execute_field_window(
            snapshot,
            strategy="objective",
            max_steps=max_steps,
            objective_id=objective["id"],
            affordance_id=None,
        )
        snapshot = self.telemetry()
        progress_entry = update_objective_memory(
            self._decision_state,
            before=initial_snapshot,
            after=snapshot,
            objective=objective,
            steps=steps,
        )
        snapshot = self.telemetry()
        macro_trace = {
            "timestamp": self._timestamp(),
            "kind": "follow_objective",
            "max_steps": max_steps,
            "objective_id": objective["id"],
            "objective": objective,
            "progress": progress_entry,
            "before": self._trace_state(initial_snapshot),
            "after": self._trace_state(snapshot),
            "steps": steps,
        }
        self._record_trace(macro_trace)
        snapshot["macro_trace"] = macro_trace
        return snapshot

    def follow_target(self, max_steps: int = 6, *, affordance_id: str | None = None) -> dict:
        initial_snapshot = self.telemetry()
        steps = self._execute_field_window(
            initial_snapshot,
            strategy="target",
            max_steps=max_steps,
            objective_id=None,
            affordance_id=affordance_id,
        )
        snapshot = self.telemetry()
        macro_trace = {
            "timestamp": self._timestamp(),
            "kind": "follow_target",
            "max_steps": max_steps,
            "affordance_id": affordance_id,
            "before": self._trace_state(initial_snapshot),
            "after": self._trace_state(snapshot),
            "steps": steps,
        }
        self._record_trace(macro_trace)
        snapshot["macro_trace"] = macro_trace
        return snapshot

    def follow_interaction(self, max_steps: int = 6) -> dict:
        steps: list[dict] = []
        initial_snapshot = self.telemetry()
        snapshot = initial_snapshot

        for _ in range(max_steps):
            interaction_type = snapshot.get("interaction", {}).get("type")
            if interaction_type in {None, "field"}:
                break

            decision = self._choose_interaction_action(snapshot)
            if decision["type"] == "routine":
                snapshot = self.run_routine(decision["name"])
            elif decision["type"] == "action":
                snapshot = self.tap(decision["button"])
            elif decision["type"] == "tick":
                snapshot = self.tick(decision["frames"])
            else:
                break

            steps.append(
                {
                    "decision": decision,
                    "after": self._trace_state(snapshot),
                }
            )
            next_interaction = snapshot.get("interaction", {}).get("type")
            if next_interaction in {None, "field"}:
                break
            if next_interaction != interaction_type:
                break

        interaction_resolution = reconcile_objective_interaction_resolution(
            self._decision_state,
            before=initial_snapshot,
            after=snapshot,
        )

        macro_trace = {
            "timestamp": self._timestamp(),
            "kind": "follow_interaction",
            "max_steps": max_steps,
            "before": self._trace_state(initial_snapshot),
            "after": self._trace_state(snapshot),
            "steps": steps,
            "interaction_resolution": interaction_resolution,
        }
        self._record_trace(macro_trace)
        snapshot["macro_trace"] = macro_trace
        return snapshot

    def _refine_field_decision(self, snapshot: dict, decision: dict) -> dict:
        return decision

    def _execute_field_window(
        self,
        snapshot: dict,
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

            decision = self._choose_field_action(
                snapshot,
                strategy=strategy,
                objective_id=pinned_objective_id,
                affordance_id=affordance_id,
            )
            decision = self._refine_field_decision(snapshot, decision)
            if decision["type"] == "routine":
                snapshot = self.run_routine(decision["name"])
            elif decision["type"] == "action":
                snapshot = self.tap(decision["button"])
            elif decision["type"] == "tick":
                snapshot = self.tick(decision["frames"])
            else:
                break

            steps.append(
                {
                    "decision": decision,
                    "after": self._trace_state(snapshot),
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

    def _select_objective(self, snapshot: dict, objective_id: str | None) -> dict[str, Any] | None:
        objective = self._resolve_objective(snapshot, objective_id)
        if objective is None:
            return None
        record_objective_selection(
            self._decision_state,
            objective=objective,
            frame=snapshot["frame"],
        )
        return objective

    def _resolve_objective(self, snapshot: dict, objective_id: str | None) -> dict[str, Any] | None:
        objective = find_objective_by_id(snapshot, objective_id)
        if objective is not None:
            return objective
        navigation = snapshot.get("navigation") or {}
        return navigation.get("active_objective") or navigation.get("objective")

    def _plan_objective_path(self, snapshot: dict, objective: dict, *, max_depth: int) -> list[str]:
        start_distance = self._objective_distance(snapshot, objective)
        if start_distance is None:
            return []

        start_state = self._capture_runtime_state()
        start_key = self._search_state_key(snapshot)
        frontier: list[tuple[int, int, int, dict, tuple[bytes, dict, dict, dict], list[str]]] = []
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
            self._last_observation = snapshot

        return best_path

    def planner_step(self, goal: str = "progress") -> dict:
        snapshot = self.telemetry()
        self._update_decision_state(snapshot)
        decision = self._choose_planner_action(snapshot, goal)

        if decision["type"] == "routine":
            result = self.run_routine(decision["name"])
        elif decision["type"] == "action":
            result = self.tap(decision["button"])
        elif decision["type"] == "tick":
            result = self.tick(decision["frames"])
        else:
            raise ValueError(f"Unsupported planner action type '{decision['type']}'")

        planner_trace = {
            "timestamp": self._timestamp(),
            "kind": "planner_step",
            "goal": goal,
            "decision": decision,
            "before": self._trace_state(snapshot),
            "after": self._trace_state(result),
            "verification": {
                "passed": snapshot["mode"] != result["mode"]
                or snapshot["dialogue"]["visible_lines"] != result["dialogue"]["visible_lines"]
                or snapshot["menu"]["selected_item_text"] != result["menu"]["selected_item_text"]
                or snapshot["map"]["id"] != result["map"]["id"]
                or (snapshot["map"]["x"], snapshot["map"]["y"]) != (result["map"]["x"], result["map"]["y"]),
            },
        }
        self._update_decision_state(result)
        self._update_move_strategy(decision, planner_trace["verification"]["passed"])
        self._record_trace(planner_trace)
        result["planner"] = {"goal": goal, "decision": decision, "trace": planner_trace}
        return result

    def telemetry(self) -> dict:
        with self._lock:
            return self._snapshot_unlocked()

    def snapshot_bundle(self) -> dict:
        with self._lock:
            telemetry = self._snapshot_unlocked()
            buffer = io.BytesIO()
            self.pyboy.screen.image.save(buffer, format="PNG")
            return {
                "telemetry": telemetry,
                "frame_png_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
            }

    def recent_traces(self, limit: int = 50) -> dict:
        if limit < 1:
            limit = 1
        lines: deque[str] = deque(maxlen=min(limit, 500))
        if self.trace_log_path.exists():
            with self.trace_log_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        lines.append(line)
        return {
            "rom": self.rom_name,
            "trace_log_path": str(self.trace_log_path),
            "traces": [json.loads(line) for line in lines],
        }

    def agent_context(self) -> dict:
        snapshot = self.telemetry()
        traces = self.recent_traces(limit=12)["traces"]
        return build_agent_context(
            snapshot,
            traces,
            decision_state=dict(self._decision_state),
        )

    def execute_agent_action(
        self,
        action_id: str,
        reason: str | None = None,
        *,
        affordance_id: str | None = None,
        objective_id: str | None = None,
    ) -> dict:
        context = self.agent_context()
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
            result = self.tap(action["button"])
        elif action_type == "routine":
            result = self.run_routine(action["name"])
        elif action_type == "macro" and action["name"] == "follow_objective":
            result = self.follow_objective(objective_id=objective_id)
        elif action_type == "macro" and action["name"] == "follow_target":
            result = self.follow_target(affordance_id=affordance_id)
        elif action_type == "macro" and action["name"] == "follow_interaction":
            result = self.follow_interaction()
        elif action_type == "tick":
            result = self.tick(action["frames"])
        elif action_type == "save_state":
            result = self.save_state(action["slot"])
        elif action_type == "load_state":
            result = self.load_state(action["slot"])
        else:
            raise ValueError(f"Unsupported agent action type '{action_type}' for '{action_id}'")

        execution_trace = {
            "timestamp": self._timestamp(),
            "kind": "agent_action",
            "action_id": resolved_action_id,
            "requested_action_id": action_id,
            "action": action,
            "reason": reason,
            "affordance_id": affordance_id,
            "objective_id": objective_id,
            "after": self._trace_state(result),
        }
        self._record_trace(execution_trace)
        result["agent_action"] = execution_trace
        return result

    def _resolve_agent_action_alias(self, action_id: str, actions: dict[str, dict]) -> str:
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

    def _choose_planner_action(self, snapshot: dict, goal: str) -> dict:
        if goal != "progress":
            raise ValueError(f"Unsupported planner goal '{goal}'")

        interaction_type = snapshot.get("interaction", {}).get("type")
        if interaction_type and interaction_type != "field":
            return self._choose_interaction_action(snapshot)

        mode = snapshot["mode"]
        if mode == "menu_dialogue":
            return self._choose_menu_action(snapshot, allow_dialogue_fallback=True)
        if mode == "dialogue":
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": "Visible dialogue is active, so advance it with A.",
            }
        if mode == "menu":
            return self._choose_menu_action(snapshot, allow_dialogue_fallback=False)
        if mode == "field":
            return self._choose_field_action(snapshot, strategy="objective")
        if mode == "battle":
            return self._choose_battle_action(snapshot)
        return {
            "type": "tick",
            "frames": 10,
            "reason": "Wait through transition/loading frames before deciding again.",
        }

    def _choose_interaction_action(self, snapshot: dict) -> dict:
        interaction = snapshot.get("interaction", {})
        interaction_type = interaction.get("type")
        if interaction_type in {"dialogue", "battle_dialogue"}:
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": "Visible dialogue is active, so advance it with A.",
            }
        if interaction_type == "pokedex_info":
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": "A full-screen Pokédex info card is open and waits for A or B, so advance it with A.",
            }
        if interaction_type == "binary_choice":
            return self._choose_binary_choice_action(snapshot)
        if interaction_type == "preset_name_choice":
            return self._choose_menu_action(snapshot, allow_dialogue_fallback=True)
        if interaction_type == "text_entry":
            return self._choose_text_entry_action(snapshot)
        if interaction_type == "battle_command_menu" or interaction_type == "battle_move_menu":
            return self._choose_battle_action(snapshot)
        if interaction_type in {"list_choice", "menu_dialogue"}:
            return self._choose_menu_action(snapshot, allow_dialogue_fallback=True)
        if interaction_type == "battle_transition":
            return {
                "type": "tick",
                "frames": 20,
                "reason": "Battle state is transitioning; wait briefly for the next stable input window.",
            }
        return {
            "type": "tick",
            "frames": 10,
            "reason": "Interaction state is ambiguous, so re-observe briefly.",
        }

    def _choose_field_action(
        self,
        snapshot: dict,
        *,
        strategy: str = "objective",
        objective_id: str | None = None,
        affordance_id: str | None = None,
    ) -> dict:
        recent_event_types = [event["type"] for event in snapshot["events"]["recent"][-4:]]
        if snapshot["dialogue"]["active"] or snapshot["screen"].get("message_box_present"):
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": "A dialogue box is visible, so continue it with A instead of treating the scene as free movement.",
            }

        if "dialogue_closed" in recent_event_types or "menu_closed" in recent_event_types:
            return {
                "type": "tick",
                "frames": 20,
                "reason": "A script-driven dialogue or menu just closed, so wait briefly for the next stable field state.",
            }
        return choose_field_action(
            snapshot,
            decision_state=self._decision_state,
            map_catalog=self.map_catalog,
            strategy=strategy,
            objective_id=objective_id,
            preferred_affordance_id=affordance_id,
        )

    def _candidate_directions(self, snapshot: dict, objective: dict, *, preferred: str) -> list[str]:
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

    def _simulate_direction(self, snapshot: dict, button: str) -> dict | None:
        base_state = self._capture_runtime_state()
        return self._simulate_direction_from_state(base_state, snapshot, button)

    def _simulate_direction_from_state(
        self,
        base_state: tuple[bytes, dict, dict, dict],
        snapshot: dict,
        button: str,
    ) -> dict | None:
        try:
            candidate = self.press(button, hold_frames=8, settle_frames=16, record_trace=False)
            return {
                "button": button,
                "snapshot": candidate,
                "state": self._capture_runtime_state(),
            }
        finally:
            self._restore_runtime_state(base_state)
            self._last_observation = snapshot

    def _score_objective_probe(self, before: dict, probe: dict, objective: dict) -> int | None:
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

    def _objective_distance(self, snapshot: dict, objective: dict) -> int | None:
        return objective_distance(snapshot, objective)

    def _search_state_key(self, snapshot: dict) -> tuple:
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

    def _choose_menu_action(self, snapshot: dict, *, allow_dialogue_fallback: bool) -> dict:
        visible_items = snapshot["menu"]["visible_items"]
        selected = snapshot["menu"]["selected_item_text"]
        current_index = snapshot["menu"]["selected_index"]
        dialogue_lines = snapshot["dialogue"]["visible_lines"]

        target_label = self._select_menu_target(snapshot)
        if target_label is not None:
            target_index = visible_items.index(target_label)
            if current_index is None or current_index == target_index:
                return {
                    "type": "routine",
                    "name": "advance_dialogue",
                    "reason": f"Menu target '{target_label}' is selected, so confirm it.",
                }
            if current_index > target_index:
                return {
                    "type": "routine",
                    "name": "move_up",
                    "reason": f"Move menu selection up toward '{target_label}'.",
                }
            return {
                "type": "routine",
                "name": "move_down",
                "reason": f"Move menu selection down toward '{target_label}'.",
            }

        if "NEW GAME" in visible_items:
            return {
                "type": "routine",
                "name": "advance_dialogue" if selected == "NEW GAME" else "move_up",
                "reason": "Title menu is open; target NEW GAME.",
            }

        if selected:
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": "A menu item is selected, so confirm it.",
            }

        if allow_dialogue_fallback and dialogue_lines:
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": "Dialogue is visible alongside the menu, so try advancing.",
            }

        return {
            "type": "routine",
            "name": "close_menu",
            "reason": "Menu is open without a clear target, so close it.",
        }

    def _select_menu_target(self, snapshot: dict) -> str | None:
        visible_items = snapshot["menu"]["visible_items"]
        if not visible_items:
            return None

        dialogue_lines = snapshot["dialogue"]["visible_lines"]
        normalized_dialogue = " ".join(dialogue_lines).lower()
        upper_items = {item.upper(): item for item in visible_items}
        preferred_binary = self._determine_binary_choice(snapshot)
        if preferred_binary and preferred_binary in upper_items:
            return upper_items[preferred_binary]

        if "your name" in normalized_dialogue:
            target = self._select_preset_name_target(visible_items, self._decision_preference("player_name"))
            if target is not None:
                return target

        if "his name" in normalized_dialogue or "rival" in normalized_dialogue:
            target = self._select_preset_name_target(visible_items, self._decision_preference("rival_name"))
            if target is not None:
                return target

        if (snapshot.get("interaction") or {}).get("type") == "preset_name_choice":
            target = self._select_preset_name_target(visible_items, None)
            if target is not None:
                return target

        for candidate in ("CANCEL", "EXIT"):
            if candidate in upper_items:
                return upper_items[candidate]

        return None

    def _select_preset_name_target(self, visible_items: list[str], preferred_name: str | None) -> str | None:
        upper_items = {item.upper(): item for item in visible_items}
        if preferred_name:
            normalized_name = preferred_name.upper()
            if normalized_name in upper_items:
                return upper_items[normalized_name]

        preset_items = [
            item for item in visible_items
            if item.upper() not in {"NEW NAME", "CANCEL", "EXIT"}
        ]
        if preset_items:
            return preset_items[0]

        if "NEW NAME" in upper_items:
            return upper_items["NEW NAME"]

        return None

    def _choose_binary_choice_action(self, snapshot: dict) -> dict:
        visible_items = snapshot["menu"]["visible_items"]
        current_index = snapshot["menu"]["selected_index"]
        preferred = self._determine_binary_choice(snapshot) or "YES"
        upper_items = {item.upper(): index for index, item in enumerate(visible_items)}
        target_index = upper_items.get(preferred)
        if target_index is None:
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": "A binary choice is visible and the preferred option is already implied, so confirm it.",
            }
        if current_index is None or current_index == target_index:
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": f"Choose {preferred} for the current prompt.",
            }
        if current_index > target_index:
            return {
                "type": "routine",
                "name": "move_up",
                "reason": f"Move the binary-choice cursor toward {preferred}.",
            }
        return {
            "type": "routine",
            "name": "move_down",
            "reason": f"Move the binary-choice cursor toward {preferred}.",
        }

    def _determine_binary_choice(self, snapshot: dict) -> str | None:
        prompt = ((snapshot.get("interaction") or {}).get("prompt") or "").lower()
        if not prompt:
            prompt = " ".join(snapshot["dialogue"]["visible_lines"]).lower()

        if "nickname" in prompt:
            return "NO" if self._decision_preference("nickname_policy") == "decline" else "YES"
        if "you want the" in prompt:
            preferred = str(self._decision_preference("starter_preference", "SQUIRTLE")).lower()
            return "YES" if preferred in prompt else "NO"
        if "save" in prompt:
            return "YES"
        if "sure" in prompt or "okay" in prompt or "ready" in prompt:
            return "YES"
        return "YES"

    def _choose_text_entry_action(self, snapshot: dict) -> dict:
        naming = snapshot["naming"]
        desired_text = self._desired_name_for_screen(snapshot)
        current_text = naming["current_text"]
        if current_text == desired_text:
            return {
                "type": "action",
                "button": "start",
                "reason": f"The desired name '{desired_text}' is already entered, so submit it with Start.",
            }
        if not desired_text.startswith(current_text):
            return {
                "type": "action",
                "button": "b",
                "reason": f"Backspace to realign the current name with the desired target '{desired_text}'.",
            }

        next_char = desired_text[len(current_text)]
        target_position = self._find_naming_character(snapshot, next_char)
        if target_position is None:
            return {
                "type": "tick",
                "frames": 10,
                "reason": f"Wait briefly because the naming keyboard could not locate '{next_char}'.",
            }

        current_row = naming.get("cursor_row")
        current_col = naming.get("cursor_col")
        target_row, target_col = target_position
        if current_row is None or current_col is None:
            return {
                "type": "tick",
                "frames": 10,
                "reason": "Wait briefly because the naming cursor position is not yet stable.",
            }
        if current_row > target_row:
            return {"type": "routine", "name": "move_up", "reason": f"Move naming cursor up toward '{next_char}'."}
        if current_row < target_row:
            return {"type": "routine", "name": "move_down", "reason": f"Move naming cursor down toward '{next_char}'."}
        if current_col > target_col:
            return {"type": "routine", "name": "move_left", "reason": f"Move naming cursor left toward '{next_char}'."}
        if current_col < target_col:
            return {"type": "routine", "name": "move_right", "reason": f"Move naming cursor right toward '{next_char}'."}
        return {
            "type": "routine",
            "name": "advance_dialogue",
            "reason": f"Select '{next_char}' on the naming keyboard.",
        }

    def _desired_name_for_screen(self, snapshot: dict) -> str:
        naming = snapshot["naming"]
        screen_type = naming.get("screen_type")
        if screen_type == "player":
            return str(self._decision_preference("player_name", "RED"))
        if screen_type == "rival":
            return str(self._decision_preference("rival_name", "BLUE"))
        if screen_type == "pokemon" and self._decision_preference("nickname_policy") == "decline":
            return ""
        base_name = naming.get("base_name") or "MON"
        if self._decision_preference("nickname_policy") == "decline":
            return base_name
        return base_name

    def _find_naming_character(self, snapshot: dict, target_char: str) -> tuple[int, int] | None:
        keyboard_rows = snapshot["naming"].get("keyboard_rows") or []
        for row_index, row in enumerate(keyboard_rows[:5]):
            for col_index, char in enumerate(row):
                if char == target_char:
                    return row_index, col_index
        return None

    def _choose_battle_action(self, snapshot: dict) -> dict:
        battle = snapshot["battle"]
        if battle["ui_state"] == "dialogue":
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": "Visible battle dialogue is active, so advance it with A.",
            }
        if battle["ui_state"] == "command_menu":
            selected = battle["command_menu"]["selected_command"]
            if selected == "FIGHT":
                return {
                    "type": "routine",
                    "name": "advance_dialogue",
                    "reason": "The FIGHT command is selected, so confirm it.",
                }
            command_positions = {
                "FIGHT": 0,
                "PKMN": 1,
                "ITEM": 2,
                "RUN": 3,
            }
            selected_index = battle["command_menu"]["selected_index"]
            target_index = command_positions["FIGHT"]
            if selected_index is None:
                return {
                    "type": "routine",
                    "name": "advance_dialogue",
                    "reason": "The battle command menu is visible; try confirming the default command.",
                }
            if selected_index in {1, 3} and target_index in {0, 2}:
                return {"type": "routine", "name": "move_up", "reason": "Move the battle cursor toward FIGHT."}
            if selected_index in {2, 3} and target_index in {0, 1}:
                return {"type": "routine", "name": "move_left", "reason": "Move the battle cursor toward FIGHT."}
            if selected_index == 0:
                return {"type": "routine", "name": "advance_dialogue", "reason": "Confirm FIGHT."}
        if battle["ui_state"] == "move_menu":
            moves = battle["move_menu"]["moves"]
            if not moves:
                return {
                    "type": "tick",
                    "frames": 10,
                    "reason": "The move menu is visible but the available moves are not parsed yet.",
                }
            preferred = max(
                moves,
                key=lambda move: (
                    1 if move["pp"] > 0 else 0,
                    move["power"],
                    move["accuracy"] or 0,
                ),
            )
            selected_index = battle["move_menu"]["selected_index"]
            if selected_index is None or selected_index == preferred["slot"]:
                return {
                    "type": "routine",
                    "name": "advance_dialogue",
                    "reason": f"Use {preferred['name']} because it is the strongest available move with PP remaining.",
                }
            if selected_index > preferred["slot"]:
                return {"type": "routine", "name": "move_up", "reason": f"Move the battle cursor toward {preferred['name']}."}
            return {"type": "routine", "name": "move_down", "reason": f"Move the battle cursor toward {preferred['name']}."}
        return {
            "type": "tick",
            "frames": 10,
            "reason": "Battle state is in transition, so wait for the next clear prompt.",
        }

    def _update_decision_state(self, snapshot: dict) -> None:
        record_map_history(self._decision_state, snapshot)
        dialogue = " ".join(snapshot["dialogue"]["visible_lines"]).lower()
        intro_markers = (
            "hello there",
            "world of pok",
            "my name is oak",
            "what is your name",
            "what is his name again",
            "your rival since",
            "remember now! his name is",
            "your very own",
            "legend is",
            "adventures",
        )
        gameplay_markers = (
            "playing the snes",
            "...okay!",
        )

        if any(marker in dialogue for marker in intro_markers):
            self._set_decision_flag("oak_intro_active", True)
        if any(marker in dialogue for marker in gameplay_markers):
            self._set_decision_flag("oak_intro_active", False)

    def _update_move_strategy(self, decision: dict, passed: bool) -> None:
        name = decision.get("name", "")
        if not name.startswith("move_"):
            return
        if not passed:
            self._advance_field_move_index()

    def list_states(self) -> dict:
        with self._lock:
            states = []
            for path in sorted(self.states_dir.glob("*.state")):
                metadata = self._load_state_metadata(path)
                states.append(
                    {
                        "slot": path.stem,
                        "path": str(path),
                        "size": path.stat().st_size,
                        "updated_at": int(path.stat().st_mtime),
                        "metadata": metadata,
                    }
                )
            return {
                "rom": self.rom_name,
                "states_dir": str(self.states_dir),
                "states": states,
            }

    def save_state(self, slot: str = "quick") -> dict:
        path = self._state_path(slot)
        with self._lock:
            snapshot_before_save = self._build_snapshot_unlocked()
            with path.open("wb") as handle:
                self.pyboy.save_state(handle)
            metadata = {
                "slot": slot,
                "saved_frame": snapshot_before_save["frame"],
                "saved_mode": snapshot_before_save["mode"],
                "saved_map": snapshot_before_save["map"],
                "saved_menu": {
                    "active": snapshot_before_save["menu"]["active"],
                    "selected_item_text": snapshot_before_save["menu"]["selected_item_text"],
                },
                "saved_dialogue": snapshot_before_save["dialogue"]["visible_lines"],
            }
            self._state_metadata_path(slot).write_text(
                json.dumps(metadata, indent=2) + "\n",
                encoding="utf-8",
            )
            snapshot = self._snapshot_unlocked(
                suppress_derive=True,
                extra_events=[
                    {
                        "frame": self.pyboy.frame_count,
                        "type": "state_saved",
                        "label": f"Saved state: {slot}",
                    }
                ],
            )
            snapshot["state"] = {
                "slot": slot,
                "path": str(path),
                "action": "saved",
                "metadata": metadata,
            }
            return snapshot

    def load_state(self, slot: str = "quick") -> dict:
        path = self._state_path(slot)
        if not path.exists():
            raise FileNotFoundError(f"Missing save state slot '{slot}' at {path}")
        with self._lock:
            with path.open("rb") as handle:
                self.pyboy.load_state(handle)
            self._reset_runtime_memory_unlocked()
            metadata = self._load_state_metadata(path)
            snapshot = self._snapshot_unlocked(
                suppress_derive=True,
                extra_events=[
                    {
                        "frame": self.pyboy.frame_count,
                        "type": "state_loaded",
                        "label": f"Loaded state: {slot}",
                    }
                ],
            )
            snapshot["state"] = {
                "slot": slot,
                "path": str(path),
                "action": "loaded",
                "metadata": metadata,
            }
            return snapshot

    def reset_runtime_memory(self) -> dict:
        with self._lock:
            self._reset_runtime_memory_unlocked()
            snapshot = self._snapshot_unlocked(
                suppress_derive=True,
                extra_events=[
                    {
                        "frame": self.pyboy.frame_count,
                        "type": "runtime_memory_reset",
                        "label": "Runtime memory reset",
                    }
                ],
            )
            snapshot["runtime_memory"] = {
                "action": "reset",
            }
            return snapshot

    def _capture_runtime_state(self) -> tuple[bytes, dict, dict, dict]:
        with self._lock:
            buffer = io.BytesIO()
            self.pyboy.save_state(buffer)
            return (
                buffer.getvalue(),
                deepcopy(self._navigation_state),
                deepcopy(self._decision_state),
                capture_progress_memory(self._progress_memory),
            )

    def _restore_runtime_state(self, state: tuple[bytes, dict, dict, dict]) -> None:
        state_bytes, navigation_state, decision_state, progress_memory = state
        with self._lock:
            self.pyboy.load_state(io.BytesIO(state_bytes))
            self._navigation_state = deepcopy(navigation_state)
            self._decision_state = deepcopy(decision_state)
            self._progress_memory = capture_progress_memory(progress_memory)

    def _snapshot_unlocked(
        self,
        *,
        suppress_derive: bool = False,
        extra_events: list[dict] | None = None,
    ) -> dict:
        snapshot = self._build_snapshot_unlocked()
        events = [] if suppress_derive else derive_events(self._last_observation, snapshot)
        if extra_events:
            events.extend(extra_events)
        self._last_observation = snapshot
        self._event_log.extend(events)
        snapshot["events"] = {
            "latest": events[-1] if events else None,
            "recent": list(self._event_log),
        }
        return snapshot

    def _build_snapshot_unlocked(self) -> dict:
        snapshot = build_telemetry(self.pyboy, self.telemetry_addresses)
        enrich_snapshot_with_navigation(
            snapshot,
            map_catalog=self.map_catalog,
            navigation_state=self._navigation_state,
            progress_memory=self._progress_memory,
            decision_state=self._decision_state,
        )
        return snapshot

    def _settle_after_input_unlocked(
        self,
        *,
        before: dict,
        button_name: str,
        minimum_frames: int,
        reason: str,
    ) -> dict:
        progress_predicate = self._progress_predicate(before, button_name)
        requires_progress = progress_predicate is not None
        last_signature = None
        stable_count = 0
        total_ticks = 0
        target_stable_frames = 3
        progress_seen = False
        max_extra_frames = self._max_extra_frames(before, button_name)

        while True:
            self.pyboy.tick()
            total_ticks += 1
            snapshot = self._build_snapshot_unlocked()
            signature = self._stability_signature(snapshot)
            if signature == last_signature:
                stable_count += 1
            else:
                stable_count = 1
                last_signature = signature

            if not requires_progress or progress_predicate(snapshot):
                progress_seen = True

            if total_ticks < minimum_frames:
                continue
            if stable_count >= target_stable_frames and progress_seen:
                return self._snapshot_unlocked(
                    suppress_derive=True,
                    extra_events=[
                        {
                            "frame": self.pyboy.frame_count,
                            "type": "input_settled",
                            "label": f"Settled after {reason}",
                        }
                    ],
                )
            if total_ticks >= minimum_frames + max_extra_frames:
                return self._snapshot_unlocked(
                    suppress_derive=True,
                    extra_events=[
                        {
                            "frame": self.pyboy.frame_count,
                            "type": "input_settle_timeout" if not progress_seen else "input_settled",
                            "label": (
                                f"Settle timeout after {reason}"
                                if not progress_seen
                                else f"Settled after {reason}"
                            ),
                        }
                    ],
                )

    def _stability_signature(self, snapshot: dict) -> tuple:
        return (
            snapshot["mode"],
            snapshot["map"]["id"],
            snapshot["map"]["x"],
            snapshot["map"]["y"],
            snapshot["menu"]["active"],
            snapshot["menu"]["selected_item_text"],
            tuple(snapshot["dialogue"]["visible_lines"]),
            snapshot["battle"]["in_battle"],
            snapshot["battle"]["opponent"],
        )

    def _build_action_trace(self, *, kind: str, payload: dict, before: dict, after: dict) -> dict:
        return {
            "timestamp": self._timestamp(),
            "kind": kind,
            "payload": payload,
            "before": self._trace_state(before),
            "after": self._trace_state(after),
            "verification": self._verify_action(before, after, payload),
        }

    def _verify_action(self, before: dict, after: dict, payload: dict) -> dict:
        button = payload.get("button")
        checks: list[dict] = []

        menu_opened = not before["menu"]["active"] and after["menu"]["active"]
        menu_closed = before["menu"]["active"] and not after["menu"]["active"]
        dialogue_changed = before["dialogue"]["visible_lines"] != after["dialogue"]["visible_lines"]
        moved = (before["map"]["x"], before["map"]["y"]) != (after["map"]["x"], after["map"]["y"])
        mode_changed = before["mode"] != after["mode"]
        map_changed = before["map"]["id"] != after["map"]["id"]

        if button == "start":
            checks.append({"name": "menu_toggled", "passed": menu_opened or menu_closed or mode_changed})
        elif button == "b":
            checks.append({"name": "menu_or_dialogue_closed", "passed": menu_closed or dialogue_changed or mode_changed})
        elif button == "a":
            checks.append({"name": "dialogue_or_selection_progressed", "passed": dialogue_changed or mode_changed or moved or map_changed})
        elif button in {"up", "down", "left", "right"}:
            checks.append({"name": "player_or_cursor_moved", "passed": moved or map_changed or dialogue_changed or before["menu"]["selected_item_text"] != after["menu"]["selected_item_text"]})
        else:
            checks.append({"name": "state_changed", "passed": mode_changed or moved or map_changed or dialogue_changed})

        return {
            "passed": all(check["passed"] for check in checks),
            "checks": checks,
        }

    def _progress_predicate(self, before: dict, button_name: str):
        if button_name == "a":
            if before["dialogue"]["active"] or before["mode"] == "menu_dialogue":
                return lambda snapshot: (
                    snapshot["dialogue"]["visible_lines"] != before["dialogue"]["visible_lines"]
                    or snapshot["menu"]["active"] != before["menu"]["active"]
                    or snapshot["menu"]["selected_item_text"] != before["menu"]["selected_item_text"]
                    or snapshot["mode"] != before["mode"]
                )
            if before["menu"]["active"]:
                return lambda snapshot: (
                    snapshot["menu"]["selected_item_text"] != before["menu"]["selected_item_text"]
                    or snapshot["mode"] != before["mode"]
                    or snapshot["dialogue"]["visible_lines"] != before["dialogue"]["visible_lines"]
                )
        if button_name == "start":
            return lambda snapshot: snapshot["menu"]["active"] != before["menu"]["active"] or snapshot["mode"] != before["mode"]
        if button_name == "b":
            return lambda snapshot: (
                snapshot["dialogue"]["visible_lines"] != before["dialogue"]["visible_lines"]
                or snapshot["menu"]["active"] != before["menu"]["active"]
                or snapshot["mode"] != before["mode"]
            )
        if button_name in {"up", "down", "left", "right"}:
            if before["menu"]["active"]:
                return lambda snapshot: (
                    snapshot["menu"]["selected_index"] != before["menu"]["selected_index"]
                    or snapshot["menu"]["selected_item_text"] != before["menu"]["selected_item_text"]
                    or snapshot["mode"] != before["mode"]
                )
            return lambda snapshot: (
                (snapshot["map"]["x"], snapshot["map"]["y"]) != (before["map"]["x"], before["map"]["y"])
                or snapshot["map"]["id"] != before["map"]["id"]
                or snapshot["mode"] != before["mode"]
                or snapshot["dialogue"]["visible_lines"] != before["dialogue"]["visible_lines"]
            )
        return None

    def _max_extra_frames(self, before: dict, button_name: str) -> int:
        if button_name == "a" and (before["dialogue"]["active"] or before["mode"] == "menu_dialogue"):
            return 300
        if button_name == "a" and before["menu"]["active"]:
            return 180
        if button_name == "start":
            return 180
        if button_name in {"up", "down", "left", "right"}:
            return 72
        return 24

    def _trace_state(self, snapshot: dict) -> dict:
        return {
            "frame": snapshot["frame"],
            "mode": snapshot["mode"],
            "map": snapshot["map"],
            "movement": snapshot.get("movement"),
            "navigation": snapshot.get("navigation"),
            "menu": {
                "active": snapshot["menu"]["active"],
                "selected_item_text": snapshot["menu"]["selected_item_text"],
            },
            "dialogue": {
                "active": snapshot["dialogue"]["active"],
                "visible_lines": snapshot["dialogue"]["visible_lines"],
            },
            "battle": snapshot["battle"],
        }

    def _record_trace(self, trace: dict) -> None:
        with self.trace_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace) + "\n")

    def _timestamp(self) -> str:
        return datetime.now(UTC).isoformat()

    def _state_path(self, slot: str) -> Path:
        return self.states_dir / f"{slot}.state"

    def _state_metadata_path(self, slot: str) -> Path:
        return self.states_dir / f"{slot}.json"

    def _load_state_metadata(self, state_path: Path) -> dict | None:
        metadata_path = state_path.with_suffix(".json")
        if not metadata_path.exists():
            return None
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    def _update_navigation_state(self, *, before: dict, after: dict, payload: dict) -> None:
        update_navigation_state(
            self._navigation_state,
            before=before,
            after=after,
            payload=payload,
        )

    def _update_progress_memory(self, *, before: dict, after: dict) -> None:
        update_progress_memory(
            self._progress_memory,
            before=before,
            after=after,
        )

    def _fresh_navigation_state(self) -> dict:
        return {
            "last_result": None,
            "last_transition": None,
            "consecutive_failures": 0,
            "blocked_directions": [],
        }

    def _reset_runtime_memory_unlocked(self) -> None:
        self._navigation_state = self._fresh_navigation_state()
        self._progress_memory = fresh_progress_memory()
        self._decision_state = self._fresh_decision_state()

    def frame_png(self) -> bytes:
        with self._lock:
            buffer = io.BytesIO()
            self.pyboy.screen.image.save(buffer, format="PNG")
            return buffer.getvalue()
