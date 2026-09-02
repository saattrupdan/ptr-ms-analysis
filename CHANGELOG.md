# Changelog

All notable changes to `ptr-ms-analysis` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- The Peaks sidebar tick is now sample-specific and follows the **average over**
  choice: with one sample selected it is that sample's own tick, with the whole run
  selected it is the aggregate — ticked for every sample interval, empty for none, a
  dash for some. Clicking the aggregate box only ever flicks it between ticked and
  empty, so a dash becomes a full tick rather than a dead end. The Details view shows
  one box per sample interval in every row, and the heading names the scope. A
  compound in only some samples records `samples` in the config; a compound in every
  sample needs no new field, and the summary output is unchanged either way.

### Changed

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
