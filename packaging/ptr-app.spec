# PyInstaller spec for the PTR-MS review app.
#
# One-dir, not one-file: a one-file bundle unpacks into %TEMP% on every start, which
# is slow, trips antivirus heuristics, and leaves a second copy of numpy behind. A
# folder the user can look at is easier to run, easier to debug and easier to sign.
#
# Build on the machine you are targeting — PyInstaller cannot cross-compile. The
# `package` workflow does exactly that on native macOS and Windows runners.
#
#   pip install . pyinstaller
#   pyinstaller --noconfirm packaging/ptr-app.spec

import os

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("ptr_ms_analysis")

# Imported lazily inside cmd_app, so freeze it explicitly rather than hoping the
# import graph reaches it.
hiddenimports += [
    "ptr_ms_analysis.app",
    "ptr_ms_analysis.analyze",
    "ptr_ms_analysis.viz",
    "ptr_ms_analysis.ptrms",
    "ptr_ms_analysis.formula_id",
]

a = Analysis(
    [os.path.join(SPECPATH, "ptr_entry.py")],  # SPECPATH is this file's directory
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
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
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries are the single most common AV false positive
    console=True,  # the app prints its URL; a window that shows it is worth keeping
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
