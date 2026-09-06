# Building the installers

Two artifacts, each built on the machine it is meant for:

| Platform | Artifact | Authoring tool | Lands in |
| --- | --- | --- | --- |
| macOS (arm64) | `ptr-review-macos-arm64.pkg` | `pkgbuild` + `productbuild` | `/Applications/PTR-MS Review.app` |
| Windows (x86_64) | `ptr-review-windows-x86_64.msi` | WiX v3 `candle` + `light` | `C:\Program Files\PTR-MS Review` |

Both carry the same payload: a PyInstaller one-dir bundle — a Python interpreter,
NumPy, h5py, this package, and the bundled reference data — so a reviewer with no
Python installed can double-click it and review a run. At 0.4.0 that is 273 files and
46 MB on disk, 20 MB once packaged. Neither installer is signed or notarised; see
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

What *is* shared is the authoring: `packaging/ptr-app.spec` describes the bundle
identically on both, and `packaging/make_msi.py` and `packaging/make_pkg.py`
generate their installer sources from what the build produced rather than from a
hand-maintained file list.

## 1. Build the bundle (both platforms)

In an isolated environment — a virtualenv you activate, or `uv run` — with the
version of Python you want to ship:

```bash
pip install --upgrade pip
pip install ".[desktop]" pyinstaller
pyinstaller --noconfirm packaging/ptr-app.spec
```

`pyinstaller` is a build tool, never a runtime dependency. The `desktop` extra
(`pywebview`) is optional by design: the spec collects it when it is present so
a double-click opens a real window, and leaves it out when it is not, where the
same bundle falls back to a browser tab instead of failing. If you want the
window in the artifact — you do — install the extra before building.

That leaves `dist/PTR-MS Review.app/Contents/MacOS/ptr` on macOS and
`dist/ptr/ptr.exe` on Windows. Check it before wrapping it:

```bash
python scripts/smoke_frozen.py "dist/PTR-MS Review.app/Contents/MacOS/ptr"   # macOS
python scripts/smoke_frozen.py dist/ptr/ptr.exe                              # Windows
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
version=$(python packaging/make_pkg.py --print-version)
python packaging/make_pkg.py --out build/pkg --arch "$(uname -m)"
pkgbuild --component "dist/PTR-MS Review.app" --install-location /Applications \
  --identifier dk.samsmart.ptrms --version "$version" build/pkg/ptr-component.pkg
productbuild --distribution build/pkg/distribution.xml \
  --package-path build/pkg dist/ptr.pkg
mv dist/ptr.pkg dist/ptr-review-macos-arm64.pkg
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
| identifier | `dk.samsmart.ptrms` | the same as `CFBundleIdentifier` in the spec, so bundle and package are one product to the receipts database |
| title | `PTR-MS Review` | what Installer shows |
| minimum system | `11.0` | Big Sur is where arm64 macOS starts, and the payload is Mach-O thin arm64 — claiming lower would promise a run that cannot start |
| architectures | `$(uname -m)` | an arm64 bundle installed on an Intel Mac fails on the first double-click rather than at install time |
| scripts | none | `require-scripts="false"`; there is no preinstall or postinstall |

The version comes from the installed package metadata and falls back to
`pyproject.toml`, reduced to three numeric parts — the same rule
`packaging/make_msi.py` applies to the MSI, so one checkout gives one version
string in both installers.

`pkgbuild` wants a real bundle, so run it on `dist/PTR-MS Review.app`, not on
the folder inside it. An Intel artifact would be a separate run on an Intel
machine; `--arch x86_64` then describes it.

## 3. Windows: the `.msi`

```powershell
curl.exe -sSL -o wix3.zip https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip
Expand-Archive -Path wix3.zip -DestinationPath wix3 -Force
python packaging/make_msi.py dist/ptr build/msi/ptr-app.wxs
wix3/candle.exe -arch x64 -out build/msi/ptr-app.wixobj build/msi/ptr-app.wxs
wix3/light.exe -o dist/ptr.msi build/msi/ptr-app.wixobj
Move-Item dist/ptr.msi dist/ptr-review-windows-x86_64.msi
```

`make_msi.py` mirrors `dist/ptr` into WiX source: one component per directory,
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
| payload | `dist/PTR-MS Review.app` | `dist/ptr/` |
| installs to | `/Applications/PTR-MS Review.app` | `C:\Program Files\PTR-MS Review\` |
| the executable | `Contents/MacOS/ptr` | `ptr.exe` |
| entry point | double-click, or `open -a "PTR-MS Review"` | Start Menu → PTR-MS Review |
| console window | none (a windowed bundle logs to `~/.ptr-ms/log.txt`) | yes, and it prints the URL there |
| scope | the machine, needs administrator rights | the machine (`InstallScope: perMachine`), needs administrator rights |

A double-clicked bundle arrives **with no arguments at all** — that is how
Finder and the Start Menu shortcut launch it, and the plain `ptr` command line
answers that with usage text and exit code 2. A runtime hook
(`packaging/runtime_hook.py`) turns a bare launch inside a frozen bundle into
`ptr app`, so a double-click opens the review app. `scripts/smoke_frozen.py`
launches the bundle bare for exactly that reason; without the hook the artifact
installs cleanly and does nothing.

## Checking an artifact

`ptr --help` passes on a bundle that can do nothing else, so the check is
`scripts/smoke_frozen.py`, against **both** the built copy and the installed
one — the build folder proves PyInstaller worked, the installed copy proves the
installer worked:

```bash
python scripts/smoke_frozen.py "dist/PTR-MS Review.app/Contents/MacOS/ptr"
python scripts/smoke_frozen.py "/Applications/PTR-MS Review.app/Contents/MacOS/ptr"
```

It runs three phases: `app <file> --no-browser --port N` must serve the review
page for a synthetic run; the same executable with no arguments must open the
start screen; and `--window` must either open a window or say it fell back to a
browser tab, never die on a machine with no window server.

To look inside a package without installing it — what it will write, and where:

```bash
pkgutil --expand dist/ptr-review-macos-arm64.pkg /tmp/ptrpkg
lsbom /tmp/ptrpkg/ptr-component.pkg/Bom | head     # every path, relative to /Applications
head -3 /tmp/ptrpkg/ptr-component.pkg/PackageInfo   # identifier, version, install-location
pkgutil --check-signature dist/ptr-review-macos-arm64.pkg   # "Status: no signature"
spctl --assess --type open --context context:primary-signature -vv dist/ptr-review-macos-arm64.pkg
```

`pkgutil --expand` wants a destination that does not exist yet. The `Bom` is also what
`installer` records as a receipt, so `lsbom` is how you answer "what did that package
write?" — paths are relative to the install location, which is why they read
`./PTR-MS Review.app/…` instead of `/Applications/…`, with an AppleDouble `._` entry
beside each real file. `PackageInfo` is the same story in XML, plus the
`<bundle … id="dk.samsmart.ptrms">` line tying the payload to `CFBundleIdentifier`.
The last command is Gatekeeper's own opinion, and today it always answers
`rejected / source=no usable signature`.

## Installing and removing a `.pkg` from the command line

```bash
installer -pkginfo -pkg dist/ptr-review-macos-arm64.pkg   # the product title in there
sudo installer -pkg dist/ptr-review-macos-arm64.pkg -target /
```

That is the whole install; there is no drag-to-Applications step, and the path
never varies.

One caution when scripting it: `installer` returns, and writes its receipt, while
`installd` is still moving the payload from the staging sandbox into place —
`install.log` calls this "atomically shoved". The exit status therefore says nothing
about the destination, and a check placed immediately afterwards can look for the
bundle before the rename has happened. Ask the filesystem, with a wait around it:

```bash
sudo installer -pkg dist/ptr.pkg -target / -verboseR
for _ in $(seq 1 60); do
  [ -d "/Applications/PTR-MS Review.app" ] && break
  sleep 0.5
done
test -x "/Applications/PTR-MS Review.app/Contents/MacOS/ptr" || exit 1
``` `sudo installer` is what CI runs, and it is worth running once
even if you mean to double-click, because it fails loudly and prints the
destination. To see what a machine already has:

```bash
pkgutil --pkgs | grep dk.samsmart      # is it installed at all
pkgutil --pkg-info dk.samsmart.ptrms   # version and install time, from the receipt
pkgutil --files dk.samsmart.ptrms      # every file that receipt accounts for
```

Removing it is manual, because a `.pkg` has no uninstaller of its own — the
receipt is a record, not a script:

```bash
sudo rm -rf "/Applications/PTR-MS Review.app"
sudo pkgutil --forget dk.samsmart.ptrms   # drop the receipt too
```

To install into a scratch root instead of a real system, pass a directory:
`sudo installer -pkg dist/ptr.pkg -target /tmp/ptrroot` builds the tree there rather
than in `/Applications`, and writes its receipts into that image rather than your
system's. It still needs root, so it is not a way to avoid `sudo`. The Windows
equivalent is `msiexec /i dist\ptr.msi /qn` to install and `msiexec /x dist\ptr.msi /qn`
to remove it, with the app in `C:\Program Files\PTR-MS Review`.

## First run on a clean machine

Neither artifact is signed, so expect a warning the first time.

**macOS.** A downloaded file carries the `com.apple.quarantine` flag, and opening
the `.pkg` asks Gatekeeper about it; an unsigned package fails that check with
"cannot be verified" or "unidentified developer". Two ways through:

```bash
sudo installer -pkg ptr-review-macos-arm64.pkg -target /    # not routed through Gatekeeper
xattr -dr com.apple.quarantine ptr-review-macos-arm64.pkg    # or clear the flag, then double-click
xattr -dr com.apple.quarantine "/Applications/PTR-MS Review.app"
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
     --sign "Developer ID Application: …" "PTR-MS Review.app"` — sign the bundle
  with the hardened runtime. PyInstaller already ad-hoc signs it (`codesign -dv`
  reports `Signature=adhoc`, `Identifier=dk.samsmart.ptrms`), which is enough for
  arm64 macOS to run it and nothing like enough for Gatekeeper.
- `productsign --sign "Developer ID Installer: …" dist/ptr.pkg dist/ptr-signed.pkg`
  — sign the product archive itself.
- `xcrun notarytool submit dist/ptr-signed.pkg --keychain-profile … --wait` and
  then `xcrun stapler staple dist/ptr-signed.pkg` — notarize it, so the warning
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
