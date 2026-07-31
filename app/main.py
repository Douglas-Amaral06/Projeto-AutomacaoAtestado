import shutil
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook

from .database import BASE_DIR, UPLOAD_DIR, connect, initialize_database
from .gemini_service import extract_document


load_dotenv(BASE_DIR / ".env")
initialize_database()

app = FastAPI(title="Recebimento de Atestados")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000"],
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["*"],
    allow_headers=["*"],
)
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM atestados ORDER BY id DESC"
        ).fetchall()
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={"atestados": rows}
    )


@app.post("/api/atestados")
def receive_document(file: UploadFile = File(...)):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Envie PDF, JPG, PNG ou WEBP")

    extension = Path(file.filename or "documento").suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{extension}"
    stored_path = UPLOAD_DIR / stored_name
    with stored_path.open("wb") as output:
        shutil.copyfileobj(file.file, output)

    try:
        extracted = extract_document(stored_path)
    except Exception as error:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(502, f"Falha na extracao: {error}") from error

    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO atestados (
                nome, cpf, cid, dias_afastamento, data_atestado,
                arquivo_original, arquivo_salvo, observacoes, confianca
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                extracted.get("nome"),
                extracted.get("cpf"),
                extracted.get("cid"),
                extracted.get("dias_afastamento"),
                extracted.get("data_atestado"),
                file.filename or "documento",
                stored_name,
                extracted.get("observacoes"),
                extracted.get("confianca"),
            ),
        )
        record_id = cursor.lastrowid

    return {"id": record_id, "status": "pendente", "dados": extracted}


@app.post("/atestados/{record_id}/revisar")
def review_document(
    record_id: int,
    nome: str = Form(""),
    cpf: str = Form(""),
    cid: str = Form(""),
    dias_afastamento: str = Form(""),
    data_atestado: str = Form(""),
    observacoes: str = Form(""),
):
    days = int(dias_afastamento) if dias_afastamento.strip() else None
    with connect() as connection:
        connection.execute(
            """
            UPDATE atestados SET nome=?, cpf=?, cid=?, dias_afastamento=?,
                data_atestado=?, observacoes=?, status='confirmado',
                revisado_em=? WHERE id=?
            """,
            (
                nome.strip() or None,
                cpf.strip() or None,
                cid.strip() or None,
                days,
                data_atestado.strip() or None,
                observacoes.strip() or None,
                datetime.now().isoformat(timespec="seconds"),
                record_id,
            ),
        )
    return RedirectResponse("/", status_code=303)


@app.get("/atestados/{record_id}/arquivo")
def download_original(record_id: int):
    with connect() as connection:
        row = connection.execute(
            "SELECT arquivo_original, arquivo_salvo FROM atestados WHERE id=?",
            (record_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Registro nao encontrado")
    return FileResponse(
        UPLOAD_DIR / row["arquivo_salvo"], filename=row["arquivo_original"]
    )


@app.get("/exportar.xlsx")
def export_xlsx():
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT nome, cpf, cid, dias_afastamento, data_atestado, status,
                   observacoes, criado_em, revisado_em
            FROM atestados ORDER BY id
            """
        ).fetchall()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Atestados"
    sheet.append(
        ["Nome", "CPF", "CID", "Dias", "Data", "Status", "Observacoes", "Recebido em", "Revisado em"]
    )
    for row in rows:
        sheet.append(list(row))
    output = BASE_DIR / "data" / "atestados_exportados.xlsx"
    workbook.save(output)
    return FileResponse(output, filename="atestados.xlsx")
