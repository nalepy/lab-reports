# -*- coding: utf-8 -*-
"""Servidor FastAPI: API REST + observador de carpeta + re-escaneo manual.

El backend:
  - observa G:\\My Drive\\MyFiles\\lab por archivos nuevos (polling)
  - ingesta cada PDF (deduplicando copias por contenido y por persona+fecha)
  - expone /api/* para la interfaz web
  - POST /api/rescan fuerza una búsqueda de archivos nuevos
"""
import os
import time
import threading
import glob
import asyncio
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
import re

from .db import DB
from .assessment import build_assessment
from .sources import sources_for, sources_for_finding
from . import drugs as drugs_mod
from . import catalog
from . import ai_engine
from . import auth

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
DATA_DIR = HERE.parent / "data"
DB_PATH = DATA_DIR / "labs.db"
LIBRARY_DIR = DATA_DIR / "library"   # almacenamiento autocontenido (nube)
LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

LAB_FOLDER = os.environ.get(
    "LAB_FOLDER", r"G:\My Drive\MyFiles\lab")

app = FastAPI(title="Panel de Laboratorio Clínico", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"])

DATA_DIR.mkdir(parents=True, exist_ok=True)
db = DB(str(DB_PATH))

# ------------------------------------------------------------------ watcher

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
_scan_lock = threading.Lock()
_last_scan: dict = {"ts": 0, "new": 0, "status": ""}
_watch_errors: list[str] = []


def scan_folder(force: bool = False) -> dict:
    """Escanea la carpeta de laboratorio e ingesta archivos nuevos."""
    with _scan_lock:
        summary = {"checked": 0, "new_reports": 0, "new_files": 0,
                   "duplicates_removed": 0, "errors": [], "files": []}
        if not os.path.isdir(LAB_FOLDER):
            summary["errors"].append(
                f"La carpeta {LAB_FOLDER} no existe o no es accesible.")
            _last_scan.update({"ts": time.time(), **summary})
            return summary
        files = sorted(glob.glob(os.path.join(LAB_FOLDER, "*.pdf")))
        summary["checked"] = len(files)
        for path in files:
            summary["files"].append(os.path.basename(path))
            try:
                res = db.ingest(path, force=force, library_dir=str(LIBRARY_DIR))
            except Exception as e:  # noqa: BLE001
                summary["errors"].append(f"{os.path.basename(path)}: {e}")
                continue
            if res.get("status") == "duplicate":
                summary["duplicates_removed"] += 1
                # eliminar la copia duplicada del disco (el original se conserva)
                try:
                    os.remove(path)
                except OSError as e:
                    summary["errors"].append(
                        f"No se pudo eliminar duplicado {os.path.basename(path)}: {e}")
            if res.get("new_reports", 0) > 0:
                summary["new_files"] += 1
                summary["new_reports"] += res["new_reports"]
        _last_scan.update({"ts": time.time(), **summary})
        return summary


def _watch_loop():
    """Bucle de polling que detecta archivos nuevos."""
    seen = set()
    if os.path.isdir(LAB_FOLDER):
        seen = set(glob.glob(os.path.join(LAB_FOLDER, "*.pdf")))
    while True:
        time.sleep(POLL_INTERVAL)
        try:
            if not os.path.isdir(LAB_FOLDER):
                continue
            now_files = set(glob.glob(os.path.join(LAB_FOLDER, "*.pdf")))
            new = now_files - seen
            if new:
                scan_folder(force=False)
                seen = now_files
        except Exception as e:  # noqa: BLE001
            _watch_errors.append(str(e))


# ------------------------------------------------------------------ models

class MedIn(BaseModel):
    name: str
    dose: str = ""
    frequency: str = ""
    notes: str = ""


class MedDelete(BaseModel):
    id: int


# ------------------------------------------------------------------ api

@app.get("/api/status")
def status():
    return {
        "lab_folder": LAB_FOLDER,
        "exists": os.path.isdir(LAB_FOLDER),
        "last_scan": _last_scan,
        "watch_errors": _watch_errors[-5:],
        "poll_interval": POLL_INTERVAL,
    }


@app.post("/api/rescan")
def rescan(force: bool = False):
    """Botón manual: buscar e ingestar archivos nuevos (y borrar duplicados)."""
    return scan_folder(force=force)


@app.get("/api/persons")
def persons():
    return db.persons()


@app.get("/api/person/{pid}")
def person_detail(pid: int):
    p = db.person(pid)
    if not p:
        return JSONResponse({"error": "Persona no encontrada"}, status_code=404)
    tests = db.tests_for(pid)
    meds = db.meds_for(pid)
    reports = db.reports_for(pid)
    documents = db.documents_for(pid)
    assessment = build_assessment(p, tests, meds)
    # adjuntar fuentes a cada hallazgo
    for f in assessment["findings"]:
        f["sources"] = sources_for_finding(f)
    # adjuntar fuentes a cada recomendación (por título aproximado)
    for r in assessment["recommendations"]:
        r["sources"] = []
        if "DIABETES" in r["title"].upper() or "PREDIABETES" in r["title"].upper():
            r["sources"] = sources_for("diabetes_risk")
        elif "LDL" in r["title"].upper():
            r["sources"] = sources_for("ldl_high")
        elif "HDL" in r["title"].upper():
            r["sources"] = sources_for("hdl_low")
        elif "TRIGLIC" in r["title"].upper():
            r["sources"] = sources_for("trig_high")
        elif "RENAL" in r["title"].upper():
            r["sources"] = sources_for("renal_risk")
        elif "HEP" in r["title"].upper():
            r["sources"] = sources_for("fatty_liver_risk")
        elif "ANEMIA" in r["title"].upper():
            r["sources"] = sources_for("anemia_risk")
        elif "ACIDO" in r["title"].upper() or "ÚRICO" in r["title"].upper():
            r["sources"] = sources_for("gout_risk")
        elif "TIROID" in r["title"].upper():
            r["sources"] = sources_for("thyroid_risk")
        elif "PSA" in r["title"].upper():
            r["sources"] = sources_for("psa_risk")
        elif "PCR" in r["title"].upper():
            r["sources"] = sources_for("inflammation_risk")
        elif "TROPONINA" in r["title"].upper():
            r["sources"] = sources_for("troponin_risk")
        elif "POTASIO" in r["title"].upper():
            r["sources"] = sources_for("hyperkalemia_risk")
        elif "PLAQUETAS" in r["title"].upper():
            r["sources"] = sources_for("thrombocytopenia_risk")
        elif "ACTIVIDAD" in r["title"].upper() or "CAMINAR" in r["title"].upper():
            r["sources"] = sources_for("sedentary_risk")
        elif "PESO" in r["title"].upper():
            r["sources"] = sources_for("obesity_risk")
        elif "FUMAR" in r["title"].upper() or "TABAQ" in r["title"].upper():
            r["sources"] = sources_for("smoking_risk")
    return {
        "person": p,
        "reports": reports,
        "meds": meds,
        "documents": documents,
        "assessment": assessment,
    }


@app.get("/api/person/{pid}/report/{rid}")
def report_detail(pid: int, rid: int):
    rows = db.tests_for_report(rid)
    return {"report_id": rid, "tests": rows}


@app.post("/api/person/{pid}/meds")
def add_med(pid: int, med: MedIn):
    if not med.name.strip():
        return JSONResponse({"error": "Nombre de medicamento requerido"},
                            status_code=400)
    mid = db.add_med(pid, med.name.strip(), med.dose, med.frequency, med.notes)
    return {"id": mid, "ok": True}


@app.delete("/api/person/{pid}/meds/{mid}")
def del_med(pid: int, mid: int):
    ok = db.del_med(pid, mid)
    return {"ok": ok}


@app.get("/api/drugs/search")
def drug_search(q: str = ""):
    """Autocompletado (live search) desde el catálogo normalizado.

    Devuelve genérico + marcas (global/LATAM/BR/ES) + dosis reales (CIMA).
    Búsqueda por genérico, marca comercial o alias.
    """
    return catalog.search(q, limit=12)


# ------------------------------------------------------------------ IA

@app.get("/api/ai/models")
def ai_models():
    """Modelos de IA seleccionables (solo los dos permitidos)."""
    return ai_engine.model_options()


@app.post("/api/person/{pid}/ai-report")
def ai_report(pid: int, model: str = "deepseek", force: bool = False):
    """Genera el informe IA personalizado del paciente."""
    if model not in ai_engine.MODELS:
        return JSONResponse(
            {"error": "Modelo no permitido. Elija DeepSeek V4 Pro u Opus 4.8."},
            status_code=400)
    p = db.person(pid)
    if not p:
        return JSONResponse({"error": "Persona no encontrada"}, status_code=404)
    tests = db.tests_for(pid)
    meds = db.meds_for(pid)
    reports = db.reports_for(pid)
    assessment = build_assessment(p, tests, meds)
    try:
        report = ai_engine.generate_report(p, assessment, meds, reports,
                                           model_key=model, force=force,
                                           db=db)
        return report
    except ai_engine.AIError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# ------------------------------------------------------------------ upload

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# extensiones reconocidas -> tipo de documento
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
_OTHER_EXT = {".pdf", ".dcm", ".dicom", ".zip", ".doc", ".docx", ".xls",
              ".xlsx", ".txt", ".csv", ".nii", ".nii.gz", ".mhd", ".raw"}

ALLOWED_EXT = _IMAGE_EXT | _OTHER_EXT


def _kind_for(fname: str) -> str:
    ext = "." + fname.lower().rsplit(".", 1)[-1] if "." in fname else ""
    if ext in _IMAGE_EXT:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in (".dcm", ".dicom"):
        return "dicom"
    return "other"


@app.post("/api/person/{pid}/upload")
async def upload_file(pid: int, file: UploadFile = File(...)):
    """Sube cualquier tipo de estudio médico (PDF, imagen, RX, IRM/DICOM…)
    directamente para un paciente, aunque no esté en la carpeta monitoreada.

    - PDF: se intenta parsear como laboratorio; si no, queda como documento.
    - Imágenes y otros: se guardan como documento adjunto, visibles en el tab.
    """
    if not file.filename:
        return JSONResponse({"error": "Archivo sin nombre."}, status_code=400)
    ext = "." + file.filename.lower().rsplit(".", 1)[-1] if "." in file.filename else ""
    if ext not in ALLOWED_EXT:
        return JSONResponse(
            {"error": f"Tipo de archivo no soportado ({ext or 'sin extensión'}). "
                      f"Aceptados: PDF, imágenes (JPG, PNG, GIF, WEBP, BMP, TIFF), "
                      f"DICOM y otros."},
            status_code=400)
    p = db.person(pid)
    if not p:
        return JSONResponse({"error": "Persona no encontrada"}, status_code=404)

    # guardar en el directorio de uploads del paciente
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename)
    dest = UPLOAD_DIR / f"p{pid}_{int(time.time())}_{safe}"
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    kind = _kind_for(file.filename)
    size = len(content)

    # PDF: intentar parsear como laboratorio
    if kind == "pdf":
        try:
            res = db.ingest(str(dest), force=False, library_dir=str(LIBRARY_DIR))
            if res.get("status") == "duplicate":
                dest.unlink(missing_ok=True)
                return {"ok": True, "status": "duplicate",
                        "message": "Archivo duplicado (ya estaba registrado).",
                        "file": file.filename}
            if res.get("new_reports", 0) > 0:
                db.add_document(pid, file.filename, str(dest), "pdf", size,
                                "Informe de laboratorio ingerido.")
                return {"ok": True, "status": "ok",
                        "message": f"{res['new_reports']} informe(s) ingerido(s).",
                        "file": file.filename, "new_reports": res["new_reports"]}
            # no aportó informes nuevos: queda como documento adjunto
        except Exception as e:  # noqa: BLE001
            pass  # PDF no parseable: se guarda igual como documento

    # imágenes / DICOM / otros: documento adjunto
    doc_id = db.add_document(pid, file.filename, str(dest), kind, size,
                             "Estudio/imagen subida manualmente.")
    return {"ok": True, "status": "document",
            "message": f"Documento guardado como adjunto ({kind}).",
            "file": file.filename, "document_id": doc_id}


# ------------------------------------------------------------------ carpeta DICOM / zip

import zipfile


@app.post("/api/person/{pid}/upload-folder")
async def upload_folder(pid: int, files: list[UploadFile] = File(...),
                        notes: str = Form("")):
    """Sube una carpeta completa de estudios (DICOM u otros), preservando
    subcarpetas. El navegador envía cada archivo con su ruta relativa
    (webkitRelativePath) en 'path'.

    También acepta un único ZIP con la estructura interna (DICOMDIR, series,
    subcarpetas) que se descomprime y archiva.
    """
    if not files:
        return JSONResponse({"error": "No se recibieron archivos."},
                            status_code=400)
    p = db.person(pid)
    if not p:
        return JSONResponse({"error": "Persona no encontrada"}, status_code=404)

    group_id = f"{int(time.time())}_{pid}"
    group_dir = UPLOAD_DIR / f"grupo_{group_id}"
    group_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    dcm_count = 0
    skipped = 0

    for f in files:
        rel = f.filename or ""
        # normalizar y eliminar path traversal
        rel = rel.replace("\\", "/").lstrip("/")
        # proteger contra rutas absolutas de Windows (C:/..., C:Windows...)
        # y Unix (//etc/passwd)
        if re.match(r"^[A-Za-z]:", rel):
            rel = rel[2:].lstrip("/")
        parts = [x for x in rel.split("/")
                 if x and x not in (".", "..")]
        if not parts:
            skipped += 1
            continue
        safe_rel = os.path.join(*parts)
        if re.match(r"^[A-Za-z]:", safe_rel):
            # os.path.join restored the drive letter — rechazar
            skipped += 1
            continue
        dest = group_dir / safe_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # verificación de que el destino está dentro del grupo
        try:
            real_dest = os.path.realpath(dest)
            real_group = os.path.realpath(group_dir)
            if os.path.commonpath([real_dest, real_group]) != real_group:
                skipped += 1
                continue
        except (OSError, ValueError):
            skipped += 1
            continue
        try:
            data = await f.read()
            with open(dest, "wb") as fh:
                fh.write(data)
            saved += 1
            if dest.suffix.lower() in (".dcm", ".dicom"):
                dcm_count += 1
        except Exception:  # noqa: BLE001
            skipped += 1

    # si fue un ZIP, descomprimir en el grupo
    if saved == 1 and dcm_count == 0:
        zips = list(group_dir.glob("*.zip")) + list(group_dir.glob("*.ZIP"))
        if zips:
            zf = zips[0]
            try:
                with zipfile.ZipFile(zf) as z:
                    for m in z.namelist():
                        safe = os.path.basename(m) if "/" not in m else m
                        target = group_dir / safe
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if not m.endswith("/"):
                            with z.open(m) as src, open(target, "wb") as out:
                                out.write(src.read())
                            if target.suffix.lower() in (".dcm", ".dicom"):
                                dcm_count += 1
                zf.unlink()
                saved = len(list(group_dir.rglob("*"))) - 1
            except zipfile.BadZipFile:
                pass

    if saved == 0:
        import shutil
        shutil.rmtree(group_dir, ignore_errors=True)
        return JSONResponse({"error": "No se pudo guardar ningún archivo."},
                            status_code=500)

    # registrar como documento "carpeta" (el documento apunta al directorio)
    kind = "dicom_folder" if dcm_count else "folder"
    doc_id = db.add_document(
        pid, f"Carpeta {group_id}" + (f" ({dcm_count} DICOM)" if dcm_count else ""),
        str(group_dir), kind, saved,
        notes or f"Carpeta de estudio: {saved} archivo(s), {dcm_count} DICOM.")
    return {"ok": True, "status": "folder",
            "message": f"Carpeta guardada: {saved} archivo(s), {dcm_count} DICOM.",
            "file": f"Carpeta {group_id}", "document_id": doc_id,
            "files": saved, "dicom": dcm_count}


@app.get("/api/documents/{doc_id}/list")
def document_list(doc_id: int):
    """Lista el contenido de una carpeta de documentos (estructura)."""
    doc = db.document(doc_id)
    if not doc:
        return JSONResponse({"error": "Documento no encontrado"}, status_code=404)
    root = os.path.realpath(db.resolve_path(doc["stored_path"]))
    if not os.path.isdir(root):
        return JSONResponse({"error": "No es una carpeta"}, status_code=404)
    tree = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            tree.append({
                "path": os.path.join(rel_dir, fn) if rel_dir != "." else fn,
                "size": os.path.getsize(full),
                "ext": os.path.splitext(fn)[1].lower(),
            })
    return {"document_id": doc_id, "files": tree}


@app.get("/api/documents/{doc_id}/file/{relpath:path}")
def document_file_in_folder(doc_id: int, relpath: str):
    """Sirve un archivo dentro de una carpeta de documentos."""
    doc = db.document(doc_id)
    if not doc:
        return JSONResponse({"error": "Documento no encontrado"}, status_code=404)
    root = os.path.realpath(db.resolve_path(doc["stored_path"]))
    target = os.path.realpath(os.path.join(root, relpath))
    # contención robusta: target debe estar dentro de root (evita traversal por
    # hermanos como "<root>_evil" que 'startswith' aceptaría por error).
    try:
        contained = target == root or os.path.commonpath([target, root]) == root
    except ValueError:  # unidades distintas (Windows) → fuera de root
        contained = False
    if not contained or not os.path.isfile(target):
        return JSONResponse({"error": "Archivo no encontrado"}, status_code=404)
    ext = os.path.splitext(target)[1].lower()
    media = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".tif": "image/tiff", ".tiff": "image/tiff",
        ".dcm": "application/dicom", ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")
    return FileResponse(target, media_type=media)


@app.get("/api/person/{pid}/documents")
def documents(pid: int):
    return db.documents_for(pid)


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: int):
    doc = db.document(doc_id)
    if not doc:
        return JSONResponse({"error": "Documento no encontrado"}, status_code=404)
    path = db.resolve_path(doc["stored_path"])
    if not os.path.exists(path):
        return JSONResponse({"error": "Archivo no disponible en disco"},
                            status_code=404)
    media = {
        "image": "image/jpeg",
        "pdf": "application/pdf",
        "dicom": "application/dicom",
    }.get(doc["kind"], "application/octet-stream")
    # adivinar mime por extensión para imágenes
    if doc["kind"] == "image":
        ext = os.path.splitext(doc["orig_filename"])[1].lower()
        media = {
            ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
            ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff",
        }.get(ext, "image/jpeg")
    return FileResponse(path, media_type=media,
                        filename=doc["orig_filename"])


@app.delete("/api/person/{pid}/documents/{doc_id}")
def delete_document(pid: int, doc_id: int):
    doc = db.document(doc_id)
    if doc:
        path = db.resolve_path(doc["stored_path"])
        if os.path.isdir(path):
            import shutil
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    ok = db.del_document(pid, doc_id)
    return {"ok": ok}


# ------------------------------------------------------------------ dicomlibrary.com

import urllib.request
import urllib.parse


def _extract_study_uid(url: str) -> str | None:
    """Extrae el UID del estudio de un link de dicomlibrary.com.

    Formatos típicos:
      https://www.dicomlibrary.com/?study=1.2.826.0.1.3680043.8.1055.1....
      https://www.dicomlibrary.com/?manage=<key>
    """
    m = re.search(r"[?&]study=([0-9.]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]manage=([0-9a-f]+)", url)
    if m:
        return m.group(1)
    # estudio como segmento de la URL
    m = re.search(r"/study/([0-9.]+)", url)
    if m:
        return m.group(1)
    return None


@app.post("/api/person/{pid}/fetch-dicomlibrary")
async def fetch_dicomlibrary(pid: int, url: str = Form(...),
                             notes: str = Form("")):
    """Descarga un estudio de dicomlibrary.com a partir de su link.

    dicomlibrary.com NO expone una API pública estable: los estudios se ven
    en su visor web (MedDream) y la descarga requiere su sesión. Este endpoint:
      1) extrae el UID del estudio del link,
      2) intenta los endpoints de descarga conocidos,
      3) si no hay API, devuelve una guía clara para descargar el ZIP
         manualmente y subirlo (se acepta el ZIP de DICOM).
    """
    if not url:
        return JSONResponse({"error": "Falta la URL del estudio."},
                            status_code=400)
    p = db.person(pid)
    if not p:
        return JSONResponse({"error": "Persona no encontrada"}, status_code=404)

    uid = _extract_study_uid(url)
    if not uid:
        return JSONResponse(
            {"error": "No se pudo identificar el estudio en el link. "
                      "Use un link tipo https://www.dicomlibrary.com/?study=…"},
            status_code=400)

    # intentos de descarga conocidos (best-effort; suelen requerir sesión)
    candidates = [
        f"https://www.dicomlibrary.com/download?study={uid}",
        f"https://www.dicomlibrary.com/zip?study={uid}",
        f"https://www.dicomlibrary.com/dicom/download?study={uid}",
    ]
    for c in candidates:
        try:
            req = urllib.request.Request(c, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                ct = resp.headers.get("Content-Type", "")
                data = resp.read()
            if data and ("zip" in ct or b"PK\x03\x04" in data[:4]):
                fname = f"dicomlibrary_{uid}.zip"
                dest = UPLOAD_DIR / f"p{pid}_{int(time.time())}_{fname}"
                with open(dest, "wb") as f:
                    f.write(data)
                doc_id = db.add_document(pid, fname, str(dest), "dicom_zip",
                                         len(data),
                                         f"Descargado de dicomlibrary.com ({uid})")
                return {"ok": True, "status": "downloaded",
                        "message": f"Estudio descargado ({len(data)//1024} KB).",
                        "document_id": doc_id}
        except Exception:  # noqa: BLE001
            continue

    return {
        "ok": False,
        "status": "no_api",
        "message": ("dicomlibrary.com no permite descarga automática (su visor "
                    "requiere sesión y no hay API pública). Vaya al estudio, use "
                    "el botón 'Download Anonymized DICOM Study' para bajar el ZIP "
                    "y súbalo aquí: se descomprime y archiva automáticamente."),
        "study_uid": uid,
        "how_to": (
            "1. Abra el link en el navegador.\n"
            "2. Espere a que cargue el visor.\n"
            "3. Busque el botón de descarga (Download DICOM / Exportar).\n"
            "4. Suba el ZIP resultante por 'Subir estudio médico' o la carpeta."),
    }


# ------------------------------------------------------------------ auth

@app.get("/login")
def login_page():
    return FileResponse(str(STATIC / "login.html"))


@app.post("/api/login")
def api_login(password: str = Form(...)):
    if auth.check_password(password):
        token = auth.create_session()
        resp = JSONResponse({"ok": True})
        resp.set_cookie(auth.COOKIE_NAME, token,
                        httponly=True, samesite="lax",
                        max_age=auth.SESSION_TTL)
        return resp
    return JSONResponse({"error": "Contraseña incorrecta"}, status_code=401)


@app.post("/api/logout")
def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


@app.post("/api/change-password")
def api_change_password(current: str = Form(...), new: str = Form(...)):
    if not auth.check_password(current):
        return JSONResponse({"error": "Contraseña actual incorrecta"},
                            status_code=403)
    if len(new.strip()) < 4:
        return JSONResponse({"error": "La nueva contraseña debe tener al "
                                      "menos 4 caracteres"}, status_code=400)
    auth.set_password(new.strip())
    token = auth.create_session()
    resp = JSONResponse({"ok": True, "message": "Contraseña actualizada"})
    resp.set_cookie(auth.COOKIE_NAME, token,
                    httponly=True, samesite="strict",
                    max_age=auth.SESSION_TTL)
    return resp


@app.get("/api/auth-check")
def api_auth_check():
    return {"ok": True}


# dependencia: protege endpoints que requieren sesión
def require_session(request: Request):
    token = request.cookies.get(auth.COOKIE_NAME, "")
    if not auth.validate_session(token):
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    return None


# ------------------------------------------------------------------ static

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def index(request: Request):
    token = request.cookies.get(auth.COOKIE_NAME, "")
    resp = FileResponse(str(STATIC / "index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    if auth.validate_session(token):
        return resp
    login_resp = FileResponse(str(STATIC / "login.html"))
    login_resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return login_resp


# protege /api/* menos login/auth-check/change-password
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # rutas públicas
    if path in ("/login", "/", "/api/login", "/api/auth-check",
                "/api/change-password", "/api/logout",
                "/api/status"):
        return await call_next(request)
    if path.startswith("/static/"):
        return await call_next(request)
    # rutas protegidas: verificar sesión
    token = request.cookies.get(auth.COOKIE_NAME, "")
    if not auth.validate_session(token):
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    return await call_next(request)


# ------------------------------------------------------------------ startup

@app.on_event("startup")
def _start():
    # ingesta inicial (solo si la base está vacía o se pide)
    if db.conn.execute("SELECT COUNT(*) AS c FROM reports").fetchone()["c"] == 0:
        scan_folder(force=False)
    watcher = threading.Thread(target=_watch_loop, daemon=True,
                               name="lab-watcher")
    watcher.start()
