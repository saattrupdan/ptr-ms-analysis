"""Required internal calibration and legacy config migration tests."""

import json
from argparse import Namespace

import h5py
import numpy as np
import pytest

from sniff import analyze, ptrms

A = 1000.0
B = 0.0
SCALE = 1.0007
OFFSET = -0.020
NBIN = 15000


def _observed(corrected):
    return (corrected - OFFSET) / SCALE


def _spectrum(include_water=True, include_iodobenzene=True):
    spectrum = np.full(NBIN, 4.0, dtype=np.float64)
    for mass, height in (
        (_observed(37.033), 1200.0),
        (_observed(204.951), 1000.0),
    ):
        if mass == _observed(37.033) and not include_water:
            continue
        if mass == _observed(204.951) and not include_iodobenzene:
            continue
        centre = A * np.sqrt(mass) + B
        bins = np.arange(int(centre) - 5, int(centre) + 7, dtype=np.float64)
        shape = np.maximum(0.0, height * (1.0 - ((bins - centre) / 3.5) ** 2))
        spectrum[int(centre) - 5 : int(centre) + 7] += shape
    return spectrum


def _file(spectrum):
    handle = h5py.File("calibration", "w", driver="core", backing_store=False)
    handle.create_dataset("CALdata/Spectrum", data=np.array([[A, B]]))
    handle.create_dataset("SPECdata/AverageSpec", data=spectrum)
    handle.create_dataset("SPECdata/Intensities", data=np.vstack([spectrum] * 8))
    return handle


def test_missing_required_anchor_is_a_structured_calibration_error():
    with (
        _file(_spectrum(include_iodobenzene=False)) as handle,
        pytest.raises(ptrms.MassCalibrationError) as caught,
    ):
        ptrms.load_mass_axis(handle)

    error = caught.value
    assert "mass calibration failed" in str(error)
    assert error.diagnostics["anchors"][1]["name"] == "iodobenzene"
    assert error.diagnostics["anchors"][1]["status"] != "accepted"


def test_raw_cycle_persistence_is_required_when_available():
    spectrum = _spectrum()
    with _file(spectrum) as handle:
        raw = handle["SPECdata/Intensities"]
        raw[0:4, :] = 4.0
        with pytest.raises(ptrms.MassCalibrationError) as caught:
            ptrms.load_mass_axis(handle)

    assert "persistence" in caught.value.diagnostics["anchors"][1]
    assert caught.value.diagnostics["anchors"][1]["persistence"]["accepted_blocks"] < 5


def test_old_config_migrates_absolute_masses_and_widths_once():
    axis = ptrms.MassAxisCalibration(
        A, B, scale=SCALE, offset=OFFSET, diagnostics={"applied": True}
    )
    old = {
        "peaks": [
            {
                "mz": 100.0,
                "window": {"left": 0.1, "right": 0.2},
                "unknown": {"keep": True},
            },
            {"mz": 100.0, "window": 0.4},
        ],
        "analyze": {"primary_mz": 21.022},
        "ranges": [{"label": "sample_01", "start": 1, "end": 2}],
        "extra": "preserved",
    }

    migrated, changed = ptrms.migrate_config_mass_axis(old, axis)
    again, changed_again = ptrms.migrate_config_mass_axis(migrated, axis)

    assert changed is True
    assert changed_again is False
    assert again == migrated
    assert migrated["mass_axis_domain"] == "corrected"
    assert migrated["mass_axis_version"] == 1
    assert migrated["peaks"][0]["mz"] == pytest.approx(100.05)
    assert migrated["peaks"][0]["window"]["left"] == pytest.approx(0.10007)
    assert migrated["peaks"][0]["window"]["right"] == pytest.approx(0.20014)
    assert migrated["peaks"][1]["window"] == pytest.approx(0.40028)
    assert migrated["analyze"]["primary_mz"] == pytest.approx(21.0167146)
    assert migrated["extra"] == "preserved"
    json.dumps(migrated)


def test_migration_refuses_an_uncalibrated_axis():
    axis = ptrms.MassAxisCalibration(
        A, B, diagnostics={"applied": False, "fallback_reason": "missing"}
    )
    with pytest.raises(ptrms.MassCalibrationError, match="before required"):
        ptrms.migrate_config_mass_axis({"peaks": [{"mz": 42.0}]}, axis)


def test_direct_cli_config_migration_persists_the_marker(tmp_path):
    axis = ptrms.MassAxisCalibration(
        A, B, scale=SCALE, offset=OFFSET, diagnostics={"applied": True}
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"peaks": [{"mz": 100.0}]}), encoding="utf-8")
    args = Namespace(config=str(path))

    migrated = analyze._migrate_loaded_config(
        json.loads(path.read_text(encoding="utf-8")), args, axis
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert migrated == saved
    assert saved["mass_axis_domain"] == "corrected"
    assert saved["mass_axis_version"] == 1
    assert saved["peaks"][0]["mz"] == pytest.approx(100.05)


def test_identity_migration_only_adds_the_marker():
    axis = ptrms.MassAxisCalibration(
        A, B, diagnostics={"applied": True, "scale": 1.0, "offset_da": 0.0}
    )
    old = {"peaks": [{"mz": 42.0, "window": 0.2}]}
    migrated, changed = ptrms.migrate_config_mass_axis(old, axis)

    assert changed is True
    assert migrated["peaks"] == old["peaks"]
    assert migrated["mass_axis_domain"] == "corrected"
    assert migrated["mass_axis_version"] == 1
