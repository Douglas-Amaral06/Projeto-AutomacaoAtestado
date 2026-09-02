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


def test_extension_keeps_only_whatsapp_upload_flow():
    popup_html = (ROOT / "extension" / "popup.html").read_text(encoding="utf-8")
    popup_js = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
    background_js = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")

    assert "Simulação sem WhatsApp" not in popup_html
    assert "Abrir tela de apresentação" not in popup_html
    assert "Extração Manual" in popup_html
    assert 'id_conversa: "extracao-manual-extensao"' in popup_js
    assert 'type: "UPLOAD_ATTACHMENT"' in popup_js
    assert 'formData.append("id_mensagem"' in background_js
    assert 'formData.append("data_recebimento"' in background_js
    assert "id_documento: result.id_documento" in background_js
