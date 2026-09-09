"""Progress and cancellation on the analysis hot path.

The axis these tests hold the code to is the one measured on the real 2 GB /
20,725-cycle fixture: an open takes about 33 s, of which the two passes in
``ptrms.extract_traces`` are 28.6 s — 14.6 s of streaming every cycle once and
13.9 s of re-centring the intervals' cycles again. The callbacks are allowed to
report that shape; they are never allowed to change what the arithmetic produces.
"""

import unittest
from unittest import mock

import h5py
import numpy as np
from calibration_helpers import identity_mass_axis

from sniff import ptrms, viz

# timebin = a*sqrt(m) + b, anchored at m/z 20 -> 1000 and m/z 200 -> 5000, which
# spreads a peak over enough bins for a local apex search to have something to do.
_MAP = np.array([[20.0, 1000.0], [200.0, 5000.0]])
_A = 4000.0 / (np.sqrt(200.0) - np.sqrt(20.0))
_B = 1000.0 - _A * np.sqrt(20.0)
_CYCLES, _BINS = 50, 4000
_MASSES = [30.0, 60.0]  # well apart, so both are isolated and both are integrated


def _spectra(peaks=_MASSES, height=2000.0, width=1.2):
    """Low noise with a Gaussian spike at each peak's timebin."""
    tb = np.arange(_BINS)
    data = np.random.default_rng(0).normal(1.0, 0.2, size=(_CYCLES, _BINS))
    for m in peaks:
        centre = _A * np.sqrt(m) + _B
        data += height * np.exp(-0.5 * ((tb - centre) / width) ** 2)[None, :]
    return data


def _write(h5, data):
    h5.create_dataset("SPECdata/Intensities", data=data)
    h5.create_dataset("SPECdata/AverageSpec", data=data.mean(axis=0))
    h5.create_dataset("CALdata/Mapping", data=_MAP)


def _run(h5, **kwargs):
    """Five blocks of ten cycles, so the progress axis has five honest steps."""
    with mock.patch.object(
        ptrms, "load_mass_axis", return_value=identity_mass_axis(_A, _B)
    ):
        return ptrms.extract_traces(h5, _MASSES, block=10, **kwargs)


class StreamingPassTest(unittest.TestCase):
    def test_the_pass_reports_cycles_consumed_and_lands_on_one(self):
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            _write(h5, _spectra())
            seen = []
            traces = _run(h5, progress=seen.append)[0]
        self.assertEqual([round(v, 6) for v in seen], [0.2, 0.4, 0.6, 0.8, 1.0])
        self.assertEqual(len(traces), len(_MASSES))

    def test_both_passes_share_one_axis_rather_than_each_reaching_one(self):
        """With intervals to re-centre, the re-read cycles count too: otherwise the
        bar would sit at 100 % through a second pass that is half the work."""
        seen = []
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            _write(h5, _spectra())
            _run(h5, per_range={"sample_01": (1, _CYCLES)}, progress=seen.append)
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(seen[-1], 1.0)
        first, second = seen[: _CYCLES // 10], seen[_CYCLES // 10 :]
        self.assertLessEqual(max(first), 0.5)  # the first pass is half the reads
        self.assertTrue(all(0.5 < v < 1.0 for v in second[:-1]), second)
        self.assertEqual(second[-1], 1.0)

    def test_a_callback_nobody_asks_for_changes_no_numbers(self):
        """This is the scientific hot path: the defaults have to be inert."""
        data = _spectra()
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            _write(h5, data)
            plain, cal_plain = _run(h5)
            watched, cal_watched = _run(
                h5, progress=lambda frac: None, should_stop=lambda: False
            )
        for m in _MASSES:
            np.testing.assert_array_equal(plain[m][0], watched[m][0])
            self.assertEqual(plain[m][1], watched[m][1])
        self.assertEqual(cal_plain, cal_watched)

    def test_a_cancel_ends_the_pass_rather_than_the_process(self):
        seen = []
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            _write(h5, _spectra())
            with self.assertRaises(ptrms.AnalysisCancelled):
                _run(h5, progress=seen.append, should_stop=lambda: len(seen) >= 2)
        self.assertEqual(len(seen), 2)  # it stopped; it did not run to the end

    def test_a_cancel_is_honoured_while_intervals_are_being_recentred(self):
        """An interval's second pass re-reads its own cycles, which on a long run is
        a wait of its own (13.9 s of the 28.6 s on the 2 GB fixture), and the
        reviewer is still entitled to leave."""
        seen = []
        blocks = _CYCLES // 10
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            _write(h5, _spectra())
            with self.assertRaises(ptrms.AnalysisCancelled):
                _run(
                    h5,
                    per_range={"sample_01": (1, _CYCLES)},
                    progress=seen.append,
                    should_stop=lambda: len(seen) > blocks,
                )
        self.assertLess(max(seen), 1.0)  # it stopped inside the second pass
        self.assertGreater(len(seen), blocks)  # ... after the first one had ended

    def test_the_exception_is_an_exception_and_not_a_string(self):
        self.assertTrue(issubclass(ptrms.AnalysisCancelled, Exception))


class BuildVizDataProgressTest(unittest.TestCase):
    """The review-data build's own axis: its phases first, the streaming pass then."""

    def _build(self, **kwargs):
        def fake_extract(f, masses, **kw):
            sink = kw.get("progress")
            for frac in (0.0, 0.5, 1.0):
                if sink:
                    sink(frac)
                calls["stream"].append(frac)
            return {m: (np.array([2.0, 4.0, 6.0, 8.0]), m) for m in masses}, (10.0, 1.0)

        calls = {"stream": []}
        with h5py.File("in-memory", "w", driver="core", backing_store=False) as h5:
            h5.create_dataset("SPECdata/Intensities", data=np.zeros((4, 5)))
            h5.create_dataset("SPECdata/AverageSpec", data=np.ones(5))
            with (
                mock.patch.object(
                    ptrms,
                    "load_mass_axis",
                    return_value=identity_mass_axis(10.0, 1.0),
                ),
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
                    ptrms, "build_discriminator", return_value=np.ones(4)
                ),
                mock.patch.object(
                    ptrms,
                    "derive_molar_volume_info",
                    return_value=(24.465, "test"),
                ),
                mock.patch.object(ptrms, "derive_K", return_value=None),
                mock.patch.object(ptrms, "extract_traces", side_effect=fake_extract),
                mock.patch.object(ptrms, "_cluster", return_value=[]),
                mock.patch.object(ptrms, "resolve_k", return_value={}),
                mock.patch.object(
                    ptrms, "load_rate_constants", return_value={"compounds": []}
                ),
                mock.patch.object(viz.formula_id, "score_peak", return_value=[]),
            ):
                payload = viz.build_viz_data(h5, [{"mz": 2.0}], [], **kwargs)
        return payload, calls["stream"]

    def test_the_bar_spends_its_time_where_the_time_is(self):
        seen = []
        self._build(progress=seen.append)
        self.assertEqual(seen[0], 0.0)
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(seen[-1], 1.0)
        # The four quick phases stay inside the first 11 %, and the streaming pass
        # is given everything after it — never a slice of its own. The reported
        # sequence is 0.0, the four phases, the pass's three steps, and the
        # completed payload at the end.
        phases, streamed = seen[:5], seen[5:]
        self.assertLessEqual(max(phases), viz.PREP_FRACTION)
        self.assertEqual(
            [round(v, 12) for v in streamed],
            [
                round(viz.PREP_FRACTION, 12),
                round(viz.PREP_FRACTION + (1.0 - viz.PREP_FRACTION) * 0.5, 12),
                1.0,
                1.0,
            ],
        )

    def test_the_work_is_the_same_whether_or_not_anyone_is_listening(self):
        """A bar is an observation of the work, never an input to it."""
        plain, _ = self._build()
        watched, stream = self._build(progress=lambda frac: None)
        self.assertEqual(plain["peaks"], watched["peaks"])
        self.assertEqual(len(stream), 3)

    def test_a_stop_requested_up_front_stops_the_phases(self):
        try:
            self._build(should_stop=lambda: True)
        except ptrms.AnalysisCancelled:
            return
        self.fail("the phases ran although this open was already cancelled")


if __name__ == "__main__":
    unittest.main()
