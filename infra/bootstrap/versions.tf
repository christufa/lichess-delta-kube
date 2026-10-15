# Syntax: the outer block is optional; at most one per module is the norm
# (multiple are merged, but backend/cloud may appear only once overall).
# [terraform {
#
#   # Terraform CLI version constraint, e.g. ">= 1.9" or "~> 1.9.0".
#   [required_version = "<VERSION_CONSTRAINT>"]
#
#   # Providers this module needs; one entry per provider local name.
#   [required_providers {
#     <PROVIDER_NAME> = {
#       source  = "[<HOSTNAME>/]<NAMESPACE>/<TYPE>"      # e.g. "hashicorp/azurerm"
#       [version = "<VERSION_CONSTRAINT>"]                # e.g. "~> 4.0"
#       [configuration_aliases = [ <PROVIDER_NAME>.<ALIAS>+ ]]   # modules only
#     }+
#   }]
#
#   # Metadata a provider can receive from this module (rare; provider-specific).
#   [provider_meta "<PROVIDER_NAME>" {
#     <PROVIDER_DEFINED_ARGUMENTS>
#   }]+
#
#   # Where state lives: EITHER a backend OR cloud, never both. Omit both = local state.
#   [backend "<BACKEND_TYPE>" {                           # local | azurerm | s3 | gcs | http | …
#     <BACKEND_ARGUMENTS>
#   }
#   |
#   cloud {
#     [organization = "<ORG_NAME>"]                       # required for HCP Terraform
#     [hostname     = "<HOSTNAME>"]                       # default "app.terraform.io"
#     [token        = "<API_TOKEN>"]                      # prefer `terraform login` instead
#     workspaces {                                        # required for HCP Terraform
#       ( name = "<WORKSPACE_NAME>"                       # one workspace
#       | tags = ["<TAG>"+] | { <KEY> = "<VALUE>" }+ )    # or select workspaces by tag
#       [project = "<PROJECT_NAME>"]
#     }
#   }]
#
#   # Opt-in language experiments (only in alpha/dev builds; avoid).
#   [experiments = [ <EXPERIMENT_NAME>+ ]]
# }]
terraform {
  # Terraform will error if you attempt to use this
  # configuration with a more recent version than specified
  # here. 
  # https://developer.hashicorp.com/terraform/tutorials/configuration-language/versions
  required_version = ">= 1.9"

  # The Terraform CLI uses the required_providers block to
  # download the listed providers and compute provider lock
  # hashes.
  # https://developer.hashicorp.com/terraform/language/block/stack/tfcomponent/required_providers
  # Syntax: the outer block is optional; include one or 
  # more provider entries.
  # [required_providers {
  #   <PROVIDER_NAME> = {
  #     source  = "<SOURCE>"
  #     [version = "<VERSION>"]
  #   }+
  # }]
  required_providers {
    # The azurerm provider is used to manage resources in Microsoft Azure.
    # https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    # The random provider is used to generate random values for
    # resource names and other values. It is not required for
    # all Terraform configurations, but it is commonly used in
    # many examples.
    # https://registry.terraform.io/providers/hashicorp/random/latest/docs
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}