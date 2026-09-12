"""Check GitHub releases and open a verified installer for Sniff."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import typing as t
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_RELEASE_API = "https://api.github.com/repos/saattrupdan/sniff/releases/latest"
_DOWNLOAD_PREFIX = "/saattrupdan/sniff/releases/download/"
_RELEASE_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_INSTALLED_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(.*)$")
_SHA256_RE = re.compile(r"sha256:[0-9a-fA-F]{64}")


@dataclass(frozen=True)
class Update:
    """One compatible, newer Sniff release."""

    version: str
    asset_name: str
    download_url: str
    digest: str
    release_url: str


def find_update(
    installed_version: str,
    *,
    system: t.Optional[str] = None,
    machine: t.Optional[str] = None,
    linux_ids: t.Optional[t.AbstractSet[str]] = None,
    timeout: float = 4.0,
) -> t.Optional[Update]:
    """Return the latest compatible update, or ``None`` when none is available.

    Network errors are intentionally allowed to reach the caller. The app turns them
    into a quiet no-update result, while tests and other callers retain the reason.
    """
    detected_system = system or sys.platform
    if linux_ids is None and detected_system.startswith("linux"):
        linux_ids = _linux_distribution_ids()
    asset_name = _installer_name(
        system=detected_system,
        machine=machine or platform.machine(),
        linux_ids=linux_ids,
    )
    if asset_name is None:
        return None
    request = urllib.request.Request(
        _RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Sniff/{installed_version}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        release = json.load(response)
    if release.get("draft") or release.get("prerelease"):
        return None
    tag = str(release.get("tag_name") or "")
    if not _is_newer(tag, installed_version):
        return None
    for asset in release.get("assets") or []:
        if asset.get("name") != asset_name:
            continue
        download_url = str(asset.get("browser_download_url") or "")
        if not _trusted_download(download_url, asset_name=asset_name, tag=tag):
            return None
        digest = str(asset.get("digest") or "")
        if _SHA256_RE.fullmatch(digest) is None:
            return None
        return Update(
            version=tag.removeprefix("v"),
            asset_name=asset_name,
            download_url=download_url,
            digest=digest,
            release_url=str(release.get("html_url") or ""),
        )
    return None


def install_update(update: Update, *, timeout: float = 120.0) -> Path:
    """Download, verify and open an update in the operating system installer."""
    expected_name = _installer_name(
        system=sys.platform,
        machine=platform.machine(),
        linux_ids=_linux_distribution_ids() if sys.platform.startswith("linux") else None,
    )
    tag = f"v{update.version}"
    if (
        update.asset_name != expected_name
        or _RELEASE_VERSION_RE.fullmatch(update.version) is None
        or _SHA256_RE.fullmatch(update.digest) is None
        or not _trusted_download(
            update.download_url, asset_name=update.asset_name, tag=tag
        )
    ):
        raise ValueError("the update does not describe a trusted installer")
    root = Path(
        tempfile.mkdtemp(
            prefix=f"sniff-{update.version}-", dir=tempfile.gettempdir()
        )
    )
    target = root / update.asset_name
    descriptor, partial_name = tempfile.mkstemp(
        prefix=f"{update.asset_name}.", suffix=".part", dir=root
    )
    os.close(descriptor)
    partial = Path(partial_name)
    request = urllib.request.Request(
        update.download_url,
        headers={"User-Agent": f"Sniff/{update.version}"},
    )
    downloaded_digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, partial.open(
            "wb"
        ) as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
                downloaded_digest.update(chunk)
        expected = update.digest.removeprefix("sha256:").lower()
        if downloaded_digest.hexdigest() != expected:
            raise ValueError("the downloaded installer failed its integrity check")
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        try:
            root.rmdir()
        except OSError:
            pass
        raise
    _open_installer(target)
    return target


def _installer_name(
    *,
    system: str,
    machine: str,
    linux_ids: t.Optional[t.AbstractSet[str]] = None,
) -> t.Optional[str]:
    architecture = (
        machine.lower().replace("amd64", "x86_64").replace("aarch64", "arm64")
    )
    if system == "darwin" and architecture == "arm64":
        return "sniff-review-macos-arm64.pkg"
    if system == "win32" and architecture == "x86_64":
        return "sniff-review-windows-x86_64.msi"
    if (
        system.startswith("linux")
        and architecture == "x86_64"
        and linux_ids is not None
        and linux_ids.intersection({"debian", "ubuntu"})
    ):
        return "sniff-review-linux-x86_64.deb"
    return None


def _linux_distribution_ids(path: Path = Path("/etc/os-release")) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    values = {}
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip("\"'")
    identifiers = {values.get("ID", "").lower()}
    identifiers.update(values.get("ID_LIKE", "").lower().split())
    identifiers.discard("")
    return identifiers


def _is_newer(candidate: str, installed: str) -> bool:
    candidate_match = _RELEASE_VERSION_RE.fullmatch(candidate)
    installed_match = _INSTALLED_VERSION_RE.fullmatch(installed)
    if candidate_match is None or installed_match is None:
        return False
    candidate_parts = tuple(int(part) for part in candidate_match.groups())
    installed_parts = tuple(int(part) for part in installed_match.groups()[:3])
    if candidate_parts != installed_parts:
        return candidate_parts > installed_parts
    installed_suffix = installed_match.group(4)
    return bool(installed_suffix and not installed_suffix.startswith("+"))


def _trusted_download(url: str, *, asset_name: str, tag: str) -> bool:
    parsed = urlparse(url)
    expected_path = f"{_DOWNLOAD_PREFIX}{tag}/{asset_name}"
    return (
        parsed.scheme == "https"
        and parsed.hostname == "github.com"
        and parsed.path == expected_path
        and not parsed.query
        and not parsed.fragment
    )


def _open_installer(path: Path) -> None:
    if sys.platform == "darwin":
        command = ["open", str(path)]
    elif os.name == "nt":
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise OSError("Windows could not open the installer")
        startfile(str(path))
        return
    else:
        command = ["xdg-open", str(path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
