from __future__ import annotations

import unittest

from tools.runtime.agent_context import build_agent_context
from tools.runtime.game_data import DEFAULT_ITEM_CATALOG, DEFAULT_SPECIES_CATALOG, KANTO_BADGE_NAMES
from tools.runtime.progress_memory import _made_progress
from tools.runtime.telemetry import (
    TelemetryAddresses,
    _build_inventory_state,
    _build_party_state,
    _build_trainer_state,
    _decode_bcd_money,
)
from tools.runtime.tilemap import DEFAULT_CHARMAP


TERMINATOR = 0x50
CHAR_TO_CODE = {
    text: code
    for code, text in DEFAULT_CHARMAP.code_to_text.items()
    if len(text) == 1
}


def _write_text(mem: list[int], address: int, text: str, *, length: int) -> None:
    values = [CHAR_TO_CODE[char] for char in text]
    values = values[: max(length - 1, 0)]
    values.append(TERMINATOR)
    while len(values) < length:
        values.append(TERMINATOR)
    for offset, value in enumerate(values):
        mem[address + offset] = value


class TrainerStateTelemetryTests(unittest.TestCase):
    def test_decode_bcd_money(self) -> None:
        mem = [0x00] * 8
        mem[1:4] = [0x01, 0x23, 0x45]
        money, money_bcd = _decode_bcd_money(mem, 1)
        self.assertEqual(money, 12345)
        self.assertEqual(money_bcd, "012345")

    def test_build_inventory_state(self) -> None:
        mem = [0x00] * 64
        addresses = TelemetryAddresses(
            values={
                "wNumBagItems": 4,
                "wBagItems": 8,
            }
        )
        mem[addresses["wNumBagItems"]] = 2
        mem[addresses["wBagItems"] : addresses["wBagItems"] + 4] = [0x04, 10, 0x14, 3]

        inventory = _build_inventory_state(mem, addresses)

        self.assertEqual(inventory["count"], 2)
        self.assertEqual(
            inventory["items"],
            [
                {"slot": 0, "item_id": 0x04, "name": DEFAULT_ITEM_CATALOG[0x04], "quantity": 10},
                {"slot": 1, "item_id": 0x14, "name": DEFAULT_ITEM_CATALOG[0x14], "quantity": 3},
            ],
        )

    def test_build_trainer_state(self) -> None:
        mem = [0x00] * 64
        addresses = TelemetryAddresses(
            values={
                "wPlayerMoney": 12,
                "wObtainedBadges": 20,
            }
        )
        mem[addresses["wPlayerMoney"] : addresses["wPlayerMoney"] + 3] = [0x00, 0x12, 0x34]
        mem[addresses["wObtainedBadges"]] = (1 << 0) | (1 << 2) | (1 << 7)

        trainer = _build_trainer_state(mem, addresses)

        self.assertEqual(trainer["money"], 1234)
        self.assertEqual(trainer["money_bcd"], "001234")
        self.assertEqual(trainer["badge_count"], 3)
        self.assertEqual(
            [badge["name"] for badge in trainer["badges"] if badge["owned"]],
            [KANTO_BADGE_NAMES[0], KANTO_BADGE_NAMES[2], KANTO_BADGE_NAMES[7]],
        )

    def test_build_party_state(self) -> None:
        mem = [0x00] * 512
        addresses = TelemetryAddresses(
            values={
                "wPartyCount": 0,
                "wPartyMon1": 32,
                "wPartyMon2": 76,
                "wPartyMon1Species": 32,
                "wPartyMon1HP": 33,
                "wPartyMon1Status": 36,
                "wPartyMon1Level": 65,
                "wPartyMon1MaxHP": 66,
                "wPartyMon1Nick": 200,
                "wPartyMon2Nick": 211,
            }
        )
        mem[addresses["wPartyCount"]] = 2

        mem[32] = 1
        mem[33:35] = [0x00, 0x14]
        mem[36] = 0
        mem[65] = 5
        mem[66:68] = [0x00, 0x1E]
        _write_text(mem, 200, "RED", length=11)

        mem[76] = 2
        mem[77:79] = [0x00, 0x00]
        mem[80] = 1 << 3
        mem[109] = 8
        mem[110:112] = [0x00, 0x20]
        _write_text(mem, 211, "BLUE", length=11)

        party = _build_party_state(mem, addresses)

        self.assertEqual(party["count"], 2)
        self.assertEqual(party["members"][0]["species_name"], DEFAULT_SPECIES_CATALOG[1])
        self.assertEqual(party["members"][0]["nickname"], "RED")
        self.assertEqual(party["members"][0]["hp_ratio"], 0.6667)
        self.assertEqual(party["members"][0]["status"], "OK")
        self.assertFalse(party["members"][0]["fainted"])
        self.assertEqual(party["members"][1]["species_name"], DEFAULT_SPECIES_CATALOG[2])
        self.assertEqual(party["members"][1]["nickname"], "BLUE")
        self.assertEqual(party["members"][1]["status"], "PSN")
        self.assertTrue(party["members"][1]["fainted"])

    def test_agent_prompt_summarizes_trainer_state_compactly(self) -> None:
        snapshot = {
            "mode": "field",
            "interaction": {"type": "field"},
            "map": {"id": 1, "x": 2, "y": 3, "script": 0, "name": "Pallet Town", "const_name": "PALLET_TOWN"},
            "movement": {"facing": "down"},
            "navigation": {
                "objective": None,
                "target_affordance": None,
                "ranked_affordances": [],
                "target_reason": None,
                "consecutive_failures": 0,
            },
            "dialogue": {"active": False, "visible_lines": [], "source": "none"},
            "naming": {"active": False, "screen_type": None, "current_text": "", "base_name": ""},
            "pokedex": {"active": False, "species_name": None, "species_class": None, "description_lines": []},
            "party": {
                "count": 2,
                "members": [
                    {"nickname": "RED", "species_name": "RHYDON", "level": 5, "hp": 20, "max_hp": 30, "status": "OK"},
                    {"nickname": "BLUE", "species_name": "KANGASKHAN", "level": 8, "hp": 12, "max_hp": 24, "status": "PAR"},
                ],
                "current_species": 1,
                "player_starter": 0,
                "rival_starter": 0,
            },
            "inventory": {
                "count": 6,
                "items": [
                    {"name": "POKé BALL", "quantity": 5},
                    {"name": "POTION", "quantity": 2},
                    {"name": "ANTIDOTE", "quantity": 1},
                    {"name": "ESCAPE ROPE", "quantity": 1},
                    {"name": "TOWN MAP", "quantity": 1},
                    {"name": "NUGGET", "quantity": 1},
                ],
            },
            "trainer": {
                "money": 1234,
                "money_bcd": "001234",
                "badge_count": 2,
                "badges": [
                    {"name": "BOULDERBADGE", "owned": True},
                    {"name": "CASCADEBADGE", "owned": False},
                    {"name": "THUNDERBADGE", "owned": True},
                ],
            },
            "menu": {"active": False, "visible_items": [], "selected_item_text": None, "selected_index": None},
            "battle": {"ui_state": "none", "command_menu": {}, "move_menu": {}},
            "screen": {"message_box_present": False, "blank_ratio": 0.1, "decoded_rows": [""] * 18},
            "events": {"recent": [{"label": "Runtime ready"}]},
        }

        context = build_agent_context(snapshot, [], decision_state={"preferences": {}, "flags": {}})

        self.assertEqual(context["observation"]["party"]["count"], 2)
        self.assertEqual(context["observation"]["inventory"]["count"], 6)
        self.assertEqual(context["observation"]["trainer"]["money"], 1234)
        self.assertIn("Party summary: RED lv5 20/30 OK, BLUE lv8 12/24 PAR", context["prompt"])
        self.assertIn("Inventory summary: 6 items: POKé BALL x5, POTION x2, ANTIDOTE x1, ESCAPE ROPE x1, TOWN MAP x1, +1 more", context["prompt"])
        self.assertIn("Trainer summary: money=1234 badges=2 [BOULDERBADGE, THUNDERBADGE]", context["prompt"])

    def test_hp_only_party_changes_do_not_count_as_progress(self) -> None:
        before = {
            "map": {"id": 1, "script": 0},
            "mode": "field",
            "interaction": {"type": "field"},
            "dialogue": {"visible_lines": []},
            "menu": {"selected_item_text": None},
            "battle": {},
            "naming": {},
            "pokedex": {},
            "party": {
                "player_starter": 1,
                "rival_starter": 2,
                "current_species": 1,
                "count": 1,
                "members": [{"species_id": 1, "hp": 20, "max_hp": 30}],
            },
        }
        after = {
            **before,
            "party": {
                "player_starter": 1,
                "rival_starter": 2,
                "current_species": 1,
                "count": 1,
                "members": [{"species_id": 1, "hp": 10, "max_hp": 30}],
            },
        }

        self.assertFalse(_made_progress(before, after))


if __name__ == "__main__":
    unittest.main()
