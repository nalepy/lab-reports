# -*- coding: utf-8 -*-
"""SQLite storage: persons, reports, tests, medications, file registry."""
import os
import re
import sqlite3
import hashlib
import threading
from datetime import datetime
from pathlib import Path

from .parser import parse_pdf, person_match_tokens, _UNIT_RE

_REF_POLLUTION_RE = re.compile(
    r"deseable|inferior|hasta|menor\s*a|mayor\s*a|superior\s*a|rango|"
    r"referencia|no\s*deseable", re.I)

# unidad anclada al FINAL del texto: los rangos de referencia terminan con la
# unidad real ("Deseable: Inferior a 150 mg/dL" -> "mg/dL"). _UNIT_RE por sí
# solo hace match por posición (primera coincidencia), lo que devuelve basura
# ("Deseable" -> 'l').
_TRAIL_UNIT_RE = re.compile(r"(?:" + _UNIT_RE.pattern + r")\s*$", re.I)


def _clean_unit(u: str) -> str:
    """Normaliza la unidad contaminada por texto de referencia al parsear.

    Algunos formatos de laboratorio ponen el rango de referencia en la columna
    de unidad (p. ej. 'Deseable: Inferior a 150 mg/dL'). Deja solo la unidad
    real ('mg/dL') sin perder el texto de referencia (que queda en ref_text).
    También corrige un typo histórico del parser ('md/dL' -> 'mg/dL').
    """
    if not u:
        return ""
    if len(u) > 12 or _REF_POLLUTION_RE.search(u):
        # quedarse con la coincidencia MÁS LARGA que termina en el final del
        # texto (la unidad real está al final del rango de referencia)
        best = ""
        for m in _UNIT_RE.finditer(u):
            if m.end() == len(u) and len(m.group(0)) > len(best):
                best = m.group(0).strip()
        u = best
    u = u.replace("md/dL", "mg/dL").replace("md/dl", "mg/dL").strip()
    return u

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # carpeta del proyecto


def _relative_path(p: str) -> str:
    """Convierte una ruta a forma relativa al proyecto (portable a la nube).

    - Si ya es relativa, la devuelve normalizada.
    - Si es absoluta, busca el marcador 'data/library' o 'data/uploads'
      (todo lo almacenado vive bajo data/) y recorta desde ahí.
    """
    if not p:
        return p
    p2 = str(p).replace("\\", "/")
    if not os.path.isabs(p2) and not re.match(r"^[A-Za-z]:", p2):
        return p2
    for marker in ("data/library", "data/uploads"):
        idx = p2.find("/" + marker)
        if idx != -1:
            return p2[idx + 1:]
    try:
        rel = os.path.relpath(p2, str(PROJECT_ROOT))
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except ValueError:
        pass
    return p2


def _normalize_birth(s) -> str:
    """Normaliza una fecha de nacimiento a yyyy-mm-dd (para el date picker).

    Acepta: yyyy-mm-dd, dd-mm-yyyy, dd/mm/yyyy, con 1-2 dígitos por campo.
    Devuelve la entrada sin cambios si no reconoce el formato.
    """
    s = (s or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


def _absolute_path(p: str) -> str:
    """Ruta relativa -> absoluta contra el proyecto actual."""
    if not p:
        return p
    if os.path.isabs(p):
        return p
    return str(PROJECT_ROOT / p.replace("\\", "/"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    doc TEXT DEFAULT '',
    sex TEXT DEFAULT '',
    age INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS person_docs (
    person_id INTEGER,
    doc TEXT PRIMARY KEY,
    FOREIGN KEY(person_id) REFERENCES persons(id)
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER,
    source_file TEXT,
    stored_path TEXT DEFAULT '',
    file_hash TEXT,
    lab TEXT,
    date TEXT,
    date_text TEXT,
    order_code TEXT,
    doctor TEXT,
    age INTEGER,
    sex TEXT,
    notes TEXT DEFAULT '',
    is_document INTEGER DEFAULT 0,
    ingested_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(person_id) REFERENCES persons(id)
);
CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,
    section TEXT,
    name TEXT,
    canonical TEXT,
    value REAL,
    raw_result TEXT,
    unit TEXT,
    ref_low REAL,
    ref_high REAL,
    ref_text TEXT,
    flag TEXT,
    qual TEXT,
    method TEXT,
    FOREIGN KEY(report_id) REFERENCES reports(id)
);
CREATE TABLE IF NOT EXISTS meds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER,
    name TEXT,
    dose TEXT DEFAULT '',
    frequency TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    added_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(person_id) REFERENCES persons(id)
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER,
    orig_filename TEXT,
    stored_path TEXT,
    kind TEXT DEFAULT 'other',
    size INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    uploaded_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(person_id) REFERENCES persons(id)
);
CREATE TABLE IF NOT EXISTS ai_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER,
    model_key TEXT DEFAULT 'deepseek',
    model_label TEXT DEFAULT '',
    content TEXT DEFAULT '',
    generated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(person_id, model_key)
);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha1 TEXT,
    size INTEGER,
    mtime REAL,
    ingested_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reports_person ON reports(person_id);
CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);
CREATE INDEX IF NOT EXISTS idx_tests_report ON tests(report_id);
CREATE INDEX IF NOT EXISTS idx_tests_canonical ON tests(canonical);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.migrate_paths()
        self.migrate_person_metrics()

    def migrate_person_metrics(self):
        """Agrega columnas de datos vitales manuales a persons (idempotente)."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(persons)")}
        adds = {
            "birth_date": "TEXT DEFAULT ''",
            "weight_kg": "REAL",
            "height_cm": "REAL",
            "bp": "TEXT DEFAULT ''",
            "hr": "INTEGER",
            "notes": "TEXT DEFAULT ''",
        }
        changed = False
        for name, ddl in adds.items():
            if name not in cols:
                self.conn.execute(f"ALTER TABLE persons ADD COLUMN {name} {ddl}")
                changed = True
        # normalizar fechas de nacimiento ya guardadas (dd-mm-yyyy -> yyyy-mm-dd)
        rows = self.conn.execute(
            "SELECT id, birth_date FROM persons WHERE birth_date != ''").fetchall()
        for r in rows:
            norm = _normalize_birth(r["birth_date"])
            if norm != r["birth_date"]:
                self.conn.execute(
                    "UPDATE persons SET birth_date=? WHERE id=?", (norm, r["id"]))
                changed = True
        if changed:
            self.conn.commit()
        return changed

    def update_person_metrics(self, pid: int, birth_date: str = "",
                              weight_kg=None, height_cm=None,
                              bp: str = "", hr=None, notes: str = ""):
        """Guarda datos vitales manuales; recalcula la edad si hay nacimiento."""
        age = None
        bd = _normalize_birth(birth_date)
        if bd:
            try:
                born = datetime.strptime(bd, "%Y-%m-%d").date()
                age = (datetime.now().date() - born).days // 365
            except ValueError:
                age = None
        self.conn.execute(
            """UPDATE persons
               SET birth_date=?, weight_kg=?, height_cm=?, bp=?, hr=?,
                   notes=?, age=COALESCE(?, age)
               WHERE id=?""",
            (bd, weight_kg, height_cm, bp, hr, notes, age, pid))
        self.conn.commit()

    def newest_data_at(self, pid: int) -> str | None:
        """Última marca de datos (informe ingerido o documento subido), local."""
        r = self.conn.execute(
            """SELECT MAX(t) AS m FROM (
                 SELECT ingested_at AS t FROM reports WHERE person_id=?
                 UNION ALL
                 SELECT uploaded_at AS t FROM documents WHERE person_id=?
               )""", (pid, pid)).fetchone()
        return r["m"] if r and r["m"] else None

    def close(self):
        with self._lock:
            self.conn.close()

    # ------------------------------------------------------------- helpers

    def _person_by_doc(self, doc: str):
        if not doc:
            return None
        cur = self.conn.execute(
            """SELECT p.* FROM persons p
               LEFT JOIN person_docs d ON d.person_id = p.id
               WHERE p.doc=? OR d.doc=? ORDER BY p.id LIMIT 1""",
            (doc, doc))
        return cur.fetchone()

    def _first_name(self, name: str) -> str:
        n = " ".join(re.sub(r"[^a-z0-9]+", " ",
                     name.strip().lower()).split())
        return n.split()[0] if n else ""

    def _fuzzy_person(self, name: str, doc: str = ""):
        """Match by given name (first token) + doc alias / surname overlap.

        The family shares surnames (ALE, MEZA, ROMERO), so raw token overlap
        is not enough: the given name must agree, and either the doc or at
        least one other token must match.
        """
        if doc:
            p = self._person_by_doc(doc)
            if p:
                return p
        toks = person_match_tokens(name)
        if not toks:
            return None
        given = self._first_name(name)
        rows = self.conn.execute("SELECT * FROM persons").fetchall()
        for p in rows:
            ptoks = person_match_tokens(p["name"])
            if not ptoks:
                continue
            pgiven = self._first_name(p["name"])
            shared = toks & ptoks
            if given and given == pgiven and len(shared) >= 3:
                return p
            # same doc string stored on the person
            if p["doc"] and doc and p["doc"].replace(".", "") == \
                    doc.replace(".", ""):
                return p
        return None

    def _get_or_create_person(self, report) -> int:
        return self._get_or_create_person2(report)[0]

    def _get_or_create_person2(self, report) -> tuple[int, bool]:
        """Resuelve/crea la persona de un informe. Devuelve (id, creada_ahora)."""
        p = self._person_by_doc(report.doc)
        if p is None:
            p = self._fuzzy_person(report.patient_name, report.doc)
        if p is not None:
            # update missing metadata + register doc alias
            upd = {}
            if report.sex and not p["sex"]:
                upd["sex"] = report.sex
            if report.age and not p["age"]:
                upd["age"] = report.age
            if upd:
                sets = ", ".join(f"{k}=?" for k in upd)
                self.conn.execute(
                    f"UPDATE persons SET {sets} WHERE id=?",
                    (*upd.values(), p["id"]))
            if report.doc and report.doc != p["doc"]:
                # register alias so future files match
                try:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO person_docs(person_id, doc) "
                        "VALUES(?,?)", (p["id"], report.doc))
                except sqlite3.IntegrityError:
                    pass
                if not p["doc"]:
                    self.conn.execute(
                        "UPDATE persons SET doc=? WHERE id=?",
                        (report.doc, p["id"]))
            self.conn.commit()
            return p["id"], False
        cur = self.conn.execute(
            "INSERT INTO persons(name, doc, sex, age) VALUES(?,?,?,?)",
            (report.patient_name or "DESCONOCIDO", report.doc, report.sex,
             report.age))
        pid = cur.lastrowid
        if report.doc:
            try:
                self.conn.execute(
                    "INSERT OR IGNORE INTO person_docs(person_id, doc) "
                    "VALUES(?,?)", (pid, report.doc))
            except sqlite3.IntegrityError:
                pass
        self.conn.commit()
        return pid, True

    # ------------------------------------------------------------- ingest

    def report_exists(self, file_hash: str = None, source_file: str = None,
                      date=None, order_code=None) -> bool:
        if file_hash:
            cur = self.conn.execute(
                "SELECT 1 FROM reports WHERE file_hash=?", (file_hash,))
            if cur.fetchone():
                return True
        if date and order_code and source_file:
            cur = self.conn.execute(
                "SELECT 1 FROM reports WHERE date=? AND order_code=? "
                "AND source_file=?", (date, order_code, source_file))
            if cur.fetchone():
                return True
        return False

    def ingest(self, path: str, force: bool = False,
               library_dir: str = None) -> dict:
        """Parse and store one PDF.

        El PDF se COPIA a la biblioteca autocontenida (library_dir) para que
        la aplicación no dependa de la carpeta original (G:\\My Drive...).
        """
        with self._lock:
            return self._ingest_locked(path, force, library_dir)

    def _copy_to_library(self, path, sha, library_dir):
        """Copia el archivo a data/library/reports/ y devuelve la ruta
        RELATIVA (portable). Si no hay library_dir, devuelve ruta relativa
        de la original."""
        if not library_dir:
            return _relative_path(path)
        lib = os.path.join(library_dir, "reports")
        os.makedirs(lib, exist_ok=True)
        fname = os.path.basename(path)
        dest = os.path.join(lib, f"{sha[:12]}_{fname}")
        if not os.path.exists(dest):
            try:
                import shutil
                shutil.copy2(path, dest)
            except OSError:
                return _relative_path(path)
        return _relative_path(dest)

    def _ingest_locked(self, path: str, force: bool, library_dir: str) -> dict:
        fname = os.path.basename(path)
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            return {"ok": False, "file": fname, "error": str(e)}
        sha = hashlib.sha1(raw).hexdigest()
        size = len(raw)
        mtime = os.path.getmtime(path)

        # registry check
        row = self.conn.execute(
            "SELECT * FROM files WHERE path=?", (path,)).fetchone()
        if row and row["sha1"] == sha and not force:
            return {"ok": True, "file": fname, "status": "unchanged",
                    "new_reports": 0}

        # duplicate-content check across files
        dup = self.conn.execute(
            "SELECT path FROM files WHERE sha1=? AND path<>?",
            (sha, path)).fetchone()
        if dup:
            return {"ok": True, "file": fname, "status": "duplicate",
                    "duplicate_of": dup["path"], "new_reports": 0}

        stored_rel = self._copy_to_library(path, sha, library_dir)
        stored_abs = _absolute_path(stored_rel)
        reports = parse_pdf(stored_abs if os.path.exists(stored_abs) else path)
        if not reports:
            self._upsert_file(path, sha, size, mtime)
            return {"ok": True, "file": fname, "status": "unparsed",
                    "new_reports": 0}

        new_count = 0
        person_ids: set[int] = set()
        created_any = False
        for r in reports:
            if not r.patient_name:
                continue
            if self.report_exists(file_hash=sha + f"|{r.lab}|{r.date}"):
                continue
            # dedupe by same person+date+lab (e.g. ORD61211 vs 61211)
            pid, created = self._get_or_create_person2(r)
            person_ids.add(pid)
            created_any = created_any or created
            dup_report = self.conn.execute(
                "SELECT id FROM reports WHERE person_id=? AND date=? AND lab=?",
                (pid, r.date, r.lab)).fetchone()
            if dup_report and not force:
                continue
            cur = self.conn.execute(
                """INSERT INTO reports(person_id, source_file, stored_path,
                       file_hash, lab, date, date_text, order_code, doctor,
                       age, sex, notes, is_document)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, fname, stored_rel, sha + f"|{r.lab}|{r.date}", r.lab,
                 r.date, r.date_text, r.order_code, r.doctor, r.age, r.sex,
                 r.notes, 1 if r.is_document else 0))
            rid = cur.lastrowid
            for s in r.sections:
                for t in s["tests"]:
                    self.conn.execute(
                        """INSERT INTO tests(report_id, section, name, canonical,
                               value, raw_result, unit, ref_low, ref_high,
                               ref_text, flag, qual, method)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (rid, s["name"], t["name"], t["canonical"], t["value"],
                         t["raw_result"], t["unit"], t["ref_low"], t["ref_high"],
                         t["ref_text"], t["flag"], t["qual"], t["method"]))
            new_count += 1
        self.conn.commit()
        self._upsert_file(path, sha, size, mtime)
        # Vincular también como documento adjunto descargable (pestaña del
        # paciente). La vía de subida web ya lo hace; la ingesta directa
        # (escaneo de carpeta) no lo hacía y los PDFs quedaban solo como
        # informes parseados, sin tarjeta de descarga.
        for pid in person_ids:
            exists = self.conn.execute(
                "SELECT id FROM documents WHERE person_id=? AND stored_path=?",
                (pid, stored_rel)).fetchone()
            if not exists:
                self.add_document(pid, fname, stored_rel, "pdf", size,
                                  "Informe de laboratorio ingerido.")
        status = "ok" if new_count else "no_new"
        out = {"ok": True, "file": fname, "status": status,
               "new_reports": new_count,
               "lab": reports[0].lab if reports else ""}
        # a qué paciente(s) se asignaron los informes (para detectar subida
        # en la pestaña equivocada)
        if person_ids:
            out["person_ids"] = sorted(person_ids)
            out["person_id"] = min(person_ids)
            out["created"] = created_any
            nm = self.conn.execute(
                "SELECT name FROM persons WHERE id=?",
                (out["person_id"],)).fetchone()
            out["person_name"] = nm["name"] if nm else ""
        return out

    def _upsert_file(self, path, sha, size, mtime):
        self.conn.execute(
            """INSERT INTO files(path, sha1, size, mtime, ingested_at)
               VALUES(?,?,?,?, datetime('now'))
               ON CONFLICT(path) DO UPDATE SET
                 sha1=excluded.sha1, size=excluded.size, mtime=excluded.mtime,
                 ingested_at=datetime('now')""",
            (path, sha, size, mtime))
        self.conn.commit()

    # ------------------------------------------------------------- queries

    def persons(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT p.*, COUNT(DISTINCT r.id) AS n_reports,
                      COUNT(t.id) AS n_tests,
                      MAX(r.date) AS last_report,
                      MIN(r.date) AS first_report
               FROM persons p
               LEFT JOIN reports r ON r.person_id = p.id
               LEFT JOIN tests t ON t.report_id = r.id
               GROUP BY p.id ORDER BY p.name""").fetchall()
        return [dict(r) for r in rows]

    def person(self, pid: int) -> dict | None:
        r = self.conn.execute(
            """SELECT p.*, COUNT(DISTINCT r.id) AS n_reports,
                      COUNT(t.id) AS n_tests,
                      MAX(r.date) AS last_report,
                      MIN(r.date) AS first_report
               FROM persons p
               LEFT JOIN reports r ON r.person_id = p.id
               LEFT JOIN tests t ON t.report_id = r.id
               WHERE p.id=? GROUP BY p.id""", (pid,)).fetchone()
        return dict(r) if r else None

    def add_person(self, name: str, doc: str = "", sex: str = "", age: str = "") -> int:
        """Crea un paciente nuevo (o devuelve el existente si coincide por doc/nombre)."""
        name = (name or "").strip()
        if not name:
            raise ValueError("El nombre del paciente es obligatorio")
        doc = (doc or "").strip()
        if doc:
            r = self.conn.execute(
                "SELECT id FROM persons WHERE doc=? LIMIT 1", (doc,)).fetchone()
            if r:
                return r["id"]
        tok = self._first_name(name)
        if tok:
            r = self.conn.execute(
                "SELECT id FROM persons WHERE ? != '' AND LOWER(name) LIKE LOWER(?) LIMIT 1",
                (tok, f"%{tok}%")).fetchone()
            if r:
                return r["id"]
        age_i = None
        try:
            age_i = int(age) if str(age).strip() else None
        except ValueError:
            age_i = None
        cur = self.conn.execute(
            "INSERT INTO persons(name, doc, sex, age) VALUES(?,?,?,?)",
            (name, doc, sex.strip().upper()[:1], age_i))
        self.conn.commit()
        return cur.lastrowid

    def delete_person(self, pid: int, to_pid: int | None = None) -> None:
        """Elimina un paciente.

        Si to_pid está presente, sus archivos (documentos, informes+análisis,
        medicamentos, informes IA) se reasignan a ese paciente. Si no, se
        eliminan en cascada junto con el paciente.
        """
        with self._lock:
            if to_pid:
                for table in ("documents", "reports", "meds", "ai_reports"):
                    self.conn.execute(
                        f"UPDATE {table} SET person_id=? WHERE person_id=?",
                        (to_pid, pid))
            else:
                rids = [r["id"] for r in self.conn.execute(
                    "SELECT id FROM reports WHERE person_id=?", (pid,))]
                for rid in rids:
                    self.conn.execute("DELETE FROM tests WHERE report_id=?", (rid,))
                self.conn.execute("DELETE FROM reports WHERE person_id=?", (pid,))
                self.conn.execute("DELETE FROM documents WHERE person_id=?", (pid,))
                self.conn.execute("DELETE FROM meds WHERE person_id=?", (pid,))
                self.conn.execute("DELETE FROM ai_reports WHERE person_id=?", (pid,))
            self.conn.execute("DELETE FROM person_docs WHERE person_id=?", (pid,))
            self.conn.execute("DELETE FROM persons WHERE id=?", (pid,))
            self.conn.commit()

    def last_ai_report_at(self, pid: int) -> str | None:
        r = self.conn.execute(
            "SELECT MAX(generated_at) AS at FROM ai_reports WHERE person_id=?",
            (pid,)).fetchone()
        return r["at"] if r and r["at"] else None

    def report(self, rid: int) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None

    def reports_for(self, pid: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT r.*, GROUP_CONCAT(DISTINCT s.section) AS sections
               FROM reports r
               LEFT JOIN (SELECT report_id, section FROM tests GROUP BY report_id, section) s
                 ON s.report_id = r.id
               WHERE r.person_id=?
               GROUP BY r.id ORDER BY r.date""", (pid,)).fetchall()
        return [dict(r) for r in rows]

    def tests_for(self, pid: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT t.*, r.date, r.lab, r.source_file
               FROM tests t JOIN reports r ON t.report_id = r.id
               WHERE r.person_id=?
               ORDER BY r.date, t.id""", (pid,)).fetchall()
        out = [dict(r) for r in rows]
        for t in out:
            t["unit"] = _clean_unit(t.get("unit", ""))
        return out

    def tests_for_report(self, rid: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tests WHERE report_id=? ORDER BY id", (rid,)).fetchall()
        out = [dict(r) for r in rows]
        for t in out:
            t["unit"] = _clean_unit(t.get("unit", ""))
        return out

    # ------------------------------------------------------------- meds

    def add_med(self, pid: int, name: str, dose="", frequency="", notes="") -> int:
        cur = self.conn.execute(
            "INSERT INTO meds(person_id, name, dose, frequency, notes) "
            "VALUES(?,?,?,?,?)", (pid, name, dose, frequency, notes))
        self.conn.commit()
        return cur.lastrowid

    def del_med(self, pid: int, med_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM meds WHERE id=? AND person_id=?", (med_id, pid))
        self.conn.commit()
        return cur.rowcount > 0

    def meds_for(self, pid: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM meds WHERE person_id=? ORDER BY name", (pid,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- documents

    def add_document(self, pid: int, orig_filename: str, stored_path: str,
                     kind: str = "other", size: int = 0, notes: str = "") -> int:
        rel = _relative_path(stored_path)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # local, igual que ai_reports
        cur = self.conn.execute(
            "INSERT INTO documents(person_id, orig_filename, stored_path, kind, "
            "size, notes, uploaded_at) VALUES(?,?,?,?,?,?,?)",
            (pid, orig_filename, rel, kind, size, notes, now))
        self.conn.commit()
        return cur.lastrowid

    def resolve_path(self, p: str) -> str:
        """Convierte una ruta almacenada (relativa) a absoluta actual."""
        return _absolute_path(p)

    def migrate_paths(self):
        """Normaliza las rutas almacenadas a forma relativa (portable).

        Se ejecuta al arrancar: convierte rutas absolutas viejas
        (C:\\Users\\...\\data\\...) a relativas (data/...).
        """
        changed = 0
        for table, col in (("reports", "stored_path"),
                           ("documents", "stored_path")):
            rows = self.conn.execute(
                f"SELECT id, {col} AS p FROM {table} WHERE {col} != ''").fetchall()
            for r in rows:
                newp = _relative_path(r["p"])
                if newp != r["p"]:
                    self.conn.execute(
                        f"UPDATE {table} SET {col}=? WHERE id=?",
                        (newp, r["id"]))
                    changed += 1
        if changed:
            self.conn.commit()
        return changed

    def documents_for(self, pid: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM documents WHERE person_id=? ORDER BY uploaded_at DESC",
            (pid,)).fetchall()
        return [dict(r) for r in rows]

    def document(self, doc_id: int) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return dict(r) if r else None

    def del_document(self, pid: int, doc_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM documents WHERE id=? AND person_id=?", (doc_id, pid))
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------- ai reports

    def save_ai_report(self, pid: int, model_key: str, model_label: str,
                       content: str):
        # hora local (igual que documents/reports) para comparar vencimiento
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """INSERT OR REPLACE INTO ai_reports(person_id, model_key,
               model_label, content, generated_at)
               VALUES(?,?,?,?,?)""",
            (pid, model_key, model_label, content, now))
        self.conn.commit()

    def load_ai_report(self, pid: int, model_key: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM ai_reports WHERE person_id=? AND model_key=?",
            (pid, model_key)).fetchone()
        return dict(r) if r else None
