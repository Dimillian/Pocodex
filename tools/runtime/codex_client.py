from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import json
from pathlib import Path
import queue
import re
import subprocess
from threading import Lock, Thread
import time
from typing import Any, Callable


class CodexAppServerError(RuntimeError):
    pass


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _runtime_tool_spec(
    name: str,
    description: str,
    *,
    supports_affordance_id: bool = False,
    supports_objective_id: bool = False,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "reason": {
            "type": "string",
            "description": "Short explanation for why this tool was chosen in the current game state.",
        }
    }
    if supports_affordance_id:
        properties["affordance_id"] = {
            "type": "string",
            "description": "Optional affordance id from the current ranked affordance list when you want to pursue a specific target.",
        }
    if supports_objective_id:
        properties["objective_id"] = {
            "type": "string",
            "description": "Optional objective id from the current objective candidate list when you want to bind the execution window to a specific objective.",
        }
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        },
    }


RUNTIME_DYNAMIC_TOOLS: list[dict[str, Any]] = [
    _runtime_tool_spec("press_a", "Press the A button to confirm, interact, or advance dialogue."),
    _runtime_tool_spec("press_b", "Press the B button to cancel or back out."),
    _runtime_tool_spec("press_start", "Press the Start button to open the in-game menu."),
    _runtime_tool_spec("press_select", "Press the Select button."),
    _runtime_tool_spec("move_up", "Attempt to move or navigate upward by one step."),
    _runtime_tool_spec("move_down", "Attempt to move or navigate downward by one step."),
    _runtime_tool_spec("move_left", "Attempt to move or navigate left by one step."),
    _runtime_tool_spec("move_right", "Attempt to move or navigate right by one step."),
    _runtime_tool_spec(
        "follow_target",
        "Let the runtime pursue a specific affordance target for several verified steps. Pass affordance_id from the current ranked affordance list to choose explicitly.",
        supports_affordance_id=True,
    ),
    _runtime_tool_spec(
        "follow_objective",
        "Let the runtime follow the current inferred objective for several verified steps. Pass objective_id from the current candidate objective list to bind the window to a specific objective.",
        supports_objective_id=True,
    ),
    _runtime_tool_spec("follow_interaction", "Let the runtime resolve the current dialogue, menu, naming, or battle interaction for several verified steps."),
    _runtime_tool_spec("wait_short", "Wait briefly for scripted movement, transitions, or text to update."),
    _runtime_tool_spec("save_quick", "Save the current quick checkpoint."),
    _runtime_tool_spec("load_quick", "Load the current quick checkpoint."),
]


class CodexAppServerClient:
    def __init__(
        self,
        *,
        cwd: Path,
        thread_state_path: Path | None = None,
        fresh_thread: bool = False,
        model: str | None = None,
        reasoning_effort: str | None = None,
        tool_handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        status_handler: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.cwd = cwd
        self.thread_state_path = thread_state_path
        self.fresh_thread = fresh_thread
        self.requested_model = model
        self.requested_reasoning_effort = reasoning_effort
        self.tool_handler = tool_handler
        self.status_handler = status_handler
        self.thread_id: str | None = None
        self.model: str | None = None
        self.model_provider: str | None = None
        self.reasoning_effort: str | None = None
        self.token_usage: dict[str, Any] = {
            "last": None,
            "total": None,
            "model_context_window": None,
        }
        self._process: subprocess.Popen[str] | None = None
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = Lock()
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._turn_tool_results: dict[str, list[dict[str, Any]]] = {}
        self._next_id = 1
        self._id_lock = Lock()
        self._stderr_lines: deque[str] = deque(maxlen=50)
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=50)
        self._pending_turn: dict[str, Any] | None = None
        self._stdout_thread: Thread | None = None
        self._stderr_thread: Thread | None = None

    def __enter__(self) -> CodexAppServerClient:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        self._connect(ensure_thread=True)

    def _connect(self, *, ensure_thread: bool) -> None:
        if self._process is not None:
            return

        command = ["codex", "app-server"]
        if self.requested_reasoning_effort:
            command.extend(
                [
                    "-c",
                    f'model_reasoning_effort="{self.requested_reasoning_effort}"',
                ]
            )

        process = subprocess.Popen(
            command,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            raise CodexAppServerError("codex app-server is missing a stdio pipe")

        self._process = process
        self._stdout_thread = Thread(target=self._read_stdout, name="codex-app-server-stdout", daemon=True)
        self._stderr_thread = Thread(target=self._read_stderr, name="codex-app-server-stderr", daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "pokered_runtime",
                    "title": "Pokered Runtime Agent Runner",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                },
            },
            timeout=15.0,
        )
        self.notify("initialized", None)
        if ensure_thread:
            self._ensure_thread()

    def close(self) -> None:
        process = self._process
        if process is None:
            return

        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

        self._process = None

    def decide_action(
        self,
        context: dict[str, Any],
        *,
        operator_prompt: str | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        self.start()
        allowed_ids = [action["id"] for action in context["allowed_actions"]]
        model_input = json.dumps(context.get("model_input") or {}, ensure_ascii=True, separators=(",", ":"))
        output_schema = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": allowed_ids,
                },
                "reason": {
                    "type": "string",
                },
                "affordance_id": {
                    "type": ["string", "null"],
                },
                "objective_id": {
                    "type": ["string", "null"],
                },
            },
            "required": ["action", "reason", "affordance_id", "objective_id"],
            "additionalProperties": False,
        }
        inputs: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": context["prompt"],
            }
        ]
        if operator_prompt:
            inputs.append(
                {
                    "type": "text",
                    "text": (
                        "Runtime operator note from the web UI follows. Treat it as high-priority guidance for the next action "
                        "selection, but still obey the structured game state, allowed actions, and tool-only response contract.\n"
                        f"{operator_prompt}"
                    ),
                }
            )
        inputs.append(
            {
                "type": "text",
                "text": (
                    "Structured turn input JSON follows. Use it as the only game-state source of truth for this turn.\n"
                    f"{model_input}"
                ),
            }
        )
        self._pending_turn = {
            "phase": "requesting",
            "requested_at": _timestamp(),
            "thread_id": self._require_thread_id(),
            "turn_id": None,
            "observation_mode": context.get("observation", {}).get("mode"),
            "allowed_action_count": len(context.get("allowed_actions") or []),
            "requested_model": self.requested_model,
            "requested_reasoning_effort": self.requested_reasoning_effort,
            "operator_prompt_present": bool(operator_prompt),
        }
        self._emit_status("turn_requested", self._pending_turn)
        response = self.request(
            "turn/start",
            {
                "threadId": self._require_thread_id(),
                "input": inputs,
                "cwd": str(self.cwd),
                "approvalPolicy": "never",
                "sandboxPolicy": {
                    "type": "readOnly",
                },
                "model": self.requested_model,
                "effort": self.requested_reasoning_effort,
                "personality": "pragmatic",
                "outputSchema": output_schema,
            },
            timeout=30.0,
        )
        turn = response.get("turn") or {}
        turn_id = turn.get("id")
        if not turn_id:
            raise CodexAppServerError(f"Missing turn id in turn/start response: {response}")
        self._pending_turn = {
            **(self._pending_turn or {}),
            "phase": "waiting",
            "turn_id": turn_id,
            "started_at": _timestamp(),
        }
        self._emit_status("turn_started", self._pending_turn)
        result = self._wait_for_turn(turn_id, timeout=timeout)
        tool_results = result.get("tool_results") or []
        decision: dict[str, str]
        if result["text"]:
            decision = self._parse_agent_decision(result["text"], allowed_ids)
        elif tool_results:
            tool_record = self._select_tool_result(tool_results)
            decision = {
                "action": tool_record["action"],
                "reason": tool_record.get("reason") or "Executed via runtime tool call.",
            }
            if tool_record and tool_record.get("affordance_id"):
                decision["affordance_id"] = tool_record["affordance_id"]
            if tool_record and tool_record.get("objective_id"):
                decision["objective_id"] = tool_record["objective_id"]
        else:
            raise CodexAppServerError(
                f"Turn '{turn_id}' completed without a tool call or agent message. "
                f"Recent stderr: {list(self._stderr_lines)}"
            )
        tool_record = self._select_tool_result(tool_results)
        if tool_record and tool_record.get("success"):
            decision = {
                "action": tool_record["action"],
                "reason": decision["reason"],
            }
            if tool_record.get("affordance_id"):
                decision["affordance_id"] = tool_record["affordance_id"]
            if tool_record.get("objective_id"):
                decision["objective_id"] = tool_record["objective_id"]
        self._pending_turn = None
        self._emit_status(
            "turn_completed",
            {
                "turn_id": turn_id,
                "thread_id": self._require_thread_id(),
            },
        )
        return {
            "decision": decision,
            "thread_id": self._require_thread_id(),
            "turn_id": turn_id,
            "response_text": result["text"],
            "events": result["events"],
            "tool_result": tool_record,
            "model": self.model,
            "model_provider": self.model_provider,
            "reasoning_effort": self.reasoning_effort,
            "token_usage": self.token_usage,
            "stderr_lines": list(self._stderr_lines),
        }

    def request(self, method: str, params: dict[str, Any] | None, *, timeout: float) -> dict[str, Any]:
        request_id = self._next_request_id()
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = response_queue
        self._write_message(
            {
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        try:
            message = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise CodexAppServerError(f"Timed out waiting for '{method}' response") from exc

        if "error" in message:
            error_payload = message["error"]
            raise CodexAppServerError(f"{method} failed: {json.dumps(error_payload)}")
        return message.get("result") or {}

    def notify(self, method: str, params: dict[str, Any] | None) -> None:
        self._write_message(
            {
                "method": method,
                "params": params or {},
            }
        )

    def _ensure_thread(self) -> None:
        if self.fresh_thread:
            self._clear_saved_thread_id()

        saved_thread_id = self._load_thread_id()
        if saved_thread_id:
            try:
                result = self.request(
                    "thread/resume",
                    {
                        "threadId": saved_thread_id,
                        "model": self.requested_model,
                        "effort": self.requested_reasoning_effort,
                        "personality": "pragmatic",
                    },
                    timeout=15.0,
                )
                thread = result.get("thread") or {}
                self.thread_id = thread.get("id") or saved_thread_id
                self._apply_thread_settings(result)
                self._save_thread_id(self.thread_id)
                self._emit_status(
                    "thread_ready",
                    {
                        "thread_id": self.thread_id,
                        "model": self.model,
                        "model_provider": self.model_provider,
                        "reasoning_effort": self.reasoning_effort,
                    },
                )
                return
            except CodexAppServerError:
                self.thread_id = None

        result = self.request(
            "thread/start",
            {
                "model": self.requested_model,
                "effort": self.requested_reasoning_effort,
                "cwd": str(self.cwd),
                "approvalPolicy": "never",
                "sandboxPolicy": {
                    "type": "readOnly",
                },
                "personality": "pragmatic",
                "dynamicTools": RUNTIME_DYNAMIC_TOOLS if self.tool_handler is not None else [],
            },
            timeout=15.0,
        )
        thread = result.get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise CodexAppServerError(f"Missing thread id in thread/start response: {result}")
        self.thread_id = thread_id
        self._apply_thread_settings(result)
        self._save_thread_id(thread_id)
        self._emit_status(
            "thread_ready",
            {
                "thread_id": self.thread_id,
                "model": self.model,
                "model_provider": self.model_provider,
                "reasoning_effort": self.reasoning_effort,
            },
        )

    @classmethod
    def list_models(cls, *, cwd: Path) -> list[dict[str, Any]]:
        client = cls(cwd=cwd)
        try:
            client._connect(ensure_thread=False)
            models: list[dict[str, Any]] = []
            cursor: str | None = None
            while True:
                result = client.request(
                    "model/list",
                    {
                        "limit": 100,
                        "includeHidden": False,
                        "cursor": cursor,
                    },
                    timeout=15.0,
                )
                models.extend(result.get("data") or [])
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            return models
        finally:
            client.close()

    def _wait_for_turn(self, turn_id: str, *, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        agent_text_by_item: dict[str, str] = {}
        final_text: str | None = None
        event_log: list[dict[str, Any]] = []

        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                message = self._events.get(timeout=remaining)
            except queue.Empty as exc:
                raise CodexAppServerError(self._format_turn_timeout(turn_id, event_log)) from exc

            method = message.get("method")
            params = message.get("params") or {}
            event_summary = self._summarize_event(message)
            event_log.append(event_summary)
            self._recent_events.append(event_summary)
            if self._pending_turn and self._pending_turn.get("turn_id") == turn_id:
                self._pending_turn = {
                    **self._pending_turn,
                    "last_event": event_summary,
                    "event_count": len(event_log),
                }

            if method == "item/agentMessage/delta":
                item_id = params.get("itemId")
                if item_id:
                    agent_text_by_item[item_id] = agent_text_by_item.get(item_id, "") + params.get("delta", "")
                continue

            if method == "thread/tokenUsage/updated":
                if params.get("threadId") == self.thread_id:
                    self._apply_token_usage(params.get("tokenUsage"))
                continue

            if method == "model/rerouted":
                if params.get("threadId") == self.thread_id:
                    self.model = params.get("toModel") or self.model
                continue

            if method == "item/completed":
                item = params.get("item") or {}
                if self._item_type(item) == "agentMessage":
                    item_id = item.get("id")
                    item_text = item.get("text") or ""
                    if not item_text and item_id:
                        item_text = agent_text_by_item.get(item_id, "")
                    if item_text:
                        final_text = item_text
                continue

            if method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("id") != turn_id:
                    continue
                status = turn.get("status")
                if status != "completed":
                    raise CodexAppServerError(f"Turn '{turn_id}' ended with status '{status}': {turn}")
                if not final_text:
                    final_text = self._agent_text_from_turn(turn, agent_text_by_item)
                tool_results = self._consume_turn_tool_results(turn_id)
                if not final_text and not tool_results:
                    raise CodexAppServerError(
                        f"Turn '{turn_id}' completed without an agent message. "
                        f"Recent stderr: {list(self._stderr_lines)}"
                    )
                return {
                    "text": final_text or "",
                    "events": event_log[-20:],
                    "tool_results": tool_results,
                }

        raise CodexAppServerError(self._format_turn_timeout(turn_id, event_log))

    def _agent_text_from_turn(self, turn: dict[str, Any], agent_text_by_item: dict[str, str]) -> str | None:
        for item in turn.get("items") or []:
            if self._item_type(item) != "agentMessage":
                continue
            item_id = item.get("id")
            item_text = item.get("text") or ""
            if not item_text and item_id:
                item_text = agent_text_by_item.get(item_id, "")
            if item_text:
                return item_text
        if agent_text_by_item:
            return list(agent_text_by_item.values())[-1]
        return None

    def _item_type(self, item: dict[str, Any]) -> str | None:
        return item.get("type")

    def _summarize_event(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method")
        params = message.get("params") or {}
        summary = {"method": method}
        if "turn" in params:
            summary["turn_id"] = params["turn"].get("id")
            summary["status"] = params["turn"].get("status")
        if "itemId" in params:
            summary["item_id"] = params.get("itemId")
        if "delta" in params:
            summary["delta_preview"] = params.get("delta", "")[:120]
        if "item" in params:
            item = params["item"] or {}
            summary["item_type"] = item.get("type")
            summary["item_id"] = item.get("id")
        if method == "thread/tokenUsage/updated":
            token_usage = params.get("tokenUsage") or {}
            summary["last_total_tokens"] = (token_usage.get("last") or {}).get("totalTokens")
            summary["total_total_tokens"] = (token_usage.get("total") or {}).get("totalTokens")
            summary["model_context_window"] = token_usage.get("modelContextWindow")
        if method == "model/rerouted":
            summary["from_model"] = params.get("fromModel")
            summary["to_model"] = params.get("toModel")
        return summary

    def _apply_thread_settings(self, payload: dict[str, Any]) -> None:
        self.model = payload.get("model") or self.model
        self.model_provider = payload.get("modelProvider") or self.model_provider
        self.reasoning_effort = payload.get("reasoningEffort") or self.reasoning_effort

    def debug_snapshot(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "model": self.model,
            "model_provider": self.model_provider,
            "reasoning_effort": self.reasoning_effort,
            "requested_model": self.requested_model,
            "requested_reasoning_effort": self.requested_reasoning_effort,
            "pending_turn": dict(self._pending_turn) if self._pending_turn else None,
            "recent_events": list(self._recent_events),
            "stderr_lines": list(self._stderr_lines),
            "token_usage": self.token_usage,
        }

    def _apply_token_usage(self, payload: dict[str, Any] | None) -> None:
        payload = payload or {}
        self.token_usage = {
            "last": self._normalize_token_usage_breakdown(payload.get("last")),
            "total": self._normalize_token_usage_breakdown(payload.get("total")),
            "model_context_window": payload.get("modelContextWindow"),
        }

    def _normalize_token_usage_breakdown(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if not payload:
            return None
        return {
            "cached_input_tokens": payload.get("cachedInputTokens"),
            "input_tokens": payload.get("inputTokens"),
            "output_tokens": payload.get("outputTokens"),
            "reasoning_output_tokens": payload.get("reasoningOutputTokens"),
            "total_tokens": payload.get("totalTokens"),
        }

    def _parse_agent_decision(self, text: str, allowed_ids: list[str]) -> dict[str, Any]:
        normalized = text.strip()
        if normalized.startswith("```"):
            lines = normalized.splitlines()
            if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
                normalized = "\n".join(lines[1:-1]).strip()
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise CodexAppServerError(f"Agent returned non-JSON text: {text}") from exc

        action = payload.get("action")
        reason = payload.get("reason")
        if action not in allowed_ids:
            allowed = ", ".join(allowed_ids)
            raise CodexAppServerError(f"Agent chose invalid action '{action}'. Allowed: {allowed}")
        if not isinstance(reason, str) or not reason.strip():
            raise CodexAppServerError(f"Agent returned invalid reason payload: {payload}")
        affordance_id = payload.get("affordance_id")
        if affordance_id is not None and not isinstance(affordance_id, str):
            raise CodexAppServerError(f"Agent returned invalid affordance_id payload: {payload}")
        objective_id = payload.get("objective_id")
        if objective_id is not None and not isinstance(objective_id, str):
            raise CodexAppServerError(f"Agent returned invalid objective_id payload: {payload}")
        return {
            "action": action,
            "reason": reason.strip(),
            "affordance_id": affordance_id,
            "objective_id": objective_id,
        }

    def _next_request_id(self) -> int:
        with self._id_lock:
            request_id = self._next_id
            self._next_id += 1
        return request_id

    def _write_message(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise CodexAppServerError("codex app-server is not running")
        process.stdin.write(json.dumps(payload))
        process.stdin.write("\n")
        process.stdin.flush()

    def _read_stdout(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        for raw_line in self._process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._events.put(
                    {
                        "method": "codex/parseError",
                        "params": {
                            "raw": line,
                        },
                    }
                )
                continue

            request_id = message.get("id")
            has_response = "result" in message or "error" in message
            has_method = "method" in message
            if request_id is not None and has_response:
                with self._pending_lock:
                    response_queue = self._pending.pop(request_id, None)
                if response_queue is not None:
                    response_queue.put(message)
                else:
                    self._events.put(message)
                continue

            if request_id is not None and has_method:
                self._recent_events.append(self._summarize_event(message))
                self._respond_to_server_request(message)
                continue

            if has_method:
                self._recent_events.append(self._summarize_event(message))
                self._events.put(message)

    def _read_stderr(self) -> None:
        assert self._process is not None
        assert self._process.stderr is not None
        for raw_line in self._process.stderr:
            line = _strip_ansi(raw_line).strip()
            if line:
                self._stderr_lines.append(line)

    def _respond_to_server_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "item/tool/call":
            self._handle_dynamic_tool_call(message)
            return
        self._write_message(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unsupported server request '{method}' in pokered agent runner",
                },
            }
        )

    def _handle_dynamic_tool_call(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        params = message.get("params") or {}
        turn_id = params.get("turnId")
        tool = params.get("tool")
        arguments = params.get("arguments") or {}

        if not request_id or not isinstance(tool, str):
            self._write_message(
                {
                    "id": request_id,
                    "result": {
                        "contentItems": [
                            {"type": "inputText", "text": "Invalid dynamic tool request payload."}
                        ],
                        "success": False,
                    },
                }
            )
            return

        if self.tool_handler is None:
            record = {
                "tool": tool,
                "action": tool,
                "arguments": arguments,
                "reason": arguments.get("reason"),
                "success": False,
                "error": f"No dynamic tool handler is configured for '{tool}'.",
            }
            self._record_turn_tool_result(turn_id, record)
            self._write_message(
                {
                    "id": request_id,
                    "result": {
                        "contentItems": [
                            {"type": "inputText", "text": json.dumps(record, ensure_ascii=True)}
                        ],
                        "success": False,
                    },
                }
            )
            return

        try:
            handler_result = self.tool_handler(tool, arguments)
        except Exception as exc:
            record = {
                "tool": tool,
                "action": tool,
                "arguments": arguments,
                "reason": arguments.get("reason"),
                "success": False,
                "error": str(exc),
            }
        else:
            record = dict(handler_result.get("record") or {})
            record.setdefault("tool", tool)
            record.setdefault("action", tool)
            record.setdefault("arguments", arguments)
            record.setdefault("reason", arguments.get("reason"))
            record.setdefault("success", bool(handler_result.get("success", True)))

        content_items = handler_result.get("content_items") if "handler_result" in locals() else None
        if not content_items:
            content_items = [{"type": "inputText", "text": json.dumps(record, ensure_ascii=True)}]
        success = bool(record.get("success"))
        self._record_turn_tool_result(turn_id, record)
        self._write_message(
            {
                "id": request_id,
                "result": {
                    "contentItems": content_items,
                    "success": success,
                },
            }
        )

    def _record_turn_tool_result(self, turn_id: str | None, record: dict[str, Any]) -> None:
        if not turn_id:
            return
        self._turn_tool_results.setdefault(turn_id, []).append(record)

    def _consume_turn_tool_results(self, turn_id: str) -> list[dict[str, Any]]:
        return self._turn_tool_results.pop(turn_id, [])

    def _select_tool_result(self, tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not tool_results:
            return None
        for record in reversed(tool_results):
            if record.get("success"):
                return record
        return tool_results[-1]

    def _load_thread_id(self) -> str | None:
        if self.thread_state_path is None or not self.thread_state_path.exists():
            return None
        try:
            payload = json.loads(self.thread_state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        thread_id = payload.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
        return None

    def _save_thread_id(self, thread_id: str) -> None:
        if self.thread_state_path is None:
            return
        self.thread_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.thread_state_path.write_text(
            json.dumps(
                {
                    "thread_id": thread_id,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _clear_saved_thread_id(self) -> None:
        self.thread_id = None
        if self.thread_state_path is None or not self.thread_state_path.exists():
            return
        self.thread_state_path.unlink()

    def _require_thread_id(self) -> str:
        if not self.thread_id:
            raise CodexAppServerError("No active Codex thread")
        return self.thread_id

    def _format_turn_timeout(self, turn_id: str, event_log: list[dict[str, Any]]) -> str:
        details = {
            "thread_id": self.thread_id,
            "requested_model": self.requested_model,
            "requested_reasoning_effort": self.requested_reasoning_effort,
            "resolved_model": self.model,
            "resolved_reasoning_effort": self.reasoning_effort,
            "pending_turn": self._pending_turn,
            "recent_events": event_log[-12:] or list(self._recent_events)[-12:],
            "recent_stderr": list(self._stderr_lines),
        }
        return f"Timed out waiting for turn '{turn_id}' to complete: {json.dumps(details, ensure_ascii=True)}"

    def _emit_status(self, event: str, payload: dict[str, Any]) -> None:
        if self.status_handler is None:
            return
        self.status_handler(event, payload)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)
