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
            "file_calibration": {"a": float(a), "b": float(b)},
            "anchors": [
                {
                    "name": "water_cluster",
                    "target_mz": 37.033,
                    "status": "accepted",
                    "observed_file_mz": 37.033,
                    "corrected_mz": 37.033,
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
                    "observed_file_mz": 204.951,
                    "corrected_mz": 204.951,
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
