from __future__ import annotations

from typing import Any

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
    objective: dict[str, Any] | None,
) -> dict[str, Any]:
    ranked: list[dict[str, Any]] = []
    for affordance in affordances:
        score, reasons = _score_affordance(
            snapshot,
            affordance=affordance,
            progress_memory=progress_memory,
            objective=objective,
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
    elif objective is not None:
        target_source = "objective_fallback"

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
    objective: dict[str, Any] | None,
    affordances: list[dict[str, Any]],
) -> tuple[int, list[str]]:
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
        if current_signature in noop_before_signatures:
            score -= 24
            reasons.append("known no-op in this state")
        if stats.get("stale_count", 0):
            penalty = min(stats["stale_count"], 3) * 10
            score -= penalty
            reasons.append(f"stale repeats -{penalty}")
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

    if objective and objective.get("affordance_id") == affordance["id"]:
        score += 20
        reasons.append("matches fallback objective")

    if affordance["kind"] == "trigger_region" and affordance.get("next_script"):
        score += 20
        reasons.append("script trigger")

    if affordance["kind"] == "warp":
        target_map = affordance.get("target_map")
        visited_maps = progress_memory.get("visited_maps", set())
        if target_map and target_map not in visited_maps and target_map != "LAST_MAP":
            score += 25
            reasons.append("leads to unseen map")
        if _prefer_exit_warp(snapshot, affordances, progress_memory):
            score += 25
            reasons.append("exit warp preferred after stale local interactions")

    if affordance["kind"] == "object":
        text_ref = affordance.get("text_ref") or ""
        if text_ref.endswith("POKE_BALL") and snapshot["party"]["player_starter"] == 0:
            score += 35
            reasons.append("starter ball before selection")
        if affordance.get("sprite") in {"SPRITE_OAK", "SPRITE_RIVAL", "SPRITE_GARY"}:
            score += 10
            reasons.append("story NPC")

    return score, reasons


def _prefer_exit_warp(
    snapshot: dict[str, Any],
    affordances: list[dict[str, Any]],
    progress_memory: dict[str, Any],
) -> bool:
    if snapshot["mode"] != "field":
        return False
    if snapshot["dialogue"]["active"] or snapshot["menu"]["active"] or snapshot["battle"]["in_battle"]:
        return False

    stale_nonwarp = 0
    checked_nonwarp = 0
    current_signature = progress_state_signature(snapshot)
    for affordance in affordances:
        if affordance["kind"] == "warp":
            continue
        checked_nonwarp += 1
        stats = progress_memory.get("affordances", {}).get(affordance_memory_key(snapshot, affordance))
        if not stats:
            continue
        if (
            stats.get("lifecycle") == "stale"
            or current_signature in stats.get("noop_before_signatures", [])
            or current_signature in stats.get("successful_after_signatures", [])
            or stats.get("stale_count", 0) >= 2
        ):
            stale_nonwarp += 1
    return checked_nonwarp > 0 and stale_nonwarp == checked_nonwarp
