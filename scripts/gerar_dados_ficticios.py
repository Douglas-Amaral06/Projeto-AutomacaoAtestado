"""Gera e classifica massa local sintética para validar Curso × Trabalho."""

import csv
from datetime import date, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.absence import determinar_tipo_ausencia, nome_dia_semana


DEFAULT_OUTPUT = ROOT / "data" / "fixtures"
DIAS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira")


def jovens_ficticios() -> list[dict[str, str]]:
    return [
        {
            "matricula": str(90001 + indice),
            "nome": f"Aprendiz Fictício {indice + 1:02d}",
            "dia_curso": DIAS[indice % len(DIAS)],
        }
        for indice in range(25)
    ]


def atestados_ficticios(jovens: list[dict[str, str]]) -> list[dict[str, str]]:
    inicio = date(2026, 8, 17)  # segunda-feira
    registros = []
    for indice, jovem in enumerate(jovens, start=1):
        dia_curso = DIAS.index(jovem["dia_curso"])
        data_curso = inicio + timedelta(days=dia_curso)
        data_trabalho = inicio + timedelta(days=(dia_curso + 1) % len(DIAS))
        registros.extend((
            {"id_atestado": f"F{indice:03d}A", "matricula": jovem["matricula"], "data_atestado": data_curso.isoformat()},
            {"id_atestado": f"F{indice:03d}B", "matricula": jovem["matricula"], "data_atestado": data_trabalho.isoformat()},
        ))
    return registros


def escrever_csv(destino: Path, campos: list[str], linhas: list[dict[str, str]]) -> None:
    with destino.open("w", newline="", encoding="utf-8") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=campos, delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)


def gerar_massa(destino: Path = DEFAULT_OUTPUT) -> list[dict[str, str]]:
    destino.mkdir(parents=True, exist_ok=True)
    jovens = jovens_ficticios()
    atestados = atestados_ficticios(jovens)
    por_matricula = {jovem["matricula"]: jovem for jovem in jovens}
    resultado = []
    for atestado in atestados:
        jovem = por_matricula[atestado["matricula"]]
        resultado.append({
            **atestado,
            "nome": jovem["nome"],
            "dia_curso": jovem["dia_curso"],
            "dia_semana": nome_dia_semana(atestado["data_atestado"]),
            "tipo_ausencia": determinar_tipo_ausencia(atestado["data_atestado"], jovem["dia_curso"]),
        })
    escrever_csv(destino / "jovens_ficticios.csv", ["matricula", "nome", "dia_curso"], jovens)
    escrever_csv(destino / "atestados_ficticios.csv", ["id_atestado", "matricula", "data_atestado"], atestados)
    escrever_csv(destino / "resultado_ausencias_ficticias.csv", ["id_atestado", "matricula", "nome", "dia_curso", "data_atestado", "dia_semana", "tipo_ausencia"], resultado)
    return resultado


if __name__ == "__main__":
    linhas = gerar_massa()
    print("MATRÍCULA | NOME                 | DIA CURSO      | DATA       | DIA SEMANA     | TIPO")
    for linha in linhas[:10]:
        print(f"{linha['matricula']} | {linha['nome']:<20} | {linha['dia_curso']:<14} | {linha['data_atestado']} | {linha['dia_semana']:<14} | {linha['tipo_ausencia']}")
    print(f"\nJovens fictícios: 25 | Atestados fictícios: {len(linhas)}")
