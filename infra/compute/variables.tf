variable "subscription_id" {
  description = "Azure subscription to deploy into"
  type        = string
}

variable "project" {
  description = "Short name used in resource names; must match platform"
  type        = string
  default     = "lichess"
}

variable "system_vm_size" {
  description = "VM size for the always-on system node: cluster add-ons plus Argo CD and Argo Workflows"
  type        = string
  default     = "Standard_D4as_v5" # 4 vCPU / 16 GB. Resizing rotates the pool via temporary_name_for_rotation
}

variable "system_max_nodes" {
  description = "Upper limit for the autoscaling system pool. Needs (max + 1 for node-image rotation) x 4 vCPU of DASv5 quota: 16 for 3"
  type        = number
  default     = 3
}

variable "batch_vm_size" {
  description = "VM size for the spot batch pool (ingest jobs now, Spark executors later)"
  type        = string
  default     = "Standard_E8s_v5" # 8 vCPU / 64 GB, memory-optimised for joins. Not every size is offered
  # to every subscription: az aks / activity log errors list the allowed ones
}

variable "batch_max_nodes" {
  description = "Upper limit for the batch pool (4 × 8 vCPU = the 32 spot vCPU quota). It scales to 0 when idle"
  type        = number
  default     = 4
}

variable "batch_os_disk_gb" {
  description = "OS disk per batch node; holds ingest scratch (a month's .pgn.zst is up to ~35 GB)"
  type        = number
  default     = 256
}

variable "jobs_namespace" {
  description = "Kubernetes namespace the pipeline jobs run in"
  type        = string
  default     = "lichess"
}

variable "jobs_service_account" {
  description = "Kubernetes service account the jobs use; it is federated to the jobs managed identity"
  type        = string
  default     = "lichess-jobs"
}

# ---- Where platform's state lives (same values as ../state.tfbackend) ----

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

variable "github_oidc_repository" {
  description = <<-EOT
    The repository as it appears in GitHub OIDC subjects. This repo uses GitHub's
    immutable-ID format, owner@<owner id>/repo@<repo id>, so a renamed or re-created
    repository can't inherit the trust. The IDs are public (GitHub API). If sign-in
    fails, the images workflow annotates the subject GitHub actually presents.
  EOT
  type        = string
  default     = "christufa@215463783/lichess-delta-kube@1253954100"
}

variable "eso_namespace" {
  description = "Namespace of External Secrets Operator"
  type        = string
  default     = "external-secrets"
}

variable "eso_service_account" {
  description = "Service account the Key Vault ClusterSecretStore signs in as (federated to id-<project>-eso)"
  type        = string
  default     = "eso-azure-kv"
}

variable "alert_email" {
  description = "Where monitoring alerts are emailed (kept in terraform.tfvars, not in git)"
  type        = string
}

