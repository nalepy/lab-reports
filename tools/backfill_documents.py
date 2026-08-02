# -*- coding: utf-8 -*-
"""Backfill: crea filas en `documents` para informes ya ingeridos.

La ingesta directa (escaneo de carpeta, db.ingest) copiaba los PDF a
data/library/reports/ y los parseaba a `reports`/`tests`, pero no creaba la
fila en `documents` (eso solo lo hacía la vía de subida web). Sin esa fila el
paciente no ve la tarjeta del archivo ni puede descargarlo.

Este script recorre `reports` y por cada (person_id, stored_path) distinto crea
la fila en `documents` si falta. Idempotente: no duplica.

Uso:  python tools/backfill_documents.py
"""
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DB_PATH = HERE / "data" / "labs.db"


def _absolute_path(p: str) -> str:
    if os.path.isabs(p):
        return p
    return str((HERE / p).resolve())


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT r.person_id, r.stored_path, r.source_file, COUNT(*) n
           FROM reports r
           WHERE r.stored_path != ''
           GROUP BY r.person_id, r.stored_path
           ORDER BY r.person_id, r.stored_path"""
    ).fetchall()

    created = skipped = 0
    for r in rows:
        exists = conn.execute(
            "SELECT id FROM documents WHERE person_id=? AND stored_path=?",
            (r["person_id"], r["stored_path"])).fetchone()
        if exists:
            skipped += 1
            continue
        size = 0
        try:
            size = os.path.getsize(_absolute_path(r["stored_path"]))
        except OSError:
            pass
        orig = r["source_file"] or os.path.basename(r["stored_path"])
        now = __import__("datetime").datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO documents(person_id, orig_filename, stored_path,
                   kind, size, notes, uploaded_at)
               VALUES(?,?,?,?,?,?,?)""",
            (r["person_id"], orig, r["stored_path"], "pdf", size,
             "Informe de laboratorio ingerido (backfill).", now))
        created += 1

    conn.commit()
    total = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    conn.close()
    print(f"documentos creados: {created}")
    print(f"ya existentes (sin tocar): {skipped}")
    print(f"total en `documents`: {total}")


if __name__ == "__main__":
    sys.exit(main())
