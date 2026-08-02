# -*- coding: utf-8 -*-
"""Canonical test-name normalization.

Maps the raw Spanish test names found across the four lab formats into a
single canonical key so time-series charts and assessments can be built
across different labs and dates.
"""
import re
import unicodedata


def _norm(s: str) -> str:
    """Lowercase, strip accents, collapse whitespace/punct."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# canonical_key -> (display_name_es, category)
CATEGORIES = {
    "rbc": ("Globulos Rojos (RBC)", "hemogram"),
    "hemoglobin": ("Hemoglobina (Hb)", "hemogram"),
    "hematocrit": ("Hematocrito (Hto)", "hemogram"),
    "mcv": ("MCV", "hemogram"),
    "mch": ("MCH", "hemogram"),
    "mchc": ("MCHC", "hemogram"),
    "rdw": ("RDW", "hemogram"),
    "wbc": ("Globulos Blancos (WBC)", "hemogram"),
    "neut_pct": ("Neutrofilos %", "hemogram"),
    "lymph_pct": ("Linfocitos %", "hemogram"),
    "mono_pct": ("Monocitos %", "hemogram"),
    "eos_pct": ("Eosinofilos %", "hemogram"),
    "baso_pct": ("Basofilos %", "hemogram"),
    "neut_abs": ("Neutrofilos abs.", "hemogram"),
    "lymph_abs": ("Linfocitos abs.", "hemogram"),
    "mono_abs": ("Monocitos abs.", "hemogram"),
    "eos_abs": ("Eosinofilos abs.", "hemogram"),
    "baso_abs": ("Basofilos abs.", "hemogram"),
    "platelets": ("Plaquetas (PLT)", "hemogram"),
    "mpv": ("MPV", "hemogram"),
    "esr": ("Eritrosedimentacion (VSG)", "hemogram"),
    "glucose": ("Glucosa", "metabolic"),
    "hba1c": ("Hemoglobina Glicada (HbA1c)", "metabolic"),
    "hba1c_ifcc": ("HbA1c (IFCC)", "metabolic"),
    "urea": ("Urea", "renal"),
    "creatinine": ("Creatinina", "renal"),
    "uric_acid": ("Acido Urico", "renal"),
    "cholesterol": ("Colesterol Total", "lipids"),
    "hdl": ("Colesterol HDL", "lipids"),
    "ldl": ("Colesterol LDL", "lipids"),
    "vldl": ("Colesterol VLDL", "lipids"),
    "trig": ("Trigliceridos", "lipids"),
    "lipids_total": ("Lipidos Totales", "lipids"),
    "phospholipids": ("Fosfolipidos", "lipids"),
    "got": ("GOT (AST)", "hepatic"),
    "gpt": ("GPT (ALT)", "hepatic"),
    "alp": ("Fosfatasa Alcalina", "hepatic"),
    "bili_t": ("Bilirrubina Total", "hepatic"),
    "bili_d": ("Bilirrubina Directa", "hepatic"),
    "bili_i": ("Bilirrubina Indirecta", "hepatic"),
    "total_protein": ("Proteinas Totales", "hepatic"),
    "albumin": ("Albumina", "hepatic"),
    "calcium": ("Calcio", "metabolic"),
    "phosphorus": ("Fosforo", "metabolic"),
    "magnesium": ("Magnesio", "electrolytes"),
    "sodium": ("Sodio", "electrolytes"),
    "potassium": ("Potasio", "electrolytes"),
    "chloride": ("Cloro", "electrolytes"),
    "amylase": ("Amilasa", "pancreatic"),
    "ck": ("CK Total", "cardiac"),
    "ck_mb": ("CK-MB", "cardiac"),
    "ldh": ("LDH", "hepatic"),
    "crp": ("Proteina C Reactiva (PCR)", "inflammation"),
    "psa_total": ("PSA Total", "tumor"),
    "psa_free": ("PSA Libre", "tumor"),
    "troponin": ("Troponina I ultrasensible", "cardiac"),
    "tsh": ("TSH", "thyroid"),
    "t4_total": ("T4 Total", "thyroid"),
    "t4_free": ("T4 Libre", "thyroid"),
    "t3_total": ("T3 Total", "thyroid"),
    "t3_free": ("T3 Libre", "thyroid"),
    "blood_group": ("Grupo Sanguineo", "blood_type"),
    "rh": ("Factor RH", "blood_type"),
    "influenza_a": ("Influenza A - Antigeno", "respiratory"),
    "influenza_b": ("Influenza B - Antigeno", "respiratory"),
    "rsv": ("VRS - Antigeno", "respiratory"),
    "adenovirus": ("Adenovirus - Antigeno", "respiratory"),
    "mycoplasma": ("Mycoplasma pneumoniae - Ag", "respiratory"),
    "vit_d": ("Vitamina D (25-OH)", "metabolic"),
    "ferritin": ("Ferritina", "metabolic"),
    "uric": ("Uricemia", "renal"),
}

# synonym table: normalized raw name -> canonical key
_SYNONYMS = {
    "globulos rojos": "rbc", "globulos rojos rbc": "rbc", "eritrocitos": "rbc",
    "hemoglobina": "hemoglobin", "hemoglobina hb": "hemoglobin",
    "hematocrito": "hematocrit", "hematocrito hto": "hematocrit",
    "mcv": "mcv", "mcv volumen corpuscular medio": "mcv", "vcm": "mcv",
    "v c m": "mcv",
    "mch": "mch", "mch hemoglobina corpuscular media": "mch", "hcm": "mch",
    "h c m": "mch",
    "mchc": "mchc", "mchc conc de hg corpuscular media": "mchc",
    "chcm": "mchc", "c h c m": "mchc",
    "rdw": "rdw", "rdw cv": "rdw", "ade": "rdw",
    "globulos blancos": "wbc", "globulos blancos wbc": "wbc",
    "leucocitos": "wbc",
    "neutrofilos": "neut_pct", "neutrofilos segmentados": "neut_pct",
    "neutrofilos segmentados %": "neut_pct", "neutrofilos %": "neut_pct",
    "linfocitos": "lymph_pct",
    "monocitos": "mono_pct",
    "eosinofilos": "eos_pct",
    "basofilos": "baso_pct",
    "neutrofilos segmentados abs": "neut_abs", "neutrofilos absolutos": "neut_abs",
    "linfocitos abs": "lymph_abs", "linfocitos absolutos": "lymph_abs",
    "monocitos abs": "mono_abs",
    "eosinofilos abs": "eos_abs",
    "basofilos abs": "baso_abs",
    "plaquetas": "platelets", "plaquetas plt": "platelets",
    "mpv": "mpv",
    "eritrosedimentacion": "esr", "eritrosedimentacion vsg": "esr",
    "glucosa": "glucose", "glicemia": "glucose", "glicemia sangre": "glucose",
    "hemoglobina glicada": "hba1c", "hemoglobina glicosilada": "hba1c",
    "hemoglobina glicada hba1c": "hba1c", "hba1c": "hba1c",
    "hemoglobina glicada al c": "hba1c", "hemoglobina glicada a1c": "hba1c",
    "al c ngsp": "hba1c", "al c ifcc": "hba1c_ifcc",
    "urea": "urea", "urea en sangre": "urea", "uremia": "urea",
    "creatinina": "creatinine", "creatinina en suero": "creatinine",
    "creatinina sangre": "creatinine",
    "acido urico": "uric_acid", "acidourico": "uric_acid",
    "acido urico sangre": "uric_acid",
    "colesterol total": "cholesterol",
    "colesterol total sangre": "cholesterol",
    "colestrol total": "cholesterol",
    "colesterol hdl": "hdl", "colesterol hdl sangre": "hdl",
    "colestrol hdl": "hdl",
    "colesterol ldl": "ldl", "colesterol ldl sangre": "ldl",
    "colestrol ldl": "ldl",
    "colesterol vldl": "vldl", "colesterol vldl sangre": "vldl",
    "colestrol vldl": "vldl",
    "trigliceridos": "trig", "trigliceridos sangre": "trig",
    "trigliceridos sanguineos": "trig",
    "lipidos totales": "lipids_total",
    "fosfolipidos": "phospholipids",
    "got ast": "got", "got": "got", "asat got": "got", "got ast sangre": "got",
    "gpt alt": "gpt", "gpt": "gpt", "alat gpt": "gpt", "gpt alt sangre": "gpt",
    "fosfatasa alcalina": "alp",
    "bilirrubina total": "bili_t",
    "bilirrubina directa": "bili_d", "bilirrubina d": "bili_d",
    "bilirrubina indirecta": "bili_i", "bilirrubina i": "bili_i",
    "proteinas totales": "total_protein",
    "albumina": "albumin",
    "calcio": "calcium", "calcio total sangre": "calcium",
    "fosforo": "phosphorus", "fosforo sangre": "phosphorus",
    "magnesio": "magnesium",
    "sodio": "sodium",
    "potasio": "potassium",
    "cloro": "chloride", "cloruro": "chloride",
    "amilasa": "amylase",
    "ck total": "ck", "ck total sangre": "ck",
    "ck mb": "ck_mb", "ck mb sangre": "ck_mb",
    "ldh": "ldh", "ldh sangre": "ldh",
    "proteina c reactiva": "crp", "proteina c reactiva pcr": "crp", "pcr": "crp",
    "psa total": "psa_total",
    "psa libre": "psa_free",
    "troponina i ultrasensible cuantitativa sangre": "troponin",
    "troponina i": "troponin", "troponina i ultrasensible": "troponin",
    "tsh": "tsh", "tsh tirotropina": "tsh", "tsh 3ra generacion": "tsh",
    "t4 total": "t4_total", "t4 libre": "t4_free",
    "t3 total": "t3_total", "t3 libre": "t3_free",
    "grupo": "blood_group",
    "factor rh": "rh",
    "influenza a antigeno": "influenza_a", "ag influenza a": "influenza_a",
    "influenza b antigeno": "influenza_b", "ag influenza b": "influenza_b",
    "ag virus sincitial respiratorio": "rsv",
    "ag adenovirus": "adenovirus",
    "ag mycoplasma pneumoniae": "mycoplasma",
    "vitamina d": "vit_d", "vitamina d 25 oh": "vit_d", "25 oh vitamina d": "vit_d",
    "ferritina": "ferritin",
}

# aliases that need result-value disambiguation: same name, different absolute/%
_ABS_ALIASES = {
    "neutrofilos segmentados": "neut_abs",
    "neutrofilos": "neut_abs",
    "linfocitos": "lymph_abs",
    "monocitos": "mono_abs",
    "eosinofilos": "eos_abs",
    "basofilos": "baso_abs",
}

_QUALITATIVE_KEYS = {
    "influenza_a", "influenza_b", "rsv", "adenovirus", "mycoplasma",
}


def is_qualitative(key: str) -> bool:
    return key in _QUALITATIVE_KEYS


def canonicalize(raw_name: str, unit: str = "", section: str = "") -> str | None:
    """Return canonical key for a raw test name, or None."""
    n = _norm(raw_name)
    if not n:
        return None
    sec = _norm(section)
    # análisis de orina: no son equivalentes a los análisis de sangre
    if any(k in sec for k in ("orina", "sedimento", "urina", "chorro")):
        return None
    # differential-count disambiguation: "Fórmula Absoluta" -> absolute keys
    if "absoluta" in sec or (unit and any(u in unit.lower() for u in
                                          ("10e3", "10e6", "/ul", "/µl"))):
        for syn, key in _ABS_ALIASES.items():
            if n == syn or (len(syn) >= 4 and syn in n):
                return key
    # exact synonym
    if n in _SYNONYMS:
        return _SYNONYMS[n]
    # substring search (e.g. "Glucosa en Ayunas", "Urea en Sangre"):
    # probar los sinónimos más largos primero para evitar que
    # "hemoglobina" capture "hemoglobina glicada"
    for syn, key in sorted(_SYNONYMS.items(), key=lambda kv: -len(kv[0])):
        if len(syn) >= 4 and syn in n:
            return key
    return None
