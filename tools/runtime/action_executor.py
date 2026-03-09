from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .controls import normalize_button
from .interaction_policy import update_decision_state
from .navigator import update_navigation_state
from .progress_memory import update_progress_memory
from .runtime_core import RuntimeCore
from .runtime_memory import RuntimeMemory
from .snapshot_service import SnapshotService
from .trace_recorder import TraceRecorder


class ActionExecutor:
    def __init__(
        self,
        *,
        core: RuntimeCore,
        memory: RuntimeMemory,
        snapshot_service: SnapshotService,
        trace_recorder: TraceRecorder,
    ) -> None:
        self.core = core
        self.memory = memory
        self.snapshot_service = snapshot_service
        self.trace_recorder = trace_recorder

    def tick(self, frames: int = 1) -> dict[str, Any]:
        with self.core.lock:
            for _ in range(frames):
                self.core.pyboy.tick()
            return self.snapshot_service.snapshot_unlocked()

    def tap(
        self,
        button: str,
        hold_frames: int = 2,
        settle_frames: int = 2,
        *,
        record_trace: bool = True,
    ) -> dict[str, Any]:
        return self.press(button, hold_frames=hold_frames, settle_frames=settle_frames, record_trace=record_trace)

    def press(
        self,
        button: str,
        hold_frames: int = 2,
        settle_frames: int = 2,
        *,
        record_trace: bool = True,
    ) -> dict[str, Any]:
        button_name = normalize_button(button)
        with self.core.lock:
            before = self.snapshot_service.build_snapshot_body_unlocked()
            self.core.pyboy.button_press(button_name)
            for _ in range(hold_frames):
                self.core.pyboy.tick()
            self.core.pyboy.button_release(button_name)
            after = self._settle_after_input_unlocked(
                before=before,
                button_name=button_name,
                minimum_frames=settle_frames,
                reason=f"press:{button_name}",
            )
            self._update_runtime_memory(before=before, after=after, payload={"button": button_name})
            if record_trace:
                trace = self.trace_recorder.build_action_trace(
                    kind="press",
                    payload={
                        "button": button_name,
                        "hold_frames": hold_frames,
                        "settle_frames": settle_frames,
                    },
                    before=before,
                    after=after,
                )
                self.trace_recorder.record_trace(trace)
                after["action_trace"] = trace
            return after

    def sequence(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        traces: list[dict[str, Any]] = []
        with self.core.lock:
            before_sequence = self.snapshot_service.build_snapshot_body_unlocked()
            for index, step in enumerate(steps):
                button_name = normalize_button(step["button"])
                hold_frames = step.get("hold_frames", 2)
                settle_frames = step.get("settle_frames", 2)
                before = self.snapshot_service.build_snapshot_body_unlocked()
                self.core.pyboy.button_press(button_name)
                for _ in range(hold_frames):
                    self.core.pyboy.tick()
                self.core.pyboy.button_release(button_name)
                after = self._settle_after_input_unlocked(
                    before=before,
                    button_name=button_name,
                    minimum_frames=settle_frames,
                    reason=f"sequence_step:{index}:{button_name}",
                )
                self._update_runtime_memory(before=before, after=after, payload={"button": button_name})
                trace = self.trace_recorder.build_action_trace(
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
                self.trace_recorder.record_trace(trace)
            final_snapshot = self.snapshot_service.snapshot_unlocked(
                suppress_derive=True,
                extra_events=[
                    {
                        "frame": self.core.frame_count,
                        "type": "sequence_completed",
                        "label": f"Sequence completed ({len(steps)} step{'s' if len(steps) != 1 else ''})",
                    }
                ],
            )
            sequence_trace = {
                "timestamp": self.trace_recorder.timestamp(),
                "kind": "sequence",
                "steps": len(steps),
                "before": self.trace_recorder.trace_state(before_sequence),
                "after": self.trace_recorder.trace_state(final_snapshot),
                "step_results": traces,
            }
            self.trace_recorder.record_trace(sequence_trace)
            final_snapshot["sequence_trace"] = sequence_trace
            return final_snapshot

    def run_routine(self, name: str) -> dict[str, Any]:
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

    def execute_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision["type"] == "routine":
            return self.run_routine(decision["name"])
        if decision["type"] == "action":
            return self.tap(decision["button"])
        if decision["type"] == "tick":
            return self.tick(decision["frames"])
        raise ValueError(f"Unsupported decision type '{decision['type']}'")

    def list_states(self) -> dict[str, Any]:
        with self.core.lock:
            states = []
            for path in sorted(self.core.states_dir.glob("*.state")):
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
                "rom": self.core.rom_name,
                "states_dir": str(self.core.states_dir),
                "states": states,
            }

    def save_state(self, slot: str = "quick") -> dict[str, Any]:
        path = self._state_path(slot)
        with self.core.lock:
            snapshot_before_save = self.snapshot_service.build_snapshot_body_unlocked()
            with path.open("wb") as handle:
                self.core.pyboy.save_state(handle)
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
            snapshot = self.snapshot_service.snapshot_unlocked(
                suppress_derive=True,
                extra_events=[
                    {
                        "frame": self.core.frame_count,
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

    def load_state(self, slot: str = "quick") -> dict[str, Any]:
        path = self._state_path(slot)
        if not path.exists():
            raise FileNotFoundError(f"Missing save state slot '{slot}' at {path}")
        with self.core.lock:
            with path.open("rb") as handle:
                self.core.pyboy.load_state(handle)
            self.core.invalidate_frame_cache()
            self.memory.reset_runtime_memory()
            metadata = self._load_state_metadata(path)
            snapshot = self.snapshot_service.snapshot_unlocked(
                suppress_derive=True,
                extra_events=[
                    {
                        "frame": self.core.frame_count,
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

    def reset_runtime_memory(self) -> dict[str, Any]:
        with self.core.lock:
            self.memory.reset_runtime_memory()
            snapshot = self.snapshot_service.snapshot_unlocked(
                suppress_derive=True,
                extra_events=[
                    {
                        "frame": self.core.frame_count,
                        "type": "runtime_memory_reset",
                        "label": "Runtime memory reset",
                    }
                ],
            )
            snapshot["runtime_memory"] = {
                "action": "reset",
            }
            return snapshot

    def _update_runtime_memory(self, *, before: dict[str, Any], after: dict[str, Any], payload: dict[str, Any]) -> None:
        update_navigation_state(
            self.memory.navigation_state,
            before=before,
            after=after,
            payload=payload,
        )
        self.snapshot_service.enrich_navigation(after)
        update_progress_memory(
            self.memory.progress_memory,
            before=before,
            after=after,
        )
        update_decision_state(self.memory.decision_state, after)

    def _settle_after_input_unlocked(
        self,
        *,
        before: dict[str, Any],
        button_name: str,
        minimum_frames: int,
        reason: str,
    ) -> dict[str, Any]:
        progress_predicate = self._progress_predicate(before, button_name)
        requires_progress = progress_predicate is not None
        last_signature = None
        stable_count = 0
        total_ticks = 0
        target_stable_frames = 3
        progress_seen = False
        max_extra_frames = self._max_extra_frames(before, button_name)

        while True:
            self.core.pyboy.tick()
            total_ticks += 1
            snapshot = self.snapshot_service.build_snapshot_body_unlocked()
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
                return self.snapshot_service.snapshot_unlocked(
                    suppress_derive=True,
                    extra_events=[
                        {
                            "frame": self.core.frame_count,
                            "type": "input_settled",
                            "label": f"Settled after {reason}",
                        }
                    ],
                )
            if total_ticks >= minimum_frames + max_extra_frames:
                return self.snapshot_service.snapshot_unlocked(
                    suppress_derive=True,
                    extra_events=[
                        {
                            "frame": self.core.frame_count,
                            "type": "input_settle_timeout" if not progress_seen else "input_settled",
                            "label": (
                                f"Settle timeout after {reason}"
                                if not progress_seen
                                else f"Settled after {reason}"
                            ),
                        }
                    ],
                )

    @staticmethod
    def _stability_signature(snapshot: dict[str, Any]) -> tuple[Any, ...]:
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

    @staticmethod
    def _progress_predicate(before: dict[str, Any], button_name: str):
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

    @staticmethod
    def _max_extra_frames(before: dict[str, Any], button_name: str) -> int:
        if button_name == "a" and (before["dialogue"]["active"] or before["mode"] == "menu_dialogue"):
            return 300
        if button_name == "a" and before["menu"]["active"]:
            return 180
        if button_name == "start":
            return 180
        if button_name in {"up", "down", "left", "right"}:
            return 72
        return 24

    def _state_path(self, slot: str) -> Path:
        return self.core.states_dir / f"{slot}.state"

    def _state_metadata_path(self, slot: str) -> Path:
        return self.core.states_dir / f"{slot}.json"

    def _load_state_metadata(self, state_path: Path) -> dict[str, Any] | None:
        metadata_path = state_path.with_suffix(".json")
        if not metadata_path.exists():
            return None
        return json.loads(metadata_path.read_text(encoding="utf-8"))
