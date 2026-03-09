from __future__ import annotations

import unittest

from tools.runtime.action_executor import ActionExecutor


def _before_snapshot() -> dict:
    return {
        "mode": "field",
        "map": {"id": 1, "x": 1, "y": 1},
        "menu": {"active": False, "selected_index": None, "selected_item_text": None},
        "dialogue": {"active": False, "visible_lines": []},
    }


class ActionExecutorTests(unittest.TestCase):
    def test_progress_predicate_for_start_tracks_menu_toggle(self) -> None:
        before = _before_snapshot()
        predicate = ActionExecutor._progress_predicate(before, "start")

        self.assertIsNotNone(predicate)
        self.assertTrue(predicate({**before, "menu": {"active": True}}))

    def test_progress_predicate_for_b_tracks_dialogue_change(self) -> None:
        before = _before_snapshot()
        before["dialogue"] = {"active": True, "visible_lines": ["HELLO"]}
        predicate = ActionExecutor._progress_predicate(before, "b")

        self.assertIsNotNone(predicate)
        self.assertTrue(predicate({**before, "dialogue": {"active": False, "visible_lines": []}}))

    def test_progress_predicate_for_menu_movement_tracks_cursor_change(self) -> None:
        before = _before_snapshot()
        before["menu"] = {"active": True, "selected_index": 0, "selected_item_text": "YES"}
        predicate = ActionExecutor._progress_predicate(before, "down")

        self.assertIsNotNone(predicate)
        self.assertTrue(predicate({**before, "menu": {"active": True, "selected_index": 1, "selected_item_text": "NO"}}))

    def test_progress_predicate_for_field_movement_tracks_coordinate_change(self) -> None:
        before = _before_snapshot()
        predicate = ActionExecutor._progress_predicate(before, "right")

        self.assertIsNotNone(predicate)
        self.assertTrue(predicate({**before, "map": {"id": 1, "x": 2, "y": 1}}))
