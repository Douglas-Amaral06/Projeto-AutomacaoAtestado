import argparse
import getpass
import secrets
import string
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from app.database import BASE_DIR, connect, initialize_database
from app.security import encrypt_totp, hash_password


def generated_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%*-_"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
            return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--usuario")
    parser.add_argument("--nome")
    parser.add_argument("--gerar-senha", action="store_true")
    args = parser.parse_args()
    load_dotenv(BASE_DIR / ".env")
    initialize_database()
    username = (args.usuario or input("Usuario administrador: ")).strip()
    name = (args.nome or input("Nome completo: ")).strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,50}", username):
        raise SystemExit("Usuario invalido. Use 3 a 50 letras, numeros, ponto, hifen ou sublinhado.")
    if not 2 <= len(name) <= 100:
        raise SystemExit("Nome deve ter entre 2 e 100 caracteres.")
    if args.gerar_senha:
        password = generated_password()
    else:
        password = getpass.getpass("Senha (minimo 12 caracteres): ")
        confirmation = getpass.getpass("Confirme a senha: ")
        if password != confirmation:
            raise SystemExit("As senhas nao coincidem")
    totp_secret = secrets.token_urlsafe(32)
    with connect() as connection:
        connection.execute(
            "INSERT INTO usuarios(usuario,nome,senha_hash,totp_secret_encrypted,perfil) VALUES(?,?,?,?, 'admin')",
            (username, name, hash_password(password), encrypt_totp(totp_secret)),
        )
    print(f"USUARIO={username}")
    print(f"SENHA={password}")
    print("A senha nao foi armazenada em texto puro.")


if __name__ == "__main__":
    main()
