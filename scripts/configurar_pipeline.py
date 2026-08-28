import argparse
from pathlib import Path

from dotenv import set_key


def validated_workbook(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".xlsx":
        raise argparse.ArgumentTypeError("Informe um arquivo XLSX existente.")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Configura fontes locais do pipeline sem gravar caminhos no codigo.")
    parser.add_argument("--atestados", required=True, type=validated_workbook)
    parser.add_argument("--base-geral", required=True, type=validated_workbook)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    set_key(env_path, "PIPELINE_ATESTADOS_PATH", str(args.atestados), quote_mode="always")
    set_key(env_path, "PIPELINE_BASE_GERAL_PATH", str(args.base_geral), quote_mode="always")
    print("Fontes do pipeline configuradas no .env. Caminhos nao foram exibidos.")


if __name__ == "__main__":
    main()
