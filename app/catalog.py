# -*- coding: utf-8 -*-
"""Catálogo normalizado de medicamentos para el autocompletado (live search).

Carga app/drug_catalog.json (generado por tools/build_drug_catalog.py desde
CIMA/AEMPS + mapa de marcas LATAM/BR/ES). Ofrece búsqueda por genérico, marca
comercial o alias, devolviendo las dosis reales disponibles.
"""
import json
import os

from .drugs import _norm

_HERE = os.path.dirname(os.path.abspath(__file__))
_CATALOG_PATH = os.path.join(_HERE, "drug_catalog.json")

_DRUGS: list[dict] = []
_LOADED = False


def _load() -> None:
    global _DRUGS, _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        with open(_CATALOG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("drugs", [])
    except (OSError, json.JSONDecodeError):
        _DRUGS = []
        return
    # precalcular índices normalizados para búsqueda rápida
    for e in raw:
        e["_gen"] = _norm(e.get("generic", ""))
        e["_aliases"] = [_norm(a) for a in e.get("aliases", [])]
        e["_brands"] = [_norm(b) for b in e.get("brands", [])]
    _DRUGS = raw


def search(q: str, limit: int = 12) -> list[dict]:
    """Devuelve fármacos cuyo genérico, marca o alias coincide con q.

    Prioriza coincidencias por prefijo. Cada resultado incluye las dosis
    reales (strengths) y, si coincidió por marca, cuál fue (matched_brand).
    """
    _load()
    qn = _norm(q)
    if not qn or len(qn) < 2:
        return []
    scored = []
    for e in _DRUGS:
        haystacks = [e["_gen"], *e["_aliases"], *e["_brands"]]
        starts = any(h.startswith(qn) for h in haystacks if h)
        contains = any(qn in h for h in haystacks if h)
        if not (starts or contains):
            continue
        matched_brand = None
        for i, bn in enumerate(e["_brands"]):
            if qn in bn:
                matched_brand = e["brands"][i]
                break
        # orden por calidad de coincidencia: nombre exacto > prefijo de genérico
        # > prefijo de alias > prefijo de marca > resto (contiene)
        if e["_gen"] == qn:
            tier = 0
        elif e["_gen"].startswith(qn):
            tier = 1
        elif any(a.startswith(qn) for a in e["_aliases"] if a):
            tier = 2
        elif any(b.startswith(qn) for b in e["_brands"] if b):
            tier = 3
        else:
            tier = 4
        has_dose = bool(e.get("strengths"))
        scored.append((0 if has_dose else 1, tier, e["_gen"], {
            "generic": e.get("generic", ""),
            "brands": e.get("brands", []),
            "strengths": e.get("strengths", []),
            "form": e.get("form", ""),
            "key": e.get("key"),
            "matched_brand": matched_brand,
        }))
    # con dosis primero (el usuario quiere la dosis), luego calidad de
    # coincidencia, luego alfabético
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return [item for _, _, _, item in scored[:limit]]
