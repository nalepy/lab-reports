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

  // Transfer syntax UIDs
  var TS_IMPLICIT_VR_LE    = '1.2.840.10008.1.2';
  var TS_EXPLICIT_VR_LE    = '1.2.840.10008.1.2.1';
  var TS_JPEG_BASELINE     = '1.2.840.10008.1.2.4.50';
  var TS_JPEG_EXTENDED     = '1.2.840.10008.1.2.4.51';

  // ── Helpers ──────────────────────────────────────────────────────

  function tagToHex(tag) {
    return ('00000000' + tag.toString(16)).slice(-8).toUpperCase();
  }

  // Is this a known JPEG transfer syntax (pixel data = embedded JPEG)?
  function isJpegTS(ts) {
    return ts === TS_JPEG_BASELINE || ts === TS_JPEG_EXTENDED;
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
      var longVr = { OB:1, OD:1, OF:1, OL:1, OW:1, SQ:1, UC:1, UN:1, UR:1, UT:1 };
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
          pos = skipSequence(dv, valOff, fileSize);
        }
      }
    }

    return { tags: tags, pixelDataInfo: pixelDataInfo, transferSyntax: transferSyntax };
  }

  function skipSequence(dv, start, maxOffset) {
    var pos = start;
    var depth = 1;
    while (pos < maxOffset - 7 && depth > 0) {
      var tg = dv.getUint32(pos, true);
      var len = dv.getUint32(pos + 4, true);
      if (tg === TAG_SEQUENCE_DELIMITATION) {
        depth--;
        pos += 8;
      } else if (tg === TAG_ITEM) {
        depth++;
        if (len === 0xFFFFFFFF) len = 0;
        pos += 8 + len;
      } else {
        pos += 8 + (len === 0xFFFFFFFF ? 0 : len);
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
   * Render a raw monochrome frame to an ImageData.
   */
  function renderMonochrome(dv, frame, meta) {
    var rows = meta.rows;
    var cols = meta.cols;
    var bitsAllocated = meta.bitsAllocated;
    var pixelCount = rows * cols;

    // Read pixel values
    var pixels;
    var pixelMin = Infinity;
    var pixelMax = -Infinity;
    var isSigned = meta.pixelRepresentation === 1;

    if (bitsAllocated === 16) {
      pixels = new Int32Array(pixelCount); // use Int32 so rescale values fit
      for (var i = 0; i < pixelCount; i++) {
        var raw = dv.getUint16(frame.offset + i * 2, true);
        if (isSigned && raw >= 32768) raw = raw - 65536;
        pixels[i] = raw;
      }
    } else {
      // 8-bit (or other — treat as 8)
      pixels = new Int32Array(pixelCount);
      for (var j = 0; j < pixelCount; j++) {
        var r = dv.getUint8(frame.offset + j);
        if (isSigned && r >= 128) r = r - 256;
        pixels[j] = r;
      }
    }

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
   * Render a single frame (raw or JPEG) to a canvas and return the canvas.
   */
  function renderFrame(dv, frame, meta, isJpeg) {
    return new Promise(function (resolve, reject) {
      var canvas = document.createElement('canvas');
      canvas.width = meta.cols;
      canvas.height = meta.rows;
      var ctx = canvas.getContext('2d');

      if (isJpeg) {
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
      } else {
        // Raw monochrome → ImageData → canvas
        var imageData = renderMonochrome(dv, frame, meta);
        ctx.putImageData(imageData, 0, 0);
        resolve(canvas);
      }
    });
  }

  // ── Video recording ───────────────────────────────────────────────

  function recordVideo(frames, dv, meta, isJpeg, opts) {
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

        renderFrame(dv, frames[frameIndex], meta, isJpeg).then(function (c) {
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

            if (!parsed || !parsed.pixelDataInfo) {
              return resolve({ kind: 'unparsed' });
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
              ? readString(dv, tags[TAG_PHOTOMETRIC_INTERP]) : 'MONOCHROME2';
            var samplesPerPixel = readIntTag(dv, tags[TAG_SAMPLES_PER_PIXEL]) || 1;
            var numberOfFrames = readIntTag(dv, tags[TAG_NUMBER_OF_FRAMES]) || 1;

            var rescaleSlope = tags[TAG_RESCALE_SLOPE] ? readNumericTag(dv, tags[TAG_RESCALE_SLOPE]) : null;
            var rescaleIntercept = tags[TAG_RESCALE_INTERCEPT] ? readNumericTag(dv, tags[TAG_RESCALE_INTERCEPT]) : null;
            // WindowCenter / WindowWidth may have multiple values (separated by backslash)
            var wcRaw = tags[TAG_WINDOW_CENTER] ? parseFloat(readString(dv, tags[TAG_WINDOW_CENTER]).split('\\')[0]) : null;
            var wwRaw = tags[TAG_WINDOW_WIDTH] ? parseFloat(readString(dv, tags[TAG_WINDOW_WIDTH]).split('\\')[0]) : null;

            if (rows === 0 || cols === 0) {
              return resolve({ kind: 'unparsed' });
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
            var raw = isRawTS(ts);
            var frames;

            if (jpeg && pdInfo.isEncapsulated) {
              frames = extractJpegFrames(dv, pdInfo.offset, dv.byteLength);
            } else if (raw && !pdInfo.isEncapsulated) {
              frames = extractRawFrames(dv, pdInfo.offset, pdInfo.length, meta);
            } else if (!pdInfo.isEncapsulated && pdInfo.length > 0) {
              // Unknown transfer syntax but uncompressed — try raw
              frames = extractRawFrames(dv, pdInfo.offset, pdInfo.length, meta);
            } else {
              return resolve({ kind: 'unparsed' });
            }

            if (!frames || frames.length === 0) {
              return resolve({ kind: 'unparsed' });
            }

            // Limit frames
            var maxFrames = opts.maxFrames || 200;
            if (frames.length > maxFrames) {
              frames = frames.slice(0, maxFrames);
              meta.framesTruncated = true;
              meta.originalFrames = numberOfFrames;
            }

            // Single frame → JPEG
            if (frames.length === 1) {
              renderFrame(dv, frames[0], meta, jpeg).then(function (canvas) {
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
                resolve({ kind: 'unparsed' });
              });
              return;
            }

            // Multi-frame → WebM
            recordVideo(frames, dv, meta, jpeg, opts).then(function (videoBlob) {
              if (!videoBlob) {
                return resolve({ kind: 'unparsed' });
              }
              var baseName = (file.name || 'dicom').replace(/\.dcm$/i, '');
              resolve({
                kind: 'video',
                blob: videoBlob,
                name: baseName + '.webm',
                meta: meta
              });
            }).catch(function () {
              resolve({ kind: 'unparsed' });
            });

          } catch (e) {
            resolve({ kind: 'unparsed' });
          }
        };

        reader.onerror = function () {
          resolve({ kind: 'unparsed' });
        };

        reader.readAsArrayBuffer(file);
      } catch (e) {
        resolve({ kind: 'unparsed' });
      }
    });
  }

  // ── Export ────────────────────────────────────────────────────────
  window.DicomConverter = {
    convert: convert
  };

})();
