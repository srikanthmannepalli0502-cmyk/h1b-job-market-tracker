# "Ask the data": Azure OpenAI + a separate, public Function App that turns questions
# into read-only SQL over the curated gold tables.
#
# Kept apart from the ingestion Function on purpose: this one is anonymous and
# reachable from the internet, so its identity can only read gold tables, write its
# own usage counter, and call the model. It cannot touch raw or bronze data.

resource "azurerm_cognitive_account" "openai" {
  name                  = "oai-${var.prefix}-${local.suffix}"
  resource_group_name   = local.rg
  location              = var.openai_location
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "oai-${var.prefix}-${local.suffix}"
  local_auth_enabled    = false # Entra ID (managed identity) only, no API keys
  tags                  = var.tags
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.openai_model
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = var.openai_model
    version = var.openai_model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.openai_capacity_k_tpm # thousands of tokens per minute: a hard throughput cap
  }
}

resource "azurerm_storage_container" "ask_deployments" {
  name                  = "deployments-ask"
  storage_account_id    = azurerm_storage_account.func.id
  container_access_type = "private"
}

resource "azurerm_service_plan" "ask" {
  name                = "asp-${var.prefix}-ask-${local.suffix}"
  resource_group_name = local.rg
  location            = var.location
  os_type             = "Linux"
  sku_name            = "FC1"
  tags                = var.tags
}

resource "azurerm_function_app_flex_consumption" "ask" {
  name                = "func-${var.prefix}-ask-${local.suffix}"
  resource_group_name = local.rg
  location            = var.location
  service_plan_id     = azurerm_service_plan.ask.id
  tags                = var.tags

  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.func.primary_blob_endpoint}${azurerm_storage_container.ask_deployments.name}"
  storage_authentication_type = "StorageAccountConnectionString"
  storage_access_key          = azurerm_storage_account.func.primary_access_key

  runtime_name           = "python"
  runtime_version        = "3.12"
  instance_memory_in_mb  = 2048
  maximum_instance_count = 40

  identity {
    type = "SystemAssigned"
  }

  app_settings = {
    LAKE_BLOB_ENDPOINT   = azurerm_storage_account.lake.primary_blob_endpoint
    GOLD_CONTAINER       = "gold"
    USAGE_CONTAINER      = "usage"
    OPENAI_ENDPOINT      = azurerm_cognitive_account.openai.endpoint
    OPENAI_DEPLOYMENT    = azurerm_cognitive_deployment.chat.name
    DAILY_QUESTION_LIMIT = tostring(var.daily_question_limit)
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string

    cors {
      # Browsers may call the API only from the dashboard's site.
      allowed_origins = concat([trimsuffix(var.dashboard_origin, "/")], var.extra_ask_origins)
    }
  }
}

# ---------------------------------------------------------------------------
# Access (least privilege)
# ---------------------------------------------------------------------------

resource "azurerm_role_assignment" "ask_gold_read" {
  scope                = azurerm_storage_container.lake["gold"].id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_function_app_flex_consumption.ask.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "ask_usage_write" {
  scope                = azurerm_storage_container.lake["usage"].id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_function_app_flex_consumption.ask.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "ask_openai" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_function_app_flex_consumption.ask.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# GitHub Actions: publish gold tables after each rebuild, and deploy the ask Function.
resource "azurerm_role_assignment" "github_gold_write" {
  scope                = azurerm_storage_container.lake["gold"].id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_user_assigned_identity.github.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "github_ask_deploy" {
  scope                = azurerm_function_app_flex_consumption.ask.id
  role_definition_name = "Website Contributor"
  principal_id         = data.azurerm_user_assigned_identity.github.principal_id
  principal_type       = "ServicePrincipal"
}

# You: test the assistant locally with your own az login.
resource "azurerm_role_assignment" "operator_openai" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = data.azurerm_client_config.current.object_id
}
