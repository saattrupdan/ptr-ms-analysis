#!/usr/bin/env python3
"""Compound naming and sample-specific inclusion carried into the review app.

Two review contracts live here:

* a compound is named once, so an auto-generated ``unknown m/z ...`` label must
  never sit next to an assigned formula (it says "unknown" and identifies the
  compound at the same time);
* ``peaks[].samples`` is carried through to the browser unchanged, while the
  delivered summary rows stay exactly as they were.
"""

from __future__ import annotations

import unittest
from unittest import mock

import h5py
import numpy as np

from sniff import formula_id, ptrms, viz


def _candidate(formula: str, name: str) -> dict:
    return {
        "formula": formula,
        "name": name,
        "ion_mz": 59.049,
        "delta_mDa": 1.0,
        "dbe": 1.0,
        "k": None,
        "k_estimated": True,
        "flags": [],
        "iso_pred": [0.01, 0.01],
        "iso_obs": None,
        "iso_used": False,
        "probability": 0.99,
    }


class IdentityLabelTest(unittest.TestCase):
    def test_formula_replaces_an_auto_generated_unknown_label(self):
        self.assertEqual(
            formula_id.identity_label("unknown m/z 73.029", "C4H8O"), "C4H8O"
        )

    def test_expert_wording_is_never_treated_as_a_placeholder(self):
        # "Unknown terpenes" is a statement about the sample, not a blank to fill
        self.assertEqual(
            formula_id.identity_label("Unknown terpenes", "C5H8"), "Unknown terpenes"
        )

    def test_an_unassigned_candidate_does_not_rename_the_peak(self):
        # the Identification card offers candidates; naming the peak from one would
        # turn an unreviewed guess into the compound's identity
        self.assertEqual(
            formula_id.identity_label("unknown m/z 59.049", ""), "unknown m/z 59.049"
        )

    def test_hand_written_label_wins(self):
        self.assertEqual(
            formula_id.identity_label("2-butanone", "C4H8O"), "2-butanone"
        )

    def test_unknown_label_without_a_formula_is_left_alone(self):
        self.assertEqual(
            formula_id.identity_label("unknown m/z 131.104", ""), "unknown m/z 131.104"
        )

    def test_missing_label_falls_back_to_the_formula(self):
        self.assertEqual(formula_id.identity_label("", "C6H6"), "C6H6")
        # nothing known at all stays blank in the CSV, as it always was
        self.assertEqual(formula_id.identity_label(None, None), "")


def _payload(peaks_cfg, candidates=None):
    """Build a review payload for `peaks_cfg` from a tiny synthetic file."""
    with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
        h5.create_dataset("SPECdata/Intensities", data=np.zeros((2, 5)))
        h5.create_dataset("SPECdata/AverageSpec", data=np.ones(5))
        with (
            mock.patch.object(ptrms, "load_mass_cal", return_value=(10.0, 1.0)),
            mock.patch.object(
                ptrms,
                "load_transmission",
                return_value=(np.array([1.0, 1000.0]), np.array([1.0, 1.0])),
            ),
            mock.patch.object(ptrms, "spec_duration_s", return_value=1.0),
            mock.patch.object(ptrms, "extract_primary", return_value=None),
            mock.patch.object(ptrms, "water_cluster_ratio", return_value=None),
            mock.patch.object(ptrms, "build_discriminator", return_value=np.ones(2)),
            mock.patch.object(
                ptrms, "derive_molar_volume_info", return_value=(24.465, "test")
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
            mock.patch.object(
                viz.formula_id, "score_peak", return_value=candidates or []
            ),
        ):
            return viz.build_viz_data(
                h5,
                peaks_cfg=peaks_cfg,
                ranges_cfg=[
                    {"label": "sample_01", "start": 1, "end": 1, "class": "sample"},
                    {"label": "sample_02", "start": 2, "end": 2, "class": "sample"},
                ],
            )


class ReviewPayloadTest(unittest.TestCase):
    def test_assigned_formula_wins_over_unknown_label_in_payload(self):
        data = _payload(
            [{"mz": 2.0, "label": "unknown m/z 2.000", "formula": "C3H6O"}],
            candidates=[_candidate("C3H6O", "acetone")],
        )
        peak = data["peaks"][0]
        self.assertEqual(peak["label"], "C3H6O")
        self.assertNotIn("unknown", peak["label"].lower())
        # the name is still offered for review, it is just not the compound's name
        self.assertEqual(peak["candidates"][0]["name"], "acetone")

    def test_unknown_label_stands_alone_without_a_formula(self):
        data = _payload([{"mz": 2.0, "label": "unknown m/z 2.000"}])
        self.assertEqual(data["peaks"][0]["label"], "unknown m/z 2.000")
        self.assertEqual(data["peaks"][0]["formula"], "")

    def test_sample_selection_is_carried_into_the_payload(self):
        data = _payload(
            [
                {"mz": 2.0, "label": "acetone", "samples": ["sample_02"]},
                {"mz": 2.0, "label": "methanol"},
            ]
        )
        self.assertEqual(data["peaks"][0]["samples"], ["sample_02"])
        # no explicit list means every sample interval, as in older configs
        self.assertIsNone(data["peaks"][1]["samples"])


if __name__ == "__main__":
    unittest.main()


def test_sample_selection_is_inert_in_the_summary_analysis():
    """`samples` says which sample intervals a compound is part of.

    Until per-sample output exists it must stay metadata: a compound selected for
    at least one sample is summarised exactly as it was before the field existed.
    """
    from sniff import analyze

    plain = {"mz": 100.0, "label": "analyte", "window": 0.4, "use": True}
    tagged = dict(plain, samples=["sample_01"])

    assert analyze._peak_windows([plain]) == analyze._peak_windows([tagged])
    constants = ptrms.load_rate_constants()
    assert ptrms.resolve_k([plain], constants) == ptrms.resolve_k([tagged], constants)
