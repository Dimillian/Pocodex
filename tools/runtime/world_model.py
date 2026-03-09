from __future__ import annotations

from typing import Any

from .navigation_heuristics import (
    choice_interaction_focus_level,
    describe_scripted_trigger,
    has_engaged_choice_interaction,
    has_nearby_choice_interaction,
    should_prefer_exit_warp,
)
from .progress_memory import (
    affordance_distance,
    affordance_memory_key,
    progress_state_signature,
    summarize_progress_memory,
)


BASE_KIND_SCORES = {
    "trigger_region": 90,
    "object": 70,
    "warp": 55,
    "bg_event": 25,
}


def build_world_model(
    snapshot: dict[str, Any],
    *,
    affordances: list[dict[str, Any]],
    progress_memory: dict[str, Any],
) -> dict[str, Any]:
    ranked: list[dict[str, Any]] = []
    for affordance in affordances:
        score, reasons = _score_affordance(
            snapshot,
            affordance=affordance,
            progress_memory=progress_memory,
            affordances=affordances,
        )
        ranked.append(
            {
                **affordance,
                "score": score,
                "score_reasons": reasons,
                "memory_key": affordance_memory_key(snapshot, affordance),
            }
        )

    ranked.sort(key=lambda affordance: (-affordance["score"], affordance["distance"], affordance["id"]))
    target_affordance = ranked[0] if ranked and ranked[0]["score"] > 0 else None
    target_reason = None
    target_source = None
    if target_affordance is not None:
        target_reason = "; ".join(target_affordance["score_reasons"][:4]) or "Highest-scoring nearby affordance."
        target_source = "world_model"

    return {
        "target_affordance": target_affordance,
        "target_reason": target_reason,
        "target_source": target_source,
        "ranked_affordances": ranked[:8],
        "memory": summarize_progress_memory(progress_memory),
    }


def _score_affordance(
    snapshot: dict[str, Any],
    *,
    affordance: dict[str, Any],
    progress_memory: dict[str, Any],
    affordances: list[dict[str, Any]],
) -> tuple[int, list[str]]:
    prefer_exit_warp, exit_reasons = should_prefer_exit_warp(
        snapshot,
        affordances=affordances,
        progress_memory=progress_memory,
    )
    scripted_trigger = describe_scripted_trigger(
        snapshot,
        affordance=affordance,
        affordances=affordances,
        progress_memory=progress_memory,
    )
    nearby_choice_interaction = has_nearby_choice_interaction(snapshot, affordances=affordances)
    engaged_choice_interaction = has_engaged_choice_interaction(snapshot, affordances=affordances)
    choice_focus_level = choice_interaction_focus_level(snapshot, affordance=affordance)
    score = BASE_KIND_SCORES.get(affordance["kind"], 10)
    reasons = [f"base score for {affordance['kind']}"]
    distance = affordance_distance(snapshot, affordance)
    if distance is not None:
        score -= min(distance, 20) * 3
        reasons.append(f"distance {distance}")

    memory_key = affordance_memory_key(snapshot, affordance)
    stats = progress_memory.get("affordances", {}).get(memory_key)
    current_signature = progress_state_signature(snapshot)
    if not stats:
        score += 25
        reasons.append("unseen affordance")
    else:
        lifecycle = stats.get("lifecycle")
        if lifecycle == "stale":
            score -= 45
            reasons.append("stale in current phase")
        elif lifecycle == "blocked":
            score -= 18
            reasons.append("recently blocked")
        elif lifecycle == "approaching":
            score += 6
            reasons.append("recent approach")

        successful_before_signatures = stats.get("successful_before_signatures", [])
        successful_after_signatures = stats.get("successful_after_signatures", [])
        noop_before_signatures = stats.get("noop_before_signatures", [])

        if stats.get("progress_count", 0) and current_signature in successful_before_signatures:
            bonus = min(stats["progress_count"], 2) * 6
            score += bonus
            reasons.append(f"historical progress from this state +{bonus}")
        if current_signature in successful_after_signatures:
            score -= 42
            reasons.append("already consumed in this resulting state")
        if current_signature in stats.get("consumed_field_signatures", []):
            score -= 90
            reasons.append("already inspected in this field state")
        if current_signature in noop_before_signatures:
            score -= 24
            reasons.append("known no-op in this state")
        if stats.get("stale_count", 0):
            penalty = min(stats["stale_count"], 3) * 10
            score -= penalty
            reasons.append(f"stale repeats -{penalty}")
        if stats.get("consumed_count", 0):
            penalty = min(stats["consumed_count"], 3) * 20
            score -= penalty
            reasons.append(f"consumed repeats -{penalty}")
        if stats.get("noop_count", 0):
            penalty = min(stats["noop_count"], 3) * 18
            score -= penalty
            reasons.append(f"prior no-op -{penalty}")
        if stats.get("blocked_count", 0):
            penalty = min(stats["blocked_count"], 3) * 12
            score -= penalty
            reasons.append(f"prior block -{penalty}")

    recent_targets = list(progress_memory.get("recent_targets", ()))
    if recent_targets:
        repeat_penalty = sum(1 for key in recent_targets[-4:] if key == memory_key) * 10
        if repeat_penalty:
            score -= repeat_penalty
            reasons.append(f"recently repeated -{repeat_penalty}")

    if affordance["kind"] == "trigger_region" and affordance.get("next_script"):
        score += 20
        reasons.append("script trigger")
        if scripted_trigger["progression_like"]:
            score += 22
            reasons.extend(reason for reason in scripted_trigger["reasons"] if reason != "scripted trigger")
            trigger_recovery = 0
            if stats:
                if stats.get("lifecycle") == "stale":
                    trigger_recovery += 24
                if current_signature in noop_before_signatures:
                    trigger_recovery += 14
                if stats.get("stale_count", 0):
                    trigger_recovery += min(int(stats.get("stale_count", 0)), 2) * 6
                if stats.get("blocked_count", 0) and stats.get("last_outcome") in {"blocked", "regressed"}:
                    trigger_recovery += 8
            if trigger_recovery:
                score += min(trigger_recovery, 42)
                reasons.append("softened stale penalties for progression trigger")

    if affordance["kind"] == "warp":
        target_map = affordance.get("target_map")
        visited_maps = progress_memory.get("visited_maps", set())
        if target_map and target_map not in visited_maps and target_map != "LAST_MAP":
            score += 25
            reasons.append("leads to unseen map")
        if prefer_exit_warp:
            score += 35
            reasons.extend(exit_reasons or ["exit warp preferred after stale local interactions"])

    if affordance["kind"] == "object":
        text_ref = affordance.get("text_ref") or ""
        if text_ref.endswith("POKE_BALL") and snapshot["party"]["player_starter"] == 0:
            score += 35
            reasons.append("starter ball before selection")
        if choice_focus_level >= 2:
            score += 28
            reasons.append("choice interaction is ready from current tile")
        elif choice_focus_level == 1:
            score += 12
            reasons.append("choice interaction is nearby")
        if nearby_choice_interaction and {"pickup_like", "starter_choice_like"} & set(affordance.get("identity_hints") or []):
            score += 18
            reasons.append("nearby choice interaction should be resolved before trigger recovery")
        if engaged_choice_interaction and choice_focus_level == 0 and distance is not None and distance <= 1:
            score -= 24
            reasons.append("adjacent non-choice interaction while a choice interaction is ready")
        if affordance.get("sprite") in {"SPRITE_OAK", "SPRITE_RIVAL", "SPRITE_GARY"}:
            score += 10
            reasons.append("story NPC")

    return score, reasons
