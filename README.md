# ptr-ms-analysis

Open-source reprocessor for IONICON IoniTOF PTR-MS / PTR-TOF `.h5` files — a
replacement for the proprietary PTR-MS Viewer. Extracts product-ion peaks from the
raw mass spectra, transmission-corrects them, converts to concentration (ppb and
µg/m³), and summarises per time segment.

**Agent-driven by design.** The CLI does the deterministic physics and detects
candidate peaks (with compound assignments + artifact flags) and time segments; an
agent assigns chemistry and curates segments. Humans talk to the agent, not to this
CLI. The commands below describe the complete package interface.

## Install / run

It's a proper package that ships its own dependencies (h5py + numpy) and reference
data. Install it **once** and `ptr` is on PATH everywhere. Recommended via `pipx`
(isolated environment for the CLI and its dependencies):

```bash
pipx install ptr-ms-analysis
ptr inspect FILE.h5
```

**If `pipx` isn't installed yet**, install it first, then re-run the command above:

```bash
brew install pipx && pipx ensurepath                              # macOS (Homebrew)
python3 -m pip install --user pipx && python3 -m pipx ensurepath  # Linux / macOS (no brew)
py -m pip install --user pipx; py -m pipx ensurepath              # Windows (PowerShell)
```

`pipx ensurepath` puts pipx's bin dir on PATH — open a new shell afterwards. Alternatives
that skip pipx entirely are `uv tool install ptr-ms-analysis` and
`python3 -m pip install ptr-ms-analysis` in a virtual environment. Works identically on
macOS, Linux, and Windows (pipx makes a real `ptr.exe`). Requires Python ≥ 3.9.

## Commands (all discovery output is JSON)

```bash
ptr inspect  FILE.h5                       # metadata, calibration, concentration-K, Vm
ptr peaks    FILE.h5                       # peaks + a ready-to-use suggested_label + top formula (--full for all candidates)
ptr segments FILE.h5                       # stable plateaus (high=sample / low=bg)
# agent curates peaks + ranges into cfg.json, then:
ptr viz      FILE.h5 --config cfg.json --out results.csv   # serve review; 'Done' -> writes CSV
ptr viz      FILE.h5 --config cfg.json --html review.html   # portable standalone HTML instead
ptr analyze  FILE.h5 \                     # no review: curated config -> Viewer-style CSV
    --config cfg.json --include-cycle-rows --out results.csv
ptr analyze  FILE.h5 --auto-peaks --auto-segments --out results.csv   # zero-curation fallback (auto-labels, drops noise)
ptr calibrate FILE.h5 viewer.csv          # fit concentration constant K -> pass via --K
ptr compare   results.csv viewer.csv --per-mass   # accuracy vs a Viewer export
ptr rates     benzaldehyde                # browse proton-transfer rate constants (k)
```

### App mode — `ptr app`

`ptr app` is the same review UI as a program you live in rather than a command you run
once per file: one server that stays up, files opened from its own start screen, and
**Export** where the CLI has Done.

```bash
ptr app                        # start screen: recent files, or type a path
ptr app FILE.h5 --no-browser   # open one file immediately
ptr app --port 8791            # fixed port (it probes upward if the port is taken)
ptr app --agent URL            # let an agent curate a newly detected config
```

Each file's config sits beside it under the same name: `ptr.h5` → `ptr.json`. A
`ptr-analysis-config.json` left by the CLI flow is found automatically, so a file that
has been reviewed before reopens exactly as it was saved. A file that has never been
reviewed gets the deterministic pipeline — detected peaks, detected intervals, honest
checklist — written to that path and then loaded, so the panel starts as a starting
point rather than an empty table. **Export** runs the full-precision analysis to
`<name>.csv` beside the file and leaves everything open; if a table that is not a ptr
summary already sits at that name — a Viewer export, say — it writes `<name>-ptr.csv`
instead of overwriting it. Opening another file closes the current one, since a large
run holds its data in memory.

### Packaging the app

`ptr app` freezes into something a reviewer can run with no Python installed:

```bash
pip install . pyinstaller
pyinstaller --noconfirm packaging/ptr-app.spec
python scripts/smoke_frozen.py dist/ptr/ptr        # proves the bundle serves a review
```

On Windows that leaves `dist/ptr/ptr.exe`; on macOS it leaves `dist/PTR-MS Review.app`.
Either way the bundle is the same ~40 MB of interpreter, NumPy, HDF5 and bundled
reference data. `scripts/smoke_frozen.py` starts it against a tiny synthetic file and
checks it really serves the review page, because `ptr --help` would pass on a bundle
that cannot do anything else.

Then wrap it the way each system expects:

```bash
wix build build/msi/ptr-app.wxs -arch x64 -o dist/ptr.msi              # Windows
hdiutil create -volname "PTR-MS Review" -srcfolder stage -format UDZO dist/ptr.dmg
```

`packaging/make_msi.py` writes the WiX source from the built folder — one component per
directory with a GUID derived from the path, so an upgrade replaces the files it should
and removes the ones it should. The folder layout, not a hand-maintained file list, is
what the installer installs.

Each operating system needs its own build: PyInstaller cannot cross-compile, and neither
can an installer tool. The `package` workflow does all of it on native macOS and Windows
runners and uploads the two installers; pushing a `v*` tag also publishes them as a
GitHub Release. To build for Windows without any of that, run the commands above on a
Windows machine.

Neither installer is signed, so the first run warns. On macOS, drag the app to
Applications and start it once with right-click (or Control-click) → Open; the
alternative is dropping the download flag directly:

```bash
xattr -dr com.apple.quarantine "PTR-MS Review.app"
```

Windows SmartScreen says "More info" → "Run anyway". Both go away once the bundle is
signed and notarised with a Developer ID or code-signing certificate, which the spec and
the WiX source are ready for without other changes.

A double-clicked app opens its review page in the browser and prints nothing, since
there is no terminal; its URL and any errors go to `~/.ptr-ms/log.txt`. "Stop the app"
at the bottom of the start screen shuts the server down — from a terminal, Ctrl-C does
the same.

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
`ptr viz`:

```json
{
  "viz": { "x_axis_unit": "relative" }
}
```

Precedence is CLI override > config value > cycle default. The selector is shown only
on the **Signal over time** tab and updates that plot and the Intervals card; it sits
on the right of the header, where **average over** sits on the Mass spectrum tab, so
the Raw/Conc selector keeps the same slot either way. Saved ranges and CSV `Cycle`
rows remain integer, 1-based, inclusive cycle boundaries. The Intervals card stays in
chronological order as intervals are added, dragged and undone, and each row's range
updates while you drag an edge.

The **Peaks** sidebar can also be ordered by descending abundance or alphabetically by
label. Abundance is the mean per-cycle integrated Raw signal (the peak integral), with
m/z used to break ties. The compact list shows only the active sort field; the details
view shows both m/z and abundance. The m/z and abundance values follow the Mass
spectrum tab's selected average-over interval; isolated peaks use that interval's apex
and clustered peaks retain their fixed model centres. The choice is saved as
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
and `ptr segments` places the intervals from it. It is a detector for *when* signal is
present, not a concentration, and it is drawn against its own maximum rather than the
axis it sits on. Its legend entry says so and doubles as a switch, remembered as
`viz.show_disc`.

An analysis config may include an `analyze` object with `R`, `R_phys`, `K`,
`molar_volume`, `primary_mz`, `kinetic`, `k_anchor`, `humidity_correct`, `humidity_p`,
`humidity_ref`, and `whole_run_windows`. Omitted CLI options do not replace these
curated values: precedence is **CLI override > `analyze` config > legacy default**.
The same resolver is used by `analyze`, browser initial state, live-save, and Done.
Unknown top-level and nested config fields survive browser round trips.

By default `analyze` integrates each interval with each isolated peak's apex/window
**re-centred on that interval's own spectrum** — peaks drift between intervals (mass-cal
drift; a compound may be absent in a background), so one whole-run window sits off-peak
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
backgrounds). `--K` / `--molar-volume`
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

Everything instrument-specific (mass calibration, transmission, concentration constant
K, molar volume from drift temperature) is read from the `.h5`. Isolated peaks use an
apex-centred resolution window; overlapping peaks are separated by linear Gaussian
deconvolution. Time segments are found by log-space plateau detection on a composite VOC
signal. Compound identification enumerates candidate molecular formulas offline (no
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
`ptr rates` to browse the bundled values. The installed package also includes the
ionisation, compound-assignment, and HCN/humidity reference documents.

## Accuracy

Median error vs PTR-MS Viewer on two reference exports — breath (396 points, default K):
Raw 2.4 %, Corrected 5.0 %, Conc 3.1 %, Conc[µg] 3.2 %; bitter-almonds (16 points,
calibrated K): Raw 0.7 %, Corrected 3.1 %, Conc 2.6 %, Conc[µg] 2.5 %.

Concentration carries one calibration constant K not uniquely fixed by the raw file (a
Viewer project uses its own sensitivity). Default K is the file's own acquisition
calibration; run `calibrate FILE.h5 reference.csv` and pass `--K` to match a specific
Viewer project exactly. Raw and Corrected are file-derived and robust.
