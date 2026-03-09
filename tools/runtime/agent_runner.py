from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from urllib import error, request

from .codex_client import CodexAppServerClient, CodexAppServerError


class RuntimeClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def get_json(self, path: str) -> dict[str, Any]:
        with request.urlopen(f"{self.base_url}{path}") as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with request.urlopen(http_request) as response:
            return json.loads(response.read().decode("utf-8"))


def choose_action(context: dict[str, Any], mode: str) -> tuple[str, str, str | None, str | None]:
    allowed_ids = {action["id"] for action in context["allowed_actions"]}

    if mode == "heuristic":
        heuristic = context["heuristic_next_action"]
        action_id = heuristic["action"]
        if action_id not in allowed_ids:
            action_id = "wait_short"
        return action_id, heuristic["reason"], heuristic.get("affordance_id"), heuristic.get("objective_id")

    raise ValueError(f"Unsupported agent mode '{mode}'")


def choose_action_with_codex(
    context: dict[str, Any],
    *,
    codex_client: CodexAppServerClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = codex_client.decide_action(context)
    return result["decision"], {
        "thread_id": result["thread_id"],
        "turn_id": result["turn_id"],
        "response_text": result["response_text"],
        "events": result["events"],
    }


def append_runner_log(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def step_once(
    client: RuntimeClient,
    *,
    mode: str,
    dry_run: bool,
    log_path: Path,
    codex_client: CodexAppServerClient | None = None,
) -> dict[str, Any]:
    context = client.get_json("/agent_context")
    codex_result: dict[str, Any] | None = None

    if mode == "codex":
        if codex_client is None:
            raise ValueError("codex mode requires a Codex app-server client")
        decision, codex_result = choose_action_with_codex(context, codex_client=codex_client)
        action_id = decision["action"]
        reason = decision["reason"]
        affordance_id = decision.get("affordance_id")
        objective_id = decision.get("objective_id")
    else:
        action_id, reason, affordance_id, objective_id = choose_action(context, mode)

    record: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "kind": "agent_runner_step",
        "mode": mode,
        "context_summary": {
            "observation": context["observation"],
            "heuristic_next_action": context["heuristic_next_action"],
            "allowed_action_ids": [action["id"] for action in context["allowed_actions"]],
        },
        "decision": {
            "action": action_id,
            "reason": reason,
        },
        "dry_run": dry_run,
    }
    if affordance_id:
        record["decision"]["affordance_id"] = affordance_id
    if objective_id:
        record["decision"]["objective_id"] = objective_id
    if codex_result is not None:
        record["codex"] = codex_result

    if dry_run:
        append_runner_log(log_path, record)
        return record

    tool_result = codex_result.get("tool_result") if codex_result is not None else None
    if tool_result and tool_result.get("success"):
        record["result"] = tool_result["result"]
    else:
        result = client.post_json(
            "/execute_action",
            {
                "action": action_id,
                "reason": reason,
                "affordance_id": affordance_id,
                "objective_id": objective_id,
            },
        )
        record["result"] = {
            "mode": result["mode"],
            "map": result["map"],
            "dialogue": result["dialogue"],
            "menu": {
                "active": result["menu"]["active"],
                "selected_item_text": result["menu"]["selected_item_text"],
                "visible_items": result["menu"]["visible_items"],
            },
            "events": result["events"]["recent"][-8:],
        }
    append_runner_log(log_path, record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an external agent loop against the pokered runtime service")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--mode", default="codex", choices=("codex", "heuristic"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-prompt", action="store_true")
    parser.add_argument("--log-path")
    parser.add_argument("--thread-state-path")
    parser.add_argument("--resume-thread", action="store_true")
    args = parser.parse_args()

    client = RuntimeClient(args.base_url)

    if args.print_prompt:
        context = client.get_json("/agent_context")
        print(context["prompt"])
        return

    log_path = Path(args.log_path) if args.log_path else Path(".runtime-traces") / "agent-runner" / "steps.jsonl"
    repo_root = Path(__file__).resolve().parents[2]
    thread_state_path = (
        Path(args.thread_state_path)
        if args.thread_state_path
        else Path(".runtime-traces") / "agent-runner" / "codex-thread.json"
    )

    try:
        with (
            CodexAppServerClient(
                cwd=repo_root,
                thread_state_path=thread_state_path,
                fresh_thread=not args.resume_thread,
                tool_handler=lambda tool_name, arguments: _handle_codex_runtime_tool(client, tool_name, arguments),
            )
            if args.mode == "codex"
            else _NullCodexClient()
        ) as codex_client:
            for step_index in range(args.steps):
                try:
                    record = step_once(
                        client,
                        mode=args.mode,
                        dry_run=args.dry_run,
                        log_path=log_path,
                        codex_client=codex_client,
                    )
                except error.HTTPError as exc:
                    detail = exc.read().decode("utf-8")
                    raise SystemExit(f"Runtime request failed: {exc.code} {detail}") from exc
                except error.URLError as exc:
                    raise SystemExit(f"Unable to reach runtime at {args.base_url}: {exc.reason}") from exc

                decision = record["decision"]
                output = {
                    "step": step_index + 1,
                    "action": decision["action"],
                    "reason": decision["reason"],
                    "mode": record.get("result", {}).get("mode"),
                }
                if "codex" in record:
                    output["thread_id"] = record["codex"]["thread_id"]
                    output["turn_id"] = record["codex"]["turn_id"]
                print(json.dumps(output))
    except CodexAppServerError as exc:
        raise SystemExit(f"Codex app-server failed: {exc}") from exc


class _NullCodexClient:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _handle_codex_runtime_tool(
    client: RuntimeClient,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    reason = arguments.get("reason")
    affordance_id = arguments.get("affordance_id")
    objective_id = arguments.get("objective_id")
    try:
        result = client.post_json(
            "/execute_action",
            {
                "action": tool_name,
                "reason": reason,
                "affordance_id": affordance_id,
                "objective_id": objective_id,
            },
        )
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        record = {
            "tool": tool_name,
            "action": tool_name,
            "reason": reason,
            "affordance_id": affordance_id,
            "objective_id": objective_id,
            "success": False,
            "error": f"{exc.code} {detail}",
        }
        return {
            "success": False,
            "record": record,
            "content_items": [
                {
                    "type": "inputText",
                    "text": json.dumps(record, ensure_ascii=True),
                }
            ],
        }

    result_summary = {
        "mode": result["mode"],
        "map": result["map"],
        "dialogue": result["dialogue"],
        "menu": {
            "active": result["menu"]["active"],
            "selected_item_text": result["menu"]["selected_item_text"],
            "visible_items": result["menu"]["visible_items"],
        },
        "events": result["events"]["recent"][-8:],
    }
    record = {
        "tool": tool_name,
        "action": result.get("agent_action", {}).get("action_id", tool_name),
        "reason": reason,
        "affordance_id": affordance_id,
        "objective_id": objective_id,
        "success": True,
        "result": result_summary,
    }
    return {
        "success": True,
        "record": record,
        "content_items": [
            {
                "type": "inputText",
                "text": json.dumps(record, ensure_ascii=True),
            }
        ],
    }


if __name__ == "__main__":
    main()
