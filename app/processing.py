import json
import sqlite3
from datetime import timedelta
from pathlib import Path

from .database import UPLOAD_DIR, connect
from .gemini_service import QuotaExceededError, extract_document
from .security import redact, utc_now


def add_log(level: str, event: str, message: str, details: dict | None = None) -> None:
    safe_details = sanitize_log_details(details) if details else None
    with connect() as connection:
        connection.execute(
            "INSERT INTO logs(nivel,evento,mensagem,detalhes) VALUES(?,?,?,?)",
            (level[:20], event[:80], redact(message)[:1000], json.dumps(safe_details, ensure_ascii=False) if safe_details else None),
        )


def sanitize_log_details(value, key: str = ""):
    sensitive_keys = {"cpf", "nome", "conversa", "conversation", "filename", "arquivo", "token", "senha", "password", "secret", "codigo"}
    if key.casefold() in sensitive_keys:
        return "[DADO PROTEGIDO]"
    if isinstance(value, dict):
        return {str(item_key)[:80]: sanitize_log_details(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [sanitize_log_details(item) for item in value[:50]]
    if isinstance(value, str):
        return redact(value)[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return "[VALOR OMITIDO]"


def process_queue_item(queue_id: int) -> dict:
    with connect() as connection:
        item = connection.execute("SELECT * FROM fila_processamento WHERE id=?", (queue_id,)).fetchone()
        if not item:
            raise RuntimeError("Item da fila inexistente")
        connection.execute(
            "UPDATE fila_processamento SET status='processando', tentativas=tentativas+1, atualizado_em=? WHERE id=?",
            (utc_now().isoformat(), queue_id),
        )
    path = UPLOAD_DIR / item["arquivo_salvo"]
    try:
        extracted = extract_document(path)
        if not extracted.get("is_atestado"):
            path.unlink(missing_ok=True)
            with connect() as connection:
                connection.execute(
                    "UPDATE fila_processamento SET status='ignorado', ultimo_erro=?, atualizado_em=? WHERE id=?",
                    (extracted.get("motivo_classificacao"), utc_now().isoformat(), queue_id),
                )
            add_log("info", "arquivo_ignorado", f"Documento ignorado: {extracted.get('motivo_classificacao','nao e atestado')}")
            return {"id": None, "status": "ignorado", "motivo": extracted.get("motivo_classificacao"), "tipo_documento": extracted.get("tipo_documento")}

        with connect() as connection:
            cursor = connection.execute(
                """INSERT INTO atestados(nome,cpf,cid,dias_afastamento,data_atestado,
                   arquivo_original,arquivo_salvo,observacoes,confianca,arquivo_hash,dados_originais)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (extracted.get("nome"), extracted.get("cpf"), extracted.get("cid"),
                 extracted.get("dias_afastamento"), extracted.get("data_atestado"),
                 item["arquivo_original"], item["arquivo_salvo"], extracted.get("observacoes"),
                 extracted.get("confianca"), item["arquivo_hash"], json.dumps(extracted, ensure_ascii=False)),
            )
            atestado_id = cursor.lastrowid
            connection.execute(
                "UPDATE fila_processamento SET status='concluido',atestado_id=?,atualizado_em=? WHERE id=?",
                (atestado_id, utc_now().isoformat(), queue_id),
            )
        add_log("info", "atestado_salvo", f"Atestado #{atestado_id} salvo para conferencia")
        return {"id": atestado_id, "status": "pendente", "dados": extracted}
    except QuotaExceededError as error:
        with connect() as connection:
            connection.execute(
                "UPDATE fila_processamento SET status='pausado_quota',ultimo_erro=?,disponivel_em=?,atualizado_em=? WHERE id=?",
                (str(error), (utc_now() + timedelta(seconds=error.retry_after or 60)).isoformat(), utc_now().isoformat(), queue_id),
            )
        add_log("aviso", "tarefa_pausada_quota", "Fila pausada por limite da API Gemini", {"fila_id": queue_id, "aguarde_segundos": error.retry_after})
        raise
    except sqlite3.IntegrityError:
        path.unlink(missing_ok=True)
        with connect() as connection:
            existing = connection.execute("SELECT id FROM atestados WHERE arquivo_hash=?", (item["arquivo_hash"],)).fetchone()
            connection.execute("UPDATE fila_processamento SET status='duplicado',atestado_id=?,atualizado_em=? WHERE id=?", (existing["id"] if existing else None, utc_now().isoformat(), queue_id))
        return {"id": existing["id"] if existing else None, "status": "duplicado"}
    except Exception as error:
        with connect() as connection:
            attempts = connection.execute("SELECT tentativas FROM fila_processamento WHERE id=?", (queue_id,)).fetchone()[0]
            status = "falhou" if attempts >= 3 else "aguardando_retentativa"
            connection.execute(
                "UPDATE fila_processamento SET status=?,ultimo_erro=?,disponivel_em=?,atualizado_em=? WHERE id=?",
                (status, str(error)[:500], (utc_now() + timedelta(minutes=2)).isoformat(), utc_now().isoformat(), queue_id),
            )
        add_log("erro", "processamento_falhou", str(error), {"fila_id": queue_id})
        raise


def resume_pending_once() -> int:
    with connect() as connection:
        rows = connection.execute(
            "SELECT id FROM fila_processamento WHERE status='aguardando_retentativa' AND (disponivel_em IS NULL OR disponivel_em<=?) ORDER BY id LIMIT 5",
            (utc_now().isoformat(),),
        ).fetchall()
    for row in rows:
        try:
            process_queue_item(row["id"])
        except Exception:
            pass
    return len(rows)
