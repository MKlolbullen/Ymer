#!/bin/sh
# Written by Ymer Factory. The same script runs in Ymer and in your CI.
set -eu
python .ymer-ci/cli-smoke.py
