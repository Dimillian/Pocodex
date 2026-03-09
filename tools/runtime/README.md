# Runtime Service

This package boots a `pokered` ROM through PyBoy, exposes a small HTTP API, and
returns symbol-aware telemetry for future UI work.

## Setup

Recommended environment:

```bash
cd /Users/dimillian/Documents/Dev/pokered
uv venv --python 3.13 .venv-runtime-uv
source .venv-runtime-uv/bin/activate
uv pip install -r tools/runtime/requirements.txt
```

Build a ROM first if needed:

```bash
make blue
```

## Run

Primary entrypoint:

```bash
./runtime --rom blue --port 8765 --auto-run --boot-frames 600
./runtime --rom blue --port 8765
./runtime red --paused --no-open
```

The launcher script:

- checks that the ROM exists and builds it if needed
- checks that the runtime virtualenv exists
- starts the server
- polls `/health` until the service is actually ready
- prints the UI and health URLs in the terminal
- stops the server cleanly on `Ctrl-C`

Direct Python entrypoint:

```bash
source .venv-runtime-uv/bin/activate
python -m tools.runtime.server --rom blue --port 8765
python -m tools.runtime.server --rom blue --port 8765 --auto-run --boot-frames 600
```

Then open:

```text
http://127.0.0.1:8765/
```

Available ROM values:

- `blue`
- `red`
- `blue-debug`

## Internal Architecture

The public runtime entrypoint is `tools/runtime/runtime_app.py`. It composes a
small set of focused modules:

- `runtime_core.py` for emulator lifecycle, frame reads, and raw save/load
- `runtime_memory.py` for mutable shared runtime state
- `snapshot_service.py` for telemetry, snapshots, event logs, and agent context
- `trace_recorder.py` for action verification and JSONL traces
- `interaction_policy.py` for menu/dialogue/battle/planner decision helpers
- `action_executor.py` for validated input execution and deterministic routines
- `objective_runner.py` for planner-step and objective-following macros
- `affordance_builder.py` for building nearby world affordances and their
  reachability/semantic annotations
- `objective_memory.py` for inferred-objective memory, progress tracking, and
  invalidation bookkeeping
- `objective_scoring.py` for ranking candidate objectives from affordances plus
  runtime memory
- `objective_queries.py` for resolving and reconstructing objective ids from
  the current snapshot
- `objective_primitives.py` for shared objective ids, labels, phases, and
  distance helpers

## Endpoints

- `GET /health`
- `GET /snapshot`
- `GET /telemetry`
- `GET /agent_context`
- `GET /agent/status`
- `GET /agent/models`
- `POST /agent/start`
- `POST /agent/stop`
- `POST /agent/prompt`
- `POST /agent/prompt/clear`
- `POST /execute_action`
- `GET /frame`
- `POST /tick`
- `POST /action`
- `POST /pause`
- `POST /resume`
- `GET /states`
- `POST /save_state`
- `POST /load_state`
- `GET /traces`
- `POST /sequence`
- `POST /routine`
- `POST /planner_step`

`GET /telemetry` currently includes:

- symbol-aware map, battle, input, and menu state
- party members with current HP, max HP, status, and nicknames
- bag inventory with item names and quantities
- trainer economy/progression state with money and badges
- named event-flag progress summaries and the current map-script pointer
- decoded screen rows from `wTileMap`
- extracted dialogue text from the standard bottom message box
- validated menu extraction that only activates when a visible cursor is on screen
- a rolling event log for mode, map, menu, dialogue, and battle transitions
- semantic affordances and inferred objective memory inside `navigation`

`GET /snapshot` returns telemetry plus a base64-encoded PNG from the same locked
runtime read, which keeps the UI frame and the agent-visible state in sync.

`GET /agent_context` currently includes:

- `world_state`, a compact strategic view of the current snapshot
- `affordances`, semantic nearby exits / entities / triggers / inspectables
- richer progress state, including key event flags and compact milestone summaries
- `objective_state`, including:
  - active objective
  - candidate objectives with confidence and evidence
  - recent objective progress and invalidations
  - recent map history
  - progress and loop signals
- structured model input built from those fields
- recent events and recent action traces
- decision state
- allowed next actions for the current mode
- a heuristic next-action hint
- a compact `model_input` payload for the external LLM decision step
- a short fixed prompt string; the detailed runtime state now lives in `model_input`

`POST /execute_action` accepts one validated action id chosen from the current
`allowed_actions` list in `/agent_context` and executes it through the runtime.
Field-mode objective windows can include `objective_id`; tactical target
overrides can include `affordance_id`.

`GET /agent/status` returns the live state of the built-in UI agent controller,
including whether it is running, the current Codex thread/turn ids, the last
decision, the last execution result, whether it started from a fresh Codex
thread, current model/provider/reasoning-effort metadata, latest token usage
and context-window telemetry reported by app-server, pending-turn diagnostics
(including requested model/effort before the first step completes), recent
app-server stderr/event context for failed turns, queued web-UI prompt
overrides, and recent controller logs.

`GET /agent/models` returns the current app-server model catalog used by the
web UI model picker, including each model's display name and supported
reasoning-effort options.

`POST /agent/prompt` queues a free-form operator note that will be injected
into the next Codex decision turn started by the built-in agent. Queued notes
are cleared automatically after that turn begins. `POST /agent/prompt/clear`
removes any queued note without sending it.

The control layer currently includes:

- `POST /action` for a single validated tap
- `POST /sequence` for multi-step scripted input
- `POST /routine` for common high-level actions such as `open_menu`,
  `close_menu`, `advance_dialogue`, and one-tile movement routines
- `POST /planner_step` for a deterministic observe-decide-act-verify step
- `GET /traces` for recent JSONL action traces

Examples:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/snapshot | jq '.telemetry'
curl http://127.0.0.1:8765/telemetry | jq
curl http://127.0.0.1:8765/agent_context | jq
curl http://127.0.0.1:8765/agent/status | jq
curl -X POST http://127.0.0.1:8765/agent/start -H 'content-type: application/json' -d '{"mode":"codex","step_delay_ms":100,"fresh_thread":true}'
curl -X POST http://127.0.0.1:8765/agent/stop
curl -X POST http://127.0.0.1:8765/agent/prompt -H 'content-type: application/json' -d '{"prompt":"Prioritize leaving the house and interact with any visible sign first."}'
curl -X POST http://127.0.0.1:8765/agent/prompt/clear
curl -X POST http://127.0.0.1:8765/execute_action -H 'content-type: application/json' -d '{"action":"press_start","reason":"open the title menu"}'
curl -X POST http://127.0.0.1:8765/execute_action -H 'content-type: application/json' -d '{"action":"follow_objective","reason":"bind to the top inferred objective","objective_id":"reach_exit:warp:0"}'
curl -o frame.png http://127.0.0.1:8765/frame
curl -X POST http://127.0.0.1:8765/tick -H 'content-type: application/json' -d '{"frames": 60}'
curl -X POST http://127.0.0.1:8765/action -H 'content-type: application/json' -d '{"button": "start"}'
curl -X POST http://127.0.0.1:8765/pause
curl -X POST http://127.0.0.1:8765/resume
curl http://127.0.0.1:8765/states | jq
curl -X POST http://127.0.0.1:8765/save_state -H 'content-type: application/json' -d '{"slot": "quick"}'
curl -X POST http://127.0.0.1:8765/load_state -H 'content-type: application/json' -d '{"slot": "quick"}'
curl -X POST http://127.0.0.1:8765/routine -H 'content-type: application/json' -d '{"name": "open_menu"}'
curl -X POST http://127.0.0.1:8765/sequence -H 'content-type: application/json' -d '{"steps":[{"button":"start","settle_frames":120},{"button":"a","settle_frames":240}]}'
curl -X POST http://127.0.0.1:8765/planner_step -H 'content-type: application/json' -d '{"goal":"progress"}'
curl 'http://127.0.0.1:8765/traces?limit=10' | jq
```

Save states are stored locally under `.runtime-state/<rom>/`.
This is separate from in-game save RAM and is intended for fast runtime
checkpointing while testing or driving the game programmatically.

Action traces are appended to `.runtime-traces/<rom>/actions.jsonl`.
Each trace records the action payload, compact before/after state, and
verification checks showing whether the expected state transition occurred.

## External Agent Runner

Use the external runner when you want a controller process outside the runtime:

```bash
./agent-runner --mode codex --steps 3
./agent-runner --steps 3
./agent-runner --resume-thread --steps 3
./agent-runner --dry-run --steps 1
./agent-runner --print-prompt
```

It currently:

- fetches `/agent_context`
- can choose one action from `allowed_actions` in two modes:
  - `--mode codex` uses `codex app-server` over stdio and keeps a persisted
    Codex thread in `.runtime-traces/agent-runner/codex-thread.json`
    but starts with a fresh thread by default unless `--resume-thread` is set
  - `--mode heuristic` uses the current heuristic hint directly
- executes that action through `/execute_action`, including optional
  `objective_id` for objective windows or `affordance_id` for tactical overrides
- logs each step to `.runtime-traces/agent-runner/steps.jsonl`

This keeps the runtime focused on execution while the controller owns the
decision loop.

## Current UI

The root page serves a minimal browser shell with:

- live framebuffer
- pause/resume and step controls
- start/stop controls for the built-in Codex agent
- model and reasoning selectors for Codex starts
- fresh-thread toggle for Codex agent starts
- queued free-form prompt input for the next Codex turn
- quick save/load state controls
- planner-step control for deterministic progression
- routine buttons for common gameplay actions
- button controls
- keyboard input
- map, battle, menu, dialogue, and input telemetry
- trainer state telemetry for party, bag, money, and badges
- recent event log
- recent action traces
- compact agent context view
- live agent status with current model/reasoning and token/context telemetry
- recent agent-step log
- decoded screen rows
- raw tilemap rows in hex

This is intentionally minimal. It is the first integration layer for the
future custom UI, not the final interface.
