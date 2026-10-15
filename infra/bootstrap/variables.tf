
variable "subscription_id" {
  description = "Azure subscription to deploy into"
  type        = string
}

variable "location" {
  type    = string
  default = "eastus2"
}

variable "project" {
  type    = string
  default = "lichess"
}

variable "budget_amount" {
  description = "Monthly budget for the whole subscription, in your billing currency"
  type        = number
  default     = 100
}

variable "budget_emails" {
  description = "Who gets the budget alerts"
  type        = list(string)
}

variable "budget_start_date" {
  description = "First month the budget applies to (must be the 1st of a month, UTC)"
  type        = string
  default     = "2026-10-01T00:00:00Z"
}
