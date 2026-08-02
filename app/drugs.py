# -*- coding: utf-8 -*-
"""Base de conocimiento de medicamentos e interacciones.

Cada medicamento puede alterar resultados de laboratorio (efecto
drogas-analito) o interactuar con otros fármacos. El motor cruza estas
reglas contra los valores anormales del paciente y sus medicamentos
registrados, marcando hallazgos con severidad: rojo (grave/urgente),
amarillo (precaución), verde (sin problema detectado).

Fuentes de referencia (información estándar de farmacovigilancia):
FDA prescribing information, Lexicomp / UpToDate drug interactions,
British National Formulary. Los textos se mantienen en lenguaje claro.
"""
import unicodedata
import re


def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


# medicamento -> (nombre común, efectos sobre laboratorio, interacciones)
# lab effects: (canonical_test, direccion, severidad, mensaje)
#   direccion: "up" (sube), "down" (baja), "interference" (interfiere)
# interacciones: (otro medicamento, severidad, mensaje)
DRUGS = {
    "metformina": {
        "name": "Metformina",
        "lab_effects": [
            ("glucose", "down", "green",
             "La metformina baja la glucosa. Un valor normal o bajo confirma su efecto; un valor alto sugiere dosis insuficiente o abandono del tratamiento."),
            ("b12", "down", "yellow",
             "Uso prolongado de metformina (>4-5 años) puede causar deficiencia de vitamina B12 y anemia. Considerar medir B12."),
            ("creatinine", "up", "yellow",
             "Metformina se elimina por riñón. Creatinina elevada aumenta riesgo de acidosis láctica; verificar función renal (eGFR)."),
        ],
        "interactions": [
            ("diureticos", "yellow",
             "Diuréticos pueden elevar creatinina y descompensar la función renal mientras toma metformina."),
            ("contraste", "red",
             "El contraste yodado puede causar fallo renal agudo y acidosis láctica con metformina. Suspender metformina antes de estudios con contraste."),
            ("alcohol", "red",
             "Consumo de alcohol aumenta el riesgo de acidosis láctica con metformina."),
        ],
    },
    "glibenclamida": {
        "name": "Glibenclamida (sulfonilurea)",
        "lab_effects": [
            ("glucose", "down", "green",
             "Baja la glucosa. Riesgo de hipoglucemia si ayuna, salta comidas o agrega actividad física."),
        ],
        "interactions": [
            ("alcohol", "yellow",
             "El alcohol puede causar hipoglucemia severa y reacción tipo disulfiram con glibenclamida."),
        ],
    },
    "insulina": {
        "name": "Insulina",
        "lab_effects": [
            ("glucose", "down", "green",
             "Baja la glucosa; valores persistentemente altos indican dosis insuficiente o mal control."),
            ("potassium", "down", "yellow",
             "La insulina desplaza potasio al interior celular; puede bajar el potasio sérico."),
        ],
        "interactions": [],
    },
    "atorvastatina": {
        "name": "Atorvastatina (estatina)",
        "lab_effects": [
            ("cholesterol", "down", "green",
             "Baja colesterol total y LDL. Efecto esperado del tratamiento."),
            ("ldl", "down", "green",
             "Baja el LDL; si el LDL sigue alto, evaluar dosis o adherencia."),
            ("got", "up", "yellow",
             "Las estatinas pueden elevar transaminasas (GOT/AST, GPT/ALT). Elevación <3x el límite superior suele ser benigna; mayor, requiere evaluación."),
            ("gpt", "up", "yellow",
             "Las estatinas pueden elevar transaminasas. Verificar si hay síntomas (ictericia, orina oscura, fatiga)."),
            ("ck", "up", "red",
             "Elevación de CK (creatina quinasa) con dolor o debilidad muscular puede indicar miopatía/rabdomiólisis por estatina. Consultar urgente."),
        ],
        "interactions": [
            ("fibratos", "red",
             "Combinar estatina con fibratos (gemfibrozilo) aumenta el riesgo de rabdomiólisis."),
            ("warfarin", "yellow",
             "Las estatinas pueden potenciar el efecto del anticoagulante; requiere control de INR."),
            ("eritromicina", "yellow",
             "Los macrólidos aumentan los niveles de estatina y el riesgo de toxicidad muscular."),
        ],
    },
    "simvastatina": {
        "name": "Simvastatina (estatina)",
        "lab_effects": [
            ("cholesterol", "down", "green", "Baja colesterol total y LDL."),
            ("ldl", "down", "green", "Baja el LDL."),
            ("got", "up", "yellow", "Puede elevar transaminasas."),
            ("gpt", "up", "yellow", "Puede elevar transaminasas."),
            ("ck", "up", "red", "Riesgo de miopatía/rabdomiólisis si CK elevada con síntomas."),
        ],
        "interactions": [
            ("fibratos", "red", "Riesgo aumentado de rabdomiólisis."),
            ("warfarin", "yellow", "Potencia el efecto anticoagulante."),
        ],
    },
    "enalapril": {
        "name": "Enalapril (IECA)",
        "lab_effects": [
            ("potassium", "up", "yellow",
             "Los IECA pueden elevar el potasio sérico; hiperpotasemia severa (>5.5) es peligrosa."),
            ("creatinine", "up", "yellow",
             "Puede elevar creatinina levemente al inicio (hasta 30%). Aumento mayor o progresivo requiere evaluación."),
        ],
        "interactions": [
            ("diureticos ahorradores", "red",
             "Enalapril + espironolactona/amilorida/triamtereno puede causar hiperpotasemia grave."),
            ("aines", "yellow",
             "Los antiinflamatorios (ibuprofeno, diclofenac) reducen el efecto antihipertensivo y pueden dañar el riñón."),
            ("suplementos potasio", "yellow",
             "Evitar suplementos de potasio sin control."),
        ],
    },
    "losartan": {
        "name": "Losartán (ARA-II)",
        "lab_effects": [
            ("potassium", "up", "yellow", "Puede elevar el potasio sérico."),
            ("creatinine", "up", "yellow", "Puede elevar creatinina."),
        ],
        "interactions": [
            ("diureticos ahorradores", "red", "Riesgo de hiperpotasemia grave."),
            ("aines", "yellow", "Reduce efecto y daña riñón."),
        ],
    },
    "hidroclorotiazida": {
        "name": "Hidroclorotiazida (diurético tiazídico)",
        "lab_effects": [
            ("potassium", "down", "yellow",
             "Los tiazídicos bajan el potasio (hipocalemia); riesgo de arritmias si es severa."),
            ("sodium", "down", "yellow", "Puede bajar el sodio (hiponatremia)."),
            ("uric_acid", "up", "yellow",
             "Eleva el ácido úrico; puede desencadenar gota."),
            ("glucose", "up", "yellow", "Puede elevar la glucosa."),
            ("calcium", "up", "yellow", "Puede elevar levemente el calcio."),
            ("cholesterol", "up", "yellow", "Puede elevar lípidos."),
        ],
        "interactions": [
            ("digoxina", "red",
             "Hipocalemia inducida por tiazidas aumenta toxicidad de digoxina (arritmias)."),
            ("litio", "yellow", "Las tiazidas aumentan niveles de litio."),
        ],
    },
    "furosemida": {
        "name": "Furosemida (diurético de asa)",
        "lab_effects": [
            ("potassium", "down", "yellow", "Baja potasio (hipocalemia)."),
            ("sodium", "down", "yellow", "Baja sodio (hiponatremia)."),
            ("uric_acid", "up", "yellow", "Eleva ácido úrico."),
            ("creatinine", "up", "yellow", "Puede elevar creatinina por deshidratación."),
            ("calcium", "down", "yellow", "Baja calcio (hipocalcemia)."),
        ],
        "interactions": [
            ("digoxina", "red", "Riesgo de toxicidad por digoxina si hipocalemia."),
            ("aines", "yellow", "Reduce efecto diurético y daña riñón."),
        ],
    },
    "aspirina": {
        "name": "Aspirina (ácido acetilsalicílico)",
        "lab_effects": [
            ("uric_acid", "down", "green", "Dosis bajas de aspirina bajan el ácido úrico."),
            ("platelets", "down", "yellow", "Inhibe la función plaquetaria (no el conteo); puede alargar sangrado."),
            ("creatinine", "up", "yellow", "En dosis altas o uso prolongado puede afectar el riñón."),
        ],
        "interactions": [
            ("warfarin", "red", "Aspirina + anticoagulante aumenta mucho el riesgo de hemorragia."),
            ("aines", "yellow", "Aumenta riesgo de úlcera y sangrado gastrointestinal."),
            ("metotrexato", "red", "La aspirina aumenta la toxicidad del metotrexato."),
        ],
    },
    "clopidogrel": {
        "name": "Clopidogrel",
        "lab_effects": [],
        "interactions": [
            ("omeprazol", "yellow", "El omeprazol reduce la activación del clopidogrel y su efecto antiagregante."),
            ("warfarin", "yellow", "Riesgo de sangrado aumentado."),
        ],
    },
    "warfarin": {
        "name": "Warfarina (anticoagulante)",
        "lab_effects": [
            ("inr", "up", "red", "INR elevado = riesgo de sangrado. Requiere control estricto."),
        ],
        "interactions": [
            ("aspirina", "red", "Riesgo de hemorragia grave."),
            ("aines", "red", "Riesgo de sangrado gastrointestinal."),
            ("antibioticos", "yellow", "Muchos antibióticos alteran el INR."),
            ("amiodarona", "red", "Aumenta fuertemente el efecto de warfarina."),
        ],
    },
    "levotiroxina": {
        "name": "Levotiroxina (hormona tiroidea)",
        "lab_effects": [
            ("tsh", "down", "yellow", "TSH baja puede indicar sobredosis de levotiroxina (hipertiroidismo) o dosis ajustada."),
            ("t4_total", "up", "green", "T4 total sube con el tratamiento; esperado."),
        ],
        "interactions": [
            ("calcio", "yellow", "El calcio reduce absorción de levotiroxina; separar 4 horas."),
            ("hierro", "yellow", "El hierro reduce absorción; separar 4 horas."),
            ("omeprazol", "yellow", "Los inhibidores de bomba pueden reducir absorción."),
        ],
    },
    "amiodarona": {
        "name": "Amiodarona",
        "lab_effects": [
            ("tsh", "up", "yellow", "La amiodarona altera la función tiroidea (puede causar hipo o hipertiroidismo)."),
            ("got", "up", "yellow", "Puede elevar transaminasas."),
            ("gpt", "up", "yellow", "Puede elevar transaminasas."),
        ],
        "interactions": [
            ("warfarin", "red", "Aumenta fuertemente el INR."),
            ("digoxina", "red", "Aumenta niveles de digoxina."),
            ("estatinas", "yellow", "Aumenta niveles de estatinas."),
        ],
    },
    "omeprazol": {
        "name": "Omeprazol (inhibidor de bomba)",
        "lab_effects": [
            ("magnesium", "down", "yellow", "Uso prolongado puede bajar magnesio."),
            ("b12", "down", "yellow", "Uso prolongado puede causar deficiencia de B12."),
        ],
        "interactions": [
            ("clopidogrel", "yellow", "Reduce el efecto del clopidogrel."),
            ("levotiroxina", "yellow", "Reduce absorción de levotiroxina."),
        ],
    },
    "vitamina b12": {
        "name": "Vitamina B12",
        "lab_effects": [
            ("b12", "up", "green", "Sube el nivel de B12; esperado con suplementación."),
        ],
        "interactions": [],
    },
    "vitamina d": {
        "name": "Vitamina D (colecalciferol)",
        "lab_effects": [
            ("vit_d", "up", "green", "Sube el nivel de vitamina D; esperado."),
            ("calcium", "up", "yellow", "Dosis altas pueden elevar calcio (hipercalcemia)."),
        ],
        "interactions": [],
    },
    "calcio": {
        "name": "Calcio (suplemento)",
        "lab_effects": [
            ("calcium", "up", "yellow", "Puede elevar calcio sérico."),
        ],
        "interactions": [
            ("levotiroxina", "yellow", "Reduce absorción; separar 4 horas."),
        ],
    },
    "hierro": {
        "name": "Hierro (suplemento)",
        "lab_effects": [
            ("ferritin", "up", "green", "Sube ferritina; esperado."),
        ],
        "interactions": [
            ("levotiroxina", "yellow", "Reduce absorción; separar 4 horas."),
        ],
    },
    "alopurinol": {
        "name": "Alopurinol",
        "lab_effects": [
            ("uric_acid", "down", "green", "Baja el ácido úrico; efecto esperado."),
        ],
        "interactions": [
            ("azatioprina", "red", "Riesgo de mielosupresión grave."),
        ],
    },
    "paracetamol": {
        "name": "Paracetamol (acetaminofén)",
        "lab_effects": [
            ("got", "up", "yellow", "En dosis altas o crónicas puede elevar transaminasas."),
            ("gpt", "up", "yellow", "Hepatotoxicidad en sobredosis."),
        ],
        "interactions": [
            ("warfarin", "yellow", "Uso regular puede elevar INR."),
        ],
    },
    "ibuprofeno": {
        "name": "Ibuprofeno (AINE)",
        "lab_effects": [
            ("creatinine", "up", "yellow", "Los AINE pueden dañar el riñón, especialmente con uso crónico."),
            ("potassium", "up", "yellow", "Pueden elevar potasio."),
            ("sodium", "up", "yellow", "Retienen sodio y agua."),
        ],
        "interactions": [
            ("enalapril", "yellow", "Reduce efecto antihipertensivo; daño renal."),
            ("losartan", "yellow", "Reduce efecto antihipertensivo; daño renal."),
            ("aspirina", "yellow", "Aumenta riesgo de úlcera/sangrado."),
            ("furosemida", "yellow", "Reduce efecto diurético."),
            ("warfarin", "red", "Riesgo de hemorragia grave."),
        ],
    },
    "sildenafil": {
        "name": "Sildenafil",
        "lab_effects": [],
        "interactions": [
            ("nitratos", "red", "Combinación contraindicada: hipotensión grave."),
        ],
    },
    "prednisona": {
        "name": "Prednisona (corticoide)",
        "lab_effects": [
            ("glucose", "up", "yellow", "Los corticoides elevan la glucosa."),
            ("potassium", "down", "yellow", "Pueden bajar potasio."),
            ("sodium", "up", "yellow", "Retienen sodio."),
            ("wbc", "up", "yellow", "Elevan glóbulos blancos (leucocitosis por desmarginalización)."),
        ],
        "interactions": [
            ("aines", "red", "Riesgo muy aumentado de úlcera gastrointestinal."),
            ("diureticos", "yellow", "Pérdida de potasio sumada."),
        ],
    },
    "salbutamol": {
        "name": "Salbutamol (broncodilatador)",
        "lab_effects": [
            ("potassium", "down", "yellow", "Puede bajar potasio transitoriamente."),
            ("glucose", "up", "yellow", "Puede elevar glucosa."),
        ],
        "interactions": [],
    },
    "biotina": {
        "name": "Biotina (vitamina B7, suplemento)",
        "lab_effects": [
            ("tsh", "interference", "red",
             "La biotina interfiere con ensayos de TSH, troponina y hormona tiroidea, dando resultados falsos. Suspender 48-72 h antes de análisis."),
            ("troponin", "interference", "red",
             "La biotina puede dar troponina falsamente baja (riesgo de infarto no detectado)."),
        ],
        "interactions": [],
    },
    "anticonceptivos": {
        "name": "Anticonceptivos orales",
        "lab_effects": [
            ("trig", "up", "yellow", "Pueden elevar triglicéridos."),
            ("glucose", "up", "yellow", "Pueden alterar tolerancia a la glucosa."),
            ("tsh", "up", "yellow", "Pueden elevar TSH por aumento de TBG."),
        ],
        "interactions": [],
    },
    "sertralina": {
        "name": "Sertralina (ISRS)",
        "lab_effects": [
            ("sodium", "down", "yellow", "Riesgo de hiponatremia (especialmente en mayores)."),
        ],
        "interactions": [
            ("aines", "yellow", "Aumenta riesgo de sangrado gastrointestinal."),
            ("aspirina", "yellow", "Aumenta riesgo de sangrado."),
        ],
    },
    "tamsulosina": {
        "name": "Tamsulosina (alfabloqueante)",
        "lab_effects": [],
        "interactions": [],
    },
    "finasterida": {
        "name": "Finasterida",
        "lab_effects": [
            ("psa_total", "down", "yellow", "Baja el PSA ~50%; interpretar resultados de PSA con esto en cuenta."),
        ],
        "interactions": [],
    },
    "rosuvastatina": {
        "name": "Rosuvastatina (estatina)",
        "lab_effects": [
            ("cholesterol", "down", "green", "Baja colesterol y LDL."),
            ("ldl", "down", "green", "Baja LDL."),
            ("got", "up", "yellow", "Puede elevar transaminasas."),
            ("gpt", "up", "yellow", "Puede elevar transaminasas."),
            ("ck", "up", "red", "Riesgo de miopatía si CK elevada con síntomas."),
        ],
        "interactions": [
            ("fibratos", "red", "Riesgo de rabdomiólisis."),
        ],
    },
    "amlodipino": {
        "name": "Amlodipino (bloqueante cálcico)",
        "lab_effects": [],
        "interactions": [
            ("simvastatina", "yellow", "Amlodipino aumenta niveles de simvastatina; limitar dosis."),
        ],
    },
    "espirolactona": {
        "name": "Espironolactona (diurético ahorrador de K)",
        "lab_effects": [
            ("potassium", "up", "red", "Espironolactona eleva el potasio; hiperpotasemia grave posible, sobre todo con insuficiencia renal."),
            ("creatinine", "up", "yellow", "Puede elevar creatinina."),
        ],
        "interactions": [
            ("enalapril", "red", "Riesgo de hiperpotasemia grave."),
            ("losartan", "red", "Riesgo de hiperpotasemia grave."),
            ("suplementos potasio", "red", "No combinar."),
        ],
    },
    "digoxina": {
        "name": "Digoxina",
        "lab_effects": [
            ("potassium", "down", "yellow", "La hipocalemia aumenta toxicidad de digoxina."),
            ("creatinine", "up", "yellow", "La función renal alterada aumenta niveles de digoxina."),
        ],
        "interactions": [
            ("furosemida", "red", "Hipocalemia por diurético → toxicidad por digoxina."),
            ("hidroclorotiazida", "red", "Hipocalemia → toxicidad por digoxina."),
            ("amiodarona", "red", "Aumenta niveles de digoxina."),
        ],
    },
}

# sinónimos comunes -> clave canónica del diccionario
ALIASES = {
    "metformin": "metformina", "metformina": "metformina",
    "glibenclamida": "glibenclamida", "glinbenclamida": "glibenclamida",
    "insulina": "insulina", "insulina glargina": "insulina",
    "atorvastatina": "atorvastatina", "atorvastatin": "atorvastatina",
    "simvastatina": "simvastatina", "simvastatin": "simvastatina",
    "rosuvastatina": "rosuvastatina", "rosuvastatin": "rosuvastatina",
    "enalapril": "enalapril", "enalaprilo": "enalapril",
    "losartan": "losartan", "losartán": "losartan", "losartan potasico": "losartan",
    "hidroclorotiazida": "hidroclorotiazida", "hidroclorotiazide": "hidroclorotiazida",
    "hctz": "hidroclorotiazida", "furosemida": "furosemida",
    "furosemide": "furosemida", "aspirina": "aspirina",
    "aspirin": "aspirina", "aas": "aspirina",
    "clopidogrel": "clopidogrel", "warfarin": "warfarin",
    "warfarina": "warfarin", "coumadin": "warfarin",
    "levotiroxina": "levotiroxina", "levothyroxine": "levotiroxina",
    "eutirox": "levotiroxina", "amiodarona": "amiodarona",
    "amiodarone": "amiodarona", "omeprazol": "omeprazol",
    "omeprazole": "omeprazol", "vitamina b12": "vitamina b12",
    "b12": "vitamina b12", "cianocobalamina": "vitamina b12",
    "vitamina d": "vitamina d", "colecalciferol": "vitamina d",
    "calcio": "calcio", "calcium": "calcio",
    "hierro": "hierro", "hierro sulfato": "hierro", "iron": "hierro",
    "alopurinol": "alopurinol", "allopurinol": "alopurinol",
    "paracetamol": "paracetamol", "acetaminofen": "paracetamol",
    "acetaminofén": "paracetamol", "ibuprofeno": "ibuprofeno",
    "ibuprofen": "ibuprofeno", "sildenafil": "sildenafil",
    "viagra": "sildenafil", "prednisona": "prednisona",
    "prednisone": "prednisona", "salbutamol": "salbutamol",
    "biotina": "biotina", "biotin": "biotina",
    "anticonceptivos": "anticonceptivos", "anticonceptivo": "anticonceptivos",
    "anovulatorios": "anticonceptivos", "sertralina": "sertralina",
    "sertraline": "sertralina", "tamsulosina": "tamsulosina",
    "finasterida": "finasterida", "finasteride": "finasterida",
    "amlodipino": "amlodipino", "amlodipine": "amlodipino",
    "espirolactona": "espirolactona", "espironolactona": "espirolactona",
    "spironolactone": "espirolactona", "digoxina": "digoxina",
    "digoxin": "digoxina",
}


def resolve_med(name: str) -> dict | None:
    """Resolve a user-entered medicine name to a known drug entry."""
    n = _norm(name)
    if not n:
        return None
    if n in ALIASES:
        return DRUGS.get(ALIASES[n])
    # substring match
    for alias, key in ALIASES.items():
        if len(alias) >= 5 and (alias in n or n in alias):
            return DRUGS.get(key)
    return None


def check_drug_lab(med_name: str, canonical_test: str,
                   abnormal: bool, direction: str) -> list[dict]:
    """Cross a drug's lab effects against a patient's abnormal test.

    Returns list of findings {severity, message, test, drug}.
    """
    med = resolve_med(med_name)
    if not med:
        return []
    out = []
    for eff in med.get("lab_effects", []):
        t, dr, sev, msg = eff
        if t != canonical_test:
            continue
        relevant = False
        if dr == "interference":
            relevant = abnormal  # interference matters if value looks off
        elif dr == "up" and direction == "up":
            relevant = True
        elif dr == "down" and direction == "down":
            relevant = True
        elif dr in ("up", "down") and not abnormal:
            # still warn when the drug effect and the observed value align
            relevant = direction == dr
        if relevant:
            out.append({
                "severity": sev,
                "message": msg,
                "test": canonical_test,
                "drug": med["name"],
            })
    return out


def check_drug_drug(meds: list[str]) -> list[dict]:
    """Check pairwise interactions among a list of medicine names."""
    resolved = []
    for m in meds:
        d = resolve_med(m)
        if d:
            resolved.append((m, d))
    out = []
    seen = set()
    for i in range(len(resolved)):
        for j in range(i + 1, len(resolved)):
            name_a, da = resolved[i]
            name_b, db = resolved[j]
            for inter in da.get("interactions", []):
                other, sev, msg = inter
                if _norm(other) in (_norm(db["name"]),) or \
                        other in (db["name"], name_b) or \
                        _norm(other) == _norm(name_b):
                    key = tuple(sorted([_norm(name_a), _norm(name_b)]))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({
                        "severity": sev,
                        "message": msg,
                        "drugs": f"{da['name']} + {db['name']}",
                    })
    return out
