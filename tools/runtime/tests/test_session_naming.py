from __future__ import annotations

import unittest

from tools.runtime.session import RuntimeSession


def _session(
    *,
    nickname_policy: str = "decline",
    starter_preference: str = "SQUIRTLE",
    player_name: str = "RED",
    rival_name: str = "BLUE",
) -> RuntimeSession:
    session = RuntimeSession.__new__(RuntimeSession)
    preferences = {
        "nickname_policy": nickname_policy,
        "starter_preference": starter_preference,
        "player_name": player_name,
        "rival_name": rival_name,
    }
    session._decision_preference = lambda key, default=None: preferences.get(key, default)
    return session


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
        }
    }


class SessionNamingTests(unittest.TestCase):
    def test_decline_nickname_submits_empty_name_for_pokemon_screen(self) -> None:
        session = _session(nickname_policy="decline")

        decision = session._choose_text_entry_action(_snapshot(current_text="", screen_type="pokemon"))

        self.assertEqual(
            decision,
            {
                "type": "action",
                "button": "start",
                "reason": "The desired name '' is already entered, so submit it with Start.",
            },
        )

    def test_decline_nickname_backspaces_if_text_is_already_entered(self) -> None:
        session = _session(nickname_policy="decline")

        decision = session._choose_text_entry_action(_snapshot(current_text="C", screen_type="pokemon"))

        self.assertEqual(
            decision,
            {
                "type": "action",
                "button": "b",
                "reason": "Backspace to realign the current name with the desired target ''.",
            },
        )

    def test_decline_policy_does_not_affect_player_name_screen(self) -> None:
        session = _session(nickname_policy="decline")

        desired = session._desired_name_for_screen(_snapshot(current_text="", screen_type="player"))

        self.assertEqual(desired, "RED")

    def test_binary_choice_uses_classified_nickname_prompt(self) -> None:
        session = _session(nickname_policy="decline")
        snapshot = {
            "interaction": {
                "type": "binary_choice",
                "prompt": "Would you like to give a nickname?",
                "details": {"choice_kind": "nickname_prompt"},
            },
            "dialogue": {"visible_lines": []},
            "party": {"player_starter": 0},
        }

        self.assertEqual(session._determine_binary_choice(snapshot), "NO")

    def test_binary_choice_uses_offer_species_for_starter_prompt(self) -> None:
        session = _session(starter_preference="SQUIRTLE")
        snapshot = {
            "interaction": {
                "type": "binary_choice",
                "prompt": "Do you want this POKeMON?",
                "details": {"choice_kind": "starter_offer", "offered_species": "SQUIRTLE"},
            },
            "dialogue": {"visible_lines": []},
            "party": {"player_starter": 0},
        }

        self.assertEqual(session._determine_binary_choice(snapshot), "YES")

    def test_preset_name_choice_uses_name_kind_without_dialogue_text(self) -> None:
        session = _session(player_name="ASH")
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

        self.assertEqual(session._select_menu_target(snapshot), "ASH")


if __name__ == "__main__":
    unittest.main()
