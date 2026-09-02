"""Tests for the persistent review-app backend."""
from pathlib import Path
from unittest import mock

import h5py
import numpy as np

from ptr_ms_analysis import app


def make_h5(path, signal=True):
    with h5py.File(path, "w") as h5:
        h5.create_dataset("SPECdata/Intensities", data=np.ones((4, 8)) if signal else np.zeros((4, 8)))
        h5.create_dataset("SPECdata/AverageSpec", data=np.ones(8) if signal else np.zeros(8))
        h5.attrs["InstrumentType"] = "test"


def test_config_path_decisions(tmp_path):
    h5 = tmp_path / "ptr.h5"
    h5.touch()
    beside = tmp_path / "ptr.json"
    beside.write_text('{"peaks": []}')
    assert app.config_path_for(str(h5)) == beside
    beside.write_text("not json")
    assert app.config_path_for(str(h5)) == tmp_path / "ptr.ptr.json"
    beside.unlink()
    legacy = tmp_path / "ptr-analysis-config.json"
    legacy.write_text('{"ranges": []}')
    assert app.config_path_for(str(h5)) == legacy


def test_bootstrap_config_and_no_signal_note(tmp_path):
    h5 = tmp_path / "signal.h5"
    make_h5(h5)
    with mock.patch.object(app, "auto_peaks", return_value=[{"mz": 42.0}]), mock.patch.object(
        app, "auto_ranges", return_value=[{"label": "sample_01", "start": 1, "end": 2}]
    ):
        config = app.bootstrap_config(str(h5))
    assert config["peaks"] and config["ranges"] and config["checklist"]
    with mock.patch.object(app, "auto_peaks", return_value=[]), mock.patch.object(
        app, "auto_ranges", return_value=[]
    ):
        config = app.bootstrap_config(str(h5))
    assert any("No signal" in item for item in config["checklist"])


def test_agent_failure_keeps_deterministic_config(tmp_path):
    h5 = tmp_path / "signal.h5"
    make_h5(h5)
    deterministic = {"peaks": [{"mz": 42}], "ranges": [], "analyze": {}, "viz": {}}
    with mock.patch.object(app, "bootstrap_config", return_value=deterministic), mock.patch.object(
        app.viz, "build_viz_data", return_value={"meta": {}}
    ), mock.patch.object(app.urllib.request, "urlopen", side_effect=OSError("offline")):
        session = app.Session()
        session.open(str(h5), "http://agent.invalid")
    assert session.config["peaks"] == deterministic["peaks"]
    assert session.agent_status.startswith("Agent review failed:")


def test_recents_are_ordered_capped_and_corrupt_tolerant(tmp_path):
    recent = tmp_path / "recent.json"
    with mock.patch.object(app, "RECENT_PATH", recent):
        for i in range(25):
            app.remember_recent(str(tmp_path / f"{i}.h5"))
        values = app.load_recent()
        assert len(values) == 20 and values[0].endswith("24.h5")
        recent.write_text("{")
        assert app.load_recent() == []


def test_session_export_writes_csv_and_stays_ready(tmp_path):
    h5 = tmp_path / "signal.h5"
    make_h5(h5)
    session = app.Session()
    session.path = str(h5)
    session.config = {"peaks": [], "ranges": []}
    session.status = "ready"
    def fake_export(p, c, out):
        Path(out).write_text("csv")
        return {"out": out}
    with mock.patch.object(app, "analyze_config_to_csv", side_effect=fake_export):
        result = session.export()
    assert Path(result["out"]).name == "signal.csv"
    assert Path(result["out"]).exists() and session.status == "ready"
