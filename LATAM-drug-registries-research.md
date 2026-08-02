# LATAM Drug Registries — Research Notes

Goal: add BR / AR / UY / CL as secondary sources to the drug catalog, alongside the current single source DINAVISA (Paraguay). This file documents HOW each registry exposes its data, what it contains, and what blocks a clean harvest.

**Date:** 2026-08-02
**Status:** research only, no code written.

---

## Quick comparison

| Country | Agency | Registry / tool | Data access | Clean harvest? |
|---------|--------|-----------------|-------------|----------------|
| BR | ANVISA | `dados.anvisa.gov.br` open data | **CSV snapshot** (full list) | ✅ Easiest of the four |
| AR | ANMAT | Vademécum Nacional (VNM) | SPA app, JSON backend (JS-discovered) | ⚠️ Doable, needs API reverse-engineering |
| UY | MSP (DIGESA) | Listado de Medicamentos | Server-side servlet web app | ⚠️ Geo-blocked from this host; needs server-side harvest or proxy |
| CL | ISP / ANAMED | Registro Sanitario | ASP.NET WebForms (VIEWSTATE POST) | ⚠️ Stateful form scraping |

---

## 🇧🇷 BRAZIL — ANVISA

**Agency:** Agência Nacional de Vigilância Sanitária.

**Best source: Open Data portal (dados.anvisa.gov.br)**
- Full product-registration snapshot CSV: `https://dados.anvisa.gov.br/dados/DADOS_ABERTOS_MEDICAMENTOS.csv`
- Directory listing (all datasets): `https://dados.anvisa.gov.br/dados/`
- Companion docs (same dir, PDF): `Documentacao_e_Dicionario_de_Dados_MEDICAMENTOS.pdf`, `Documentacao_e_Dicionario_de_Dados_Registros_Validos_Medicamento_V1.pdf`
- Snapshot = current state of the registry, no year/month params. Columns include `NOME_PRODUTO`, `PRINCIPIO_ATIVO`, `SITUACAO_REGISTRO` (filter `== "ATIVO"`), `CLASSE_TERAPEUTICA`.
- The R package `healthbR` (`anvisa_data(type = "medicines")`) wraps exactly this CSV.

**Alternative: online consultation portal** (`https://consultas.anvisa.gov.br/#/medicamentos/...`)
- Angular SPA over internal JSON API: `https://consultas.anvisa.gov.br/api/consulta/medicamento/produtos/{numero}`
- Requires browser-like headers (`Authorization: Guest`, UA, Referer).
- **Blocked by WAF (Cloudflare + Dynatrace) → 403 for plain HTTP clients.** Open-data CSV is the way around it.

**Notes for ingestion:** CSV download is trivial (like DINAVISA's paginated DataTables but simpler — one file). Watch the column semantics (`PRINCIPIO_ATIVO` may be multi-`+` / multi-ingredient) and the `SITUACAO_REGISTRO` filter. File is big (full BR registry).

---

## 🇦🇷 ARGENTINA — ANMAT

**Agency:** Administración Nacional de Medicamentos, Alimentos y Tecnología Médica.

**Public tool: Vademécum Nacional de Medicamentos (VNM)**
- App: `http://anmatvademecum.servicios.pami.org.ar/index.html` (hosted by PAMI, linked from `argentina.gob.ar/anmat/regulados/medicamentos`)
- Legacy jQuery SPA (jquery 1.5.2). Data backend must be discovered by reading the app's JS — likely a JSON endpoint behind it.
- Searchable by: trade name, IFA (active ingredient), registration certificate number, lab.

**Other surface:** `http://www.anmat.gob.ar/aplicaciones_net/applications/consultas/legajo_electronico/index.html` — registry-certificate lookup (not a bulk list).

**No open-data API found.** No `datos.gob.ar` dataset surfaced in searches.

**Notes for ingestion:** Needs one-time reverse-engineering of the VNM SPA to find its JSON API. Certificate-based registry (RNEM) is the authoritative list but the bulk access path is the VNM search. Medium effort.

---

## 🇺🇾 URUGUAY — MSP (DIGESA)

**Note on naming:** the agency is the MSP / DIGESA — the *listado* tool is often referred to as AViSU in day-to-day usage, but the registry authority is the Ministerio de Salud Pública.

**Official tool: Listado de Medicamentos (public access)**
- URL: `https://listadomedicamentos.msp.gub.uy/ListadoMedicamentos/servlet/com.listadomedicamentos.listadomedicamentos`
- Confirmed by MSP official response to a public-information request (Ref. N.° 12/001/3/4468/2025): this is the sanctioned public list of registered + marketed medicines.
- Search by: brand, active principle, dose, responsible laboratory (UY), condition of sale.
- Server-side servlet (Java) app → HTML forms, not a clean API.

**Access blocker:** `listadomedicamentos.msp.gub.uy` **unreachable from this machine (geo/IP block, connection refused / HTTP 000)** while `www.gub.uy` works fine. Harvest must run from a host that can reach it (e.g. one of the Oracle VMs, São Paulo or London), or be done once and snapshotted.

**Notes for ingestion:** Characterize the servlet's form (POST params, pagination) from a reachable host first. The URL contains `/servlet/com.listadomedicamentos.listadomedicamentos` — classic Java servlet mapping; likely a form POST with page params. Once the request shape is known it behaves like DINAVISA's DataTables pattern.

---

## 🇨🇱 CHILE — ISP / ANAMED

**Agency:** Instituto de Salud Pública; medicines branch = **Agencia Nacional de Medicamentos (ANAMED)**.

**Public tool: Registro Sanitario — product search**
- URL: `https://registrosanitario.ispch.gob.cl/` (link "Busca aqui Productos Registrados" on `ispch.gob.cl`)
- **ASP.NET WebForms** app:
  - Hidden state: `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION` (all required on POST)
  - Filter checkboxes: `ctl00$ContentPlaceHolder1$chkTipoBusqueda$0..6` (product types; one is medicines)
  - Status dropdown: `ctl00$ContentPlaceHolder1$ddlEstado`
  - Submit: `btnBuscar` (with client validation `validoSeleccion()`)
- Results in an HTML grid (server-side paging, standard WebForms gridview pattern).
- No public API. Post-2025 IT-contingency; the online consultation system was restored ~Aug 2025.

**Notes for ingestion:** Classic stateful form scraping — GET the page, parse `__VIEWSTATE`/`__EVENTVALIDATION`, POST search, iterate result pages. Heavier than a CSV or JSON API but mechanical. Requires selecting the medicines checkbox and an "activo/vigente" status filter.

---

## Harvest results (2026-08-02, second pass)

| Country | Outcome | Blocking issue |
|---------|---------|----------------|
| 🇧🇷 ANVISA | ✅ **Harvested** — `DADOS_ABERTOS_MEDICAMENTOS.csv` (8.3 MB, latin-1, `;`-delimited). 2445 principles / 6657 brands merged into `app/drug_catalog.json` with CIMA dose overlay + curated PY/incretin extras. | none |
| 🇦🇷 ANMAT | ⚠️ **Not bulk-harvestable.** `medicamentos.asp` 302→argentina.gob.ar (dead). `datos.gob.ar` only has *incremental* monthly VNM CSVs (the 2018 CSV is 60 rows of updates, not a snapshot). Live VNM is a ZK 7 SPA at `servicios.pami.org.ar/vademecum` (AU-engine, needs reverse-engineering). | no full snapshot available |
| 🇨🇱 ISP | ⚠️ **Broken server-side.** GET works, but search POST returns **HTTP 500** (tested from VM2 with full `__VIEWSTATE`/`__EVENTVALIDATION`; `txtPrincipio` search for losartan → 500). Post-2025 IT-contingency app is effectively down for automation. | app 500 on search |
| 🇺🇾 MSP | ⚠️ **Unreachable.** `listadomedicamentos.msp.gub.uy` times out from this machine AND from VM2 São Paulo (memory: VM1/VM3 too). Not geo-IP on the local host; likely blocks all foreign hosts. | network-level block |

**Net:** ANVISA is the single reliably bulk-harvestable LATAM registry and is now in the catalog. AR/CL/UY need either manual browser harvest, a data request to the agency, or (for AR) reverse-engineering the ZK app. The app's manual medication entry covers any drug these lists would add.

## Recommended path forward (next session)

1. **ANVISA (BR)** — first: direct CSV download, simplest win. Map to existing catalog schema (`generic + brand + strengths`). Reuse existing `_norm`, `STRENGTH_RE`, `clean_brand` helpers.
2. **ANMAT (AR)** — read VNM SPA JS to find its JSON API; one quick probe decides if it's clean.
3. **Uruguay (UY)** — run harvest from a reachable VM (VM2 São Paulo or VM3 London); document servlet POST shape there.
4. **Chile (CL)** — WebForms session scraper; needs cookie+viewstate handling.

Design question for later: keep one catalog JSON with a `source` field per entry (DINAVISA / ANVISA / ANMAT / MSP-UY / ISP-CL) so brands and dose lists stay attributed and mergeable.

---

## Sources

- ANVISA open data portal: https://dados.anvisa.gov.br/dados/
- healthbR (R) ANVISA access: https://cran.r-project.org/web/packages/healthbR/vignettes/anvisa-health-surveillance.html
- ANVISA portal API discussion: https://stackoverflow.com/questions/... (consultas.anvisa.gov.br JSON API)
- ANVISA scrapers (Apify, for reference): https://apify.com/parseforge/anvisa-brazil-medicines-scraper, https://apify.com/david_craft/anvisa-raw-material-scraper
- ANMAT medicines section: https://www.argentina.gob.ar/anmat/regulados/medicamentos
- ANMAT VNM app: http://anmatvademecum.servicios.pami.org.ar/index.html
- ANMAT VNM creation doc: http://www.anmat.gov.ar/comunicados/Vademecum_Nacional_de_Medicamentos.pdf
- Uruguay MSP listado (official, via AIA response): https://listadomedicamentos.msp.gub.uy/ListadoMedicamentos/servlet/com.listadomedicamentos.listadomedicamentos
- Uruguay MSP portal: https://www.gub.uy/ministerio-salud-publica/home
- Chile ISP registros: https://registrosanitario.ispch.gob.cl/
- Chile ISP portal: https://www.ispch.gob.cl/
