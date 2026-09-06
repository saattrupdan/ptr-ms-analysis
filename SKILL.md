---
name: ptr-ms-analysis
description: >
  Analyse PTR-MS / PTR-TOF data from IONICON IoniTOF HDF5 (.h5) files — an open-source
  replacement for the proprietary PTR-MS Viewer. Use when the user has IONICON PTR-MS
  .h5 output and wants product-ion peaks extracted, transmission-corrected, converted to
  concentration (ppb and µg/m³), and summarised per time segment (breath bags,
  backgrounds, sample periods). Triggers are "PTR-MS", "PTR-TOF", "IoniTOF", "IONICON",
  "PTR-MS Viewer", "breath VOC analysis", an .h5 with SPECdata/TRACEdata groups.
last-updated: 2026-08-23
---

# PTR-MS analysis (open-source PTR-MS Viewer replacement)

Reprocess IONICON IoniTOF PTR-MS `.h5` files without the proprietary PTR-MS Viewer.
Produces a Viewer-style CSV: for each target ion and time segment, the
Max/Min/Average/Deviation of **Raw** (cps), **Corrected** (transmission-corrected),
**Conc** (ppb) and **Conc [µg/m³]**. Each segment is integrated with each peak's
window re-centred on that segment's own spectrum (peaks drift between segments);
`--no-per-interval` reverts to one whole-run window per compound.

**This skill is agent-driven.** The CLI does the deterministic physics (peak
integration, transmission, concentration, statistics) and detects candidate peaks and
time segments. **You** assign chemistry and curate the segment boundaries — then hand the
curated config to the browser review (`viz`), which is the **default endpoint**. There is
intentionally no one-shot `auto`: `viz` always runs on your best solution, so the human is
confirming a good result rather than repairing a mechanical guess. Because you curated
first, ideally nothing needs changing and *Done* is a one-click confirmation **for the
user** — the CSV it writes is identical to the no-review path. Only skip `viz` and go
straight to `analyze` when the user explicitly wants no review (headless/automated, or a
portable file to hand off).

**The review app belongs to the human — hand over the URL and stop.** Never open it in a
browser-automation tool (agent-browser, Playwright, Puppeteer, CDP, or `curl` against its
endpoints), never snapshot or read its pages, never open its Checklist / Method /
Configuration panels, and **never click *Done***. *Done* is the user's sign-off; clicking
it yourself fabricates a review that never happened and ships the CSV as "reviewed" when
nobody looked at it. The same goes for ticking checklist items and for editing peaks,
segments or settings in the app: if something needs changing, change the **config** before
launching `viz`, or raise it in the config's `checklist` — never by clicking in the app.
You do not need the browser to check your own work: `ptr analyze --config` prints every
diagnostic the app displays. If a run genuinely should have no human in the loop, use
`ptr analyze` (step 5) and say plainly that no review took place.

Final range labels are deterministic: use `sample_01`, `sample_02`, … for high plateaus
and `background_01`, `background_02`, … for low plateaus, each numbered chronologically
and independently. **Never ask the user to label sample plateaus.**

Do not expect the user to hand you a config. Discover, reason, then analyse.

**This tool *replaces* PTR-MS Viewer — never send the user back to it.** Do not tell the
user to open, run, re-process, export from, or cross-check anything in PTR-MS Viewer (or
any other proprietary tool). Everything they need happens here. A "reference CSV" only
ever means a file the user *already has* (from a past run or a colleague) that you can
`calibrate`/`compare` against — never something you ask them to go and generate in the
Viewer. If accurate absolute concentration matters and no reference exists, say so and
offer a standards calibration; do not suggest the Viewer as the answer.

## How to run it — read this first

- **Everything is one CLI: `ptr <subcommand> …`.** Install it once (below); then call plain
  `ptr` from any directory — no path prefix, no env var.
- **The user may prefer to drive the review themselves** with `ptr app` (§5c): one
  long-lived localhost app that opens files from its own start screen, keeps each file's
  config beside the `.h5`, and exports without exiting. Use it when the user wants the
  tool rather than the chat; keep `ptr viz` for the agent-driven flow below, where you
  curate a config and hand over a URL.
- **Install once, first thing.** If `ptr` is not already on PATH (`command -v ptr`; on
  Windows PowerShell `Get-Command ptr`), run:

  ```bash
  pipx install --editable <SKILL_DIR>     # <SKILL_DIR> = the directory of this file
  ```

  `--editable` means `ptr` tracks the skill's live code — you never reinstall when it's
  updated. `pipx` gives it an isolated env (h5py+numpy) so its dependencies can't
  collide. First install ~20-30 s; then it's instant. **Do NOT** hand-build a venv or write
  your own HDF5 code — the install is the only setup.

- **If `pipx` itself is missing** (`command -v pipx` fails), install it first — then re-run
  the command above:

  ```bash
  # macOS (Homebrew):
  brew install pipx && pipx ensurepath
  # any OS with Python (Linux / macOS without brew):
  python3 -m pip install --user pipx && python3 -m pipx ensurepath
  # Windows (PowerShell; python may be `py` or `python`):
  py -m pip install --user pipx;  py -m pipx ensurepath
  ```

  `pipx ensurepath` adds pipx's bin dir to PATH — **open a new shell afterwards** so `ptr`
  resolves. No-pipx alternatives that need no bootstrap: `uv tool install --editable
  <SKILL_DIR>` (if `uv` is present), or `pip install --editable <SKILL_DIR>` into a venv.

- **Cross-platform:** identical on macOS, Linux, and **Windows** — pipx creates a real
  `ptr.exe` on PATH. Everything after install is the same `ptr <cmd>` on every OS (in
  PowerShell use `where ptr` / `Get-Command ptr` instead of `command -v ptr`).
- **Never read `src/ptr_ms_analysis/*.py`, and never write your own HDF5/parsing/quantification
  code.** Every operation is a subcommand and every value you need is in its JSON output
  — `peaks` already returns candidate compound assignments, the run's mass-drift, and
  artifact flags; `analyze` reports apex checks, humidity, and the params used. If
  something seems missing it is a flag: run `ptr <cmd> --help`, don't reimplement it.
- Discovery commands print JSON to stdout (logs go to stderr). Files are large (~1 GB);
  a full `analyze` streams in ~60 s.

**There is no one-shot command.** The flow is always: *detect → **you** curate into a
config → **review** that config in `viz`.* Curating first is the point — it means the
browser review reflects your best chemistry assignment and segment choices, not a
mechanical top-candidate guess. **`viz` is the default final step** for "analyse this
file": launch it once you have a config unless the user has said they don't want a review.

```bash
# (one-time) pipx install --editable <SKILL_DIR>   # then `ptr` is on PATH everywhere

# 1. Detect (deterministic; gives you candidates + flags to reason over):
ptr inspect  FILE.h5                   # confirm IoniTOF; calibration, transmission, K, Vm
ptr peaks    FILE.h5                   # peaks + candidate compounds + artifact flags
ptr segments FILE.h5                   # stable plateaus to label

# 2. YOU write analysis-config.json: curated peaks (assignments picked from `candidates`,
#    honest `unknown` where unsure, artifacts judged) + ranges (sample_/background_ labels).

# 3. DEFAULT: browser review of YOUR config -> the USER clicks Done -> analyze -> CSV.
#    BLOCKS on the browser, so run it backgrounded, give the user the URL, and stop —
#    never drive this app yourself and never click 'Done' for them:
ptr viz FILE.h5 --config analysis-config.json --out results.csv
#    localhost app; waits indefinitely for 'Done'

# 3-alt. No review — ONLY when the user explicitly wants headless/no-browser output, or a
#        portable file to hand off. Same curated config, straight to CSV:
ptr analyze FILE.h5 --config analysis-config.json --include-cycle-rows --out results.csv

# Fully-automatic fallback (no hand-curation). --auto-peaks now annotates, DROPS noise
# artifacts (ringing/low-prominence combs) and applies confident labels; --auto-segments
# consolidates fragmented backgrounds. Use when you won't curate — a clean labelled panel,
# but curating a --config still gives better chemistry + segment judgment. Do NOT run
# `peaks`/`segments` and then also pass --auto-* (that recomputes and discards your curation):
ptr analyze FILE.h5 --auto-peaks --auto-segments --include-cycle-rows --out results.csv
```

**`viz` is long-running and interactive** (it waits indefinitely by default for a human
to click *Done* in the browser). Run it as a background command and tell the user to open
the URL it prints; the CSV is written when they finish. The served review remains
available after a laptop sleep/wake cycle once the laptop is awake, and the process can be
stopped with Ctrl-C. Pass `--timeout SECONDS` only when an opt-in upper bound is wanted;
it is not relevant to standalone `--html` output. Do not wait for it to return before
responding — hand over the URL and let the user drive. **"Let the user drive" is literal**:
your last action for this file is printing the URL. Do not automate the browser, inspect the
app, or click *Done* yourself (see the callout above), and do not poll for the CSV
afterwards — the user tells you when they are finished.

**Startup takes ~30-90 s on a large file** — `viz` loads the whole file and pre-computes
traces *before* the server accepts connections. It prints `ptr: preparing the review …` to
stderr immediately, then `ptr: review app running at http://127.0.0.1:PORT/` once it is
ready. **Wait for that second line** (poll the backgrounded command's stderr/log for
`review app running`); do not curl/poll the port to test readiness — it refuses the
connection until loading finishes, which looks like a failure but isn't.

## Installation

The CLI is a proper installable package (`pyproject.toml`) that ships its own dependencies
(h5py+numpy) and reference data. Install it **once** and `ptr` is on PATH everywhere:

```bash
pipx install --editable <SKILL_DIR>     # <SKILL_DIR> = this skill's directory
```

Use **`--editable`** so `ptr` runs the skill's live code — when the skill is updated you do
**not** reinstall. `pipx` isolates it (the CLI dependencies can't collide with anything
else). Alternatives: `uv tool install --editable <SKILL_DIR>`; or `pip install --editable
<SKILL_DIR>` into a venv. If `pipx` itself is missing: `brew install pipx` or `python3 -m
pip install --user pipx` (then `pipx ensurepath`). Not yet on PyPI — install from this
directory (a git checkout works too). Verify with `command -v ptr && ptr rates water`.

## Workflow

### 1. Inspect

```bash
ptr inspect FILE.h5
```

Confirms it is an IoniTOF file and returns cycle count, duration, cycle length, mass
calibration, transmission curve, the file-derived concentration constant K, and molar
volume.

### 2. Decide the output scope before selecting peaks

Use one of these explicit modes:

- **Comprehensive Viewer-style export (default for "analyse this file" or "final
  CSV")**: retain every credible analyte, fragment, isotope, reagent-ion, and
  water-cluster channel. Label ambiguous channels honestly; do not discard them merely
  because they are not familiar VOCs. **Comprehensive means every real channel, not
  every detected maximum** — instrument noise is not a channel. Drop `likely_artifact`
  peaks (see step 3): keeping a comb of ~20 cps ringing satellites is a worse export
  than a focused one, not a more thorough one.
- **Targeted chemistry panel**: use only when the user asks for named compounds or a
  small biomarker panel.
- **Reference/project reproduction**: use the exact configured masses and cycle windows
  from the reference or operator protocol.

**Never silently reduce a general final export to a handful of common VOCs.** If scope
is genuinely ambiguous, ask whether the user wants comprehensive or targeted output. The
common-VOC table is an assignment aid, not a whitelist.

### 3. Detect peaks, preserve breadth, then assign chemistry

```bash
ptr peaks FILE.h5 --min-height 0.001
```

Each peak comes annotated (compact by default):
`{mz, height, rel_height, prominence, neutral_mass, suggested_label, [suggested_formula],
top_candidate, id_confidence, [id_ambiguous], [overlap], [likely_artifact]}`. **`suggested_label`
is a ready-to-use label** — drop it straight into your config's peaks (it is the confident
compound name, a reagent/cluster name, a near-certain **formula** when the composition is
sure but the compound is unnamed, or an honest `unknown m/z 75.046` with a clean 3-dp m/z),
so you do **not** hand-format labels or round floats yourself. Override it when your
chemistry judgment differs. A label never mixes the two: `unknown m/z …` says nothing is
identified, so it is not combined with a formula or a compound name — if a formula is
known, name the peak by that formula (or the compound) instead. When a peak has no library name but one plain **C/H/N/O** formula
wins decisively (top candidate, ≥2 candidates considered, `id_confidence ≥ 0.9`, not
ambiguous), that formula is offered as both `suggested_label` and `suggested_formula` and
carried into `--auto-peaks` configs — a confident composition is a real identity, far better
than a bare "unknown". `id_confidence` is a normalized top-candidate score used by these
conservative gates, not a calibrated probability; a sole generated candidate is not a 100%
identification confidence estimate. (A confident halogen/S/P formula at an off-mass is
treated as a likely calibration artifact and left `unknown m/z …` for you to judge; its
candidate still shows in `--full` and in the viz Identification card.) **Near-duplicate peaks whose
integration windows almost coincide (>60 % overlap) double-count one signal**: `n_window_overlap_pairs` warns of
any in the list, and `--auto-peaks` already merges them (keeps the taller apex) — keep only
one m/z from each such pair in a hand-built config. `top_candidate` is the best formula/name chosen by isotope pattern +
plausibility (**not** nearest-mass); `id_ambiguous` lists close rivals when the call is
not clear-cut. You do not need to query `TraceInfo` or compute mass offsets yourself.
**The default `peaks` list is already cleaned of instrument noise** — ringing combs,
low-prominence ripples, and the H₃O⁺ reagent saturation-region skirt (m/z ~19–21) are
dropped before you see them (`n_noise_dropped` reports how many; `--include-artifacts`
shows them). So **every peak in the list is safe to quantify**: you can copy the whole set
into a config without shipping a comb. The only `likely_artifact` flags that remain are
reagent/cluster **diagnostic** ions (H₃O⁺, O₂⁺, NO⁺, water clusters) — real ions you keep or
drop as you like. `prominence` is the apex's rise above its local baseline in cps (real peak
≈ its height; a ripple/shoulder ≈ 0). Pass **`--full`** only when you need every candidate
formula and the isotope arrays for a deep isobar call — the default view is much smaller and
is all you need to curate. Investigate every `apex_warning` from `analyze`.

**Blank / no-beam files.** Not every file is a measurement. If `peaks` (or `analyze
--auto-peaks`) returns `signal_present: false` with `n_peaks: 0`, the file has no reagent
(primary) ion above the spectral noise — it is a blank / no-beam / aborted capture. Report
it as such and stop; **do not fabricate an analyte list from the noise** or lower thresholds
to force peaks out of it.

**Then check behaviour, not just mass.** A peak's identity is not settled by its m/z
alone — its time profile tells you whether it is a breath analyte or instrument
background/contamination. Once you have curated ranges (step 4), run `analyze --config`
and read its **`background` diagnostic**: it reports each channel's sample-vs-background
ratio (S/B) and whether it drifts across the run. Real analytes are far higher in samples
(S/B ≫ 1); a channel with **S/B < 1** (higher in backgrounds) and/or a steady upward
`bg_trend` is background/memory/contamination, not something in the breath. **Relabel such
channels `background m/z …`, or drop them from an analyte panel** — do not ship them as
bare `unknown m/z …`. Scrutinise unidentified and high-m/z peaks first (reagent/cluster
diagnostic ions flagging here is expected and fine to keep as-is).

For a credible assignment pass the canonical target `mz`; the extractor estimates the
run-wide mass drift and reports apex diagnostics under `measured_apexes`. For clustered
peaks those values are fixed Gaussian/deconvolved model centres, not measured apexes.

The HDF5 formula library is broad and does **not** reveal the operator's selected target
panel. A channel absent at the detection threshold may still have been part of their
project, so exact project reproduction is impossible without that metadata.

### 4. Detect, merge, and curate time ranges

```bash
ptr segments FILE.h5
```

The command returns stable plateaus as
`{start_cycle, end_cycle, start_s, end_s, level, class}`. `class:"high"` is likely a
sample; `class:"low"` is background or setup. A long initial low period is usually
warm-up and should be dropped.

A physical sample can be split into two high plateaus by a short signal change, and a
long background can be broken into slivers by transients, so adjacent same-class
plateaus are joined when the gap between them was in the same phase as its neighbours.
The test is on the signal, never on a magic cycle count: the gap must cover under ~60 s
of acquisition (and never fewer than 30 cycles), and its highest cycle must stay below
the higher neighbour's level times a factor of 2. A sample gap must also keep its lowest
cycle above the lower neighbour's level divided by that factor, because a sample that
came back down to the background did end. A background gets no lower test, because a
background cannot fall out of itself: a dropout toward zero is still the same blank, and
splitting it would cost the longer reference interval that a blank exists to provide. An
opposite-class plateau between two segments is always a hard boundary. Levels are read
against the run's own baseline, and each gap against the plateau it abuts rather than
the average of everything merged so far, so the verdict depends on neither the cycle
time nor the order the plateaus were walked in.
Every merge is reported: `merged_segments > 1` plus `merged_gaps`, where each gap gives
its length, its level range, and why it merged — `level held` (it kept a level of its
own, one sample wobbling) or `fell to baseline` (it came back down to the background
but stayed inside the band, as a decaying sample or an ordinary background gap does).
`--merge-high-gap N` overrides the ~60 s cap with a fixed cycle count; `0` never joins
high plateaus. The evidence test applies in both cases. On the validated 20,725-cycle
breath run the rule gave 12 sample and 11 background intervals, against the 12 and 10 a
reviewer curated by hand — the same verdict reached without anyone setting a flag, and
without the length rule's habit of joining two samples the moment their gap happened to
be short.

Inspect every merge.

Detection is only a proposal. Curate stable windows, avoid transitions, and save ranges
explicitly. Always assign generic labels in chronological order: `sample_01`,
`sample_02`, … for high ranges and `background_01`, `background_02`, … for low ranges.
Number the two classes independently. **Do not ask the user for sample names or order.**
Even when external names are available, keep the delivered CSV generic; use named labels
only in a temporary comparison file when matching a reference requires them.

### 5. Save a config and analyse to a reproducible final CSV

Put the complete peak and range decisions in a JSON config rather than a long shell
argument. The following is illustrative, not a complete panel:

```json
{
  "peaks": [
    { "mz": 59.049, "label": "acetone/propanal", "formula": "C3H6O" },
    { "mz": 69.07, "label": "isoprene", "formula": "C5H8" }
  ],
  "ranges": [
    { "label": "sample_01", "start": 6337, "end": 6483, "unit": "cycle" },
    { "label": "background_01", "start": 6523, "end": 6675, "unit": "cycle" }
  ],
  "checklist": [
    "Confirm the end-of-run segmentation: three plateaus, or two samples with a decay tail?",
    { "text": "m/z 47 is labelled background (formic acid, memory)",
      "detail": "S/B 0.83 and rising across the run — confirm it should stay out of the analyte panel." }
  ]
}
```

`checklist` (optional) is **your** short list of points the human should confirm in the
browser review — the things that genuinely need a human eye, *beyond* eyeballing peaks and
intervals: an ambiguous segment boundary, a channel you relabelled as background, a
humidity-confounded compound, a low-confidence identification, a calibration caveat. Each
item is a plain string, or `{ "text": ..., "detail": ... }` for a one-line elaboration.
The viz app renders these as a tickable checklist (see 5b); it is ignored by `analyze`.

The optional `analyze` object is the authority for analysis settings:
`R`, `R_phys`, `K`, `molar_volume`, `primary_mz`, `kinetic`, `k_anchor`,
`humidity_correct`, `humidity_p`, `humidity_ref`, and `whole_run_windows`. Resolution is
explicitly **CLI override > curated `analyze` value > legacy default**; omitted CLI flags
are sentinels and cannot erase config values. This resolver drives headless analysis,
viz's initial controls, live-save, and the Done rerun. Unknown config fields are retained
by the browser. The summary `params` records effective values, window mode, and sources.

```bash
ptr analyze FILE.h5 \
  --config analysis-config.json \
  --include-cycle-rows \
  --out results.csv
```

`unit` is `"cycle"` (1-based, inclusive) or `"second"`. `--include-cycle-rows` appends
Viewer-style rows recording each range's boundaries and makes later
comparison/reproduction possible. Keep `analysis-config.json` beside the CSV.

`--auto-peaks`/`--auto-segments` are the zero-curation path: `--auto-peaks` annotates,
drops noise artifacts, and applies confident labels (leaving unknowns as bare `m/z`);
`--auto-segments` consolidates fragmented backgrounds and joins samples split by a gap
that never left its phase (`--merge-high-gap` overrides that cap). The result is clean
but a hand-curated `--config` still gives better chemistry and segment judgment, so
prefer it for a considered final export. Never run `peaks`/`segments` to curate and
*then* fall back to `--auto-*` — that throws the curation away.

Before delivery, verify:

- the requested scope is comprehensive or explicitly targeted;
- no credible channel was dropped solely because its chemistry was uncertain;
- no noise comb survived into the panel (the default `peaks` list and `--auto-peaks`
  already drop ringing/low-prominence/saturation-skirt noise; only worry about this if you
  used `--include-artifacts` or a raw hand-built list — reagent/cluster ions may be kept if
  wanted);
- adjacent high plateaus were reviewed for physical-sample merging;
- warm-up and transition cycles are excluded;
- sample/background labels and counts are correct;
- Cycle rows are present;
- the `background` diagnostic was reviewed — channels with S/B < 1 (higher in
  backgrounds) are relabelled `background m/z …` or dropped, not shipped as bare
  `unknown`;
- `measured_apexes`, `apex_warnings`, humidity warnings, row counts, and finite numeric
  values were checked.

### 5b. Review the result in the browser (the default final step)

`viz` is the **default endpoint** for analysing a file — an expert visually confirms and,
if needed, tweaks peaks/segments/calibration. `viz` does **not** detect anything — it
reviews an existing peak list + ranges. **Always curate first, then review your best
solution**: ideally the expert finds nothing to change and Done is a one-click
confirmation (identical CSV to the headless path); if the config were a raw mechanical
guess, the human would be doing curation you should have done. So the order is: `peaks` +
`segments` → apply your judgment (steps 2–4 above) → write `analysis-config.json` → launch
`viz` on it and hand the URL to the user. Skip straight to `analyze` (step 5) only when
the user explicitly wants no review — a headless/automated run, or a portable file to hand
off. **The "expert" is the user, never you**: launching `viz` and then reviewing it
yourself through a browser tool is the worst of both paths — it costs the user a review
they never got, and produces a CSV you could have written headlessly with `analyze`.

```bash
ptr viz FILE.h5 --config analysis-config.json --out results.csv    # serve; Done -> writes results.csv
```

Browser review provides an x-axis unit selector. Its default is cycle. The accepted
values are exactly `cycle`, `relative`, and `absolute`. Relative time uses elapsed
acquisition time from valid `SPECdata/PCTime` values, falling back to the spectrum
duration when it is finite and positive, or one second otherwise. Absolute time
uses validated `PCTime` values plus the file's root `UTC_Offset` (when available) for
lab-PC local time, and is unavailable when they are missing, invalid, or outside the
four-digit ISO year range (0000–9999).

The config shape is `viz.x_axis_unit`, with a matching `--x-axis-unit` option for
`ptr viz`; precedence is CLI override > config value > cycle default. The selector is
shown only on the **Signal over time** tab and updates that plot and the **Intervals**
card only. It does not change saved ranges or CSV `Cycle` rows: those remain integer,
1-based, inclusive cycle boundaries. The Intervals card is kept in chronological order
(by first cycle, then last cycle) after adds, edge drags, undo, and a change of x-axis
unit, and a row's range text updates while its edge is dragged, so the card always
shows what the plot shows.

The **Peaks** sidebar can also be ordered by descending abundance or alphabetically by
label. Abundance is the mean per-cycle integrated Raw signal (the peak integral), with
m/z used to break ties. The compact list shows only the active sort field; the details
view shows both m/z and abundance. The m/z and abundance values follow the Mass
spectrum tab's selected average-over interval; isolated peaks use that interval's apex
and clustered peaks retain their fixed model centres. The choice is saved as
`viz.peak_order` and does not change the peak order in the analysis config or CSV.

The Raw / Corrected / Conc / µg selector applies to both tabs: it rescales the mass
spectrum (with its y-axis label) and the sidebar abundance values. In Conc and µg the
sidebar figure is the mean of that compound's own converted trace over the cycles being
shown — transmission, then **K** divided by the primary current cycle by cycle, then the
molar-volume factor — so it is the number the CSV reports as `Average` for that
interval, not an estimate of it. The humidity correction belongs to individual
near-thermoneutral (`humid`) compounds, so it is applied per compound on the traces and
the sidebar; the shared spectrum axis cannot carry it and says so. Where a value cannot
be converted (no correction curve, no **K**, or no primary signal), the sidebar shows
the Raw value and says why in its tooltip instead of inventing one.

Each peak's box in the sidebar is a **per-sample** tick, not a plain on/off box, and
it follows the mass-spectrum **average over** choice: with one sample selected the box
is that sample's own tick and touching it leaves every other interval alone; with the
whole run (or a background) selected it is the aggregate — ticked when the compound is
part of every sample interval, empty in none, and a partial (dash) tick when some
samples only, which is also how a mixed selection from the check/uncheck-all toggle
reads. Clicking an aggregate box only ever flicks it: a dash becomes a proper tick, the
next click clears it, the next ticks it again. In the Details view each row also carries
one small box per sample interval, labelled by interval number, so which samples a
compound belongs to is visible without opening anything; clicking one changes that
sample only. The heading says which of the two the boxes mean (`Peaks · all samples`,
`Peaks · sample_03`). A peak recorded in every sample writes no extra config field; a
partial one writes `samples`, the list of interval labels it belongs to. Either way a
compound selected for at least one sample still appears in the summary CSV exactly as
before — the per-sample split is recorded
now so that per-sample output can be built on it later. Interval labels key this
selection, so a rename that would duplicate a label is rejected, and a `samples` list
whose labels no longer exist falls back to every sample rather than silently dropping
the compound.

Above that list the sidebar names the **interval** the boxes speak about and switches it
between **sample** and **background**, whichever tab is open; it is the same control as
the class column in the **Intervals** card, mirrored with it. An interval's class is
carried by its label — `background_*` is what the analysis blanks against — so
changing the class renames the interval to the first free label of the target class
and rewrites the `samples` lists that referenced it. Classifying back restores the name
and the per-compound membership, so a misclassified sample that was corrected is saved
exactly as it was.

The faint curve behind the **Signal over time** plot is the composite VOC signal used
by `ptr segments`: the strong m/z 40–200 traces, each divided by its own median,
averaged per cycle (about 1 over a background, higher over a sample). It is drawn
against its own maximum, not the plotted axis, and says so in the corner; the legend
entry toggles it, remembered as `viz.show_disc`.

By default `viz` runs a localhost server, opens the browser, and **writes every change
straight into the `--config` file**; when the expert clicks **Done** it runs the
full-precision analysis and writes `--out`. The CLI prints the URL and waits indefinitely
for *Done* by default, so run it backgrounded. After a laptop sleep/wake cycle, the
served review remains available once the laptop is awake; stop it with Ctrl-C. Use
`--timeout SECONDS` only when an opt-in upper bound is wanted. For a portable file to email
to someone offline, use `--html review.html` instead (no server, no CSV; the expert tweaks
and clicks **Download config.json** to hand back for a later `ptr analyze`).

**Put your review points in the config's `checklist`, not in a wall of chat text.** The
long message you would otherwise write *after* launching `viz` is bad UX — it lands after
the user has already opened the browser, and it buries the few things that actually need
their attention. Instead: fold those points into the `checklist` (see section 5) so they
appear as a tickable list inside the app, right where the user is looking. Anything you'd
have written as "things to check" — an ambiguous boundary, a relabelled background
channel, a humidity/calibration caveat, a shaky identification — belongs there. Then your
chat reply is short: one or two lines handing over the URL and noting there's a checklist
waiting in the app. First-time users also get an automatic guided tour of the interface;
you don't configure it, but it means you don't need to explain the buttons in chat either.

In the app the expert (the user, at their own keyboard) sees the mass spectrum, a large
zoom of the selected peak with its integration window, and the compound's time trace with
segments overlaid. They can re-centre / add / remove / relabel peaks, drag / add / rename
segments, and change **R**,
**R_phys**, primary m/z, **K**, molar volume, the kinetic (`k`) correction, humidity
settings, and whole-run versus isolated per-interval windows. R windowing, peak/interval
edits, K, molar volume, kinetic, and humidity controls recompute from embedded preview
data. In contrast, primary m/z, R_phys, and whole-run window mode require HDF5
re-extraction/deconvolution; the plots and Methods card prominently mark those settings
stale, show preview versus final values, and label their provenance `browser edit`.
Reverting to the preview initial value clears the stale state. This remains true in
standalone HTML; there is no hidden refresh API. The Methods card states R integration
windows, which settings are live-safe or Done-only, R_phys Gaussian/deconvolution
resolution, K/Vm sources, primary m/z, k priority and anchor, humidity p/reference,
transmission fallback, concentration availability, manual-window behaviour, and
clustered fixed-centre behaviour. **Done**
performs the authoritative full-precision `analyze` rerun and writes the CSV. The
delivered CSV always comes from `analyze`, never the browser.

A compound label must never contradict its identification. A peak whose formula was
assigned keeps that identity: the placeholder name `unknown m/z …` is dropped in favour
of the assigned formula (or the top candidate's name when the formula is not curated),
and a hand-written label is never rewritten. A hand-drawn peak is named from the
library only when its measured mass really matches a library compound within 10 mDa,
and gets no formula at all otherwise, so an unlabelled peak cannot end up claiming an
identity. A label that names a *different* formula than the assigned one is surfaced as
a name/formula conflict in the sidebar and the Identification card rather than being
presented as an identification, and the Mass spectrum's "Average over" list tracks
interval renames, colour changes and resizes, so a renamed interval is never offered
under its old name.

### 5c. App mode (`ptr app`) — the reviewer works in the tool, not in chat

`ptr app` keeps one review server up and lets the user open files inside it:

```bash
ptr app                       # start screen: recent files, or type an absolute path
ptr app FILE.h5 --no-browser  # open one file immediately (background it: ~30-90 s for 2 GB)
ptr app --agent URL           # have an agent curate a newly detected config
```

- **The config lives beside the h5 file, same stem**: `ptr.h5` → `ptr.json`. An existing
  `<stem>-analysis-config.json` is used when there is no `<stem>.json`, so a file
  reviewed through the CLI reopens exactly as it was left. A same-stem JSON that is not a
  ptr config is never overwritten — the app writes `<stem>.ptr.json` instead.
- **Opening a never-reviewed file runs the deterministic pipeline** (the same detection
  as `--auto-peaks`/`--auto-segments`) and writes that config before loading it. The
  checklist it carries states that nothing has been curated yet, what was detected, and
  which calls remain the reviewer's. Anything the interval detection joined is said in
  one line on the Intervals card (config `merge_note`), because a reviewer who never saw
  a command line otherwise has no way to learn a merge happened. A file with no beam is
  labelled as one rather than presented as an empty panel.
- **The optional agent endpoint** (`--agent URL`, or the `PTR_AGENT_URL` environment
  variable) is posted the generated config as
  `{"file": …, "config": …, "diagnostics": {n_peaks, n_ranges, ncyc, instrument}}` and
  may answer with a bare config or `{"config": …}`. Any failure — unreachable, timeout,
  or a reply with no peaks or ranges — keeps the deterministic config and says so in the
  UI, so an offline agent never costs the user a file.
- **Export replaces Done**: it runs the full-precision analysis to `<stem>.csv` beside
  the file and leaves the app open, so the reviewer can keep working and export again.
  If something that is not a ptr summary already sits at that name (a Viewer export, say)
  the app writes `<stem>-ptr.csv` instead of overwriting it. `ptr app` never returns — it
  serves until Ctrl-C — so background it the way you would background `ptr viz` and hand
  the user the URL. It also cannot review a file on read-only media, since the config must
  be written beside the `.h5`.
- One file is open at a time (opening another closes the first, since a 2 GB run holds
  its traces in memory), recents live in `~/.ptr-ms/recent.json`, and the server binds to
  127.0.0.1 — nothing is uploaded anywhere.

App mode does not replace the agent-driven flow. When you are the one curating, keep
using `peaks` + `segments` → your config → `ptr viz`, where the review's Done ends the
run and writes the CSV that was asked for.

### 6. Calibrate concentration when accurate ppb/µg matters

Absolute concentration needs one calibration constant **K**. By default it is derived
from the file and reproduces the instrument's own concentration; a different acquisition
setup can use a different scale. If the user **already has** a reference CSV (a past
export or a colleague's) or a known standard, pin K to it — never ask them to go generate
one in PTR-MS Viewer:

```bash
ptr calibrate FILE.h5 reference.csv
ptr analyze FILE.h5 --config analysis-config.json --K 16.26 \
  --include-cycle-rows --out results.csv
```

`calibrate` recovers ranges from the reference's Cycle rows. Without a reference, state
that concentrations use the file's own calibration and may carry roughly 10-15 % absolute
uncertainty (raise it in the review `checklist`); relative comparisons remain useful. If
they want tighter absolute numbers, offer a standards calibration — not the Viewer.

### 7. Compare when a reference exists

```bash
ptr compare results.csv viewer.csv --per-mass
```

Comparison requires matching mass and range labels. First align the target panel and
cycle windows; otherwise "no overlapping rows" is a structural mismatch, not a numerical
failure. The validated breath reference gives median errors of 2.4 % Raw, 5.0 %
Corrected, 3.1 % Conc, and 3.2 % Conc[µg].

## How the numbers are produced

| Column           | Formula                                     | Constants — all read from the .h5                                                 |
| ---------------- | ------------------------------------------- | --------------------------------------------------------------------------------- |
| **Raw** [cps]    | Σ intensities over the peak's m/z window    | residual-checked `CALdata/Mapping` anchors (N ≥ 2), or usable per-cycle `CALdata/Spectrum` coefficients if Mapping is absent or unusable |
| **Corrected**    | Raw / Transmission(m/z)                     | `PTR-Transmission` curve; if absent, unit transmission (Corrected == Raw, flagged)  |
| **Conc** [ppb]   | Corrected × K / I_primary(t) × (k_anchor/k) | K from `TRACEdata`; configured primary m/z (21.022 by default); k from rate-constant table (`--kinetic`) |
| **Conc [µg/m³]** | Conc × (mz − proton) / Vₘ                   | Vₘ from drift temperature                                                         |

Files vary in what they carry. Standard processed files have usable
`CALdata/Mapping` anchors, `PTR-Transmission`, and pre-computed `TRACEdata` (full
Raw→Corrected→Conc). Mapping has shape `(N, 2)` with at least two `(m/z, timebin)`
anchors: exactly two determine the calibration directly, while three or more require
finite positive rows, strictly increasing masses and timebins, and a well-conditioned
least-squares fit of `timebin = a·√m + b`, then reconstructed anchor masses must have
finite absolute relative errors of at most 100 ppm. This is a deliberately generous
corruption/model-consistency ceiling, not an accuracy claim. If Mapping is absent or
unusable, the mass calibration falls back to usable per-cycle `CALdata/Spectrum`
coefficients. Some raw acquisition exports also omit `PTR-Transmission` and
pre-computed `TRACEdata`: transmission then defaults to unity (so **Corrected ==
Raw**, reported via `transmission_available: false`), and with no pre-computed
concentration the **Conc columns are NaN** unless you pass `--K`. `inspect`/`analyze`
surface these flags — report the degradation honestly rather than presenting
uncalibrated Corrected/Conc as final.

Concentration uses the standard **primary-ion-normalised** model: dividing by the
per-cycle reagent-ion signal (the configured primary-ion m/z, 21.022 by default) tracks
reagent-ion drift over the run, and **K** is a single calibration constant. Isolated peaks use apex-centred
(auto-corrected) windows; closely-spaced clustered peaks are Gaussian/deconvolved fitted
components at fixed model centres. Full derivation: `src/ptr_ms_analysis/reference/ionicon-h5-format.md`.

**Per-compound sensitivity (`--kinetic`).** Sensitivity scales with each compound's
proton-transfer rate constant k (`Conc ∝ 1/k`). By default one k is assumed for all
compounds (matches a single-sensitivity reference). Passing `--kinetic` scales each
compound by its own k — physically more accurate but it _diverges from_ a single-k
reference (e.g. benzaldehyde, k≈3.9, drops to ~half a k=2 reference). k comes from a
peak's explicit `"k"`, its `"formula"`, or its m/z, looked up in
`src/ptr_ms_analysis/reference/rate_constants.json` (218 compounds from the PTR Library — Pagonis,
Sekimoto & de Gouw 2019; browse with the `rates` command). See
`src/ptr_ms_analysis/reference/ptr-ms-chemistry.md`.

**Humidity handling (low-proton-affinity compounds).** HCN, formaldehyde, H₂S, formic
acid and ammonia have proton affinity near water's, so proton transfer is partly
reversible and their sensitivity depends on sample **humidity** — a fixed k or K
misquantifies them. Whenever such a compound is in the run, `analyze` reports a
**humidity diagnostic**: the per-range water-cluster ratio (m/z 37 / the configured
primary-ion m/z, 21.022 by default — m/z 19 is usually saturated) and the cross-range
spread. If humidity varies (it swung 30 % across
the almond samples and 237 % across the breath run), relative concentrations of those
compounds are confounded and it says so. Pass `--humidity-correct` to normalise them
per-cycle by `(ratio/ref)^p` (`--humidity-p`, 0=off … 1=equilibrium upper bound). **p
must be calibrated** from a standard at ≥2 humidities for accurate absolute values;
uncalibrated, the correction only puts _relative_ comparisons on equal-humidity footing
and its magnitude is approximate. HCN is the canonical case — flag it to the user and,
without an HCN standard, treat its absolute concentration as indicative only. The
wet-lab calibration that pins HCN (and how to recognise existing calibration data) is
described in `src/ptr_ms_analysis/reference/hcn-calibration.md`.

## Accuracy & concentration calibration

Validated against two reference Viewer exports:

| Experiment                                             | Raw   | Corrected | Conc  | Conc[µg] |
| ------------------------------------------------------ | ----- | --------- | ----- | -------- |
| Breath, 22 masses × 18 ranges (default K)              | 2.4 % | 5.0 %     | 3.1 % | 3.2 %    |
| Bitter-almonds, 2 masses × 8 ranges (**calibrated K**) | 0.7 % | 3.1 %     | 2.6 % | 2.5 %    |

Raw and Corrected are file-derived and robust (median < 5 % on both). Larger Raw errors
appear only on very small isotope peaks (low SNR) and intense overlapping peaks (the m37
water-cluster region) — flag these rather than trusting them.

**Concentration has one irreducible calibration constant K.** The raw file does not
store the sensitivity a specific Viewer project used, and it genuinely varies between
experiments (e.g. K ≈ 19 vs 16 on the two above — the operator's Viewer setting). So:

- **With a reference Viewer CSV** (or a known standard): run `calibrate` to fit K, then
  pass `--K`. Concentration then matches to a few percent (as above).
- **Without one**: the default K is derived from the file's own acquisition calibration
  — physically valid, but its absolute scale may differ from a given Viewer project by
  ~10-15 %. **Tell the user** the concentration is on the instrument's own scale and
  offer to calibrate if they have a reference. Raw and Corrected are unaffected by this.

## Tuning

| Flag                                                  | Default           | Change when                                                                                                            |
| ----------------------------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `--R`                                                 | 1200              | Integration window (half-width m/2R). Raise to reduce neighbour overlap; lower for more area.                          |
| `--R-phys`                                            | 2400              | Physical resolution for deconvolution Gaussians; set to the instrument's measured resolution.                          |
| `--K`                                                 | derived from file | Concentration constant; set from `calibrate` or a standards calibration to fix the absolute scale.                     |
| `--kinetic`                                           | off               | Per-compound rate-constant correction (more accurate absolute concentrations, but diverges from a single-k reference). |
| `--k-anchor`                                          | 2.0               | Rate constant (1e-9 cm³/s) the baseline K assumes; only affects `--kinetic`.                                           |
| `--humidity-correct`                                  | off               | Humidity-normalise near-thermoneutral compounds (HCN, formaldehyde, formic acid…) using the water-cluster ratio.       |
| `--humidity-p`                                        | 1.0               | Humidity exponent, 0 (off) … 1 (equilibrium upper bound). Calibrate from a standard at ≥2 humidities.                  |
| `--humidity-ref`                                      | run median        | Water-cluster ratio to normalise humidity to.                                                                          |
| `--primary-mz`                                        | 21.022            | Reagent-ion normaliser; change for NO⁺/O₂⁺ modes.                                                                      |
| `--molar-volume`                                      | from drift T      | Set 24.465 for the 25 °C ambient µg/m³ convention.                                                                     |
| `--include-cycle-rows`                                | off               | Enable for final Viewer-style exports so every range boundary is reproducible.                                         |
| `--merge-high-gap`                                    | automatic (~60 s) | Override the gap cap for joining high plateaus; `0` never joins them. The join itself is judged on the signal, not on the cycle count.        |
| `segments --grad-thr / --min-duration / --high-ratio` | 0.02 / 30 / 3.0   | Loosen/tighten segment detection if plateaus are noisy or missed.                                                      |

## Commands

`inspect` · `peaks` · `segments` · `analyze` (add `--auto-peaks`/`--auto-segments` for a
quick detect-only pass) · `viz` (browser review/tweak app; serves live-saving or exports
standalone HTML with `--html`) · `calibrate` · `compare` · `rates <name|formula|mz>`
(browse the proton-transfer rate-constant table). Run any with `--help`; all discovery
output is JSON (add `--pretty` to indent).

## Out of scope

- **Untargeted formula assignment / isomer identification** — this quantifies a peak
  list _you_ define. For untargeted discovery use `ptairMS` (R) or `PTRwid`; for general
  IONICON file reading in Python, IONICON's own `PyTRMS`.
- **Segment _identities_** — the data shows _when_ samples occurred, never _what_ they
  were. Final exports deliberately use chronological generic labels rather than asking
  the user for identities.
- **The operator's target panel** — `TRACEdata/TraceInfo` is a broad formula library,
  not a record of which channels were selected in a Viewer project.
- **Exact manual windows** — plateau detection can propose stable ranges but cannot
  recover the operator's scientific choice of which samples/backgrounds to export.
