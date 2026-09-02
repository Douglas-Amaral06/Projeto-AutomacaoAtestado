import json
import os
import uuid
from datetime import timedelta
from pathlib import Path

from .database import UPLOAD_DIR, connect
from .gemini_service import QuotaExceededError, extract_document
from .safe_errors import format_safe_error
from .security import redact, utc_now
from .spreadsheet_pipeline import append_received_document, find_employee
from .validation import normalize_cid, normalize_cpf, validation_summary


class QueueItemBusyError(RuntimeError):
    pass


def understandable_error(error: Exception) -> str:
    """Traduz falhas técnicas sem expor credenciais ou detalhes internos."""
    if isinstance(error, QuotaExceededError) or getattr(error, "status_code", None) == 429:
        return "O serviço de leitura atingiu o limite temporário. Aguarde alguns minutos e reprocesse."
    if isinstance(error, FileNotFoundError):
        return "O arquivo original não foi encontrado na pasta local."
    if getattr(error, "status_code", None) in {401, 403}:
        return "A credencial do serviço de leitura está ausente, inválida ou expirada."
    if isinstance(error, (TimeoutError, ConnectionError)):
        return "Não foi possível comunicar com o serviço de leitura. Verifique a conexão e tente novamente."
    if isinstance(error, QueueItemBusyError):
        return "Este documento já está sendo processado por outra instância do servidor."
    return "A extração não pôde ser concluída. Consulte os logs técnicos e tente reprocessar."


def _claim_queue_item(queue_id: int):
    token = uuid.uuid4().hex
    now = utc_now()
    expires = now + timedelta(minutes=30)
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """UPDATE fila_processamento
               SET lock_token=?,lock_expires_em=?,status='processando',tentativas=tentativas+1,
                   ultimo_erro=NULL,erro_amigavel=NULL,atualizado_em=?
               WHERE id=? AND atestado_id IS NULL
                 AND (lock_token IS NULL OR lock_expires_em IS NULL OR lock_expires_em<=?)""",
            (token, expires.isoformat(), now.isoformat(), queue_id, now.isoformat()),
        )
        if cursor.rowcount != 1:
            exists = connection.execute("SELECT 1 FROM fila_processamento WHERE id=?", (queue_id,)).fetchone()
            if not exists:
                raise RuntimeError("Item da fila inexistente")
            raise QueueItemBusyError("Item já está em processamento")
        return connection.execute("SELECT * FROM fila_processamento WHERE id=?", (queue_id,)).fetchone(), token


def renew_queue_lease(queue_id: int, lock_token: str) -> None:
    now = utc_now()
    expires = now + timedelta(minutes=30)
    with connect() as connection:
        cursor = connection.execute(
            """UPDATE fila_processamento SET lock_expires_em=?,atualizado_em=?
               WHERE id=? AND lock_token=? AND atestado_id IS NULL""",
            (expires.isoformat(), now.isoformat(), queue_id, lock_token),
        )
    if cursor.rowcount != 1:
        raise QueueItemBusyError("A posse do item foi perdida durante o processamento")


def add_log(level: str, event: str, message: str, details: dict | None = None) -> None:
    if level.casefold() in {"erro", "error", "critical", "fatal"} and not message.startswith(
        "Falha de processamento. Referência: "
    ):
        message, correlation = format_safe_error(RuntimeError())
        details = {**(details or {}), **correlation}
    safe_details = sanitize_log_details(details) if details else None
    with connect() as connection:
        connection.execute(
            "INSERT INTO logs(nivel,evento,mensagem,detalhes) VALUES(?,?,?,?)",
            (level[:20], event[:80], redact(message)[:1000], json.dumps(safe_details, ensure_ascii=False) if safe_details else None),
        )


def sanitize_log_details(value, key: str = ""):
    sensitive_keys = {
        "cpf", "nome", "conversa", "conversation", "filename", "arquivo", "token",
        "senha", "password", "secret", "codigo", "error", "erro", "exception",
        "payload", "headers", "authorization", "url", "uri", "query",
    }
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
    item, lock_token = _claim_queue_item(queue_id)
    path = UPLOAD_DIR / item["arquivo_salvo"]
    try:
        if not path.is_file():
            raise FileNotFoundError(path.name)
        renew_queue_lease(queue_id, lock_token)
        extracted = extract_document(path)
        extracted["cpf"] = normalize_cpf(extracted.get("cpf"))
        extracted["cid"] = normalize_cid(extracted.get("cid"))
        if not extracted.get("is_atestado"):
            path.unlink(missing_ok=True)
            with connect() as connection:
                connection.execute(
                    """UPDATE fila_processamento SET status='ignorado',ultimo_erro=?,erro_amigavel=?,
                       lock_token=NULL,lock_expires_em=NULL,atualizado_em=? WHERE id=? AND lock_token=?""",
                    ("classificacao_documento_invalido", "O arquivo não foi reconhecido como atestado ou comprovante de horas.", utc_now().isoformat(), queue_id, lock_token),
                )
            add_log("info", "arquivo_ignorado", "Documento ignorado por classificação incompatível")
            return {"id": None, "status": "ignorado", "motivo": extracted.get("motivo_classificacao"), "tipo_documento": extracted.get("tipo_documento")}

        employee = None
        enrichment_status = "BASE_NAO_CONFIGURADA"
        try:
            employee, enrichment_status = find_employee(extracted.get("nome"), extracted.get("cpf"))
        except RuntimeError:
            pass
        employee = employee or {}
        validation = validation_summary({**extracted, "status_enriquecimento": enrichment_status})
        renew_queue_lease(queue_id, lock_token)
        spreadsheet_result = {"status": "desabilitada"}
        if os.getenv("SPREADSHEET_PIPELINE_ENABLED", "true").strip().lower() == "true":
            try:
                spreadsheet_result = append_received_document(
                    extracted, employee, item["arquivo_hash"], enrichment_status, validation
                )
            except (RuntimeError, OSError):
                # A planilha é uma saída auxiliar. Caminho ausente, arquivo ocupado
                # ou XLSX inválido não pode apagar uma extração válida nem impedir
                # que o documento chegue ao painel e ao storage configurado.
                spreadsheet_result = {"status": "indisponivel"}
                add_log(
                    "aviso",
                    "planilha_indisponivel",
                    "A planilha auxiliar não pôde ser atualizada; o processamento principal continuou.",
                    {"fila_id": queue_id},
                )

        renew_queue_lease(queue_id, lock_token)
        with connect() as connection:
            owner = connection.execute(
                "SELECT lock_token FROM fila_processamento WHERE id=?", (queue_id,)
            ).fetchone()
            if not owner or owner["lock_token"] != lock_token:
                raise QueueItemBusyError("A posse do item expirou durante o processamento")
            cursor = connection.execute(
                """INSERT INTO atestados(nome,cpf,cid,dias_afastamento,data_atestado,
                   arquivo_original,arquivo_salvo,observacoes,confianca,arquivo_hash,dados_originais,
                   matricula,telefone,email,empresa,tipo_documento,status_enriquecimento,
                   crm,crm_uf,assinado,carimbado,operador_envio_id,id_documento,status_entrega)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (extracted.get("nome"), extracted.get("cpf"), extracted.get("cid"),
                 extracted.get("dias_afastamento"), extracted.get("data_atestado"),
                 item["arquivo_original"], item["arquivo_salvo"], extracted.get("observacoes"),
                 extracted.get("confianca"), item["arquivo_hash"], json.dumps(extracted, ensure_ascii=False),
                 employee.get("matricula"), employee.get("telefone"), employee.get("email"),
                 employee.get("empresa"), extracted.get("tipo_documento"), enrichment_status,
                 extracted.get("crm"), extracted.get("crm_uf"), extracted.get("assinado"),
                 extracted.get("carimbado"), item["operador_id"],None,"aguardando_aprovacao"),
            )
            atestado_id = cursor.lastrowid
            connection.execute(
                """UPDATE fila_processamento SET status='concluido',atestado_id=?,ultimo_erro=NULL,
                   erro_amigavel=NULL,lock_token=NULL,lock_expires_em=NULL,atualizado_em=?
                   WHERE id=? AND lock_token=?""",
                (atestado_id, utc_now().isoformat(), queue_id, lock_token),
            )
        add_log("info", "atestado_salvo", f"Atestado #{atestado_id} salvo para conferencia", {"enriquecimento": enrichment_status, "planilha": spreadsheet_result["status"], "entrega": "aguardando_aprovacao"})
        return {
            "id": atestado_id,
            "status": "pendente",
            "dados": extracted,
            "enriquecimento": enrichment_status,
            "id_documento": None,
            "status_entrega": "aguardando_aprovacao",
        }
    except QuotaExceededError as error:
        safe_message, safe_details = format_safe_error(error)
        friendly = f"{understandable_error(error)} Referência: {safe_details['correlation_id']}"
        with connect() as connection:
            connection.execute(
                """UPDATE fila_processamento SET status='pausado_quota',ultimo_erro=?,erro_amigavel=?,
                   disponivel_em=?,lock_token=NULL,lock_expires_em=NULL,atualizado_em=? WHERE id=? AND lock_token=?""",
                (safe_message, friendly, (utc_now() + timedelta(seconds=error.retry_after or 60)).isoformat(), utc_now().isoformat(), queue_id, lock_token),
            )
        add_log("aviso", "tarefa_pausada_quota", safe_message, {**safe_details, "fila_id": queue_id, "aguarde_segundos": error.retry_after})
        raise
    except Exception as error:
        safe_message, safe_details = format_safe_error(error)
        friendly = f"{understandable_error(error)} Referência: {safe_details['correlation_id']}"
        with connect() as connection:
            attempts = connection.execute("SELECT tentativas FROM fila_processamento WHERE id=?", (queue_id,)).fetchone()[0]
            status = "falhou" if attempts >= 3 else "aguardando_retentativa"
            connection.execute(
                """UPDATE fila_processamento SET status=?,ultimo_erro=?,erro_amigavel=?,disponivel_em=?,
                   lock_token=NULL,lock_expires_em=NULL,atualizado_em=? WHERE id=? AND lock_token=?""",
                (status, safe_message, friendly, (utc_now() + timedelta(minutes=2)).isoformat(), utc_now().isoformat(), queue_id, lock_token),
            )
        add_log("erro", "processamento_falhou", safe_message, {**safe_details, "fila_id": queue_id})
        raise


def resume_pending_once() -> int:
    now = utc_now().isoformat()
    with connect() as connection:
        rows = connection.execute(
            """SELECT id FROM fila_processamento
               WHERE atestado_id IS NULL AND (
                   (status='aguardando_retentativa' AND (disponivel_em IS NULL OR disponivel_em<=?))
                   OR (status='processando' AND (
                       lock_token IS NULL OR lock_expires_em IS NULL OR lock_expires_em<=?
                   ))
               ) ORDER BY id LIMIT 5""",
            (now, now),
        ).fetchall()
    for row in rows:
        try:
            process_queue_item(row["id"])
        except Exception:
            pass
    return len(rows)
