# packaging/sniff-app.spec (78 lines)
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
#   uv sync --extra desktop
#   uv run --with pyinstaller pyinstaller --noconfirm packaging/sniff-app.spec
#
# The desktop extra is what gives the bundle a window of its own; built without it the
# bundle still works, and opens a browser tab instead. The spec says so in its log.

import ast
import importlib.util
import os
import sys
from importlib.metadata import requires

from PyInstaller.utils.hooks import collect_all

IS_MAC = sys.platform == "darwin"
APP_NAME = "Sniff"
BUNDLE_ID = "dk.samsmart.sniff"

datas, binaries, hiddenimports = collect_all("sniff")

# The icon is drawn here rather than stored in the repo: packaging/make_icons.py holds
# the only copy of the artwork, and this builds from it whichever container the platform
# reads. A committed PNG would be a second drawing to keep in step.
sys.path.insert(0, SPECPATH)
import make_icons  # noqa: E402
from make_msi import project_version  # noqa: E402

# Under build/, which is gitignored. PyInstaller's own work-path global is spelled
# differently across versions, and the spec is read on Windows and macOS runners, so
# the one name that never changes is used instead.
ICON_DIR = os.path.join(os.path.dirname(SPECPATH), "build", "icons")
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
def _requirement_names(package):
    """Top-level module names ``package`` declares as dependencies.

    Distribution names are not module names, so each is taken down to its import name
    and dropped if it cannot be imported here.
    """
    names = []
    for spec in requires(package) or []:
        if "extra ==" in spec or ";" in spec.split(";")[0]:
            continue  # an optional extra, not something pip installed for us
        name = spec.split(";")[0].split("[")[0]
        for op in ("===", "==", "~=", ">=", "<=", ">", "<", "!="):
            name = name.split(op)[0]
        name = name.strip().replace("-", "_")
        if name:
            names.append(name)
    return names


def _backend_toolkits():
    """Modules the installed GUI backend imports once a window is actually built.

    Read out of the backend's own source, not from a list: pywebview imports PyObjC on
    macOS and the WebView2 bridge on Windows, and a toolkit missing from the bundle
    fails at window time rather than at start-up.
    """
    import webview  # only reached when the extra is installed

    root = os.path.dirname(webview.__file__)
    found = set()
    for entry in sorted(os.listdir(os.path.join(root, "platforms"))):
        if not entry.endswith(".py") or entry == "__init__.py":
            continue
        try:
            tree = ast.parse(open(os.path.join(root, "platforms", entry)).read())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.add(node.names[0].name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return sorted(
        name for name in found
        if not name.startswith(("webview", "sniff", "."))
        and name not in {"os", "sys", "time", "re", "json", "threading", "typing", "math"}
        and importlib.util.find_spec(name) is not None
    )


if importlib.util.find_spec("webview") is not None:
    _w_datas, _w_binaries, _w_hidden = collect_all("webview")
    datas += _w_datas
    binaries += _w_binaries
    hiddenimports += _w_hidden

    # ...but collect_all() covers one package's own files, not the modules that its
    # __init__ chain imports. The app reaches pywebview through importlib.import_module
    # inside a function, which modulegraph never sees, so nothing else in the build was
    # looking at pywebview's dependencies either -- and webview/__init__ imports bottle
    # and proxy_tools at module scope. A bundle built that way installs cleanly, starts
    # cleanly, and then fails to open a window with an ImportError that looks like a
    # missing extra. So: whatever pip installed next to pywebview is bundled with it.
    for _dep in _requirement_names("pywebview"):
        if importlib.util.find_spec(_dep) is not None:
            _d_datas, _d_binaries, _d_hidden = collect_all(_dep)
            datas += _d_datas
            binaries += _d_binaries
            hiddenimports += _d_hidden

    # The GUI toolkit a backend imports only when a window is actually created is a
    # second, later failure of the same kind. Read it out of the installed backend's
    # source rather than from a list someone remembered: PyObjC on macOS, and the
    # WinForms/WebView2 bridge on Windows.
    hiddenimports += _backend_toolkits()
    print("sniff-app.spec: bundling the desktop window (pywebview + its dependencies)")
else:
    # Loud, because the alternative is a shipped installer that opens a browser tab and
    # a build log that says nothing about it.
    print(
        "sniff-app.spec: WARNING - pywebview is not installed in this environment, so "
        "this bundle will open a browser tab instead of its own window. "
        "Build with: uv sync --extra desktop && uv run --with pyinstaller pyinstaller ..."
    )

# Imported lazily inside cmd_app, so freeze it explicitly rather than hoping the
# import graph reaches it.
hiddenimports += [
    "sniff.app",
    "sniff.analyze",
    "sniff.viz",
    "sniff.ptrms",
    "sniff.formula_id",
]

VERSION = project_version().split("+")[0].split("rc")[0]

a = Analysis(
    [os.path.join(SPECPATH, "sniff_entry.py")],  # SPECPATH is this file's directory
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

def make_executable(name, console):
    return EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        icon=ICON,  # the Explorer and taskbar icon on Windows
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,  # UPX-packed binaries are the most common AV false positive
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_name=None,
        # Keep visible executables at the bundle root while placing Python modules,
        # data, and shared libraries in PyInstaller's private one-dir contents folder.
        # Using "." makes the collected ``sniff`` package collide with the executable
        # of the same name during COLLECT.
        contents_directory="_internal",
    )


# The desktop launcher opens its own UI and logs to ~/.sniff/log.txt. Keeping it
# windowed on Windows prevents a command prompt from remaining beside the app.
exe = make_executable(name="sniff", console=False)
executables = [exe]
if not IS_MAC:
    # Windowed programs have no stdout/stderr on Windows. Preserve a separate terminal
    # launcher for JSON-producing CLI commands rather than silently swallowing output.
    executables.append(make_executable(name="sniff-cli", console=True))

coll = COLLECT(
    *executables,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="sniff",
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
