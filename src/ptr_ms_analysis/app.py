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
import urllib.error
import urllib.request
import webbrowser
from http import server
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import h5py

from . import brand, desktop, viz
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
    """Where recent files are remembered. ``PTR_RECENT_PATH`` overrides it so a
    packaged build can be exercised (or a home folder kept clean) without patching
    Python in a frozen bundle."""
    override = os.environ.get("PTR_RECENT_PATH")
    return Path(override).expanduser() if override else Path.home() / ".ptr-ms" / "recent.json"


RECENT_PATH = _recent_path()
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


# A ptr summary starts with this header; anything else that turns up under the name
# we would like to write is somebody else's table and stays untouched.
_CSV_MARKERS = ("Variable", "Average(Corrected)")


def _csv_target(h5_path: str) -> Path:
    """Where an export of ``h5_path`` goes: ``<stem>.csv`` beside it, unless a file
    that is not a ptr summary already lives there — a Viewer or Excel export often
    does, and the review may be comparing against it."""
    target = Path(h5_path).with_suffix(".csv")
    if not target.exists():
        return target
    try:
        with target.open("r", encoding="utf-8-sig", errors="replace") as handle:
            head = handle.readline()
    except OSError:
        return target.parent / (target.stem + "-ptr.csv")
    if all(marker in head for marker in _CSV_MARKERS):
        return target
    return target.parent / (target.stem + "-ptr.csv")


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
        self.export_result = None
        self.export_error = None
        self.agent_status = None
        self._file = None
        self._lock = threading.Lock()
        self._opening = False
        # A double-clicked bundle has no terminal to press Ctrl-C in, so stopping the
        # server is something the page has to be able to ask for.
        self.stop = threading.Event()

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
            merge_note=config.get("merge_note") or "",
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
        return {
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "file": self.path,
            "config": str(self.config_path) if self.config_path else None,
            "agent_status": self.agent_status,
            "export": self.export_result,
            # "window" or "browser": how the user is looking at this app right
            # now. A bundle that meant to open a window and did not has to be able to say
            # so — otherwise the only evidence is a tab the user has to notice.
            "surface": surface(),
        }


_START_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__PAGE_TITLE__</title>
<style>
:root{
  --bg:#f5f6f8;--card:#fff;--sunk:#f7f8fa;--fg:#131a22;--mut:#5f6b78;
  --line:#e2e6ec;--line2:#eef1f5;--acc:#2f6feb;--accc:#fff;--ok:#0f7b4f;
  --err:#b3261e;--errbg:#fdf0ef;--ring:rgba(47,111,235,.30);
}
@media(prefers-color-scheme:dark){:root{
  --bg:#0d1117;--card:#151b23;--sunk:#111721;--fg:#e6edf3;--mut:#8b98a6;
  --line:#28313c;--line2:#1e252e;--acc:#4d8dff;--accc:#0b1220;--ok:#41b883;
  --err:#ff6b60;--errbg:#2a1613;--ring:rgba(77,141,255,.40);
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);-webkit-font-smoothing:antialiased;
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
main{max-width:660px;margin:0 auto;padding:60px 24px 44px}
.head{display:flex;gap:12px;align-items:center;margin-bottom:10px}
svg.brand{flex:none;width:40px;height:40px;border-radius:10px;
  box-shadow:0 1px 4px rgba(0,0,0,.35)}
.tag{font-weight:400;color:var(--mut);font-size:15px;letter-spacing:0}
h1{margin:0;font-size:20px;font-weight:600;letter-spacing:-.015em}
.lede{margin:0 0 26px;color:var(--mut)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
  box-shadow:0 1px 1px rgba(16,24,40,.04),0 8px 24px -16px rgba(16,24,40,.30)}
.now{display:flex;gap:14px;align-items:center;padding:14px 16px;margin-bottom:20px;
  border-color:var(--acc)}
.now .txt{min-width:0;flex:1}
.now b{display:block;font-weight:600;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.now .sub{display:block;color:var(--mut);font-size:12px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.pick{padding:24px 22px;text-align:center}
.pick h2{margin:0 2px 4px;font-size:15px;font-weight:600}
.pick p{margin:0 0 16px;color:var(--mut);font-size:13px}
.btn{appearance:none;border:0;border-radius:9px;background:var(--acc);color:var(--accc);
  font:inherit;font-weight:550;padding:9px 15px;cursor:pointer}
.btn:hover{filter:brightness(1.07)}
.btn:disabled{opacity:.55;cursor:default;filter:none}
.btn.sec{background:transparent;color:var(--fg);border:1px solid var(--line);font-weight:500}
.btn.sec:hover{background:var(--sunk)}
.row{display:flex;gap:8px;max-width:470px;margin:0 auto}
input[type=text]{flex:1;min-width:0;padding:9px 11px;background:var(--sunk);
  color:var(--fg);border:1px solid var(--line);border-radius:9px;font:13px/1.4 inherit}
input[type=text]:focus-visible,.btn:focus-visible,.link:focus-visible,
li:focus-visible{outline:2px solid var(--ring);outline-offset:2px}
.or{display:flex;align-items:center;gap:10px;margin:16px auto;max-width:470px;
  color:var(--mut);font-size:11px;letter-spacing:.07em;text-transform:uppercase}
.or::before,.or::after{content:"";flex:1;height:1px;background:var(--line)}
section{margin-top:30px}
h3{margin:0 0 10px;font-size:11px;font-weight:600;letter-spacing:.07em;
  text-transform:uppercase;color:var(--mut)}
ul{list-style:none;margin:0;padding:0}
li{display:flex;gap:12px;align-items:center;padding:11px 14px;cursor:pointer;
  border-bottom:1px solid var(--line2)}
li:last-child{border-bottom:0}
li:hover{background:var(--sunk)}
.glyph{flex:none;width:32px;height:32px;border-radius:8px;background:var(--sunk);
  border:1px solid var(--line);display:grid;place-items:center;font-size:10px;
  font-weight:600;color:var(--mut)}
.glyph.gone{color:var(--err)}
.nm{min-width:0;flex:1}
.nm b{display:block;font-weight:550;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nm span{display:block;color:var(--mut);font-size:12px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.meta{flex:none;text-align:right;font-size:12px;color:var(--mut)}
.meta em{display:block;font-style:normal}
.meta .on{color:var(--ok)}
.meta .miss{color:var(--err)}
.go{flex:none;color:var(--mut);opacity:0;font-size:15px}
li:hover .go,li:focus-visible .go{opacity:1}
#empty{padding:16px;color:var(--mut);font-size:13px;text-align:center}
.note{margin-top:18px;padding:11px 14px;border-radius:10px;background:var(--sunk);
  color:var(--mut);font-size:13px}
.note[hidden]{display:none}
.note.err{background:var(--errbg);color:var(--err)}
.bar{height:2px;margin-top:9px;border-radius:2px;background:var(--line);overflow:hidden}
.bar i{display:block;height:100%;width:35%;background:var(--acc);
  animation:slide 1.5s ease-in-out infinite}
@keyframes slide{from{transform:translateX(-100%)}to{transform:translateX(380%)}}
@media(prefers-reduced-motion:reduce){.bar i{animation:none;width:100%;opacity:.5}}
footer{display:flex;gap:12px;align-items:center;justify-content:space-between;
  margin-top:32px;color:var(--mut);font-size:12px}
.link{background:none;border:0;padding:0;color:var(--mut);font:inherit;
  text-decoration:underline;cursor:pointer}
.link:hover{color:var(--fg)}
</style></head><body><main>
  <div class="head">__MARK__<h1>__APP_NAME__ <span class="tag">__TAGLINE__</span></h1></div>
  <p class="lede">Open an IONICON run to review its peaks and intervals. A file you have
    reviewed before reopens with its saved config; a new one is processed first.</p>

  <div id="now"></div>

  <div class="card pick">
    <h2>Open an IONICON run</h2>
    <p>Choose a file on this computer, or type the path to one.</p>
    <div class="row">
      <input id="path" type="text" placeholder="/path/to/run.h5" spellcheck="false"
             autocomplete="off">
      <button class="btn" id="go" type="button">Open</button>
    </div>
    <div class="or">or</div>
    <button class="btn sec" id="browse" type="button">Browse this computer&hellip;</button>
  </div>

  <section>
    <h3>Recent</h3>
    <div class="card"><ul id="recent"></ul><div id="empty" hidden></div></div>
  </section>

  <div class="note" id="state" role="status" aria-live="polite" hidden></div>

  <footer>
    <span>Served from 127.0.0.1 &mdash; nothing leaves this computer.</span>
    <button class="link" id="quit" type="button">Stop the app</button>
  </footer>
</main><script>
const $=s=>document.querySelector(s);
let shown=null;                                  // the file the recents list was built for

function human(b){const u=['B','KB','MB','GB','TB'];let v=b||0,i=0;
  while(v>=1024&&i<u.length-1){v/=1024;i++;}
  return (i&&v<10?v.toFixed(1):Math.round(v))+' '+u[i];}
function when(t){if(!t)return'';const d=new Date(t*1000),mid=new Date();
  mid.setHours(0,0,0,0);const days=Math.round((mid-d)/864e5);
  if(days<=0)return'today';
  if(days===1)return'yesterday';
  if(days<14)return days+' days ago';
  return d.toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'});}
const SEP=String.fromCharCode(92);          // Windows separators, without a literal
function parts(p){const s=String(p).split(SEP).join('/'),i=s.lastIndexOf('/');
  if(i<0)return{name:s,dir:''};
  return{name:s.slice(i+1),dir:i===0?'/':s.slice(0,i)}}
function el(tag,cls,text){const n=document.createElement(tag);
  if(cls)n.className=cls; if(text!=null)n.textContent=text; return n;}

async function recent(){
  let items=[];
  try{items=await (await fetch('/api/recent')).json();}catch(e){}
  const ul=$('#recent'); ul.innerHTML='';
  const list=items.filter(e=>!e.is_open);
  const msg=$('#empty');
  msg.hidden=list.length>0;
  if(!list.length)msg.textContent=items.length
    ?'The only file you have opened is the one above.'
    :'Nothing opened yet. Files you review will be listed here.';
  for(const e of list){
    const li=el('li'); li.dataset.path=e.path; li.tabIndex=0;
    li.title=e.path;
    li.append(el('div','glyph'+(e.exists?'':' gone'),e.exists?'H5':'!'));
    const nm=el('div','nm'),q=parts(e.path);
    nm.append(el('b',null,q.name),el('span',null,q.dir));
    const m=el('div','meta');
    m.append(el('div',null,e.exists?human(e.size)+' · '+when(e.mtime):null),
             el('em',e.exists?(e.config_exists?'on':''):'miss',
                e.exists?(e.config_exists?'reviewed before':'new · will be processed')
                        :'gone from disk'));
    li.append(nm,m,el('div','go','→'));
    li.onclick=()=>openFile(e.path);
    li.onkeydown=ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();openFile(e.path);}};
    ul.append(li);
  }
}

async function openFile(path){
  let r;
  try{r=await fetch('/open',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path})});}
  catch(e){return note('The app is no longer running.',true,false,true);}
  if(!r.ok){
    const b=await r.json().catch(()=>({}));
    return note(b.error||'Could not open that file.',true,false,true);
  }
  $('#path').value=''; note('Opening '+path+' …',false,true); tick();
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
    note(''); box.innerHTML=''; shown=null; recent();
  };
  card.append(open,close); box.append(card);
}

async function tick(){
  let s=null;
  try{s=await (await fetch('/api/state')).json();}catch(e){return;}
  const busy=s.status==='loading'||s.status==='exporting';
  if(busy){
    note((s.stage||'Working')+((s.agent_status||'')?' — '+s.agent_status:''),false,true);
  }else if(s.status==='error'){
    note(s.error||'Could not open that file.',true,false,true);
  }else{
    if(Date.now()>sticky)note('');
    if(s.status==='ready') current(s); else $('#now').innerHTML='';
  }
  if(shown!==s.file){shown=s.file||null; recent();}
  setTimeout(tick,busy?900:2500);
}

$('#go').onclick=()=>{const p=$('#path').value.trim(); if(p)openFile(p);};
$('#path').onkeydown=ev=>{if(ev.key==='Enter'){ev.preventDefault();$('#go').click();}};
$('#browse').onclick=async()=>{
  const btn=$('#browse'); btn.disabled=true;
  note('Choose a file in the dialog that just opened on this computer.');
  let r=null;
  try{r=await fetch('/browse',{method:'POST'});}catch(e){}
  btn.disabled=false;
  const body=r?await r.json().catch(()=>({})):{};
  if(!r||!r.ok){
    note((body&&body.error)||'No file dialog here — type the path instead.',true,false,true);
    return $('#path').focus();
  }
  if(body.cancelled)return note('');
  $('#path').value=body.path; openFile(body.path);
};
$('#quit').onclick=async()=>{
  const ok=await fetch('/shutdown',{method:'POST'}).then(r=>r.ok).catch(()=>false);
  note(ok?'The app has stopped. You can close this tab.':'Could not stop the app.',!ok,false,
       !ok?true:false);
};
recent(); tick();
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
    """The recents list, with the open file flagged so the page can leave it out of
    the list: it is already shown in the panel above, and twice is one too many."""
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
            raise RuntimeError("no file dialog here; type the path instead")
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return done.stdout.strip() or None


def _browse():
    """Ask for a path using whichever dialog this machine offers, or ``None`` if
    cancelled.

    A desktop window owns a real dialog, and that is the one the reviewer is looking
    at. A browser tab owns none, so the machine is asked instead — its dialog can land
    behind the window, which is normal and still better than typing a path.
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
        _log(f"ptr: {exc}; close that window yourself to get rid of it")


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

    _log(f"ptr: app running at {url}")
    _log("ptr: a large run takes 30-90 s to open; the app stays up between files.")
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
            _log(f"ptr: no desktop window ({exc}); opening a browser instead")
    if not in_window and open_browser:
        try:
            if not webbrowser.open(url):
                raise OSError("no browser answered")
        except (OSError, webbrowser.Error) as exc:
            # In a double-clicked bundle this line is the only way the user learns the
            # server is up, so it has to carry the address.
            _log(f"ptr: could not open a browser ({exc}); open {url} in one yourself")
    session.stop.wait()
    session.close()
    httpd.shutdown()
    httpd.server_close()
