import pytest

from lichess_spark.session import WI_PROVIDER, dest_root, spot_conf, storage_conf

DEST = "abfss://lake@acct.dfs.core.windows.net/bootstrap"
HOST = "acct.dfs.core.windows.net"


def test_workload_identity_conf_is_account_scoped():
    env = {"LICHESS_DEST": DEST, "AZURE_FEDERATED_TOKEN_FILE": "/var/run/token",
           "AZURE_CLIENT_ID": "cid", "AZURE_TENANT_ID": "tid", "AZURE_STORAGE_SAS_TOKEN": "ignored"}
    conf = storage_conf(env)
    assert conf[f"spark.hadoop.fs.azure.account.auth.type.{HOST}"] == "OAuth"
    assert conf[f"spark.hadoop.fs.azure.account.oauth.provider.type.{HOST}"] == WI_PROVIDER
    assert conf[f"spark.hadoop.fs.azure.account.oauth2.client.id.{HOST}"] == "cid"
    assert conf[f"spark.hadoop.fs.azure.account.oauth2.msi.tenant.{HOST}"] == "tid"
    assert conf[f"spark.hadoop.fs.azure.account.oauth2.token.file.{HOST}"] == "/var/run/token"


def test_sas_conf_for_local_runs():
    conf = storage_conf({"LICHESS_DEST": DEST, "AZURE_STORAGE_SAS_TOKEN": "?sv=1&sig=x"})
    assert conf == {f"spark.hadoop.fs.azure.account.auth.type.{HOST}": "SAS",
                    f"spark.hadoop.fs.azure.sas.fixed.token.{HOST}": "sv=1&sig=x"}


def test_local_destinations_need_no_credentials():
    assert storage_conf({"LICHESS_DEST": "file:///tmp/lake"}) == {}


def test_missing_settings_fail_clearly():
    with pytest.raises(RuntimeError, match="LICHESS_DEST"):
        dest_root({})
    with pytest.raises(RuntimeError, match="no Azure credentials"):
        storage_conf({"LICHESS_DEST": DEST})


def test_spot_fallback_storage_lives_in_the_lake():
    assert spot_conf({"LICHESS_DEST": DEST + "/"})["spark.storage.decommission.fallbackStorage.path"] == \
        DEST + "/spark/fallback/"
