from __future__ import annotations

from typing import Any

from .objective_primitives import (
    expected_progress_signals,
    objective_affordance_id,
    objective_distance,
    objective_kind_for_affordance,
    objective_kind_from_id,
    objective_label,
    objective_phase,
)


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
    return _reconstruct_objective(snapshot, objective_id)


def _reconstruct_objective(snapshot: dict[str, Any], objective_id: str) -> dict[str, Any] | None:
    affordance_id = objective_affordance_id(objective_id)
    objective_kind = objective_kind_from_id(objective_id)
    if affordance_id is None or objective_kind is None:
        return None

    navigation = snapshot.get("navigation") or {}
    affordances = navigation.get("affordances") or []
    affordance = next((item for item in affordances if item.get("id") == affordance_id), None)
    if affordance is None:
        return None

    current_kind = objective_kind_for_affordance(affordance)
    phase = objective_phase(snapshot, affordance)
    if objective_kind != current_kind and objective_kind not in {"approach_entity", "interact_entity"}:
        return None

    resolved_kind = current_kind or objective_kind
    return {
        "id": objective_id,
        "kind": objective_kind,
        "label": objective_label(resolved_kind, affordance),
        "target_affordance_ids": [affordance_id],
        "navigation_target": affordance,
        "confidence": 0.5,
        "evidence": ["reconstructed from pinned affordance"],
        "expected_progress_signals": expected_progress_signals(resolved_kind),
        "failure_signals": ["repeated_noop_window", "blocked_movement_window"],
        "phase": phase,
        "status": "active",
    }
