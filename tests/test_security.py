import json
from pathlib import Path

import pyotp

from app.security import hash_password, hash_token, redact, verify_password


def test_password_uses_unique_salted_argon2_hashes():
    first = hash_password("Uma-Senha-Forte-123!")
    second = hash_password("Uma-Senha-Forte-123!")
    assert first != second
    assert first.startswith("$argon2id$")
    assert verify_password(first, "Uma-Senha-Forte-123!")
    assert not verify_password(first, "senha-errada")


def test_totp_and_token_hash():
    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()
    assert pyotp.TOTP(secret).verify(code)
    assert hash_token("token") != "token"


def test_sensitive_cpf_is_redacted():
    assert "123" not in redact("CPF 123.456.789-09")


def test_extension_manifest_has_security_permissions():
    with open("extension/manifest.json", encoding="utf-8") as file:
        manifest = json.load(file)
    assert manifest["manifest_version"] == 3
    assert "https://web.whatsapp.com/*" in manifest["host_permissions"]


def test_sensitive_runtime_files_are_ignored_by_git():
    ignore = Path(".gitignore").read_text(encoding="utf-8")
    for rule in (".env", "data/*.db", "data/*.xlsx", "data/uploads/*", "data/onboarding/*", "backups/*.zip"):
        assert rule in ignore


def test_no_real_secret_in_example_environment():
    values = {}
    for line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    assert values["GEMINI_API_KEY"] == "coloque_a_chave_aqui"
    assert values["APP_SECRET_KEY"] == "gere_com_configurar_seguranca.ps1"
