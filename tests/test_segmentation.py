"""Segmentation-gap tests: a merge must be earned by the signal, not by a cycle count.

A plateau gets split whenever the signal wobbles, so the merge that puts it back
together has to tell a wobble from a real phase change. A cycle count cannot: the same
40-second dip is 40 cycles at 1 s/cycle and 8 at 5 s/cycle. Everything here therefore
runs on synthetic discriminator traces — no measurement file, and no wall-clock
assumption beyond the cycle time stated in each case.
"""

import unittest

import numpy as np

from sniff import analyze, ptrms

BASELINE = 1.0  # the trace is written in multiples of the run's own background


class _FakeRun:
    """Just enough HDF5 for ptrms.spec_duration_s: one attribute, the cycle time."""

    def __init__(self, cycle_s):
        self.attrs = {"Single Spec Duration (ms)": np.array([cycle_s * 1000.0])}


def _plateau(D, start_cycle, end_cycle, baseline):
    """A plateau levelled and classed exactly the way detect_segments does it."""
    level = float(D[start_cycle - 1 : end_cycle].mean() / baseline)
    return {
        "start_cycle": start_cycle,
        "end_cycle": end_cycle,
        "n_cycles": end_cycle - start_cycle + 1,
        "start_s": round((start_cycle - 1) * 1.0, 1),
        "end_s": round((end_cycle - 1) * 1.0, 1),
        "level": round(level, 2),
        "class": "high" if level >= 3.0 else "low",
    }


def _sample_pair(
    gap_cycles,
    gap_level,
    *,
    cycle_s=1.0,
    sample_cycles=40,
    background_cycles=240,
    sample_level=10.0,
):
    """One sample, a gap, and the same sample again — background at both ends.

    Returns ``(segments, discriminator, file)``. The surrounding background keeps the
    20th percentile — the baseline every level is divided by — on the background
    itself, so a level in these tests reads as it does in a real run. ``gap_level`` is
    one x-baseline level for the whole gap, or one per gap cycle.
    """
    values = np.asarray(gap_level, dtype=float)
    if values.ndim == 0:
        values = np.full(gap_cycles, float(values))
    elif values.size != gap_cycles:
        raise ValueError("gap_level needs one value per gap cycle")
    head = np.full(background_cycles, BASELINE)
    sample = np.full(sample_cycles, sample_level)
    D = np.concatenate([head, sample, values, sample, head])
    baseline = ptrms.discriminator_baseline(D)
    first = background_cycles + 1
    first_end = background_cycles + sample_cycles
    second = first_end + gap_cycles + 1
    segments = [
        _plateau(D, first, first_end, baseline),
        _plateau(D, second, second + sample_cycles - 1, baseline),
    ]
    return segments, D, _FakeRun(cycle_s)


def _merge(segments, D, f, **kwargs):
    """Merge the way every caller that holds the file does: on the evidence."""
    kwargs.setdefault("high_gap", None)  # None = the cap below decides
    kwargs.setdefault("baseline", ptrms.discriminator_baseline(D))
    kwargs.setdefault("cap", ptrms.merge_gap_cap(f))
    return ptrms.merge_adjacent_segments(segments, discriminator=D, **kwargs)


class GapEvidenceTest(unittest.TestCase):
    def test_brief_dip_inside_a_sample_merges(self):
        segments, D, f = _sample_pair(20, 6.0)
        self.assertEqual([s["class"] for s in segments], ["high", "high"])

        merged = _merge(segments, D, f)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["merged_segments"], 2)
        gaps = merged[0]["merged_gaps"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["reason"], "level held")
        self.assertAlmostEqual(gaps[0]["min_level"], 6.0, places=2)
        self.assertAlmostEqual(gaps[0]["max_level"], 6.0, places=2)

    def test_real_background_trough_never_merges_however_short(self):
        for gap_cycles in (1, 2, 5, 20):
            with self.subTest(gap_cycles=gap_cycles):
                segments, D, f = _sample_pair(gap_cycles, BASELINE)
                merged = _merge(segments, D, f)
                self.assertEqual(len(merged), 2, "a trough to baseline is a boundary")
                self.assertEqual(merged[0].get("merged_gaps"), [])

    def test_spike_out_of_the_phase_does_not_merge(self):
        gap = np.full(20, 6.0)
        gap[-1] = 40.0  # one cycle out of the sample's own band
        segments, D, f = _sample_pair(20, gap)

        merged = _merge(segments, D, f)

        self.assertEqual(len(merged), 2)

    def test_opposite_class_stays_a_hard_boundary(self):
        segments, D, f = _sample_pair(20, 6.0)
        segments[1]["class"] = "low"  # a plateau of the other class, gap and all

        merged = _merge(segments, D, f)

        self.assertEqual(len(merged), 2)

    def test_gap_slice_follows_the_1_based_inclusive_convention(self):
        # prev ends at cycle 40 and cur starts at 71, so the gap is cycles 41..70:
        # 30 cycles, D[40:70] in 0-based slice form. The two neighbours' facing cycles
        # (D[39] and D[70]) sit far out of band, so a slice off by one on either edge
        # would pull one in and refuse the merge instead of reporting these levels.
        D = np.concatenate([np.full(100, 8.0), np.full(100, BASELINE)])
        D[39] = 40.0
        D[70] = 40.0
        baseline = ptrms.discriminator_baseline(D)
        self.assertAlmostEqual(baseline, BASELINE, places=9)
        segments = [
            _plateau(D, 11, 40, baseline),
            _plateau(D, 71, 100, baseline),
        ]
        self.assertEqual(segments[1]["start_cycle"] - segments[0]["end_cycle"] - 1, 30)

        merged = ptrms.merge_adjacent_segments(
            segments,
            high_gap=None,
            discriminator=D,
            baseline=baseline,
            cap=60,
        )

        self.assertEqual(len(merged), 1)
        gap = merged[0]["merged_gaps"][0]
        self.assertEqual(gap["cycles"], 30)
        self.assertAlmostEqual(gap["min_level"], 8.0, places=2)
        self.assertAlmostEqual(gap["max_level"], 8.0, places=2)

    def test_deterministic_baseline_is_shared_with_detect_segments(self):
        self.assertAlmostEqual(
            ptrms.discriminator_baseline(np.full(50, 4.0)), 4.0, places=9
        )
        self.assertEqual(ptrms.discriminator_baseline(np.zeros(10)), 1.0)
        self.assertEqual(ptrms.discriminator_baseline(np.zeros(0)), 1.0)


class GapCapTest(unittest.TestCase):
    def test_cap_follows_the_cycle_time(self):
        self.assertEqual(ptrms.merge_gap_cap(_FakeRun(1.0)), 60)
        self.assertEqual(ptrms.merge_gap_cap(_FakeRun(5.0)), 30)
        self.assertEqual(ptrms.merge_gap_cap(_FakeRun(10.0)), 30)  # the 30-cycle floor
        self.assertEqual(ptrms.merge_gap_cap(_FakeRun(0.1)), 600)

    def test_same_physical_trace_merges_at_any_cycle_time(self):
        # One 40-second dip inside one 40-second sample: 40 cycles at 1 s/cycle, 8 at
        # 5 s/cycle. The verdict is a property of the run, not of how fast it was
        # acquired, which is the whole reason the cap is a duration.
        slow = _sample_pair(
            40, 6.0, cycle_s=1.0, sample_cycles=40, background_cycles=240
        )
        fast = _sample_pair(8, 6.0, cycle_s=5.0, sample_cycles=8, background_cycles=48)

        merged_slow = _merge(*slow)
        merged_fast = _merge(*fast)

        self.assertEqual(len(merged_slow), 1)
        self.assertEqual(len(merged_fast), 1)
        self.assertEqual(
            merged_slow[0]["merged_gaps"][0]["reason"],
            merged_fast[0]["merged_gaps"][0]["reason"],
        )
        # a fixed cycle count, which is what the old rule used, gets this wrong: it
        # sees the slow file as too long and the fast one as short enough
        self.assertEqual(len(_merge(*slow, cap=30)), 2)
        self.assertEqual(len(_merge(*fast, cap=30)), 1)

    def test_a_chain_of_plateaus_is_judged_gap_by_gap(self):
        # A sample that drifts reads as a chain of plateaus in one phase. Each gap has
        # to be judged against the plateau it actually sits next to: judged against
        # the running blend instead, the chain starts refusing itself after the first
        # merge, and the verdict depends on which plateau happened to come first.
        cycles, edge, gap = 30, 120, 20
        D = np.concatenate(
            [
                np.full(edge, BASELINE),
                np.full(cycles, 40.0 * BASELINE),
                np.full(gap, 10.0 * BASELINE),
                np.full(cycles, 4.0 * BASELINE),
                np.full(gap, 2.1 * BASELINE),
                np.full(cycles, 8.0 * BASELINE),
                np.full(edge, BASELINE),
            ]
        )
        baseline = ptrms.discriminator_baseline(D)
        segments = []
        start = edge + 1
        for _ in range(3):
            segments.append(_plateau(D, start, start + cycles - 1, baseline))
            start += cycles + gap

        merged = _merge(segments, D, _FakeRun(1.0))

        self.assertEqual(len(merged), 1)
        self.assertEqual([gap["cycles"] for gap in merged[0]["merged_gaps"]], [20, 20])
        # the level is the plateaus' mean weighted by their own cycles, so the cycles
        # between them cannot drag a merged interval's level toward the baseline
        self.assertEqual(
            merged[0]["level"], round((40.0 * 30 + 4.0 * 30 + 8.0 * 30) / 90, 2)
        )

    def test_a_dropout_inside_a_background_stays_one_blank(self):
        # A background cannot fall out of itself: a stretch where the signal nearly
        # vanished is still the same blank, and splitting it would replace one good
        # reference interval with two shorter ones.
        segments, D, f = _sample_pair(20, 0.02, sample_level=1.0)

        merged = _merge(segments, D, f)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["class"], "low")
        self.assertEqual(
            merged[0]["merged_gaps"][0]["reason"], "fell to baseline"
        )

    def test_a_transient_of_sample_signal_keeps_a_background_split(self):
        # The upper test still belongs to a background: signal that clearly belongs to
        # a sample must not be averaged into the blank it sits in the middle of.
        segments, D, f = _sample_pair(20, 12.0, sample_level=1.0)

        merged = _merge(segments, D, f)

        self.assertEqual(len(merged), 2)

    def test_gap_longer_than_the_window_stays_split(self):
        # 200 s of dip is over the ~60 s window at both speeds, so neither merges
        slow = _sample_pair(
            200, 6.0, cycle_s=1.0, sample_cycles=40, background_cycles=240
        )
        fast = _sample_pair(40, 6.0, cycle_s=5.0, sample_cycles=8, background_cycles=48)
        self.assertEqual(len(_merge(*slow)), 2)
        self.assertEqual(len(_merge(*fast)), 2)

    def test_zero_cap_never_merges_high_plateaus(self):
        segments, D, f = _sample_pair(4, 6.0)
        self.assertEqual(len(_merge(segments, D, f)), 1)  # the same gap by default

        self.assertEqual(len(_merge(segments, D, f, high_gap=0)), 2)
        self.assertEqual(len(_merge(segments, D, f, cap=0)), 2)

    def test_forced_cycle_cap_overrides_the_derived_one(self):
        segments, D, f = _sample_pair(40, 6.0)
        self.assertEqual(len(_merge(segments, D, f)), 1)  # cap 60 at 1 s/cycle
        self.assertEqual(len(_merge(segments, D, f, high_gap=20)), 2)


class LegacyLengthPathTest(unittest.TestCase):
    def test_length_only_path_still_merges_without_a_discriminator(self):
        segments, D, _f = _sample_pair(20, 6.0)

        merged = ptrms.merge_adjacent_segments(segments, high_gap=30, low_gap=0)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["merged_gaps"][0]["cycles"], 20)
        self.assertEqual(merged[0]["merged_gaps"][0]["reason"], "length only")
        self.assertIsNone(merged[0]["merged_gaps"][0]["min_level"])

        # and it merges straight across the trough the evidence test refuses
        trough, D_trough, _ = _sample_pair(20, BASELINE)
        self.assertEqual(
            len(ptrms.merge_adjacent_segments(trough, high_gap=30, low_gap=0)), 1
        )
        self.assertEqual(
            len(_merge(trough, D_trough, _FakeRun(1.0), high_gap=30)),
            2,
            "the evidence test refuses that gap once a signal is given",
        )

    def test_disabled_class_never_merges(self):
        segments, D, _f = _sample_pair(0, 6.0)  # plateaus that merely touch
        self.assertEqual(
            len(ptrms.merge_adjacent_segments(segments, high_gap=0, low_gap=0)), 2
        )


class MergeProvenanceTest(unittest.TestCase):
    def test_reason_separates_a_held_level_from_a_fall_to_baseline(self):
        held, D_held, f = _sample_pair(20, 6.0)  # stays well above the background
        fell, D_fell, _ = _sample_pair(20, 1.9, sample_level=3.6)

        merged_held = _merge(held, D_held, f)
        merged_fell = _merge(fell, D_fell, f)

        self.assertEqual(merged_held[0]["merged_gaps"][0]["reason"], "level held")
        self.assertEqual(merged_fell[0]["merged_gaps"][0]["reason"], "fell to baseline")

    def test_note_says_what_was_joined(self):
        self.assertEqual(ptrms.merge_gaps_note([]), "")
        gaps = [
            {"cycles": 12, "min_level": 7.4, "max_level": 9.1, "reason": "level held"},
            {"cycles": 28, "min_level": 6.2, "max_level": 9.8, "reason": "level held"},
        ]
        self.assertEqual(
            ptrms.merge_gaps_note(gaps),
            "joined 2 wobbles, level held (\u2264 28 cycles)",
        )
        mixed = gaps[:1] + [
            {
                "cycles": 4,
                "min_level": 1.3,
                "max_level": 2.2,
                "reason": "fell to baseline",
            }
        ]
        self.assertEqual(
            ptrms.merge_gaps_note(mixed),
            "joined 2 gaps, 1 level held, 1 fell to baseline (\u2264 12 cycles)",
        )

    def test_ranges_carry_their_own_provenance_into_the_note(self):
        ranges = [
            {"label": "sample_01", "start": 1, "end": 40, "unit": "cycle"},
            {
                "label": "sample_02",
                "start": 61,
                "end": 100,
                "unit": "cycle",
                "merged_gaps": [
                    {
                        "cycles": 20,
                        "min_level": 6.1,
                        "max_level": 8.0,
                        "reason": "level held",
                    }
                ],
            },
        ]
        self.assertEqual(
            analyze.auto_ranges_note(ranges),
            "joined 1 wobble, level held (\u2264 20 cycles)",
        )
        self.assertEqual(
            analyze.auto_ranges_note(ranges[:1]),
            "",
            "a config nobody merged anything in says nothing",
        )


if __name__ == "__main__":
    unittest.main()
