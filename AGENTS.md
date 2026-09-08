# PTR-MS analysis

This skill is an agent-driven, open-source replacement for PTR-MS Viewer. It ships a
Python CLI that reads IONICON IoniTOF `.h5` files, detects and quantifies ion peaks,
proposes time segments, and serves a browser-based expert review.

Read `SKILL.md` before changing behaviour. It defines the intended analysis workflow,
scientific caveats, and agent-facing contract; `README.md` is the shorter user-facing
CLI reference.

## Stack

- Python 3.9 or newer, packaged with setuptools through `pyproject.toml`.
- Runtime dependencies: NumPy and h5py.
- The `sniff` console entry point resolves to `sniff.analyze:main`.
- The review UI is generated and served by Python; there is no separate frontend build.

## Layout

| Path | Purpose |
| --- | --- |
| `src/sniff/analyze.py` | CLI parsing, command handlers, CSV output, and orchestration. |
| `src/sniff/ptrms.py` | HDF5 loading, extraction, segmentation, and quantification. |
| `src/sniff/formula_id.py` | Formula enumeration and candidate scoring. |
| `src/sniff/viz.py` | Self-contained browser review UI and localhost server. |
| `src/sniff/gen_rate_constants.py` | Rebuilds the bundled rate-constant JSON. |
| `src/sniff/reference/` | Scientific references and package data shipped with the CLI. |
| `packaging/` | PyInstaller spec and frozen-entry point; the `package` workflow builds a folder bundle per OS. |

## Running it

For development, use the checkout's project environment:
```bash
uv sync
uv run sniff --help
uv run sniff rates water
```

Use real IoniTOF data only when exercising file-dependent commands. `.h5` files can be
about 1 GB, and a full analysis commonly takes about a minute.

## Validation

Run the automated tests, lint check, build, and CLI smoke checks after a change. The
browser regression is especially relevant when changing identification display; it
requires the `agent-browser` CLI (`npm i -g agent-browser` and `agent-browser install`)
in addition to the package's normal Python dependencies:

```bash
uv run pytest
uv run ruff check --select F,I src tests scripts
uv run python scripts/smoke_viz.py
uv run sniff --help
uv run sniff inspect --help
uv run sniff peaks --help
uv run sniff segments --help
uv run sniff analyze --help
uv run sniff viz --help
uv run sniff app --help
uv run sniff calibrate --help
uv run sniff compare --help
uv run sniff rates h2o    # the bundled library has no water entry; h2o returns matches
uv build
```

For scientific or HDF5-processing changes, also run the affected command on a suitable
local fixture and inspect its JSON diagnostics or CSV output. Do not commit measurement
files, generated review HTML, configs, or result CSVs.

Packaging is checked separately because it is slow and pulls its own toolchain:
Remove stale `dist/` and `build/` output, then run
`uv run --with pyinstaller pyinstaller --noconfirm packaging/sniff-app.spec` and
`uv run python scripts/smoke_frozen.py "dist/Sniff.app/Contents/MacOS/sniff"` on
macOS, or `dist/sniff/sniff.exe` on Windows, which starts the frozen bundle and asserts
it serves the review page. The executable is at the bundle root; PyInstaller
uses the app's `Contents/Resources/` on macOS and `_internal/` on Windows for its
package data and libraries. Run it when `packaging/`, dependencies, or the app server
change; the Windows half of it can only be verified on a Windows runner.

## Conventions

- Keep compatibility with Python 3.9; do not introduce Python 3.10+ syntax without first
  raising `requires-python` deliberately.
- Write documentation and new prose in British English, wrapped at 88 characters.
- Preserve JSON on stdout for discovery commands and send progress logs to stderr.
- Keep the CLI deterministic. Chemistry assignment and segment curation remain explicit
  agent decisions; do not add a one-shot automatic workflow.
- Update `SKILL.md` and `README.md` when flags, output fields, workflow, or scientific
  interpretation change. The `analyze` object is resolved with CLI override > curated
  config > legacy default; keep the Methods provenance and authoritative Done rerun
  wording aligned with that implementation.
- Use Conventional Commits, following the parent dotfiles repository.

## Gotchas

- `src/sniff/` is the installable package. Keep package-internal imports
  relative and use `importlib.resources` for bundled data; do not reintroduce flat
  top-level modules.
- Run checkout commands with `uv run sniff`; package modules are imported as
  `sniff.*`.
- `src/sniff/reference/rate_constants.json` is generated from
  `src/sniff/reference/ptrlibrary.csv` by
  `uv run python -m sniff.gen_rate_constants`. Change the source or generator,
  regenerate the JSON, and review both files together rather than hand-editing entries.
- Reference Markdown, CSV, and JSON files under `src/sniff/reference/` are
  package data. Keep `pyproject.toml` in sync when adding a new bundled file type.
- `viz` reviews an already curated config; it must not silently perform peak or segment
  detection. The delivered CSV is always produced by the analysis path.
- Preserve 1-based, inclusive cycle ranges and deterministic chronological labels:
  `sample_01`, `sample_02`, and `background_01`, `background_02`, numbered separately.
- Do not replace missing calibration or transmission data with plausible-looking values.
  Surface degraded accuracy through the existing diagnostics and NaN behaviour.
- Do not weaken noise, overlap, apex, humidity, blank-file, or sample/background checks
  merely to produce more populated output. These are scientific safeguards.
- The skill replaces proprietary PTR-MS Viewer. Documentation must not instruct users to
  generate, validate, or repair results in that tool; an existing reference CSV may only
  be used for calibration or comparison.
