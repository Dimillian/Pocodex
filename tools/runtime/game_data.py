from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


MOVE_NAME_RE = re.compile(r'^\s*li\s+"(?P<name>[^"]+)"')
ITEM_NAME_RE = re.compile(r'^\s*li\s+"(?P<name>[^"]+)"')
SPECIES_NAME_RE = re.compile(r'^\s*dname\s+"(?P<name>[^"]+)"')
MOVE_DATA_RE = re.compile(
    r"^\s*move\s+"
    r"(?P<const>[A-Z0-9_]+),\s+"
    r"(?P<effect>[A-Z0-9_]+),\s+"
    r"(?P<power>\d+),\s+"
    r"(?P<type>[A-Z0-9_]+),\s+"
    r"(?P<accuracy>\d+)\s*,\s+"
    r"(?P<pp>\d+)"
)
CONST_DEF_RE = re.compile(r"^\s*const_def(?:\s+(.+))?$")
CONST_NEXT_RE = re.compile(r"^\s*const_next\s+(.+)$")
CONST_SKIP_RE = re.compile(r"^\s*const_skip(?:\s+(.+))?$")
CONST_RE = re.compile(r"^\s*const\s+([A-Z0-9_]+)")


@dataclass(frozen=True)
class MoveInfo:
    move_id: int
    name: str
    power: int
    type_name: str
    accuracy: int
    pp: int


KANTO_BADGE_NAMES = (
    "BOULDERBADGE",
    "CASCADEBADGE",
    "THUNDERBADGE",
    "RAINBOWBADGE",
    "SOULBADGE",
    "MARSHBADGE",
    "VOLCANOBADGE",
    "EARTHBADGE",
)


def _load_quoted_name_list(path: Path, pattern: re.Pattern[str]) -> list[str]:
    names: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split(";", 1)[0].rstrip()
        if not line:
            continue
        match = pattern.match(line)
        if match:
            names.append(match.group("name"))
    return names


def load_move_catalog(repo_root: Path) -> dict[int, MoveInfo]:
    names_path = repo_root / "data" / "moves" / "names.asm"
    data_path = repo_root / "data" / "moves" / "moves.asm"

    names = _load_quoted_name_list(names_path, MOVE_NAME_RE)

    data_rows: list[tuple[int, str, int, str, int, int]] = []
    for raw_line in data_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split(";", 1)[0].rstrip()
        if not line:
            continue
        match = MOVE_DATA_RE.match(line)
        if not match:
            continue
        data_rows.append(
            (
                len(data_rows) + 1,
                match.group("const"),
                int(match.group("power")),
                match.group("type"),
                int(match.group("accuracy")),
                int(match.group("pp")),
            )
        )

    catalog: dict[int, MoveInfo] = {}
    for move_id, (_, _, power, type_name, accuracy, pp) in enumerate(data_rows, start=1):
        name = names[move_id - 1] if move_id - 1 < len(names) else f"MOVE_{move_id}"
        catalog[move_id] = MoveInfo(
            move_id=move_id,
            name=name,
            power=power,
            type_name=type_name,
            accuracy=accuracy,
            pp=pp,
        )
    return catalog


def load_item_catalog(repo_root: Path) -> dict[int, str]:
    names_path = repo_root / "data" / "items" / "names.asm"
    names = _load_quoted_name_list(names_path, ITEM_NAME_RE)
    return {
        item_id: name
        for item_id, name in enumerate(names, start=1)
    }


def load_species_catalog(repo_root: Path) -> dict[int, str]:
    names_path = repo_root / "data" / "pokemon" / "names.asm"
    names = _load_quoted_name_list(names_path, SPECIES_NAME_RE)
    return {
        species_id: name
        for species_id, name in enumerate(names, start=1)
    }


def load_event_catalog(repo_root: Path) -> dict[int, str]:
    path = repo_root / "constants" / "event_constants.asm"
    next_value = 0
    catalog: dict[int, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split(";", 1)[0].rstrip()
        if not line:
            continue
        const_def_match = CONST_DEF_RE.match(line)
        if const_def_match:
            expr = const_def_match.group(1)
            next_value = _parse_const_expression(expr) if expr else 0
            continue
        const_next_match = CONST_NEXT_RE.match(line)
        if const_next_match:
            next_value = _parse_const_expression(const_next_match.group(1))
            continue
        const_skip_match = CONST_SKIP_RE.match(line)
        if const_skip_match:
            amount_expr = const_skip_match.group(1)
            next_value += _parse_const_expression(amount_expr) if amount_expr else 1
            continue
        const_match = CONST_RE.match(line)
        if const_match:
            catalog[next_value] = const_match.group(1)
            next_value += 1
    return catalog


def _parse_const_expression(expr: str) -> int:
    total: int | None = None
    operator = "+"
    for token in expr.replace("$", "0x").split():
        if token in {"+", "-"}:
            operator = token
            continue
        value = int(token, 0)
        if total is None:
            total = value
            continue
        if operator == "+":
            total += value
        else:
            total -= value
    return total or 0


DEFAULT_MOVE_CATALOG = load_move_catalog(Path(__file__).resolve().parents[2])
DEFAULT_ITEM_CATALOG = load_item_catalog(Path(__file__).resolve().parents[2])
DEFAULT_SPECIES_CATALOG = load_species_catalog(Path(__file__).resolve().parents[2])
DEFAULT_EVENT_CATALOG = load_event_catalog(Path(__file__).resolve().parents[2])
