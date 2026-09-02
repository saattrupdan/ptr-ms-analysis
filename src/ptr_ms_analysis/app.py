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
import socketserver
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http import server
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import h5py

from . import viz
from .analyze import (
    analyze_config_to_csv,
    auto_peaks,
    auto_ranges,
    interval_spectrum,
    resolve_analysis_settings,
    resolve_x_axis_unit,
)

RECENT_PATH = Path.home() / ".ptr-ms" / "recent.json"
RECENT_LIMIT = 20


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
    """Write through a temporary name so an interrupted save cannot truncate a good
    config — the file is the user's work product."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def config_path_for(h5_path: str) -> Path:
    """The config file belonging to an h5 file: same name, same folder.

    ``~/d/ptr.h5`` -> ``~/d/ptr.json``. A ``<stem>-analysis-config.json`` written by
    the older CLI flow is honoured when no ``<stem>.json`` exists yet, so opening a
    previously reviewed file does not start a fresh pipeline run. A same-stem JSON
    that is not one of our configs is never overwritten: a ``<stem>.ptr.json`` is
    used instead.
    """
    p = Path(h5_path).expanduser()
    beside = p.with_suffix(".json")
    legacy = p.parent / f"{p.stem}-analysis-config.json"
    if beside.exists():
        if _valid_config(_read_json(beside)):
            return beside
        return p.with_suffix(".ptr.json")
    if legacy.exists() and _valid_config(_read_json(legacy)):
        return legacy
    return beside


def _instrument(f) -> str:
    value = f.attrs.get("InstrumentType", "unknown")
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


def bootstrap_config(h5_path: str, f=None) -> dict:
    """Build a config from the file alone, with no agent and no judgement calls.

    Peaks and intervals come from the deterministic pipeline; the checklist says
    plainly what was decided automatically and what still needs a human. ``f`` may be
    an already-open file, since opening a 2 GB run costs tens of seconds.
    """
    own = f is None
    source = h5py.File(h5_path, "r") if own else f
    try:
        peaks = auto_peaks(source)
        ranges = auto_ranges(source)
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
    return {
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


def load_recent() -> list:
    value = _read_json(RECENT_PATH)
    return value if isinstance(value, list) else []


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
        self.export_result = None
        self.agent_status = None
        self._file = None
        self._lock = threading.Lock()
        self._opening = False

    # ---- opening -------------------------------------------------------------
    def open(self, path, agent_url=None, agent_timeout=300.0):
        """Load ``path``, making a config first if the file has never been reviewed.

        The h5 file is opened once and reused for detection and for the review data:
        reopening a large file costs the user another 30-90 s for nothing.
        """
        with self._lock:
            if self._opening:
                raise RuntimeError("a file is already opening")
            self._opening = True
        try:
            self.close()
            self.status, self.stage, self.error = "loading", "Opening the file", None
            self.agent_status = None
            self.export_result = None
            path = str(Path(path).expanduser().resolve())
            config_path = config_path_for(path)
            self._file = h5py.File(path, "r")
            try:
                config = _read_json(config_path) if config_path.exists() else None
                if config is not None and not _valid_config(config):
                    raise ValueError(f"{config_path} is not a ptr config")
                if config is None:
                    self.stage = "Detecting peaks and intervals"
                    config = bootstrap_config(path, f=self._file)
                    _write_json(config_path, config)
                    if agent_url:
                        config = self._ask_agent(
                            config, path, config_path, agent_url, agent_timeout
                        )
                self.stage = "Computing the review data"
                self.payload = self._payload(path, config)
            except Exception:
                self.close()
                raise
            self.path, self.config_path, self.config = path, config_path, config
            self.status, self.stage = "ready", "Ready"
            remember_recent(path)
            return self.payload
        except Exception as exc:
            self.close()
            self.status, self.error, self.stage = "error", str(exc), "Failed to open"
            raise
        finally:
            with self._lock:
                self._opening = False

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
            if not _valid_config(answer):
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

    def _payload(self, path, config):
        settings = resolve_analysis_settings(config)
        return viz.build_viz_data(
            self._file,
            config.get("peaks", []),
            config.get("ranges", []),
            analysis_settings=settings,
            config_base=config,
            checklist=config.get("checklist"),
            x_axis_unit=resolve_x_axis_unit(config),
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
        self.status, self.stage = "empty", ""

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
        out = str(Path(self.path).with_suffix(".csv"))
        try:
            result = analyze_config_to_csv(self.path, self.config, out)
        except Exception as exc:
            # The file is still open and still worth reviewing, so a failed export
            # reports the error without dropping the session into an error state.
            self.status, self.error, self.stage = "ready", str(exc), "Export failed"
            raise
        result["out"] = out
        self.export_result = result
        self.status, self.stage = "ready", "Ready"
        return result

    # ---- state ---------------------------------------------------------------
    def status_payload(self) -> dict:
        """Export progress in the shape the review page's overlay already polls, so
        the app and the one-shot CLI share one piece of UI code."""
        if self.status == "exporting":
            return {"status": "running"}
        if self.stage == "Export failed":
            return {"status": "error", "error": self.error or "the analysis failed"}
        if self.export_result:
            return {"status": "done", "out": self.export_result.get("out")}
        return {"status": "idle"}

    def state(self) -> dict:
        return {
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "file": self.path,
            "config": str(self.config_path) if self.config_path else None,
            "agent_status": self.agent_status,
            "export": self.export_result,
        }


_START_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>PTR-MS review</title>
<style>
  :root{--bg:#0e141b;--panel:#161f29;--panel2:#1c2733;--fg:#e6edf3;--mut:#8b98a5;
        --line:#26313d;--acc:#4c8dff;--hi:#f59e0b}
  @media(prefers-color-scheme:light){:root{--bg:#f5f7fa;--panel:#fff;--panel2:#eef2f7;
        --fg:#1a2027;--mut:#5c6775;--line:#d7dee8;--acc:#2563eb;--hi:#b45309}}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:13px/1.5 -apple-system,system-ui,"Segoe UI",sans-serif}
  main{max-width:820px;margin:8vh auto;padding:0 22px}
  h1{font-size:19px;margin:0 0 4px}
  p{color:var(--mut);margin:0 0 22px}
  form{display:flex;gap:8px}
  input{flex:1;padding:9px 11px;background:var(--panel);color:var(--fg);
        border:1px solid var(--line);border-radius:8px;font:inherit}
  button{padding:9px 14px;background:var(--acc);color:#fff;border:0;border-radius:8px;
         font:inherit;cursor:pointer}
  h2{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);
     margin:26px 0 8px;font-weight:600}
  ul{list-style:none;margin:0;padding:0}
  li{display:flex;gap:10px;align-items:baseline;padding:8px 11px;border:1px solid var(--line);
     border-radius:8px;margin-bottom:6px;cursor:pointer;background:var(--panel)}
  li:hover{border-color:var(--acc)}
  .p{font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
  .m{color:var(--mut);font-size:11px;white-space:nowrap}
  .cfg{color:var(--hi);font-size:11px}
  #state{margin-top:20px;min-height:20px;color:var(--mut)}
  #state.err{color:#f87171}
  .open{display:flex;gap:10px;align-items:center;padding:10px 12px;margin-bottom:6px;
        border:1px solid var(--acc);border-radius:8px;background:var(--panel)}
  .open .p{flex:1;font-weight:500;overflow:hidden;text-overflow:ellipsis}
  button.ghost{background:transparent;color:var(--mut);border:1px solid var(--line)}
</style></head><body><main>
  <h1>PTR-MS review</h1>
  <p>Open an IONICON <code>.h5</code> file. A file that has been reviewed before
     reopens with its saved config; a new one is processed automatically first.</p>
  <form id="open"><input id="path" placeholder="/path/to/run.h5" required autocomplete="off">
    <button type="submit">Open</button></form>
  <h2>Recent</h2><ul id="recent"></ul>
  <div id="openwrap"></div>
  <div id="state"></div>
</main><script>
const $=s=>document.querySelector(s);
async function recent(){
  const items=await (await fetch('/api/recent')).json();
  $('#recent').innerHTML=items.map(e=>`<li data-path="${e.path}">`+
    `<span class="p">${e.path}</span>`+
    (e.exists?`<span class="m">${(e.size/1073741826).toFixed(2)} GB</span>`:'<span class="m">missing</span>')+
    (e.config_exists?'<span class="cfg">config saved</span>':'<span class="m">new</span>')+
    `</li>`).join('')||'<li style="cursor:default"><span class="m">Nothing opened yet.</span></li>';
  document.querySelectorAll('#recent li[data-path]').forEach(el=>
    el.onclick=()=>{$('#path').value=el.dataset.path;$('#path').focus();});
}
async function openFile(path){
  const r=await fetch('/open',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path})});
  if(!r.ok) return show(((await r.json())||{}).error||'Could not open that file',true);
  $('#openwrap').innerHTML=''; tick();
}
function show(t,isErr){$('#state').textContent=t||'';$('#state').className=isErr?'err':'';}
function current(s){
  const done=s.export?`<span class="m">last export ${s.export.out||''}</span>`:'';
  $('#openwrap').innerHTML=`<div class="open"><span class="p">${s.file||''}</span>${done}`+
    `<button id="resume">Open the review</button>`+
    `<button class="ghost" id="closefile">Close file</button></div>`;
  $('#resume').onclick=()=>location='/review';
  $('#closefile').onclick=async()=>{ await fetch('/close',{method:'POST'});
    $('#openwrap').innerHTML=''; recent(); };
  show(s.agent_status||'');
}
async function tick(){
  let s; try{ s=await (await fetch('/api/state')).json(); }catch(e){ return; }
  if(s.status==='loading'||s.status==='exporting'){
    show((s.stage||'Working')+(s.agent_status?' — '+s.agent_status:''));
    return setTimeout(tick,900);
  }
  if(s.status==='error') return show(s.error||'Could not open that file',true);
  if(s.status==='ready') return current(s);
  show('');
}
$('#open').onsubmit=e=>{e.preventDefault();openFile($('#path').value.trim());};
recent(); tick();
</script></body></html>"""


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


def _recent_entries():
    entries = []
    for raw in load_recent():
        p = Path(raw)
        cfg = config_path_for(raw)
        try:
            st = p.stat()
            entries.append(
                {
                    "path": str(p),
                    "exists": True,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "config_exists": cfg.exists(),
                }
            )
        except OSError:
            entries.append(
                {
                    "path": str(p),
                    "exists": False,
                    "size": 0,
                    "mtime": 0,
                    "config_exists": cfg.exists(),
                }
            )
    return entries


def _background(fn, *args, **kwargs):
    """Run a long job off the request thread. The session is where the page reads the
    result or the failure, so the thread itself only echoes to stderr."""

    def run():
        try:
            fn(*args, **kwargs)
        except Exception as exc:
            print(f"ptr: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

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
                self._send(200, _recent_entries())
            elif route.path == "/browse":
                q = parse_qs(route.query)
                home = Path.home().resolve()
                target = Path(q.get("dir", [str(home)])[0]).expanduser().resolve()
                if target != home and home not in target.parents:
                    self._send(403, {"error": "only paths under the home folder"})
                    return
                try:
                    rows = sorted(
                        (
                            {"name": x.name, "path": str(x), "directory": x.is_dir()}
                            for x in target.iterdir()
                            if x.is_dir() or x.suffix.lower() in (".h5", ".hdf5")
                        ),
                        key=lambda r: (not r["directory"], r["name"].lower()),
                    )
                    self._send(200, {"dir": str(target), "entries": rows})
                except OSError as exc:
                    self._send(400, {"error": str(exc)})
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
                if session.status == "loading":
                    self._send(409, {"error": "another file is still opening"})
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
                session.config = body
                _write_json(session.config_path, body)
                self._send(200, {"ok": True})
            elif self.path == "/export":
                if not session.path:
                    self._send(409, {"error": "no file is open"})
                    return
                _background(session.export)
                self._send(202, {"ok": True})
            elif self.path == "/close":
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
            else:
                self._send(404, {"error": "not found"})

    httpd = None
    for candidate in range(port, port + 20):
        try:
            httpd = socketserver.ThreadingTCPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        raise OSError("no free port found for the app server")
    httpd.daemon_threads = True
    return httpd, session, f"http://127.0.0.1:{port}/"


def serve_app(
    port=8765,
    open_browser=True,
    agent_url=None,
    agent_timeout=300.0,
    initial=None,
):
    """Serve the app until interrupted. Nothing here closes on its own: an export, a
    closed tab or a closed file all leave the server up."""
    httpd, session, url = make_server(
        port=port, agent_url=agent_url, agent_timeout=agent_timeout
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print(f"ptr: app running at {url}", file=sys.stderr, flush=True)
    print(
        "ptr: a large run takes 30-90 s to open; the app stays up between files.",
        file=sys.stderr,
        flush=True,
    )
    if initial:
        _background(
            session.open, initial, agent_url=agent_url, agent_timeout=agent_timeout
        )
    if open_browser:
        try:
            webbrowser.open(url)
        except (OSError, webbrowser.Error):
            pass
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
        httpd.shutdown()
        httpd.server_close()
