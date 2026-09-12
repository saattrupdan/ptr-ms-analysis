"""Release checks select and verify only a compatible Sniff installer."""

import hashlib
import io
import json

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
            "1.1.0",
            system=system,
            machine=machine,
            linux_ids={"ubuntu"} if system == "linux" else None,
        )
        assert available is not None
        assert available.asset_name == release["assets"][0]["name"]

    assert update.find_update("1.1.0", system="darwin", machine="x86_64") is None
    assert (
        update.find_update(
            "1.1.0",
            system="linux",
            machine="arm64",
            linux_ids={"ubuntu"},
        )
        is None
    )


def test_linux_update_is_limited_to_debian_compatible_systems(monkeypatch):
    monkeypatch.setattr(
        update.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("an incompatible system checked GitHub"),
    )

    assert (
        update.find_update(
            "1.1.0",
            system="linux",
            machine="x86_64",
            linux_ids={"fedora"},
        )
        is None
    )
    assert update._installer_name(
        system="linux", machine="x86_64", linux_ids={"linuxmint", "ubuntu"}
    ) == "sniff-review-linux-x86_64.deb"


def test_os_release_identifies_a_distribution_and_its_family(tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_text('ID="linuxmint"\nID_LIKE="ubuntu debian"\n', encoding="utf-8")

    assert update._linux_distribution_ids(os_release) == {
        "linuxmint",
        "ubuntu",
        "debian",
    }


def test_stable_release_replaces_the_same_version_prerelease(monkeypatch):
    monkeypatch.setattr(
        update.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(_release("v1.2.0")).encode()),
    )

    assert (
        update.find_update("1.2.0rc1", system="darwin", machine="arm64")
        is not None
    )


def test_release_check_rejects_a_prerelease_tag(monkeypatch):
    monkeypatch.setattr(
        update.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            json.dumps(_release("v1.2.1-rc1")).encode()
        ),
    )

    assert update.find_update("1.2.0", system="darwin", machine="arm64") is None


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
    monkeypatch.setattr(update.sys, "platform", "darwin")
    monkeypatch.setattr(update.platform, "machine", lambda: "arm64")
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
    second_target = update.install_update(available)

    assert target.read_bytes() == payload
    assert second_target.read_bytes() == payload
    assert opened == [target, second_target]
    assert target.parent != second_target.parent
    assert not list(tmp_path.rglob("*.part"))


def test_install_boundary_rejects_an_untrusted_update(monkeypatch):
    monkeypatch.setattr(update.sys, "platform", "darwin")
    monkeypatch.setattr(update.platform, "machine", lambda: "arm64")
    available = update.Update(
        version="1.2.0",
        asset_name="sniff-review-macos-arm64.pkg",
        download_url="https://example.com/installer.pkg",
        digest="sha256:" + "0" * 64,
        release_url="https://example.com",
    )
    monkeypatch.setattr(
        update.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("an untrusted update was downloaded"),
    )

    with pytest.raises(ValueError, match="trusted installer"):
        update.install_update(available)


def test_a_corrupt_installer_is_removed_and_never_opened(tmp_path, monkeypatch):
    monkeypatch.setattr(update.sys, "platform", "darwin")
    monkeypatch.setattr(update.platform, "machine", lambda: "arm64")
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
