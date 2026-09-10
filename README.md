# Sniff

Open-source reprocessor for IONICON IoniTOF PTR-MS / PTR-TOF `.h5` files — a
replacement for the proprietary PTR-MS Viewer. Extracts product-ion peaks from the
raw mass spectra, transmission-corrects them, converts to concentration (ppb and
µg/m³), and summarises per time segment.

**Agent-driven by design.** The CLI does the deterministic physics and detects
candidate peaks (with compound assignments + artifact flags) and time segments; an
agent assigns chemistry and curates segments. Humans talk to the agent, not to this
CLI. The commands below describe the complete package interface.

## Install / run

For normal use, download the Sniff installer for your platform from the repository's
GitHub Release page. The macOS `.pkg` installs **Sniff.app** in `/Applications`; the
Windows `.msi` installs Sniff in `Program Files`. The installers include Python,
NumPy, h5py and the PTR reference data — no separate Python installation is needed.

After installation, launch Sniff from the Applications folder or Start Menu. The
command-line executable is also available as `sniff`:

```bash
sniff --help
sniff inspect FILE.h5
```

For development from this checkout, use the project environment rather than an index
installation:

```bash
uv sync
uv run sniff --help
uv run sniff rates water
```

Sniff requires Python 3.9 or newer when run from a checkout. It works on macOS,
Linux and Windows; packaged desktop installers are currently produced for macOS and
Windows.

## Commands (all discovery output is JSON)

```bash
sniff inspect  FILE.h5                       # metadata, calibration, concentration-K, Vm
sniff peaks    FILE.h5                       # peaks + a ready-to-use suggested_label + top formula (--full for all candidates)
sniff segments FILE.h5                       # stable plateaus (high=sample / low=bg)
# agent curates peaks + ranges into cfg.json, then:
sniff viz      FILE.h5 --config cfg.json --out results.csv   # serve review; 'Done' -> writes CSV
sniff viz      FILE.h5 --config cfg.json --html review.html   # portable standalone HTML instead
sniff analyze  FILE.h5 \                     # no review: curated config -> Viewer-style CSV
    --config cfg.json --include-cycle-rows --out results.csv
sniff analyze  FILE.h5 --auto-peaks --auto-segments --out results.csv   # zero-curation fallback (auto-labels, drops noise)
sniff calibrate FILE.h5 viewer.csv          # fit concentration constant K -> pass via --K
sniff compare   results.csv viewer.csv --per-mass   # accuracy vs a Viewer export
sniff rates     benzaldehyde                # browse proton-transfer rate constants (k)
```

### App mode — `sniff app`

`sniff app` is the same review UI as a program you live in rather than a command you run
once per file: one server that stays up, files opened from its own start screen, and
**Export** where the CLI has Done. Packaged for desktop use it is called **Sniff** — the
name is in `sniff/brand.py`, and the mark next to it is drawn by
`packaging/make_icons.py`, which also builds the `.icns` and `.ico` the installers carry.

```bash
sniff app                        # start screen: browse for a run
sniff app FILE.h5 --no-browser   # open one file immediately, in a browser tab
sniff app --window               # force the desktop window
sniff app --port 8791            # fixed port (it probes upward if the port is taken)
sniff app --agent URL            # let an agent curate a newly detected config
```

Installed as an app bundle, `sniff app` opens a **desktop window** — its own window,
menus and file dialog rather than a tab in whatever browser you happen to use. From a
source checkout it opens a browser tab unless you ask for `--window`, because there the
terminal is right beside you. The packaged installer includes the desktop window. A checkout can use the optional
`pywebview` dependency with `uv sync --extra desktop`; without it, Sniff logs one line
and opens a browser tab, so nothing is ever lost — the same page, the same localhost
server, the same Export.

Each file's config sits beside it under the same name: `sniff.h5` → `sniff.json`. Every
review edit is saved there on the fly, and closing the page or desktop window flushes the
latest edit before it goes away. A `sniff-analysis-config.json` left by the CLI flow is
found automatically, so a file that has been reviewed before reopens exactly as it was
saved. Sniff also remembers which review was active: closing and reopening the app
resumes that run automatically, while deliberately leaving the review returns the next
launch to the opening screen. Sleeping or shutting down the laptop does not discard the
review. A file that has never been reviewed gets the deterministic pipeline — detected
peaks, detected intervals, honest checklist — written to that path and then loaded, so
the panel starts as a starting point rather than an empty table. **Export** runs the
full-precision analysis to `<name>.csv` beside the file and leaves everything open; if a
table that is not a sniff summary already sits at that name — a Viewer export, say — it
writes `<name>-sniff.csv` instead of overwriting it. Opening another file closes the
current one, since a large run holds its data in memory.

The opening screen is deliberately uncluttered: use **Browse this computer…** to
choose a run in the native file dialog. Paths cannot be typed or pasted into the start
screen. If a file is already open, it appears once in its own panel with **Open the
review**. The app still remembers opened files in its local recent file store for API
clients and diagnostics, but does not display that history in the opening screen. The
dialog belongs to the computer rather than the tab, so it can appear behind the browser
window. If native file browsing is unavailable, the start screen reports that clearly;
there is no path-field fallback. Files are never uploaded anywhere; the page talks only
to `127.0.0.1`.
Opening a file happens behind a full-screen sheet rather than as a line of text: the
file's name, what the app is doing, a bar that tracks the cycles it has read, a rough
ETA and **Cancel**. The bar is driven by the work itself — on the 2 GB fixture reading
the run is about 89 % of an open, so it gets about 89 % of the bar — and a cancel stops
the analysis at the next block, closes the file and puts you back on the start screen
with nothing written and nothing broken, ready to open the same file again. Small,
non-blocking peak, ion and breath details make the wait feel alive without hiding the
status information. Completion settles at 100% and hands smoothly into the review;
reduced-motion preferences skip the animation. Back does not return you to a sheet for
a file that is already open.

### Packaging the app

`sniff app` freezes into something a reviewer can run with no Python installed:

```bash
uv sync --extra desktop
# Remove stale one-dir output: PyInstaller cannot repair an old executable/data collision.
uv run python -c "import shutil; [shutil.rmtree(path, ignore_errors=True) for path in ('dist', 'build')]"
uv run --with pyinstaller pyinstaller --noconfirm packaging/sniff-app.spec
uv run python scripts/smoke_frozen.py "dist/Sniff.app/Contents/MacOS/sniff"  # macOS
uv run python scripts/smoke_frozen.py dist/sniff/sniff.exe                    # Windows
```

On Windows that leaves `dist/sniff/sniff.exe`; on macOS it leaves `dist/Sniff.app`.
The visible executable stays at that path while PyInstaller keeps package data,
Python modules and shared libraries separate from it: under `_internal/` on Windows,
and under the app bundle's `Contents/Resources/` on macOS. Either way the bundle is
the same ~40 MB of interpreter, NumPy, HDF5 and bundled reference data.
`scripts/smoke_frozen.py` starts it against a tiny synthetic file and
checks it really serves the review page, because `sniff --help` would pass on a bundle
that cannot do anything else.

Then wrap it the way each system expects — a `.pkg` built by `pkgbuild` and
`productbuild` on macOS, an `.msi` built by WiX on Windows:

```bash
version=$(uv run python packaging/make_pkg.py --print-version)                  # macOS
uv run python packaging/make_pkg.py --out build/pkg --arch "$(uname -m)"
pkgbuild --component "dist/Sniff.app" --install-location /Applications \
  --identifier dk.samsmart.sniff --version "$version" build/pkg/sniff-component.pkg
productbuild --distribution build/pkg/distribution.xml --package-path build/pkg dist/sniff.pkg

uv run python packaging/make_msi.py dist/sniff build/msi/sniff-app.wxs        # Windows
candle.exe -arch x64 -out build/msi/sniff-app.wixobj build/msi/sniff-app.wxs
light.exe  -o dist/sniff.msi build/msi/sniff-app.wixobj
```

`packaging/README.md` is the guide to all of it — both command pairs in full, why
PyInstaller cannot cross-compile, why WiX v3.14 is the pinned toolchain, what each
installer contains and where it lands (`/Applications/Sniff.app`,
`C:\Program Files\Sniff`), how to check an artifact, and what a signed build
would still need.

Both installers are generated from what the build produced rather than from a
hand-maintained file list. `packaging/make_msi.py` writes the WiX source with one
component per directory and one file id and GUID derived from each path, so an upgrade
replaces the files it should and removes the ones it should; it targets WiX v3
deliberately, because v6 and later refuse to build until the Open Source Maintenance Fee
EULA is accepted, which asks a fee of anyone shipping a product for money.
`packaging/make_pkg.py` writes the distribution XML for `productbuild` — the title, the
version, the minimum system and the architecture, with no paths and no timestamps in it,
so the same checkout gives the same file twice.

Each operating system needs its own build: PyInstaller cannot cross-compile, and neither
can an installer tool. The `package` workflow does all of it on native macOS and Windows
runners and uploads the two installers; pushing a `v*` tag also publishes them as a
GitHub Release. To build for Windows without any of that, run the commands above on a
Windows machine.

Neither installer is signed, so the first run warns. To install the macOS package, either
double-click it or install it from a terminal:

```bash
sudo installer -pkg sniff-review-macos-arm64.pkg -target /   # → /Applications/Sniff.app
```

An unsigned `.pkg` still trips Gatekeeper when you double-click it, and `installer` is not
routed through Gatekeeper at all, so the terminal command works whatever the download
flag says; clearing the flag off the downloaded package first (`xattr -dr
com.apple.quarantine sniff-review-macos-arm64.pkg`) makes double-clicking work too. To
remove it, delete the bundle and forget the receipt:

```bash
sudo rm -rf "/Applications/Sniff.app"
sudo pkgutil --forget dk.samsmart.sniff
```

Windows SmartScreen says "More info" → "Run anyway". Both warnings go away once the
bundle is signed and notarised with a Developer ID or code-signing certificate, which the
spec and both installer sources are ready for without other changes.

A double-clicked app opens its review page in the browser and prints nothing, since
there is no terminal; its URL and any errors go to `~/.sniff/log.txt`. A window stops
the server when you close it; in a browser tab, where there is no window to close, the
*Stop the app* link in the footer does it — from a terminal, Ctrl-C does the same.

`viz` opens a browser review app for an existing peak list + ranges so an expert can
visually check and tweak peaks / segments / calibration. K, molar volume, kinetic and
humidity controls, R windowing, and peak/interval edits recompute from embedded preview
data; primary m/z, R_phys, and whole-run window mode require raw HDF5 re-extraction and
are prominently marked stale until Done. **It is the default final step** for analysing
a file: the agent curates a config from `peaks`/`segments` first, then opens `viz` on that
best solution — ideally nothing needs changing and *Done* is a one-click confirmation.
By default it serves a localhost app that live-saves every edit into the `--config` file
and, when the expert clicks *Done*, runs the full-precision analysis and writes the
`--out` CSV; `--html review.html` writes a portable offline file instead (edits exported
via a Download button).
A first-time user gets an automatic guided tour of the interface (skippable,
remembered in the browser). The agent can also add a `"checklist"` array to the config —
short points for the reviewer to confirm (an ambiguous segment, a relabelled background
channel, a calibration caveat) — which the app shows as a tickable list, so review
notes live in the app instead of a wall of chat text. `viz` does not detect
peaks/segments. Skip it and run `analyze` directly only for a headless/no-browser run or
a hand-off file. There is no one-shot command; the delivered CSV always comes from
`analyze`, never the browser.

A served `viz` review waits indefinitely for *Done* by default. After a laptop sleep/wake
cycle, the localhost server remains available once the laptop is awake; stop it with
Ctrl-C. Pass `--timeout SECONDS` only when an opt-in upper bound is wanted; it is not
relevant to standalone `--html` output.

`viz` offers a configurable x-axis unit for the browser review. The default is cycle
display. Accepted values are exactly `cycle`, `relative`, and `absolute`. Relative time
uses elapsed acquisition time from valid `SPECdata/PCTime` values, falling back to the
spectrum duration when it is finite and positive, or one second otherwise. Absolute
time uses validated `PCTime` values plus the file's root `UTC_Offset` (when available)
for lab-PC local time, and is unavailable when they are missing, invalid, or outside the
four-digit ISO year range (0000–9999).

Set `viz.x_axis_unit` in the config, or use the matching `--x-axis-unit` option on
`sniff viz`:

```json
{
  "viz": { "x_axis_unit": "relative" }
}
```

Precedence is CLI override > config value > cycle default. The selector is shown only
on the **Signal over time** tab and updates that plot and the Intervals card; it sits on
the right of the header, and nothing else sits there, so the Raw/Conc selector keeps the
same slot either way. Saved ranges and CSV `Cycle`
rows remain integer, 1-based, inclusive cycle boundaries. The Intervals card stays in
chronological order as intervals are added, dragged and undone, and each row's range
updates while you drag an edge.

The **Peaks** sidebar can also be ordered by descending abundance or alphabetically by
label. Abundance is the mean per-cycle integrated Raw signal (the peak integral), with
m/z used to break ties. The compact list shows only the active sort field; the details
view shows both m/z and abundance. The m/z and abundance values follow the interval
selected above the peak list; isolated peaks use that interval's apex and clustered
peaks retain their fixed model centres. The choice is saved as
`viz.peak_order` and does not change the peak order in the analysis config or CSV.
Arrow keys move the selection down and up the order you chose, not the stored one.

The Raw / Corrected / Conc / µg selector works on both tabs: it rescales the mass
spectrum and the sidebar abundance values. In Conc and µg the sidebar figure is the mean
of that compound's own converted trace over the cycles being shown — the same number the
CSV reports as `Average` for that interval. The per-compound humidity correction is
applied to the traces and the sidebar values; the shared spectrum axis cannot carry it
and says so. A value that cannot be converted (no correction curve, no **K**, or no
primary signal) is shown as Raw and says why in its tooltip.

Each peak's box is a **per-sample** selection, and it follows the **average over**
choice: pick one sample and the box is that sample's own tick; pick the whole run and
it is the aggregate — ticked = in every sample interval, empty = in none, a dash = in
some samples only. Clicking the aggregate box only ever flicks it: a dash becomes a
proper tick, the next click clears it, the next ticks it again. The Details view adds
one small box per sample interval to every row, so a compound's sample list is visible
in the sidebar itself; the header toggle works over whichever scope is showing. A peak
in every sample needs no extra config; a partial one records `samples`, the interval
labels it belongs to:

```json
{
  "peaks": [
    { "mz": 78.0469, "label": "benzene", "samples": ["sample_01"] }
  ]
}
```

A compound selected for at least one sample is still part of the summary output exactly
as before; the per-sample distinction is stored so per-sample output can build on it.

Above the compound list the sidebar carries the **interval** the tick boxes and the Mass
spectrum both speak about, with a **sample / background** switch beside it. It is the
same choice as the class column in the Intervals card, but reachable whichever tab is
open. Because the analysis blanks against `background_*` by name, switching class also
renames the interval (`sample_07` → `background_05`) and moves the recorded `samples`
labels with it; the name is the part that is saved, so a switch that renamed nothing
would be lost on save. Switching the class back restores the previous name and the
previous per-compound membership.

The faint curve behind the Signal over time trace is the **composite VOC signal**: the
mean of the strong m/z 40–200 traces, each divided by its own median so no single ion
dominates. It sits near 1 while the instrument sees background and rises over a sample,
and `sniff segments` places the intervals from it. It is a detector for *when* signal is
present, not a concentration, and it is drawn against its own maximum rather than the
axis it sits on. Its legend entry says so and doubles as a switch, remembered as
`viz.show_disc`.

A plateau can also be split by a wobble rather than by a real change of phase, so two
adjacent plateaus of the same class are joined when the unclassified gap between them
never left that phase: it must cover under ~60 s of acquisition (never fewer than 30
cycles), and its highest cycle must stay within a factor of 2 of the higher neighbour's
level. A sample gap has to keep its lowest cycle within that factor of the lower
neighbour too — a sample that came back down to the background ended — while a
background has no lower test, because a dropout toward zero is still the same blank.
Those levels are read against the run's own background, so the same physical wobble
merges at 1 s/cycle and at 5 s/cycle alike, and each gap is judged against the plateau
it abuts, so nothing depends on which plateau came first. An opposite-class plateau in
between is always a boundary. `--merge-high-gap N` overrides
the ~60 s cap with a fixed cycle count (`0` never joins high plateaus), and every join
says what it did: per gap in `sniff segments` JSON, and as one line on the Intervals card
of the review app.

An analysis config may include an `analyze` object with `R`, `R_phys`, `K`,
`molar_volume`, `primary_mz`, `kinetic`, `k_anchor`, `humidity_correct`, `humidity_p`,
`humidity_ref`, and `whole_run_windows`. Omitted CLI options do not replace these
curated values: precedence is **CLI override > `analyze` config > legacy default**.
The same resolver is used by `analyze`, browser initial state, live-save, and Done.
Unknown top-level and nested config fields survive browser round trips. New detected and
saved configs carry `mass_axis_domain: "corrected"` and
`mass_axis_version: 1`. When an older unmarked config is opened, Sniff first proves
both internal anchors, then migrates every saved absolute mass and mass width from the
file axis exactly once and persists the marker; cycle ranges and unknown fields are
unchanged.

Before peak detection or extraction, Sniff requires an internal two-point mass-axis
check. It detects the operational water calibrant at 37.033 and protonated
iodobenzene at 204.951 in the sanitised run-average spectrum using the file's own
HDF5 timebin calibration. Sub-bin centres must be prominent, high-S/N, unambiguous,
within a conservative proximity window and persistent in at least five of eight
deterministic raw-cycle blocks. Raw-cycle persistence is mandatory: files without a
valid `SPECdata/Intensities` block set cannot be calibrated or analysed. The accepted
correction is separate from the file's `a,b` coefficients:
`m_corrected = scale*m_file + offset`, so it
translates and scales the whole axis rather than moving only selected targets. If
either mandatory anchor is missing, weak, ambiguous or implausible, analysis stops
with a structured calibration error; it never silently falls back to the HDF5 axis.
The 37.033 value is the operational calibration water peak; humidity-sensitive
water-cluster ratios are reported separately and are not calibration evidence.

By default `analyze` integrates each interval with each isolated peak's apex/window
**re-centred on that interval's own spectrum** — peaks drift between intervals (a
compound may be absent in a background), so one whole-run window sits off-peak
elsewhere. Clustered peaks are Gaussian/deconvolved fitted components at fixed model
centres, so their centre is not a measured apex and may not be a visible local maximum in
every interval. The delivered CSV is unchanged in shape (still one row per compound ×
interval); only each row's numbers reflect its interval's real peak. Set
`whole_run_windows: true` or pass `--no-per-interval` for one whole-run window per
compound. Manual peak windows remain manual. The Methods card reports these effective
values, their provenance, and whether the transmission curve and concentration are
available. Browser numbers are preview values: R windowing and other embedded-data
controls update live, while primary m/z, R_phys, and whole-run window mode are marked
stale and are applied only by the authoritative **Done**/`analyze` re-extraction.

Add `--pretty` to any command for indented JSON. `analyze` peak/segment sources:
`--config file.json` (curated, preferred), or `--auto-peaks`/`--auto-segments`
(zero-curation — auto-labels confident IDs, drops noise artifacts, consolidates
backgrounds, and joins samples split by a wobble). `--K` / `--molar-volume`
override the file-derived calibration to match a specific Viewer project. `--kinetic`
applies per-compound rate-constant (k) sensitivities from the bundled 218-compound
PTR Library table for physically resolved absolute concentrations. Low-proton-affinity
compounds (HCN, formaldehyde, formic acid…) are
auto-flagged: `analyze` always reports a humidity diagnostic for them, and
`--humidity-correct` (with a calibrated `--humidity-p`) normalises the humidity swing.

## Reference data attribution

The bundled `ptrlibrary.csv` is the PTR Library compiled by Demetrios Pagonis,
Kanako Sekimoto, and Joost de Gouw. It is redistributed with permission, upstream
attribution, publication references, and the source citations in individual records.
The MIT licence for this package does not relicense the CSV or its cited data. The
derived `rate_constants.json` is generated from that CSV by the bundled generator and
carries the same attribution.

## How it works

The baseline timebin calibration, transmission, concentration constant K and molar volume are read from the `.h5`. A separate, conservative water/iodobenzene affine
correction aligns the mass domain only after both internal references and their mandatory
raw-cycle persistence pass; otherwise analysis stops with a structured calibration
error. Isolated peaks use an apex-centred resolution window;
overlapping peaks are separated by linear Gaussian deconvolution. Time segments are
found by log-space plateau detection on a composite VOC signal. Compound identification enumerates candidate molecular formulas offline (no
external database) and ranks them by exact-mass error, the measured vs predicted
¹³C(M+1)/heteroatom(M+2, e.g. S/Cl) isotope pattern, and plausibility (integer DBE,
nitrogen rule, element ratios) — so near-isobars are told apart by composition, not
"nearest mass". Candidate rankings cannot determine structural isomers; names and
isomer labels come from the bundled PTR Library mapping. Proton-transfer rate constants
come from the bundled 218-compound table when the formula is known. The entries are
compiled from the **PTR Library** (Pagonis, Sekimoto & de Gouw, *J. Am. Soc. Mass
Spectrom.* 2019, doi.org/10.1007/s13361-019-02209-3; tinyurl.com/PTRLibrary), with
measured k where available (else Su-Chesnavich capture-theory k, flagged
`k_estimated`), plus proton affinity, isomer names, and fragmentation flags. Use
`sniff rates` to browse the bundled values. The installed package also includes the
ionisation, compound-assignment, and HCN/humidity reference documents.

## Accuracy

Median error vs PTR-MS Viewer on two reference exports — breath (396 points, default K):
Raw 2.4 %, Corrected 5.0 %, Conc 3.1 %, Conc[µg] 3.2 %; bitter-almonds (16 points,
calibrated K): Raw 0.7 %, Corrected 3.1 %, Conc 2.6 %, Conc[µg] 2.5 %.

Concentration carries one calibration constant K not uniquely fixed by the raw file (a
Viewer project uses its own sensitivity). Default K is the file's own acquisition
calibration; run `calibrate FILE.h5 reference.csv` and pass `--K` to match a specific
Viewer project exactly. Raw and Corrected are file-derived and robust.
