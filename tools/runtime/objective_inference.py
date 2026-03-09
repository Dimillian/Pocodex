from __future__ import annotations

from collections import deque
from typing import Any

from .map_data import MapCatalog, MapInfo, build_walkability_grid
from .navigation_heuristics import (
    choice_interaction_focus_level,
    describe_scripted_trigger,
    has_engaged_choice_interaction,
    has_nearby_choice_interaction,
    should_prefer_exit_warp,
)
from .progress_memory import affordance_memory_key, progress_state_signature

GENERIC_OBJECTIVE_KINDS = {
    "reach_exit",
    "approach_entity",
    "interact_entity",
    "reach_region",
    "continue_script",
    "inspect_interactable",
    "stabilize_transition",
}


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


def build_affordances(
    snapshot: dict[str, Any],
    *,
    map_info: MapInfo | None,
    map_catalog: MapCatalog,
    progress_memory: dict[str, Any],
) -> list[dict[str, Any]]:
    if map_info is None:
        return []

    walkable_grid = _walkable_grid(map_info, map_catalog)
    affordances: list[dict[str, Any]] = []
    for index, warp in enumerate(map_info.warps):
        affordances.append(
            _annotate_affordance(
                snapshot,
                {
                    "id": f"warp:{index}",
                    "kind": "warp",
                    "label": f"Exit toward {_target_name(warp.target_map, map_catalog)}",
                    "target": {"x": warp.x, "y": warp.y},
                    "target_map": warp.target_map,
                    "target_name": _target_name(warp.target_map, map_catalog),
                    "target_warp_id": warp.target_warp_id,
                    "trigger_direction": _boundary_direction(map_info, warp.x, warp.y),
                    "semantic_tags": ["exit", "warp", "transition"],
                    "interaction_class": "transition",
                    "identity_hints": ["exit", "map_transition"],
                },
                progress_memory=progress_memory,
                walkable_grid=walkable_grid,
            )
        )

    for index, bg_event in enumerate(map_info.bg_events):
        bg_profile = _describe_bg_event(bg_event)
        affordances.append(
            _annotate_affordance(
                snapshot,
                {
                    "id": f"bg_event:{index}",
                    "kind": "bg_event",
                    "label": bg_profile["label"],
                    "target": {"x": bg_event.x, "y": bg_event.y},
                    "text_ref": bg_event.text_ref,
                    "approach_tiles": _approach_tiles(map_info, bg_event.x, bg_event.y),
                    "semantic_tags": bg_profile["semantic_tags"],
                    "interaction_class": bg_profile["interaction_class"],
                    "identity_hints": bg_profile["identity_hints"],
                },
                progress_memory=progress_memory,
                walkable_grid=walkable_grid,
            )
        )

    for index, obj in enumerate(map_info.objects):
        object_profile = _describe_object(obj)
        affordances.append(
            _annotate_affordance(
                snapshot,
                {
                    "id": f"object:{index}",
                    "kind": "object",
                    "label": object_profile["label"],
                    "target": {"x": obj.x, "y": obj.y},
                    "sprite": obj.sprite,
                    "movement": obj.movement,
                    "facing": obj.facing,
                    "text_ref": obj.text_ref,
                    "const_name": obj.const_name,
                    "approach_tiles": _approach_tiles(map_info, obj.x, obj.y),
                    "semantic_tags": object_profile["semantic_tags"],
                    "interaction_class": object_profile["interaction_class"],
                    "identity_hints": object_profile["identity_hints"],
                },
                progress_memory=progress_memory,
                walkable_grid=walkable_grid,
            )
        )

    for index, trigger in enumerate(map_info.triggers):
        affordances.append(
            _annotate_affordance(
                snapshot,
                {
                    "id": f"trigger:{index}",
                    "kind": "trigger_region",
                    "label": f"Reach trigger region {trigger.axis.upper()} == {trigger.value}",
                    "axis": trigger.axis,
                    "value": trigger.value,
                    "source_label": trigger.source_label,
                    "next_script": trigger.next_script,
                    "note": trigger.note,
                    "semantic_tags": ["boundary_trigger", "script_region"],
                    "interaction_class": "reach_region",
                    "identity_hints": ["boundary_trigger", "script_trigger" if trigger.next_script else "region"],
                },
                progress_memory=progress_memory,
                walkable_grid=walkable_grid,
            )
        )

    affordances.sort(key=lambda affordance: (affordance["distance"], affordance["kind"], affordance["id"]))
    return affordances


def build_objective_state(
    snapshot: dict[str, Any],
    *,
    affordances: list[dict[str, Any]],
    decision_state: dict[str, Any],
    progress_memory: dict[str, Any],
    navigation_state: dict[str, Any],
) -> dict[str, Any]:
    memory = decision_state.get("objective") or fresh_objective_memory()
    candidates = _build_candidate_objectives(
        snapshot,
        affordances=affordances,
        memory=memory,
        progress_memory=progress_memory,
        navigation_state=navigation_state,
    )
    active_objective = _resolve_active_objective(memory, candidates)
    progress = list(memory.get("objective_progress", []))[-8:]
    invalidations = list(memory.get("invalidated_objectives", []))[-8:]
    history = list(memory.get("objective_history", []))[-8:]
    recent_map_history = list(memory.get("recent_map_history", []))[-8:]
    progress_signals = _build_progress_signals(progress, navigation_state, progress_memory)
    loop_signals = _build_loop_signals(snapshot, navigation_state, progress_memory, memory, candidates)
    return {
        "active_objective": active_objective,
        "candidate_objectives": candidates,
        "objective_history": history,
        "objective_progress": progress,
        "objective_invalidations": invalidations,
        "recent_map_history": recent_map_history,
        "progress_signals": progress_signals,
        "loop_signals": loop_signals,
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
    if memory.get("active_affordance_id") == _objective_affordance_id(objective_id):
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


def find_objective_by_id(snapshot: dict[str, Any], objective_id: str | None) -> dict[str, Any] | None:
    objective_state = (snapshot.get("navigation") or {}).get("objective_state") or {}
    active = objective_state.get("active_objective")
    if objective_id is None:
        return active
    if active and active.get("id") == objective_id:
        return active
    for objective in objective_state.get("candidate_objectives") or []:
        if objective.get("id") == objective_id:
            return objective
    reconstructed = _reconstruct_objective(snapshot, objective_id)
    if reconstructed is not None:
        return reconstructed
    return None


def _build_candidate_objectives(
    snapshot: dict[str, Any],
    *,
    affordances: list[dict[str, Any]],
    memory: dict[str, Any],
    progress_memory: dict[str, Any],
    navigation_state: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    last_result = navigation_state.get("last_result") or {}
    prefer_exit_warp, exit_preference_reasons = should_prefer_exit_warp(
        snapshot,
        affordances=affordances,
        progress_memory=progress_memory,
    )
    interaction_ready_affordances: set[str] = set()
    if snapshot["dialogue"]["active"] or snapshot["screen"].get("message_box_present"):
        candidates.append(
            _candidate(
                kind="continue_script",
                label="Continue the current scripted interaction until the field state changes.",
                navigation_target=None,
                confidence=0.92,
                evidence=["dialogue is visible", "script likely expects acknowledgement"],
                expected_progress_signals=["dialogue_progressed", "interaction_changed", "mode_changed"],
                failure_signals=["repeated_noop_window"],
                active_id=memory.get("active_objective_id"),
                active_affordance_id=memory.get("active_affordance_id"),
            )
        )
    elif navigation_state.get("last_transition") or last_result.get("kind") == "interaction":
        candidates.append(
            _candidate(
                kind="stabilize_transition",
                label="Pause briefly and stabilize the current transition before choosing a new destination.",
                navigation_target=None,
                confidence=0.68,
                evidence=["recent transition or forced interaction", "state may still be settling"],
                expected_progress_signals=["mode_changed", "dialogue_progressed"],
                failure_signals=["repeated_noop_window"],
                active_id=memory.get("active_objective_id"),
                active_affordance_id=memory.get("active_affordance_id"),
            )
        )

    for affordance in affordances:
        objective_kind = _objective_kind_for_affordance(affordance)
        if objective_kind is None:
            continue
        confidence, evidence = _candidate_confidence(
            snapshot,
            affordance,
            affordances=affordances,
            memory=memory,
            progress_memory=progress_memory,
            prefer_exit_warp=prefer_exit_warp,
            exit_preference_reasons=exit_preference_reasons,
        )
        phase = _objective_phase(snapshot, affordance)
        if phase == "interaction_ready":
            interaction_ready_affordances.add(affordance["id"])
        candidates.append(
            _candidate(
                kind=objective_kind,
                label=_objective_label(objective_kind, affordance),
                navigation_target=affordance,
                confidence=confidence,
                evidence=evidence,
                expected_progress_signals=_expected_progress_signals(objective_kind),
                failure_signals=["repeated_noop_window", "blocked_movement_window"],
                active_id=memory.get("active_objective_id"),
                active_affordance_id=memory.get("active_affordance_id"),
                phase=phase,
            )
        )

    invalidated = {entry["id"]: entry for entry in memory.get("invalidated_objectives", [])}
    deduped: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if interaction_ready_affordances and candidate["kind"] == "reach_region":
            candidate["confidence"] = max(0.05, candidate["confidence"] - 0.18)
            candidate["evidence"].append("interaction-ready affordance nearby")
        invalidated_entry = invalidated.get(candidate["id"])
        if invalidated_entry:
            affordance = candidate.get("navigation_target") or {}
            invalidation_penalty = 0.25
            if choice_interaction_focus_level(snapshot, affordance=affordance) >= 2:
                invalidation_penalty = 0.08
                candidate["evidence"].append("still-ready choice interaction despite prior failed window")
            candidate["confidence"] = max(0.05, candidate["confidence"] - invalidation_penalty)
            candidate["evidence"].append(f"previously invalidated: {invalidated_entry['reason']}")
            candidate["status"] = "reconsider"
        deduped[candidate["id"]] = candidate

    ranked = sorted(
        deduped.values(),
        key=lambda objective: (
            objective.get("status") == "active",
            objective["confidence"],
            choice_interaction_focus_level(snapshot, affordance=(objective.get("navigation_target") or {})),
            objective["kind"],
            objective["id"],
        ),
        reverse=True,
    )
    return ranked[:6]


def _candidate(
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
    is_active = active_id == objective_id or (active_affordance_id is not None and active_affordance_id == target_affordance_id)
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


def _candidate_confidence(
    snapshot: dict[str, Any],
    affordance: dict[str, Any],
    *,
    affordances: list[dict[str, Any]],
    memory: dict[str, Any],
    progress_memory: dict[str, Any],
    prefer_exit_warp: bool,
    exit_preference_reasons: list[str],
) -> tuple[float, list[str]]:
    distance = affordance.get("distance") or 0
    reachability = affordance.get("reachability") or {}
    reachable = reachability.get("reachable", True)
    path_length = reachability.get("path_length")
    base = {
        "warp": 0.62,
        "trigger_region": 0.76,
        "object": 0.66,
        "bg_event": 0.48,
    }.get(affordance["kind"], 0.4)
    evidence = [f"kind={affordance['kind']}", f"distance={distance}"]
    if affordance.get("novelty") == "new":
        base += 0.12
        evidence.append("novel affordance")
    if reachable:
        base += 0.08
        evidence.append("reachable from current tile")
    else:
        base -= 0.18
        evidence.append("path not currently reachable")
    if path_length is not None and path_length > 0:
        base -= min(path_length, 12) * 0.015
    last_outcome = affordance.get("last_outcome")
    if last_outcome == "progress":
        base += 0.08
        evidence.append("historically useful")
    elif last_outcome in {"blocked", "noop", "regressed"}:
        base -= 0.12
        evidence.append(f"recent outcome={last_outcome}")

    stats = (memory.get("objective_stats") or {}).get(f"{_objective_kind_for_affordance(affordance)}:{affordance['id']}") or {}
    failures = int(stats.get("recent_failures", 0))
    if failures:
        base -= min(failures, 3) * 0.08
        evidence.append(f"recent objective failures={failures}")
    if affordance.get("kind") in {"object", "bg_event"} and _is_interaction_ready_affordance(affordance):
        base += 0.2
        evidence.append("interaction-ready from current tile")
    if affordance.get("consumed_in_state"):
        base -= 0.32
        evidence.append("already consumed in this field state")
    object_hints = set(affordance.get("identity_hints") or [])
    if affordance.get("kind") == "object" and "story_npc" in object_hints:
        base += 0.04
        evidence.append("story-relevant NPC")
    if affordance.get("kind") == "object" and {"info_fixture", "reference_display"} & object_hints:
        base -= 0.04
        evidence.append("reference fixture is lower urgency than live actors")
    if "starter_choice_like" in affordance.get("identity_hints", []):
        base += 0.04
        evidence.append("choice-like interaction")
    if affordance["kind"] == "warp" and affordance.get("target_map") not in progress_memory.get("visited_maps", set()):
        base += 0.08
        evidence.append("unseen destination")
    if affordance["kind"] == "warp" and prefer_exit_warp:
        base += 0.18
        evidence.extend(exit_preference_reasons or ["local room interactions appear exhausted"])
    scripted_trigger = describe_scripted_trigger(
        snapshot,
        affordance=affordance,
        affordances=affordances,
        progress_memory=progress_memory,
    )
    nearby_choice_interaction = has_nearby_choice_interaction(snapshot, affordances=affordances)
    engaged_choice_interaction = has_engaged_choice_interaction(snapshot, affordances=affordances)
    choice_focus_level = choice_interaction_focus_level(snapshot, affordance=affordance)
    if scripted_trigger["progression_like"]:
        base += 0.16
        evidence.extend(reason for reason in scripted_trigger["reasons"] if reason != "scripted trigger")
        if last_outcome in {"blocked", "noop", "regressed"}:
            base += 0.08
            evidence.append("softened stale penalty for progression trigger")
    if affordance.get("kind") == "object" and nearby_choice_interaction:
        if {"pickup_like", "starter_choice_like"} & object_hints:
            base += 0.1
            evidence.append("nearby choice interaction should be resolved before trigger recovery")
    if choice_focus_level >= 2:
        base += 0.16
        evidence.append("choice interaction is ready from current tile")
    elif choice_focus_level == 1:
        base += 0.08
        evidence.append("choice interaction is nearby")
    if engaged_choice_interaction and choice_focus_level == 0 and affordance.get("kind") in {"object", "bg_event"}:
        reachability = affordance.get("reachability") or {}
        if (reachability.get("path_length") or 99) <= 1 or (affordance.get("distance") or 99) <= 1:
            base -= 0.18
            evidence.append("adjacent non-choice interaction while a choice interaction is ready")
    return max(0.05, min(base, 0.95)), evidence


def _resolve_active_objective(memory: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    active_id = memory.get("active_objective_id")
    active_affordance_id = memory.get("active_affordance_id")
    for objective in candidates:
        if objective["id"] == active_id:
            objective["status"] = "active"
            return objective
    if active_affordance_id is not None:
        for objective in candidates:
            if active_affordance_id in (objective.get("target_affordance_ids") or []):
                objective["status"] = "active"
                return objective
    if candidates:
        fallback = dict(candidates[0])
        fallback["status"] = "candidate"
        return fallback
    return None


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


def _build_progress_signals(
    progress_entries: list[dict[str, Any]],
    navigation_state: dict[str, Any],
    progress_memory: dict[str, Any],
) -> list[str]:
    signals: list[str] = []
    if navigation_state.get("last_transition"):
        signals.append("recent_map_transition")
    for item in list(progress_memory.get("recent_progress", ()))[:4]:
        signals.append(item)
    if progress_entries:
        signals.extend(progress_entries[-1].get("progress_signals") or [])
    return signals[:8]


def _build_loop_signals(
    snapshot: dict[str, Any],
    navigation_state: dict[str, Any],
    progress_memory: dict[str, Any],
    memory: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[str]:
    signals: list[str] = []
    if navigation_state.get("consecutive_failures", 0) >= 2 and not _has_interaction_ready_candidate(candidates):
        signals.append("movement_loop")
    recent_targets = list(progress_memory.get("recent_targets", ()))
    if recent_targets and len(recent_targets) >= 3 and len(set(recent_targets[-3:])) == 1:
        signals.append("repeated_target_loop")
    invalidations = memory.get("invalidated_objectives", [])
    if invalidations:
        signals.append("recent_objective_invalidation")
    if snapshot.get("navigation", {}).get("blocked_directions"):
        signals.append("blocked_directions_present")
    return signals[:8]


def _objective_kind_for_affordance(affordance: dict[str, Any]) -> str | None:
    if affordance["kind"] == "warp":
        return "reach_exit"
    if affordance["kind"] == "trigger_region":
        return "reach_region"
    if affordance["kind"] == "object":
        return "interact_entity" if _is_interaction_ready_affordance(affordance) else "approach_entity"
    if affordance["kind"] == "bg_event":
        return "inspect_interactable"
    return None


def _objective_phase(snapshot: dict[str, Any], affordance: dict[str, Any]) -> str:
    if affordance["kind"] in {"object", "bg_event"} and _is_interaction_ready_affordance(affordance):
        return "interaction_ready"
    if affordance["kind"] == "trigger_region" and objective_distance(snapshot, {"kind": affordance["kind"], **affordance}) == 0:
        return "resolving"
    return "approach"


def _is_interaction_ready_affordance(affordance: dict[str, Any]) -> bool:
    reachability = affordance.get("reachability") or {}
    if (reachability.get("path_length") or 0) == 0:
        return True
    return (affordance.get("distance") or 99) <= 1


def _has_interaction_ready_candidate(candidates: list[dict[str, Any]]) -> bool:
    return any(candidate.get("phase") == "interaction_ready" for candidate in candidates)


def _objective_affordance_id(objective_id: str | None) -> str | None:
    if not objective_id or ":" not in objective_id:
        return None
    parts = objective_id.split(":", 2)
    if len(parts) < 3:
        return None
    affordance_id = ":".join(parts[1:])
    return affordance_id


def _objective_kind_from_id(objective_id: str | None) -> str | None:
    if not objective_id or ":" not in objective_id:
        return objective_id
    return objective_id.split(":", 1)[0]


def _reconstruct_objective(snapshot: dict[str, Any], objective_id: str) -> dict[str, Any] | None:
    affordance_id = _objective_affordance_id(objective_id)
    objective_kind = _objective_kind_from_id(objective_id)
    if affordance_id is None or objective_kind is None:
        return None

    navigation = snapshot.get("navigation") or {}
    affordances = navigation.get("affordances") or []
    affordance = next((item for item in affordances if item.get("id") == affordance_id), None)
    if affordance is None:
        return None

    current_kind = _objective_kind_for_affordance(affordance)
    phase = _objective_phase(snapshot, affordance)
    if objective_kind != current_kind and objective_kind not in {"approach_entity", "interact_entity"}:
        return None

    label = _objective_label(current_kind or objective_kind, affordance)
    return {
        "id": objective_id,
        "kind": objective_kind,
        "label": label,
        "target_affordance_ids": [affordance_id],
        "navigation_target": affordance,
        "confidence": 0.5,
        "evidence": ["reconstructed from pinned affordance"],
        "expected_progress_signals": _expected_progress_signals(current_kind or objective_kind),
        "failure_signals": ["repeated_noop_window", "blocked_movement_window"],
        "phase": phase,
        "status": "active",
    }


def _objective_label(objective_kind: str, affordance: dict[str, Any]) -> str:
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


def _expected_progress_signals(objective_kind: str) -> list[str]:
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


def _annotate_affordance(
    snapshot: dict[str, Any],
    affordance: dict[str, Any],
    *,
    progress_memory: dict[str, Any],
    walkable_grid: list[list[bool]] | None,
) -> dict[str, Any]:
    current = (snapshot["map"]["x"], snapshot["map"]["y"])
    affordance["distance"] = _affordance_distance(current, affordance)
    memory_key = affordance_memory_key(snapshot, affordance)
    stats = progress_memory.get("affordances", {}).get(memory_key) or {}
    affordance["memory_key"] = memory_key
    affordance["novelty"] = "new" if not stats else "known"
    affordance["last_outcome"] = stats.get("last_outcome")
    affordance["consumed_in_state"] = progress_state_signature(snapshot) in stats.get("consumed_field_signatures", [])
    affordance["reachability"] = _reachability(snapshot, affordance, walkable_grid)
    return affordance


def _reachability(
    snapshot: dict[str, Any],
    affordance: dict[str, Any],
    walkable_grid: list[list[bool]] | None,
) -> dict[str, Any]:
    if walkable_grid is None:
        return {"reachable": True, "path_length": None}
    start = (snapshot["map"]["x"], snapshot["map"]["y"])
    blocked = _blocked_positions(snapshot, affordance)
    targets = _affordance_targets(snapshot, affordance, walkable_grid, blocked)
    path_length = _shortest_path_length(start, targets, walkable_grid, blocked)
    return {
        "reachable": path_length is not None,
        "path_length": path_length,
    }


def _shortest_path_length(
    start: tuple[int, int],
    targets: set[tuple[int, int]],
    walkable_grid: list[list[bool]],
    blocked: set[tuple[int, int]],
) -> int | None:
    if start in targets:
        return 0
    queue = deque([(start, 0)])
    seen = {start}
    while queue:
        position, depth = queue.popleft()
        for _, neighbor in _neighbors(position):
            if neighbor in seen:
                continue
            if not _is_walkable(neighbor, walkable_grid, blocked):
                continue
            if neighbor in targets:
                return depth + 1
            seen.add(neighbor)
            queue.append((neighbor, depth + 1))
    return None


def _affordance_targets(
    snapshot: dict[str, Any],
    affordance: dict[str, Any],
    walkable_grid: list[list[bool]],
    blocked: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    if affordance["kind"] == "warp":
        return {(affordance["target"]["x"], affordance["target"]["y"])}
    if affordance["kind"] == "object":
        targets: set[tuple[int, int]] = set()
        for tile in affordance.get("approach_tiles", []):
            coord = (tile["x"], tile["y"])
            if coord == (snapshot["map"]["x"], snapshot["map"]["y"]) or _is_walkable(coord, walkable_grid, blocked):
                targets.add(coord)
        return targets
    if affordance["kind"] == "bg_event":
        target = affordance.get("target")
        if not target:
            return set()
        candidates = {
            (target["x"], target["y"] - 1),
            (target["x"], target["y"] + 1),
            (target["x"] - 1, target["y"]),
            (target["x"] + 1, target["y"]),
        }
        return {
            coord
            for coord in candidates
            if _is_in_bounds(coord, walkable_grid) and (
                coord == (snapshot["map"]["x"], snapshot["map"]["y"]) or _is_walkable(coord, walkable_grid, blocked)
            )
        }
    if affordance["kind"] == "trigger_region":
        targets: set[tuple[int, int]] = set()
        axis = affordance["axis"]
        value = affordance["value"]
        height = len(walkable_grid)
        width = len(walkable_grid[0]) if height else 0
        if axis == "y":
            for x in range(width):
                coord = (x, value)
                if coord == (snapshot["map"]["x"], snapshot["map"]["y"]) or _is_walkable(coord, walkable_grid, blocked):
                    targets.add(coord)
        else:
            for y in range(height):
                coord = (value, y)
                if coord == (snapshot["map"]["x"], snapshot["map"]["y"]) or _is_walkable(coord, walkable_grid, blocked):
                    targets.add(coord)
        return targets
    return set()


def _blocked_positions(snapshot: dict[str, Any], affordance: dict[str, Any]) -> set[tuple[int, int]]:
    blocked: set[tuple[int, int]] = set()
    current = (snapshot["map"]["x"], snapshot["map"]["y"])
    objective_target = tuple(affordance["target"].values()) if affordance.get("target") else None
    for warp in snapshot["map"].get("warps", []):
        coord = (warp["x"], warp["y"])
        if coord == current or coord == objective_target:
            continue
        blocked.add(coord)
    for obj in snapshot["map"].get("objects", []):
        coord = (obj["x"], obj["y"])
        if coord != current and coord != objective_target:
            blocked.add(coord)
    return blocked


def _walkable_grid(map_info: MapInfo, map_catalog: MapCatalog) -> list[list[bool]] | None:
    grid = build_walkability_grid(map_info, map_catalog)
    if grid is None:
        return None
    walkable_grid, _ = grid
    return walkable_grid


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


def _describe_bg_event(bg_event: Any) -> dict[str, Any]:
    text_ref = (getattr(bg_event, "text_ref", "") or "").upper()
    semantic_tags = ["inspectable", "text"]
    identity_hints = ["interactable", "text_source"]
    label = "Inspect nearby text or fixture."
    if "SIGN" in text_ref:
        semantic_tags.append("sign_like")
        identity_hints.extend(["sign", "info_fixture"])
        label = "Read the nearby sign."
    elif "MAP" in text_ref:
        semantic_tags.extend(["display", "map_fixture"])
        identity_hints.extend(["map_display", "info_fixture"])
        label = "Inspect the nearby map display."
    elif any(token in text_ref for token in ("BLACKBOARD", "BOOKSHELF", "BOOK_SHELF")):
        semantic_tags.extend(["display", "reference_fixture"])
        identity_hints.extend(["reference_display", "info_fixture"])
        label = "Inspect the nearby reference display."
    return {
        "label": label,
        "semantic_tags": semantic_tags,
        "interaction_class": "inspect",
        "identity_hints": identity_hints,
    }


def _describe_object(obj: Any) -> dict[str, Any]:
    sprite = (getattr(obj, "sprite", "") or "").upper()
    text_ref = (getattr(obj, "text_ref", "") or "").upper()
    const_name = (getattr(obj, "const_name", "") or "").upper()
    identity_hints = ["entity", "npc"]
    semantic_tags = ["entity", "interactable"]
    if getattr(obj, "movement", "") == "STAY":
        identity_hints.append("stationary_entity")
    else:
        identity_hints.append("moving_entity")

    label = "Interact with the nearby entity."
    npc_name = _sprite_label(sprite)
    if npc_name:
        label = f"Talk to {npc_name}."

    if sprite in {"SPRITE_OAK", "SPRITE_BLUE", "SPRITE_GARY", "SPRITE_RIVAL"}:
        identity_hints.append("story_npc")
        semantic_tags.append("story_npc")
    if sprite == "SPRITE_POKEDEX":
        identity_hints.extend(["info_fixture", "reference_display"])
        semantic_tags.extend(["display", "reference_fixture"])
        label = "Inspect the nearby Pokédex display."
    if sprite == "SPRITE_POKE_BALL":
        identity_hints.extend(["pickup_like", "choice_like"])
        semantic_tags.append("pickup_like")
        species_name = _starter_species_name(text_ref, const_name)
        if species_name:
            identity_hints.append("starter_choice_like")
            semantic_tags.append("starter_choice")
            label = f"Choose the {species_name.title()} Poké Ball."
        else:
            label = "Inspect the nearby Poké Ball."
    if "POKEDEX" in text_ref or "POKEDEX" in const_name:
        identity_hints.extend(["info_fixture", "reference_display"])
        semantic_tags.extend(["display", "reference_fixture"])
        if sprite != "SPRITE_POKEDEX":
            label = "Inspect the nearby Pokédex display."
    if "TOWN_MAP" in text_ref or "TOWN_MAP" in const_name:
        identity_hints.extend(["map_display", "info_fixture"])
        semantic_tags.extend(["display", "map_fixture"])
        label = "Inspect the nearby town map."

    return {
        "label": label,
        "semantic_tags": semantic_tags,
        "interaction_class": "interact",
        "identity_hints": identity_hints,
    }


def _starter_species_name(*tokens: str) -> str | None:
    joined = " ".join(token.upper() for token in tokens if token)
    for species_name in ("BULBASAUR", "CHARMANDER", "SQUIRTLE"):
        if species_name in joined:
            return species_name
    return None


def _sprite_label(sprite: str) -> str | None:
    sprite_names = {
        "SPRITE_OAK": "Professor Oak",
        "SPRITE_BLUE": "your rival",
        "SPRITE_GARY": "your rival",
    }
    return sprite_names.get(sprite)


def _target_name(const_name: str, map_catalog: MapCatalog) -> str:
    if const_name == "LAST_MAP":
        return "previous map"
    target = map_catalog.get_by_name(const_name)
    return target.display_name if target else const_name


def _boundary_direction(map_info: MapInfo, x: int, y: int) -> str | None:
    max_x = map_info.width * 2 - 1
    max_y = map_info.height * 2 - 1
    if y == 0:
        return "up"
    if y == max_y:
        return "down"
    if x == 0:
        return "left"
    if x == max_x:
        return "right"
    return None


def _approach_tiles(map_info: MapInfo, x: int, y: int) -> list[dict[str, int]]:
    candidates = []
    for candidate_x, candidate_y in ((x, y - 1), (x, y + 1), (x - 1, y), (x + 1, y)):
        if 0 <= candidate_x < map_info.width * 2 and 0 <= candidate_y < map_info.height * 2:
            candidates.append({"x": candidate_x, "y": candidate_y})
    return candidates


def _affordance_distance(current: tuple[int, int], affordance: dict[str, Any]) -> int:
    if affordance["kind"] == "trigger_region":
        if affordance["axis"] == "y":
            return abs(current[1] - affordance["value"])
        return abs(current[0] - affordance["value"])
    target = affordance.get("target")
    if target is None:
        return 999
    return abs(current[0] - target["x"]) + abs(current[1] - target["y"])


def _neighbors(position: tuple[int, int]) -> list[tuple[str, tuple[int, int]]]:
    x, y = position
    return [
        ("up", (x, y - 1)),
        ("down", (x, y + 1)),
        ("left", (x - 1, y)),
        ("right", (x + 1, y)),
    ]


def _is_in_bounds(coord: tuple[int, int], walkable_grid: list[list[bool]]) -> bool:
    x, y = coord
    return 0 <= y < len(walkable_grid) and 0 <= x < len(walkable_grid[0])


def _is_walkable(
    coord: tuple[int, int],
    walkable_grid: list[list[bool]],
    blocked: set[tuple[int, int]],
) -> bool:
    return _is_in_bounds(coord, walkable_grid) and walkable_grid[coord[1]][coord[0]] and coord not in blocked
