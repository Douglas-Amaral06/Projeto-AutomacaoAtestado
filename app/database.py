import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "atestados.db"


def initialize_database() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS atestados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT,
                cpf TEXT,
                cid TEXT,
                dias_afastamento INTEGER,
                data_atestado TEXT,
                arquivo_original TEXT NOT NULL,
                arquivo_salvo TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pendente',
                observacoes TEXT,
                confianca TEXT,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                revisado_em TEXT
            )
            """
        )


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection

