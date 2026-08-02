# -*- coding: utf-8 -*-
"""Motor de evaluación médica.

Genera, en español, un informe interpretativo por persona:
  - estado de cada biomarcador frente a su rango de referencia
  - tendencias entre fechas
  - hallazgos anormales con severidad y color (rojo/amarillo/verde)
  - evaluación global por sistemas (metabólico, cardiovascular, hepático...)
  - recomendaciones ordenadas por urgencia, con estadísticas reales
  - advertencias de interacción medicamento-laboratorio

ADVERTENCIA: esto es una herramienta de apoyo, NO reemplaza a un médico.
Todos los valores de referencia y estadísticas provienen de guías
estándar (ADA, AHA/ACC, AACE, KDIGO, NCEP/ATP III) y se citan como
tales. El paciente debe consultar siempre con su profesional de salud.
"""
import re
from datetime import datetime

from .canonical import CATEGORIES
from . import drugs as drugs_mod

SEV_COLORS = {"red": "rojo", "yellow": "amarillo", "green": "verde"}

# resultados con más de esta antigüedad se degradan a "repetir" (no alerta)
MAX_AGE_DAYS = 365

# rangos de referencia por defecto por biomarcador (adultos)
# (low, high) o (low, high, sexo) para sexo-específicos
DEFAULT_RANGES = {
    "rbc": [(4.50e6, 5.85e6, "M"), (4.08e6, 5.20e6, "F")],
    "hemoglobin": [(13.5, 17.5, "M"), (12.0, 16.0, "F")],
    "hematocrit": [(40.0, 52.0, "M"), (37.0, 47.0, "F")],
    "mcv": [(80, 100)],
    "mch": [(27, 33)],
    "mchc": [(32, 36)],
    "rdw": [(11.5, 14.5)],
    "wbc": [(4.0e3, 10.0e3)],
    "neut_pct": [(42, 65)],
    "lymph_pct": [(17, 45)],
    "mono_pct": [(2, 10)],
    "eos_pct": [(1, 4)],
    "baso_pct": [(0, 2)],
    "neut_abs": [(2.0e3, 7.0e3)],
    "lymph_abs": [(0.8e3, 4.0e3)],
    "mono_abs": [(0.2e3, 0.9e3)],
    "eos_abs": [(0.02e3, 0.5e3)],
    "baso_abs": [(0, 0.2e3)],
    "platelets": [(150e3, 450e3)],
    "mpv": [(6.2, 11.8)],
    "esr": [(0, 20)],
    "glucose": [(70, 100)],
    "hba1c": [(4.0, 5.6)],
    "urea": [(10, 50)],
    "creatinine": [(0.7, 1.2)],
    "uric_acid": [(3.5, 7.0)],
    "cholesterol": [(0, 200)],
    "hdl": [(40, 100, "M"), (50, 100, "F")],
    "ldl": [(0, 100)],
    "vldl": [(0, 40)],
    "trig": [(0, 150)],
    "lipids_total": [(400, 800)],
    "got": [(0, 37)],
    "gpt": [(0, 42)],
    "alp": [(0, 120)],
    "bili_t": [(0, 1.1)],
    "bili_d": [(0, 0.25)],
    "bili_i": [(0, 0.85)],
    "total_protein": [(6.1, 7.9)],
    "albumin": [(3.5, 4.8)],
    "calcium": [(8.5, 10.5)],
    "phosphorus": [(2.5, 4.5)],
    "magnesium": [(1.7, 2.4)],
    "sodium": [(135, 145)],
    "potassium": [(3.5, 5.1)],
    "chloride": [(98, 107)],
    "amylase": [(0, 120)],
    "ck": [(0, 170, "M"), (0, 145, "F")],
    "ck_mb": [(0, 5)],
    "ldh": [(0, 250)],
    "crp": [(0, 6)],
    "psa_total": [(0, 3.1)],
    "psa_free": [(0, 2.5)],
    "troponin": [(0, 0.03)],
    "tsh": [(0.27, 4.2)],
    "t4_total": [(5.1, 14.1)],
    "t4_free": [(0.8, 1.8)],
    "t3_total": [(80, 200)],
    "t3_free": [(2.0, 4.4)],
    "vit_d": [(30, 100)],
    "ferritin": [(20, 300)],
}

# texto descriptivo por biomarcador para el informe
TEST_INFO = {
    "glucose": {
        "label": "Glucosa en ayunas",
        "normal": "Glucosa en ayunas dentro del rango normal (70-100 mg/dL).",
        "high": "Glucosa elevada: sugiere intolerancia a la glucosa o diabetes.",
        "low": "Glucosa baja: puede indicar hipoglucemia.",
    },
    "hba1c": {
        "label": "Hemoglobina glicada (HbA1c)",
        "normal": "HbA1c < 5,7%: control glucémico normal.",
        "high": "HbA1c elevada: refleja glucosa alta promedio de los últimos 2-3 meses.",
        "low": "",
    },
    "cholesterol": {
        "label": "Colesterol total",
        "normal": "Colesterol total < 200 mg/dL: deseable.",
        "high": "Colesterol total alto (>200 mg/dL) es factor de riesgo cardiovascular.",
        "low": "",
    },
    "ldl": {
        "label": "Colesterol LDL ('malo')",
        "normal": "LDL < 100 mg/dL: óptimo.",
        "high": "LDL elevado: principal factor de riesgo de aterosclerosis.",
        "low": "",
    },
    "hdl": {
        "label": "Colesterol HDL ('bueno')",
        "normal": "HDL en rango protector.",
        "high": "HDL alto: protector.",
        "low": "HDL bajo: menor protección cardiovascular.",
    },
    "trig": {
        "label": "Triglicéridos",
        "normal": "Triglicéridos < 150 mg/dL: normal.",
        "high": "Triglicéridos elevados: riesgo cardiovascular y pancreatitis si >500.",
        "low": "",
    },
    "creatinine": {
        "label": "Creatinina",
        "normal": "Creatinina normal: función renal conservada.",
        "high": "Creatinina elevada: sugiere deterioro de la función renal.",
        "low": "",
    },
    "urea": {
        "label": "Urea",
        "normal": "Urea normal.",
        "high": "Urea elevada: puede indicar alteración renal o deshidratación.",
        "low": "",
    },
    "uric_acid": {
        "label": "Ácido úrico",
        "normal": "Ácido úrico normal.",
        "high": "Ácido úrico elevado: riesgo de gota y nefrolitiasis.",
        "low": "",
    },
    "got": {
        "label": "GOT (AST)",
        "normal": "GOT normal.",
        "high": "GOT elevada: sugiere daño hepático o muscular.",
        "low": "",
    },
    "gpt": {
        "label": "GPT (ALT)",
        "normal": "GPT normal.",
        "high": "GPT elevada: marcador de daño hepático.",
        "low": "",
    },
    "alp": {
        "label": "Fosfatasa alcalina",
        "normal": "Fosfatasa alcalina normal.",
        "high": "Fosfatasa alcalina elevada: sugiere colestasis o enfermedad ósea.",
        "low": "",
    },
    "bili_t": {
        "label": "Bilirrubina total",
        "normal": "Bilirrubina total normal.",
        "high": "Bilirrubina elevada: puede indicar enfermedad hepática o hemólisis.",
        "low": "",
    },
    "tsh": {
        "label": "TSH",
        "normal": "TSH normal: función tiroidea conservada.",
        "high": "TSH elevada: sugiere hipotiroidismo.",
        "low": "TSH baja: sugiere hipertiroidismo o sobretratamiento tiroideo.",
    },
    "t4_total": {
        "label": "T4 total",
        "normal": "T4 total normal.",
        "high": "T4 elevada: sugiere hipertiroidismo.",
        "low": "T4 baja: sugiere hipotiroidismo.",
    },
    "t3_total": {
        "label": "T3 total",
        "normal": "T3 total normal.",
        "high": "T3 elevada: sugiere hipertiroidismo.",
        "low": "T3 baja: puede verse en hipotiroidismo o enfermedad crónica.",
    },
    "wbc": {
        "label": "Glóbulos blancos (leucocitos)",
        "normal": "Leucocitos normales.",
        "high": "Leucocitosis: sugiere infección o inflamación.",
        "low": "Leucopenia: puede indicar supresión de médula ósea.",
    },
    "hemoglobin": {
        "label": "Hemoglobina",
        "normal": "Hemoglobina normal.",
        "high": "Hemoglobina elevada.",
        "low": "Anemia: hemoglobina baja.",
    },
    "hematocrit": {
        "label": "Hematocrito",
        "normal": "Hematocrito normal.",
        "high": "Hematocrito elevado.",
        "low": "Hematocrito bajo: sugiere anemia.",
    },
    "platelets": {
        "label": "Plaquetas",
        "normal": "Plaquetas normales.",
        "high": "Trombocitosis: plaquetas elevadas.",
        "low": "Trombocitopenia: plaquetas bajas, riesgo de sangrado.",
    },
    "crp": {
        "label": "Proteína C reactiva (PCR)",
        "normal": "PCR normal (<6 mg/L).",
        "high": "PCR elevada: marcador de inflamación o infección.",
        "low": "",
    },
    "psa_total": {
        "label": "PSA total",
        "normal": "PSA normal para la edad.",
        "high": "PSA elevado: requiere evaluación urológica.",
        "low": "",
    },
    "troponin": {
        "label": "Troponina I",
        "normal": "Troponina normal: sin daño miocárdico agudo.",
        "high": "Troponina elevada: daño miocárdico, requiere atención inmediata.",
        "low": "",
    },
    "vit_d": {
        "label": "Vitamina D (25-OH)",
        "normal": "Vitamina D normal (30-100 ng/mL).",
        "high": "",
        "low": "Vitamina D baja: riesgo óseo y cardiovascular.",
    },
    "ferritin": {
        "label": "Ferritina",
        "normal": "Ferritina normal.",
        "high": "Ferritina elevada: puede indicar sobrecarga de hierro o inflamación.",
        "low": "Ferritina baja: déficit de hierro.",
    },
    "potassium": {
        "label": "Potasio",
        "normal": "Potasio normal (3,5-5,1 mEq/L).",
        "high": "Hiperpotasemia: riesgo de arritmias cardiacas.",
        "low": "Hipocalemia: riesgo de debilidad y arritmias.",
    },
    "sodium": {
        "label": "Sodio",
        "normal": "Sodio normal.",
        "high": "Hipernatremia.",
        "low": "Hiponatremia: puede causar confusión y convulsiones.",
    },
    "calcium": {
        "label": "Calcio",
        "normal": "Calcio normal.",
        "high": "Hipercalcemia.",
        "low": "Hipocalcemia.",
    },
    "magnesium": {
        "label": "Magnesio",
        "normal": "Magnesio normal.",
        "high": "Hipermagnesemia.",
        "low": "Hipomagnesemia: riesgo de arritmias.",
    },
}

# estadísticas reales (cifras ampliamente publicadas por guías/estudios)
STATS = {
    "diabetes_risk": (
        "Estudios de referencia (Diabetes Prevention Program, NEJM 2002) muestran "
        "que con prediabetes (glucosa 100-125 o HbA1c 5,7-6,4%) el riesgo de "
        "progresar a diabetes en 10 años es del 35-50% si no se interviene. "
        "Perder solo 5-7% del peso corporal reduce ese riesgo en un 58%."),
    "ldl_high": (
        "El estudio de Framingham y guías ACC/AHA muestran que cada reducción de "
        "38 mg/dL de LDL se asocia con una caída del ~23% en eventos "
        "cardiovasculares mayores. Un LDL sostenido >160 mg/dL duplica el riesgo "
        "de infarto en 10 años."),
    "hdl_low": (
        "HDL bajo (<40 mg/dL en hombres, <50 en mujeres) es un factor de riesgo "
        "independiente: el estudio INTERHEART (Lancet 2004) encontró que la "
        "dislipidemia (colesterol alto / HDL bajo) explica ~49% del riesgo de "
        "infarto agudo de miocardio."),
    "trig_high": (
        "Triglicéridos >150 mg/dL se asocian con mayor riesgo cardiovascular "
        "(estudio de cohortes de Copenhague). Niveles >500 mg/dL elevan "
        "significativamente el riesgo de pancreatitis aguda."),
    "obesity_risk": (
        "Estudios de cohortes (Lancet 2009, meta-análisis de 57 estudios con "
        "900.000 personas) muestran que la obesidad (IMC ≥30) reduce la "
        "esperanza de vida en 2-4 años en promedio; la obesidad severa la reduce "
        "8-10 años. Con obesidad + diabetes tipo 2, la pérdida de solo 5-10% del "
        "peso mejora la glucemia, los lípidos y la presión."),
    "sedentary_risk": (
        "El sedentarismo es un factor de riesgo mayor: estudios muestran que la "
        "inactividad física aumenta la mortalidad cardiovascular en un 30-40% "
        "(OMS, 2020). Caminar 150 min/semana reduce el riesgo de muerte "
        "prematura ~30%."),
    "hypertension_risk": (
        "La hipertensión no tratada (PAS ≥140) duplica el riesgo de accidente "
        "cerebrovascular y aumenta ~50% el riesgo de infarto (Framingham). "
        "El riesgo de ACV se reduce ~40% con un control adecuado de la presión."),
    "smoking_risk": (
        "El tabaquismo causa ~1 de cada 5 muertes en adultos (CDC). Fumar "
        "acorta la vida en promedio 10 años; dejar de fumar antes de los 40 "
        "recupera casi toda esa expectativa."),
    "renal_risk": (
        "La creatinina elevada persistente indica deterioro renal: en estadios "
        "3-4 de ERC (eGFR <60), el riesgo de progresión a diálisis en 5 años "
        "es de ~20-30% sin control de la presión, la glucosa y la proteinuria."),
    "fatty_liver_risk": (
        "El hígado graso no alcohólico afecta ~25-30% de la población; en "
        "presencia de transaminasas elevadas y obesidad, la esteatohepatitis "
        "(NASH) progresa a cirrosis en ~20% de los casos a lo largo de 10-20 "
        "años si no se corrige la causa."),
    "anemia_risk": (
        "La anemia (hemoglobina <13 en hombres, <12 en mujeres) se asocia con "
        "fatiga, deterioro cognitivo y mayor riesgo cardiovascular; su causa "
        "más frecuente es la deficiencia de hierro, corregible."),
    "gout_risk": (
        "El ácido úrico >7 mg/dL aumenta el riesgo de gota: cada 1 mg/dL "
        "adicional eleva el riesgo de crisis de gota ~2,3 veces (estudios "
        "prospectivos). Niveles >9 mg/dL se asocian con daño renal."),
    "thyroid_risk": (
        "El hipotiroidismo no tratado (TSH elevada) aumenta el riesgo de "
        "colesterol alto, bradicardia y depresión; el hipertiroidismo (TSH "
        "baja) se asocia con arritmias, sobre todo fibrilación auricular, cuyo "
        "riesgo se triplica."),
    "psa_risk": (
        "El PSA elevado requiere descartar cáncer de próstata: con PSA "
        ">4 ng/mL la probabilidad de biopsia positiva es de ~25-30%. "
        "No todos los PSA elevados son cáncer (puede ser prostatitis o "
        "hiperplasia), pero no debe ignorarse."),
    "inflammation_risk": (
        "La PCR elevada de forma persistente se asocia con inflamación crónica; "
        "si es >10 mg/L suele indicar infección o inflamación activa que "
        "requiere evaluación."),
    "troponin_risk": (
        "La troponina elevada indica daño del músculo cardíaco: requiere "
        "evaluación en urgencias de inmediato, no esperar a una cita."),
    "hyperkalemia_risk": (
        "El potasio >5,5 mEq/L puede causar arritmias ventriculares y paro "
        "cardíaco; es una emergencia médica. >6,0 requiere tratamiento "
        "inmediato en urgencias."),
    "thrombocytopenia_risk": (
        "Plaquetas <100.000 aumentan el riesgo de sangrado; <50.000 el riesgo "
        "es significativo y requiere evaluación hematológica urgente."),
    "hyperuricemia_renal": (
        "El ácido úrico elevado sostenido se asocia con nefrolitiasis y "
        "nefropatía; su control reduce las crisis de gota en ~40-60%."),
}


def severity_of(value, low, high, key):
    """Determina severidad (rojo/amarillo) de un valor anormal.

    Rojo solo para desviaciones grandes o umbrales críticos; amarillo para
    cualquier otra alteración fuera de rango.
    """
    if value is None or (low is None and high is None):
        return None
    # umbrales críticos específicos (emergencia real)
    if key in CRITICAL:
        ch, cl = CRITICAL[key]
        if ch is not None and value > ch:
            return "red"
        if cl is not None and value < cl:
            return "red"
    if low is not None and high is not None:
        span = max(high - low, abs(low) * 0.3, abs(high) * 0.3, 1e-9)
        pct_above = (value - high) / span if value > high else 0
        pct_below = (low - value) / span if value < low else 0
        # >60% fuera del rango -> rojo
        if value > high and pct_above > 0.60:
            return "red"
        if value < low and pct_below > 0.60:
            return "red"
        return "yellow"
    # solo un límite
    if high is not None and value > high:
        return "red" if value > high * 1.5 else "yellow"
    if low is not None and value < low:
        return "red" if value < low * 0.5 else "yellow"
    return "yellow"


# umbrales de emergencia específicos (valor -> rojo directo)
CRITICAL = {
    "potassium": (5.5, 3.0),
    "sodium": (150, 125),
    "glucose": (250, 45),
    "calcium": (12, 7.0),
    "troponin": (0.05, None),
    "platelets": (None, 50e3),
    "wbc": (30e3, 1.0e3),
    "crp": (10, None),
    "creatinine": (3.0, None),
    "bili_t": (5, None),
    "psa_total": (10, None),
}


# límites plausibles por biomarcador (para descartar referencias corruptas)
PLAUSIBLE = {
    "glucose": (30, 600), "hba1c": (2, 20), "urea": (3, 300),
    "creatinine": (0.1, 20), "uric_acid": (1, 15),
    "cholesterol": (50, 600), "hdl": (10, 150), "ldl": (10, 400),
    "vldl": (1, 200), "trig": (10, 2000), "lipids_total": (100, 2000),
    "got": (2, 1000), "gpt": (2, 1000), "alp": (10, 1000),
    "bili_t": (0.01, 30), "bili_d": (0.0, 10), "bili_i": (0.0, 20),
    "total_protein": (3, 12), "albumin": (1, 8),
    "calcium": (4, 16), "phosphorus": (0.5, 10), "magnesium": (0.5, 6),
    "sodium": (100, 180), "potassium": (1, 9), "chloride": (70, 140),
    "amylase": (10, 2000), "ck": (10, 50000), "ck_mb": (0, 100),
    "ldh": (50, 3000), "crp": (0, 300), "psa_total": (0, 100),
    "psa_free": (0, 20), "troponin": (0, 10), "tsh": (0.001, 100),
    "t4_total": (0.5, 30), "t3_total": (10, 500), "vit_d": (1, 200),
    "ferritin": (1, 5000), "esr": (0, 200),
    "rbc": (1e6, 9e6), "hemoglobin": (3, 25), "hematocrit": (10, 70),
    "mcv": (50, 140), "mch": (15, 45), "mchc": (20, 45), "rdw": (5, 30),
    "wbc": (0.2e3, 50e3), "neut_pct": (5, 95), "lymph_pct": (5, 95),
    "mono_pct": (0, 30), "eos_pct": (0, 30), "baso_pct": (0, 10),
    "neut_abs": (0.1e3, 20e3), "lymph_abs": (0.1e3, 15e3),
    "mono_abs": (0.01e3, 5e3), "eos_abs": (0.0e3, 3e3),
    "baso_abs": (0.0e3, 1e3), "platelets": (10e3, 1000e3),
    "mpv": (3, 20),
}


def _ref_is_plausible(key, low, high):
    if low is None and high is None:
        return True
    bounds = PLAUSIBLE.get(key)
    if not bounds:
        return True
    lo_b, hi_b = bounds
    if low is not None and not (lo_b <= low <= hi_b):
        return False
    if high is not None and not (lo_b <= high <= hi_b):
        return False
    return True


def _range_for(key, sex):
    ranges = DEFAULT_RANGES.get(key)
    if not ranges:
        return (None, None)
    for r in ranges:
        if len(r) == 3 and r[2] == sex:
            return (r[0], r[1])
    for r in ranges:
        if len(r) == 2:
            return (r[0], r[1])
    return (ranges[0][0], ranges[0][1])


def _norm_qual(q):
    if not q:
        return ""
    s = q.strip().lower()
    s = re.sub(r"[^a-záéíóúñ]+", " ", s)
    return s.strip()


# unidades estándar por biomarcador (las ampliamente usadas; cada laboratorio
# puede informar las suyas, esto es solo para mostrar el rango de referencia
# deseado junto al nombre del análisis).
STD_UNITS = {
    "rbc": "/µL", "hemoglobin": "g/dL", "hematocrit": "%",
    "mcv": "fL", "mch": "pg", "mchc": "g/dL", "rdw": "%",
    "wbc": "/µL", "neut_pct": "%", "lymph_pct": "%", "mono_pct": "%",
    "eos_pct": "%", "baso_pct": "%",
    "neut_abs": "/µL", "lymph_abs": "/µL", "mono_abs": "/µL",
    "eos_abs": "/µL", "baso_abs": "/µL",
    "platelets": "/µL", "mpv": "fL", "esr": "mm/h",
    "glucose": "mg/dL", "hba1c": "%", "urea": "mg/dL",
    "creatinine": "mg/dL", "uric_acid": "mg/dL",
    "cholesterol": "mg/dL", "hdl": "mg/dL", "ldl": "mg/dL",
    "vldl": "mg/dL", "trig": "mg/dL", "lipids_total": "mg/dL",
    "phospholipids": "mg/dL",
    "got": "U/L", "gpt": "U/L", "alp": "U/L", "ldh": "U/L",
    "bili_t": "mg/dL", "bili_d": "mg/dL", "bili_i": "mg/dL",
    "total_protein": "g/dL", "albumin": "g/dL",
    "calcium": "mg/dL", "phosphorus": "mg/dL", "magnesium": "mg/dL",
    "sodium": "mEq/L", "potassium": "mEq/L", "chloride": "mEq/L",
    "amylase": "U/L", "ck": "U/L", "ck_mb": "ng/mL", "crp": "mg/L",
    "psa_total": "ng/mL", "psa_free": "ng/mL", "troponin": "ng/mL",
    "tsh": "µUI/mL", "t4_total": "µg/dL", "t4_free": "ng/dL",
    "t3_total": "ng/dL", "t3_free": "pg/mL",
    "vit_d": "ng/mL", "ferritin": "ng/mL",
}

# frases curadas para los análisis más comunes (formato humano)
STD_PHRASES = {
    "cholesterol": "deseable inferior a 200 mg/dL",
    "ldl": "deseable inferior a 100 mg/dL",
    "hdl": "superior a 40 mg/dL (hombre) / 50 mg/dL (mujer)",
    "trig": "inferior a 150 mg/dL",
    "hba1c": "4,0 – 5,6 %",
    "glucose": "70 – 100 mg/dL en ayunas",
    "rbc": "4.500.000 – 5.850.000 /µL (hombre) · 4.080.000 – 5.200.000 /µL (mujer)",
    "hemoglobin": "13,5 – 17,5 g/dL (hombre) · 12,0 – 16,0 g/dL (mujer)",
    "hematocrit": "40 – 52 % (hombre) · 37 – 47 % (mujer)",
    "wbc": "4.000 – 10.000 /µL",
    "platelets": "150.000 – 450.000 /µL",
}


def _fmt_std_num(v) -> str:
    if v is None:
        return ""
    if v >= 1000:
        if float(v).is_integer():
            return f"{int(v):,}".replace(",", ".")
        return f"{v:,.1f}".replace(",", ".").replace(".", ",", 1)
    if float(v).is_integer():
        return str(int(v))
    return str(v).replace(".", ",")


def std_range_text(key: str, sex: str = "") -> str:
    """Texto del rango de referencia estándar (ampliamente adoptado)."""
    if key in STD_PHRASES:
        return STD_PHRASES[key]
    ranges = DEFAULT_RANGES.get(key)
    if not ranges:
        return ""
    unit = STD_UNITS.get(key, "")
    parts = []
    for r in ranges:
        lo, hi = r[0], r[1]
        g = r[2] if len(r) > 2 else None
        s = f"inferior a {_fmt_std_num(hi)}" if lo == 0 \
            else f"entre {_fmt_std_num(lo)} y {_fmt_std_num(hi)}"
        if unit:
            s += f" {unit}"
        if g == "M":
            s += " (hombre)"
        elif g == "F":
            s += " (mujer)"
        parts.append(s)
    return " · ".join(parts)


def build_assessment(person: dict, tests: list[dict], meds: list[dict]) -> dict:
    """Construye la evaluación completa de una persona."""
    sex = person.get("sex") or ""
    # ---- agrupar por biomarcador canónico, series por fecha
    series: dict[str, list[dict]] = {}
    raw_tests = []
    for t in tests:
        raw_tests.append(t)
        if not t["canonical"]:
            continue
        series.setdefault(t["canonical"], []).append(t)
    # ordenar cada serie por fecha
    for k in series:
        series[k].sort(key=lambda x: x["date"] or "")

    # ---- estado de cada biomarcador (última medición)
    markers = []
    for key in sorted(series):
        pts = series[key]
        last = pts[-1]
        value = last["value"]
        low, high = last["ref_low"], last["ref_high"]
        if low is None and high is None:
            low, high = _range_for(key, sex)
        elif not _ref_is_plausible(key, low, high):
            low, high = _range_for(key, sex)
        direction = ""
        status = "normal"
        severity = None
        qual = last.get("qual")
        if qual is not None:
            # análisis cualitativo (Negativo / Positivo)
            if _norm_qual(qual) in ("negativo", "no detectable", "trazas",
                                    "neg", "ausente"):
                status = "normal"
            elif _norm_qual(qual) in ("positivo", "pos", "detectable"):
                status = "positivo"
                severity = "red" if key in (
                    "influenza_a", "influenza_b", "rsv", "adenovirus",
                    "mycoplasma") else "yellow"
            else:
                status = "no_realizado"
        elif value is None:
            status = "no_realizado"  # el laboratorio no imprimió el valor
        elif low is not None or high is not None:
            if low is not None and value < low:
                status = "bajo"
                direction = "down"
            elif high is not None and value > high:
                status = "alto"
                direction = "up"
            severity = severity_of(value, low, high, key)
        # tendencia: comparar últimas 2 mediciones
        trend = ""
        if len(pts) >= 2 and pts[-2]["value"] is not None and value is not None:
            delta = value - pts[-2]["value"]
            if abs(delta) < 1e-9:
                trend = "estable"
            elif delta > 0:
                trend = "subiendo"
            else:
                trend = "bajando"
        # info textual
        info = TEST_INFO.get(key, {})
        label = info.get("label", last["name"])
        if status == "normal":
            txt = info.get("normal", "") or f"{label} dentro del rango normal."
        elif status == "positivo":
            txt = f"{label}: POSITIVO (resultado cualitativo)."
        elif status == "no_realizado":
            txt = f"{label}: análisis no realizado / sin valor informado."
        elif status == "alto":
            txt = info.get("high", "") or f"{label} por encima del rango."
        else:
            txt = info.get("low", "") or f"{label} por debajo del rango."
        markers.append({
            "key": key,
            "label": label,
            "status": status,
            "severity": severity,
            "value": value,
            "unit": last.get("unit", ""),
            "ref_low": low,
            "ref_high": high,
            "ref_text": last.get("ref_text", ""),
            "std_range": std_range_text(key, sex),
            "trend": trend,
            "n_measurements": len(pts),
            "last_date": last.get("date"),
            "text": txt,
            "latest": last,
        })

    # ---- hallazgos clínicos (markers anormales con contexto)
    findings = []
    last_report_date = None
    if person.get("last_report"):
        try:
            last_report_date = datetime.fromisoformat(person["last_report"])
        except ValueError:
            last_report_date = None
    for m in markers:
        if m["status"] == "normal" or m["status"] == "no_realizado":
            continue
        # PSA libre no se interpreta aislado: solo junto al PSA total
        if m["key"] == "psa_free":
            continue
        sev = m["severity"] or "yellow"
        # regla de antigüedad: si el resultado es "malo" pero tiene más de 12
        # meses, no alertar como crítico — recomendar repetir el análisis
        aged = False
        if m.get("last_date") and last_report_date:
            try:
                d = datetime.fromisoformat(m["last_date"])
                age_days = (last_report_date - d).days
                if age_days > MAX_AGE_DAYS:
                    aged = True
            except ValueError:
                pass
        if aged and sev == "red":
            sev = "yellow"
        stat = ""
        if m["key"] in ("glucose", "hba1c"):
            stat = STATS["diabetes_risk"]
        elif m["key"] == "ldl":
            stat = STATS["ldl_high"]
        elif m["key"] == "hdl":
            stat = STATS["hdl_low"]
        elif m["key"] == "trig":
            stat = STATS["trig_high"]
        elif m["key"] in ("creatinine", "urea"):
            stat = STATS["renal_risk"]
        elif m["key"] in ("got", "gpt", "alp"):
            stat = STATS["fatty_liver_risk"]
        elif m["key"] == "hemoglobin":
            stat = STATS["anemia_risk"]
        elif m["key"] == "uric_acid":
            stat = STATS["gout_risk"]
        elif m["key"] in ("tsh", "t4_total", "t3_total"):
            stat = STATS["thyroid_risk"]
        elif m["key"] == "psa_total":
            stat = STATS["psa_risk"]
        elif m["key"] == "crp":
            stat = STATS["inflammation_risk"]
        elif m["key"] == "troponin":
            stat = STATS["troponin_risk"]
        elif m["key"] == "potassium":
            stat = STATS["hyperkalemia_risk"]
        elif m["key"] == "platelets":
            stat = STATS["thrombocytopenia_risk"]
        findings.append({
            "severity": sev,
            "marker": m,
            "stat": stat,
            "date": m.get("last_date"),
            "aged": aged,
        })
    # ordenar: rojo primero, luego amarillo
    order = {"red": 0, "yellow": 1, "green": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 9))

    # ---- evaluación por sistemas
    systems = _system_review(markers, sex)

    # ---- recomendaciones priorizadas
    recommendations = _recommendations(markers, findings, person, sex)

    # ---- interacciones medicamentosas
    drug_checks = _drug_checks(markers, meds)

    # ---- resumen ejecutivo
    summary = _summary(markers, findings, systems)

    return {
        "person": person,
        "markers": markers,
        "findings": findings,
        "systems": systems,
        "recommendations": recommendations,
        "drug_checks": drug_checks,
        "summary": summary,
        "series": series,
        "n_tests": len(raw_tests),
        "generated_at": datetime.now().isoformat(),
    }


def _system_review(markers, sex) -> list[dict]:
    """Revisión por sistemas: metabólico, cardiovascular, renal, hepático,
    tiroideo, hematológico, inflamación."""
    out = []
    by_key = {m["key"]: m for m in markers}

    # metabólico
    g = by_key.get("glucose")
    h = by_key.get("hba1c")
    metab_issues = []
    for m in (g, h):
        if m and m["status"] != "normal":
            metab_issues.append(m)
    metab = {
        "system": "Metabólico (glucosa)",
        "status": "normal",
        "severity": None,
        "text": "Control glucémico dentro de límites normales.",
        "details": [],
    }
    if metab_issues:
        worst = max(metab_issues, key=lambda m: order_of(m["severity"]))
        metab["status"] = "alterado"
        metab["severity"] = worst["severity"]
        detail = (f"{worst['label']}: {worst['value']} {worst['unit']} "
                  f"({worst['status']}).")
        metab["text"] = detail
        metab["details"] = [f"{m['label']} {m['status']}" for m in metab_issues]
    out.append(metab)

    # cardiovascular (lípidos)
    lip_keys = ["cholesterol", "ldl", "hdl", "trig"]
    lip_issues = [by_key[k] for k in lip_keys if k in by_key
                  and by_key[k]["status"] != "normal"]
    lip = {
        "system": "Cardiovascular (lípidos)",
        "status": "normal",
        "severity": None,
        "text": "Perfil lipídico sin alteraciones relevantes.",
        "details": [],
    }
    if lip_issues:
        worst = max(lip_issues, key=lambda m: order_of(m["severity"]))
        lip["status"] = "alterado"
        lip["severity"] = worst["severity"]
        parts = [f"{m['label']}: {m['value']} {m['unit']} ({m['status']})"
                 for m in lip_issues]
        lip["text"] = "Perfil lipídico con alteraciones: " + "; ".join(parts[:3])
        lip["details"] = parts
    out.append(lip)

    # renal
    renal_issues = [by_key[k] for k in ("creatinine", "urea", "uric_acid")
                    if k in by_key and by_key[k]["status"] != "normal"]
    renal = {
        "system": "Renal",
        "status": "normal",
        "severity": None,
        "text": "Función renal dentro de parámetros normales.",
        "details": [],
    }
    if renal_issues:
        worst = max(renal_issues, key=lambda m: order_of(m["severity"]))
        renal["status"] = "alterado"
        renal["severity"] = worst["severity"]
        parts = [f"{m['label']}: {m['value']} {m['unit']} ({m['status']})"
                 for m in renal_issues]
        renal["text"] = "Función renal con alteraciones: " + "; ".join(parts[:3])
        renal["details"] = parts
    out.append(renal)

    # hepático
    hep_issues = [by_key[k] for k in ("got", "gpt", "alp", "bili_t")
                  if k in by_key and by_key[k]["status"] != "normal"]
    hep = {
        "system": "Hepático",
        "status": "normal",
        "severity": None,
        "text": "Perfil hepático sin alteraciones.",
        "details": [],
    }
    if hep_issues:
        worst = max(hep_issues, key=lambda m: order_of(m["severity"]))
        hep["status"] = "alterado"
        hep["severity"] = worst["severity"]
        parts = [f"{m['label']}: {m['value']} {m['unit']} ({m['status']})"
                 for m in hep_issues]
        hep["text"] = "Perfil hepático con alteraciones: " + "; ".join(parts[:3])
        hep["details"] = parts
    out.append(hep)

    # tiroideo
    thy_issues = [by_key[k] for k in ("tsh", "t4_total", "t3_total")
                  if k in by_key and by_key[k]["status"] != "normal"]
    thy = {
        "system": "Tiroideo",
        "status": "normal",
        "severity": None,
        "text": "Función tiroidea normal.",
        "details": [],
    }
    if thy_issues:
        worst = max(thy_issues, key=lambda m: order_of(m["severity"]))
        thy["status"] = "alterado"
        thy["severity"] = worst["severity"]
        parts = [f"{m['label']}: {m['value']} {m['unit']} ({m['status']})"
                 for m in thy_issues]
        thy["text"] = "Función tiroidea alterada: " + "; ".join(parts[:3])
        thy["details"] = parts
    out.append(thy)

    # hematológico
    hema_keys = ["hemoglobin", "hematocrit", "wbc", "platelets"]
    hema_issues = [by_key[k] for k in hema_keys if k in by_key
                   and by_key[k]["status"] != "normal"]
    hema = {
        "system": "Hematológico",
        "status": "normal",
        "severity": None,
        "text": "Hemograma sin alteraciones relevantes.",
        "details": [],
    }
    if hema_issues:
        worst = max(hema_issues, key=lambda m: order_of(m["severity"]))
        hema["status"] = "alterado"
        hema["severity"] = worst["severity"]
        parts = [f"{m['label']}: {m['value']} {m['unit']} ({m['status']})"
                 for m in hema_issues]
        hema["text"] = "Hemograma con alteraciones: " + "; ".join(parts[:3])
        hema["details"] = parts
    out.append(hema)

    # inflamación
    inf_issues = [by_key[k] for k in ("crp", "esr") if k in by_key
                  and by_key[k]["status"] != "normal"]
    inf = {
        "system": "Inflamación",
        "status": "normal",
        "severity": None,
        "text": "Marcadores de inflamación normales.",
        "details": [],
    }
    if inf_issues:
        worst = max(inf_issues, key=lambda m: order_of(m["severity"]))
        inf["status"] = "alterado"
        inf["severity"] = worst["severity"]
        parts = [f"{m['label']}: {m['value']} {m['unit']} ({m['status']})"
                 for m in inf_issues]
        inf["text"] = "Inflamación presente: " + "; ".join(parts[:3])
        inf["details"] = parts
    out.append(inf)

    # electrolitos
    elec_keys = ["potassium", "sodium", "calcium", "magnesium"]
    elec_issues = [by_key[k] for k in elec_keys if k in by_key
                   and by_key[k]["status"] != "normal"]
    elec = {
        "system": "Electrolitos",
        "status": "normal",
        "severity": None,
        "text": "Electrolitos normales.",
        "details": [],
    }
    if elec_issues:
        worst = max(elec_issues, key=lambda m: order_of(m["severity"]))
        elec["status"] = "alterado"
        elec["severity"] = worst["severity"]
        parts = [f"{m['label']}: {m['value']} {m['unit']} ({m['status']})"
                 for m in elec_issues]
        elec["text"] = "Electrolitos alterados: " + "; ".join(parts[:3])
        elec["details"] = parts
    out.append(elec)

    # ordenar sistemas por severidad
    out.sort(key=lambda s: order_of(s["severity"]))
    return out


def order_of(sev):
    return {"red": 0, "yellow": 1, "green": 2, None: 3}.get(sev, 3)


def _recommendations(markers, findings, person, sex) -> list[dict]:
    """Recomendaciones ordenadas por urgencia (rojo > amarillo > verde)."""
    recs = []
    by_key = {m["key"]: m for m in markers}

    def add(severity, title, body, action="", urgency_text=""):
        recs.append({
            "severity": severity,
            "title": title,
            "body": body,
            "action": action,
            "urgency_text": urgency_text,
        })

    # --- críticos (rojo)
    for f in findings:
        if f["severity"] != "red":
            continue
        m = f["marker"]
        k = m["key"]
        if k == "troponin":
            add("red",
                f"TROPONINA ELEVADA ({m['value']} {m['unit']}) — POSIBLE DAÑO CARDÍACO",
                "La troponina elevada indica daño del músculo del corazón. Esto puede ser un infarto en curso.",
                "ACUDIR A URGENCIAS DE INMEDIATO. No conducir. Llamar al 911/emergencias locales.",
                "URGENTE — atención médica inmediata")
        elif k == "potassium" and m["value"] and m["value"] > 5.5:
            add("red",
                f"POTASIO CRÍTICO ({m['value']} {m['unit']}) — RIESGO DE ARRITMIA",
                "El potasio muy alto puede causar arritmias ventriculares y paro cardíaco.",
                "ACUDIR A URGENCIAS HOY MISMO. Revisar medicamentos que suban el potasio.",
                "URGENTE — atención médica inmediata")
        elif k == "platelets" and m["value"] and m["value"] < 50e3:
            add("red",
                f"PLAQUETAS CRÍTICAMENTE BAJAS ({m['value']}) — RIESGO DE SANGRADO",
                "Plaquetas muy bajas aumentan el riesgo de hemorragia espontánea.",
                "EVALUACIÓN HEMATOLÓGICA URGENTE. Evitar aspirina y anticoagulantes.",
                "URGENTE — atención médica inmediata")
        else:
            add("red",
                f"{m['label'].upper()} ALTERADO — VALOR CRÍTICO",
                m["text"],
                "Consultar a un médico con esta evaluación en los próximos días.",
                "ALTA PRIORIDAD")

    # --- amarillos (precaución)
    for f in findings:
        if f["severity"] != "yellow":
            continue
        m = f["marker"]
        k = m["key"]
        # resultado alterado pero con más de 12 meses: recomendar repetir
        if f.get("aged"):
            add("yellow",
                f"{m['label'].upper()} ALTERADO EN ANÁLISIS ANTERIOR "
                f"({m['value']} {m['unit']}, {m.get('last_date', '')[:10]})",
                m["text"] + " Este resultado tiene más de 12 meses: el estado "
                "actual puede ser diferente (mejor o peor).",
                "Repetir este análisis en un control nuevo para confirmar el "
                "estado actual.",
                "SEGUIMIENTO — repetir análisis")
            continue
        if k in ("glucose", "hba1c"):
            if k == "glucose" and m["value"] and 100 <= m["value"] < 126:
                add("yellow",
                    "GLUCOSA EN AYUNAS ELEVADA — PREDIABETES",
                    "Su glucosa de ayunas indica prediabetes (100-125 mg/dL). "
                    "Sin cambios, progresa a diabetes tipo 2.",
                    "Repetir glucosa y HbA1c. Evaluar con médico. Iniciar cambios de hábitos.",
                    "PRIORIDAD MEDIA — programar consulta en 1-3 meses")
                add("yellow", "PERDER 5-7% DEL PESO CORPORAL",
                    STATS["diabetes_risk"], "",
                    "PRIORIDAD MEDIA")
            elif k == "hba1c" and m["value"] and 5.7 <= m["value"] < 6.5:
                add("yellow",
                    "HbA1c EN RANGO DE PREDIABETES (5,7-6,4%)",
                    "Su hemoglobina glicada indica prediabetes.",
                    "Confirmar con nuevo análisis y consulta médica. Cambios de hábitos.",
                    "PRIORIDAD MEDIA — programar consulta en 1-3 meses")
                add("yellow", "PERDER 5-7% DEL PESO CORPORAL",
                    STATS["diabetes_risk"], "", "PRIORIDAD MEDIA")
            else:
                add("yellow",
                    f"{m['label']} {m['status'].upper()} ({m['value']} {m['unit']})",
                    m["text"], "Consultar con médico.", "PRIORIDAD MEDIA")
            if f["stat"]:
                add("yellow", "ESTADÍSTICA PARA TENER EN CUENTA", f["stat"],
                    "", "CONTEXTO")
        elif k == "ldl":
            add("yellow",
                f"LDL ELEVADO ({m['value']} {m['unit']})",
                "El colesterol LDL alto acumula placa en las arterias.",
                "Evaluar riesgo cardiovascular con médico. Considerar dieta mediterránea, ejercicio.",
                "PRIORIDAD MEDIA — consultar en 1-3 meses")
            add("yellow", "ESTADÍSTICA PARA TENER EN CUENTA", STATS["ldl_high"],
                "", "CONTEXTO")
        elif k == "trig":
            add("yellow",
                f"TRIGLICÉRIDOS ELEVADOS ({m['value']} {m['unit']})",
                m["text"],
                "Reducir azúcares y alcohol. Si >500 mg/dL: riesgo de pancreatitis, consultar pronto.",
                "PRIORIDAD MEDIA")
            add("yellow", "ESTADÍSTICA PARA TENER EN CUENTA", STATS["trig_high"],
                "", "CONTEXTO")
        elif k == "hdl":
            add("yellow",
                f"HDL BAJO ({m['value']} {m['unit']})",
                m["text"],
                "Ejercicio aeróbico regular y dejar de fumar suben el HDL.",
                "PRIORIDAD MEDIA")
            add("yellow", "ESTADÍSTICA PARA TENER EN CUENTA", STATS["hdl_low"],
                "", "CONTEXTO")
        elif k in ("creatinine", "urea"):
            add("yellow",
                f"FUNCIÓN RENAL ALTERADA ({m['label']}: {m['value']} {m['unit']})",
                m["text"] + " " + STATS["renal_risk"],
                "Medir creatinina + eGFR. Controlar presión y glucosa. Revisar AINEs y medicamentos.",
                "PRIORIDAD MEDIA — consultar en 1-2 meses")
        elif k in ("got", "gpt", "alp"):
            add("yellow",
                f"ENZIMAS HEPÁTICAS ELEVADAS ({m['label']}: {m['value']} {m['unit']})",
                m["text"] + " " + STATS["fatty_liver_risk"],
                "Evitar alcohol. Evaluar hígado graso (ecografía). Revisar medicamentos.",
                "PRIORIDAD MEDIA — consultar en 1-2 meses")
        elif k in ("tsh", "t4_total", "t3_total"):
            add("yellow",
                f"TIROIDES ALTERADO ({m['label']}: {m['value']} {m['unit']})",
                m["text"] + " " + STATS["thyroid_risk"],
                "Perfil tiroideo completo + consulta endocrinológica.",
                "PRIORIDAD MEDIA")
        elif k == "uric_acid":
            add("yellow",
                f"ÁCIDO ÚRICO ELEVADO ({m['value']} {m['unit']})",
                m["text"] + " " + STATS["gout_risk"],
                "Hidratación, reducir carnes rojas y alcohol. Control con médico.",
                "PRIORIDAD MEDIA")
        elif k == "crp":
            add("yellow",
                f"PCR ELEVADA ({m['value']} {m['unit']})",
                m["text"],
                "Buscar causa de inflamación con médico (infección, artritis, etc.).",
                "PRIORIDAD MEDIA")
        elif k == "psa_total":
            add("yellow",
                f"PSA ELEVADO ({m['value']} {m['unit']})",
                m["text"] + " " + STATS["psa_risk"],
                "Consulta con urólogo. Evaluar repetición de PSA y tacto rectal.",
                "PRIORIDAD MEDIA — consultar en 1-2 meses")
        elif k == "hemoglobin":
            add("yellow",
                f"ANEMIA ({m['label']}: {m['value']} {m['unit']})",
                m["text"] + " " + STATS["anemia_risk"],
                "Estudiar causa: ferritina, hierro, B12, ácido fólico.",
                "PRIORIDAD MEDIA")
        elif k == "potassium":
            add("yellow",
                f"POTASIO ALTERADO ({m['value']} {m['unit']})",
                m["text"],
                "Revisar dieta y medicamentos. Repetir análisis.",
                "PRIORIDAD MEDIA — consultar en días")
        else:
            add("yellow",
                f"{m['label']} {m['status'].upper()} ({m['value']} {m['unit']})",
                m["text"], "Consultar con médico.", "PRIORIDAD MEDIA")

    # --- verdes (hábitos / prevención)
    add("green", "ACTIVIDAD FÍSICA: 150 MIN/SEMANA",
        STATS["sedentary_risk"],
        "Caminar, nadar o andar en bicicleta. Comenzar de a poco.",
        "RECOMENDACIÓN GENERAL")
    add("green", "ALIMENTACIÓN EQUILIBRADA",
        "Reducir azúcares, ultraprocesados y grasas saturadas. Priorizar "
        "verduras, frutas, legumbres, pescado y agua.",
        "", "RECOMENDACIÓN GENERAL")
    add("green", "CONTROL ANUAL DE LABORATORIO",
        "Repetir hemograma, glucosa, lípidos y función renal/ hepática "
        "anualmente, o según indique el médico.",
        "", "PREVENCIÓN")
    if sex == "M":
        add("green", "CONTROL DE PSA SEGÚN EDAD",
            "A partir de los 50 años (45 con antecedentes familiares), el "
            "PSA anual permite detectar cáncer de próstata temprano.",
            "", "PREVENCIÓN")

    return recs


def _drug_checks(markers, meds) -> dict:
    """Cruza medicamentos registrados con resultados anormales."""
    by_key = {m["key"]: m for m in markers if m.get("key")}
    med_names = [m["name"] for m in meds]
    out = {
        "meds": med_names,
        "drug_lab": [],
        "drug_drug": [],
        "unknown_meds": [],
        "severity": None,
    }
    for m in meds:
        if not drugs_mod.resolve_med(m["name"]):
            out["unknown_meds"].append(m["name"])
    for key, marker in by_key.items():
        if marker["status"] == "normal":
            continue
        direction = "up" if marker["status"] == "alto" else "down"
        for med_name in med_names:
            res = drugs_mod.check_drug_lab(med_name, key, True, direction)
            out["drug_lab"].extend(res)
    out["drug_drug"] = drugs_mod.check_drug_drug(med_names)
    sevs = [f["severity"] for f in out["drug_lab"]] + \
           [f["severity"] for f in out["drug_drug"]]
    if "red" in sevs:
        out["severity"] = "red"
    elif "yellow" in sevs:
        out["severity"] = "yellow"
    elif sevs:
        out["severity"] = "green"
    return out


def _summary(markers, findings, systems) -> dict:
    """Resumen ejecutivo en texto plano."""
    n_red = sum(1 for f in findings if f["severity"] == "red")
    n_yellow = sum(1 for f in findings if f["severity"] == "yellow")
    n_aged = sum(1 for f in findings if f.get("aged"))
    n_sys = sum(1 for s in systems if s["status"] == "alterado")
    if n_red:
        tone = "crítico"
        first = "EXISTEN ALTERACIONES CRÍTICAS que requieren atención médica INMEDIATA."
    elif n_yellow:
        tone = "precaución"
        first = ("Se detectaron alteraciones que requieren seguimiento médico; "
                 "ninguna alcanza el nivel de urgencia crítica, pero no deben ignorarse.")
    else:
        tone = "favorable"
        first = "No se detectaron alteraciones de laboratorio relevantes en la última evaluación."
    text = (f"Resumen: {first} Se identificaron {n_red} hallazgo(s) de prioridad "
            f"alta, {n_yellow} de prioridad media y {n_sys} sistema(s) con "
            f"alteraciones.")
    if n_aged:
        text += (f" {n_aged} hallazgo(s) provienen de análisis con más de 12 "
                 f"meses: se recomienda repetirlos para conocer el estado actual.")
    return {
        "tone": tone,
        "n_red": n_red,
        "n_yellow": n_yellow,
        "n_aged": n_aged,
        "n_systems_altered": n_sys,
        "text": text,
    }
