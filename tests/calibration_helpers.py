"""Explicit calibration doubles for synthetic tests."""

from sniff import ptrms


def identity_mass_axis(a=10.0, b=1.0):
    """Return a deliberately explicit identity correction for tiny fixtures."""
    return ptrms.MassAxisCalibration(
        a,
        b,
        diagnostics={
            "model": "m_corrected = scale*m_file + offset",
            "applied": True,
            "scale": 1.0,
            "offset_da": 0.0,
            "file_calibration": {
                "model": "timebin = a*sqrt(m_file) + b",
                "a": float(a),
                "b": float(b),
            },
            "anchors": [
                {
                    "name": "water_cluster",
                    "target_mz": 37.033,
                    "status": "accepted",
                    "reason": "",
                    "observed_file_mz": 37.033,
                    "corrected_mz": 37.033,
                    "timebin": a * 37.033**0.5 + b,
                    "prominence": 100.0,
                    "snr": 100.0,
                    "persistence": {
                        "available": True,
                        "blocks": 8,
                        "accepted_blocks": 8,
                        "fraction": 1.0,
                        "statuses": ["accepted"] * 8,
                    },
                },
                {
                    "name": "iodobenzene",
                    "target_mz": 204.951,
                    "status": "accepted",
                    "reason": "",
                    "observed_file_mz": 204.951,
                    "corrected_mz": 204.951,
                    "timebin": a * 204.951**0.5 + b,
                    "prominence": 100.0,
                    "snr": 100.0,
                    "persistence": {
                        "available": True,
                        "blocks": 8,
                        "accepted_blocks": 8,
                        "fraction": 1.0,
                        "statuses": ["accepted"] * 8,
                    },
                },
            ],
        },
    )
