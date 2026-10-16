# infra

Terraform for the Lichess platform, in four stacks applied in order. Each has its
own state; `platform` and `compute` keep theirs in the account `bootstrap` creates.

| Stack | Creates | State |
|---|---|---|
| `bootstrap/` | State storage account, subscription budget | Local file `bootstrap/terraform.tfstate`; **back it up** |
| `platform/` | Lake (ADLS Gen2), VNet and AKS subnet | `platform.tfstate` |
| `compute/` | ACR, AKS (system + spot batch pool), managed identities | `compute.tfstate` |
| `gitops/` | Argo CD + root app, `lichess` namespace / service account / config (needs `kubelogin`) | `gitops.tfstate` |

First time only:

```powershell
cd infra
copy state.tfbackend.example state.tfbackend         # values from: terraform -chdir=bootstrap output
copy <stack>\terraform.tfvars.example <stack>\terraform.tfvars
```

Then per stack:

```powershell
cd infra\<stack>
terraform init                                     # bootstrap
terraform init "-backend-config=../state.tfbackend"  # platform, compute, gitops
terraform plan -out main.tfplan
terraform apply main.tfplan
```

**This repo is public.** Names and IDs specific to the deployment (subscription,
state account, IPs, emails) live only in gitignored files: `*.tfvars`,
`state.tfbackend`, state and plans. Don't paste `terraform plan` output into
issues or PRs; summarise it instead.

## Firewalls and your IP

Terraform owns the firewall design: deny by default, no trusted-service bypass,
the AKS subnet allowed on the lake. **It never sets or reads IP rules.** You add
your IP by hand, and changes you make in Azure never show up as drift.

Terraform manages storage only through Azure Resource Manager
(`data_plane_available = false`), which the firewalls don't block, so applies
work even before your IP is added. Reading and writing **state** does go through
the firewall, so the state account needs your IP before `platform`/`compute`.

Where to add your IP (portal):

| Resource | Portal page | When |
|---|---|---|
| State account (`st<project>tf…`) | Storage account → Networking → Firewall → Add your client IP | **Before** applying the bootstrap change that turns on Deny |
| Lake (`st<project>lake…`) | Same | Right after `platform` is applied |
| AKS `aks-lichess` | Kubernetes service → Networking → Set authorized IP ranges | Right after `compute` is applied; until then its API answers any IP (Entra ID sign-in still required) |

When your IP changes, update the same three places. Signs of a stale IP:
`terraform init/plan` fails reading state (403), ingest uploads fail with
`AuthorizationFailure`, `kubectl` times out.

## Hard limits on spend

The budget only sends emails. What actually caps cost:

- `batch_max_nodes` on the spot pool (`compute/variables.tf`)
- vCPU quotas: `az vm list-usage --location eastus2 -o table`
- `az aks stop -g rg-lichess-compute -n aks-lichess` when the cluster isn't needed
