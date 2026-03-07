from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TickRequest(BaseModel):
    frames: int = Field(default=1, ge=1, le=3600)


class ActionRequest(BaseModel):
    button: str
    hold_frames: int = Field(default=2, ge=1, le=120)
    settle_frames: int = Field(default=2, ge=0, le=600)


class StateSlotRequest(BaseModel):
    slot: str = Field(default="quick", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class SequenceStep(BaseModel):
    button: str
    hold_frames: int = Field(default=2, ge=1, le=120)
    settle_frames: int = Field(default=2, ge=0, le=600)


class SequenceRequest(BaseModel):
    steps: list[SequenceStep] = Field(min_length=1, max_length=128)


class RoutineRequest(BaseModel):
    name: Literal["open_menu", "close_menu", "advance_dialogue", "move_up", "move_down", "move_left", "move_right"]


class PlannerStepRequest(BaseModel):
    goal: Literal["progress"] = "progress"


class AgentActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=1000)


class AgentControlStartRequest(BaseModel):
    mode: Literal["codex", "heuristic"] = "codex"
    step_delay_ms: int = Field(default=500, ge=0, le=10000)
    max_steps: int | None = Field(default=None, ge=1, le=100000)
