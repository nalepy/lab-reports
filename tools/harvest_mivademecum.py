# -*- coding: utf-8 -*-
"""Crawlea mivademecum.com (AR, CL, PY, UY) -> tools/mv_<pais>.json.

Cada país es un subdominio con el mismo CMS WordPress:
    https://<pais>.mivademecum.com/medicamentos/page-N
Cada página lista 20 medicamentos con URL:
    /medicamento-<slug>-id-<id>
Se extrae id + nombre (slug) + dosis (parseada del slug si la lleva).

Uso:  python tools/harvest_mivademecum.py [ar cl py uy]   (default: todos)
"""
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) labs-catalog/3.0"}
MAX_PAGES = int(os.environ.get("MV_MAX_PAGES", "300"))
DELAY = float(os.environ.get("MV_DELAY", "0.2"))

STRENGTH_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)?)\s*"
    r"(mg\s*/\s*ml|mcg\s*/\s*ml|ui\s*/\s*ml|microgramos?|miligramos?|gramos?|"
    r"mcg|mg|µg|ug|g|unidades|ui|u\.i\.|%|ml)",
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


def fetch_page(country: str, page: int) -> tuple[list[dict], bool]:
    url = f"https://{country}.mivademecum.com/medicamentos/" + (
        f"page-{page}" if page > 1 else "")
    req = urllib.request.Request(url, headers=UA)
    html = ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                html = resp.read().decode("utf-8", "replace")
            break
        except Exception:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    if not html:
        return [], False
    out = []
    for m in re.finditer(r"medicamento-([a-z0-9\-]+)-id-(\d+)", html, re.I):
        slug, mid = m.group(1), m.group(2)
        name = slug.replace("-", " ").strip()
        out.append({"id": int(mid), "name": name, "dose": parse_dose(name)})
    # dedupe por id (páginas del CMS repiten entradas)
    seen = {}
    for x in out:
        seen.setdefault(x["id"], x)
    return list(seen.values()), bool(out)


def crawl(country: str) -> list[dict]:
    meds = {}
    page = 1
    while page <= MAX_PAGES:
        rows, more = fetch_page(country, page)
        if not more:
            break
        for m in rows:
            meds[m["id"]] = m
        if page % 50 == 0 or page == 1:
            print(f"  {country} page {page}: {len(meds)} medicamentos", flush=True)
        page += 1
        time.sleep(DELAY)
    print(f"  {country}: FIN (páginas {page-1}, {len(meds)} medicamentos)",
          flush=True)
    return sorted(meds.values(), key=lambda x: x["name"])


def main():
    countries = sys.argv[1:] or ["ar", "cl", "py", "uy"]
    for c in countries:
        print(f"== {c}.mivademecum.com ==", flush=True)
        meds = crawl(c)
        out = os.path.join(HERE, f"mv_{c}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"source": f"mivademecum.com/{c}", "count": len(meds),
                       "medicines": meds}, f, ensure_ascii=False,
                      separators=(",", ":"))
        nd = sum(1 for m in meds if m["dose"])
        print(f"  -> {out}: {len(meds)} ({nd} con dosis)", flush=True)


if __name__ == "__main__":
    main()
