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
            _lifecycle_rank(entry.get("lifecycle")),
            entry.get("progress_count", 0),
            entry.get("approach_count", 0),
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
            "approach_count": 0,
            "noop_count": 0,
            "blocked_count": 0,
            "stale_count": 0,
            "last_outcome": None,
            "last_frame": None,
            "lifecycle": "unseen",
            "successful_before_signatures": [],
            "successful_after_signatures": [],
            "noop_before_signatures": [],
        },
    )
    stats["selected_count"] += 1
    stats["last_frame"] = after["frame"]
    state["recent_targets"].append(key)

    before_distance = affordance_distance(before, target)
    after_distance = affordance_distance(after, target)
    before_signature = progress_state_signature(before)
    after_signature = progress_state_signature(after)
    outcome = "noop"
    if _made_progress(before, after):
        outcome = "progress"
        stats["progress_count"] += 1
        _remember_signature(stats["successful_before_signatures"], before_signature)
        _remember_signature(stats["successful_after_signatures"], after_signature)
        state["recent_progress"].append(f"{key}:progress")
        stats["lifecycle"] = "useful"
    elif before_distance is not None and after_distance is not None and after_distance < before_distance:
        outcome = "approached"
        stats["approach_count"] += 1
        if stats["lifecycle"] == "unseen":
            stats["lifecycle"] = "approaching"
    elif after_distance is not None and before_distance is not None and after_distance > before_distance:
        outcome = "regressed"
        stats["blocked_count"] += 1
        stats["lifecycle"] = "blocked"
    else:
        same_position = (before["map"]["x"], before["map"]["y"]) == (after["map"]["x"], after["map"]["y"])
        if same_position:
            stats["blocked_count"] += 1
            outcome = "blocked"
            if _is_stale_noop(before_distance, after_distance):
                stats["stale_count"] += 1
                stats["lifecycle"] = "stale"
                _remember_signature(stats["noop_before_signatures"], before_signature)
            else:
                stats["lifecycle"] = "blocked"
        else:
            stats["noop_count"] += 1
            if _is_stale_noop(before_distance, after_distance):
                stats["stale_count"] += 1
                stats["lifecycle"] = "stale"
                _remember_signature(stats["noop_before_signatures"], before_signature)
            elif stats["lifecycle"] == "unseen":
                stats["lifecycle"] = "approaching"

    stats["last_outcome"] = outcome


def progress_state_signature(snapshot: dict[str, Any]) -> dict[str, Any]:
    interaction = snapshot.get("interaction") or {}
    battle = snapshot.get("battle") or {}
    naming = snapshot.get("naming") or {}
    pokedex = snapshot.get("pokedex") or {}
    return {
        "map": snapshot["map"].get("const_name") or snapshot["map"]["id"],
        "script": snapshot["map"]["script"],
        "mode": snapshot["mode"],
        "interaction": interaction.get("type"),
        "dialogue": tuple(snapshot["dialogue"].get("visible_lines", [])),
        "menu": snapshot["menu"].get("selected_item_text"),
        "battle_ui": battle.get("ui_state"),
        "player_starter": snapshot["party"].get("player_starter"),
        "rival_starter": snapshot["party"].get("rival_starter"),
        "current_species": snapshot["party"].get("current_species"),
        "naming": naming.get("screen_type") if naming.get("active") else None,
        "pokedex": pokedex.get("species_name") if pokedex.get("active") else None,
    }


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
) -> bool:
    if before["map"]["id"] != after["map"]["id"]:
        return True
    if before["map"]["script"] != after["map"]["script"]:
        return True
    if before["mode"] != after["mode"]:
        return True
    if (before.get("interaction") or {}).get("type") != (after.get("interaction") or {}).get("type"):
        return True
    if before["dialogue"]["visible_lines"] != after["dialogue"]["visible_lines"]:
        return True
    if before["menu"]["selected_item_text"] != after["menu"]["selected_item_text"]:
        return True
    if before["battle"] != after["battle"]:
        return True
    if before.get("naming") != after.get("naming"):
        return True
    if before.get("pokedex") != after.get("pokedex"):
        return True
    if _party_progress_signature(before.get("party")) != _party_progress_signature(after.get("party")):
        return True
    return False


def _is_stale_noop(before_distance: int | None, after_distance: int | None) -> bool:
    if before_distance is None or after_distance is None:
        return False
    return before_distance == after_distance == 0


def _remember_signature(signatures: list[dict[str, Any]], signature: dict[str, Any], *, limit: int = 8) -> None:
    if signature in signatures:
        return
    signatures.append(signature)
    if len(signatures) > limit:
        del signatures[0 : len(signatures) - limit]


def _party_progress_signature(party: dict[str, Any] | None) -> tuple[Any, ...]:
    party = party or {}
    return (
        party.get("player_starter"),
        party.get("rival_starter"),
        party.get("current_species"),
        party.get("count"),
        tuple(member.get("species_id") for member in party.get("members", [])),
    )


def _lifecycle_rank(lifecycle: str | None) -> int:
    return {
        "useful": 4,
        "approaching": 3,
        "unseen": 2,
        "blocked": 1,
        "stale": 0,
    }.get(lifecycle, 0)
