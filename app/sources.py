# -*- coding: utf-8 -*-
"""Fuentes científicas verificables para cada estadística y recomendación.

Cada entrada es una referencia real y comprobable: publicación, revista,
año y enlace a PubMed / guía oficial. Se muestran en el informe para que
el usuario pueda verificar la evidencia.
"""

SOURCES = {
    "diabetes_risk": [
        {
            "title": "Reduction in the Incidence of Type 2 Diabetes with Lifestyle Intervention or Metformin (Diabetes Prevention Program)",
            "journal": "New England Journal of Medicine, 2002",
            "ref": "NEJM 2002;346(6):393-403. PubMed 11832527",
            "url": "https://pubmed.ncbi.nlm.nih.gov/11832527/",
            "note": "La pérdida de 5-7% del peso redujo la incidencia de diabetes en 58%.",
        },
        {
            "title": "Standards of Care in Diabetes (prediabetes: HbA1c 5,7-6,4%, glucosa 100-125 mg/dL)",
            "journal": "American Diabetes Association",
            "ref": "ADA Standards of Care in Diabetes, edición vigente",
            "url": "https://diabetesjournals.org/care",
            "note": "Definición de prediabetes y recomendaciones de intervención.",
        },
    ],
    "ldl_high": [
        {
            "title": "2018 AHA/ACC Guideline on the Management of Blood Cholesterol",
            "journal": "Circulation, 2019",
            "ref": "Circulation. 2019;139:e1082-e1143. PubMed 30586774",
            "url": "https://pubmed.ncbi.nlm.nih.gov/30586774/",
            "note": "Meta-análisis de estatinas: cada 38 mg/dL (1 mmol/L) de reducción de LDL ≈ 23% menos de eventos CV mayores.",
        },
    ],
    "hdl_low": [
        {
            "title": "Effect of potentially modifiable risk factors associated with myocardial infarction (INTERHEART)",
            "journal": "The Lancet, 2004",
            "ref": "Lancet. 2004;364(9438):937-52. PubMed 15364185",
            "url": "https://pubmed.ncbi.nlm.nih.gov/15364185/",
            "note": "La dislipidemia explicó ~49% del riesgo atribuible poblacional de infarto agudo de miocardio.",
        },
    ],
    "trig_high": [
        {
            "title": "Nonfasting Triglycerides and Risk of Myocardial Infarction",
            "journal": "JAMA, 2007",
            "ref": "Nordestgaard BG et al. JAMA. 2007;298(3):299-308. PubMed 17635890",
            "url": "https://pubmed.ncbi.nlm.nih.gov/17635890/",
            "note": "Estudio de cohortes de Copenhague: triglicéridos elevados asociados a mayor riesgo de infarto.",
        },
        {
            "title": "Triglycerides and the Risk of Pancreatitis",
            "journal": "Revista médica (revisión)",
            "ref": "Triglicéridos >500 mg/dL elevan el riesgo de pancreatitis aguda (criterios de Endocrine Society).",
            "url": "https://www.endocrine.org/clinical-practice-guidelines",
            "note": "Umbral de pancreatitis hipertrigliceridémica.",
        },
    ],
    "obesity_risk": [
        {
            "title": "Body-mass index and cause-specific mortality in 900 000 adults (Prospective Studies Collaboration)",
            "journal": "The Lancet, 2009",
            "ref": "Lancet. 2009;373(9669):1083-96. PubMed 19299006",
            "url": "https://pubmed.ncbi.nlm.nih.gov/19299006/",
            "note": "Meta-análisis de 57 estudios: obesidad reduce esperanza de vida 2-4 años; obesidad severa 8-10 años.",
        },
    ],
    "sedentary_risk": [
        {
            "title": "WHO Guidelines on Physical Activity and Sedentary Behaviour",
            "journal": "Organización Mundial de la Salud, 2020",
            "ref": "OMS 2020. ISBN 978-92-4-001512-8",
            "url": "https://www.who.int/publications/i/item/9789240015128",
            "note": "La inactividad física aumenta la mortalidad por todas las causas; 150 min/semana reducen el riesgo ~30%.",
        },
    ],
    "smoking_risk": [
        {
            "title": "Mortality in relation to smoking: 50 years' observations on male British doctors",
            "journal": "BMJ, 2004",
            "ref": "Doll R et al. BMJ. 2004;328:1519. PubMed 15213107",
            "url": "https://pubmed.ncbi.nlm.nih.gov/15213107/",
            "note": "Fumar acorta la vida ~10 años; dejar antes de los 40 recupera casi toda la expectativa.",
        },
        {
            "title": "Health Effects of Cigarette Smoking",
            "journal": "CDC (Centros para el Control de Enfermedades)",
            "ref": "CDC. ~1 de cada 5 muertes en adultos en EE.UU. se atribuye al tabaquismo.",
            "url": "https://www.cdc.gov/tobacco/data_statistics/fact_sheets/health_effects/effects_cig_smoking/",
            "note": "Carga de mortalidad atribuible al tabaco.",
        },
    ],
    "renal_risk": [
        {
            "title": "KDIGO 2024 Clinical Practice Guideline for the Evaluation and Management of Chronic Kidney Disease",
            "journal": "Kidney International, 2024",
            "ref": "KDIGO CKD Guideline 2024",
            "url": "https://kdigo.org/guidelines/ckd-evaluation-and-management/",
            "note": "Estadificación de ERC por eGFR y albuminuria; progresión y manejo.",
        },
    ],
    "fatty_liver_risk": [
        {
            "title": "Fibrosis Progression in Nonalcoholic Fatty Liver Disease",
            "journal": "Clinical Gastroenterology and Hepatology, 2015",
            "ref": "Singh S et al. Clin Gastroenterol Hepatol. 2015;13(4):643-54. PubMed 24768810",
            "url": "https://pubmed.ncbi.nlm.nih.gov/24768810/",
            "note": "La esteatohepatitis (NASH) progresa a cirrosis en ~20% de los casos en 10-20 años.",
        },
    ],
    "anemia_risk": [
        {
            "title": "Prevalence and causes of anemia (WHO / NHANES)",
            "journal": "Organización Mundial de la Salud",
            "ref": "Criterios OMS: Hb <13 g/dL (hombres), <12 g/dL (mujeres).",
            "url": "https://www.who.int/data/gho/indicator-metadata-registry/imr-details/4814",
            "note": "Umbrales de anemia y carga global.",
        },
    ],
    "gout_risk": [
        {
            "title": "Asymptomatic Hyperuricemia: Risks and Consequences (Normative Aging Study)",
            "journal": "American Journal of Medicine, 1987",
            "ref": "Campion EW et al. Am J Med. 1987;82(3):421-6. PubMed 3826044",
            "url": "https://pubmed.ncbi.nlm.nih.gov/3826044/",
            "note": "Riesgo de gota aumenta con niveles crecientes de ácido úrico.",
        },
    ],
    "thyroid_risk": [
        {
            "title": "2017 ATA Guidelines for Diagnosis and Management of Thyroid Disease During Pregnancy / Hipertiroidismo y fibrilación auricular",
            "journal": "American Thyroid Association / revisión en Circulation",
            "ref": "El hipertiroidismo se asocia a ~3x más riesgo de fibrilación auricular (cohortes publicadas).",
            "url": "https://www.thyroid.org/patient-thyroid-information/guidelines/",
            "note": "Asociación hipertiroidismo-arritmias documentada.",
        },
    ],
    "psa_risk": [
        {
            "title": "Prevalence of Prostate Cancer among Men with a Prostate-Specific Antigen Level ≤4.0 ng/mL",
            "journal": "New England Journal of Medicine, 2004",
            "ref": "Thompson IM et al. NEJM. 2004;350(22):2239-46. PubMed 15470201",
            "url": "https://pubmed.ncbi.nlm.nih.gov/15470201/",
            "note": "Con PSA 3,1-4,0 ng/mL, ~27% tuvieron biopsia positiva. PSA no es 100% específico.",
        },
    ],
    "inflammation_risk": [
        {
            "title": "C-reactive protein and the risk of cardiovascular disease (meta-análisis)",
            "journal": "Circulation / New England Journal of Medicine (CRP como marcador)",
            "ref": "Kaptoge S et al. NEJM. 2010;362:2319-28. PubMed 20505167",
            "url": "https://pubmed.ncbi.nlm.nih.gov/20505167/",
            "note": "PCR elevada de forma persistente como marcador de inflamación.",
        },
    ],
    "troponin_risk": [
        {
            "title": "Fourth Universal Definition of Myocardial Infarction",
            "journal": "European Heart Journal, 2018",
            "ref": "Thygesen K et al. Eur Heart J. 2019;40(3):237-269. PubMed 30165437",
            "url": "https://pubmed.ncbi.nlm.nih.gov/30165437/",
            "note": "La troponina elevada indica daño miocárdico; requiere evaluación inmediata.",
        },
    ],
    "hyperkalemia_risk": [
        {
            "title": "KDOQI / KDIGO Clinical Practice Guidelines (potasio y ERC)",
            "journal": "Kidney Disease: Improving Global Outcomes",
            "ref": "Potasio >5,5 mEq/L: riesgo de arritmias; >6,0: emergencia.",
            "url": "https://kdigo.org/guidelines/",
            "note": "Umbrales de hiperpotasemia clínicamente relevante.",
        },
    ],
    "thrombocytopenia_risk": [
        {
            "title": "Platelet transfusion and management of thrombocytopenia (ASH/BCSH guías)",
            "journal": "American Society of Hematology / British Society for Haematology",
            "ref": "Plaquetas <100.000/µL: riesgo de sangrado; <50.000/µL: riesgo significativo.",
            "url": "https://www.hematology.org/education/patients/bleeding-disorders/thrombocytopenia",
            "note": "Umbrales de trombocitopenia.",
        },
    ],
    "hypertension_risk": [
        {
            "title": "Framingham Heart Study: blood pressure and risk of stroke and MI",
            "journal": "Circulation / Stroke (publicaciones Framingham)",
            "ref": "Hipertensión no tratada duplica el riesgo de ACV (datos Framingham).",
            "url": "https://www.framinghamheartstudy.org/",
            "note": "Riesgo cardiovascular de la hipertensión no controlada.",
        },
    ],
}


def sources_for(stat_key: str) -> list[dict]:
    """Devuelve las fuentes para una clave de estadística (vacío si no hay)."""
    return SOURCES.get(stat_key, [])


def sources_for_finding(finding: dict) -> list[dict]:
    """Mapea un hallazgo a sus fuentes según la clave de estadística."""
    marker = finding.get("marker", {})
    key = marker.get("key", "")
    if key in ("glucose", "hba1c"):
        return sources_for("diabetes_risk")
    if key == "ldl":
        return sources_for("ldl_high")
    if key == "hdl":
        return sources_for("hdl_low")
    if key == "trig":
        return sources_for("trig_high")
    if key in ("creatinine", "urea"):
        return sources_for("renal_risk")
    if key in ("got", "gpt", "alp"):
        return sources_for("fatty_liver_risk")
    if key == "hemoglobin":
        return sources_for("anemia_risk")
    if key == "uric_acid":
        return sources_for("gout_risk")
    if key in ("tsh", "t4_total", "t3_total"):
        return sources_for("thyroid_risk")
    if key == "psa_total":
        return sources_for("psa_risk")
    if key == "crp":
        return sources_for("inflammation_risk")
    if key == "troponin":
        return sources_for("troponin_risk")
    if key == "potassium":
        return sources_for("hyperkalemia_risk")
    if key == "platelets":
        return sources_for("thrombocytopenia_risk")
    return []
