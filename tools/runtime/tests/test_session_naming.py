from __future__ import annotations

import unittest

from tools.runtime.session import RuntimeSession


def _session(*, nickname_policy: str = "decline") -> RuntimeSession:
    session = RuntimeSession.__new__(RuntimeSession)
    session._decision_preference = lambda key, default=None: nickname_policy if key == "nickname_policy" else default
    return session


def _snapshot(*, current_text: str = "", screen_type: str = "pokemon") -> dict:
    return {
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


if __name__ == "__main__":
    unittest.main()
