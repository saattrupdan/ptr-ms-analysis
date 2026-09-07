# Changelog

All notable changes to `ptr-ms-analysis` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- **Exporting no longer ends on a dead end.** The results dialog used to turn its own
  button into a disabled label reading "Opened ✓ — you can close this tab" — which does
  nothing in an app that has no tab to close — and a failed export left an error card
  with no way out at all. Revealing the CSV now closes the dialog and returns you to the
  review, **Keep reviewing** closes it without revealing, and both routes leave the
  **Export** button ready to run again. The one-shot `ptr viz` flow is unchanged.

### Added

- **The app can open in its own desktop window.** `ptr app --window` runs the review in
  a single window with no address bar, `pywebview` being an extra
  (`pip install 'ptr-ms-analysis[desktop]'`) rather than a dependency, and a packaged
  bundle uses the window by default because a double-clicked app has no terminal to read
  an address out of. Closing the window stops the server, **Stop the app** closes the
  window, and **Browse this computer…** uses the window's own dialog when there is one; a
  machine without the extra, or without a display, says so once and serves a browser tab
  exactly as before. The `package` workflow installs `.[desktop]` and the PyInstaller
  spec bundles `webview` when it is present, so the `.pkg` and `.msi` ship the window;
  the frozen smoke runs `--window` on a runner with no display to prove the fallback
  rather than assume the window.
- **The start screen was rebuilt, and gained a real file dialog.** Recent runs are
  listed with their size, when you last opened them and whether a config exists yet;
  **Browse this computer…** opens the desktop's own file picker, because the server runs
  where the files are, which is the one thing a web page normally cannot do. The page is
  a single-column layout with proper type, focus rings, a dark scheme and a progress bar
  while a run loads.
- **Double-clicking the bundled app opens it.** Finder starts the bundle with no
  arguments, and the plain `ptr` command line answers that with usage text and exit code
  2 — invisibly, in a windowed bundle. A runtime hook turns a bare launch inside a bundle
  into `ptr app`, and when no browser opens the address is written to
  `~/.ptr-ms/log.txt` rather than vanishing.
- **Packaging: `packaging/ptr-app.spec`, `packaging/make_msi.py`, `scripts/smoke_frozen.py`,
  and a `package` workflow.** PyInstaller builds a bundle a reviewer can run with no
  Python installed (one-dir by choice — one-file unpacks into `%TEMP%` on every start and
  is what antivirus tools object to), wrapped as a `.app` that a `.pkg` installs on
  macOS and an `.msi` on Windows. The MSI's component list is generated from the built
  folder with path-derived ids and GUIDs, so an upgrade replaces and removes exactly the
  right files,
  and it targets the MIT-licensed WiX v3 rather than v6+, which will not run until the
  Open Source Maintenance Fee EULA is accepted.
  PyInstaller cannot cross-compile, so the workflow builds on native macOS and Windows
  runners and uploads one installer per platform, with a `v*` tag publishing them as a
  GitHub Release. The smoke script starts the finished bundle against a tiny synthetic
  file and fails unless it serves the review page, since `ptr --help` would pass on a
  bundle that can do nothing else.
- **"Stop the app" on the start screen, and a log file for bundle runs.** A double-clicked
  app has no terminal to press Ctrl-C in, so the page can shut the server down itself
  (`POST /shutdown`, and `SIGTERM` now quits the same way); with no console, its URL and
  errors are appended to `~/.ptr-ms/log.txt`.
- **App mode: `ptr app`.** A persistent local review app for people who want the tool
  rather than the chat. It opens on a start screen of recent files, takes a file from
  there, and stays up between files. Each file's config lives beside it under the same
  stem (`ptr.h5` → `ptr.json`, with an existing `<stem>-analysis-config.json` honoured),
  so reopening a reviewed file returns exactly what was saved; a file that has never been
  reviewed gets the deterministic peak and interval pipeline written to that path, with a
  checklist that says plainly that nothing has been curated yet and which calls are still
  a human's. The primary button is **Export** instead of Done: it writes `<stem>.csv`
  beside the file and leaves the app open for more work. With `--agent URL` (or
  `PTR_AGENT_URL`) a newly generated config is offered to an agent for curation first,
  and the deterministic config is kept — visibly — whenever that endpoint is missing,
  slow or unhelpful. The server binds to 127.0.0.1 and the only outbound request is to
  the endpoint the user named.
- `analyze.auto_peaks` / `analyze.auto_ranges`: the detection pipeline is now callable
  without argparse, which is what lets the app build a config on the user's behalf.
- The Peaks sidebar tick is now sample-specific and follows the **average over**
  choice: with one sample selected it is that sample's own tick, with the whole run
  selected it is the aggregate — ticked for every sample interval, empty for none, a
  dash for some. Clicking the aggregate box only ever flicks it between ticked and
  empty, so a dash becomes a full tick rather than a dead end. The Details view shows
  one box per sample interval in every row, and the heading names the scope. A
  compound in only some samples records `samples` in the config; a compound in every
  sample needs no new field, and the summary output is unchanged either way.

### Changed

- **The macOS artifact is a `.pkg`, and `packaging/README.md` now explains both
  installers.** A disk image asked the reviewer to drag a bundle into Applications; a
  product archive installs it, records what it wrote, and installs from a command line,
  which is how CI now proves the artifact rather than the build folder.
  `packaging/make_pkg.py` writes the `productbuild` distribution — title, version, a
  macOS 11.0 floor and the architecture, with no paths and no timestamps, so the same
  checkout gives the same XML twice — and the `package` workflow pairs it with
  `pkgbuild --component … --install-location /Applications`, then installs the result
  with `sudo installer -pkg` and smokes
  `/Applications/PTR-MS Review.app/Contents/MacOS/ptr`, exactly as the Windows job smokes
  both `dist/ptr` and `C:\Program Files\PTR-MS Review`. The guide has both command pairs,
  the reason WiX v3.14 is pinned (v6 and later are gated behind the Open Source
  Maintenance Fee), how to inspect a `.pkg` (`lsbom`, `pkgutil`) and remove one (there is
  no uninstaller), what a double-clicked bundle gets, and what is still missing: no
  Developer ID signing or notarization, and no Authenticode, so an unsigned `.pkg` still
  trips Gatekeeper on first run and SmartScreen warns on Windows.
- **Adjacent plateaus are now joined on evidence, not on a cycle count.** A gap between
  two same-class plateaus merges only when it covers under ~60 s of acquisition (never
  fewer than 30 cycles) *and* never left the phase its neighbours are in: its highest
  cycle stays below the higher neighbour's level times 2, read against the run's own
  background, and for a sample its lowest cycle also stays above the lower neighbour's
  level divided by 2. A background has no lower test, since a background cannot fall out
  of itself — a dropout toward zero is still the same blank, and splitting it would cost
  the longer reference interval a blank exists to provide. A wobble
  inside one sample therefore merges at 1 s/cycle and at 5 s/cycle alike, while a sample
  that fell back to the background — or a gap that spiked out of the phase — stays a
  break however short it is. An opposite-class plateau between two segments is still a
  hard boundary, and each gap is judged against the plateau it abuts rather than the
  running average of a partly merged interval, so the verdict does not depend on which
  plateau came first. On a 20,725-cycle IoniTOF run this gave 12 sample and 11
  background intervals where a reviewer curated 12 and 10 by hand, and where the length
  rule at 30 cycles gave 17 and 7 — it joined two samples whenever the gap between them
  happened to be short. `--merge-high-gap N` survives as a cap override and
  `0` still means never join high plateaus; `ptr segments --merge-high-gap` and
  `ptr analyze --merge-high-gap` now default to the automatic test instead of off.
- **Every merge explains itself.** `merged_gaps` now carries, per gap, its length, the
  gap's minimum and maximum level and a reason (`level held`, `fell to baseline`,
  `adjacent`, or `length only` on the legacy path), and the review app says the same in
  one line on the Intervals card — `joined 2 wobbles, level held (≤ 28 cycles)` — via
  the config's `merge_note`. The reviewer in `ptr app` never sees a command line, so a
  silent join of their intervals would have been unfalsifiable. A merged interval's
  level is the mean of its plateaus weighted by plateau cycles, so the cycles between
  them cannot drag the reported level toward the baseline.
- The interval in scope and its sample/background class now sit in the Peaks sidebar,
  one click away on either tab instead of only in the Intervals card. Switching class
  renames the interval, because the analysis reads the class from the interval name;
  switching it back restores the name and the recorded per-sample selections.
- The composite VOC curve behind the Signal over time trace is labelled in the plot,
  and its legend entry is a switch kept in the config as `viz.show_disc`.
- The Intervals card shrinks as far as the splitter is dragged: the plot is no longer
  capped at 560 px, which left the card never smaller than half a tall window.
- The Intervals card updates while an interval edge is dragged and keeps its rows in
  chronological order, so the table no longer lags behind the plot.
- The Mass spectrum "Average over" list follows interval renames, recolouring and
  resizes instead of showing stale names, and re-averages the spectrum when the
  interval it points at changes shape.
- The Raw / Corrected / Conc / µg selector now also drives the mass spectrum and the
  sidebar values. In Conc and µg the sidebar figure is the mean of the compound's own
  converted trace over the cycles being shown — the number the CSV reports as `Average`
  for that interval. The shared spectrum axis carries only the conversion every compound
  shares, and says so; the per-compound humidity correction stays per compound.
- A compound name never contradicts its identification: an auto-generated
  `unknown m/z …` label on a peak with an assigned formula is replaced by that
  formula, a hand-drawn peak is named from the library only within 10 mDa of a library
  mass, and a label naming a different formula than the assigned one is flagged.
- Interval labels must be unique, because they key the per-sample selection.

### Fixed

- **An autosave no longer invents compound names.** A peak with neither a label nor a
  formula needs something to draw on the spectrum, so the review page displays a
  mass-derived stand-in (`m17.032`) — and wrote it back into the config as though
  someone had assigned it. A freshly detected file therefore gained 132 names that
  looked curated but were not, and its CSV printed the mass twice. The stand-in is now
  display-only, and the saved config keeps the name empty until someone chooses one.
- **A file opened from the recents list no longer appears twice on the start screen**:
  once in its own panel with **Open the review**, once as a plain row. The recents list
  now says which entry is the open one, and leaves it out.
- **The app no longer reports itself busy the moment after it reports itself ready.**
  Readiness was announced before the in-flight flag was cleared, so a client that acted
  on "ready" could have a close or an export refused.
- **An error you caused stays on screen.** The status poll cleared the message within a
  couple of seconds, so a failed open left no trace of what went wrong.
- ↑/↓ move the peak selection down and up the order the sidebar is showing. They had
  always walked the stored m/z order, so with the list sorted by abundance or label
  they jumped to compounds that were nowhere near the highlighted row.
- The Raw / Corrected / Conc / µg buttons now sit in the same place on both tabs: the
  x-axis selector moved to the right-hand slot that **average over** occupies on the
  Mass spectrum tab, and a long interval list can no longer squeeze the tab buttons
  sideways as a side effect.
- Reclassifying an interval from sample to background now reaches the saved config. It
  used to change only the in-memory colour: the class is read back from the interval
  name at load, so an unrenamed interval came back as a sample. A compound's `samples`
  list also survives the trip — before, an interval classed away and back silently
  dropped compounds that were in only some samples.

## [0.4.0] - 2026-08-24

### Added

- Added a check/uncheck-all toggle to the Peaks sidebar.
- Added alphabetical label ordering alongside m/z and abundance ordering.
- Added a persistent draggable splitter between the plot and context card.

### Changed

- `viz` now waits indefinitely by default; `--timeout` remains available as an
  explicit opt-in limit.
- Improved responsive sizing and alignment of the Peaks sidebar and context cards.
- Removed redundant card guidance and improved the guided-tour button's light-mode
  contrast.

## [0.3.0] - 2026-08-23

### Added

- Added a peaks-sidebar ordering selector for m/z order or descending abundance,
  where abundance is the mean per-cycle integrated Raw signal. The compact list now
  shows only the active sort field; details shows both m/z and abundance. Sidebar
  values follow the Mass spectrum tab's selected average-over interval.

### Changed

- Absolute-time displays now apply the file's `UTC_Offset` so plot, crosshair, and
  interval times use the lab PC's local wall-clock time.

## [0.2.1] - 2026-08-22

### Fixed

- Restored the detailed scientific sections in the browser review Methods panel while
  retaining its live effective-settings and provenance summary.

## [0.2.0] - 2026-08-22

### Added

- Added the configurable `viz` x-axis unit selector. It accepts exactly `cycle`,
  `relative`, and `absolute`, with CLI/config precedence and cycle-based range and
  CSV persistence. Relative time uses valid `PCTime` values before a duration-based
  fallback; absolute time requires valid timestamps within ISO UTC years 0000–9999.

## [0.1.2] - 2026-08-22

### Fixed

- Fixed browser-review preparation failing when `SPECdata/AverageSpec` contains
  non-finite bins; `viz` now treats those rare corrupt bins as zero, matching peak
  detection and trace extraction.

## [0.1.1] - 2026-08-21

### Fixed

- Fixed the former too-many-values-to-unpack error when `CALdata/Mapping` contains
  more than two anchors; three or more `(m/z, timebin)` anchors are now accepted
  only after a well-conditioned least-squares fit and a finite reconstructed-mass
  residual check of at most 100 ppm. This is a deliberately generous
  corruption/model-consistency ceiling, not an accuracy claim. Mapping still falls
  back to usable per-cycle `CALdata/Spectrum` coefficients when absent or unusable.

## [0.1.0] - 2026-08-21

### Added

- Initial standalone Python package for processing IONICON IoniTOF PTR-MS and
  PTR-TOF `.h5` files.
- `ptr` command-line interface with commands for inspection, peak detection,
  segmentation, analysis, visual review, calibration, comparison, and rate
  constants.
- Browser-based review workflow for curating detected peaks and time segments.
- Formula identification, transmission correction, concentration conversion,
  and bundled PTR-MS reference data.
- Package metadata, resource loading, automated tests, and CLI smoke checks for
  installation in isolated environments.
