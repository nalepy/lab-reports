#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lanzador del Panel de Laboratorio Clínico."""
import os
import sys
import webbrowser
import threading
import time

import uvicorn

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PORT = int(os.environ.get("PORT", "8000"))


def _open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://127.0.0.1:{PORT}")


if __name__ == "__main__":
    print("=" * 60)
    print("  PANEL DE LABORATORIO CLINICO")
    print("  Aplicacion: http://127.0.0.1:%d" % PORT)
    print("  Carpeta monitoreada: G:\\My Drive\\MyFiles\\lab")
    print("  (Cambie con la variable de entorno LAB_FOLDER si es necesario)")
    print("  Detenga con Ctrl+C")
    print("=" * 60)
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run("app.server:app", host="127.0.0.1", port=PORT, log_level="warning")
