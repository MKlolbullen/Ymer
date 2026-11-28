"""Written by Ymer Factory: run the command's --help."""

import importlib
import sys

sys.path.insert(0, "src")
cli = importlib.import_module("app.cli")
main = getattr(cli, "main", None)
if main is None:
    print("app.cli has no main() function.")
    sys.exit(1)
try:
    code = main(["--help"])
except SystemExit as exc:
    code = exc.code
print(f"main(['--help']) returned {code!r}.")
sys.exit(0 if code in (0, None) else 1)
