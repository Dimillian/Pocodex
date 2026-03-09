from __future__ import annotations

from typing import Any


GENERIC_OBJECTIVE_KINDS = {
    "reach_exit",
    "approach_entity",
    "interact_entity",
    "reach_region",
    "continue_script",
    "inspect_interactable",
    "stabilize_transition",
}


def objective_distance(snapshot: dict[str, Any], objective: dict[str, Any]) -> int | None:
    target = objective.get("navigation_target") or objective
    if target.get("kind") == "trigger_region":
        axis = target.get("axis")
        value = target.get("value")
        if axis == "x" and value is not None:
            return abs(snapshot["map"]["x"] - value)
        if axis == "y" and value is not None:
            return abs(snapshot["map"]["y"] - value)
        return None
    if target.get("kind") == "object":
        approach_tiles = target.get("approach_tiles") or []
        current = (snapshot["map"]["x"], snapshot["map"]["y"])
        if approach_tiles:
            return min(abs(current[0] - tile["x"]) + abs(current[1] - tile["y"]) for tile in approach_tiles)
    point = target.get("target")
    if not point:
        return None
    return abs(snapshot["map"]["x"] - point["x"]) + abs(snapshot["map"]["y"] - point["y"])


def candidate(
    *,
    kind: str,
    label: str,
    navigation_target: dict[str, Any] | None,
    confidence: float,
    evidence: list[str],
    expected_progress_signals: list[str],
    failure_signals: list[str],
    active_id: str | None,
    active_affordance_id: str | None,
    phase: str = "approach",
) -> dict[str, Any]:
    target_affordance_ids = []
    objective_id = kind
    if navigation_target is not None:
        target_affordance_ids = [navigation_target["id"]]
        objective_id = f"{kind}:{navigation_target['id']}"
    target_affordance_id = target_affordance_ids[0] if target_affordance_ids else None
    is_active = active_id == objective_id or (
        active_affordance_id is not None and active_affordance_id == target_affordance_id
    )
    return {
        "id": objective_id,
        "kind": kind,
        "label": label,
        "target_affordance_ids": target_affordance_ids,
        "navigation_target": navigation_target,
        "confidence": round(confidence, 3),
        "evidence": evidence,
        "expected_progress_signals": expected_progress_signals,
        "failure_signals": failure_signals,
        "phase": phase,
        "status": "active" if is_active else "candidate",
    }


def objective_kind_for_affordance(affordance: dict[str, Any]) -> str | None:
    if affordance["kind"] == "warp":
        return "reach_exit"
    if affordance["kind"] == "trigger_region":
        return "reach_region"
    if affordance["kind"] == "object":
        return "interact_entity" if is_interaction_ready_affordance(affordance) else "approach_entity"
    if affordance["kind"] == "bg_event":
        return "inspect_interactable"
    return None


def objective_phase(snapshot: dict[str, Any], affordance: dict[str, Any]) -> str:
    if affordance["kind"] in {"object", "bg_event"} and is_interaction_ready_affordance(affordance):
        return "interaction_ready"
    if affordance["kind"] == "trigger_region" and objective_distance(snapshot, {"kind": affordance["kind"], **affordance}) == 0:
        return "resolving"
    return "approach"


def is_interaction_ready_affordance(affordance: dict[str, Any]) -> bool:
    reachability = affordance.get("reachability") or {}
    if (reachability.get("path_length") or 0) == 0:
        return True
    return (affordance.get("distance") or 99) <= 1


def has_interaction_ready_candidate(candidates: list[dict[str, Any]]) -> bool:
    return any(candidate.get("phase") == "interaction_ready" for candidate in candidates)


def objective_affordance_id(objective_id: str | None) -> str | None:
    if not objective_id or ":" not in objective_id:
        return None
    parts = objective_id.split(":", 2)
    if len(parts) < 3:
        return None
    return ":".join(parts[1:])


def objective_kind_from_id(objective_id: str | None) -> str | None:
    if not objective_id or ":" not in objective_id:
        return objective_id
    return objective_id.split(":", 1)[0]


def objective_label(objective_kind: str, affordance: dict[str, Any]) -> str:
    if objective_kind == "reach_exit":
        return f"Reach the nearby exit toward {affordance.get('target_name') or 'the next map'}."
    if objective_kind == "reach_region":
        return "Reach the nearby boundary or trigger region."
    if objective_kind == "interact_entity":
        return "Interact with the nearby entity to reveal what it does."
    if objective_kind == "approach_entity":
        return "Approach the nearby entity that looks relevant."
    if objective_kind == "inspect_interactable":
        return "Inspect the nearby interactable or sign for information."
    return affordance["label"]


def expected_progress_signals(objective_kind: str) -> list[str]:
    if objective_kind == "reach_exit":
        return ["map_changed", "mode_changed"]
    if objective_kind == "reach_region":
        return ["script_changed", "dialogue_progressed", "interaction_changed"]
    if objective_kind in {"approach_entity", "interact_entity"}:
        return ["objective_distance_decreased", "dialogue_progressed", "interaction_changed"]
    if objective_kind == "inspect_interactable":
        return ["dialogue_progressed", "interaction_changed"]
    if objective_kind == "stabilize_transition":
        return ["mode_changed", "dialogue_progressed"]
    return ["dialogue_progressed", "interaction_changed"]
