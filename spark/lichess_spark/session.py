"""SparkSession for the Lichess lake, configured from the environment.

Where the data is:
  LICHESS_DEST   abfss://<container>@<account>.dfs.core.windows.net/<prefix>  (or file:///… for tests)
                 In the cluster it comes from the lichess-config ConfigMap.

How Spark signs in to ADLS (first match wins):
  AZURE_FEDERATED_TOKEN_FILE (+ AZURE_CLIENT_ID, AZURE_TENANT_ID)
      Workload identity, injected into pods labelled azure.workload.identity/use=true.
  AZURE_STORAGE_SAS_TOKEN
      A container SAS, e.g. the user-delegation SAS from ingest/.env, for local runs.

The Iceberg catalog (if LICHESS_CATALOG_URI is set, as in the cluster):
  LICHESS_CATALOG_URI  Lakekeeper's Iceberg REST endpoint, …/catalog
  LICHESS_WAREHOUSE    warehouse name (default "lichess")
  Tables are lake.<namespace>.<table>; Lakekeeper vends a SAS token scoped to each table.
  The pod's Kubernetes service account token authenticates to Lakekeeper (redacted in the
  Spark UI by spark.redaction.regex).
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

WI_PROVIDER = "org.apache.hadoop.fs.azurebfs.oauth2.WorkloadIdentityTokenProvider"
CATALOG = "lake"
SA_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"


def dest_root(env=None) -> str:
    env = os.environ if env is None else env
    dest = env.get("LICHESS_DEST")
    if not dest:
        raise RuntimeError("LICHESS_DEST is not set (abfss://<container>@<account>.dfs.core.windows.net/<prefix>)")
    return dest.rstrip("/")


def storage_conf(env=None) -> dict[str, str]:
    """Spark settings that let ABFS reach the lake. Empty for local (file://) destinations."""
    env = os.environ if env is None else env
    url = urlparse(dest_root(env))
    if url.scheme != "abfss":
        return {}
    host = url.netloc.split("@", 1)[1]          # <account>.dfs.core.windows.net
    k = lambda name: f"spark.hadoop.fs.azure.{name}.{host}"  # account-scoped ABFS keys
    if env.get("AZURE_FEDERATED_TOKEN_FILE"):
        return {
            k("account.auth.type"): "OAuth",
            k("account.oauth.provider.type"): WI_PROVIDER,
            k("account.oauth2.msi.tenant"): env["AZURE_TENANT_ID"],
            k("account.oauth2.client.id"): env["AZURE_CLIENT_ID"],
            k("account.oauth2.token.file"): env["AZURE_FEDERATED_TOKEN_FILE"],
        }
    if env.get("AZURE_STORAGE_SAS_TOKEN"):
        return {
            k("account.auth.type"): "SAS",
            k("sas.fixed.token"): env["AZURE_STORAGE_SAS_TOKEN"].lstrip("?"),
        }
    raise RuntimeError("no Azure credentials: expected workload identity (AZURE_FEDERATED_TOKEN_FILE) "
                       "or AZURE_STORAGE_SAS_TOKEN")


def spot_conf(env=None) -> dict[str, str]:
    """Survive spot evictions: move cached and shuffle blocks off a node that's being reclaimed,
    to other executors or, failing that, to the lake."""
    root = dest_root(env)
    return {
        "spark.decommission.enabled": "true",
        "spark.storage.decommission.enabled": "true",
        "spark.storage.decommission.shuffleBlocks.enabled": "true",
        "spark.storage.decommission.rddBlocks.enabled": "true",
        "spark.storage.decommission.fallbackStorage.path": f"{root}/spark/fallback/",
    }


def catalog_conf(env=None) -> dict[str, str]:
    """Iceberg REST catalog `lake` (Lakekeeper) with vended credentials; empty without LICHESS_CATALOG_URI."""
    env = os.environ if env is None else env
    uri = env.get("LICHESS_CATALOG_URI")
    if not uri:
        return {}
    c = f"spark.sql.catalog.{CATALOG}"
    token_file = env.get("LICHESS_CATALOG_TOKEN_FILE", SA_TOKEN)
    auth = {}
    if os.path.exists(token_file):
        with open(token_file) as fh:
            auth = {f"{c}.token": fh.read().strip()}
    return {
        **auth,
        "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        c: "org.apache.iceberg.spark.SparkCatalog",
        f"{c}.type": "rest",
        f"{c}.uri": uri,
        f"{c}.warehouse": env.get("LICHESS_WAREHOUSE", "lichess"),
        f"{c}.io-impl": "org.apache.iceberg.azure.adlsv2.ADLSFileIO",
        # Ask Lakekeeper for a SAS token scoped to each table instead of using our own identity.
        f"{c}.header.X-Iceberg-Access-Delegation": "vended-credentials",
        "spark.sql.defaultCatalog": CATALOG,
    }


def get_spark(app_name: str, extra: dict[str, str] | None = None, env=None):
    from pyspark.sql import SparkSession

    builder = SparkSession.builder.appName(app_name)
    conf = {**storage_conf(env), **(spot_conf(env) if urlparse(dest_root(env)).scheme == "abfss" else {}),
            **catalog_conf(env), **(extra or {})}
    for key, value in conf.items():
        builder = builder.config(key, value)
    return builder.getOrCreate()
