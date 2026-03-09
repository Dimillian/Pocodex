from __future__ import annotations

from typing import Any

from .navigation_heuristics import (
    choice_interaction_focus_level,
    describe_scripted_trigger,
    has_engaged_choice_interaction,
    has_nearby_choice_interaction,
    should_prefer_exit_warp,
)
from .objective_memory import fresh_objective_memory
from .objective_primitives import (
    candidate,
    expected_progress_signals,
    has_interaction_ready_candidate,
    is_interaction_ready_affordance,
    objective_kind_for_affordance,
    objective_label,
    objective_phase,
)


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
            candidate(
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
            candidate(
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
        objective_kind = objective_kind_for_affordance(affordance)
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
        phase = objective_phase(snapshot, affordance)
        if phase == "interaction_ready":
            interaction_ready_affordances.add(affordance["id"])
        candidates.append(
            candidate(
                kind=objective_kind,
                label=objective_label(objective_kind, affordance),
                navigation_target=affordance,
                confidence=confidence,
                evidence=evidence,
                expected_progress_signals=expected_progress_signals(objective_kind),
                failure_signals=["repeated_noop_window", "blocked_movement_window"],
                active_id=memory.get("active_objective_id"),
                active_affordance_id=memory.get("active_affordance_id"),
                phase=phase,
            )
        )

    invalidated = {entry["id"]: entry for entry in memory.get("invalidated_objectives", [])}
    deduped: dict[str, dict[str, Any]] = {}
    for current in candidates:
        if interaction_ready_affordances and current["kind"] == "reach_region":
            current["confidence"] = max(0.05, current["confidence"] - 0.18)
            current["evidence"].append("interaction-ready affordance nearby")
        invalidated_entry = invalidated.get(current["id"])
        if invalidated_entry:
            affordance = current.get("navigation_target") or {}
            invalidation_penalty = 0.25
            if choice_interaction_focus_level(snapshot, affordance=affordance) >= 2:
                invalidation_penalty = 0.08
                current["evidence"].append("still-ready choice interaction despite prior failed window")
            current["confidence"] = max(0.05, current["confidence"] - invalidation_penalty)
            current["evidence"].append(f"previously invalidated: {invalidated_entry['reason']}")
            current["status"] = "reconsider"
        deduped[current["id"]] = current

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

    objective_kind = objective_kind_for_affordance(affordance)
    stats = (memory.get("objective_stats") or {}).get(f"{objective_kind}:{affordance['id']}") or {}
    failures = int(stats.get("recent_failures", 0))
    if failures:
        base -= min(failures, 3) * 0.08
        evidence.append(f"recent objective failures={failures}")
    if affordance.get("kind") in {"object", "bg_event"} and is_interaction_ready_affordance(affordance):
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
    if navigation_state.get("consecutive_failures", 0) >= 2 and not has_interaction_ready_candidate(candidates):
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
