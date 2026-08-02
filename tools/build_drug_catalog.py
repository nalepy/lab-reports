# -*- coding: utf-8 -*-
"""Construye el catálogo de medicamentos -> app/drug_catalog.json (multi-fuente).

FUENTES (todo real, nada inventado):
  1) ANVISA (Brasil) — dados abiertos, registro COMPLETO de medicamentos:
     https://dados.anvisa.gov.br/dados/DADOS_ABERTOS_MEDICAMENTOS.csv
     Da cobertura amplia: marca comercial (NOME_PRODUTO) + principio activo
     (PRINCIPIO_ATIVO). Miles de fármacos y marcas de LATAM/Brasil.
  2) CIMA (AEMPS, España) — capa de DOSIS REALES + marcas curadas LATAM/BR/ES,
     ya generada en app/drug_catalog.json (se lee como 'overlay' antes de
     sobrescribir). Las dosis son universales (un comprimido de 50 mg es 50 mg).
  3) Extras curados verificados (marcas de Paraguay y clase incretina) con dosis
     tomadas de la ficha oficial del producto — p. ej. Lipoless = tirzepatida
     (Laboratorios Eticos Paraguay), dosis 2,5–15 mg.

El resultado se agrupa por principio activo: {generic, brands[], strengths[]}.
La entrada MANUAL en la app cubre cualquier fármaco/marca no listado.

Uso:  python tools/build_drug_catalog.py
"""
import csv
import io
import json
import os
import re
import ssl
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "app", "drug_catalog.json")
ANVISA_CSV = "https://dados.anvisa.gov.br/dados/DADOS_ABERTOS_MEDICAMENTOS.csv"

sys.path.insert(0, os.path.join(HERE, ".."))
from app.drugs import _norm, ALIASES  # noqa: E402

# ANVISA sirve una cadena incompleta (falta un intermedio) que Python ssl no
# puede resolver por sí solo (schannel de Windows sí). truststore inyecta el
# almacén de CAs del sistema en ssl y conserva la verificación TLS completa.
# Fallback: certifi (bundle Mozilla). Nunca se desactiva la verificación.
def _ssl_context() -> ssl.SSLContext:
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
    return ssl.create_default_context()


_SSL = _ssl_context()

# prefijos/sufijos de sal para obtener la base del principio activo
SALT_LEAD = re.compile(
    r"^(cloridrato|clorhidrato|sulfato|maleato|besilato|besilato|acetato|"
    r"fumarato|tartarato|tartrato|succinato|mesilato|fosfato|bromidrato|"
    r"bromhidrato|nitrato|citrato|sodico|potassico|calcico|hemifumarato|"
    r"dicloridrato|valerato|propionato|pamoato|estolato)\s+de\s+",
    re.IGNORECASE)
SALT_TRAIL = re.compile(
    r"\s+(sodica|sódica|sodico|sódico|potassica|potássica|potassico|potássico|"
    r"calcica|cálcica|dihidratado|monohidratado|anhidro|trihidratado)$",
    re.IGNORECASE)

STRENGTH_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)?)\s*"
    r"(mg\s*/\s*ml|mcg\s*/\s*ml|microgramas?|microgramos?|miligramas?|"
    r"miligramos?|gramas?|gramos?|mcg|mg|µg|ug|g|ui|u\.i\.|%)",
    re.IGNORECASE)

FORM_WORDS = re.compile(
    r"\b(comprimidos?|comp|capsulas?|c[aá]psulas?|caps|tabletas?|drageas?|"
    r"grageas?|solucao|soluç[aã]o|solucion|soluci[oó]n|injetavel|inyectable|"
    r"iny|xarope|jarabe|suspensao|suspensi[oó]n|creme|crema|gel|pomada|"
    r"revestidos?|recubiertos?|pelicula|pel[ií]cula|liberacao|liberaci[oó]n|"
    r"prolongada|oral|subcutanea|subcut[aá]nea|reditabs?|md|forte|xr|sr)\b",
    re.IGNORECASE)


def _norm_unit(u: str) -> str:
    u = u.lower().replace(" ", "")
    if u.startswith(("micrograma", "microgramo")):
        return "mcg"
    if u.startswith(("miligrama", "miligramo")):
        return "mg"
    if u.startswith(("grama", "gramo")):
        return "g"
    if u in ("µg", "ug"):
        return "mcg"
    if u in ("ui", "u.i."):
        return "UI"
    return u


def _parse_num(raw: str):
    raw = raw.replace(" ", "")
    head = raw.split("/", 1)[0] if "/" in raw else raw
    if re.match(r"^\d{1,3}\.\d{3}$", head):
        head = head.replace(".", "")
    else:
        head = head.replace(",", ".")
    try:
        return float(head)
    except ValueError:
        return None


def parse_strengths(name: str) -> dict:
    out = {}
    for m in STRENGTH_RE.finditer(name):
        raw = m.group(1).replace(" ", "")
        unit = _norm_unit(m.group(2))
        val = _parse_num(raw)
        if val is None or val <= 0 or val > 100000:
            continue
        if "/" in raw:
            label = f"{raw.replace('.', ',')} {unit}"
        elif val == int(val):
            label = f"{int(val)} {unit}"
        else:
            label = f"{val:g}".replace(".", ",") + f" {unit}"
        out[label] = (unit, val)
    return out


def base_inn(generica: str) -> str:
    """Base del principio activo (sin sal) para agrupar; '' si es combinación."""
    g = generica.strip()
    if "," in g or " + " in g or re.search(r"\se\s", g):
        return ""  # combinación: se trata como su propia clave abajo
    g = SALT_LEAD.sub("", g)
    g = SALT_TRAIL.sub("", g)
    return g.strip()


def title(s: str) -> str:
    return s.title() if (s.isupper() or s.islower()) else s


def clean_brand(name: str) -> str:
    b = STRENGTH_RE.sub(" ", name)
    b = re.sub(r"\b\d+(?:[.,]\d+)?\b", " ", b)
    b = FORM_WORDS.sub(" ", b)
    b = re.sub(r"[®™]", " ", b)
    b = re.sub(r"\s+", " ", b).strip(" .-·,")
    return b


# ---- extras verificados (dosis de ficha oficial; marcas Paraguay/incretinas) ----
EXTRAS = {
    "tirzepatida": {"generic": "Tirzepatida",
                    "brands": ["Lipoless", "Mounjaro", "Zepbound"],
                    "strengths": ["2,5 mg", "5 mg", "7,5 mg", "10 mg", "12,5 mg", "15 mg"]},
    "semaglutida": {"generic": "Semaglutida",
                    "brands": ["Ozempic", "Wegovy", "Rybelsus"],
                    "strengths": ["0,25 mg", "0,5 mg", "1 mg", "2 mg", "3 mg", "7 mg", "14 mg"]},
    "liraglutida": {"generic": "Liraglutida", "brands": ["Victoza", "Saxenda"],
                    "strengths": ["6 mg/ml"]},
    "dulaglutida": {"generic": "Dulaglutida", "brands": ["Trulicity"],
                    "strengths": ["0,75 mg", "1,5 mg", "3 mg", "4,5 mg"]},
    "nebivolol": {"generic": "Nebivolol", "brands": ["Nebilet", "Bystolic"],
                  "strengths": ["2,5 mg", "5 mg", "10 mg"]},
    "empagliflozina": {"generic": "Empagliflozina", "brands": ["Jardiance"],
                       "strengths": ["10 mg", "25 mg"]},
    "dapagliflozina": {"generic": "Dapagliflozina", "brands": ["Forxiga", "Farxiga"],
                       "strengths": ["5 mg", "10 mg"]},
}


def fetch_anvisa() -> list[list[str]]:
    print("descargando ANVISA (Brasil)…", flush=True)
    req = urllib.request.Request(ANVISA_CSV, headers={"User-Agent": "labs-catalog/3.0"})
    with urllib.request.urlopen(req, timeout=180, context=_SSL) as r:
        data = r.read()
    print(f"  {len(data)//1024} KB descargados", flush=True)
    text = data.decode("latin-1")
    return list(csv.reader(io.StringIO(text), delimiter=";"))


def main():
    # 1) overlay CIMA (dosis reales + marcas curadas) — leer ANTES de sobrescribir
    overlay = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT, encoding="utf-8"))
            if "CIMA" in (prev.get("source", "")):
                for e in prev.get("drugs", []):
                    overlay[_norm(base_inn(e["generic"]))] = e
        except (OSError, json.JSONDecodeError):
            pass
    print(f"overlay CIMA: {len(overlay)} genéricos con dosis")

    # 2) ANVISA -> agrupar por principio activo base
    rows = fetch_anvisa()
    header = rows[0]
    idx = {h: i for i, h in enumerate(header)}
    iN, iP = idx["NOME_PRODUTO"], idx["PRINCIPIO_ATIVO"]
    iT, iS = idx["TIPO_PRODUTO"], idx["SITUACAO_REGISTRO"]

    cat = {}   # gk -> {generic, brands:set, strengths:{label:(u,v)}, key}
    n_prod = 0
    for row in rows[1:]:
        if len(row) <= max(iN, iP, iT, iS):
            continue
        if row[iT].strip().upper() != "MEDICAMENTO":
            continue
        if row[iS].strip().lower() != "ativo":
            continue
        generica = (row[iP] or "").strip()
        if not generica:
            continue
        base = base_inn(generica) or generica  # combinaciones: usar tal cual
        gk = _norm(base)
        if not gk or len(gk) < 3:
            continue
        n_prod += 1
        e = cat.setdefault(gk, {"generic": title(base), "brands": set(),
                                "strengths": {}, "key": ALIASES.get(gk)})
        brand = clean_brand(row[iN] or "")
        bn = _norm(brand)
        if bn and bn != gk and len(bn) >= 2:
            e["brands"].add(brand.title() if brand.isupper() else brand)
        for label, uv in parse_strengths(row[iN] or "").items():
            e["strengths"][label] = uv

    # 3) overlay CIMA: dosis reales + marcas curadas
    for gk, o in overlay.items():
        e = cat.setdefault(gk, {"generic": o["generic"], "brands": set(),
                                "strengths": {}, "key": o.get("key")})
        e["key"] = e["key"] or o.get("key")
        for b in o.get("brands", []):
            e["brands"].add(b)
        # las dosis CIMA son de referencia limpia: se anteponen
        cima_str = o.get("strengths", [])
        if cima_str:
            e["_cima"] = cima_str

    # 4) extras verificados
    for gk, x in EXTRAS.items():
        e = cat.setdefault(gk, {"generic": x["generic"], "brands": set(),
                                "strengths": {}, "key": ALIASES.get(gk)})
        for b in x["brands"]:
            e["brands"].add(b)
        e["_cima"] = x["strengths"]  # dosis verificadas de ficha

    # 5) materializar
    def sort_strengths(d):
        return [k for k, _ in sorted(d.items(), key=lambda kv: (kv[1][0], kv[1][1]))]

    catalog = []
    for gk, e in cat.items():
        strengths = e.get("_cima") or sort_strengths(e["strengths"])
        catalog.append({
            "key": e["key"],
            "generic": e["generic"],
            "aliases": [gk],
            "brands": sorted(set(e["brands"]), key=str.lower)[:50],
            "strengths": strengths[:24],
            "form": "",
        })

    catalog.sort(key=lambda x: x["generic"].lower())
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"source": "ANVISA (Brasil) + CIMA (dosis) + curado LATAM/PY",
                   "count": len(catalog), "drugs": catalog},
                  f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(OUT) // 1024
    n_dosed = sum(1 for e in catalog if e["strengths"])
    n_brands = sum(len(e["brands"]) for e in catalog)
    print(f"\nOK: {len(catalog)} principios activos, {n_brands} marcas, "
          f"{n_dosed} con dosis; {n_prod} productos ANVISA -> "
          f"{os.path.relpath(OUT)} ({size_kb} KB)")


if __name__ == "__main__":
    main()
