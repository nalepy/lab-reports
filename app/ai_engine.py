# -*- coding: utf-8 -*-
"""Cerebro IA: genera el informe personalizado de cada paciente vía OpenRouter.

Usa un LLM (DeepSeek V4 Pro por defecto; Opus 4.8 como alternativa si el
usuario no queda conforme) que revisa los datos estructurados extraídos de
los laboratorios —últimas mediciones, tendencias, medicamentos, hallazgos—
y redacta un informe clínico personalizado en español, directo y honesto.

El prompt solo recibe el ESTADO ACTUAL (última medición por biomarcador +
tendencia), para no generar alertas sobre valores ya corregidos en análisis
posteriores.
"""
import json
import os
import base64
import urllib.request
import urllib.error
from datetime import datetime

_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", ".env")


def _load_key() -> str:
    """Fuente única de la API key: data/.env (editable desde la UI)."""
    if os.path.exists(_ENV_PATH):
        try:
            with open(_ENV_PATH, "r", encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line.startswith("OPENROUTER_API_KEY="):
                        return _line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return ""


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# modelos seleccionables por el usuario (solo estos dos)
MODELS = {
    "deepseek": {
        "label": "DeepSeek V4 Pro",
        "id": "deepseek/deepseek-v4-pro",
    },
    "opus": {
        "label": "Opus 4.8",
        "id": "anthropic/claude-opus-4.8",
    },
}

# Modelos de visión permitidos para analizar estudios (imágenes/DICOM/PDFs).
# SOLO modelos baratos de OpenRouter; nunca Opus u otros caros.
VISION_MODELS = {
    "qwen-vl-32b": {
        "label": "Qwen3 VL 32B",
        "id": "qwen/qwen3-vl-32b-instruct",
    },
    "gemini-flash": {
        "label": "Gemini 2.5 Flash (visión)",
        "id": "google/gemini-2.5-flash",
    },
    "gemini-flash-lite": {
        "label": "Gemini 2.5 Flash Lite",
        "id": "google/gemini-2.5-flash-lite",
    },
    "qwen-vl": {
        "label": "Qwen3 VL 8B",
        "id": "qwen/qwen3-vl-8b-instruct",
    },
}
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen-vl-32b")
if VISION_MODEL not in VISION_MODELS:
    VISION_MODEL = "qwen-vl-32b"


class AIError(Exception):
    pass


def _call_openrouter(model_id: str, messages: list, temperature=0.4) -> str:
    key = _load_key()
    if not key or "sk-or-v1-" not in key:
        raise AIError(
            "No hay una API key de OpenRouter configurada. Agregue "
            "OPENROUTER_API_KEY en data/.env "
            "para usar los modelos de IA.")
    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 8000,
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Panel de Laboratorio Clinico",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise AIError(f"OpenRouter HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise AIError(f"No se pudo conectar con OpenRouter: {e.reason}")
    except json.JSONDecodeError:
        raise AIError("Respuesta inválida de OpenRouter.")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise AIError(f"Respuesta inesperada de OpenRouter: {data}")


def _img_data_url(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    mime = "image/png"
    lower = path.lower()
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        mime = "image/jpeg"
    elif lower.endswith(".webp"):
        mime = "image/webp"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _call_openrouter_vision(model_id: str, system: str, user_text: str,
                            images: list, temperature=0.2) -> str:
    key = _load_key()
    if not key or "sk-or-v1-" not in key:
        raise AIError(
            "No hay una API key de OpenRouter configurada. Agregue "
            "OPENROUTER_API_KEY en data/.env "
            "para usar los modelos de IA.")
    content: list = [{"type": "text", "text": user_text}]
    for img in images:
        content.append({"type": "image_url", "image_url": {"url": _img_data_url(img)}})
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": temperature,
        "max_tokens": 4000,
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Panel de Laboratorio Clinico",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise AIError(f"OpenRouter HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise AIError(f"No se pudo conectar con OpenRouter: {e.reason}")
    except json.JSONDecodeError:
        raise AIError("Respuesta inválida de OpenRouter.")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise AIError(f"Respuesta inesperada de OpenRouter: {data}")


def _parse_findings(raw: str) -> list:
    """Convierte la respuesta de visión a lista de hallazgos estructurados."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # intentar extraer el primer arreglo JSON dentro de la respuesta
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                data = None
        else:
            data = None
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict):
            f = {
                "system": str(item.get("system", "Estudio")),
                "severity": str(item.get("severity", "normal")),
                "text": str(item.get("text", "")),
                "value": item.get("value"),
                "unit": item.get("unit"),
            }
            out.append(f)
    return out


def analyze_image(image_paths: list, hint: str) -> dict:
    """Analiza imágenes de un estudio (RX, TC, resonancia, PDF, DICOM).

    Devuelve {"text": str, "findings": list} con los hallazgos en español.
    """
    model = VISION_MODELS[VISION_MODEL]["id"]
    system = """Sos un médico especialista en diagnóstico por imágenes. Analizás las
imágenes de estudios médicos y describís los hallazgos relevantes en español,
de forma clara y directa.
Respondé SOLO con JSON, sin texto adicional, un arreglo de objetos con esta forma:
[{"system": "Nombre del sistema (ej: Radiografía de tórax, TC de cráneo,
Resonancia de rodilla, Estudio de laboratorio)", "severity": "normal|leve|moderado|severo",
"text": "Hallazgo descriptivo y concreto", "value": null, "unit": null}]
Si hay valores numéricos (ej: una glucemia en un PDF de laboratorio), ponelos en
value/unit. Si un estudio está normal, indicá severity normal."""
    user = (
        "Paciente: %s.\nAnalizá el/los estudio(s) adjunto(s) y extraé todos los "
        "hallazgos relevantes. No inventes hallazgos que no veas." % hint
    )
    raw = _call_openrouter_vision(model, system, user, image_paths)
    return {"text": raw, "findings": _parse_findings(raw)}


def _fmt_value(m):
    v = m.get("value")
    if v is None or m.get("status") == "no_realizado":
        return "no realizado (el laboratorio no informó valor)"
    u = m.get("unit") or ""
    ref = ""
    if m.get("ref_low") is not None or m.get("ref_high") is not None:
        lo = m["ref_low"]
        hi = m["ref_high"]
        if lo is not None and hi is not None:
            ref = f" (ref: {lo}-{hi})"
        elif hi is not None:
            ref = f" (ref: <{hi})"
        elif lo is not None:
            ref = f" (ref: >{lo})"
    return f"{v} {u}{ref}"


def _build_prompt(person, assessment, meds, reports, analyses=None) -> list[dict]:
    """Construye el prompt con SOLO el estado actual de cada biomarcador."""
    analyses = analyses or []
    markers = []
    for m in sorted(assessment["markers"], key=lambda x: x["label"]):
        status_txt = {"normal": "normal", "alto": "ALTO", "bajo": "BAJO"}.get(
            m["status"], m["status"])
        trend = m.get("trend") or ""
        markers.append(
            f"- {m['label']}: {_fmt_value(m)} -> {status_txt}"
            f"{(' · tendencia: ' + trend) if trend else ''}"
            f" ({m.get('n_measurements', 1)} mediciones)"
            f", última {m.get('last_date', '')[:10]}")

    systems = "\n".join(
        f"  [{s['system']}] {s['text']}" for s in assessment["systems"])

    med_lines = "\n".join(
        f"- {m['name']}" +
        (f" {m['dose']}" if m.get("dose") else "") +
        (f" ({m['frequency']})" if m.get("frequency") else "")
        for m in meds) or "- Ninguno registrado"

    # interacciones YA calculadas por el sistema (fármaco-fármaco y fármaco-lab)
    dc = assessment.get("drug_checks", {}) or {}
    inter_parts = []
    for it in dc.get("drug_drug", []):
        inter_parts.append(
            f"- [{it.get('severity', '')}] {it.get('drugs', '')}: {it.get('message', '')}")
    for it in dc.get("drug_lab", []):
        inter_parts.append(
            f"- [{it.get('severity', '')}] {it.get('drug', '')} ↔ {it.get('test', '')}: "
            f"{it.get('message', '')}")
    if dc.get("unknown_meds"):
        inter_parts.append(
            "- Medicamento(s) sin datos de interacción en la base: "
            + ", ".join(dc["unknown_meds"]))
    inter_lines = "\n".join(inter_parts) or "- Ninguna interacción detectada por el sistema."

    rec_lines = "\n".join(
        f"  [{r['severity']}] {r['title']}: {r['body']}" +
        (f" ACCION: {r['action']}" if r.get("action") else "")
        for r in assessment["recommendations"][:12])

    # ---- análisis de estudios por imagen (RX, TC, resonancia, DICOM, PDFs) ----
    ana_lines = []
    for a in analyses:
        if a.get("status") != "done":
            continue
        findings = []
        try:
            findings = json.loads(a.get("findings_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            findings = []
        if not findings:
            continue
        fname = a.get("orig_filename") or "estudio"
        head = f"- {fname}:"
        for f in findings:
            val = ""
            if f.get("value") is not None:
                val = f" ({f['value']}{' ' + str(f['unit']) if f.get('unit') else ''})"
            head += f"\n  - [{f.get('severity', 'normal')}] {f.get('system', 'Estudio')}: {f.get('text', '')}{val}"
        ana_lines.append(head)
    ana_block = "\n".join(ana_lines)

    n_reports = len(reports)
    # el 'date' puede ser NULL (informe subido sin fecha parseable)
    first_date = (reports[0]["date"] or "")[:10] if reports else "?"
    last_date = (reports[-1]["date"] or "")[:10] if reports else "?"

    # ---- datos vitales + información médica manual (si están registrados) ----
    vitals = []
    bd = str(person.get("birth_date") or "")
    if bd:
        try:
            bd = datetime.strptime(bd[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
        except ValueError:
            pass
        vitals.append(f"- Fecha de nacimiento: {bd}")
    if person.get("age"):
        vitals.append(f"- Edad: {person['age']} años")
    if person.get("sex"):
        vitals.append("- Sexo: " + ("M" if person["sex"] == "M" else "F"))
    if person.get("weight_kg"):
        vitals.append(f"- Peso: {person['weight_kg']} kg")
    if person.get("height_cm"):
        vitals.append(f"- Talla: {person['height_cm']} cm")
    if person.get("weight_kg") and person.get("height_cm") and float(person["height_cm"]) > 0:
        bmi = float(person["weight_kg"]) / (float(person["height_cm"]) / 100) ** 2
        vitals.append(f"- IMC: {bmi:.1f} kg/m²")
    if person.get("bp"):
        vitals.append(f"- PRESIÓN ARTERIAL: {person['bp']} mmHg")
    if person.get("hr"):
        vitals.append(f"- Pulso: {person['hr']} bpm")
    vitals_lines = "\n".join(vitals) or "- Sin datos vitales registrados"

    notes_txt = str(person.get("notes") or "").strip()
    notes_block = ""
    if notes_txt:
        notes_block = ("INFORMACIÓN MÉDICA ADICIONAL REFERIDA POR EL PACIENTE "
                       "(alergias, enfermedades crónicas, antecedentes, otros):\n"
                       + notes_txt)

    system_prompt = """Eres un médico analista senior y directo. Tu tarea es redactar
un INFORME CLÍNICO PERSONALIZADO en español para un paciente, a partir de sus
análisis de laboratorio y medicación.

REGLAS DE ORO:
1. USA SOLO EL ESTADO ACTUAL: la última medición de cada biomarcador. Si un
   valor estaba alterado en 2025 pero volvió a la normalidad en 2026, NO lo
   reportes como problema. Reporta la tendencia, pero solo alerta lo vigente.
2. RESULTADOS ANTERIORES A 12 MESES: si un valor está alterado pero su última
   medición tiene más de 12 meses, NO lo presentes como alerta actual:
   recomienda repetir el análisis para confirmar el estado presente.
3. SÉ DIRECTO Y HONESTO. No suavices malas noticias. Usa estadísticas reales
   cuando aporten (las que se te proporcionan ya están verificadas; no inventes
   cifras ni fuentes).
4. ORDENA TODO POR URGENCIA: rojo (urgente/crítico) primero, luego amarillo,
   luego verde. Prioriza la acción más importante.
5. MENCIONA las interacciones medicamento-laboratorio y fármaco-fármaco
   relevantes.
6. CIERRA con: 1) acciones inmediatas, 2) estudios/controles recomendados
   (con su motivo), 3) hábitos de vida. Nada de relleno.
7. Formato: Markdown con secciones claras. Usa **negritas** para lo crítico.
   Idioma: español.
8. Si no hay hallazgos críticos ni de precaución, dilo claro y da consejos
   preventivos breves. No inventes enfermedades.
9. INCORPORA los hallazgos de estudios de imagen/diagnóstico por imagen
   (radiografías, tomografías, resonancias, ecografías) que se te entregan:
   menciónalos en la sección de hallazgos y en las acciones recomendadas,
   respetando su severidad (normal/leve/moderado/severo). Si no se entrega
   ninguno, ignora esta regla."""
    # noinspection PyUnresolvedReferences
    user_prompt = f"""PACIENTE: {person['name']}
INFORMES ANALIZADOS: {n_reports} ({first_date} → {last_date})

DATOS VITALES DEL PACIENTE:
{vitals_lines}
{notes_block}

ESTADO ACTUAL DE BIOMARCADORES (solo última medición):
{chr(10).join(markers)}

REVISIÓN POR SISTEMAS:
{systems}

HALLAZGOS (prioridad alta/media):
{chr(10).join('- [' + f['severity'] + '] ' + f['marker']['label'] + ': ' + (f['stat'] or f['marker']['text']) for f in assessment['findings'][:8])}

MEDICAMENTOS REGISTRADOS:
{med_lines}

INTERACCIONES DETECTADAS POR EL SISTEMA (fármaco-fármaco y fármaco-laboratorio,
ya verificadas contra la base; intégralas y explícalas si son relevantes):
{inter_lines}

RECOMENDACIONES PRELIMINARES (solo orientativas, reescríbelas con criterio):
{rec_lines}

ESTUDIOS DE IMAGEN Y/O DOCUMENTOS ANALIZADOS (hallazgos extraídos por IA):
{ana_block if ana_block else '- Sin estudios de imagen analizados'}

Redacta el informe final completo en español."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def generate_report(person, assessment, meds, reports, model_key="deepseek",
                    force=False, db=None) -> dict:
    """Genera (o carga desde DB) el informe IA de una persona."""
    pid = person["id"]
    # si no se fuerza regeneración, devolver el guardado
    if not force and db is not None:
        saved = db.load_ai_report(pid, model_key)
        if saved:
            return {
                "model": saved["model_label"],
                "model_key": saved["model_key"],
                "content": saved["content"],
                "generated_at": saved["generated_at"],
                "fallback": False,
                "saved": True,
            }
    try:
        analyses = db.analyses_for_person(pid) if db is not None else []
        messages = _build_prompt(person, assessment, meds, reports, analyses)
        content = _call_openrouter(MODELS[model_key]["id"], messages)
        report = {
            "model": MODELS[model_key]["label"],
            "model_key": model_key,
            "content": content,
            "generated_at": __import__("datetime").datetime.now().isoformat(),
            "fallback": False,
            "saved": False,
        }
        # persistir en DB
        if db is not None:
            db.save_ai_report(pid, model_key, report["model"], content)
    except AIError as e:
        # en caso de error, si hay uno guardado, usarlo
        if db is not None:
            saved = db.load_ai_report(pid, model_key)
            if saved:
                return {
                    "model": saved["model_label"],
                    "model_key": saved["model_key"],
                    "content": saved["content"],
                    "generated_at": saved["generated_at"],
                    "fallback": True,
                    "saved": True,
                    "fallback_reason": str(e),
                }
        report = {
            "model": "Local (sin conexión a IA)",
            "model_key": model_key,
            "content": _fallback_report(person, assessment, meds, reports),
            "generated_at": __import__("datetime").datetime.now().isoformat(),
            "fallback": True,
            "saved": False,
            "fallback_reason": str(e),
        }
    return report


def _fallback_report(person, assessment, meds, reports) -> str:
    """Informe estructurado local cuando la IA no está disponible."""
    lines = []
    s = assessment["summary"]
    lines.append(f"# Informe de laboratorio — {person['name']}")
    lines.append("")
    lines.append(f"**{s['text']}**")
    lines.append("")
    if assessment["findings"]:
        lines.append("## Hallazgos por prioridad")
        for f in assessment["findings"]:
            m = f["marker"]
            icon = "🔴" if f["severity"] == "red" else "🟡"
            date = f" (medido {f['date'][:10]})" if f.get("date") else ""
            lines.append(f"- {icon} **{m['label']}**: {m['value']} {m['unit']} "
                         f"— {m['status']}{date}. {m['text']}")
    else:
        lines.append("## Hallazgos")
        lines.append("- No se detectaron valores fuera de rango.")
    lines.append("")
    if assessment["drug_checks"].get("drug_lab") or \
            assessment["drug_checks"].get("drug_drug"):
        lines.append("## Interacciones con medicamentos")
        for inter in assessment["drug_checks"]["drug_drug"]:
            lines.append(f"- {inter['severity'].upper()}: {inter['drugs']} — "
                         f"{inter['message']}")
        for f in assessment["drug_checks"]["drug_lab"]:
            lines.append(f"- {f['severity'].upper()}: {f['drug']} ↔ "
                         f"{f['test']} — {f['message']}")
        lines.append("")
    lines.append("## Recomendaciones (por urgencia)")
    for r in assessment["recommendations"]:
        icon = {"red": "🔴", "yellow": "🟡", "green": "🟢"}.get(r["severity"], "•")
        lines.append(f"- {icon} **{r['title']}**: {r['body']}")
        if r.get("action"):
            lines.append(f"  → {r['action']}")
    lines.append("")
    lines.append("> ⚠️ **Advertencia**: este informe fue generado por "
                 "inteligencia artificial y NO reemplaza la consulta con un "
                 "médico. Consulte siempre con un profesional de la salud.")
    return "\n".join(lines)


def model_options() -> dict:
    return {k: v["label"] for k, v in MODELS.items()}
