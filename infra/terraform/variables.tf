# ---------------------------------------------------------------------------
# variables.tf — AI Log Agent infrastructure
# ---------------------------------------------------------------------------

# ── Deployment ──────────────────────────────────────────────────────────────
variable "location" {
  description = "Azure region for all new resources."
  type        = string
  default     = "eastus"
}

variable "environment" {
  description = "Deployment environment tag (dev | staging | prod)."
  type        = string
  default     = "dev"
}

variable "resource_group_name" {
  description = "Name of the resource group to create for the AI Log Agent app resources."
  type        = string
  default     = "ai-log-agent-rg"
}

# ── Existing resources (data source lookups) ─────────────────────────────────
variable "openai_resource_group" {
  description = "Resource group that contains the existing Azure OpenAI account."
  type        = string
}

variable "openai_account_name" {
  description = "Name of the existing Azure OpenAI account (e.g. proproctor-investigator-resource)."
  type        = string
}

variable "cosmos_resource_group" {
  description = "Resource group that contains the existing Cosmos DB account."
  type        = string
}

variable "cosmos_account_name" {
  description = "Name of the existing Cosmos DB account."
  type        = string
}

variable "proproctor_workspace_resource_group" {
  description = "Resource group that contains the ProProctor Log Analytics workspace."
  type        = string
}

variable "proproctor_workspace_name" {
  description = "Name of the ProProctor Log Analytics workspace."
  type        = string
}

variable "infra_workspace_resource_group" {
  description = "Resource group that contains the infrastructure Log Analytics workspace."
  type        = string
}

variable "infra_workspace_name" {
  description = "Name of the infrastructure Log Analytics workspace."
  type        = string
}

# ── Azure OpenAI settings ────────────────────────────────────────────────────
variable "openai_deployment_name" {
  description = "Azure OpenAI model deployment name (e.g. gpt-4o)."
  type        = string
  default     = "gpt-4o"
}

variable "openai_api_version" {
  description = "Azure OpenAI API version."
  type        = string
  default     = "2024-12-01-preview"
}

# ── Container image tags ─────────────────────────────────────────────────────
variable "backend_image_tag" {
  description = "Docker image tag for the backend container."
  type        = string
  default     = "latest"
}

variable "frontend_image_tag" {
  description = "Docker image tag for the frontend container."
  type        = string
  default     = "latest"
}

# ── Container App sizing ─────────────────────────────────────────────────────
variable "backend_min_replicas" {
  description = "Minimum replica count for the backend Container App."
  type        = number
  default     = 1
}

variable "backend_max_replicas" {
  description = "Maximum replica count for the backend Container App."
  type        = number
  default     = 3
}

variable "frontend_min_replicas" {
  description = "Minimum replica count for the frontend Container App."
  type        = number
  default     = 1
}

variable "frontend_max_replicas" {
  description = "Maximum replica count for the frontend Container App."
  type        = number
  default     = 3
}

# ── App settings ─────────────────────────────────────────────────────────────
variable "analyze_timeout_seconds" {
  description = "Timeout in seconds for the /analyze endpoint."
  type        = number
  default     = 600
}

variable "max_agent_iterations" {
  description = "Maximum LLM agent iterations per request."
  type        = number
  default     = 10
}

variable "tool_response_max_tokens" {
  description = "Token cap applied to tool responses before sending to the LLM."
  type        = number
  default     = 40000
}
