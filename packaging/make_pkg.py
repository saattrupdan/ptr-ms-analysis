"""Write productbuild authoring for a .pkg that installs the built app bundle.

A macOS product archive is two nested things. A *component package* is the
payload — the `.app` folder PyInstaller built, the path it goes to, and a bill
of materials — and a *distribution* is the script around it that decides the
title, the version, the minimum system, and which architectures may install it.
`pkgbuild` writes the first and `productbuild` combines it with the second,
which is what this file writes.

Keeping the distribution as authoring rather than letting productbuild
synthesize one from `--component` is what makes a rebuild comparable: the
component package is a function of the built folder, and this XML is a function
of the arguments below. Nothing about the runner leaks into either — no
absolute paths, no timestamps, no machine names — so two runs of the same
checkout produce the same bytes.

    python packaging/make_pkg.py --out build/pkg
    pkgbuild --component "dist/PTR-MS Review.app" --install-location /Applications \
        --identifier dk.samsmart.ptrms --version 0.4.0 build/pkg/ptr-component.pkg
    productbuild --distribution build/pkg/distribution.xml \
        --package-path build/pkg dist/ptr.pkg

`--package-path` is how productbuild finds the payload: the distribution names
the component package by file name, so `--package` has to match what pkgbuild
was told to write. Nothing here signs anything — see packaging/README.md for
what a Developer ID build would add, and for the SmartScreen and Gatekeeper
consequences of shipping unsigned.
"""

import argparse
import os
import sys
import xml.etree.ElementTree as ET

# The version rules live with the MSI authoring: one checkout, one version
# string, in both installers. This folder is sys.path[0] when the script is run
# the documented way, so the sibling import needs no bootstrap.
from make_msi import msi_version

APP_NAME = "PTR-MS Review"
# Same identifier as the bundle's CFBundleIdentifier (packaging/ptr-app.spec), so
# the bundle and the package that carries it are one product to LaunchServices,
# the receipts database, and a future signed build.
IDENTIFIER = "dk.samsmart.ptrms"
TITLE = APP_NAME
# What pkgbuild writes into --package-path; the distribution names this file, so
# the two have to agree. It is a name, never a path, for exactly that reason.
COMPONENT_PKG = "ptr-component.pkg"
# Big Sur is where arm64 macOS starts, and where the NumPy and h5py wheels this
# bundle carries start. Claiming less would be a promise the payload cannot keep.
MIN_MACOS = "11.0"
# What the artifact is for. A package that installs an arm64 bundle on an Intel
# Mac fails on the first double-click rather than at install time, so say it here.
ARCH = "arm64"
SUMMARY = "Review PTR-MS measurements and export the CSV"


def build_distribution(version: str, package: str, arch: str) -> ET.ElementTree:
    """A one-package distribution: no custom install screen, no scripts."""
    script = ET.Element("installer-gui-script", {"minSpecVersion": "1"})
    ET.SubElement(script, "title").text = TITLE
    ET.SubElement(
        script,
        "options",
        {
            # Nothing to tick: one package, always installed, always to the
            # startup disk's /Applications. `require-scripts` is a statement of
            # fact — there are no preinstall or postinstall scripts here.
            "customize": "never",
            "require-scripts": "false",
            "rootVolumeOnly": "true",
            "hostArchitectures": arch,
        },
    )
    volume_check = ET.SubElement(script, "volume-check")
    ET.SubElement(
        ET.SubElement(volume_check, "allowed-os-versions"), "os-version", {"min": MIN_MACOS}
    )
    # The outline and the two choices are how Installer is told to show one line
    # and hide the package behind it: the id doubles as the disclosure, and
    # `visible="false"` is what stops it being a second tick box.
    outline = ET.SubElement(script, "choices-outline")
    default = ET.SubElement(outline, "line", {"choice": "default"})
    ET.SubElement(default, "line", {"choice": IDENTIFIER})
    ET.SubElement(script, "choice", {"id": "default"})
    choice = ET.SubElement(script, "choice", {"id": IDENTIFIER, "visible": "false"})
    ET.SubElement(choice, "pkg-ref", {"id": IDENTIFIER})
    ET.SubElement(
        script,
        "pkg-ref",
        {"id": IDENTIFIER, "version": version, "onConclusion": "none"},
    ).text = package
    return ET.ElementTree(script)


def main(argv) -> int:
    parser = argparse.ArgumentParser(
        prog="make_pkg.py",
        description="Write the productbuild distribution XML for the macOS package.",
        epilog=__doc__,
        # The docstring is full of command pairs; re-wrapping them would break them.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--out",
        default=os.path.join("build", "pkg"),
        metavar="DIR",
        help="directory for distribution.xml (default: %(default)s)",
    )
    parser.add_argument(
        "--package",
        default=COMPONENT_PKG,
        metavar="NAME",
        help="file name pkgbuild wrote inside --package-path (default: %(default)s)",
    )
    parser.add_argument(
        "--arch",
        default=ARCH,
        metavar="ARCH",
        help="architecture the bundle is built for (default: %(default)s)",
    )
    parser.add_argument(
        "--version",
        default=None,
        metavar="X.Y.Z",
        help="override the version read from the installed package",
    )
    parser.add_argument(
        "--print-version",
        action="store_true",
        help="print the version pkgbuild should be given and write nothing",
    )
    args = parser.parse_args(argv[1:])
    version = args.version or msi_version()
    if args.print_version:
        print(version)
        return 0

    tree = build_distribution(version, args.package, args.arch)
    ET.indent(tree, space="    ")
    os.makedirs(os.path.abspath(args.out), exist_ok=True)
    target = os.path.join(args.out, "distribution.xml")
    tree.write(target, encoding="utf-8", xml_declaration=True)
    print(f"wrote {target}: {TITLE} {version} for {args.arch}, min macOS {MIN_MACOS}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
