"""Persistent local review application.

The CLI flow is agent-driven: one command per file, and the server exits when the
expert clicks Done. This module is the other way round — one long-lived localhost
server that the user lives in, opening one file after another. Opening a file either
loads the config saved beside it or, the first time, runs the deterministic pipeline
to make one, optionally asking an agent endpoint to post-process it. Either way the
result is a config file on disk, which is what the UI then edits.

Everything here is local: files are read and written in place, and the only network
call is to an agent endpoint the user supplied.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http import server
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import h5py

from . import brand, desktop, ptrms, viz
from .analyze import (
    analyze_config_to_csv,
    auto_peaks,
    auto_ranges,
    auto_ranges_note,
    interval_spectrum,
    resolve_analysis_settings,
    resolve_x_axis_unit,
)


def _recent_path() -> Path:
    """Where recent files are remembered. ``SNIFF_RECENT_PATH`` overrides it so a
    packaged build can be exercised (or a home folder kept clean) without patching
    Python in a frozen bundle."""
    override = os.environ.get("SNIFF_RECENT_PATH")
    return (
        Path(override).expanduser()
        if override
        else Path.home() / ".sniff" / "recent.json"
    )


RECENT_PATH = _recent_path()
LEGACY_RECENT_PATH = Path.home() / ".ptr-ms" / "recent.json"
RECENT_LIMIT = 20

# Where an open's phases sit on its progress axis, measured on the real 2 GB /
# 20,725-cycle fixture rather than guessed: opening the h5 file and reading its
# header costs no measurable time, the deterministic pipeline about a second
# (auto_peaks 0.1 s, auto_ranges 0.8 s), and everything that is not the extraction
# pass in viz.build_viz_data another three and a half. The extraction is the rest,
# and it is 89 % of the 33 s an open takes — which is why it gets the bar's whole
# remaining span and reports cycles read, not anything smoother. It reads the run
# twice on a curated file (14.6 s streaming, 13.9 s re-centring the intervals), and
# both belong on the axis.
P_META = 0.03
P_DETECT = 0.08
P_BUILD = viz.PREP_FRACTION  # 0.11: where the streaming starts, here and in viz


def _valid_config(value) -> bool:
    """True if a mapping looks like one of our configs, rather than some unrelated
    JSON file that happens to share the h5 file's stem. Key presence is the test, not
    truthiness: a reviewed-but-empty config is still a config."""
    return isinstance(value, dict) and ("peaks" in value or "ranges" in value)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def _write_json(path: Path, value) -> None:
    """Write through a private temporary name in the same folder, then move it into
    place. Two review tabs autosave to the same config, so the temp name must be
    unique per write: a shared one lets one tab publish another tab's bytes."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _replace_from(tmp, target: Path) -> None:
    """Publish a file written elsewhere (a temp name) onto its real path."""
    try:
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


# A sniff summary starts with this header; anything else that turns up under the name
# we would like to write is somebody else's table and stays untouched.
_CSV_MARKERS = ("Variable", "Average(Corrected)")


def _csv_target(h5_path: str) -> Path:
    """Where an export of ``h5_path`` goes: ``<stem>.csv`` beside it, unless a file
    that is not a sniff summary already lives there — a Viewer or Excel export often
    does, and the review may be comparing against it."""
    target = Path(h5_path).with_suffix(".csv")
    if not target.exists():
        return target
    try:
        with target.open("r", encoding="utf-8-sig", errors="replace") as handle:
            head = handle.readline()
    except OSError:
        return target.parent / (target.stem + "-sniff.csv")
    if all(marker in head for marker in _CSV_MARKERS):
        return target
    return target.parent / (target.stem + "-sniff.csv")


def config_path_for(h5_path: str) -> Path:
    """The config file belonging to an h5 file: same name, same folder.

    ``~/d/sniff.h5`` -> ``~/d/sniff.json``. A ``<stem>-analysis-config.json`` written by
    the older CLI flow is honoured when no ``<stem>.json`` exists yet, so opening a
    previously reviewed file does not start a fresh pipeline run. A same-stem JSON
    that is not one of our configs is never overwritten: a ``<stem>.sniff.json`` is
    used instead.
    """
    p = Path(h5_path).expanduser()
    beside = p.with_suffix(".json")
    legacy_cli = p.parent / f"{p.stem}-analysis-config.json"
    legacy_product = p.with_suffix(".ptr.json")
    if beside.exists():
        if _valid_config(_read_json(beside)):
            return beside
        if legacy_product.exists() and _valid_config(_read_json(legacy_product)):
            return legacy_product
        return p.with_suffix(".sniff.json")
    if legacy_cli.exists() and _valid_config(_read_json(legacy_cli)):
        return legacy_cli
    if legacy_product.exists() and _valid_config(_read_json(legacy_product)):
        return legacy_product
    return beside


def _instrument(f) -> str:
    value = f.attrs.get("InstrumentType", "unknown")
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


def bootstrap_config(h5_path: str, f=None, *, progress=None, should_stop=None) -> dict:
    """Build a config from the file alone, with no agent and no judgement calls.

    Peaks and intervals come from the deterministic pipeline; the checklist says
    plainly what was decided automatically and what still needs a human. ``f`` may be
    an already-open file, since opening a 2 GB run costs tens of seconds.
    ``progress`` and ``should_stop`` are the same pair :func:`ptrms.extract_traces`
    takes: detection is one call each for peaks and intervals, so it reports at the
    boundaries between them (auto_peaks 0.1 s, auto_ranges 0.8 s on the 2 GB run).
    """

    def _say(frac):
        if progress is not None:
            progress(max(0.0, min(1.0, float(frac))))

    def _halt():
        if should_stop is not None and should_stop():
            raise ptrms.AnalysisCancelled("the analysis was cancelled")

    own = f is None
    source = h5py.File(h5_path, "r") if own else f
    try:
        _say(0.0)
        peaks = auto_peaks(source)
        _say(0.1)
        _halt()  # detection is the only cancellable gap before the review data
        ranges = auto_ranges(source)
        _say(1.0)
        ncyc = int(source["SPECdata/Intensities"].shape[0])
        instrument = _instrument(source)
    finally:
        if own:
            source.close()

    settings = resolve_analysis_settings({})
    checklist = [
        {
            "text": "This config was generated automatically — nothing has been "
            "curated yet.",
            "detail": "Peaks and intervals come from the deterministic pipeline. "
            "The judgment calls stay with the reviewer: the pipeline cannot tell "
            "a real low-level analyte from an artifact, and it classifies "
            "intervals purely by signal level.",
        },
        f"{len(peaks)} channels and {len(ranges)} intervals were detected.",
        "Check each interval's class: a plateau at analyte levels that matches the "
        "background is a background, whatever the level detector said.",
        "Confirm or correct the chemistry on the named channels, and name the rest.",
        "Look for artifact channels — mass-locked fragments and ringing combs sit "
        "at fixed offsets from strong ions and have no plausible formula.",
    ]
    if not peaks:
        checklist.insert(
            1,
            "No significant signal was detected in this file: treat it as a blank or "
            "a no-beam capture rather than an analyte panel.",
        )
    config = {
        "peaks": peaks,
        "ranges": ranges,
        "analyze": {k: v for k, v in settings.items() if k != "sources"},
        "viz": {"x_axis_unit": "cycle"},
        "checklist": checklist,
        "diagnostics": {
            "n_peaks": len(peaks),
            "n_ranges": len(ranges),
            "ncyc": ncyc,
            "instrument": instrument,
        },
    }
    # Said on the Intervals card: whoever opens the file here never sees a command
    # line, so a gap the pipeline joined has to explain itself or it is an unannounced
    # edit to the reviewer's intervals. Nothing merged, nothing to say.
    note = auto_ranges_note(ranges)
    if note:
        config["merge_note"] = note
    return config


def load_recent() -> list:
    value = _read_json(RECENT_PATH)
    if value is None and RECENT_PATH == _recent_path():
        # Read the old store without deleting it. The next write publishes the same
        # entries under ~/.sniff, while an interrupted migration leaves the old file
        # available for the previous release.
        value = _read_json(LEGACY_RECENT_PATH)
    if not isinstance(value, list):
        return []
    return [p for p in value if isinstance(p, str)]


def remember_recent(path) -> list:
    path = str(Path(path).expanduser().resolve())
    values = [p for p in load_recent() if p != path]
    values.insert(0, path)
    values = values[:RECENT_LIMIT]
    RECENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_json(RECENT_PATH, values)
    return values


class Session:
    """The one file currently open, plus the work in flight on it.

    Only one file is open at a time: a 2 GB run holds traces for tens of thousands of
    cycles, so a second open would double the footprint. Opening one closes the other.
    """

    def __init__(self):
        self.path = None
        self.config_path = None
        self.config = None
        self.payload = None
        self.status = "empty"  # empty | loading | ready | error
        self.stage = ""
        self.error = None
        self.progress = None  # 0..1 while an open runs, None at every other time
        self.started_at = None  # monotonic clock, for the page's ETA
        self.export_result = None
        self.export_error = None
        self.agent_status = None
        self._file = None
        self._lock = threading.Lock()
        self._opening = False
        self._cancel = threading.Event()
        # A double-clicked bundle has no terminal to press Ctrl-C in, so stopping the
        # server is something the page has to be able to ask for.
        self.stop = threading.Event()

    # ---- opening -------------------------------------------------------------
    def _say(self, value):
        """Publish an open's progress. The axis may only ever move forward: one
        phase running after another must not make the bar go backwards."""
        value = max(0.0, min(1.0, float(value)))
        if self.progress is None or value > self.progress:
            self.progress = value

    def _halt(self):
        if self._cancel.is_set():
            raise ptrms.AnalysisCancelled("the open was cancelled")

    def _band(self, lo, hi):
        """A sink that puts one phase's own 0..1 fraction onto the open's axis."""

        def report(frac):
            self._say(lo + max(0.0, min(1.0, float(frac))) * (hi - lo))

        return report

    def _build_sink(self, prep_start):
        """A sink for build_viz_data's fractions, which are already an axis of their
        own: its phases take its first ``viz.PREP_FRACTION`` and the streaming pass
        the rest. From that fraction on the two axes agree — the streaming pass is
        89 % of the work in both — so only what precedes it is re-scaled, into
        whatever the open has not already spent."""

        def report(frac):
            frac = max(0.0, min(1.0, float(frac)))
            if frac <= viz.PREP_FRACTION:
                self._say(
                    prep_start
                    + (frac / viz.PREP_FRACTION) * (P_BUILD - prep_start)
                )
            else:
                self._say(frac)

        return report

    def cancel(self):
        """Ask an in-flight open to stop, and report whether there was one to stop.

        The flag is read by the ``should_stop`` callbacks the phases poll, so the
        open ends on its own thread at the next block boundary and leaves the
        session exactly as an open that never started.
        """
        with self._lock:
            opening = self._opening
        if opening:
            self._cancel.set()
        return opening

    def open(self, path, agent_url=None, agent_timeout=300.0):
        """Load ``path``, making a config first if the file has never been reviewed.

        The h5 file is opened once and reused for detection and for the review data:
        reopening a large file costs the user another 30-90 s for nothing.

        Every phase is given the same progress sink and the same cancel flag, so the
        page can show a bar that reflects the work and get out of the way of a user
        who changed their mind. A cancelled open is not a failure: it leaves the
        session empty, with no error, and ready to open the same file again.
        """
        with self._lock:
            if self._opening:
                raise RuntimeError("a file is already opening")
            self._opening = True
            self._cancel.clear()
        self.progress, self.started_at = 0.0, time.monotonic()
        try:
            self.close()
            self.status, self.stage, self.error = "loading", "Opening the file", None
            self.agent_status = None
            self.export_result = None
            path = str(Path(path).expanduser().resolve())
            config_path = config_path_for(path)
            self._file = h5py.File(path, "r")
            self._say(P_META)
            config = _read_json(config_path) if config_path.exists() else None
            if config is not None and not _valid_config(config):
                raise ValueError(f"{config_path} is not a sniff config")
            prep_start = P_META
            if config is None:
                self.stage = "Detecting peaks and intervals"
                config = bootstrap_config(
                    path,
                    f=self._file,
                    progress=self._band(P_META, P_DETECT),
                    should_stop=self._cancel.is_set,
                )
                self._halt()  # a cancel must not leave a half-made config on disk
                _write_json(config_path, config)
                if agent_url:
                    config = self._ask_agent(
                        config, path, config_path, agent_url, agent_timeout
                    )
                    self._halt()
                prep_start = P_DETECT
            self.stage = "Computing the review data"
            self.payload = self._payload(
                path,
                config,
                progress=self._build_sink(prep_start),
                should_stop=self._cancel.is_set,
            )
            self.path, self.config_path, self.config = path, config_path, config
            # Clear the flag before announcing readiness: the page polls the state and
            # offers its buttons the moment it sees "ready", so "ready" has to mean it
            # will accept a close or an export rather than answering "busy".
            with self._lock:
                self._opening = False
            self.status, self.stage = "ready", "Ready"
            try:
                remember_recent(path)
            except OSError:
                # Bookkeeping. It must never cost the user a file that opened fine.
                pass
            return self.payload
        except ptrms.AnalysisCancelled:
            # Nothing was decided and nothing was half-written: the file closes and
            # the session is as though the open had never been asked for.
            self.close()
            return None
        except Exception as exc:
            self.close()
            self.status, self.error, self.stage = "error", str(exc), "Failed to open"
            raise
        finally:
            with self._lock:
                self._opening = False
            self.progress = None

    def _ask_agent(self, config, path, config_path, agent_url, agent_timeout):
        """Let an attached agent post-process the automatic config. Its answer is a
        convenience: any failure at all leaves the deterministic config in place,
        because the alternative is losing the pipeline's work over one bad request."""
        self.stage = "Asking the agent to review it"
        body = json.dumps(
            {
                "file": path,
                "config": config,
                "diagnostics": config.get("diagnostics") or {},
            }
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                agent_url,
                body,
                {"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=agent_timeout) as response:
                answer = json.loads(response.read().decode("utf-8", "replace"))
            if isinstance(answer, dict):
                answer = answer.get("config", answer)
            if not isinstance(answer, dict) or not (
                answer.get("peaks") or answer.get("ranges")
            ):
                # Key presence is not enough here: an answer of {"peaks": []} would
                # replace real detected work with an empty panel.
                raise ValueError("the agent reply contained no peaks or ranges")
        except Exception as exc:  # any endpoint failure must not cost the user a file
            self.agent_status = (
                f"Agent review failed — using the automatic config "
                f"({type(exc).__name__}: {exc})"
            )[:300]
            return config
        self.agent_status = "Agent review applied."
        _write_json(config_path, answer)
        return answer

    def _payload(self, path, config, progress=None, should_stop=None):
        settings = resolve_analysis_settings(config)
        return viz.build_viz_data(
            self._file,
            config.get("peaks", []),
            config.get("ranges", []),
            analysis_settings=settings,
            config_base=config,
            checklist=config.get("checklist"),
            x_axis_unit=resolve_x_axis_unit(config),
            merge_note=config.get("merge_note") or "",
            progress=progress,
            should_stop=should_stop,
        )

    def close(self):
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
        self._file = None
        self.path = None
        self.config_path = None
        self.config = None
        self.payload = None
        self.progress = None
        # A closed file has no last export: a tab left open must not be told a run
        # finished when it belongs to a file that is no longer loaded.
        self.export_result = None
        self.export_error = None
        self.agent_status = None
        self.status, self.stage = "empty", ""

    @property
    def busy(self) -> bool:
        """True while an open or an export is in flight."""
        with self._lock:
            return self._opening or self.status == "exporting"

    # ---- exporting -----------------------------------------------------------
    def export(self):
        """Run the full-precision analysis to ``<stem>.csv`` beside the file.

        Unlike the CLI's Done, nothing shuts down afterwards: the reviewer keeps
        working and exports again.
        """
        if not self.path or self.config is None:
            raise RuntimeError("no file is open")
        self.status, self.stage, self.error = "exporting", "Running the analysis", None
        self.export_result = None  # so a second export cannot report the last one
        self.export_error = None
        target = _csv_target(self.path)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".",
                                   suffix=".tmp")
        os.close(fd)
        try:
            result = analyze_config_to_csv(self.path, self.config, tmp)
            _replace_from(tmp, target)
        except Exception as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            # The file is still open and still worth reviewing, so a failed export
            # reports the error without dropping the session into an error state.
            self.status, self.error, self.stage = "ready", str(exc), "Export failed"
            self.export_error = str(exc)
            raise
        result["out"] = str(target)
        self.export_result = result
        self.status, self.stage = "ready", "Ready"
        return result

    # ---- state ---------------------------------------------------------------
    def status_payload(self) -> dict:
        """Export progress in the shape the review page's overlay already polls, so
        the app and the one-shot CLI share one piece of UI code."""
        if self.status == "exporting":
            return {"status": "running"}
        if self.export_error:
            return {"status": "error", "error": self.export_error}
        if self.export_result:
            return {"status": "done", "out": self.export_result.get("out")}
        return {"status": "idle"}

    def state(self) -> dict:
        loading = self.status == "loading"
        return {
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "file": self.path,
            "config": str(self.config_path) if self.config_path else None,
            "agent_status": self.agent_status,
            "export": self.export_result,
            # The open's own bar: how far it has got — a float only while it runs,
            # since nothing else on this page has a fraction to report — and whether a
            # Cancel button would do anything at all right now.
            "progress": (
                float(self.progress)
                if loading and self.progress is not None
                else None
            ),
            "cancellable": bool(loading and self._opening),
            # "window" or "browser": how the user is looking at this app right
            # now. A bundle that meant to open a window and did not has to be able to say
            # so — otherwise the only evidence is a tab the user has to notice.
            "surface": surface(),
        }


_START_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__PAGE_TITLE__</title>
<style>
:root{
  --bg:#fbf3e8;--card:#fffdf9;--sunk:#fff8ef;--fg:#173c3b;--mut:#5b706d;
  --line:#d8e5df;--acc:#1f6f6b;--accc:#fffdf9;
  --peach:#ffd9a8;--err:#a33b32;--errbg:#fff0e9;
  --ring:rgba(31,111,107,.34);--scrim:rgba(251,243,232,.88);
}
@media(prefers-color-scheme:dark){:root{
  --bg:#102322;--card:#173331;--sunk:#132b29;--fg:#effaf3;--mut:#a9c0b9;
  --line:#31514d;--acc:#71c3ad;--accc:#102322;
  --peach:#ffd9a8;--err:#ff9c8f;--errbg:#3b211e;
  --ring:rgba(113,195,173,.45);--scrim:rgba(16,35,34,.9);
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);-webkit-font-smoothing:antialiased;
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
main{max-width:920px;margin:0 auto;padding:clamp(28px,7vw,72px) 24px 44px}
.head{display:flex;gap:12px;align-items:center;margin-bottom:28px}
svg.brand{flex:none;width:42px;height:42px;border-radius:11px;
  box-shadow:0 1px 4px rgba(0,0,0,.25)}
.tag{font-weight:400;color:var(--mut);font-size:15px;letter-spacing:0}
h1{margin:0;font-size:20px;font-weight:650;letter-spacing:-.015em}
.lede{margin:0;color:var(--mut);max-width:38em}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;
  box-shadow:0 1px 1px rgba(16,24,40,.04),0 14px 35px -24px rgba(16,24,40,.42)}
.hero{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:28px;align-items:center;
  margin-bottom:30px}
.eyebrow{display:flex;align-items:center;gap:8px;margin:0 0 13px;color:var(--acc);
  font-size:11px;font-weight:700;letter-spacing:.13em;text-transform:uppercase}
.eyebrow i{width:8px;height:8px;border-radius:50%;background:var(--peach);
  box-shadow:0 0 0 4px rgba(255,217,168,.35)}
.hero h2{max-width:11em;margin:0 0 10px;font-size:clamp(30px,5vw,48px);line-height:1.02;
  letter-spacing:-.045em;font-weight:700}
.spectrum{position:relative;min-height:170px;padding:14px;border-radius:28px;
  background:var(--acc);overflow:hidden;box-shadow:0 16px 35px -22px rgba(31,111,107,.75)}
.spectrum::before,.spectrum::after{content:"";position:absolute;border-radius:50%;
  background:var(--peach);opacity:.9}
.spectrum::before{width:125px;height:125px;right:-32px;top:-44px}
.spectrum::after{width:70px;height:70px;left:-23px;bottom:-28px;background:#f6b89c}
.spectrum svg{position:relative;z-index:1;width:100%;height:140px}
.spectrum .trace{stroke-dasharray:420;stroke-dashoffset:420;animation:trace 1.8s ease-out forwards}
.spectrum .nose{transform-origin:115px 30px;animation:nose 3.4s ease-in-out 1.8s infinite}
@keyframes trace{to{stroke-dashoffset:0}}
@keyframes nose{0%,100%{transform:rotate(0)}50%{transform:rotate(4deg)}}
.now{display:flex;gap:14px;align-items:center;padding:14px 16px;margin-bottom:20px;
  border-color:var(--acc)}
.now .txt{min-width:0;flex:1}
.now b{display:block;font-weight:600;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.now .sub{display:block;color:var(--mut);font-size:12px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.now .meta{min-width:0;overflow:hidden}
.now .meta em{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pick{padding:28px 30px;text-align:left}
.pick h2{margin:0 2px 4px;font-size:18px;font-weight:650;letter-spacing:-.02em}
.pick p{margin:0 0 18px;color:var(--mut);font-size:13px}
.quick{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:18px;color:var(--mut);font-size:11px}
.quick span{display:inline-flex;align-items:center;gap:5px}
.quick i{width:6px;height:6px;border-radius:50%;background:var(--peach)}
.btn{appearance:none;border:0;border-radius:9px;background:var(--acc);color:var(--accc);
  font:inherit;font-weight:550;padding:9px 15px;cursor:pointer}
.btn:hover{filter:brightness(1.07)}
.btn:disabled{opacity:.55;cursor:default;filter:none}
.btn.sec{background:transparent;color:var(--fg);border:1px solid var(--line);font-weight:500}
.btn.sec:hover{background:var(--sunk)}
.btn:focus-visible,.link:focus-visible{outline:2px solid var(--ring);
  outline-offset:2px}
.meta{flex:none;text-align:right;font-size:12px;color:var(--mut)}
.meta em{display:block;font-style:normal}
.note{margin-top:18px;padding:11px 14px;border-radius:10px;background:var(--sunk);
  color:var(--mut);font-size:13px}
.note[hidden]{display:none}
.note.err{background:var(--errbg);color:var(--err)}
.bar{height:2px;margin-top:9px;border-radius:2px;background:var(--line);overflow:hidden}
.bar i{display:block;height:100%;width:35%;background:var(--acc);
  animation:slide 1.5s ease-in-out infinite}
@keyframes slide{from{transform:translateX(-100%)}to{transform:translateX(380%)}}
@media(prefers-reduced-motion:reduce){.bar i{animation:none;width:100%;opacity:.5}
  .spectrum .trace{animation:none;stroke-dashoffset:0}.spectrum .nose{animation:none}}
footer{display:flex;gap:12px;align-items:center;justify-content:space-between;
  margin-top:32px;color:var(--mut);font-size:12px}
.link{background:none;border:0;padding:0;color:var(--mut);font:inherit;
  text-decoration:underline;cursor:pointer}
.link:hover{color:var(--fg)}
.link[hidden]{display:none}
/* An open blocks the whole screen. It is the one thing on this page that takes
   long enough to be worth leaving, so the page has to make leaving possible
   rather than let a second click start a second open behind the first. */
html.lock,html.lock body{overflow:hidden}
#ov{position:fixed;inset:0;z-index:9;display:grid;place-items:center;padding:24px;
  background:var(--scrim);overscroll-behavior:contain;
  -webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)}
#ov[hidden]{display:none}
.ovcard{width:min(460px,100%);background:var(--card);border:1px solid var(--line);
  border-radius:14px;padding:20px;box-shadow:0 1px 1px rgba(16,24,40,.04),
  0 24px 60px -28px rgba(16,24,40,.45)}
.ovhead{display:flex;gap:12px;align-items:flex-start}
.ovhead .txt{min-width:0;flex:1}
.ovhead b{display:block;font-weight:600;font-size:15px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.ovhead .sub{display:block;color:var(--mut);font-size:12px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.ovmark{flex:none;width:16px;height:16px;margin-top:3px;border-radius:50%;
  border:2px solid var(--line);border-top-color:var(--acc);animation:spin .9s linear infinite}
.ovmark.stop{border-color:var(--err);border-top-color:transparent;animation:none;
  border-radius:0;background:none}
@keyframes spin{to{transform:rotate(360deg)}}
.ovart{height:42px;margin:14px 0 4px;border-radius:10px;background:var(--sunk);overflow:hidden}
.ovart svg{display:block;width:100%;height:100%}
.ovart .base{stroke:var(--line);stroke-width:2}
.ovart .peak{stroke:var(--acc);stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round;
  fill:none;stroke-dasharray:90;stroke-dashoffset:90;animation:drawpeak 2.8s ease-in-out infinite}
.ovart .ion{fill:var(--peach);opacity:.7;animation:iondrift 3.2s ease-in-out infinite}
.ovart circle.ion:nth-of-type(2){animation-delay:-1.1s}
.ovart circle.ion:nth-of-type(3){animation-delay:-2.2s}
.ovart .breath{stroke:var(--peach);stroke-width:1.7;fill:none;stroke-linecap:round;
  opacity:.72;animation:breathe 3.4s ease-in-out infinite}
@keyframes drawpeak{0%,100%{stroke-dashoffset:90;opacity:.5}45%,70%{stroke-dashoffset:0;opacity:1}}
@keyframes iondrift{0%,100%{transform:translate(0,3px);opacity:.25}50%{transform:translate(9px,-3px);opacity:.85}}
@keyframes breathe{0%,100%{transform:translateX(-3px);opacity:.2}50%{transform:translateX(4px);opacity:.8}}
.ovstage{margin:10px 0 6px;color:var(--mut);font-size:13px;min-height:20px}
.pbar{height:6px;border-radius:4px;background:var(--line);overflow:hidden}
.pbar i{display:block;height:100%;width:0;background:var(--acc);border-radius:4px;
  transition:width .35s ease}
.ovmeta{display:flex;gap:10px;justify-content:space-between;margin-top:7px;
  color:var(--mut);font-size:12px;font-variant-numeric:tabular-nums}
.ovrow{display:flex;justify-content:flex-end;margin-top:16px}
#overr .msg{margin:10px 0 0;color:var(--err);font-size:13px;white-space:pre-wrap}
#ov.handoff .ovcard{animation:handoffcard .62s cubic-bezier(.22,.75,.25,1) both}
#ov.handoff .ovart{animation:handoffart .62s ease both}
@keyframes handoffcard{to{opacity:0;transform:scale(1.035) translateY(-6px)}}
@keyframes handoffart{to{opacity:0;transform:scale(1.08)}}
@media(prefers-reduced-motion:reduce){.ovmark,.ovart .peak,.ovart .ion,.ovart .breath{animation:none}
  .ovart .peak{stroke-dashoffset:0}.pbar i{transition:none}}
@media(max-width:620px){main{padding-top:28px}.hero{grid-template-columns:1fr;gap:20px}
  .spectrum{min-height:125px}.spectrum svg{height:100px}.pick{padding:23px 20px}
  .quick{margin-top:16px}
  .now{align-items:flex-start;row-gap:10px;flex-wrap:wrap}
  .now .txt,.now .meta{flex:1 1 100%}
  .now b,.now .sub{white-space:normal;overflow-wrap:anywhere;text-overflow:clip}
}
</style></head><body><main>
  <div class="head">__MARK__<h1>__APP_NAME__ <span class="tag">__TAGLINE__</span></h1></div>

  <div class="hero">
    <div>
      <p class="eyebrow"><i aria-hidden="true"></i>Local signal desk</p>
      <h2>Find the story in your spectrum.</h2>
      <p class="lede">Open an IONICON run to review its peaks and intervals. Saved configs
        return exactly as you left them; new runs get a clear starting point.</p>
    </div>
    <div class="spectrum" aria-label="A stylised mass spectrum with a nose motif" role="img">
      <svg viewBox="0 0 260 140" aria-hidden="true" focusable="false">
        <polyline class="trace" points="8,105 74,105 99,105 108,34 117,105 251,105" fill="none"
          stroke="#eafaf6" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
        <g class="nose" fill="#ffd9a8"><path d="M91 43c-1-8 3-15 11-19 7-3 16-1 20 4 3 4 2 8-3 10-6 2-8 6-13 8-6 2-11 1-15-3Z"/>
          <circle cx="113" cy="30" r="2" fill="#1f6f6b"/>
          <path d="M126 22c9-7 18-4 23 4" fill="none" stroke="#ffd9a8" stroke-width="3" stroke-linecap="round"/></g>
      </svg>
    </div>
  </div>

  <div id="now"></div>

  <div class="card pick">
    <h2>Open an IONICON run</h2>
    <p>Choose an HDF5 run with the native file dialog on this computer.</p>
    <button class="btn" id="browse" type="button">Browse this computer&hellip;</button>
    <div class="quick" id="quick-help"><span><i aria-hidden="true"></i>Runs locally</span>
      <span><i aria-hidden="true"></i>No uploads</span><span><i aria-hidden="true"></i>HDF5 input</span>
    </div>
  </div>

  <div class="note" id="state" role="status" aria-live="polite" hidden></div>

  <footer>
    <span>Served from 127.0.0.1 &mdash; nothing leaves this computer.</span>
    <a class="link" id="quit" href="#" hidden>Stop the app</a>
  </footer>
</main>

<div id="ov" role="dialog" aria-modal="true" aria-labelledby="ovname" hidden>
  <div class="ovcard">
    <div id="ovload">
      <div class="ovhead">
        <span class="ovmark" aria-hidden="true"></span>
        <div class="txt"><b id="ovname">Opening a file</b>
          <span class="sub" id="ovdir"></span></div>
      </div>
      <div class="ovart" aria-hidden="true">
        <svg viewBox="0 0 320 42" focusable="false">
          <path class="base" d="M12 28H308" fill="none"/>
          <path class="peak" d="M12 28H137L151 8L165 28H308"/>
          <path class="breath" d="M184 18c12-9 24-9 35 0"/>
          <circle class="ion" cx="205" cy="28" r="2.5"/>
          <circle class="ion" cx="248" cy="28" r="2.5"/>
          <circle class="ion" cx="278" cy="28" r="2.5"/>
        </svg>
      </div>
      <p class="ovstage" id="ovstage" role="status" aria-live="polite"></p>
      <div class="pbar" role="progressbar" id="ovbar"
           aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><i id="ovfill"></i></div>
      <div class="ovmeta"><span id="ovpct">0%</span><span id="oveta"></span></div>
      <div class="ovrow"><button class="btn sec" id="cancel" type="button">Cancel</button>
      </div>
    </div>
    <div id="overr" hidden>
      <div class="ovhead">
        <span class="ovmark stop" aria-hidden="true"></span>
        <div class="txt"><b>That did not open</b></div>
      </div>
      <p class="msg" id="overrmsg"></p>
      <div class="ovrow">
        <button class="btn" id="ovback" type="button">Back to the start screen</button>
      </div>
    </div>
  </div>
</div>
<script>
const $=s=>document.querySelector(s);
const SEP=String.fromCharCode(92);          // Windows separators, without a literal
function parts(p){const s=String(p).split(SEP).join('/'),i=s.lastIndexOf('/');
  if(i<0)return{name:s,dir:''};
  return{name:s.slice(i+1),dir:i===0?'/':s.slice(0,i)}}
function el(tag,cls,text){const n=document.createElement(tag);
  if(cls)n.className=cls; if(text!=null)n.textContent=text; return n;}

async function openFile(path){
  let r;
  try{r=await fetch('/open',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path})});}
  catch(e){return note('The app is no longer running.',true,false,true);}
  if(!r.ok){
    const b=await r.json().catch(()=>({}));
    return note(b.error||'Could not open that file.',true,false,true);
  }
  // Ask and start watching in the same breath: the server sets "loading" on its own
  // thread, so a poll that arrives first would otherwise show the old screen and
  // wait two and a half seconds before the sheet went up.
  ask=path; watching=true; resetEta(); showSheet(); tick();
}

// ---- the sheet an open runs behind -----------------------------------------
// Reading a 2 GB run takes about half a minute, which is long enough to be worth
// leaving, so the open is shown full screen with a real bar and a Cancel button
// rather than as a line of text the user has to trust.
let watching=false;                    // an open is in flight, from this page
let failed=false;                      // the sheet is showing an error, not a bar
let ask=null;                          // the path this page asked to open
let t0=0;                              // when this page started watching
let navigating=false;                   // ready can be observed by two poll turns
const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function resetEta(){t0=0;}
function handoff(){
  if(navigating)return;
  navigating=true;
  watching=false;
  setBar(1);
  $('#ovstage').textContent='Ready';
  $('#cancel').disabled=true; $('#cancel').hidden=true;
  try{sessionStorage.setItem('sniff-review-entrance','1');}catch(e){}
  const go=()=>{
    if(!navigating)return;
    navigating=false;
    location.replace('/review');
  };
  if(reducedMotion)return go();
  const sheet=$('#ov'), card=sheet.querySelector('.ovcard');
  let settled=false;
  const settle=e=>{
    if(settled|| (e && e.animationName!=='handoffcard'))return;
    settled=true; card.removeEventListener('animationend',settle); go();
  };
  card.addEventListener('animationend',settle);
  sheet.classList.add('handoff');
  window.setTimeout(settle,760);
}
function lock(on){
  document.documentElement.classList.toggle('lock',on);
  const m=document.querySelector('main'); if(m)m.inert=on;
}
function showSheet(){
  $('#ovload').hidden=false; $('#overr').hidden=true;
  $('#cancel').disabled=false; $('#cancel').hidden=false;
  setBar(0); $('#oveta').textContent='';
  $('#ov').hidden=false; lock(true); $('#cancel').focus();
}
function closeSheet(){
  $('#ov').hidden=true; $('#ovload').hidden=false; $('#overr').hidden=true;
  lock(false); resetEta(); ask=null; watching=false; failed=false;
}
function failSheet(msg){
  failed=true;
  note(msg,true,false,true);           // so it is still there once the sheet is gone
  $('#ovload').hidden=true; $('#overr').hidden=false;
  $('#overrmsg').textContent=msg;
  $('#ovback').focus();
}
function setBar(p){
  const pct=Math.round(p*100);
  $('#ovfill').style.width=pct+'%';
  $('#ovbar').setAttribute('aria-valuenow',String(pct));
  $('#ovpct').textContent=pct+'%';
}
function left(sec){
  if(sec<10)return'a few seconds left';
  if(sec<55)return'~'+Math.round(sec/5)*5+' s left';
  const m=Math.round(sec/60);
  return m<=1?'about a minute left':'about '+m+' minutes left';
}
function paint(s){
  const q=parts(s.file||ask||'');
  $('#ovname').textContent=q.name||'Opening a file';
  $('#ovname').title=s.file||ask||'';
  $('#ovdir').textContent=q.dir;
  $('#ovstage').textContent=(s.stage||'Working')+((s.agent_status||'')?' — '+s.agent_status:'');
  const now=Date.now(), p=(typeof s.progress==='number')?s.progress:0;
  if(!t0)t0=now;
  // Elapsed time and the fraction it bought, which is the only estimate that needs
  // no guess about how the phases are weighted. It is rough by nature: the first
  // 11 % of the bar is the quick phases, so say "~" and leave it at that.
  const secs=(now-t0)/1000;
  let eta='';
  if(p>=0.04&&secs>=2){
    const rest=secs*(1-p)/p;
    if(isFinite(rest)&&rest>=0&&rest<1800)eta=left(rest);
  }
  $('#oveta').textContent=eta;
  setBar(p);
  $('#cancel').hidden=(s.cancellable===false);   // nothing to cancel, no button
}

let sticky=0;                                // until when the poller must leave this alone
function note(text,isErr,progress,hold){
  if(hold)sticky=Date.now()+12000; else if(!text)sticky=0;
  const box=$('#state'); box.innerHTML='';
  box.className='note'+(isErr?' err':'');
  box.hidden=!text;
  if(!text)return;
  box.append(document.createTextNode(text));
  if(progress){const bar=el('div','bar'); bar.append(el('i')); box.append(bar);}
}

function current(s){
  const box=$('#now'); box.innerHTML='';
  if(!s.file)return;
  const card=el('div','card now'),txt=el('div','txt'),q=parts(s.file);
  txt.append(el('b',null,q.name),el('span','sub',q.dir||q.name));
  txt.title=s.file;
  card.append(txt);
  if(s.export&&s.export.out){
    const out=parts(s.export.out),m=el('div','meta');
    m.append(el('div',null,'exported'),el('em',null,out.name));
    m.title=s.export.out;
    card.append(m);
  }
  const open=el('button','btn','Open the review'),close=el('button','btn sec','Close');
  open.onclick=()=>location='/review';
  close.onclick=async()=>{
    const r=await fetch('/close',{method:'POST'}).catch(()=>null);
    if(!r||!r.ok)return note('Could not close the file.',true,false,true);
    note(''); box.innerHTML='';
  };
  card.append(open,close); box.append(card);
}

async function tick(){
  let s=null;
  try{s=await (await fetch('/api/state')).json();}catch(e){return;}
  $('#quit').hidden=(s.surface!=='browser');   // a tab has no window to close
  if(s.status==='loading'){
    if(!watching){watching=true; resetEta(); showSheet();}
    paint(s);
    note('');
  }else if(watching){
    watching=false;
    if(s.status==='ready'){
      // The handoff lets the user see that the work completed; its guard also
      // handles a ready response arriving twice before navigation finishes.
      handoff();
      return;
    }
    if(s.status==='error')failSheet(s.error||'Could not open that file.');
    else{closeSheet(); note('Opening cancelled.',false,false,true);}
  }
  if(!watching&&!failed){
    if(s.status==='exporting'){
      note((s.stage||'Working')+((s.agent_status||'')?' — '+s.agent_status:''),false,true);
    }else if(s.status==='error'){
      note(s.error||'Could not open that file.',true,false,true);
    }else{
      if(Date.now()>sticky)note('');
      if(s.status==='ready') current(s); else $('#now').innerHTML='';
    }
  }
  setTimeout(tick, watching||s.status==='exporting'?900:2500);
}

$('#browse').onclick=async()=>{
  const btn=$('#browse'); btn.disabled=true;
  note('Choose a file in the dialog that just opened on this computer.');
  let r=null;
  try{r=await fetch('/browse',{method:'POST'});}catch(e){}
  btn.disabled=false;
  const body=r?await r.json().catch(()=>({})):{};
  if(!r||!r.ok){
    return note((body&&body.error)||'Native file browsing is unavailable.',true,false,true);
  }
  if(body.cancelled)return note('');
  openFile(body.path);
};
$('#cancel').onclick=async()=>{
  // Disable it here rather than wait for the poll to say the open is over: a second
  // click has nothing to cancel, and a button that answers twice looks like it lied.
  $('#cancel').disabled=true;
  $('#ovstage').textContent='Cancelling';
  try{await fetch('/cancel',{method:'POST'});}catch(e){}
};
$('#ovback').onclick=()=>{closeSheet(); note('');};
$('#quit').onclick=async ev=>{
  ev.preventDefault();
  const ok=await fetch('/shutdown',{method:'POST'}).then(r=>r.ok).catch(()=>false);
  note(ok?'The app has stopped. You can close this tab.':'Could not stop the app.',!ok,false,
       !ok?true:false);
};
tick();
</script></body></html>"""

# The brand is spelled once, in brand.py; the page is a template rather than an
# f-string because its CSS is full of braces.
_START_HTML = (
    _START_TEMPLATE.replace('__MARK__', brand.MARK_SVG)
    .replace('__APP_NAME__', brand.APP_NAME)
    .replace('__TAGLINE__', brand.TAGLINE)
    .replace('__PAGE_TITLE__', brand.PAGE_TITLE)
)


def _reveal(path) -> bool:
    """Show a file in the user's own file manager. Best effort: the path is always
    printed in the UI as well, so a missing helper costs nothing."""
    target = str(Path(path))
    if sys.platform == "darwin":
        cmd = ["open", "-R", target]
    elif os.name == "nt":
        cmd = ["explorer", "/select," + target]
    else:
        cmd = ["xdg-open", str(Path(target).parent)]
    try:
        return subprocess.run(cmd, check=False, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _recent_entries(open_path=None):
    """Build the recent-file API records, including the current-file flag."""
    open_resolved = None
    if open_path:
        try:
            open_resolved = Path(open_path).resolve()
        except OSError:
            open_resolved = None
    entries = []
    for raw in load_recent():
        p = Path(raw)
        cfg = config_path_for(raw)
        try:
            st = p.stat()
            entry = {
                "path": str(p),
                "exists": True,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "config_exists": cfg.exists(),
            }
        except OSError:
            entry = {
                "path": str(p),
                "exists": False,
                "size": 0,
                "mtime": 0,
                "config_exists": cfg.exists(),
            }
        if open_resolved is not None:
            try:
                entry["is_open"] = p.resolve() == open_resolved
            except OSError:
                entry["is_open"] = str(p) == str(open_path)
        entries.append(entry)
    return entries


def _pick_file():
    """Ask the desktop for a path and return it, or ``None`` if cancelled.

    A browser hands over a file's name but never its location, so the only way to give
    the start screen a real file dialog is to ask the machine the server runs on.
    """
    if sys.platform == "darwin":
        cmd = [
            "osascript",
            "-e",
            'POSIX path of (choose file with prompt "Choose an IONICON run")',
        ]
    elif os.name == "nt":
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Add-Type -AssemblyName System.Windows.Forms;"
            " $d = New-Object System.Windows.Forms.OpenFileDialog;"
            " $d.Filter = 'IONICON runs (*.h5)|*.h5|All files (*.*)|*.*';"
            " if ($d.ShowDialog() -eq 'OK') { [Console]::Out.Write($d.FileName) }",
        ]
    else:
        for tool, extra in (
            ("zenity", ["--file-selection"]),
            ("kdialog", ["--getexistingfile", "*"]),
        ):
            if shutil.which(tool):
                cmd = [tool] + extra
                break
        else:
            raise RuntimeError("Native file browsing is unavailable.")
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return done.stdout.strip() or None


def _browse():
    """Ask for a path using whichever dialog this machine offers, or ``None`` if
    cancelled.

    A desktop window owns a real dialog, and that is the one the reviewer is looking
    at. A browser tab owns none, so the machine is asked instead — its dialog can land
    behind the window, which is normal for a browser-based start screen.
    """
    window = desktop.current_window()
    if window is not None:
        try:
            return desktop.pick_file(window)
        except desktop.DesktopUnavailable:
            pass  # no dialog in the window after all; ask the machine itself
    return _pick_file()


def _background(fn, *args, **kwargs):
    """Run a long job off the request thread. The session is where the page reads the
    result or the failure, so the thread itself only echoes to stderr."""

    def run():
        try:
            fn(*args, **kwargs)
        except Exception as exc:
            print(f"sniff: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    threading.Thread(target=run, daemon=True).start()


def make_server(port=8765, agent_url=None, agent_timeout=300.0):
    """Build the app server on the first free localhost port.

    Returns ``(server, session, url)``. Kept separate from :func:`serve_app` so tests
    can drive the routes without blocking.
    """
    session = Session()

    class Handler(server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body=b"", ctype="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            route = urlparse(self.path)
            if route.path in ("/", "/index.html"):
                self._send(200, _START_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif route.path == "/review":
                if not session.payload:
                    self._send(404, {"error": "no file is open"})
                    return
                html = viz.render_html(
                    session.payload,
                    config_path=str(session.config_path or ""),
                    mode="app",
                )
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif route.path == "/api/state":
                self._send(200, session.state())
            elif route.path == "/status":
                self._send(200, session.status_payload())
            elif route.path == "/api/recent":
                self._send(200, _recent_entries(session.path))
            elif route.path == "/spectrum":
                if not session.path:
                    self._send(404, {"error": "no file is open"})
                    return
                q = parse_qs(route.query)
                try:
                    lo = int(q.get("lo", ["1"])[0])
                    hi = int(q.get("hi", ["1"])[0])
                    self._send(200, interval_spectrum(session.path, lo, hi))
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    self._send(500, {"error": str(exc)})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            try:
                body = json.loads(self.rfile.read(n) if n else b"{}")
            except (UnicodeError, json.JSONDecodeError, ValueError):
                self._send(400, {"error": "invalid JSON"})
                return
            if self.path == "/open":
                target = str(body.get("path") or "").strip()
                if not target:
                    self._send(400, {"error": "no path given"})
                    return
                if not Path(target).expanduser().is_file():
                    self._send(404, {"error": f"no such file: {target}"})
                    return
                if session.busy:
                    self._send(409, {"error": "the app is busy with the current file"})
                    return
                _background(
                    session.open, target, agent_url=agent_url, agent_timeout=agent_timeout
                )
                self._send(202, {"ok": True})
            elif self.path == "/save":
                if not session.config_path:
                    self._send(409, {"error": "no file is open"})
                    return
                if not _valid_config(body):
                    self._send(400, {"error": "config must contain peaks or ranges"})
                    return
                try:
                    _write_json(session.config_path, body)
                except OSError as exc:
                    # Say so rather than let the request thread die: the page needs a
                    # reason, and the config on disk is still the last good one.
                    self._send(500, {"error": f"could not write the config: {exc}"})
                    return
                session.config = body
                self._send(200, {"ok": True})
            elif self.path == "/export":
                if not session.path:
                    self._send(409, {"error": "no file is open"})
                    return
                if session.busy:
                    self._send(409, {"error": "an export is already running"})
                    return
                # The page posts the config it is showing. Autosave is debounced, so
                # exporting whatever happens to be on disk can describe an earlier
                # state than the one the reviewer just looked at.
                if _valid_config(body):
                    try:
                        _write_json(session.config_path, body)
                    except OSError as exc:
                        self._send(500, {"error": f"could not write the config: {exc}"})
                        return
                    session.config = body
                _background(session.export)
                self._send(202, {"ok": True})
            elif self.path == "/cancel":
                # Cancelling an open that is not running is not an event: the page's
                # Cancel button and a poll can cross, and the answer must not depend
                # on which one arrived first.
                session.cancel()
                self._send(200, {"ok": True})
            elif self.path == "/close":
                if session.busy:
                    self._send(409, {"error": "wait for the current work to finish"})
                    return
                session.close()
                self._send(200, {"ok": True})
            elif self.path == "/reveal":
                last = (session.export_result or {}).get("out")
                if not last:
                    self._send(409, {"error": "nothing has been exported yet"})
                    return
                self._send(200, {"ok": _reveal(last), "path": last})
            elif self.path == "/ack":
                self._send(200, {"ok": True})
            elif self.path == "/browse":
                try:
                    picked = _browse()
                except (
                    OSError,
                    RuntimeError,
                    subprocess.SubprocessError,
                    desktop.DesktopUnavailable,
                ) as exc:
                    self._send(501, {"error": str(exc) or "no file dialog here"})
                    return
                self._send(200, {"path": picked} if picked else {"cancelled": True})
            elif self.path == "/shutdown":
                # One direction each, so the two can never chase one another: the page
                # stops the session and closes the window, while closing the window
                # only ever stops the session. Nothing here waits for the other.
                stop_the_app(session)
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})

    httpd = None
    # port=0 asks the OS for a free port, which is what a test wants: probing upward
    # from a guessed number can land on a server a previous test has not released.
    candidates = [0] if port == 0 else range(port, port + 20)
    for candidate in candidates:
        try:
            # ThreadingHTTPServer, not a bare TCPServer: it sets allow_reuse_address,
            # so a restart lands back on the same port instead of drifting.
            httpd = server.ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        raise OSError("no free port found for the app server")
    httpd.daemon_threads = True
    actual = httpd.server_address[1]
    return httpd, session, f"http://127.0.0.1:{actual}/"


# How the running app reached the user: a window of its own, or a browser tab. Set by
# serve_app, read by /api/state, and the difference between "the app opened" and "the
# app opened a window", which no log line a windowed bundle can write would ever show.
_surface = "browser"


def surface() -> str:
    return _surface

def _log(text):
    """Report progress on stderr, and to a log file too when there is no console.

    A double-clicked ``.app`` bundle is started by LaunchServices and has nowhere to
    print, so its URL and its tracebacks would otherwise be lost.
    """
    print(text, file=sys.stderr, flush=True)
    if not getattr(sys, "frozen", False):
        return
    try:
        path = _recent_path().parent / "log.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError:
        pass  # a missing log file must not stop the app


def _install_quit_handlers(on_quit):
    """Make Ctrl-C, ``pkill`` and a bundle's quit all stop the server the same way."""
    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, lambda *_: on_quit())
        except (OSError, ValueError):
            pass  # not the main thread, or this platform has no such signal


def stop_the_app(session):
    """Stop the server, and take the desktop window with it if there is one.

    This is the one quit path, used by Ctrl-C, ``pkill``, a bundle's quit and the page's
    Stop button. In window mode the native loop owns the main thread, so a quit that
    only set the flag would leave a window standing over a dead server. With no window
    there is nothing to close, and :func:`desktop.close_window` says so by returning
    False; closing it is never a second way into the session, because the window's own
    close handler only ever sets the same flag.
    """
    session.stop.set()
    try:
        desktop.close_window()
    except desktop.DesktopUnavailable as exc:
        # The server is stopping either way; a window left standing is worth a line on
        # stderr, not a failed request or a swallowed quit.
        _log(f"sniff: {exc}; close that window yourself to get rid of it")


def serve_app(
    port=8765,
    open_browser=True,
    agent_url=None,
    agent_timeout=300.0,
    initial=None,
    window=False,
):
    """Serve the app until interrupted. Nothing here closes on its own: an export, a
    closed tab or a closed file all leave the server up.

    ``window=True`` asks for a desktop window instead of a browser tab: the native loop
    takes the main thread, which is why the server runs on a daemon thread behind it.
    A window that cannot start is never fatal — one line on stderr, then the browser
    route, because losing the session over losing the window is the worse trade.
    """
    httpd, session, url = make_server(
        port=port, agent_url=agent_url, agent_timeout=agent_timeout
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _install_quit_handlers(lambda: stop_the_app(session))

    _log(f"sniff: app running at {url}")
    _log("sniff: a large run takes 30-90 s to open; the app stays up between files.")
    if initial:
        _background(
            session.open, initial, agent_url=agent_url, agent_timeout=agent_timeout
        )
    global _surface
    # Decided fresh on every serve: a second call in the same process — a test, or a
    # script that restarts the app — must not inherit the previous run's surface.
    _surface = "browser"
    in_window = False
    if window:
        try:
            # Said before the call, because run_window does not return for the life of
            # the window: recorded afterwards, /api/state would answer "browser" at
            # every moment a window was actually on screen. run_window raises before it
            # blocks when there is no window to make, so this is not a promise it cannot
            # keep. Closing the window is the one thing it may do to the session, and it
            # is the reverse of the /shutdown route, which stops the session first.
            _surface = "window"
            desktop.run_window(url, on_close=session.stop.set)
            in_window = True
        except desktop.DesktopUnavailable as exc:
            _surface = "browser"
            _log(f"sniff: no desktop window ({exc}); opening a browser instead")
    if not in_window and open_browser:
        try:
            if not webbrowser.open(url):
                raise OSError("no browser answered")
        except (OSError, webbrowser.Error) as exc:
            # In a double-clicked bundle this line is the only way the user learns the
            # server is up, so it has to carry the address.
            _log(f"sniff: could not open a browser ({exc}); open {url} in one yourself")
    session.stop.wait()
    session.close()
    httpd.shutdown()
    httpd.server_close()
