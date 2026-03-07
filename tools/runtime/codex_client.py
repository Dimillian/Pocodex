from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import queue
import subprocess
from threading import Lock, Thread
import time
from typing import Any, Callable


class CodexAppServerError(RuntimeError):
    pass


def _runtime_tool_spec(name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short explanation for why this tool was chosen in the current game state.",
                }
            },
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
    _runtime_tool_spec("follow_objective", "Let the runtime follow the current navigation objective for several verified steps."),
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
        tool_handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.cwd = cwd
        self.thread_state_path = thread_state_path
        self.fresh_thread = fresh_thread
        self.tool_handler = tool_handler
        self.thread_id: str | None = None
        self._process: subprocess.Popen[str] | None = None
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = Lock()
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._turn_tool_results: dict[str, list[dict[str, Any]]] = {}
        self._next_id = 1
        self._id_lock = Lock()
        self._stderr_lines: deque[str] = deque(maxlen=50)
        self._stdout_thread: Thread | None = None
        self._stderr_thread: Thread | None = None

    def __enter__(self) -> CodexAppServerClient:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if self._process is not None:
            return

        process = subprocess.Popen(
            ["codex", "app-server"],
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

    def decide_action(self, context: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
        self.start()
        allowed_ids = [action["id"] for action in context["allowed_actions"]]
        model_input = json.dumps(
            {
                "objective": context["objective"],
                "rules": context["rules"],
                "allowed_actions": context["allowed_actions"],
                "heuristic_next_action": context["heuristic_next_action"],
                "model_input": context.get("model_input"),
            },
            ensure_ascii=True,
            indent=2,
        )
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
            },
            "required": ["action", "reason"],
            "additionalProperties": False,
        }
        prompt = (
            f"{context['prompt']}\n"
            "Use the provided runtime tools to interact with the game instead of describing an action in prose. "
            "Prefer exactly one tool call per turn. If a tool reports it is invalid for the current state, choose a different tool. "
            "After acting, return the JSON object only."
        )
        response = self.request(
            "turn/start",
            {
                "threadId": self._require_thread_id(),
                "input": [
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "text",
                        "text": (
                            "Structured context JSON follows. Use it as the source of truth, especially for "
                            "map warps, objects, navigation objective, recent movement results, and allowed actions.\n"
                            f"{model_input}"
                        ),
                    }
                ],
                "cwd": str(self.cwd),
                "approvalPolicy": "never",
                "sandboxPolicy": {
                    "type": "readOnly",
                },
                "personality": "pragmatic",
                "summary": "concise",
                "outputSchema": output_schema,
            },
            timeout=30.0,
        )
        turn = response.get("turn") or {}
        turn_id = turn.get("id")
        if not turn_id:
            raise CodexAppServerError(f"Missing turn id in turn/start response: {response}")
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
        return {
            "decision": decision,
            "thread_id": self._require_thread_id(),
            "turn_id": turn_id,
            "response_text": result["text"],
            "events": result["events"],
            "tool_result": tool_record,
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
                        "personality": "pragmatic",
                    },
                    timeout=15.0,
                )
                thread = result.get("thread") or {}
                self.thread_id = thread.get("id") or saved_thread_id
                self._save_thread_id(self.thread_id)
                return
            except CodexAppServerError:
                self.thread_id = None

        result = self.request(
            "thread/start",
            {
                "cwd": str(self.cwd),
                "approvalPolicy": "never",
                "sandboxPolicy": {
                    "type": "readOnly",
                },
                "personality": "pragmatic",
                "summary": "concise",
                "dynamicTools": RUNTIME_DYNAMIC_TOOLS if self.tool_handler is not None else [],
            },
            timeout=15.0,
        )
        thread = result.get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise CodexAppServerError(f"Missing thread id in thread/start response: {result}")
        self.thread_id = thread_id
        self._save_thread_id(thread_id)

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
                raise CodexAppServerError(f"Timed out waiting for turn '{turn_id}' to complete") from exc

            method = message.get("method")
            params = message.get("params") or {}
            event_log.append(self._summarize_event(message))

            if method == "item/agentMessage/delta":
                item_id = params.get("itemId")
                if item_id:
                    agent_text_by_item[item_id] = agent_text_by_item.get(item_id, "") + params.get("delta", "")
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

        raise CodexAppServerError(f"Timed out waiting for turn '{turn_id}'")

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
        return summary

    def _parse_agent_decision(self, text: str, allowed_ids: list[str]) -> dict[str, str]:
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
        return {
            "action": action,
            "reason": reason.strip(),
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
                self._respond_to_server_request(message)
                continue

            if has_method:
                self._events.put(message)

    def _read_stderr(self) -> None:
        assert self._process is not None
        assert self._process.stderr is not None
        for raw_line in self._process.stderr:
            line = raw_line.strip()
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
