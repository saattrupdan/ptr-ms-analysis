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

`viz` offers a configurable x-axis unit for the browser review. The default is cycle
display. Accepted values are exactly `cycle`, `relative`, and `absolute`. Relative time
uses elapsed acquisition time from valid `SPECdata/PCTime` values, falling back to the
spectrum duration when it is finite and positive, or one second otherwise. Absolute
time uses validated `PCTime` values and is unavailable when they are missing, invalid,
or outside the four-digit ISO UTC year range (0000–9999).

Set `viz.x_axis_unit` in the config, or use the matching `--x-axis-unit` option on
`ptr viz`:

```json
{
  "viz": { "x_axis_unit": "relative" }
}
```

Precedence is CLI override > config value > cycle default. The selector is shown only
on the **Signal over time** tab and updates that plot and the Intervals card. Saved
ranges and CSV `Cycle` rows remain integer, 1-based, inclusive cycle boundaries.

The **Peaks** sidebar can also be ordered by descending abundance. This is the mean
per-cycle integrated Raw signal (the peak integral), with m/z used to break ties. The
compact list shows only the active sort field; the details view shows both m/z and
abundance. The m/z and abundance values follow the Mass spectrum tab's selected
average-over interval; isolated peaks use that interval's apex and clustered peaks
retain their fixed model centres. The choice is saved as `viz.peak_order` and does not
change the peak order in the analysis config or CSV.

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
