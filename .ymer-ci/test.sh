#!/bin/sh
# Written by Ymer Factory. The same script runs in Ymer and in your CI.
set -eu
python -m pytest -q -p no:cacheprovider
