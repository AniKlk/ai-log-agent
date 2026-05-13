# ---------------------------------------------------------------------------
# outputs.tf — AI Log Agent infrastructure
# ---------------------------------------------------------------------------

output "resource_group_name" {
  description = "Resource group containing the deployed app resources."
  value       = azurerm_resource_group.app.name
}

output "acr_login_server" {
  description = "Login server for the Azure Container Registry."
  value       = azurerm_container_registry.app.login_server
}

output "backend_fqdn" {
  description = "Fully-qualified domain name of the backend Container App. Use this as NEXT_PUBLIC_API_URL when building the frontend image."
  value       = "https://${azurerm_container_app.backend.ingress[0].fqdn}"
}

output "frontend_fqdn" {
  description = "Fully-qualified domain name of the frontend Container App (public URL)."
  value       = "https://${azurerm_container_app.frontend.ingress[0].fqdn}"
}

output "managed_identity_client_id" {
  description = "Client ID of the user-assigned managed identity. Set as AZURE_CLIENT_ID in any additional deployments."
  value       = azurerm_user_assigned_identity.app.client_id
}

output "managed_identity_principal_id" {
  description = "Principal ID of the user-assigned managed identity (used for additional role assignments)."
  value       = azurerm_user_assigned_identity.app.principal_id
}

output "backend_push_command" {
  description = "Docker push command for the backend image."
  value       = "docker push ${azurerm_container_registry.app.login_server}/ai-log-agent/backend:latest"
}

output "frontend_build_command" {
  description = "Docker build command for the frontend image (bakes in the backend URL at build time)."
  value       = "docker build --build-arg NEXT_PUBLIC_API_URL=https://${azurerm_container_app.backend.ingress[0].fqdn} -t ${azurerm_container_registry.app.login_server}/ai-log-agent/frontend:latest ./frontend"
}
