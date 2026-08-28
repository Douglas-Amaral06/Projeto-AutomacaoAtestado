"""Executa localmente o simulador do contrato Volume + JSON."""

import csv
from datetime import datetime, timedelta
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.databricks_delivery import LocalDeliverySimulator, SAO_PAULO


FIXTURES = ROOT / "data" / "fixtures"
OUTPUT = FIXTURES / "databricks_delivery"


def carregar_registros() -> list[dict[str, str]]:
    jovens = {}
    with (FIXTURES / "jovens_ficticios.csv").open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file, delimiter=";"):
            jovens[row["matricula"]] = row
    with (FIXTURES / "atestados_ficticios.csv").open(encoding="utf-8", newline="") as file:
        return [{**row, "nome": jovens[row["matricula"]]["nome"]} for row in csv.DictReader(file, delimiter=";")]


def simular_entregas(destino: Path = OUTPUT) -> list[tuple[Path, Path, dict]]:
    simulator = LocalDeliverySimulator(destino)
    start = datetime(2026, 8, 21, 13, 0, 0, tzinfo=SAO_PAULO)
    return [simulator.deliver(registro, start + timedelta(seconds=index)) for index, registro in enumerate(carregar_registros())]


if __name__ == "__main__":
    deliveries = simular_entregas()
    print(f"Arquivos gerados: {len(deliveries) * 2}")
    print(f"JSONs válidos: {len(deliveries)}")
    print(f"Documentos válidos: {len(deliveries)}")
    print(f"Pares JSON/documento: {len(deliveries)}")
    print("\nJSON de exemplo:\n")
    print(deliveries[0][2])
