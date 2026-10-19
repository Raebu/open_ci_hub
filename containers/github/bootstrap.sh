#!/bin/sh
# Initialise the persistent runner directory once, preserving registration on restart.
set -eu
umask 077
runner_home="${OPEN_CI_RUNNER_HOME:-/home/runner}"
runner_assets="${OPEN_CI_RUNNER_ASSETS:-/opt/open-ci-runner}"
if [ ! -f "$runner_home/run.sh" ] || [ ! -f "$runner_home/config.sh" ]; then
    cp -a "$runner_assets/." "$runner_home/"
fi
cd "$runner_home"
exec "$@"
