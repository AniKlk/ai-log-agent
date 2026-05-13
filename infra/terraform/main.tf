# ---------------------------------------------------------------------------
# main.tf — AI Log Agent infrastructure
#
# What this creates (all NEW resources):
#   • Resource group
#   • Azure Container Registry (ACR)
#   • Log Analytics workspace for Container Apps diagnostics
#   • Container Apps environment
#   • User-assigned Managed Identity
#   • Backend Container App  (FastAPI / Python)
#   • Frontend Container App (Next.js)
#
# What it REFERENCES (existing resources — data sources only):
#   • Azure OpenAI account
#   • Cosmos DB account
#   • ProProctor Log Analytics workspace
#   • Infrastructure Log Analytics workspace
#
# Authentication model:
#   The app uses DefaultAzureCredential.  On Container Apps the user-assigned
#   managed identity is used automatically once AZURE_CLIENT_ID is injected.
#   No API keys are stored anywhere — access is granted via RBAC roles below.
# ---------------------------------------------------------------------------

terraform {
  required_version = ">= 1.7.0"

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
}

provider "azurerm" {
  features {}
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------
locals {
  app_name = "ai-log-agent"
  tags = {
    application = local.app_name
    environment = var.environment
    managed_by  = "terraform"
  }
}

# ---------------------------------------------------------------------------
# Resource group
# ---------------------------------------------------------------------------
resource "azurerm_resource_group" "app" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------------------
# Data sources — existing resources
# ---------------------------------------------------------------------------
data "azurerm_cognitive_account" "openai" {
  name                = var.openai_account_name
  resource_group_name = var.openai_resource_group
}

data "azurerm_cosmosdb_account" "main" {
  name                = var.cosmos_account_name
  resource_group_name = var.cosmos_resource_group
}

data "azurerm_log_analytics_workspace" "proproctor" {
  name                = var.proproctor_workspace_name
  resource_group_name = var.proproctor_workspace_resource_group
}

data "azurerm_log_analytics_workspace" "infra" {
  name                = var.infra_workspace_name
  resource_group_name = var.infra_workspace_resource_group
}

# ---------------------------------------------------------------------------
# Azure Container Registry
# ---------------------------------------------------------------------------
resource "azurerm_container_registry" "app" {
  name                = replace("${local.app_name}acr${var.environment}", "-", "")
  resource_group_name = azurerm_resource_group.app.name
  location            = azurerm_resource_group.app.location
  sku                 = "Basic"
  admin_enabled       = false # authentication via managed identity, not admin creds
  tags                = local.tags
}

# ---------------------------------------------------------------------------
# User-assigned Managed Identity
# The identity is shared by both container apps.  All Azure SDK calls
# (Azure Monitor, Azure OpenAI, Cosmos DB) authenticate through it.
# ---------------------------------------------------------------------------
resource "azurerm_user_assigned_identity" "app" {
  name                = "${local.app_name}-identity-${var.environment}"
  resource_group_name = azurerm_resource_group.app.name
  location            = azurerm_resource_group.app.location
  tags                = local.tags
}

# ── ACR pull ────────────────────────────────────────────────────────────────
resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.app.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# ── Azure OpenAI — Cognitive Services OpenAI User ───────────────────────────
# Grants the identity the right to call Azure OpenAI deployments.
# DefaultAzureCredential acquires a token for scope:
#   https://cognitiveservices.azure.com/.default
resource "azurerm_role_assignment" "openai_user" {
  scope                = data.azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# ── Log Analytics — Monitoring Reader on both workspaces ────────────────────
# Required for LogsQueryClient.query_workspace() calls.
resource "azurerm_role_assignment" "logs_proproctor" {
  scope                = data.azurerm_log_analytics_workspace.proproctor.id
  role_definition_name = "Log Analytics Reader"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_role_assignment" "logs_infra" {
  scope                = data.azurerm_log_analytics_workspace.infra.id
  role_definition_name = "Log Analytics Reader"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# ── Cosmos DB — built-in Data Reader (SQL RBAC, not Azure RBAC) ─────────────
# The app uses CosmosClient with DefaultAzureCredential when COSMOS_KEY is
# unset.  This built-in role allows read-only data-plane access.
resource "azurerm_cosmosdb_sql_role_assignment" "data_reader" {
  resource_group_name = var.cosmos_resource_group
  account_name        = var.cosmos_account_name
  # Built-in "Cosmos DB Built-in Data Reader" role definition ID
  role_definition_id  = "${data.azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000001"
  principal_id        = azurerm_user_assigned_identity.app.principal_id
  scope               = data.azurerm_cosmosdb_account.main.id
}

# ---------------------------------------------------------------------------
# Log Analytics workspace for Container Apps diagnostics
# (separate from the application workspaces)
# ---------------------------------------------------------------------------
resource "azurerm_log_analytics_workspace" "aca" {
  name                = "${local.app_name}-aca-logs-${var.environment}"
  resource_group_name = azurerm_resource_group.app.name
  location            = azurerm_resource_group.app.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

# ---------------------------------------------------------------------------
# Container Apps environment
# ---------------------------------------------------------------------------
resource "azurerm_container_app_environment" "app" {
  name                       = "${local.app_name}-env-${var.environment}"
  resource_group_name        = azurerm_resource_group.app.name
  location                   = azurerm_resource_group.app.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.aca.id
  tags                       = local.tags
}

# ---------------------------------------------------------------------------
# Backend Container App — FastAPI / Python 3.12
# ---------------------------------------------------------------------------
resource "azurerm_container_app" "backend" {
  name                         = "${local.app_name}-backend-${var.environment}"
  resource_group_name          = azurerm_resource_group.app.name
  container_app_environment_id = azurerm_container_app_environment.app.id
  revision_mode                = "Single"
  tags                         = local.tags

  # Attach the user-assigned managed identity
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  # Allow the Container App to pull images from ACR via the managed identity
  registry {
    server   = azurerm_container_registry.app.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "http"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = var.backend_min_replicas
    max_replicas = var.backend_max_replicas

    container {
      name   = "backend"
      image  = "${azurerm_container_registry.app.login_server}/ai-log-agent/backend:${var.backend_image_tag}"
      cpu    = 1.0
      memory = "2Gi"

      # ── Identity — tells DefaultAzureCredential which managed identity to use
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.app.client_id
      }

      # ── Azure OpenAI ──────────────────────────────────────────────────────
      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        value = data.azurerm_cognitive_account.openai.endpoint
      }
      env {
        name  = "AZURE_OPENAI_DEPLOYMENT"
        value = var.openai_deployment_name
      }
      env {
        name  = "AZURE_OPENAI_API_VERSION"
        value = var.openai_api_version
      }

      # ── Log Analytics workspace IDs ───────────────────────────────────────
      env {
        name  = "PROPROCTOR_WORKSPACE_ID"
        value = data.azurerm_log_analytics_workspace.proproctor.workspace_id
      }
      env {
        name  = "INFRA_WORKSPACE_ID"
        value = data.azurerm_log_analytics_workspace.infra.workspace_id
      }

      # ── Cosmos DB ─────────────────────────────────────────────────────────
      env {
        name  = "COSMOS_ENDPOINT"
        value = data.azurerm_cosmosdb_account.main.endpoint
      }
      # COSMOS_KEY is intentionally omitted — the app falls back to
      # DefaultAzureCredential when this is unset, using the managed identity.

      # ── App settings ──────────────────────────────────────────────────────
      env {
        name  = "CORS_ORIGINS"
        value = "https://${azurerm_container_app.frontend.ingress[0].fqdn}"
      }
      env {
        name  = "ANALYZE_TIMEOUT_SECONDS"
        value = tostring(var.analyze_timeout_seconds)
      }
      env {
        name  = "MAX_AGENT_ITERATIONS"
        value = tostring(var.max_agent_iterations)
      }
      env {
        name  = "TOOL_RESPONSE_MAX_TOKENS"
        value = tostring(var.tool_response_max_tokens)
      }
      env {
        name  = "LOG_LEVEL"
        value = "INFO"
      }

      liveness_probe {
        path      = "/health"
        port      = 8000
        transport = "HTTP"
        # Give the app time to acquire an initial Azure AD token on startup
        initial_delay    = 15
        interval_seconds = 30
        failure_count_threshold = 3
      }

      readiness_probe {
        path      = "/health"
        port      = 8000
        transport = "HTTP"
        initial_delay    = 10
        interval_seconds = 10
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.acr_pull,
    azurerm_role_assignment.openai_user,
    azurerm_role_assignment.logs_proproctor,
    azurerm_role_assignment.logs_infra,
    azurerm_cosmosdb_sql_role_assignment.data_reader,
  ]
}

# ---------------------------------------------------------------------------
# Frontend Container App — Next.js 14 standalone
#
# NEXT_PUBLIC_API_URL is a build-time variable baked into the Next.js bundle.
# You must build the frontend image AFTER the backend FQDN is known, passing:
#   --build-arg NEXT_PUBLIC_API_URL=https://<backend-fqdn>
# See outputs.tf for the backend URL, and the example CI snippet in the README.
# ---------------------------------------------------------------------------
resource "azurerm_container_app" "frontend" {
  name                         = "${local.app_name}-frontend-${var.environment}"
  resource_group_name          = azurerm_resource_group.app.name
  container_app_environment_id = azurerm_container_app_environment.app.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.app.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  ingress {
    external_enabled = true
    target_port      = 3000
    transport        = "http"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = var.frontend_min_replicas
    max_replicas = var.frontend_max_replicas

    container {
      name   = "frontend"
      image  = "${azurerm_container_registry.app.login_server}/ai-log-agent/frontend:${var.frontend_image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "NODE_ENV"
        value = "production"
      }
      # NEXT_PUBLIC_API_URL is baked in at build time (see Dockerfile ARG).
      # The runtime value here is informational only — Next.js will use whatever
      # was set during `docker build`.
      env {
        name  = "NEXT_PUBLIC_API_URL"
        value = "https://${azurerm_container_app.backend.ingress[0].fqdn}"
      }
    }
  }

  depends_on = [azurerm_role_assignment.acr_pull]
}
