/* Panel de Laboratorio Clínico — lógica de la interfaz */
"use strict";

const state = {
  persons: [],
  current: null,       // person id
  detail: null,        // /api/person/{id} response
  charts: {},
  medAutocomplete: [],
};

const $ = (sel) => document.querySelector(sel);

/* ---------------- helpers ---------------- */

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString("es-PY", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function fmtNum(v, unit) {
  if (v === null || v === undefined || v === "" || isNaN(Number(v))) return "—";
  const u = (unit || "").replace(/\u03BC/g, "\u00B5");
  const s = Number(v).toLocaleString("es-PY", { maximumFractionDigits: 3 });
  return u ? `${s} ${u}` : s;
}

function sevClass(sev) {
  return sev === "red" ? "sev-border-red" : sev === "yellow" ? "sev-border-yellow" : sev === "green" ? "sev-border-green" : "sev-border-blue";
}
function sevChip(sev, label) {
  const cls = sev === "red" ? "sev-red" : sev === "yellow" ? "sev-yellow" : sev === "green" ? "sev-green" : "sev-none";
  return `<span class="sev-chip ${cls}">${label || sev || "sin datos"}</span>`;
}

function toast(msg, type = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show " + type;
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 4000);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- API ---------------- */

async function api(url, opts) {
  opts = opts || {};
  opts.credentials = opts.credentials || "same-origin";
  const res = await fetch(url, opts);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("Sesión expirada");
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/* ---------------- carga inicial ---------------- */

async function loadPersons() {
  try {
    state.persons = await api("/api/persons");
    renderPersonList();
    const st = await api("/api/status");
    const exists = st.exists ? `📂 ${esc(st.lab_folder)}` : `⚠️ Carpeta no encontrada: ${esc(st.lab_folder)}`;
    $("#folderInfo").textContent = exists;
    if (st.last_scan && st.last_scan.ts) {
      const n = st.last_scan.new_reports || 0;
      $("#scanResult").textContent = `Último escaneo: ${new Date(st.last_scan.ts * 1000).toLocaleTimeString("es-PY")}` +
        (n ? ` · ${n} informe(s) nuevos` : "");
    }
  } catch (e) {
    toast("Error al cargar datos: " + e.message, "red");
  }
}

function renderPersonList() {
  const el = $("#personList");
  if (!state.persons.length) {
    el.innerHTML = `<h3>Personas</h3><div style="padding:12px;color:#888">Sin datos aún. Escanee la carpeta.</div>`;
    return;
  }
  const items = state.persons.map((p) => {
    const active = state.current === p.id ? "active" : "";
    const badge = p.last_report
      ? `<span class="p-badge sev-green">${p.n_reports} informes · ${fmtDate(p.last_report)}</span>`
      : `<span class="p-badge sev-none">sin informes</span>`;
    return `<div class="person-item ${active}" data-id="${p.id}" onclick="selectPerson(${p.id})">
      <div class="p-name">${esc(p.name)}</div>
      <div class="p-meta">${p.sex ? "Sexo: " + (p.sex === "M" ? "M" : "F") + " · " : ""}${p.age ? "Edad: " + p.age + " años" : ""}${p.doc ? " · Doc: " + esc(p.doc) : ""}</div>
      ${badge}
    </div>`;
  }).join("");
  el.innerHTML = `<h3>Personas (${state.persons.length})</h3>` + items;
}

async function selectPerson(id) {
  state.current = id;
  renderPersonList();
  $("#personPanel").innerHTML = `<div class="empty-state"><p>Cargando…</p></div>`;
  const token = id;   // guard for race condition
  try {
    const detail = await api(`/api/person/${id}`);
    if (state.current !== token) return;  // stale request
    state.detail = detail;
    renderPerson();
  } catch (e) {
    if (state.current !== token) return;
    $("#personPanel").innerHTML = `<div class="empty-state"><h2>Error</h2><p>${esc(e.message)}</p></div>`;
  }
}

/* ---------------- rescan ---------------- */

async function rescanFolder(silent = false) {
  if (!silent) {
    const btn = $("#btnRescan");
    btn.disabled = true;
    btn.textContent = "⏳ Escaneando…";
    $("#scanResult").textContent = "";
  }
  try {
    const r = await api("/api/rescan", { method: "POST" });
    const dupMsg = r.duplicates_removed ? ` · ${r.duplicates_removed} duplicado(s) eliminado(s)` : "";
    const errMsg = r.errors && r.errors.length ? ` · ⚠️ ${r.errors.length} error(es)` : "";
    const msg = `${r.checked} archivo(s) revisado(s) · ${r.new_reports} nuevo(s)${dupMsg}${errMsg}`;
    $("#scanResult").textContent = msg;
    if (silent) return;
    if (r.new_reports > 0) {
      toast(`${r.new_reports} informe(s) nuevo(s) ingerido(s)`, "green");
    } else if (r.errors && r.errors.length) {
      toast("Errores: " + r.errors[0], "yellow");
    } else {
      toast("Sin archivos nuevos.", "green");
    }
    await loadPersons();
    if (state.current) {
      state.detail = await api(`/api/person/${state.current}`);
      renderPerson();
    }
  } catch (e) {
    if (!silent) toast("Error al escanear: " + e.message, "red");
  } finally {
    if (!silent) {
      const btn = $("#btnRescan");
      btn.disabled = false;
      btn.textContent = "🔄 Buscar archivos nuevos";
    }
  }
}

/* ---------------- render persona ---------------- */

function renderPerson() {
  const d = state.detail;
  const p = d.person;
  const a = d.assessment;
  const panel = $("#personPanel");

  const sevChips = [
    a.summary.n_red ? sevChip("red", `${a.summary.n_red} crítico(s)`) : "",
    a.summary.n_yellow ? sevChip("yellow", `${a.summary.n_yellow} precaución`) : "",
    a.summary.n_systems_altered ? sevChip("green", `${a.summary.n_systems_altered} sistema(s) revisado(s)`) : "",
  ].join("");

  panel.innerHTML = `
  <div class="person-header">
    <div>
      <h2>${esc(p.name)}</h2>
      <div class="meta">
        ${p.sex ? "Sexo: " + (p.sex === "M" ? "Masculino" : "Femenino") + " · " : ""}
        ${p.age ? "Edad: " + p.age + " años · " : ""}
        ${p.doc ? "Doc: " + esc(p.doc) + " · " : ""}
        ${p.n_reports} informe(s) · ${p.n_tests} análisis en total
      </div>
      <div class="severity-strip">${sevChips}</div>
    </div>
    <div class="header-actions">
      <button class="sev-chip sev-red" onclick="document.getElementById('disclaimer').scrollIntoView({behavior:'smooth'})">⚠️ Ver advertencia</button>
    </div>
  </div>

  <div class="card" id="uploadCard">
    <div class="card-header">📤 Subir estudio médico</div>
    <div class="card-body">
      <form class="med-form" onsubmit="return uploadReport(event)">
        <input type="file" id="uploadFile" accept=".pdf,.jpg,.jpeg,.png,.gif,.webp,.bmp,.tif,.tiff,.dcm,.dicom,.zip,.doc,.docx" style="grid-column: span 2;">
        <input id="uploadNote" placeholder="Nota (ej: Radiografía de tórax, IRM rodilla…)" style="grid-column: span 2;">
        <button type="submit" style="grid-column: span 2;">Subir y analizar</button>
      </form>
      <div class="med-form" style="margin-top:8px">
        <input type="file" id="uploadFolder" webkitdirectory directory multiple style="grid-column: span 2;">
        <button type="button" onclick="uploadFolder(event)" style="grid-column: span 2;background:var(--gray)">📁 Subir carpeta (DICOM, con subcarpetas)</button>
      </div>
      <form class="med-form" style="margin-top:8px" onsubmit="return fetchDicomLibrary(event)">
        <input type="text" id="dicomLink" placeholder="Link de dicomlibrary.com (…/?study=…)" style="grid-column: span 2;">
        <button type="submit" style="grid-column: span 2;background:#6a1b9a">🌐 Importar desde dicomlibrary.com</button>
      </form>
      <div class="med-hint">Acepta <strong>cualquier tipo de estudio</strong>: PDF de
      laboratorio (se ingesta al historial), imágenes (RX, IRM, ecografías),
      <strong>carpetas DICOM completas</strong> (con subcarpetas o ZIP), y links de
      dicomlibrary.com. Todo queda adjunto en esta pestaña del paciente.</div>
      <div id="uploadResult"></div>
    </div>
  </div>

  <div class="card" id="docsCard">
    <div class="card-header">🖼️ Estudios e imágenes adjuntos</div>
    <div class="card-body" id="docsBody">
      <p style="color:var(--muted)">Cargando…</p>
    </div>
  </div>

  <div class="card" id="medsCard">
    <div class="card-header">💊 Medicamentos del paciente</div>
    <div class="card-body">
      ${renderMedsForm()}
      ${renderDrugChecks(d)}
      <div class="med-hint">Los medicamentos se consideran en las recomendaciones:
      se marcan posibles interacciones entre fármacos y efectos de cada medicamento
      sobre los resultados de laboratorio (por ejemplo, estatinas y transaminasas,
      diuréticos y potasio, biotina y TSH).</div>
    </div>
  </div>

  <div class="card" id="aiCard">
    <div class="card-header">🧠 Informe médico con IA</div>
    <div class="card-body">
      <div class="med-form">
        <label style="display:flex;align-items:center;gap:6px;font-weight:600">
          Modelo de IA:
          <select id="aiModel" style="padding:8px;border:1px solid var(--border);border-radius:6px">
            <option value="deepseek">DeepSeek V4 Pro</option>
            <option value="opus">Opus 4.8</option>
          </select>
        </label>
        <button onclick="generateAIReport()" style="background:var(--blue);color:#fff;border:none;border-radius:6px;padding:8px 16px;font-weight:600;cursor:pointer">✨ Generar informe IA</button>
        <button onclick="generateAIReport(true)" style="background:var(--gray);color:#fff;border:none;border-radius:6px;padding:8px 16px;font-weight:600;cursor:pointer">🔄 Regenerar (fuerza)</button>
      </div>
      <div class="med-hint">DeepSeek V4 Pro es el modelo por defecto. Si no queda
      conforme con el informe, puede regenerarlo con <strong>Opus 4.8</strong>.</div>
      <div id="aiResult" style="margin-top:12px"></div>
    </div>
  </div>

  <div class="card summary-card tone-${esc(a.summary.tone)}">
    <div class="card-header">📋 Resumen ejecutivo</div>
    <div class="card-body"><p>${esc(a.summary.text)}</p></div>
  </div>

  <div class="card">
    <div class="card-header">📊 Tablas comparativas (evolución por mes/año)</div>
    <div class="card-body">
      ${renderTables(d)}
    </div>
  </div>

  <div class="card">
    <div class="card-header">📈 Evolución temporal (gráficos)</div>
    <div class="card-body">
      ${renderCharts(a)}
    </div>
  </div>

  <div class="card summary-card tone-${esc(a.summary.tone)}" style="display:none"></div>


  <div class="card">
    <div class="card-header">🔴 Hallazgos anormales (por severidad)</div>
    <div class="card-body">
      ${a.findings.length ? a.findings.map(renderFinding).join("") : `<p style="color:var(--green);font-weight:600">No se detectaron valores fuera de rango en la última evaluación.</p>`}
    </div>
  </div>

  <div class="card">
    <div class="card-header">🩺 Evaluación por sistemas</div>
    <div class="card-body">
      ${a.systems.map(renderSystem).join("")}
    </div>
  </div>

  <div class="card">
    <div class="card-header">✅ Recomendaciones y próximos pasos (por urgencia)</div>
    <div class="card-body">
      ${a.recommendations.map(renderRec).join("")}
      <div class="legend-note legend-dots">
        <span style="background:var(--red)"></span> urgente / alto riesgo
        <span style="background:var(--yellow)"></span> precaución / seguimiento
        <span style="background:var(--green)"></span> hábitos saludables
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-header">🗂 Historial de informes</div>
    <div class="card-body">
      ${d.reports.map((r) => `
        <div class="report-item">
          <div class="r-lab">${esc(r.lab)} ${r.order_code ? "· Orden " + esc(r.order_code) : ""}</div>
          <div class="r-date">${fmtDate(r.date)} · ${esc(r.source_file)}</div>
          <div class="r-sections">${esc(r.sections || "")}</div>
        </div>`).join("")}
    </div>
  </div>

  <div class="card" id="disclaimer">
    <div class="card-header">⚠️ Advertencia médica</div>
    <div class="card-body">
      <p><strong>Este panel es generado por inteligencia artificial y NO reemplaza el
      diagnóstico ni el tratamiento de un médico.</strong></p>
      <p style="margin-top:8px">Los resultados de laboratorio deben ser interpretados por
      un profesional de la salud calificado. Las recomendaciones y estadísticas citadas
      provienen de estudios y guías publicadas, pero cada caso es individual. No modifique,
      suspenda o inicie ningún medicamento sin consultar a su médico.</p>
      <p style="margin-top:8px;color:var(--red);font-weight:600">Si presenta dolor de pecho,
      dificultad para respirar, sangrado, debilidad súbita, confusión, fiebre alta o
      cualquier síntoma grave, <strong>acuda a urgencias de inmediato</strong>.</p>
    </div>
  </div>`;

  // charts después de insertar el HTML
  renderChartsInit(a);
  // documentos (carpetas requieren fetch del listado)
  renderDocuments(d).then((html) => {
    const body = document.getElementById("docsBody");
    if (body) body.innerHTML = html;
  }).catch(() => {
    const body = document.getElementById("docsBody");
    if (body) body.innerHTML = `<p style="color:var(--muted)">No se pudieron cargar los documentos.</p>`;
  });
  // auto-cargar informe IA guardado (silent, no bloquea)
  autoLoadAIReport();
}

/* ---------------- medicamentos ---------------- */

function renderMedsForm() {
  return `
  <form class="med-form" onsubmit="return addMed(event)">
    <div class="autocomplete">
      <input id="medName" list="" placeholder="Nombre del medicamento (ej: metformina)" autocomplete="off"
             oninput="medAutocomplete()" onfocus="medAutocomplete()">
      <div id="medAutoList" class="autocomplete-list"></div>
    </div>
    <input id="medDose" placeholder="Dosis (ej: 500 mg)">
    <input id="medFreq" placeholder="Frecuencia (ej: 2×/día)">
    <button type="submit">Agregar</button>
  </form>
  <div class="med-list">
    ${state.detail.meds.map((m) => `
      <div class="med-item">
        <div>
          <div class="med-name">${esc(m.name)}</div>
          <div class="med-detail">${[m.dose, m.frequency, m.notes].filter(Boolean).join(" · ")}</div>
        </div>
        <button onclick="delMed(${m.id})" title="Eliminar">🗑</button>
      </div>`).join("") || `<div style="color:var(--muted)">No hay medicamentos registrados.</div>`}
  </div>`;
}

let _medSearchTimer = null;
async function medAutocomplete() {
  const q = $("#medName").value.trim();
  const list = $("#medAutoList");
  if (q.length < 2) { list.innerHTML = ""; state.medAutocomplete = []; return; }
  clearTimeout(_medSearchTimer);
  _medSearchTimer = setTimeout(async () => {
    try {
      const res = await api("/api/drugs/search?q=" + encodeURIComponent(q));
      state.medAutocomplete = res;
      if (!res.length) { list.innerHTML = ""; return; }
      list.innerHTML = res.map((d, i) => {
        // marcas: destacar la que coincidió con la búsqueda
        const brands = (d.brands || []).slice(0, 5);
        if (d.matched_brand && !brands.includes(d.matched_brand)) brands.unshift(d.matched_brand);
        const brandHtml = brands.length
          ? `<span class="ac-brands">${brands.map((b) =>
              b === d.matched_brand ? `<b>${esc(b)}</b>` : esc(b)).join(" · ")}</span>`
          : "";
        const doses = (d.strengths || []).map((s, j) =>
          `<button type="button" class="ac-dose" onclick="pickMedDose(${i},${j})">${esc(s)}</button>`
        ).join("");
        return `<div class="ac-row">
          <div class="ac-main" onclick="pickMed(${i})">
            <span class="ac-generic">${esc(d.generic)}</span>
            ${brandHtml}
          </div>
          ${doses ? `<div class="ac-doses">${doses}</div>` : ""}
        </div>`;
      }).join("");
    } catch (e) { /* silencio */ }
  }, 130);
}

// seleccionar solo el nombre (genérico normalizado); la dosis queda editable
function pickMed(i) {
  const d = state.medAutocomplete[i];
  if (!d) return;
  $("#medName").value = d.generic;
  $("#medAutoList").innerHTML = "";
  const dose = $("#medDose");
  if (dose) dose.focus();
}

// seleccionar nombre + dosis real; la dosis sigue siendo editable a mano
function pickMedDose(i, j) {
  const d = state.medAutocomplete[i];
  if (!d) return;
  $("#medName").value = d.generic;
  const dose = $("#medDose");
  if (dose) dose.value = (d.strengths && d.strengths[j]) || "";
  $("#medAutoList").innerHTML = "";
  if (dose) dose.focus();
}

async function addMed(ev) {
  ev.preventDefault();
  const name = $("#medName").value.trim();
  if (!name) { toast("Escriba un medicamento", "yellow"); return; }
  try {
    await api(`/api/person/${state.current}/meds`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        dose: $("#medDose").value.trim(),
        frequency: $("#medFreq").value.trim(),
        notes: "",
      }),
    });
    toast("Medicamento agregado", "green");
    state.detail = await api(`/api/person/${state.current}`);
    renderPerson();
  } catch (e) {
    toast("Error: " + e.message, "red");
  }
  return false;
}

async function delMed(mid) {
  if (!confirm("¿Eliminar este medicamento?")) return;
  try {
    await api(`/api/person/${state.current}/meds/${mid}`, { method: "DELETE" });
    state.detail = await api(`/api/person/${state.current}`);
    renderPerson();
  } catch (e) {
    toast("Error: " + e.message, "red");
  }
}

function renderDrugChecks(d) {
  const dc = d.assessment.drug_checks;
  if (!dc || (!dc.drug_lab.length && !dc.drug_drug.length && !dc.unknown_meds.length)) {
    return "";
  }
  let html = `<div class="drug-warning" style="margin-top:10px">`;
  html += `<div class="d-title" style="font-weight:700;margin-bottom:6px">💊 Análisis de interacciones</div>`;
  if (dc.drug_drug.length) {
    for (const inter of dc.drug_drug) {
      const cls = inter.severity === "red" ? "sev-border-red" : inter.severity === "yellow" ? "sev-border-yellow" : "sev-border-green";
      html += `<div class="drug-warning ${cls}">
        <div class="d-title">${inter.severity === "red" ? "🔴" : inter.severity === "yellow" ? "🟡" : "🟢"} Interacción: ${esc(inter.drugs)}</div>
        <div>${esc(inter.message)}</div>
      </div>`;
    }
  }
  if (dc.drug_lab.length) {
    for (const f of dc.drug_lab) {
      const cls = f.severity === "red" ? "sev-border-red" : f.severity === "yellow" ? "sev-border-yellow" : "sev-border-green";
      html += `<div class="drug-warning ${cls}">
        <div class="d-title">${f.severity === "red" ? "🔴" : f.severity === "yellow" ? "🟡" : "🟢"} ${esc(f.drug)} ↔ laboratorio (${esc(f.test)})</div>
        <div>${esc(f.message)}</div>
      </div>`;
    }
  }
  if (dc.unknown_meds.length) {
    html += `<div class="drug-warning sev-border-yellow">
      <div class="d-title">🟡 Medicamento(s) sin datos de interacción en la base: ${dc.unknown_meds.map(esc).join(", ")}</div>
      <div>Se recomienda consultar con farmacéutico o médico.</div>
    </div>`;
  }
  html += `</div>`;
  return html;
}

/* ---------------- hallazgos ---------------- */

function renderFinding(f) {
  const m = f.marker;
  const srcHtml = f.sources && f.sources.length ? renderSources(f.sources) : "";
  const dateNote = f.date ? ` · medido el ${fmtDate(f.date)}` : "";
  const agedBadge = f.aged ? `<span class="aged-badge">⏳ hace más de 12 meses — repetir</span>` : "";
  const icon = f.severity === "red" ? "🔴" : f.aged ? "⏳" : "🟡";
  return `
  <div class="find-item ${sevClass(f.severity)}">
    <div class="f-title">${icon} ${esc(m.label)} — ${m.status === "alto" ? "ALTO" : "BAJO"}
      (${fmtNum(m.value, m.unit)}) ${agedBadge}</div>
    <div>${esc(m.text)}</div>
    ${rangeMeter(m)}
    <div style="margin-top:4px;font-size:12px;color:var(--gray)">Estado según la última medición${dateNote} · ${m.n_measurements} medicion(es)</div>
    ${f.aged ? `<div style="margin-top:4px;font-size:12.5px;color:var(--yellow);font-weight:600">⏳ Este resultado es de un análisis anterior (más de 12 meses). El estado actual puede haber cambiado: conviene repetir el análisis para confirmar.</div>` : ""}
    ${f.stat ? `<div class="rec-stat">📚 ${esc(f.stat)}</div>` : ""}
    ${srcHtml}
  </div>`;
}

function trendLabel(m) {
  if (m.trend === "subiendo") return `📈 subiendo (última: ${fmtDate(m.last_date)})`;
  if (m.trend === "bajando") return `📉 bajando (última: ${fmtDate(m.last_date)})`;
  return `➡️ estable (última: ${fmtDate(m.last_date)})`;
}

function renderSystem(s) {
  const cls = s.severity === "red" ? "sev-border-red" : s.severity === "yellow" ? "sev-border-yellow" : s.status === "normal" ? "sev-border-green" : "sev-border-blue";
  const icon = s.severity === "red" ? "🔴" : s.severity === "yellow" ? "🟡" : "🟢";
  return `
  <div class="sys-item ${cls}">
    <div class="s-name">${icon} ${esc(s.system)}</div>
    <div class="s-text">${esc(s.text)}</div>
    ${s.details.length ? `<div class="s-text" style="font-size:12px;color:var(--muted)">${s.details.map(esc).join(" · ")}</div>` : ""}
  </div>`;
}

function renderRec(r) {
  const srcHtml = r.sources && r.sources.length ? renderSources(r.sources) : "";
  return `
  <div class="rec-item ${sevClass(r.severity)}">
    <span class="r-urgency">${esc(r.urgency_text || r.severity)}</span>
    <div class="r-title">${r.severity === "red" ? "🔴" : r.severity === "yellow" ? "🟡" : "🟢"} ${esc(r.title)}</div>
    <div>${esc(r.body)}</div>
    ${r.action ? `<div class="r-action">➡️ ${esc(r.action)}</div>` : ""}
    ${srcHtml}
  </div>`;
}

function renderSources(sources) {
  return `<div class="sources"><details>
    <summary>📚 Fuentes / evidencia científica (${sources.length})</summary>
    ${sources.map((s) => `
      <div class="src-item">
        <div class="src-title">${esc(s.title)}</div>
        <div class="src-journal">${esc(s.journal)}</div>
        <div class="src-ref">${esc(s.ref)}</div>
        ${s.note ? `<div class="src-ref">${esc(s.note)}</div>` : ""}
        ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">Ver fuente ↗</a>` : ""}
      </div>`).join("")}
  </details></div>`;
}

/* ---------------- gráficos ---------------- */

function renderCharts(a) {
  // elegir biomarcadores con ≥2 mediciones
  const chartKeys = Object.keys(a.series)
    .filter((k) => a.series[k].length >= 2 && a.series[k].some((t) => t.value != null))
    .slice(0, 8);
  if (!chartKeys.length) {
    return `<p style="color:var(--muted)">Se necesitan al menos 2 mediciones del mismo análisis para graficar la evolución.</p>`;
  }
  return `<div class="chart-grid" id="chartGrid"></div>`;
}

function renderChartsInit(a) {
  const keys = Object.keys(a.series)
    .filter((k) => a.series[k].length >= 2 && a.series[k].some((t) => t.value != null))
    .slice(0, 8);
  const grid = $("#chartGrid");
  if (!grid) return;
  // destroy previous chart instances to free memory
  Object.values(state.charts).forEach((c) => c.destroy());
  state.charts = {};
  grid.innerHTML = keys.map((k, i) => `<div class="chart-box"><h4>${esc(a.series[k][0].name || k)}</h4><canvas id="chart_${i}"></canvas></div>`).join("");
  keys.forEach((k, i) => {
    state.charts[k] = drawChart(`chart_${i}`, k, a.series[k]);
  });
}

function drawChart(canvasId, key, pts) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  const labels = pts.map((t) => fmtDate(t.date));
  const values = pts.map((t) => t.value);
  // rango de referencia (último)
  const last = pts[pts.length - 1];
  let low = last.ref_low, high = last.ref_high;
  const unit = last.unit || "";
  const allVals = values.filter((v) => v != null);
  const ymin = Math.min(low ?? Math.min(...allVals) * 0.9, Math.min(...allVals) * 0.9);
  const ymax = Math.max(high ?? Math.max(...allVals) * 1.1, Math.max(...allVals) * 1.1);

  const datasets = [{
    label: "Valor",
    data: values,
    borderColor: "#1565c0",
    backgroundColor: "rgba(21,101,192,0.1)",
    fill: false,
    tension: 0.25,
    pointRadius: 4,
    spanGaps: true,
  }];
  // bandas de referencia
  const bandData = values.map(() => (high != null ? high : null));
  if (low != null || high != null) {
    if (low != null) {
      datasets.push({ label: "Límite bajo", data: values.map(() => low), borderColor: "#90caf9", borderDash: [5, 5], pointRadius: 0, fill: false });
    }
    if (high != null) {
      datasets.push({ label: "Límite alto", data: values.map(() => high), borderColor: "#e57373", borderDash: [5, 5], pointRadius: 0, fill: false });
    }
  }
  return new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: datasets.length > 1, labels: { font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y} ${unit}`,
          },
        },
      },
      scales: {
        y: { suggestedMin: ymin, suggestedMax: ymax, title: { display: !!unit, text: unit } },
        x: { ticks: { maxRotation: 45, font: { size: 10 } } },
      },
    },
  });
}

/* ---------------- tablas ---------------- */

function monthKey(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d)) return "";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(key) {
  const [y, m] = key.split("-");
  const months = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  return `${months[parseInt(m, 10) - 1]}/${y}`;
}

function renderTables(d) {
  const tests = d.assessment.markers;
  const series = state.detail.assessment.series || {};

  // ---- tabla: evolución por mes/año ----
  // agrupar todas las mediciones por (análisis, mes/año); última del mes
  const byMonth = {};   // "YYYY-MM" -> { canonical -> {value, unit, flag, date} }
  const monthOrder = [];
  for (const [key, pts] of Object.entries(series)) {
    if (!pts.length) continue;
    for (const t of pts) {
      const mk = monthKey(t.date);
      if (!mk) continue;
      if (!byMonth[mk]) { byMonth[mk] = {}; monthOrder.push(mk); }
      // conservar la última medición del mes
      byMonth[mk][key] = {
        value: t.value,
        unit: t.unit,
        flag: t.flag,
        date: t.date,
        qual: t.qual,
      };
    }
  }
  monthOrder.sort();

  // filas: solo análisis con al menos una medición numérica
  const rows = tests
    .filter((m) => m.value != null)
    .sort((a, b) => a.label.localeCompare(b.label));

  const t2head = monthOrder.map((mk) => `<th>${monthLabel(mk)}</th>`).join("");
  const t2rows = rows.map((m) => {
    const cells = monthOrder.map((mk) => {
      const cell = byMonth[mk] && byMonth[mk][m.key];
      if (!cell) return `<td class="num"></td>`;  // no se realizó ese mes
      if (cell.value == null) return `<td class="num"></td>`;
      const cls = cell.flag === "H" ? "val-abnormal-H" : cell.flag === "L" ? "val-abnormal-L" : "";
      return `<td class="num ${cls}" title="${esc(cell.date || "")}">${fmtNum(cell.value, cell.unit)}</td>`;
    }).join("");
    return `<tr><td>${esc(m.label)}</td>${cells}</tr>`;
  }).join("");

  return `
  <div class="table-wrap">
    <table>
      <thead><tr><th>Análisis</th>${t2head}</tr></thead>
      <tbody>${t2rows}</tbody>
    </table>
  </div>
  <div class="med-hint">Cada columna es un mes/año. Si un análisis no se realizó ese
  mes, la celda queda vacía. Colores: <span class="flag-H">rojo = alto</span>,
  <span class="flag-L">amarillo = bajo</span>. El estado actual y el rango de referencia
  de cada valor anormal se muestran en “Hallazgos anormales”.</div>`;
}

/* medidor de rango de referencia — elemento firma visual.
   Coloca el valor del paciente sobre la banda normal (mapeada al centro
   18%–82% del riel, coincidiendo con las zonas de color del CSS). */
function rangeMeter(m) {
  const lo = m.ref_low, hi = m.ref_high, v = m.value;
  if (lo == null || hi == null || v == null || hi <= lo) return "";
  // 18% = ref_low, 82% = ref_high → 64% de ancho para la banda normal
  let pct = 18 + ((v - lo) / (hi - lo)) * 64;
  if (pct < 2) pct = 2;
  if (pct > 98) pct = 98;
  const tick = m.status === "alto" ? "tick-H" : m.status === "bajo" ? "tick-L" : "tick-N";
  return `<div class="range-meter" title="Valor sobre el rango de referencia">
    <div class="range-track"><span class="range-zone-normal"></span><span class="range-tick ${tick}" style="left:${pct.toFixed(1)}%"></span></div>
    <div class="meter-caption"><span>${fmtNum(lo, "")}</span><span>${fmtNum(hi, "")}</span></div>
  </div>`;
}

/* ---------------- IA ---------------- */

async function autoLoadAIReport() {
  const model = $("#aiModel") ? $("#aiModel").value : "deepseek";
  const box = $("#aiResult");
  if (!box) return;
  box.innerHTML = `<p style="color:var(--muted)">Cargando informe…</p>`;
  try {
    const res = await fetch(`/api/person/${state.current}/ai-report?model=${encodeURIComponent(model)}`, { method: "POST" });
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();
    if (data.error) {
      box.innerHTML = `<p style="color:var(--muted)">Sin informe generado aún. Use "Generar informe IA".</p>`;
      return;
    }
    box.innerHTML = `
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">
        ${data.saved ? '📋 Informe guardado' : '✨ Recién generado'} con <strong>${esc(data.model)}</strong> · ${esc(data.generated_at)}</div>
      ${data.fallback ? renderFallbackNotice(data) : ""}
      <div class="ai-report-body">${marked.parse(data.content)}</div>`;
  } catch (e) {
    box.innerHTML = `<p style="color:var(--muted)">No se pudo cargar el informe.</p>`;
  }
}

function renderFallbackNotice(res) {
  return `<div class="drug-warning sev-border-yellow" style="margin-bottom:10px">
    <div class="d-title">⚠️ Informe local (sin conexión al servicio de IA)</div>
    <div>${esc(res.fallback_reason || "El servicio de IA no respondió.")}</div>
  </div>`;
}

async function generateAIReport(force = false) {
  const model = $("#aiModel") ? $("#aiModel").value : "deepseek";
  const box = $("#aiResult");
  box.innerHTML = `<p style="color:var(--muted)">⏳ Generando informe con IA (${esc(model)}), puede tardar 30-90 segundos…</p>`;
  try {
    const res = await api(`/api/person/${state.current}/ai-report?model=${encodeURIComponent(model)}&force=${force ? "true" : "false"}`, { method: "POST" });
    if (res.error) {
      box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Error del servicio de IA</div><div>${esc(res.error)}</div></div>`;
      return;
    }
    box.innerHTML = `
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">
        Generado con <strong>${esc(res.model)}</strong> · ${esc(res.generated_at)}</div>
      ${res.fallback ? renderFallbackNotice(res) : ""}
      <div class="ai-report-body">${marked.parse(res.content)}</div>`;
  } catch (e) {
    box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Error</div><div>${esc(e.message)}</div></div>`;
  }
}

/* ---------------- documentos adjuntos ---------------- */

async function folderFiles(docId) {
  try {
    return await api(`/api/documents/${docId}/list`);
  } catch (e) {
    return { files: [] };
  }
}

async function renderDocuments(d) {
  const docs = d.documents || [];
  if (!docs.length) {
    return `<p style="color:var(--muted)">No hay estudios adjuntos todavía. Suba imágenes, radiografías, IRM, informes PDF, carpetas DICOM, etc.</p>`;
  }
  // resolver listados de carpetas en paralelo
  const items = await Promise.all(docs.map(async (doc) => {
    if (doc.kind === "dicom_folder" || doc.kind === "folder") {
      const list = await folderFiles(doc.id);
      const imgs = (list.files || []).filter((f) => [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"].includes(f.ext)).slice(0, 4);
      const dcmCount = (list.files || []).filter((f) => f.ext === ".dcm" || f.ext === ".dicom").length;
      const thumbnails = imgs.map((f) =>
        `<img src="/api/documents/${doc.id}/file/${encodeURIComponent(f.path)}" alt="${esc(f.path)}" loading="lazy">`).join("");
      const fileList = (list.files || []).slice(0, 8).map((f) =>
        `<li><a href="/api/documents/${doc.id}/file/${encodeURIComponent(f.path)}" target="_blank" rel="noopener">${esc(f.path)}</a> <span style="color:#999">(${Math.max(1, Math.round(f.size / 1024))} KB)</span></li>`).join("");
      const more = (list.files || []).length > 8 ? `<li style="color:#999">… ${(list.files || []).length - 8} más</li>` : "";
      return {
        ...doc,
        folderList: `<div class="doc-thumb thumb-multi">${thumbnails || `<span class="doc-icon">📁</span>`}</div>
          <div class="doc-meta">
            <div class="doc-name">📁 ${esc(doc.orig_filename)}</div>
            <div class="doc-sub">${dcmCount ? dcmCount + " DICOM · " : ""}${(list.files || []).length} archivo(s)</div>
            ${fileList ? `<ul class="doc-filelist">${fileList}${more}</ul>` : ""}
            <div class="doc-actions"><span></span><button onclick="deleteDocument(${doc.id})" title="Eliminar">🗑</button></div>
          </div>`,
      };
    }
    const isImage = doc.kind === "image";
    const sizeKb = doc.size ? Math.max(1, Math.round(doc.size / 1024)) + " KB" : "";
    const icon = doc.kind === "image" ? "🖼️" : doc.kind === "pdf" ? "📄" : doc.kind === "dicom" || doc.kind === "dicom_zip" ? "🩻" : "📎";
    return {
      ...doc,
      folderList: `<div class="doc-thumb">${isImage ? `<img src="/api/documents/${doc.id}/file" alt="${esc(doc.orig_filename)}" loading="lazy">` : `<span class="doc-icon">${icon}</span>`}</div>
        <div class="doc-meta">
          <div class="doc-name" title="${esc(doc.orig_filename)}">${icon} ${esc(doc.orig_filename)}</div>
          <div class="doc-sub">${esc(doc.notes || doc.kind)}${sizeKb ? " · " + sizeKb : ""} · ${fmtDate(doc.uploaded_at)}</div>
          <div class="doc-actions">
            <a href="/api/documents/${doc.id}/file" target="_blank" rel="noopener">${isImage ? "Ver / abrir" : "Descargar"}</a>
            <button onclick="deleteDocument(${doc.id})" title="Eliminar">🗑</button>
          </div>
        </div>`,
    };
  }));
  return `<div class="docs-grid">${items.map((doc) => `<div class="doc-item">${doc.folderList}</div>`).join("")}</div>`;
}

async function deleteDocument(docId) {
  if (!confirm("¿Eliminar este documento?")) return;
  try {
    await api(`/api/person/${state.current}/documents/${docId}`, { method: "DELETE" });
    state.detail = await api(`/api/person/${state.current}`);
    renderPerson();
    toast("Documento eliminado", "green");
  } catch (e) {
    toast("Error: " + e.message, "red");
  }
}

async function uploadFolder(ev) {
  ev.preventDefault();
  const input = $("#uploadFolder");
  const box = $("#uploadResult");
  if (!input.files || !input.files.length) {
    toast("Seleccione una carpeta", "yellow");
    return false;
  }
  const files = Array.from(input.files);
  const fd = new FormData();
  for (const f of files) {
    // conservar la ruta relativa (subcarpetas)
    const rel = f.webkitRelativePath || f.name;
    fd.append("files", f, rel);
  }
  const note = $("#uploadNote") ? $("#uploadNote").value.trim() : "";
  if (note) fd.append("notes", note);
  box.innerHTML = `<p style="color:var(--muted)">⏳ Subiendo carpeta (${files.length} archivos)…</p>`;
  try {
    const res = await fetch(`/api/person/${state.current}/upload-folder`, { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok || data.error) {
      box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Error</div><div>${esc(data.error || "Error al subir carpeta")}</div></div>`;
      return false;
    }
    box.innerHTML = `<div class="drug-warning sev-border-green"><div class="d-title">✅ ${esc(data.message)}</div></div>`;
    toast(data.message, "green");
    state.detail = await api(`/api/person/${state.current}`);
    renderPerson();
  } catch (e) {
    box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Error</div><div>${esc(e.message)}</div></div>`;
  }
  return false;
}

async function fetchDicomLibrary(ev) {
  ev.preventDefault();
  const url = $("#dicomLink").value.trim();
  const box = $("#uploadResult");
  if (!url) { toast("Ingrese el link del estudio", "yellow"); return false; }
  box.innerHTML = `<p style="color:var(--muted)">⏳ Intentando importar estudio desde dicomlibrary.com…</p>`;
  try {
    const fd = new FormData();
    fd.append("url", url);
    const res = await fetch(`/api/person/${state.current}/fetch-dicomlibrary`, { method: "POST", body: fd });
    const data = await res.json();
    if (data.status === "no_api") {
      box.innerHTML = `<div class="drug-warning sev-border-yellow">
        <div class="d-title">⚠️ dicomlibrary.com no permite descarga automática</div>
        <div>${esc(data.message)}</div>
        <div style="margin-top:6px;white-space:pre-line">${esc(data.how_to || "")}</div>
      </div>`;
      return false;
    }
    if (!res.ok || data.error) {
      box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Error</div><div>${esc(data.error || "Error")}</div></div>`;
      return false;
    }
    box.innerHTML = `<div class="drug-warning sev-border-green"><div class="d-title">✅ ${esc(data.message)}</div></div>`;
    state.detail = await api(`/api/person/${state.current}`);
    renderPerson();
  } catch (e) {
    box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Error</div><div>${esc(e.message)}</div></div>`;
  }
  return false;
}

async function uploadReport(ev) {
  ev.preventDefault();
  const input = $("#uploadFile");
  const box = $("#uploadResult");
  const note = $("#uploadNote") ? $("#uploadNote").value.trim() : "";
  if (!input.files || !input.files.length) {
    toast("Seleccione un archivo", "yellow");
    return false;
  }
  const fd = new FormData();
  fd.append("file", input.files[0]);
  fd.append("notes", note);
  box.innerHTML = `<p style="color:var(--muted)">⏳ Procesando ${esc(input.files[0].name)}…</p>`;
  try {
    const res = await fetch(`/api/person/${state.current}/upload`, { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok || data.error) {
      box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Error</div><div>${esc(data.error || "Error al subir")}</div></div>`;
      return false;
    }
    box.innerHTML = `<div class="drug-warning ${data.status === "duplicate" ? "sev-border-yellow" : "sev-border-green"}"><div class="d-title">✅ ${esc(data.message || "OK")}</div></div>`;
    toast(data.message || "Archivo subido", data.status === "duplicate" ? "yellow" : "green");
    // recargar persona (informes nuevos + documentos)
    state.detail = await api(`/api/person/${state.current}`);
    await loadPersons();
    renderPerson();
  } catch (e) {
    box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Error</div><div>${esc(e.message)}</div></div>`;
  }
  return false;
}

/* ---------------- settings ---------------- */

function toggleSettings() {
  const p = $("#settingsPanel");
  p.style.display = p.style.display === "block" ? "none" : "block";
}

async function changePassword(ev) {
  ev.preventDefault();
  const cur = $("#currentPw").value.trim();
  const n1 = $("#newPw").value.trim();
  const n2 = $("#newPw2").value.trim();
  const msg = $("#settingsMsg");
  if (n1 !== n2) { msg.innerHTML = `<span style="color:#d32f2f">Las contraseñas no coinciden.</span>`; return false; }
  const fd = new FormData();
  fd.append("current", cur);
  fd.append("new", n1);
  try {
    const res = await fetch("/api/change-password", { method: "POST", body: fd });
    if (res.status === 401) { window.location.href = "/login"; return false; }
    const d = await res.json();
    if (!res.ok) { msg.innerHTML = `<span style="color:#d32f2f">${esc(d.error)}</span>`; return false; }
    msg.innerHTML = `<span style="color:#2e7d32">✓ Contraseña actualizada.</span>`;
    setTimeout(() => { msg.innerHTML = ""; }, 3000);
  } catch (e) {
    msg.innerHTML = `<span style="color:#d32f2f">Error: ${esc(e.message)}</span>`;
  }
  return false;
}

async function doLogout() {
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/login";
}

/* ---------------- init ---------------- */

document.addEventListener("DOMContentLoaded", () => {
  loadPersons();
  // escaneo en segundo plano al iniciar (no bloquea navegación)
  setTimeout(() => rescanFolder(false), 500);
  // refresco automático de estado cada 60s
  setInterval(() => {
    api("/api/status").then((st) => {
      if (st.last_scan && st.last_scan.ts) {
        $("#scanResult").textContent = `Último escaneo: ${new Date(st.last_scan.ts * 1000).toLocaleTimeString("es-PY")}`;
      }
    }).catch(() => {});
  }, 60000);
});
