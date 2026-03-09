from __future__ import annotations

import unittest

from tools.runtime.progress_memory import fresh_progress_memory, progress_state_signature, update_progress_memory
from tools.runtime.world_model import build_world_model


def _snapshot(*, mode: str = "field", interaction_type: str = "field") -> dict:
    return {
        "frame": 100,
        "mode": mode,
        "interaction": {"type": interaction_type},
        "map": {
            "id": 1,
            "x": 2,
            "y": 2,
            "script": 0,
            "name": "Test Map",
            "const_name": "TEST_MAP",
            "width": 4,
            "height": 4,
            "triggers": [],
        },
        "dialogue": {"active": mode != "field", "visible_lines": ["INFO"] if mode != "field" else [], "source": "none"},
        "menu": {"active": False, "visible_items": [], "selected_item_text": None, "selected_index": None},
        "battle": {"in_battle": False, "ui_state": "none", "command_menu": {}, "move_menu": {}},
        "naming": {"active": False, "screen_type": None, "current_text": "", "base_name": ""},
        "pokedex": {"active": interaction_type == "pokedex_info", "species_name": None, "species_class": None, "description_lines": []},
        "party": {"player_starter": 0, "rival_starter": 0, "current_species": 0, "count": 0, "members": []},
        "navigation": {},
    }


class ProgressMemoryConsumptionTests(unittest.TestCase):
    def test_info_interaction_consumes_affordance_when_it_returns_to_same_field_state(self) -> None:
        state = fresh_progress_memory()
        target = {
            "id": "object:2",
            "kind": "object",
            "label": "Town map",
            "target": {"x": 3, "y": 3},
        }
        before = _snapshot()
        before["navigation"] = {"target_affordance": target}

        after_open = _snapshot(mode="menu_dialogue", interaction_type="pokedex_info")
        after_open["frame"] = 110
        update_progress_memory(state, before=before, after=after_open)

        after_close = _snapshot()
        after_close["frame"] = 120
        update_progress_memory(state, before=after_open, after=after_close)

        stats = state["affordances"]["TEST_MAP:object:2"]
        self.assertEqual(stats["consumed_count"], 1)
        self.assertEqual(stats["last_outcome"], "consumed")
        self.assertIn(progress_state_signature(before), stats["consumed_field_signatures"])
        self.assertIsNone(state["pending_interaction"])

    def test_world_model_penalizes_consumed_affordance_in_same_field_state(self) -> None:
        state = fresh_progress_memory()
        snapshot = _snapshot()
        signature = progress_state_signature(snapshot)
        state["affordances"]["TEST_MAP:object:2"] = {
            "key": "TEST_MAP:object:2",
            "map": "TEST_MAP",
            "affordance_id": "object:2",
            "kind": "object",
            "label": "Town map",
            "selected_count": 2,
            "progress_count": 1,
            "approach_count": 0,
            "noop_count": 0,
            "blocked_count": 0,
            "stale_count": 0,
            "consumed_count": 1,
            "last_outcome": "consumed",
            "last_frame": 120,
            "lifecycle": "stale",
            "successful_before_signatures": [],
            "successful_after_signatures": [],
            "noop_before_signatures": [],
            "consumed_field_signatures": [signature],
        }

        consumed_object = {
            "id": "object:2",
            "kind": "object",
            "label": "Town map",
            "target": {"x": 3, "y": 2},
            "distance": 1,
            "identity_hints": ["entity"],
            "interaction_class": "interact",
        }
        warp = {
            "id": "warp:0",
            "kind": "warp",
            "label": "Exit",
            "target": {"x": 0, "y": 0},
            "target_map": "NEXT_MAP",
            "distance": 4,
            "identity_hints": ["exit"],
            "interaction_class": "transition",
        }

        world_model = build_world_model(snapshot, affordances=[consumed_object, warp], progress_memory=state)

        self.assertEqual(world_model["target_affordance"]["id"], "warp:0")

    def test_dialogue_like_battle_ui_still_consumes_affordance_on_same_field_return(self) -> None:
        state = fresh_progress_memory()
        target = {
            "id": "object:4",
            "kind": "object",
            "label": "Oak",
            "target": {"x": 5, "y": 2},
        }
        before = _snapshot()
        before["map"].update({"const_name": "OAKS_LAB", "name": "Oak's Lab", "x": 5, "y": 3})
        before["party"]["current_species"] = 167
        before["navigation"] = {"target_affordance": target}

        after_open = _snapshot(mode="dialogue")
        after_open["frame"] = 110
        after_open["map"].update({"const_name": "OAKS_LAB", "name": "Oak's Lab", "x": 5, "y": 3})
        after_open["party"]["current_species"] = 167
        after_open["dialogue"] = {"active": False, "visible_lines": [], "source": "none"}
        after_open["battle"]["ui_state"] = "dialogue"
        update_progress_memory(state, before=before, after=after_open)

        after_close = _snapshot()
        after_close["frame"] = 120
        after_close["map"].update({"const_name": "OAKS_LAB", "name": "Oak's Lab", "x": 5, "y": 3})
        after_close["party"]["current_species"] = 167
        update_progress_memory(state, before=after_open, after=after_close)

        stats = state["affordances"]["OAKS_LAB:object:4"]
        self.assertEqual(stats["consumed_count"], 1)
        self.assertEqual(stats["last_outcome"], "consumed")
        self.assertIn(progress_state_signature(before), stats["consumed_field_signatures"])
        self.assertIsNone(state["pending_interaction"])

    def test_world_model_prefers_exit_after_small_room_interactions_stall(self) -> None:
        state = fresh_progress_memory()
        snapshot = _snapshot()
        signature = progress_state_signature(snapshot)
        state["affordances"]["TEST_MAP:object:0"] = {
            "key": "TEST_MAP:object:0",
            "map": "TEST_MAP",
            "affordance_id": "object:0",
            "kind": "object",
            "label": "NPC 1",
            "selected_count": 2,
            "progress_count": 0,
            "approach_count": 0,
            "noop_count": 0,
            "blocked_count": 2,
            "stale_count": 1,
            "consumed_count": 0,
            "last_outcome": "blocked",
            "last_frame": 125,
            "lifecycle": "stale",
            "successful_before_signatures": [],
            "successful_after_signatures": [],
            "noop_before_signatures": [],
            "consumed_field_signatures": [],
        }
        state["affordances"]["TEST_MAP:bg_event:0"] = {
            "key": "TEST_MAP:bg_event:0",
            "map": "TEST_MAP",
            "affordance_id": "bg_event:0",
            "kind": "bg_event",
            "label": "Town map",
            "selected_count": 1,
            "progress_count": 1,
            "approach_count": 0,
            "noop_count": 0,
            "blocked_count": 0,
            "stale_count": 0,
            "consumed_count": 1,
            "last_outcome": "consumed",
            "last_frame": 130,
            "lifecycle": "stale",
            "successful_before_signatures": [],
            "successful_after_signatures": [],
            "noop_before_signatures": [],
            "consumed_field_signatures": [signature],
        }
        state["recent_targets"].extend(
            [
                "TEST_MAP:bg_event:0",
                "TEST_MAP:object:0",
                "TEST_MAP:bg_event:0",
                "TEST_MAP:object:0",
            ]
        )

        npc = {
            "id": "object:0",
            "kind": "object",
            "label": "NPC 1",
            "target": {"x": 3, "y": 2},
            "distance": 1,
            "identity_hints": ["entity", "npc"],
            "interaction_class": "interact",
        }
        map_bg = {
            "id": "bg_event:0",
            "kind": "bg_event",
            "label": "Town map",
            "target": {"x": 2, "y": 3},
            "distance": 1,
            "identity_hints": ["interactable", "text_source"],
            "interaction_class": "inspect",
        }
        warp = {
            "id": "warp:0",
            "kind": "warp",
            "label": "Exit",
            "target": {"x": 0, "y": 0},
            "target_map": "NEXT_MAP",
            "distance": 4,
            "identity_hints": ["exit"],
            "interaction_class": "transition",
            "reachability": {"reachable": True, "path_length": 4},
        }

        world_model = build_world_model(snapshot, affordances=[npc, map_bg, warp], progress_memory=state)

        self.assertEqual(world_model["target_affordance"]["id"], "warp:0")

    def test_world_model_keeps_progression_trigger_above_stale_local_distractions(self) -> None:
        state = fresh_progress_memory()
        snapshot = _snapshot()
        snapshot["map"].update({"width": 10, "height": 9})
        signature = progress_state_signature(snapshot)
        state["affordances"]["TEST_MAP:trigger:0"] = {
            "key": "TEST_MAP:trigger:0",
            "map": "TEST_MAP",
            "affordance_id": "trigger:0",
            "kind": "trigger_region",
            "label": "North exit trigger",
            "selected_count": 2,
            "progress_count": 0,
            "approach_count": 0,
            "noop_count": 1,
            "blocked_count": 0,
            "stale_count": 1,
            "consumed_count": 0,
            "last_outcome": "noop",
            "last_frame": 120,
            "lifecycle": "stale",
            "successful_before_signatures": [],
            "successful_after_signatures": [],
            "noop_before_signatures": [signature],
            "consumed_field_signatures": [],
        }
        state["affordances"]["TEST_MAP:bg_event:0"] = {
            "key": "TEST_MAP:bg_event:0",
            "map": "TEST_MAP",
            "affordance_id": "bg_event:0",
            "kind": "bg_event",
            "label": "Town sign",
            "selected_count": 1,
            "progress_count": 1,
            "approach_count": 0,
            "noop_count": 0,
            "blocked_count": 0,
            "stale_count": 0,
            "consumed_count": 1,
            "last_outcome": "consumed",
            "last_frame": 121,
            "lifecycle": "stale",
            "successful_before_signatures": [],
            "successful_after_signatures": [],
            "noop_before_signatures": [],
            "consumed_field_signatures": [signature],
        }
        state["affordances"]["TEST_MAP:object:0"] = {
            "key": "TEST_MAP:object:0",
            "map": "TEST_MAP",
            "affordance_id": "object:0",
            "kind": "object",
            "label": "NPC",
            "selected_count": 2,
            "progress_count": 0,
            "approach_count": 0,
            "noop_count": 0,
            "blocked_count": 2,
            "stale_count": 1,
            "consumed_count": 0,
            "last_outcome": "blocked",
            "last_frame": 122,
            "lifecycle": "stale",
            "successful_before_signatures": [],
            "successful_after_signatures": [],
            "noop_before_signatures": [],
            "consumed_field_signatures": [],
        }
        state["recent_targets"].extend(
            [
                "TEST_MAP:bg_event:0",
                "TEST_MAP:object:0",
                "TEST_MAP:trigger:0",
                "TEST_MAP:trigger:0",
            ]
        )

        trigger = {
            "id": "trigger:0",
            "kind": "trigger_region",
            "label": "North exit trigger",
            "axis": "y",
            "value": 1,
            "next_script": "SCRIPT_TEST_TRIGGER",
            "source_label": "TestMapDefaultScript",
            "distance": 3,
            "identity_hints": ["boundary_trigger", "script_trigger"],
            "interaction_class": "reach_region",
        }
        town_sign = {
            "id": "bg_event:0",
            "kind": "bg_event",
            "label": "Town sign",
            "target": {"x": 2, "y": 3},
            "distance": 1,
            "identity_hints": ["interactable", "text_source"],
            "interaction_class": "inspect",
        }
        npc = {
            "id": "object:0",
            "kind": "object",
            "label": "NPC",
            "target": {"x": 3, "y": 2},
            "distance": 1,
            "identity_hints": ["entity", "npc"],
            "interaction_class": "interact",
        }

        world_model = build_world_model(snapshot, affordances=[town_sign, npc, trigger], progress_memory=state)

        self.assertEqual(world_model["target_affordance"]["id"], "trigger:0")

    def test_world_model_prefers_nearby_choice_interaction_over_trigger_recovery(self) -> None:
        state = fresh_progress_memory()
        snapshot = _snapshot()
        snapshot["map"].update({"const_name": "OAKS_LAB", "name": "Oak's Lab", "width": 5, "height": 6})
        signature = progress_state_signature(snapshot)
        state["affordances"]["OAKS_LAB:trigger:1"] = {
            "key": "OAKS_LAB:trigger:1",
            "map": "OAKS_LAB",
            "affordance_id": "trigger:1",
            "kind": "trigger_region",
            "label": "Starter table trigger",
            "selected_count": 3,
            "progress_count": 1,
            "approach_count": 0,
            "noop_count": 0,
            "blocked_count": 2,
            "stale_count": 0,
            "consumed_count": 0,
            "last_outcome": "blocked",
            "last_frame": 120,
            "lifecycle": "blocked",
            "successful_before_signatures": [],
            "successful_after_signatures": [],
            "noop_before_signatures": [],
            "consumed_field_signatures": [],
        }
        state["recent_targets"].extend(
            [
                "OAKS_LAB:object:2",
                "OAKS_LAB:object:3",
                "OAKS_LAB:trigger:1",
                "OAKS_LAB:trigger:1",
            ]
        )

        trigger = {
            "id": "trigger:1",
            "kind": "trigger_region",
            "label": "Starter table trigger",
            "axis": "y",
            "value": 4,
            "next_script": "SCRIPT_OAKSLAB_RIVAL_CHOOSES_STARTER",
            "source_label": "OaksLabChoseStarterScript",
            "distance": 1,
            "identity_hints": ["boundary_trigger", "script_trigger"],
            "interaction_class": "reach_region",
            "reachability": {"reachable": True, "path_length": 1},
        }
        starter_ball = {
            "id": "object:1",
            "kind": "object",
            "label": "Charmander ball",
            "target": {"x": 6, "y": 3},
            "text_ref": "TEXT_OAKSLAB_CHARMANDER_POKE_BALL",
            "distance": 1,
            "identity_hints": ["entity", "npc", "stationary_entity", "pickup_like", "starter_choice_like"],
            "interaction_class": "interact",
            "reachability": {"reachable": True, "path_length": 0},
        }

        world_model = build_world_model(snapshot, affordances=[trigger, starter_ball], progress_memory=state)

        self.assertEqual(world_model["target_affordance"]["id"], "object:1")

    def test_world_model_prefers_ready_choice_interaction_over_adjacent_npc(self) -> None:
        state = fresh_progress_memory()
        snapshot = _snapshot()
        snapshot["map"].update({"id": 40, "const_name": "OAKS_LAB", "name": "Oak's Lab", "x": 5, "y": 3, "width": 5, "height": 6})

        npc = {
            "id": "object:0",
            "kind": "object",
            "label": "Rival",
            "target": {"x": 4, "y": 3},
            "distance": 1,
            "identity_hints": ["entity", "npc", "stationary_entity"],
            "interaction_class": "interact",
            "reachability": {"reachable": True, "path_length": 0},
            "approach_tiles": [{"x": 5, "y": 3}],
        }
        starter_ball = {
            "id": "object:1",
            "kind": "object",
            "label": "Charmander ball",
            "target": {"x": 6, "y": 3},
            "sprite": "SPRITE_POKE_BALL",
            "text_ref": "TEXT_OAKSLAB_CHARMANDER_POKE_BALL",
            "distance": 1,
            "identity_hints": ["entity", "npc", "stationary_entity", "pickup_like", "starter_choice_like"],
            "interaction_class": "interact",
            "reachability": {"reachable": True, "path_length": 0},
            "approach_tiles": [{"x": 5, "y": 3}],
        }

        world_model = build_world_model(snapshot, affordances=[npc, starter_ball], progress_memory=state)

        self.assertEqual(world_model["target_affordance"]["id"], "object:1")


if __name__ == "__main__":
    unittest.main()
