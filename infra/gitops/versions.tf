terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.3"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.3"
    }
  }

  # Partial backend config, like platform and compute:
  #   terraform init "-backend-config=../state.tfbackend"
  backend "azurerm" {
    key = "gitops.tfstate"
  }
}
