data "azurerm_client_config" "current" {}

data "azurerm_user_assigned_identity" "github" {
  name                = var.github_identity_name
  resource_group_name = var.github_identity_resource_group
}

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

locals {
  rg     = azurerm_resource_group.main.name
  suffix = random_string.suffix.result
}

# ---------------------------------------------------------------------------
# Data lake (ADLS Gen2): raw workbooks and bronze Parquet
# ---------------------------------------------------------------------------

resource "azurerm_storage_account" "lake" {
  name                     = "st${var.prefix}lake${local.suffix}"
  resource_group_name      = local.rg
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"
  is_hns_enabled           = true # hierarchical namespace = ADLS Gen2
  min_tls_version          = "TLS1_2"

  # Entra ID (RBAC) only: no account keys or SAS tokens can be used against the lake.
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  default_to_oauth_authentication = true

  blob_properties {
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }

  tags = var.tags
}

resource "azurerm_storage_container" "lake" {
  for_each              = toset(["raw", "bronze"])
  name                  = each.key
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

# ---------------------------------------------------------------------------
# Ingestion Function (Flex Consumption, Python)
# ---------------------------------------------------------------------------

# Host storage for the Function runtime (timers, deployment package). Kept
# separate from the lake so the lake can stay key-less.
resource "azurerm_storage_account" "func" {
  name                     = "st${var.prefix}fn${local.suffix}"
  resource_group_name      = local.rg
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
  tags                     = var.tags
}

resource "azurerm_storage_container" "deployments" {
  name                  = "deployments"
  storage_account_id    = azurerm_storage_account.func.id
  container_access_type = "private"
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${var.prefix}-${local.suffix}"
  resource_group_name = local.rg
  location            = var.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 0.1 # hard cap on log ingestion cost
  tags                = var.tags
}

resource "azurerm_application_insights" "main" {
  name                = "appi-${var.prefix}-${local.suffix}"
  resource_group_name = local.rg
  location            = var.location
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = var.tags
}

resource "azurerm_service_plan" "func" {
  name                = "asp-${var.prefix}-${local.suffix}"
  resource_group_name = local.rg
  location            = var.location
  os_type             = "Linux"
  sku_name            = "FC1"
  tags                = var.tags
}

resource "azurerm_function_app_flex_consumption" "ingest" {
  name                = "func-${var.prefix}-ingest-${local.suffix}"
  resource_group_name = local.rg
  location            = var.location
  service_plan_id     = azurerm_service_plan.func.id
  tags                = var.tags

  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.func.primary_blob_endpoint}${azurerm_storage_container.deployments.name}"
  storage_authentication_type = "StorageAccountConnectionString"
  storage_access_key          = azurerm_storage_account.func.primary_access_key

  runtime_name           = "python"
  runtime_version        = "3.12"
  instance_memory_in_mb  = var.function_memory_mb
  maximum_instance_count = 40

  identity {
    type = "SystemAssigned"
  }

  app_settings = {
    LAKE_BLOB_ENDPOINT = azurerm_storage_account.lake.primary_blob_endpoint
    RAW_CONTAINER      = "raw"
    BRONZE_CONTAINER   = "bronze"
    # $HOME is read-only on Flex Consumption; DuckDB installs its excel extension here.
    DUCKDB_EXTENSION_DIR = "/tmp/duckdb_extensions"
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
  }
}

# ---------------------------------------------------------------------------
# Access (least privilege)
# ---------------------------------------------------------------------------

# Function: read raw, write bronze + manifests.
resource "azurerm_role_assignment" "func_lake" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_function_app_flex_consumption.ingest.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# GitHub Actions: read bronze for dbt builds (read-only).
resource "azurerm_role_assignment" "github_lake_read" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = data.azurerm_user_assigned_identity.github.principal_id
  principal_type       = "ServicePrincipal"
}

# GitHub Actions: deploy code to the Function App only.
resource "azurerm_role_assignment" "github_func_deploy" {
  scope                = azurerm_function_app_flex_consumption.ingest.id
  role_definition_name = "Website Contributor"
  principal_id         = data.azurerm_user_assigned_identity.github.principal_id
  principal_type       = "ServicePrincipal"
}

# You (whoever runs terraform): upload raw workbooks with scripts/upload_raw.py.
resource "azurerm_role_assignment" "operator_lake" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}
