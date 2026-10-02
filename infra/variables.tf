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
