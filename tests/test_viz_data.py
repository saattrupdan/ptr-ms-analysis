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

    def test_peak_abundance_is_mean_integrated_raw_signal(self):
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            h5.create_dataset("SPECdata/Intensities", data=np.zeros((2, 5)))
            h5.create_dataset("SPECdata/AverageSpec", data=np.ones(5))
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
                    ptrms,
                    "derive_molar_volume_info",
                    return_value=(24.465, "test"),
                ),
                mock.patch.object(ptrms, "derive_K", return_value=None),
                mock.patch.object(
                    ptrms,
                    "extract_traces",
                    return_value=({2.0: (np.array([2.0, 4.0]), 2.0)}, (10.0, 1.0)),
                ),
                mock.patch.object(ptrms, "_cluster", return_value=[]),
                mock.patch.object(ptrms, "resolve_k", return_value={}),
                mock.patch.object(
                    ptrms, "load_rate_constants", return_value={"compounds": []}
                ),
                mock.patch.object(viz.formula_id, "score_peak", return_value=[]),
            ):
                data = viz.build_viz_data(
                    h5,
                    peaks_cfg=[{"mz": 2.0}],
                    ranges_cfg=[],
                )

        self.assertEqual(data["peaks"][0]["abundance"], 3.0)

    def test_irregular_pctimes_make_relative_and_absolute_axes(self):
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            h5.create_dataset("SPECdata/Intensities", data=np.zeros((3, 2)))
            h5.create_dataset(
                "SPECdata/PCTime",
                data=[[1_700_000_000], [1_700_000_002], [1_700_000_009]],
            )
            h5.attrs["Single Spec Duration (ms)"] = [1000.0]
            axes = ptrms.viz_x_axis_data(h5)

        self.assertEqual(axes["relative"], [0.0, 2.0, 9.0])
        self.assertEqual(axes["absolute"], [1700000000.0, 1700000002.0, 1700000009.0])
        self.assertTrue(axes["absolute_available"])

    def test_absolute_axis_applies_file_lab_timezone_offset(self):
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            h5.create_dataset("SPECdata/Intensities", data=np.zeros((2, 2)))
            h5.create_dataset("SPECdata/PCTime", data=[[1000.0], [1002.0]])
            h5.attrs["Single Spec Duration (ms)"] = [1000.0]
            h5.attrs["UTC_Offset"] = [3600.0]
            axes = ptrms.viz_x_axis_data(h5)

        self.assertEqual(axes["relative"], [0.0, 2.0])
        self.assertEqual(axes["absolute"], [4600.0, 4602.0])
        self.assertEqual(axes["absolute_offset_s"], 3600.0)

    def test_adjacent_sub_millisecond_pctimes_keep_distinct_axis_values(self):
        pctimes = [
            1_700_000_000.0004,
            1_700_000_000.0008,
            1_700_000_000.0012,
        ]
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            h5.create_dataset("SPECdata/Intensities", data=np.zeros((3, 2)))
            h5.create_dataset("SPECdata/PCTime", data=np.asarray(pctimes)[:, None])
            h5.attrs["Single Spec Duration (ms)"] = [1.0]
            axes = ptrms.viz_x_axis_data(h5)

        self.assertEqual(axes["absolute"], pctimes)
        self.assertTrue(np.all(np.diff(axes["absolute"]) > 0))
        json.loads(json.dumps(axes, allow_nan=False))

    def test_absolute_axis_accepts_year_zero_but_rejects_expanded_years(self):
        year_zero = -62167219200.0
        year_10000 = 253402300800.0
        cases = (
            ([year_zero, year_zero + 0.001], True),
            ([year_zero - 0.001, year_zero], False),
            ([year_10000 - 0.001, year_10000 - 0.0004], True),
            ([year_10000, year_10000 + 0.001], False),
        )
        for pctimes, available in cases:
            with self.subTest(pctimes=pctimes):
                with h5py.File(
                    "in-memory", "w", driver="core", backing_store=False
                ) as h5:
                    h5.create_dataset("SPECdata/Intensities", data=np.zeros((2, 2)))
                    h5.create_dataset(
                        "SPECdata/PCTime", data=np.asarray(pctimes)[:, None]
                    )
                    h5.attrs["Single Spec Duration (ms)"] = [1.0]
                    axes = ptrms.viz_x_axis_data(h5)

                self.assertEqual(axes["absolute_available"], available)
                if not available:
                    self.assertIsNone(axes["absolute"])

    def test_render_html_rejects_ambiguous_embedded_absolute_axis(self):
        data = {
            "meta": {
                "x_axis": {
                    "absolute": [1.0, 1.0],
                    "absolute_available": True,
                }
            }
        }
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            viz.render_html(data)

    def test_malformed_pctimes_disable_absolute_and_use_duration_fallback(self):
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            h5.create_dataset("SPECdata/Intensities", data=np.zeros((3, 2)))
            h5.create_dataset(
                "SPECdata/PCTime",
                data=[[1_700_000_000], [np.nan], [1_700_000_009]],
            )
            h5.attrs["Single Spec Duration (ms)"] = [2500.0]
            axes = ptrms.viz_x_axis_data(h5)

        self.assertIsNone(axes["absolute"])
        self.assertFalse(axes["absolute_available"])
        self.assertEqual(axes["relative"], [0.0, 2.5, 5.0])

    def test_nonpositive_or_nonfinite_duration_uses_safe_relative_domain(self):
        for duration_ms in (0.0, -1000.0, np.nan):
            with self.subTest(duration_ms=duration_ms):
                with h5py.File(
                    "in-memory", "w", driver="core", backing_store=False
                ) as h5:
                    h5.create_dataset("SPECdata/Intensities", data=np.zeros((3, 2)))
                    h5.attrs["Single Spec Duration (ms)"] = [duration_ms]
                    axes = ptrms.viz_x_axis_data(h5)

                self.assertEqual(axes["relative"], [0.0, 1.0, 2.0])
                self.assertTrue(np.all(np.isfinite(axes["relative"])))
                self.assertTrue(np.all(np.diff(axes["relative"]) > 0))

    def test_out_of_javascript_date_range_keeps_relative_but_disables_absolute(self):
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            h5.create_dataset("SPECdata/Intensities", data=np.zeros((3, 2)))
            h5.create_dataset(
                "SPECdata/PCTime",
                data=[[10_000_000_000_000], [10_000_000_000_002], [10_000_000_000_005]],
            )
            h5.attrs["Single Spec Duration (ms)"] = [1000.0]
            axes = ptrms.viz_x_axis_data(h5)

        self.assertIsNone(axes["absolute"])
        self.assertFalse(axes["absolute_available"])
        self.assertEqual(axes["relative"], [0.0, 2.0, 5.0])
        self.assertTrue(np.all(np.diff(axes["relative"]) > 0))


if __name__ == "__main__":
    unittest.main()
