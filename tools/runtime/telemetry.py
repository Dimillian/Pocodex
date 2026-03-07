from __future__ import annotations

from dataclasses import dataclass
import re

from .game_data import DEFAULT_MOVE_CATALOG
from .navigator import decode_player_direction
from .symbols import SymbolTable
from .tilemap import (
    DEFAULT_CHARMAP,
    MESSAGE_BOX,
    clean_ui_text,
    decode_tilemap_cells,
    decode_tilemap_rows,
    decode_text_bytes,
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
    "wMoveMenuType",
    "wPlayerMoveListIndex",
    "wPlayerSelectedMove",
    "wBattleMenuCurrentPP",
    "wPlayerMovePower",
    "wPlayerMoveType",
    "wPlayerMoveAccuracy",
    "wPlayerMoveMaxPP",
    "wBattleMonNick",
    "wBattleMonHP",
    "wBattleMonMaxHP",
    "wBattleMonLevel",
    "wBattleMonMoves",
    "wBattleMonPP",
    "wBattleMonStatus",
    "wEnemyMonNick",
    "wEnemyMonHP",
    "wEnemyMonMaxHP",
    "wEnemyMonLevel",
    "wEnemyMonMoves",
    "wEnemyMonPP",
    "wEnemyMonStatus",
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
    "wNameBuffer",
    "wStringBuffer",
    "wNamingScreenType",
    "wNamingScreenNameLength",
    "wNamingScreenSubmitName",
    "wNamingScreenLetter",
    "wPlayerStarter",
    "wRivalStarter",
    "wCurPartySpecies",
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


def _read_u16_be(mem, address: int) -> int:
    return (mem[address] << 8) | mem[address + 1]


def _decode_ram_text(mem, address: int, length: int) -> str:
    values = [mem[address + offset] for offset in range(length)]
    return decode_text_bytes(values, DEFAULT_CHARMAP)


DECORATION_TOKEN_RE = re.compile(r"BOLD_[A-Z]|<[0-9A-Fa-f]{2}>")
UPPER_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9'.-]{2,}")
DEX_NUMBER_RE = re.compile(r"(?:NO\.|·\.|\.)(\d{3})", re.IGNORECASE)


def _normalize_overlay_row(row: str) -> str:
    cleaned = DECORATION_TOKEN_RE.sub(" ", row)
    cleaned = clean_ui_text(cleaned)
    return " ".join(cleaned.split())


def _build_battle_state(
    mem,
    addresses: TelemetryAddresses,
    *,
    decoded_rows: list[str],
    decoded_cells: list[list[str]],
    message_box_present: bool,
    dialogue_lines: list[str],
    menu_item: int,
) -> dict:
    in_battle = mem[addresses["wIsInBattle"]] != 0
    player_move_ids = [
        mem[addresses["wBattleMonMoves"] + index]
        for index in range(4)
    ]
    player_move_pp = [
        mem[addresses["wBattleMonPP"] + index] & 0x3F
        for index in range(4)
    ]
    player_moves = []
    for index, move_id in enumerate(player_move_ids):
        if move_id == 0:
            continue
        info = DEFAULT_MOVE_CATALOG.get(move_id)
        player_moves.append(
            {
                "slot": index,
                "move_id": move_id,
                "name": info.name if info else f"MOVE_{move_id}",
                "power": info.power if info else 0,
                "type_name": info.type_name if info else "UNKNOWN",
                "accuracy": info.accuracy if info else None,
                "pp": player_move_pp[index],
                "max_pp": info.pp if info else None,
            }
        )

    battle_rows = decoded_rows[12:18]
    battle_text = " ".join(clean_ui_text(row) for row in battle_rows).upper()
    command_labels = ["FIGHT", "PKMN", "ITEM", "RUN"]
    command_menu_visible = (
        not message_box_present
        and "FIGHT" in battle_text
        and "RUN" in battle_text
    )
    move_menu_visible = (
        not message_box_present
        and bool(player_moves)
        and any(move["name"] in battle_text for move in player_moves)
    )
    if message_box_present or dialogue_lines:
        ui_state = "dialogue"
    elif move_menu_visible:
        ui_state = "move_menu"
    elif command_menu_visible:
        ui_state = "command_menu"
    elif in_battle:
        ui_state = "transition"
    else:
        ui_state = "none"

    selected_command_index = menu_item if command_menu_visible and 0 <= menu_item < len(command_labels) else None
    selected_move_index = menu_item - 1 if move_menu_visible and 1 <= menu_item <= 4 else None
    if selected_move_index is not None and selected_move_index >= len(player_moves):
        selected_move_index = None

    return {
        "in_battle": in_battle,
        "type": mem[addresses["wBattleType"]],
        "opponent": mem[addresses["wCurOpponent"]],
        "ui_state": ui_state,
        "command_menu": {
            "visible": command_menu_visible,
            "commands": command_labels if command_menu_visible else [],
            "selected_index": selected_command_index,
            "selected_command": (
                command_labels[selected_command_index]
                if selected_command_index is not None
                else None
            ),
        },
        "move_menu": {
            "visible": move_menu_visible,
            "moves": player_moves,
            "selected_index": selected_move_index,
            "selected_move": (
                player_moves[selected_move_index]
                if selected_move_index is not None
                else None
            ),
            "menu_type": mem[addresses["wMoveMenuType"]],
            "selected_move_id": mem[addresses["wPlayerSelectedMove"]],
            "selected_move_slot": mem[addresses["wPlayerMoveListIndex"]],
            "current_pp": mem[addresses["wBattleMenuCurrentPP"]] & 0x3F,
            "current_move_power": mem[addresses["wPlayerMovePower"]],
            "current_move_type": mem[addresses["wPlayerMoveType"]],
            "current_move_accuracy": mem[addresses["wPlayerMoveAccuracy"]],
            "current_move_max_pp": mem[addresses["wPlayerMoveMaxPP"]],
        },
        "player": {
            "nickname": _decode_ram_text(mem, addresses["wBattleMonNick"], 11),
            "hp": _read_u16_be(mem, addresses["wBattleMonHP"]),
            "max_hp": _read_u16_be(mem, addresses["wBattleMonMaxHP"]),
            "level": mem[addresses["wBattleMonLevel"]],
            "status": mem[addresses["wBattleMonStatus"]],
        },
        "enemy": {
            "nickname": _decode_ram_text(mem, addresses["wEnemyMonNick"], 11),
            "hp": _read_u16_be(mem, addresses["wEnemyMonHP"]),
            "max_hp": _read_u16_be(mem, addresses["wEnemyMonMaxHP"]),
            "level": mem[addresses["wEnemyMonLevel"]],
            "status": mem[addresses["wEnemyMonStatus"]],
        },
    }


def _build_naming_state(
    mem,
    addresses: TelemetryAddresses,
    *,
    decoded_cells: list[list[str]],
    decoded_rows: list[str],
    top_menu_x: int,
    menu_item: int,
    max_menu_item: int,
) -> dict:
    prompt_rows = [clean_ui_text(row) for row in decoded_rows[:5] if "NAME?" in row or "NICKNAME?" in row]
    keyboard_rows = [
        "".join(decoded_cells[row_index][2:11]).strip()
        for row_index in range(5, 10)
        if row_index < len(decoded_cells)
    ]
    active = bool(prompt_rows) and max_menu_item == 7 and top_menu_x >= 1
    if not active:
        return {
            "active": False,
            "screen_type": None,
            "screen_type_id": None,
            "prompt": None,
            "current_text": "",
            "base_name": "",
            "current_length": 0,
            "submit_pending": False,
            "current_letter": "",
            "cursor_row": None,
            "cursor_col": None,
            "keyboard_rows": [],
        }
    screen_type_id = mem[addresses["wNamingScreenType"]]
    screen_type = {
        0: "player",
        1: "rival",
        2: "pokemon",
    }.get(screen_type_id, "unknown")
    cursor_row = menu_item - 1 if 1 <= menu_item <= 6 else None
    cursor_col = (top_menu_x - 1) // 2 if cursor_row is not None else None
    return {
        "active": active,
        "screen_type": screen_type,
        "screen_type_id": screen_type_id,
        "prompt": " ".join(prompt_rows).strip() or None,
        "current_text": _decode_ram_text(mem, addresses["wStringBuffer"], 11),
        "base_name": _decode_ram_text(mem, addresses["wNameBuffer"], 11),
        "current_length": mem[addresses["wNamingScreenNameLength"]],
        "submit_pending": mem[addresses["wNamingScreenSubmitName"]] != 0,
        "current_letter": decode_text_bytes([mem[addresses["wNamingScreenLetter"]]], DEFAULT_CHARMAP),
        "cursor_row": cursor_row,
        "cursor_col": cursor_col,
        "keyboard_rows": keyboard_rows,
    }


def _build_pokedex_state(decoded_rows: list[str]) -> dict:
    normalized_rows = [_normalize_overlay_row(row) for row in decoded_rows]
    height_row = next((row[row.index("HT") :] for row in normalized_rows if "HT" in row), "")
    weight_row = next((row[row.index("WT") :] for row in normalized_rows if "WT" in row), "")
    dex_number_row = next((row for row in normalized_rows if DEX_NUMBER_RE.search(row)), "")
    description_lines = [
        row
        for row in normalized_rows[10:17]
        if row and "HT" not in row and "WT" not in row
    ]
    species_name = ""
    species_class = ""
    for row in normalized_rows[1:6]:
        tokens = UPPER_TOKEN_RE.findall(row)
        if tokens:
            species_name = tokens[-1]
            break
    for row in normalized_rows[2:8]:
        tokens = [token for token in UPPER_TOKEN_RE.findall(row) if token != species_name]
        if tokens:
            species_class = tokens[-1]
            break

    active = bool(species_name and description_lines and (height_row or weight_row))
    if not active:
        return {
            "active": False,
            "species_name": None,
            "species_class": None,
            "dex_number": None,
            "height_weight": None,
            "description_lines": [],
        }

    return {
        "active": True,
        "species_name": species_name,
        "species_class": species_class or None,
        "dex_number": (f"No.{DEX_NUMBER_RE.search(dex_number_row).group(1)}" if dex_number_row else None),
        "height_weight": " / ".join(part for part in (height_row, weight_row) if part) or None,
        "description_lines": description_lines,
    }


def _build_interaction_state(snapshot: dict) -> dict:
    dialogue_active = snapshot["dialogue"]["active"] or snapshot["screen"]["message_box_present"]
    menu = snapshot["menu"]
    battle = snapshot["battle"]
    naming = snapshot["naming"]
    pokedex = snapshot["pokedex"]
    dialogue_text = " ".join(snapshot["dialogue"]["visible_lines"]).strip()

    if naming["active"]:
        return {
            "type": "text_entry",
            "prompt": naming["prompt"],
            "details": {
                "screen_type": naming["screen_type"],
                "current_text": naming["current_text"],
                "submit_pending": naming["submit_pending"],
            },
        }
    if pokedex["active"]:
        description = " ".join(pokedex["description_lines"]).strip()
        prompt = f"Pokédex info for {pokedex['species_name']}"
        if description:
            prompt = f"{prompt}: {description}"
        return {
            "type": "pokedex_info",
            "prompt": prompt,
            "details": {
                "species_name": pokedex["species_name"],
                "species_class": pokedex["species_class"],
                "dex_number": pokedex["dex_number"],
                "height_weight": pokedex["height_weight"],
                "description_lines": pokedex["description_lines"],
            },
        }
    if battle["in_battle"]:
        if battle["ui_state"] == "dialogue":
            return {"type": "battle_dialogue", "prompt": dialogue_text or None, "details": {}}
        if battle["ui_state"] == "move_menu":
            return {
                "type": "battle_move_menu",
                "prompt": dialogue_text or None,
                "details": {
                    "selected_move": battle["move_menu"]["selected_move"],
                    "moves": battle["move_menu"]["moves"],
                },
            }
        if battle["ui_state"] == "command_menu":
            return {
                "type": "battle_command_menu",
                "prompt": dialogue_text or None,
                "details": {
                    "selected_command": battle["command_menu"]["selected_command"],
                    "commands": battle["command_menu"]["commands"],
                },
            }
        return {"type": "battle_transition", "prompt": dialogue_text or None, "details": {}}
    if menu["active"] and {"YES", "NO"}.issubset({item.upper() for item in menu["visible_items"]}):
        return {
            "type": "binary_choice",
            "prompt": dialogue_text or None,
            "details": {
                "visible_items": menu["visible_items"],
                "selected_item_text": menu["selected_item_text"],
            },
        }
    if menu["active"]:
        return {
            "type": "list_choice",
            "prompt": dialogue_text or None,
            "details": {
                "visible_items": menu["visible_items"],
                "selected_item_text": menu["selected_item_text"],
            },
        }
    if dialogue_active:
        return {
            "type": "dialogue",
            "prompt": dialogue_text or None,
            "details": {
                "prompt_visible": any("▼" in row or "▶" in row or "▷" in row for row in snapshot["screen"]["decoded_rows"][12:18]),
            },
        }
    return {"type": "field", "prompt": None, "details": {}}


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
    battle_state = _build_battle_state(
        mem,
        addresses,
        decoded_rows=decoded_rows,
        decoded_cells=decoded_cells,
        message_box_present=message_box_present,
        dialogue_lines=dialogue_lines,
        menu_item=menu_item,
    )
    naming_state = _build_naming_state(
        mem,
        addresses,
        decoded_cells=decoded_cells,
        decoded_rows=decoded_rows,
        top_menu_x=top_menu_x,
        menu_item=menu_item,
        max_menu_item=max_menu_item,
    )
    pokedex_state = _build_pokedex_state(decoded_rows)
    if in_battle:
        mode = "battle"
    elif pokedex_state["active"]:
        mode = "pokedex"
    elif naming_state["active"]:
        mode = "naming"
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

    snapshot = {
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
        "battle": battle_state,
        "party": {
            "current_species": mem[addresses["wCurPartySpecies"]],
            "player_starter": mem[addresses["wPlayerStarter"]],
            "rival_starter": mem[addresses["wRivalStarter"]],
        },
        "pokedex": pokedex_state,
        "naming": naming_state,
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
    snapshot["interaction"] = _build_interaction_state(snapshot)
    return snapshot
