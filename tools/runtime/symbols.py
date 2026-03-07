from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

SYM_LINE_RE = re.compile(r"^(?P<bank>[0-9a-fA-F]{2}):(?P<addr>[0-9a-fA-F]{4}) (?P<name>\S+)$")


@dataclass(frozen=True)
class SymbolTable:
    by_name: dict[str, int]

    def address_of(self, name: str) -> int:
        if name not in self.by_name:
            raise KeyError(f"Unknown symbol: {name}")
        return self.by_name[name]


def load_symbol_table(path: str | Path) -> SymbolTable:
    by_name: dict[str, int] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        match = SYM_LINE_RE.match(line)
        if not match:
            continue

        bank = int(match.group("bank"), 16)
        addr = int(match.group("addr"), 16)
        name = match.group("name")

        # We only need absolute 16-bit CPU addresses for WRAM/HRAM telemetry.
        # Banked ROM labels are not used by the runtime yet.
        if bank != 0 and addr < 0xA000:
            continue

        by_name.setdefault(name, addr)

    return SymbolTable(by_name=by_name)
