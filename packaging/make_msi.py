"""Write WiX source for an .msi that installs a PyInstaller folder (see packaging/).

WiX v4/v5 can harvest a directory itself, but how it splits files into components
depends on the tool version. An MSI's components are a stable interface — their GUIDs
must not change between builds or an upgrade leaves files behind — so the split is
written out here instead: one component per directory, one stable GUID per directory,
so the same tree always produces the same XML.

    python packaging/make_msi.py dist/sniff build/msi/sniff-app.wxs
    wix build build/msi/sniff-app.wxs -arch x64 -o dist/sniff.msi
"""

import os
import re
import sys
import uuid
import xml.etree.ElementTree as ET

APP_NAME = "Sniff"
# The product name changed on 2026-09-07 ("PTR-MS Review" -> "Sniff"); UPGRADE_CODE
# below deliberately did not, so an installed copy of the old name upgrades into the
# new one rather than sitting beside it.
MANUFACTURER = "Dan Saattrup Smart"
URL = "https://github.com/saattrupdan/sniff"
# Generated once, never regenerated: this is what identifies the product across versions,
# so it must survive a version bump and a re-clone of the repository.
UPGRADE_CODE = "8f0c2f4c-6e1b-5a0d-9e2f-4b7c1a3d6e85"
# MIT-licensed, and the last version without the Open Source Maintenance Fee EULA
# that WiX v6+ insists on accepting. The authoring below is v3-shaped for that reason.
NAMESPACE = "http://schemas.microsoft.com/wix/2006/wi"
# Keep the original namespace so existing component GUIDs remain valid after the
# product rename.
GUID_SPACE = uuid.uuid5(uuid.NAMESPACE_URL, "ptr-ms-analysis/component/")
# Where each component keeps the key path an MSI insists on. A component may not key on
# one of this bundle's .dll files, so it keys on a registry value instead.
REGISTRY_KEY = r"Software\Dan Saattrup Smart\Sniff\components"


def project_version() -> str:
    """Read the project version from the checkout's pyproject metadata."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "..", "pyproject.toml"), encoding="utf-8") as handle:
            for line in handle:
                found = re.match(r'\s*version\s*=\s*["\']([^"\']+)', line)
                if found:
                    return found.group(1)
    except OSError:
        pass
    return "0.0.0"


def msi_version() -> str:
    """Return a three-part version for the MSI from repository metadata."""
    raw = project_version().split("+")[0]
    parts = []
    for chunk in raw.replace("-", ".").split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(digits)
    return ".".join((parts + ["0", "0"])[:3])


def _id(kind: str, relative: str) -> str:
    """A deterministic WiX identifier for a path, so rebuilds match component for
    component. MSI identifiers must be short and unambiguous, hence the fixed width."""
    digest = uuid.uuid5(GUID_SPACE, f"{kind}/{relative.lower().replace(chr(92), '/')}")
    return f"{kind[0].upper()}{digest.hex[:20]}"


def _component(parent, relative: str, source: str, files) -> str:
    """One component per directory. Returns its Id for the enclosing feature."""
    component = ET.SubElement(
        parent,
        "Component",
        {"Id": _id("component", relative), "Guid": str(uuid.uuid5(GUID_SPACE, relative))},
    )
    ET.SubElement(
        component,
        "RegistryValue",
        {
            "Root": "HKMU",
            "Key": REGISTRY_KEY,
            "Name": _id("key", relative),
            "Type": "integer",
            "Value": "1",
            "KeyPath": "yes",
        },
    )
    for name in files:
        # Absolute on purpose: candle resolves a relative Source against the .wxs
        # file's own folder, which is the build directory, not the checkout. The
        # separators are left as this OS writes them — rewriting them here turns a
        # POSIX path into one with no root.
        ET.SubElement(
            component,
            "File",
            {
                # A File with no Id is given one derived from its filename alone, so
                # the LICENSE.md in each bundled package collides (LGHT0091). Ids must
                # be unique across the whole install, so derive them from the path.
                "Id": _id("file", f"{relative}/{name}"),
                "Source": os.path.join(source, relative.replace("/", os.sep), name),
            },
        )
    return component.get("Id")


def _tree(parent, source: str, relative: str, feature) -> None:
    """Emit `parent`'s component and recurse, mirroring the folder structure."""
    dirnames, filenames = [], []
    for entry in sorted(os.scandir(os.path.join(source, relative.replace("/", os.sep))),
                        key=lambda e: e.name):
        (dirnames if entry.is_dir() else filenames).append(entry.name)
    component_id = _component(parent, relative, source, filenames)
    ET.SubElement(feature, "ComponentRef", {"Id": component_id})
    for name in dirnames:
        child = ET.SubElement(parent, "Directory", {"Id": _id("dir", f"{relative}/{name}"),
                                                   "Name": name})
        _tree(child, source, f"{relative}/{name}" if relative else name, feature)


def build_wxs(source: str, product_version: str) -> ET.ElementTree:
    """WiX v3 authoring: a Product, one Feature, and the built folder mirrored.

    The PyInstaller executable is at the source root; its ``_internal`` one-dir
    contents folder is mirrored beneath it as ordinary application data.
    """
    wix = ET.Element("Wix", {"xmlns": NAMESPACE})
    product = ET.SubElement(
        wix,
        "Product",
        {
            # Id="*" means a fresh product code per build, which is what MajorUpgrade
            # wants: the version goes up, the UpgradeCode does not.
            "Id": "*",
            "Name": APP_NAME,
            "Manufacturer": MANUFACTURER,
            "Version": product_version,
            "Language": "1033",
            "UpgradeCode": UPGRADE_CODE,
        },
    )
    ET.SubElement(
        product,
        "Package",
        {
            "InstallerVersion": "500",
            "Compressed": "yes",
            "InstallScope": "perMachine",
            "Description": APP_NAME,
        },
    )
    ET.SubElement(
        product,
        "MajorUpgrade",
        {"DowngradeErrorMessage": f"A newer version of {APP_NAME} is already installed."},
    )
    ET.SubElement(product, "MediaTemplate", {"EmbedCab": "yes"})
    ET.SubElement(product, "Property", {"Id": "ARPCOMMENTS",
                                        "Value": "Review PTR-MS measurements, export the CSV"})
    ET.SubElement(product, "Property", {"Id": "ARPURLINFOABOUT", "Value": URL})

    targetdir = ET.SubElement(product, "Directory", {"Id": "TARGETDIR", "Name": "SourceDir"})
    program_files = ET.SubElement(targetdir, "Directory", {"Id": "ProgramFiles64Folder"})
    app_dir = ET.SubElement(program_files, "Directory", {"Id": "APPLICATIONFOLDER",
                                                          "Name": APP_NAME})
    feature = ET.SubElement(product, "Feature", {"Id": "ApplicationFeature",
                                                 "Title": APP_NAME, "Level": "1"})
    _tree(app_dir, os.path.abspath(source), "", feature)

    # The Start Menu entry, because the console window it opens is where the URL appears.
    menu = ET.SubElement(targetdir, "Directory", {"Id": "ProgramMenuFolder"})
    menu_dir = ET.SubElement(menu, "Directory", {"Id": _id("dir", "__menu__"), "Name": APP_NAME})
    shortcut = ET.SubElement(menu_dir, "Component", {"Id": _id("component", "__shortcut__"),
                                                     "Guid": str(uuid.uuid5(GUID_SPACE,
                                                                            "__shortcut__"))})
    ET.SubElement(
        shortcut,
        "Shortcut",
        {
            "Id": "StartMenuShortcut",
            "Name": APP_NAME,
            "Description": "Open the Sniff PTR-MS review app",
            "Target": "[APPLICATIONFOLDER]sniff.exe",
            "WorkingDirectory": "APPLICATIONFOLDER",
        },
    )
    ET.SubElement(shortcut, "RemoveFolder", {"Id": "RemoveMenuFolder", "On": "uninstall"})
    ET.SubElement(
        shortcut,
        "RegistryValue",
        {"Root": "HKCU", "Key": REGISTRY_KEY, "Name": "startmenu", "Type": "integer",
         "Value": "1", "KeyPath": "yes"},
    )
    ET.SubElement(feature, "ComponentRef", {"Id": shortcut.get("Id")})
    return ET.ElementTree(wix)


def main(argv):
    if len(argv) != 3:
        raise SystemExit(__doc__)
    source, target = argv[1], argv[2]
    if not os.path.isdir(source):
        raise SystemExit(f"no such folder to install: {source}")
    tree = build_wxs(source, msi_version())
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    tree.write(target, encoding="utf-8", xml_declaration=True)
    files = len(list(tree.iter("File")))
    components = len(list(tree.iter("Component")))
    print(f"wrote {target}: {files} files in {components} components")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
