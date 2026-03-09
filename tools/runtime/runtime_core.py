from __future__ import annotations

import base64
import io
import logging
import os
import warnings
from pathlib import Path
from threading import Event, RLock, Thread
import time

from .map_data import load_map_catalog
from .symbols import load_symbol_table
from .telemetry import TelemetryAddresses

LOGGER = logging.getLogger(__name__)

ROMS = {
    "blue": ("pokeblue.gbc", "pokeblue.sym"),
    "red": ("pokered.gbc", "pokered.sym"),
    "blue-debug": ("pokeblue_debug.gbc", "pokeblue_debug.sym"),
}


class RuntimeCore:
    def __init__(
        self,
        *,
        repo_root: Path,
        rom_name: str,
        boot_frames: int = 0,
        auto_run: bool = False,
    ) -> None:
        if rom_name not in ROMS:
            supported = ", ".join(sorted(ROMS))
            raise ValueError(f"Unknown ROM '{rom_name}'. Expected one of: {supported}")

        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        warnings.filterwarnings("ignore", message="Using SDL2 binaries from pysdl2-dll.*")
        logging.getLogger("pyboy").setLevel(logging.ERROR)
        logging.getLogger("pyboy.pyboy").setLevel(logging.ERROR)
        from pyboy import PyBoy

        rom_filename, sym_filename = ROMS[rom_name]
        self.repo_root = repo_root
        self.rom_name = rom_name
        self.rom_path = repo_root / rom_filename
        self.sym_path = repo_root / sym_filename
        self.states_dir = repo_root / ".runtime-state" / rom_name
        self.traces_dir = repo_root / ".runtime-traces" / rom_name
        self.trace_log_path = self.traces_dir / "actions.jsonl"
        self.lock = RLock()
        self._stop_event = Event()
        self._run_event = Event()
        self._cached_frame_png: bytes | None = None
        self._cached_frame_base64: str | None = None
        self._cached_frame_count: int | None = None

        if not self.rom_path.exists():
            raise FileNotFoundError(f"Missing ROM: {self.rom_path}")
        if not self.sym_path.exists():
            raise FileNotFoundError(f"Missing symbol map: {self.sym_path}")
        self.states_dir.mkdir(parents=True, exist_ok=True)
        self.traces_dir.mkdir(parents=True, exist_ok=True)

        self.symbols = load_symbol_table(self.sym_path)
        self.telemetry_addresses = TelemetryAddresses.from_symbols(self.symbols)
        self.map_catalog = load_map_catalog(self.repo_root)
        self.pyboy = PyBoy(
            str(self.rom_path),
            window="null",
            sound_emulated=False,
            symbols=None,
            log_level="ERROR",
        )
        if boot_frames:
            for _ in range(boot_frames):
                self.pyboy.tick()

        self._runner = Thread(target=self._run_loop, name="pokered-runtime", daemon=True)
        self._runner.start()
        if auto_run:
            self.resume()

        LOGGER.info("Booted runtime for %s", self.rom_path.name)

    @property
    def frame_count(self) -> int:
        return self.pyboy.frame_count

    @property
    def running(self) -> bool:
        return self._run_event.is_set()

    def stop(self) -> None:
        self._stop_event.set()
        self._run_event.set()
        if self._runner.is_alive():
            self._runner.join(timeout=1)
        with self.lock:
            self.pyboy.stop(save=False)

    def pause(self) -> None:
        self._run_event.clear()

    def resume(self) -> None:
        self._run_event.set()

    def tick_frames(self, frames: int) -> None:
        with self.lock:
            for _ in range(frames):
                self.pyboy.tick()

    def button_press(self, button_name: str) -> None:
        with self.lock:
            self.pyboy.button_press(button_name)

    def button_release(self, button_name: str) -> None:
        with self.lock:
            self.pyboy.button_release(button_name)

    def save_state_file(self, path: Path) -> None:
        with self.lock:
            with path.open("wb") as handle:
                self.pyboy.save_state(handle)

    def load_state_file(self, path: Path) -> None:
        with self.lock:
            with path.open("rb") as handle:
                self.pyboy.load_state(handle)
            self.invalidate_frame_cache()

    def capture_state_bytes(self) -> bytes:
        with self.lock:
            buffer = io.BytesIO()
            self.pyboy.save_state(buffer)
            return buffer.getvalue()

    def restore_state_bytes(self, state_bytes: bytes) -> None:
        with self.lock:
            self.pyboy.load_state(io.BytesIO(state_bytes))
            self.invalidate_frame_cache()

    def invalidate_frame_cache(self) -> None:
        self._cached_frame_png = None
        self._cached_frame_base64 = None
        self._cached_frame_count = None

    def frame_png(self) -> bytes:
        frame_png, _ = self.frame_png_payload()
        return frame_png

    def frame_png_payload(self) -> tuple[bytes, str]:
        with self.lock:
            frame_count = self.pyboy.frame_count
            if (
                self._cached_frame_png is not None
                and self._cached_frame_base64 is not None
                and self._cached_frame_count == frame_count
            ):
                return self._cached_frame_png, self._cached_frame_base64

            buffer = io.BytesIO()
            self.pyboy.screen.image.save(buffer, format="PNG")
            frame_png = buffer.getvalue()
            frame_base64 = base64.b64encode(frame_png).decode("ascii")
            self._cached_frame_png = frame_png
            self._cached_frame_base64 = frame_base64
            self._cached_frame_count = frame_count
            return frame_png, frame_base64

    def _run_loop(self) -> None:
        frame_duration = 1 / 60
        next_tick = time.perf_counter()
        while not self._stop_event.is_set():
            if not self._run_event.is_set():
                time.sleep(0.01)
                next_tick = time.perf_counter()
                continue

            now = time.perf_counter()
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.01))
                continue

            with self.lock:
                self.pyboy.tick()
            next_tick += frame_duration
