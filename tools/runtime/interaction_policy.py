from __future__ import annotations

from typing import Any

from .map_data import MapCatalog
from .navigator import choose_field_action as choose_navigation_field_action
from .objective_inference import record_map_history
from .runtime_memory import advance_field_move_index, decision_preference, set_decision_flag


def choose_planner_action(snapshot: dict[str, Any], *, decision_state: dict[str, Any], map_catalog: MapCatalog, goal: str) -> dict[str, Any]:
    if goal != "progress":
        raise ValueError(f"Unsupported planner goal '{goal}'")

    interaction_type = snapshot.get("interaction", {}).get("type")
    if interaction_type and interaction_type != "field":
        return choose_interaction_action(snapshot, decision_state=decision_state)

    mode = snapshot["mode"]
    if mode == "menu_dialogue":
        return choose_menu_action(snapshot, decision_state=decision_state, allow_dialogue_fallback=True)
    if mode == "dialogue":
        return {
            "type": "routine",
            "name": "advance_dialogue",
            "reason": "Visible dialogue is active, so advance it with A.",
        }
    if mode == "menu":
        return choose_menu_action(snapshot, decision_state=decision_state, allow_dialogue_fallback=False)
    if mode == "field":
        return choose_field_action(snapshot, decision_state=decision_state, map_catalog=map_catalog, strategy="objective")
    if mode == "battle":
        return choose_battle_action(snapshot)
    return {
        "type": "tick",
        "frames": 10,
        "reason": "Wait through transition/loading frames before deciding again.",
    }


def choose_interaction_action(snapshot: dict[str, Any], *, decision_state: dict[str, Any]) -> dict[str, Any]:
    interaction = snapshot.get("interaction", {})
    interaction_type = interaction.get("type")
    if interaction_type in {"dialogue", "battle_dialogue"}:
        return {
            "type": "routine",
            "name": "advance_dialogue",
            "reason": "Visible dialogue is active, so advance it with A.",
        }
    if interaction_type == "pokedex_info":
        return {
            "type": "routine",
            "name": "advance_dialogue",
            "reason": "A full-screen Pokédex info card is open and waits for A or B, so advance it with A.",
        }
    if interaction_type == "binary_choice":
        return choose_binary_choice_action(snapshot, decision_state=decision_state)
    if interaction_type == "preset_name_choice":
        return choose_menu_action(snapshot, decision_state=decision_state, allow_dialogue_fallback=True)
    if interaction_type == "text_entry":
        return choose_text_entry_action(snapshot, decision_state=decision_state)
    if interaction_type in {"battle_command_menu", "battle_move_menu"}:
        return choose_battle_action(snapshot)
    if interaction_type in {"list_choice", "menu_dialogue"}:
        return choose_menu_action(snapshot, decision_state=decision_state, allow_dialogue_fallback=True)
    if interaction_type == "battle_transition":
        return {
            "type": "tick",
            "frames": 20,
            "reason": "Battle state is transitioning; wait briefly for the next stable input window.",
        }
    return {
        "type": "tick",
        "frames": 10,
        "reason": "Interaction state is ambiguous, so re-observe briefly.",
    }


def choose_field_action(
    snapshot: dict[str, Any],
    *,
    decision_state: dict[str, Any],
    map_catalog: MapCatalog,
    strategy: str = "objective",
    objective_id: str | None = None,
    affordance_id: str | None = None,
) -> dict[str, Any]:
    recent_event_types = [event["type"] for event in snapshot["events"]["recent"][-4:]]
    if snapshot["dialogue"]["active"] or snapshot["screen"].get("message_box_present"):
        return {
            "type": "routine",
            "name": "advance_dialogue",
            "reason": "A dialogue box is visible, so continue it with A instead of treating the scene as free movement.",
        }

    if "dialogue_closed" in recent_event_types or "menu_closed" in recent_event_types:
        return {
            "type": "tick",
            "frames": 20,
            "reason": "A script-driven dialogue or menu just closed, so wait briefly for the next stable field state.",
        }
    return choose_navigation_field_action(
        snapshot,
        decision_state=decision_state,
        map_catalog=map_catalog,
        strategy=strategy,
        objective_id=objective_id,
        preferred_affordance_id=affordance_id,
    )


def choose_menu_action(snapshot: dict[str, Any], *, decision_state: dict[str, Any], allow_dialogue_fallback: bool) -> dict[str, Any]:
    visible_items = snapshot["menu"]["visible_items"]
    selected = snapshot["menu"]["selected_item_text"]
    current_index = snapshot["menu"]["selected_index"]
    dialogue_lines = snapshot["dialogue"]["visible_lines"]

    target_label = select_menu_target(snapshot, decision_state=decision_state)
    if target_label is not None:
        target_index = visible_items.index(target_label)
        if current_index is None or current_index == target_index:
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": f"Menu target '{target_label}' is selected, so confirm it.",
            }
        if current_index > target_index:
            return {
                "type": "routine",
                "name": "move_up",
                "reason": f"Move menu selection up toward '{target_label}'.",
            }
        return {
            "type": "routine",
            "name": "move_down",
            "reason": f"Move menu selection down toward '{target_label}'.",
        }

    if "NEW GAME" in visible_items:
        return {
            "type": "routine",
            "name": "advance_dialogue" if selected == "NEW GAME" else "move_up",
            "reason": "Title menu is open; target NEW GAME.",
        }

    if selected:
        return {
            "type": "routine",
            "name": "advance_dialogue",
            "reason": "A menu item is selected, so confirm it.",
        }

    if allow_dialogue_fallback and dialogue_lines:
        return {
            "type": "routine",
            "name": "advance_dialogue",
            "reason": "Dialogue is visible alongside the menu, so try advancing.",
        }

    return {
        "type": "routine",
        "name": "close_menu",
        "reason": "Menu is open without a clear target, so close it.",
    }


def select_menu_target(snapshot: dict[str, Any], *, decision_state: dict[str, Any]) -> str | None:
    visible_items = snapshot["menu"]["visible_items"]
    if not visible_items:
        return None

    dialogue_lines = snapshot["dialogue"]["visible_lines"]
    normalized_dialogue = " ".join(dialogue_lines).lower()
    upper_items = {item.upper(): item for item in visible_items}
    preferred_binary = determine_binary_choice(snapshot, decision_state=decision_state)
    if preferred_binary and preferred_binary in upper_items:
        return upper_items[preferred_binary]

    interaction = snapshot.get("interaction") or {}
    details = interaction.get("details") or {}
    name_kind = details.get("name_kind")

    if name_kind == "player":
        target = select_preset_name_target(visible_items, decision_preference(decision_state, "player_name"))
        if target is not None:
            return target

    if name_kind == "rival":
        target = select_preset_name_target(visible_items, decision_preference(decision_state, "rival_name"))
        if target is not None:
            return target

    if "your name" in normalized_dialogue:
        target = select_preset_name_target(visible_items, decision_preference(decision_state, "player_name"))
        if target is not None:
            return target

    if "his name" in normalized_dialogue or "rival" in normalized_dialogue:
        target = select_preset_name_target(visible_items, decision_preference(decision_state, "rival_name"))
        if target is not None:
            return target

    if (snapshot.get("interaction") or {}).get("type") == "preset_name_choice":
        target = select_preset_name_target(visible_items, None)
        if target is not None:
            return target

    for candidate in ("CANCEL", "EXIT"):
        if candidate in upper_items:
            return upper_items[candidate]

    return None


def select_preset_name_target(visible_items: list[str], preferred_name: str | None) -> str | None:
    upper_items = {item.upper(): item for item in visible_items}
    if preferred_name:
        normalized_name = preferred_name.upper()
        if normalized_name in upper_items:
            return upper_items[normalized_name]

    preset_items = [
        item for item in visible_items
        if item.upper() not in {"NEW NAME", "CANCEL", "EXIT"}
    ]
    if preset_items:
        return preset_items[0]

    if "NEW NAME" in upper_items:
        return upper_items["NEW NAME"]

    return None


def choose_binary_choice_action(snapshot: dict[str, Any], *, decision_state: dict[str, Any]) -> dict[str, Any]:
    visible_items = snapshot["menu"]["visible_items"]
    current_index = snapshot["menu"]["selected_index"]
    preferred = determine_binary_choice(snapshot, decision_state=decision_state) or "YES"
    upper_items = {item.upper(): index for index, item in enumerate(visible_items)}
    target_index = upper_items.get(preferred)
    if target_index is None:
        return {
            "type": "routine",
            "name": "advance_dialogue",
            "reason": "A binary choice is visible and the preferred option is already implied, so confirm it.",
        }
    if current_index is None or current_index == target_index:
        return {
            "type": "routine",
            "name": "advance_dialogue",
            "reason": f"Choose {preferred} for the current prompt.",
        }
    if current_index > target_index:
        return {
            "type": "routine",
            "name": "move_up",
            "reason": f"Move the binary-choice cursor toward {preferred}.",
        }
    return {
        "type": "routine",
        "name": "move_down",
        "reason": f"Move the binary-choice cursor toward {preferred}.",
    }


def determine_binary_choice(snapshot: dict[str, Any], *, decision_state: dict[str, Any]) -> str | None:
    interaction = snapshot.get("interaction") or {}
    details = interaction.get("details") or {}
    choice_kind = details.get("choice_kind")
    offered_species = (details.get("offered_species") or "").upper() or None
    prompt = (interaction.get("prompt") or "").lower()
    if not prompt:
        prompt = " ".join(snapshot["dialogue"]["visible_lines"]).lower()

    if choice_kind == "nickname_prompt":
        return "NO" if decision_preference(decision_state, "nickname_policy") == "decline" else "YES"
    if choice_kind == "starter_offer":
        preferred = str(decision_preference(decision_state, "starter_preference", "SQUIRTLE")).upper()
        if snapshot["party"].get("player_starter"):
            return "NO"
        return "YES" if offered_species == preferred else "NO"
    if choice_kind in {"save_prompt", "confirmation"}:
        return "YES"
    if "nickname" in prompt:
        return "NO" if decision_preference(decision_state, "nickname_policy") == "decline" else "YES"
    if "you want the" in prompt:
        preferred = str(decision_preference(decision_state, "starter_preference", "SQUIRTLE")).lower()
        return "YES" if preferred in prompt else "NO"
    if "save" in prompt or "sure" in prompt or "okay" in prompt or "ready" in prompt:
        return "YES"
    return "YES"


def choose_text_entry_action(snapshot: dict[str, Any], *, decision_state: dict[str, Any]) -> dict[str, Any]:
    naming = snapshot["naming"]
    desired_text = desired_name_for_screen(snapshot, decision_state=decision_state)
    current_text = naming["current_text"]
    if current_text == desired_text:
        return {
            "type": "action",
            "button": "start",
            "reason": f"The desired name '{desired_text}' is already entered, so submit it with Start.",
        }
    if not desired_text.startswith(current_text):
        return {
            "type": "action",
            "button": "b",
            "reason": f"Backspace to realign the current name with the desired target '{desired_text}'.",
        }

    next_char = desired_text[len(current_text)]
    target_position = find_naming_character(snapshot, next_char)
    if target_position is None:
        return {
            "type": "tick",
            "frames": 10,
            "reason": f"Wait briefly because the naming keyboard could not locate '{next_char}'.",
        }

    current_row = naming.get("cursor_row")
    current_col = naming.get("cursor_col")
    target_row, target_col = target_position
    if current_row is None or current_col is None:
        return {
            "type": "tick",
            "frames": 10,
            "reason": "Wait briefly because the naming cursor position is not yet stable.",
        }
    if current_row > target_row:
        return {"type": "routine", "name": "move_up", "reason": f"Move naming cursor up toward '{next_char}'."}
    if current_row < target_row:
        return {"type": "routine", "name": "move_down", "reason": f"Move naming cursor down toward '{next_char}'."}
    if current_col > target_col:
        return {"type": "routine", "name": "move_left", "reason": f"Move naming cursor left toward '{next_char}'."}
    if current_col < target_col:
        return {"type": "routine", "name": "move_right", "reason": f"Move naming cursor right toward '{next_char}'."}
    return {
        "type": "routine",
        "name": "advance_dialogue",
        "reason": f"Select '{next_char}' on the naming keyboard.",
    }


def desired_name_for_screen(snapshot: dict[str, Any], *, decision_state: dict[str, Any]) -> str:
    naming = snapshot["naming"]
    screen_type = naming.get("screen_type")
    if screen_type == "player":
        return str(decision_preference(decision_state, "player_name", "RED"))
    if screen_type == "rival":
        return str(decision_preference(decision_state, "rival_name", "BLUE"))
    if screen_type == "pokemon" and decision_preference(decision_state, "nickname_policy") == "decline":
        return ""
    base_name = naming.get("base_name") or "MON"
    if decision_preference(decision_state, "nickname_policy") == "decline":
        return base_name
    return base_name


def find_naming_character(snapshot: dict[str, Any], target_char: str) -> tuple[int, int] | None:
    keyboard_rows = snapshot["naming"].get("keyboard_rows") or []
    for row_index, row in enumerate(keyboard_rows[:5]):
        for col_index, char in enumerate(row):
            if char == target_char:
                return row_index, col_index
    return None


def choose_battle_action(snapshot: dict[str, Any]) -> dict[str, Any]:
    battle = snapshot["battle"]
    if battle["ui_state"] == "dialogue":
        return {
            "type": "routine",
            "name": "advance_dialogue",
            "reason": "Visible battle dialogue is active, so advance it with A.",
        }
    if battle["ui_state"] == "command_menu":
        selected = battle["command_menu"]["selected_command"]
        if selected == "FIGHT":
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": "The FIGHT command is selected, so confirm it.",
            }
        command_positions = {
            "FIGHT": 0,
            "PKMN": 1,
            "ITEM": 2,
            "RUN": 3,
        }
        selected_index = battle["command_menu"]["selected_index"]
        target_index = command_positions["FIGHT"]
        if selected_index is None:
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": "The battle command menu is visible; try confirming the default command.",
            }
        if selected_index in {1, 3} and target_index in {0, 2}:
            return {"type": "routine", "name": "move_up", "reason": "Move the battle cursor toward FIGHT."}
        if selected_index in {2, 3} and target_index in {0, 1}:
            return {"type": "routine", "name": "move_left", "reason": "Move the battle cursor toward FIGHT."}
        if selected_index == 0:
            return {"type": "routine", "name": "advance_dialogue", "reason": "Confirm FIGHT."}
    if battle["ui_state"] == "move_menu":
        moves = battle["move_menu"]["moves"]
        if not moves:
            return {
                "type": "tick",
                "frames": 10,
                "reason": "The move menu is visible but the available moves are not parsed yet.",
            }
        preferred = max(
            moves,
            key=lambda move: (
                1 if move["pp"] > 0 else 0,
                move["power"],
                move["accuracy"] or 0,
            ),
        )
        selected_index = battle["move_menu"]["selected_index"]
        if selected_index is None or selected_index == preferred["slot"]:
            return {
                "type": "routine",
                "name": "advance_dialogue",
                "reason": f"Use {preferred['name']} because it is the strongest available move with PP remaining.",
            }
        if selected_index > preferred["slot"]:
            return {"type": "routine", "name": "move_up", "reason": f"Move the battle cursor toward {preferred['name']}."}
        return {"type": "routine", "name": "move_down", "reason": f"Move the battle cursor toward {preferred['name']}."}
    return {
        "type": "tick",
        "frames": 10,
        "reason": "Battle state is in transition, so wait for the next clear prompt.",
    }


def update_decision_state(decision_state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    record_map_history(decision_state, snapshot)
    dialogue = " ".join(snapshot["dialogue"]["visible_lines"]).lower()
    intro_markers = (
        "hello there",
        "world of pok",
        "my name is oak",
        "what is your name",
        "what is his name again",
        "your rival since",
        "remember now! his name is",
        "your very own",
        "legend is",
        "adventures",
    )
    gameplay_markers = (
        "playing the snes",
        "...okay!",
    )

    if any(marker in dialogue for marker in intro_markers):
        set_decision_flag(decision_state, "oak_intro_active", True)
    if any(marker in dialogue for marker in gameplay_markers):
        set_decision_flag(decision_state, "oak_intro_active", False)


def update_move_strategy(decision_state: dict[str, Any], decision: dict[str, Any], passed: bool) -> None:
    name = decision.get("name", "")
    if not name.startswith("move_"):
        return
    if not passed:
        advance_field_move_index(decision_state)
