from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re


MAP_CONST_RE = re.compile(r"^\s*map_const\s+([A-Z0-9_]+),\s*(\d+),\s*(\d+)")
MAP_HEADER_RE = re.compile(r"^\s*map_header\s+([A-Za-z0-9_]+),\s*([A-Z0-9_]+),\s*([A-Z0-9_]+),")
WARP_EVENT_RE = re.compile(r"^\s*warp_event\s+(\d+),\s*(\d+),\s*([A-Z0-9_]+),\s*(\d+)")
BG_EVENT_RE = re.compile(r"^\s*bg_event\s+(\d+),\s*(\d+),\s*([A-Z0-9_]+)")
OBJECT_EVENT_RE = re.compile(
    r"^\s*object_event\s+(\d+),\s*(\d+),\s*([A-Z0-9_]+),\s*([A-Z_]+),\s*([A-Z_]+),\s*([A-Z0-9_]+)"
)
MAP_KEY_RE = re.compile(r"^\s*def_warps_to\s+([A-Z0-9_]+)")
TILESET_RE = re.compile(r"^\s*tileset\s+([A-Za-z0-9_]+),")
BLOCK_LABEL_RE = re.compile(r"^\s*([A-Za-z0-9_]+)_Block::(?:\s+INCBIN\s+\"([^\"]+)\")?")
COLL_LABEL_RE = re.compile(r"^\s*([A-Za-z0-9_]+)_Coll::")
COLL_TILES_RE = re.compile(r"^\s*coll_tiles(?:\s+(.*))?$")
INCBIN_RE = re.compile(r'^\s*INCBIN\s+"([^"]+)"')
LABEL_RE = re.compile(r"^([A-Za-z0-9_.]+):$")
SCRIPT_CONST_RE = re.compile(r"^\s*ld a,\s*(SCRIPT_[A-Z0-9_]+)")
X_COORD_RE = re.compile(r"^\s*ld a,\s*\[wXCoord\]")
Y_COORD_RE = re.compile(r"^\s*ld a,\s*\[wYCoord\]")
CP_RE = re.compile(r"^\s*cp\s+(\d+)")


@dataclass(frozen=True)
class MapWarp:
    x: int
    y: int
    target_map: str
    target_warp_id: int


@dataclass(frozen=True)
class MapObject:
    x: int
    y: int
    sprite: str
    movement: str
    facing: str
    text_ref: str


@dataclass(frozen=True)
class MapBgEvent:
    x: int
    y: int
    text_ref: str


@dataclass(frozen=True)
class MapTriggerRegion:
    axis: str
    value: int
    source_label: str
    next_script: str | None
    note: str | None = None


@dataclass
class MapInfo:
    id: int
    const_name: str
    display_name: str
    width: int
    height: int
    header_stem: str | None = None
    tileset_name: str | None = None
    block_file: str | None = None
    warps: list[MapWarp] = field(default_factory=list)
    bg_events: list[MapBgEvent] = field(default_factory=list)
    objects: list[MapObject] = field(default_factory=list)
    triggers: list[MapTriggerRegion] = field(default_factory=list)
    object_file: str | None = None
    script_file: str | None = None
    walkable_grid: list[list[bool]] | None = None
    tile_grid: list[list[int]] | None = None


@dataclass(frozen=True)
class TilesetInfo:
    name: str
    block_file: str
    blocks: list[list[int]]
    walkable_tile_ids: frozenset[int]


@dataclass(frozen=True)
class MapCatalog:
    by_id: dict[int, MapInfo]
    by_name: dict[str, MapInfo]
    tilesets: dict[str, TilesetInfo]

    def get_by_id(self, map_id: int) -> MapInfo | None:
        return self.by_id.get(map_id)

    def get_by_name(self, const_name: str) -> MapInfo | None:
        return self.by_name.get(const_name)

    def get_tileset(self, name: str | None) -> TilesetInfo | None:
        if name is None:
            return None
        normalized = _canonical_name(name)
        return self.tilesets.get(normalized) or self.tilesets.get(name)


def load_map_catalog(repo_root: Path) -> MapCatalog:
    maps = _parse_map_constants(repo_root / "constants" / "map_constants.asm")
    _attach_header_data(maps, repo_root / "data" / "maps" / "headers")
    _attach_object_data(maps, repo_root / "data" / "maps" / "objects")
    _attach_script_triggers(maps, repo_root / "scripts")
    tilesets = _parse_tilesets(repo_root)
    return MapCatalog(
        by_id={map_info.id: map_info for map_info in maps.values()},
        by_name=maps,
        tilesets=tilesets,
    )


def _parse_map_constants(path: Path) -> dict[str, MapInfo]:
    maps: dict[str, MapInfo] = {}
    next_id = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        match = MAP_CONST_RE.match(line)
        if not match:
            continue
        const_name, width, height = match.groups()
        maps[const_name] = MapInfo(
            id=next_id,
            const_name=const_name,
            display_name=_display_name(const_name),
            width=int(width),
            height=int(height),
        )
        next_id += 1
    return maps


def _attach_header_data(maps: dict[str, MapInfo], headers_dir: Path) -> None:
    for path in sorted(headers_dir.glob("*.asm")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = MAP_HEADER_RE.match(line)
            if not match:
                continue
            stem, const_name, tileset_name = match.groups()
            map_info = maps.get(const_name)
            if map_info is None:
                break
            map_info.header_stem = stem
            map_info.tileset_name = tileset_name
            block_path = Path("maps") / f"{stem}.blk"
            if block_path.exists():
                map_info.block_file = str(block_path)
            break


def _attach_object_data(maps: dict[str, MapInfo], objects_dir: Path) -> None:
    for path in sorted(objects_dir.glob("*.asm")):
        lines = path.read_text(encoding="utf-8").splitlines()
        map_key = None
        for line in lines:
            key_match = MAP_KEY_RE.match(line)
            if key_match:
                map_key = key_match.group(1)
                break
        if map_key is None or map_key not in maps:
            continue

        bg_events: list[MapBgEvent] = []
        warps: list[MapWarp] = []
        objects: list[MapObject] = []
        for line in lines:
            warp_match = WARP_EVENT_RE.match(line)
            if warp_match:
                x, y, target_map, target_warp_id = warp_match.groups()
                warps.append(
                    MapWarp(
                        x=int(x),
                        y=int(y),
                        target_map=target_map,
                        target_warp_id=int(target_warp_id),
                    )
                )
                continue

            bg_match = BG_EVENT_RE.match(line)
            if bg_match:
                x, y, text_ref = bg_match.groups()
                bg_events.append(
                    MapBgEvent(
                        x=int(x),
                        y=int(y),
                        text_ref=text_ref,
                    )
                )
                continue

            object_match = OBJECT_EVENT_RE.match(line)
            if object_match:
                x, y, sprite, movement, facing, text_ref = object_match.groups()
                objects.append(
                    MapObject(
                        x=int(x),
                        y=int(y),
                        sprite=sprite,
                        movement=movement,
                        facing=facing,
                        text_ref=text_ref,
                    )
                )
        maps[map_key].warps = warps
        maps[map_key].bg_events = bg_events
        maps[map_key].objects = objects
        maps[map_key].object_file = str(path.relative_to(objects_dir.parent.parent))


def _parse_tilesets(repo_root: Path) -> dict[str, TilesetInfo]:
    block_files = _parse_blockset_files(repo_root / "gfx" / "tilesets.asm")
    walkable_tiles = _parse_collision_tiles(repo_root / "data" / "tilesets" / "collision_tile_ids.asm")
    tilesets: dict[str, TilesetInfo] = {}
    for line in (repo_root / "data" / "tilesets" / "tileset_headers.asm").read_text(encoding="utf-8").splitlines():
        match = TILESET_RE.match(line)
        if not match:
            continue
        name = match.group(1)
        canonical_name = _canonical_name(name)
        block_file = block_files.get(canonical_name) or block_files.get(name)
        if block_file is None:
            continue
        blocks_path = repo_root / block_file
        raw_blocks = blocks_path.read_bytes()
        blocks = [list(raw_blocks[index : index + 16]) for index in range(0, len(raw_blocks), 16)]
        tilesets[canonical_name] = TilesetInfo(
            name=canonical_name,
            block_file=block_file,
            blocks=blocks,
            walkable_tile_ids=frozenset(walkable_tiles.get(canonical_name, set())),
        )
    return tilesets


def _parse_blockset_files(path: Path) -> dict[str, str]:
    pending: list[str] = []
    block_files: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = BLOCK_LABEL_RE.match(line)
        if match:
            label_name, inline_path = match.groups()
            pending.append(_canonical_name(label_name))
            if inline_path:
                for pending_name in pending:
                    block_files[pending_name] = inline_path
                pending.clear()
            continue
        if pending:
            incbin_match = INCBIN_RE.match(line)
            if incbin_match:
                file_path = incbin_match.group(1)
                for pending_name in pending:
                    block_files[pending_name] = file_path
                pending.clear()
    return block_files


def _parse_collision_tiles(path: Path) -> dict[str, set[int]]:
    pending: list[str] = []
    collisions: dict[str, set[int]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split(";", 1)[0].rstrip()
        if not line:
            continue
        label_match = COLL_LABEL_RE.match(line)
        if label_match:
            pending.append(_canonical_name(label_match.group(1)))
            continue
        tiles_match = COLL_TILES_RE.match(line)
        if tiles_match and pending:
            entries = tiles_match.group(1) or ""
            tiles = {
                int(token.strip().removeprefix("$"), 16)
                for token in entries.split(",")
                if token.strip()
            }
            for pending_name in pending:
                collisions[pending_name] = set(tiles)
            pending.clear()
    return collisions


def _attach_script_triggers(maps: dict[str, MapInfo], scripts_dir: Path) -> None:
    for const_name, map_info in maps.items():
        path = scripts_dir / f"{_script_stem(const_name)}.asm"
        if not path.exists():
            continue
        map_info.script_file = str(path.relative_to(scripts_dir.parent))
        map_info.triggers = _parse_script_triggers(path)


def _parse_script_triggers(path: Path) -> list[MapTriggerRegion]:
    sections: dict[str, list[str]] = {}
    current_label: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        label_match = LABEL_RE.match(raw_line.strip())
        if label_match:
            current_label = label_match.group(1)
            sections.setdefault(current_label, [])
            continue
        if current_label is not None:
            sections[current_label].append(raw_line.rstrip())

    triggers: list[MapTriggerRegion] = []
    for label, lines in sections.items():
        axis: str | None = None
        value: int | None = None
        next_script: str | None = None
        note: str | None = None
        for line in lines:
            stripped = line.strip()
            if axis is None:
                if X_COORD_RE.match(stripped):
                    axis = "x"
                    continue
                if Y_COORD_RE.match(stripped):
                    axis = "y"
                    continue
            elif value is None:
                cp_match = CP_RE.match(stripped)
                if cp_match:
                    value = int(cp_match.group(1))
                    if ";" in stripped:
                        note = stripped.split(";", 1)[1].strip()
                    continue

            if next_script is None:
                script_match = SCRIPT_CONST_RE.match(stripped)
                if script_match:
                    next_script = script_match.group(1)

        if axis is not None and value is not None:
            triggers.append(
                MapTriggerRegion(
                    axis=axis,
                    value=value,
                    source_label=label,
                    next_script=next_script,
                    note=note,
                )
            )
    return triggers


def build_walkability_grid(map_info: MapInfo, map_catalog: MapCatalog) -> tuple[list[list[bool]], list[list[int]]] | None:
    if map_info.walkable_grid is not None and map_info.tile_grid is not None:
        return map_info.walkable_grid, map_info.tile_grid
    if map_info.tileset_name is None or map_info.block_file is None:
        return None

    tileset = map_catalog.get_tileset(map_info.tileset_name)
    if tileset is None:
        return None

    block_ids = (Path(__file__).resolve().parents[2] / map_info.block_file).read_bytes()
    expected = map_info.width * map_info.height
    if len(block_ids) < expected:
        return None

    width_cells = map_info.width * 2
    height_cells = map_info.height * 2
    walkable_grid = [[False for _ in range(width_cells)] for _ in range(height_cells)]
    tile_grid = [[-1 for _ in range(width_cells)] for _ in range(height_cells)]

    quadrant_indices = (
        (0, 0, 4),
        (0, 1, 6),
        (1, 0, 12),
        (1, 1, 14),
    )
    for block_y in range(map_info.height):
        for block_x in range(map_info.width):
            block_id = block_ids[block_y * map_info.width + block_x]
            if block_id >= len(tileset.blocks):
                continue
            block = tileset.blocks[block_id]
            for y_offset, x_offset, tile_index in quadrant_indices:
                grid_y = block_y * 2 + y_offset
                grid_x = block_x * 2 + x_offset
                tile_id = block[tile_index]
                tile_grid[grid_y][grid_x] = tile_id
                walkable_grid[grid_y][grid_x] = tile_id in tileset.walkable_tile_ids

    map_info.walkable_grid = walkable_grid
    map_info.tile_grid = tile_grid
    return walkable_grid, tile_grid


def _script_stem(const_name: str) -> str:
    parts = const_name.split("_")
    stem_parts: list[str] = []
    for part in parts:
        if part == "SS":
            stem_parts.append("SS")
        else:
            stem_parts.append(part.title())
    return "".join(stem_parts)


def _display_name(const_name: str) -> str:
    replacements = {
        "REDS": "Red's",
        "BLUES": "Blue's",
        "OAKS": "Oak's",
        "POKEMON": "Pokemon",
        "POKECENTER": "Poke Center",
        "MART": "Mart",
        "GYM": "Gym",
        "LAB": "Lab",
        "ROUTE": "Route",
        "TOWN": "Town",
        "CITY": "City",
        "HOUSE": "House",
        "ISLAND": "Island",
        "PLATEAU": "Plateau",
        "FLOOR": "Floor",
        "ROOM": "Room",
        "SS": "S.S.",
    }
    parts = const_name.split("_")
    pretty_parts: list[str] = []
    for part in parts:
        if part in replacements:
            pretty_parts.append(replacements[part])
        elif part.endswith("F") and part[:-1].isdigit():
            pretty_parts.append(part)
        elif part.isdigit():
            pretty_parts.append(part)
        else:
            pretty_parts.append(part.title())
    return " ".join(pretty_parts)


def _canonical_name(name: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    value = re.sub(r"(?<=[A-Za-z])(?=[0-9])", "_", value)
    return value.upper()
