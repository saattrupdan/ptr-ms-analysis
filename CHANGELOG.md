# Changelog

All notable changes to `ptr-ms-analysis` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Added the configurable `viz` x-axis unit selector. It accepts exactly `cycle`,
  `relative`, and `absolute`, with CLI/config precedence and cycle-based range and
  CSV persistence. Relative time uses a finite positive spectrum duration, or one
  second when the duration metadata is invalid; absolute time is disabled for invalid
  or browser-unrenderable `PCTime` values.

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
