# Task: client-side DICOM -> JPEG/WebM converter for a vanilla JS web app

Branch: `dicom-converter` — commit small / push / open a PR. Do NOT touch the base branch.

## Context
The repo is a vanilla-JS + FastAPI medical lab panel (no build system, no bundler, plain
`<script src>` files in `app/static/`). Users upload DICOM studies (x-rays, CT series) which
are big and not viewable in the browser. We want a **client-side** converter that turns DICOM
files into small, natively-viewable media **before** upload.

## Deliverable
Create exactly ONE new file: `app/static/dicom-convert.js` (vanilla JS, IIFE or plain script
that sets `window.DicomConverter`). Do not modify any other file. Also add a short
`orchestrator/tasks/dicom-converter-README.md` in your branch summarizing API + usage + limits.

## Requirements
1. `window.DicomConverter.convert(file: File, opts) -> Promise<{kind, blob, name, meta}>`
   - `kind`: `'image'` (single-frame) | `'video'` (multi-frame) | `'unparsed'` (could not parse)
   - `blob`: a JPEG blob for images (quality ≈ 0.92) or a WebM blob for multi-frame
   - `name`: suggested filename (`*.jpg` / `*.webm`)
   - `meta`: `{ frames, rows, cols, bitsAllocated, photometricInterpretation }`
2. Parse DICOM pixel data WITHOUT external libraries:
   - Uncompressed little-endian, 8-bit and 16-bit (Monochrome1, Monochrome2).
   - Baseline JPEG (JPEG lossy, transfer syntax 1.2.840.10008.1.2.4.50): DICOM stores the
     JPEG stream inline — extract the embedded JPEG bytes (find SOI `FF D8` .. EOI `FF D9`) and
     `createImageBitmap`/`Image` decode it; note: many x-rays use this.
   - Handle `RescaleSlope`/`RescaleIntercept`, and `WindowCenter`/`WindowWidth` (auto-compute
     from min/max of the pixel data when absent).
3. Rendering: monochrome 16-bit -> 8-bit via window/level -> `ImageData` on a canvas ->
   `canvas.toBlob('image/jpeg', 0.92)`.
4. Multi-frame (e.g. CT): render each frame to canvas and record a WebM video using
   `canvas.captureStream()` + `MediaRecorder` (VP8/VP9, ~12-15 fps, capped at ~200 frames; if
   more frames, still record up to 200 and note it in `meta`). Fallback for browsers without
   MediaRecorder/captureStream: return `'unparsed'`.
5. Robust: wrapped in try/catch; any unparseable file returns `{kind:'unparsed'}` (never
   throws). Keep the file small (< 40 KB). No third-party scripts, no import maps.
6. Add at the top a concise header comment (what it does, supported transfer syntaxes,
   that it is lossy and not for primary diagnosis).

## Constraints
- Vanilla JS only, ES5-ish or modern-but-standalone (works as a plain script tag).
- No network, no build step, no npm.
- Do not touch app.js, server.py, or anything else — integration happens later by the
  orchestrator.

## Definition of done
- `app/static/dicom-convert.js` exists, is syntactically valid (`node --check`), and exposes
  `window.DicomConverter.convert`.
- A tiny optional `dicom-convert-test.html` in the branch lets a human pick a `.dcm` and see
  the converted output (paste a real DICOM to test if available; otherwise document).
- Commit small, push, open a PR titled "feat: client-side DICOM converter".
