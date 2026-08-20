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
- The `ptr` console entry point resolves to `ptr_ms_analysis.analyze:main`.
- The review UI is generated and served by Python; there is no separate frontend build.

## Layout

| Path | Purpose |
| --- | --- |
| `src/ptr_ms_analysis/analyze.py` | CLI parsing, command handlers, CSV output, and orchestration. |
| `src/ptr_ms_analysis/ptrms.py` | HDF5 loading, extraction, segmentation, and quantification. |
| `src/ptr_ms_analysis/formula_id.py` | Formula enumeration and candidate scoring. |
| `src/ptr_ms_analysis/viz.py` | Self-contained browser review UI and localhost server. |
| `src/ptr_ms_analysis/gen_rate_constants.py` | Rebuilds the bundled rate-constant JSON. |
| `src/ptr_ms_analysis/reference/` | Scientific references and package data shipped with the CLI. |

## Running it

Install the checkout in an isolated environment so its CLI and dependencies stay
 isolated from other packages:
```bash
pipx install --editable .
ptr --help
ptr rates water
```

For development without a persistent installation, run commands from this directory:

```bash
uvx --from . ptr --help
uvx --from . ptr rates water
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
uvx --from . ptr --help
uvx --from . ptr inspect --help
uvx --from . ptr peaks --help
uvx --from . ptr segments --help
uvx --from . ptr analyze --help
uvx --from . ptr viz --help
uvx --from . ptr calibrate --help
uvx --from . ptr compare --help
uvx --from . ptr rates water
uv build
```

For scientific or HDF5-processing changes, also run the affected command on a suitable
local fixture and inspect its JSON diagnostics or CSV output. Do not commit measurement
files, generated review HTML, configs, or result CSVs.

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

- `src/ptr_ms_analysis/` is the installable package. Keep package-internal imports
  relative and use `importlib.resources` for bundled data; do not reintroduce flat
  top-level modules.
- Install with `pipx --editable` or another isolated environment. The public command is
  `ptr`; package modules are imported as `ptr_ms_analysis.*`.
- `src/ptr_ms_analysis/reference/rate_constants.json` is generated from
  `src/ptr_ms_analysis/reference/ptrlibrary.csv` by
  `uv run python -m ptr_ms_analysis.gen_rate_constants`. Change the source or generator,
  regenerate the JSON, and review both files together rather than hand-editing entries.
- Reference Markdown, CSV, and JSON files under `src/ptr_ms_analysis/reference/` are
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
