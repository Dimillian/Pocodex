from __future__ import annotations

import unittest

from tools.runtime.interaction_policy import (
    choose_text_entry_action,
    desired_name_for_screen,
    determine_binary_choice,
    select_menu_target,
)


def _decision_state(
    *,
    nickname_policy: str = "decline",
    starter_preference: str = "SQUIRTLE",
    player_name: str = "RED",
    rival_name: str = "BLUE",
) -> dict:
    return {
        "preferences": {
            "nickname_policy": nickname_policy,
            "starter_preference": starter_preference,
            "player_name": player_name,
            "rival_name": rival_name,
        }
    }


def _snapshot(*, current_text: str = "", screen_type: str = "pokemon") -> dict:
    return {
        "interaction": {"type": "text_entry", "details": {}},
        "party": {"player_starter": 0},
        "dialogue": {"visible_lines": []},
        "menu": {"visible_items": [], "selected_index": None},
        "naming": {
            "active": True,
            "screen_type": screen_type,
            "current_text": current_text,
            "base_name": "CHARMANDER",
            "cursor_row": 0,
            "cursor_col": 0,
            "keyboard_rows": [
                "A B C D E",
                "F G H I J",
                "K L M N O",
                "P Q R S T",
                "U V W X Y",
            ],
        },
    }


class InteractionPolicyNamingTests(unittest.TestCase):
    def test_decline_nickname_submits_empty_name_for_pokemon_screen(self) -> None:
        decision = choose_text_entry_action(
            _snapshot(current_text="", screen_type="pokemon"),
            decision_state=_decision_state(nickname_policy="decline"),
        )

        self.assertEqual(
            decision,
            {
                "type": "action",
                "button": "start",
                "reason": "The desired name '' is already entered, so submit it with Start.",
            },
        )

    def test_decline_nickname_backspaces_if_text_is_already_entered(self) -> None:
        decision = choose_text_entry_action(
            _snapshot(current_text="C", screen_type="pokemon"),
            decision_state=_decision_state(nickname_policy="decline"),
        )

        self.assertEqual(
            decision,
            {
                "type": "action",
                "button": "b",
                "reason": "Backspace to realign the current name with the desired target ''.",
            },
        )

    def test_decline_policy_does_not_affect_player_name_screen(self) -> None:
        desired = desired_name_for_screen(
            _snapshot(current_text="", screen_type="player"),
            decision_state=_decision_state(nickname_policy="decline"),
        )

        self.assertEqual(desired, "RED")

    def test_binary_choice_uses_classified_nickname_prompt(self) -> None:
        snapshot = {
            "interaction": {
                "type": "binary_choice",
                "prompt": "Would you like to give a nickname?",
                "details": {"choice_kind": "nickname_prompt"},
            },
            "dialogue": {"visible_lines": []},
            "party": {"player_starter": 0},
        }

        self.assertEqual(
            determine_binary_choice(snapshot, decision_state=_decision_state(nickname_policy="decline")),
            "NO",
        )

    def test_binary_choice_uses_offer_species_for_starter_prompt(self) -> None:
        snapshot = {
            "interaction": {
                "type": "binary_choice",
                "prompt": "Do you want this POKeMON?",
                "details": {"choice_kind": "starter_offer", "offered_species": "SQUIRTLE"},
            },
            "dialogue": {"visible_lines": []},
            "party": {"player_starter": 0},
        }

        self.assertEqual(
            determine_binary_choice(snapshot, decision_state=_decision_state(starter_preference="SQUIRTLE")),
            "YES",
        )

    def test_preset_name_choice_uses_name_kind_without_dialogue_text(self) -> None:
        snapshot = {
            "interaction": {
                "type": "preset_name_choice",
                "details": {"name_kind": "player"},
            },
            "dialogue": {"visible_lines": []},
            "menu": {
                "visible_items": ["RED", "ASH", "JACK", "NEW NAME"],
                "selected_index": 0,
            },
        }

        self.assertEqual(
            select_menu_target(snapshot, decision_state=_decision_state(player_name="ASH")),
            "ASH",
        )
