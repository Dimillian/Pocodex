from __future__ import annotations

import unittest

from tools.runtime.map_data import MapBgEvent, MapCatalog, MapInfo, MapObject, MapTriggerRegion, MapWarp
from tools.runtime.navigator import choose_field_action, update_navigation_state
from tools.runtime.objective_inference import (
    build_affordances,
    build_objective_state,
    find_objective_by_id,
    fresh_objective_memory,
    reconcile_objective_interaction_resolution,
    record_objective_selection,
    update_objective_memory,
)
from tools.runtime.progress_memory import fresh_progress_memory, progress_state_signature


def _map_catalog(map_info: MapInfo) -> MapCatalog:
    return MapCatalog(
        by_id={map_info.id: map_info},
        by_name={map_info.const_name: map_info},
        tilesets={},
    )


def _snapshot(*, x: int = 2, y: int = 2, dialogue_active: bool = False) -> dict:
    return {
        "frame": 100,
        "mode": "field",
        "interaction": {"type": "field"},
        "map": {
            "id": 1,
            "x": x,
            "y": y,
            "script": 0,
            "name": "Test Map",
            "const_name": "TEST_MAP",
            "width": 4,
            "height": 4,
            "triggers": [],
        },
        "movement": {"facing": "down", "moving_direction": None, "last_stop_direction": "down"},
        "dialogue": {"active": dialogue_active, "visible_lines": ["HELLO"] if dialogue_active else [], "source": "none"},
        "menu": {"active": False, "visible_items": [], "selected_item_text": None, "selected_index": None},
        "battle": {"in_battle": False, "ui_state": "none", "command_menu": {}, "move_menu": {}},
        "party": {"player_starter": 0, "rival_starter": 0, "current_species": 0, "count": 0, "members": []},
        "naming": {"active": False, "screen_type": None, "current_text": "", "base_name": ""},
        "pokedex": {"active": False, "species_name": None, "species_class": None, "description_lines": []},
        "screen": {"message_box_present": dialogue_active, "blank_ratio": 0.1, "decoded_rows": [""] * 18},
        "events": {"recent": [{"type": "runtime_ready", "label": "Runtime ready"}]},
    }


class ObjectiveInferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        walkable = [[True for _ in range(8)] for _ in range(8)]
        tiles = [[0 for _ in range(8)] for _ in range(8)]
        self.map_info = MapInfo(
            id=1,
            const_name="TEST_MAP",
            display_name="Test Map",
            width=4,
            height=4,
            warps=[MapWarp(x=0, y=0, target_map="NEXT_MAP", target_warp_id=0)],
            bg_events=[MapBgEvent(x=1, y=3, text_ref="TEXT_SIGN")],
            objects=[MapObject(x=3, y=2, sprite="SPRITE_OAK", movement="STAY", facing="DOWN", text_ref="TEXT_NPC")],
            triggers=[MapTriggerRegion(axis="y", value=0, source_label="NorthEdge", next_script="SCRIPT_EDGE")],
            walkable_grid=walkable,
            tile_grid=tiles,
        )
        self.map_catalog = _map_catalog(self.map_info)

    def test_generic_candidate_generation_covers_nearby_world_targets(self) -> None:
        snapshot = _snapshot(x=2, y=2)
        affordances = build_affordances(
            snapshot,
            map_info=self.map_info,
            map_catalog=self.map_catalog,
            progress_memory=fresh_progress_memory(),
        )
        objective_state = build_objective_state(
            snapshot,
            affordances=affordances,
            decision_state={"objective": fresh_objective_memory()},
            progress_memory=fresh_progress_memory(),
            navigation_state={},
        )

        kinds = {objective["kind"] for objective in objective_state["candidate_objectives"]}
        self.assertIn("reach_exit", kinds)
        self.assertIn("reach_region", kinds)
        self.assertTrue({"approach_entity", "interact_entity"} & kinds)
        self.assertIn("inspect_interactable", kinds)

    def test_dialogue_visible_adds_continue_script_candidate(self) -> None:
        snapshot = _snapshot(x=2, y=2, dialogue_active=True)
        affordances = build_affordances(
            snapshot,
            map_info=self.map_info,
            map_catalog=self.map_catalog,
            progress_memory=fresh_progress_memory(),
        )
        objective_state = build_objective_state(
            snapshot,
            affordances=affordances,
            decision_state={"objective": fresh_objective_memory()},
            progress_memory=fresh_progress_memory(),
            navigation_state={},
        )

        kinds = [objective["kind"] for objective in objective_state["candidate_objectives"]]
        self.assertIn("continue_script", kinds)

    def test_candidate_generation_tolerates_empty_navigation_result(self) -> None:
        snapshot = _snapshot(x=2, y=2)
        affordances = build_affordances(
            snapshot,
            map_info=self.map_info,
            map_catalog=self.map_catalog,
            progress_memory=fresh_progress_memory(),
        )
        objective_state = build_objective_state(
            snapshot,
            affordances=affordances,
            decision_state={"objective": fresh_objective_memory()},
            progress_memory=fresh_progress_memory(),
            navigation_state={"last_result": None, "last_transition": None},
        )

        self.assertTrue(objective_state["candidate_objectives"])

    def test_affordances_include_semantic_labels_for_signs_and_story_objects(self) -> None:
        map_info = MapInfo(
            id=40,
            const_name="OAKS_LAB",
            display_name="Oak's Lab",
            width=5,
            height=6,
            bg_events=[MapBgEvent(x=1, y=1, text_ref="TEXT_PALLETTOWN_SIGN")],
            objects=[
                MapObject(
                    x=6,
                    y=3,
                    sprite="SPRITE_POKE_BALL",
                    movement="STAY",
                    facing="NONE",
                    text_ref="TEXT_OAKSLAB_CHARMANDER_POKE_BALL",
                    const_name="OAKSLAB_CHARMANDER_POKE_BALL",
                ),
                MapObject(
                    x=5,
                    y=2,
                    sprite="SPRITE_OAK",
                    movement="STAY",
                    facing="DOWN",
                    text_ref="TEXT_OAKSLAB_OAK1",
                    const_name="OAKSLAB_OAK1",
                ),
            ],
            walkable_grid=[[True for _ in range(12)] for _ in range(12)],
            tile_grid=[[0 for _ in range(12)] for _ in range(12)],
        )
        snapshot = _snapshot(x=5, y=3)
        snapshot["map"].update({"id": 40, "const_name": "OAKS_LAB", "name": "Oak's Lab", "width": 5, "height": 6})

        affordances = build_affordances(
            snapshot,
            map_info=map_info,
            map_catalog=_map_catalog(map_info),
            progress_memory=fresh_progress_memory(),
        )

        sign = next(affordance for affordance in affordances if affordance["kind"] == "bg_event")
        starter = next(affordance for affordance in affordances if affordance["id"] == "object:0")
        oak = next(affordance for affordance in affordances if affordance["id"] == "object:1")

        self.assertEqual(sign["label"], "Read the nearby sign.")
        self.assertIn("sign", sign["identity_hints"])
        self.assertEqual(starter["label"], "Choose the Charmander Poké Ball.")
        self.assertIn("starter_choice_like", starter["identity_hints"])
        self.assertEqual(oak["label"], "Talk to Professor Oak.")
        self.assertIn("story_npc", oak["identity_hints"])

    def test_interaction_ready_entity_outranks_trigger_regions(self) -> None:
        snapshot = _snapshot(x=2, y=2)
        affordances = build_affordances(
            snapshot,
            map_info=self.map_info,
            map_catalog=self.map_catalog,
            progress_memory=fresh_progress_memory(),
        )
        objective_state = build_objective_state(
            snapshot,
            affordances=affordances,
            decision_state={"objective": fresh_objective_memory()},
            progress_memory=fresh_progress_memory(),
            navigation_state={"consecutive_failures": 2},
        )

        active = objective_state["active_objective"]
        self.assertIsNotNone(active)
        self.assertEqual(active["kind"], "interact_entity")
        self.assertEqual(active["phase"], "interaction_ready")
        self.assertNotIn("movement_loop", objective_state["loop_signals"])

    def test_active_objective_sticks_to_same_affordance_when_phase_changes(self) -> None:
        decision_state = {"objective": fresh_objective_memory()}
        approach_objective = {
            "id": "approach_entity:object:0",
            "kind": "approach_entity",
            "label": "Approach the nearby entity.",
            "target_affordance_ids": ["object:0"],
            "confidence": 0.7,
            "evidence": ["nearby entity"],
        }
        record_objective_selection(decision_state, objective=approach_objective, frame=100)

        snapshot = _snapshot(x=2, y=2)
        affordances = build_affordances(
            snapshot,
            map_info=self.map_info,
            map_catalog=self.map_catalog,
            progress_memory=fresh_progress_memory(),
        )
        objective_state = build_objective_state(
            snapshot,
            affordances=affordances,
            decision_state=decision_state,
            progress_memory=fresh_progress_memory(),
            navigation_state={},
        )

        active = objective_state["active_objective"]
        self.assertIsNotNone(active)
        self.assertEqual(active["id"], "interact_entity:object:0")
        self.assertEqual(active["status"], "active")

    def test_choice_like_interaction_stays_above_trigger_recovery(self) -> None:
        snapshot = _snapshot(x=5, y=3)
        snapshot["map"].update(
            {
                "id": 40,
                "const_name": "OAKS_LAB",
                "name": "Oak's Lab",
                "width": 5,
                "height": 6,
                "triggers": [],
            }
        )
        affordances = [
            {
                "id": "trigger:1",
                "kind": "trigger_region",
                "label": "Starter trigger",
                "axis": "y",
                "value": 4,
                "source_label": "OaksLabChoseStarterScript",
                "next_script": "SCRIPT_OAKSLAB_RIVAL_CHOOSES_STARTER",
                "distance": 1,
                "identity_hints": ["boundary_trigger", "script_trigger"],
                "interaction_class": "reach_region",
                "reachability": {"reachable": True, "path_length": 1},
            },
            {
                "id": "object:1",
                "kind": "object",
                "label": "Charmander ball",
                "target": {"x": 6, "y": 3},
                "sprite": "SPRITE_POKE_BALL",
                "movement": "STAY",
                "facing": "NONE",
                "text_ref": "TEXT_OAKSLAB_CHARMANDER_POKE_BALL",
                "approach_tiles": [{"x": 5, "y": 3}, {"x": 6, "y": 2}],
                "distance": 1,
                "memory_key": "OAKS_LAB:object:1",
                "novelty": "known",
                "last_outcome": "progress",
                "consumed_in_state": False,
                "interaction_class": "interact",
                "identity_hints": ["entity", "npc", "stationary_entity", "pickup_like", "starter_choice_like"],
                "reachability": {"reachable": True, "path_length": 0},
            },
        ]
        progress_memory = fresh_progress_memory()
        progress_memory["affordances"]["OAKS_LAB:trigger:1"] = {
            "key": "OAKS_LAB:trigger:1",
            "map": "OAKS_LAB",
            "affordance_id": "trigger:1",
            "kind": "trigger_region",
            "label": "Starter trigger",
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
        objective_state = build_objective_state(
            snapshot,
            affordances=affordances,
            decision_state={"objective": fresh_objective_memory()},
            progress_memory=progress_memory,
            navigation_state={},
        )

        self.assertEqual(objective_state["active_objective"]["id"], "interact_entity:object:1")

    def test_ready_choice_like_interaction_outranks_adjacent_npc_candidates(self) -> None:
        snapshot = _snapshot(x=5, y=3)
        snapshot["map"].update(
            {
                "id": 40,
                "const_name": "OAKS_LAB",
                "name": "Oak's Lab",
                "width": 5,
                "height": 6,
                "triggers": [],
            }
        )
        affordances = [
            {
                "id": "object:0",
                "kind": "object",
                "label": "Rival",
                "target": {"x": 4, "y": 3},
                "distance": 1,
                "memory_key": "OAKS_LAB:object:0",
                "novelty": "new",
                "last_outcome": None,
                "consumed_in_state": False,
                "interaction_class": "interact",
                "identity_hints": ["entity", "npc", "stationary_entity"],
                "approach_tiles": [{"x": 5, "y": 3}],
                "reachability": {"reachable": True, "path_length": 0},
            },
            {
                "id": "object:1",
                "kind": "object",
                "label": "Charmander ball",
                "target": {"x": 6, "y": 3},
                "sprite": "SPRITE_POKE_BALL",
                "text_ref": "TEXT_OAKSLAB_CHARMANDER_POKE_BALL",
                "distance": 1,
                "memory_key": "OAKS_LAB:object:1",
                "novelty": "known",
                "last_outcome": "progress",
                "consumed_in_state": False,
                "interaction_class": "interact",
                "identity_hints": ["entity", "npc", "stationary_entity", "pickup_like", "starter_choice_like"],
                "approach_tiles": [{"x": 5, "y": 3}],
                "reachability": {"reachable": True, "path_length": 0},
            },
        ]

        objective_state = build_objective_state(
            snapshot,
            affordances=affordances,
            decision_state={"objective": fresh_objective_memory()},
            progress_memory=fresh_progress_memory(),
            navigation_state={},
        )

        self.assertEqual(objective_state["active_objective"]["id"], "interact_entity:object:1")

    def test_ready_choice_like_interaction_survives_prior_invalidation(self) -> None:
        snapshot = _snapshot(x=5, y=3)
        snapshot["map"].update(
            {
                "id": 40,
                "const_name": "OAKS_LAB",
                "name": "Oak's Lab",
                "width": 5,
                "height": 6,
                "triggers": [],
            }
        )
        affordances = [
            {
                "id": "object:1",
                "kind": "object",
                "label": "Charmander ball",
                "target": {"x": 6, "y": 3},
                "sprite": "SPRITE_POKE_BALL",
                "text_ref": "TEXT_OAKSLAB_CHARMANDER_POKE_BALL",
                "distance": 1,
                "memory_key": "OAKS_LAB:object:1",
                "novelty": "known",
                "last_outcome": "progress",
                "consumed_in_state": False,
                "interaction_class": "interact",
                "identity_hints": ["entity", "npc", "stationary_entity", "pickup_like", "starter_choice_like"],
                "approach_tiles": [{"x": 5, "y": 3}],
                "reachability": {"reachable": True, "path_length": 0},
            },
            {
                "id": "object:6",
                "kind": "object",
                "label": "Pokedex",
                "target": {"x": 3, "y": 1},
                "distance": 4,
                "memory_key": "OAKS_LAB:object:6",
                "novelty": "new",
                "last_outcome": None,
                "consumed_in_state": False,
                "interaction_class": "interact",
                "identity_hints": ["entity", "npc", "stationary_entity"],
                "approach_tiles": [{"x": 3, "y": 2}],
                "reachability": {"reachable": True, "path_length": 5},
            },
        ]
        decision_state = {"objective": fresh_objective_memory()}
        decision_state["objective"]["invalidated_objectives"].append(
            {
                "frame": 200,
                "id": "interact_entity:object:1",
                "reason": "repeated_noop_window, blocked_movement_window",
            }
        )

        objective_state = build_objective_state(
            snapshot,
            affordances=affordances,
            decision_state=decision_state,
            progress_memory=fresh_progress_memory(),
            navigation_state={},
        )

        self.assertEqual(objective_state["active_objective"]["id"], "interact_entity:object:1")

    def test_find_objective_by_id_reconstructs_pinned_affordance_outside_candidate_slice(self) -> None:
        snapshot = _snapshot(x=2, y=2)
        affordances = build_affordances(
            snapshot,
            map_info=self.map_info,
            map_catalog=self.map_catalog,
            progress_memory=fresh_progress_memory(),
        )
        snapshot["navigation"] = {
            "objective_state": {
                "active_objective": None,
                "candidate_objectives": [],
            },
            "affordances": affordances,
        }

        objective = find_objective_by_id(snapshot, "inspect_interactable:bg_event:0")

        self.assertIsNotNone(objective)
        self.assertEqual(objective["id"], "inspect_interactable:bg_event:0")
        self.assertEqual(objective["kind"], "inspect_interactable")
        self.assertEqual(objective["target_affordance_ids"], ["bg_event:0"])

    def test_consumed_inspectable_is_demoted_in_current_field_state(self) -> None:
        snapshot = _snapshot(x=1, y=2)
        progress_memory = fresh_progress_memory()
        progress_memory["affordances"]["TEST_MAP:bg_event:0"] = {
            "key": "TEST_MAP:bg_event:0",
            "map": "TEST_MAP",
            "affordance_id": "bg_event:0",
            "kind": "bg_event",
            "label": "Inspect sign",
            "selected_count": 1,
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
            "consumed_field_signatures": [progress_state_signature(snapshot)],
        }
        affordances = build_affordances(
            snapshot,
            map_info=self.map_info,
            map_catalog=self.map_catalog,
            progress_memory=progress_memory,
        )
        objective_state = build_objective_state(
            snapshot,
            affordances=affordances,
            decision_state={"objective": fresh_objective_memory()},
            progress_memory=progress_memory,
            navigation_state={},
        )

        candidate = next(objective for objective in objective_state["candidate_objectives"] if objective["id"] == "inspect_interactable:bg_event:0")
        self.assertIn("already consumed in this field state", candidate["evidence"])
        self.assertNotEqual(objective_state["active_objective"]["id"], "inspect_interactable:bg_event:0")

    def test_exit_candidate_is_promoted_after_small_room_interactions_stall(self) -> None:
        snapshot = _snapshot(x=2, y=2)
        room_map = MapInfo(
            id=1,
            const_name="TEST_MAP",
            display_name="Test Map",
            width=4,
            height=4,
            warps=[MapWarp(x=0, y=0, target_map="NEXT_MAP", target_warp_id=0)],
            bg_events=[MapBgEvent(x=2, y=3, text_ref="TEXT_MAP")],
            objects=[
                MapObject(x=3, y=2, sprite="SPRITE_GIRL", movement="STAY", facing="DOWN", text_ref="TEXT_NPC_1"),
                MapObject(x=1, y=2, sprite="SPRITE_GIRL", movement="WALK", facing="LEFT", text_ref="TEXT_NPC_2"),
            ],
            triggers=[],
            walkable_grid=[[True for _ in range(8)] for _ in range(8)],
            tile_grid=[[0 for _ in range(8)] for _ in range(8)],
        )
        room_catalog = _map_catalog(room_map)
        progress_memory = fresh_progress_memory()
        signature = progress_state_signature(snapshot)
        progress_memory["affordances"]["TEST_MAP:bg_event:0"] = {
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
            "last_frame": 120,
            "lifecycle": "stale",
            "successful_before_signatures": [],
            "successful_after_signatures": [],
            "noop_before_signatures": [],
            "consumed_field_signatures": [signature],
        }
        progress_memory["affordances"]["TEST_MAP:object:0"] = {
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
        progress_memory["recent_targets"].extend(
            [
                "TEST_MAP:bg_event:0",
                "TEST_MAP:object:0",
                "TEST_MAP:bg_event:0",
                "TEST_MAP:object:0",
            ]
        )

        affordances = build_affordances(
            snapshot,
            map_info=room_map,
            map_catalog=room_catalog,
            progress_memory=progress_memory,
        )
        objective_state = build_objective_state(
            snapshot,
            affordances=affordances,
            decision_state={"objective": fresh_objective_memory()},
            progress_memory=progress_memory,
            navigation_state={},
        )

        active = objective_state["active_objective"]
        self.assertIsNotNone(active)
        self.assertEqual(active["kind"], "reach_exit")
        self.assertIn("small-room interactions look exhausted", active["evidence"])

    def test_scripted_boundary_trigger_is_promoted_when_local_distractions_are_exhausted(self) -> None:
        snapshot = _snapshot(x=5, y=4)
        snapshot["map"].update({"width": 10, "height": 9})
        trigger_map = MapInfo(
            id=1,
            const_name="TEST_MAP",
            display_name="Test Map",
            width=10,
            height=9,
            warps=[],
            bg_events=[MapBgEvent(x=7, y=9, text_ref="TEXT_SIGN")],
            objects=[MapObject(x=3, y=8, sprite="SPRITE_GIRL", movement="WALK", facing="ANY_DIR", text_ref="TEXT_NPC")],
            triggers=[MapTriggerRegion(axis="y", value=1, source_label="TestMapDefaultScript", next_script="SCRIPT_TEST_TRIGGER", note="north gate")],
            walkable_grid=[[True for _ in range(20)] for _ in range(18)],
            tile_grid=[[0 for _ in range(20)] for _ in range(18)],
        )
        trigger_catalog = _map_catalog(trigger_map)
        progress_memory = fresh_progress_memory()
        signature = progress_state_signature(snapshot)
        progress_memory["affordances"]["TEST_MAP:bg_event:0"] = {
            "key": "TEST_MAP:bg_event:0",
            "map": "TEST_MAP",
            "affordance_id": "bg_event:0",
            "kind": "bg_event",
            "label": "Sign",
            "selected_count": 1,
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
        progress_memory["affordances"]["TEST_MAP:object:0"] = {
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
            "last_frame": 121,
            "lifecycle": "stale",
            "successful_before_signatures": [],
            "successful_after_signatures": [],
            "noop_before_signatures": [],
            "consumed_field_signatures": [],
        }
        progress_memory["affordances"]["TEST_MAP:trigger:0"] = {
            "key": "TEST_MAP:trigger:0",
            "map": "TEST_MAP",
            "affordance_id": "trigger:0",
            "kind": "trigger_region",
            "label": "North trigger",
            "selected_count": 2,
            "progress_count": 0,
            "approach_count": 0,
            "noop_count": 1,
            "blocked_count": 0,
            "stale_count": 1,
            "consumed_count": 0,
            "last_outcome": "noop",
            "last_frame": 122,
            "lifecycle": "stale",
            "successful_before_signatures": [],
            "successful_after_signatures": [],
            "noop_before_signatures": [signature],
            "consumed_field_signatures": [],
        }
        progress_memory["recent_targets"].extend(
            [
                "TEST_MAP:bg_event:0",
                "TEST_MAP:object:0",
                "TEST_MAP:trigger:0",
                "TEST_MAP:trigger:0",
            ]
        )

        affordances = build_affordances(
            snapshot,
            map_info=trigger_map,
            map_catalog=trigger_catalog,
            progress_memory=progress_memory,
        )
        objective_state = build_objective_state(
            snapshot,
            affordances=affordances,
            decision_state={"objective": fresh_objective_memory()},
            progress_memory=progress_memory,
            navigation_state={},
        )

        active = objective_state["active_objective"]
        self.assertIsNotNone(active)
        self.assertEqual(active["kind"], "reach_region")
        self.assertIn("default map script gate", active["evidence"])
        self.assertIn("near map boundary", active["evidence"])

    def test_objective_memory_marks_success_and_clears_active_objective(self) -> None:
        decision_state = {"objective": fresh_objective_memory()}
        objective = {
            "id": "reach_exit:warp:0",
            "kind": "reach_exit",
            "label": "Reach the nearby exit.",
            "navigation_target": {"kind": "warp", "target": {"x": 0, "y": 0}, "target_map": "NEXT_MAP"},
            "confidence": 0.7,
            "evidence": ["nearby exit"],
        }
        before = _snapshot(x=1, y=1)
        after = _snapshot(x=0, y=0)
        after["frame"] = 120
        after["map"]["id"] = 2
        after["map"]["const_name"] = "NEXT_MAP"
        after["map"]["name"] = "Next Map"

        record_objective_selection(decision_state, objective=objective, frame=before["frame"])
        entry = update_objective_memory(
            decision_state,
            before=before,
            after=after,
            objective=objective,
            steps=[{"decision": {"type": "routine", "name": "move_up"}}],
        )

        self.assertTrue(entry["success"])
        self.assertIsNone(decision_state["objective"]["active_objective_id"])
        stats = decision_state["objective"]["objective_stats"][objective["id"]]
        self.assertEqual(stats["recent_failures"], 0)
        self.assertEqual(stats["last_progress_frame"], 120)

    def test_objective_memory_invalidates_repeated_noop_windows(self) -> None:
        decision_state = {"objective": fresh_objective_memory()}
        objective = {
            "id": "approach_entity:object:0",
            "kind": "approach_entity",
            "label": "Approach the nearby entity.",
            "navigation_target": {"kind": "object", "target": {"x": 3, "y": 2}, "approach_tiles": [{"x": 2, "y": 2}]},
            "confidence": 0.6,
            "evidence": ["nearby entity"],
        }
        before = _snapshot(x=1, y=1)
        after = _snapshot(x=1, y=1)
        after["frame"] = 110

        record_objective_selection(decision_state, objective=objective, frame=before["frame"])
        update_objective_memory(decision_state, before=before, after=after, objective=objective, steps=[])
        after_again = _snapshot(x=1, y=1)
        after_again["frame"] = 120
        update_objective_memory(decision_state, before=before, after=after_again, objective=objective, steps=[])

        self.assertIsNone(decision_state["objective"]["active_objective_id"])
        self.assertEqual(decision_state["objective"]["invalidated_objectives"][-1]["id"], objective["id"])

    def test_same_state_dialogue_resolution_invalidates_interaction_objective(self) -> None:
        decision_state = {"objective": fresh_objective_memory()}
        objective = {
            "id": "interact_entity:object:4",
            "kind": "interact_entity",
            "label": "Talk to Oak.",
            "target_affordance_ids": ["object:4"],
            "navigation_target": {"kind": "object", "target": {"x": 5, "y": 2}, "approach_tiles": [{"x": 5, "y": 3}]},
            "confidence": 0.8,
            "evidence": ["interaction-ready from current tile"],
        }
        before = _snapshot(x=5, y=3)
        before["map"].update({"const_name": "OAKS_LAB", "name": "Oak's Lab"})
        after_open = _snapshot(x=5, y=3)
        after_open["frame"] = 110
        after_open["mode"] = "dialogue"
        after_open["interaction"] = {"type": "field"}
        after_open["dialogue"] = {"active": False, "visible_lines": [], "source": "none"}
        after_open["battle"]["ui_state"] = "dialogue"
        after_close = _snapshot(x=5, y=3)
        after_close["frame"] = 120
        after_close["map"].update({"const_name": "OAKS_LAB", "name": "Oak's Lab"})

        record_objective_selection(decision_state, objective=objective, frame=before["frame"])
        update_objective_memory(
            decision_state,
            before=before,
            after=after_open,
            objective=objective,
            steps=[{"decision": {"type": "action", "button": "a"}}],
        )
        resolution = reconcile_objective_interaction_resolution(
            decision_state,
            before=after_open,
            after=after_close,
        )

        self.assertEqual(resolution, {"consumed": True, "objective_id": objective["id"]})
        self.assertIsNone(decision_state["objective"]["active_objective_id"])
        self.assertEqual(decision_state["objective"]["invalidated_objectives"][-1]["id"], objective["id"])

    def test_facing_only_turns_do_not_count_as_blocked_movement(self) -> None:
        navigation_state = {"consecutive_failures": 1, "blocked_directions": ["up"]}
        before = _snapshot(x=2, y=2)
        after = _snapshot(x=2, y=2)
        after["movement"]["facing"] = "right"

        update_navigation_state(
            navigation_state,
            before=before,
            after=after,
            payload={"button": "right"},
        )

        self.assertEqual(navigation_state["last_result"]["kind"], "reoriented")
        self.assertEqual(navigation_state["consecutive_failures"], 0)
        self.assertEqual(navigation_state["blocked_directions"], [])

    def test_objective_strategy_binds_navigation_over_tactical_target(self) -> None:
        snapshot = _snapshot(x=2, y=1)
        affordances = build_affordances(
            snapshot,
            map_info=self.map_info,
            map_catalog=self.map_catalog,
            progress_memory=fresh_progress_memory(),
        )
        warp = next(affordance for affordance in affordances if affordance["kind"] == "warp")
        entity = next(affordance for affordance in affordances if affordance["kind"] == "object")
        snapshot["navigation"] = {
            "active_objective": {
                "id": "reach_exit:warp:0",
                "kind": "reach_exit",
                "label": "Reach the nearby exit.",
                "navigation_target": warp,
            },
            "objective_state": {
                "active_objective": {
                    "id": "reach_exit:warp:0",
                    "kind": "reach_exit",
                    "label": "Reach the nearby exit.",
                    "navigation_target": warp,
                },
                "candidate_objectives": [],
            },
            "target_affordance": entity,
            "ranked_affordances": [entity, warp],
            "consecutive_failures": 0,
            "blocked_directions": [],
        }

        objective_decision = choose_field_action(
            snapshot,
            decision_state={"exploration": {"field_move_index": 0}},
            map_catalog=self.map_catalog,
            strategy="objective",
            objective_id="reach_exit:warp:0",
        )
        target_decision = choose_field_action(
            snapshot,
            decision_state={"exploration": {"field_move_index": 0}},
            map_catalog=self.map_catalog,
            strategy="target",
            preferred_affordance_id=entity["id"],
        )

        self.assertIn(objective_decision["name"], {"move_left", "move_up"})
        self.assertIn(target_decision["name"], {"move_right", "move_down"})


if __name__ == "__main__":
    unittest.main()
