from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi import status
from fastapi.responses import FileResponse
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .agent_service import AgentController
from .session import RuntimeSession
from .state_models import (
    ActionRequest,
    AgentActionRequest,
    AgentControlStartRequest,
    AgentPromptRequest,
    PlannerStepRequest,
    RoutineRequest,
    SequenceRequest,
    StateSlotRequest,
    TickRequest,
)

SESSION: RuntimeSession | None = None
AGENT_CONTROLLER: AgentController | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        controller = AGENT_CONTROLLER
        if controller is not None:
            controller.shutdown()
        session = SESSION
        if session is not None:
            session.stop()


app = FastAPI(title="pokered runtime", lifespan=lifespan)


def get_session() -> RuntimeSession:
    if SESSION is None:
        raise RuntimeError("Runtime session has not been initialized")
    return SESSION


def get_agent_controller() -> AgentController:
    if AGENT_CONTROLLER is None:
        raise RuntimeError("Agent controller has not been initialized")
    return AGENT_CONTROLLER


@app.get("/health")
def health() -> dict:
    session = get_session()
    status = session.status()
    return {"ok": True, **status}


@app.get("/")
def root() -> FileResponse:
    return FileResponse(Path(__file__).with_name("static").joinpath("index.html"))


@app.post("/pause")
def pause() -> dict:
    return get_session().pause()


@app.post("/resume")
def resume() -> dict:
    return get_session().resume()


@app.get("/states")
def list_states() -> dict:
    return get_session().list_states()


@app.post("/save_state")
def save_state(request: StateSlotRequest) -> dict:
    return get_session().save_state(request.slot)


@app.post("/load_state")
def load_state(request: StateSlotRequest) -> dict:
    try:
        return get_session().load_state(request.slot)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@app.post("/reset_runtime_memory")
def reset_runtime_memory() -> dict:
    return get_session().reset_runtime_memory()


@app.get("/telemetry")
def telemetry() -> dict:
    return get_session().telemetry()


@app.get("/snapshot")
def snapshot() -> dict:
    return get_session().snapshot_bundle()


@app.get("/agent_context")
def agent_context() -> dict:
    return get_session().agent_context()


@app.get("/agent/status")
def agent_status() -> dict:
    return get_agent_controller().status()


@app.post("/agent/start")
def agent_start(request: AgentControlStartRequest) -> dict:
    try:
        return get_agent_controller().start(
            mode=request.mode,
            step_delay_ms=request.step_delay_ms,
            max_steps=request.max_steps,
            fresh_thread=request.fresh_thread,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/agent/stop")
def agent_stop() -> dict:
    return get_agent_controller().stop()


@app.post("/agent/prompt")
def agent_prompt(request: AgentPromptRequest) -> dict:
    try:
        return get_agent_controller().queue_prompt(request.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/agent/prompt/clear")
def agent_prompt_clear() -> dict:
    return get_agent_controller().clear_prompt()


@app.post("/execute_action")
def execute_action(request: AgentActionRequest) -> dict:
    try:
        return get_session().execute_agent_action(
            request.action,
            request.reason,
            affordance_id=request.affordance_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/frame")
def frame() -> Response:
    return Response(get_session().frame_png(), media_type="image/png")


@app.post("/tick")
def tick(request: TickRequest) -> dict:
    return get_session().tick(request.frames)


@app.post("/action")
def action(request: ActionRequest) -> dict:
    try:
        return get_session().tap(
            request.button,
            hold_frames=request.hold_frames,
            settle_frames=request.settle_frames,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sequence")
def sequence(request: SequenceRequest) -> dict:
    try:
        return get_session().sequence([step.model_dump() for step in request.steps])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/routine")
def routine(request: RoutineRequest) -> dict:
    try:
        return get_session().run_routine(request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/traces")
def traces(limit: int = 50) -> dict:
    return get_session().recent_traces(limit=limit)


@app.post("/planner_step")
def planner_step(request: PlannerStepRequest) -> dict:
    try:
        return get_session().planner_step(goal=request.goal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the pokered runtime service")
    parser.add_argument("--rom", default="blue", choices=("blue", "red", "blue-debug"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--boot-frames", type=int, default=0)
    parser.add_argument("--auto-run", action="store_true")
    args = parser.parse_args()

    global SESSION
    SESSION = RuntimeSession(
        repo_root=Path(__file__).resolve().parents[2],
        rom_name=args.rom,
        boot_frames=args.boot_frames,
        auto_run=args.auto_run,
    )
    global AGENT_CONTROLLER
    AGENT_CONTROLLER = AgentController(session=SESSION, repo_root=SESSION.repo_root)
    app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")), name="static")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
