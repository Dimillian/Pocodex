from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import Event, Lock, Thread
import time
from typing import Any

from .codex_client import CodexAppServerClient
from .session import RuntimeSession


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


class AgentController:
    def __init__(self, session: RuntimeSession, *, repo_root: Path) -> None:
        self.session = session
        self.repo_root = repo_root
        self.logs_dir = repo_root / ".runtime-traces" / "agent-ui" / session.rom_name
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.logs_dir / "steps.jsonl"
        self.thread_state_path = self.logs_dir / "codex-thread.json"
        self._thread: Thread | None = None
        self._stop_event = Event()
        self._lock = Lock()
        self._recent_logs: deque[dict[str, Any]] = deque(maxlen=30)
        self._status: dict[str, Any] = {
            "running": False,
            "state": "idle",
            "mode": None,
            "step_count": 0,
            "step_delay_ms": 500,
            "max_steps": None,
            "thread_id": None,
            "turn_id": None,
            "current_action": None,
            "last_decision": None,
            "last_result": None,
            "last_error": None,
            "last_response_text": None,
            "started_at": None,
            "updated_at": _timestamp(),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            payload = dict(self._status)
            payload["recent_logs"] = list(self._recent_logs)
            payload["log_path"] = str(self.log_path)
            payload["thread_state_path"] = str(self.thread_state_path)
            payload["stop_requested"] = self._stop_event.is_set()
            return payload

    def start(
        self,
        *,
        mode: str = "codex",
        step_delay_ms: int = 500,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("Agent controller is already running")
            self._stop_event.clear()
            self._status.update(
                {
                    "running": True,
                    "state": "starting",
                    "mode": mode,
                    "step_count": 0,
                    "step_delay_ms": step_delay_ms,
                    "max_steps": max_steps,
                    "thread_id": None,
                    "turn_id": None,
                    "current_action": None,
                    "last_decision": None,
                    "last_result": None,
                    "last_error": None,
                    "last_response_text": None,
                    "started_at": _timestamp(),
                    "updated_at": _timestamp(),
                }
            )
            self._thread = Thread(
                target=self._run_loop,
                kwargs={
                    "mode": mode,
                    "step_delay_ms": step_delay_ms,
                    "max_steps": max_steps,
                },
                name="pokered-agent-controller",
                daemon=True,
            )
            self._thread.start()
        self._append_log(
            {
                "timestamp": _timestamp(),
                "kind": "agent_controller_started",
                "mode": mode,
                "step_delay_ms": step_delay_ms,
                "max_steps": max_steps,
            }
        )
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        with self._lock:
            if self._status["running"]:
                self._status["state"] = "stopping"
                self._status["updated_at"] = _timestamp()
        self._append_log(
            {
                "timestamp": _timestamp(),
                "kind": "agent_controller_stop_requested",
            }
        )
        return self.status()

    def shutdown(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1)

    def _run_loop(self, *, mode: str, step_delay_ms: int, max_steps: int | None) -> None:
        codex_client = None
        try:
            if mode == "codex":
                codex_client = CodexAppServerClient(
                    cwd=self.repo_root,
                    thread_state_path=self.thread_state_path,
                )
                codex_client.start()
                self._update_status(
                    state="running",
                    thread_id=codex_client.thread_id,
                )
            elif mode == "heuristic":
                self._update_status(state="running")
            else:
                raise ValueError(f"Unsupported agent mode '{mode}'")

            while not self._stop_event.is_set():
                if max_steps is not None and self.status()["step_count"] >= max_steps:
                    self._update_status(
                        running=False,
                        state="completed",
                    )
                    break

                context = self.session.agent_context()
                decision, codex_meta = self._decide_action(context, mode=mode, codex_client=codex_client)
                action_id = decision["action"]
                reason = decision["reason"]
                self._update_status(
                    state="executing",
                    current_action=action_id,
                    last_decision=decision,
                    last_response_text=codex_meta.get("response_text"),
                    thread_id=codex_meta.get("thread_id"),
                    turn_id=codex_meta.get("turn_id"),
                )
                result = self.session.execute_agent_action(action_id, reason)
                step_count = self.status()["step_count"] + 1
                result_summary = {
                    "mode": result["mode"],
                    "map": result["map"],
                    "menu": {
                        "active": result["menu"]["active"],
                        "selected_item_text": result["menu"]["selected_item_text"],
                    },
                    "dialogue": result["dialogue"]["visible_lines"],
                }
                self._update_status(
                    state="running",
                    step_count=step_count,
                    current_action=None,
                    last_result=result_summary,
                    last_error=None,
                )
                self._append_log(
                    {
                        "timestamp": _timestamp(),
                        "kind": "agent_controller_step",
                        "mode": mode,
                        "step": step_count,
                        "decision": decision,
                        "codex": codex_meta,
                        "result": result_summary,
                    }
                )
                if self._stop_event.wait(step_delay_ms / 1000):
                    break
        except Exception as exc:
            self._update_status(
                running=False,
                state="error",
                current_action=None,
                last_error=str(exc),
            )
            self._append_log(
                {
                    "timestamp": _timestamp(),
                    "kind": "agent_controller_error",
                    "message": str(exc),
                }
            )
        finally:
            if codex_client is not None:
                codex_client.close()
            with self._lock:
                if self._status["state"] not in {"error", "completed"}:
                    self._status["running"] = False
                    self._status["state"] = "stopped" if self._stop_event.is_set() else "idle"
                    self._status["current_action"] = None
                    self._status["updated_at"] = _timestamp()

    def _decide_action(
        self,
        context: dict[str, Any],
        *,
        mode: str,
        codex_client: CodexAppServerClient | None,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        if mode == "heuristic":
            return (
                {
                    "action": context["heuristic_next_action"]["action"],
                    "reason": context["heuristic_next_action"]["reason"],
                },
                {},
            )
        if codex_client is None:
            raise ValueError("codex mode requires an active Codex app-server client")
        result = codex_client.decide_action(context)
        return result["decision"], {
            "thread_id": result["thread_id"],
            "turn_id": result["turn_id"],
            "response_text": result["response_text"],
            "events": result["events"],
        }

    def _update_status(self, **changes: Any) -> None:
        with self._lock:
            self._status.update(changes)
            self._status["updated_at"] = _timestamp()

    def _append_log(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._recent_logs.append(record)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
