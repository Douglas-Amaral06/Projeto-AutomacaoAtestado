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
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(atestados)").fetchall()
        }
        if "arquivo_hash" not in columns:
            connection.execute("ALTER TABLE atestados ADD COLUMN arquivo_hash TEXT")
        for column, definition in {
            "revisado_por": "INTEGER",
            "motivo_rejeicao": "TEXT",
            "dados_originais": "TEXT",
        }.items():
            if column not in columns:
                connection.execute(f"ALTER TABLE atestados ADD COLUMN {column} {definition}")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_atestados_arquivo_hash ON atestados(arquivo_hash)"
        )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL UNIQUE COLLATE NOCASE,
                nome TEXT NOT NULL,
                senha_hash TEXT NOT NULL,
                totp_secret_encrypted TEXT NOT NULL,
                perfil TEXT NOT NULL CHECK(perfil IN ('admin','analista')),
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ultimo_login TEXT
            );
            CREATE TABLE IF NOT EXISTS sessoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                csrf_token TEXT NOT NULL,
                ip_hash TEXT,
                user_agent_hash TEXT,
                expira_em TEXT NOT NULL,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
            );
            CREATE TABLE IF NOT EXISTS tokens_servico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_por INTEGER,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ultimo_uso TEXT,
                FOREIGN KEY(criado_por) REFERENCES usuarios(id)
            );
            CREATE TABLE IF NOT EXISTS tentativas_login (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chave_hash TEXT NOT NULL,
                sucesso INTEGER NOT NULL,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_login_chave_data ON tentativas_login(chave_hash, criado_em);
            CREATE TABLE IF NOT EXISTS codigos_pareamento (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_hash TEXT NOT NULL UNIQUE,
                criado_por INTEGER NOT NULL,
                expira_em TEXT NOT NULL,
                usado_em TEXT,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(criado_por) REFERENCES usuarios(id)
            );
            CREATE TABLE IF NOT EXISTS fila_processamento (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                arquivo_hash TEXT NOT NULL UNIQUE,
                arquivo_original TEXT NOT NULL,
                arquivo_salvo TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processando',
                tentativas INTEGER NOT NULL DEFAULT 0,
                ultimo_erro TEXT,
                disponivel_em TEXT,
                atestado_id INTEGER,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(atestado_id) REFERENCES atestados(id)
            );
            CREATE INDEX IF NOT EXISTS idx_fila_status ON fila_processamento(status, disponivel_em);
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nivel TEXT NOT NULL,
                evento TEXT NOT NULL,
                mensagem TEXT NOT NULL,
                detalhes TEXT,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection
