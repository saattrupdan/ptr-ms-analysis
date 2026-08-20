"""Smoke coverage for the installed package and resource-backed CLI."""

import json
import sys
import unittest
from contextlib import redirect_stdout
from importlib import resources
from io import StringIO
from unittest import mock

from ptr_ms_analysis import analyze, ptrms


class PackageSmokeTest(unittest.TestCase):
    def test_reference_resources_are_packaged(self):
        rate_constants = resources.files("ptr_ms_analysis").joinpath(
            "reference", "rate_constants.json"
        )
        library = resources.files("ptr_ms_analysis").joinpath(
            "reference", "ptrlibrary.csv"
        )
        self.assertTrue(rate_constants.is_file())
        self.assertTrue(library.is_file())
        table = ptrms.load_rate_constants()
        self.assertIsNotNone(table)
        self.assertGreater(len(table["compounds"]), 100)

    def test_rates_command_needs_no_hdf5_fixture(self):
        output = StringIO()
        with mock.patch.object(
            sys, "argv", ["ptr", "rates", "benzaldehyde"]
        ), redirect_stdout(output):
            analyze.main()

        payload = json.loads(output.getvalue())
        self.assertIn("compounds", payload)
        self.assertTrue(payload["compounds"])

    def test_help_command_needs_no_hdf5_fixture(self):
        output = StringIO()
        with mock.patch.object(sys, "argv", ["ptr", "--help"]), redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                analyze.main()

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("inspect", output.getvalue())


if __name__ == "__main__":
    unittest.main()
