# Word counter, release 1

Built by Ymer Factory on 2026-11-28 with the Python command-line tool stack (Python 3.12, argparse, SQLite or files for storage). Delivered as: an installable python package.

## Release notes

Live test of Ymer Factory delivery.

## Run it

```sh
python -m venv .venv && . .venv/bin/activate
pip install . pytest
python -m app.cli --help
python -m pytest
```

Or as a container: `docker build -t app . && docker run --rm app --help`.

## Checks

Ymer ran these before the release; your CI runs the same scripts (`.github/workflows/ymer-checks.yml` or `.gitlab-ci.yml`):

```sh
sh .ymer-ci/lint.sh
sh .ymer-ci/build.sh
sh .ymer-ci/test.sh
sh .ymer-ci/smoke.sh
```
