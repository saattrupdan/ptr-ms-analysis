# packaging/ptr-app.spec (78 lines)
# PyInstaller spec for the PTR-MS review app.
#
# One-dir, not one-file: a one-file bundle unpacks into %TEMP% on every start, which
# is slow, trips antivirus heuristics, and leaves a second copy of numpy behind. A
# folder the user can look at is easier to run, easier to debug and easier to sign.
#
# macOS gets a real .app wrapper around that folder, because LaunchServices only gives
# a plain executable a Dock icon, a name, and a right-click "Open" to get past
# Gatekeeper. Windows gets the folder, which the WiX project turns into an .msi.
#
# Build on the machine you are targeting — PyInstaller cannot cross-compile. The
# `package` workflow does exactly that on native macOS and Windows runners.
#
#   pip install . pyinstaller
#   pyinstaller --noconfirm packaging/ptr-app.spec

import importlib.util
import os
import sys
from importlib.metadata import version as distribution_version

from PyInstaller.utils.hooks import collect_all

IS_MAC = sys.platform == "darwin"
APP_NAME = "Sniff"
BUNDLE_ID = "dk.samsmart.sniff"

datas, binaries, hiddenimports = collect_all("ptr_ms_analysis")

# The icon is drawn here rather than stored in the repo: packaging/make_icons.py holds
# the only copy of the artwork, and this builds from it whichever container the platform
# reads. A committed PNG would be a second drawing to keep in step.
sys.path.insert(0, SPECPATH)
import make_icons  # noqa: E402

ICON_DIR = os.path.join(WORKPATH, "icons")
os.makedirs(ICON_DIR, exist_ok=True)
if IS_MAC:
    ICON = os.path.join(ICON_DIR, "sniff.icns")
    make_icons.icns_file(ICON)
else:
    ICON = os.path.join(ICON_DIR, "sniff.ico")
    with open(ICON, "wb") as _handle:
        _handle.write(make_icons.ico_bytes(make_icons.ICO_SIZES))

# The desktop extra is optional by design: bundle it when it is installed so a
# double-click opens a real window, and leave it out when it is not, where the very
# same bundle falls back to a browser tab instead of failing.
if importlib.util.find_spec("webview") is not None:
    _w_datas, _w_binaries, _w_hidden = collect_all("webview")
    datas += _w_datas
    binaries += _w_binaries
    hiddenimports += _w_hidden

# Imported lazily inside cmd_app, so freeze it explicitly rather than hoping the
# import graph reaches it.
hiddenimports += [
    "ptr_ms_analysis.app",
    "ptr_ms_analysis.analyze",
    "ptr_ms_analysis.viz",
    "ptr_ms_analysis.ptrms",
    "ptr_ms_analysis.formula_id",
]

try:
    VERSION = distribution_version("ptr_ms_analysis").split("+")[0].split("rc")[0]
except Exception:  # pragma: no cover - only when the package is not installed
    VERSION = "0.0.0"

a = Analysis(
    [os.path.join(SPECPATH, "ptr_entry.py")],  # SPECPATH is this file's directory
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    # A double-clicked bundle arrives with no arguments; see the hook.
    runtime_hooks=[os.path.join(SPECPATH, "runtime_hook.py")],
    # Nothing here plots or draws; these only bloat the bundle and slow startup.
    excludes=["tkinter", "matplotlib", "pandas", "scipy", "PIL", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ptr",
    icon=ICON,           # the Explorer and taskbar icon on Windows
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries are the single most common AV false positive
    # A bundle started from Finder has no console, and it does not need one: it opens
    # the page itself and logs to ~/.ptr-ms/log.txt. From a terminal the same build
    # prints, because `ptr app` is what a script calls.
    console=not IS_MAC,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_name=None,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ptr",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name=APP_NAME + ".app",
        icon=ICON,
        bundle_identifier=BUNDLE_ID,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            # The app talks to no network but itself; say so where it can be read.
            "NSLocalNetworkUsageDescription": (
                "Sniff serves its own review page on this computer only."
            ),
            "NSHumanReadableCopyright": "BSD-3-Clause",
        },
    )
