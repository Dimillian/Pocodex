from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any


def fresh_progress_memory() -> dict[str, Any]:
    return {
        "visited_maps": set(),
        "affordances": {},
        "recent_targets": deque(maxlen=12),
        "recent_progress": deque(maxlen=20),
    }


def capture_progress_memory(state: dict[str, Any]) -> dict[str, Any]:
    snapshot = deepcopy(state)
    snapshot["visited_maps"] = set(state.get("visited_maps", set()))
    snapshot["recent_targets"] = deque(state.get("recent_targets", ()), maxlen=12)
    snapshot["recent_progress"] = deque(state.get("recent_progress", ()), maxlen=20)
    return snapshot


def summarize_progress_memory(state: dict[str, Any]) -> dict[str, Any]:
    affordances = state.get("affordances", {})
    ranked = sorted(
        affordances.values(),
        key=lambda entry: (
            entry.get("progress_count", 0),
            -entry.get("noop_count", 0),
            -entry.get("blocked_count", 0),
            entry.get("last_frame", 0),
        ),
        reverse=True,
    )
    return {
        "visited_maps": sorted(state.get("visited_maps", set())),
        "recent_targets": list(state.get("recent_targets", ())),
        "recent_progress": list(state.get("recent_progress", ())),
        "top_affordances": ranked[:8],
    }


def affordance_memory_key(snapshot: dict[str, Any], affordance: dict[str, Any]) -> str:
    const_name = snapshot["map"].get("const_name") or f"map:{snapshot['map']['id']}"
    return f"{const_name}:{affordance['id']}"


def update_progress_memory(state: dict[str, Any], *, before: dict[str, Any], after: dict[str, Any]) -> None:
    _remember_map(state, before)
    _remember_map(state, after)

    target = (before.get("navigation") or {}).get("target_affordance")
    if not target:
        objective = (before.get("navigation") or {}).get("objective")
        if objective and objective.get("affordance_id"):
            target = {
                "id": objective["affordance_id"],
                "kind": objective["kind"],
                "label": objective["label"],
                **{key: value for key, value in objective.items() if key not in {"affordance_id", "milestone", "label"}},
            }
    if not target or target.get("kind") == "script_progress":
        return

    key = affordance_memory_key(before, target)
    stats = state["affordances"].setdefault(
        key,
        {
            "key": key,
            "map": before["map"].get("const_name") or before["map"]["id"],
            "affordance_id": target["id"],
            "kind": target["kind"],
            "label": target.get("label"),
            "selected_count": 0,
            "progress_count": 0,
            "noop_count": 0,
            "blocked_count": 0,
            "last_outcome": None,
            "last_frame": None,
        },
    )
    stats["selected_count"] += 1
    stats["last_frame"] = after["frame"]
    state["recent_targets"].append(key)

    before_distance = affordance_distance(before, target)
    after_distance = affordance_distance(after, target)
    outcome = "noop"
    if _made_progress(before, after, before_distance, after_distance):
        outcome = "progress"
        stats["progress_count"] += 1
        state["recent_progress"].append(f"{key}:progress")
    elif after_distance is not None and before_distance is not None and after_distance > before_distance:
        outcome = "regressed"
        stats["blocked_count"] += 1
    else:
        same_position = (before["map"]["x"], before["map"]["y"]) == (after["map"]["x"], after["map"]["y"])
        if same_position:
            stats["blocked_count"] += 1
            outcome = "blocked"
        else:
            stats["noop_count"] += 1

    stats["last_outcome"] = outcome


def affordance_distance(snapshot: dict[str, Any], affordance: dict[str, Any]) -> int | None:
    if affordance["kind"] == "trigger_region":
        axis = affordance.get("axis")
        value = affordance.get("value")
        if axis == "x" and value is not None:
            return abs(snapshot["map"]["x"] - value)
        if axis == "y" and value is not None:
            return abs(snapshot["map"]["y"] - value)
        return None

    target = affordance.get("target")
    if not target:
        return None
    return abs(snapshot["map"]["x"] - target["x"]) + abs(snapshot["map"]["y"] - target["y"])


def _remember_map(state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    map_name = snapshot["map"].get("const_name")
    if map_name:
        state["visited_maps"].add(map_name)


def _made_progress(
    before: dict[str, Any],
    after: dict[str, Any],
    before_distance: int | None,
    after_distance: int | None,
) -> bool:
    if before["map"]["id"] != after["map"]["id"]:
        return True
    if before["map"]["script"] != after["map"]["script"]:
        return True
    if before["mode"] != after["mode"]:
        return True
    if before["dialogue"]["visible_lines"] != after["dialogue"]["visible_lines"]:
        return True
    if before["menu"]["selected_item_text"] != after["menu"]["selected_item_text"]:
        return True
    if before["battle"] != after["battle"]:
        return True
    if before_distance is not None and after_distance is not None and after_distance < before_distance:
        return True
    return False
