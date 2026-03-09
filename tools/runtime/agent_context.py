from __future__ import annotations

from typing import Any

AGENT_GOAL = "Make forward progress in Pokemon Blue one verified action at a time."
AGENT_RULES = [
    "Choose exactly one next action from allowed_actions.",
    "Prefer resolving the current interaction over guessing movement.",
    "When a menu is visible, navigate or confirm within that menu before moving in the field.",
    "When a preset name list is visible and a listed recommended name exists, prefer that listed option over NEW NAME or manual text entry.",
    "In field mode, prefer follow_objective with an objective_id from candidate_objectives before falling back to direct movement.",
    "Use follow_target only as a tactical override or debugging tool when you intentionally want a specific affordance.",
    "When using follow_objective, include objective_id from candidate_objectives when you want to bind the execution window to a specific objective.",
    "When using follow_target, include an affordance_id from navigation.ranked_affordances if you want to choose a specific world target yourself.",
    "If movement fails twice in a row, prefer waiting briefly and re-observing before forcing another direction.",
    "Use save/load only when recovery is needed, not as a normal action.",
    "If the state is ambiguous, choose wait_short rather than inventing a risky action.",
]


def build_agent_context(
    snapshot: dict[str, Any],
    traces: list[dict[str, Any]],
    *,
    decision_state: dict[str, Any],
) -> dict[str, Any]:
    dialogue_context = build_dialogue_context(snapshot)
    allowed_actions = build_allowed_actions(snapshot)
    heuristic_next_action = build_heuristic_hint(snapshot, decision_state)
    recent_events = [
        event["label"]
        for event in snapshot["events"]["recent"][-8:]
    ]
    recent_traces = [
        {
            "kind": trace["kind"],
            "decision": trace.get("decision"),
            "payload": trace.get("payload"),
            "after_mode": trace.get("after", {}).get("mode"),
            "passed": trace.get("verification", {}).get("passed"),
        }
        for trace in traces[-6:]
    ]
    navigation = snapshot.get("navigation") or {}
    objective_state = navigation.get("objective_state") or {}
    candidate_objectives = objective_state.get("candidate_objectives") or navigation.get("candidate_objectives") or []
    active_objective = objective_state.get("active_objective") or navigation.get("active_objective") or navigation.get("objective")
    world_state = {
        "mode": snapshot["mode"],
        "interaction": snapshot.get("interaction"),
        "map": snapshot["map"],
        "movement": snapshot.get("movement"),
        "dialogue": {
            **snapshot["dialogue"],
            "context": dialogue_context,
        },
        "naming": snapshot.get("naming"),
        "pokedex": snapshot.get("pokedex"),
        "party": snapshot.get("party"),
        "inventory": snapshot.get("inventory"),
        "trainer": snapshot.get("trainer"),
        "menu": {
            "active": snapshot["menu"]["active"],
            "visible_items": snapshot["menu"]["visible_items"],
            "selected_item_text": snapshot["menu"]["selected_item_text"],
            "selected_index": snapshot["menu"]["selected_index"],
        },
        "battle": snapshot["battle"],
        "screen": {
            "message_box_present": snapshot["screen"]["message_box_present"],
            "blank_ratio": snapshot["screen"]["blank_ratio"],
            "decoded_rows": snapshot["screen"]["decoded_rows"],
        },
        "events": recent_events,
        "recent_map_history": objective_state.get("recent_map_history") or navigation.get("recent_map_history") or [],
    }
    objective_memory = {
        "active_objective": active_objective,
        "objective_history": objective_state.get("objective_history") or navigation.get("objective_history") or [],
        "objective_progress": objective_state.get("objective_progress") or navigation.get("objective_progress") or [],
        "objective_invalidations": objective_state.get("objective_invalidations") or navigation.get("objective_invalidations") or [],
    }
    model_input = {
        "version": 2,
        "goal": AGENT_GOAL,
        "mode": snapshot["mode"],
        "interaction_type": (snapshot.get("interaction") or {}).get("type"),
        "allowed_action_ids": [action["id"] for action in allowed_actions],
        "heuristic_hint": heuristic_next_action,
        "state": _build_mode_state(
            snapshot,
            dialogue_context=dialogue_context,
            navigation=navigation,
            candidate_objectives=candidate_objectives,
            decision_state=decision_state,
        ),
        "agent_memory": _build_agent_memory(
            world_state,
            objective_memory=objective_memory,
            progress_signals=objective_state.get("progress_signals") or navigation.get("progress_signals") or [],
            loop_signals=objective_state.get("loop_signals") or navigation.get("loop_signals") or [],
        ),
        "recent_action_results": [_compact_trace_summary(trace) for trace in recent_traces],
    }

    context = {
        "objective": AGENT_GOAL,
        "observation": {
            **world_state,
            "navigation": navigation,
        },
        "world_state": world_state,
        "affordances": navigation.get("affordances") or [],
        "objective_state": objective_state,
        "candidate_objectives": candidate_objectives,
        "progress_signals": objective_state.get("progress_signals") or navigation.get("progress_signals") or [],
        "loop_signals": objective_state.get("loop_signals") or navigation.get("loop_signals") or [],
        "objective_memory": objective_memory,
        "decision_state": decision_state,
        "heuristic_next_action": heuristic_next_action,
        "allowed_actions": allowed_actions,
        "rules": AGENT_RULES,
        "recent_traces": recent_traces,
        "model_input": model_input,
        "output_contract": {
            "format": "json",
            "schema": {
                "action": "one action id from allowed_actions",
                "reason": "short explanation grounded in the observation",
                "objective_id": "optional objective id from objective_state.candidate_objectives when action is follow_objective",
                "affordance_id": "optional affordance id from navigation.ranked_affordances when action is follow_target",
            },
        },
    }
    context["prompt"] = build_agent_prompt(context)
    return context


def build_allowed_actions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = [
        {"id": "wait_short", "type": "tick", "frames": 10, "description": "Wait briefly for a transition or script update."},
        {"id": "save_quick", "type": "save_state", "slot": "quick", "description": "Save the current runtime checkpoint."},
        {"id": "load_quick", "type": "load_state", "slot": "quick", "description": "Restore the quick runtime checkpoint."},
    ]

    mode = snapshot["mode"]
    interaction_type = (snapshot.get("interaction") or {}).get("type")
    dialogue_visible = snapshot["dialogue"]["active"] or snapshot["screen"].get("message_box_present", False)
    if interaction_type and interaction_type != "field":
        actions.append(
            {
                "id": "follow_interaction",
                "type": "macro",
                "name": "follow_interaction",
                "description": "Let the runtime resolve the current dialogue, choice, naming, or battle interaction for several verified steps.",
            }
        )
    if mode in {"dialogue", "menu_dialogue", "naming"} or dialogue_visible:
        actions.extend(
            [
                {"id": "press_a", "type": "action", "button": "a", "description": "Advance or confirm the visible dialogue/menu prompt."},
                {"id": "press_b", "type": "action", "button": "b", "description": "Back out or dismiss the current prompt if needed."},
            ]
        )
    if interaction_type == "preset_name_choice":
        deduped_actions: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for action in actions:
            action_id = action["id"]
            if action_id in seen_ids:
                continue
            seen_ids.add(action_id)
            deduped_actions.append(action)
        return deduped_actions
    if mode in {"menu", "menu_dialogue", "naming"}:
        actions.extend(
            [
                {"id": "move_up", "type": "routine", "name": "move_up", "description": "Move the menu cursor up."},
                {"id": "move_down", "type": "routine", "name": "move_down", "description": "Move the menu cursor down."},
                {"id": "move_left", "type": "routine", "name": "move_left", "description": "Move the menu cursor left."},
                {"id": "move_right", "type": "routine", "name": "move_right", "description": "Move the menu cursor right."},
                {"id": "press_a", "type": "action", "button": "a", "description": "Confirm the selected menu item."},
                {"id": "press_b", "type": "action", "button": "b", "description": "Back out of the current menu."},
            ]
        )
        if mode == "naming":
            actions.append({"id": "press_start", "type": "action", "button": "start", "description": "Submit the current entered name."})
    if mode == "field":
        actions.extend(
            [
                {"id": "follow_target", "type": "macro", "name": "follow_target", "description": "Use local navigation to pursue the highest-confidence target affordance for several verified steps."},
                {"id": "follow_objective", "type": "macro", "name": "follow_objective", "description": "Use local navigation to follow the current inferred objective window for several verified steps."},
                {"id": "move_up", "type": "routine", "name": "move_up", "description": "Attempt to move one step up."},
                {"id": "move_down", "type": "routine", "name": "move_down", "description": "Attempt to move one step down."},
                {"id": "move_left", "type": "routine", "name": "move_left", "description": "Attempt to move one step left."},
                {"id": "move_right", "type": "routine", "name": "move_right", "description": "Attempt to move one step right."},
                {"id": "press_start", "type": "action", "button": "start", "description": "Open the pause/menu screen."},
                {"id": "press_a", "type": "action", "button": "a", "description": "Interact with the tile or object in front of the player."},
            ]
        )
    if mode == "battle":
        actions.extend(
            [
                {"id": "press_a", "type": "action", "button": "a", "description": "Advance or confirm the current battle prompt."},
                {"id": "press_b", "type": "action", "button": "b", "description": "Back out if the battle menu allows it."},
                {"id": "move_up", "type": "routine", "name": "move_up", "description": "Move the battle cursor up."},
                {"id": "move_down", "type": "routine", "name": "move_down", "description": "Move the battle cursor down."},
                {"id": "move_left", "type": "routine", "name": "move_left", "description": "Move the battle cursor left."},
                {"id": "move_right", "type": "routine", "name": "move_right", "description": "Move the battle cursor right."},
            ]
        )

    deduped_actions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for action in actions:
        action_id = action["id"]
        if action_id in seen_ids:
            continue
        seen_ids.add(action_id)
        deduped_actions.append(action)
    return deduped_actions


def build_heuristic_hint(snapshot: dict[str, Any], decision_state: dict[str, Any]) -> dict[str, Any]:
    mode = snapshot["mode"]
    interaction = snapshot.get("interaction") or {}
    interaction_type = interaction.get("type")
    navigation = snapshot.get("navigation") or {}
    objective_state = navigation.get("objective_state") or {}
    objective = objective_state.get("active_objective") or navigation.get("active_objective") or navigation.get("objective")
    target_affordance = navigation.get("target_affordance")
    candidates = objective_state.get("candidate_objectives") or navigation.get("candidate_objectives") or []
    dialogue_visible = snapshot["dialogue"]["active"] or snapshot["screen"].get("message_box_present", False)
    dialogue_context = build_dialogue_context(snapshot)
    if interaction_type and interaction_type != "field":
        if interaction_type == "preset_name_choice":
            recommended_item = _recommended_preset_name(snapshot, decision_state)
            reason = "A preset name list is open; let the runtime choose a listed name instead of entering NEW NAME."
            if recommended_item:
                reason = f"A preset name list is open; prefer the listed name '{recommended_item}' instead of NEW NAME."
            return {
                "action": "follow_interaction",
                "reason": reason,
            }
        return {
            "action": "follow_interaction",
            "reason": f"The current interaction is {interaction_type}, so let the runtime resolve it safely.",
        }
    if dialogue_visible:
        return {
            "action": "press_a",
            "reason": dialogue_context["recommended_reason"],
        }
    if mode == "dialogue":
        return {"action": "press_a", "reason": "Visible dialogue is active."}
    if mode == "menu_dialogue":
        if snapshot["menu"]["selected_item_text"]:
            return {"action": "press_a", "reason": "A menu choice is selected alongside dialogue."}
        return {"action": "move_down", "reason": "A menu is open; cursor movement is safer than guessing."}
    if mode == "menu":
        if snapshot["menu"]["selected_item_text"]:
            return {"action": "press_a", "reason": "A menu item is selected."}
        return {"action": "move_down", "reason": "A menu is open without a selected target."}
    if mode == "field" and snapshot["map"]["id"] == 0 and snapshot["map"]["x"] == 0 and snapshot["map"]["y"] == 0:
        return {"action": "press_start", "reason": "At the title screen, opening the menu is the next deterministic step."}
    if mode == "field":
        if navigation.get("consecutive_failures", 0) >= 2:
            return {"action": "wait_short", "reason": "Recent movement attempts failed; re-observe before pushing another direction."}
        if objective or candidates:
            selected_objective = objective or candidates[0]
            return {
                "action": "follow_objective",
                "reason": f"Use a bounded objective window to pursue {selected_objective['label']}.",
                "objective_id": selected_objective.get("id"),
            }
        if target_affordance:
            return {
                "action": "follow_target",
                "reason": f"Use local navigation to pursue {target_affordance['label']} based on the world-model ranking.",
                "affordance_id": target_affordance.get("id"),
            }
        if _decision_flag(decision_state, "oak_intro_active"):
            return {"action": "press_a", "reason": "The intro script still appears active."}
        return {"action": "move_down", "reason": "Field exploration can probe one move at a time."}
    if mode == "battle":
        return {"action": "follow_interaction", "reason": "Battle interaction is active."}
    return {"action": "wait_short", "reason": "The state appears transitional."}


def build_agent_prompt(context: dict[str, Any]) -> str:
    return (
        "Choose the next verified action for Pokemon Blue.\n"
        "Use the structured turn input JSON as the source of truth for game state.\n"
        "Do not restate the observation or narrate what you see.\n"
        "Choose exactly one action from allowed_action_ids.\n"
        "Prefer follow_interaction when the game is already in dialogue, battle, naming, or another non-field interaction.\n"
        "In field mode, prefer follow_objective with an objective_id from candidate objectives before using direct movement.\n"
        "Use follow_target only when you intentionally want a tactical override to a specific affordance.\n"
        "Keep the reason short and grounded in the current turn input.\n"
        'Return JSON only: {"action":"...", "reason":"...", "objective_id":"...optional...", "affordance_id":"...optional..."}'
    )


def _recommended_preset_name(snapshot: dict[str, Any], decision_state: dict[str, Any]) -> str | None:
    interaction = snapshot.get("interaction") or {}
    if interaction.get("type") != "preset_name_choice":
        return None

    visible_items = snapshot.get("menu", {}).get("visible_items") or []
    if not visible_items:
        return None

    dialogue_lines = snapshot.get("dialogue", {}).get("visible_lines") or []
    normalized_dialogue = " ".join(dialogue_lines).lower()
    upper_items = {item.upper(): item for item in visible_items}

    preferred_key = None
    if "your name" in normalized_dialogue:
        preferred_key = str(_decision_preference(decision_state, "player_name") or "").upper() or None
    elif "his name" in normalized_dialogue or "rival" in normalized_dialogue:
        preferred_key = str(_decision_preference(decision_state, "rival_name") or "").upper() or None

    if preferred_key and preferred_key in upper_items:
        return upper_items[preferred_key]

    for item in visible_items:
        if item.upper() not in {"NEW NAME", "CANCEL", "EXIT"}:
            return item
    return None


def _decision_preference(decision_state: dict[str, Any], key: str) -> Any:
    return (decision_state.get("preferences") or {}).get(key)


def _decision_flag(decision_state: dict[str, Any], key: str) -> bool:
    return bool((decision_state.get("flags") or {}).get(key))


def _summarize_party(party: dict[str, Any]) -> str:
    members = party.get("members") or []
    if not members:
        return "none"
    return ", ".join(
        f"{member.get('nickname') or member.get('species_name') or '?'} lv{member.get('level', '?')} "
        f"{member.get('hp', '?')}/{member.get('max_hp', '?')} {member.get('status', 'OK')}"
        for member in members
    )


def _summarize_inventory(inventory: dict[str, Any], *, limit: int = 5) -> str:
    items = inventory.get("items") or []
    if not items:
        return "0 items"
    preview = ", ".join(
        f"{item.get('name', '?')} x{item.get('quantity', '?')}"
        for item in items[:limit]
    )
    remainder = len(items) - limit
    if remainder > 0:
        preview = f"{preview}, +{remainder} more"
    return f"{inventory.get('count', len(items))} items: {preview}"


def _summarize_objectives(objectives: list[dict[str, Any]], *, limit: int = 4) -> str:
    if not objectives:
        return "none"
    summary = ", ".join(
        f"{objective.get('id')}({objective.get('confidence', '?')}): {objective.get('kind')}"
        for objective in objectives[:limit]
    )
    remainder = len(objectives) - limit
    if remainder > 0:
        summary = f"{summary}, +{remainder} more"
    return summary


def _summarize_money_and_badges(trainer: dict[str, Any]) -> str:
    badge_names = [
        badge.get("name")
        for badge in trainer.get("badges", [])
        if badge.get("owned")
    ]
    badge_summary = ", ".join(badge_names) if badge_names else "none"
    return (
        f"money={trainer.get('money', 0)} "
        f"badges={trainer.get('badge_count', len(badge_names))} [{badge_summary}]"
    )


def build_dialogue_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    decoded_rows = snapshot["screen"].get("decoded_rows") or []
    visible_lines = snapshot["dialogue"].get("visible_lines") or []
    message_box_present = snapshot["screen"].get("message_box_present", False)
    visible = bool(visible_lines) or message_box_present
    dialogue_text = " ".join(visible_lines).strip()
    box_rows = decoded_rows[12:18] if len(decoded_rows) >= 18 else decoded_rows
    prompt_visible = any("▼" in row or "▶" in row or "▷" in row for row in box_rows)
    classification = "none"

    lowered = dialogue_text.lower()
    if not dialogue_text and visible:
        classification = "text_box_visible"
    elif any(keyword in lowered for keyword in ("oak:", "wild pok", "it's unsafe", "hey! wait")):
        classification = "story_text"
    elif any(keyword in lowered for keyword in ("yes", "no")):
        classification = "choice_prompt"
    elif dialogue_text:
        classification = "text"

    recommended_reason = "Visible dialogue is active, so advancing with A is safer than field movement."
    if prompt_visible:
        recommended_reason = "A dialogue prompt arrow is visible, so press A to advance the conversation."
    elif classification == "text_box_visible":
        recommended_reason = "A dialogue box is visible even if the decoded text is incomplete, so press A instead of moving."

    return {
        "visible": visible,
        "message_box_present": message_box_present,
        "prompt_visible": prompt_visible,
        "classification": classification,
        "recommended_reason": recommended_reason,
        "text": dialogue_text,
    }


def _build_mode_state(
    snapshot: dict[str, Any],
    *,
    dialogue_context: dict[str, Any],
    navigation: dict[str, Any],
    candidate_objectives: list[dict[str, Any]],
    decision_state: dict[str, Any],
) -> dict[str, Any]:
    mode = snapshot["mode"]
    state = {
        "map": _compact_map(snapshot["map"]),
        "dialogue": _compact_dialogue(snapshot, dialogue_context),
        "resources": _compact_resources(snapshot),
    }
    if mode == "field":
        state["movement"] = {
            "facing": (snapshot.get("movement") or {}).get("facing"),
        }
        state["field_navigation"] = {
            "active_objective": _compact_objective(
                (navigation.get("objective_state") or {}).get("active_objective")
                or navigation.get("active_objective")
                or navigation.get("objective")
            ),
            "candidate_objectives": [_compact_objective(objective) for objective in candidate_objectives[:4]],
            "tactical_target": _compact_affordance(navigation.get("target_affordance"), include_score=True),
            "ranked_affordances": [
                _compact_affordance(affordance, include_score=True)
                for affordance in (navigation.get("ranked_affordances") or [])[:6]
            ],
            "nearby_affordances": [
                _compact_affordance(affordance)
                for affordance in (navigation.get("affordances") or [])[:8]
            ],
            "progress_signals": (navigation.get("objective_state") or {}).get("progress_signals")
            or navigation.get("progress_signals")
            or [],
            "loop_signals": (navigation.get("objective_state") or {}).get("loop_signals")
            or navigation.get("loop_signals")
            or [],
            "last_movement_result": (navigation.get("last_result") or {}).get("kind"),
            "consecutive_failures": navigation.get("consecutive_failures", 0),
        }
        return state

    if mode in {"dialogue", "menu", "menu_dialogue", "naming"}:
        state["menu"] = _compact_menu(snapshot["menu"])
        recommended_preset_name = _recommended_preset_name(snapshot, decision_state)
        if recommended_preset_name:
            state["recommended_preset_choice"] = recommended_preset_name
        if mode == "naming" or (snapshot.get("naming") or {}).get("active"):
            state["naming"] = _compact_naming(snapshot.get("naming") or {})
        return state

    if mode == "battle":
        state["battle"] = _compact_battle(snapshot.get("battle") or {})
        state["menu"] = _compact_menu(snapshot["menu"])
        return state

    return state


def _build_agent_memory(
    world_state: dict[str, Any],
    *,
    objective_memory: dict[str, Any],
    progress_signals: list[str],
    loop_signals: list[str],
) -> dict[str, Any]:
    return {
        "active_objective": _compact_objective(objective_memory.get("active_objective")),
        "recent_map_history": [entry.get("map") for entry in world_state.get("recent_map_history", [])[-4:]],
        "recent_progress": _compact_progress_entries(objective_memory.get("objective_progress") or []),
        "recent_invalidations": _compact_invalidations(objective_memory.get("objective_invalidations") or []),
        "progress_signals": progress_signals[:4],
        "loop_signals": loop_signals[:4],
    }


def _compact_map(map_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": map_state.get("id"),
        "name": map_state.get("name") or map_state.get("const_name"),
        "x": map_state.get("x"),
        "y": map_state.get("y"),
        "script": map_state.get("script"),
    }


def _compact_dialogue(snapshot: dict[str, Any], dialogue_context: dict[str, Any]) -> dict[str, Any]:
    screen_rows = _non_empty_rows((snapshot.get("screen") or {}).get("decoded_rows") or [])
    payload = {
        "active": snapshot["dialogue"].get("active", False),
        "visible_lines": (snapshot["dialogue"].get("visible_lines") or [])[:3],
        "classification": dialogue_context.get("classification"),
        "prompt_visible": dialogue_context.get("prompt_visible"),
    }
    if screen_rows:
        payload["screen_rows"] = screen_rows[:4]
    return payload


def _compact_menu(menu: dict[str, Any]) -> dict[str, Any]:
    return {
        "active": menu.get("active", False),
        "visible_items": (menu.get("visible_items") or [])[:6],
        "selected_item_text": menu.get("selected_item_text"),
        "selected_index": menu.get("selected_index"),
    }


def _compact_naming(naming: dict[str, Any]) -> dict[str, Any]:
    return {
        "active": naming.get("active", False),
        "screen_type": naming.get("screen_type"),
        "current_text": naming.get("current_text"),
        "base_name": naming.get("base_name"),
    }


def _compact_battle(battle: dict[str, Any]) -> dict[str, Any]:
    return {
        "ui_state": battle.get("ui_state"),
        "selected_command": (battle.get("command_menu") or {}).get("selected_command"),
        "selected_move": ((battle.get("move_menu") or {}).get("selected_move") or {}).get("name"),
    }


def _compact_resources(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "party_summary": _summarize_party(snapshot.get("party") or {}),
        "inventory_summary": _summarize_inventory(snapshot.get("inventory") or {}),
        "trainer_summary": _summarize_money_and_badges(snapshot.get("trainer") or {}),
    }


def _compact_objective(objective: dict[str, Any] | None) -> dict[str, Any] | None:
    if not objective:
        return None
    payload = {
        "id": objective.get("id"),
        "kind": objective.get("kind"),
        "phase": objective.get("phase"),
        "label": objective.get("label"),
        "confidence": objective.get("confidence"),
        "target_affordance_ids": objective.get("target_affordance_ids") or [],
        "evidence": (objective.get("evidence") or [])[:2],
    }
    return payload


def _compact_affordance(affordance: dict[str, Any] | None, *, include_score: bool = False) -> dict[str, Any] | None:
    if not affordance:
        return None
    payload = {
        "id": affordance.get("id"),
        "kind": affordance.get("kind"),
        "label": affordance.get("label"),
        "distance": affordance.get("distance"),
        "reachable": (affordance.get("reachability") or {}).get("reachable"),
        "path_length": (affordance.get("reachability") or {}).get("path_length"),
        "interaction_class": affordance.get("interaction_class"),
        "identity_hints": (affordance.get("identity_hints") or [])[:3],
        "last_outcome": affordance.get("last_outcome"),
    }
    if include_score and affordance.get("score") is not None:
        payload["score"] = affordance.get("score")
    return payload


def _compact_trace_summary(trace: dict[str, Any]) -> dict[str, Any]:
    decision = trace.get("decision") or {}
    return {
        "kind": trace.get("kind"),
        "action": decision.get("action") or (trace.get("payload") or {}).get("action"),
        "after_mode": trace.get("after_mode"),
        "passed": trace.get("passed"),
    }


def _compact_progress_entries(entries: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    return [
        {
            "id": entry.get("id"),
            "success": entry.get("success"),
            "partial": entry.get("partial"),
            "signals": (entry.get("progress_signals") or [])[:3],
        }
        for entry in entries[-limit:]
    ]


def _compact_invalidations(entries: list[dict[str, Any]], *, limit: int = 2) -> list[dict[str, Any]]:
    return [
        {
            "id": entry.get("id"),
            "reason": entry.get("reason"),
        }
        for entry in entries[-limit:]
    ]


def _non_empty_rows(rows: list[str], *, limit: int = 6) -> list[str]:
    return [row for row in rows if row.strip()][:limit]
