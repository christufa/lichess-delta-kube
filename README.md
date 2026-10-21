# deploy-state

Written only by `.github/workflows/images.yml` (job `release`): the digest and commit of
the newest signed main build of each long-running service, as Helm values for
`deploy/charts/<app>`. Argo CD reads these files next to the chart (see `deploy/README.md`,
"Rolling out new builds"). Don't edit by hand except to roll back (`git revert`).
