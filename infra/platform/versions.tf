terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Partial backend config: the state account's name is kept out of this public repo.
  # The rest comes from ../state.tfbackend (gitignored; copy state.tfbackend.example):
  #   terraform init -backend-config=../state.tfbackend
  # https://developer.hashicorp.com/terraform/language/backend#partial-configuration
  backend "azurerm" {
    key = "platform.tfstate"
  }
}
