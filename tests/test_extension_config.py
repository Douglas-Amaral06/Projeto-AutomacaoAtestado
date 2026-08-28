import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "extension" / "backend-config.js"


def _normalize_with_node(value: str) -> subprocess.CompletedProcess[str]:
    if not shutil.which("node"):
        pytest.skip("Node.js não está disponível para validar a extensão.")
    script = (
        f"const c=require({json.dumps(str(CONFIG))});"
        f"try{{process.stdout.write(c.normalizeBackendUrl({json.dumps(value)}));}}"
        "catch(e){process.stderr.write(e.message);process.exit(2)}"
    )
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("https://atestados.empresa.com.br/", "https://atestados.empresa.com.br"),
    ],
)
def test_extension_accepts_safe_backend_urls(value, expected):
    result = _normalize_with_node(value)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://atestados.empresa.com.br",
        "https://usuario:senha@atestados.empresa.com.br",
        "https://atestados.empresa.com.br/api/atestados",
        "javascript:alert(1)",
    ],
)
def test_extension_rejects_unsafe_backend_urls(value):
    result = _normalize_with_node(value)
    assert result.returncode == 2


def test_extension_requests_only_https_remote_hosts():
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["optional_host_permissions"] == ["https://*/*"]
    assert "backend-config.js" in (ROOT / "extension" / "popup.html").read_text(encoding="utf-8")
