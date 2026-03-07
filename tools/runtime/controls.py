from __future__ import annotations

BUTTON_NAMES = {
    "a",
    "b",
    "start",
    "select",
    "up",
    "down",
    "left",
    "right",
}


def normalize_button(button: str) -> str:
    value = button.strip().lower()
    if value not in BUTTON_NAMES:
        supported = ", ".join(sorted(BUTTON_NAMES))
        raise ValueError(f"Unsupported button '{button}'. Expected one of: {supported}")
    return value
