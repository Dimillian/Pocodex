from __future__ import annotations

from typing import Any

from .map_data import MapCatalog, MapInfo

MILESTONE_ORDER = {
    "intro.leave_bedroom": 10,
    "intro.leave_house": 20,
    "intro.trigger_oak": 30,
    "intro.follow_oak": 40,
    "lab.progress_with_oak": 50,
    "lab.choose_starter": 55,
    "lab.stay_near_table": 60,
}


def milestone_rank(milestone: str | None) -> int:
    if milestone is None:
        return 0
    return MILESTONE_ORDER.get(milestone, 0)


def build_affordances(snapshot: dict[str, Any], *, map_info: MapInfo | None, map_catalog: MapCatalog) -> list[dict[str, Any]]:
    if map_info is None:
        return []

    affordances: list[dict[str, Any]] = []
    for index, warp in enumerate(map_info.warps):
        affordances.append(
            {
                "id": f"warp:{index}",
                "kind": "warp",
                "label": f"Warp to {_target_name(warp.target_map, map_catalog)}",
                "target": {"x": warp.x, "y": warp.y},
                "target_map": warp.target_map,
                "target_name": _target_name(warp.target_map, map_catalog),
                "target_warp_id": warp.target_warp_id,
                "trigger_direction": _boundary_direction(map_info, warp.x, warp.y),
            }
        )

    for index, bg_event in enumerate(map_info.bg_events):
        affordances.append(
            {
                "id": f"sign:{index}",
                "kind": "bg_event",
                "label": f"Read {bg_event.text_ref}",
                "target": {"x": bg_event.x, "y": bg_event.y},
                "text_ref": bg_event.text_ref,
            }
        )

    for index, obj in enumerate(map_info.objects):
        affordances.append(
            {
                "id": f"object:{index}",
                "kind": "object",
                "label": f"Interact with {obj.sprite}",
                "target": {"x": obj.x, "y": obj.y},
                "sprite": obj.sprite,
                "movement": obj.movement,
                "facing": obj.facing,
                "text_ref": obj.text_ref,
                "approach_tiles": _approach_tiles(map_info, obj.x, obj.y),
            }
        )

    for index, trigger in enumerate(map_info.triggers):
        affordances.append(
            {
                "id": f"trigger:{index}",
                "kind": "trigger_region",
                "label": _trigger_label(trigger.source_label, trigger.axis, trigger.value, trigger.note),
                "axis": trigger.axis,
                "value": trigger.value,
                "source_label": trigger.source_label,
                "next_script": trigger.next_script,
                "note": trigger.note,
            }
        )

    current = (snapshot["map"]["x"], snapshot["map"]["y"])
    for affordance in affordances:
        affordance["distance"] = _affordance_distance(current, affordance)
    affordances.sort(key=lambda affordance: (affordance["distance"], affordance["kind"], affordance["id"]))
    return affordances


def build_current_objective(
    snapshot: dict[str, Any],
    *,
    map_info: MapInfo | None,
    map_catalog: MapCatalog,
    affordances: list[dict[str, Any]],
    decision_state: dict[str, Any],
) -> dict[str, Any] | None:
    if map_info is None:
        return None

    const_name = map_info.const_name
    if const_name == "REDS_HOUSE_2F":
        return _objective_from_affordance(
            affordances,
            "warp",
            lambda affordance: affordance.get("target_map") == "REDS_HOUSE_1F",
            milestone="intro.leave_bedroom",
            label="Leave the bedroom and go downstairs.",
        )

    if const_name == "REDS_HOUSE_1F":
        return _objective_from_affordance(
            affordances,
            "warp",
            lambda affordance: affordance.get("target_map") == "LAST_MAP",
            milestone="intro.leave_house",
            label="Leave the house and enter Pallet Town.",
        )

    if const_name == "PALLET_TOWN":
        if (
            snapshot["dialogue"]["active"]
            or snapshot["screen"].get("message_box_present")
            or snapshot["map"]["script"] in {1, 2, 3, 4}
        ):
            return {
                "milestone": "intro.follow_oak",
                "kind": "script_progress",
                "label": "Let Oak's intro progress.",
            }
        trigger_objective = _objective_from_affordance(
            affordances,
            "trigger_region",
            lambda affordance: affordance.get("next_script") == "SCRIPT_PALLETTOWN_OAK_HEY_WAIT",
            milestone="intro.trigger_oak",
            label="Trigger Oak's intro near the north exit.",
        )
        if trigger_objective is not None:
            return trigger_objective
        return _objective_from_affordance(
            affordances,
            "object",
            lambda affordance: affordance.get("sprite") == "SPRITE_OAK",
            milestone="intro.approach_oak",
            label="Approach Oak.",
        )

    if const_name == "OAKS_LAB":
        preferred_starter = str(_decision_preference(decision_state, "starter_preference") or "SQUIRTLE").upper()
        starter_text_ref = {
            "CHARMANDER": "TEXT_OAKSLAB_CHARMANDER_POKE_BALL",
            "SQUIRTLE": "TEXT_OAKSLAB_SQUIRTLE_POKE_BALL",
            "BULBASAUR": "TEXT_OAKSLAB_BULBASAUR_POKE_BALL",
        }.get(preferred_starter, "TEXT_OAKSLAB_SQUIRTLE_POKE_BALL")
        if snapshot["party"]["player_starter"] == 0 and snapshot["map"]["script"] in {6, 7}:
            if snapshot["map"]["y"] >= 6:
                return _objective_from_affordance(
                    affordances,
                    "trigger_region",
                    lambda affordance: affordance.get("source_label") == "OaksLabPlayerDontGoAwayScript",
                    milestone="lab.stay_near_table",
                    label="Stay close to Oak's table while choosing a starter.",
                )
            return _objective_from_affordance(
                affordances,
                "object",
                lambda affordance: affordance.get("text_ref") == starter_text_ref,
                milestone="lab.choose_starter",
                label=f"Choose {preferred_starter.title()} from Oak's table.",
            )
        if snapshot["map"]["script"] in {6, 7}:
            return _objective_from_affordance(
                affordances,
                "trigger_region",
                lambda affordance: affordance.get("source_label") == "OaksLabPlayerDontGoAwayScript",
                milestone="lab.stay_near_table",
                label="Stay close to Oak's table while choosing a starter.",
            )
        return _objective_from_affordance(
            affordances,
            "object",
            lambda affordance: affordance.get("sprite") == "SPRITE_OAK",
            milestone="lab.progress_with_oak",
            label="Progress Oak's Lab with Oak.",
        )

    return None


def _objective_from_affordance(
    affordances: list[dict[str, Any]],
    expected_kind: str,
    predicate,
    *,
    milestone: str,
    label: str,
) -> dict[str, Any] | None:
    for affordance in affordances:
        if affordance["kind"] != expected_kind:
            continue
        if not predicate(affordance):
            continue
        objective = {
            "milestone": milestone,
            "kind": affordance["kind"],
            "label": label,
            "affordance_id": affordance["id"],
        }
        for key in ("target", "target_map", "target_name", "target_warp_id", "trigger_direction", "axis", "value", "sprite", "text_ref", "approach_tiles", "source_label", "next_script", "note"):
            if key in affordance:
                objective[key] = affordance[key]
        return objective
    return None


def _target_name(const_name: str, map_catalog: MapCatalog) -> str:
    if const_name == "LAST_MAP":
        return "previous map"
    target = map_catalog.get_by_name(const_name)
    return target.display_name if target else const_name


def _trigger_label(source_label: str, axis: str, value: int, note: str | None) -> str:
    descriptor = f"{axis.upper()} == {value}"
    if note:
        descriptor = f"{descriptor} ({note})"
    return f"Trigger region from {source_label}: {descriptor}"


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
    for candidate_x, candidate_y in (
        (x, y - 1),
        (x, y + 1),
        (x - 1, y),
        (x + 1, y),
    ):
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


def _decision_preference(decision_state: dict[str, Any], key: str) -> Any:
    return (decision_state.get("preferences") or {}).get(key)
