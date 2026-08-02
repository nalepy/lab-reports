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
    "drospirenona": {"generic": "Drospirenona", "brands": ["Yasmin", "Yaz", "Drelle", "Drosbela"],
                     "strengths": ["3 mg"]},
}


def fetch_anvisa() -> list[list[str]]:
    print("descargando ANVISA (Brasil)…", flush=True)
    req = urllib.request.Request(ANVISA_CSV, headers={"User-Agent": "labs-catalog/3.0"})
    with urllib.request.urlopen(req, timeout=180, context=_SSL) as r:
        data = r.read()
    print(f"  {len(data)//1024} KB descargados", flush=True)
    text = data.decode("latin-1")
    return list(csv.reader(io.StringIO(text), delimiter=";"))


CIMA_OVERLAY = os.path.join(HERE, "cima_overlay.json")


def _load_cima_overlay() -> dict:
    """Capa de dosis reales + marcas curadas de CIMA (archivo fijo, no el
    catálogo de salida: evita que un rebuild anterior se use a sí mismo)."""
    overlay = {}
    try:
        prev = json.load(open(CIMA_OVERLAY, encoding="utf-8"))
        for e in prev.get("drugs", []):
            overlay[_norm(base_inn(e["generic"]))] = e
    except (OSError, json.JSONDecodeError):
        pass
    return overlay


def main():
    # 1) overlay CIMA (dosis reales + marcas curadas)
    overlay = _load_cima_overlay()
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
                                "strengths": {}, "key": ALIASES.get(gk),
                                "_aliases": set()})
        e["_aliases"].add(gk)
        brand = clean_brand(row[iN] or "")
        bn = _norm(brand)
        if bn and bn != gk and len(bn) >= 2:
            e["brands"].add(brand.title() if brand.isupper() else brand)
        for label, uv in parse_strengths(row[iN] or "").items():
            e["strengths"][label] = uv

    def _find_target(aliases, fallback_key, generic, cat_):
        """Localiza la entrada donde fusionar: coincide por alias normalizado."""
        for a in aliases:
            a = _norm(a)
            if a and a in cat_:
                return a
        cand = fallback_key
        if cand not in cat_:
            cand = _norm(generic)
        return cand if cand in cat_ else fallback_key

    # 3) overlay CIMA: dosis reales + marcas curadas (fusiona por alias)
    for gk, o in overlay.items():
        target = _find_target(o.get("aliases", []), gk, o["generic"], cat)
        e = cat.setdefault(target, {"generic": o["generic"], "brands": set(),
                                    "strengths": {}, "key": o.get("key"),
                                    "_aliases": set()})
        e["_aliases"].update(_norm(a) for a in o.get("aliases", []))
        e["_aliases"].add(gk)
        e["key"] = e["key"] or o.get("key")
        for b in o.get("brands", []):
            e["brands"].add(b)
        # las dosis CIMA son de referencia limpia: se anteponen
        cima_str = o.get("strengths", [])
        if cima_str:
            e["_cima"] = cima_str

    # 4) extras verificados
    for gk, x in EXTRAS.items():
        target = _find_target([gk] + [a for a in x.get("aliases", [])], gk,
                              x["generic"], cat)
        e = cat.setdefault(target, {"generic": x["generic"], "brands": set(),
                                    "strengths": {}, "key": ALIASES.get(gk),
                                    "_aliases": set()})
        e["_aliases"].add(gk)
        for b in x["brands"]:
            e["brands"].add(b)
        e["_cima"] = x["strengths"]  # dosis verificadas de ficha

    # 4.5) marcas y dosis por país (vademecum.es/uy + mivademecum ar/cl/py/uy)
    for _f in sorted(os.listdir(HERE)):
        if not (_f.startswith("mv_") or _f == "uy_extra.json"):
            continue
        try:
            src = json.load(open(os.path.join(HERE, _f), encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        added = 0
        # índice genérico normalizado -> clave, para match O(1) (el loop por
        # cada medicamento x cada entrada es demasiado lento con +20k medicinas)
        gen_index = {}
        for cand, e in cat.items():
            ng = _norm(e.get("generic", ""))
            if len(ng) >= 3:
                gen_index.setdefault(ng, cand)
        _gen_list = sorted(gen_index.items(), key=lambda kv: -len(kv[0]))
        for m in src.get("medicines", []):
            name = (m.get("name") or "").strip()
            gk = _norm(name)
            if not gk or len(gk) < 3:
                continue
            strengths = m.get("dose") or []
            base = _norm(name)
            # buscar la entrada del catálogo cuyo genérico es prefijo del
            # nombre (p. ej. "metformina x mg" -> metformina); el más largo gana
            target = gen_index.get(base)
            if target is None:
                for ng, cand in _gen_list:
                    if ng in base:
                        target = cand
                        break
            # la marca es el nombre base sin dosis ni formas (p. ej. "Baclof 10
            # mg comprimido" -> "Baclof")
            brand = re.sub(r"\b\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?\s*"
                           r"(mg|mcg|g|ui|ml|%|microgramos|miligramos|gramos|"
                           r"unidades)\b", " ", name, flags=re.I)
            brand = re.sub(r"\b(comprimido|c[aá]psula|tableta|jarabe|soluci[oó]n|"
                           r"inyectable|crema|gel|suspensi[oó]n|recubierto|"
                           r"masticable|efervescente|gastrorresistente|nasal|"
                           r"oft[aá]lmico|d[ée]rmico|para|polvo|suspensi[oó]n|"
                           r"gastrorresistente|bucodispersable|recubierto)\b",
                           " ", brand, flags=re.I)
            brand = re.sub(r"\s+", " ", brand).strip(" -")
            # sin coincidencia de genérico: crear entrada propia (clave única)
            if target is None:
                target = gk
            e = cat.setdefault(
                target, {"generic": (brand.title() if brand else name.title()),
                         "brands": set(), "strengths": {},
                         "key": ALIASES.get(gk), "_aliases": set()})
            e["_aliases"].add(gk)
            if len(brand) >= 2:
                e["brands"].add(brand.title() if brand.islower() else brand)
            for s in strengths:
                e["strengths"][s] = ("mg", 0)  # orden aproximado
            added += 1
        print(f"  país {_f}: {added} medicamentos fusionados", flush=True)

    # 5) materializar
    def sort_strengths(d):
        return [k for k, _ in sorted(d.items(), key=lambda kv: (kv[1][0], kv[1][1]))]

    catalog = []
    for gk, e in cat.items():
        strengths = e.get("_cima") or sort_strengths(e["strengths"])
        aliases = sorted({a for a in (e.get("_aliases") or set()) | {gk}
                          if isinstance(a, str) and a})
        catalog.append({
            "key": e["key"],
            "generic": e["generic"],
            "aliases": aliases,
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
