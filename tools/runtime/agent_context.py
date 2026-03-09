from __future__ import annotations

from typing import Any


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
    model_input = {
        "objective": "Make forward progress in Pokemon Blue one verified action at a time.",
        "observation": {
            "mode": snapshot["mode"],
            "interaction": snapshot.get("interaction"),
            "map": snapshot["map"],
            "movement": snapshot.get("movement"),
            "navigation": snapshot.get("navigation"),
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
            "events": recent_events,
        },
        "decision_state": decision_state,
        "recent_traces": recent_traces,
    }

    context = {
        "objective": "Make forward progress in Pokemon Blue one verified action at a time.",
        "observation": {
            "mode": snapshot["mode"],
            "interaction": snapshot.get("interaction"),
            "map": snapshot["map"],
            "movement": snapshot.get("movement"),
            "navigation": snapshot.get("navigation"),
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
        },
        "decision_state": decision_state,
        "heuristic_next_action": heuristic_next_action,
        "allowed_actions": allowed_actions,
        "rules": [
            "Choose exactly one next action from allowed_actions.",
            "Prefer resolving the current interaction over guessing movement.",
            "When a menu is visible, navigate or confirm within that menu before moving in the field.",
            "When a preset name list is visible and a listed recommended name exists, prefer that listed option over NEW NAME or manual text entry.",
            "When navigation.target_affordance is present, prefer actions that move toward or interact with that target before relying on the older objective fallback.",
            "When using follow_target, include an affordance_id from navigation.ranked_affordances if you want to choose a specific world target yourself.",
            "If movement fails twice in a row, prefer waiting briefly and re-observing before forcing another direction.",
            "Use save/load only when recovery is needed, not as a normal action.",
            "If the state is ambiguous, choose wait_short rather than inventing a risky action.",
        ],
        "recent_traces": recent_traces,
        "model_input": model_input,
        "output_contract": {
            "format": "json",
            "schema": {
                "action": "one action id from allowed_actions",
                "reason": "short explanation grounded in the observation",
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
                {"id": "follow_objective", "type": "macro", "name": "follow_objective", "description": "Use local navigation to follow the current story objective for several verified steps."},
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
    objective = navigation.get("objective")
    target_affordance = navigation.get("target_affordance")
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
        if target_affordance:
            return {
                "action": "follow_target",
                "reason": f"Use local navigation to pursue {target_affordance['label']} based on the world-model ranking.",
            }
        if _decision_flag(decision_state, "oak_intro_active"):
            return {"action": "press_a", "reason": "The intro script still appears active."}
        if objective:
            objective_label = objective.get("label", "the current objective")
            return {"action": "follow_objective", "reason": f"Use local navigation to progress toward {objective_label}."}
        return {"action": "move_down", "reason": "Field exploration can probe one move at a time."}
    if mode == "battle":
        return {"action": "follow_interaction", "reason": "Battle interaction is active."}
    return {"action": "wait_short", "reason": "The state appears transitional."}


def build_agent_prompt(context: dict[str, Any]) -> str:
    observation = context["observation"]
    allowed_ids = ", ".join(action["id"] for action in context["allowed_actions"])
    event_lines = "\n".join(f"- {event}" for event in observation["events"]) or "- none"
    trace_lines = "\n".join(
        f"- kind={trace['kind']} after_mode={trace['after_mode']} passed={trace['passed']}"
        for trace in context["recent_traces"]
    ) or "- none"
    menu_line = ", ".join(observation["menu"]["visible_items"]) or "none"
    dialogue_line = " | ".join(observation["dialogue"]["visible_lines"]) or "none"
    dialogue_context = observation["dialogue"].get("context") or {}
    movement = observation.get("movement") or {}
    navigation = observation.get("navigation") or {}
    interaction = observation.get("interaction") or {}
    naming = observation.get("naming") or {}
    pokedex = observation.get("pokedex") or {}
    battle = observation.get("battle") or {}
    party = observation.get("party") or {}
    inventory = observation.get("inventory") or {}
    trainer = observation.get("trainer") or {}
    selected_move = ((battle.get("move_menu") or {}).get("selected_move") or {}).get("name")
    objective = navigation.get("objective")
    target_affordance = navigation.get("target_affordance")
    objective_line = "none"
    if objective:
        objective_line = f"{objective.get('kind')}: {objective.get('label')}"
    target_line = "none"
    if target_affordance:
        target_line = f"{target_affordance.get('kind')}: {target_affordance.get('label')}"
    affordance_lines = context["model_input"]["observation"]["navigation"].get("affordances") or []
    affordance_summary = ", ".join(
        f"{affordance['id']}={affordance['kind']}"
        for affordance in affordance_lines[:8]
    ) or "none"
    ranked_affordances = navigation.get("ranked_affordances") or []
    ranked_summary = ", ".join(
        f"{affordance['id']}({affordance['score']})"
        for affordance in ranked_affordances[:6]
    ) or "none"
    facing = movement.get("facing") or "unknown"
    move_result = navigation.get("last_result", {}).get("kind") if navigation.get("last_result") else "none"
    failures = navigation.get("consecutive_failures", 0)
    map_name = observation["map"].get("name") or observation["map"].get("const_name") or f"Map {observation['map']['id']}"
    target_reason = navigation.get("target_reason") or "none"
    recommended_preset_name = _recommended_preset_name(observation, context["decision_state"])
    preset_name_line = "none"
    if recommended_preset_name:
        preset_name_line = recommended_preset_name
    party_summary = _summarize_party(party)
    inventory_summary = _summarize_inventory(inventory)
    money_summary = _summarize_money_and_badges(trainer)

    return (
        "You are choosing the next action for Pokemon Blue.\n"
        f"Current mode: {observation['mode']}\n"
        f"Interaction type: {interaction.get('type')}\n"
        f"Map: {map_name} (id={observation['map']['id']}) x={observation['map']['x']} y={observation['map']['y']}\n"
        f"Facing: {facing}\n"
        f"Target affordance: {target_line}\n"
        f"Target reason: {target_reason}\n"
        f"Objective: {objective_line}\n"
        f"Affordances: {affordance_summary}\n"
        f"Ranked affordances: {ranked_summary}\n"
        f"Last movement result: {move_result}; consecutive failures: {failures}\n"
        f"Dialogue: {dialogue_line}\n"
        f"Dialogue visible: {dialogue_context.get('visible', False)}; "
        f"prompt visible: {dialogue_context.get('prompt_visible', False)}; "
        f"classification: {dialogue_context.get('classification', 'none')}\n"
        f"Menu items: {menu_line}\n"
        f"Selected menu item: {observation['menu']['selected_item_text']}\n"
        f"Recommended preset menu choice: {preset_name_line}\n"
        f"Naming screen: {naming.get('active', False)} type={naming.get('screen_type')} current='{naming.get('current_text')}' base='{naming.get('base_name')}'\n"
        f"Pokedex screen: {pokedex.get('active', False)} species={pokedex.get('species_name')} class={pokedex.get('species_class')} info={' | '.join(pokedex.get('description_lines', []))}\n"
        f"Party summary: {party_summary}\n"
        f"Inventory summary: {inventory_summary}\n"
        f"Trainer summary: {money_summary}\n"
        f"Battle state: ui={battle.get('ui_state')} selected_command={battle.get('command_menu', {}).get('selected_command')} selected_move={selected_move}\n"
        "Recent events:\n"
        f"{event_lines}\n"
        "Recent traces:\n"
        f"{trace_lines}\n"
        f"Heuristic next action: {context['heuristic_next_action']['action']} "
        f"because {context['heuristic_next_action']['reason']}\n"
        "Rules:\n"
        + "\n".join(f"- {rule}" for rule in context["rules"])
        + "\n"
        f"Allowed actions: {allowed_ids}\n"
        'Return JSON only: {"action":"...", "reason":"...", "affordance_id":"...optional..."}'
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
