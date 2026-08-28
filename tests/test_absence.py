from datetime import date, datetime

import pytest

from app.absence import (
    AusenciaValidationError,
    determinar_tipo_ausencia,
    nome_dia_semana,
    normalizar_data_atestado,
)
from scripts.gerar_dados_ficticios import gerar_massa


@pytest.mark.parametrize(("data_atestado", "dia_curso", "esperado"), [
    ("14/08/2026", "sexta-feira", "CURSO"),
    ("13/08/2026", "SEXTA", "TRABALHO"),
    ("2026-08-18", " terça-feira ", "CURSO"),
    ("19/08/2026", "terça-feira", "TRABALHO"),
    ("17/08/2026", "segunda-feira", "CURSO"),
    ("18/08/2026", "terça-feira", "CURSO"),
    ("19/08/2026", "quarta-feira", "CURSO"),
    ("20/08/2026", "quinta-feira", "CURSO"),
    ("21/08/2026", "sexta-feira", "CURSO"),
])
def test_determinar_tipo_ausencia(data_atestado, dia_curso, esperado):
    assert determinar_tipo_ausencia(data_atestado, dia_curso) == esperado


def test_normaliza_data_real_e_iso_datetime():
    assert normalizar_data_atestado(date(2026, 8, 14)) == date(2026, 8, 14)
    assert normalizar_data_atestado(datetime(2026, 8, 14, 9, 30)) == date(2026, 8, 14)
    assert normalizar_data_atestado("2026-08-14T09:30:00Z") == date(2026, 8, 14)
    assert nome_dia_semana("14/08/2026") == "sexta-feira"


@pytest.mark.parametrize("valor", ["31/02/2026", None, ""])
def test_data_invalida_nunca_vira_trabalho(valor):
    with pytest.raises(AusenciaValidationError):
        determinar_tipo_ausencia(valor, "sexta-feira")


@pytest.mark.parametrize("valor", ["banana", "", None])
def test_dia_curso_invalido_retorna_erro_controlado(valor):
    with pytest.raises(AusenciaValidationError):
        determinar_tipo_ausencia("14/08/2026", valor)


def test_massa_ficticia_tem_25_jovens_50_atestados_e_classificacoes(tmp_path):
    linhas = gerar_massa(tmp_path)
    assert len(linhas) == 50
    assert {linha["tipo_ausencia"] for linha in linhas} == {"CURSO", "TRABALHO"}
    assert len({linha["matricula"] for linha in linhas}) == 25
    assert (tmp_path / "jovens_ficticios.csv").is_file()
    assert (tmp_path / "atestados_ficticios.csv").is_file()
    assert (tmp_path / "resultado_ausencias_ficticias.csv").is_file()
