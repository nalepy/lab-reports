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
import glob
import tempfile
import threading
import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
import json
import re

from .db import DB
from .assessment import build_assessment
from .sources import sources_for, sources_for_finding
from . import drugs as drugs_mod
from . import catalog
from . import ai_engine
from . import auth
from . import imaging
from .parser import parse_pdf

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


class PersonIn(BaseModel):
    name: str
    doc: str = ""
    sex: str = ""
    age: str = ""


class PendingResolveIn(BaseModel):
    key: str
    action: str
    target_pid: int = None
    name: str = ""


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


@app.post("/api/persons")
def add_person(p: PersonIn):
    """Crea un paciente nuevo (o devuelve el existente si coincide)."""
    if not p.name.strip():
        return JSONResponse({"error": "El nombre del paciente es obligatorio"},
                            status_code=400)
    try:
        pid = db.add_person(p.name, p.doc, p.sex, p.age)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, "id": pid}


def _merge_imaging_series(assessment: dict, analyses: list) -> None:
    """Inyecta hallazgos numéricos de análisis de imagen en la tabla mensual
    y los gráficos. Crea marcadores sintéticos con key `_ana_<n>` para que
    `renderTables` los muestre como filas."""
    series = assessment.setdefault("series", {})
    markers = assessment.setdefault("markers", [])
    n = 0
    for a in analyses:
        if a.get("status") != "done":
            continue
        try:
            findings = json.loads(a.get("findings_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            findings = []
        if not isinstance(findings, list):
            continue
        adate = (a.get("created_at") or "")[:10]
        for f in findings:
            v = f.get("value")
            if v is None or isinstance(v, bool):
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            system = str(f.get("system", "Estudio")).strip() or "Estudio"
            unit = str(f.get("unit") or "").strip()
            skey = f"_ana_{n}"
            label = f"{system} — {str(f.get('text', '')).split('.')[0][:60]}"
            marker = {
                "key": skey,
                "label": label,
                "value": fv,
                "unit": unit,
                "status": "normal",
                "last_date": adate,
                "n_measurements": 1,
                "trend": "",
            }
            markers.append(marker)
            series[skey] = [{"name": label, "date": adate, "value": fv, "unit": unit}]
            n += 1


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
    # aviso de datos nuevos desde el último informe IA
    last_rep = db.last_ai_report_at(pid)
    last_rep_n = (last_rep or "").replace("T", " ").split(".")[0]
    new_since = []
    for d in documents:
        up = (d.get("uploaded_at") or "").replace("T", " ").split(".")[0]
        if last_rep_n and up > last_rep_n:
            new_since.append(d)
    # series de estudios de imagen: valores numéricos repetidos entre análisis
    _merge_imaging_series(assessment, db.analyses_for_person(pid))
    return {
        "person": p,
        "reports": reports,
        "meds": meds,
        "documents": documents,
        "assessment": assessment,
        "new_info": {
            "has_new": bool(new_since),
            "count": len(new_since),
            "last_report": last_rep,
        },
    }


@app.get("/api/person/{pid}/report/{rid}")
def report_detail(pid: int, rid: int):
    rows = db.tests_for_report(rid)
    return {"report_id": rid, "tests": rows}


@app.get("/api/report/{rid}/file")
def report_file(rid: int):
    """Sirve el PDF original ingerido de un informe de laboratorio."""
    r = db.report(rid)
    if not r:
        return JSONResponse({"error": "Informe no encontrado"}, status_code=404)
    path = db.resolve_path(r["stored_path"])
    if not os.path.exists(path):
        return JSONResponse({"error": "Archivo no disponible en disco"},
                            status_code=404)
    fname = r.get("source_file") or os.path.basename(path)
    return FileResponse(path, media_type="application/pdf", filename=fname)


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


# ------------------------------------------------------------------ informe IA en 2do plano

class MetricsIn(BaseModel):
    birth_date: str = ""
    weight_kg: float | None = None
    height_cm: float | None = None
    bp: str = ""
    hr: int | None = None
    notes: str = ""


@app.patch("/api/person/{pid}/metrics")
def person_metrics(pid: int, m: MetricsIn):
    """Guarda datos vitales manuales (fecha nac, peso, talla, PA, pulso, notas médicas)."""
    if not db.person(pid):
        return JSONResponse({"error": "Persona no encontrada"}, status_code=404)
    db.update_person_metrics(pid, m.birth_date, m.weight_kg, m.height_cm,
                             m.bp, m.hr, m.notes)
    return {"ok": True, "person": db.person(pid)}


# ------------------------------------------------------------------ eliminar paciente

class PersonDeleteIn(BaseModel):
    mode: str          # delete_all | transfer | transfer_new
    to_pid: int | None = None
    name: str = ""
    doc: str = ""
    sex: str = ""
    age: str = ""


@app.post("/api/person/{pid}/delete")
def delete_person(pid: int, d: PersonDeleteIn):
    """Elimina un paciente.

    - delete_all: borra el paciente y todos sus archivos.
    - transfer: reasigna sus archivos a otro paciente (to_pid).
    - transfer_new: crea un paciente nuevo y le reasigna los archivos.
    """
    p = db.person(pid)
    if not p:
        return JSONResponse({"error": "Persona no encontrada"}, status_code=404)
    to_pid = None
    if d.mode == "transfer":
        if not d.to_pid or d.to_pid == pid:
            return JSONResponse(
                {"error": "Elija otro paciente de destino"},
                status_code=400)
        if not db.person(d.to_pid):
            return JSONResponse(
                {"error": "Paciente de destino no encontrado"},
                status_code=404)
        to_pid = d.to_pid
    elif d.mode == "transfer_new":
        if not d.name.strip():
            return JSONResponse(
                {"error": "Indique el nombre del paciente nuevo"},
                status_code=400)
        try:
            to_pid = db.add_person(d.name, d.doc, d.sex, d.age)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
    elif d.mode != "delete_all":
        return JSONResponse({"error": "Modo inválido"}, status_code=400)
    db.delete_person(pid, to_pid)
    return {"ok": True, "deleted": pid, "to_pid": to_pid}


@app.get("/api/person/{pid}/suggest-target")
def suggest_target(pid: int):
    """Busca a qué paciente REAL pertenecen los archivos de este registro
    (p. ej. una empresa de laboratorio), analizando los PDFs: por documento
    (C.I.) o por nombre del paciente detectado."""
    p = db.person(pid)
    if not p:
        return JSONResponse({"error": "Persona no encontrada"}, status_code=404)
    candidates: list[dict] = []
    seen: set[int] = set()
    for r in db.reports_for(pid):
        path = db.resolve_path(r["stored_path"])
        if not os.path.exists(path):
            continue
        try:
            parsed = parse_pdf(path)
        except Exception:  # noqa: BLE001
            parsed = []
        for pr in parsed:
            tgt = None
            how = ""
            if pr.doc:
                tgt = db._person_by_doc(pr.doc)
                how = "documento"
            if not tgt and pr.patient_name:
                tgt = db._fuzzy_person(pr.patient_name, pr.doc or "")
                how = "nombre"
            if tgt and tgt["id"] != pid and tgt["id"] not in seen:
                seen.add(tgt["id"])
                candidates.append(
                    {"id": tgt["id"], "name": tgt["name"], "match": how})
        # fallback: escaneo del texto crudo (formatos no parseados, ej. cultivos)
        _suggest_from_raw_text(path, pid, candidates, seen)
    return {"source": {"id": pid, "name": p["name"]},
            "candidates": candidates[:5]}


def _suggest_from_raw_text(path: str, pid: int,
                           candidates: list[dict], seen: set[int]) -> None:
    """Escanea el texto plano del PDF buscando C.I. y nombres en mayúsculas
    que coincidan con pacientes existentes."""
    try:
        import fitz
        with fitz.open(path) as doc:
            text = "\n".join(pg.get_text() for pg in doc)
    except Exception:  # noqa: BLE001
        return
    # documento: número de 6-8 dígitos (C.I.)
    for m in re.finditer(r"\b(\d{6,8})\b", text):
        tgt = db._person_by_doc(m.group(1))
        if tgt and tgt["id"] != pid and tgt["id"] not in seen:
            seen.add(tgt["id"])
            candidates.append(
                {"id": tgt["id"], "name": tgt["name"], "match": "documento"})
    # nombre: secuencia de palabras en MAYÚSCULAS (máx 6 palabras)
    for m in re.finditer(
            r"\b([A-ZÁÉÍÓÚÑ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ]{2,}){1,5})\b", text):
        tgt = db._fuzzy_person(m.group(1), "")
        if tgt and tgt["id"] != pid and tgt["id"] not in seen:
            seen.add(tgt["id"])
            candidates.append(
                {"id": tgt["id"], "name": tgt["name"], "match": "nombre"})


_AI_BG_LOCK = threading.Lock()
_ai_jobs: dict[int, dict] = {}


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ai_report_stale(pid: int) -> bool:
    """True si falta el informe IA o hay datos más nuevos que el último."""
    last = db.last_ai_report_at(pid)
    if not last:
        return True
    newest = db.newest_data_at(pid)
    if not newest:
        return False
    try:
        t_last = datetime.fromisoformat(last.replace("T", " "))
        t_new = datetime.fromisoformat(newest.replace("T", " "))
        return t_new > t_last
    except ValueError:
        return True


def _bg_generate_reports(pids: list[int]):
    """Genera informes secuencialmente en segundo plano (DeepSeek, sin bloquear)."""
    for pid in pids:
        _ai_jobs[pid] = {"status": "running", "started_at": _now_iso()}
        try:
            p = db.person(pid)
            if not p:
                _ai_jobs[pid] = {"status": "error", "error": "paciente inexistente"}
                continue
            tests = db.tests_for(pid)
            meds = db.meds_for(pid)
            reports = db.reports_for(pid)
            assessment = build_assessment(p, tests, meds)
            res = ai_engine.generate_report(p, assessment, meds, reports,
                                            model_key="deepseek", force=True,
                                            db=db)
            _ai_jobs[pid] = {
                "status": "done", "model": res.get("model"),
                "finished_at": _now_iso(),
                "fallback": bool(res.get("fallback")),
            }
        except Exception as e:  # noqa: BLE001
            _ai_jobs[pid] = {"status": "error", "error": str(e)}


@app.post("/api/ensure-ai-reports")
def ensure_ai_reports(pid: int | None = None):
    """Verifica TODOS los pacientes; encola en 2do plano los informes IA
    faltantes o vencidos (datos nuevos desde el último). No bloquea el sitio.

    Si se pasa ?pid=, regenera ESE paciente aunque no esté vencido
    (por ejemplo, tras guardar datos vitales / notas médicas)."""
    pending: list[int] = []
    busy = any(j.get("status") in ("running", "queued")
               for j in _ai_jobs.values())
    if not busy:
        with _AI_BG_LOCK:
            if pid is not None:
                if _ai_jobs.get(pid, {}).get("status") not in ("running", "queued"):
                    _ai_jobs[pid] = {"status": "queued", "queued_at": _now_iso(),
                                     "forced": True}
                    pending.append(pid)
            else:
                for p in db.persons():
                    pid2 = p["id"]
                    if _ai_jobs.get(pid2, {}).get("status") in ("running", "queued"):
                        continue
                    if _ai_report_stale(pid2):
                        _ai_jobs[pid2] = {"status": "queued", "queued_at": _now_iso()}
                        pending.append(pid2)
    if pending:
        threading.Thread(target=_bg_generate_reports, args=(pending,),
                         daemon=True).start()
    return {"pending": pending, "queued": len(pending), "busy": busy}


@app.get("/api/ai-jobs")
def ai_jobs_status():
    return {"jobs": _ai_jobs}


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


def _store_upload(pid: int, filename: str, content: bytes) -> dict:
    """Clasifica y guarda un archivo subido desde la web.

    - PDF: intenta ingestarlo como informe de laboratorio; si no parsea, queda
      como documento adjunto. El original se conserva siempre para descarga.
    - Imágenes / DICOM / otros: documento adjunto, formato original intacto.
    """
    ext = "." + filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext not in ALLOWED_EXT:
        return {"ok": False, "file": filename, "status": "error",
                "message": f"Tipo no soportado ({ext or 'sin extensión'})."}
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    dest = UPLOAD_DIR / f"p{pid}_{int(time.time())}_{safe}"
    with open(dest, "wb") as f:
        f.write(content)
    kind = _kind_for(filename)
    size = len(content)
    study_date = ""

    if kind == "pdf":
        try:
            res = db.ingest(str(dest), force=False, library_dir=str(LIBRARY_DIR))
            study_date = res.get("study_date") or ""
            if res.get("status") == "pending":
                # paciente no reconocido: el informe espera confirmación del
                # usuario (crear / elegir / corregir) antes de crearse persona
                dest.unlink(missing_ok=True)   # la copia ya vive en la biblioteca
                return {
                    "ok": True, "file": filename, "status": "pending",
                    "pending": res.get("pending") or [],
                    "message": "Paciente no reconocido: requiere confirmación.",
                }
            if res.get("status") == "duplicate":
                dest.unlink(missing_ok=True)
                return {"ok": True, "file": filename, "status": "duplicate",
                        "message": "Duplicado (ya estaba registrado)."}
            if res.get("new_reports", 0) > 0:
                actual = res.get("person_id")
                # el PDF se asignó a OTRO paciente (o se creó uno nuevo):
                # detectar la pestaña equivocada y mover el documento ahí
                if actual and actual != pid:
                    created = bool(res.get("created"))
                    to_name = res.get("person_name") or f"#{actual}"
                    db.add_document(
                        actual, filename, str(dest), "pdf", size,
                        "Informe de laboratorio ingerido (movido al paciente "
                        "correcto tras detectar el nombre).",
                        study_date=study_date)
                    return {
                        "ok": True, "file": filename, "status": "moved",
                        "to_pid": actual, "to_name": to_name,
                        "created": created,
                        "new_reports": res["new_reports"],
                        "message": ("Paciente no coincide: movido a "
                                    f"{to_name}{' (creado)' if created else ''}."),
                    }
                db.add_document(pid, filename, str(dest), "pdf", size,
                                "Informe de laboratorio ingerido.",
                                study_date=study_date)
                return {"ok": True, "file": filename, "status": "laboratorio",
                        "new_reports": res["new_reports"],
                        "message": f"{res['new_reports']} informe(s) ingerido(s)."}
        except Exception:  # noqa: BLE001
            pass  # PDF no parseable: queda como adjunto

    doc_id = db.add_document(pid, filename, str(dest), kind, size,
                             "Estudio subido desde la web.",
                             study_date=study_date)
    return {"ok": True, "file": filename, "status": "document",
            "document_id": doc_id,
            "message": f"Guardado como adjunto ({kind})."}


@app.post("/api/person/{pid}/upload")
async def upload_file(pid: int, file: UploadFile = File(...)):
    """Sube un estudio médico (PDF, imagen, DICOM…) desde la web."""
    if not file.filename:
        return JSONResponse({"error": "Archivo sin nombre."}, status_code=400)
    p = db.person(pid)
    if not p:
        return JSONResponse({"error": "Persona no encontrada"}, status_code=404)
    content = await file.read()
    return _store_upload(pid, file.filename, content)


@app.post("/api/person/{pid}/upload-batch")
async def upload_batch(pid: int, files: list[UploadFile] = File(...)):
    """Sube varios archivos a la vez (selector único de archivos/carpeta).

    Cada archivo se clasifica (laboratorio PDF / imagen / DICOM / otro) y se
    guarda en su formato original. Los DICOM en lote se agrupan en una carpeta.
    """
    if not files:
        return JSONResponse({"error": "No se recibieron archivos."},
                            status_code=400)
    p = db.person(pid)
    if not p:
        return JSONResponse({"error": "Persona no encontrada"}, status_code=404)

    results = []
    dcm_group = []
    total_new_reports = 0
    for f in files:
        if not f.filename:
            continue
        content = await f.read()
        if f.filename.lower().endswith((".dcm", ".dicom")):
            dcm_group.append((f.filename, content))
            continue
        results.append(_store_upload(pid, f.filename, content))

    # agrupar DICOM en una sola carpeta (serie), preservando el formato
    if dcm_group:
        group_id = f"{int(time.time())}_{pid}"
        group_dir = UPLOAD_DIR / f"grupo_{group_id}"
        group_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for fname, content in dcm_group:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", fname)
            (group_dir / safe).write_bytes(content)
            saved += 1
        doc_id = db.add_document(pid, f"Serie DICOM {group_id} ({saved} archivos)",
                                 str(group_dir), "dicom_folder", saved,
                                 "Serie DICOM subida desde la web.")
        results.append({"ok": True, "file": f"Serie DICOM ({saved} archivos)",
                        "status": "dicom_folder", "document_id": doc_id,
                        "message": f"Serie DICOM agrupada ({saved} archivos)."})

    for r in results:
        total_new_reports += r.get("new_reports", 0)
    ok_count = sum(1 for r in results if r.get("ok"))
    err_count = sum(1 for r in results if not r.get("ok"))
    return {
        "ok": True,
        "results": results,
        "summary": {
            "total": len(results),
            "uploaded": ok_count,
            "errors": err_count,
            "new_reports": total_new_reports,
        },
    }


@app.post("/api/person/{pid}/process-studies")
def process_studies(pid: int):
    """Analiza con IA (visión) los estudios subidos aún sin análisis.

    Recorre los documentos del paciente que no tienen un análisis completo,
    convierte cada uno (PDF escaneado, imagen, serie DICOM) a PNG y pide a un
    modelo de visión barato de OpenRouter que extraiga los hallazgos. Guarda
    el resultado en `analyses` para mostrarlo en el informe IA, la grilla de
    estudios y los gráficos.
    """
    p = db.person(pid)
    if not p:
        return JSONResponse({"error": "Persona no encontrada"}, status_code=404)
    docs = db.documents_for(pid)
    parsed_paths = {r.get("stored_path") for r in db.reports_for(pid)}
    hint = f"{p.get('name') or 'Paciente'} — estudio clínico"
    model_id = ai_engine.VISION_MODELS[ai_engine.VISION_MODEL]["id"]
    results = []
    analyzed = 0
    errors = 0
    skipped = 0
    with tempfile.TemporaryDirectory() as tmp:
        for d in docs:
            if d.get("analysis_status") == "done":
                continue
            # PDFs ya ingeridos como informe: ya están en tablas/gráficos
            if d.get("kind") == "pdf" and d.get("stored_path") in parsed_paths:
                continue
            path = db.resolve_path(d.get("stored_path") or "")
            if not path or not os.path.exists(path):
                db.save_analysis(d["id"], "error", model=model_id, kind=d["kind"],
                                 error="Archivo no encontrado en disco")
                results.append({"ok": False, "document_id": d["id"],
                                "file": d.get("orig_filename"),
                                "status": "error", "message": "Archivo no encontrado"})
                errors += 1
                continue
            pngs = imaging.study_to_pngs(path, tmp, max_images=3)
            if not pngs:
                db.save_analysis(d["id"], "error", model=model_id, kind=d["kind"],
                                 error="No se pudo generar una imagen del estudio")
                results.append({"ok": False, "document_id": d["id"],
                                "file": d.get("orig_filename"),
                                "status": "error",
                                "message": "No se pudo generar una imagen"})
                errors += 1
                continue
            try:
                res = ai_engine.analyze_image(
                    pngs, f"{hint} — {d.get('orig_filename')}")
                findings = res["findings"]
                db.save_analysis(d["id"], "done", model=model_id,
                                 text=res["text"], findings=findings,
                                 kind=d["kind"])
                analyzed += 1
                results.append({"ok": True, "document_id": d["id"],
                                "file": d.get("orig_filename"),
                                "status": "analyzed",
                                "findings": len(findings)})
            except ai_engine.AIError as e:
                db.save_analysis(d["id"], "error", model=model_id, kind=d["kind"],
                                 error=str(e))
                results.append({"ok": False, "document_id": d["id"],
                                "file": d.get("orig_filename"),
                                "status": "error", "message": str(e)})
                errors += 1
            except Exception as e:  # noqa: BLE001
                db.save_analysis(d["id"], "error", model=model_id, kind=d["kind"],
                                 error=str(e))
                results.append({"ok": False, "document_id": d["id"],
                                "file": d.get("orig_filename"),
                                "status": "error", "message": str(e)})
                errors += 1
    return {"ok": True, "model": model_id, "results": results,
            "summary": {"total": len(results), "analyzed": analyzed,
                        "errors": errors, "skipped": skipped}}


@app.post("/api/person/{pid}/pending")
async def resolve_pending(pid: int, body: PendingResolveIn):
    """Resuelve un informe con paciente no reconocido tras la confirmación.

    actions: approve (crear con nombre inferido), rename (nombre corregido),
    existing (vincular a target_pid), current (pestaña actual), cancel.
    """
    b = body
    if not b.key or b.action not in (
            "approve", "rename", "existing", "current", "cancel"):
        return JSONResponse({"error": "Solicitud inválida."}, status_code=400)
    target_pid = None
    if b.action in ("existing", "current"):
        target_pid = b.target_pid or pid
        if b.action == "current":
            target_pid = pid
    return db.resolve_pending(b.key, b.action, target_pid=target_pid,
                              name=(b.name or "").strip())


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


@app.get("/api/documents/{doc_id}/zip")
def document_zip(doc_id: int):
    """Descarga una carpeta de documentos completa como ZIP (formatos originales)."""
    doc = db.document(doc_id)
    if not doc:
        return JSONResponse({"error": "Documento no encontrado"}, status_code=404)
    root = os.path.realpath(db.resolve_path(doc["stored_path"]))
    if not os.path.isdir(root):
        return JSONResponse({"error": "No es una carpeta"}, status_code=404)
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(root):
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                z.write(full, rel)
    fname = os.path.splitext(doc["orig_filename"])[0] or "estudio"
    # sanitizar para evitar inyección de cabeceras (CRLF) y comillas/fugas
    safe = re.sub(r'[\r\n"\\]', "_", fname) or "estudio"
    ascii_name = safe.encode("ascii", "replace").decode("ascii") or "estudio"
    utf8_quoted = urllib.parse.quote(f"{safe}.zip")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="{ascii_name}.zip"; '
                 f"filename*=UTF-8''{utf8_quoted}"},
    )


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
    # La carpeta local de laboratorio ya no existe: la ingesta es solo por la
    # web (uploads). No se arranca observador ni escaneo inicial.
    pass
