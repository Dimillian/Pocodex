from __future__ import annotations

from collections import deque
from typing import Any

from .map_data import MapCatalog, MapInfo, build_walkability_grid
from .progress_memory import affordance_memory_key, progress_state_signature


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


def blocked_positions(snapshot: dict[str, Any], affordance: dict[str, Any]) -> set[tuple[int, int]]:
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


def affordance_targets(
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
            if coord == (snapshot["map"]["x"], snapshot["map"]["y"]) or is_walkable(coord, walkable_grid, blocked):
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
            if _is_in_bounds(coord, walkable_grid)
            and (coord == (snapshot["map"]["x"], snapshot["map"]["y"]) or is_walkable(coord, walkable_grid, blocked))
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
                if coord == (snapshot["map"]["x"], snapshot["map"]["y"]) or is_walkable(coord, walkable_grid, blocked):
                    targets.add(coord)
        else:
            for y in range(height):
                coord = (value, y)
                if coord == (snapshot["map"]["x"], snapshot["map"]["y"]) or is_walkable(coord, walkable_grid, blocked):
                    targets.add(coord)
        return targets
    return set()


def neighbors(position: tuple[int, int]) -> list[tuple[str, tuple[int, int]]]:
    x, y = position
    return [
        ("up", (x, y - 1)),
        ("down", (x, y + 1)),
        ("left", (x - 1, y)),
        ("right", (x + 1, y)),
    ]


def is_walkable(
    coord: tuple[int, int],
    walkable_grid: list[list[bool]],
    blocked: set[tuple[int, int]],
) -> bool:
    return _is_in_bounds(coord, walkable_grid) and walkable_grid[coord[1]][coord[0]] and coord not in blocked


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
    blocked = blocked_positions(snapshot, affordance)
    targets = affordance_targets(snapshot, affordance, walkable_grid, blocked)
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
        for _, neighbor in neighbors(position):
            if neighbor in seen:
                continue
            if not is_walkable(neighbor, walkable_grid, blocked):
                continue
            if neighbor in targets:
                return depth + 1
            seen.add(neighbor)
            queue.append((neighbor, depth + 1))
    return None


def _walkable_grid(map_info: MapInfo, map_catalog: MapCatalog) -> list[list[bool]] | None:
    grid = build_walkability_grid(map_info, map_catalog)
    if grid is None:
        return None
    walkable_grid, _ = grid
    return walkable_grid


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


def _is_in_bounds(coord: tuple[int, int], walkable_grid: list[list[bool]]) -> bool:
    x, y = coord
    return 0 <= y < len(walkable_grid) and 0 <= x < len(walkable_grid[0])
