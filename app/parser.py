# -*- coding: utf-8 -*-
"""PDF lab-report parser.

Handles the five formats seen in the lab folder:
  1. "Verdejo"          - tabular layout, columns by x-position
  2. "Curie" (ORDxxxx)  - two-column (Resultados | Intervalo de Referencia)
  3. "Ypacarai"         - scattered single-page chemistry report
  4. "Sanacoop"         - multi-page reports (HbA1c, TSH, T4, T3, PSA...)
  5. "Sanisidro"        - 2021 single-line hematology
  6. "Medvital"         - radiology narrative (ANGIOTAC) -> document, no values

One file may contain several reports (e.g. Ypacarai chemistry + Sanacoop
thyroid in a single PDF), so parse_pdf returns a list of Report.
"""
import re
import unicodedata
from datetime import datetime

import fitz  # PyMuPDF

from .canonical import canonicalize

BIG_UNITS = ("/ul", "/µl", "/μl", "10e3", "10e6", "x10", "mm3", "10³", "10⁶",
             "10e9", "por ul", "por µl", "u/l", "u/l")


# ---------------------------------------------------------------- helpers

def clean_text(s: str) -> str:
    """Fix mojibake from the PDFs (utf-8 bytes decoded as latin-1) and tidy.

    Some PDFs are already clean; the fix must only apply when the text
    actually contains mojibake artifacts.
    """
    if not s:
        return ""
    fixed = s
    try:
        fixed = s.encode("utf-8", errors="ignore").decode("latin-1",
                                                           errors="ignore")
    except Exception:
        fixed = s

    def bad(t):
        # replacement chars + classic mojibake pairs (Ã±, Ã³, Ã© ...)
        n = t.count("\ufffd")
        n += len(re.findall(r"[\u00c0-\u00df][\u0080-\u00bf\u00a0-\u00ff]", t))
        n += len(re.findall(r"Ã[\u0080-\u00ff]", t))
        return n

    def good(t):
        return len(re.findall(r"[áéíóúñüÁÉÍÓÚÑ]", t))

    if bad(fixed) < bad(s) or (bad(fixed) == bad(s) and good(fixed) > good(s)):
        s = fixed
    s = s.replace("\x00", "").replace("\ufffd", "")
    s = re.sub(r"[ \t]+", " ", s)
    return s


def _normalize_units(value, unit, ref_low, ref_high, ref_text):
    """Convierte unidades hematológicas a una escala común (/µL, /µL-e6).

    10e3/µL, 10³/µL, x10³/µL -> /µL (valor ×1000)
    10e6/µL, 10⁶/µL, x10⁶/µL -> /µL (valor ×1e6)
    También normaliza /uL y /µl a /µL.
    """
    u = (unit or "").strip()
    ul = u.lower().replace(" ", "")
    factor = 1.0
    new_unit = u
    if any(t in ul for t in ("10e3/", "10e6/", "10³/", "10⁶/", "x10³/", "x10⁶/",
                             "x103/", "x106/")):
        if "e3" in ul or "³" in ul or "x103" in ul:
            factor = 1000.0
        else:
            factor = 1e6
        new_unit = "/µL"
    elif ul in ("/ul", "/µl", "/μl", "/l"):
        new_unit = "/µL"
    if factor != 1.0 or new_unit != u:
        if value is not None:
            value = round(value * factor, 6)
        if ref_low is not None:
            ref_low = round(ref_low * factor, 6)
        if ref_high is not None:
            ref_high = round(ref_high * factor, 6)
        # actualizar el texto de referencia con la unidad normalizada
        if ref_text:
            ref_text = ref_text.replace(ul, new_unit).replace(u, new_unit)
    return value, new_unit, ref_low, ref_high, ref_text


def _norm_key(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def parse_number(s: str, unit: str = "") -> float | None:
    """Parse a Spanish-locale number (dots/commas) into a float.

    Tolerates garbage prefixes found in some PDFs ("-Z0-110.", "--116.")
    and units that embed digits ("10e3/µL 0.000"): the LAST number token
    wins, since values follow units in those rows.
    """
    if not s:
        return None
    s = s.strip().replace(" ", "").replace("\u00a0", "")
    # strip leading garbage until the first digit (values here are positive)
    s = re.sub(r"^[^\d]+", "", s)
    nums = re.findall(r"\d[\d.,]*", s)
    if not nums:
        return None
    s = nums[-1]
    big = any(u in (unit or "").lower() for u in BIG_UNITS)
    if big:
        # unidades "10e3/µL": el valor es decimal (3.135 = 3,135×10³) —
        # NO quitar puntos; la escala está en la unidad.
        scaled = any(t in (unit or "").lower() for t in
                     ("10e3", "10e6", "10³", "10⁶", "x10³", "x10⁶"))
        if not scaled:
            s = s.replace(".", "").replace(",", "")
        else:
            if "," in s and "." in s:
                s = s.replace(".", "").replace(",", ".")
            elif "," in s:
                s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_range(s: str, unit: str = "") -> tuple[float | None, float | None]:
    """Return (low, high) from a reference range string."""
    if not s:
        return (None, None)
    s = s.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    s = re.sub(r",\s+(\d)", r",\1", s)
    s = re.sub(r"\s+", " ", s)
    m = re.search(r"([\d.,]+)\s*(?:-|a|–|—)\s*([\d.,]+)", s)
    if m:
        return (parse_number(m.group(1), unit), parse_number(m.group(2), unit))
    # "Hasta X" / "Inferior a X" / "Menor a X" / "< X" — preferir la última
    # coincidencia ("Niños hasta 15 años: hasta 400" -> 400, no 15)
    low_m = list(re.finditer(
        r"(?:hasta|inferior\s*a|menor\s*a|menor|<)\s*([\d.,]+)", s, re.I))
    if low_m:
        return (None, parse_number(low_m[-1].group(1), unit))
    high_m = list(re.finditer(
        r"(?:mayor\s*a|superior\s*a|mayor|superior|>)\s*([\d.,]+)", s, re.I))
    if high_m:
        return (parse_number(high_m[-1].group(1), unit), None)
    return (None, None)


def compute_flag(value: float | None, low: float | None, high: float | None) -> str:
    if value is None or (low is None and high is None):
        return ""
    if low is not None and value < low:
        return "L"
    if high is not None and value > high:
        return "H"
    return "N"


def parse_date(s: str) -> str | None:
    if not s:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    if not (1 <= d <= 31 and 1 <= mo <= 12):
        return None
    tm = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if tm:
        hh, mm = int(tm.group(1)), int(tm.group(2))
        ss = int(tm.group(3)) if tm.group(3) else 0
        try:
            return datetime(y, mo, d, hh, mm, ss).isoformat()
        except ValueError:
            return None
    try:
        return datetime(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_age(s: str) -> int | None:
    m = re.search(r"(\d{1,3})\s*(?:años|anos|a\u00f1os|anios)", s, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"edad\s*[:]?\s*(\d{1,3})", s, re.I)
    if m:
        return int(m.group(1))
    return None


def _clean_name(name: str) -> str:
    name = clean_text(name).strip()
    name = re.sub(r"\s+", " ", name)
    return name.strip(" :,;.").upper()


# palabras de institución/laboratorio que NUNCA son un paciente
_LAB_INSTITUTION_RE = re.compile(
    r"\b(LABORATORIOS?|LAB|CL[IÍ]NICA|SANATORIO|HOSPITAL|INSTITUTO|"
    r"CENTRO\s+M[EÉ]DICO|CENTRO\s+DE|SERVI[CG]IO\s+DE|ESTUDIO\s+|"
    r"MEDICAL\s+CENTER|FUNDACI[OÓ]N)\b",
    re.I)
# sufijos de razón social que indican empresa, no persona
_LAB_SUFFIX_RE = re.compile(
    r"(?:^|\s)(S\.?R\.?L\.?|LTDA\.?|EIRL|S\.?A\.?C\.?I\.?|"
    r"S\.?A\.?S|C\.?I\.?F\.?A\.?)\s*$",
    re.I)
# nombres de laboratorio ya conocidos (pueden aparecer solos en la cabecera).
# NOTA: no incluir DOCTORES (p. ej. VERDEJO es el médico que firma), ni
# apellidos que un paciente real podría llevar.
_KNOWN_LABS = {"CURIE", "MEDVITAL", "SANACOOP", "SANISIDRO", "YPACARAI",
               "BRUNELLI"}


def _is_lab_like(name: str) -> bool:
    """Detecta si un 'nombre' extraído es en realidad un laboratorio o
    institución (p. ej. 'LABORATORIO BRUNELLI S.R.L'), no un paciente."""
    if not name:
        return False
    if _LAB_INSTITUTION_RE.search(name):
        return True
    # razón social: sin comas de apellido, con sufijo de empresa al final
    if _LAB_SUFFIX_RE.search(name) and "," not in name:
        return True
    # laboratorio conocido por sí solo (cabecera "VERDEJO")
    if name.upper() in _KNOWN_LABS:
        return True
    return False


def normalize_person_name(raw: str) -> str:
    """Normalize a patient name into a canonical 'FIRST LAST' form.

    Handles "ALE MEZA, NEIL" -> "NEIL ALE MEZA" and strips noise tokens.
    Returns '' cuando el texto es un laboratorio/institución (no un paciente).
    """
    raw = _clean_name(raw)
    if _is_lab_like(raw):
        return ""
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) == 2 and parts[0] and parts[1]:
            raw = f"{parts[1]} {parts[0]}"
    raw = re.sub(r"\b(DE|DEL|LA|LAS|LOS|DA|DO|DI|VON|VAN)\b", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def person_match_tokens(name: str) -> set[str]:
    toks = set(_norm_key(name).split())
    noise = {"de", "del", "la", "las", "los", "da", "do", "di"}
    return {t for t in toks if t and t not in noise and len(t) > 1}


# ---------------------------------------------------------------- base

class Report:
    def __init__(self, source_file: str):
        self.source_file = source_file
        self.lab = "Desconocido"
        self.patient_raw = ""
        self.patient_name = ""
        self.doc = ""
        self.age = None
        self.sex = ""
        self.date = None
        self.date_text = ""
        self.order_code = ""
        self.doctor = ""
        self.sections: list[dict] = []
        self.notes = ""
        self.is_document = False

    def add_test(self, section, name, value, unit="", ref_low=None,
                 ref_high=None, ref_text="", raw_result="", qual=None,
                 method="", date_override=None):
        if not name:
            return
        if value is None and qual is None:
            return
        # normalizar unidades hematológicas: 10e3/µL -> /µL (×1000),
        # 10e6/µL -> /µL (×1e6), para comparar entre laboratorios
        value, unit, ref_low, ref_high, ref_text = \
            _normalize_units(value, unit, ref_low, ref_high, ref_text)
        canonical = canonicalize(name, unit, section)
        flag = compute_flag(value, ref_low, ref_high) if value is not None else ""
        for s in self.sections:
            if s["name"] == section:
                sec = s
                break
        else:
            sec = {"name": section, "tests": []}
            self.sections.append(sec)
        sec["tests"].append({
            "name": _clean_name(name),
            "canonical": canonical,
            "value": value,
            "raw_result": (raw_result or "").strip(),
            "unit": (unit or "").strip(),
            "ref_low": ref_low,
            "ref_high": ref_high,
            "ref_text": (ref_text or "").strip(),
            "flag": flag,
            "qual": qual,
            "method": (method or "").strip(),
        })

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "lab": self.lab,
            "patient_raw": self.patient_raw,
            "patient_name": self.patient_name,
            "doc": self.doc,
            "age": self.age,
            "sex": self.sex,
            "date": self.date,
            "date_text": self.date_text,
            "order_code": self.order_code,
            "doctor": self.doctor,
            "sections": self.sections,
            "notes": self.notes,
            "is_document": self.is_document,
        }


# ---------------------------------------------------------------- layout helpers

def _page_lines(doc: fitz.Document, pno: int, y_tol: float = 1.0):
    """Group page words into (y, [(x, word)]) lines."""
    words = doc[pno].get_text("words")
    lines: dict[float, list[tuple[float, str]]] = {}
    order = []
    for tup in words:
        x, y, word = tup[0], tup[1], tup[4]
        placed = False
        for key in lines:
            if abs(key - y) <= y_tol:
                lines[key].append((x, word))
                placed = True
                break
        if not placed:
            lines[round(y, 1)] = [(x, word)]
            order.append(round(y, 1))
    return sorted(lines.items())


def _line_text(pair) -> str:
    _, ws = pair
    return " ".join(w for _, w in sorted(ws))


# ---------------------------------------------------------------- Verdejo

_SECTION_RE = re.compile(
    r"^(HEMOGRAMA|QUIMICA|PERFIL\s+\w+|ELECTROLITOS|COAGULOGRAMA|ORINA|"
    r"INMUNOLOGIA|MARCADORES\s+TUMORALES|PERFIL\s+TIROIDEO|PERFIL\s+CARDIACO|"
    r"HISOPADO|PANEL\s+RESPIRATORIO|TIPIFICACION|HEMOGLOBINA\s+GLICADA|"
    r"SEROLOGIA|HEMATOLOGIA|FORMULA\s+\w+|INDICES\s+\w+|SERIE\s+\w+|"
    r"EXAMEN\s+\w+|SEDIMENTO|ORINA SIMPLE)\b", re.I)


def _parse_verdejo_header(doc: fitz.Document, report: Report):
    """Parse the two-column interleaved header of Verdejo reports.

    Label lines sit ~1.2px BELOW their value lines; left-column labels
    (x~89 colon) pair with left value words, right-column (x~347) with
    right value words.
    """
    lines = _page_lines(doc, 0, y_tol=1.0)
    pairs = {}
    wanted = {"paciente", "documento", "edad", "fecha", "medico", "nro.", "cod."}
    for i, (y, ws) in enumerate(lines):
        text = _line_text((y, ws))
        if ":" not in text:
            continue
        # find every 'LABEL :' group: the colon word, and the word before it
        for j, (x, w) in enumerate(ws):
            if not w.endswith(":"):
                continue
            if j == 0:
                continue
            lab_word = ws[j - 1][1]
            lab = lab_word.strip()
            if lab.lower() not in wanted:
                continue
            # skip "Paciente" when it belongs to "Tipo Paciente"
            if lab.lower() == "paciente" and j >= 2 and \
                    ws[j - 2][1].lower() == "tipo":
                continue
            # the label 'Cod.' / 'Nro.' continues with 'Orden' before ':'
            if lab.lower() in ("cod.", "nro.") and j + 1 < len(ws) and \
                    not ws[j + 1][1].endswith(":") and \
                    ws[j + 1][1].lower() == "orden":
                lab = lab + " Orden"
            col_left = x < 200
            # find the value line above (same column band)
            for py, pws in lines:
                if py < y and y - py <= 3:
                    vwords = [w for vx, w in pws
                              if (vx < 200) == col_left]
                    if vwords:
                        pairs[lab] = " ".join(vwords)
                    break
    first = clean_text(doc[0].get_text())
    m = re.search(r"([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .']+)\s*\n", first)
    if m:
        report.patient_raw = m.group(1).strip()
    report.patient_raw = pairs.get("Paciente", report.patient_raw)
    report.patient_name = normalize_person_name(report.patient_raw)
    report.doc = pairs.get("Documento", "") or ""
    report.date_text = pairs.get("Fecha", "")
    report.date = parse_date(report.date_text)
    m = re.search(r"(\d{1,3})\s*(?:años|anos|a\u00f1os)", pairs.get("Edad", ""), re.I)
    if m:
        report.age = int(m.group(1))
    report.order_code = pairs.get("Cod. Orden", "") or ""
    report.doctor = pairs.get("Medico", "") or ""


_HEADER_FIELD = {
    "paciente": "patient",
    "nombre": "patient",
    "documento": "doc",
    "c i": "doc",
    "c i nro": "doc",
    "edad": "age",
    "fecha": "date",
    "fecha recep": "date",
    "fecha recepcion": "date",
    "medico": "doctor",
    "medico tratante": "doctor",
    "nro orden": "order",
    "cod orden": "order",
}


def _parse_brunelli_header(doc: fitz.Document, report: Report):
    """Cabecera tipo Brunelli: etiquetas que terminan en ':' con el valor a la
    derecha en la MISMA línea ('Paciente : KAREN BETTINA MEZA MORINIGO',
    'Fecha Recep. : 31/07/2026 07:13').

    El nombre del paciente está SIEMPRE en la cabecera (arriba); médicos y
    laboratorios firman al pie o en la primera línea y nunca deben confundirse
    con el paciente.
    """
    lines = _page_lines(doc, 0, y_tol=1.0)
    found = {}
    for y, ws in lines:
        ws = sorted(ws)
        colons = [i for i, w in enumerate(ws) if w[1].endswith(":")]
        if not colons:
            continue
        # pasada 1: qué palabras forman parte de cada etiqueta (pegadas a ':')
        label_idx = set()
        label_key = {}
        for ci in colons:
            parts = []
            cw = ws[ci][1]
            if cw != ":":
                parts.insert(0, cw.rstrip(":"))
                label_idx.add(ci)
            px = ws[ci][0]
            for j in range(ci - 1, -1, -1):
                if ws[j][1].endswith(":"):
                    break
                if px - ws[j][0] > 40:
                    break
                parts.insert(0, ws[j][1])
                label_idx.add(j)
                px = ws[j][0]
            label_key[ci] = _norm_key(" ".join(parts))
        # pasada 2: valor a la derecha de cada etiqueta, en la misma línea
        for ci in colons:
            field = _HEADER_FIELD.get(label_key[ci])
            if not field:
                continue
            val = []
            px = ws[ci][0]
            for j in range(ci + 1, len(ws)):
                if ws[j][1].endswith(":") or j in label_idx:
                    break
                if ws[j][0] - px > 50:
                    break
                val.append(ws[j][1])
                px = ws[j][0]
            v = " ".join(val).strip()
            if v:
                found.setdefault(field, []).append(v)
    report.patient_raw = (found.get("patient") or [""])[0]
    report.patient_name = normalize_person_name(report.patient_raw)
    report.doc = (found.get("doc") or [""])[0]
    report.date_text = (found.get("date") or [""])[0]
    report.date = parse_date(report.date_text)
    m = re.search(r"(\d{1,3})\s*(?:años|anos|a\u00f1os)?",
                  (found.get("age") or [""])[0], re.I)
    if m:
        report.age = int(m.group(1))
    dr = (found.get("doctor") or [""])[0]
    if re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]", dr):
        report.doctor = dr
    report.order_code = (found.get("order") or [""])[0]
    if not report.patient_name:
        first = clean_text(doc[0].get_text())
        m = re.search(r"([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .']+)\s*\n", first)
        if m and not _is_lab_like(m.group(1).strip()):
            report.patient_raw = m.group(1).strip()
            report.patient_name = normalize_person_name(report.patient_raw)


def _parse_brunelli(doc: fitz.Document, report: Report):
    report.lab = "Brunelli"
    _parse_brunelli_header(doc, report)
    start_y = 0.0
    for y, ws in _page_lines(doc, 0, y_tol=1.0):
        name = " ".join(w for x, w in sorted(ws) if x < 140).strip()
        if _norm_key(name) == "analisis":
            start_y = y + 1.0
            break
    _parse_verdejo_results(doc, report, start_y=start_y,
                           cols=(140, 258, 310, 450), scaled_units=False)


def _parse_verdejo(doc: fitz.Document, report: Report):
    # 'Verdejo' es la DOCTORA que firma (cabecera), no el laboratorio ni un
    # análisis: etiqueta neutral para el origen y el nombre real al médico.
    report.lab = "Laboratorio"
    _parse_verdejo_header(doc, report)
    head = clean_text(doc[0].get_text())
    if not report.doctor or not re.search(r"[A-Za-z]", report.doctor.replace("-", "")):
        dm = re.search(r"^((?:Dra?\.|Bioq\.|Lic\.|Q\.F\.)[^\n]+)$", head, re.M)
        if dm:
            line = re.sub(r"\bReg\.?\s*Prof\.?.*$", "", dm.group(1), flags=re.I)
            report.doctor = re.sub(r"\s+", " ", line).strip()
    # empezar a leer resultados DESPUÉS de la fila de encabezado de columnas
    # (evita que teléfono/dirección de la cabecera se parseen como análisis).
    start_y = 0.0
    for y, ws in _page_lines(doc, 0, y_tol=1.0):
        low = _norm_key(_line_text((y, ws)))
        if "determinaciones" in low or ("resultados" in low and "unidad" in low):
            start_y = y + 1.0
            break
    _parse_verdejo_results(doc, report, start_y=start_y)


def _parse_verdejo_results(doc: fitz.Document, report: Report,
                           start_y: float = 0.0,
                           cols: tuple = (200, 290, 335, 450),
                           scaled_units: bool = True):
    # columns (x): name<cols0, result cols0-cols1, unit cols1-cols2,
    #              method cols2-cols3, ref>cols3
    name_c, res_c, unit_c, meth_c = cols
    section = "GENERAL"
    pending_method = ""
    pending_ref = ""
    pending_unit = ""
    for pno in range(len(doc)):
        for y, ws in _page_lines(doc, pno, y_tol=1.5):
            if pno == 0 and y < start_y:
                continue
            name_words = [w for x, w in ws if x < name_c]
            res_words = [w for x, w in ws if name_c <= x < res_c]
            unit_words = [w for x, w in ws if res_c <= x < unit_c]
            meth_words = [w for x, w in ws if unit_c <= x < meth_c]
            ref_words = [w for x, w in ws if x >= meth_c]
            name = " ".join(name_words).strip()
            res = " ".join(res_words).strip()
            unit = " ".join(unit_words).strip()
            meth = " ".join(meth_words).strip()
            ref = " ".join(ref_words).strip()

            # section header?
            if name and not re.search(r"\d", name) and (name.isupper() or
                                                        _SECTION_RE.match(name)) \
                    and len(name.split()) <= 4 and not res and not ref:
                sec_m = _SECTION_RE.match(name)
                if sec_m:
                    section = name
                    pending_method = pending_ref = pending_unit = ""
                    continue

            if not name:
                # continuation line: method or ref fragment
                if meth:
                    pending_method += " " + meth
                if ref:
                    pending_ref += " " + ref
                continue

            # test row
            punit = unit
            if not scaled_units:
                # unidades simples (p. ej. "p/ul"): los puntos son decimales
                punit = re.sub(r"(10e[0-9]+|10³|10⁶|x10[³⁶]?|mm3|/ul|/µl|/μl)",
                               "", unit, flags=re.I)
            value = parse_number(res, punit)
            low, high = parse_range(ref if ref else pending_ref, punit)
            if not ref:
                ref = pending_ref.strip()
            qual = None
            if value is None and res:
                rn = _norm_key(res)
                if rn in ("negativo", "positivo", "no detectable", "trazas",
                          "neg", "pos"):
                    qual = res
            if meth:
                pending_method = meth
            full_method = (pending_method + " " + meth).strip()
            report.add_test(section, name, value, unit, low, high,
                            ref, raw_result=res, qual=qual, method=full_method)
            pending_method = pending_ref = pending_unit = ""


# ---------------------------------------------------------------- Curie

_CURIE_NAME = re.compile(
    r"^(leucocitos|eritrocitos|hemoglobina|hematocrito|v\.c\.m\.|h\.c\.m\.|"
    r"c\.h\.c\.m\.|ade|plaquetas|neutr[óo]filos segmentados|linfocitos|"
    r"monocitos|eosin[óo]filos|bas[óo]filos|creatinina|urea|acido urico|"
    r"colesterol total|colesterol ldl|colesterol hdl|colesterol vldl|"
    r"trigliceridos|lipidos totales|fosfolipidos|asat \(got\)|alat \(gpt\)|"
    r"bilirrubina total|fosfatasa alcalina|glicemia|calcio total|"
    r"fosforo|magnesio|ck total|ck mb|ldh|pas total|tsh|t4 libre|t4 total|"
    r"t3 libre|t3 total|vitamina d|ferritina|glucosa|hba1c|"
    r"hemoglobina glicada|acido urico sangre|glucosa en sangre)\b", re.I)

_CURIE_SECTIONS = {
    "serie blanca", "serie roja", "plaquetas", "formula leucocitaria",
    "formula absoluta", "perfil lipidico sangre", "perfil hepatico sangre",
    "orina simple chorro medio", "examen fisico", "examen quimico",
    "examen del sedimento", "sedimento urinario", "glucemia", "glucemia sangre",
    "creatinina sangre", "urea sangre", "glicemia sangre",
}


def _parse_curie(doc: fitz.Document, report: Report):
    report.lab = "Curie"
    first = clean_text(doc[0].get_text())
    m = re.search(r"paciente\s*:?\s*([^\n]+)", first, re.I)
    if m:
        report.patient_raw = m.group(1).split("Edad:")[0].strip()
    m = re.search(r"edad\s*:?\s*(\d{1,3})\s*a", first, re.I)
    if m:
        report.age = int(m.group(1))
    m = re.search(r"sexo\s*:?\s*([MF])", first, re.I)
    if m:
        report.sex = m.group(1).upper()
    # CI number: value column is read before the "CI N°" label column
    m = re.search(r":\s*(\d{6,})\s*\n.*?CI\s*N\s*[°ºÂ]", first, re.I | re.S)
    if m:
        report.doc = m.group(1)
    if not report.doc:
        m = re.search(r"ci\s*n\s*[°ºÂ]?\s*\n\s*:?\s*(\d+)", first, re.I)
        if m:
            report.doc = m.group(1)
    m = re.search(r"fecha\s+de\s+ingreso\s*:?\s*([\d/]+\s+[\d:]+)", first, re.I)
    if m:
        report.date = parse_date(m.group(1))
        report.date_text = m.group(1).strip()
    m = re.search(r"c[óo]digo\s*:?\s*(\d+)", first, re.I)
    if m:
        report.order_code = m.group(1)
    m = re.search(r"m[ée]dico\s*:?\s*([A-ZÁÉÍÓÚÑ. ]+)", first, re.I)
    if m:
        report.doctor = m.group(1).strip()
    report.patient_name = normalize_person_name(report.patient_raw)

    cur_section = "GENERAL"
    prev_left = ""
    prev_right = ""
    for pno in range(len(doc)):
        for y, ws in _page_lines(doc, pno, y_tol=1.0):
            left = " ".join(w for x, w in ws if x < 300)
            right = " ".join(w for x, w in ws if x >= 300)
            ln = _norm_key(left)
            if ln in _CURIE_SECTIONS or (left and not re.search(r"\d", left)
                                         and len(left) < 30 and left.isupper()
                                         and ln not in ("resultados", "intervalo")):
                cur_section = left.strip()
                prev_left = prev_right = ""
                continue
            m = _CURIE_NAME.match(left)
            if not m:
                prev_left, prev_right = left, right
                continue
            name = m.group(1).strip()
            low, high = parse_range(right)
            # result: number on this line after name, else from previous line
            rest = left[len(name):].strip()
            value = parse_number(rest)
            res_raw = re.search(r"-?\d[\d.,]+", rest)
            res_raw = res_raw.group(0) if res_raw else ""
            if value is None:
                value = parse_number(prev_left)
                res_raw = re.search(r"-?\d[\d.,]+", prev_left)
                res_raw = res_raw.group(0) if res_raw else ""
            unit = _unit_from(right) or _unit_from(rest)
            if unit.lower() in ("mg", "dl", "g", "ml", "l"):
                # unit fragments like "mg/dL" split across x -> join
                unit = right.replace(re.sub(r"[\d.,\- ]+", "", right), "") or unit
            qual = None
            if value is None and rest:
                rn = _norm_key(rest)
                if rn in ("negativo", "positivo", "no detectable", "neg", "pos"):
                    qual = rest
            report.add_test(cur_section, name, value, unit, low, high, right,
                            raw_result=res_raw or rest, qual=qual)
            prev_left, prev_right = left, right


def _unit_from(s: str) -> str:
    """Extrae una unidad de laboratorio válida de un texto (o '')."""
    if not s:
        return ""
    m = _UNIT_RE.search(s)
    return m.group(0).strip() if m else ""


_UNIT_RE = re.compile(
    r"(?:10[eE]?3/|10[eE]?6/|x10[36]/|10\u00b3/|10\u2076/)?\s*"
    r"(?:mg/dL|g/dL|mg/dl|g/dl|mg%|mg|mmol/mol|mmol/L|mEq/L|mEq/l|uL|µL|μL|"
    r"ng/mL|ng/ml|pg/dL|pg/ml|µg/dL|μg/dL|µIU/mL|μIU/mL|mU/L|U/L|U/I|"
    r"uI/mL|IU/mL|fL|pg|%|mm/h|mg/d?|dl|g|ml|l|dL|gldl|gdl|md/dl)")


# ---------------------------------------------------------------- Ypacarai

_YPAC_TEST = re.compile(
    r"^(glicemia|glucosa|urea|creatinina|acido urico|colesterol total|"
    r"hdl|ldl|vldl|triglic[ée]ridos|l[íi]pidos totales|got|gpt|"
    r"bilirrubina\s*t|bilirrubina\s*d|bilirrubina\s*i|fosfatasa alcalina|"
    r"calcio|proteinas totales|albumina|amilasa|f[óo]sforo|magnesio|"
    r"colesterol)\b", re.I)


def _parse_ypacarai(doc: fitz.Document, report: Report):
    report.lab = "Ypacarai"
    page_text = clean_text(doc[0].get_text())
    m = re.search(r"fecha\s*:?\s*([\d/]+)", page_text, re.I)
    if m:
        report.date = parse_date(m.group(1))
        report.date_text = m.group(1).strip()
    m = re.search(r"nombre\s+usuario\s*:?\s*([^\n]+)", page_text, re.I)
    if m:
        report.patient_raw = m.group(1).strip()
    m = re.search(r"edad\s*:?\s*(\d{1,3})\s*a", page_text, re.I)
    if m:
        report.age = int(m.group(1))
    report.patient_name = normalize_person_name(report.patient_raw)

    section = "QUIMICA"
    lines = _page_lines(doc, 0, y_tol=2.5)
    # index of every test-name line
    test_idx = [i for i, (y, ws) in enumerate(lines)
                if _YPAC_TEST.match(_line_text((y, ws)).strip())]
    for k, i in enumerate(test_idx):
        name = _YPAC_TEST.match(_line_text(lines[i]).strip()).group(1).strip()
        block = lines[i: test_idx[k + 1] if k + 1 < len(test_idx) else None]
        _finish_ypac(name, block, report, section)


def _finish_ypac(name, lines, report, section):
    """Collect value + ref from a test-name line plus the following block.

    Layout: value in the x<330 column, reference in the x>=330 column.
    The value often shares the same (merged) line as the test name.
    """
    value = None
    low = high = None
    ref_text = ""
    res_raw = ""
    name_tokens = set(_norm_key(name).split())
    range_re = re.compile(r"\b\d[\d.,]*\s*[-–—]\s*\d[\d.,]*\b")
    for y, ws in lines[:8]:
        val_words = [w for x, w in ws if x < 330]
        ref_words = [w for x, w in ws if x >= 330]
        val_text = clean_text(" ".join(val_words))
        ref_text_line = clean_text(" ".join(ref_words))
        if not val_text and not ref_text_line:
            continue
        # strip the test name itself off the value column
        val_text_clean = " ".join(w for w in val_text.split()
                                  if _norm_key(w) not in name_tokens)
        full = val_text_clean + " " + ref_text_line
        # reference? require range keywords or a clean "N - M" range
        ref_like = (bool(re.search(r"hasta|menor|mayor|inferior|superior|"
                                   r"hombres|mujeres|normal", full, re.I))
                    and bool(re.search(r"\d", full))) or \
                   bool(range_re.search(full))
        # value: last number in the value column
        if value is None and val_text_clean:
            nums = re.findall(r"-?\d[\d.,]+", val_text_clean)
            if nums:
                cand = parse_number(nums[-1])
                if cand is not None:
                    value = cand
                    res_raw = nums[-1]
        if ref_like and ref_text == "":
            lo, hi = parse_range(full)
            # discard inverted/garbage ranges (e.g. "-85-10,5")
            if (lo is not None or hi is not None) and not (
                    lo is not None and hi is not None and lo > hi):
                low, high = lo, hi
                ref_text = full.strip()
            continue
        # glicemia-style: value alone in ref column (no range printed)
        if value is None and ref_text_line:
            nums = re.findall(r"-?\d[\d.,]+", ref_text_line)
            if nums:
                cand = parse_number(nums[-1])
                if cand is not None:
                    value = cand
                    res_raw = nums[-1]
    unit = _unit_from(ref_text or res_raw or "")
    report.add_test(section, name, value, unit, low, high, ref_text,
                    raw_result=res_raw)


# ---------------------------------------------------------------- Sanacoop

_SANA_TEST = re.compile(
    r"^(hemoglobina glicada\s*\w*|tsh\s*\w*|t4\s*(libre|total)?|t3\s*(libre|total)?|"
    r"pas\s*(total|libre)?|vitamina d|ferritina|prolactina|cortisol|"
    r"glucosa|creatinina|urea|colesterol|trigliceridos|hba1c|glicemia)\b",
    re.I)


def _parse_sanacoop(doc: fitz.Document, report: Report, start_page: int = 0):
    report.lab = "Sanacoop"
    full = clean_text("".join(p.get_text() for p in doc[start_page:]))
    m = re.search(r"paciente\s*:?\s*([^\n]+)", full, re.I)
    if m:
        report.patient_raw = m.group(1).strip()
    m = re.search(r"edad\s*:?\s*(\d{1,3})\s*a", full, re.I)
    if m:
        report.age = int(m.group(1))
    m = re.search(r"sexo\s*:?\s*([MF])", full, re.I)
    if m:
        report.sex = m.group(1).upper()
    m = re.search(r"doc\.?\s*n[°º]?\s*:?\s*([\d-]+)", full, re.I)
    if m:
        report.doc = m.group(1).strip("- ")
    m = re.search(r"fecha\s+de\s+(?:toma\s+de\s+)?muestra\s*:?\s*([\d/]+)", full, re.I)
    if m:
        report.date = parse_date(m.group(1))
        report.date_text = m.group(1).strip()
    m = re.search(r"impreso\s*:?\s*([\d/]+)", full, re.I)
    if m and not report.date:
        report.date = parse_date(m.group(1))
        report.date_text = m.group(1).strip()
    report.patient_name = normalize_person_name(report.patient_raw)

    # Collect lines into per-test blocks, then resolve value + refs.
    for pno in range(start_page, len(doc)):
        lines = _page_lines(doc, pno, y_tol=1.0)
        cur_test = None
        block: list[str] = []
        def flush():
            nonlocal cur_test, block
            if not cur_test or not block:
                cur_test, block = None, []
                return
            _finish_sana(cur_test, block, report)
            cur_test, block = None, []
        for y, ws in lines:
            text = clean_text(_line_text((y, ws)))
            m = _SANA_TEST.match(text.strip())
            if m:
                flush()
                cur_test = m.group(1).strip()
                block = []
                continue
            if cur_test:
                block.append(text)
        flush()


def _finish_sana(name, block, report):
    """Resolve value + refs for one Sanacoop test block."""
    value = None
    res_raw = ""
    low = high = None
    ref_text = ""
    prev_line = ""
    for line in block:
        ln = _norm_key(line)
        if re.search(r"material|metodo|m[ée]todo|observa|firma|muestra|impreso|"
                     r"pagina|m[ée]todo|estandar|prog\.|hora de", ln):
            prev_line = line
            continue
        if "resultado" in ln:
            m = re.search(r"resultado\s*:?\s*([\d.,]+)", line, re.I)
            if m:
                value = parse_number(m.group(1))
                res_raw = m.group(1)
            elif value is None and prev_line:
                # value on the line before "Resultado:"
                nums = re.findall(r"-?\d[\d.,]+", prev_line)
                if nums:
                    cand = parse_number(nums[-1])
                    if cand is not None:
                        value = cand
                        res_raw = nums[-1]
            continue
        # reference lines (fix OCR: 'S,' -> '5,', 'S0' -> '50')
        if re.search(r"nivel|adultos|hombres|mujeres|intervalo|normal|pre|"
                     r"diabetes|referencia|deseable|insuficiente|deficiente|"
                     r"a\u00f1os|anos|hasta|inferior|mayor", ln):
            fixed = re.sub(r"(?i)\bS(?=[,.]?\d)", "5", line)
            rm = re.search(r"([\d.,]+\s*(?:-|a)\s*[\d.,]+|hasta\s*[\d.,]+|"
                           r"inferior\s*a\s*[\d.,]+|mayor\s*a\s*[\d.,]+)", fixed, re.I)
            if rm:
                lo, hi = parse_range(rm.group(1))
                if (lo is not None or hi is not None) and not (
                        lo is not None and hi is not None and lo > hi):
                    low, high = lo, hi
                    ref_text = fixed.strip()
            prev_line = line
            continue
        # value-looking short line: "% 4,9" / "4,9 %" / "30 mmo/mol"
        if value is None and len(line) < 40:
            nm = re.search(r"(\d[\d.,]*)\s*%", line) or \
                 re.search(r"%\s*(\d[\d.,]*)", line) or \
                 re.search(r"(\d[\d.,]+)\s*[a-zA-Zµμ/°³⁶]+", line)
            if nm:
                cand = parse_number(nm.group(1))
                if cand is not None:
                    value = cand
                    res_raw = nm.group(1)
        prev_line = line
    if value is None and res_raw == "":
        return
    unit = _unit_from(res_raw or ref_text or "")
    report.add_test("PERFIL", name, value, unit, low, high, ref_text,
                    raw_result=res_raw)


# ---------------------------------------------------------------- Sanisidro

_SANIS_TEST = re.compile(
    r"^(gl[óo]bulos rojos|hematocrito|hemoglobina|gl[óo]bulos blancos|"
    r"neutr[óo]filos segmentados|neutr[óo]filos en cayado|linfocitos|"
    r"monocitos|eosin[óo]filos|chcm|plaquetas|vcm|hcm|pcr|glicemia|glucosa|"
    r"leucocitos)\b", re.I)


def _parse_sanisidro(doc: fitz.Document, report: Report):
    report.lab = "Sanisidro"
    full = clean_text("".join(p.get_text() for p in doc))
    m = re.search(r"paciente\s*:?\s*([^\n]+)", full, re.I)
    if m:
        report.patient_raw = m.group(1).split("Edad")[0].strip()
    m = re.search(r"edad\s*:?\s*(\d{1,3})\s*a", full, re.I)
    if m:
        report.age = int(m.group(1))
    m = re.search(r"sexo\s*:?\s*([MF])", full, re.I)
    if m:
        report.sex = m.group(1).upper()
    m = re.search(r"documento\s*:?\s*([\d.]+)", full, re.I)
    if m:
        report.doc = m.group(1)
    m = re.search(r"fecha\s*:?\s*([\d/]+)", full, re.I)
    if m:
        report.date = parse_date(m.group(1))
        report.date_text = m.group(1).strip()
    report.patient_name = normalize_person_name(report.patient_raw)

    # columns: name x<185, value 185-310, unit 310-440, ref x>=440
    section = "HEMATOLOGIA"
    pending = {}
    for pno in range(len(doc)):
        for y, ws in _page_lines(doc, pno, y_tol=2.0):
            name = " ".join(w for x, w in ws if x < 185).strip()
            val = " ".join(w for x, w in ws if 185 <= x < 310).strip()
            unit = " ".join(w for x, w in ws if 310 <= x < 440).strip()
            ref = " ".join(w for x, w in ws if x >= 440).strip()
            if not name and not val and not ref:
                continue
            if re.search(r"hematologia|serologia|quimica|resultado|metodo|"
                         r"rango de|referencia", name, re.I):
                sec_m = re.match(r"(HEMATOLOGIA|SEROLOGIA|QUIMICA)", name, re.I)
                if sec_m:
                    section = sec_m.group(1)
                continue
            m = _SANIS_TEST.match(name)
            if not m:
                continue
            tname = m.group(1).strip()
            value = parse_number(val, unit)
            low = high = None
            ref_text = ""
            if ref:
                lo, hi = parse_range(ref)
                if lo is not None or hi is not None:
                    low, high = lo, hi
                    ref_text = ref
            # el texto de referencia ("Deseable: Inferior a 150 mg/dL") puede
            # invadir la columna de unidad: dejar solo la unidad real y
            # conservar el texto como referencia si no se obtuvo de otra columna
            if unit and re.search(r"deseable|inferior|hasta|menor|mayor|"
                                  r"superior|rango|referencia|no deseable",
                                  unit, re.I):
                if not ref_text:
                    ref_text = unit
                unit = _unit_from(unit) or ""
            report.add_test(section, tname, value, unit, low, high, ref_text,
                            raw_result=val)


# ---------------------------------------------------------------- Medvital (radiology)

def _parse_medvital(doc: fitz.Document, report: Report):
    report.lab = "Medvital"
    full = clean_text("".join(p.get_text() for p in doc))
    m = re.search(r"paciente\s*:?\s*([^\n]+)", full, re.I)
    if m:
        report.patient_raw = m.group(1).split("Sexo")[0].strip()
    m = re.search(r"sexo\s*:?\s*([MF])", full, re.I)
    if m:
        report.sex = m.group(1).upper()
    m = re.search(r"c\.?i\.?\s*n[°º]?\s*:?\s*([\d]+)", full, re.I)
    if m:
        report.doc = m.group(1)
    m = re.search(r"edad\s*:?\s*(\d{1,3})", full, re.I)
    if m:
        report.age = int(m.group(1))
    m = re.search(r"fecha\s*:?\s*([\d-]+)", full, re.I)
    if m:
        report.date = parse_date(m.group(1).replace("-", "/"))
        report.date_text = m.group(1)
    m = re.search(r"estudio\s*:?\s*([^\n]+)", full, re.I)
    if m:
        report.order_code = m.group(1).strip()
    report.patient_name = normalize_person_name(report.patient_raw)
    report.is_document = True
    report.notes = full.strip()


# ---------------------------------------------------------------- dispatch

def detect_format(path: str) -> str:
    doc = fitz.open(path)
    try:
        text = clean_text("".join(p.get_text() for p in doc))
    finally:
        doc.close()
    t = _norm_key(text)
    if "angiotomografia" in t or ("angiotac" in t and "medi" in t):
        return "medvital"
    if "brunelli" in t:
        return "brunelli"
    if "laboratoriocurie" in t or "sanatorio italiano" in t or "ord5" in _norm_key(path) or "ord6" in _norm_key(path):
        return "curie"
    if "ypacarai" in t and "valores obtenidos" in t:
        return "ypacarai"
    if "labsanisidro" in t:
        return "sanisidro"
    if "impedancia" in t or "glucosa oxidasa" in t or "itagua paraguay" in t \
            or "verdejo" in t:
        return "verdejo"
    if "neodiagnosticos" in t or "sanacoop" in t or "hemoglobina glicada" in t:
        return "sanacoop"
    return "verdejo"


def parse_pdf(path: str) -> list[Report]:
    """Parse a PDF into one or more Reports."""
    try:
        doc = fitz.open(path)
    except Exception:
        return []
    try:
        fmt = detect_format(path)
        fname = path.split("\\")[-1].split("/")[-1]
        reports: list[Report] = []
        if fmt == "verdejo":
            r = Report(fname)
            _parse_verdejo(doc, r)
            reports.append(r)
        elif fmt == "brunelli":
            r = Report(fname)
            _parse_brunelli(doc, r)
            reports.append(r)
        elif fmt == "curie":
            r = Report(fname)
            _parse_curie(doc, r)
            reports.append(r)
        elif fmt == "ypacarai":
            # check for a second report (Sanacoop pages) in the same file
            r = Report(fname)
            _parse_ypacarai(doc, r)
            reports.append(r)
            rest = clean_text("".join(doc[i].get_text() for i in range(1, len(doc))))
            if "sanacoop" in _norm_key(rest) or "neodiagnosticos" in _norm_key(rest) \
                    or "hemoglobina glicada" in _norm_key(rest):
                r2 = Report(fname)
                _parse_sanacoop(doc, r2, start_page=1)
                if r2.date is None:
                    r2.date = r.date
                    r2.date_text = r.date_text
                if not r2.patient_name:
                    r2.patient_raw = r.patient_raw
                    r2.patient_name = r.patient_name
                reports.append(r2)
        elif fmt == "sanacoop":
            r = Report(fname)
            _parse_sanacoop(doc, r)
            reports.append(r)
        elif fmt == "sanisidro":
            r = Report(fname)
            _parse_sanisidro(doc, r)
            reports.append(r)
        elif fmt == "medvital":
            r = Report(fname)
            _parse_medvital(doc, r)
            reports.append(r)
        else:
            r = Report(fname)
            _parse_verdejo(doc, r)
            reports.append(r)
        return reports
    finally:
        doc.close()
