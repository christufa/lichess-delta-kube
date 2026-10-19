#!/usr/bin/env bash
# Entry point of the jupyter workflow pod.
#
# Access control: JupyterLab listens on 127.0.0.1 only, so nothing else in the cluster can
# reach it. The ways in are `kubectl port-forward` (your Entra sign-in + RBAC in lichess) and
# the cloudflared sidecar publishing chess-lab behind Cloudflare Access. No token.
#
# allow_remote_access only turns off Jupyter's Host-header check (DNS-rebinding protection
# for servers on localhost), which would reject requests for chess-lab.generops.dev. The
# 127.0.0.1 bind is still the boundary.
#
# Stops itself after LICHESS_NOTEBOOK_HOURS.
set -euo pipefail
hours="${LICHESS_NOTEBOOK_HOURS:-8}"

# Starter notebooks, copied once: your edits in $HOME/examples are never overwritten.
mkdir -p "$HOME/examples"
cp -rn /opt/lichess/notebooks/. "$HOME/examples/"

set +e
timeout "${hours}h" jupyter lab --ip=127.0.0.1 --port=8888 --no-browser \
  --ServerApp.root_dir="$HOME" --ServerApp.allow_remote_access=True \
  --IdentityProvider.token='' --ServerApp.password=''
rc=$?
if [ "$rc" -eq 124 ]; then
  echo "JupyterLab stopped after ${hours}h (parameter: hours)"
  exit 0
fi
exit "$rc"
