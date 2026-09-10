"""Shared test fixtures.

The review app appends opened files to ``~/.sniff/recent.json``. No test may touch
that file: an open runs on a background thread that can outlive the test that started
it, so the redirect is session-wide rather than per-test.
"""

import pytest


@pytest.fixture(autouse=True, scope="session")
def _app_recents_never_touch_the_home_folder(tmp_path_factory):
    from sniff import app

    state = tmp_path_factory.mktemp("sniff-ms")
    original_recent = app.RECENT_PATH
    original_active = app.ACTIVE_PATH
    app.RECENT_PATH = state / "recent.json"
    app.ACTIVE_PATH = state / "active.json"
    yield
    app.RECENT_PATH = original_recent
    app.ACTIVE_PATH = original_active


@pytest.fixture(autouse=True)
def _active_review_never_leaks_between_tests():
    from sniff import app

    try:
        app.ACTIVE_PATH.unlink()
    except FileNotFoundError:
        pass
    yield
    try:
        app.ACTIVE_PATH.unlink()
    except FileNotFoundError:
        pass
