from __future__ import annotations

from typing import Any


def build_agent_context(
    snapshot: dict[str, Any],
    traces: list[dict[str, Any]],
    *,
    planner_state: dict[str, Any],
) -> dict[str, Any]:
    allowed_actions = build_allowed_actions(snapshot)
    heuristic_next_action = build_heuristic_hint(snapshot, planner_state)
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

    context = {
        "objective": "Make forward progress in Pokemon Blue one verified action at a time.",
        "observation": {
            "mode": snapshot["mode"],
            "map": snapshot["map"],
            "dialogue": snapshot["dialogue"],
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
        "planner_state": planner_state,
        "heuristic_next_action": heuristic_next_action,
        "allowed_actions": allowed_actions,
        "rules": [
            "Choose exactly one next action from allowed_actions.",
            "Prefer advancing visible dialogue over guessing movement.",
            "When a menu is visible, navigate or confirm within that menu before moving in the field.",
            "Use save/load only when recovery is needed, not as a normal action.",
            "If the state is ambiguous, choose wait_short rather than inventing a risky action.",
        ],
        "recent_traces": recent_traces,
        "output_contract": {
            "format": "json",
            "schema": {
                "action": "one action id from allowed_actions",
                "reason": "short explanation grounded in the observation",
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
    if mode in {"dialogue", "menu_dialogue"}:
        actions.extend(
            [
                {"id": "press_a", "type": "action", "button": "a", "description": "Advance or confirm the visible dialogue/menu prompt."},
                {"id": "press_b", "type": "action", "button": "b", "description": "Back out or dismiss the current prompt if needed."},
            ]
        )
    if mode in {"menu", "menu_dialogue"}:
        actions.extend(
            [
                {"id": "menu_up", "type": "routine", "name": "move_up", "description": "Move the menu cursor up."},
                {"id": "menu_down", "type": "routine", "name": "move_down", "description": "Move the menu cursor down."},
                {"id": "menu_confirm", "type": "action", "button": "a", "description": "Confirm the selected menu item."},
                {"id": "menu_back", "type": "action", "button": "b", "description": "Back out of the current menu."},
            ]
        )
    if mode == "field":
        actions.extend(
            [
                {"id": "move_up", "type": "routine", "name": "move_up", "description": "Attempt to move one step up."},
                {"id": "move_down", "type": "routine", "name": "move_down", "description": "Attempt to move one step down."},
                {"id": "move_left", "type": "routine", "name": "move_left", "description": "Attempt to move one step left."},
                {"id": "move_right", "type": "routine", "name": "move_right", "description": "Attempt to move one step right."},
                {"id": "press_start", "type": "action", "button": "start", "description": "Open the pause/menu screen."},
                {"id": "interact_a", "type": "action", "button": "a", "description": "Interact with the tile or object in front of the player."},
            ]
        )
    if mode == "battle":
        actions.extend(
            [
                {"id": "battle_confirm", "type": "action", "button": "a", "description": "Advance or confirm the current battle prompt."},
                {"id": "battle_cancel", "type": "action", "button": "b", "description": "Back out if the battle menu allows it."},
                {"id": "battle_up", "type": "routine", "name": "move_up", "description": "Move the battle cursor up."},
                {"id": "battle_down", "type": "routine", "name": "move_down", "description": "Move the battle cursor down."},
                {"id": "battle_left", "type": "routine", "name": "move_left", "description": "Move the battle cursor left."},
                {"id": "battle_right", "type": "routine", "name": "move_right", "description": "Move the battle cursor right."},
            ]
        )

    return actions


def build_heuristic_hint(snapshot: dict[str, Any], planner_state: dict[str, Any]) -> dict[str, Any]:
    mode = snapshot["mode"]
    if mode == "dialogue":
        return {"action": "press_a", "reason": "Visible dialogue is active."}
    if mode == "menu_dialogue":
        if snapshot["menu"]["selected_item_text"]:
            return {"action": "menu_confirm", "reason": "A menu choice is selected alongside dialogue."}
        return {"action": "menu_down", "reason": "A menu is open; cursor movement is safer than guessing."}
    if mode == "menu":
        if snapshot["menu"]["selected_item_text"]:
            return {"action": "menu_confirm", "reason": "A menu item is selected."}
        return {"action": "menu_down", "reason": "A menu is open without a selected target."}
    if mode == "field" and snapshot["map"]["id"] == 0:
        return {"action": "press_start", "reason": "At the title screen, opening the menu is the next deterministic step."}
    if mode == "field" and planner_state.get("oak_intro_active"):
        return {"action": "interact_a", "reason": "The intro script still appears active."}
    if mode == "field":
        return {"action": "move_down", "reason": "Field exploration can probe one move at a time."}
    if mode == "battle":
        return {"action": "battle_confirm", "reason": "Battle flow is not specialized yet."}
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

    return (
        "You are choosing the next action for Pokemon Blue.\n"
        f"Current mode: {observation['mode']}\n"
        f"Map: id={observation['map']['id']} x={observation['map']['x']} y={observation['map']['y']}\n"
        f"Dialogue: {dialogue_line}\n"
        f"Menu items: {menu_line}\n"
        f"Selected menu item: {observation['menu']['selected_item_text']}\n"
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
        'Return JSON only: {"action":"...", "reason":"..."}'
    )
