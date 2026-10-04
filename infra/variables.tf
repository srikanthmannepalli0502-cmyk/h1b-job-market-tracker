variable "subscription_id" {
  type = string
}

variable "location" {
  type    = string
  default = "eastus"
}

variable "resource_group_name" {
  type    = string
  default = "rg-h1b-tracker"
}

variable "prefix" {
  description = "Lowercase letters/digits used in resource names (storage names max 24 chars)."
  type        = string
  default     = "h1b"

  validation {
    condition     = can(regex("^[a-z0-9]{1,8}$", var.prefix))
    error_message = "prefix must be 1-8 lowercase letters or digits."
  }
}

variable "github_identity_name" {
  description = "User-assigned identity GitHub Actions logs in as (created by cloud-resume-azure's bootstrap)."
  type        = string
  default     = "id-cloudresume-github"
}

variable "github_identity_resource_group" {
  type    = string
  default = "rg-cloudresume-tfstate"
}

variable "function_memory_mb" {
  description = "Instance memory for the ingestion Function. Large DOL workbooks (~250 MB) are parsed in memory."
  type        = number
  default     = 4096
}

variable "tags" {
  type = map(string)
  default = {
    project = "h1b-job-market-tracker"
  }
}

# --- "Ask the data" assistant ---

variable "openai_location" {
  type    = string
  default = "eastus"
}

variable "openai_model" {
  description = "Chat model for question -> SQL. Must have GlobalStandard quota in the subscription."
  type        = string
  default     = "gpt-5-mini"
}

variable "openai_model_version" {
  type    = string
  default = "2025-08-07"
}

variable "openai_capacity_k_tpm" {
  description = "Deployment throughput in thousands of tokens per minute (hard cap on spend rate)."
  type        = number
  default     = 10
}

variable "daily_question_limit" {
  description = "Questions answered per UTC day across all users; further questions get HTTP 429."
  type        = number
  default     = 300
}

variable "dashboard_origin" {
  description = "Origin of the public dashboard (CORS for the ask API)."
  type        = string
  default     = "https://stcloudresumeweby4ajk1.z13.web.core.windows.net"
}

variable "extra_ask_origins" {
  description = "Extra CORS origins, e.g. a local preview server."
  type        = list(string)
  default     = ["http://127.0.0.1:8765", "http://localhost:8765"]
}
