"""Calibrate Braid heading → arena screen heading.

Displays a bright dot on each arena screen in sequence. Place a
Braid-trackable target (small white ball) directly in front of the dot and
press Enter. After all screens, the script fits braid_heading_offset_rad and
braid_heading_flip and optionally saves them to visual_stimuli.toml.

The calibration works by measuring the Braid *position* angle of the target
placed at each known screen direction, then fitting:

    world_heading = (braid_position_angle - offset) * (-1 if flip else 1)

This is the same transform applied to mean_heading at runtime, because Braid
heading (velocity direction) and position angle live in the same coordinate
frame.

Usage:
    uv run python -m src.tools.calibrate_heading
    uv run python -m src.tools.calibrate_heading --standalone   # no Braid
    uv run python -m src.tools.calibrate_heading --screens North South
"""

import argparse
import math
import re
import sys
import threading
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tools.calibrate_braid_ximea import parse_chunk


# World headings in radians — compass convention (North=0, East=π/2 …)
_WORLD_RAD: dict[str, float] = {
    "North": 0.0,
    "East": math.pi / 2,
    "South": math.pi,
    "West": 3 * math.pi / 2,
}

# Arena headings in Panda3D degrees for each compass direction
_HEADING_DEG: dict[str, float] = {
    "North": 0.0,
    "East": 90.0,
    "South": 180.0,
    "West": 270.0,
}

# Dot colors (yellow, cyan, green, orange)
_DOT_COLORS = [
    (1.0, 1.0, 0.0),
    (0.0, 1.0, 1.0),
    (0.0, 1.0, 0.0),
    (1.0, 0.5, 0.0),
]

DOT_VISUAL_ANGLE_DEG = 15.0  # diameter of the calibration dot
N_SAMPLES = 50  # Braid position samples to collect per screen
COLLECT_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
# Braid SSE position reader (background thread)
# ---------------------------------------------------------------------------


class _BraidPositionReader(threading.Thread):
    """Reads (x, y) object positions from the Braid SSE /events stream."""

    def __init__(self, base_url: str) -> None:
        super().__init__(daemon=True, name="braid-pos")
        self._url = f"{base_url.rstrip('/')}/events"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._samples: list[tuple[float, float]] = []

    def run(self) -> None:
        session = requests.Session()
        while not self._stop.is_set():
            try:
                resp = session.get(
                    self._url,
                    stream=True,
                    headers={"Accept": "text/event-stream"},
                    timeout=10,
                )
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
                    if self._stop.is_set():
                        return
                    try:
                        data = parse_chunk(chunk)
                        msg = data.get("msg", {})
                        obj = msg.get("Update") or msg.get("Birth")
                        if isinstance(obj, dict):
                            with self._lock:
                                self._samples.append((float(obj["x"]), float(obj["y"])))
                    except Exception:
                        pass
            except Exception:
                if not self._stop.is_set():
                    time.sleep(1)

    def stop(self) -> None:
        self._stop.set()

    def drain(self) -> None:
        with self._lock:
            self._samples.clear()

    def collect(
        self, n: int = N_SAMPLES, timeout_s: float = COLLECT_TIMEOUT_S
    ) -> list[tuple[float, float]]:
        """Drain buffer, then wait until n samples arrive (or timeout)."""
        self.drain()
        deadline = time.time() + timeout_s
        while True:
            with self._lock:
                count = len(self._samples)
            if count >= n or time.time() >= deadline:
                break
            time.sleep(0.05)
        with self._lock:
            return list(self._samples[:n])


# ---------------------------------------------------------------------------
# Calibration math
# ---------------------------------------------------------------------------


def _circ_mean(angles: list[float]) -> float:
    s = sum(math.sin(a) for a in angles)
    c = sum(math.cos(a) for a in angles)
    return math.atan2(s, c)


def _wrap(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


def fit_calibration(
    measurements: list[tuple[float, tuple[float, float]]],
) -> tuple[float, bool, float]:
    """Fit (offset_rad, flip, rms_deg) from calibration measurements.

    Args:
        measurements: list of (world_rad, (braid_x, braid_y)) — one per screen.

    Returns:
        offset_rad, flip, rms_deg for the best-fitting combination.
    """
    if len(measurements) < 2:
        raise ValueError("Need at least 2 calibration points.")

    # Must match tracking.py's heading formula, math.atan2(yvel, xvel), exactly:
    # atan2(y, x), not atan2(x, y). The two argument orders differ by a fixed
    # 90 degree rotation plus a reflection, so fitting the wrong one still looks
    # perfect at the 4 calibration points (offset/flip absorb the difference
    # there), but produces a heading-dependent error at runtime — 0 degrees at
    # headings resembling the calibration points, +-90 or 180 degrees
    # elsewhere — since only tracking.py's convention is ever evaluated outside
    # those 4 points. Do not search over argument order here.
    braid_angles = [math.atan2(by, bx) for _, (bx, by) in measurements]

    best: tuple[float, bool, float] | None = None
    for flip in (False, True):
        sign = -1.0 if flip else 1.0
        # world = (braid_angle - offset) * sign
        # → offset = braid_angle - world * sign
        offsets = [
            _wrap(ba - world * sign)
            for (world, _), ba in zip(measurements, braid_angles)
        ]
        offset = _circ_mean(offsets)
        residuals = [
            _wrap(_wrap((ba - offset) * sign) - world)
            for (world, _), ba in zip(measurements, braid_angles)
        ]
        rms_deg = math.degrees(math.sqrt(sum(r**2 for r in residuals) / len(residuals)))
        if best is None or rms_deg < best[2]:
            best = (offset, flip, rms_deg)

    assert best is not None
    return best


# ---------------------------------------------------------------------------
# Panda3D calibration display
# ---------------------------------------------------------------------------


def _build_dot(heading_deg: float, dist_cm: float, color_rgb: tuple) -> object:
    """Return a GeomNode for a unit disk; caller sets scale and position."""
    from panda3d.core import (
        Geom,
        GeomNode,
        GeomTriangles,
        GeomVertexData,
        GeomVertexFormat,
        GeomVertexWriter,
    )

    r, g, b = color_rgb
    n = 48
    vformat = GeomVertexFormat.getV3c4()
    vdata = GeomVertexData("dot", vformat, Geom.UHStatic)
    vdata.setNumRows(n + 1)
    vtx = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    vtx.addData3(0, 0, 0)
    col.addData4(r, g, b, 1.0)
    for i in range(n):
        a = 2.0 * math.pi * i / n
        vtx.addData3(math.cos(a), 0.0, math.sin(a))
        col.addData4(r, g, b, 1.0)
    tris = GeomTriangles(Geom.UHStatic)
    for i in range(n):
        tris.addVertices(0, i + 1, (i + 1) % n + 1)
    tris.closePrimitive()
    geom = Geom(vdata)
    geom.addPrimitive(tris)
    node = GeomNode("dot")
    node.addGeom(geom)
    return node


def _attach_dot(render, heading_deg: float, dist_cm: float, color_rgb: tuple):
    """Create and attach a billboard dot at the given heading."""
    h_rad = math.radians(heading_deg)
    x = dist_cm * math.sin(h_rad)
    y = dist_cm * math.cos(h_rad)
    radius = dist_cm * math.tan(math.radians(DOT_VISUAL_ANGLE_DEG / 2.0))

    node = _build_dot(heading_deg, dist_cm, color_rgb)
    np = render.attachNewNode(node)
    np.setPos(x, y, 0.0)
    np.setScale(radius)
    np.setTwoSided(True)
    np.setBillboardPointWorld()
    return np


class HeadingCalibrationApp:
    """Panda3D app that drives the per-screen calibration sequence."""

    # Internal states
    _WAITING = "waiting"  # showing dot, waiting for Enter
    _COLLECTING = "collecting"  # background thread gathering Braid data
    _DONE = "done"  # all screens collected

    def __init__(
        self,
        screen_mapping: list[str],
        viewing_distance_cm: float,
        window_x_offset: int,
        braid_reader: "_BraidPositionReader | None",
        standalone: bool = False,
    ) -> None:
        self._screens = screen_mapping
        self._dist = viewing_distance_cm
        self._braid = braid_reader
        self._standalone = standalone

        # Build scene
        from src.visual.scene import ArenaScene

        self._scene = ArenaScene(
            viewing_distance_cm=viewing_distance_cm,
            camera_headings=[_HEADING_DEG[d] for d in screen_mapping],
            window_x_offset=window_x_offset,
            standalone=standalone,
        )

        self._step = 0
        self._dot = None
        self._measurements: list[tuple[float, tuple[float, float]]] = []
        self._state = self._WAITING

        # Collect-thread communication (read only in main thread except _collect_*)
        self._collect_done = threading.Event()
        self._collect_result: tuple[float, tuple[float, float]] | None = None

        # Input: set event each time user presses Enter in the terminal
        # (avoids needing Panda3D window focus)
        self._input_event = threading.Event()
        self._scene.accept("enter", self._input_event.set)
        threading.Thread(target=self._terminal_input_loop, daemon=True).start()

    def _terminal_input_loop(self) -> None:
        """Set _input_event on each Enter press in the terminal."""
        while self._state != self._DONE:
            try:
                input()
            except EOFError:
                break
            self._input_event.set()

    # ------------------------------------------------------------------
    def run(self) -> list[tuple[float, tuple[float, float]]]:
        print("\n" + "=" * 60)
        print("BRAID → ARENA HEADING CALIBRATION")
        print("=" * 60)
        if self._standalone:
            print("Standalone mode — synthetic Braid data will be injected.")
        print(
            f"Screens: {', '.join(self._screens)}\n"
            "For each screen: place a Braid-trackable target in front of\n"
            "the bright dot, then press Enter (in this terminal).\n"
        )
        self._show_step(0)

        self._scene.taskMgr.add(self._tick, "cal_tick")
        self._scene.run()

        return self._measurements

    # ------------------------------------------------------------------
    # Panda3D task (main thread)
    # ------------------------------------------------------------------

    def _tick(self, task):
        from direct.task import Task

        if self._state == self._DONE:
            self._scene.taskMgr.stop()
            return Task.done

        if self._state == self._WAITING and self._input_event.is_set():
            self._input_event.clear()
            if self._standalone:
                self._inject_fake()
            else:
                self._state = self._COLLECTING
                self._collect_done.clear()
                threading.Thread(target=self._collect_thread, daemon=True).start()

        elif self._state == self._COLLECTING and self._collect_done.is_set():
            # Back in the main thread — safe to modify scene graph
            result = self._collect_result
            if result is not None:
                self._measurements.append(result)
            self._step += 1
            self._show_step(self._step)

        return Task.cont

    # ------------------------------------------------------------------
    # Step display (main thread only)
    # ------------------------------------------------------------------

    def _show_step(self, step: int) -> None:
        if self._dot is not None:
            self._dot.removeNode()
            self._dot = None

        if step >= len(self._screens):
            self._state = self._DONE
            return

        screen = self._screens[step]
        color = _DOT_COLORS[step % len(_DOT_COLORS)]
        self._dot = _attach_dot(
            self._scene.render,
            _HEADING_DEG[screen],
            self._dist * 0.8,  # 20 % inside cylinder so depth test passes
            color,
        )
        self._state = self._WAITING
        print(
            f"[{step + 1}/{len(self._screens)}] {screen} screen  "
            f"(heading {_HEADING_DEG[screen]:.0f}°)  "
            f"color={color}\n"
            "Place target in front of dot, then press Enter... ",
            end="",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Collect thread (background)
    # ------------------------------------------------------------------

    def _collect_thread(self) -> None:
        assert self._braid is not None
        samples = self._braid.collect()

        if len(samples) < 2:
            print(
                f"\nWARNING: only {len(samples)} Braid sample(s) received "
                "(is Braid tracking an object?)."
            )
            if len(samples) == 0:
                print(f"Skipping {self._screens[self._step]}.")
                self._collect_result = None
                self._collect_done.set()
                return

        mean_x = sum(p[0] for p in samples) / len(samples)
        mean_y = sum(p[1] for p in samples) / len(samples)
        world_rad = _WORLD_RAD[self._screens[self._step]]
        self._collect_result = (world_rad, (mean_x, mean_y))

        print(
            f"{len(samples)} samples → mean ({mean_x:.4f}, {mean_y:.4f}) m  "
            f"[atan2(y,x) = {math.degrees(math.atan2(mean_y, mean_x)):.1f}°]"
        )
        self._collect_done.set()

    # ------------------------------------------------------------------
    # Standalone fake data
    # ------------------------------------------------------------------

    def _inject_fake(self) -> None:
        screen = self._screens[self._step]
        world_rad = _WORLD_RAD[screen]
        # Synthetic: Braid coords = world coords (offset=0, flip=False)
        bx = math.sin(world_rad) * 0.25
        by = math.cos(world_rad) * 0.25
        self._measurements.append((world_rad, (bx, by)))
        print("(fake data injected)")
        self._step += 1
        self._show_step(self._step)


# ---------------------------------------------------------------------------
# Config save (regex replacement to preserve comments)
# ---------------------------------------------------------------------------


def _save_to_config(vs_path: Path, offset_rad: float, flip: bool) -> None:
    text = vs_path.read_text()

    def replace_key(src: str, key: str, value: str) -> str:
        pattern = rf"^({re.escape(key)}\s*=\s*).*$"
        new, n = re.subn(pattern, rf"\g<1>{value}", src, flags=re.MULTILINE)
        if n == 0:
            # Key absent — insert after [visual_stimuli.arena] header
            new = re.sub(
                r"(\[visual_stimuli\.arena\][^\[]*)",
                rf"\1{key} = {value}\n",
                new,
                count=1,
                flags=re.DOTALL,
            )
        return new

    text = replace_key(text, "braid_heading_offset_rad", f"{offset_rad:.6f}")
    text = replace_key(text, "braid_heading_flip", "true" if flip else "false")
    vs_path.write_text(text)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate Braid heading → arena screen heading"
    )
    parser.add_argument("--config", default="configs/config.toml")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Run without Braid (injects synthetic data for UI testing)",
    )
    parser.add_argument(
        "--screens",
        nargs="+",
        choices=["North", "East", "South", "West"],
        metavar="DIR",
        help="Calibrate only these screens (default: full screen_mapping order)",
    )
    args = parser.parse_args()

    # --- Load config -------------------------------------------------------
    import tomllib

    cfg_path = Path(args.config)
    with cfg_path.open("rb") as f:
        cfg = tomllib.load(f)

    vs_path_str = cfg.get("visual_stimuli", {}).get(
        "config_file", "configs/visual_stimuli.toml"
    )
    vs_path = Path(vs_path_str)
    with (vs_path if vs_path.exists() else cfg_path).open("rb") as f:
        vs_cfg = tomllib.load(f)

    arena_cfg = vs_cfg.get("visual_stimuli", {}).get("arena", {})
    screen_mapping: list[str] = arena_cfg.get(
        "screen_mapping", ["North", "East", "South", "West"]
    )
    viewing_distance_cm: float = arena_cfg.get("viewing_distance_cm", 25.0)
    window_x_offset: int = arena_cfg.get("window_x_offset", 3840)

    braid_cfg = cfg.get("braid_publisher", {})
    braid_url = (
        f"http://{braid_cfg.get('host', '127.0.0.1')}"
        f":{braid_cfg.get('events_port', 8397)}"
    )

    screens = args.screens if args.screens else screen_mapping

    # --- Start Braid reader ------------------------------------------------
    braid_reader: _BraidPositionReader | None = None
    if not args.standalone:
        print(f"Connecting to Braid at {braid_url} ... ", end="", flush=True)
        try:
            import requests as _req

            _req.get(braid_url, timeout=3).raise_for_status()
        except Exception as e:
            print(
                f"FAILED\nCannot reach Braid: {e}\nRun with --standalone for UI testing."
            )
            sys.exit(1)
        braid_reader = _BraidPositionReader(braid_url)
        braid_reader.start()
        time.sleep(0.3)
        print("ok")

    # --- Run calibration app -----------------------------------------------
    app = HeadingCalibrationApp(
        screen_mapping=screens,
        viewing_distance_cm=viewing_distance_cm,
        window_x_offset=window_x_offset,
        braid_reader=braid_reader,
        standalone=args.standalone,
    )
    measurements = app.run()
    if braid_reader:
        braid_reader.stop()
    app._scene.destroy()

    # --- Fit ---------------------------------------------------------------
    if len(measurements) < 2:
        print(
            f"\nOnly {len(measurements)} point(s) collected — need ≥ 2. Nothing saved."
        )
        sys.exit(1)

    offset_rad, flip, rms_deg = fit_calibration(measurements)

    print("\n" + "=" * 60)
    print("RESULT")
    print("=" * 60)
    print(
        f"  braid_heading_offset_rad = {offset_rad:.6f}  ({math.degrees(offset_rad):.2f}°)"
    )
    print(f"  braid_heading_flip       = {flip}")
    print(f"  RMS residual             = {rms_deg:.2f}°")
    if rms_deg > 10.0:
        print(
            "  ⚠  High residual — check that the target was centred in front\n"
            "     of each screen and that Braid was tracking it steadily."
        )
    print()

    # --- Save? -------------------------------------------------------------
    save = input(f"Save to {vs_path}? [y/N] ").strip().lower()
    if save == "y":
        _save_to_config(vs_path, offset_rad, flip)
        print("Saved ✓")
    else:
        print(
            "Not saved. Add these lines manually under [visual_stimuli.arena]:\n"
            f"  braid_heading_offset_rad = {offset_rad:.6f}\n"
            f"  braid_heading_flip = {'true' if flip else 'false'}"
        )


if __name__ == "__main__":
    main()
