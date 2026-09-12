"""Linux artifacts carry one browser-first bundle into two distribution formats."""

import importlib.util
import pathlib
import sys
import tarfile

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PACKAGING = REPO / "packaging"
sys.path.insert(0, str(PACKAGING))


def _load_linux_packaging():
    spec = importlib.util.spec_from_file_location(
        "make_linux", PACKAGING / "make_linux.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_linux"] = module
    spec.loader.exec_module(module)
    return module


linux = _load_linux_packaging()


def _bundle(root):
    bundle = root / "sniff"
    contents = bundle / "_internal"
    contents.mkdir(parents=True)
    (contents / "reference.json").write_text("{}", encoding="utf-8")
    for name in ("sniff", "sniff-cli"):
        launcher = bundle / name
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o755)
    return bundle


def test_linux_architectures_use_release_and_debian_spellings():
    assert linux.normalise_arch("amd64") == "x86_64"
    assert linux.normalise_arch("aarch64") == "arm64"
    assert linux.debian_arch("x86_64") == "amd64"
    assert linux.debian_arch("arm64") == "arm64"
    with pytest.raises(ValueError, match="unsupported Linux architecture"):
        linux.normalise_arch("riscv64")


def test_debian_stage_installs_the_app_cli_desktop_entry_and_icon(tmp_path):
    bundle = _bundle(tmp_path)
    root = tmp_path / "deb"

    linux.stage_deb(bundle=bundle, root=root, arch="x86_64", version="1.2.3")

    assert (root / "opt/sniff/sniff").stat().st_mode & 0o111
    cli = root / "usr/bin/sniff"
    assert cli.is_symlink()
    assert cli.readlink() == pathlib.Path("/opt/sniff/sniff-cli")

    desktop = (root / "usr/share/applications/dk.samsmart.sniff.desktop").read_text()
    assert "Name=Sniff\n" in desktop
    assert "Exec=/opt/sniff/sniff\n" in desktop
    assert "Terminal=false\n" in desktop
    assert "Icon=dk.samsmart.sniff\n" in desktop

    icon = root / "usr/share/icons/hicolor/256x256/apps/dk.samsmart.sniff.png"
    assert icon.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (root / "usr/share/doc/sniff/copyright").read_text() == (
        REPO / "LICENSE"
    ).read_text()

    control = (root / "DEBIAN/control").read_text()
    assert "Package: sniff\n" in control
    assert "Version: 1.2.3\n" in control
    assert "Architecture: amd64\n" in control
    assert "Depends: xdg-utils, zenity | kdialog\n" in control


def test_portable_archive_has_one_movable_root_and_both_launchers(tmp_path):
    bundle = _bundle(tmp_path)
    output = tmp_path / "release"

    target = linux.build_portable(bundle=bundle, output_dir=output, arch="amd64")

    assert target.name == "sniff-review-linux-x86_64.tar.gz"
    with tarfile.open(target, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
    root = "sniff-review-linux-x86_64"
    assert f"{root}/sniff" in members
    assert f"{root}/sniff-cli" in members
    assert f"{root}/_internal/reference.json" in members
    assert f"{root}/README-Linux.txt" in members
    assert f"{root}/LICENSE" in members
    assert members[f"{root}/sniff"].mode & 0o111


def test_incomplete_bundle_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="missing sniff, sniff-cli, _internal"):
        linux.validate_bundle(tmp_path)
