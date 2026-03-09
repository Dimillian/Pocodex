from __future__ import annotations

import unittest
from pathlib import Path

from tools.runtime.trace_recorder import TraceRecorder


def _snapshot() -> dict:
    return {
        "frame": 1,
        "mode": "field",
        "map": {"id": 1, "x": 1, "y": 1},
        "movement": None,
        "navigation": None,
        "menu": {"active": False, "selected_item_text": None},
        "dialogue": {"active": False, "visible_lines": []},
        "battle": {"in_battle": False, "opponent": 0},
    }


class TraceRecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recorder = TraceRecorder(trace_log_path=Path("/tmp/test-trace-recorder.jsonl"))

    def test_verify_action_accepts_menu_toggle_for_start(self) -> None:
        before = _snapshot()
        after = _snapshot()
        after["menu"]["active"] = True

        verification = self.recorder.verify_action(before, after, {"button": "start"})

        self.assertTrue(verification["passed"])

    def test_verify_action_accepts_dialogue_progress_for_a(self) -> None:
        before = _snapshot()
        before["dialogue"]["visible_lines"] = ["HELLO"]
        after = _snapshot()
        after["dialogue"]["visible_lines"] = ["BYE"]

        verification = self.recorder.verify_action(before, after, {"button": "a"})

        self.assertTrue(verification["passed"])

    def test_verify_action_requires_movement_or_cursor_change_for_direction(self) -> None:
        before = _snapshot()
        after = _snapshot()
        after["map"]["x"] = 2

        verification = self.recorder.verify_action(before, after, {"button": "right"})

        self.assertTrue(verification["passed"])
