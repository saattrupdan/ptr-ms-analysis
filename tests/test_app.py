"""Tests for the persistent review app: config discovery, the deterministic
bootstrap, the agent hand-off and the routes the start screen drives."""

import json
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import h5py
import numpy as np
import pytest

from ptr_ms_analysis import app, viz


@pytest.fixture(autouse=True)
def _keep_recents_out_of_the_home_folder(tmp_path, monkeypatch):
    """Opening a file appends to ``~/.ptr-ms/recent.json``; tests must not."""
    monkeypatch.setattr(app, "RECENT_PATH", tmp_path / "recent.json")


def make_h5(path, *, cycles=4, mz_count=8, signal=True):
    """A minimal IoniTOF-shaped file: enough for the app, not for the science."""
    intensities = np.ones((cycles, mz_count)) if signal else np.zeros((cycles, mz_count))
    with h5py.File(path, "w") as h5:
        h5.create_dataset("SPECdata/Intensities", data=intensities)
        h5.create_dataset("SPECdata/AverageSpec", data=np.ones(mz_count) if signal else np.zeros(mz_count))
        h5.attrs["InstrumentType"] = "test"


def payload_stub(f, peaks, ranges, **kwargs):
    return {
        "file": "stub.h5",
        "peaks": [],
        "ranges": [],
        "meta": {"ncyc": 4},
        "config_base": {},
    }


# --------------------------------------------------------------------------
# where the config lives
# --------------------------------------------------------------------------
def test_valid_config_beside_the_file_is_used(tmp_path):
    h5 = tmp_path / "ptr.h5"
    h5.touch()
    beside = tmp_path / "ptr.json"
    beside.write_text(json.dumps({"peaks": [{"mz": 42.0}]}), encoding="utf-8")
    assert app.config_path_for(str(h5)) == beside


def test_unrelated_same_stem_json_is_never_clobbered(tmp_path):
    h5 = tmp_path / "ptr.h5"
    h5.touch()
    (tmp_path / "ptr.json").write_text('{"unrelated": true}', encoding="utf-8")
    target = app.config_path_for(str(h5))
    assert target == tmp_path / "ptr.ptr.json"
    assert json.loads((tmp_path / "ptr.json").read_text(encoding="utf-8")) == {
        "unrelated": True
    }


def test_legacy_config_name_is_honoured(tmp_path):
    h5 = tmp_path / "run.h5"
    h5.touch()
    legacy = tmp_path / "run-analysis-config.json"
    legacy.write_text(json.dumps({"ranges": []}), encoding="utf-8")
    assert app.config_path_for(str(h5)) == legacy


def test_missing_config_targets_the_stem_json(tmp_path):
    h5 = tmp_path / "run.h5"
    h5.touch()
    assert app.config_path_for(str(h5)) == tmp_path / "run.json"


def test_config_written_on_open_is_reread_on_the_next_open(tmp_path):
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    session = app.Session()
    with (
        mock.patch.object(app, "auto_peaks", return_value=[{"mz": 42.0}]),
        mock.patch.object(app, "auto_ranges", return_value=[{"label": "sample_01", "start": 1, "end": 2}]),
        mock.patch.object(app.viz, "build_viz_data", payload_stub),
    ):
        session.open(str(h5))
    written = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert written["peaks"] and written["ranges"]
    assert written["checklist"]
    with mock.patch.object(app, "auto_peaks") as detect_peaks:
        with mock.patch.object(app.viz, "build_viz_data", payload_stub):
            session.open(str(h5))
    detect_peaks.assert_not_called()  # the saved config is reused, not recomputed


# --------------------------------------------------------------------------
# the deterministic bootstrap
# --------------------------------------------------------------------------
def test_bootstrap_reports_what_it_decided(tmp_path):
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    with (
        mock.patch.object(app, "auto_peaks", return_value=[{"mz": 42.0}]),
        mock.patch.object(app, "auto_ranges", return_value=[{"label": "sample_01", "start": 1, "end": 2}]),
    ):
        config = app.bootstrap_config(str(h5))
    assert config["peaks"] and config["ranges"]
    assert any("generated automatically" in str(item) for item in config["checklist"])
    assert config["diagnostics"]["n_peaks"] == 1
    assert config["diagnostics"]["n_ranges"] == 1


def test_a_blank_file_says_so(tmp_path):
    h5 = tmp_path / "blank.h5"
    make_h5(h5, signal=False)
    with (
        mock.patch.object(app, "auto_peaks", return_value=[]),
        mock.patch.object(app, "auto_ranges", return_value=[]),
    ):
        config = app.bootstrap_config(str(h5))
    assert any("No significant signal" in str(item) for item in config["checklist"])


# --------------------------------------------------------------------------
# the optional agent endpoint
# --------------------------------------------------------------------------
def _agent_answer(config):
    class Response:
        def read(self):
            return json.dumps(config).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return Response()


def test_agent_answer_replaces_the_deterministic_config(tmp_path):
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    curated = {"peaks": [{"mz": 7.0, "label": "N2"}], "ranges": []}
    session = app.Session()
    with (
        mock.patch.object(app, "auto_peaks", return_value=[{"mz": 42.0}]),
        mock.patch.object(app, "auto_ranges", return_value=[]),
        mock.patch.object(app.viz, "build_viz_data", payload_stub),
        mock.patch.object(app.urllib.request, "urlopen", return_value=_agent_answer(curated)),
    ):
        session.open(str(h5), agent_url="http://agent.invalid/curate")
    assert session.config["peaks"] == curated["peaks"]
    assert session.agent_status == "Agent review applied."
    on_disk = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert on_disk["peaks"] == curated["peaks"]


def test_unreachable_agent_keeps_the_deterministic_config(tmp_path):
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    session = app.Session()
    with (
        mock.patch.object(app, "auto_peaks", return_value=[{"mz": 42.0}]),
        mock.patch.object(app, "auto_ranges", return_value=[]),
        mock.patch.object(app.viz, "build_viz_data", payload_stub),
        mock.patch.object(
            app.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")
        ),
    ):
        session.open(str(h5), agent_url="http://agent.invalid/curate")
    assert session.status == "ready"
    assert session.config["peaks"] == [{"mz": 42.0}]
    assert session.agent_status.startswith("Agent review failed")
    on_disk = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert on_disk["peaks"] == [{"mz": 42.0}]


def test_agent_reply_without_peaks_is_refused(tmp_path):
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    session = app.Session()
    with (
        mock.patch.object(app, "auto_peaks", return_value=[{"mz": 42.0}]),
        mock.patch.object(app, "auto_ranges", return_value=[]),
        mock.patch.object(app.viz, "build_viz_data", payload_stub),
        mock.patch.object(
            app.urllib.request, "urlopen", return_value=_agent_answer({"note": "hi"})
        ),
    ):
        session.open(str(h5), agent_url="http://agent.invalid/curate")
    assert session.config["peaks"] == [{"mz": 42.0}]
    assert "no peaks or ranges" in session.agent_status


# --------------------------------------------------------------------------
# recents
# --------------------------------------------------------------------------
def test_recents_are_ordered_capped_and_tolerant(tmp_path):
    recent = tmp_path / "recent.json"
    with mock.patch.object(app, "RECENT_PATH", recent):
        for i in range(25):
            app.remember_recent(str(tmp_path / f"{i}.h5"))
        values = app.load_recent()
        assert len(values) == 20
        assert values[0].endswith("24.h5")
        recent.write_text("{", encoding="utf-8")
        assert app.load_recent() == []


def test_remembering_a_file_twice_does_not_duplicate_it(tmp_path):
    recent = tmp_path / "recent.json"
    with mock.patch.object(app, "RECENT_PATH", recent):
        app.remember_recent(str(tmp_path / "a.h5"))
        app.remember_recent(str(tmp_path / "b.h5"))
        app.remember_recent(str(tmp_path / "a.h5"))
        assert app.load_recent() == [str(tmp_path / "a.h5"), str(tmp_path / "b.h5")]


# --------------------------------------------------------------------------
# exporting
# --------------------------------------------------------------------------
def test_export_writes_beside_the_file_and_stays_ready(tmp_path):
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    session = app.Session()
    session.path = str(h5)
    session.config = {"peaks": [], "ranges": []}
    session.status = "ready"

    def fake_analysis(path, config, out):
        Path(out).write_text("compound,cycle\n", encoding="utf-8")
        return {"n_rows": 1}

    with mock.patch.object(app, "analyze_config_to_csv", side_effect=fake_analysis):
        result = session.export()
    assert Path(result["out"]) == tmp_path / "run.csv"
    assert session.status == "ready"
    assert session.status_payload() == {"status": "done", "out": str(tmp_path / "run.csv")}


def test_a_second_export_cannot_report_the_previous_one(tmp_path):
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    session = app.Session()
    session.path = str(h5)
    session.config = {"peaks": [], "ranges": []}
    session.status = "ready"
    session.export_result = {"out": str(tmp_path / "run.csv")}
    with mock.patch.object(app, "analyze_config_to_csv", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            session.export()
    assert session.status_payload()["status"] == "error"
    assert session.status == "ready"  # the file is still open and still reviewable


# --------------------------------------------------------------------------
# the routes
# --------------------------------------------------------------------------
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    def __init__(self, base):
        self.base = base

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, r.read()

    def post(self, path, body=None):
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data, {"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture
def server(tmp_path):
    port = _free_port()
    httpd, session, url = app.make_server(port=port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Server(url), session
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()
        session.close()


def _wait_ready(api, timeout=20):
    spent = 0.0
    while spent < timeout:
        _, state = api.get("/api/state")
        if json.loads(state)["status"] in ("ready", "error"):
            return json.loads(state)
        threading.Event().wait(0.05)
        spent += 0.05
    raise AssertionError("the file never finished opening")


def test_start_screen_lists_recents_and_opens_files(server, tmp_path, monkeypatch):
    api, session = server
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    status, body = api.get("/")
    assert status == 200 and b"PTR-MS review" in body

    with (
        mock.patch.object(app, "auto_peaks", return_value=[{"mz": 42.0}]),
        mock.patch.object(app, "auto_ranges", return_value=[]),
        mock.patch.object(app.viz, "build_viz_data", payload_stub),
    ):
        code, _ = api.post("/open", {"path": str(h5)})
        assert code == 202
        state = _wait_ready(api)
    assert state["status"] == "ready"
    assert state["config"] == str(tmp_path / "run.json")

    _, recent = api.get("/api/recent")
    assert json.loads(recent)[0]["path"] == str(h5)


def test_review_page_404s_until_a_file_is_open(server):
    api, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        api.get("/review")
    assert exc.value.code == 404


def test_review_page_is_rendered_in_app_mode(server, tmp_path):
    api, _ = server
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    with (
        mock.patch.object(app, "auto_peaks", return_value=[]),
        mock.patch.object(app, "auto_ranges", return_value=[]),
        mock.patch.object(app.viz, "build_viz_data", payload_stub),
    ):
        api.post("/open", {"path": str(h5)})
        _wait_ready(api)
    status, html = api.get("/review")
    assert status == 200
    assert b"const APPMODE = true" in html  # Export, not Done
    assert b"Open another file" in html


def test_save_rejects_a_body_that_is_not_a_config(server, tmp_path):
    api, _ = server
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    with (
        mock.patch.object(app, "auto_peaks", return_value=[]),
        mock.patch.object(app, "auto_ranges", return_value=[]),
        mock.patch.object(app.viz, "build_viz_data", payload_stub),
    ):
        api.post("/open", {"path": str(h5)})
        _wait_ready(api)
    code, _ = api.post("/save", {"nothing": True})
    assert code == 400
    code, _ = api.post("/save", {"peaks": [{"mz": 1.0}]})
    assert code == 200
    on_disk = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert on_disk["peaks"] == [{"mz": 1.0}]


def test_open_reports_a_missing_file_without_touching_the_session(server):
    api, session = server
    code, body = api.post("/open", {"path": "/nope/nothing.h5"})
    assert code == 404
    assert session.status == "empty"


def test_export_keeps_the_server_and_the_file_open(server, tmp_path):
    api, session = server
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    with (
        mock.patch.object(app, "auto_peaks", return_value=[]),
        mock.patch.object(app, "auto_ranges", return_value=[]),
        mock.patch.object(app.viz, "build_viz_data", payload_stub),
    ):
        api.post("/open", {"path": str(h5)})
        _wait_ready(api)

    def fake_analysis(path, config, out):
        Path(out).write_text("compound\n", encoding="utf-8")
        return {"n_rows": 0}

    with mock.patch.object(app, "analyze_config_to_csv", side_effect=fake_analysis):
        code, _ = api.post("/export")
        assert code == 202
        for _ in range(200):
            _, st = api.get("/status")
            if json.loads(st)["status"] in ("done", "error"):
                break
            threading.Event().wait(0.02)
    assert json.loads(st)["status"] == "done"
    assert session.path == str(h5)  # still open, still reviewable
    _, page = api.get("/")
    assert b"PTR-MS review" in page  # and the app is still serving


def test_closing_a_file_leaves_the_server_up(server, tmp_path):
    api, session = server
    h5 = tmp_path / "run.h5"
    make_h5(h5)
    with (
        mock.patch.object(app, "auto_peaks", return_value=[]),
        mock.patch.object(app, "auto_ranges", return_value=[]),
        mock.patch.object(app.viz, "build_viz_data", payload_stub),
    ):
        api.post("/open", {"path": str(h5)})
        _wait_ready(api)
    code, _ = api.post("/close")
    assert code == 200 and session.status == "empty"
    with pytest.raises(urllib.error.HTTPError) as exc:
        api.get("/review")
    assert exc.value.code == 404


def test_opening_another_file_closes_the_first_one(tmp_path):
    a, b = tmp_path / "a.h5", tmp_path / "b.h5"
    make_h5(a)
    make_h5(b)
    session = app.Session()
    handles = []
    real_open = h5py.File

    class TrackedFile:
        def __init__(self, path):
            self._f = real_open(path, "r")
            self.closed = False
            handles.append(self)

        def __getattr__(self, name):
            return getattr(self._f, name)

        def __getitem__(self, key):
            return self._f[key]

        def close(self):
            self.closed = True
            self._f.close()

    with (
        mock.patch.object(app, "auto_peaks", return_value=[]),
        mock.patch.object(app, "auto_ranges", return_value=[]),
        mock.patch.object(app.viz, "build_viz_data", payload_stub),
        mock.patch.object(app.h5py, "File", lambda p, *a, **k: TrackedFile(p)),
    ):
        session.open(str(a))
        first = handles[0]
        session.open(str(b))
    assert first.closed
    assert session.path == str(b)


def test_render_html_mode_flag_is_not_confused_with_the_review_page():
    data = {"file": "x.h5", "peaks": [], "ranges": [], "meta": {}}
    assert "const APPMODE = true" in viz.render_html(data, mode="app")
    assert "const APPMODE = false" in viz.render_html(data)


def test_no_template_marker_survives_rendering():
    """A marker left in the page parses as an identifier and only fails in the
    browser, where it kills the whole script: catch it here instead."""
    data = {"file": "x.h5", "peaks": [], "ranges": [], "meta": {}}
    for mode in ("review", "app"):
        html = viz.render_html(data, config_path="/tmp/x.json", mode=mode)
        assert "/*__" not in html, f"unreplaced template marker in {mode} mode"
