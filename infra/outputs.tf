output "lake_account" {
  description = "Set as the LAKE_ACCOUNT GitHub variable."
  value       = azurerm_storage_account.lake.name
}

output "lake_blob_endpoint" {
  value = azurerm_storage_account.lake.primary_blob_endpoint
}

output "function_app_name" {
  description = "Set as the FUNCTION_APP_NAME GitHub variable."
  value       = azurerm_function_app_flex_consumption.ingest.name
}

output "function_process_url" {
  description = "POST here (with a function key) to process new raw files immediately."
  value       = "https://${azurerm_function_app_flex_consumption.ingest.default_hostname}/api/process"
}

output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "ask_function_app_name" {
  description = "Set as the ASK_FUNCTION_APP_NAME GitHub variable."
  value       = azurerm_function_app_flex_consumption.ask.name
}

output "ask_api_url" {
  value = "https://${azurerm_function_app_flex_consumption.ask.default_hostname}/api/ask"
}

output "openai_endpoint" {
  value = azurerm_cognitive_account.openai.endpoint
}
