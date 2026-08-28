from datetime import date
import sqlite3

from app.validation import normalize_cid, normalize_cpf, validation_summary, valid_cpf


def test_valid_cpf_uses_official_check_digits():
    assert valid_cpf("529.982.247-25")
    assert not valid_cpf("529.982.247-24")
    assert not valid_cpf("111.111.111-11")


def test_document_identifiers_are_normalized_and_cid_is_validated():
    assert normalize_cpf("529.982.247-25") == "52998224725"
    assert normalize_cid(" n39-0 ") == "N39.0"
    assert normalize_cid("z00") == "Z00"
    result = validation_summary({
        "tipo_documento": "atestado_medico", "nome": "Pessoa", "cpf": "52998224725",
        "cid": "39N", "data_atestado": "2026-08-14", "dias_afastamento": 1,
    }, today=date(2026, 8, 14))
    assert "CID inválido. Use um código como N39.0 ou Z00." in result["errors"]


def test_possible_duplicate_is_warning_only():
    result = validation_summary({
        "tipo_documento": "atestado_medico", "nome": "Pessoa", "cpf": "52998224725",
        "cid": "N39.0", "data_atestado": "2026-08-14", "dias_afastamento": 1,
        "possivel_repeticao": True,
    }, today=date(2026, 8, 14))
    assert result["is_valid"]
    assert any("Possível documento repetido" in warning for warning in result["warnings"])


def test_medical_certificate_requires_days_and_blocks_future_date():
    result = validation_summary({
        "tipo_documento": "atestado_medico", "nome": "Pessoa", "cpf": "52998224725",
        "data_atestado": "2030-01-01", "dias_afastamento": "",
    }, today=date(2026, 8, 14))
    assert not result["is_valid"]
    assert "A data do documento não pode ser futura." in result["errors"]
    assert "Dias de afastamento é obrigatório para Atestado Médico." in result["errors"]


def test_comprovante_does_not_require_days_and_inss_is_prioritized():
    comprovante = validation_summary({
        "tipo_documento": "comprovante_horas", "nome": "Pessoa", "cpf": "52998224725",
        "data_atestado": "2026-08-14", "dias_afastamento": "",
    }, today=date(2026, 8, 14))
    assert comprovante["is_valid"]
    assert not comprovante["inss_ping"]

    inss = validation_summary({
        "tipo_documento": "atestado_medico", "nome": "Pessoa", "cpf": "52998224725",
        "data_atestado": "2026-08-14", "dias_afastamento": 16,
    }, today=date(2026, 8, 14))
    assert inss["inss_ping"]
    assert inss["label"] == "PING INSS"


def test_validation_accepts_sqlite_row_from_dashboard_query():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE documento (tipo_documento, nome, cpf, data_atestado, dias_afastamento)")
    connection.execute(
        "INSERT INTO documento VALUES (?, ?, ?, ?, ?)",
        ("atestado_medico", "Pessoa Teste", "52998224725", "2026-08-14", 1),
    )
    row = connection.execute("SELECT * FROM documento").fetchone()
    assert validation_summary(row, today=date(2026, 8, 14))["is_valid"]
    connection.close()
