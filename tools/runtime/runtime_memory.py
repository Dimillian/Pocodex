from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .objective_memory import fresh_objective_memory
from .progress_memory import capture_progress_memory, fresh_progress_memory

RuntimeMemoryState = tuple[dict[str, Any], dict[str, Any], dict[str, Any]]


def fresh_decision_state() -> dict[str, Any]:
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


def fresh_navigation_state() -> dict[str, Any]:
    return {
        "last_result": None,
        "last_transition": None,
        "consecutive_failures": 0,
        "blocked_directions": [],
    }


def decision_preferences(decision_state: dict[str, Any]) -> dict[str, str]:
    return decision_state.setdefault("preferences", {})


def decision_preference(decision_state: dict[str, Any], key: str, default: str | None = None) -> str | None:
    return decision_preferences(decision_state).get(key, default)


def decision_flag(decision_state: dict[str, Any], key: str) -> bool:
    return bool((decision_state.get("flags") or {}).get(key))


def set_decision_flag(decision_state: dict[str, Any], key: str, value: bool) -> None:
    decision_state.setdefault("flags", {})[key] = value


def field_move_index(decision_state: dict[str, Any]) -> int:
    return int((decision_state.get("exploration") or {}).get("field_move_index", 0))


def advance_field_move_index(decision_state: dict[str, Any]) -> None:
    exploration = decision_state.setdefault("exploration", {})
    exploration["field_move_index"] = (field_move_index(decision_state) + 1) % 4


@dataclass
class RuntimeMemory:
    last_observation: dict[str, Any] | None = None
    event_log: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=50))
    decision_state: dict[str, Any] = field(default_factory=fresh_decision_state)
    navigation_state: dict[str, Any] = field(default_factory=fresh_navigation_state)
    progress_memory: dict[str, Any] = field(default_factory=fresh_progress_memory)

    def reset_runtime_memory(self) -> None:
        self.navigation_state = fresh_navigation_state()
        self.progress_memory = fresh_progress_memory()
        self.decision_state = fresh_decision_state()

    def capture_runtime_state(self) -> RuntimeMemoryState:
        return (
            deepcopy(self.navigation_state),
            deepcopy(self.decision_state),
            capture_progress_memory(self.progress_memory),
        )

    def restore_runtime_state(self, state: RuntimeMemoryState) -> None:
        navigation_state, decision_state, progress_memory = state
        self.navigation_state = deepcopy(navigation_state)
        self.decision_state = deepcopy(decision_state)
        self.progress_memory = capture_progress_memory(progress_memory)
