#!/usr/bin/env bash
# Record this main build's long-running service images on the deploy-state branch, one
# Helm values file per app (image.digest + image.commit). deploy/apps/chess-{api,web}.yaml
# read them with a git files generator, so a new signed build is a manifest change Argo CD syncs.
# Called by images.yml (job release) from a full-history checkout of main.
#
#   env: CHESS_API, CHESS_WEB, CHESS_MCP, CHESS_CHAT  sha256:… digest that build pushed and signed; empty when
#                              that image wasn't published (its file is left as it was)
#
# deploy-state isn't covered by main's ruleset (PRs + review), so GITHUB_TOKEN can push.
# Only fast-forward commits from here; nothing in git decides what may run: Kyverno still
# admits only images signed by images.yml on main.
set -euo pipefail
branch=deploy-state

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

if git fetch --depth=1 origin "$branch"; then
  git switch --detach FETCH_HEAD
else
  echo "no $branch branch yet: starting it"
  git switch --orphan "$branch"
  cat > README.md <<'EOF'
# deploy-state

Written only by `.github/workflows/images.yml` (job `release`): the digest and commit of
the newest signed main build of each long-running service, as Helm values for
`deploy/charts/<app>`. Argo CD reads these files next to the chart (see `deploy/README.md`,
"Rolling out new builds"). Don't edit by hand except to roll back (`git revert`).
EOF
fi

changed=()
for app in chess-api chess-web chess-mcp chess-chat; do
  var=${app^^}; var=${var//-/_}
  digest=${!var:-}
  [ -n "$digest" ] || { echo "$app: not published in this run, unchanged"; continue; }
  [[ $digest =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "::error::$app: bad digest '$digest'"; exit 1; }

  # A re-run of an older workflow must not roll back a newer release.
  prev=$(sed -n 's/^  commit: *//p' "$app.yaml" 2>/dev/null || true)
  if [ -n "$prev" ] && [ "$prev" != "$GITHUB_SHA" ] && ! git merge-base --is-ancestor "$prev" "$GITHUB_SHA"; then
    echo "::warning::$app: deploy-state already has $prev, which is not an ancestor of $GITHUB_SHA; skipped"
    continue
  fi

  cat > "$app.yaml" <<EOF
# Written by .github/workflows/images.yml for main commit $GITHUB_SHA. Don't edit.
# Helm values for deploy/charts/$app (deploy/README.md, "Rolling out new builds").
image:
  digest: $digest
  commit: $GITHUB_SHA
EOF
  changed+=("$app")
done

git add -A
if git diff --cached --quiet; then
  echo "deploy-state is already up to date"
  exit 0
fi
git commit -q -m "release ${changed[*]} from ${GITHUB_SHA::12}" \
  -m "${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
git push origin "HEAD:refs/heads/$branch"
{
  echo "### deploy-state"
  for app in "${changed[@]}"; do echo "- $app → \`${GITHUB_SHA::12}\`"; done
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
