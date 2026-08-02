# -*- coding: utf-8 -*-
"""Construye el catálogo normalizado de medicamentos -> app/drug_catalog.json.

Fuente de DOSIS REALES: CIMA (AEMPS, registro oficial de medicamentos de España)
    https://cima.aemps.es/cima/rest/medicamentos?nombre=<principio_activo>
Las concentraciones (mg/g/mcg/UI/%) se PARSEAN de los nombres comerciales reales;
NUNCA se inventan. Solo se toman presentaciones mono-principio (se descartan las
combinaciones "A / B").

MARCAS COMERCIALES: mapa curado de marcas reales y ampliamente documentadas de
mercado global + España + LATAM + Brasil (p. ej. Cialis=tadalafilo,
Glifage=metformina, Aradois=losartán). No se inventan marcas.

Uso:
    python tools/build_drug_catalog.py
Requiere red (consulta CIMA una vez). Volver a ejecutar para refrescar dosis.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "app", "drug_catalog.json")
CIMA = "https://cima.aemps.es/cima/rest/medicamentos"

# ---------------------------------------------------------------------------
# Principios activos a incluir. Cada entrada:
#   (generico_display, consulta_cima, key_interacciones|None, [aliases],
#    [marcas_comerciales])
# 'key' enlaza con app/drugs.py DRUGS para conservar el motor de interacciones.
# aliases: variantes ES/PT y sinónimos para que la búsqueda las encuentre.
# marcas: reales, global + ES + LATAM + Brasil.
# ---------------------------------------------------------------------------
INGREDIENTS = [
    # --- antihipertensivos / cardiovascular ---
    ("Losartán", "losartan", "losartan",
     ["losartan", "losartana", "losartan potasico"],
     ["Cozaar", "Aradois", "Corus", "Losacor", "Torlos", "Loriax"]),
    ("Enalapril", "enalapril", "enalapril",
     ["enalapril", "enalaprilo", "maleato de enalapril"],
     ["Renitec", "Vasotec", "Glioten", "Eupressin", "Renipril"]),
    ("Valsartán", "valsartan", None, ["valsartan", "valsartana"],
     ["Diovan", "Tareg", "Valpression"]),
    ("Irbesartán", "irbesartan", None, ["irbesartan", "irbesartana"],
     ["Aprovel", "Avapro", "Karvea"]),
    ("Telmisartán", "telmisartan", None, ["telmisartan", "telmisartana"],
     ["Micardis", "Pritor", "Predxal"]),
    ("Amlodipino", "amlodipino", "amlodipino",
     ["amlodipino", "amlodipina", "besilato de amlodipino"],
     ["Norvasc", "Amlodin", "Pressat", "Amlo"]),
    ("Nifedipino", "nifedipino", None, ["nifedipino", "nifedipina"],
     ["Adalat", "Cardifen"]),
    ("Hidroclorotiazida", "hidroclorotiazida", "hidroclorotiazida",
     ["hidroclorotiazida", "hctz"],
     ["Esidrix", "Clorana", "Diurex"]),
    ("Furosemida", "furosemida", "furosemida", ["furosemida", "furosemide"],
     ["Lasix", "Seguril", "Furosem"]),
    ("Espironolactona", "espironolactona", "espirolactona",
     ["espironolactona", "espirolactona", "spironolactone"],
     ["Aldactone", "Aldactona"]),
    ("Atenolol", "atenolol", None, ["atenolol"],
     ["Tenormin", "Atenol", "Ablok"]),
    ("Bisoprolol", "bisoprolol", None, ["bisoprolol", "fumarato de bisoprolol"],
     ["Concor", "Emconcor", "Concardio"]),
    ("Carvedilol", "carvedilol", None, ["carvedilol"],
     ["Coreg", "Dilatrend", "Divelol", "Coreg"]),
    ("Metoprolol", "metoprolol", None, ["metoprolol", "tartrato de metoprolol"],
     ["Lopressor", "Betaloc", "Seloken", "Selozok"]),
    ("Propranolol", "propranolol", None, ["propranolol"],
     ["Inderal", "Sumial"]),
    ("Digoxina", "digoxina", "digoxina", ["digoxina", "digoxin"],
     ["Lanoxin", "Digoxina"]),
    ("Warfarina", "warfarina", "warfarin",
     ["warfarina", "warfarin", "warfarina sodica"],
     ["Coumadin", "Marevan", "Aldocumar"]),
    ("Clopidogrel", "clopidogrel", "clopidogrel", ["clopidogrel"],
     ["Plavix", "Iscover"]),
    ("Amiodarona", "amiodarona", "amiodarona", ["amiodarona", "amiodarone"],
     ["Cordarone", "Ancoron", "Trangorex"]),

    # --- estatinas / lípidos ---
    ("Atorvastatina", "atorvastatina", "atorvastatina",
     ["atorvastatina", "atorvastatin"],
     ["Lipitor", "Citalor", "Torvast", "Cardyl", "Zarator"]),
    ("Simvastatina", "simvastatina", "simvastatina",
     ["simvastatina", "sinvastatina", "simvastatin"],
     ["Zocor", "Sinvascor", "Zovast"]),
    ("Rosuvastatina", "rosuvastatina", "rosuvastatina",
     ["rosuvastatina", "rosuvastatin"],
     ["Crestor", "Rosucard", "Provisacor"]),
    ("Pravastatina", "pravastatina", None, ["pravastatina"],
     ["Pravacol", "Pravacol"]),
    ("Ezetimiba", "ezetimiba", None, ["ezetimiba", "ezetimibe"],
     ["Ezetrol", "Zetia"]),
    ("Fenofibrato", "fenofibrato", None, ["fenofibrato"],
     ["Lipidil", "Secalip"]),
    ("Gemfibrozilo", "gemfibrozilo", None, ["gemfibrozilo", "gemfibrozil"],
     ["Lopid"]),

    # --- diabetes ---
    ("Metformina", "metformina", "metformina",
     ["metformina", "metformin", "clorhidrato de metformina"],
     ["Glucophage", "Glifage", "Dimefor", "Dianben", "Glucofage"]),
    ("Glibenclamida", "glibenclamida", "glibenclamida",
     ["glibenclamida", "gliburida", "glibenclamide"],
     ["Daonil", "Euglucon"]),
    ("Gliclazida", "gliclazida", None, ["gliclazida", "gliclazide"],
     ["Diamicron"]),
    ("Glimepirida", "glimepirida", None, ["glimepirida", "glimepiride"],
     ["Amaryl", "Amaryl"]),
    ("Sitagliptina", "sitagliptina", None, ["sitagliptina", "sitagliptin"],
     ["Januvia"]),
    ("Empagliflozina", "empagliflozina", None,
     ["empagliflozina", "empagliflozin"], ["Jardiance"]),
    ("Dapagliflozina", "dapagliflozina", None,
     ["dapagliflozina", "dapagliflozin"], ["Forxiga", "Farxiga"]),
    ("Insulina glargina", "insulina glargina", "insulina",
     ["insulina glargina", "insulina"], ["Lantus", "Basaglar", "Toujeo"]),

    # --- tiroides ---
    ("Levotiroxina", "levotiroxina", "levotiroxina",
     ["levotiroxina", "levothyroxine", "levotiroxina sodica"],
     ["Eutirox", "Synthroid", "Puran T4", "Levoid"]),

    # --- gastro / PPI ---
    ("Omeprazol", "omeprazol", "omeprazol", ["omeprazol", "omeprazole"],
     ["Losec", "Prilosec", "Peprazol", "Gastrium"]),
    ("Pantoprazol", "pantoprazol", None, ["pantoprazol", "pantoprazole"],
     ["Protonix", "Pantozol", "Pantecta"]),
    ("Esomeprazol", "esomeprazol", None, ["esomeprazol", "esomeprazole"],
     ["Nexium"]),
    ("Ranitidina", "ranitidina", None, ["ranitidina", "ranitidine"],
     ["Zantac", "Label"]),

    # --- analgésicos / AINE ---
    ("Paracetamol", "paracetamol", "paracetamol",
     ["paracetamol", "acetaminofen", "acetaminofeno"],
     ["Tylenol", "Panadol", "Gelocatil", "Tempra"]),
    ("Ibuprofeno", "ibuprofeno", "ibuprofeno", ["ibuprofeno", "ibuprofen"],
     ["Advil", "Motrin", "Alivium", "Espidifen"]),
    ("Naproxeno", "naproxeno", None, ["naproxeno", "naproxen"],
     ["Naprosyn", "Flanax", "Aleve"]),
    ("Diclofenaco", "diclofenaco", None, ["diclofenaco", "diclofenac"],
     ["Voltaren", "Cataflam", "Voltaren"]),
    ("Metamizol (dipirona)", "metamizol", None,
     ["metamizol", "dipirona", "dipirona sodica", "metamizol sodico"],
     ["Nolotil", "Novalgina", "Dorona", "Buscapina Composta"]),
    ("Ketorolaco", "ketorolaco", None, ["ketorolaco", "ketorolac"],
     ["Toradol", "Dolac"]),
    ("Aspirina", "acido acetilsalicilico", "aspirina",
     ["aspirina", "aspirin", "acido acetilsalicilico", "aas"],
     ["Aspirina", "AAS", "Adiro", "Bufferin"]),
    ("Tramadol", "tramadol", None, ["tramadol", "clorhidrato de tramadol"],
     ["Adolonta", "Tramal", "Ultram"]),

    # --- corticoides / respiratorio ---
    ("Prednisona", "prednisona", "prednisona", ["prednisona", "prednisone"],
     ["Meticorten", "Deltasone"]),
    ("Prednisolona", "prednisolona", None, ["prednisolona", "prednisolone"],
     ["Prelone", "Estilsona"]),
    ("Dexametasona", "dexametasona", None, ["dexametasona", "dexamethasone"],
     ["Decadron", "Fortecortin"]),
    ("Salbutamol", "salbutamol", "salbutamol",
     ["salbutamol", "albuterol"], ["Ventolin", "Aerolin"]),
    ("Budesonida", "budesonida", None, ["budesonida", "budesonide"],
     ["Pulmicort", "Busonid"]),
    ("Montelukast", "montelukast", None, ["montelukast"],
     ["Singulair", "Montelair"]),
    ("Loratadina", "loratadina", None, ["loratadina", "loratadine"],
     ["Claritin", "Claritine", "Loratadina"]),
    ("Cetirizina", "cetirizina", None, ["cetirizina", "cetirizine"],
     ["Zyrtec", "Reactine"]),

    # --- antibióticos ---
    ("Amoxicilina", "amoxicilina", None, ["amoxicilina", "amoxicillin"],
     ["Amoxil", "Clamoxyl", "Novamox"]),
    ("Amoxicilina/clavulánico", "amoxicilina clavulanico", None,
     ["amoxicilina clavulanico", "amoxicilina acido clavulanico", "co-amoxiclav"],
     ["Augmentin", "Clavulin", "Amoxidal Duo"]),
    ("Azitromicina", "azitromicina", None, ["azitromicina", "azithromycin"],
     ["Zithromax", "Zitromax", "Azitrom"]),
    ("Ciprofloxacino", "ciprofloxacino", None,
     ["ciprofloxacino", "ciprofloxacin", "ciprofloxacina"],
     ["Cipro", "Ciproxina", "Baycip"]),
    ("Levofloxacino", "levofloxacino", None,
     ["levofloxacino", "levofloxacin", "levofloxacina"],
     ["Levaquin", "Tavanic"]),
    ("Cefalexina", "cefalexina", None, ["cefalexina", "cephalexin"],
     ["Keflex", "Cefaclin"]),
    ("Claritromicina", "claritromicina", None,
     ["claritromicina", "clarithromycin"], ["Klaricid", "Biaxin"]),
    ("Metronidazol", "metronidazol", None, ["metronidazol", "metronidazole"],
     ["Flagyl", "Flagystatin"]),
    ("Trimetoprima/sulfametoxazol", "sulfametoxazol trimetoprima", None,
     ["cotrimoxazol", "sulfametoxazol trimetoprima", "tmp smx"],
     ["Bactrim", "Septra", "Septrin"]),
    ("Nitrofurantoína", "nitrofurantoina", None,
     ["nitrofurantoina", "nitrofurantoin"], ["Macrodantina", "Furadantina"]),

    # --- psiquiatría / neuro ---
    ("Sertralina", "sertralina", "sertralina", ["sertralina", "sertraline"],
     ["Zoloft", "Besitran", "Tolrest"]),
    ("Fluoxetina", "fluoxetina", None, ["fluoxetina", "fluoxetine"],
     ["Prozac", "Daforin", "Adofen"]),
    ("Escitalopram", "escitalopram", None, ["escitalopram"],
     ["Lexapro", "Cipralex"]),
    ("Paroxetina", "paroxetina", None, ["paroxetina", "paroxetine"],
     ["Paxil", "Aropax", "Seroxat"]),
    ("Amitriptilina", "amitriptilina", None,
     ["amitriptilina", "amitriptyline"], ["Tryptanol", "Elavil"]),
    ("Alprazolam", "alprazolam", None, ["alprazolam"],
     ["Xanax", "Frontal", "Trankimazin"]),
    ("Clonazepam", "clonazepam", None, ["clonazepam"],
     ["Rivotril", "Klonopin"]),
    ("Diazepam", "diazepam", None, ["diazepam"],
     ["Valium", "Valium"]),
    ("Quetiapina", "quetiapina", None, ["quetiapina", "quetiapine"],
     ["Seroquel"]),
    ("Gabapentina", "gabapentina", None, ["gabapentina", "gabapentin"],
     ["Neurontin", "Gabapentin"]),
    ("Pregabalina", "pregabalina", None, ["pregabalina", "pregabalin"],
     ["Lyrica"]),
    ("Levetiracetam", "levetiracetam", None, ["levetiracetam"],
     ["Keppra"]),

    # --- urología / hormonal ---
    ("Sildenafil", "sildenafilo", "sildenafil",
     ["sildenafil", "sildenafilo", "citrato de sildenafil"],
     ["Viagra", "Pramil", "Suvvia", "Revatio"]),
    ("Tadalafilo", "tadalafilo", None, ["tadalafil", "tadalafilo"],
     ["Cialis", "Tadora", "Adcirca"]),
    ("Tamsulosina", "tamsulosina", "tamsulosina",
     ["tamsulosina", "tamsulosin"], ["Flomax", "Secotex", "Omnic"]),
    ("Finasterida", "finasterida", "finasterida",
     ["finasterida", "finasteride"], ["Proscar", "Propecia", "Finasterida"]),

    # --- suplementos / otros ---
    ("Alopurinol", "alopurinol", "alopurinol", ["alopurinol", "allopurinol"],
     ["Zyloric", "Zyloprim"]),
    ("Colchicina", "colchicina", None, ["colchicina", "colchicine"],
     ["Colchicine", "Colchis"]),
    ("Vitamina D (colecalciferol)", "colecalciferol", "vitamina d",
     ["vitamina d", "colecalciferol", "vitamina d3"],
     ["Deltius", "Vigantol", "Hidroferol"]),
    ("Vitamina B12 (cianocobalamina)", "cianocobalamina", "vitamina b12",
     ["vitamina b12", "cianocobalamina", "b12"], ["Optovite", "Dodex"]),
    ("Calcio (carbonato)", "calcio", "calcio", ["calcio", "carbonato de calcio"],
     ["Caltrate", "Calcium"]),
    ("Hierro (sulfato ferroso)", "sulfato ferroso", "hierro",
     ["hierro", "sulfato ferroso", "hierro sulfato"],
     ["Ferro-Gradumet", "Tardyferon", "Ferplex"]),
    ("Ácido fólico", "acido folico", None, ["acido folico", "folato"],
     ["Acfol", "Folacin"]),
    ("Biotina", "biotina", "biotina", ["biotina", "biotin"],
     ["Medebiotin"]),
    ("Levonorgestrel/etinilestradiol", "etinilestradiol levonorgestrel",
     "anticonceptivos",
     ["anticonceptivo", "anticonceptivos", "anovulatorios"],
     ["Microgynon", "Nordette"]),
]

# marcas extra que apuntan a un genérico (para búsqueda por marca aunque el
# genérico ya las liste; el mapa principal está arriba en cada entrada).

STRENGTH_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(mg/ml|mcg/ml|microgramos?|miligramos?|gramos?|mcg|mg|µg|ug|g|"
    r"unidades|ui|u\.i\.|%)",
    re.IGNORECASE)


def _norm_unit(u: str) -> str:
    u = u.lower()
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
    return u  # mg, mcg, g, %, mg/ml, mcg/ml


def _parse_number(raw: str) -> float | None:
    """Convierte '1.000'->1000 (miles), '0,5'->0.5, '12,5'->12.5 (coma decimal)."""
    if re.match(r"^\d{1,3}\.\d{3}$", raw):  # separador de miles español
        raw = raw.replace(".", "")
    else:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def fetch_cima(query: str) -> list[dict]:
    """Trae todas las páginas de resultados de CIMA para una consulta."""
    out = []
    page = 1
    while page <= 6:  # tope defensivo
        url = f"{CIMA}?nombre={urllib.parse.quote(query)}&pagina={page}"
        req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": "labs-catalog/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"  ! {query} pág {page}: {e}", file=sys.stderr)
            break
        res = data.get("resultados", [])
        out.extend(res)
        total = data.get("totalFilas", 0)
        if page * data.get("tamanioPagina", len(res) or 25) >= total or not res:
            break
        page += 1
        time.sleep(0.15)
    return out


def parse_strengths(products: list[dict], query: str) -> list[str]:
    """Extrae concentraciones mono-principio reales de los nombres CIMA."""
    strengths = {}   # texto normalizado -> (valor_num, unidad) para ordenar
    for p in products:
        name = (p.get("nombre") or "")
        # descartar combinaciones (dos principios): "A / B ... 100/25 mg"
        first_digit = next((idx for idx, c in enumerate(name) if c.isdigit()), -1)
        if first_digit < 0:
            continue  # sin concentración numérica
        prefix = name[:first_digit]
        if "/" in prefix:
            continue
        m = STRENGTH_RE.search(name)
        if not m:
            continue
        unit = _norm_unit(m.group(2))
        val = _parse_number(m.group(1))
        if val is None or val <= 0 or val > 100000:
            continue
        # etiqueta limpia: entero sin decimales; decimal con coma española
        if val == int(val):
            label = f"{int(val)} {unit}"
        else:
            label = f"{val:g}".replace(".", ",") + f" {unit}"
        strengths[label] = (unit, val)
    # ordenar por unidad y luego valor
    items = sorted(strengths.items(), key=lambda kv: (kv[1][0], kv[1][1]))
    return [k for k, _ in items]


def main():
    catalog = []
    total = len(INGREDIENTS)
    for i, (generic, query, key, aliases, brands) in enumerate(INGREDIENTS, 1):
        print(f"[{i}/{total}] {generic} …", flush=True)
        products = fetch_cima(query)
        strengths = parse_strengths(products, query)
        # forma farmacéutica dominante (informativa)
        form = ""
        for p in products[:5]:
            nm = (p.get("nombre") or "").lower()
            if "comprimidos" in nm: form = "comprimidos"; break
            if "capsulas" in nm or "cápsulas" in nm: form = "cápsulas"; break
            if "solucion" in nm or "solución" in nm: form = "solución"; break
            if "inyectable" in nm: form = "inyectable"; break
        catalog.append({
            "key": key,
            "generic": generic,
            "aliases": sorted(set(a.lower() for a in aliases)),
            "brands": brands,
            "strengths": strengths,
            "form": form,
        })
        time.sleep(0.1)

    catalog.sort(key=lambda e: e["generic"].lower())
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"source": "CIMA (AEMPS) + curated LATAM/BR/ES brand map",
                   "count": len(catalog), "drugs": catalog},
                  f, ensure_ascii=False, indent=1)
    n_no_str = sum(1 for e in catalog if not e["strengths"])
    print(f"\n✓ {len(catalog)} fármacos -> {os.path.relpath(OUT)}")
    print(f"  sin dosis parseada: {n_no_str} (revisar si es alto)")


if __name__ == "__main__":
    main()
