variable "subscription_id" {
  description = "Azure subscription the cluster is in"
  type        = string
}

# ---- Where platform's and compute's state live (same values as ../state.tfbackend) ----

variable "state_resource_group_name" {
  description = "Resource group of the Terraform state account (bootstrap output)"
  type        = string
}

variable "state_storage_account_name" {
  description = "Terraform state account (bootstrap output)"
  type        = string
}

variable "state_container_name" {
  description = "Container holding the state files"
  type        = string
  default     = "tfstate"
}

# ---- GitOps ----

variable "repo_url" {
  description = "Git repository Argo CD syncs from (public, so no credentials)"
  type        = string
  default     = "https://github.com/christufa/lichess-delta-kube.git"
}

variable "repo_revision" {
  description = "Branch, tag or commit Argo CD follows"
  type        = string
  default     = "main"
}

variable "argocd_chart_version" {
  description = "argo-cd Helm chart version (https://github.com/argoproj/argo-helm)"
  type        = string
  default     = "10.10.3" # Argo CD v3.5.5
}

variable "argocd_apps_chart_version" {
  description = "argocd-apps Helm chart version, used to create the root Application"
  type        = string
  default     = "2.0.6"
}

# ---- Public entry (Cloudflare Tunnel + Access), #45 ----

variable "domain" {
  description = "Cloudflare zone the chess-* hostnames live in"
  type        = string
  default     = "generops.dev"
}

variable "hostname_prefix" {
  description = "Prefix for every public hostname: <prefix>, <prefix>-cd, <prefix>-flows, …"
  type        = string
  default     = "chess"
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID (zone Overview page, right sidebar)"
  type        = string
}

variable "cloudflare_zone_id" {
  description = "Zone ID of var.domain (zone Overview page, right sidebar)"
  type        = string
}

variable "cloudflare_team_name" {
  description = "Zero Trust team name: <team>.cloudflareaccess.com"
  type        = string
}

variable "access_allowed_emails" {
  description = "Who may sign in to the private hostnames (Argo CD, Argo Workflows, JupyterLab): your Entra sign-in email"
  type        = list(string)
}

variable "argocd_admin_enabled" {
  description = "Keep Argo CD's local admin account. Set to false once Microsoft sign-in works (kubectl access remains the way back in)"
  type        = bool
  default     = true
}


variable "mcp_extra_users" {
  description = "Entra sign-in names (UPNs) besides you who may use the MCP server and the chat (app roles Lakehouse.Query and Chat.User, #68)"
  type        = list(string)
  default     = []
}
