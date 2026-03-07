const frameEl = document.getElementById("game-frame");
const statusPillEl = document.getElementById("status-pill");
const frameLabelEl = document.getElementById("frame-label");
const fpsLabelEl = document.getElementById("fps-label");
const statusBlockEl = document.getElementById("status-block");
const mapBlockEl = document.getElementById("map-block");
const battleBlockEl = document.getElementById("battle-block");
const menuBlockEl = document.getElementById("menu-block");
const dialogueBlockEl = document.getElementById("dialogue-block");
const eventsBlockEl = document.getElementById("events-block");
const statesBlockEl = document.getElementById("states-block");
const tracesBlockEl = document.getElementById("traces-block");
const targetBlockEl = document.getElementById("target-block");
const interactionBlockEl = document.getElementById("interaction-block");
const affordancesBlockEl = document.getElementById("affordances-block");
const memoryBlockEl = document.getElementById("memory-block");
const decisionBlockEl = document.getElementById("decision-block");
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

async function runtimeAction(path) {
  await fetchJson(path, { method: "POST" });
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
  if (framePngBase64) {
    frameEl.src = `data:image/png;base64,${framePngBase64}`;
  }
}

function joinLines(lines, emptyLabel = "none") {
  return lines.filter(Boolean).join("\n") || emptyLabel;
}

function formatStateSummary(telemetry, running) {
  return [
    `mode           ${telemetry.mode}`,
    `interaction    ${telemetry.interaction?.type ?? "none"}`,
    `runtime        ${running ? "running" : "paused"}`,
    `message box    ${telemetry.screen.message_box_present ? "visible" : "hidden"}`,
    `blank ratio    ${telemetry.screen.blank_ratio.toFixed(3)}`,
  ].join("\n");
}

function formatWorldSnapshot(telemetry) {
  const navigation = telemetry.navigation || {};
  const movement = telemetry.movement || {};
  const map = telemetry.map || {};
  return [
    `name           ${map.name ?? `Map ${map.id}`}`,
    `const          ${map.const_name ?? "unknown"}`,
    `map id         ${map.id}`,
    `script         ${map.script}`,
    `coords         (${map.x}, ${map.y})`,
    `size           ${map.width} x ${map.height}`,
    "",
    `facing         ${movement.facing ?? "unknown"}`,
    `moving         ${movement.moving_direction ?? "none"}`,
    `last stop      ${movement.last_stop_direction ?? "unknown"}`,
    `last result    ${navigation.last_result?.kind ?? "none"}`,
    `failures       ${navigation.consecutive_failures ?? 0}`,
  ].join("\n");
}

function formatStatesAndInput(states, telemetry) {
  const stateLines = states.states.length
    ? states.states.map((state) => {
        const savedFrame = state.metadata?.saved_frame ?? "?";
        const savedMode = state.metadata?.saved_mode ?? "unknown";
        return `${state.slot.padEnd(12)} ${savedMode} @ ${savedFrame}`;
      })
    : ["none"];
  return [
    "save states",
    ...stateLines,
    "",
    `held           ${telemetry.input.held}`,
    `pressed        ${telemetry.input.pressed}`,
    `released       ${telemetry.input.released}`,
    `joy input      ${telemetry.input.input}`,
  ].join("\n");
}

function formatTargetBlock(telemetry) {
  const navigation = telemetry.navigation || {};
  const target = navigation.target_affordance;
  const objective = navigation.objective;
  const reasons = (target?.score_reasons || []).slice(0, 4);
  const base = [
    `target         ${target?.label ?? "none"}`,
    `kind           ${target?.kind ?? "none"}`,
    `id             ${target?.id ?? "none"}`,
    `source         ${navigation.target_source ?? "none"}`,
    `reason         ${navigation.target_reason ?? "none"}`,
    "",
    `fallback obj   ${objective?.label ?? "none"}`,
  ];
  if (reasons.length) {
    base.push("", "score reasons", ...reasons.map((reason) => `- ${reason}`));
  }
  return base.join("\n");
}

function formatInteractionBlock(telemetry) {
  const interaction = telemetry.interaction || {};
  const details = interaction.details || {};
  const naming = telemetry.naming || {};
  return [
    `type           ${interaction.type ?? "none"}`,
    `prompt         ${interaction.prompt ?? "none"}`,
    details.selected_command ? `command        ${details.selected_command}` : null,
    details.selected_move?.name ? `move           ${details.selected_move.name}` : null,
    details.selected_item_text ? `selected       ${details.selected_item_text}` : null,
    naming.active ? `naming         ${naming.screen_type ?? "unknown"} '${naming.current_text ?? ""}'` : null,
    naming.active && naming.cursor_row !== null ? `cursor         row ${naming.cursor_row} col ${naming.cursor_col}` : null,
  ].filter(Boolean).join("\n");
}

function formatAffordancesBlock(telemetry) {
  const ranked = telemetry.navigation?.ranked_affordances || [];
  if (!ranked.length) {
    return "No ranked affordances";
  }
  return ranked
    .slice(0, 8)
    .map((affordance, index) => {
      const reasons = (affordance.score_reasons || []).slice(0, 2).join(", ");
      return `${index + 1}. [${affordance.score}] ${affordance.id}\n   ${affordance.label}\n   ${reasons || affordance.kind}`;
    })
    .join("\n\n");
}

function formatMemoryBlock(telemetry) {
  const memory = telemetry.navigation?.memory || {};
  const top = memory.top_affordances || [];
  return [
    "visited maps",
    joinLines(memory.visited_maps || [], "none"),
    "",
    "recent targets",
    joinLines(memory.recent_targets || [], "none"),
    "",
    "recent progress",
    joinLines(memory.recent_progress || [], "none"),
    "",
    "top affordances",
    top.length
      ? top
          .slice(0, 6)
          .map((entry) => `${entry.affordance_id} p:${entry.progress_count} n:${entry.noop_count} b:${entry.blocked_count} last:${entry.last_outcome ?? "none"}`)
          .join("\n")
      : "none",
  ].join("\n");
}

function formatDecisionBlock(agentContext, agentStatus) {
  const decision = agentStatus.last_decision || {};
  const result = agentStatus.last_result || {};
  const heuristic = agentContext.heuristic_next_action || {};
  return [
    `agent state     ${agentStatus.state}`,
    `mode            ${agentStatus.mode ?? "none"}`,
    `fresh thread    ${agentStatus.fresh_thread ?? false}`,
    `steps           ${agentStatus.step_count}`,
    `thread          ${agentStatus.thread_id ?? "none"}`,
    "",
    `last action     ${decision.action ?? "none"}`,
    decision.affordance_id ? `affordance      ${decision.affordance_id}` : null,
    `reason          ${decision.reason ?? "none"}`,
    `result mode     ${result.mode ?? "none"}`,
    result.map?.const_name ? `result map      ${result.map.const_name}` : null,
    "",
    `heuristic       ${heuristic.action ?? "none"}`,
    `heuristic why   ${heuristic.reason ?? "none"}`,
    agentStatus.last_error ? "" : null,
    agentStatus.last_error ? `error           ${agentStatus.last_error}` : null,
  ].filter(Boolean).join("\n");
}

function formatDialogueBlock(telemetry) {
  if (telemetry.dialogue.visible_lines.length) {
    return telemetry.dialogue.visible_lines.join("\n");
  }
  if (telemetry.dialogue.active) {
    return "Dialogue box visible (text buffer not decoded for this frame)";
  }
  return "No visible dialogue";
}

function formatMenuBlock(telemetry) {
  const naming = telemetry.naming || {};
  const lines = [
    `menu active     ${telemetry.menu.active}`,
    `selected        ${telemetry.menu.selected_item_text ?? "none"}`,
    `selected idx    ${telemetry.menu.selected_index ?? "-"}`,
    "",
    "items",
    joinLines(
      telemetry.menu.visible_items.map((item, index) => {
        const marker = telemetry.menu.selected_index === index ? ">" : " ";
        return `${marker} ${item}`;
      }),
      "none",
    ),
  ];
  if (naming.active) {
    lines.push(
      "",
      "naming",
      `type           ${naming.screen_type ?? "unknown"}`,
      `prompt         ${naming.prompt ?? "none"}`,
      `current        ${naming.current_text ?? ""}`,
      `base           ${naming.base_name ?? ""}`,
      `submit         ${naming.submit_pending}`,
      `cursor         row ${naming.cursor_row ?? "-"} col ${naming.cursor_col ?? "-"}`,
    );
  }
  return lines.join("\n");
}

function formatBattleBlock(telemetry) {
  const battle = telemetry.battle || {};
  const commandMenu = battle.command_menu || {};
  const moveMenu = battle.move_menu || {};
  const player = battle.player || {};
  const enemy = battle.enemy || {};
  return [
    `in battle       ${battle.in_battle}`,
    `ui state        ${battle.ui_state ?? "none"}`,
    `opponent        ${battle.opponent}`,
    "",
    `player          ${player.nickname ?? "?"} lv${player.level ?? "?"} ${player.hp ?? "?"}/${player.max_hp ?? "?"}`,
    `enemy           ${enemy.nickname ?? "?"} lv${enemy.level ?? "?"} ${enemy.hp ?? "?"}/${enemy.max_hp ?? "?"}`,
    "",
    `command         ${commandMenu.selected_command ?? "none"}`,
    commandMenu.commands?.length ? `commands        ${commandMenu.commands.join(" / ")}` : null,
    moveMenu.selected_move?.name ? `move           ${moveMenu.selected_move.name} (${moveMenu.selected_move.pp}/${moveMenu.selected_move.max_pp ?? "?"} PP)` : null,
    moveMenu.moves?.length
      ? "moves\n" + moveMenu.moves.map((move, index) => {
          const marker = moveMenu.selected_index === index ? ">" : " ";
          return `${marker} ${move.name}  ${move.pp}/${move.max_pp ?? "?"}  ${move.type_name}`;
        }).join("\n")
      : null,
  ].filter(Boolean).join("\n");
}

function formatEventsBlock(telemetry) {
  return telemetry.events.recent
    .slice(-12)
    .map((event) => `[${event.frame}] ${event.label}`)
    .join("\n");
}

function formatTracesBlock(traces) {
  if (!traces.traces.length) {
    return "No traces yet";
  }
  return traces.traces
    .slice(-10)
    .map((trace) => {
      const action = trace.payload?.button ?? trace.action_id ?? trace.kind;
      const affordance = trace.affordance_id ? ` @ ${trace.affordance_id}` : "";
      const afterMode = trace.after?.mode ?? "?";
      const label = trace.verification?.passed === undefined ? "info" : trace.verification.passed ? "ok" : "check";
      return `${action}${affordance} -> ${afterMode} [${label}]`;
    })
    .join("\n");
}

function formatAgentLog(agentStatus) {
  if (!agentStatus.recent_logs.length) {
    return "No agent activity yet";
  }
  return agentStatus.recent_logs
    .slice(-10)
    .map((entry) => {
      if (entry.kind === "agent_controller_step") {
        const action = entry.decision?.action ?? "?";
        const affordance = entry.decision?.affordance_id ? ` @ ${entry.decision.affordance_id}` : "";
        const mode = entry.result?.mode ?? "?";
        return `[${entry.step}] ${action}${affordance} -> ${mode}\n${entry.decision?.reason ?? ""}`;
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
    .join("\n\n");
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
      fetchJson("/traces?limit=16"),
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

    statusBlockEl.textContent = formatStateSummary(telemetry, health.running);
    mapBlockEl.textContent = formatWorldSnapshot(telemetry);
    statesBlockEl.textContent = formatStatesAndInput(states, telemetry);
    targetBlockEl.textContent = formatTargetBlock(telemetry);
    interactionBlockEl.textContent = formatInteractionBlock(telemetry);
    affordancesBlockEl.textContent = formatAffordancesBlock(telemetry);
    memoryBlockEl.textContent = formatMemoryBlock(telemetry);
    decisionBlockEl.textContent = formatDecisionBlock(agentContext, agentStatus);
    dialogueBlockEl.textContent = formatDialogueBlock(telemetry);
    menuBlockEl.textContent = formatMenuBlock(telemetry);
    battleBlockEl.textContent = formatBattleBlock(telemetry);
    eventsBlockEl.textContent = formatEventsBlock(telemetry);
    tracesBlockEl.textContent = formatTracesBlock(traces);
    agentLogBlockEl.textContent = formatAgentLog(agentStatus);
    decodedRowsBlockEl.textContent = telemetry.screen.decoded_rows.join("\n");
    tilemapBlockEl.textContent = telemetry.screen.tilemap_rows_hex.join("\n");

    stateSlotLabelEl.textContent = "slot: quick";
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
    ].join("\n");

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
document.getElementById("reset-memory-btn").addEventListener("click", () => runtimeAction("/reset_runtime_memory"));
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
