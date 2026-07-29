"""Tests for the camera preflight check documented in troubleshooting.md.

This is the first thing a user runs when the camera isn't recording, so each
check must report a concrete next action rather than just a boolean.
"""

import os
import socket

import pytest

from src.processes.camera import (
    CheckResult,
    _check_trigger_port,
    check_camera_prerequisites,
)

CONFIG = "configs/config.example.toml"

# root bypasses the permission bits these tests rely on, which would make the
# assertions vacuously pass in a container rather than fail loudly.
skip_if_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores directory write permissions"
)


def test_reports_every_documented_check():
    results = check_camera_prerequisites(CONFIG)

    assert set(results) == {
        "camera_binary",
        "ffmpeg",
        "save_folder_writable",
        "trigger_port",
    }
    assert all(isinstance(r, CheckResult) for r in results.values())


def test_missing_ffmpeg_reports_how_to_install_it(monkeypatch):
    monkeypatch.setattr("src.processes.camera.shutil.which", lambda name: None)

    result = check_camera_prerequisites(CONFIG)["ffmpeg"]

    assert result.ok is False
    assert "apt" in result.detail, f"expected an install hint, got {result.detail!r}"


def test_present_ffmpeg_reports_its_path(monkeypatch):
    monkeypatch.setattr("src.processes.camera.shutil.which", lambda name: "/usr/bin/ffmpeg")

    result = check_camera_prerequisites(CONFIG)["ffmpeg"]

    assert result.ok is True
    assert "/usr/bin/ffmpeg" in result.detail


def test_unbuilt_binary_reports_the_cargo_command(monkeypatch):
    monkeypatch.setattr(
        "src.processes.camera.find_camera_binary",
        lambda: (_ for _ in ()).throw(FileNotFoundError("nope")),
    )

    result = check_camera_prerequisites(CONFIG)["camera_binary"]

    assert result.ok is False
    assert "cargo build --release" in result.detail


@skip_if_root
def test_unwritable_save_folder_is_reported(tmp_path, monkeypatch):
    unwritable = tmp_path / "readonly"
    unwritable.mkdir(mode=0o500)

    result = check_camera_prerequisites(CONFIG, save_folder=str(unwritable))[
        "save_folder_writable"
    ]

    assert result.ok is False
    assert str(unwritable) in result.detail


def test_writable_save_folder_is_reported_ok(tmp_path):
    result = check_camera_prerequisites(CONFIG, save_folder=str(tmp_path))[
        "save_folder_writable"
    ]

    assert result.ok is True
    assert str(tmp_path) in result.detail


def test_save_folder_check_passes_for_a_folder_that_does_not_exist_yet(tmp_path):
    """The folder not existing yet is normal -- the camera creates it on
    start. What matters is whether it *could* be created and written to."""
    target = tmp_path / "videos" / "not_yet_there"

    result = check_camera_prerequisites(CONFIG, save_folder=str(target))[
        "save_folder_writable"
    ]

    assert result.ok is True


def test_save_folder_check_has_no_side_effects(tmp_path):
    """A preflight check must not mutate the filesystem. The previous version
    called os.makedirs() and left a stray camera_videos/ in the repo root
    every time the docs' example command was run."""
    target = tmp_path / "should_not_be_created"

    check_camera_prerequisites(CONFIG, save_folder=str(target))

    assert not target.exists()


def _free_port() -> int:
    """An ephemeral port with nothing on it.

    The configured trigger_port can legitimately be in use on a machine that
    has a stack running, so these tests must never depend on it being free.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_trigger_port_reports_not_running_when_nothing_is_bound():
    """A free trigger port isn't an error -- it means the experiment isn't
    running yet. The wording must not read as a failure to fix."""
    result = _check_trigger_port(_free_port())

    assert result.ok is False
    assert "not running" in result.detail.lower()


def test_trigger_port_detects_a_bound_publisher():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        result = _check_trigger_port(port)
    finally:
        listener.close()

    assert result.ok is True
    assert str(port) in result.detail


def test_results_render_readably_when_printed():
    """`print(check_camera_prerequisites(...))` is the documented usage, so
    the repr has to be legible to a novice, not a wall of object addresses."""
    rendered = str(check_camera_prerequisites(CONFIG))

    assert "object at 0x" not in rendered
    assert "camera_binary" in rendered


@skip_if_root
@pytest.mark.parametrize("name", ["camera_binary", "ffmpeg", "save_folder_writable"])
def test_every_failing_check_says_what_to_do(name, monkeypatch, tmp_path):
    """Regression guard for the whole point of this function: a novice must
    never get a bare False."""
    monkeypatch.setattr("src.processes.camera.shutil.which", lambda n: None)
    monkeypatch.setattr(
        "src.processes.camera.find_camera_binary",
        lambda: (_ for _ in ()).throw(FileNotFoundError("nope")),
    )
    unwritable = tmp_path / "ro"
    unwritable.mkdir(mode=0o500)

    result = check_camera_prerequisites(CONFIG, save_folder=str(unwritable))[name]

    assert result.ok is False
    assert len(result.detail) > 20, f"{name} detail is too terse: {result.detail!r}"
