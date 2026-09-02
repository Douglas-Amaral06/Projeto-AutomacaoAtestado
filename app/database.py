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
            "matricula": "TEXT",
            "telefone": "TEXT",
            "email": "TEXT",
            "empresa": "TEXT",
            "tipo_documento": "TEXT",
            "status_enriquecimento": "TEXT",
            "crm": "TEXT",
            "crm_uf": "TEXT",
            "assinado": "INTEGER",
            "carimbado": "INTEGER",
            "operador_envio_id": "INTEGER",
            "id_documento": "TEXT",
            "status_entrega": "TEXT",
        }.items():
            if column not in columns:
                connection.execute(f"ALTER TABLE atestados ADD COLUMN {column} {definition}")
        connection.execute("DROP INDEX IF EXISTS idx_atestados_arquivo_hash")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_atestados_arquivo_hash ON atestados(arquivo_hash)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_atestados_id_documento ON atestados(id_documento)")
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
            CREATE TABLE IF NOT EXISTS recursos_lock (
                nome TEXT PRIMARY KEY,
                owner TEXT,
                expires_at TEXT
            );
            CREATE TABLE IF NOT EXISTS gemini_consumo (
                dia TEXT PRIMARY KEY,
                chamadas INTEGER NOT NULL DEFAULT 0,
                tokens_reservados INTEGER NOT NULL DEFAULT 0
            );
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
                arquivo_hash TEXT NOT NULL,
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
        user_columns = {row[1] for row in connection.execute("PRAGMA table_info(usuarios)").fetchall()}
        for column in ("ultimo_totp_login", "ultimo_totp_extensao"):
            if column not in user_columns:
                connection.execute(f"ALTER TABLE usuarios ADD COLUMN {column} INTEGER")
        token_columns = {row[1] for row in connection.execute("PRAGMA table_info(tokens_servico)").fetchall()}
        if "expira_em" not in token_columns:
            connection.execute("ALTER TABLE tokens_servico ADD COLUMN expira_em TEXT")
        queue_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(fila_processamento)").fetchall()
        }
        queue_extra_columns = {
            "id_mensagem": "TEXT", "id_conversa": "TEXT", "whatsapp_remetente": "TEXT",
            "data_recebimento": "TEXT", "unidade": "TEXT", "lock_token": "TEXT",
            "lock_expires_em": "TEXT", "erro_amigavel": "TEXT",
            "token_servico_id": "INTEGER", "operador_id": "INTEGER",
        }
        for column, definition in queue_extra_columns.items():
            if column not in queue_columns:
                connection.execute(f"ALTER TABLE fila_processamento ADD COLUMN {column} {definition}")
        _migrate_queue_hash_uniqueness(connection)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_fila_arquivo_hash ON fila_processamento(arquivo_hash)")
        connection.execute(
            """UPDATE fila_processamento SET id_mensagem=NULL
               WHERE id_mensagem IS NOT NULL AND id NOT IN (
                   SELECT MIN(id) FROM fila_processamento
                   WHERE id_mensagem IS NOT NULL GROUP BY id_mensagem
               )"""
        )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_fila_id_mensagem
               ON fila_processamento(id_mensagem) WHERE id_mensagem IS NOT NULL"""
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
        # Versões antigas persistiam mensagens cruas de exceção. Preserva o
        # evento e a data, removendo somente os campos que podiam conter segredos.
        connection.execute(
            """UPDATE logs SET mensagem='Falha histórica sanitizada.', detalhes=NULL
               WHERE evento IN ('processamento_falhou','manutencao_falhou','falha_interna')
                 AND mensagem NOT LIKE 'Falha de processamento. Referência: %'"""
        )
        connection.execute(
            """UPDATE fila_processamento SET ultimo_erro='Falha histórica sanitizada.'
               WHERE ultimo_erro IS NOT NULL
                 AND ultimo_erro<>'classificacao_documento_invalido'
                 AND ultimo_erro NOT LIKE 'Falha de processamento. Referência: %'"""
        )


def _migrate_queue_hash_uniqueness(connection: sqlite3.Connection) -> None:
    """Remove a unicidade antiga do SHA sem perder itens existentes da fila."""
    schema = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='fila_processamento'"
    ).fetchone()[0]
    compact = " ".join(schema.upper().split())
    if "ARQUIVO_HASH TEXT NOT NULL UNIQUE" not in compact:
        return
    connection.executescript(
        """
        ALTER TABLE fila_processamento RENAME TO fila_processamento_anterior;
        CREATE TABLE fila_processamento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo_hash TEXT NOT NULL,
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
            id_mensagem TEXT,
            id_conversa TEXT,
            whatsapp_remetente TEXT,
            data_recebimento TEXT,
            unidade TEXT,
            lock_token TEXT,
            lock_expires_em TEXT,
            erro_amigavel TEXT,
            FOREIGN KEY(atestado_id) REFERENCES atestados(id)
        );
        INSERT INTO fila_processamento(
            id,arquivo_hash,arquivo_original,arquivo_salvo,mime_type,status,
            tentativas,ultimo_erro,disponivel_em,atestado_id,criado_em,atualizado_em,
            id_mensagem,id_conversa,whatsapp_remetente,data_recebimento,unidade,
            lock_token,lock_expires_em,erro_amigavel
        )
        SELECT
            id,arquivo_hash,arquivo_original,arquivo_salvo,mime_type,status,
            tentativas,ultimo_erro,disponivel_em,atestado_id,criado_em,atualizado_em,
            id_mensagem,id_conversa,whatsapp_remetente,data_recebimento,NULL,
            NULL,NULL,NULL
        FROM fila_processamento_anterior;
        DROP TABLE fila_processamento_anterior;
        CREATE INDEX idx_fila_status ON fila_processamento(status, disponivel_em);
        """
    )


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection
