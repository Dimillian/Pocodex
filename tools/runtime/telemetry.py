from __future__ import annotations

from dataclasses import dataclass

from .navigator import decode_player_direction
from .symbols import SymbolTable
from .tilemap import (
    DEFAULT_CHARMAP,
    MESSAGE_BOX,
    decode_tilemap_cells,
    decode_tilemap_rows,
    extract_box_lines,
    extract_menu_state,
    is_box_present,
)

TILEMAP_WIDTH = 20
TILEMAP_HEIGHT = 18

SYMBOL_NAMES = (
    "wTileMap",
    "wCurrentMenuItem",
    "wMenuJoypadPollCount",
    "wTopMenuItemY",
    "wTopMenuItemX",
    "wMaxMenuItem",
    "wMenuWatchedKeys",
    "wMenuCursorLocation",
    "wListScrollOffset",
    "wIsInBattle",
    "wCurOpponent",
    "wBattleType",
    "wCurMap",
    "wCurMapScript",
    "wCurMapWidth",
    "wCurMapHeight",
    "wYCoord",
    "wXCoord",
    "wDestinationWarpID",
    "wWarpedFromWhichWarp",
    "wPlayerMovingDirection",
    "wPlayerLastStopDirection",
    "wPlayerDirection",
    "hJoyReleased",
    "hJoyPressed",
    "hJoyHeld",
    "hJoyInput",
)


@dataclass(frozen=True)
class TelemetryAddresses:
    values: dict[str, int]

    @classmethod
    def from_symbols(cls, symbols: SymbolTable) -> "TelemetryAddresses":
        return cls(values={name: symbols.address_of(name) for name in SYMBOL_NAMES})

    def __getitem__(self, name: str) -> int:
        return self.values[name]


def _blank_ratio(decoded_rows: list[str]) -> float:
    total_tiles = len(decoded_rows) * len(decoded_rows[0]) if decoded_rows else 0
    if total_tiles == 0:
        return 1.0
    blank_tiles = sum(ch == " " for row in decoded_rows for ch in row)
    return blank_tiles / total_tiles


def derive_events(previous: dict | None, current: dict) -> list[dict]:
    if previous is None:
        return [{"frame": current["frame"], "type": "runtime_ready", "label": "Runtime ready"}]

    events: list[dict] = []
    frame = current["frame"]

    if previous["mode"] != current["mode"]:
        events.append(
            {
                "frame": frame,
                "type": "mode_changed",
                "label": f"Mode: {previous['mode']} -> {current['mode']}",
            }
        )

    prev_map = previous["map"]
    curr_map = current["map"]
    if prev_map["id"] != curr_map["id"]:
        prev_name = prev_map.get("name") or f"Map {prev_map['id']}"
        curr_name = curr_map.get("name") or f"Map {curr_map['id']}"
        events.append(
            {
                "frame": frame,
                "type": "map_changed",
                "label": f"Map changed: {prev_name} -> {curr_name}",
            }
        )
    elif (prev_map["x"], prev_map["y"]) != (curr_map["x"], curr_map["y"]):
        events.append(
            {
                "frame": frame,
                "type": "player_moved",
                "label": (
                    f"Player moved: ({prev_map['x']}, {prev_map['y']}) -> "
                    f"({curr_map['x']}, {curr_map['y']})"
                ),
            }
        )

    prev_battle = previous["battle"]["in_battle"]
    curr_battle = current["battle"]["in_battle"]
    if prev_battle != curr_battle:
        events.append(
            {
                "frame": frame,
                "type": "battle_started" if curr_battle else "battle_ended",
                "label": "Entered battle" if curr_battle else "Exited battle",
            }
        )

    prev_menu = previous["menu"]
    curr_menu = current["menu"]
    if not prev_menu["active"] and curr_menu["active"]:
        label = "Opened menu"
        if curr_menu["selected_item_text"]:
            label = f"Opened menu: {curr_menu['selected_item_text']}"
        events.append({"frame": frame, "type": "menu_opened", "label": label})
    elif prev_menu["active"] and not curr_menu["active"]:
        events.append({"frame": frame, "type": "menu_closed", "label": "Closed menu"})
    elif (
        curr_menu["active"]
        and prev_menu["selected_item_text"] != curr_menu["selected_item_text"]
        and curr_menu["selected_item_text"]
    ):
        events.append(
            {
                "frame": frame,
                "type": "menu_selection_changed",
                "label": f"Menu selection: {curr_menu['selected_item_text']}",
            }
        )

    prev_dialogue = previous["dialogue"]["visible_lines"]
    curr_dialogue = current["dialogue"]["visible_lines"]
    if not prev_dialogue and curr_dialogue:
        events.append(
            {
                "frame": frame,
                "type": "dialogue_opened",
                "label": f"Dialogue: {' / '.join(curr_dialogue)}",
            }
        )
    elif prev_dialogue and not curr_dialogue:
        events.append({"frame": frame, "type": "dialogue_closed", "label": "Dialogue closed"})
    elif prev_dialogue != curr_dialogue and curr_dialogue:
        events.append(
            {
                "frame": frame,
                "type": "dialogue_updated",
                "label": f"Dialogue: {' / '.join(curr_dialogue)}",
            }
        )

    return events


def build_telemetry(pyboy, addresses: TelemetryAddresses) -> dict:
    mem = pyboy.memory

    tilemap = [
        mem[addresses["wTileMap"] + offset]
        for offset in range(TILEMAP_WIDTH * TILEMAP_HEIGHT)
    ]
    tilemap_rows = [
        tilemap[row_start:row_start + TILEMAP_WIDTH]
        for row_start in range(0, len(tilemap), TILEMAP_WIDTH)
    ]
    decoded_rows = decode_tilemap_rows(tilemap_rows, DEFAULT_CHARMAP)
    decoded_cells = decode_tilemap_cells(tilemap_rows, DEFAULT_CHARMAP)

    map_id = mem[addresses["wCurMap"]]
    map_width = mem[addresses["wCurMapWidth"]]
    map_height = mem[addresses["wCurMapHeight"]]
    menu_item = mem[addresses["wCurrentMenuItem"]]
    menu_poll_count = mem[addresses["wMenuJoypadPollCount"]]
    top_menu_y = mem[addresses["wTopMenuItemY"]]
    top_menu_x = mem[addresses["wTopMenuItemX"]]
    max_menu_item = mem[addresses["wMaxMenuItem"]]
    menu_watched_keys = mem[addresses["wMenuWatchedKeys"]]
    list_scroll_offset = mem[addresses["wListScrollOffset"]]
    in_battle = mem[addresses["wIsInBattle"]] != 0
    menu_cursor_addr = mem[addresses["wMenuCursorLocation"]] | (
        mem[addresses["wMenuCursorLocation"] + 1] << 8
    )
    message_box_present = is_box_present(tilemap_rows, *MESSAGE_BOX)
    dialogue_lines = extract_box_lines(
        decoded_rows,
        x0=MESSAGE_BOX[0],
        y0=MESSAGE_BOX[1],
        x1=MESSAGE_BOX[2],
        y1=MESSAGE_BOX[3],
        decoded_cells=decoded_cells,
    ) if message_box_present else []
    dialogue_active = bool(dialogue_lines) or message_box_present
    dialogue_source = "tilemap" if dialogue_lines else "box_only" if message_box_present else "none"

    menu_cursor_offset = menu_cursor_addr - addresses["wTileMap"]
    if 0 <= menu_cursor_offset < TILEMAP_WIDTH * TILEMAP_HEIGHT:
        menu_cursor = {
            "address": menu_cursor_addr,
            "x": menu_cursor_offset % TILEMAP_WIDTH,
            "y": menu_cursor_offset // TILEMAP_WIDTH,
        }
    else:
        menu_cursor = {
            "address": menu_cursor_addr,
            "x": None,
            "y": None,
        }

    menu_state = extract_menu_state(
        decoded_rows,
        top_menu_x,
        top_menu_y,
        max_menu_item,
        cursor_x=menu_cursor["x"],
        cursor_y=menu_cursor["y"],
    )
    selected_menu_line = None
    if menu_state.selected_index is not None and 0 <= menu_state.selected_index < len(menu_state.items):
        selected_menu_line = menu_state.items[menu_state.selected_index]

    blank_ratio = _blank_ratio(decoded_rows)
    if in_battle:
        mode = "battle"
    elif message_box_present and menu_state.active:
        mode = "menu_dialogue"
    elif message_box_present:
        mode = "dialogue"
    elif menu_state.active:
        mode = "menu"
    elif blank_ratio > 0.92:
        mode = "transition"
    else:
        mode = "field"

    return {
        "frame": pyboy.frame_count,
        "mode": mode,
        "map": {
            "id": map_id,
            "script": mem[addresses["wCurMapScript"]],
            "x": mem[addresses["wXCoord"]],
            "y": mem[addresses["wYCoord"]],
            "width": map_width,
            "height": map_height,
        },
        "movement": {
            "facing": decode_player_direction(mem[addresses["wPlayerDirection"]]),
            "moving_direction": decode_player_direction(mem[addresses["wPlayerMovingDirection"]]),
            "last_stop_direction": decode_player_direction(mem[addresses["wPlayerLastStopDirection"]]),
            "destination_warp_id": mem[addresses["wDestinationWarpID"]],
            "warped_from_which_warp": mem[addresses["wWarpedFromWhichWarp"]],
        },
        "menu": {
            "active": menu_state.active,
            "current_item": menu_item,
            "poll_count": menu_poll_count,
            "top_item_x": top_menu_x,
            "top_item_y": top_menu_y,
            "max_item": max_menu_item,
            "watched_keys": menu_watched_keys,
            "list_scroll_offset": list_scroll_offset,
            "cursor": menu_cursor,
            "cursor_visible": menu_state.cursor_visible,
            "cursor_tile": menu_state.cursor_tile,
            "candidate_rows": menu_state.candidate_rows,
            "raw_segments": menu_state.raw_segments,
            "selected_index": menu_state.selected_index,
            "visible_items": menu_state.items,
            "selected_item_text": selected_menu_line,
        },
        "battle": {
            "in_battle": in_battle,
            "type": mem[addresses["wBattleType"]],
            "opponent": mem[addresses["wCurOpponent"]],
        },
        "input": {
            "input": mem[addresses["hJoyInput"]],
            "held": mem[addresses["hJoyHeld"]],
            "pressed": mem[addresses["hJoyPressed"]],
            "released": mem[addresses["hJoyReleased"]],
        },
        "screen": {
            "tilemap_rows": tilemap_rows,
            "decoded_rows": decoded_rows,
            "tilemap_rows_hex": [
                " ".join(f"{value:02x}" for value in row)
                for row in tilemap_rows
            ],
            "message_box_present": message_box_present,
            "blank_ratio": round(blank_ratio, 4),
        },
        "dialogue": {
            "visible_lines": dialogue_lines,
            "active": dialogue_active,
            "source": dialogue_source,
        },
    }
