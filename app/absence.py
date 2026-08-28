"""Regra de negócio para classificar ausência de curso ou trabalho."""

from datetime import date, datetime
import re
import unicodedata


class AusenciaValidationError(ValueError):
    """Indica que a data do atestado ou o dia de curso não é utilizável."""


_DIAS_SEMANA = {
    "segunda": 0,
    "segunda feira": 0,
    "terca": 1,
    "terca feira": 1,
    "quarta": 2,
    "quarta feira": 2,
    "quinta": 3,
    "quinta feira": 3,
    "sexta": 4,
    "sexta feira": 4,
    "sabado": 5,
    "domingo": 6,
}
_NOMES_DIAS_SEMANA = (
    "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
    "sexta-feira", "sábado", "domingo",
)


def _normalizar_texto(valor: object) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texto.strip().casefold().replace("-", " "))


def normalizar_data_atestado(valor: date | datetime | str) -> date:
    """Converte datas brasileiras e ISO em ``date``.

    Formatos aceitos: ``DD/MM/AAAA``, ``AAAA-MM-DD`` e datetime ISO. Valores
    ausentes ou inválidos geram ``AusenciaValidationError`` para não serem
    classificados indevidamente como trabalho.
    """
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if not isinstance(valor, str) or not valor.strip():
        raise AusenciaValidationError("Data do atestado é obrigatória.")

    texto = valor.strip()
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except ValueError as error:
        raise AusenciaValidationError(f"Data do atestado inválida: {valor!r}.") from error


def normalizar_dia_curso(valor: object) -> int:
    """Retorna o índice ISO interno (segunda=0 ... domingo=6) do dia de curso."""
    dia = _DIAS_SEMANA.get(_normalizar_texto(valor))
    if dia is None:
        raise AusenciaValidationError(f"Dia de curso inválido: {valor!r}.")
    return dia


def nome_dia_semana(data_atestado: date | datetime | str) -> str:
    """Retorna o nome em português do dia da semana da data informada."""
    return _NOMES_DIAS_SEMANA[normalizar_data_atestado(data_atestado).weekday()]


def determinar_tipo_ausencia(data_atestado: date | datetime | str, dia_curso: object) -> str:
    """Classifica uma ausência como ``CURSO`` ou ``TRABALHO``.

    A classificação usa o dia calculado a partir de uma data real. Entradas
    inválidas levantam ``AusenciaValidationError`` e nunca retornam TRABALHO.
    """
    data = normalizar_data_atestado(data_atestado)
    return "CURSO" if data.weekday() == normalizar_dia_curso(dia_curso) else "TRABALHO"
