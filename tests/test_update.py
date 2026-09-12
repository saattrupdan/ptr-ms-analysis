"""Release checks select and verify only a compatible Sniff installer."""

import hashlib
import io
import json
from pathlib import Path

import pytest

from sniff import update


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _release(version="v1.2.0", *, asset=None, digest=None):
    name = asset or "sniff-review-macos-arm64.pkg"
    digest = digest or "sha256:" + "a" * 64
    return {
        "tag_name": version,
        "draft": False,
        "prerelease": False,
        "html_url": f"https://github.com/saattrupdan/sniff/releases/tag/{version}",
        "assets": [
            {
                "name": name,
                "browser_download_url": (
                    f"https://github.com/saattrupdan/sniff/releases/download/"
                    f"{version}/{name}"
                ),
                "digest": digest,
            }
        ],
    }


def test_release_check_returns_a_newer_compatible_installer(monkeypatch):
    release = _release(digest="sha256:" + "a" * 64)
    seen = {}

    def open_release(request, timeout):
        seen["url"] = request.full_url
        seen["agent"] = request.headers["User-agent"]
        seen["timeout"] = timeout
        return _Response(json.dumps(release).encode())

    monkeypatch.setattr(update.urllib.request, "urlopen", open_release)

    available = update.find_update(
        "1.1.9", system="darwin", machine="arm64", timeout=2.5
    )

    assert available == update.Update(
        version="1.2.0",
        asset_name="sniff-review-macos-arm64.pkg",
        download_url=(
            "https://github.com/saattrupdan/sniff/releases/download/"
            "v1.2.0/sniff-review-macos-arm64.pkg"
        ),
        digest="sha256:" + "a" * 64,
        release_url="https://github.com/saattrupdan/sniff/releases/tag/v1.2.0",
    )
    assert seen == {"url": update._RELEASE_API, "agent": "Sniff/1.1.9", "timeout": 2.5}


@pytest.mark.parametrize(
    ("installed", "release"),
    [("1.2.0", "v1.2.0"), ("1.3.0", "v1.2.0"), ("unknown", "v1.2.0")],
)
def test_release_check_ignores_versions_that_are_not_newer(
    monkeypatch, installed, release
):
    monkeypatch.setattr(
        update.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(_release(release)).encode()),
    )

    assert (
        update.find_update(installed, system="darwin", machine="arm64") is None
    )


def test_release_check_uses_each_supported_platform_asset(monkeypatch):
    releases = {
        ("darwin", "arm64"): _release(asset="sniff-review-macos-arm64.pkg"),
        ("win32", "x86_64"): _release(
            asset="sniff-review-windows-x86_64.msi"
        ),
        ("linux", "x86_64"): _release(asset="sniff-review-linux-x86_64.deb"),
    }

    for (system, machine), release in releases.items():
        monkeypatch.setattr(
            update.urllib.request,
            "urlopen",
            lambda *_args, release=release, **_kwargs: _Response(
                json.dumps(release).encode()
            ),
        )
        available = update.find_update(
            "1.1.0", system=system, machine=machine
        )
        assert available is not None
        assert available.asset_name == release["assets"][0]["name"]

    assert update.find_update("1.1.0", system="darwin", machine="x86_64") is None
    assert update.find_update("1.1.0", system="linux", machine="arm64") is None


def test_release_check_rejects_an_untrusted_download(monkeypatch):
    release = _release()
    release["assets"][0]["browser_download_url"] = (
        "https://example.com/sniff-review-macos-arm64.pkg"
    )
    monkeypatch.setattr(
        update.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(release).encode()),
    )

    assert update.find_update("1.1.0", system="darwin", machine="arm64") is None


def test_installer_is_verified_before_it_is_opened(tmp_path, monkeypatch):
    payload = b"trusted installer"
    available = update.Update(
        version="1.2.0",
        asset_name="sniff-review-macos-arm64.pkg",
        download_url=(
            "https://github.com/saattrupdan/sniff/releases/download/"
            "v1.2.0/sniff-review-macos-arm64.pkg"
        ),
        digest="sha256:" + hashlib.sha256(payload).hexdigest(),
        release_url="https://github.com/saattrupdan/sniff/releases/tag/v1.2.0",
    )
    opened = []
    monkeypatch.setattr(update.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        update.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(payload),
    )
    monkeypatch.setattr(update, "_open_installer", lambda path: opened.append(path))

    target = update.install_update(available)

    assert target.read_bytes() == payload
    assert opened == [target]
    assert not Path(str(target) + ".part").exists()


def test_a_corrupt_installer_is_removed_and_never_opened(tmp_path, monkeypatch):
    available = update.Update(
        version="1.2.0",
        asset_name="sniff-review-macos-arm64.pkg",
        download_url=(
            "https://github.com/saattrupdan/sniff/releases/download/"
            "v1.2.0/sniff-review-macos-arm64.pkg"
        ),
        digest="sha256:" + "0" * 64,
        release_url="https://github.com/saattrupdan/sniff/releases/tag/v1.2.0",
    )
    monkeypatch.setattr(update.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        update.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"corrupt"),
    )
    monkeypatch.setattr(
        update,
        "_open_installer",
        lambda _path: pytest.fail("a corrupt installer was opened"),
    )

    with pytest.raises(ValueError, match="integrity"):
        update.install_update(available)

    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("*.pkg"))
