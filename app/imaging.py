# -*- coding: utf-8 -*-
"""Conversión de estudios (DICOM, PDF) a imágenes PNG para el análisis por IA.

- PDF (escaneado o sin texto) -> render de páginas a PNG (fitz/PyMuPDF)
- DICOM (archivo o carpeta de serie) -> ventana/normalización a PNG (pydicom+PIL)
"""

import os

PNG_MIME = "image/png"

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None

try:
    import pydicom
    from pydicom.pixels import apply_voi_lut
except ImportError:  # pragma: no cover
    pydicom = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def pdf_pages_to_png(pdf_path: str, out_dir: str, max_pages: int = 3,
                     dpi: float = 150.0) -> list[str]:
    """Renderiza hasta `max_pages` páginas de un PDF a PNG. Devuelve rutas."""
    if fitz is None:
        return []
    os.makedirs(out_dir, exist_ok=True)
    out: list[str] = []
    try:
        doc = fitz.open(pdf_path)
    except Exception:  # noqa: BLE001
        return []
    try:
        n = min(len(doc), max_pages)
        for i in range(n):
            page = doc[i]
            pix = page.get_pixmap(dpi=int(dpi))
            dest = os.path.join(out_dir, f"page_{i + 1}.png")
            pix.save(dest)
            out.append(dest)
    finally:
        doc.close()
    return out


def _normalize_mono(pixel_array):
    """Normaliza una matriz mono (int16/uint16) a uint8 0-255 (ventana simple)."""
    arr = pixel_array.astype(np.float32)
    lo = np.percentile(arr, 0.5)
    hi = np.percentile(arr, 99.5)
    if hi <= lo:
        hi = lo + 1
    norm = (arr - lo) / (hi - lo) * 255.0
    return np.clip(norm, 0, 255).astype(np.uint8)


def _slice_to_png(ds, dest: str) -> bool:
    """Guarda una instancia DICOM como PNG (aplica VOI LUT cuando es posible)."""
    try:
        if hasattr(ds, "pixel_array") and ds.pixel_array is None:
            return False
        arr = ds.pixel_array
    except Exception:  # noqa: BLE001
        return False
    try:
        if arr.ndim == 2:
            if np is not None:
                img = _normalize_mono(arr)
                # MONOCHROME1 (huesos oscuros): invertir para ver como RX
                photometric = str(getattr(ds, "PhotometricInterpretation", "MONOCHROME2"))
                if photometric.strip().upper() == "MONOCHROME1":
                    img = 255 - img
                im = Image.fromarray(img, mode="L")
            else:  # pragma: no cover
                im = Image.fromarray(arr).convert("L")
        elif arr.ndim == 3:
            im = Image.fromarray(arr).convert("RGB")
        else:
            return False
        im.save(dest)
        return True
    except Exception:  # noqa: BLE001
        return False


def _dicom_files(path: str) -> list[str]:
    if os.path.isdir(path):
        found = []
        for root, _dirs, files in os.walk(path):
            for f in sorted(files):
                if f.lower().endswith((".dcm", ".dicom")) or "." not in f:
                    found.append(os.path.join(root, f))
        found.sort()
        return found
    if path.lower().endswith((".dcm", ".dicom")):
        return [path]
    return []


def dicom_to_png(path: str, out_dir: str, max_slices: int = 3) -> list[str]:
    """Convierte hasta `max_slices` cortes de una serie DICOM a PNG."""
    if pydicom is None or Image is None:
        return []
    os.makedirs(out_dir, exist_ok=True)
    files = _dicom_files(path)
    if not files:
        return []
    # elegir cortes representativos: primero, medio, último
    if len(files) == 1:
        picks = files
    else:
        picks = [files[0], files[len(files) // 2], files[-1]]
    out: list[str] = []
    for i, f in enumerate(picks[:max_slices]):
        try:
            ds = pydicom.dcmread(f)
        except Exception:  # noqa: BLE001
            continue
        if not hasattr(ds, "pixel_array"):
            continue
        dest = os.path.join(out_dir, f"slice_{i + 1}.png")
        if _slice_to_png(ds, dest):
            out.append(dest)
    return out


def study_to_pngs(path: str, out_dir: str, max_images: int = 3) -> list[str]:
    """Convierte un estudio (PDF, imagen, carpeta DICOM) a PNGs para visión."""
    p = os.path.abspath(path)
    if os.path.isfile(p) and p.lower().endswith(".pdf"):
        return pdf_pages_to_png(p, out_dir, max_images)
    if p.lower().endswith((".dcm", ".dicom")):
        return dicom_to_png(p, out_dir, max_images)
    if os.path.isdir(p):
        if _dicom_files(p):
            return dicom_to_png(p, out_dir, max_images)
        # carpeta con imágenes sueltas: usar hasta max_images directamente
        imgs = []
        for f in sorted(os.listdir(p)):
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")):
                imgs.append(os.path.join(p, f))
        return imgs[:max_images]
    return [p]
