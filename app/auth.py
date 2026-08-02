# -*- coding: utf-8 -*-
"""Autenticación simple: contraseña guardada en hash + cookie de sesión.

Solo hay un usuario (el dueño de los análisis). La contraseña inicial es
'1053020' y se puede cambiar desde la app. No hay registro ni múltiples
usuarios.

El hash se guarda en data/password.hash (SHA-256, una línea).
Las sesiones usan HMAC-SHA256 + timestamp (unix), sin dependencias externas.
"""
import base64
import hashlib
import hmac
import os
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HASH_FILE = DATA_DIR / "password.hash"
SECRET_FILE = DATA_DIR / "session.secret"

COOKIE_NAME = "lab_session"
SESSION_TTL = 86400 * 7  # 7 días

# inicializar secreto de sesión (persiste entre reinicios)
if not SECRET_FILE.exists():
    with open(SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(os.urandom(32).hex())
SESSION_SECRET = open(SECRET_FILE, encoding="utf-8").read().strip().encode("utf-8")


def _read_hash() -> str | None:
    if HASH_FILE.exists():
        return open(HASH_FILE, encoding="utf-8").read().strip()
    return None


def _write_hash(h: str):
    with open(HASH_FILE, "w", encoding="utf-8") as f:
        f.write(h)


def set_password(plain: str):
    """Guarda el hash de la nueva contraseña."""
    h = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    _write_hash(h)


def check_password(plain: str) -> bool:
    """Verifica la contraseña contra el hash almacenado."""
    h = _read_hash()
    if h is None:
        default = "1053020"
        set_password(default)
        return plain == default
    expected = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, h)


def create_session() -> str:
    """Crea un token de sesión firmado: payload|hmac."""
    payload = str(int(time.time()))
    raw = f"{payload}|{_sign(payload)}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")


def validate_session(token: str) -> bool:
    """Verifica que el token de sesión sea válido y no haya expirado."""
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        payload, sig = raw.rsplit("|", 1)
        ts = int(payload)
        if time.time() - ts > SESSION_TTL:
            return False
        return hmac.compare_digest(_sign(payload), sig)
    except (ValueError, base64.binascii.Error):
        return False


def _sign(payload: str) -> str:
    return hmac.new(SESSION_SECRET, payload.encode("utf-8"),
                    hashlib.sha256).hexdigest()
