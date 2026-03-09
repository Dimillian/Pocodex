from __future__ import annotations

from collections import deque
from typing import Any

from .map_data import MapCatalog, build_walkability_grid
from .objective_inference import build_affordances, build_objective_state, find_objective_by_id
from .world_model import build_world_model


DIRECTION_BY_VALUE = {
    0x1: "right",
    0x2: "left",
    0x4: "down",
    0x8: "up",
}

OPPOSITE_DIRECTION = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}


def decode_player_direction(value: int) -> str | None:
    return DIRECTION_BY_VALUE.get(value)


def enrich_snapshot_with_navigation(
    snapshot: dict[str, Any],
    *,
    map_catalog: MapCatalog,
    navigation_state: dict[str, Any],
    progress_memory: dict[str, Any],
    decision_state: dict[str, Any] | None = None,
) -> None:
    map_info = map_catalog.get_by_id(snapshot["map"]["id"])
    snapshot["map"]["const_name"] = map_info.const_name if map_info else None
    snapshot["map"]["name"] = map_info.display_name if map_info else f"Map {snapshot['map']['id']}"
    snapshot["map"]["width"] = map_info.width if map_info else snapshot["map"].get("width")
    snapshot["map"]["height"] = map_info.height if map_info else snapshot["map"].get("height")
    snapshot["map"]["warps"] = []
    snapshot["map"]["bg_events"] = []
    snapshot["map"]["objects"] = []
    snapshot["map"]["triggers"] = []
    if map_info is not None:
        snapshot["map"]["warps"] = [
            {
                "x": warp.x,
                "y": warp.y,
                "target_map": warp.target_map,
                "target_name": _target_display_name(warp.target_map, map_catalog),
                "target_warp_id": warp.target_warp_id,
            }
            for warp in map_info.warps
        ]
        snapshot["map"]["bg_events"] = [
            {
                "x": event.x,
                "y": event.y,
                "text_ref": event.text_ref,
            }
            for event in map_info.bg_events
        ]
        snapshot["map"]["objects"] = [
            {
                "x": obj.x,
                "y": obj.y,
                "sprite": obj.sprite,
                "movement": obj.movement,
                "facing": obj.facing,
                "text_ref": obj.text_ref,
            }
            for obj in map_info.objects
        ]
        snapshot["map"]["triggers"] = [
            {
                "axis": trigger.axis,
                "value": trigger.value,
                "source_label": trigger.source_label,
                "next_script": trigger.next_script,
                "note": trigger.note,
            }
            for trigger in map_info.triggers
        ]

    affordances = build_affordances(
        snapshot,
        map_info=map_info,
        map_catalog=map_catalog,
        progress_memory=progress_memory,
    )
    objective_state = build_objective_state(
        snapshot,
        affordances=affordances,
        decision_state=decision_state or {},
        progress_memory=progress_memory,
        navigation_state=navigation_state,
    )
    active_objective = objective_state.get("active_objective")
    world_model = build_world_model(
        snapshot,
        affordances=affordances,
        progress_memory=progress_memory,
    )
    snapshot["navigation"] = {
        "objective": active_objective,
        "active_objective": active_objective,
        "objective_state": objective_state,
        "candidate_objectives": objective_state.get("candidate_objectives", []),
        "objective_history": objective_state.get("objective_history", []),
        "objective_progress": objective_state.get("objective_progress", []),
        "objective_invalidations": objective_state.get("objective_invalidations", []),
        "recent_map_history": objective_state.get("recent_map_history", []),
        "progress_signals": objective_state.get("progress_signals", []),
        "loop_signals": objective_state.get("loop_signals", []),
        "affordances": affordances,
        "target_affordance": world_model["target_affordance"],
        "target_reason": world_model["target_reason"],
        "target_source": world_model["target_source"],
        "ranked_affordances": world_model["ranked_affordances"],
        "memory": world_model["memory"],
        "facing": {
            "current": snapshot["movement"]["facing"],
            "moving": snapshot["movement"]["moving_direction"],
            "last_stop": snapshot["movement"]["last_stop_direction"],
        },
        "last_result": navigation_state.get("last_result"),
        "last_transition": navigation_state.get("last_transition"),
        "consecutive_failures": navigation_state.get("consecutive_failures", 0),
        "blocked_directions": navigation_state.get("blocked_directions", []),
        "minimap": _build_minimap_model(
            snapshot,
            map_info=map_info,
            map_catalog=map_catalog,
            objective=active_objective,
            target_affordance=world_model["target_affordance"],
            ranked_affordances=world_model["ranked_affordances"],
            progress_memory=progress_memory,
        ),
    }


def choose_field_action(
    snapshot: dict[str, Any],
    *,
    decision_state: dict[str, Any],
    map_catalog: MapCatalog,
    strategy: str = "objective",
    objective_id: str | None = None,
    preferred_affordance_id: str | None = None,
) -> dict[str, Any]:
    if snapshot["map"]["id"] == 0 and snapshot["map"]["x"] == 0 and snapshot["map"]["y"] == 0:
        return {
            "type": "routine",
            "name": "open_menu",
            "reason": "At the title screen, the next deterministic step is opening the menu.",
        }

    objective = _resolve_objective(snapshot, objective_id)
    target_affordance = _resolve_target_affordance(snapshot, preferred_affordance_id)
    map_info = map_catalog.get_by_id(snapshot["map"]["id"])

    if strategy == "objective" and objective and objective["kind"] in {"continue_script", "stabilize_transition"}:
        return {
            "type": "tick",
            "frames": 20 if objective["kind"] == "continue_script" else 12,
            "reason": objective["label"],
        }

    focus = target_affordance if strategy == "target" else objective
    if focus is None and strategy == "objective":
        return _fallback_exploration(snapshot, decision_state)
    if focus is None:
        focus = target_affordance

    navigation_target = (focus or {}).get("navigation_target") if strategy == "objective" else focus
    if navigation_target is not None:
        path = _path_to_objective(snapshot, navigation_target, map_info=map_info, map_catalog=map_catalog)
        if navigation_target["kind"] == "warp":
            if path:
                return _path_step(path, focus["label"])
            return _step_toward_point(snapshot, decision_state, navigation_target, exact=True)
        if navigation_target["kind"] == "trigger_region":
            if path:
                return _path_step(path, focus["label"])
            return _step_toward_region(snapshot, navigation_target, label=focus["label"])
        if navigation_target["kind"] in {"object", "bg_event"}:
            if path:
                return _path_step(path, focus["label"])
            return _step_toward_interactable(snapshot, decision_state, navigation_target, label=focus["label"])

    return _fallback_exploration(snapshot, decision_state)


def _resolve_target_affordance(snapshot: dict[str, Any], preferred_affordance_id: str | None) -> dict[str, Any] | None:
    navigation = snapshot.get("navigation") or {}
    if preferred_affordance_id:
        for affordance in navigation.get("ranked_affordances", []) or []:
            if affordance["id"] == preferred_affordance_id:
                return affordance
        for affordance in navigation.get("affordances", []) or []:
            if affordance["id"] == preferred_affordance_id:
                return affordance
    return navigation.get("target_affordance")


def _resolve_objective(snapshot: dict[str, Any], objective_id: str | None) -> dict[str, Any] | None:
    objective = find_objective_by_id(snapshot, objective_id)
    if objective is not None:
        return objective
    navigation = snapshot.get("navigation") or {}
    return navigation.get("active_objective") or navigation.get("objective")


def _build_minimap_model(
    snapshot: dict[str, Any],
    *,
    map_info,
    map_catalog: MapCatalog,
    objective: dict[str, Any] | None,
    target_affordance: dict[str, Any] | None,
    ranked_affordances: list[dict[str, Any]],
    progress_memory: dict[str, Any],
) -> dict[str, Any] | None:
    if map_info is None:
        return None

    grid_data = build_walkability_grid(map_info, map_catalog)
    if grid_data is None:
        return None

    walkable_grid, tile_grid = grid_data
    focus = (objective or {}).get("navigation_target") if objective else None
    focus = focus or target_affordance
    blocked = _blocked_positions(snapshot, focus or {"kind": "none"})
    target_tiles = sorted(_objective_targets(snapshot, focus, walkable_grid, blocked)) if focus else []
    path = _path_to_objective(snapshot, focus, map_info=map_info, map_catalog=map_catalog) if focus else []

    current = (snapshot["map"]["x"], snapshot["map"]["y"])
    path_tiles: list[dict[str, int | str]] = []
    x, y = current
    for direction in path:
        if direction == "up":
            y -= 1
        elif direction == "down":
            y += 1
        elif direction == "left":
            x -= 1
        elif direction == "right":
            x += 1
        path_tiles.append({"x": x, "y": y, "direction": direction})

    return {
        "width": len(walkable_grid[0]) if walkable_grid else 0,
        "height": len(walkable_grid),
        "walkable_grid": walkable_grid,
        "tile_grid": tile_grid,
        "player": {
            "x": snapshot["map"]["x"],
            "y": snapshot["map"]["y"],
        },
        "blocked_positions": [
            {"x": x_coord, "y": y_coord}
            for x_coord, y_coord in sorted(blocked)
        ],
        "target_tiles": [
            {"x": x_coord, "y": y_coord}
            for x_coord, y_coord in target_tiles
        ],
        "path_tiles": path_tiles,
        "ranked_affordance_ids": [affordance["id"] for affordance in ranked_affordances[:8]],
        "visited_maps": sorted(progress_memory.get("visited_maps", set())),
    }


def update_navigation_state(
    navigation_state: dict[str, Any],
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    button = payload.get("button")
    if button not in {"up", "down", "left", "right"}:
        return

    before_pos = (before["map"]["x"], before["map"]["y"])
    after_pos = (after["map"]["x"], after["map"]["y"])
    map_changed = before["map"]["id"] != after["map"]["id"]
    facing_changed = before["movement"].get("facing") != after["movement"].get("facing")
    result: str
    if map_changed:
        result = "transitioned"
        navigation_state["last_transition"] = {
            "from_map_id": before["map"]["id"],
            "from_map_name": before["map"].get("name"),
            "to_map_id": after["map"]["id"],
            "to_map_name": after["map"].get("name"),
        }
        navigation_state["consecutive_failures"] = 0
        navigation_state["blocked_directions"] = []
    elif before_pos != after_pos:
        result = "moved"
        navigation_state["consecutive_failures"] = 0
        navigation_state["blocked_directions"] = []
    elif after["dialogue"]["active"] or after["menu"]["active"]:
        result = "interaction"
        navigation_state["consecutive_failures"] = 0
        navigation_state["blocked_directions"] = []
    elif facing_changed:
        # Short face_* routines use directional inputs without intending to move.
        result = "reoriented"
        navigation_state["consecutive_failures"] = 0
        navigation_state["blocked_directions"] = []
    else:
        result = "blocked"
        navigation_state["consecutive_failures"] = navigation_state.get("consecutive_failures", 0) + 1
        blocked = list(navigation_state.get("blocked_directions", []))
        if button not in blocked:
            blocked.append(button)
        navigation_state["blocked_directions"] = blocked[-4:]

    navigation_state["last_result"] = {
        "kind": result,
        "button": button,
        "before": {"map_id": before["map"]["id"], "x": before["map"]["x"], "y": before["map"]["y"]},
        "after": {"map_id": after["map"]["id"], "x": after["map"]["x"], "y": after["map"]["y"]},
    }

def _step_toward_point(
    snapshot: dict[str, Any],
    decision_state: dict[str, Any],
    objective: dict[str, Any],
    *,
    exact: bool,
) -> dict[str, Any]:
    current = (snapshot["map"]["x"], snapshot["map"]["y"])
    target = (objective["target"]["x"], objective["target"]["y"])
    if current == target and exact:
        trigger_direction = objective.get("trigger_direction")
        if trigger_direction:
            return {
                "type": "routine",
                "name": f"move_{trigger_direction}",
                "reason": (
                    f"Standing on the warp tile for {objective['label']}; "
                    f"move {trigger_direction} to trigger the exit."
                ),
            }
        return {
            "type": "tick",
            "frames": 20,
            "reason": f"Standing on the objective tile for {objective['label']}; wait for the warp or script to resolve.",
        }

    return _directional_step(
        snapshot,
        target=target,
        reason=objective["label"],
    )


def _step_toward_interactable(
    snapshot: dict[str, Any],
    decision_state: dict[str, Any],
    target: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    current = (snapshot["map"]["x"], snapshot["map"]["y"])
    object_pos = (target["target"]["x"], target["target"]["y"])
    if _manhattan(current, object_pos) == 1:
        desired_facing = _direction_toward(current, object_pos)
        current_facing = snapshot["movement"]["facing"]
        if current_facing == desired_facing:
            return {
                "type": "action",
                "button": "a",
                "reason": f"Facing the objective for {label}; interact with A.",
            }
        return {
            "type": "routine",
            "name": f"face_{desired_facing}",
            "reason": f"Face toward the objective for {label}.",
        }

    approach_tiles = target.get("approach_tiles", [])
    if approach_tiles:
        best_tile = min(
            approach_tiles,
            key=lambda tile: _manhattan(current, (tile["x"], tile["y"])),
        )
        return _directional_step(
            snapshot,
            target=(best_tile["x"], best_tile["y"]),
            reason=label,
        )

    return _fallback_exploration(snapshot, decision_state)


def _step_toward_region(snapshot: dict[str, Any], target: dict[str, Any], *, label: str | None = None) -> dict[str, Any]:
    axis = target["axis"]
    value = target["value"]
    objective_label = label or target["label"]
    current = snapshot["map"][axis]
    if current == value:
        return {
            "type": "tick",
            "frames": 20,
            "reason": f"Standing on the trigger region for {objective_label}; wait for the script to react.",
        }

    if axis == "y":
        direction = "up" if current > value else "down"
    else:
        direction = "left" if current > value else "right"
    return {
        "type": "routine",
        "name": f"move_{direction}",
        "reason": f"{objective_label} Move {direction} to reach {axis.upper()} == {value}.",
    }


def _path_to_objective(
    snapshot: dict[str, Any],
    objective: dict[str, Any],
    *,
    map_info,
    map_catalog: MapCatalog,
) -> list[str]:
    if map_info is None:
        return []

    grid_data = build_walkability_grid(map_info, map_catalog)
    if grid_data is None:
        return []
    walkable_grid, _ = grid_data
    start = (snapshot["map"]["x"], snapshot["map"]["y"])
    if not _is_in_bounds(start, walkable_grid):
        return []

    blocked = _blocked_positions(snapshot, objective)
    targets = _objective_targets(snapshot, objective, walkable_grid, blocked)
    if not targets:
        return []
    if start in targets:
        return []

    queue = deque([start])
    came_from: dict[tuple[int, int], tuple[tuple[int, int], str] | None] = {start: None}
    while queue:
        position = queue.popleft()
        if position in targets:
            return _reconstruct_path(came_from, position)

        for direction, neighbor in _neighbors(position):
            if neighbor in came_from:
                continue
            if not _is_walkable(neighbor, walkable_grid, blocked):
                continue
            came_from[neighbor] = (position, direction)
            queue.append(neighbor)

    return []


def _objective_targets(
    snapshot: dict[str, Any],
    objective: dict[str, Any],
    walkable_grid: list[list[bool]],
    blocked: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    if objective["kind"] == "warp":
        target = (objective["target"]["x"], objective["target"]["y"])
        return {target}

    if objective["kind"] == "object":
        targets: set[tuple[int, int]] = set()
        for tile in objective.get("approach_tiles", []):
            coord = (tile["x"], tile["y"])
            if coord == (snapshot["map"]["x"], snapshot["map"]["y"]) or _is_walkable(coord, walkable_grid, blocked):
                targets.add(coord)
        return targets

    if objective["kind"] == "bg_event":
        targets: set[tuple[int, int]] = set()
        for tile in objective.get("approach_tiles", []):
            coord = (tile["x"], tile["y"])
            if coord == (snapshot["map"]["x"], snapshot["map"]["y"]) or _is_walkable(coord, walkable_grid, blocked):
                targets.add(coord)
        return targets

    if objective["kind"] == "trigger_region":
        targets: set[tuple[int, int]] = set()
        axis = objective["axis"]
        value = objective["value"]
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


def _blocked_positions(snapshot: dict[str, Any], objective: dict[str, Any]) -> set[tuple[int, int]]:
    blocked: set[tuple[int, int]] = set()
    current = (snapshot["map"]["x"], snapshot["map"]["y"])
    objective_target = tuple(objective["target"].values()) if objective.get("target") else None

    for warp in snapshot["map"].get("warps", []):
        coord = (warp["x"], warp["y"])
        if coord == current or coord == objective_target:
            continue
        blocked.add(coord)

    for obj in snapshot["map"].get("objects", []):
        coord = (obj["x"], obj["y"])
        if coord != current:
            blocked.add(coord)

    return blocked


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


def _reconstruct_path(
    came_from: dict[tuple[int, int], tuple[tuple[int, int], str] | None],
    target: tuple[int, int],
) -> list[str]:
    path: list[str] = []
    current = target
    while True:
        previous = came_from[current]
        if previous is None:
            break
        current, direction = previous
        path.append(direction)
    path.reverse()
    return path


def _path_step(path: list[str], reason: str) -> dict[str, Any]:
    direction = path[0]
    return {
        "type": "routine",
        "name": f"move_{direction}",
        "reason": f"{reason} Follow the planned route by moving {direction}.",
    }


def _directional_step(snapshot: dict[str, Any], *, target: tuple[int, int], reason: str) -> dict[str, Any]:
    current = (snapshot["map"]["x"], snapshot["map"]["y"])
    preferred_directions = _directions_toward(current, target)
    blocked = snapshot.get("navigation", {}).get("blocked_directions", [])

    for direction in preferred_directions:
        if direction in blocked and snapshot.get("navigation", {}).get("consecutive_failures", 0) < 2:
            continue
        return {
            "type": "routine",
            "name": f"move_{direction}",
            "reason": f"{reason} Move {direction} toward ({target[0]}, {target[1]}).",
        }

    return {
        "type": "tick",
        "frames": 10,
        "reason": f"{reason} Re-observe before attempting another move.",
    }


def _fallback_exploration(snapshot: dict[str, Any], decision_state: dict[str, Any]) -> dict[str, Any]:
    failures = snapshot.get("navigation", {}).get("consecutive_failures", 0)
    if failures >= 2:
        return {
            "type": "tick",
            "frames": 20,
            "reason": "Movement has failed multiple times; pause to re-observe.",
        }
    directions = ("down", "left", "up", "right")
    index = _field_move_index(decision_state) % len(directions)
    direction = directions[index]
    return {
        "type": "routine",
        "name": f"move_{direction}",
        "reason": f"No specific overworld objective is known, so probe movement by stepping {direction}.",
    }


def _field_move_index(decision_state: dict[str, Any]) -> int:
    return int((decision_state.get("exploration") or {}).get("field_move_index", 0))
def _target_display_name(const_name: str, map_catalog: MapCatalog) -> str:
    if const_name == "LAST_MAP":
        return "previous map"
    target = map_catalog.get_by_name(const_name)
    return target.display_name if target else const_name


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _directions_toward(current: tuple[int, int], target: tuple[int, int]) -> list[str]:
    directions: list[str] = []
    dx = target[0] - current[0]
    dy = target[1] - current[1]
    if dy < 0:
        directions.append("up")
    elif dy > 0:
        directions.append("down")
    if dx < 0:
        directions.append("left")
    elif dx > 0:
        directions.append("right")
    for fallback in ("up", "down", "left", "right"):
        if fallback not in directions:
            directions.append(fallback)
    return directions


def _direction_toward(current: tuple[int, int], target: tuple[int, int]) -> str:
    dx = target[0] - current[0]
    dy = target[1] - current[1]
    if abs(dx) > abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"
