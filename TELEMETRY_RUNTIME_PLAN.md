# Programmable Runtime And Telemetry Plan

This document is the working execution plan for turning `pokered` into a local
runtime we can:

- launch from code, not only from an emulator app
- inspect with symbol-aware telemetry
- control programmatically
- wrap in a custom UI with extra panels such as a minimap and dialogue log

It is intentionally written as an execution document, not a product brief.
Items are split into phases with explicit gates so we can implement and verify
them incrementally.


## Current State

- ROM build is working locally with `rgbds v1.0.1`.
- Manual launch is working via `./play` and SameBoy.
- The disassembly emits `.sym` files we can use for symbol-aware RAM reads.
- The repo already gives us the key state symbols we need for phase 1:
  - map and player position
  - battle state
  - menu state
  - joypad state
  - screen tile buffer (`wTileMap`)


## Goals

1. Create a programmable runtime that can launch the ROM and step it from code.
2. Add a telemetry layer that turns raw RAM into structured state.
3. Add an input/control layer so the agent can interact with the game.
4. Build a custom UI around the main Game Boy render.
5. Support future overlays such as a minimap, event log, and agent panel.


## Non-Goals For The First Implementation

- Do not build a true native reimplementation of Pokemon Red/Blue.
- Do not modify game logic unless instrumentation requires it.
- Do not start with a polished cross-platform desktop app.
- Do not start with full map decoding, battle AI, or pathfinding automation.


## Recommended Architecture

Use a programmable emulator runtime first, then layer telemetry and UI on top.

### Runtime choice

Phase 1 recommendation: `PyBoy`

Why:

- fastest path to launch, frame stepping, screenshots, RAM reads, and input
- Python is the best fit for telemetry extraction and agent control loops
- easy to expose state over local HTTP/WebSocket for a custom UI

Longer-term option:

- swap to an embedded `mGBA` or `SameBoy` core if we need tighter control,
  better performance, or a more polished packaged app


## Proposed Repository Additions

```text
tools/runtime/
  README.md
  requirements.txt
  server.py
  session.py
  symbols.py
  telemetry.py
  controls.py
  screen.py
  tilemap.py
  state_models.py
  map_data.py
  tests/
ui/
  package.json
  src/
    main.tsx
    App.tsx
    panels/
    lib/api.ts
```

Notes:

- `tools/runtime/` owns emulator integration and telemetry extraction.
- `ui/` owns the custom shell around the live framebuffer and telemetry panels.
- The existing ROM build stays in the repo root as the source of truth.


## Phase Breakdown

## Phase 0: Working Foundations

Status: `DONE`

Deliverables:

- buildable ROMs
- manual local launcher
- reproducible local prerequisites

Completed:

- [x] Install compatible `rgbds`
- [x] Install a local emulator for manual play
- [x] Add `./play`
- [x] Document local play flow

Exit criteria:

- `make -j4` succeeds
- `./play` launches the game


## Phase 1: Programmable Runtime Skeleton

Status: `DONE`

Objective:

Get the game running from code, with a local process we can start/stop without
using the GUI emulator as the integration point.

Tasks:

- [x] Create `tools/runtime/README.md` with setup and usage
- [x] Create a Python virtualenv-friendly dependency file
- [x] Add a runtime session module that:
  - starts the emulator with a selected ROM
  - loads/saves SRAM
  - advances frames
  - captures the framebuffer
  - sends button presses
- [x] Add a command to launch the runtime from the terminal
- [x] Keep `./play` as a manual fallback path

Suggested commands:

- `python -m tools.runtime.server --rom blue`
- `python -m tools.runtime.server --rom red`

Exit criteria:

- runtime launches the ROM from the command line
- runtime can capture one framebuffer image
- runtime can press a button and produce a visible state change
- runtime can optionally auto-run in the background

Validation:

- start runtime
- request current frame
- press `Start`
- request next frame
- confirm the framebuffer changed


## Phase 2: Symbol And RAM Telemetry

Status: `DONE`

Objective:

Turn raw RAM reads into structured, named game state using the generated `.sym`
file from this repo.

Tasks:

- [x] Parse `.sym` into an address lookup table
- [x] Support both Blue and Red symbol maps
- [x] Add named symbol reads for:
  - `wCurMap`
  - `wXCoord`
  - `wYCoord`
  - `wCurMapScript`
  - `wCurrentMenuItem`
  - `wMenuJoypadPollCount`
  - `wIsInBattle`
  - `wCurOpponent`
  - `wBattleType`
  - `hJoyInput`
  - `hJoyHeld`
  - `hJoyPressed`
  - `hJoyReleased`
  - `wTileMap`
- [x] Emit structured telemetry JSON
- [x] Add a polling endpoint and optional frame-stream endpoint

Example output shape:

```json
{
  "frame": 12345,
  "mode": "overworld",
  "map": { "id": 37, "x": 10, "y": 4, "script": 0 },
  "menu": { "current_item": 0, "poll_count": 0 },
  "battle": { "in_battle": false, "type": 0, "opponent": 0 },
  "input": { "held": 0, "pressed": 0, "released": 0 },
  "screen": { "tilemap_rows": ["...", "..."] }
}
```

Exit criteria:

- a single command returns a valid telemetry snapshot
- the snapshot includes live player coordinates and current map id
- the snapshot updates correctly while moving around the map

Validation:

- stand still and capture snapshot
- move right one tile
- capture another snapshot
- confirm `x` changed and `frame` advanced


## Phase 3: Text And UI Decoding

Status: `DONE`

Objective:

Make the runtime understandable without OCR by decoding screen-relevant buffers
into agent-usable text and UI state.

Tasks:

- [x] Decode `wTileMap` into tile rows
- [x] Map tile ids to visible text where feasible
- [x] Detect common UI modes:
  - overworld
  - dialogue
  - menu
  - battle
  - transition/loading
- [x] Extract visible dialogue box text
- [x] Extract basic menu cursor position and visible menu entries
- [x] Add a rolling event log for notable state transitions

Exit criteria:

- runtime can tell whether the game is in overworld, dialogue, menu, or battle
- visible dialogue is available as text without OCR
- current menu selection is available in structured state

Validation:

- open main menu
- confirm `mode=menu`
- advance to a dialogue box
- confirm `mode=dialogue`
- capture decoded text rows


## Phase 4: Agent Control Loop

Status: `IN PROGRESS`

Objective:

Allow the agent to observe, decide, and act through a stable interface.

Tasks:

- [x] Add an action API:
  - `press(button, frames=2)`
  - `tap(button)`
  - `sequence([...])`
- [x] Add local save/load state checkpointing
- [x] Add frame-stable action timing
- [x] Prevent repeated button spam on non-stable frames
- [x] Add a small planner-safe loop:
  - observe
  - classify mode
  - choose action
  - execute
  - verify result
- [x] Add trace logging for every observation and action
- [x] Add an external agent-runner loop against the runtime API
- [x] Add a Codex app-server mode for the external agent runner

Exit criteria:

- the runtime can reliably open and close the main menu
- the runtime can advance dialogue with `A`
- the runtime can move one tile at a time in the overworld

Validation:

- run a scripted sequence:
  - `Start`
  - `B`
  - `Right`
  - `A`
- confirm telemetry and framebuffer reflect each step


## Phase 5: Custom UI Shell

Status: `DONE`

Objective:

Build a local custom interface around the main GBC render and telemetry output.

Tasks:

- [x] Create a minimal UI app
- [x] Display the live framebuffer
- [x] Display live telemetry panels:
  - map id
  - player coordinates
  - mode
  - battle state
  - current menu item
  - input state
  - decoded dialogue
- [x] Add manual controls in the UI
- [x] Add connection health and frame rate display
- [x] Add UI controls and status panels for the Codex agent loop

Suggested layout:

- center: live game screen
- right rail: telemetry, dialogue, mode, controls
- bottom rail: event log / action log

Exit criteria:

- the runtime and UI can run together locally
- the UI updates with live telemetry while playing
- manual button presses in the UI affect the running game

Validation:

- open UI
- move player using UI controls
- confirm framebuffer and telemetry stay in sync


## Phase 6: Minimap And Derived Overlays

Status: `NOT STARTED`

Objective:

Use telemetry plus map data to render additional game-aware UI such as a
minimap and derived overlays.

Tasks:

- [ ] Parse map metadata from the disassembly
- [ ] Build a map lookup from `wCurMap` to named map data
- [ ] Render a basic minimap for the current map
- [ ] Draw player position on the minimap
- [ ] Add optional overlays for:
  - collision hints
  - warp tiles
  - NPC positions when available
  - encounter/battle metadata

Exit criteria:

- minimap renders for at least one map correctly
- player marker updates while moving
- minimap remains synchronized with map transitions

Validation:

- move around one map
- cross a map boundary
- confirm minimap and player marker update correctly


## Cross-Cutting Requirements

These are mandatory across all phases.

- [ ] Keep the ROM build reproducible from this repo
- [ ] Do not break `./play`
- [ ] Prefer read-only instrumentation first
- [ ] Store telemetry traces in a predictable local directory
- [ ] Keep the runtime deterministic enough for scripted tests
- [ ] Separate raw emulator reads from interpreted game state


## API Direction

This is the current preferred interface boundary.

### Runtime service

- process-local Python service
- exposes:
  - `GET /frame`
  - `GET /telemetry`
  - `POST /action`
  - `GET /health`
  - optional WebSocket stream for frame + telemetry updates

### UI client

- subscribes to frame and telemetry
- sends button actions
- renders overlays independently of emulator internals


## Risks And Mitigations

### Risk: emulator API does not expose enough RAM/frame control

Mitigation:

- validate emulator bindings first before building the UI
- if PyBoy is too limiting, replace the runtime while keeping the telemetry API

### Risk: text decoding from `wTileMap` is incomplete

Mitigation:

- start with tile rows and basic dialogue detection
- use OCR only as a fallback, not as the primary path

### Risk: minimap becomes a data-parsing project too early

Mitigation:

- keep minimap out of the critical path until runtime + telemetry are stable

### Risk: timing issues make agent input unreliable

Mitigation:

- gate actions on stable frames and explicit mode detection
- log every action and post-action telemetry delta


## Decisions To Lock Early

- [x] Runtime backend: `PyBoy` first unless blocked by missing APIs
- [ ] Telemetry transport: HTTP + optional WebSocket
- [ ] UI stack: lightweight web UI first, not a native macOS app
- [ ] Save path convention for runtime-managed sessions
- [ ] Trace output directory for telemetry and action logs


## Execution Order

This is the recommended implementation order.

1. Phase 1: programmable runtime skeleton
2. Phase 2: symbol and RAM telemetry
3. Phase 3: text and UI decoding
4. Phase 4: agent control loop
5. Phase 5: custom UI shell
6. Phase 6: minimap and overlays

Do not start the UI before phases 1 and 2 are stable.
Do not start minimap work before telemetry and map lookups are stable.


## Immediate Next Tasks

These are the first concrete tasks to execute next.

- [x] Create `tools/runtime/`
- [x] Add `requirements.txt` with the chosen emulator/runtime dependencies
- [x] Add a minimal `server.py` that can boot the ROM
- [x] Add symbol parsing for `.sym`
- [x] Add one telemetry endpoint returning:
  - frame number
  - map id
  - x/y
  - battle state
  - menu state

Next focus:

- [ ] add mode detection beyond the current heuristic
- [ ] add text/tilemap decoding beyond raw tile ids
- [x] add a small local client around `/frame` and `/telemetry`
- [ ] add richer mode detection beyond the current heuristic


## Progress Summary

Use this table as the top-level progress tracker during execution.

| Phase | Name | Status | Notes |
| --- | --- | --- | --- |
| 0 | Working foundations | Done | ROM build and manual launch working |
| 1 | Programmable runtime skeleton | Done | Runtime boots ROM via PyBoy, supports auto-run, and exposes HTTP endpoints |
| 2 | Symbol and RAM telemetry | In progress | `.sym` parser and basic telemetry endpoint implemented |
| 3 | Text and UI decoding | Not started | `wTileMap` decoding not implemented |
| 4 | Agent control loop | Not started | No programmable action layer yet |
| 5 | Custom UI shell | In progress | Minimal browser shell served from the runtime |
| 6 | Minimap and overlays | Not started | Blocked on telemetry stability |
