"""Shared test fixtures.

The review app appends opened files to ``~/.sniff/recent.json``. No test may touch
that file: an open runs on a background thread that can outlive the test that started
it, so the redirect is session-wide rather than per-test.
"""

import pytest


@pytest.fixture(autouse=True, scope="session")
def _app_recents_never_touch_the_home_folder(tmp_path_factory):
    from sniff import app

    original = app.RECENT_PATH
    app.RECENT_PATH = tmp_path_factory.mktemp("sniff-ms") / "recent.json"
    yield
    app.RECENT_PATH = original
