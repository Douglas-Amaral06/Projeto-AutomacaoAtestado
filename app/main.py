import hashlib
import io
import json
import os
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pyotp
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from pydantic import BaseModel

from .database import BASE_DIR, UPLOAD_DIR, connect, initialize_database
from .gemini_service import QuotaExceededError
from .maintenance import BACKUP_DIR, apply_retention, create_backup, prune_backups
from .processing import add_log, process_queue_item, resume_pending_once
from .security import (SESSION_COOKIE, attempt_retry_after, create_session, current_user, decrypt_totp,
                       encrypt_totp, hash_password, hash_token, is_login_blocked, login_key, record_login,
                       is_attempt_blocked, require_csrf, utc_now, verify_password, verify_service_token)

load_dotenv(BASE_DIR / ".env")
initialize_database()
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
_worker_stop = threading.Event()


def queue_worker():
    while not _worker_stop.is_set():
        try: resume_pending_once()
        except Exception: pass
        _worker_stop.wait(20)


def maintenance_worker():
    while not _worker_stop.is_set():
        try:
            interval = max(1, int(os.getenv("BACKUP_INTERVAL_HOURS", "24"))) * 3600
            newest = max((item.stat().st_mtime for item in BACKUP_DIR.glob("*.zip")), default=0)
            if time.time() - newest >= interval:
                backup = create_backup()
                retention = apply_retention()
                removed = prune_backups()
                add_log("info", "manutencao_concluida", "Backup automatico verificado e manutencao executada", {
                    "backup": backup.name, "retencao": retention, "backups_removidos": removed,
                })
        except Exception as error:
            try: add_log("erro", "manutencao_falhou", str(error))
            except Exception: pass
        _worker_stop.wait(3600)


@asynccontextmanager
async def lifespan(_app):
    _worker_stop.clear()
    thread = threading.Thread(target=queue_worker, daemon=True, name="fila-atestados")
    maintenance = threading.Thread(target=maintenance_worker, daemon=True, name="manutencao-atestados")
    thread.start()
    maintenance.start()
    yield
    _worker_stop.set()


app = FastAPI(title="Recebimento Seguro de Atestados", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(","))
app.add_middleware(CORSMiddleware, allow_origins=[], allow_origin_regex=r"chrome-extension://.*", allow_methods=["POST","GET"], allow_headers=["Content-Type","X-API-Token"])
ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}


def detected_mime(path: Path) -> str | None:
    head = path.read_bytes()[:16]
    if head.startswith(b"%PDF-"): return "application/pdf"
    if head.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP": return "image/webp"
    return None


def require_admin(user) -> None:
    if user["perfil"] != "admin": raise HTTPException(403, "Acesso exclusivo do administrador")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update({
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; frame-ancestors 'self'; form-action 'self'",
        "Cache-Control": "no-store",
    })
    if os.getenv("COOKIE_SECURE", "false").lower() == "true":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def web_user(request: Request):
    try: return current_user(request)
    except HTTPException: return None


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, erro: str = ""):
    if web_user(request): return RedirectResponse("/", 303)
    return templates.TemplateResponse(request=request, name="login.html", context={"erro": erro})


@app.post("/login")
def login(request: Request, usuario: str = Form(...), senha: str = Form(...), codigo_2fa: str = Form(...)):
    key = login_key(request, usuario)
    if is_login_blocked(key): return RedirectResponse("/login?erro=Bloqueado+por+15+minutos", 303)
    with connect() as connection:
        user = connection.execute("SELECT * FROM usuarios WHERE usuario=? AND ativo=1", (usuario.strip(),)).fetchone()
    valid = bool(user and verify_password(user["senha_hash"], senha))
    if valid:
        valid = pyotp.TOTP(decrypt_totp(user["totp_secret_encrypted"])).verify(codigo_2fa.strip(), valid_window=1)
    record_login(key, valid)
    if not valid: return RedirectResponse("/login?erro=Credenciais+invalidas", 303)
    token, _, expires = create_session(user["id"], request)
    with connect() as connection: connection.execute("UPDATE usuarios SET ultimo_login=? WHERE id=?", (utc_now().isoformat(), user["id"]))
    response = RedirectResponse("/", 303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=os.getenv("COOKIE_SECURE","false").lower()=="true", samesite="strict", max_age=8*3600, path="/")
    add_log("info", "login", f"Login realizado por usuario #{user['id']}")
    return response


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    user = current_user(request); require_csrf(request, user, csrf_token)
    with connect() as connection: connection.execute("DELETE FROM sessoes WHERE id=?", (user["session_id"],))
    response = RedirectResponse("/login", 303); response.delete_cookie(SESSION_COOKIE); return response


@app.get("/usuarios", response_class=HTMLResponse)
def users_page(request: Request):
    user=web_user(request)
    if not user:return RedirectResponse("/login",303)
    require_admin(user)
    with connect() as connection: rows=connection.execute("SELECT id,usuario,nome,perfil,ativo,criado_em,ultimo_login FROM usuarios ORDER BY nome").fetchall()
    return templates.TemplateResponse(request=request,name="users.html",context={"usuarios":rows,"user":user,"csrf":user["csrf_token"],"provisioning_uri":None})


@app.post("/usuarios")
def create_user(request:Request,csrf_token:str=Form(...),usuario:str=Form(...),nome:str=Form(...),senha:str=Form(...),perfil:str=Form(...)):
    admin=current_user(request); require_csrf(request,admin,csrf_token); require_admin(admin)
    if perfil not in {"admin","analista"}:raise HTTPException(400,"Perfil invalido")
    secret=pyotp.random_base32()
    try:
        with connect() as connection: uid=connection.execute("INSERT INTO usuarios(usuario,nome,senha_hash,totp_secret_encrypted,perfil) VALUES(?,?,?,?,?)",(usuario.strip(),nome.strip(),hash_password(senha),encrypt_totp(secret),perfil)).lastrowid
    except Exception as error:
        raise HTTPException(400,"Usuario existente ou dados invalidos") from error
    add_log("info","usuario_criado",f"Usuario #{uid} criado pelo administrador #{admin['id']}")
    with connect() as connection: rows=connection.execute("SELECT id,usuario,nome,perfil,ativo,criado_em,ultimo_login FROM usuarios ORDER BY nome").fetchall()
    uri=pyotp.TOTP(secret).provisioning_uri(usuario.strip(),issuer_name="RH Atestados")
    return templates.TemplateResponse(request=request,name="users.html",context={"usuarios":rows,"user":admin,"csrf":admin["csrf_token"],"provisioning_uri":uri})


@app.post("/usuarios/{user_id}/alternar")
def toggle_user(user_id:int,request:Request,csrf_token:str=Form(...)):
    admin=current_user(request);require_csrf(request,admin,csrf_token);require_admin(admin)
    if user_id==admin["id"]:raise HTTPException(400,"Nao desative a propria conta")
    with connect() as connection:
        target=connection.execute("SELECT perfil,ativo FROM usuarios WHERE id=?",(user_id,)).fetchone()
        if not target:raise HTTPException(404,"Usuario inexistente")
        if target["perfil"]=="admin" and target["ativo"]:
            active_admins=connection.execute("SELECT COUNT(*) FROM usuarios WHERE perfil='admin' AND ativo=1").fetchone()[0]
            if active_admins<=1:raise HTTPException(400,"O sistema deve manter ao menos um administrador ativo")
        connection.execute("UPDATE usuarios SET ativo=CASE ativo WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(user_id,))
        connection.execute("DELETE FROM sessoes WHERE usuario_id=?",(user_id,))
    add_log("info","usuario_alternado",f"Status do usuario #{user_id} alterado por #{admin['id']}");return RedirectResponse("/usuarios",303)


@app.get("/extensao", response_class=HTMLResponse)
def extension_pairing_page(request: Request):
    user=web_user(request)
    if not user:return RedirectResponse("/login",303)
    require_admin(user)
    with connect() as connection: tokens=connection.execute("SELECT id,nome,ativo,criado_em,ultimo_uso FROM tokens_servico ORDER BY id DESC").fetchall()
    return templates.TemplateResponse(request=request,name="pairing.html",context={"user":user,"csrf":user["csrf_token"],"codigo":None,"tokens":tokens})


@app.post("/extensao/gerar-codigo", response_class=HTMLResponse)
def generate_pairing_code(request:Request,csrf_token:str=Form(...)):
    admin=current_user(request);require_csrf(request,admin,csrf_token);require_admin(admin)
    code=f"{secrets.randbelow(1_000_000):06d}"
    expires=utc_now()+timedelta(minutes=10)
    with connect() as connection:
        connection.execute("DELETE FROM codigos_pareamento WHERE criado_por=? AND usado_em IS NULL",(admin["id"],))
        connection.execute("INSERT INTO codigos_pareamento(codigo_hash,criado_por,expira_em) VALUES(?,?,?)",(hash_token(code),admin["id"],expires.isoformat()))
        tokens=connection.execute("SELECT id,nome,ativo,criado_em,ultimo_uso FROM tokens_servico ORDER BY id DESC").fetchall()
    add_log("info","pareamento_criado",f"Codigo de pareamento criado pelo administrador #{admin['id']}")
    return templates.TemplateResponse(request=request,name="pairing.html",context={"user":admin,"csrf":admin["csrf_token"],"codigo":code,"tokens":tokens})


@app.post("/extensao/tokens/{token_id}/revogar")
def revoke_extension_token(token_id:int,request:Request,csrf_token:str=Form(...)):
    admin=current_user(request);require_csrf(request,admin,csrf_token);require_admin(admin)
    with connect() as connection:connection.execute("UPDATE tokens_servico SET ativo=0 WHERE id=?",(token_id,))
    add_log("aviso","token_revogado",f"Token de extensao #{token_id} revogado por #{admin['id']}");return RedirectResponse("/extensao",303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, status: str = ""):
    user = web_user(request)
    if not user: return RedirectResponse("/login", 303)
    params, where = [], ""
    if status: where, params = "WHERE a.status=?", [status]
    with connect() as connection:
        rows = connection.execute(f"SELECT a.*,u.nome revisor_nome FROM atestados a LEFT JOIN usuarios u ON u.id=a.revisado_por {where} ORDER BY a.id DESC", params).fetchall()
        queue = connection.execute("SELECT status,COUNT(*) quantidade FROM fila_processamento GROUP BY status").fetchall()
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"atestados":rows,"queue":queue,"user":user,"csrf":user["csrf_token"]})


@app.get("/atestados/{record_id}", response_class=HTMLResponse)
def review_page(record_id: int, request: Request):
    user = web_user(request)
    if not user: return RedirectResponse("/login",303)
    with connect() as connection: row=connection.execute("SELECT * FROM atestados WHERE id=?",(record_id,)).fetchone()
    if not row: raise HTTPException(404,"Registro inexistente")
    return templates.TemplateResponse(request=request,name="review.html",context={"item":row,"user":user,"csrf":user["csrf_token"]})


@app.post("/atestados/{record_id}/revisar")
def review_document(record_id:int, request:Request, acao:str=Form(...), csrf_token:str=Form(...), nome:str=Form(""), cpf:str=Form(""), cid:str=Form(""), dias_afastamento:str=Form(""), data_atestado:str=Form(""), observacoes:str=Form(""), motivo_rejeicao:str=Form("")):
    user=current_user(request); require_csrf(request,user,csrf_token)
    if acao not in {"aprovar","rejeitar"}: raise HTTPException(400,"Acao invalida")
    if acao=="rejeitar" and not motivo_rejeicao.strip(): raise HTTPException(400,"Informe o motivo")
    status="confirmado" if acao=="aprovar" else "rejeitado"
    days=int(dias_afastamento) if dias_afastamento.strip() else None
    with connect() as connection:
        before=connection.execute("SELECT * FROM atestados WHERE id=?",(record_id,)).fetchone()
        connection.execute("""UPDATE atestados SET nome=?,cpf=?,cid=?,dias_afastamento=?,data_atestado=?,observacoes=?,status=?,motivo_rejeicao=?,revisado_por=?,revisado_em=? WHERE id=?""",(nome.strip() or None,cpf.strip() or None,cid.strip() or None,days,data_atestado.strip() or None,observacoes.strip() or None,status,motivo_rejeicao.strip() or None,user["id"],utc_now().isoformat(),record_id))
    add_log("info","revisao",f"Atestado #{record_id} {status} por usuario #{user['id']}",{"campos_alterados":[k for k in ("nome","cpf","cid","dias_afastamento","data_atestado","observacoes") if str(before[k] or "")!=str(locals()[k] or "")]})
    return RedirectResponse("/",303)


@app.post("/atestados/{record_id}/excluir")
def delete_document(record_id:int, request:Request, csrf_token:str=Form(...)):
    user=current_user(request); require_csrf(request,user,csrf_token)
    with connect() as connection:
        item=connection.execute("SELECT arquivo_salvo,arquivo_hash FROM atestados WHERE id=?",(record_id,)).fetchone()
        if not item:raise HTTPException(404,"Registro inexistente")
        connection.execute("DELETE FROM fila_processamento WHERE atestado_id=? OR arquivo_hash=?",(record_id,item["arquivo_hash"]))
        connection.execute("DELETE FROM atestados WHERE id=?",(record_id,))
        still_used=connection.execute("SELECT COUNT(*) FROM atestados WHERE arquivo_salvo=?",(item["arquivo_salvo"],)).fetchone()[0]
    if not still_used:
        (UPLOAD_DIR/item["arquivo_salvo"]).unlink(missing_ok=True)
    add_log("aviso","atestado_excluido",f"Atestado #{record_id} e arquivo removidos pelo usuario #{user['id']}")
    return RedirectResponse("/",303)


@app.get("/atestados/{record_id}/arquivo")
def download_original(record_id:int, request:Request):
    if not web_user(request): return RedirectResponse("/login",303)
    with connect() as connection: row=connection.execute("SELECT arquivo_original,arquivo_salvo FROM atestados WHERE id=?",(record_id,)).fetchone()
    if not row: raise HTTPException(404,"Registro inexistente")
    return FileResponse(UPLOAD_DIR/row["arquivo_salvo"],filename=row["arquivo_original"],headers={"Content-Disposition":f"inline; filename=\"documento-{record_id}{Path(row['arquivo_original']).suffix}\""})


class LogEntry(BaseModel):
    nivel:str="info"; evento:str; mensagem:str; detalhes:dict|None=None


class PairingRequest(BaseModel):
    codigo: str
    nome: str = "Extensao Chrome"


@app.post("/api/parear")
def pair_extension(entry:PairingRequest,request:Request):
    key=login_key(request,"pareamento")
    if is_attempt_blocked(key,max_failures=3,window_minutes=30):
        retry=attempt_retry_after(key,30)
        add_log("aviso","pareamento_bloqueado","Origem bloqueada apos 3 tentativas invalidas de pareamento",{"aguarde_segundos":retry})
        raise HTTPException(429,{"codigo":"pareamento_bloqueado","mensagem":"Pareamento bloqueado apos 3 tentativas invalidas.","aguarde_segundos":retry})
    code=entry.codigo.strip()
    if len(code)!=6 or not code.isdigit():
        record_login(key,False);raise HTTPException(400,"Codigo invalido")
    with connect() as connection:
        row=connection.execute("SELECT * FROM codigos_pareamento WHERE codigo_hash=? AND usado_em IS NULL AND expira_em>?",(hash_token(code),utc_now().isoformat())).fetchone()
        if not row:
            record_login(key,False);raise HTTPException(401,"Codigo incorreto ou expirado")
        raw_token=secrets.token_urlsafe(48)
        token_id=connection.execute("INSERT INTO tokens_servico(nome,token_hash,criado_por) VALUES(?,?,?)",(entry.nome[:80],hash_token(raw_token),row["criado_por"])).lastrowid
        connection.execute("UPDATE codigos_pareamento SET usado_em=? WHERE id=?",(utc_now().isoformat(),row["id"]))
    record_login(key,True);add_log("info","extensao_pareada",f"Extensao #{token_id} pareada")
    return {"token":raw_token,"token_id":token_id}


@app.post("/api/logs")
def create_log(entry:LogEntry, request:Request):
    verify_service_token(request); add_log(entry.nivel,entry.evento,entry.mensagem,entry.detalhes); return {"ok":True}


@app.get("/api/extensao/status")
def extension_status(request:Request):
    verify_service_token(request)
    return {"conectada":True}


@app.get("/logs",response_class=HTMLResponse)
def logs_page(request:Request):
    user=web_user(request)
    if not user:return RedirectResponse("/login",303)
    require_admin(user)
    with connect() as connection: rows=connection.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 500").fetchall()
    return templates.TemplateResponse(request=request,name="logs.html",context={"logs":rows,"user":user,"csrf":user["csrf_token"]})


@app.post("/api/atestados")
def receive_document(request:Request,file:UploadFile=File(...)):
    verify_service_token(request)
    if file.content_type not in ALLOWED_TYPES: raise HTTPException(400,"Envie PDF, JPG, PNG ou WEBP")
    extension=Path(file.filename or "documento").suffix.lower(); stored_name=f"{uuid.uuid4().hex}{extension}"
    stored_path=UPLOAD_DIR/stored_name
    written=0
    with stored_path.open("wb") as output:
        while chunk:=file.file.read(1024*1024):
            written+=len(chunk)
            if written>15*1024*1024:
                output.close();stored_path.unlink(missing_ok=True);raise HTTPException(413,"Arquivo acima de 15 MB")
            output.write(chunk)
    actual_mime=detected_mime(stored_path)
    if actual_mime != file.content_type:
        stored_path.unlink(missing_ok=True); raise HTTPException(400,"Conteudo do arquivo nao corresponde ao tipo informado")
    digest=hashlib.sha256(stored_path.read_bytes()).hexdigest()
    with connect() as connection:
        existing=connection.execute("SELECT id FROM atestados WHERE arquivo_hash=?",(digest,)).fetchone()
        queued=connection.execute("SELECT id,status,atestado_id FROM fila_processamento WHERE arquivo_hash=?",(digest,)).fetchone()
        if existing or queued:
            stored_path.unlink(missing_ok=True); return {"id":existing["id"] if existing else queued["atestado_id"],"status":"duplicado"}
        cursor=connection.execute("INSERT INTO fila_processamento(arquivo_hash,arquivo_original,arquivo_salvo,mime_type,status) VALUES(?,?,?,?, 'processando')",(digest,file.filename or "documento",stored_name,file.content_type))
        queue_id=cursor.lastrowid
    try:return process_queue_item(queue_id)
    except QuotaExceededError as error: raise HTTPException(429,detail={"codigo":"gemini_quota_exceeded","mensagem":str(error),"aguarde_segundos":error.retry_after}) from error
    except Exception as error: raise HTTPException(503,detail={"codigo":"enfileirado","mensagem":"Falha temporaria; arquivo preservado para nova tentativa.","fila_id":queue_id}) from error


@app.post("/fila/retomar")
def resume_queue(request:Request,csrf_token:str=Form(...)):
    user=current_user(request); require_csrf(request,user,csrf_token)
    require_admin(user)
    with connect() as connection: connection.execute("UPDATE fila_processamento SET status='aguardando_retentativa',disponivel_em=? WHERE status IN ('pausado_quota','falhou')",(utc_now().isoformat(),))
    add_log("info","fila_retomada",f"Fila retomada por usuario #{user['id']}"); return RedirectResponse("/",303)


@app.get("/relatorios",response_class=HTMLResponse)
def reports(request:Request):
    user=web_user(request)
    if not user:return RedirectResponse("/login",303)
    with connect() as connection:
        summary=connection.execute("SELECT COUNT(*) total,SUM(CASE WHEN status='pendente' THEN 1 ELSE 0 END) pendentes,SUM(CASE WHEN status='confirmado' THEN 1 ELSE 0 END) confirmados,SUM(CASE WHEN status='rejeitado' THEN 1 ELSE 0 END) rejeitados,COALESCE(SUM(CASE WHEN status='confirmado' THEN dias_afastamento ELSE 0 END),0) dias FROM atestados").fetchone()
        monthly=connection.execute("SELECT substr(COALESCE(data_atestado,criado_em),1,7) mes,COUNT(*) quantidade,COALESCE(SUM(dias_afastamento),0) dias FROM atestados GROUP BY mes ORDER BY mes DESC LIMIT 12").fetchall()
        timing=connection.execute("SELECT ROUND(AVG((julianday(revisado_em)-julianday(criado_em))*24),2) horas FROM atestados WHERE revisado_em IS NOT NULL").fetchone()
    return templates.TemplateResponse(request=request,name="reports.html",context={"summary":summary,"monthly":monthly,"timing":timing,"user":user,"csrf":user["csrf_token"]})


@app.get("/exportar.xlsx")
def export_xlsx(request:Request):
    if not web_user(request):return RedirectResponse("/login",303)
    with connect() as connection: rows=connection.execute("SELECT nome,cpf,cid,dias_afastamento,data_atestado,status,observacoes,criado_em,revisado_em FROM atestados ORDER BY id").fetchall()
    workbook=Workbook(); sheet=workbook.active; sheet.title="Atestados"; sheet.append(["Nome","CPF","CID","Dias","Data","Status","Observacoes","Recebido em","Revisado em"])
    for row in rows:sheet.append(list(row))
    output=io.BytesIO();workbook.save(output);output.seek(0)
    return StreamingResponse(output,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":"attachment; filename=atestados.xlsx"})
