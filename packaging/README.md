# Building the installers

Two artifacts, each built on the machine it is meant for:

| Platform | Artifact | Authoring tool | Lands in |
| --- | --- | --- | --- |
| macOS (arm64) | `sniff-review-macos-arm64.pkg` | `pkgbuild` + `productbuild` | `/Applications/Sniff.app` |
| Windows (x86_64) | `sniff-review-windows-x86_64.msi` | WiX v3 `candle` + `light` | `C:\Program Files\Sniff` |

Both carry the same payload: a PyInstaller one-dir bundle — a Python interpreter,
NumPy, h5py, this package, and the bundled reference data — so a reviewer with no
Python installed can double-click it and review a run. At 0.5.0 that is 306 files and
46 MB on disk, 20 MB once packaged — the extra files over the 273 an unsigned
browser-only bundle carried are pywebview and PyObjC, which give it a window. Neither installer is signed or notarised; see
[What is not done yet](#what-is-not-done-yet).

This guide builds both by hand. The `package` workflow
(`.github/workflows/dist.yml`) runs exactly these commands on native macOS and
Windows runners, and a `v*` tag publishes the results as a GitHub Release.

## One payload, two installers

PyInstaller analyses the interpreter it is running under: it walks the import
graph, then copies the shared libraries and native extensions that interpreter
actually loads. A Linux or macOS machine has no Windows `python3xx.dll` to copy
and no way to learn which one is needed, so **PyInstaller cannot cross-compile**
— each bundle must be built on its target operating system.

The installers have the same problem in a different dress. An `.msi` is a
Windows Installer database, written by `candle`/`light` and validated against
the Windows Installer schema; a `.pkg` is a flat package holding a payload, a
bill of materials and a distribution script, written by `pkgbuild` and
`productbuild`, which ship with the macOS command line tools. Neither toolchain
runs usefully on the other platform. So there is one build per platform, which
is also what the CI matrix encodes.

What *is* shared is the authoring: `packaging/sniff-app.spec` describes the bundle
identically on both, and `packaging/make_msi.py` and `packaging/make_pkg.py`
generate their installer sources from what the build produced rather than from a
hand-maintained file list.

## 1. Build the bundle (both platforms)

In an isolated environment — a virtualenv you activate, or `uv run` — with the
version of Python you want to ship:

```bash
uv sync --extra desktop
uv run --with pyinstaller pyinstaller --noconfirm packaging/sniff-app.spec
```

`pyinstaller` is a build tool, never a runtime dependency. The `desktop` extra
(`pywebview`) is optional by design: the spec collects it when it is present so
a double-click opens a real window, and leaves it out when it is not, where the
same bundle falls back to a browser tab instead of failing. If you want the
window in the artifact — you do — install the extra before building, and check
the build log says so:

```
sniff-app.spec: bundling the desktop window (pywebview + its dependencies)
```

Installing the extra is necessary and, on its own, not enough. `desktop.py`
reaches pywebview through `importlib.import_module("webview")`, and PyInstaller's
analysis follows only `import` statements, so nothing in the build looked at
pywebview at all until the spec started calling `collect_all()` for it — and
`collect_all()` covers one package's own files, not the modules its `__init__`
chain imports. `webview/__init__` imports `bottle` and `proxy_tools` at module
scope. The bundle therefore held `webview`, installed cleanly, and failed its
first `import webview` at runtime with `No module named 'bottle'` — which
`desktop.py` reported as *"the desktop extra is not installed"*, advising the user
to install an extra that was inside the bundle at that moment. The spec now
bundles what pywebview's own metadata declares, plus the GUI toolkit its installed
backend imports, read out of that source with `ast` because PyObjC on macOS and
the WebView2 bridge on Windows are not the same list.

### Does the artifact open a window?

Ask the running app rather than watching for a window:

```bash
curl -s http://127.0.0.1:8765/api/state | uv run python -c "import json,sys; print(json.load(sys.stdin)['surface'])"
```

`window` or `browser`. For a macOS bundle this is the only reliable signal: it is
built `console=False`, so it writes to no terminal, and the line explaining a
fallback never reaches anyone who is not already reading `~/.sniff/log.txt`.
`scripts/smoke_frozen.py` reads this field for that reason. On a desktop machine
`--window` must report `window`; a CI runner with no window server may report
`browser`, and neither counts as a crash. To see the same fact from outside,
`lsappinfo list | grep -i sniff` shows `type="Foreground"` only once something has
registered with the window server — a bundle that only opened a tab never appears
there at all.

That leaves `dist/Sniff.app/Contents/MacOS/sniff` on macOS and
`dist/sniff/sniff.exe` on Windows. Check it before wrapping it:

```bash
uv run python scripts/smoke_frozen.py "dist/Sniff.app/Contents/MacOS/sniff"   # macOS
uv run python scripts/smoke_frozen.py dist/sniff/sniff.exe                              # Windows
```

The smoke script writes a tiny synthetic `.h5` file, starts the bundle against
it, and insists the review page is served; see [Checking an
artifact](#checking-an-artifact).

## 2. macOS: the `.pkg`

A macOS product archive is two nested things, and two tools write them. A
**component package** is the payload — the `.app` folder, the path it goes to,
and a bill of materials (a `Bom`) that lists every file it wrote. A
**distribution** is the script wrapped around it: the title, the version, the
minimum system, the architectures that may install it. `pkgbuild` writes the
first, `productbuild` combines it with the second:

```bash
version=$(uv run python packaging/make_pkg.py --print-version)
uv run python packaging/make_pkg.py --out build/pkg --arch "$(uname -m)"
pkgbuild --component "dist/Sniff.app" --install-location /Applications \
  --identifier dk.samsmart.sniff --version "$version" build/pkg/sniff-component.pkg
productbuild --distribution build/pkg/distribution.xml \
  --package-path build/pkg dist/sniff.pkg
mv dist/sniff.pkg dist/sniff-review-macos-arm64.pkg
```

`--package-path` is how `productbuild` finds the payload: `distribution.xml`
names the component package by **file name**, not by path, so the name in it (set
with `make_pkg.py --package`) has to be the one `pkgbuild` was told to write.
`--print-version` exists because the same version string has to reach both
`pkgbuild --version` and the distribution, and nobody should have to retype it.

The distribution is written by `packaging/make_pkg.py` rather than synthesized
by `productbuild --synthesize` so that it is reviewable and stable: it is a
function of its arguments alone — no absolute paths, no timestamps — so the same
checkout gives the same XML twice. It declares:

| Field | Value | Why |
| --- | --- | --- |
| identifier | `dk.samsmart.sniff` | the same as `CFBundleIdentifier` in the spec, so bundle and package are one product to the receipts database |
| title | `Sniff` | what Installer shows |
| minimum system | `11.0` | Big Sur is where arm64 macOS starts, and the payload is Mach-O thin arm64 — claiming lower would promise a run that cannot start |
| architectures | `$(uname -m)` | an arm64 bundle installed on an Intel Mac fails on the first double-click rather than at install time |
| scripts | none | `require-scripts="false"`; there is no preinstall or postinstall |

The version comes from the installed package metadata and falls back to
`pyproject.toml`, reduced to three numeric parts — the same rule
`packaging/make_msi.py` applies to the MSI, so one checkout gives one version
string in both installers.

`pkgbuild` wants a real bundle, so run it on `dist/Sniff.app`, not on
the folder inside it. An Intel artifact would be a separate run on an Intel
machine; `--arch x86_64` then describes it.

## 3. Windows: the `.msi`

```powershell
curl.exe -sSL -o wix3.zip https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip
Expand-Archive -Path wix3.zip -DestinationPath wix3 -Force
uv run python packaging/make_msi.py dist/sniff build/msi/sniff-app.wxs
wix3/candle.exe -arch x64 -out build/msi/sniff-app.wixobj build/msi/sniff-app.wxs
wix3/light.exe -o dist/sniff.msi build/msi/sniff-app.wixobj
Move-Item dist/sniff.msi dist/sniff-review-windows-x86_64.msi
```

`make_msi.py` mirrors `dist/sniff` into WiX source: one component per directory,
one file id and one GUID derived from each path, so the folder layout — not a
hand-maintained list — is what gets installed, an upgrade replaces exactly the
files that changed, and an uninstall removes exactly the ones it put there. The
GUIDs are derived rather than random, so they survive a rebuild, which is what
lets a later version upgrade clean up after an earlier one.

### Why WiX v3, and why not v6

WiX v4 and v5 moved harvesting into the tool (`wix build file.wxs`), where the
split of files into components depends on the tool version — and a component
GUID that changes between builds leaves files behind on upgrade. WiX **v3.14** is
pinned for a sharper reason: it is the last MIT-licensed release. WiX v6 and
later refuse to build anything until the **Open Source Maintenance Fee** EULA is
accepted, which asks a fee of anyone shipping a product for money. There is no
reason to hang that condition over this project's builds, so the authoring here
is deliberately v3-shaped (`candle` then `light`, a `<Wix>` root in the 2006
namespace) and will not feed a v6 toolchain.

## What is inside, and where it lands

Both installers put the same bundle in a system location and leave the user's
data alone. Nothing is downloaded or uploaded: the app serves its page on
`127.0.0.1` and writes its config beside the `.h5` file it opened.

| | macOS | Windows |
| --- | --- | --- |
| payload | `dist/Sniff.app` | `dist/sniff/` |
| installs to | `/Applications/Sniff.app` | `C:\Program Files\Sniff\` |
| the executable | `Contents/MacOS/sniff` | `sniff.exe` |
| entry point | double-click, or `open -a "Sniff"` | Start Menu → Sniff |
| console window | none (a windowed bundle logs to `~/.sniff/log.txt`) | yes, and it prints the URL there |
| scope | the machine, needs administrator rights | the machine (`InstallScope: perMachine`), needs administrator rights |

A double-clicked bundle arrives **with no arguments at all** — that is how
Finder and the Start Menu shortcut launch it, and the plain `sniff` command line
answers that with usage text and exit code 2. A runtime hook
(`packaging/runtime_hook.py`) turns a bare launch inside a frozen bundle into
`sniff app`, so a double-click opens the review app. `scripts/smoke_frozen.py`
launches the bundle bare for exactly that reason; without the hook the artifact
installs cleanly and does nothing.

## The icon, and what a rename costs

The mark is not a file in the repo. `packaging/make_icons.py` holds the geometry
— a rounded teal tile, the mass-spectrum trace, one warm nose over the tallest
peak — and draws it twice: as SVG (`gfx/sniff.svg`, and the same string in
`sniff/brand.py`, which is what the pages show) and as a bitmap. The
spec calls it during the build, so the macOS job gets a `sniff.icns` through
`iconutil` and the Windows job gets a `sniff.ico`, both from the same run.

```bash
uv run python packaging/make_icons.py --svg gfx/sniff.svg      # the drawing, as SVG
uv run python packaging/make_icons.py --ico /tmp/sniff.ico     # what the MSI attaches
uv run python packaging/make_icons.py --icns /tmp/sniff.icns   # what the .app attaches
uv run python packaging/make_icons.py --png /tmp/sniff.png --size 512
```

Nothing here needs an image library, which is the reason it is drawn rather than
committed: the same script has to run on a macOS runner and a Windows runner
without installing anything, and `tests/test_brand.py` fails if the page's SVG
and the icon's primitives ever disagree.

The product is now called **Sniff**. `UPGRADE_CODE` did not change, so an
installed Windows copy upgrades into the new name in place. On macOS the
identifier did change — `dk.samsmart.ptrms` became `dk.samsmart.sniff`, matching
`CFBundleIdentifier` — and to the receipts database that is a different product:
the `.pkg` installs `Sniff.app` beside the old `PTR-MS Review.app` rather than
replacing it. Remove the old one deliberately, once:

```bash
ls -d "/Applications/PTR-MS Review.app" 2>/dev/null &&
  sudo rm -rf "/Applications/PTR-MS Review.app" && sudo pkgutil --forget dk.samsmart.ptrms
```

Nothing in either installer deletes it on its own. Two copies would not corrupt
anything — the newer one takes the next free port — but two icons in the Dock is
its own kind of bug.

## Checking an artifact

`sniff --help` passes on a bundle that can do nothing else, so the check is
`scripts/smoke_frozen.py`, against **both** the built copy and the installed
one — the build folder proves PyInstaller worked, the installed copy proves the
installer worked:

```bash
uv run python scripts/smoke_frozen.py "dist/Sniff.app/Contents/MacOS/sniff"
uv run python scripts/smoke_frozen.py "/Applications/Sniff.app/Contents/MacOS/sniff"
```

It runs three phases: `app <file> --no-browser --port N` must serve the review
page for a synthetic run; the same executable with no arguments must open the
start screen; and `--window` must report which surface it got, never die on a
machine with no window server.

The third phase asks `/api/state` for the surface. An earlier version decided by
grepping the captured output for the word `window`, which was wrong twice over: a
`console=False` macOS bundle writes nothing there, so an empty log read as
success, and the one line that reported failure — `no desktop window (...)` —
contained the word as well. That is how a browser-only artifact passed for green.

To look inside a package without installing it — what it will write, and where:

```bash
pkgutil --expand dist/sniff-review-macos-arm64.pkg /tmp/sniffpkg
lsbom /tmp/sniffpkg/sniff-component.pkg/Bom | head     # every path, relative to /Applications
head -3 /tmp/sniffpkg/sniff-component.pkg/PackageInfo   # identifier, version, install-location
pkgutil --check-signature dist/sniff-review-macos-arm64.pkg   # "Status: no signature"
spctl --assess --type open --context context:primary-signature -vv dist/sniff-review-macos-arm64.pkg
```

`pkgutil --expand` wants a destination that does not exist yet. The `Bom` is also what
`installer` records as a receipt, so `lsbom` is how you answer "what did that package
write?" — paths are relative to the install location, which is why they read
`./Sniff.app/…` instead of `/Applications/…`, with an AppleDouble `._` entry
beside each real file. `PackageInfo` is the same story in XML, plus the
`<bundle … id="dk.samsmart.sniff">` line tying the payload to `CFBundleIdentifier`.
The last command is Gatekeeper's own opinion, and today it always answers
`rejected / source=no usable signature`.

## Installing and removing a `.pkg` from the command line

```bash
installer -pkginfo -pkg dist/sniff-review-macos-arm64.pkg   # the product title in there
sudo installer -pkg dist/sniff-review-macos-arm64.pkg -target /
```

That is the whole install; there is no drag-to-Applications step, and the path
never varies.

One caution when scripting it: **a successful `installer` run is not proof that
anything arrived in `/Applications`.** It reports success and writes its receipt
without regard to where the payload ended up, and there is a specific way to lose the
files. If some copy of the bundle has been *run* from another path, LaunchServices
registers the bundle id there, and `installd` then treats that path as the bundle's
home and moves the installed payload onto it — `/Applications` stays empty, and
`install.log` says so in plain sight:

```text
PackageKit: Applications/Sniff.app relocated to Users/you/work/…/dist/PTR-MS Review.app
PackageKit: Touched bundle /Users/you/work/…/dist/Sniff.app
```

This is what the CI job hit: it smoke-tested the bundle inside `dist/` first, and the
package install then landed there. The same commands passed on one runner and failed on
the next, because whether the id was still registered was a property of the machine.
The check is therefore to move any built copy out of the way, unregister it, and then
ask the filesystem rather than the exit status:

```bash
mv "dist/Sniff.app" "$RUNNER_TEMP/built-app"
sudo installer -pkg dist/sniff.pkg -target / -verboseR
for _ in $(seq 1 60); do
  [ -d "/Applications/Sniff.app" ] && break
  sleep 0.5
done
test -x "/Applications/Sniff.app/Contents/MacOS/sniff" || exit 1
``` `sudo installer` is what CI runs, and it is worth running once
even if you mean to double-click, because it fails loudly and prints the
destination. To see what a machine already has:

```bash
pkgutil --pkgs | grep dk.samsmart      # is it installed at all
pkgutil --pkg-info dk.samsmart.sniff   # version and install time, from the receipt
pkgutil --files dk.samsmart.sniff      # every file that receipt accounts for
```

Removing it is manual, because a `.pkg` has no uninstaller of its own — the
receipt is a record, not a script:

```bash
sudo rm -rf "/Applications/Sniff.app"
sudo pkgutil --forget dk.samsmart.sniff   # drop the receipt too
```

To install into a scratch root instead of a real system, pass a directory:
`sudo installer -pkg dist/sniff.pkg -target /tmp/sniffroot` builds the tree there rather
than in `/Applications`, and writes its receipts into that image rather than your
system's. It still needs root, so it is not a way to avoid `sudo`. The Windows
equivalent is `msiexec /i dist\sniff.msi /qn` to install and `msiexec /x dist\sniff.msi /qn`
to remove it, with the app in `C:\Program Files\Sniff`.

## First run on a clean machine

Neither artifact is signed, so expect a warning the first time.

**macOS.** A downloaded file carries the `com.apple.quarantine` flag, and opening
the `.pkg` asks Gatekeeper about it; an unsigned package fails that check with
"cannot be verified" or "unidentified developer". Two ways through:

```bash
sudo installer -pkg sniff-review-macos-arm64.pkg -target /    # not routed through Gatekeeper
xattr -dr com.apple.quarantine sniff-review-macos-arm64.pkg    # or clear the flag, then double-click
xattr -dr com.apple.quarantine "/Applications/Sniff.app"
```

`installer(8)` never asks Gatekeeper, so installing from a terminal works
whatever the download flag says. The flag sits on the downloaded archive rather
than on the files the installer writes, so an installed app starts from Finder
without a prompt; the last line above is for the case where it does, and for an
app copied out of a `.zip` — Firefox propagates the flag into extracted files,
where `gh run download` and `curl` do not. Newer macOS (15 onward) may not offer
the old Control-click → Open override for a download from an unidentified
developer, in which case the route is the terminal command above or Settings →
Privacy & Security → Open Anyway. Nothing here is a substitute for signing — see
below.

**Windows.** SmartScreen says "Windows protected your PC" because the `.msi` has
no Authenticode signature. "More info" → "Run anyway" installs it; your
corporate antivirus may still take an interest in an unsigned bundle.

Both warnings disappear once the artifact is signed and notarised.

## What is not done yet

Nothing signs either artifact. There is no Developer ID certificate, no
notarization, no Authenticode certificate, and no signing step in the workflow —
`pkgbuild` and `productbuild` will happily sign a package and `codesign` a bundle
if a Developer ID is in the keychain of whoever runs them, but nothing does so
today. Concretely, a build is missing:

- `codesign --force --deep --options runtime --timestamp \
     --sign "Developer ID Application: …" "Sniff.app"` — sign the bundle
  with the hardened runtime. PyInstaller already ad-hoc signs it (`codesign -dv`
  reports `Signature=adhoc`, `Identifier=dk.samsmart.sniff`), which is enough for
  arm64 macOS to run it and nothing like enough for Gatekeeper.
- `productsign --sign "Developer ID Installer: …" dist/sniff.pkg dist/sniff-signed.pkg`
  — sign the product archive itself.
- `xcrun notarytool submit dist/sniff-signed.pkg --keychain-profile … --wait` and
  then `xcrun stapler staple dist/sniff-signed.pkg` — notarize it, so the warning
  is gone on a machine with no network view of your certificate.
- Authenticode on the `.msi`, and `signtool`/`msiexec`-friendly timestamps.

The spec and both authoring scripts need no changes for any of that: signing is a
step to add between the build and the archive. Until then, ship the installers
with the first-run instructions above, and say plainly that an unsigned `.pkg`
still trips Gatekeeper on first run.

Two smaller gaps, both deliberate: the distribution has no `welcome`, `license`
or `conclusion` resource (no `--resources` directory is authored, so Installer
shows its own default screens), and there is no `.icns` in the repo, so Finder
shows the generic application icon.
