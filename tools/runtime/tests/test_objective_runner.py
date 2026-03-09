from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.runtime.objective_runner import ObjectiveRunner


def _field_snapshot(*, failures: int = 0, mode: str = "field") -> dict:
    return {
        "mode": mode,
        "map": {"id": 1, "x": 1, "y": 1, "script": 0},
        "dialogue": {"active": False, "visible_lines": []},
        "menu": {"active": False},
        "navigation": {
            "consecutive_failures": failures,
            "target_affordance": {"id": "object:0"},
        },
    }


class _DummyExecutor:
    def __init__(self, results):
        self._results = iter(results)

    def execute_decision(self, decision):
        return next(self._results)


class ObjectiveRunnerTests(unittest.TestCase):
    def _runner(self, results) -> ObjectiveRunner:
        runner = ObjectiveRunner.__new__(ObjectiveRunner)
        runner.core = type("Core", (), {"map_catalog": object()})()
        runner.memory = type("Memory", (), {"decision_state": {}, "last_observation": None})()
        runner.trace_recorder = type("Trace", (), {"trace_state": staticmethod(lambda snapshot: snapshot)})()
        runner.action_executor = _DummyExecutor(results)
        return runner

    def test_execute_field_window_stops_when_objective_missing(self) -> None:
        runner = self._runner([])
        runner._resolve_objective = lambda snapshot, objective_id: None

        with patch("tools.runtime.objective_runner.choose_field_action") as choose_field_action:
            steps = runner._execute_field_window(
                _field_snapshot(),
                strategy="objective",
                max_steps=3,
                objective_id="objective:1",
                affordance_id=None,
            )

        self.assertEqual(steps, [])
        choose_field_action.assert_not_called()

    def test_execute_field_window_stops_after_field_exit(self) -> None:
        runner = self._runner([_field_snapshot(mode="battle")])
        runner._resolve_objective = lambda snapshot, objective_id: {"id": objective_id}

        with patch("tools.runtime.objective_runner.choose_field_action", return_value={"type": "tick", "frames": 10}):
            steps = runner._execute_field_window(
                _field_snapshot(),
                strategy="objective",
                max_steps=3,
                objective_id="objective:1",
                affordance_id=None,
            )

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["after"]["mode"], "battle")

    def test_execute_field_window_stops_after_repeated_failures(self) -> None:
        runner = self._runner([_field_snapshot(failures=2)])
        runner._resolve_objective = lambda snapshot, objective_id: {"id": objective_id}

        with patch("tools.runtime.objective_runner.choose_field_action", return_value={"type": "tick", "frames": 10}):
            steps = runner._execute_field_window(
                _field_snapshot(),
                strategy="objective",
                max_steps=3,
                objective_id="objective:1",
                affordance_id=None,
            )

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["after"]["navigation"]["consecutive_failures"], 2)
