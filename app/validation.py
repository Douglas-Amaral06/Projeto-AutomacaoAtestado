import re
from datetime import date, datetime


DOCUMENT_TYPES = {
    "atestado": "atestado_medico",
    "atestado medico": "atestado_medico",
    "atestado médico": "atestado_medico",
    "atestado_medico": "atestado_medico",
    "comprovante de hora": "comprovante_horas",
    "comprovante de horas": "comprovante_horas",
    "comprovante_horas": "comprovante_horas",
}


def document_type(value) -> str | None:
    normalized = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return DOCUMENT_TYPES.get(normalized)


def valid_cpf(value) -> bool:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(digits[index]) * (size + 1 - index) for index in range(size))
        check = (total * 10) % 11
        if check == 10:
            check = 0
        if check != int(digits[size]):
            return False
    return True


def normalize_cpf(value) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits or None


def normalize_cid(value) -> str | None:
    compact = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    if not compact:
        return None
    if not re.fullmatch(r"[A-Z]\d{2}[A-Z0-9]{0,2}", compact):
        return compact
    return compact[:3] + (f".{compact[3:]}" if len(compact) > 3 else "")


def validation_summary(record, today: date | None = None) -> dict:
    """Retorna alertas para exibicao e bloqueios para aprovacao."""
    # sqlite3.Row oferece acesso por indice/chave, mas nao implementa .get().
    # Normalizar aqui permite reutilizar a validacao em telas e fluxos do banco.
    record = dict(record)
    today = today or date.today()
    errors, warnings = [], []
    kind = document_type(record.get("tipo_documento"))
    name = str(record.get("nome") or "").strip()
    cpf = record.get("cpf")
    document_date = str(record.get("data_atestado") or "").strip()
    raw_days = record.get("dias_afastamento")
    cid = normalize_cid(record.get("cid"))

    if not kind:
        errors.append("Selecione Atestado Médico ou Comprovante de Horas.")
    if not name:
        errors.append("Nome é obrigatório.")
    if not valid_cpf(cpf):
        errors.append("CPF inválido ou ausente.")
    if cid and not re.fullmatch(r"[A-Z]\d{2}(?:\.[A-Z0-9]{1,2})?", cid):
        errors.append("CID inválido. Use um código como N39.0 ou Z00.")
    parsed_date = None
    if not document_date:
        errors.append("Data do documento é obrigatória.")
    else:
        try:
            parsed_date = datetime.strptime(document_date, "%Y-%m-%d").date()
            if parsed_date > today:
                errors.append("A data do documento não pode ser futura.")
        except ValueError:
            errors.append("Data do documento inválida.")

    days = None
    if raw_days not in (None, ""):
        try:
            days = int(raw_days)
            if days < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Dias de afastamento deve ser um número inteiro não negativo.")
    if kind == "atestado_medico" and days is None:
        errors.append("Dias de afastamento é obrigatório para Atestado Médico.")

    enrichment = str(record.get("status_enriquecimento") or "")
    if enrichment in {"NAO_ENCONTRADO", "REVISAR_DUPLICIDADE"}:
        errors.append("Nome não localizado de forma única na Base Geral.")
    elif enrichment == "BASE_NAO_CONFIGURADA":
        warnings.append("Base Geral não configurada; não foi possível localizar o nome.")
    elif enrichment == "DADOS_INSUFICIENTES":
        warnings.append("Dados insuficientes para localizar o nome na Base Geral.")
    if record.get("possivel_repeticao"):
        warnings.append("Possível documento repetido. Confira o histórico antes de aprovar; o reenvio não foi bloqueado.")

    inss_ping = kind == "atestado_medico" and days is not None and days > 15
    if inss_ping:
        warnings.append("PING INSS: afastamento superior a 15 dias requer encaminhamento.")
    return {
        "document_type": kind,
        "errors": errors,
        "warnings": warnings,
        "inss_ping": inss_ping,
        "is_valid": not errors,
        "label": "PING INSS" if inss_ping else ("PENDÊNCIA" if errors else ("ATENÇÃO" if warnings else "OK")),
    }
