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
            "anchors": [],
        },
    )
