#!/usr/bin/env bash
# Entry point of the jupyter workflow pod.
#
# Access control: JupyterLab listens on 127.0.0.1 only, so nothing else in the cluster can
# reach it. The only way in is `kubectl port-forward`, which needs your Entra sign-in and
# pods/portforward permission in the lichess namespace. That's why there is no token.
#
# Stops itself after LICHESS_NOTEBOOK_HOURS.
set -euo pipefail
hours="${LICHESS_NOTEBOOK_HOURS:-8}"

# Starter notebooks, copied once: your edits in $HOME/examples are never overwritten.
mkdir -p "$HOME/examples"
cp -rn /opt/lichess/notebooks/. "$HOME/examples/"

set +e
timeout "${hours}h" jupyter lab --ip=127.0.0.1 --port=8888 --no-browser \
  --ServerApp.root_dir="$HOME" \
  --IdentityProvider.token='' --ServerApp.password=''
rc=$?
if [ "$rc" -eq 124 ]; then
  echo "JupyterLab stopped after ${hours}h (parameter: hours)"
  exit 0
fi
exit "$rc"
