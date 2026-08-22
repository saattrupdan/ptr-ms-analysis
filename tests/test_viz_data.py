"""Regression coverage for browser-review data preparation."""

import json
import unittest
from unittest import mock

import h5py
import numpy as np

from ptr_ms_analysis import ptrms, viz


class VizDataTest(unittest.TestCase):
    def test_nonfinite_average_spectrum_bins_are_zero_filled(self):
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            h5.create_dataset("SPECdata/Intensities", data=np.zeros((2, 5)))
            h5.create_dataset(
                "SPECdata/AverageSpec",
                data=np.array([1.2, np.nan, np.inf, -np.inf, 4.8]),
            )
            with (
                mock.patch.object(ptrms, "load_mass_cal", return_value=(10.0, 1.0)),
                mock.patch.object(
                    ptrms,
                    "load_transmission",
                    return_value=(np.array([1.0]), np.array([1.0])),
                ),
                mock.patch.object(ptrms, "spec_duration_s", return_value=1.0),
                mock.patch.object(ptrms, "extract_primary", return_value=None),
                mock.patch.object(ptrms, "water_cluster_ratio", return_value=None),
                mock.patch.object(
                    ptrms, "build_discriminator", return_value=np.ones(2)
                ),
                mock.patch.object(
                    ptrms, "derive_molar_volume_info", return_value=(24.465, "test")
                ),
                mock.patch.object(ptrms, "derive_K", return_value=None),
                mock.patch.object(ptrms, "resolve_k", return_value={}),
                mock.patch.object(
                    ptrms, "load_rate_constants", return_value={"compounds": []}
                ),
            ):
                data = viz.build_viz_data(h5, peaks_cfg=[], ranges_cfg=[])

        self.assertEqual(data["spectrum"], [1, 0, 0, 0, 5])
        json.dumps(data, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
