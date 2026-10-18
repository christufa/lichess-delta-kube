"""Where output goes: a local directory (for testing) or Azure Blob / ADLS Gen2.

Destination URLs:
  /path/or/file:///path                                   local directory
  https://<account>.blob.core.windows.net/<container>[/prefix]
  abfss://<container>@<account>.dfs.core.windows.net[/prefix]

Azure credentials, first match wins:
  AZURE_STORAGE_CONNECTION_STRING   (e.g. Azurite)
  AZURE_STORAGE_SAS_TOKEN           (container SAS, e.g. a user-delegation SAS)
  DefaultAzureCredential            (az login, service principal env vars, managed/workload identity)
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.parse


def _claim_body(owner: str) -> dict:
    return {"owner": owner, "at": time.time(), "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _expired(data: dict, ttl_s: float) -> bool:
    return time.time() - float(data.get("at", 0)) > ttl_s


class LocalDest:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.url = self.root

    def _path(self, name: str) -> str:
        return os.path.join(self.root, *name.split("/"))

    def put_file(self, local_path: str, name: str) -> None:
        target = self._path(name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(local_path, target + ".tmp")
        os.replace(target + ".tmp", target)

    def put_json(self, name: str, data: dict) -> None:
        target = self._path(name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target + ".tmp", "w") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(target + ".tmp", target)

    def get_json(self, name: str) -> dict | None:
        try:
            with open(self._path(name)) as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not os.path.isdir(base):
            return []
        out = []
        for dirpath, _, files in os.walk(base):
            for f in files:
                rel = os.path.relpath(os.path.join(dirpath, f), self.root)
                out.append(rel.replace(os.sep, "/"))
        return sorted(out)

    def delete(self, name: str) -> None:
        try:
            os.remove(self._path(name))
        except FileNotFoundError:
            pass

    # Claims: "I'm working on this month", so parallel runners never share a month.
    def claim(self, name: str, owner: str, ttl_s: float) -> tuple[bool, dict | None]:
        target = self._path(name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            current = self.get_json(name) or {}
            if current.get("owner") != owner and not _expired(current, ttl_s):
                return False, current
            self.put_json(name, _claim_body(owner))  # ours already, or abandoned: take it over
            return True, current
        with os.fdopen(fd, "w") as fh:
            json.dump(_claim_body(owner), fh)
        return True, None

    def release(self, name: str, owner: str) -> None:
        current = self.get_json(name)
        if current and current.get("owner") == owner:
            self.delete(name)


class BlobDest:
    def __init__(self, account_url: str, container: str, prefix: str = ""):
        from azure.storage.blob import BlobServiceClient

        conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        sas = os.environ.get("AZURE_STORAGE_SAS_TOKEN")
        if conn:
            service = BlobServiceClient.from_connection_string(conn)
            self.auth = "connection-string"
        elif sas:
            service = BlobServiceClient(account_url, credential=sas.lstrip("?"))
            self.auth = "sas"
        else:
            from azure.identity import DefaultAzureCredential
            service = BlobServiceClient(account_url, credential=DefaultAzureCredential(
                exclude_interactive_browser_credential=True))
            self.auth = "default-credential"
        self.container = service.get_container_client(container)
        self.prefix = prefix.strip("/")
        self.url = f"{account_url.rstrip('/')}/{container}" + (f"/{self.prefix}" if self.prefix else "")

    def _name(self, name: str) -> str:
        return f"{self.prefix}/{name}" if self.prefix else name

    def put_file(self, local_path: str, name: str) -> None:
        with open(local_path, "rb") as fh:
            self.container.upload_blob(self._name(name), fh, overwrite=True, max_concurrency=8)

    def put_json(self, name: str, data: dict) -> None:
        from azure.storage.blob import ContentSettings
        self.container.upload_blob(self._name(name), json.dumps(data, indent=2, default=str).encode(),
                                   overwrite=True, content_settings=ContentSettings(content_type="application/json"))

    def get_json(self, name: str) -> dict | None:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return json.loads(self.container.download_blob(self._name(name)).readall())
        except ResourceNotFoundError:
            return None

    def list(self, prefix: str) -> list[str]:
        full = self._name(prefix)
        cut = len(self.prefix) + 1 if self.prefix else 0
        return sorted(b.name[cut:] for b in self.container.list_blobs(name_starts_with=full))

    def delete(self, name: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            self.container.delete_blob(self._name(name))
        except ResourceNotFoundError:
            pass

    def claim(self, name: str, owner: str, ttl_s: float) -> tuple[bool, dict | None]:
        """Atomically create the claim blob; take it over only if it's ours or expired."""
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
        blob = self.container.get_blob_client(self._name(name))
        body = json.dumps(_claim_body(owner)).encode()
        try:
            blob.upload_blob(body, overwrite=False)
            return True, None
        except ResourceExistsError:
            pass
        try:
            stream = blob.download_blob()
            current, etag = json.loads(stream.readall()), stream.properties.etag
        except ResourceNotFoundError:          # released in between: try once more from scratch
            try:
                blob.upload_blob(body, overwrite=False)
                return True, None
            except ResourceExistsError:
                return False, None
        if current.get("owner") != owner and not _expired(current, ttl_s):
            return False, current
        try:
            blob.upload_blob(body, overwrite=True, etag=etag, match_condition=MatchConditions.IfNotModified)
            return True, current
        except ResourceModifiedError:           # someone else took it over first
            return False, current

    def release(self, name: str, owner: str) -> None:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
        blob = self.container.get_blob_client(self._name(name))
        try:
            stream = blob.download_blob()
            current, etag = json.loads(stream.readall()), stream.properties.etag
            if current.get("owner") == owner:
                blob.delete_blob(etag=etag, match_condition=MatchConditions.IfNotModified)
        except (ResourceNotFoundError, ResourceModifiedError):
            pass

    def check(self) -> None:
        """Fail fast on bad credentials, the firewall, or a missing container.

        Lists at most one blob rather than reading container properties: a
        container-scoped SAS (what ingest uses) may list blobs but is not
        allowed to read the container's properties.
        """
        from azure.core.exceptions import ResourceNotFoundError
        try:
            next(iter(self.container.list_blobs(name_starts_with=self._name(""), results_per_page=1)), None)
        except ResourceNotFoundError:
            raise FileNotFoundError(f"container does not exist: {self.url}") from None


def open_dest(url: str):
    if url.startswith("file://"):
        return LocalDest(urllib.parse.urlparse(url).path)
    if url.startswith("https://") and ".blob." in url:
        parsed = urllib.parse.urlparse(url)
        parts = parsed.path.strip("/").split("/", 1)
        if not parts[0]:
            raise ValueError(f"destination needs a container: {url}")
        return BlobDest(f"https://{parsed.netloc}", parts[0], parts[1] if len(parts) > 1 else "")
    if url.startswith("http://"):  # Azurite: http://127.0.0.1:10000/<account>/<container>[/prefix]
        parsed = urllib.parse.urlparse(url)
        parts = parsed.path.strip("/").split("/", 2)
        if len(parts) < 2:
            raise ValueError(f"Azurite destination must be http://host:port/<account>/<container>: {url}")
        return BlobDest(f"http://{parsed.netloc}/{parts[0]}", parts[1], parts[2] if len(parts) > 2 else "")
    if url.startswith("abfss://"):
        parsed = urllib.parse.urlparse(url)
        container, host = parsed.netloc.split("@", 1)
        account = host.split(".", 1)[0]
        return BlobDest(f"https://{account}.blob.core.windows.net", container, parsed.path)
    if "://" in url:
        raise ValueError(f"unsupported destination: {url}")
    return LocalDest(url)
