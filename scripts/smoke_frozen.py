#!/usr/bin/env python3
"""Smoke-test a packaged bundle: does the frozen app actually serve a review?

``ptr --help`` only proves the bootloader found argparse, so this drives the thing
that matters — the app opening a real ``.h5`` file and serving the review page — with
nothing but a synthetic file and a few seconds of patience.

Usage:  python scripts/smoke_frozen.py path/to/dist/ptr/ptr.exe
        python scripts/smoke_frozen.py "dist/PTR-MS Review.app/Contents/MacOS/ptr"
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import h5py
import numpy as np

LAUNCH_TIMEOUT = 30  # seconds for the process to print its URL
OPEN_TIMEOUT = 120  # seconds for the app to build the review payload


def make_h5(path: Path) -> Path:
    """A tiny but honest IoniTOF-shaped file, and the config that belongs to it.

    The config is written beside it on purpose: reopening a file that already has a
    config is the path a reviewer hits every day, and it keeps this check out of the
    detection pipeline, which unit tests cover.
    """
    ncyc, nmz = 24, 6
    masses = np.array([19.036, 21.022, 31.0, 33.0, 37.0, 45.0])
    trace = np.zeros((ncyc, nmz))
    trace[:, 1] = 1e6  # the reagent ion
    trace[:, 2] = 5e3
    trace[8:, 2] = 4e4  # an analyte that arrives halfway through
    with h5py.File(path, "w") as h5:
        h5.create_dataset("SPECdata/Intensities", data=trace)
        h5.create_dataset("SPECdata/AverageSpec", data=trace.mean(axis=0))
        h5.create_dataset(
            "SPECdata/PCTime", data=np.arange(ncyc, dtype=np.float64)[:, None]
        )
        h5.attrs["InstrumentType"] = "IoniTof"
        h5.attrs["Single Spec Duration (ms)"] = [1000.0]
        h5.attrs["UTC_Offset"] = [0.0]
        h5.attrs["MassAxisType"] = "mz"
        h5.create_group("InstrumentSection")
        cal = h5.create_group("CALdata")
        cal.create_dataset("Mass_Use", data=np.ones(nmz, dtype=bool))
        cal.create_dataset("Mass_MZ", data=masses)
        cal.create_dataset(
            "Mapping", data=np.column_stack([masses, 100.0 * np.sqrt(masses) + 10.0])
        )
    config = path.with_suffix(".json")
    config.write_text(
        json.dumps(
            {
                "peaks": [{"mz": 31.0, "label": "test analyte"}],
                "ranges": [{"label": "sample_01", "start": 9, "end": 24, "unit": "cycle"}],
                "viz": {"x_axis_unit": "cycle"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.read()


def post(url: str, body=None) -> int:
    data = json.dumps(body or {}).encode("utf-8")
    request = urllib.request.Request(
        url, data, {"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def await_url(proc, timeout):
    """Block until the app reports its address on stderr. Returns (url, lines)."""
    deadline = time.monotonic() + timeout
    lines = []
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=5)
            return None, lines + [out, err]
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.05)
            continue
        lines.append(line.rstrip())
        match = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
        if match:
            return f"http://127.0.0.1:{match.group(1)}/", lines
    return None, lines


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    exe = Path(argv[1]).resolve()
    if not exe.exists():
        print(f"frozen app smoke: FAIL — no such executable: {exe}", file=sys.stderr)
        return 1

    work = Path(tempfile.mkdtemp(prefix="ptr-frozen-smoke-"))
    h5 = make_h5(work / "run.h5")
    port = free_port()
    cmd = [str(exe), "app", str(h5), "--no-browser", "--port", str(port)]
    env = dict(os.environ, PTR_RECENT_PATH=str(work / "recent.json"))
    print(f"frozen app smoke: {' '.join(cmd)}", file=sys.stderr)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        url, lines = await_url(proc, LAUNCH_TIMEOUT)
        if url is None:
            print("frozen app smoke: FAIL — no URL on stderr within the timeout", file=sys.stderr)
            print("\n".join(lines), file=sys.stderr)
            return 1
        base = url.rstrip("/")
        if int(url.rsplit(":", 1)[1].rstrip("/")) != port:
            print(f"frozen app smoke: note — the app moved to port {url}", file=sys.stderr)

        base = url.rstrip("/")
        deadline = time.monotonic() + OPEN_TIMEOUT
        state = {}
        while time.monotonic() < deadline:
            state = json.loads(get(base + "/api/state")[1])
            if state.get("status") in ("ready", "error"):
                break
            time.sleep(0.5)
        if state.get("status") != "ready":
            print(f"frozen app smoke: FAIL — the file never opened: {state}", file=sys.stderr)
            print("\n".join(lines), file=sys.stderr)
            return 1

        status, page = get(base + "/review")
        if status != 200 or b"const APPMODE = true" not in page:
            print(
                f"frozen app smoke: FAIL — /review returned {status} without the "
                "app-mode page",
                file=sys.stderr,
            )
            return 1

        recent = json.loads(get(base + "/api/recent")[1])
        if not recent or not Path(recent[0]["path"]).samefile(h5):
            print(f"frozen app smoke: FAIL — recents did not list the file: {recent}", file=sys.stderr)
            return 1
        if post(base + "/close") != 200:
            print("frozen app smoke: FAIL — could not close the file", file=sys.stderr)
            return 1

        # Second phase: the same executable with no arguments at all, which is how
        # Finder and the Start Menu shortcut launch it. The command line would answer
        # that with usage text and exit 2, and a windowed bundle does it invisibly.
        # `BROWSER` keeps the page from popping open on whoever is running this.
        bare = subprocess.Popen(
            [str(exe)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(env, BROWSER=f"{sys.executable} -c pass"),
        )
        try:
            bare_url, bare_lines = await_url(bare, LAUNCH_TIMEOUT)
            if bare_url is None:
                print(
                    "frozen app smoke: FAIL — launched with no arguments, the bundle "
                    "never started an app (a double-click would do nothing)",
                    file=sys.stderr,
                )
                print("\n".join(bare_lines), file=sys.stderr)
                return 1
            status, start = get(bare_url.rstrip("/") + "/")
            if status != 200 or b"Stop the app" not in start:
                print(
                    f"frozen app smoke: FAIL — the bare launch served {status} "
                    "without the start screen",
                    file=sys.stderr,
                )
                return 1
            post(bare_url.rstrip("/") + "/shutdown")
        finally:
            bare.kill()
            bare.wait(timeout=10)

        # Third phase: --window. A runner has no window server, which is exactly the
        # case that must degrade rather than die: the app logs why and serves anyway.
        # On a desktop machine the same command opens a real window and serves too.
        win = subprocess.Popen(
            [str(exe), "app", str(h5), "--window", "--port", str(free_port())],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(env, BROWSER=f"{sys.executable} -c pass"),
        )
        windowed = "fell back to a browser tab"
        try:
            win_url, win_lines = await_url(win, LAUNCH_TIMEOUT)
            if win_url is None:
                print(
                    "frozen app smoke: FAIL — --window neither opened a window nor "
                    "started serving; a desktop without a window server would exit",
                    file=sys.stderr,
                )
                print("\n".join(win_lines), file=sys.stderr)
                return 1
            win_base = win_url.rstrip("/")
            # /review only exists once a file is open, so ask the state first
            deadline = time.monotonic() + OPEN_TIMEOUT
            state = {}
            while time.monotonic() < deadline:
                state = json.loads(get(win_base + "/api/state")[1])
                if state.get("status") in ("ready", "error"):
                    break
                time.sleep(0.5)
            if state.get("status") != "ready":
                print(
                    f"frozen app smoke: FAIL — the windowed app never opened the "
                    f"file: {state}",
                    file=sys.stderr,
                )
                return 1
            status, page = get(win_base + "/review")
            if status != 200:
                print(
                    f"frozen app smoke: FAIL — the windowed app served {status}",
                    file=sys.stderr,
                )
                return 1
            windowed = (
                "fell back to a browser tab"
                if any("window" in line.lower() for line in win_lines)
                else "opened a window"
            )
            post(win_base + "/shutdown")
        finally:
            win.kill()
            win.wait(timeout=10)

        print(
            f"frozen app smoke: OK  ({os.path.getsize(exe) // 1024} KiB launcher, "
            f"served {url}, a bare launch opened the start screen, and --window "
            f"{windowed})"
        )
        return 0
    finally:
        proc.kill()
        proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
