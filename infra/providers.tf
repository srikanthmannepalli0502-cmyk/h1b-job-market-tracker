terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.30"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Shares the state storage account created by the cloud-resume-azure bootstrap,
  # under its own key. Values: infra/backend.hcl (see backend.hcl.example).
  backend "azurerm" {
    use_azuread_auth = true
  }
}

provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = true
    }
  }
  subscription_id                 = var.subscription_id
  resource_provider_registrations = "none"
  # The lake has shared-key access disabled, so data-plane calls must use Entra ID.
  storage_use_azuread = true
}
