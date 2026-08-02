# DICOM Converter — Client-side JS

## Overview

`app/static/dicom-convert.js` is a **client-side** DICOM-to-JPEG/WebM
converter. It runs entirely in the browser before upload, so users can
preview DICOM studies (x-rays, CT series, MRIs) as viewable images or
videos without needing a server-side DICOM stack.

## API

```js
window.DicomConverter.convert(file, opts) → Promise<result>
```

| Param | Type | Description |
|-------|------|-------------|
| `file` | `File` | A DICOM `.dcm` file from `<input>` or drag-and-drop. |
| `opts` | `Object` | *Optional.* `{ fps, maxFrames, quality }` — see below. |

### Options

| Key | Default | Description |
|-----|---------|-------------|
| `fps` | `15` | Frame rate for multi-frame → WebM output. |
| `maxFrames` | `200` | Hard cap on video frames (beyond 200, recording stops). |
| `quality` | `0.92` | JPEG quality (0–1) for single-frame output. |

### Result

```ts
{
  kind: 'image' | 'video' | 'unparsed',
  blob: Blob,           // JPEG or WebM blob
  name: string,         // suggested filename (e.g. "study_001.jpg")
  meta: {               // extracted DICOM metadata
    frames: number,
    rows: number,
    cols: number,
    bitsAllocated: number,
    bitsStored: number,
    highBit: number | null,
    photometricInterpretation: string,
    samplesPerPixel: number,
    pixelRepresentation: number,
    rescaleSlope: number | null,
    rescaleIntercept: number | null,
    windowCenter: number | null,
    windowWidth: number | null,
    framesTruncated?: true,
    originalFrames?: number
  }
}
```

If the file cannot be parsed, `kind` is `'unparsed'` and the other
fields are absent. The function **never throws** — all errors are
caught and surfaced as `kind: 'unparsed'`.

## Supported Transfer Syntaxes

| UID | Name | Handling |
|-----|------|----------|
| `1.2.840.10008.1.2` | Implicit VR Little Endian | Raw pixel buffer → window/level → 8-bit |
| `1.2.840.10008.1.2.1` | Explicit VR Little Endian | Same as above |
| `1.2.840.10008.1.2.4.50` | JPEG Baseline (lossy) | Embedded JPEG stream extracted and decoded |
| `1.2.840.10008.1.2.4.51` | JPEG Extended (lossy) | Same — JPEG stream extraction |

Photometric interpretations handled:
- `MONOCHROME1` — inverted (bright = low values)
- `MONOCHROME2` — normal (bright = high values)

## How It Works

1. **DICOM parser**: Reads the binary file with `DataView`, locates the
   `DICM` magic after the 128-byte preamble, then walks data elements
   using the correct VR encoding (determined by TransferSyntaxUID in
   group 0002).

2. **Pixel extraction**: For raw transfer syntaxes, frames are read as
   contiguous pixel buffers. For JPEG transfer syntaxes, the
   encapsulated pixel data is unpacked item-by-item — each item
   contains a standalone JPEG stream.

3. **Windowing**: 16-bit monochrome pixels are mapped to 8-bit
   grayscale using `WindowCenter` / `WindowWidth` (or auto-computed
   min/max when absent), with `RescaleSlope` / `RescaleIntercept`
   applied first.

4. **Single-frame → JPEG**: Pixels rendered to an off-screen
   `<canvas>`, then `canvas.toBlob('image/jpeg', quality)`.

5. **Multi-frame → WebM**: Each frame rendered to a canvas, then
   `canvas.captureStream()` + `MediaRecorder` (VP8/VP9, configurable
   fps) records a video. Falls back to `'unparsed'` in browsers
   without `MediaRecorder` or `captureStream`.

## Limits

- **Lossy output only** — not suitable for primary diagnosis.
- No support for JPEG 2000, JPEG-LS, RLE, or deflated transfer syntaxes.
- RGB/YBR color photometric interpretations are not rendered (JPEG
  streams with embedded color *do* work since the browser decodes
  them).
- Multi-frame capped at 200 frames (~13 seconds at 15 fps).
- No Big-Endian transfer syntax support (rare in practice).
- File must fit in browser memory (no streaming parse).

## Testing

Open `app/static/dicom-convert-test.html` in a browser, pick a `.dcm`
file, and see the converted output with metadata.

Recommended test files:
- [dicomlibrary.com](https://www.dicomlibrary.com) — free anonymous
  DICOM studies (chest x-rays, CT head, etc.)
- [SIIM DICOM samples](https://www.siim.org/page/dicom_downloads)

## Integration

The script exports `window.DicomConverter` as a global. Include it
with a `<script>` tag **before** any code that calls `convert()`:

```html
<script src="dicom-convert.js"></script>
```

No other files are modified — integration with the app's upload flow
happens separately in the orchestrator layer.
