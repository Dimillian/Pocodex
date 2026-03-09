from __future__ import annotations

from typing import Any

from .agent_context import build_agent_context
from .navigator import enrich_snapshot_with_navigation
from .runtime_core import RuntimeCore
from .runtime_memory import RuntimeMemory
from .telemetry import build_telemetry, derive_events
from .trace_recorder import TraceRecorder


class SnapshotService:
    def __init__(self, *, core: RuntimeCore, memory: RuntimeMemory, trace_recorder: TraceRecorder) -> None:
        self.core = core
        self.memory = memory
        self.trace_recorder = trace_recorder

    def status(self) -> dict[str, Any]:
        with self.core.lock:
            return {
                "rom": self.core.rom_name,
                "frame": self.core.frame_count,
                "running": self.core.running,
                "states_dir": str(self.core.states_dir),
                "trace_log_path": str(self.core.trace_log_path),
                "runtime_memory": {
                    "visited_maps": len(self.memory.progress_memory.get("visited_maps", {})),
                    "recent_targets": len(self.memory.progress_memory.get("recent_targets", [])),
                    "objective_history": len((self.memory.decision_state.get("objective") or {}).get("objective_history", [])),
                },
            }

    def telemetry(self) -> dict[str, Any]:
        with self.core.lock:
            return self.snapshot_unlocked()

    def snapshot_bundle(self) -> dict[str, Any]:
        with self.core.lock:
            telemetry = self.snapshot_unlocked()
            _, frame_png_base64 = self.core.frame_png_payload()
            return {
                "telemetry": telemetry,
                "frame_png_base64": frame_png_base64,
            }

    def agent_context(self) -> dict[str, Any]:
        snapshot = self.telemetry()
        traces = self.trace_recorder.recent_trace_entries(limit=12)
        return build_agent_context(
            snapshot,
            traces,
            decision_state=dict(self.memory.decision_state),
        )

    def build_snapshot_body_unlocked(self) -> dict[str, Any]:
        snapshot = build_telemetry(self.core.pyboy, self.core.telemetry_addresses)
        self.enrich_navigation(snapshot)
        return snapshot

    def snapshot_unlocked(
        self,
        *,
        suppress_derive: bool = False,
        extra_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        snapshot = self.build_snapshot_body_unlocked()
        events = [] if suppress_derive else derive_events(self.memory.last_observation, snapshot)
        if extra_events:
            events.extend(extra_events)
        self.memory.last_observation = snapshot
        self.memory.event_log.extend(events)
        snapshot["events"] = {
            "latest": events[-1] if events else None,
            "recent": list(self.memory.event_log),
        }
        return snapshot

    def enrich_navigation(self, snapshot: dict[str, Any]) -> None:
        enrich_snapshot_with_navigation(
            snapshot,
            map_catalog=self.core.map_catalog,
            navigation_state=self.memory.navigation_state,
            progress_memory=self.memory.progress_memory,
            decision_state=self.memory.decision_state,
        )
