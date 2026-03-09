const frameEl = document.getElementById("game-frame");
const statusPillEl = document.getElementById("status-pill");
const frameLabelEl = document.getElementById("frame-label");
const fpsLabelEl = document.getElementById("fps-label");
const statusBlockEl = document.getElementById("status-block");
const mapBlockEl = document.getElementById("map-block");
const battleBlockEl = document.getElementById("battle-block");
const trainerBlockEl = document.getElementById("trainer-block");
const menuBlockEl = document.getElementById("menu-block");
const dialogueBlockEl = document.getElementById("dialogue-block");
const eventsBlockEl = document.getElementById("events-block");
const statesBlockEl = document.getElementById("states-block");
const tracesBlockEl = document.getElementById("traces-block");
const targetBlockEl = document.getElementById("target-block");
const interactionBlockEl = document.getElementById("interaction-block");
const minimapPanelEl = document.getElementById("minimap-panel");
const minimapLegendBlockEl = document.getElementById("minimap-legend-block");
const minimapSizeLabelEl = document.getElementById("minimap-size-label");
const affordancesBlockEl = document.getElementById("affordances-block");
const memoryBlockEl = document.getElementById("memory-block");
const decisionBlockEl = document.getElementById("decision-block");
const allowedActionsBlockEl = document.getElementById("allowed-actions-block");
const decisionStateBlockEl = document.getElementById("decision-state-block");
const agentContextBlockEl = document.getElementById("agent-context-block");
const agentPromptBlockEl = document.getElementById("agent-prompt-block");
const agentStatusBlockEl = document.getElementById("agent-status-block");
const agentLogBlockEl = document.getElementById("agent-log-block");
const decodedRowsBlockEl = document.getElementById("decoded-rows-block");
const tilemapBlockEl = document.getElementById("tilemap-block");
const stateSlotLabelEl = document.getElementById("state-slot-label");
const agentStateLabelEl = document.getElementById("agent-state-label");
const agentConfigPanelEl = document.getElementById("agent-config-panel");
const agentModelSelectEl = document.getElementById("agent-model-select");
const agentReasoningSelectEl = document.getElementById("agent-reasoning-select");
const agentFreshThreadToggleEl = document.getElementById("agent-fresh-thread-toggle");
const agentPromptInputEl = document.getElementById("agent-prompt-input");
const agentQueuedPromptBlockEl = document.getElementById("agent-queued-prompt-block");
const agentLastPromptBlockEl = document.getElementById("agent-last-prompt-block");
const agentStatStateEl = document.getElementById("agent-stat-state");
const agentStatStateCaptionEl = document.getElementById("agent-stat-state-caption");
const agentStatModelEl = document.getElementById("agent-stat-model");
const agentStatModelCaptionEl = document.getElementById("agent-stat-model-caption");
const agentStatReasoningEl = document.getElementById("agent-stat-reasoning");
const agentStatReasoningCaptionEl = document.getElementById("agent-stat-reasoning-caption");
const agentStatThreadEl = document.getElementById("agent-stat-thread");
const agentStatThreadCaptionEl = document.getElementById("agent-stat-thread-caption");

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
let latestAgentStatus = null;
let agentModelCatalog = [];
let agentModelsLoaded = false;
let agentModelsRequest = null;
let agentModelsError = null;
let selectedAgentModel = "";
let selectedAgentReasoning = "";

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
      model: mode === "codex" ? (agentModelSelectEl.value || null) : null,
      reasoning_effort: mode === "codex" ? (agentReasoningSelectEl.value || null) : null,
    }),
  });
  await refresh();
}

async function stopAgent() {
  await fetchJson("/agent/stop", { method: "POST" });
  await refresh();
}

async function queueAgentPrompt() {
  const prompt = agentPromptInputEl.value.trim();
  if (!prompt) {
    return;
  }
  await fetchJson("/agent/prompt", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
  agentPromptInputEl.value = "";
  await refresh();
}

async function clearAgentPrompt() {
  await fetchJson("/agent/prompt/clear", { method: "POST" });
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

function formatJsonBlock(value, emptyLabel = "none") {
  if (value === null || value === undefined) {
    return emptyLabel;
  }
  if (typeof value === "string") {
    return value || emptyLabel;
  }
  if (Array.isArray(value) && value.length === 0) {
    return emptyLabel;
  }
  if (typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 0) {
    return emptyLabel;
  }
  return JSON.stringify(value, null, 2);
}

function formatTokenUsageInline(usage) {
  if (!usage) {
    return "none";
  }
  return `in ${usage.input_tokens ?? 0} out ${usage.output_tokens ?? 0} reason ${usage.reasoning_output_tokens ?? 0} cached ${usage.cached_input_tokens ?? 0} total ${usage.total_tokens ?? 0}`;
}

function formatInteger(value, emptyLabel = "none") {
  return typeof value === "number" ? value.toLocaleString() : emptyLabel;
}

function formatContextUsage(tokenUsage) {
  const inputTokens = tokenUsage?.last?.input_tokens;
  const contextWindow = tokenUsage?.model_context_window;
  if (
    typeof inputTokens !== "number"
    || typeof contextWindow !== "number"
    || contextWindow <= 0
  ) {
    return "unknown";
  }
  return `${inputTokens.toLocaleString()}/${contextWindow.toLocaleString()} (${((inputTokens / contextWindow) * 100).toFixed(1)}%)`;
}

function getDisplayedAgentModel(agentStatus) {
  return agentStatus.model || agentStatus.configured_model || "none";
}

function getDisplayedAgentReasoning(agentStatus) {
  return agentStatus.reasoning_effort || agentStatus.configured_reasoning_effort || "default";
}

function formatPreview(text, limit = 120) {
  if (!text) {
    return "none";
  }
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, limit - 1)}…`;
}

function shortId(value, prefixLength = 8) {
  if (!value) {
    return "none";
  }
  return value.length <= prefixLength ? value : value.slice(0, prefixLength);
}

function getDefaultModelChoice() {
  return agentModelCatalog.find((model) => model.isDefault) || agentModelCatalog[0] || null;
}

function getAgentModelDefinition(modelValue) {
  if (!modelValue) {
    return getDefaultModelChoice();
  }
  return agentModelCatalog.find((model) => model.model === modelValue) || null;
}

function replaceSelectOptions(selectEl, options, selectedValue) {
  selectEl.replaceChildren();
  for (const option of options) {
    const optionEl = document.createElement("option");
    optionEl.value = option.value;
    optionEl.textContent = option.label;
    optionEl.selected = option.value === selectedValue;
    selectEl.append(optionEl);
  }
}

function formatReasoningEffortLabel(effort) {
  if (!effort) {
    return "Default";
  }
  if (effort === "xhigh") {
    return "XHigh";
  }
  return effort.charAt(0).toUpperCase() + effort.slice(1);
}

function renderAgentReasoningOptions(agentStatus) {
  const modelDef = getAgentModelDefinition(selectedAgentModel);
  const supportedEfforts = modelDef?.supportedReasoningEfforts || [];
  if (!supportedEfforts.length) {
    selectedAgentReasoning = "";
    replaceSelectOptions(agentReasoningSelectEl, [{ value: "", label: "Not supported" }], "");
    agentReasoningSelectEl.disabled = true;
    return;
  }

  const preferredReasoning = (
    selectedAgentReasoning
    || agentStatus.configured_reasoning_effort
    || agentStatus.reasoning_effort
    || modelDef.defaultReasoningEffort
    || supportedEfforts[0]?.reasoningEffort
    || ""
  );
  const normalizedReasoning = supportedEfforts.some((effort) => effort.reasoningEffort === preferredReasoning)
    ? preferredReasoning
    : (modelDef.defaultReasoningEffort || supportedEfforts[0].reasoningEffort);
  selectedAgentReasoning = normalizedReasoning;
  agentReasoningSelectEl.disabled = false;
  replaceSelectOptions(
    agentReasoningSelectEl,
    supportedEfforts.map((effort) => ({
      value: effort.reasoningEffort,
      label: formatReasoningEffortLabel(effort.reasoningEffort),
    })),
    normalizedReasoning,
  );
}

function renderAgentConfig(agentStatus) {
  const showConfig = !agentStatus.running;
  agentConfigPanelEl.hidden = !showConfig;
  if (!showConfig) {
    return;
  }

  if (!agentModelsLoaded) {
    const loadingLabel = agentModelsError ? "Models unavailable" : "Loading models…";
    replaceSelectOptions(agentModelSelectEl, [{ value: "", label: loadingLabel }], "");
    replaceSelectOptions(agentReasoningSelectEl, [{ value: "", label: "Loading…" }], "");
    agentModelSelectEl.disabled = true;
    agentReasoningSelectEl.disabled = true;
    return;
  }

  const preferredModel = (
    selectedAgentModel
    || agentStatus.configured_model
    || agentStatus.model
    || getDefaultModelChoice()?.model
    || ""
  );
  const normalizedModel = getAgentModelDefinition(preferredModel)?.model || getDefaultModelChoice()?.model || "";
  selectedAgentModel = normalizedModel;

  replaceSelectOptions(
    agentModelSelectEl,
    agentModelCatalog.map((model) => ({
      value: model.model,
      label: model.displayName || model.model,
    })),
    normalizedModel,
  );
  agentModelSelectEl.disabled = false;
  renderAgentReasoningOptions(agentStatus);
}

async function ensureAgentModelsLoaded() {
  if (agentModelsLoaded) {
    return agentModelCatalog;
  }
  if (agentModelsRequest) {
    return agentModelsRequest;
  }
  agentModelsRequest = fetchJson("/agent/models")
    .then((payload) => {
      agentModelCatalog = (payload.data || []).filter((model) => model?.model);
      agentModelsLoaded = true;
      agentModelsError = null;
      if (!selectedAgentModel) {
        selectedAgentModel = getDefaultModelChoice()?.model || "";
      }
      if (latestAgentStatus) {
        renderAgentConfig(latestAgentStatus);
      }
      return agentModelCatalog;
    })
    .catch((error) => {
      agentModelsError = error;
      if (latestAgentStatus) {
        renderAgentConfig(latestAgentStatus);
      }
      throw error;
    })
    .finally(() => {
      agentModelsRequest = null;
    });
  return agentModelsRequest;
}

function agentElapsedSeconds(startedAt) {
  if (!startedAt) {
    return null;
  }
  const startedMs = Date.parse(startedAt);
  if (Number.isNaN(startedMs)) {
    return null;
  }
  return Math.max(0, (Date.now() - startedMs) / 1000);
}

function formatElapsedSeconds(seconds) {
  if (seconds === null || seconds === undefined) {
    return "unknown";
  }
  if (seconds < 10) {
    return `${seconds.toFixed(1)}s`;
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${remainder}s`;
}

function getAgentStartupInfo(agentStatus) {
  if (!agentStatus.running || agentStatus.current_action || (agentStatus.step_count ?? 0) > 0) {
    return null;
  }
  const elapsedSeconds = agentElapsedSeconds(agentStatus.started_at);
  let phase = "Starting";
  if (agentStatus.mode === "codex") {
    if (!agentStatus.thread_id) {
      phase = "Starting Codex";
    } else if (agentStatus.fresh_thread) {
      phase = "First turn on fresh thread";
    } else {
      phase = "Waiting for first turn";
    }
  } else if (agentStatus.mode === "heuristic") {
    phase = "First heuristic step";
  }
  return {
    phase,
    elapsedSeconds,
    elapsedLabel: formatElapsedSeconds(elapsedSeconds),
  };
}

function describeAgentState(agentStatus) {
  const startup = getAgentStartupInfo(agentStatus);
  if (agentStatus.running) {
    if (agentStatus.current_action) {
      return `Executing ${agentStatus.current_action}`;
    }
    if (startup) {
      return `${startup.phase} (${startup.elapsedLabel})`;
    }
    return "Loop active";
  }
  if (agentStatus.state === "error") {
    return "Stopped on error";
  }
  if (agentStatus.state === "completed") {
    return "Step limit reached";
  }
  if (agentStatus.pending_prompt) {
    return "Queued note ready";
  }
  return "Waiting";
}

function formatAgentSessionBlock(agentStatus) {
  const lastUsage = agentStatus.token_usage?.last;
  const contextWindow = agentStatus.token_usage?.model_context_window;
  const startup = getAgentStartupInfo(agentStatus);
  return [
    `Thread        ${shortId(agentStatus.thread_id, 12)}`,
    `Turn          ${shortId(agentStatus.turn_id, 12)}`,
    `Fresh thread  ${agentStatus.fresh_thread ? "yes" : "no"}`,
    startup ? `Startup       ${startup.phase} (${startup.elapsedLabel})` : null,
    "",
    `Context win   ${formatInteger(contextWindow, "unknown")}`,
    `Prompt load   ${formatContextUsage(agentStatus.token_usage)}`,
    `Cache reuse   ${formatInteger(lastUsage?.cached_input_tokens)}`,
    `Last usage    ${formatTokenUsageInline(agentStatus.token_usage?.last)}`,
    `Total usage   ${formatTokenUsageInline(agentStatus.token_usage?.total)}`,
  ].filter(Boolean).join("\n");
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
  const objectiveState = navigation.objective_state || {};
  const objective = objectiveState.active_objective || navigation.active_objective || navigation.objective;
  const candidates = objectiveState.candidate_objectives || navigation.candidate_objectives || [];
  const progressSignals = objectiveState.progress_signals || navigation.progress_signals || [];
  const loopSignals = objectiveState.loop_signals || navigation.loop_signals || [];
  const reasons = (target?.score_reasons || []).slice(0, 4);
  const base = [
    `active obj     ${objective?.label ?? "none"}`,
    `obj kind       ${objective?.kind ?? "none"}`,
    `obj id         ${objective?.id ?? "none"}`,
    `confidence     ${objective?.confidence ?? "none"}`,
    "",
    `target         ${target?.label ?? "none"}`,
    `target kind    ${target?.kind ?? "none"}`,
    `target id      ${target?.id ?? "none"}`,
    `source         ${navigation.target_source ?? "none"}`,
    `reason         ${navigation.target_reason ?? "none"}`,
  ];
  if (candidates.length) {
    base.push("", "candidate objectives", ...candidates.slice(0, 5).map((candidate) =>
      `- ${candidate.id} [${candidate.confidence ?? "?"}] ${candidate.kind}: ${candidate.label}`));
  }
  if (progressSignals.length) {
    base.push("", `progress       ${progressSignals.join(", ")}`);
  }
  if (loopSignals.length) {
    base.push(`loops          ${loopSignals.join(", ")}`);
  }
  if (reasons.length) {
    base.push("", "score reasons", ...reasons.map((reason) => `- ${reason}`));
  }
  return base.join("\n");
}

function formatInteractionBlock(telemetry) {
  const interaction = telemetry.interaction || {};
  const details = interaction.details || {};
  const naming = telemetry.naming || {};
  const pokedex = telemetry.pokedex || {};
  return [
    `type           ${interaction.type ?? "none"}`,
    `prompt         ${interaction.prompt ?? "none"}`,
    details.selected_command ? `command        ${details.selected_command}` : null,
    details.selected_move?.name ? `move           ${details.selected_move.name}` : null,
    details.selected_item_text ? `selected       ${details.selected_item_text}` : null,
    naming.active ? `naming         ${naming.screen_type ?? "unknown"} '${naming.current_text ?? ""}'` : null,
    naming.active && naming.cursor_row !== null ? `cursor         row ${naming.cursor_row} col ${naming.cursor_col}` : null,
    pokedex.active ? `species        ${pokedex.species_name ?? "unknown"}` : null,
    pokedex.active && pokedex.species_class ? `class          ${pokedex.species_class}` : null,
    pokedex.active && pokedex.height_weight ? `stats          ${pokedex.height_weight}` : null,
  ].filter(Boolean).join("\n");
}

function formatTrainerBlock(telemetry) {
  const party = telemetry.party || {};
  const inventory = telemetry.inventory || {};
  const trainer = telemetry.trainer || {};
  const badgeNames = (trainer.badges || [])
    .filter((badge) => badge.owned)
    .map((badge) => badge.name);
  const lines = [
    `money          ${trainer.money ?? 0}`,
    `money bcd      ${trainer.money_bcd ?? "000000"}`,
    `badges         ${trainer.badge_count ?? 0} (${badgeNames.join(", ") || "none"})`,
    "",
    "party",
    joinLines(
      (party.members || []).map((member) =>
        `${member.nickname || member.species_name || "?"} lv${member.level ?? "?"} ${member.hp ?? "?"}/${member.max_hp ?? "?"} ${member.status ?? "OK"}`,
      ),
      "none",
    ),
    "",
    `bag slots       ${inventory.count ?? 0}`,
    "inventory",
    joinLines(
      (inventory.items || []).map((item) => `${item.name || "?"} x${item.quantity ?? "?"}`),
      "none",
    ),
  ];
  return lines.join("\n");
}

function keyForPoint(x, y) {
  return `${x},${y}`;
}

function renderMinimap(telemetry) {
  const minimap = telemetry.navigation?.minimap;
  minimapPanelEl.replaceChildren();
  minimapPanelEl.classList.toggle("is-empty", !minimap);

  if (!minimap || !minimap.width || !minimap.height) {
    minimapPanelEl.textContent = "No minimap data for this screen";
    minimapLegendBlockEl.textContent = "legend\nNo map grid available";
    minimapSizeLabelEl.textContent = "0 x 0";
    return;
  }

  minimapSizeLabelEl.textContent = `${minimap.width} x ${minimap.height}`;

  const gridEl = document.createElement("div");
  gridEl.className = "minimap-grid";
  gridEl.style.setProperty("--minimap-width", String(minimap.width));

  const walkableGrid = minimap.walkable_grid || [];
  const blocked = new Set((minimap.blocked_positions || []).map((point) => keyForPoint(point.x, point.y)));
  const targetTiles = new Set((minimap.target_tiles || []).map((point) => keyForPoint(point.x, point.y)));
  const pathTiles = new Set((minimap.path_tiles || []).map((point) => keyForPoint(point.x, point.y)));
  const playerKey = minimap.player ? keyForPoint(minimap.player.x, minimap.player.y) : null;
  const visitedMaps = new Set(minimap.visited_maps || []);
  const rankedIds = minimap.ranked_affordance_ids || [];
  const topRankIds = new Set(rankedIds.slice(0, 3));
  const targetAffordanceId = telemetry.navigation?.target_affordance?.id || null;
  const activeObjectiveId = telemetry.navigation?.objective_state?.active_objective?.id || telemetry.navigation?.active_objective?.id || null;

  const objectMarkers = new Map();
  const warpMarkers = new Map();
  const bgMarkers = new Map();
  const triggerCells = new Set();

  const affordances = telemetry.navigation?.affordances || [];
  for (const affordance of affordances) {
    if (affordance.kind === "warp" && affordance.target) {
      warpMarkers.set(keyForPoint(affordance.target.x, affordance.target.y), {
        label: affordance.label,
        unexplored: affordance.target_map && affordance.target_map !== "LAST_MAP" && !visitedMaps.has(affordance.target_map),
        topRank: topRankIds.has(affordance.id),
        target: affordance.id === targetAffordanceId,
      });
    } else if (affordance.kind === "object" && affordance.target) {
      objectMarkers.set(keyForPoint(affordance.target.x, affordance.target.y), {
        label: affordance.label,
        sprite: affordance.sprite,
        topRank: topRankIds.has(affordance.id),
        target: affordance.id === targetAffordanceId,
      });
    } else if (affordance.kind === "bg_event" && affordance.target) {
      bgMarkers.set(keyForPoint(affordance.target.x, affordance.target.y), {
        label: affordance.label,
        topRank: topRankIds.has(affordance.id),
        target: affordance.id === targetAffordanceId,
      });
    } else if (affordance.kind === "trigger_region") {
      if (affordance.axis === "y") {
        for (let x = 0; x < minimap.width; x += 1) {
          triggerCells.add(keyForPoint(x, affordance.value));
        }
      } else if (affordance.axis === "x") {
        for (let y = 0; y < minimap.height; y += 1) {
          triggerCells.add(keyForPoint(affordance.value, y));
        }
      }
    }
  }

  for (let y = 0; y < minimap.height; y += 1) {
    for (let x = 0; x < minimap.width; x += 1) {
      const cellEl = document.createElement("div");
      const pointKey = keyForPoint(x, y);
      const walkable = Boolean(walkableGrid[y]?.[x]);
      cellEl.className = `minimap-cell ${walkable ? "is-walkable" : "is-wall"}`;

      if (triggerCells.has(pointKey)) cellEl.classList.add("is-trigger");
      if (pathTiles.has(pointKey)) cellEl.classList.add("is-path");
      if (targetTiles.has(pointKey)) cellEl.classList.add("is-target-tile");
      if (blocked.has(pointKey)) cellEl.classList.add("is-blocked");

      const warp = warpMarkers.get(pointKey);
      const object = objectMarkers.get(pointKey);
      const bg = bgMarkers.get(pointKey);

      let marker = "";
      if (pointKey === playerKey) {
        cellEl.classList.add("is-player");
        marker = "P";
      } else if (warp) {
        cellEl.classList.add("has-marker", "is-warp");
        if (warp.unexplored) cellEl.classList.add("is-unexplored");
        if (warp.topRank) cellEl.classList.add("is-top-rank");
        if (warp.target) cellEl.classList.add("is-target-marker");
        marker = "W";
      } else if (object) {
        cellEl.classList.add("has-marker", "is-object");
        if (object.topRank) cellEl.classList.add("is-top-rank");
        if (object.target) cellEl.classList.add("is-target-marker");
        marker = "O";
      } else if (bg) {
        cellEl.classList.add("has-marker", "is-bg");
        if (bg.topRank) cellEl.classList.add("is-top-rank");
        if (bg.target) cellEl.classList.add("is-target-marker");
        marker = "S";
      } else if (targetTiles.has(pointKey)) {
        marker = "◎";
      } else if (pathTiles.has(pointKey)) {
        marker = "·";
      }

      if (marker) {
        const markerEl = document.createElement("span");
        markerEl.className = "minimap-marker";
        markerEl.textContent = marker;
        cellEl.append(markerEl);
      }

      const titleParts = [`(${x}, ${y})`, walkable ? "walkable" : "wall"];
      if (warp) titleParts.push(warp.label);
      if (object) titleParts.push(object.label);
      if (bg) titleParts.push(bg.label);
      if (pointKey === playerKey) titleParts.push("player");
      if (targetTiles.has(pointKey)) titleParts.push("target tile");
      if (pathTiles.has(pointKey)) titleParts.push("planned path");
      cellEl.title = titleParts.join(" | ");
      gridEl.appendChild(cellEl);
    }
  }

  minimapPanelEl.appendChild(gridEl);

  const lines = [
    "legend",
    "P player  W warp  O object  S sign/bg",
    "◎ target tile  · path  cyan line trigger region",
    "gold border current target  blue border top-ranked",
    `visited maps   ${(minimap.visited_maps || []).length}`,
    `objective      ${activeObjectiveId ?? "none"}`,
    `target         ${telemetry.navigation?.target_affordance?.id ?? "none"}`,
  ];
  minimapLegendBlockEl.textContent = lines.join("\n");
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
      const semanticTags = (affordance.semantic_tags || []).slice(0, 3).join(", ");
      const hints = (affordance.identity_hints || []).slice(0, 3).join(", ");
      const pathBits = [];
      if (typeof affordance.reachability?.path_length === "number") {
        pathBits.push(`path=${affordance.reachability.path_length}`);
      }
      if (affordance.novelty) {
        pathBits.push(`novelty=${affordance.novelty}`);
      }
      return `${index + 1}. [${affordance.score}] ${affordance.id}\n   ${affordance.label}\n   ${reasons || affordance.kind}\n   ${semanticTags || "no tags"}${hints ? ` | ${hints}` : ""}${pathBits.length ? ` | ${pathBits.join(" ")}` : ""}`;
    })
    .join("\n\n");
}

function formatMemoryBlock(telemetry) {
  const memory = telemetry.navigation?.memory || {};
  const objectiveState = telemetry.navigation?.objective_state || {};
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
    "recent objective progress",
    (objectiveState.objective_progress || []).length
      ? objectiveState.objective_progress
          .slice(-6)
          .map((entry) => `${entry.id} success=${entry.success ? "yes" : "no"} partial=${entry.partial ? "yes" : "no"} signals=${(entry.progress_signals || []).join(", ") || "none"}`)
          .join("\n")
      : "none",
    "",
    "objective invalidations",
    (objectiveState.objective_invalidations || []).length
      ? objectiveState.objective_invalidations
          .slice(-6)
          .map((entry) => `${entry.id}: ${entry.reason}`)
          .join("\n")
      : "none",
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
  const observation = agentContext.observation || {};
  const navigation = observation.navigation || {};
  const objectiveState = agentContext.objective_state || {};
  const startup = getAgentStartupInfo(agentStatus);
  return [
    `agent state     ${agentStatus.state}`,
    `mode            ${agentStatus.mode ?? "none"}`,
    `model           ${agentStatus.model ?? "none"}`,
    `reasoning       ${agentStatus.reasoning_effort ?? "default"}`,
    `context         ${formatContextUsage(agentStatus.token_usage)}`,
    `fresh thread    ${agentStatus.fresh_thread ?? false}`,
    `steps           ${agentStatus.step_count}`,
    `thread          ${agentStatus.thread_id ?? "none"}`,
    startup ? `startup         ${startup.phase} (${startup.elapsedLabel})` : null,
    `objective       ${objectiveState.active_objective?.label ?? "none"}`,
    "",
    `last action     ${decision.action ?? "none"}`,
    decision.objective_id ? `objective id    ${decision.objective_id}` : null,
    decision.affordance_id ? `affordance      ${decision.affordance_id}` : null,
    `reason          ${decision.reason ?? "none"}`,
    `result mode     ${result.mode ?? "none"}`,
    result.map?.const_name ? `result map      ${result.map.const_name}` : null,
    navigation.target_affordance?.label ? `target          ${navigation.target_affordance.label}` : null,
    "",
    `heuristic       ${heuristic.action ?? "none"}`,
    heuristic.objective_id ? `heuristic obj   ${heuristic.objective_id}` : null,
    `heuristic why   ${heuristic.reason ?? "none"}`,
    `queued prompt   ${formatPreview(agentStatus.pending_prompt)}`,
    agentStatus.last_error ? "" : null,
    agentStatus.last_error ? `error           ${agentStatus.last_error}` : null,
  ].filter(Boolean).join("\n");
}

function formatAllowedActionsBlock(agentContext) {
  const actions = agentContext.allowed_actions || [];
  if (!actions.length) {
    return "No allowed actions";
  }
  return actions
    .map((action) => `${action.id}\n  ${action.description ?? action.type}`)
    .join("\n\n");
}

function formatDecisionStateBlock(agentContext) {
  return formatJsonBlock(agentContext.decision_state, "No decision state");
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
  const pokedex = telemetry.pokedex || {};
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
  if (pokedex.active) {
    lines.push(
      "",
      "pokedex",
      `species        ${pokedex.species_name ?? "unknown"}`,
      `class          ${pokedex.species_class ?? "unknown"}`,
      `dex            ${pokedex.dex_number ?? "unknown"}`,
      `stats          ${pokedex.height_weight ?? "unknown"}`,
      "",
      ...((pokedex.description_lines || []).length ? pokedex.description_lines : ["No description lines"]),
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
      if (entry.kind === "agent_prompt_queued") {
        return `PROMPT queued\n${entry.preview ?? ""}`;
      }
      if (entry.kind === "agent_prompt_consumed") {
        return `PROMPT consumed\n${entry.preview ?? ""}`;
      }
      if (entry.kind === "agent_prompt_cleared") {
        return "PROMPT cleared";
      }
      return entry.kind;
    })
    .join("\n\n");
}

function renderAgentPanel(agentStatus) {
  latestAgentStatus = agentStatus;
  const queuedPrompt = agentStatus.pending_prompt || "";
  const lastPrompt = agentStatus.last_consumed_prompt || "";
  const startup = getAgentStartupInfo(agentStatus);
  const displayedModel = getDisplayedAgentModel(agentStatus);
  const displayedReasoning = getDisplayedAgentReasoning(agentStatus);

  agentStatStateEl.textContent = agentStatus.state ?? "idle";
  agentStatStateCaptionEl.textContent = describeAgentState(agentStatus);

  agentStatModelEl.textContent = displayedModel;
  agentStatModelCaptionEl.textContent = agentStatus.model_provider
    ? `Provider: ${agentStatus.model_provider}`
    : agentStatus.configured_model ? "Configured for next turn" : "No provider";

  agentStatReasoningEl.textContent = displayedReasoning;
  agentStatReasoningCaptionEl.textContent = `Mode: ${agentStatus.mode ?? "none"} • Steps: ${agentStatus.step_count ?? 0}`;

  agentStatThreadEl.textContent = shortId(agentStatus.thread_id);
  if (agentStatus.turn_id) {
    agentStatThreadCaptionEl.textContent = `Turn ${shortId(agentStatus.turn_id)}`;
  } else if (startup) {
    agentStatThreadCaptionEl.textContent = `First action pending • ${startup.elapsedLabel}`;
  } else {
    agentStatThreadCaptionEl.textContent = "No active turn";
  }

  agentQueuedPromptBlockEl.textContent = queuedPrompt || "No queued note.";
  agentLastPromptBlockEl.textContent = lastPrompt || "No note sent yet.";
  agentStatusBlockEl.textContent = formatAgentSessionBlock(agentStatus);
  renderAgentConfig(agentStatus);
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
    renderMinimap(telemetry);
    affordancesBlockEl.textContent = formatAffordancesBlock(telemetry);
    memoryBlockEl.textContent = formatMemoryBlock(telemetry);
    decisionBlockEl.textContent = formatDecisionBlock(agentContext, agentStatus);
    allowedActionsBlockEl.textContent = formatAllowedActionsBlock(agentContext);
    decisionStateBlockEl.textContent = formatDecisionStateBlock(agentContext);
    agentContextBlockEl.textContent = formatJsonBlock(agentContext, "No agent context");
    agentPromptBlockEl.textContent = agentContext.prompt || "No prompt";
    dialogueBlockEl.textContent = formatDialogueBlock(telemetry);
    menuBlockEl.textContent = formatMenuBlock(telemetry);
    battleBlockEl.textContent = formatBattleBlock(telemetry);
    trainerBlockEl.textContent = formatTrainerBlock(telemetry);
    eventsBlockEl.textContent = formatEventsBlock(telemetry);
    tracesBlockEl.textContent = formatTracesBlock(traces);
    agentLogBlockEl.textContent = formatAgentLog(agentStatus);
    decodedRowsBlockEl.textContent = telemetry.screen.decoded_rows.join("\n");
    tilemapBlockEl.textContent = telemetry.screen.tilemap_rows_hex.join("\n");

    stateSlotLabelEl.textContent = "slot: quick";
    agentStateLabelEl.textContent = `agent: ${agentStatus.state}`;
    renderAgentPanel(agentStatus);

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
document.getElementById("agent-queue-prompt-btn").addEventListener("click", () => queueAgentPrompt());
document.getElementById("agent-clear-prompt-btn").addEventListener("click", () => clearAgentPrompt());
agentModelSelectEl.addEventListener("change", () => {
  selectedAgentModel = agentModelSelectEl.value;
  selectedAgentReasoning = "";
  if (latestAgentStatus) {
    renderAgentReasoningOptions(latestAgentStatus);
  }
});
agentReasoningSelectEl.addEventListener("change", () => {
  selectedAgentReasoning = agentReasoningSelectEl.value;
});
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

ensureAgentModelsLoaded().catch((error) => {
  console.error("Failed to load agent models", error);
});
refresh().catch((error) => console.error(error));
