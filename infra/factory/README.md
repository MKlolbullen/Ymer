# Factory sandbox images

Builds run the code the AI writes inside these images, one throwaway container per
command, with no network (see `backend/ymer/factory/sandbox/docker.py`). Build them once
with `make factory-images`, then set `YMER_FACTORY_SANDBOX=docker`.

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
