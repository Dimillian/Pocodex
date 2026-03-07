const frameEl = document.getElementById("game-frame");
const statusPillEl = document.getElementById("status-pill");
const frameLabelEl = document.getElementById("frame-label");
const fpsLabelEl = document.getElementById("fps-label");
const statusBlockEl = document.getElementById("status-block");
const mapBlockEl = document.getElementById("map-block");
const battleBlockEl = document.getElementById("battle-block");
const inputBlockEl = document.getElementById("input-block");
const menuBlockEl = document.getElementById("menu-block");
const dialogueBlockEl = document.getElementById("dialogue-block");
const eventsBlockEl = document.getElementById("events-block");
const statesBlockEl = document.getElementById("states-block");
const tracesBlockEl = document.getElementById("traces-block");
const agentContextBlockEl = document.getElementById("agent-context-block");
const agentStatusBlockEl = document.getElementById("agent-status-block");
const agentLogBlockEl = document.getElementById("agent-log-block");
const decodedRowsBlockEl = document.getElementById("decoded-rows-block");
const tilemapBlockEl = document.getElementById("tilemap-block");
const stateSlotLabelEl = document.getElementById("state-slot-label");
const agentStateLabelEl = document.getElementById("agent-state-label");
const agentFreshThreadToggleEl = document.getElementById("agent-fresh-thread-toggle");

const buttonMap = {
  ArrowUp: "up",
  ArrowDown: "down",
  ArrowLeft: "left",
  ArrowRight: "right",
  z: "a",
  x: "b",
  Enter: "start",
  Shift: "select",
};

const REFRESH_INTERVAL_MS = 30;

let lastFrame = null;
let lastRefreshAt = null;
let refreshInFlight = false;
let refreshQueued = false;

async function fetchJson(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body);
  }
  return response.json();
}

async function press(button) {
  await fetchJson("/action", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ button }),
  });
  await refresh();
}

async function stepFrames(frames) {
  await fetchJson("/tick", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ frames }),
  });
  await refresh();
}

async function setRunning(path) {
  await fetchJson(path, { method: "POST" });
  await refresh();
}

async function stateAction(path, slot = "quick") {
  await fetchJson(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ slot }),
  });
  await refresh();
}

async function runRoutine(name) {
  await fetchJson("/routine", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name }),
  });
  await refresh();
}

async function plannerStep(goal = "progress") {
  await fetchJson("/planner_step", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ goal }),
  });
  await refresh();
}

async function startAgent(mode = "codex") {
  await fetchJson("/agent/start", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      mode,
      step_delay_ms: 100,
      fresh_thread: mode === "codex" ? agentFreshThreadToggleEl.checked : false,
    }),
  });
  await refresh();
}

async function stopAgent() {
  await fetchJson("/agent/stop", { method: "POST" });
  await refresh();
}

function updateFrame(framePngBase64) {
  if (!framePngBase64) {
    return;
  }
  frameEl.src = `data:image/png;base64,${framePngBase64}`;
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function formatList(items, emptyLabel = "none") {
  return items.length ? items.join("\n") : emptyLabel;
}

async function refresh() {
  if (refreshInFlight) {
    refreshQueued = true;
    return;
  }
  refreshInFlight = true;

  try {
  const [health, snapshot, states, traces, agentContext, agentStatus] = await Promise.all([
    fetchJson("/health"),
    fetchJson("/snapshot"),
    fetchJson("/states"),
    fetchJson("/traces?limit=10"),
    fetchJson("/agent_context"),
    fetchJson("/agent/status"),
  ]);
  const telemetry = snapshot.telemetry;
  const now = performance.now();

  statusPillEl.textContent = health.running ? "running" : "paused";
  statusPillEl.dataset.running = health.running ? "true" : "false";
  frameLabelEl.textContent = `frame ${telemetry.frame}`;
  if (
    health.running
    && lastFrame !== null
    && lastRefreshAt !== null
    && telemetry.frame >= lastFrame
  ) {
    const elapsedMs = now - lastRefreshAt;
    const frameDelta = telemetry.frame - lastFrame;
    const fps = elapsedMs > 0 ? (frameDelta * 1000) / elapsedMs : 0;
    fpsLabelEl.textContent = `${fps.toFixed(1)} fps`;
  } else {
    fpsLabelEl.textContent = health.running ? "..." : "0.0 fps";
  }
  lastFrame = telemetry.frame;
  lastRefreshAt = now;

  statusBlockEl.textContent = [
    `mode           ${telemetry.mode}`,
    `runtime        ${health.running ? "running" : "paused"}`,
    `message box    ${telemetry.screen.message_box_present ? "visible" : "hidden"}`,
    `blank ratio    ${telemetry.screen.blank_ratio.toFixed(3)}`,
  ].join("\n");
  mapBlockEl.textContent = [
    `name           ${telemetry.map.name ?? `Map ${telemetry.map.id}`}`,
    `const          ${telemetry.map.const_name ?? "unknown"}`,
    `map id         ${telemetry.map.id}`,
    `script         ${telemetry.map.script}`,
    `x              ${telemetry.map.x}`,
    `y              ${telemetry.map.y}`,
    `size           ${telemetry.map.width} x ${telemetry.map.height}`,
    "",
    `objective      ${telemetry.navigation?.objective?.label ?? "none"}`,
    `last result    ${telemetry.navigation?.last_result?.kind ?? "none"}`,
    `facing         ${telemetry.movement?.facing ?? "unknown"}`,
  ].join("\n");
  battleBlockEl.textContent = [
    `in battle      ${telemetry.battle.in_battle}`,
    `battle type    ${telemetry.battle.type}`,
    `opponent       ${telemetry.battle.opponent}`,
  ].join("\n");
  inputBlockEl.textContent = [
    `held           ${telemetry.input.held}`,
    `pressed        ${telemetry.input.pressed}`,
    `released       ${telemetry.input.released}`,
    `joy input      ${telemetry.input.input}`,
  ].join("\n");
  menuBlockEl.textContent = [
    `active         ${telemetry.menu.active}`,
    `selected       ${telemetry.menu.selected_item_text ?? "none"}`,
    `selected idx   ${telemetry.menu.selected_index ?? "-"}`,
    "",
    "items",
    formatList(
      telemetry.menu.visible_items.map((item, index) => {
        const marker = telemetry.menu.selected_index === index ? ">" : " ";
        return `${marker} ${item}`;
      }),
      "none",
    ),
  ].join("\n");
  dialogueBlockEl.textContent = telemetry.dialogue.visible_lines.length
    ? telemetry.dialogue.visible_lines.join("\n")
    : telemetry.dialogue.active
      ? "Dialogue box visible (text buffer not decoded for this frame)"
      : "No visible dialogue";
  eventsBlockEl.textContent = telemetry.events.recent
    .slice(-10)
    .map((event) => `[${event.frame}] ${event.label}`)
    .join("\n");
  stateSlotLabelEl.textContent = "slot: quick";
  statesBlockEl.textContent = states.states.length
    ? states.states
        .map((state) => {
          const savedFrame = state.metadata?.saved_frame ?? "?";
          const savedMode = state.metadata?.saved_mode ?? "unknown";
          return `${state.slot} (${savedMode} @ frame ${savedFrame})`;
        })
        .join("\n")
    : "No save states yet";
  tracesBlockEl.textContent = traces.traces.length
    ? traces.traces
        .slice(-8)
        .map((trace) => {
          const action = trace.payload?.button ?? trace.action_id ?? trace.kind;
          const outcome = trace.verification?.passed;
          const afterMode = trace.after?.mode ?? "?";
          const label = outcome === undefined ? "info" : outcome ? "ok" : "check";
          return `${action} -> ${afterMode} [${label}]`;
        })
        .join("\n")
    : "No traces yet";
  agentContextBlockEl.textContent = [
    `objective      ${agentContext.observation.navigation?.objective?.label ?? "none"}`,
    `last result    ${agentContext.observation.navigation?.last_result?.kind ?? "none"}`,
    `facing         ${agentContext.observation.movement?.facing ?? "unknown"}`,
    "",
    "warps",
    formatList(
      (agentContext.observation.map.warps ?? []).map(
        (warp) => `(${warp.x}, ${warp.y}) -> ${warp.target_name}`,
      ),
    ),
    "",
    "objects",
    formatList(
      (agentContext.observation.map.objects ?? []).map(
        (object) => `${object.sprite} @ (${object.x}, ${object.y})`,
      ),
    ),
    "",
    `heuristic      ${agentContext.heuristic_next_action.action}`,
    `reason         ${agentContext.heuristic_next_action.reason}`,
    "",
    "allowed actions",
    formatList(agentContext.allowed_actions.map((action) => `- ${action.id}`)),
    "",
    "planner state",
    pretty(agentContext.planner_state),
  ].join("\n");
  agentStateLabelEl.textContent = `agent: ${agentStatus.state}`;
  agentStatusBlockEl.textContent = [
    `running        ${agentStatus.running}`,
    `state          ${agentStatus.state}`,
    `mode           ${agentStatus.mode ?? "none"}`,
    `fresh thread   ${agentStatus.fresh_thread ?? false}`,
    `step count     ${agentStatus.step_count}`,
    `current        ${agentStatus.current_action ?? "idle"}`,
    `thread         ${agentStatus.thread_id ?? "none"}`,
    `turn           ${agentStatus.turn_id ?? "none"}`,
    "",
    `last action    ${agentStatus.last_decision?.action ?? "none"}`,
    `reason         ${agentStatus.last_decision?.reason ?? "none"}`,
    `result mode    ${agentStatus.last_result?.mode ?? "none"}`,
    agentStatus.last_error ? "" : null,
    agentStatus.last_error ? `error          ${agentStatus.last_error}` : null,
  ].filter(Boolean).join("\n");
  agentLogBlockEl.textContent = agentStatus.recent_logs.length
    ? agentStatus.recent_logs
        .slice(-10)
        .map((entry) => {
          if (entry.kind === "agent_controller_step") {
            const action = entry.decision?.action ?? "?";
            const mode = entry.result?.mode ?? "?";
            return `[${entry.step}] ${action} -> ${mode}\n${entry.decision?.reason ?? ""}`;
          }
          if (entry.kind === "agent_controller_error") {
            return `ERROR: ${entry.message}`;
          }
          if (entry.kind === "agent_controller_started") {
            return `START ${entry.mode}`;
          }
          if (entry.kind === "agent_controller_stop_requested") {
            return "STOP requested";
          }
          return entry.kind;
        })
        .join("\n\n")
    : "No agent activity yet";
  decodedRowsBlockEl.textContent = telemetry.screen.decoded_rows.join("\n");
  tilemapBlockEl.textContent = telemetry.screen.tilemap_rows_hex.join("\n");

  updateFrame(snapshot.frame_png_base64);
  } finally {
    refreshInFlight = false;
    if (refreshQueued) {
      refreshQueued = false;
      queueMicrotask(() => {
        refresh().catch((error) => console.error(error));
      });
    }
  }
}

document.querySelectorAll("[data-button]").forEach((buttonEl) => {
  buttonEl.addEventListener("click", () => press(buttonEl.dataset.button));
});

document.getElementById("resume-btn").addEventListener("click", () => setRunning("/resume"));
document.getElementById("pause-btn").addEventListener("click", () => setRunning("/pause"));
document.getElementById("tick-btn").addEventListener("click", () => stepFrames(1));
document.getElementById("tick-60-btn").addEventListener("click", () => stepFrames(60));
document.getElementById("save-state-btn").addEventListener("click", () => stateAction("/save_state"));
document.getElementById("load-state-btn").addEventListener("click", () => stateAction("/load_state"));
document.getElementById("planner-step-btn").addEventListener("click", () => plannerStep());
document.getElementById("agent-start-btn").addEventListener("click", () => startAgent("codex"));
document.getElementById("agent-stop-btn").addEventListener("click", () => stopAgent());
document.getElementById("agent-start-heuristic-btn").addEventListener("click", () => startAgent("heuristic"));
document.querySelectorAll("[data-routine]").forEach((buttonEl) => {
  buttonEl.addEventListener("click", () => runRoutine(buttonEl.dataset.routine));
});

window.addEventListener("keydown", async (event) => {
  const button = buttonMap[event.key];
  if (!button || event.repeat) {
    return;
  }
  event.preventDefault();
  await press(button);
});

setInterval(() => {
  refresh().catch((error) => console.error(error));
}, REFRESH_INTERVAL_MS);

refresh().catch((error) => console.error(error));
