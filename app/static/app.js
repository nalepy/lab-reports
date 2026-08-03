/* Panel de Laboratorio Clínico — lógica de la interfaz */
"use strict";

const state = {
  persons: [],
  current: null,       // person id
  detail: null,        // /api/person/{id} response
  charts: {},
  medAutocomplete: [],
  aiReportExists: false,   // hay informe IA guardado/renderizado
  _tab: "resumen",
};

let _pendingQueue = [];   // informes con paciente no reconocido (confirmación)

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

function fmtDMY(s) {
  // "yyyy-mm-dd" (o iso completo) -> "dd-mm-yyyy"
  const m = String(s || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : String(s || "");
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
  } catch (e) {
    toast("Error al cargar datos: " + e.message, "red");
  }
}

function renderPersonList() {
  const el = $("#personList");
  const addBtn = `<div class="person-add"><button class="btn-add" onclick="openNewPatient()">+ Nuevo paciente</button></div>`;
  if (!state.persons.length) {
    el.innerHTML = `<h3>Personas</h3>${addBtn}<div style="padding:12px;color:#888">Sin pacientes aún. Agregue uno o suba estudios.</div>`;
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
  el.innerHTML = `<h3>Personas (${state.persons.length})</h3>` + addBtn + items;
}

/* ---------------- nuevo paciente ---------------- */

function openNewPatient() {
  $("#newPatientModal").style.display = "flex";
  $("#npName").focus();
}
function closeNewPatient() {
  $("#newPatientModal").style.display = "none";
  $("#newPatientMsg").innerHTML = "";
}

async function createPatient(ev) {
  ev.preventDefault();
  const msg = $("#newPatientMsg");
  const name = $("#npName").value.trim();
  if (!name) { msg.innerHTML = `<span style="color:#d32f2f">Escriba el nombre.</span>`; return false; }
  try {
    const res = await fetch("/api/persons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        doc: $("#npDoc").value.trim(),
        sex: $("#npSex").value.trim(),
        age: $("#npAge").value.trim(),
      }),
    });
    const data = await res.json();
    if (!res.ok) { msg.innerHTML = `<span style="color:#d32f2f">${esc(data.error || "Error")}</span>`; return false; }
    closeNewPatient();
    toast("Paciente creado", "green");
    await loadPersons();
    selectPerson(data.id);
  } catch (e) {
    msg.innerHTML = `<span style="color:#d32f2f">${esc(e.message)}</span>`;
  }
  return false;
}

/* ---------------- confirmación paciente no reconocido ---------------- */

function _showNextPending() {
  if (!_pendingQueue.length) { $("#pendingModal").style.display = "none"; return; }
  openPendingModal(_pendingQueue[0]);
}

function openPendingModal(item) {
  const meta = [];
  if (item.lab) meta.push(`Lab: <b>${esc(item.lab)}</b>`);
  if (item.date) meta.push(`Fecha: <b>${fmtDMY(item.date)}</b>`);
  if (item.doc) meta.push(`Doc: <b>${esc(item.doc)}</b>`);
  $("#pendingInfo").innerHTML = `
    <div style="margin-bottom:8px">El informe <b>${esc(item.file)}</b> parece de un paciente que no existe todavía:</div>
    <div style="background:var(--bg, #f5f5f5);padding:10px;border-radius:8px;margin-bottom:8px">
      <div style="font-size:15px;font-weight:700">${esc(item.patient)}</div>
      <div style="font-size:12px;color:var(--muted)">${meta.join(" · ") || "sin más datos"}</div>
    </div>`;
  const sel = $("#pendTarget");
  sel.innerHTML = state.persons
    .filter((p) => p.id !== state.current)
    .map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
  const tabName = (state.persons.find((p) => p.id === state.current) || {}).name || `#${state.current}`;
  $("#pendTabName").textContent = tabName;
  $("#pendMsg").innerHTML = "";
  $("#pendingModal").style.display = "flex";
  pendModeChanged();
}

function pendModeChanged() {
  const v = (document.querySelector('input[name="pendAction"]:checked') || {}).value || "approve";
  $("#pendNameWrap").style.display = v === "rename" ? "" : "none";
  $("#pendTargetWrap").style.display = v === "existing" ? "" : "none";
}

async function resolvePending() {
  const item = _pendingQueue[0];
  if (!item) return;
  const v = (document.querySelector('input[name="pendAction"]:checked') || {}).value || "approve";
  const body = { key: item.key, action: v };
  if (v === "rename") body.name = $("#pendName").value.trim();
  if (v === "existing") body.target_pid = Number($("#pendTarget").value);
  const msg = $("#pendMsg");
  msg.innerHTML = `<span style="color:var(--muted)">⏳ Guardando…</span>`;
  try {
    const res = await fetch(`/api/person/${state.current}/pending`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      msg.innerHTML = `<span style="color:#d32f2f">${esc(data.error || "Error al confirmar")}</span>`;
      return;
    }
    msg.innerHTML = "";
    _pendingQueue.shift();
    if (data.status === "cancelled") {
      toast("Informe descartado", "green");
    } else {
      toast(`Informe ${data.created ? "creó paciente nuevo" : "guardado"} — ${esc(data.person_name || "")}`, "green");
      await loadPersons();
      if (data.pid) await selectPerson(data.pid);
    }
    _showNextPending();
  } catch (e) {
    msg.innerHTML = `<span style="color:#d32f2f">${esc(e.message)}</span>`;
  }
}

async function cancelPending() {
  const item = _pendingQueue[0];
  if (!item) return;
  const msg = $("#pendMsg");
  try {
    const res = await fetch(`/api/person/${state.current}/pending`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: item.key, action: "cancel" }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      msg.innerHTML = `<span style="color:#d32f2f">${esc(data.error || "Error")}</span>`;
      return;
    }
    msg.innerHTML = "";
    _pendingQueue.shift();
    toast("Informe descartado", "green");
    _showNextPending();
  } catch (e) {
    msg.innerHTML = `<span style="color:#d32f2f">${esc(e.message)}</span>`;
  }
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

  const _tab = state._tab || "resumen";
  const tabBtn = (name, icon, label) =>
    `<button class="tab${_tab === name ? " active" : ""}" data-tab="${name}" onclick="switchTab('${name}')">${icon} ${label}</button>`;
  const tabActive = (name) => (_tab === name ? " active" : "");

  panel.innerHTML = `
  <div class="person-header">
    <div>
      <h2>${esc(p.name)}</h2>
      <div class="meta">
        ${p.sex ? "Sexo: " + (p.sex === "M" ? "Masculino" : "Femenino") + " · " : ""}
        ${p.age ? "Edad: " + p.age + " años" + (p.birth_date ? " (" + esc(fmtDMY(p.birth_date)) + ")" : "") + " · " : ""}
        ${p.doc ? "Doc: " + esc(p.doc) + " · " : ""}
        ${p.n_reports} informe(s) · ${p.n_tests} análisis en total
      </div>
      <div class="severity-strip">${sevChips}</div>
    </div>
    <div class="header-actions">
      <button class="sev-chip sev-red" onclick="openDeletePerson()" title="Eliminar este paciente">🗑 Eliminar</button>
    </div>
  </div>

  <div class="metrics-bar">
    <div class="metrics-title">🩺 Datos vitales e información médica</div>
    <input id="mBirth" type="date" title="Fecha de nacimiento" value="${esc(p.birth_date || "")}">
    <input id="mWeight" type="number" step="0.1" min="0" placeholder="Peso (kg)" value="${p.weight_kg ?? ""}">
    <input id="mHeight" type="number" step="0.1" min="0" placeholder="Talla (cm)" value="${p.height_cm ?? ""}">
    <input id="mBp" placeholder="Presión arterial (ej. 120/80)" value="${esc(p.bp || "")}">
    <input id="mHr" type="number" min="0" placeholder="Pulso (bpm)" value="${p.hr ?? ""}">
    <button class="btn" onclick="saveMetrics()">💾 Guardar</button>
    <textarea id="mNotes" rows="2" placeholder="Otra información médica importante: alergias, enfermedades crónicas, antecedentes, condiciones no presentes en los informes…">${esc(p.notes || "")}</textarea>
  </div>

  <div class="tabs">
    ${tabBtn("resumen", "📋", "Resumen")}
    ${tabBtn("laboratorio", "🧪", "Laboratorio")}
    ${tabBtn("hallazgos", "🔎", "Hallazgos")}
    ${tabBtn("medicamentos", "💊", "Medicamentos")}
    ${tabBtn("estudios", "🖼️", "Estudios")}
    ${tabBtn("historial", "🗂", "Historial")}
  </div>

  <div class="tab-panel${tabActive("resumen")}" id="tab-resumen">
    <div class="card summary-card tone-${esc(a.summary.tone)}">
      <div class="card-header">📋 Resumen ejecutivo</div>
      <div class="card-body"><p>${esc(a.summary.text)}</p></div>
    </div>

    <div class="card" id="aiCard">
      <div class="card-header">
        🧠 Informe médico con IA
        <button class="btn btn-ghost" style="float:right" onclick="downloadFullPDF()"
                title="Descargar el informe completo en un solo PDF">⬇️ PDF completo</button>
      </div>
      <div class="card-body">
        <div class="med-form">
          <label style="display:flex;align-items:center;gap:6px;font-weight:600">
            Modelo de IA:
            <select id="aiModel" style="padding:8px;border:1px solid var(--border);border-radius:6px">
              <option value="deepseek">DeepSeek V4 Pro</option>
              <option value="opus">Opus 4.8</option>
            </select>
          </label>
          <button id="aiGenBtn" onclick="generateAIReport()" style="background:var(--blue);color:#fff;border:none;border-radius:6px;padding:8px 16px;font-weight:600;cursor:pointer">✨ Generar informe IA</button>
        </div>
        <div class="med-hint">DeepSeek V4 Pro es el modelo por defecto. Si no queda
        conforme con el informe, puede regenerarlo con <strong>Opus 4.8</strong>.</div>
        <div id="aiResult" style="margin-top:12px"></div>
      </div>
    </div>
  </div>

  <div class="tab-panel${tabActive("laboratorio")}" id="tab-laboratorio">
    <div class="card">
      <div class="card-header">📊 Tablas comparativas (evolución por mes/año)</div>
      <div class="card-body">${renderTables(d)}</div>
    </div>
    <div class="card">
      <div class="card-header">📈 Evolución temporal (gráficos)</div>
      <div class="card-body">${renderCharts(a)}</div>
    </div>
  </div>

  <div class="tab-panel${tabActive("hallazgos")}" id="tab-hallazgos">
    <div class="card">
      <div class="card-header">🔴 Hallazgos anormales (por severidad)</div>
      <div class="card-body">
        ${a.findings.length ? a.findings.map(renderFinding).join("") : `<p style="color:var(--green);font-weight:600">No se detectaron valores fuera de rango en la última evaluación.</p>`}
      </div>
    </div>
    <div class="card">
      <div class="card-header">🩺 Evaluación por sistemas</div>
      <div class="card-body">${a.systems.map(renderSystem).join("")}</div>
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
  </div>

  <div class="tab-panel${tabActive("medicamentos")}" id="tab-medicamentos">
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
  </div>

  <div class="tab-panel${tabActive("estudios")}" id="tab-estudios">
    <div class="card" id="uploadCard">
      <div class="card-header">📤 Subir estudios</div>
      <div class="card-body">
        <div class="upload-zone">
          <div class="upload-drop" id="uploadDrop"
               onclick="document.getElementById('uploadFiles').click()"
               ondragover="ev.preventDefault(); this.classList.add('drag')"
               ondragleave="this.classList.remove('drag')"
               ondrop="onUploadDrop(event)">
            <span class="upload-icon">📁</span>
            <span><strong>Arrastre archivos o carpetas aquí</strong><br>
              o haga clic para elegir archivos · PDF de laboratorio, imágenes, DICOM u otros</span>
          </div>
          <div class="upload-actions-row">
            <button type="button" class="btn" onclick="document.getElementById('uploadFiles').click()">📄 Elegir archivos</button>
            <button type="button" class="btn btn-ghost" onclick="document.getElementById('uploadFolder').click()">📁 Elegir carpeta</button>
          </div>
          <div class="med-hint">La subida comienza automáticamente al elegir o soltar archivos/carpetas. Los DICOM se convierten a imagen/video en el navegador antes de subir.</div>
          <div class="med-hint" style="margin-top:4px">💡 Si una carpeta tiene muchos DICOM, <b>compáctela en ZIP</b> y súbala como archivo: el servidor la descomprime automáticamente.</div>
          <div class="med-hint" style="margin-top:4px">
            <label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" id="dedupToggle" checked> Descartar imágenes casi idénticas
            </label>
            <span style="margin-left:10px">mín. diferencia:
              <input type="number" id="dedupThreshold" value="10" min="1" max="50" step="1"
                     style="width:56px;padding:2px 6px;border:1px solid #ccc;border-radius:6px">%
            </span>
          </div>
          <input type="file" id="uploadFiles" class="upload-input" multiple
                 accept=".pdf,.jpg,.jpeg,.png,.gif,.webp,.bmp,.tif,.tiff,.dcm,.dicom,.zip,.doc,.docx,.txt,.csv"
                 onchange="onUploadPicked()">
          <input type="file" id="uploadFolder" class="upload-input" webkitdirectory directory multiple
                 onchange="onFolderPicked()">
          <div id="uploadFileList" class="upload-files"></div>
        </div>
        <div id="uploadResult"></div>
        <form class="med-form" style="margin-top:12px" onsubmit="return fetchDicomLibrary(event)">
          <input type="text" id="dicomLink" placeholder="O importe por link de dicomlibrary.com (…/?study=…)" style="grid-column: span 2;">
          <button type="submit" style="grid-column: span 2;background:#6a1b9a">🌐 Importar por link</button>
        </form>
      </div>
    </div>

    <div class="card" id="docsCard">
      <div class="card-header">🖼️ Estudios e imágenes adjuntos
        <span class="card-actions">
          <button type="button" class="btn btn-sm" onclick="processStudies()" id="processStudiesBtn"
                  title="Analiza con IA (visión) los estudios sin análisis: RX, TC, resonancia, DICOM y PDFs escaneados">
            ⚡ Procesar con IA</button>
        </span>
      </div>
      <div class="card-body" id="docsBody">
        <p style="color:var(--muted)">Cargando…</p>
      </div>
    </div>
  </div>

  <div class="tab-panel${tabActive("historial")}" id="tab-historial">
    <div class="card">
      <div class="card-header">🗂 Historial de informes</div>
      <div class="card-body">
        ${d.reports.map((r) => `
          <div class="report-item">
            <div class="r-top">
              <div class="r-lab">${esc(r.lab)} ${r.order_code ? "· Orden " + esc(r.order_code) : ""}</div>
              <a class="r-download" href="/api/report/${r.id}/file" target="_blank" rel="noopener"
                 title="Descargar PDF original">⬇️ PDF</a>
            </div>
            <div class="r-date">${fmtDate(r.date)} · ${esc(r.source_file)}</div>
            <div class="r-sections">${esc(r.sections || "")}</div>
          </div>`).join("")}
      </div>
    </div>
  </div>

  <div id="printRoot"></div>`;

  // gráficos: inicializar solo al abrir la pestaña Laboratorio (evita size 0)
  state._chartsInited = false;
  if (state._tab === "laboratorio") initChartsLazy();
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

/* ---------------- tabs + datos vitales + PDF ---------------- */

function switchTab(name) {
  state._tab = name;
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((p) =>
    p.classList.toggle("active", p.id === "tab-" + name));
  if (name === "laboratorio") initChartsLazy();
}

function initChartsLazy() {
  if (state._chartsInited || !state.detail) return;
  state._chartsInited = true;
  renderChartsInit(state.detail.assessment);
}

async function saveMetrics() {
  const pid = state.current;
  const val = (id) => {
    const el = document.getElementById(id);
    return el ? el.value : "";
  };
  const body = {
    birth_date: val("mBirth"),          // type=date -> yyyy-mm-dd
    weight_kg: val("mWeight") === "" ? null : parseFloat(val("mWeight")),
    height_cm: val("mHeight") === "" ? null : parseFloat(val("mHeight")),
    bp: val("mBp").trim(),
    hr: val("mHr") === "" ? null : parseInt(val("mHr"), 10),
    notes: val("mNotes"),
  };
  const res = await api(`/api/person/${pid}/metrics`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.error) { toast("Error: " + res.error, "red"); return; }
  state.detail = await api(`/api/person/${pid}`);
  await loadPersons();
  renderPerson();
  toast("Datos guardados — actualizando informe IA en segundo plano…", "green");
  // el informe IA debe reflejar los vitales/notas: regenerar en 2do plano
  ensureAIRepos(pid);
}

async function downloadFullPDF() {
  const d = state.detail, p = d.person, a = d.assessment;
  const aiHtml = ($("#aiResult") && $("#aiResult").innerHTML.trim())
    ? $("#aiResult").innerHTML
    : "<p>Sin informe IA generado.</p>";
  // gráficos -> imágenes base64
  let chartsHtml = "<p>Sin gráficos disponibles.</p>";
  const grid = $("#chartGrid");
  if (grid && grid.querySelectorAll("canvas").length) {
    chartsHtml = [...grid.querySelectorAll(".chart-box")].map((box) => {
      const canvas = box.querySelector("canvas");
      const img = canvas ? canvas.toDataURL("image/png") : null;
      return `<div class="pdf-chart"><h4>${esc((box.querySelector("h4") || {}).textContent || "")}</h4>` +
        (img ? `<img src="${img}" style="max-width:100%">` : "") + `</div>`;
    }).join("");
  }
  const meds = (d.meds && d.meds.length)
    ? `<ul>${d.meds.map((m) =>
        `<li>${esc(m.name)}${m.dose ? " · " + esc(m.dose) : ""}${m.frequency ? " · " + esc(m.frequency) : ""}</li>`).join("")}</ul>`
    : "<p>Sin medicamentos registrados.</p>";
  const hist = d.reports.map((r) =>
    `<li>${esc(r.lab)} · ${fmtDate(r.date)} · ${esc(r.source_file || "")}</li>`).join("");
  const bpLine = (p.birth_date ? " · Nac. " + esc(fmtDMY(p.birth_date)) : "")
    + (p.bp ? " · PA " + esc(p.bp) : "")
    + (p.hr ? " · Pulso " + esc(p.hr) + " bpm" : "")
    + (p.weight_kg ? " · " + esc(p.weight_kg) + " kg" : "")
    + (p.height_cm ? " · " + esc(p.height_cm) + " cm" : "");
  const notesSection = (p.notes && p.notes.trim())
    ? `<h2>Información médica adicional</h2><p>${esc(p.notes)}</p>`
    : "";
  $("#printRoot").innerHTML = `
    <div style="margin-bottom:16px">
      <h1>${esc(p.name)}</h1>
      <p class="pdf-meta">${p.sex ? "Sexo: " + (p.sex === "M" ? "Masculino" : "Femenino") + " · " : ""}Edad: ${p.age ? p.age + " años" : "—"}${bpLine}</p>
    </div>
    ${notesSection}
    <h2>Resumen ejecutivo</h2>
    <p>${esc(a.summary.text)}</p>
    <h2>Informe médico con IA</h2>
    ${aiHtml}
    ${a.findings.length ? `<h2>Hallazgos anormales</h2>${a.findings.map(renderFinding).join("")}` : ""}
    <h2>Evaluación por sistemas</h2>
    ${a.systems.map(renderSystem).join("")}
    <h2>Recomendaciones</h2>
    ${a.recommendations.map(renderRec).join("")}
    <h2>Medicamentos</h2>
    ${meds}
    <h2>Evolución (gráficos)</h2>
    ${chartsHtml}
    <h2>Historial de informes</h2>
    <ul>${hist}</ul>`;
  window.print();
}

/* ---------------- medicamentos ---------------- */

function renderMedsForm() {
  return `
  <form class="med-form" onsubmit="return addMed(event)">
    <div class="autocomplete">
      <input id="medName" list="" placeholder="Medicamento o marca (escriba libremente)" autocomplete="off"
             oninput="medAutocomplete()" onfocus="medAutocomplete()">
      <div id="medAutoList" class="autocomplete-list"></div>
    </div>
    <input id="medDose" placeholder="Dosis (ej: 500 mg)">
    <input id="medFreq" placeholder="Frecuencia (ej: 2×/día)">
    <button type="submit">Agregar</button>
  </form>
  <div class="med-hint">✍️ Escriba <strong>cualquier</strong> medicamento y dosis a mano y pulse
  “Agregar”, aunque no aparezca en las sugerencias. La lista solo ayuda a autocompletar.</div>
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
  const listEl = $("#medAutoList");
  if (listEl) listEl.innerHTML = "";   // cerrar sugerencias: no bloquean el alta manual
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
        ref_text: t.ref_text,
        ref_low: t.ref_low,
        ref_high: t.ref_high,
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
    const nameTip = refTooltip(m.ref_text, m.ref_low, m.ref_high, m.unit);
    const stdRange = m.std_range
      ? ` <span class="std-range">(${esc(m.std_range)})</span>` : "";
    const nameCell = nameTip
      ? `<td title="${nameTip}" style="cursor:help">${esc(m.label)}${stdRange}</td>`
      : `<td>${esc(m.label)}${stdRange}</td>`;
    const cells = monthOrder.map((mk) => {
      const cell = byMonth[mk] && byMonth[mk][m.key];
      if (!cell) return `<td class="num"></td>`;  // no se realizó ese mes
      if (cell.value == null) return `<td class="num"></td>`;
      const cls = cell.flag === "H" ? "val-abnormal-H" : cell.flag === "L" ? "val-abnormal-L" : "";
      const tip = refTooltip(cell.ref_text, cell.ref_low, cell.ref_high, cell.unit);
      return `<td class="num ${cls}" title="${tip || esc(cell.date || "")}"${tip ? ' style="cursor:help"' : ""}>${fmtNum(cell.value, cell.unit)}</td>`;
    }).join("");
    return `<tr>${nameCell}${cells}</tr>`;
  }).join("");

  // ---- hallazgos textuales de análisis de imagen (sin valor numérico) ----
  const anaText = (d.assessment && d.assessment._ana_text) || [];
  const textBlock = anaText.length ? `
    <div class="ana-text-card">
      <div class="ana-text-head">📄 Hallazgos de estudios (imágenes, DICOM, PDFs analizados)</div>
      ${anaText.map((t) => {
        const sev = (t.severity || "normal").toLowerCase();
        const cls = sev === "severo" || sev === "crítico" ? "red" : sev === "moderado" ? "yellow" : "green";
        return `<div class="ana-text-row ana-f-${cls}">
          <span class="ana-text-badge ${cls}">${esc(t.severity || "normal")}</span>
          <strong>${esc(t.system)}</strong> — ${esc(t.text)}${t.date ? ` <span class="ana-text-date">(${esc(t.date)})</span>` : ""}</div>`;
      }).join("")}
    </div>` : "";

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
  de cada valor anormal se muestran en “Hallazgos anormales”.</div>
  ${textBlock}`;
}

/* texto del rango deseado de referencia para tooltips (siempre escapado:
   va en un atributo title y ref_text proviene de PDFs de laboratorio). */
function refTooltip(ref_text, low, high, unit) {
  const t = (ref_text || "").trim();
  if (t && t !== "-") return esc(t);
  if (low != null && high != null) return esc(`Rango deseado: ${fmtNum(low, "")} – ${fmtNum(high, "")} ${unit || ""}`.trim());
  if (high != null) return esc(`Rango deseado: inferior a ${fmtNum(high, "")} ${unit || ""}`.trim());
  if (low != null) return esc(`Rango deseado: superior a ${fmtNum(low, "")} ${unit || ""}`.trim());
  return "";
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
      state.aiReportExists = false;
      updateAIButtonLabel();
      box.innerHTML = `<p style="color:var(--muted)">Sin informe generado aún. Use "Generar informe IA".</p>`;
      return;
    }
    state.aiReportExists = true;
    updateAIButtonLabel();
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

function updateAIButtonLabel() {
  const btn = $("#aiGenBtn");
  if (!btn) return;
  btn.textContent = state.aiReportExists
    ? "🔄 Regenerar informe IA"
    : "✨ Generar informe IA";
}

async function generateAIReport() {
  // Un solo botón: si ya hay informe guardado/renderizado, se REGENERA
  // (fuerza recálculo con los datos nuevos); si no, se genera por primera vez.
  const force = state.aiReportExists;
  const model = $("#aiModel") ? $("#aiModel").value : "deepseek";
  const box = $("#aiResult");
  box.innerHTML = `<p style="color:var(--muted)">⏳ ${force ? "Regenerando" : "Generando"} informe con IA (${esc(model)}), puede tardar 30-90 segundos…</p>`;
  try {
    const res = await api(`/api/person/${state.current}/ai-report?model=${encodeURIComponent(model)}&force=${force ? "true" : "false"}`, { method: "POST" });
    if (res.error) {
      box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Error del servicio de IA</div><div>${esc(res.error)}</div></div>`;
      return;
    }
    state.aiReportExists = true;
    updateAIButtonLabel();
    box.innerHTML = `
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">
        ${res.saved ? "📋 Informe guardado" : "✨ Recién generado"} con <strong>${esc(res.model)}</strong> · ${esc(res.generated_at)}</div>
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

/* ---------------- galería a pantalla completa ---------------- */

const _GAL_PER_PAGE = 24;

let _docTitleCache = {};

let _gallery = {
  urls: [], idx: 0, page: 0, zoom: 1, panX: 0, panY: 0, view: "thumbs",
};

function _galleryApplyView() {
  const img = $("#galleryImg");
  const { zoom, panX, panY } = _gallery;
  if (zoom <= 1.001) {
    img.style.transform = "";
  } else {
    img.style.transform = `translate3d(${panX}px, ${panY}px, 0) scale(${zoom})`;
  }
  img.style.cursor = zoom > 1.001 ? "grab" : "";
}

function _galleryResetView() {
  _gallery.zoom = 1;
  _gallery.panX = 0;
  _gallery.panY = 0;
  _galleryApplyView();
}

function _galleryImgName(url) {
  const lastSlash = url.lastIndexOf("/");
  return decodeURIComponent(url.slice(lastSlash + 1).split("?")[0]);
}

function _galleryRenderGrid() {
  const { urls, page } = _gallery;
  const pages = Math.max(1, Math.ceil(urls.length / _GAL_PER_PAGE));
  const start = Math.min(page, pages - 1) * _GAL_PER_PAGE;
  const slice = urls.slice(start, start + _GAL_PER_PAGE);
  const grid = $("#galleryThumbsGrid");
  grid.innerHTML = slice.map((u, i) => {
    const n = start + i;
    return `<div class="gal-thumb" data-gthumb="${n}" title="${esc(_galleryImgName(u))}">
      <img src="${u}" loading="lazy" alt="Imagen ${n + 1}">
      <span class="gal-thumb-idx">${n + 1}</span>
    </div>`;
  }).join("");
  const prevBtn = $("#galPrevPage");
  const nextBtn = $("#galNextPage");
  if (prevBtn) prevBtn.disabled = page <= 0;
  if (nextBtn) nextBtn.disabled = page >= pages - 1;
  const info = $("#galleryPageInfo");
  if (info) info.textContent = `${urls.length} imagen(es) · página ${page + 1}/${pages}`;
}

function _galleryShowThumbs() {
  _gallery.view = "thumbs";
  const thumbsView = $("#galleryThumbsView");
  const imgView = $("#galleryImgView");
  if (thumbsView) thumbsView.style.display = "";
  if (imgView) imgView.style.display = "none";
  const back = $("#galleryImgBack");
  if (back) back.style.visibility = "hidden";
  $("#galleryTitle").textContent = _gallery.title || "Galería";
  _galleryRenderGrid();
}

function _galleryShowImage() {
  _gallery.view = "image";
  const thumbsView = $("#galleryThumbsView");
  const imgView = $("#galleryImgView");
  if (thumbsView) thumbsView.style.display = "none";
  if (imgView) imgView.style.display = "flex";
  const back = $("#galleryImgBack");
  if (back) back.style.visibility = "visible";
  const { urls, idx } = _gallery;
  const img = $("#galleryImg");
  img.src = urls[idx];
  img.alt = `Imagen ${idx + 1} de ${urls.length}`;
  $("#galleryTitle").textContent = `${_galleryImgName(urls[idx])} · ${idx + 1}/${urls.length}`;
  document.querySelectorAll(".gallery-nav").forEach((b) => {
    b.style.display = urls.length > 1 ? "flex" : "none";
  });
  _galleryResetView();
  // precarga vecinas
  [idx - 1, idx + 1].forEach((i) => {
    if (i >= 0 && i < urls.length) {
      const pre = new Image();
      pre.src = urls[i];
    }
  });
}

function openGallery(docId, urls, startIdx, title) {
  if (!urls.length) return;
  _gallery = {
    urls,
    idx: Math.max(0, Math.min(startIdx, urls.length - 1)),
    page: Math.floor(Math.max(0, Math.min(startIdx, urls.length - 1)) / _GAL_PER_PAGE),
    zoom: 1, panX: 0, panY: 0, view: "thumbs", title: title || "",
  };
  $("#galleryModal").style.display = "flex";
  document.body.style.overflow = "hidden";
  _galleryShowThumbs();
}

function galleryPage(dir) {
  const pages = Math.max(1, Math.ceil(_gallery.urls.length / _GAL_PER_PAGE));
  _gallery.page = Math.min(Math.max(0, _gallery.page + dir), pages - 1);
  _galleryRenderGrid();
}

function galleryGoto(i) {
  const n = _gallery.urls.length;
  if (!n) return;
  _gallery.idx = i < 0 ? n - 1 : Math.max(0, Math.min(i, n - 1));
  _galleryShowImage();
}

function galleryStep(dir) {
  const { urls, idx } = _gallery;
  if (urls.length < 2) return;
  _gallery.idx = (idx + dir + urls.length) % urls.length;
  _galleryShowImage();
}

function galleryZoom(factor) {
  const prev = _gallery.zoom;
  const next = Math.min(10, Math.max(1, prev * factor));
  if (next === prev) return;
  _gallery.zoom = next;
  _galleryApplyView();
}

function galleryResetZoom() {
  _galleryResetView();
}

function galleryClose() {
  if (_gallery.view === "image") _galleryShowThumbs();
  else closeGallery();
}

function galleryShowThumbs() {
  _galleryShowThumbs();
}

function _galleryKeydown(ev) {
  if ($("#galleryModal").style.display === "none") return;
  if (ev.key === "Escape") {
    if (_gallery.view === "image") _galleryShowThumbs();
    else closeGallery();
    return;
  }
  if (_gallery.view === "image") {
    if (ev.key === "ArrowLeft") galleryStep(-1);
    else if (ev.key === "ArrowRight") galleryStep(1);
    else if (ev.key === "+" || ev.key === "=") galleryZoom(1.25);
    else if (ev.key === "-" || ev.key === "_") galleryZoom(0.8);
    else if (ev.key === "0") galleryResetZoom();
    else if (ev.key === "Backspace") _galleryShowThumbs();
  } else {
    if (ev.key === "ArrowLeft") galleryPage(-1);
    else if (ev.key === "ArrowRight") galleryPage(1);
    else if (ev.key === "Enter") { /* selección por click */ }
  }
}

function _galleryWheel(ev) {
  if ($("#galleryModal").style.display === "none") return;
  if (_gallery.view !== "image") return;
  if (ev.ctrlKey || ev.metaKey) return; // zoom de página
  ev.preventDefault();
  galleryZoom(ev.deltaY < 0 ? 1.15 : 1 / 1.15);
}

let _pan = null;

function _galleryPanStart(ev) {
  if (_gallery.view !== "image" || _gallery.zoom <= 1.001) return;
  _pan = { x: ev.clientX, y: ev.clientY, px: _gallery.panX, py: _gallery.panY };
  const img = $("#galleryImg");
  img.style.cursor = "grabbing";
  ev.preventDefault();
}

function _galleryPanMove(ev) {
  if (!_pan) return;
  _gallery.panX = _pan.px + (ev.clientX - _pan.x);
  _gallery.panY = _pan.py + (ev.clientY - _pan.y);
  _galleryApplyView();
}

function _galleryPanEnd() {
  if (!_pan) return;
  _pan = null;
  const img = $("#galleryImg");
  if (img) img.style.cursor = _gallery.zoom > 1.001 ? "grab" : "";
}

function closeGallery() {
  $("#galleryModal").style.display = "none";
  document.body.style.overflow = "";
  $("#galleryImg").src = "";
  _galleryResetView();
  _pan = null;
}

async function openFolderGallery(docId, startIdx, title) {
  const list = await folderFiles(docId);
  const urls = (list.files || [])
    .filter((f) => _IMG_EXTS.has(f.ext))
    .map((f) => `/api/documents/${docId}/file/${encodeURIComponent(f.path)}`);
  if (!urls.length) {
    toast("Esta carpeta no contiene imágenes", "yellow");
    return;
  }
  openGallery(docId, urls, startIdx || 0, title || _docTitleCache[docId] || "Galería");
}

function openDocImage(docId, title) {
  openGallery(docId, [`/api/documents/${docId}/file`], 0, title || _docTitleCache[docId] || "Imagen");
}

function openRawDoc(docId) {
  window.open(`/api/documents/${docId}/file`, "_blank", "noopener");
}

async function renderDocuments(d) {
  // ocultar PDFs que ya están en el historial (no duplicar)
  const docs = (d.documents || []).filter(
    (doc) => !(doc.kind === "pdf" && doc.is_parsed_lab));
  if (!docs.length) {
    return `<p style="color:var(--muted)">No hay imágenes, radiografías, tomografías o estudios pendientes. Los PDFs de laboratorio ya están en el historial.</p>`;
  }
  // resolver listados de carpetas en paralelo
  const items = await Promise.all(docs.map(async (doc) => {
    _docTitleCache[doc.id] = doc.orig_filename || doc.kind;
    if (doc.kind === "dicom_folder" || doc.kind === "folder") {
      const list = await folderFiles(doc.id);
      const imgs = (list.files || []).filter((f) => _IMG_EXTS.has(f.ext)).slice(0, 4);
      const dcmCount = (list.files || []).filter((f) => f.ext === ".dcm" || f.ext === ".dicom").length;
      const thumbnails = imgs.map((f) =>
        `<img src="/api/documents/${doc.id}/file/${encodeURIComponent(f.path)}" alt="${esc(f.path)}" loading="lazy" onclick="openFolderGallery(${doc.id})" title="Abrir galería">`).join("");
      const fileList = (list.files || []).filter((f) => _IMG_EXTS.has(f.ext)).slice(0, 8).map((f) =>
        `<li><a href="#" onclick="openFolderGallery(${doc.id});return false;" title="Abrir en la galería">🖼️ ${esc(f.path)}</a> <span style="color:#999">(${Math.max(1, Math.round(f.size / 1024))} KB)</span></li>`).join("");
      const nonImg = (list.files || []).filter((f) => !_IMG_EXTS.has(f.ext)).slice(0, 8).map((f) =>
        `<li><a href="/api/documents/${doc.id}/file/${encodeURIComponent(f.path)}" target="_blank" rel="noopener" download>📄 ${esc(f.path)}</a> <span style="color:#999">(${Math.max(1, Math.round(f.size / 1024))} KB)</span></li>`).join("");
      const moreImgs = (list.files || []).filter((f) => _IMG_EXTS.has(f.ext)).length > 8 ? `<li style="color:#999">… ${(list.files || []).filter((f) => _IMG_EXTS.has(f.ext)).length - 8} imágenes más en la galería</li>` : "";
      const imgCount = (list.files || []).filter((f) => _IMG_EXTS.has(f.ext)).length;
      return {
        ...doc,
        folderList: `<div class="doc-thumb thumb-multi" onclick="openFolderGallery(${doc.id})" title="Abrir galería (${imgCount} imágenes)">${thumbnails || `<span class="doc-icon">📁</span>`}</div>
          <div class="doc-meta">
            <div class="doc-name">📁 ${esc(doc.orig_filename)}</div>
            <div class="doc-sub">${dcmCount ? dcmCount + " DICOM · " : ""}${(list.files || []).length} archivo(s) · ${imgCount} imagen(es)</div>
            ${fileList || nonImg ? `<ul class="doc-filelist">${fileList}${nonImg}${moreImgs}</ul>` : ""}
            <div class="doc-actions">
              <a href="#" onclick="openFolderGallery(${doc.id});return false;" title="Ver todas las imágenes">🖼️ Ver galería</a>
              <a href="/api/documents/${doc.id}/zip" title="Descargar todo (ZIP, formatos originales)">⬇️ ZIP</a>
              <button onclick="deleteDocument(${doc.id})" title="Eliminar">🗑</button>
            </div>
          </div>`,
      };
    }
    const isImage = doc.kind === "image";
    const sizeKb = doc.size ? Math.max(1, Math.round(doc.size / 1024)) + " KB" : "";
    const icon = doc.kind === "image" ? "🖼️" : doc.kind === "pdf" ? "📄" : doc.kind === "dicom" || doc.kind === "dicom_zip" ? "🩻" : "📎";
    return {
      ...doc,
      folderList: `${isImage ? `<div class="doc-thumb"><img src="/api/documents/${doc.id}/file" alt="${esc(doc.orig_filename)}" loading="lazy" onclick="openDocImage(${doc.id})" title="Ver imagen"></div>` : ""}
        <div class="doc-meta">
          <div class="doc-name" title="${esc(doc.orig_filename)}">${icon} ${esc(doc.orig_filename)}</div>
          <div class="doc-sub">${esc(doc.notes || doc.kind)}${sizeKb ? " · " + sizeKb : ""} · ${fmtDate(doc.uploaded_at)}</div>
          ${doc.study_date ? `<div class="doc-study-date">📅 Fecha estudio: <b>${fmtDMY(doc.study_date)}</b></div>` : ""}
          <div class="doc-actions">
            <a href="#" onclick="${isImage ? `openDocImage(${doc.id})` : `openRawDoc(${doc.id})`};return false;">${isImage ? "🖼️ Ver" : "Descargar"}</a>
            <button onclick="deleteDocument(${doc.id})" title="Eliminar">🗑</button>
          </div>
        </div>`,
    };
  }));
  return `<div class="docs-grid">${items.map((doc) => {
    const compact = !(doc.kind === "image" || doc.kind === "dicom_folder" || doc.kind === "folder");
    return `<div class="doc-item${compact ? " doc-file" : ""}">${doc.folderList}${analysisBlock(doc)}</div>`;
  }).join("")}</div>`;
}

function analysisBlock(doc) {
  const st = doc.analysis_status;
  if (st === "done") {
    let findings = [];
    try { findings = JSON.parse(doc.analysis_findings || "[]"); } catch (e) { findings = []; }
    const rows = findings.map((f) => {
      const sev = (f.severity || "normal").toLowerCase();
      const cls = sev === "severo" || sev === "crítico" ? "red" : sev === "moderado" ? "yellow" : "green";
      const val = f.value != null ? ` <b>${esc(String(f.value))}${f.unit ? " " + esc(f.unit) : ""}</b>` : "";
      return `<li class="ana-f-${cls}">[${esc(f.system || "Estudio")}] ${esc(f.text || "")}${val}</li>`;
    }).join("");
    const model = doc.analysis_model ? ` · <span style="color:#999">${esc(doc.analysis_model)}</span>` : "";
    return `<div class="ana-box">
      <div class="ana-head">🧠 Análisis IA${model}</div>
      ${rows ? `<ul class="ana-list">${rows}</ul>` : `<p style="color:var(--muted)">Sin hallazgos destacados.</p>`}
    </div>`;
  }
  if (st === "error") {
    return `<div class="ana-box ana-err">⚠️ Error al analizar: ${esc(doc.analysis_error || "desconocido")}</div>`;
  }
  if (st === "pending") {
    return `<div class="ana-box ana-wait">⏳ Análisis en curso…</div>`;
  }
  return "";
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

let _processing = false;

async function processStudies() {
  if (_processing) return;
  const pid = state.current;
  if (!pid) return;
  _processing = true;
  const btn = document.getElementById("processStudiesBtn");
  const body = document.getElementById("docsBody");
  if (btn) { btn.disabled = true; btn.textContent = "⏳ Analizando…"; }
  if (body) body.innerHTML = `<p style="color:var(--muted)">Analizando estudios con IA (puede tardar varios minutos)…</p>`;
  try {
    const res = await api(`/api/person/${pid}/process-studies`, { method: "POST" });
    const s = res.summary || {};
    state.detail = await api(`/api/person/${pid}`);
    renderPerson();
    const msg = `Analizados: ${s.analyzed ?? 0} · Errores: ${s.errors ?? 0}`;
    toast((s.errors ? "Proceso con errores: " : "Procesado: ") + msg,
      s.errors ? "red" : "green");
    // regenerar el informe principal con los hallazgos de imagen
    if ((s.analyzed ?? 0) > 0) ensureAIReports(pid);
  } catch (e) {
    toast("Error al procesar: " + e.message, "red");
    state.detail = await api(`/api/person/${pid}`);
    renderPerson();
  } finally {
    _processing = false;
  }
}

/* ---------------- subir estudios (un selector: archivos y/o carpetas) ---------------- */

let _pendingUpload = [];

function _renderUploadList(listEl) {
  if (!_pendingUpload.length) { listEl.innerHTML = ""; return; }
  listEl.innerHTML = _pendingUpload.map((f) =>
    `<div class="up-file">${esc(f.webkitRelativePath || f.name)} <span>${(f.size / 1024).toFixed(0)} KB</span></div>`
  ).join("");
}

let _uploadBusy = false;

function onUploadPicked() {
  const input = $("#uploadFiles");
  _pendingUpload = Array.from(input.files || []);
  input.value = "";  // permite re-elegir el mismo archivo
  _startUpload();
}

function onFolderPicked() {
  const input = $("#uploadFolder");
  _pendingUpload = Array.from(input.files || []);
  input.value = "";
  _startUpload();
}

function onUploadDrop(ev) {
  ev.preventDefault();
  const drop = $("#uploadDrop");
  if (drop) drop.classList.remove("drag");
  _pendingUpload = Array.from(ev.dataTransfer.files || []);
  _startUpload();
}

function _startUpload() {
  if (_uploadBusy) { toast("Ya se está subiendo…", "yellow"); return; }
  uploadBatch();
}

/* --- almacenamiento temporal privado (OPFS) + empaquetado ZIP local --- */

async function _opfsWrite(dir, relPath, blob) {
  const parts = relPath.replace(/\\/g, "/").split("/").filter(p => p && p !== "." && p !== "..");
  const name = parts.pop();
  let d = dir;
  for (const p of parts) d = await d.getDirectoryHandle(p, { create: true });
  const fh = await d.getFileHandle(name, { create: true });
  const w = await fh.createWritable();
  await w.write(blob);
  await w.close();
}

async function _purgeDir(dir) {
  for await (const [name, h] of dir.entries()) {
    try {
      if (h.kind === "directory") { await _purgeDir(h); await dir.removeEntry(name); }
      else await dir.removeEntry(name);
    } catch (e) { /* ignorar */ }
  }
}

async function _listDir(dir, prefix) {
  const out = [];
  for await (const [name, h] of dir.entries()) {
    const rel = prefix ? `${prefix}/${name}` : name;
    if (h.kind === "directory") {
      out.push(...await _listDir(h, rel));
    } else {
      out.push({ name: rel, handle: h });
    }
  }
  return out;
}

const _crcTable = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    t[n] = c;
  }
  return t;
})();

function _crc32(data) {
  let c = 0xFFFFFFFF;
  const t = _crcTable;
  for (let i = 0; i < data.length; i++) c = t[(c ^ data[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

function _dosDateTime(d) {
  const time = ((d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1)) & 0xFFFF;
  const date = (((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate()) & 0xFFFF;
  return { time, date };
}

// Solo se dejan pasar (sin convertir) documentos seguros; el resto (software,
// binarios) se omite y nunca llega al servidor.
const _SAFE_DOC_EXT = new Set([
  ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".csv",
  ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
  ".dcm", ".dicom"
]);

const _IMG_EXTS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"]);

// Diferencia relativa media entre dos firmas perceptuales [0..1].
// 0 = idénticas; ~0.5 = muy distintas.
function _sigDiff(a, b) {
  if (!a || !b || a.length !== b.length) return 1;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff += Math.abs(a[i] - b[i]);
  return diff / a.length;
}

async function _streamZip(outHandle, entries) {
  // ZIP sin compresión (los JPG/WebM ya vienen comprimidos); un archivo a la vez.
  const w = await outHandle.createWritable();
  const now = _dosDateTime(new Date());
  const central = [];
  let offset = 0;
  for (const e of entries) {
    const data = new Uint8Array(await (await e.handle.getFile()).arrayBuffer());
    const nameBytes = new TextEncoder().encode(e.name);
    const crc = _crc32(data);
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true);      // local file header
    lh.setUint16(4, 20, true);              // version needed
    lh.setUint16(6, 0x0800, true);          // flag UTF-8
    lh.setUint16(8, 0, true);               // método: store
    lh.setUint16(10, now.time, true);
    lh.setUint16(12, now.date, true);
    lh.setUint32(14, crc, true);
    lh.setUint32(18, data.length, true);
    lh.setUint32(22, data.length, true);
    lh.setUint16(26, nameBytes.length, true);
    lh.setUint16(28, 0, true);              // extra len
    await w.write(lh.buffer);
    await w.write(nameBytes);
    await w.write(data);
    central.push({ nameBytes, crc, size: data.length, offset });
    offset += 30 + nameBytes.length + data.length;
  }
  let centralSize = 0;
  const centralStart = offset;
  for (const c of central) {
    const ch = new DataView(new ArrayBuffer(46));
    ch.setUint32(0, 0x02014b50, true);      // central directory
    ch.setUint16(4, 20, true);              // version made by
    ch.setUint16(6, 20, true);              // version needed
    ch.setUint16(8, 0x0800, true);
    ch.setUint16(10, 0, true);
    ch.setUint16(12, now.time, true);
    ch.setUint16(14, now.date, true);
    ch.setUint32(16, c.crc, true);
    ch.setUint32(20, c.size, true);
    ch.setUint32(24, c.size, true);
    ch.setUint16(28, c.nameBytes.length, true);
    ch.setUint16(30, 0, true);
    ch.setUint16(32, 0, true);
    ch.setUint16(34, 0, true);
    ch.setUint16(36, 0, true);
    ch.setUint32(38, 0, true);
    ch.setUint32(42, c.offset, true);
    await w.write(ch.buffer);
    await w.write(c.nameBytes);
    centralSize += 46 + c.nameBytes.length;
  }
  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, 0x06054b50, true);     // end of central directory
  eocd.setUint16(4, 0, true);
  eocd.setUint16(6, 0, true);
  eocd.setUint16(8, central.length, true);
  eocd.setUint16(10, central.length, true);
  eocd.setUint32(12, centralSize, true);
  eocd.setUint32(16, centralStart, true);
  eocd.setUint16(20, 0, true);
  await w.write(eocd.buffer);
  await w.close();
}

async function uploadBatch() {
  const box = $("#uploadResult");
  const list = $("#uploadFileList");
  const files = _pendingUpload;
  if (!files.length) { toast("Seleccione o arrastre archivos/carpetas", "yellow"); return; }
  const totalN = files.length;

  // --- PASO 1: verificar soporte de almacenamiento temporal privado ---
  if (!navigator.storage || !navigator.storage.getDirectory) {
    _pendingUpload = []; list.innerHTML = "";
    box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Navegador no compatible</div><div>Este navegador no soporta el almacenamiento temporal local. Use Chrome o Edge actualizado.</div></div>`;
    return;
  }

  // --- PASO 2: convertir y GUARDAR cada archivo a temp (libera RAM) ---
  _uploadBusy = true;
  const rootDir = await navigator.storage.getDirectory();
  const tmpName = `lab_up_${Date.now()}`;
  let tmpDir;
  try {
    tmpDir = await rootDir.getDirectoryHandle(tmpName, { create: true });
  } catch (e) {
    _uploadBusy = false;
    box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Error</div><div>No se pudo crear la carpeta temporal: ${esc(e.message || e)}</div></div>`;
    return;
  }
  let converted = 0;
  let passthrough = 0;
  let skipped = 0;
  let deduped = 0;
  let done = 0;
  const dedupOn = $("#dedupToggle") ? $("#dedupToggle").checked : false;
  const minDiffPct = Math.min(50, Math.max(1, parseFloat($("#dedupThreshold") ? $("#dedupThreshold").value : "10") || 10));
  const minDiff = minDiffPct / 100;
  let lastSig = null;
  box.innerHTML = `<p style="color:var(--muted)">⏳ Escaneando, convirtiendo y guardando… 0/${totalN}</p>`;
  for (const f of files) {
    try {
      const res = await window.DicomConverter.convert(f, { quality: 0.92 });
      if (res && (res.kind === "image" || res.kind === "video")) {
        if (res.kind === "image" && dedupOn && lastSig && res.sig && _sigDiff(lastSig, res.sig) < minDiff) {
          // imagen casi idéntica a la última conservada → se descarta
          deduped++;
        } else {
          const ext = res.kind === "image" ? "jpg" : "webm";
          const rel = f.webkitRelativePath || f.name;
          const slash = rel.lastIndexOf("/");
          const dirPart = slash >= 0 ? rel.slice(0, slash + 1) : "";
          const namePart = slash >= 0 ? rel.slice(slash + 1) : rel;
          const base = namePart.replace(/\.[^.]+$/, "") || namePart;
          await _opfsWrite(tmpDir, dirPart + base + "." + ext, res.blob);
          converted++;
          if (res.kind === "image" && res.sig) lastSig = res.sig;
          if (res.kind === "video") lastSig = null; // el video rompe la secuencia
        }
      } else if (res && res.kind === "unparsed" && res.dicom === false) {
        // no es DICOM → se guarda el original SOLO si es un documento seguro
        const relName = f.webkitRelativePath || f.name || "";
        const dot = relName.lastIndexOf(".");
        const fext = dot >= 0 ? relName.slice(dot).toLowerCase() : "";
        if (_SAFE_DOC_EXT.has(fext)) {
          await _opfsWrite(tmpDir, relName, f);
          passthrough++;
          lastSig = null;
        } else {
          // software/binarios/dangerous → no se sube
          skipped++;
        }
      } else {
        // DICOM que no se pudo convertir → se omite (nunca se sube raw)
        skipped++;
      }
    } catch (e) {
      skipped++;
    }
    done++;
    if (done % 25 === 0 || done === totalN) {
      const parts = [];
      if (converted) parts.push(`${converted} convertidos`);
      if (passthrough) parts.push(`${passthrough} no-DICOM`);
      if (deduped) parts.push(`${deduped} casi-idénticos descartados`);
      if (skipped) parts.push(`${skipped} omitidos`);
      box.innerHTML = `<p style="color:var(--muted)">⏳ Procesando… ${done}/${totalN} (${parts.join(", ")})</p>`;
    }
  }
  _pendingUpload = [];
  list.innerHTML = "";

  // --- PASO 3: listar lo guardado y confirmar tamaño real ---
  const savedEntries = await _listDir(tmpDir, "");
  const totalUpload = savedEntries.length;
  if (totalUpload === 0) {
    await _purgeDir(tmpDir).catch(() => {});
    try { await rootDir.removeEntry(tmpName); } catch (e) { /* noop */ }
    box.innerHTML = `<div class="drug-warning sev-border-yellow"><div class="d-title">Nada que subir</div><div>Todos los archivos fallaron en la conversión.</div></div>`;
    _uploadBusy = false;
    return;
  }
  // --- PASO 4: empaquetar en ZIP(s) y subir (el servidor descomprime) ---
  const MAX_ZIP = 90 * 1048576; // 90 MB por ZIP
  const chunks = [];
  let cur = [];
  let curBytes = 0;
  for (const e of savedEntries) {
    const size = (await e.handle.getFile()).size;
    if (cur.length && curBytes + size > MAX_ZIP) { chunks.push(cur); cur = []; curBytes = 0; }
    cur.push(e);
    curBytes += size;
  }
  if (cur.length) chunks.push(cur);

  let totalUploaded = 0;
  const rows = [];
  try {
    for (let i = 0; i < chunks.length; i++) {
      const zipName = chunks.length > 1 ? `estudio_${tmpName}_parte${i + 1}.zip` : `estudio_${tmpName}.zip`;
      box.innerHTML = `<p style="color:var(--muted)">⏳ Empaquetando y subiendo ZIP ${i + 1}/${chunks.length}…</p>`;
      const zipHandle = await tmpDir.getFileHandle(zipName, { create: true });
      await _streamZip(zipHandle, chunks[i]);
      const zipFile = await zipHandle.getFile();
      const timeoutSec = Math.min(900, Math.max(300, Math.round(zipFile.size / 1048576) * 10));
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeoutSec * 1000);
      let res;
      try {
        res = await fetch(`/api/person/${state.current}/upload-folder`, {
          method: "POST",
          body: (() => { const fd = new FormData(); fd.append("files", zipFile, zipName); return fd; })(),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timeoutId);
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error) {
        rows.push({ ok: false, file: zipName, message: data.error || `HTTP ${res.status}` });
        continue;
      }
      const n = data.files || 0;
      totalUploaded += n;
      rows.push({ ok: true, file: zipName, message: data.message || `${n} archivos`, n });
    }

    // --- PASO 5: purgar carpeta temporal local ---
    await _purgeDir(tmpDir).catch(() => {});
    try { await rootDir.removeEntry(tmpName); } catch (e) { /* noop */ }

    const convNote = converted
      ? `<div class="up-result">🖼️ ${converted} DICOM convertido(s) a ${converted > 1 ? "imágenes/video" : "imagen/video"}</div>`
      : "";
    const dedupNote = deduped
      ? `<div class="up-result">📉 ${deduped} imagen(es) casi idéntica(s) descartada(s) (diferencia < ${minDiffPct}%)</div>`
      : "";
    const zipRows = rows.map((r) =>
      r.ok
        ? `<div class="up-result">✅ <b>${esc(r.file)}</b> — ${esc(r.message)}</div>`
        : `<div class="up-result up-err">❌ <b>${esc(r.file)}</b> — ${esc(r.message)}</div>`
    ).join("");
    const summaryHtml = `<div class="drug-warning sev-border-green"><div class="d-title">✅ Subida completa: ${esc(totalUploaded)} archivo(s)</div></div>${convNote}${dedupNote}${zipRows}`;
    box.innerHTML = summaryHtml;
    toast(totalUploaded > 0 ? "Subida completa — analizando con IA…" : "Subida completa (nada nuevo)", totalUploaded > 0 ? "yellow" : "green");
    state.detail = await api(`/api/person/${state.current}`);
    await loadPersons();
    renderPerson();
    if (totalUploaded > 0) {
      // analizar automáticamente (processStudies regenera el informe principal)
      await processStudies();
    } else {
      ensureAIReports();
    }
  } catch (e) {
    await _purgeDir(tmpDir).catch(() => {});
    try { await rootDir.removeEntry(tmpName); } catch (e2) { /* noop */ }
    if (e.name === "AbortError") {
      box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">⏱ Tiempo de espera agotado</div>
        <div>La subida del ZIP tardó demasiado. Vuelva a intentarlo (reanuda por partes).</div></div>`;
    } else {
      box.innerHTML = `<div class="drug-warning sev-border-red"><div class="d-title">Error de conexión</div>
        <div>${esc(e.message || "Error desconocido")}. Se eliminó la carpeta temporal.</div></div>`;
    }
  } finally {
    _uploadBusy = false;
  }
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

/* ---------------- eliminar paciente ---------------- */

function openDeletePerson() {
  const p = state.detail ? state.detail.person : null;
  if (!p) return;
  const nDocs = (state.detail.documents || []).length;
  $("#delInfo").innerHTML =
    `<p>Se eliminará <b>${esc(p.name)}</b> (${p.n_reports} informe(s), ${nDocs} archivo(s) adjuntos).</p>` +
    `<p style="margin-top:6px;font-weight:600">¿Qué hacer con sus archivos?</p>`;
  document.querySelectorAll('input[name="delMode"]').forEach((r) =>
    r.checked = (r.value === "delete_all"));
  delModeChanged();
  const sel = $("#delTarget");
  sel.innerHTML = `<option value="">— Seleccione paciente —</option>` +
    state.persons.filter((x) => x.id !== state.current)
      .map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("");
  $("#delNewName").value = "";
  $("#delNewDoc").value = "";
  $("#delMsg").innerHTML = "";
  $("#delModal").style.display = "flex";
  // sugerir automáticamente el paciente real al que pertenecen los archivos
  fetch(`/api/person/${state.current}/suggest-target`)
    .then((r) => r.json())
    .then((d) => {
      if (d.candidates && d.candidates.length) {
        const c0 = d.candidates[0];
        if (sel.querySelector(`option[value="${c0.id}"]`)) {
          sel.value = c0.id;
          $("#delMsg").innerHTML =
            `<span style="color:#2e7d32">📎 Sugerencia: los archivos parecen de <b>${esc(c0.name)}</b> (coincidencia por ${c0.match}).</span>`;
        }
      }
    })
    .catch(() => {});
}

function delModeChanged() {
  const mode = document.querySelector('input[name="delMode"]:checked').value;
  $("#delTarget").style.display = mode === "transfer" ? "" : "none";
  $("#delNewFields").style.display = mode === "transfer_new" ? "" : "none";
}

function closeDeletePerson() {
  $("#delModal").style.display = "none";
  $("#delMsg").innerHTML = "";
}

async function confirmDeletePerson() {
  const mode = document.querySelector('input[name="delMode"]:checked').value;
  if (mode === "delete_all" &&
      !confirm("¿Eliminar TODOS los archivos de este paciente? No se puede deshacer.")) {
    return;
  }
  const body = { mode };
  if (mode === "transfer") {
    const t = $("#delTarget").value;
    if (!t) {
      $("#delMsg").innerHTML = `<span style="color:#d32f2f">Elija el paciente de destino.</span>`;
      return;
    }
    body.to_pid = parseInt(t, 10);
  }
  if (mode === "transfer_new") {
    body.name = $("#delNewName").value.trim();
    body.doc = $("#delNewDoc").value.trim();
    if (!body.name) {
      $("#delMsg").innerHTML = `<span style="color:#d32f2f">Escriba el nombre del paciente nuevo.</span>`;
      return;
    }
  }
  const res = await fetch(`/api/person/${state.current}/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    $("#delMsg").innerHTML = `<span style="color:#d32f2f">${esc(data.error || "Error")}</span>`;
    return;
  }
  closeDeletePerson();
  toast("Paciente eliminado", "green");
  state.current = null;
  state.detail = null;
  const panel = $("#personPanel");
  if (panel) {
    panel.innerHTML = `<div style="text-align:center;padding:40px;color:var(--muted)"><h2>Seleccione una persona</h2></div>`;
  }
  await loadPersons();
}

/* ---------------- informes IA en segundo plano ---------------- */

async function ensureAIRepos(pid) {
  try {
    const q = pid ? `?pid=${pid}` : "";
    const res = await fetch("/api/ensure-ai-reports" + q, {
      method: "POST", credentials: "same-origin",
    });
    if (!res.ok) return;
    const d = await res.json();
    if (d.pending && d.pending.length) {
      toast(`🔄 ${d.pending.length} informe(s) IA en segundo plano…`, "yellow");
    }
  } catch (e) { /* silencioso: no bloquear navegación */ }
}

async function pollAIJobs() {
  try {
    const res = await fetch("/api/ai-jobs", { credentials: "same-origin" });
    if (!res.ok) return;
    const d = await res.json();
    const jobs = d.jobs || {};
    if (state.current && jobs[state.current] &&
        jobs[state.current].status === "done") {
      const job = jobs[state.current];
      if (state._lastJobTs !== (job.finished_at || "")) {
        state._lastJobTs = job.finished_at || "";
        const pid = state.current;
        state.detail = await api(`/api/person/${pid}`);
        await loadPersons();
        renderPerson();
        toast("Informe IA actualizado en segundo plano", "green");
      }
    }
  } catch (e) { /* silencioso */ }
}

document.addEventListener("DOMContentLoaded", () => {
  loadPersons();
  ensureAIRepos();
  setInterval(pollAIJobs, 15000);
  document.addEventListener("keydown", _galleryKeydown);
  document.addEventListener("wheel", _galleryWheel, { passive: false });
  const gPan = document.getElementById("galleryPan");
  if (gPan) {
    gPan.addEventListener("mousedown", _galleryPanStart);
    gPan.addEventListener("mousemove", _galleryPanMove);
    gPan.addEventListener("mouseup", _galleryPanEnd);
    gPan.addEventListener("mouseleave", _galleryPanEnd);
    gPan.addEventListener("dblclick", () => galleryResetZoom());
  }
  const gGrid = document.getElementById("galleryThumbsGrid");
  if (gGrid) {
    gGrid.addEventListener("click", (ev) => {
      const t = ev.target.closest("[data-gthumb]");
      if (t) galleryGoto(Number(t.dataset.gthumb));
    });
  }
  const gModal = document.getElementById("galleryModal");
  if (gModal) {
    gModal.addEventListener("mousedown", (ev) => {
      if (ev.target === gModal) closeGallery();
    });
  }
});
