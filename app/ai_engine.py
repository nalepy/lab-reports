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
import urllib.request
import urllib.error

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# Si no hay variable de entorno, intentar leer data/.env
if not OPENROUTER_KEY:
    _env_path = os.path.join(os.path.dirname(__file__), "..", "data", ".env")
    if os.path.exists(_env_path):
        try:
            with open(_env_path, "r", encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line.startswith("OPENROUTER_API_KEY="):
                        OPENROUTER_KEY = _line.split("=", 1)[1].strip().strip('"')
                        break
        except OSError:
            pass
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


class AIError(Exception):
    pass


def _call_openrouter(model_id: str, messages: list, temperature=0.4) -> str:
    if not OPENROUTER_KEY or "sk-or-v1-" not in OPENROUTER_KEY:
        raise AIError(
            "No hay una API key de OpenRouter configurada. Agregue "
            "OPENROUTER_API_KEY en data/.env o como variable de entorno "
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
            "Authorization": f"Bearer {OPENROUTER_KEY}",
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


def _build_prompt(person, assessment, meds, reports) -> list[dict]:
    """Construye el prompt con SOLO el estado actual de cada biomarcador."""
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

    n_reports = len(reports)
    first_date = reports[0]["date"][:10] if reports else "?"
    last_date = reports[-1]["date"][:10] if reports else "?"

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
8. Advertencia obligatoria al final: que esto es generado por IA y no
   reemplaza la consulta médica.
9. Si no hay hallazgos críticos ni de precaución, dilo claro y da consejos
   preventivos breves. No inventes enfermedades."""
    # noinspection PyUnresolvedReferences
    user_prompt = f"""PACIENTE: {person['name']}
{('Sexo: ' + person['sex']) if person.get('sex') else ''}{(' · Edad: ' + str(person['age']) + ' años') if person.get('age') else ''}
INFORMES ANALIZADOS: {n_reports} ({first_date} → {last_date})

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
        messages = _build_prompt(person, assessment, meds, reports)
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
