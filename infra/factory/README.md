# Factory sandbox images

Builds run the code the AI writes inside these images, one throwaway container per
command, with no network (see `backend/ymer/factory/sandbox/docker.py`). Build them once
with `make factory-images`, then set `YMER_FACTORY_SANDBOX=docker`.

With `YMER_FACTORY_SANDBOX=modal` the same Dockerfiles are built by Modal instead, on
the first run of each stack, so no local Docker is needed. Keep each file self-contained
(no `COPY` from this folder), since Modal builds it without a build context.

| Stack | Image | Test command |
| --- | --- | --- |
| static-web | `ymer-factory/static-web:1` | `node --test 'tests/**/*.test.mjs'` |
| python-cli | `ymer-factory/python-cli:1` | `python -m pytest -q` |
| flask-react | `ymer-factory/flask-react:1` | `cd backend && python -m pytest -q` |
| node-cli | `ymer-factory/node-cli:1` | `node --test 'tests/**/*.test.mjs'` |
| python-api | `ymer-factory/python-api:1` | `python -m pytest -q` |

Because there is no network inside the sandbox, a build can only use the packages baked
into its image. Add a package here (and bump the tag in `backend/ymer/factory/stacks.py`)
when a stack needs it.

## Checks

The same images run each app's checks (`backend/ymer/factory/ci_scripts.py`): lint,
build, tests and a smoke run, one `sh .ymer-ci/<step>.sh` per container. The Python
images carry `ruff` for the lint step. Images built before ruff was added still work:
lint then falls back to a syntax check, and says so in its output. Run
`make factory-images` again (or push the images to your Modal registry again) to get the
full lint.
