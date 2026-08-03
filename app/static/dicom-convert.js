/**
 * dicom-convert.js — Client-side DICOM to JPEG / WebM converter
 * =================================================================
 * Converts DICOM files into browser-viewable media BEFORE upload.
 * Runs entirely in the browser; no server round-trips, no external
 * libraries, no build step.  Vanilla JS, works as a plain <script> tag.
 *
 * Supported transfer syntaxes
 * ---------------------------
 * - 1.2.840.10008.1.2        Implicit VR Little Endian (raw)
 * - 1.2.840.10008.1.2.1      Explicit VR Little Endian (raw)
 * - 1.2.840.10008.1.2.4.50   JPEG Baseline (lossy, embedded stream)
 * - 1.2.840.10008.1.2.4.51   JPEG Extended (lossy, embedded stream)
 * - 1.2.840.10008.1.2.4.57   JPEG Lossless, Process 14 (embedded JS decoder)
 * - 1.2.840.10008.1.2.4.70   JPEG Lossless, Process 14 SV1 (embedded JS decoder)
 *
 * Output
 * ------
 * - Single-frame → JPEG blob  (quality ≈ 0.92)
 * - Multi-frame  → WebM blob  (VP8/VP9, ~15 fps, capped at 200 frames)
 * - Unparseable  → { kind:'unparsed' }
 *
 * ⚠ Lossy conversion — not suitable for primary diagnosis.
 *
 * Exports: window.DicomConverter.convert(file, opts) → Promise<result>
 */

(function () {
  'use strict';

  // ── DICOM tag constants ──────────────────────────────────────────
  // DICOM serializa el tag como GRUPO (LE) seguido de ELEMENTO (LE), así que
  // dv.getUint32(offset, true) devuelve (elemento << 16) | grupo. Las
  // constantes se definen en ESE orden para que la comparación directa con
  // el int leído funcione.
  var TAG_TRANSFER_SYNTAX_UID     = 0x00100002;
  var TAG_ROWS                    = 0x00100028;
  var TAG_COLS                    = 0x00110028;
  var TAG_BITS_ALLOCATED          = 0x01000028;
  var TAG_BITS_STORED             = 0x01010028;
  var TAG_HIGH_BIT                = 0x01020028;
  var TAG_PIXEL_REPRESENTATION    = 0x01030028;
  var TAG_PHOTOMETRIC_INTERP      = 0x00040028;
  var TAG_SAMPLES_PER_PIXEL       = 0x00020028;
  var TAG_PLANAR_CONFIG           = 0x00060028;
  var TAG_NUMBER_OF_FRAMES        = 0x00080028;
  var TAG_PIXEL_DATA              = 0x00107FE0;
  var TAG_RESCALE_SLOPE           = 0x10530028;
  var TAG_RESCALE_INTERCEPT       = 0x10520028;
  var TAG_WINDOW_CENTER           = 0x10500028;
  var TAG_WINDOW_WIDTH            = 0x10510028;
  var TAG_ITEM                    = 0xE000FFFE;
  var TAG_ITEM_DELIMITATION       = 0xE00DFFFE;
  var TAG_SEQUENCE_DELIMITATION   = 0xE0DDFFFE;

  var longVr = { OB:1, OD:1, OF:1, OL:1, OW:1, SQ:1, UC:1, UN:1, UR:1, UT:1 };

  // Transfer syntax UIDs
  var TS_IMPLICIT_VR_LE    = '1.2.840.10008.1.2';
  var TS_EXPLICIT_VR_LE    = '1.2.840.10008.1.2.1';
  var TS_JPEG_BASELINE     = '1.2.840.10008.1.2.4.50';
  var TS_JPEG_EXTENDED     = '1.2.840.10008.1.2.4.51';
  var TS_JPEG_LOSSLESS_14  = '1.2.840.10008.1.2.4.57';
  var TS_JPEG_LOSSLESS_70  = '1.2.840.10008.1.2.4.70';

  // ── Helpers ──────────────────────────────────────────────────────

  function tagToHex(tag) {
    return ('00000000' + tag.toString(16)).slice(-8).toUpperCase();
  }

  // Is this a known JPEG transfer syntax (pixel data = embedded JPEG)?
  function isJpegTS(ts) {
    return ts === TS_JPEG_BASELINE || ts === TS_JPEG_EXTENDED;
  }

  // Is this a JPEG Lossless transfer syntax (decoded with the embedded JS decoder)?
  function isJpegLosslessTS(ts) {
    return ts === TS_JPEG_LOSSLESS_14 || ts === TS_JPEG_LOSSLESS_70;
  }

  // Is this a raw (uncompressed) transfer syntax?
  function isRawTS(ts) {
    return ts === TS_IMPLICIT_VR_LE || ts === TS_EXPLICIT_VR_LE;
  }

  // ── Byte-level parser ────────────────────────────────────────────

  /**
   * DICOM data-element parser.
   * Reads tags from a DataView starting at `offset` until told to stop.
   *
   * @param {DataView} dv
   * @param {number}    offset       Start offset in bytes
   * @param {number}    maxOffset    Do not read beyond this byte
   * @param {boolean}   implicitVR   If true, no VR field (group-0002 always explicit)
   * @param {boolean}   isMeta       True while parsing group 0002 (always explicit VR LE)
   * @param {function}  onTag        Called with { tag, vr, length, offset, valueOffset }
   * @returns {number}  Next offset to read from
   */
  function parseElements(dv, offset, maxOffset, implicitVR, isMeta, onTag) {
    while (offset < maxOffset - 3) {
      var tag = dv.getUint32(offset, true); // little-endian
      offset += 4;

      // Sequence / item delimiters — skip silently
      if (tag === TAG_SEQUENCE_DELIMITATION || tag === TAG_ITEM_DELIMITATION) {
        continue;
      }

      if (tag === 0) break; // padding / end

      // Determine VR
      var vr = null;
      var length;

      if (isMeta || !implicitVR) {
        // Explicit VR
        var vrBytes = String.fromCharCode(dv.getUint8(offset), dv.getUint8(offset + 1));
        offset += 2;

        // OB, OD, OF, OL, OW, SQ, UC, UN, UR, UT → 2 reserved bytes + 4-byte length
        var longVrCodes = { OB:1, OD:1, OF:1, OL:1, OW:1, SQ:1, UC:1, UN:1, UR:1, UT:1 };
        if (longVrCodes[vrBytes]) {
          offset += 2; // skip reserved
          length = dv.getUint32(offset, true);
          offset += 4;
        } else {
          length = dv.getUint16(offset, true);
          offset += 2;
        }
        vr = vrBytes;
      } else {
        // Implicit VR — always 4-byte length
        length = dv.getUint32(offset, true);
        offset += 4;
      }

      var valueOffset = offset;

      if (length === 0xFFFFFFFF) {
        // Undefined length — SQ or encapsulated pixel data
        length = -1;
      }

      onTag({
        tag: tag,
        vr: vr,
        length: length,
        offset: valueOffset,
        valueOffset: valueOffset
      });

      if (length === -1) {
        // For sequences / encapsulated data, the caller must handle
        // item-by-item parsing. We signal this and let the callback
        // advance offset itself.
        // The callback returns the new offset.
        break; // caller must drive further parsing
      }

      offset = valueOffset + length;
    }
    return offset;
  }

  /**
   * Walk all DICOM data elements (flat — does NOT recurse into SQ).
   * Returns a dict of tag→value for scalar tags and the raw pixel-data info.
   */
  function parseDicom(dv) {
    var fileSize = dv.byteLength;

    // 1. Skip 128-byte preamble, check "DICM" magic
    if (fileSize < 132) return null;
    var magic = '';
    for (var i = 128; i < 132; i++) {
      magic += String.fromCharCode(dv.getUint8(i));
    }
    if (magic !== 'DICM') return null;

    var offset = 132;
    var tags = {};
    var pixelDataInfo = null; // { offset, length, isEncapsulated }
    var transferSyntax = null;

    // 2. Parse group 0002 (meta header) — always Explicit VR LE
    var metaEnd = fileSize;
    var traverseElements = function (start, end, implicit, isMeta) {
      var pos = start;
      while (pos < end - 3) {
        var tg = dv.getUint32(pos, true);
        pos += 4;
        if (tg === TAG_SEQUENCE_DELIMITATION || tg === TAG_ITEM_DELIMITATION) continue;
        if (tg === 0) break;

        var vr, len;
        if (isMeta || !implicit) {
          var vrStr = String.fromCharCode(dv.getUint8(pos), dv.getUint8(pos + 1));
          pos += 2;
          var longVr = { OB:1, OD:1, OF:1, OL:1, OW:1, SQ:1, UC:1, UN:1, UR:1, UT:1 };
          if (longVr[vrStr]) {
            pos += 2;
            len = dv.getUint32(pos, true);
            pos += 4;
          } else {
            len = dv.getUint16(pos, true);
            pos += 2;
          }
        } else {
          len = dv.getUint32(pos, true);
          pos += 4;
        }

        var valOff = pos;

        // Capture known tags
        if (tg === TAG_TRANSFER_SYNTAX_UID && !transferSyntax) {
          transferSyntax = readString(dv, valOff, len);
        }

        if (len !== 0xFFFFFFFF) {
          // Scalar — store value
          tags[tg] = { offset: valOff, length: len, vr: isMeta ? vrStr : null };

          if (tg === TAG_PIXEL_DATA) {
            pixelDataInfo = { offset: valOff, length: len, isEncapsulated: false };
          }
        } else {
          // Undefined length (SQ or encapsulated pixel data)
          if (tg === TAG_PIXEL_DATA) {
            pixelDataInfo = { offset: valOff, length: -1, isEncapsulated: true };
            pos = valOff; // positioned at start of items — caller handles item parsing
            break; // exit loop; pixel data parsing is done separately
          } else {
            // SQ — skip by scanning to sequence delimiter
            pos = skipSequence(dv, valOff, fileSize);
          }
          continue;
        }

        pos = valOff + len;
      }
    };

    traverseElements(132, fileSize, true, true);  // first pass: meta (explicit VR, implicit for rest until we know TS)

    // We only parsed meta in the first pass if we stopped at pixel data.
    // Re-parse everything properly now that we know the transfer syntax.
    // Actually, let me redo this more cleanly.

    return { tags: tags, pixelDataInfo: pixelDataInfo, transferSyntax: transferSyntax, fileSize: fileSize };
  }

  /**
   * Full two-pass DICOM parser.
   * Pass 1: parse group 0002 (explicit VR LE) to get TransferSyntaxUID.
   * Pass 2: use correct VR mode to harvest all tags + pixel data location.
   */
  function parseDicomFull(dv) {
    var fileSize = dv.byteLength;
    if (fileSize < 132) return null;

    // Check magic
    var magic = '';
    for (var i = 128; i < 132; i++) magic += String.fromCharCode(dv.getUint8(i));
    if (magic !== 'DICM') return null;

    // ── Pass 1: group 0002 (always Explicit VR LE) ─────────────────
    var pos = 132;
    var transferSyntax = null;
    var metaEnd = 132;

    while (pos < fileSize - 3) {
      var tg = dv.getUint32(pos, true);
      pos += 4;
      // DICOM: grupo en los 16 bits bajos (se serializa primero en LE)
      var group = tg & 0xFFFF;

      if (group !== 0x0002) {
        // Reached end of meta header
        metaEnd = pos - 4;
        break;
      }

      if (tg === 0) break;

      var vrBytes = String.fromCharCode(dv.getUint8(pos), dv.getUint8(pos + 1));
      pos += 2;
      var len;
      if (longVr[vrBytes]) {
        pos += 2;
        len = dv.getUint32(pos, true);
        pos += 4;
      } else {
        len = dv.getUint16(pos, true);
        pos += 2;
      }

      if (tg === TAG_TRANSFER_SYNTAX_UID) {
        transferSyntax = readString(dv, pos, len);
      }
      pos += len;
    }

    // ── Pass 2: data set (implicit or explicit based on TS) ────────
    var implicitVR = (transferSyntax === TS_IMPLICIT_VR_LE);
    pos = metaEnd;
    var tags = {};
    var pixelDataInfo = null;

    while (pos < fileSize - 3) {
      tg = dv.getUint32(pos, true);
      pos += 4;
      if (tg === TAG_SEQUENCE_DELIMITATION || tg === TAG_ITEM_DELIMITATION) continue;
      if (tg === 0) break;

      var vr = null;
      var len;
      if (!implicitVR) {
        var vrStr = String.fromCharCode(dv.getUint8(pos), dv.getUint8(pos + 1));
        pos += 2;
        if (longVr[vrStr]) {
          pos += 2;
          len = dv.getUint32(pos, true);
          pos += 4;
        } else {
          len = dv.getUint16(pos, true);
          pos += 2;
        }
        vr = vrStr;
      } else {
        len = dv.getUint32(pos, true);
        pos += 4;
      }

      var valOff = pos;

      if (len !== 0xFFFFFFFF) {
        tags[tg] = { offset: valOff, length: len, vr: vr };
        if (tg === TAG_PIXEL_DATA) {
          pixelDataInfo = { offset: valOff, length: len, isEncapsulated: false };
        }
        pos = valOff + len;
      } else {
        if (tg === TAG_PIXEL_DATA) {
          pixelDataInfo = { offset: valOff, length: -1, isEncapsulated: true };
          // Don't advance — pixel data parsing handles items
          pos = valOff; // positioned at first item or delimiter
          // We need to not break but also not try to read the next tag from here.
          // Mark that we're done with regular tag parsing.
          break;
        } else {
          // Skip SQ
          pos = skipSequence(dv, valOff, fileSize, implicitVR);
        }
      }
    }

    return { tags: tags, pixelDataInfo: pixelDataInfo, transferSyntax: transferSyntax };
  }

  function skipSequence(dv, start, maxOffset, implicitVR) {
    var pos = start;
    var depth = 0;
    while (pos < maxOffset - 7) {
      var tg = dv.getUint32(pos, true);
      if (tg === TAG_SEQUENCE_DELIMITATION) {
        if (depth === 0) return pos + 8;
        depth--;
        pos += 8;
      } else if (tg === TAG_ITEM) {
        var ilen = dv.getUint32(pos + 4, true);
        pos += 8;
        if (ilen === 0xFFFFFFFF) {
          depth++;
        } else {
          pos += ilen;
        }
      } else if (tg === TAG_ITEM_DELIMITATION) {
        if (depth > 0) depth--;
        pos += 8;
      } else if (tg === 0) {
        break;
      } else {
        // Element inside an item: parse its header to skip the value.
        if (!implicitVR) {
          var vrs = String.fromCharCode(dv.getUint8(pos + 4), dv.getUint8(pos + 5));
          if (longVr[vrs]) {
            var elen = dv.getUint32(pos + 8, true);
            if (elen === 0xFFFFFFFF) {
              if (vrs === 'SQ' || vrs === 'UN') {
                pos = skipSequence(dv, pos + 12, maxOffset, implicitVR);
                continue;
              }
              pos += 12;
            } else {
              pos += 12 + elen;
            }
          } else {
            pos += 8 + dv.getUint16(pos + 6, true);
          }
        } else {
          var ilen2 = dv.getUint32(pos + 4, true);
          if (ilen2 === 0xFFFFFFFF) {
            pos = skipSequence(dv, pos + 8, maxOffset, implicitVR);
            continue;
          }
          pos += 8 + ilen2;
        }
      }
    }
    return pos;
  }

  function readString(dv, offset, length) {
    var s = '';
    var end = offset + length;
    for (var i = offset; i < end; i++) {
      var c = dv.getUint8(i);
      if (c === 0) break; // null-terminated or padded
      s += String.fromCharCode(c);
    }
    return s.trim();
  }

  /**
   * Read a numeric tag value (DS/IS/US/SS/UL/SL/FL/FD → float or int).
   */
  function readNumericTag(dv, info) {
    if (!info || info.length === 0) return null;
    var vr = info.vr;
    var off = info.offset;

    if (vr === 'DS' || vr === 'IS') {
      return parseFloat(readString(dv, off, info.length));
    }
    if (vr === 'US') return dv.getUint16(off, true);
    if (vr === 'SS') return dv.getInt16(off, true);
    if (vr === 'UL') return dv.getUint32(off, true);
    if (vr === 'SL') return dv.getInt32(off, true);
    if (vr === 'FL') return dv.getFloat32(off, true);
    if (vr === 'FD') return dv.getFloat64(off, true);

    // Fallback: try as string-encoded number (common for DS/IS even without VR)
    return parseFloat(readString(dv, off, info.length));
  }

  function readIntTag(dv, info) {
    var v = readNumericTag(dv, info);
    return (v === null || isNaN(v)) ? null : Math.round(v);
  }

  // ── Pixel data extraction ────────────────────────────────────────

  /**
   * Find SOI (FF D8) in a byte buffer, starting at `start`.
   * Returns offset or -1.
   */
  function findJpegSOI(bytes, start) {
    for (var i = start; i < bytes.length - 1; i++) {
      if (bytes[i] === 0xFF && bytes[i + 1] === 0xD8) return i;
    }
    return -1;
  }

  /**
   * Find EOI (FF D9) in a byte buffer, starting at `start`.
   * Returns offset (points to FF) or -1.
   */
  function findJpegEOI(bytes, start) {
    for (var i = start; i < bytes.length - 1; i++) {
      if (bytes[i] === 0xFF && bytes[i + 1] === 0xD9) {
        // Verify it's really EOI: next byte should not be 0x00 (stuffed FF)
        // Actually FF D9 is always EOI, no stuffing applies to markers FF D0-FF D9
        return i;
      }
    }
    return -1;
  }

  /**
   * Extract JPEG frames from encapsulated pixel data.
   * Returns array of { offset, length } or null.
   */
  function extractJpegFrames(dv, pixelDataOffset, maxOffset) {
    var pos = pixelDataOffset;
    var frames = [];

    while (pos < maxOffset - 7) {
      var tg = dv.getUint32(pos, true);
      pos += 4;
      var len = dv.getUint32(pos, true);
      pos += 4;

      if (tg === TAG_SEQUENCE_DELIMITATION) break;
      if (tg === TAG_ITEM) {
        if (len === 0xFFFFFFFF) len = 0;
        if (len > 0 && pos + len <= maxOffset) {
          frames.push({ offset: pos, length: len });
        }
        pos += len;
      } else {
        // Unexpected tag — stop
        break;
      }
    }

    return frames;
  }

  /**
   * Extract raw (uncompressed) pixel frames.
   * Frame size = rows * cols * (bitsAllocated/8) * samplesPerPixel.
   */
  function extractRawFrames(dv, pixelDataOffset, pixelDataLength, meta) {
    var bytesPerPixel = (meta.bitsAllocated / 8) * (meta.samplesPerPixel || 1);
    var frameSize = meta.rows * meta.cols * bytesPerPixel;
    var numFrames = meta.frames || 1;
    var frames = [];

    for (var f = 0; f < numFrames; f++) {
      var off = pixelDataOffset + f * frameSize;
      if (off + frameSize <= pixelDataOffset + pixelDataLength) {
        frames.push({ offset: off, length: frameSize });
      }
    }

    return frames;
  }

  // ── Window / level computation ────────────────────────────────────

  function computeWindow(meta, pixelMin, pixelMax) {
    var wc = meta.windowCenter;
    var ww = meta.windowWidth;

    if (wc === null || ww === null || ww <= 0) {
      // Auto-compute from data range
      wc = (pixelMin + pixelMax) / 2;
      ww = pixelMax - pixelMin;
      if (ww <= 0) ww = 1;
    }

    var wlMin = wc - ww / 2;
    var wlMax = wc + ww / 2;

    return { min: wlMin, max: wlMax, center: wc, width: ww };
  }

  // ── Rendering ─────────────────────────────────────────────────────

  /**
   * Read raw (un-rescaled) pixel values from a DICOM buffer frame.
   * Returns Int32Array of stored pixel values.
   */
  function buildRawPixels(dv, frame, meta) {
    var rows = meta.rows;
    var cols = meta.cols;
    var bitsAllocated = meta.bitsAllocated;
    var pixelCount = rows * cols;
    var isSigned = meta.pixelRepresentation === 1;
    var pixels = new Int32Array(pixelCount);

    if (bitsAllocated === 16) {
      for (var i = 0; i < pixelCount; i++) {
        var raw = dv.getUint16(frame.offset + i * 2, true);
        if (isSigned && raw >= 32768) raw = raw - 65536;
        pixels[i] = raw;
      }
    } else {
      // 8-bit (or other — treat as 8)
      for (var j = 0; j < pixelCount; j++) {
        var r = dv.getUint8(frame.offset + j);
        if (isSigned && r >= 128) r = r - 256;
        pixels[j] = r;
      }
    }
    return pixels;
  }

  /**
   * Render a 16-bit (or 8-bit) pixel array to an ImageData, applying
   * RescaleSlope/Intercept and the window/level from the DICOM meta.
   * `pixels` is mutated in place (rescale applied).
   */
  function renderPixelArray(pixels, meta) {
    var rows = meta.rows;
    var cols = meta.cols;
    var pixelCount = rows * cols;
    var pixelMin = Infinity;
    var pixelMax = -Infinity;

    // Apply RescaleSlope / RescaleIntercept
    var slope = meta.rescaleSlope !== null ? meta.rescaleSlope : 1;
    var intercept = meta.rescaleIntercept !== null ? meta.rescaleIntercept : 0;
    for (var k = 0; k < pixelCount; k++) {
      pixels[k] = pixels[k] * slope + intercept;
      if (pixels[k] < pixelMin) pixelMin = pixels[k];
      if (pixels[k] > pixelMax) pixelMax = pixels[k];
    }

    // Compute window
    var wl = computeWindow(meta, pixelMin, pixelMax);

    // Map to 0-255
    var imageData = new ImageData(cols, rows);
    var data = imageData.data;
    var wlRange = wl.max - wl.min;
    if (wlRange <= 0) wlRange = 1;

    for (var m = 0; m < pixelCount; m++) {
      var val = (pixels[m] - wl.min) / wlRange;
      if (val < 0) val = 0;
      if (val > 1) val = 1;

      // Monochrome1 → invert (bright = low values)
      if (meta.photometricInterpretation === 'MONOCHROME1') {
        val = 1 - val;
      }

      var gray = Math.round(val * 255);
      data[m * 4]     = gray; // R
      data[m * 4 + 1] = gray; // G
      data[m * 4 + 2] = gray; // B
      data[m * 4 + 3] = 255;  // A
    }

    return imageData;
  }

  /**
   * Render a raw monochrome frame to an ImageData.
   */
  function renderMonochrome(dv, frame, meta) {
    return renderPixelArray(buildRawPixels(dv, frame, meta), meta);
  }

  /**
   * Decode a JPEG Lossless frame (transfer syntax 1.2.840.10008.1.2.4.57/.70)
   * with the embedded JS decoder. Returns a Uint16Array of stored values.
   */
  function decodeLosslessFrame(dv, frame) {
    var bytes = new Uint8Array(dv.buffer, dv.byteOffset + frame.offset, frame.length);
    var decoder = new window.JpegLosslessDecoder.Decoder();
    var out = decoder.decompress(bytes, 0);
    return new Uint16Array(out);
  }

  /**
   * Render a single frame to a canvas and return the canvas.
   * mode: 'jpeg' (browser-decoded), 'lossless' (JS-decoded), 'raw' (uncompressed).
   */
  function renderFrame(dv, frame, meta, mode) {
    return new Promise(function (resolve, reject) {
      var canvas = document.createElement('canvas');
      canvas.width = meta.cols;
      canvas.height = meta.rows;
      var ctx = canvas.getContext('2d');

      if (mode === 'jpeg') {
        // JPEG frame — decode via Blob + createImageBitmap
        var jpegBytes = new Uint8Array(dv.buffer, dv.byteOffset + frame.offset, frame.length);
        var blob = new Blob([jpegBytes], { type: 'image/jpeg' });
        createImageBitmap(blob).then(function (bmp) {
          ctx.drawImage(bmp, 0, 0, meta.cols, meta.rows);
          bmp.close();
          resolve(canvas);
        }).catch(function () {
          // Fallback: try Image element
          var img = new Image();
          var url = URL.createObjectURL(blob);
          img.onload = function () {
            ctx.drawImage(img, 0, 0, meta.cols, meta.rows);
            URL.revokeObjectURL(url);
            resolve(canvas);
          };
          img.onerror = function () {
            URL.revokeObjectURL(url);
            reject(new Error('JPEG decode failed'));
          };
          img.src = url;
        });
      } else if (mode === 'lossless') {
        // JPEG Lossless — decode with the embedded JS decoder, then render
        // the 16-bit values through the same window/level path as raw data.
        var u16 = decodeLosslessFrame(dv, frame);
        var isSigned = meta.pixelRepresentation === 1;
        var pixels = new Int32Array(u16.length);
        for (var i = 0; i < u16.length; i++) {
          var v = u16[i];
          if (isSigned && v >= 32768) v = v - 65536;
          pixels[i] = v;
        }
        var imageData = renderPixelArray(pixels, meta);
        ctx.putImageData(imageData, 0, 0);
        resolve(canvas);
      } else {
        // Raw monochrome → ImageData → canvas
        var rawImageData = renderMonochrome(dv, frame, meta);
        ctx.putImageData(rawImageData, 0, 0);
        resolve(canvas);
      }
    });
  }

  // ── Video recording ───────────────────────────────────────────────

  function recordVideo(frames, dv, meta, mode, opts) {
    return new Promise(function (resolve, reject) {
      var fps = (opts && opts.fps) || 15;
      var maxFrames = (opts && opts.maxFrames) || 200;
      var capped = Math.min(frames.length, maxFrames);

      // Check browser support
      var testCanvas = document.createElement('canvas');
      testCanvas.width = meta.cols;
      testCanvas.height = meta.rows;
      var stream;
      try {
        stream = testCanvas.captureStream(0);
      } catch (e) {
        return resolve(null); // not supported
      }
      if (!stream || typeof MediaRecorder === 'undefined') {
        return resolve(null);
      }

      var chunks = [];
      var mimeType = 'video/webm;codecs=vp9';
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = 'video/webm;codecs=vp8';
        if (!MediaRecorder.isTypeSupported(mimeType)) {
          mimeType = 'video/webm';
        }
      }

      var recorder;
      try {
        recorder = new MediaRecorder(stream, { mimeType: mimeType });
      } catch (e) {
        return resolve(null);
      }

      recorder.ondataavailable = function (e) {
        if (e.data && e.data.size > 0) chunks.push(e.data);
      };

      recorder.onstop = function () {
        var blob = new Blob(chunks, { type: mimeType });
        // Stop all tracks
        stream.getTracks().forEach(function (t) { t.stop(); });
        resolve(blob);
      };

      recorder.onerror = function () {
        stream.getTracks().forEach(function (t) { t.stop(); });
        resolve(null);
      };

      recorder.start();

      var frameIndex = 0;
      var canvas = testCanvas;
      var ctx = canvas.getContext('2d');

      function drawNext() {
        if (frameIndex >= capped) {
          recorder.stop();
          return;
        }

        renderFrame(dv, frames[frameIndex], meta, mode).then(function (c) {
          // Copy rendered canvas to the recording canvas
          ctx.clearRect(0, 0, meta.cols, meta.rows);
          ctx.drawImage(c, 0, 0);
          frameIndex++;

          if (frameIndex < capped) {
            setTimeout(drawNext, Math.round(1000 / fps));
          } else {
            // Give recorder time to grab last frame
            setTimeout(function () { recorder.stop(); }, 100);
          }
        }).catch(function () {
          frameIndex++;
          if (frameIndex < capped) {
            setTimeout(drawNext, Math.round(1000 / fps));
          } else {
            setTimeout(function () { recorder.stop(); }, 100);
          }
        });
      }

      drawNext();
    });
  }

  // ── Public API ────────────────────────────────────────────────────

  /**
   * Convert a DICOM file.
   *
   * @param {File}   file   The DICOM file from an <input> or drop event.
   * @param {Object} [opts] Optional settings:
   *   - fps: frames per second for video (default 15)
   *   - maxFrames: max video frames (default 200)
   *   - quality: JPEG quality 0-1 (default 0.92)
   * @returns {Promise<{kind, blob, name, meta}>}
   */
  function convert(file, opts) {
    opts = opts || {};

    return new Promise(function (resolve) {
      try {
        var reader = new FileReader();
        reader.onload = function () {
          try {
            var buffer = reader.result;
            var dv = new DataView(buffer);
            var parsed = parseDicomFull(dv);

            if (!parsed) {
              return resolve({ kind: 'unparsed', dicom: false });
            }

            if (!parsed.pixelDataInfo) {
              return resolve({ kind: 'unparsed', dicom: true });
            }

            var tags = parsed.tags;
            var pdInfo = parsed.pixelDataInfo;
            var ts = parsed.transferSyntax;

            // Extract metadata
            var rows = readIntTag(dv, tags[TAG_ROWS]) || 0;
            var cols = readIntTag(dv, tags[TAG_COLS]) || 0;
            var bitsAllocated = readIntTag(dv, tags[TAG_BITS_ALLOCATED]) || 8;
            var bitsStored = readIntTag(dv, tags[TAG_BITS_STORED]) || bitsAllocated;
            var highBit = readIntTag(dv, tags[TAG_HIGH_BIT]);
            var pixelRep = readIntTag(dv, tags[TAG_PIXEL_REPRESENTATION]) || 0;
            var photometric = tags[TAG_PHOTOMETRIC_INTERP]
              ? readString(dv, tags[TAG_PHOTOMETRIC_INTERP].offset, tags[TAG_PHOTOMETRIC_INTERP].length) : 'MONOCHROME2';
            var samplesPerPixel = readIntTag(dv, tags[TAG_SAMPLES_PER_PIXEL]) || 1;
            var numberOfFrames = readIntTag(dv, tags[TAG_NUMBER_OF_FRAMES]) || 1;

            var rescaleSlope = tags[TAG_RESCALE_SLOPE] ? readNumericTag(dv, tags[TAG_RESCALE_SLOPE]) : null;
            var rescaleIntercept = tags[TAG_RESCALE_INTERCEPT] ? readNumericTag(dv, tags[TAG_RESCALE_INTERCEPT]) : null;
            // WindowCenter / WindowWidth may have multiple values (separated by backslash)
            var wcTag = tags[TAG_WINDOW_CENTER];
            var wwTag = tags[TAG_WINDOW_WIDTH];
            var wcRaw = wcTag ? parseFloat(readString(dv, wcTag.offset, wcTag.length).split('\\')[0]) : null;
            var wwRaw = wwTag ? parseFloat(readString(dv, wwTag.offset, wwTag.length).split('\\')[0]) : null;

            if (rows === 0 || cols === 0) {
              return resolve({ kind: 'unparsed', dicom: true });
            }

            var meta = {
              frames: numberOfFrames,
              rows: rows,
              cols: cols,
              bitsAllocated: bitsAllocated,
              bitsStored: bitsStored,
              highBit: highBit,
              pixelRepresentation: pixelRep,
              photometricInterpretation: photometric,
              samplesPerPixel: samplesPerPixel,
              rescaleSlope: rescaleSlope,
              rescaleIntercept: rescaleIntercept,
              windowCenter: isNaN(wcRaw) ? null : wcRaw,
              windowWidth: isNaN(wwRaw) ? null : wwRaw
            };

            // Extract frames
            var jpeg = isJpegTS(ts);
            var lossless = isJpegLosslessTS(ts);
            var raw = isRawTS(ts);
            var frames;

            if ((jpeg || lossless) && pdInfo.isEncapsulated) {
              frames = extractJpegFrames(dv, pdInfo.offset, dv.byteLength);
            } else if (raw && !pdInfo.isEncapsulated) {
              frames = extractRawFrames(dv, pdInfo.offset, pdInfo.length, meta);
            } else if (!pdInfo.isEncapsulated && pdInfo.length > 0) {
              // Unknown transfer syntax but uncompressed — try raw
              frames = extractRawFrames(dv, pdInfo.offset, pdInfo.length, meta);
            } else {
              return resolve({ kind: 'unparsed', dicom: true });
            }

            if (!frames || frames.length === 0) {
              return resolve({ kind: 'unparsed', dicom: true });
            }

            // Limit frames
            var maxFrames = opts.maxFrames || 200;
            if (frames.length > maxFrames) {
              frames = frames.slice(0, maxFrames);
              meta.framesTruncated = true;
              meta.originalFrames = numberOfFrames;
            }

            // Render mode: 'jpeg' | 'lossless' | 'raw'
            var mode = jpeg ? 'jpeg' : (lossless ? 'lossless' : 'raw');

            // Single frame → JPEG
            if (frames.length === 1) {
              renderFrame(dv, frames[0], meta, mode).then(function (canvas) {
                var quality = opts.quality !== undefined ? opts.quality : 0.92;
                canvas.toBlob(function (blob) {
                  var baseName = (file.name || 'dicom').replace(/\.dcm$/i, '');
                  resolve({
                    kind: 'image',
                    blob: blob,
                    name: baseName + '.jpg',
                    meta: meta
                  });
                }, 'image/jpeg', quality);
              }).catch(function () {
                resolve({ kind: 'unparsed', dicom: true });
              });
              return;
            }

            // Multi-frame → WebM
            recordVideo(frames, dv, meta, mode, opts).then(function (videoBlob) {
              if (!videoBlob) {
                return resolve({ kind: 'unparsed', dicom: true });
              }
              var baseName = (file.name || 'dicom').replace(/\.dcm$/i, '');
              resolve({
                kind: 'video',
                blob: videoBlob,
                name: baseName + '.webm',
                meta: meta
              });
            }).catch(function () {
              resolve({ kind: 'unparsed', dicom: true });
            });

          } catch (e) {
            resolve({ kind: 'unparsed', dicom: true });
          }
        };

        reader.onerror = function () {
          resolve({ kind: 'unparsed', dicom: true });
        };

        reader.readAsArrayBuffer(file);
      } catch (e) {
        resolve({ kind: 'unparsed', dicom: true });
      }
    });
  }

  // ── Embedded JPEG Lossless decoder (jpeg-lossless-decoder-js v2.1.2) ──
  // jpeg-lossless-decoder-js v2.1.2 (rii-mango) — ESM→IIFE, minified.
  // Decodes JPEG Lossless (Process 14) transfer syntaxes .4.57 / .4.70.
  (function () {
    'use strict';
var S=Object.defineProperty;var A=(a,t)=>{for(var e in t)S(a,e,{get:t[e],enumerable:!0})};var w={hSamp:0,quantTableSel:0,vSamp:0};var b=class{buffer;index;constructor(t,e,r){this.buffer=new Uint8Array(t,e,r),this.index=0}get16(){let t=(this.buffer[this.index]<<8)+this.buffer[this.index+1];return this.index+=2,t}get8(){let t=this.buffer[this.index];return this.index+=1,t}};var x=class{dimX=0;dimY=0;numComp=0;precision=0;components=[];read(t){let e=0,r,o=t.get16();e+=2,this.precision=t.get8(),e+=1,this.dimY=t.get16(),e+=2,this.dimX=t.get16(),e+=2,this.numComp=t.get8(),e+=1;for(let s=1;s<=this.numComp;s+=1){if(e>o)throw new Error("ERROR: frame format error");let i=t.get8();if(e+=1,e>=o)throw new Error("ERROR: frame format error [c>=Lf]");r=t.get8(),e+=1,this.components[i]||(this.components[i]={...w}),this.components[i].hSamp=r>>4,this.components[i].vSamp=r&15,this.components[i].quantTableSel=t.get8(),e+=1}if(e!==o)throw new Error("ERROR: frame format error [Lf!=count]");return 1}};var k={};A(k,{crc32:()=>L,crcTable:()=>T,createArray:()=>c,makeCRCTable:()=>y});var c=(...a)=>{if(a.length>1){let t=a[0],e=a.slice(1),r=[];for(let o=0;o<t;o++)r[o]=c(...e);return r}else return Array(a[0]).fill(void 0)},y=function(){let a,t=[];for(let e=0;e<256;e++){a=e;for(let r=0;r<8;r++)a=a&1?3988292384^a>>>1:a>>>1;t[e]=a}return t},T=y(),L=function(a){let t=new Uint8Array(a),e=-1;for(let r=0;r<t.length;r++)e=e>>>8^T[(e^t[r])&255];return(e^-1)>>>0};var p=class a{static MSB=2147483648;l;th;v;tc;constructor(){this.l=c(4,2,16),this.th=[0,0,0,0],this.v=c(4,2,16,200),this.tc=[[0,0],[0,0],[0,0],[0,0]]}read(t,e){let r=0,o,s,i,n,f,u=t.get16();for(r+=2;r<u;){if(o=t.get8(),r+=1,s=o&15,s>3)throw new Error("ERROR: Huffman table ID > 3");if(i=o>>4,i>2)throw new Error("ERROR: Huffman table [Table class > 2 ]");for(this.th[s]=1,this.tc[s][i]=1,n=0;n<16;n+=1)this.l[s][i][n]=t.get8(),r+=1;for(n=0;n<16;n+=1)for(f=0;f<this.l[s][i][n];f+=1){if(r>u)throw new Error("ERROR: Huffman table format error [count>Lh]");this.v[s][i][n][f]=t.get8(),r+=1}}if(r!==u)throw new Error("ERROR: Huffman table format error [count!=Lf]");for(n=0;n<4;n+=1)for(f=0;f<2;f+=1)this.tc[n][f]!==0&&this.buildHuffTable(e[n][f],this.l[n][f],this.v[n][f]);return 1}buildHuffTable(t,e,r){let o,s,i,n,f;for(s=0,i=0;i<8;i+=1)for(n=0;n<e[i];n+=1)for(f=0;f<256>>i+1;f+=1)t[s]=r[i][n]|i+1<<8,s+=1;for(i=1;s<256;i+=1,s+=1)t[s]=i|a.MSB;for(o=1,s=0,i=8;i<16;i+=1)for(n=0;n<e[i];n+=1){for(f=0;f<256>>i-7;f+=1)t[o*256+s]=r[i][n]|i+1<<8,s+=1;if(s>=256){if(s>256)throw new Error("ERROR: Huffman table error(1)!");s=0,o+=1}}}};var d=class a{precision=[];tq=[0,0,0,0];quantTables=c(4,64);static enhanceQuantizationTable=function(t,e){for(let r=0;r<8;r+=1)t[e[0*8+r]]*=90,t[e[4*8+r]]*=90,t[e[2*8+r]]*=118,t[e[6*8+r]]*=49,t[e[5*8+r]]*=71,t[e[1*8+r]]*=126,t[e[7*8+r]]*=25,t[e[3*8+r]]*=106;for(let r=0;r<8;r+=1)t[e[0+8*r]]*=90,t[e[4+8*r]]*=90,t[e[2+8*r]]*=118,t[e[6+8*r]]*=49,t[e[5+8*r]]*=71,t[e[1+8*r]]*=126,t[e[7+8*r]]*=25,t[e[3+8*r]]*=106;for(let r=0;r<64;r+=1)t[r]>>=6};read(t,e){let r=0,o,s,i,n=t.get16();for(r+=2;r<n;){if(o=t.get8(),r+=1,s=o&15,s>3)throw new Error("ERROR: Quantization table ID > 3");if(this.precision[s]=o>>4,this.precision[s]===0)this.precision[s]=8;else if(this.precision[s]===1)this.precision[s]=16;else throw new Error("ERROR: Quantization table precision error");if(this.tq[s]=1,this.precision[s]===8){for(i=0;i<64;i+=1){if(r>n)throw new Error("ERROR: Quantization table format error");this.quantTables[s][i]=t.get8(),r+=1}a.enhanceQuantizationTable(this.quantTables[s],e)}else{for(i=0;i<64;i+=1){if(r>n)throw new Error("ERROR: Quantization table format error");this.quantTables[s][i]=t.get16(),r+=2}a.enhanceQuantizationTable(this.quantTables[s],e)}}if(r!==n)throw new Error("ERROR: Quantization table error [count!=Lq]");return 1}};var R={acTabSel:0,dcTabSel:0,scanCompSel:0};var g=class{ah=0;al=0;numComp=0;selection=0;spectralEnd=0;components=[];read(t){let e=0,r,o,s=t.get16();for(e+=2,this.numComp=t.get8(),e+=1,r=0;r<this.numComp;r+=1){if(this.components[r]={...R},e>s)throw new Error("ERROR: scan header format error");this.components[r].scanCompSel=t.get8(),e+=1,o=t.get8(),e+=1,this.components[r].dcTabSel=o>>4,this.components[r].acTabSel=o&15}if(this.selection=t.get8(),e+=1,this.spectralEnd=t.get8(),e+=1,o=t.get8(),this.ah=o>>4,this.al=o&15,e+=1,e!==s)throw new Error("ERROR: scan header format error [count!=Ns]");return 1}};var D=function(){let a=new ArrayBuffer(2);return new DataView(a).setInt16(0,256,!0),new Int16Array(a)[0]===256}(),E=class a{static IDCT_P=[0,5,40,16,45,2,7,42,21,56,8,61,18,47,1,4,41,23,58,13,32,24,37,10,63,17,44,3,6,43,20,57,15,34,29,48,53,26,39,9,60,19,46,22,59,12,33,31,50,55,25,36,11,62,14,35,28,49,52,27,38,30,51,54];static TABLE=[0,1,5,6,14,15,27,28,2,4,7,13,16,26,29,42,3,8,12,17,25,30,41,43,9,11,18,24,31,40,44,53,10,19,23,32,39,45,52,54,20,22,33,38,46,51,55,60,21,34,37,47,50,56,59,61,35,36,48,49,57,58,62,63];static MAX_HUFFMAN_SUBTREE=50;static MSB=2147483648;static RESTART_MARKER_BEGIN=65488;static RESTART_MARKER_END=65495;buffer=null;stream=null;frame=new x;huffTable=new p;quantTable=new d;scan=new g;DU=c(10,4,64);HuffTab=c(4,2,50*256);IDCT_Source=[];nBlock=[];acTab=c(10,1);dcTab=c(10,1);qTab=c(10,1);marker=0;markerIndex=0;numComp=0;restartInterval=0;selection=0;xDim=0;yDim=0;xLoc=0;yLoc=0;outputData=null;restarting=!1;mask=0;numBytes=0;precision=void 0;components=[];getter=null;setter=null;output=null;selector=null;constructor(t,e){this.buffer=t??null,this.numBytes=e??0}decompress(t,e,r){return this.decode(t,e,r).buffer}decode(t,e,r,o){let s=0,i=[],n,f,u=[],l=[],m;t&&(this.buffer=t),o!==void 0&&(this.numBytes=o),this.stream=new b(this.buffer,e,r),this.buffer=null,this.xLoc=0,this.yLoc=0;let h=this.stream.get16();if(h!==65496)throw new Error("Not a JPEG file");for(h=this.stream.get16();h>>4!==4092||h===65476;){switch(h){case 65476:this.huffTable.read(this.stream,this.HuffTab);break;case 65484:throw new Error("Program doesn't support arithmetic coding. (format throw new IOException)");case 65499:this.quantTable.read(this.stream,a.TABLE);break;case 65501:this.restartInterval=this.readNumber()??0;break;case 65504:case 65505:case 65506:case 65507:case 65508:case 65509:case 65510:case 65511:case 65512:case 65513:case 65514:case 65515:case 65516:case 65517:case 65518:case 65519:this.readApp();break;case 65534:this.readComment();break;default:if(h>>8!==255)throw new Error("ERROR: format throw new IOException! (decode)")}h=this.stream.get16()}if(h<65472||h>65479)throw new Error("ERROR: could not handle arithmetic code!");this.frame.read(this.stream),h=this.stream.get16();do{for(;h!==65498;){switch(h){case 65476:this.huffTable.read(this.stream,this.HuffTab);break;case 65484:throw new Error("Program doesn't support arithmetic coding. (format throw new IOException)");case 65499:this.quantTable.read(this.stream,a.TABLE);break;case 65501:this.restartInterval=this.readNumber()??0;break;case 65504:case 65505:case 65506:case 65507:case 65508:case 65509:case 65510:case 65511:case 65512:case 65513:case 65514:case 65515:case 65516:case 65517:case 65518:case 65519:this.readApp();break;case 65534:this.readComment();break;default:if(h>>8!==255)throw new Error("ERROR: format throw new IOException! (Parser.decode)")}h=this.stream.get16()}switch(this.precision=this.frame.precision,this.components=this.frame.components,this.numBytes||(this.numBytes=Math.round(Math.ceil(this.precision/8))),this.numBytes===1?this.mask=255:this.mask=65535,this.scan.read(this.stream),this.numComp=this.scan.numComp,this.selection=this.scan.selection,this.numBytes===1?this.numComp===3?(this.getter=this.getValueRGB,this.setter=this.setValueRGB,this.output=this.outputRGB):(this.getter=this.getValue8,this.setter=this.setValue8,this.output=this.outputSingle):(this.getter=this.getValue8,this.setter=this.setValue8,this.output=this.outputSingle),this.selection){case 2:this.selector=this.select2;break;case 3:this.selector=this.select3;break;case 4:this.selector=this.select4;break;case 5:this.selector=this.select5;break;case 6:this.selector=this.select6;break;case 7:this.selector=this.select7;break;default:this.selector=this.select1;break}for(n=0;n<this.numComp;n+=1)f=this.scan.components[n].scanCompSel,this.qTab[n]=this.quantTable.quantTables[this.components[f].quantTableSel],this.nBlock[n]=this.components[f].vSamp*this.components[f].hSamp,this.dcTab[n]=this.HuffTab[this.scan.components[n].dcTabSel][0],this.acTab[n]=this.HuffTab[this.scan.components[n].acTabSel][1];for(this.xDim=this.frame.dimX,this.yDim=this.frame.dimY,this.numBytes===1?this.outputData=new Uint8Array(new ArrayBuffer(this.xDim*this.yDim*this.numBytes*this.numComp)):this.outputData=new Uint16Array(new ArrayBuffer(this.xDim*this.yDim*this.numBytes*this.numComp)),s+=1;;){for(u[0]=0,l[0]=0,n=0;n<10;n+=1)i[n]=1<<this.precision-1;if(this.restartInterval===0){for(h=this.decodeUnit(i,u,l);h===0&&this.xLoc<this.xDim&&this.yLoc<this.yDim;)this.output(i),h=this.decodeUnit(i,u,l);break}for(m=0;m<this.restartInterval&&(this.restarting=m===0,h=this.decodeUnit(i,u,l),this.output(i),h===0);m+=1);if(h===0&&(this.markerIndex!==0?(h=65280|this.marker,this.markerIndex=0):h=this.stream.get16()),!(h>=a.RESTART_MARKER_BEGIN&&h<=a.RESTART_MARKER_END))break}h===65500&&s===1&&(this.readNumber(),h=this.stream.get16())}while(h!==65497&&this.xLoc<this.xDim&&this.yLoc<this.yDim&&s===0);return this.outputData}decodeUnit(t,e,r){return this.numComp===1?this.decodeSingle(t,e,r):this.numComp===3?this.decodeRGB(t,e,r):-1}select1(t){return this.getPreviousX(t)}select2(t){return this.getPreviousY(t)}select3(t){return this.getPreviousXY(t)}select4(t){return this.getPreviousX(t)+this.getPreviousY(t)-this.getPreviousXY(t)}select5(t){return this.getPreviousX(t)+(this.getPreviousY(t)-this.getPreviousXY(t)>>1)}select6(t){return this.getPreviousY(t)+(this.getPreviousX(t)-this.getPreviousXY(t)>>1)}select7(t){return(this.getPreviousX(t)+this.getPreviousY(t))/2}decodeRGB(t,e,r){if(this.selector===null)throw new Error("decode hasn't run yet");let o,s,i,n,f,u,l;for(t[0]=this.selector(0),t[1]=this.selector(1),t[2]=this.selector(2),n=0;n<this.numComp;n+=1)for(i=this.qTab[n],o=this.acTab[n],s=this.dcTab[n],f=0;f<this.nBlock[n];f+=1){for(u=0;u<this.IDCT_Source.length;u+=1)this.IDCT_Source[u]=0;let m=this.getHuffmanValue(s,e,r);if(m>=65280)return m;for(t[n]=this.IDCT_Source[0]=t[n]+this.getn(r,m,e,r),this.IDCT_Source[0]*=i[0],l=1;l<64;l+=1){if(m=this.getHuffmanValue(o,e,r),m>=65280)return m;if(l+=m>>4,m&15)this.IDCT_Source[a.IDCT_P[l]]=this.getn(r,m&15,e,r)*i[l];else if(!(m>>4))break}}return 0}decodeSingle(t,e,r){if(this.selector===null)throw new Error("decode hasn't run yet");let o,s,i,n;for(this.restarting?(this.restarting=!1,t[0]=1<<this.frame.precision-1):t[0]=this.selector(),s=0;s<this.nBlock[0];s+=1){if(o=this.getHuffmanValue(this.dcTab[0],e,r),o>=65280)return o;if(i=this.getn(t,o,e,r),n=i>>8,n>=a.RESTART_MARKER_BEGIN&&n<=a.RESTART_MARKER_END)return n;t[0]+=i}return 0}getHuffmanValue(t,e,r){let o,s;if(!this.stream)throw new Error("stream not initialized");if(r[0]<8?(e[0]<<=8,s=this.stream.get8(),s===255&&(this.marker=this.stream.get8(),this.marker!==0&&(this.markerIndex=9)),e[0]|=s):r[0]-=8,o=t[e[0]>>r[0]],o&a.MSB){if(this.markerIndex!==0)return this.markerIndex=0,65280|this.marker;e[0]&=65535>>16-r[0],e[0]<<=8,s=this.stream.get8(),s===255&&(this.marker=this.stream.get8(),this.marker!==0&&(this.markerIndex=9)),e[0]|=s,o=t[(o&255)*256+(e[0]>>r[0])],r[0]+=8}if(r[0]+=8-(o>>8),r[0]<0)throw new Error("index="+r[0]+" temp="+e[0]+" code="+o+" in HuffmanValue()");return r[0]<this.markerIndex?(this.markerIndex=0,65280|this.marker):(e[0]&=65535>>16-r[0],o&255)}getn(t,e,r,o){let s,i;if(this.stream===null)throw new Error("stream not initialized");if(e===0)return 0;if(e===16)return t[0]>=0?-32768:32768;if(o[0]-=e,o[0]>=0){if(o[0]<this.markerIndex&&!this.isLastPixel())return this.markerIndex=0,(65280|this.marker)<<8;s=r[0]>>o[0],r[0]&=65535>>16-o[0]}else{if(r[0]<<=8,i=this.stream.get8(),i===255&&(this.marker=this.stream.get8(),this.marker!==0&&(this.markerIndex=9)),r[0]|=i,o[0]+=8,o[0]<0){if(this.markerIndex!==0)return this.markerIndex=0,(65280|this.marker)<<8;r[0]<<=8,i=this.stream.get8(),i===255&&(this.marker=this.stream.get8(),this.marker!==0&&(this.markerIndex=9)),r[0]|=i,o[0]+=8}if(o[0]<0)throw new Error("index="+o[0]+" in getn()");if(o[0]<this.markerIndex)return this.markerIndex=0,(65280|this.marker)<<8;s=r[0]>>o[0],r[0]&=65535>>16-o[0]}return s<1<<e-1&&(s+=(-1<<e)+1),s}getPreviousX(t=0){if(this.getter===null)throw new Error("decode hasn't run yet");return this.xLoc>0?this.getter(this.yLoc*this.xDim+this.xLoc-1,t):this.yLoc>0?this.getPreviousY(t):1<<this.frame.precision-1}getPreviousXY(t=0){if(this.getter===null)throw new Error("decode hasn't run yet");return this.xLoc>0&&this.yLoc>0?this.getter((this.yLoc-1)*this.xDim+this.xLoc-1,t):this.getPreviousY(t)}getPreviousY(t=0){if(this.getter===null)throw new Error("decode hasn't run yet");return this.yLoc>0?this.getter((this.yLoc-1)*this.xDim+this.xLoc,t):this.getPreviousX(t)}isLastPixel(){return this.xLoc===this.xDim-1&&this.yLoc===this.yDim-1}outputSingle(t){if(this.setter===null)throw new Error("decode hasn't run yet");this.xLoc<this.xDim&&this.yLoc<this.yDim&&(this.setter(this.yLoc*this.xDim+this.xLoc,this.mask&t[0]),this.xLoc+=1,this.xLoc>=this.xDim&&(this.yLoc+=1,this.xLoc=0))}outputRGB(t){if(this.setter===null)throw new Error("decode hasn't run yet");let e=this.yLoc*this.xDim+this.xLoc;this.xLoc<this.xDim&&this.yLoc<this.yDim&&(this.setter(e,t[0],0),this.setter(e,t[1],1),this.setter(e,t[2],2),this.xLoc+=1,this.xLoc>=this.xDim&&(this.yLoc+=1,this.xLoc=0))}setValue8(t,e){if(!this.outputData)throw new Error("output data not ready");D?this.outputData[t]=e:this.outputData[t]=(e&255)<<8|e>>8&255}getValue8(t){if(this.outputData===null)throw new Error("output data not ready");if(D)return this.outputData[t];{let e=this.outputData[t];return(e&255)<<8|e>>8&255}}setValueRGB(t,e,r=0){this.outputData!==null&&(this.outputData[t*3+r]=e)}getValueRGB(t,e){if(this.outputData===null)throw new Error("output data not ready");return this.outputData[t*3+e]}readApp(){if(this.stream===null)return null;let t=0,e=this.stream.get16();for(t+=2;t<e;)this.stream.get8(),t+=1;return e}readComment(){if(this.stream===null)return null;let t="",e=0,r=this.stream.get16();for(e+=2;e<r;)t+=this.stream.get8(),e+=1;return t}readNumber(){if(this.stream===null)return null;if(this.stream.get16()!==4)throw new Error("ERROR: Define number format throw new IOException [Ld!=4]");return this.stream.get16()}};
    window.JpegLosslessDecoder = {
      ComponentSpec: w, DataStream: b, Decoder: E, FrameHeader: x,
      HuffmanTable: p, QuantizationTable: d, ScanComponent: R,
      ScanHeader: g, Utils: k
    };
  })();

  // ── Export ────────────────────────────────────────────────────────
  window.DicomConverter = {
    convert: convert
  };

})();
