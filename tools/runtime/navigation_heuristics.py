from __future__ import annotations

from typing import Any

from .progress_memory import affordance_memory_key, progress_state_signature


LOCAL_INTERACTION_KINDS = {"object", "bg_event"}


def should_prefer_exit_warp(
    snapshot: dict[str, Any],
    *,
    affordances: list[dict[str, Any]],
    progress_memory: dict[str, Any],
) -> tuple[bool, list[str]]:
    if snapshot["mode"] != "field":
        return False, []
    if snapshot["dialogue"]["active"] or snapshot["menu"]["active"] or snapshot["battle"]["in_battle"]:
        return False, []

    reachable_warps = [
        affordance
        for affordance in affordances
        if affordance.get("kind") == "warp" and (affordance.get("reachability") or {}).get("reachable", True)
    ]
    if not reachable_warps:
        return False, []

    current_signature = progress_state_signature(snapshot)
    nonwarp_affordances = [affordance for affordance in affordances if affordance.get("kind") != "warp"]
    exhausted_nonwarp = sum(
        1
        for affordance in nonwarp_affordances
        if affordance_looks_exhausted(snapshot, affordance, progress_memory, current_signature)
    )
    if nonwarp_affordances and exhausted_nonwarp == len(nonwarp_affordances):
        return True, ["all nearby non-exit affordances look exhausted"]

    local_interactions = [
        affordance for affordance in affordances if affordance.get("kind") in LOCAL_INTERACTION_KINDS
    ]
    if not _is_small_interior(snapshot, local_interactions):
        return False, []

    exhausted_local = sum(
        1
        for affordance in local_interactions
        if affordance_looks_exhausted(snapshot, affordance, progress_memory, current_signature)
    )
    local_churn = _has_recent_local_churn(snapshot, progress_memory)
    enough_exhaustion = exhausted_local >= min(2, len(local_interactions))
    nearly_all_exhausted = local_interactions and exhausted_local >= max(1, len(local_interactions) - 1)
    if enough_exhaustion and (nearly_all_exhausted or local_churn):
        reasons = ["small-room interactions look exhausted"]
        if local_churn:
            reasons.append("recent local-object churn without progress")
        return True, reasons

    return False, []


def affordance_looks_exhausted(
    snapshot: dict[str, Any],
    affordance: dict[str, Any],
    progress_memory: dict[str, Any],
    current_signature: dict[str, Any],
) -> bool:
    stats = progress_memory.get("affordances", {}).get(affordance_memory_key(snapshot, affordance)) or {}
    if not stats:
        return False
    if current_signature in stats.get("successful_after_signatures", []):
        return True
    if current_signature in stats.get("consumed_field_signatures", []):
        return True
    if current_signature in stats.get("noop_before_signatures", []):
        return True
    if stats.get("lifecycle") == "stale":
        return True
    if stats.get("consumed_count", 0) >= 1:
        return True
    if stats.get("stale_count", 0) >= 1:
        return True
    if stats.get("noop_count", 0) >= 2:
        return True
    if stats.get("blocked_count", 0) >= 2 and stats.get("last_outcome") in {"blocked", "regressed"}:
        return True
    return False


def describe_scripted_trigger(
    snapshot: dict[str, Any],
    *,
    affordance: dict[str, Any],
    affordances: list[dict[str, Any]],
    progress_memory: dict[str, Any],
) -> dict[str, Any]:
    if affordance.get("kind") != "trigger_region" or not affordance.get("next_script"):
        return {
            "active": False,
            "progression_like": False,
            "local_exhaustion": False,
            "recent_local_churn": False,
            "reasons": [],
        }

    reasons = ["scripted trigger"]
    progression_like = False
    if _near_map_boundary(snapshot, affordance):
        progression_like = True
        reasons.append("near map boundary")
    if str(affordance.get("source_label") or "").endswith("DefaultScript"):
        progression_like = True
        reasons.append("default map script gate")

    current_signature = progress_state_signature(snapshot)
    nearby_choice_interaction = has_nearby_choice_interaction(snapshot, affordances=affordances)
    local_interactions = [
        item for item in affordances if item.get("kind") in LOCAL_INTERACTION_KINDS and item.get("id") != affordance.get("id")
    ]
    exhausted_local = sum(
        1
        for item in local_interactions
        if affordance_looks_exhausted(snapshot, item, progress_memory, current_signature)
    )
    local_exhaustion = exhausted_local >= min(2, len(local_interactions)) and exhausted_local > 0
    if local_exhaustion and not nearby_choice_interaction:
        progression_like = True
        reasons.append("nearby local interactions look exhausted")

    recent_local_churn = _has_recent_local_churn(snapshot, progress_memory)
    if recent_local_churn and not nearby_choice_interaction:
        progression_like = True
        reasons.append("recent local-object churn without progress")

    return {
        "active": True,
        "progression_like": progression_like,
        "local_exhaustion": local_exhaustion,
        "recent_local_churn": recent_local_churn,
        "nearby_choice_interaction": nearby_choice_interaction,
        "reasons": reasons,
    }


def has_nearby_choice_interaction(snapshot: dict[str, Any], *, affordances: list[dict[str, Any]]) -> bool:
    if snapshot["mode"] != "field":
        return False
    for affordance in affordances:
        if choice_interaction_focus_level(snapshot, affordance=affordance) >= 1:
            return True
    return False


def has_engaged_choice_interaction(snapshot: dict[str, Any], *, affordances: list[dict[str, Any]]) -> bool:
    if snapshot["mode"] != "field":
        return False
    return any(choice_interaction_focus_level(snapshot, affordance=affordance) >= 2 for affordance in affordances)


def choice_interaction_focus_level(snapshot: dict[str, Any], *, affordance: dict[str, Any]) -> int:
    if snapshot["mode"] != "field":
        return 0
    if affordance.get("kind") not in LOCAL_INTERACTION_KINDS:
        return 0
    if affordance.get("consumed_in_state"):
        return 0
    hints = set(affordance.get("identity_hints") or [])
    if not {"pickup_like", "starter_choice_like"} & hints:
        return 0
    reachability = affordance.get("reachability") or {}
    if not reachability.get("reachable", True):
        return 0

    current = (snapshot["map"]["x"], snapshot["map"]["y"])
    approach_tiles = {
        (tile["x"], tile["y"])
        for tile in affordance.get("approach_tiles") or []
    }
    if (reachability.get("path_length") or 0) == 0 or current in approach_tiles:
        return 2
    if (reachability.get("path_length") or 0) <= 1 or (affordance.get("distance") or 99) <= 1:
        return 1
    return 0


def _has_recent_local_churn(snapshot: dict[str, Any], progress_memory: dict[str, Any]) -> bool:
    const_name = snapshot["map"].get("const_name") or f"map:{snapshot['map']['id']}"
    recent_targets = list(progress_memory.get("recent_targets", ()))
    recent_local = [
        key
        for key in recent_targets[-6:]
        if key.startswith(f"{const_name}:object:") or key.startswith(f"{const_name}:bg_event:")
    ]
    return len(recent_local) >= 4 and len(set(recent_local)) <= 3


def _is_small_interior(snapshot: dict[str, Any], local_interactions: list[dict[str, Any]]) -> bool:
    width = int(snapshot["map"].get("width") or 0)
    height = int(snapshot["map"].get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    if width * height > 36:
        return False
    if len(local_interactions) == 0:
        return False
    return len(snapshot["map"].get("triggers") or []) == 0


def _near_map_boundary(snapshot: dict[str, Any], affordance: dict[str, Any]) -> bool:
    axis = affordance.get("axis")
    value = affordance.get("value")
    if axis not in {"x", "y"} or value is None:
        return False
    width = int(snapshot["map"].get("width") or 0)
    height = int(snapshot["map"].get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    max_value = (width * 2 - 1) if axis == "x" else (height * 2 - 1)
    return int(value) <= 1 or int(value) >= max_value - 1
