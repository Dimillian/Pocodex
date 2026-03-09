from __future__ import annotations

from pathlib import Path
from typing import Any

from .action_executor import ActionExecutor
from .objective_runner import ObjectiveRunner
from .runtime_core import RuntimeCore
from .runtime_memory import RuntimeMemory
from .snapshot_service import SnapshotService
from .trace_recorder import TraceRecorder


class RuntimeApp:
    def __init__(
        self,
        repo_root: Path,
        rom_name: str,
        boot_frames: int = 0,
        auto_run: bool = False,
    ) -> None:
        self.core = RuntimeCore(
            repo_root=repo_root,
            rom_name=rom_name,
            boot_frames=boot_frames,
            auto_run=auto_run,
        )
        self.memory = RuntimeMemory()
        self.trace_recorder = TraceRecorder(trace_log_path=self.core.trace_log_path)
        self.snapshot_service = SnapshotService(core=self.core, memory=self.memory, trace_recorder=self.trace_recorder)
        self.action_executor = ActionExecutor(
            core=self.core,
            memory=self.memory,
            snapshot_service=self.snapshot_service,
            trace_recorder=self.trace_recorder,
        )
        self.objective_runner = ObjectiveRunner(
            core=self.core,
            memory=self.memory,
            snapshot_service=self.snapshot_service,
            trace_recorder=self.trace_recorder,
            action_executor=self.action_executor,
        )

    @property
    def repo_root(self) -> Path:
        return self.core.repo_root

    @property
    def rom_name(self) -> str:
        return self.core.rom_name

    def stop(self) -> None:
        self.core.stop()

    def status(self) -> dict[str, Any]:
        return self.snapshot_service.status()

    def pause(self) -> dict[str, Any]:
        self.core.pause()
        return self.status()

    def resume(self) -> dict[str, Any]:
        self.core.resume()
        return self.status()

    def telemetry(self) -> dict[str, Any]:
        return self.snapshot_service.telemetry()

    def snapshot_bundle(self) -> dict[str, Any]:
        return self.snapshot_service.snapshot_bundle()

    def recent_traces(self, limit: int = 50) -> dict[str, Any]:
        return self.trace_recorder.recent_traces(rom_name=self.rom_name, limit=limit)

    def agent_context(self) -> dict[str, Any]:
        return self.snapshot_service.agent_context()

    def tick(self, frames: int = 1) -> dict[str, Any]:
        return self.action_executor.tick(frames)

    def tap(
        self,
        button: str,
        hold_frames: int = 2,
        settle_frames: int = 2,
        *,
        record_trace: bool = True,
    ) -> dict[str, Any]:
        return self.action_executor.tap(
            button,
            hold_frames=hold_frames,
            settle_frames=settle_frames,
            record_trace=record_trace,
        )

    def sequence(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        return self.action_executor.sequence(steps)

    def run_routine(self, name: str) -> dict[str, Any]:
        return self.action_executor.run_routine(name)

    def list_states(self) -> dict[str, Any]:
        return self.action_executor.list_states()

    def save_state(self, slot: str = "quick") -> dict[str, Any]:
        return self.action_executor.save_state(slot)

    def load_state(self, slot: str = "quick") -> dict[str, Any]:
        return self.action_executor.load_state(slot)

    def reset_runtime_memory(self) -> dict[str, Any]:
        return self.action_executor.reset_runtime_memory()

    def follow_objective(self, max_steps: int = 6, *, objective_id: str | None = None) -> dict[str, Any]:
        return self.objective_runner.follow_objective(max_steps=max_steps, objective_id=objective_id)

    def follow_target(self, max_steps: int = 6, *, affordance_id: str | None = None) -> dict[str, Any]:
        return self.objective_runner.follow_target(max_steps=max_steps, affordance_id=affordance_id)

    def follow_interaction(self, max_steps: int = 6) -> dict[str, Any]:
        return self.objective_runner.follow_interaction(max_steps=max_steps)

    def planner_step(self, goal: str = "progress") -> dict[str, Any]:
        return self.objective_runner.planner_step(goal)

    def execute_agent_action(
        self,
        action_id: str,
        reason: str | None = None,
        *,
        affordance_id: str | None = None,
        objective_id: str | None = None,
    ) -> dict[str, Any]:
        return self.objective_runner.execute_agent_action(
            action_id,
            reason,
            affordance_id=affordance_id,
            objective_id=objective_id,
        )

    def frame_png(self) -> bytes:
        return self.core.frame_png()
