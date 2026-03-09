from __future__ import annotations

from typing import Any

from .objective_primitives import objective_affordance_id, objective_distance
from .progress_memory import progress_state_signature


def fresh_objective_memory() -> dict[str, Any]:
    return {
        "active_objective_id": None,
        "active_affordance_id": None,
        "pending_interaction_resolution": None,
        "objective_stats": {},
        "objective_history": [],
        "objective_progress": [],
        "invalidated_objectives": [],
        "recent_map_history": [],
    }


def record_objective_selection(
    decision_state: dict[str, Any],
    *,
    objective: dict[str, Any],
    frame: int,
) -> None:
    memory = decision_state.setdefault("objective", fresh_objective_memory())
    memory["pending_interaction_resolution"] = None
    if memory.get("active_objective_id") != objective["id"]:
        memory.setdefault("objective_history", []).append(
            {
                "frame": frame,
                "id": objective["id"],
                "kind": objective["kind"],
                "label": objective["label"],
                "event": "selected",
            }
        )
    memory["active_objective_id"] = objective["id"]
    memory["active_affordance_id"] = next(iter(objective.get("target_affordance_ids") or []), None)
    stats = memory.setdefault("objective_stats", {}).setdefault(objective["id"], _fresh_objective_stats())
    stats["attempt_count"] += 1
    stats["confidence"] = objective.get("confidence", stats["confidence"])
    stats["evidence"] = list(objective.get("evidence") or [])
    _truncate_memory(memory)


def update_objective_memory(
    decision_state: dict[str, Any],
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    objective: dict[str, Any],
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    memory = decision_state.setdefault("objective", fresh_objective_memory())
    stats = memory.setdefault("objective_stats", {}).setdefault(objective["id"], _fresh_objective_stats())
    progress = evaluate_objective_window(before, after, objective)
    _arm_pending_interaction_resolution(memory, before=before, after=after, objective=objective)
    entry = {
        "frame": after["frame"],
        "id": objective["id"],
        "kind": objective["kind"],
        "label": objective["label"],
        "progress_signals": progress["progress_signals"],
        "loop_signals": progress["loop_signals"],
        "success": progress["success"],
        "partial": progress["partial"],
        "steps": len(steps),
    }
    memory.setdefault("objective_progress", []).append(entry)
    if progress["success"]:
        stats["confidence"] = min(0.99, max(stats.get("confidence", 0.5), objective.get("confidence", 0.5)) + 0.12)
        stats["last_progress_frame"] = after["frame"]
        stats["recent_failures"] = 0
        memory["active_objective_id"] = None
        memory["active_affordance_id"] = None
        memory.setdefault("objective_history", []).append({**entry, "event": "progressed"})
    elif progress["partial"]:
        stats["confidence"] = min(0.95, max(stats.get("confidence", 0.4), objective.get("confidence", 0.4)) + 0.04)
        stats["recent_failures"] = 0
        memory["active_objective_id"] = objective["id"]
        memory["active_affordance_id"] = next(iter(objective.get("target_affordance_ids") or []), None)
        memory.setdefault("objective_history", []).append({**entry, "event": "partial_progress"})
    else:
        stats["recent_failures"] += 1
        stats["confidence"] = max(0.05, min(stats.get("confidence", 0.5), objective.get("confidence", 0.5)) - 0.12)
        memory.setdefault("objective_history", []).append({**entry, "event": "failed_window"})
        if stats["recent_failures"] >= 2 or progress["loop_signals"]:
            invalidate_objective(
                decision_state,
                objective_id=objective["id"],
                reason=", ".join(progress["loop_signals"] or ["window produced no progress"]),
                frame=after["frame"],
            )
        else:
            memory["active_objective_id"] = objective["id"]
            memory["active_affordance_id"] = next(iter(objective.get("target_affordance_ids") or []), None)
    _truncate_memory(memory)
    return entry


def reconcile_objective_interaction_resolution(
    decision_state: dict[str, Any],
    *,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any] | None:
    memory = decision_state.setdefault("objective", fresh_objective_memory())
    pending = memory.get("pending_interaction_resolution")
    if not pending:
        return None
    if before.get("mode") == "field" or after.get("mode") != "field":
        return None

    memory["pending_interaction_resolution"] = None
    if progress_state_signature(after) != pending.get("field_signature"):
        return {"consumed": False, "objective_id": pending.get("objective_id")}

    objective_id = pending.get("objective_id")
    if objective_id:
        invalidate_objective(
            decision_state,
            objective_id=objective_id,
            reason="interaction returned to same field state",
            frame=after["frame"],
        )
    return {"consumed": True, "objective_id": objective_id}


def invalidate_objective(
    decision_state: dict[str, Any],
    *,
    objective_id: str,
    reason: str,
    frame: int,
) -> None:
    memory = decision_state.setdefault("objective", fresh_objective_memory())
    memory["active_objective_id"] = None
    pending_resolution = memory.get("pending_interaction_resolution") or {}
    if pending_resolution.get("objective_id") == objective_id:
        memory["pending_interaction_resolution"] = None
    if memory.get("active_affordance_id") == objective_affordance_id(objective_id):
        memory["active_affordance_id"] = None
    memory.setdefault("invalidated_objectives", []).append(
        {
            "frame": frame,
            "id": objective_id,
            "reason": reason,
        }
    )
    stats = memory.setdefault("objective_stats", {}).setdefault(objective_id, _fresh_objective_stats())
    stats["recent_failures"] = max(stats.get("recent_failures", 0), 2)
    stats["confidence"] = min(stats.get("confidence", 0.4), 0.2)
    _truncate_memory(memory)


def record_map_history(decision_state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    memory = decision_state.setdefault("objective", fresh_objective_memory())
    map_name = snapshot["map"].get("const_name") or snapshot["map"].get("name") or f"map:{snapshot['map']['id']}"
    history = memory.setdefault("recent_map_history", [])
    if history and history[-1].get("map") == map_name and history[-1].get("script") == snapshot["map"].get("script"):
        return
    history.append(
        {
            "frame": snapshot["frame"],
            "map": map_name,
            "script": snapshot["map"].get("script"),
            "x": snapshot["map"].get("x"),
            "y": snapshot["map"].get("y"),
        }
    )
    _truncate_memory(memory)


def evaluate_objective_window(before: dict[str, Any], after: dict[str, Any], objective: dict[str, Any]) -> dict[str, Any]:
    progress_signals: list[str] = []
    loop_signals: list[str] = []
    before_distance = objective_distance(before, objective)
    after_distance = objective_distance(after, objective)

    if before["map"]["id"] != after["map"]["id"]:
        progress_signals.append("map_changed")
    if before["map"].get("script") != after["map"].get("script"):
        progress_signals.append("script_changed")
    if before["mode"] != after["mode"]:
        progress_signals.append("mode_changed")
    if (before.get("interaction") or {}).get("type") != (after.get("interaction") or {}).get("type"):
        progress_signals.append("interaction_changed")
    if before["dialogue"].get("visible_lines") != after["dialogue"].get("visible_lines"):
        progress_signals.append("dialogue_progressed")
    if before["menu"].get("selected_item_text") != after["menu"].get("selected_item_text"):
        progress_signals.append("menu_state_changed")
    if before_distance is not None and after_distance is not None and after_distance < before_distance:
        progress_signals.append("objective_distance_decreased")
    if objective.get("kind") == "continue_script" and (progress_signals or after["dialogue"].get("active")):
        progress_signals.append("script_continued")

    same_position = (
        before["map"]["id"] == after["map"]["id"]
        and before["map"]["x"] == after["map"]["x"]
        and before["map"]["y"] == after["map"]["y"]
    )
    same_signature = (
        before["map"].get("script") == after["map"].get("script")
        and before["mode"] == after["mode"]
        and before["dialogue"].get("visible_lines") == after["dialogue"].get("visible_lines")
    )
    if same_position and same_signature and not progress_signals:
        loop_signals.append("repeated_noop_window")
    if after.get("navigation", {}).get("consecutive_failures", 0) >= 2:
        loop_signals.append("blocked_movement_window")

    strong_progress = any(
        signal in progress_signals
        for signal in ("map_changed", "script_changed", "interaction_changed", "dialogue_progressed")
    )
    partial_progress = "objective_distance_decreased" in progress_signals and not strong_progress
    return {
        "progress_signals": progress_signals,
        "loop_signals": loop_signals,
        "success": strong_progress,
        "partial": partial_progress,
    }


def _arm_pending_interaction_resolution(
    memory: dict[str, Any],
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    objective: dict[str, Any],
) -> None:
    if objective.get("kind") not in {"interact_entity", "inspect_interactable"}:
        memory["pending_interaction_resolution"] = None
        return
    if before.get("mode") != "field" or after.get("mode") == "field":
        return
    memory["pending_interaction_resolution"] = {
        "objective_id": objective["id"],
        "affordance_id": next(iter(objective.get("target_affordance_ids") or []), None),
        "field_signature": progress_state_signature(before),
    }


def _fresh_objective_stats() -> dict[str, Any]:
    return {
        "attempt_count": 0,
        "confidence": 0.5,
        "evidence": [],
        "last_progress_frame": None,
        "recent_failures": 0,
    }


def _truncate_memory(memory: dict[str, Any]) -> None:
    for key, limit in (
        ("objective_history", 24),
        ("objective_progress", 24),
        ("invalidated_objectives", 16),
        ("recent_map_history", 16),
    ):
        values = memory.setdefault(key, [])
        if len(values) > limit:
            del values[: len(values) - limit]
