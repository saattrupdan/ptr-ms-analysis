#!/usr/bin/env python3
"""Build Linux .deb and portable .tar.gz artifacts from a PyInstaller folder.

The Linux package is intentionally browser-first. It carries Sniff, Python, NumPy,
h5py and the reference data, but leaves the browser, file chooser and glibc to the
host. Build it on the oldest Linux release the artifact promises to support.

    python packaging/make_linux.py --bundle dist/sniff --output-dir dist \
        --arch x86_64
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import typing as t
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import make_icons  # noqa: E402
from make_msi import project_version  # noqa: E402

APP_NAME = "Sniff"
PACKAGE_NAME = "sniff"
BUNDLE_ID = "dk.samsmart.sniff"
DESCRIPTION = "Review PTR-MS measurements and export the CSV"
HOMEPAGE = "https://github.com/saattrupdan/sniff"
MAINTAINER = "Dan Saattrup Smart"
PORTABLE_README = """Sniff for Linux
===============

Run ./sniff to open Sniff in your default browser.
Run ./sniff-cli --help to use the command-line interface.

The folder is self-contained and may be moved as a unit. Do not move its launchers
away from _internal/. Opening files from Sniff's start screen requires zenity or
kdialog on the host. The application binds only to 127.0.0.1.
"""


def normalise_arch(value: str) -> str:
    """Return the release architecture name for a machine architecture."""
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    try:
        return aliases[value.lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported Linux architecture: {value}") from exc


def debian_arch(arch: str) -> str:
    """Return Debian's spelling for a release architecture."""
    return {"x86_64": "amd64", "arm64": "arm64"}[normalise_arch(arch)]


def validate_bundle(bundle: Path) -> None:
    """Reject an incomplete or non-Linux PyInstaller folder."""
    missing = [
        name
        for name in ("sniff", "sniff-cli", "_internal")
        if not (bundle / name).exists()
    ]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"incomplete Linux bundle {bundle}: missing {joined}")


def build_portable(bundle: Path, output_dir: Path, arch: str) -> Path:
    """Write a portable archive with one self-contained top-level folder."""
    validate_bundle(bundle=bundle)
    release_arch = normalise_arch(arch)
    root_name = f"sniff-review-linux-{release_arch}"
    target = output_dir / f"{root_name}.tar.gz"
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sniff-linux-portable-") as temporary:
        root = Path(temporary) / root_name
        shutil.copytree(bundle, root, symlinks=True)
        (root / "README-Linux.txt").write_text(PORTABLE_README, encoding="utf-8")
        shutil.copy2(REPO / "LICENSE", root / "LICENSE")
        with tarfile.open(target, "w:gz") as archive:
            archive.add(root, arcname=root_name, recursive=True)
    return target


def stage_deb(bundle: Path, root: Path, arch: str, version: str) -> None:
    """Stage the filesystem and metadata consumed by ``dpkg-deb``."""
    validate_bundle(bundle=bundle)
    release_arch = normalise_arch(arch)
    install_dir = root / "opt" / PACKAGE_NAME
    shutil.copytree(bundle, install_dir, symlinks=True)

    bin_dir = root / "usr" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / PACKAGE_NAME).symlink_to(f"/opt/{PACKAGE_NAME}/sniff-cli")

    applications = root / "usr" / "share" / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    (applications / f"{BUNDLE_ID}.desktop").write_text(
        "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                f"Name={APP_NAME}",
                f"Comment={DESCRIPTION}",
                f"Exec=/opt/{PACKAGE_NAME}/sniff",
                f"TryExec=/opt/{PACKAGE_NAME}/sniff",
                f"Icon={BUNDLE_ID}",
                "Terminal=false",
                "Categories=Science;",
                "StartupNotify=true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    icons = root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icons.mkdir(parents=True, exist_ok=True)
    (icons / f"{BUNDLE_ID}.png").write_bytes(
        make_icons.png_bytes(256, make_icons.render(256))
    )

    docs = root / "usr" / "share" / "doc" / PACKAGE_NAME
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "README.Linux").write_text(PORTABLE_README, encoding="utf-8")
    shutil.copy2(REPO / "LICENSE", docs / "copyright")

    installed_size = sum(
        path.stat().st_size for path in install_dir.rglob("*") if path.is_file()
    )
    control = root / "DEBIAN"
    control.mkdir(parents=True, exist_ok=True)
    (control / "control").write_text(
        "\n".join(
            [
                f"Package: {PACKAGE_NAME}",
                f"Version: {version}",
                "Section: science",
                "Priority: optional",
                f"Architecture: {debian_arch(release_arch)}",
                f"Installed-Size: {(installed_size + 1023) // 1024}",
                f"Maintainer: {MAINTAINER}",
                "Depends: xdg-utils, zenity | kdialog",
                f"Homepage: {HOMEPAGE}",
                f"Description: {DESCRIPTION}",
                " Sniff reads IONICON IoniTOF HDF5 files, supports expert review in a",
                " local browser interface, and exports curated CSV results.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def build_deb(bundle: Path, output_dir: Path, arch: str) -> Path:
    """Build the Debian package with the host's ``dpkg-deb`` tool."""
    tool = shutil.which("dpkg-deb")
    if tool is None:
        raise RuntimeError("dpkg-deb is required to build the Linux installer")
    release_arch = normalise_arch(arch)
    target = output_dir / f"sniff-review-linux-{release_arch}.deb"
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sniff-linux-deb-") as temporary:
        root = Path(temporary) / "root"
        stage_deb(
            bundle=bundle,
            root=root,
            arch=release_arch,
            version=project_version(),
        )
        subprocess.run(
            [tool, "--build", "--root-owner-group", os.fspath(root), os.fspath(target)],
            check=True,
        )
    return target


def main(argv: t.Optional[t.Sequence[str]] = None) -> int:
    """Build both Linux release artifacts."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", type=Path, default=Path("dist/sniff"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--arch", default=platform.machine())
    args = parser.parse_args(argv)

    bundle = args.bundle.resolve()
    output_dir = args.output_dir.resolve()
    portable = build_portable(bundle=bundle, output_dir=output_dir, arch=args.arch)
    package = build_deb(bundle=bundle, output_dir=output_dir, arch=args.arch)
    print(f"wrote {package}")
    print(f"wrote {portable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
