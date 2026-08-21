"""Regression tests for multi-point mass calibration loading."""

import unittest

import h5py
import numpy as np

from ptr_ms_analysis import ptrms

# In-memory reproduction of the three-anchor calibration used by Data_10_26_33.
# Keeping these values here avoids depending on the external measurement file.
DATA_10_26_33_MAPPING = np.array(
    [
        [21.022100, 24299.236],
        [203.94299, 130446.04],
        [330.84799, 173231.97],
    ],
    dtype=np.float32,
)


class MassCalibrationTest(unittest.TestCase):
    @staticmethod
    def _file(mapping=None, spectrum=None):
        h5 = h5py.File("in-memory", "w", driver="core", backing_store=False)
        if mapping is not None:
            h5.create_dataset("CALdata/Mapping", data=mapping)
        if spectrum is not None:
            h5.create_dataset("CALdata/Spectrum", data=spectrum)
        return h5

    def test_three_mapping_anchors_fit_with_low_residual(self):
        with self._file(mapping=DATA_10_26_33_MAPPING) as h5:
            a, b = ptrms.load_mass_cal(h5)

        masses = DATA_10_26_33_MAPPING[:, 0]
        timebins = DATA_10_26_33_MAPPING[:, 1]
        inferred_masses = ((timebins - b) / a) ** 2
        residual_ppm = np.abs((inferred_masses - masses) / masses) * 1e6

        self.assertLessEqual(float(residual_ppm.max()), 10.0)
        self.assertGreater(a, 0.0)
        self.assertTrue(np.isfinite([a, b]).all())

    def test_two_mapping_anchors_keep_closed_form_calibration(self):
        mapping = np.array([[19.0, 500.0], [181.0, 1500.0]])
        expected_a = (1500.0 - 500.0) / (np.sqrt(181.0) - np.sqrt(19.0))
        expected_b = 500.0 - expected_a * np.sqrt(19.0)

        with self._file(mapping=mapping) as h5:
            actual_a, actual_b = ptrms.load_mass_cal(h5)

        self.assertEqual(actual_a, expected_a)
        self.assertEqual(actual_b, expected_b)

    def test_float32_two_mapping_anchors_match_legacy_expression_exactly(self):
        mapping = np.array([[19.0, 500.0], [181.0, 1500.0]], dtype=np.float32)
        expected_a = (mapping[1, 1] - mapping[0, 1]) / (
            np.sqrt(mapping[1, 0]) - np.sqrt(mapping[0, 0])
        )
        expected_b = mapping[0, 1] - expected_a * np.sqrt(mapping[0, 0])

        with self._file(mapping=mapping) as h5:
            actual_a, actual_b = ptrms.load_mass_cal(h5)

        self.assertEqual(actual_a, float(expected_a))
        self.assertEqual(actual_b, float(expected_b))

    def test_valid_mapping_is_preferred_over_spectrum_fallback(self):
        spectrum = np.array([[900.0, 1.0], [901.0, 1.0]])
        with self._file(mapping=DATA_10_26_33_MAPPING, spectrum=spectrum) as h5:
            a, b = ptrms.load_mass_cal(h5)

        mapping = DATA_10_26_33_MAPPING.astype(np.float64)
        design = np.column_stack((np.sqrt(mapping[:, 0]), np.ones(3)))
        expected_a, expected_b = np.linalg.lstsq(
            design, mapping[:, 1], rcond=None
        )[0]
        self.assertAlmostEqual(a, expected_a, places=10)
        self.assertAlmostEqual(b, expected_b, places=10)
        self.assertNotEqual((a, b), (900.5, 1.0))

    def test_malformed_mapping_falls_back_to_spectrum(self):
        mapping = np.ones((3, 3))
        spectrum = np.array([[10.0, 2.0], [12.0, 4.0]])

        with self._file(mapping=mapping, spectrum=spectrum) as h5:
            self.assertEqual(ptrms.load_mass_cal(h5), (11.0, 3.0))

    def test_malformed_two_mapping_anchors_fall_back_to_spectrum(self):
        mapping = np.array([[19.0, np.nan], [181.0, 1500.0]])
        spectrum = np.array([[10.0, 2.0], [12.0, 4.0]])

        with self._file(mapping=mapping, spectrum=spectrum) as h5:
            self.assertEqual(ptrms.load_mass_cal(h5), (11.0, 3.0))

    def test_non_monotonic_multi_mapping_falls_back_to_spectrum(self):
        mapping = np.array([[19.0, 500.0], [59.0, 700.0], [181.0, 650.0]])
        spectrum = np.array([[10.0, 2.0], [12.0, 4.0]])

        with self._file(mapping=mapping, spectrum=spectrum) as h5:
            self.assertEqual(ptrms.load_mass_cal(h5), (11.0, 3.0))

    def test_grossly_nonlinear_mapping_falls_back_to_spectrum(self):
        # The anchors are positive, strictly monotonic, and well-conditioned, but
        # do not describe the stated square-root time-of-flight model.
        mapping = np.array([[19.0, 500.0], [59.0, 1000.0], [181.0, 2000.0]])
        spectrum = np.array([[10.0, 2.0], [12.0, 4.0]])

        with self._file(mapping=mapping, spectrum=spectrum) as h5:
            self.assertEqual(ptrms.load_mass_cal(h5), (11.0, 3.0))

    def test_invalid_multi_mapping_row_falls_back_to_spectrum(self):
        mapping = np.array([[19.0, 500.0], [59.0, np.nan], [181.0, 1500.0]])
        spectrum = np.array([[10.0, 2.0], [12.0, 4.0]])

        with self._file(mapping=mapping, spectrum=spectrum) as h5:
            self.assertEqual(ptrms.load_mass_cal(h5), (11.0, 3.0))

    def test_non_positive_multi_mapping_row_falls_back_to_spectrum(self):
        mapping = np.array([[19.0, 500.0], [59.0, -1.0], [181.0, 1500.0]])
        spectrum = np.array([[10.0, 2.0], [12.0, 4.0]])

        with self._file(mapping=mapping, spectrum=spectrum) as h5:
            self.assertEqual(ptrms.load_mass_cal(h5), (11.0, 3.0))

    def test_ill_conditioned_multi_mapping_falls_back_to_spectrum(self):
        mapping = np.array(
            [[100.0, 500.0], [100.00001, 501.0], [100.00002, 502.0]]
        )
        spectrum = np.array([[10.0, 2.0], [12.0, 4.0]])

        with self._file(mapping=mapping, spectrum=spectrum) as h5:
            self.assertEqual(ptrms.load_mass_cal(h5), (11.0, 3.0))

    def test_degenerate_mapping_falls_back_to_spectrum(self):
        mapping = np.array([[19.0, 500.0], [19.0, 600.0], [19.0, 700.0]])
        spectrum = np.array([[10.0, 2.0], [12.0, 4.0]])

        with self._file(mapping=mapping, spectrum=spectrum) as h5:
            self.assertEqual(ptrms.load_mass_cal(h5), (11.0, 3.0))

    def test_unusable_calibration_raises_clear_error(self):
        mapping = np.array([[19.0, 500.0], [19.0, 600.0]])
        spectrum = np.array([[0.0, 2.0], [np.nan, 4.0]])

        with self._file(mapping=mapping, spectrum=spectrum) as h5, self.assertRaisesRegex(
            ValueError, "no mass calibration"
        ):
            ptrms.load_mass_cal(h5)


if __name__ == "__main__":
    unittest.main()
