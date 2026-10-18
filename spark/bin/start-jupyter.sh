#!/usr/bin/env bash
# Entry point of the jupyter workflow pod. JupyterLab reads its token from JUPYTER_TOKEN
# (Secret "jupyter", created by infra/gitops) and stops itself after LICHESS_NOTEBOOK_HOURS.
set -euo pipefail
: "${JUPYTER_TOKEN:?JUPYTER_TOKEN is not set}"
hours="${LICHESS_NOTEBOOK_HOURS:-8}"

# Starter notebooks, copied once: your edits in $HOME/examples are never overwritten.
mkdir -p "$HOME/examples"
cp -rn /opt/lichess/notebooks/. "$HOME/examples/"

set +e
timeout "${hours}h" jupyter lab --ip=0.0.0.0 --port=8888 --no-browser \
  --ServerApp.root_dir="$HOME" --ServerApp.allow_remote_access=True
rc=$?
if [ "$rc" -eq 124 ]; then
  echo "JupyterLab stopped after ${hours}h (parameter: hours)"
  exit 0
fi
exit "$rc"
