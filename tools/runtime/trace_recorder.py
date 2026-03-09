from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


class TraceRecorder:
    def __init__(self, *, trace_log_path: Path) -> None:
        self.trace_log_path = trace_log_path

    def recent_traces(self, *, rom_name: str, limit: int = 50) -> dict[str, Any]:
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
            "rom": rom_name,
            "trace_log_path": str(self.trace_log_path),
            "traces": [json.loads(line) for line in lines],
        }

    def recent_trace_entries(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.recent_traces(rom_name="", limit=limit)["traces"]

    def build_action_trace(self, *, kind: str, payload: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp(),
            "kind": kind,
            "payload": payload,
            "before": self.trace_state(before),
            "after": self.trace_state(after),
            "verification": self.verify_action(before, after, payload),
        }

    def verify_action(self, before: dict[str, Any], after: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        button = payload.get("button")
        checks: list[dict[str, Any]] = []

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
            checks.append(
                {
                    "name": "player_or_cursor_moved",
                    "passed": moved
                    or map_changed
                    or dialogue_changed
                    or before["menu"]["selected_item_text"] != after["menu"]["selected_item_text"],
                }
            )
        else:
            checks.append({"name": "state_changed", "passed": mode_changed or moved or map_changed or dialogue_changed})

        return {
            "passed": all(check["passed"] for check in checks),
            "checks": checks,
        }

    def trace_state(self, snapshot: dict[str, Any]) -> dict[str, Any]:
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

    def record_trace(self, trace: dict[str, Any]) -> None:
        with self.trace_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace) + "\n")

    @staticmethod
    def timestamp() -> str:
        return datetime.now(UTC).isoformat()
