"""Abstração de armazenamento para a camada de entrega."""

from __future__ import annotations

import json
import os
import base64
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class StorageClient(Protocol):
    """Contrato mínimo. Uma implementação Databricks futura seguirá esta interface."""

    def write_binary(self, relative_path: PurePosixPath, content: bytes) -> None: ...
    def write_json(self, relative_path: PurePosixPath, payload: dict) -> None: ...
    def read_binary(self, relative_path: PurePosixPath) -> bytes: ...
    def size(self, relative_path: PurePosixPath) -> int: ...
    def delete_file(self, relative_path: PurePosixPath) -> None: ...


class LocalFakeStorageClient:
    """Storage fake/local para testes; não possui código de rede ou credenciais."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._write_lock = threading.Lock()

    def path_for(self, relative_path: PurePosixPath) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("O caminho de armazenamento deve ser relativo e seguro.")
        target = (self.root / relative).resolve()
        if self.root != target and self.root not in target.parents:
            raise ValueError("O caminho de armazenamento saiu da raiz local.")
        return target

    def write_binary(self, relative_path: PurePosixPath, content: bytes) -> None:
        self._atomic_write(self.path_for(relative_path), content, binary=True)

    def write_json(self, relative_path: PurePosixPath, payload: dict) -> None:
        content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        self._atomic_write(self.path_for(relative_path), content, binary=False)

    def read_binary(self, relative_path: PurePosixPath) -> bytes:
        return self.path_for(relative_path).read_bytes()

    def size(self, relative_path: PurePosixPath) -> int:
        return self.path_for(relative_path).stat().st_size

    def delete_file(self, relative_path: PurePosixPath) -> None:
        """Remove somente o arquivo relativo informado, sem apagar diretórios."""
        self.path_for(relative_path).unlink()

    def _atomic_write(self, path: Path, content: bytes | str, binary: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
        try:
            if binary:
                with temporary.open("wb") as file:
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
            else:
                with temporary.open("w", encoding="utf-8", newline="\n") as file:
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
            # Windows pode negar dois os.replace concorrentes sobre o mesmo
            # destino. A escrita do temporário continua paralela; somente a
            # publicação final é serializada e permanece atômica.
            with self._write_lock:
                temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


class DatabricksStorageClient:
    """Cliente da Files API com OAuth M2M e credenciais somente externas."""

    def __init__(
        self,
        *,
        host: str,
        client_id: str,
        client_secret: str,
        volume_root: str,
        timeout_seconds: int = 60,
        max_attempts: int = 3,
    ) -> None:
        self.host = host.strip().rstrip("/")
        self.client_id = client_id.strip()
        self.client_secret = client_secret
        self.volume_root = PurePosixPath(volume_root)
        self.timeout_seconds = max(5, min(timeout_seconds, 180))
        self.max_attempts = max(1, min(max_attempts, 3))
        self._access_token = ""
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()
        if not self.host.startswith("https://"):
            raise ValueError("DATABRICKS_HOST deve usar HTTPS.")
        if not self.client_id or not self.client_secret:
            raise ValueError("Credenciais OAuth M2M do Databricks são obrigatórias.")
        if not str(self.volume_root).startswith("/Volumes/") or ".." in self.volume_root.parts:
            raise ValueError("DATABRICKS_VOLUME_ROOT inválido.")

    def write_binary(self, relative_path: PurePosixPath, content: bytes) -> None:
        destination = self._volume_path(relative_path)
        self._create_directory(destination.parent)
        self._request(
            "PUT",
            f"/api/2.0/fs/files/{self._encoded_path(destination)}?overwrite=true",
            body=content,
            content_type="application/octet-stream",
            expected={200, 204},
        )

    def write_json(self, relative_path: PurePosixPath, payload: dict) -> None:
        content = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        self.write_binary(relative_path, content)

    def read_binary(self, relative_path: PurePosixPath) -> bytes:
        destination = self._volume_path(relative_path)
        return self._request(
            "GET", f"/api/2.0/fs/files/{self._encoded_path(destination)}", expected={200}
        )[1]

    def size(self, relative_path: PurePosixPath) -> int:
        destination = self._volume_path(relative_path)
        headers, _ = self._request(
            "HEAD", f"/api/2.0/fs/files/{self._encoded_path(destination)}", expected={200, 204}
        )
        try:
            return int(headers["Content-Length"])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("Databricks não confirmou o tamanho do arquivo.") from error

    def delete_file(self, relative_path: PurePosixPath) -> None:
        """Remove um único arquivo do Volume por caminho relativo validado."""
        destination = self._volume_path(relative_path)
        self._request(
            "DELETE",
            f"/api/2.0/fs/files/{self._encoded_path(destination)}",
            expected={204},
        )

    def check_directory_access(self, relative_path: PurePosixPath | None = None) -> None:
        """Confere autenticação e leitura do Volume sem criar ou alterar arquivos."""
        directory = self.volume_root
        if relative_path is not None:
            relative = PurePosixPath(relative_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("O diretório de verificação deve ser relativo e seguro.")
            directory = directory.joinpath(relative)
        self._request(
            "GET",
            f"/api/2.0/fs/directories/{self._encoded_path(directory)}",
            expected={200},
        )

    def _volume_path(self, relative_path: PurePosixPath) -> PurePosixPath:
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("O caminho de armazenamento deve ser relativo e seguro.")
        return self.volume_root.joinpath(relative)

    @staticmethod
    def _encoded_path(path: PurePosixPath) -> str:
        return quote(path.as_posix().lstrip("/"), safe="/")

    def _create_directory(self, directory: PurePosixPath) -> None:
        self._request(
            "PUT", f"/api/2.0/fs/directories/{self._encoded_path(directory)}", expected={200, 201, 204}
        )

    def _token(self, *, force_refresh: bool = False) -> str:
        with self._token_lock:
            if not force_refresh and self._access_token and time.time() < self._token_expires_at - 120:
                return self._access_token
            credentials = base64.b64encode(
                f"{self.client_id}:{self.client_secret}".encode("utf-8")
            ).decode("ascii")
            request = Request(
                f"{self.host}/oidc/v1/token",
                data=urlencode({"grant_type": "client_credentials", "scope": "all-apis"}).encode("ascii"),
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, ValueError) as error:
                raise RuntimeError("Falha ao autenticar no Databricks.") from error
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise RuntimeError("Databricks não retornou um token de acesso válido.")
            expires_in = payload.get("expires_in", 3600)
            try:
                lifetime = max(300, int(expires_in))
            except (TypeError, ValueError):
                lifetime = 3600
            self._access_token = token
            self._token_expires_at = time.time() + lifetime
            return token

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        expected: set[int],
    ) -> tuple[dict[str, str], bytes]:
        refreshed = False
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            headers = {"Authorization": f"Bearer {self._token(force_refresh=refreshed)}"}
            if content_type:
                headers["Content-Type"] = content_type
            request = Request(f"{self.host}{path}", data=body, headers=headers, method=method)
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    status = response.getcode()
                    response_body = response.read()
                    if status not in expected:
                        raise RuntimeError(f"Resposta inesperada da Files API: HTTP {status}.")
                    return dict(response.headers.items()), response_body
            except HTTPError as error:
                last_error = error
                if error.code == 401 and not refreshed:
                    refreshed = True
                    continue
                if error.code not in {408, 429, 500, 502, 503, 504}:
                    break
            except (URLError, TimeoutError) as error:
                last_error = error
            if attempt + 1 < self.max_attempts:
                time.sleep(min(2**attempt, 4))
        raise RuntimeError("Falha na comunicação com a Files API do Databricks.") from last_error
