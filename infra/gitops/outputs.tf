output "argocd_ui" {
  value = "kubectl port-forward -n argocd svc/argocd-server 8080:443   →   https://localhost:8080 (user: admin)"
}

output "argocd_initial_password" {
  value = "kubectl get secret -n argocd argocd-initial-admin-secret -o jsonpath='{.data.password}'  (base64; change it in the UI, then delete the secret)"
}

output "public_urls" {
  value = { for k, h in local.private_hosts : k => "https://${h.hostname}" }
}

output "mcp_client_id" {
  description = "Client ID MCP clients use to sign in to the MCP server (Claude connector, MCP Inspector)"
  value       = azuread_application.mcp.client_id
}

output "mcp_scope" {
  description = "The scope MCP clients request"
  value       = "api://${azuread_application.mcp.client_id}/MCP.Access"
}

output "mcp_client_secret" {
  description = "Client secret for Claude's custom connector (terraform output -raw mcp_client_secret)"
  value       = azuread_application_password.mcp.value
  sensitive   = true
}

output "chat_client_id" {
  description = "Client ID of the chat's Entra app (the dashboard signs in to it)"
  value       = azuread_application.chat.client_id
}

output "claude_federation" {
  description = "What the Claude Console's Connect workload wizard (Microsoft Entra tile, v2.0 issuer) asks for"
  value = {
    issuer_url = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/v2.0"
    audience   = azuread_application.claude_audience.client_id # the token's aud (v2.0: the client ID)
    oid        = local.compute.chat_identity_principal_id      # match claims.oid (and the subject)
    tid        = data.azurerm_client_config.current.tenant_id  # match claims.tid
  }
}
