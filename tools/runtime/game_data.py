from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


MOVE_NAME_RE = re.compile(r'^\s*li\s+"(?P<name>[^"]+)"')
MOVE_DATA_RE = re.compile(
    r"^\s*move\s+"
    r"(?P<const>[A-Z0-9_]+),\s+"
    r"(?P<effect>[A-Z0-9_]+),\s+"
    r"(?P<power>\d+),\s+"
    r"(?P<type>[A-Z0-9_]+),\s+"
    r"(?P<accuracy>\d+)\s*,\s+"
    r"(?P<pp>\d+)"
)


@dataclass(frozen=True)
class MoveInfo:
    move_id: int
    name: str
    power: int
    type_name: str
    accuracy: int
    pp: int


def load_move_catalog(repo_root: Path) -> dict[int, MoveInfo]:
    names_path = repo_root / "data" / "moves" / "names.asm"
    data_path = repo_root / "data" / "moves" / "moves.asm"

    names: list[str] = []
    for raw_line in names_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split(";", 1)[0].rstrip()
        if not line:
            continue
        match = MOVE_NAME_RE.match(line)
        if match:
            names.append(match.group("name"))

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


DEFAULT_MOVE_CATALOG = load_move_catalog(Path(__file__).resolve().parents[2])
