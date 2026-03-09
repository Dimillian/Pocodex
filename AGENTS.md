# Repo Instructions

This repository is still the `pret/pokered` disassembly, but this fork also
contains a local programmable runtime, telemetry service, and Codex-driven UI
for playing and instrumenting the game.

## Source Of Truth

- Original game/disassembly logic:
  - `audio/`
  - `constants/`
  - `data/`
  - `engine/`
  - `home/`
  - `maps/`
  - `ram/`
- Runtime and telemetry implementation:
  - `tools/runtime/`
- Manual play launcher:
  - `play`
  - `PLAYING.md`
- Programmable runtime launcher:
  - `runtime`
  - `tools/runtime/README.md`
- Execution plan and progress tracking:
  - `TELEMETRY_RUNTIME_PLAN.md`

## Current Runtime Architecture

- `tools/runtime/runtime_app.py`
  Composition root for the programmable runtime used by the HTTP server.
- `tools/runtime/runtime_core.py`
  Owns ROM/symbol validation, PyBoy boot, run-loop lifecycle, frame caching,
  and raw save/load state bytes.
- `tools/runtime/action_executor.py`
  Owns validated button execution, settle logic, save/load helpers, and
  deterministic routines.
- `tools/runtime/objective_runner.py`
  Owns planner-step execution plus objective, target, and interaction macros.
- `tools/runtime/affordance_builder.py`
  Builds and annotates nearby world affordances, including reachability,
  semantic labels, and interaction hints.
- `tools/runtime/objective_memory.py`
  Owns inferred-objective memory, progress tracking, invalidation, and
  interaction-resolution bookkeeping.
- `tools/runtime/objective_scoring.py`
  Turns affordances plus runtime memory into ranked candidate objectives and
  active-objective state.
- `tools/runtime/objective_queries.py`
  Resolves and reconstructs objective ids against the current snapshot.
- `tools/runtime/objective_primitives.py`
  Holds shared objective ids, labels, phase helpers, and distance logic used by
  the scoring and query layers.
- `tools/runtime/snapshot_service.py`
  Builds telemetry, snapshot bundles, event logs, and agent context payloads.
- `tools/runtime/trace_recorder.py`
  Verifies action outcomes and writes JSONL traces.
- `tools/runtime/telemetry.py`
  Reads symbol-backed RAM state and produces structured telemetry.
- `tools/runtime/agent_context.py`
  Distills telemetry into the compact action-selection context used by Codex.
- `tools/runtime/codex_client.py`
  Talks to `codex app-server` over stdio JSON-RPC.
- `tools/runtime/agent_service.py`
  Runs the built-in UI agent loop and reports live state to the web UI.
- `tools/runtime/static/`
  Browser UI for the framebuffer, telemetry, controls, and agent status.

## Current Goal

The next main workstream is overworld navigation and map-aware telemetry.

That means:

- parse map metadata from the disassembly
- expose map names, warps, exits, and movement-facing state
- continue tightening navigator/world-policy boundaries around the composed
  runtime modules instead of growing orchestration files again
- make Codex capable of leaving the intro path and navigating intentionally

Before adding more UI polish or transport changes, prefer work that improves
the agent's ability to understand and traverse the world.

## Editing Rules

- Preserve the upstream disassembly structure. Do not casually move or rename
  the original assembly/data files.
- Keep fork-specific runtime work inside `tools/runtime/` and top-level helper
  scripts/docs unless a game-data change is explicitly required.
- Do not modify ROM logic for convenience if the same result can be achieved
  from runtime telemetry or disassembly parsing.
- When changing telemetry or navigation behavior, update
  `TELEMETRY_RUNTIME_PLAN.md` if the phase status or next steps changed.
- When changing the top-level workflow, keep `README.md`,
  `PLAYING.md`, and `tools/runtime/README.md` aligned.

## Validation

Use the lightest relevant validation for the change:

- Build ROMs:
  - `make -j4`
- Manual launch:
  - `./play`
- Runtime launch:
  - `./runtime --paused --no-open`
- Python/runtime syntax:
  - `source .venv-runtime-uv/bin/activate && python -m compileall tools/runtime`
- Static UI syntax:
  - `node --check tools/runtime/static/app.js`

For runtime/API work, prefer validating with concrete endpoint calls, for
example:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/telemetry | jq
curl http://127.0.0.1:8765/agent/status | jq
```

## Practical Notes

- The runtime uses local state under `.runtime-state/` and traces under
  `.runtime-traces/`.
- The built-in web UI should remain usable for both manual play and watching
  the Codex agent.
- If you add new telemetry fields meant for the agent, expose them in both
  `/telemetry` and `/agent_context` when useful.
