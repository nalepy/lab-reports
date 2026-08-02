# -*- coding: utf-8 -*-
"""Harvesta el vademecum de Uruguay (vademecum.es/uruguay) -> tools/uy_extra.json.

Fuente: https://www.vademecum.es/uruguay/uy/alfa/<a-z|1-6>
Cada página lista medicamentos con la dosis en el propio slug:
    /uruguay/medicamento/<id>/baclof-25-mg-comprimido
=> nombre "baclof", dosis "25 mg".

La dosis se parsea del slug (nunca se inventa). El nombre base del slug puede
ser marca comercial (baclof, vytorin) o el genérico (valaciclovir, bortezomib);
el merge con el catálogo decide en build_drug_catalog.py.

Uso:  python tools/harvest_uy.py
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "uy_extra.json")
BASE = "https://www.vademecum.es/uruguay/uy/alfa/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) labs-catalog/3.0"}

LETTERS = [chr(c) for c in range(ord("a"), ord("z") + 1)] + [str(n) for n in range(1, 7)]

STRENGTH_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)?)\s*"
    r"(mg\s*/\s*ml|mcg\s*/\s*ml|ui\s*/\s*ml|microgramos?|miligramos?|gramos?|"
    r"mcg|mg|µg|ug|g|unidades|ui|u\.i\.|%|ml)",
    re.IGNORECASE)

FORM_WORDS = re.compile(
    r"\b(comprimidos?|comp|c[aá]psulas?|caps|tabletas?|grageas?|solucion|"
    r"soluci[oó]n|inyectable|iny|jarabe|suspension|suspensi[oó]n|crema|gel|"
    r"pomada|ung[uü]ento|gotas|ampollas?|vial|frasco|sobres?|recubiertos?|"
    r"pelicula|pel[ií]cula|liberacion|liberaci[oó]n|prolongada|oral|"
    r"subcutanea|subcut[aá]nea|precargada|pluma|dermica|d[ée]rmica|nasal|"
    r"oftalmica|oft[aá]lmica|masticable|bucodispersable|recubierto|"
    r"gastrorresistente|polvo|liofilizado|para|con|al|efervescente|"
    r"anhidra|clorhidrato|bromhidrato|sodica|s[oó]dica|potasica|pot[aá]sica|"
    r"calcica|c[aá]lcica|isobarica|isob[aá]rica|compuesta|compuesto|forte|"
    r"fuerte|retard|de)\b",
    re.IGNORECASE)


def _norm_unit(u: str) -> str:
    u = u.lower().replace(" ", "")
    if u.startswith("microgramo"):
        return "mcg"
    if u.startswith("miligramo"):
        return "mg"
    if u.startswith("gramo"):
        return "g"
    if u in ("µg", "ug"):
        return "mcg"
    if u in ("unidades", "ui", "u.i."):
        return "UI"
    return u


def parse_dose(name: str) -> list[str]:
    """Extrae dosis reales del nombre (slug): 'baclof-25-mg' -> ['25 mg']."""
    out = {}
    for m in STRENGTH_RE.finditer(name):
        raw = m.group(1).replace(" ", "")
        unit = _norm_unit(m.group(2))
        num = raw.split("/", 1)[0] if "/" in raw else raw
        try:
            val = float(num.replace(",", "."))
        except ValueError:
            continue
        if val <= 0 or val > 100000:
            continue
        if "/" in raw:
            label = f"{raw.replace('.', ',')} {unit}"
        elif val == int(val):
            label = f"{int(val)} {unit}"
        else:
            label = f"{val:g}".replace(".", ",") + f" {unit}"
        out[label] = (unit, val)
    return [k for k, _ in sorted(out.items(), key=lambda kv: (kv[1][0], kv[1][1]))]


def fetch_page(letter: str) -> list[dict]:
    url = BASE + letter
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as resp:
        html = resp.read().decode("utf-8", "replace")
    out = []
    for m in re.finditer(r'href="/uruguay/medicamento/(\d+)/([a-z0-9\-]+)"',
                         html, re.I):
        mid, slug = m.group(1), m.group(2)
        if mid in {x["id"] for x in out}:
            continue
        name = slug.replace("-", " ").strip()
        out.append({"id": int(mid), "name": name, "dose": parse_dose(name)})
    return out


def main():
    meds = {}
    for i, L in enumerate(LETTERS, 1):
        try:
            page = fetch_page(L)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {L}: {e}", file=sys.stderr)
            time.sleep(1)
            continue
        for m in page:
            meds[m["id"]] = m
        print(f"[{i}/{len(LETTERS)}] {L}: +{len(page)} (total {len(meds)})",
              flush=True)
        time.sleep(0.3)

    # marca base: quitar dosis y formas del nombre del slug
    for m in meds.values():
        base = STRENGTH_RE.sub(" ", m["name"])
        base = FORM_WORDS.sub(" ", base)
        base = re.sub(r"[^a-z0-9 ]", " ", base, flags=re.I)
        base = re.sub(r"\s+", " ", base).strip()
        m["brand"] = base.title() if base else m["name"]

    rows = sorted(meds.values(), key=lambda x: x["name"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"source": "vademecum.es/uruguay",
                   "count": len(rows), "medicines": rows},
                  f, ensure_ascii=False, separators=(",", ":"))
    n_dose = sum(1 for m in rows if m["dose"])
    print(f"\nOK: {len(rows)} medicamentos UY ({n_dose} con dosis) -> {OUT}")


if __name__ == "__main__":
    main()
