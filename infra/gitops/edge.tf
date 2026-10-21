# Public entry (#45): Cloudflare Tunnel + Access. No public IP, load balancer or open port
# on the cluster: cloudflared (deploy/cloudflared) dials out to Cloudflare, which routes
# the hostnames below to it.
#
# Private hostnames: Cloudflare Access asks for a Microsoft (Entra ID) sign-in and lets in
# only var.access_allowed_emails; cloudflared re-checks the Access token (aud) for every
# request, so traffic can't reach the origin without passing Access.
#
# Public routes (no Access) on <prefix>.<domain>, matched in order: /api/ is the read-only
# data API (#66), which rate-limits per client IP (CF-Connecting-IP) and can only read
# lake.gold; everything else is the dashboard (#67), static files on nginx.
# https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
locals {
  private_hosts = {
    cd = {
      hostname      = "${var.hostname_prefix}-cd.${var.domain}"
      title         = "Argo CD"
      service       = "https://argocd-server.argocd.svc.cluster.local:443"
      no_tls_verify = true # Argo CD's self-signed cert on an in-cluster hop
    }
    flows = {
      hostname      = "${var.hostname_prefix}-flows.${var.domain}"
      title         = "Argo Workflows"
      service       = "http://argo-workflows-server.argo.svc.cluster.local:2746"
      no_tls_verify = false
    }
    grafana = {
      hostname      = "${var.hostname_prefix}-grafana.${var.domain}"
      title         = "Grafana"
      service       = "http://grafana.monitoring.svc.cluster.local:80"
      no_tls_verify = false
    }
    # JupyterLab listens on 127.0.0.1 inside its pod, so it gets its own tunnel whose only
    # connector is a cloudflared sidecar in that pod (deploy/lichess/workflowtemplate-jupyter.yaml).
    # The hostname is only up while a notebook runs.
    lab = {
      hostname      = "${var.hostname_prefix}-lab.${var.domain}"
      title         = "JupyterLab"
      service       = "http://localhost:8888"
      no_tls_verify = false
    }
  }
  public_hostname = var.hostname_prefix == "" ? var.domain : "${var.hostname_prefix}.${var.domain}"
  # The MCP server (#68) gets its own hostname: no Cloudflare Access (MCP clients can't do its
  # browser sign-in), the server itself requires an Entra token with app role Lakehouse.Query.
  mcp_hostname = "${var.hostname_prefix}-mcp.${var.domain}"
  # A list, not a map: cloudflared takes the first rule that matches, so the API's path
  # must come before the dashboard's catch-all.
  public_routes = [
    {
      hostname = local.public_hostname
      path     = "^/api/"
      service  = "http://chess-api.chess-api.svc.cluster.local:80"
    },
    {
      hostname = local.public_hostname
      path     = null # every other path
      service  = "http://chess-web.chess-web.svc.cluster.local:80"
    },
    {
      hostname = local.mcp_hostname
      path     = null
      service  = "http://chess-mcp.chess-mcp.svc.cluster.local:80"
    },
  ]
  tunnel_hosts = {
    aks = { for k, h in local.private_hosts : k => h if k != "lab" }
    lab = { for k, h in local.private_hosts : k => h if k == "lab" }
  }
  tunnel_ids = {
    aks = cloudflare_zero_trust_tunnel_cloudflared.aks.id
    lab = cloudflare_zero_trust_tunnel_cloudflared.lab.id
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "aks" {
  account_id = var.cloudflare_account_id
  name       = "aks-lichess"
  config_src = "cloudflare" # ingress rules managed here, not in a cloudflared config file
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "aks" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.aks.id
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "lab" {
  account_id = var.cloudflare_account_id
  name       = "aks-lichess-lab"
  config_src = "cloudflare"
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "lab" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.lab.id
}

resource "cloudflare_zero_trust_access_identity_provider" "entra" {
  account_id = var.cloudflare_account_id
  name       = "Microsoft Entra ID"
  type       = "azureAD"
  config = {
    client_id     = azuread_application.cloudflare_access.client_id
    client_secret = azuread_application_password.cloudflare_access.value
    directory_id  = data.azuread_client_config.current.tenant_id
    pkce_enabled  = true
  }
}

resource "cloudflare_zero_trust_access_policy" "owner" {
  account_id = var.cloudflare_account_id
  name       = "${var.hostname_prefix}: owner only"
  decision   = "allow"
  include    = [for e in var.access_allowed_emails : { email = { email = e } }]
}

resource "cloudflare_zero_trust_access_application" "private" {
  for_each = local.private_hosts

  account_id                 = var.cloudflare_account_id
  name                       = "${var.hostname_prefix}: ${each.value.title}"
  type                       = "self_hosted"
  domain                     = each.value.hostname
  destinations               = [{ type = "public", uri = each.value.hostname }]
  session_duration           = "24h"
  allowed_idps               = [cloudflare_zero_trust_access_identity_provider.entra.id]
  auto_redirect_to_identity  = true # straight to the Microsoft sign-in
  app_launcher_visible       = true
  http_only_cookie_attribute = true
  same_site_cookie_attribute = "lax"
  policies                   = [{ id = cloudflare_zero_trust_access_policy.owner.id, precedence = 1 }]
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "aks" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.aks.id
  config = {
    ingress = concat(
      [for k, h in local.tunnel_hosts.aks : {
        hostname = h.hostname
        service  = h.service
        origin_request = {
          no_tls_verify = h.no_tls_verify
          access = {
            required  = true
            team_name = var.cloudflare_team_name
            aud_tag   = [cloudflare_zero_trust_access_application.private[k].aud]
          }
        }
      }],
      [for r in local.public_routes : {
        hostname = r.hostname
        path     = r.path
        service  = r.service
      }],
      [{ service = "http_status:404" }], # anything else
    )
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "lab" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.lab.id
  config = {
    ingress = concat(
      [for k, h in local.tunnel_hosts.lab : {
        hostname = h.hostname
        service  = h.service
        origin_request = {
          access = {
            required  = true
            team_name = var.cloudflare_team_name
            aud_tag   = [cloudflare_zero_trust_access_application.private[k].aud]
          }
        }
      }],
      [{ service = "http_status:404" }],
    )
  }
}

resource "cloudflare_dns_record" "private" {
  for_each = local.private_hosts

  zone_id = var.cloudflare_zone_id
  name    = each.value.hostname
  type    = "CNAME"
  content = "${each.key == "lab" ? local.tunnel_ids.lab : local.tunnel_ids.aks}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # automatic (proxied)
  comment = "lichess-delta-kube: Cloudflare Tunnel (infra/gitops/edge.tf)"
}

resource "cloudflare_dns_record" "public" {
  zone_id = var.cloudflare_zone_id
  name    = local.public_hostname
  type    = "CNAME"
  content = "${local.tunnel_ids.aks}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # automatic (proxied)
  comment = "lichess-delta-kube: public routes via Cloudflare Tunnel (infra/gitops/edge.tf)"
}

resource "cloudflare_dns_record" "mcp" {
  zone_id = var.cloudflare_zone_id
  name    = local.mcp_hostname
  type    = "CNAME"
  content = "${local.tunnel_ids.aks}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # automatic (proxied)
  comment = "lichess-delta-kube: MCP server via Cloudflare Tunnel (infra/gitops/edge.tf)"
}

# The tunnel token for cloudflared, straight into the cluster (never in a file or git).
resource "kubernetes_namespace_v1" "cloudflared" {
  metadata {
    name   = "cloudflared"
    labels = { "app.kubernetes.io/part-of" = "lichess" }
  }
}

resource "kubernetes_secret_v1" "cloudflared_token" {
  metadata {
    name      = "cloudflared-token"
    namespace = kubernetes_namespace_v1.cloudflared.metadata[0].name
  }
  data = {
    token = data.cloudflare_zero_trust_tunnel_cloudflared_token.aks.token
  }
}

# Token for the JupyterLab sidecar: in lichess, where the notebook pod runs.
resource "kubernetes_secret_v1" "cloudflared_lab_token" {
  metadata {
    name      = "cloudflared-lab-token"
    namespace = kubernetes_namespace_v1.lichess.metadata[0].name
  }
  data = {
    token = data.cloudflare_zero_trust_tunnel_cloudflared_token.lab.token
  }
}

