variable "subscription_id" {
  description = "Azure subscription to deploy into"
  type        = string
}

variable "location" {
  description = "Azure region for all platform resources"
  type        = string
  default     = "eastus2"
}

variable "project" {
  description = "Short name used in resource names. Lowercase letters and digits only (storage account names allow nothing else)"
  type        = string
  default     = "lichess"

  validation {
    condition     = can(regex("^[a-z0-9]{3,8}$", var.project))
    error_message = "project must be 3-8 lowercase letters or digits."
  }
}

variable "soft_delete_days" {
  description = "Days deleted blobs and containers in the lake can be recovered"
  type        = number
  default     = 7
}

variable "staging_retention_days" {
  description = "Days to keep landed Parquet (bootstrap/raw/games/) before the lifecycle rule deletes it; months are published into lake.raw.games long before"
  type        = number
  default     = 14
}
