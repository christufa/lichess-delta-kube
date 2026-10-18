"""Turn a Trivy JSON report into a job summary table and one annotation per finding.
Exits 1 if there are findings (Trivy already filtered to fixable criticals and applied .trivyignore)."""
import json
import os
import sys

report = json.load(open(sys.argv[1]))
image = sys.argv[2]
rows = []
for result in report.get("Results", []):
    for v in result.get("Vulnerabilities") or []:
        rows.append((v["VulnerabilityID"], v.get("PkgName", ""), v.get("InstalledVersion", ""),
                     v.get("FixedVersion", ""), result.get("Target", ""), (v.get("Title") or "")[:100]))

with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a") as out:
    out.write(f"### Trivy: {image}\n\n")
    if not rows:
        out.write("No fixable critical vulnerabilities.\n")
    else:
        out.write("| ID | package | installed | fixed in | where | title |\n|---|---|---|---|---|---|\n")
        for r in rows:
            out.write("| " + " | ".join(x.replace("|", "/") for x in r) + " |\n")
for vid, pkg, inst, fixed, target, title in rows:
    print(f"::error title={vid} in {pkg}::{pkg} {inst} → {fixed} ({target}) {title}")
print(f"{len(rows)} fixable critical vulnerabilities in {image}")
sys.exit(1 if rows else 0)
