"""Persistent local review application backend for PTR-MS files."""
from __future__ import annotations

import json
import socketserver
import sys
import threading
import urllib.request
import webbrowser
from http import server
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import h5py

from . import viz
from .analyze import (
    analyze_config_to_csv,
    auto_peaks,
    auto_ranges,
    interval_spectrum,
    resolve_analysis_settings,
)

RECENT_PATH = Path.home() / ".ptr-ms" / "recent.json"
START_HTML = """<!doctype html><html><head><meta charset='utf-8'><title>PTR-MS review</title>
<style>:root{--bg:#101820;--card:#192630;--fg:#e8eef2;--muted:#9fb0ba;--accent:#63c5b8;--line:#344651}
*{box-sizing:border-box}body{font:14px system-ui;margin:0;background:var(--bg);color:var(--fg)}main{max-width:850px;margin:10vh auto;padding:32px;background:var(--card);border:1px solid var(--line);border-radius:8px}h1{font-size:22px}input{width:75%;padding:10px;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:4px}button{padding:10px 16px;background:var(--accent);border:0;border-radius:4px;color:#102027;cursor:pointer}li{margin:9px 0;color:var(--muted);cursor:pointer}small{color:var(--muted)}#state{margin-top:18px;color:var(--accent)}</style></head><body><main>
<h1>PTR-MS review</h1><p>Open an IONICON <code>.h5</code> file to begin.</p><form id='open'><input id='path' required placeholder='/absolute/path/to/file.h5'><button>Open</button></form><h2>Recent files</h2><ul id='recent'></ul><div id='state'></div></main>
<script>async function recent(){let r=await fetch('/api/recent');let x=await r.json();document.querySelector('#recent').innerHTML=x.map(e=>`<li data-path="${e.path}">${e.path}<br><small>${e.exists?'':'missing'} ${e.config_exists?'· config found':''}</small></li>`).join('');document.querySelectorAll('li').forEach(e=>e.onclick=()=>{path.value=e.dataset.path})}recent();document.querySelector('#open').onsubmit=async e=>{e.preventDefault();await fetch('/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:path.value})});poll()};async function poll(){let s=await (await fetch('/api/state')).json();state.textContent=s.stage||s.status;if(s.status==='loading'||s.status==='exporting')setTimeout(poll,1000);else if(s.status==='ready')location='/review'}setInterval(()=>{if(state.textContent)poll()},1000)</script></body></html>"""


def _valid_config(value):
    return isinstance(value, dict) and ("peaks" in value or "ranges" in value)


def config_path_for(h5_path: str) -> Path:
    p = Path(h5_path).expanduser()
    beside = p.with_suffix(".json")
    try:
        value = json.loads(beside.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        value = None
    if beside.exists():
        if _valid_config(value):
            return beside
        # An unrelated same-stem JSON must never be overwritten.  This rule
        # deliberately takes precedence over the legacy filename.
        return p.with_suffix(".ptr.json")
    legacy = p.parent / "ptr-analysis-config.json"
    try:
        value = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        value = None
    if _valid_config(value):
        return legacy
    return p.with_suffix(".ptr.json") if beside.exists() else beside


def _instrument(f):
    value = f.attrs.get("InstrumentType", "unknown")
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


def bootstrap_config(h5_path: str) -> dict:
    with h5py.File(h5_path, "r") as f:
        peaks = auto_peaks(f)
        ranges = auto_ranges(f)
        ncyc = int(f["SPECdata/Intensities"].shape[0])
        instrument = _instrument(f)
    settings = resolve_analysis_settings({})
    analyze = {k: v for k, v in settings.items() if k != "sources"}
    checklist = [
        "This configuration was generated automatically; a human must review it.",
        f"Automatically found {len(peaks)} peaks and {len(ranges)} intervals.",
        "Check interval class corrections (sample versus background).",
        "Confirm chemistry assignments for the detected channels.",
        "Check for artifact channels and remove any that are not real analytes.",
    ]
    if not peaks:
        checklist.insert(1, "No signal was detected: this file has no beam or usable signal.")
    return {"peaks": peaks, "ranges": ranges, "analyze": analyze,
            "viz": {"x_axis_unit": "cycle"}, "checklist": checklist,
            "diagnostics": {"n_peaks": len(peaks), "n_ranges": len(ranges),
                            "ncyc": ncyc, "instrument": instrument}}


def load_recent():
    try:
        value = json.loads(RECENT_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []


def remember_recent(path):
    path = str(Path(path).expanduser().resolve())
    values = [p for p in load_recent() if p != path]
    values.insert(0, path)
    RECENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECENT_PATH.write_text(json.dumps(values[:20], indent=2), encoding="utf-8")
    return values[:20]


class Session:
    def __init__(self):
        self.path = None
        self.config_path = None
        self.config = None
        self.payload = None
        self.status = "empty"
        self.stage = ""
        self.error = None
        self.export_result = None
        self.agent_status = None
        self._file = None
        self._lock = threading.Lock()
        self._opening = False

    def open(self, path, agent_url=None, agent_timeout=300.0):
        with self._lock:
            if self._opening:
                raise RuntimeError("a file is already opening")
            self._opening = True
        try:
            self.close()
            self.status, self.stage, self.error = "loading", "Loading file", None
            self.agent_status = None
            path = str(Path(path).expanduser().resolve())
            cfg_path = config_path_for(path)
            if cfg_path.exists():
                try:
                    config = json.loads(cfg_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"invalid config: {exc}")
                if not _valid_config(config):
                    raise ValueError("config must contain peaks or ranges")
            else:
                self.stage = "Generating automatic configuration"
                config = bootstrap_config(path)
                cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
                if agent_url:
                    self.stage = "Requesting agent review"
                    with h5py.File(path, "r") as source:
                        diagnostics = {"n_peaks": len(config.get("peaks", [])),
                            "n_ranges": len(config.get("ranges", [])),
                            "ncyc": int(source["SPECdata/Intensities"].shape[0]),
                            "instrument": _instrument(source)}
                    body = json.dumps({"file": path, "config": config,
                        "diagnostics": diagnostics}).encode()
                    try:
                        req = urllib.request.Request(agent_url, body,
                            {"Content-Type": "application/json"}, method="POST")
                        with urllib.request.urlopen(req, timeout=agent_timeout) as response:
                            candidate = json.loads(response.read().decode())
                        candidate = candidate.get("config", candidate) if isinstance(candidate, dict) else None
                        if not _valid_config(candidate):
                            raise ValueError("agent response is not a config")
                        config = candidate
                        cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
                    except Exception as exc:  # agent failure must never lose deterministic work
                        self.agent_status = f"Agent review failed: {str(exc)[:160]}"
            self.stage = "Computing review data"
            settings = resolve_analysis_settings(config)
            self._file = h5py.File(path, "r")
            self.payload = viz.build_viz_data(self._file, config.get("peaks", []),
                config.get("ranges", []), analysis_settings=settings,
                config_base=config, checklist=config.get("checklist"),
                x_axis_unit=(config.get("viz") or {}).get("x_axis_unit", "cycle"))
            self.path, self.config_path, self.config = path, cfg_path, config
            self.status, self.stage = "ready", "Ready"
            remember_recent(path)
            return self.payload
        except Exception as exc:
            self.close()
            self.status, self.error, self.stage = "error", str(exc), "Error"
            raise
        finally:
            with self._lock:
                self._opening = False

    def close(self):
        if self._file is not None:
            self._file.close()
        self._file = self.path = self.config_path = self.config = self.payload = None
        self.status, self.stage = "empty", ""

    def export(self):
        if not self.path or self.config is None:
            raise RuntimeError("no file is open")
        self.status, self.stage = "exporting", "Running full-precision analysis"
        out = str(Path(self.path).with_suffix(".csv"))
        try:
            result = analyze_config_to_csv(self.path, self.config, out)
            self.export_result = result
            self.status, self.stage = "ready", "Ready"
            return result
        except Exception as exc:
            self.status, self.error, self.stage = "error", str(exc), "Export failed"
            raise


def serve_app(port=8765, open_browser=True, agent_url=None, agent_timeout=300.0):
    session = Session()
    def run_open(path):
        try: session.open(path, agent_url, agent_timeout)
        except Exception: pass
    def run_export():
        try: session.export()
        except Exception: pass
    class Handler(server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, code, value, ctype="application/json"):
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            route = urlparse(self.path)
            if route.path == "/": self.reply(200, START_HTML, "text/html; charset=utf-8")
            elif route.path == "/review":
                if not session.payload: self.reply(404, {"error": "no file open"})
                else: self.reply(200, viz.render_html(session.payload, config_path=session.config_path), "text/html; charset=utf-8")
            elif route.path == "/api/state":
                self.reply(200, {"status": session.status, "stage": session.stage, "error": session.error, "agent_status": session.agent_status, "export": session.export_result})
            elif route.path == "/api/recent":
                items=[]
                for p in load_recent():
                    q=Path(p); c=config_path_for(p)
                    try: st=q.stat(); items.append({"path":p,"exists":True,"size":st.st_size,"mtime":st.st_mtime,"config_exists":c.exists()})
                    except OSError: items.append({"path":p,"exists":False,"size":None,"mtime":None,"config_exists":c.exists()})
                self.reply(200, items)
            elif route.path == "/browse":
                d=Path(unquote(parse_qs(route.query).get("dir", [str(Path.home())])[0])).expanduser().resolve()
                home=Path.home().resolve()
                if d != home and home not in d.parents: self.reply(403,{"error":"outside home"}); return
                try: self.reply(200,[{"name":x.name,"path":str(x),"directory":x.is_dir()} for x in sorted(d.iterdir()) if x.is_dir()])
                except OSError as exc: self.reply(400,{"error":str(exc)})
            elif route.path == "/spectrum":
                if not session.path: self.reply(404,{"error":"no file open"}); return
                q=parse_qs(route.query); self.reply(200, interval_spectrum(session.path, q.get("lo",[1])[0], q.get("hi",[1])[0]))
            else: self.reply(404,{"error":"not found"})
        def do_POST(self):
            try: data=json.loads(self.rfile.read(int(self.headers.get("Content-Length",0)) or 0) or b"{}")
            except (ValueError, UnicodeError): self.reply(400,{"error":"invalid JSON"}); return
            if self.path == "/open":
                if session.status == "loading": self.reply(409,{"error":"a file is already opening"}); return
                threading.Thread(target=run_open,args=(data.get("path"),),daemon=True).start(); self.reply(202,{"ok":True})
            elif self.path == "/save":
                if not session.config_path: self.reply(409,{"error":"no file open"}); return
                if not _valid_config(data): self.reply(400,{"error":"config must contain peaks or ranges"}); return
                session.config=data; session.config_path.write_text(json.dumps(data,indent=2),encoding="utf-8"); self.reply(200,{"ok":True})
            elif self.path == "/export":
                threading.Thread(target=run_export,daemon=True).start(); self.reply(202,{"ok":True})
            elif self.path == "/close": session.close(); self.reply(200,{"ok":True})
            else: self.reply(404,{"error":"not found"})
    httpd=None
    for p in range(port,port+20):
        try: httpd=socketserver.ThreadingTCPServer(("127.0.0.1",p),Handler); port=p; break
        except OSError: pass
    if httpd is None: raise OSError("no free port found for app server")
    httpd.daemon_threads=True
    url=f"http://127.0.0.1:{port}/"
    print(f"ptr: app running at {url}",file=sys.stderr,flush=True)
    print("ptr: large files take 30-90 s to open",file=sys.stderr,flush=True)
    if open_browser:
        try: webbrowser.open(url)
        except (OSError, webbrowser.Error): pass
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
    finally: httpd.server_close()


__all__ = ["config_path_for", "bootstrap_config", "Session", "load_recent", "remember_recent", "serve_app"]
