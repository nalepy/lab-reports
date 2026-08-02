# 🧪 Panel de Laboratorio Clínico

Aplicación web que ingesta informes de laboratorio en PDF (carpeta local + subida
manual), agrupa por paciente, y genera un **informe médico personalizado en
español** para cada persona: tablas comparativas, gráficos de evolución,
evaluación por sistemas, recomendaciones priorizadas por urgencia, análisis de
interacciones de medicamentos y —opcionalmente— un informe redactado por IA.

> ⚠️ **Advertencia**: esta herramienta es un apoyo informativo generado por
> IA. **NO es un médico** y no reemplaza una consulta médica. En caso de
> urgencia, acuda a un servicio de emergencias.

---

## Requisitos

- Python 3.10+
- Instalar dependencias:

```bash
pip install -r requirements.txt
```

## Ejecutar

```bash
python run.py
```

Se abre en <http://127.0.0.1:8000>. La carpeta monitoreada por defecto es
`G:\My Drive\MyFiles\lab`; se puede cambiar con la variable de entorno
`LAB_FOLDER`.

## Fuentes de informes

1. **Carpeta local** `G:\My Drive\MyFiles\lab` — es la **fuente inicial**:
   al escanear, cada PDF se **copia a la biblioteca autocontenida**
   (`data/library/reports/`). La aplicación no depende de esa carpeta después
   del ingreso: toda la información vive en `data/` (base SQLite + archivos),
   por lo que **se puede desplegar en una VM en la nube** copiando el proyecto.
   - Se observa automáticamente (polling cada 30 s) y se re-escanea con el
     botón **"🔄 Buscar archivos nuevos"**.
   - Los archivos duplicados (mismo contenido) se **eliminan del disco**,
     conservando una sola copia.
2. **Subida directa** — en cada pestaña de paciente hay un formulario
   **"📤 Subir estudio médico"** para agregar PDFs, imágenes y otros estudios
   aunque no estén en la carpeta monitoreada. Se deduplican igualmente.

## Estudios e imágenes adjuntos (cualquier tipo)

Cada pestaña de paciente permite **subir cualquier tipo de estudio médico**:

- **PDF de laboratorio** → se ingesta al historial y se analiza.
- **Imágenes** (JPG, PNG, GIF, WEBP, BMP, TIFF) → radiografías, IRM,
  ecografías: se muestran como miniaturas y se pueden abrir en grande.
- **Carpetas DICOM completas** → botón **"📁 Subir carpeta"** con selector de
  carpeta del navegador (preserva subcarpetas y rutas relativas) o un **ZIP**
  con la estructura interna (DICOMDIR, series…). El contenido se lista y cada
  archivo se puede abrir/descargar.
- **Links de dicomlibrary.com** → botón **"🌐 Importar desde dicomlibrary.com"**.
  Se extrae el UID del estudio y se intenta la descarga; como ese sitio **no
  expone API pública**, si la descarga no es posible se muestra una guía clara
  para bajar el ZIP desde su visor y subirlo aquí (se descomprime solo).
- **DICOM** (.dcm) y **otros** (ZIP, DOC, XLS, TXT…) → se guardan y se pueden
  descargar.

Cada documento se puede acompañar de una nota y se puede eliminar. Los
adjuntos no afectan la evaluación de laboratorio: son el archivo clínico.

## Informe por paciente

Cada pestaña contiene:

- **Resumen ejecutivo** (tono: crítico / precaución / favorable).
- **Medicamentos del paciente** + detección de interacciones
  medicamento↔medicamento y medicamento↔laboratorio (rojo/amarillo/verde).
- **Hallazgos anormales** ordenados por severidad, con la fecha de la última
  medición y **fuentes científicas verificables** (PubMed / guías oficiales).
- **Evaluación por sistemas** (metabólico, cardiovascular, renal, hepático,
  tiroideo, hematológico, inflamación, electrolitos).
- **Recomendaciones y próximos pasos** ordenadas por urgencia:
  🔴 urgentes → 🟡 precaución → 🟢 hábitos. Incluyen estadísticas reales
  (DPP, Framingham, INTERHEART, WHO, CDC…) con sus fuentes.
- **Gráficos de evolución temporal** de cada biomarcador (con banda de
  referencia).
- **Tablas comparativas**: últimos resultados vs. referencia, y comparativa
  entre fechas.
- **Historial de informes** y advertencia médica.

El informe usa **siempre la última medición disponible** de cada biomarcador:
si un valor estaba alterado en 2025 pero se normalizó en 2026, no se genera la
alerta (se conserva la tendencia como información).

**Regla de antigüedad (12 meses):** si un resultado está alterado pero su
última medición tiene **más de 12 meses**, no se marca como crítico: se
muestra en amarillo con la advertencia "⏳ hace más de 12 meses — repetir" y
la recomendación de **repetir el análisis** para confirmar el estado actual.
Los análisis que el laboratorio no informó (valor en blanco) se muestran como
**"no realizado"** y no generan alertas.

## Estudios e imágenes adjuntos (cualquier tipo)

Además de los análisis de laboratorio, cada pestaña de paciente permite
**subir cualquier tipo de estudio médico** mediante el botón
**"📤 Subir estudio médico"**:

- **PDF de laboratorio** → se ingesta al historial y se analiza.
- **Imágenes** (JPG, PNG, GIF, WEBP, BMP, TIFF) → radiografías, IRM,
  ecografías, fotos de informes: se muestran como miniaturas y se pueden
  abrir en grande.
- **DICOM** (.dcm) → se guardan y se pueden descargar.
- **Otros** (ZIP, DOC, XLS, TXT…) → se guardan como adjuntos descargables.

Cada documento se puede acompañar de una nota (ej: "Radiografía de tórax",
"IRM rodilla izquierda") y se puede eliminar. Los adjuntos no afectan la
evaluación de laboratorio: son el archivo clínico del paciente.

## Informe con IA (OpenRouter)

Botón **"✨ Generar informe IA"** en la pestaña del paciente. Modelos
seleccionables (solo estos dos):

- **DeepSeek V4 Pro** (`deepseek/deepseek-v4-pro`) — por defecto.
- **Opus 4.8** (`anthropic/claude-opus-4.8`) — alternativa si el usuario no
  queda conforme con el resultado.

Para usar la IA necesita una API key de OpenRouter válida. Cree una en
<https://openrouter.ai/settings/keys> y configúrela en `data/.env`:

```bash
cp data/.env.example data/.env
# editar data/.env y pegar la key:
# OPENROUTER_API_KEY=sk-or-v1-...
```

También puede definir la variable de entorno `OPENROUTER_API_KEY`. Si no hay
key válida, el informe IA cae automáticamente a un **informe local
estructurado** (sin conexión) y la interfaz avisa del motivo.

## Estructura

```
app/
  parser.py       # parsing de PDFs (5 formatos de laboratorio)
  canonical.py    # normalización de nombres de análisis
  db.py           # almacenamiento SQLite + deduplicación + biblioteca
  assessment.py   # motor de evaluación médica (determinista)
  sources.py      # fuentes científicas verificables
  drugs.py        # base de medicamentos e interacciones
  ai_engine.py    # informe IA vía OpenRouter (DeepSeek V4 Pro / Opus 4.8)
  server.py       # API FastAPI + observador + uploads (carpetas DICOM, links)
  static/         # interfaz web (HTML/CSS/JS + Chart.js + marked)
data/             # TODO autocontenido (se copia a la nube)
  labs.db         # base de datos SQLite
  library/reports/    # PDFs de laboratorio copiados (autocontenido)
  uploads/            # estudios/imágenes/carpetas DICOM subidas
  .env            # API key de OpenRouter (opcional)
run.py            # lanzador
```

## Rutas portables (deploy en nube)

Todas las rutas de archivos se guardan en la base **relativas al proyecto**
(`data/library/reports/...`, `data/uploads/...`). Al arrancar, la app **migra
automáticamente** cualquier ruta absoluta antigua (de otra máquina) a forma
relativa, y al servir archivos las resuelve contra la ubicación actual del
proyecto. Por eso el deploy en una VM/cloud es copiar la carpeta completa
(incluida `data/`) y ejecutar `python run.py` — no hay que tocar rutas.

## API principal

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/status` | estado de la carpeta y último escaneo |
| POST | `/api/rescan` | buscar/ingerir archivos nuevos (elimina duplicados) |
| GET | `/api/persons` | lista de pacientes |
| GET | `/api/person/{id}` | detalle + evaluación completa |
| POST | `/api/person/{id}/upload` | subir cualquier estudio (PDF, imagen, DICOM…) |
| POST | `/api/person/{id}/upload-folder` | subir carpeta completa / ZIP DICOM (con subcarpetas) |
| POST | `/api/person/{id}/fetch-dicomlibrary` | importar estudio desde link de dicomlibrary.com |
| GET | `/api/person/{id}/documents` | listar adjuntos del paciente |
| GET | `/api/documents/{id}/file` | abrir/descargar adjunto |
| GET | `/api/documents/{id}/list` | listar contenido de una carpeta adjunta |
| GET | `/api/documents/{id}/file/{ruta}` | servir archivo dentro de una carpeta |
| DELETE | `/api/person/{id}/documents/{id}` | eliminar adjunto |
| GET/POST/DELETE | `/api/person/{id}/meds` | gestionar medicamentos |
| GET | `/api/ai/models` | modelos de IA disponibles |
| POST | `/api/person/{id}/ai-report?model=deepseek\|opus` | generar informe IA |
| GET | `/api/drugs/search?q=` | autocompletado de medicamentos |
