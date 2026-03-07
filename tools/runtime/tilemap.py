from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

CHARMAP_RE = re.compile(r'^\s*charmap\s+"(?P<token>.*)",\s+\$(?P<code>[0-9a-fA-F]{2})')

MESSAGE_BOX = (0, 12, 19, 17)
CURSOR_GLYPHS = {"▶", "▷", "▼", "▲"}

TOKEN_OVERRIDES = {
    "<NULL>": " ",
    "@": "",
    "<PAGE>": "",
    "<_CONT>": "",
    "<SCROLL>": "",
    "<NEXT>": "",
    "<LINE>": "",
    "<PARA>": "",
    "<CONT>": "",
    "<DONE>": "",
    "<PROMPT>": "",
    "<TARGET>": "{TARGET}",
    "<USER>": "{USER}",
    "<PLAYER>": "{PLAYER}",
    "<RIVAL>": "{RIVAL}",
    "<PC>": "PC",
    "<TM>": "TM",
    "<TRAINER>": "TRAINER",
    "<ROCKET>": "ROCKET",
    "<DEXEND>": "",
    "<BOLD_V>": "V",
    "<BOLD_S>": "S",
    "<BOLD_P>": "P",
    "<COLON>": ":",
    "<PKMN>": "PKMN",
    "<PK>": "PK",
    "<MN>": "MN",
    "<LV>": "LV",
    "<to>": "to",
    "<ID>": "ID",
    "<DOT>": ".",
}


@dataclass(frozen=True)
class Charmap:
    code_to_text: dict[int, str]

    def decode_byte(self, value: int) -> str:
        return self.code_to_text.get(value, f"<{value:02x}>")


def _normalize_token(token: str) -> str:
    if token in TOKEN_OVERRIDES:
        return TOKEN_OVERRIDES[token]
    if token.startswith("<") and token.endswith(">"):
        return token[1:-1]
    return token


def load_charmap(path: str | Path) -> Charmap:
    code_to_text: dict[int, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.split(";", 1)[0].rstrip()
        if not line:
            continue
        match = CHARMAP_RE.match(line)
        if not match:
            continue
        code = int(match.group("code"), 16)
        token = match.group("token")
        code_to_text.setdefault(code, _normalize_token(token))
    return Charmap(code_to_text=code_to_text)


def decode_tilemap_rows(tilemap_rows: list[list[int]], charmap: Charmap) -> list[str]:
    return ["".join(row) for row in decode_tilemap_cells(tilemap_rows, charmap)]


def decode_tilemap_cells(tilemap_rows: list[list[int]], charmap: Charmap) -> list[list[str]]:
    return [[charmap.decode_byte(value) for value in row] for row in tilemap_rows]


def clean_ui_text(text: str) -> str:
    return text.rstrip(" ▼▶▷▲").strip()


def is_box_present(tilemap_rows: list[list[int]], x0: int, y0: int, x1: int, y1: int) -> bool:
    decoded = decode_tilemap_cells(tilemap_rows[y0 : y1 + 1], DEFAULT_CHARMAP)
    if not decoded or len(decoded) < (y1 - y0 + 1):
        return False

    top = decoded[0]
    bottom = decoded[-1]
    if top[x0] != "┌" or top[x1] != "┐":
        return False
    if bottom[x0] != "└" or bottom[x1] != "┘":
        return False
    if any(ch != "─" for ch in top[x0 + 1 : x1]):
        return False
    if any(ch != "─" for ch in bottom[x0 + 1 : x1]):
        return False

    for row in decoded[1:-1]:
        if row[x0] != "│" or row[x1] != "│":
            return False

    return True


def extract_box_lines(
    decoded_rows: list[str],
    *,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    decoded_cells: list[list[str]] | None = None,
) -> list[str]:
    lines: list[str] = []
    if decoded_cells is None:
        decoded_cells = [list(row) for row in decoded_rows]
    for row in decoded_cells[y0 + 1 : y1]:
        inner = clean_ui_text("".join(row[x0 + 1 : x1]))
        if inner:
            lines.append(inner)
    return lines


@dataclass(frozen=True)
class MenuState:
    active: bool
    items: list[str]
    selected_index: int | None
    cursor_visible: bool
    cursor_tile: str | None
    candidate_rows: list[int]
    raw_segments: list[str]


def extract_menu_state(
    decoded_rows: list[str],
    top_x: int,
    top_y: int,
    max_item: int,
    *,
    cursor_x: int | None,
    cursor_y: int | None,
) -> MenuState:
    if top_x < 0 or top_y < 0 or max_item < 0:
        return MenuState(
            active=False,
            items=[],
            selected_index=None,
            cursor_visible=False,
            cursor_tile=None,
            candidate_rows=[],
            raw_segments=[],
        )

    raw_segments: list[str] = []
    candidate_rows: list[int] = []
    items: list[str] = []
    selected_index: int | None = None

    cursor_tile: str | None = None
    if (
        cursor_x is not None
        and cursor_y is not None
        and 0 <= cursor_y < len(decoded_rows)
        and 0 <= cursor_x < len(decoded_rows[cursor_y])
    ):
        cursor_tile = decoded_rows[cursor_y][cursor_x]
    cursor_visible = cursor_tile in CURSOR_GLYPHS

    for offset in range(max_item + 1):
        row_index = top_y + offset * 2
        if row_index >= len(decoded_rows):
            break
        row = decoded_rows[row_index]
        if top_x >= len(row):
            break

        candidate_rows.append(row_index)
        segment = row[top_x:]
        raw_segments.append(segment.rstrip())
        row_selected = cursor_visible and row_index == cursor_y and cursor_x == top_x

        if "│" in segment:
            segment = segment.split("│", 1)[0]
        cleaned = clean_ui_text(segment.lstrip("▶▷▼▲ "))
        if cleaned:
            if row_selected:
                selected_index = len(items)
            items.append(cleaned)

    active = cursor_visible and selected_index is not None and bool(items)
    if not active:
        return MenuState(
            active=False,
            items=[],
            selected_index=None,
            cursor_visible=cursor_visible,
            cursor_tile=cursor_tile,
            candidate_rows=candidate_rows,
            raw_segments=raw_segments,
        )

    if selected_index >= len(items):
        selected_index = None

    return MenuState(
        active=True,
        items=items,
        selected_index=selected_index,
        cursor_visible=cursor_visible,
        cursor_tile=cursor_tile,
        candidate_rows=candidate_rows,
        raw_segments=raw_segments,
    )


DEFAULT_CHARMAP = load_charmap(Path(__file__).resolve().parents[2] / "constants" / "charmap.asm")
