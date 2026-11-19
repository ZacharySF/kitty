# Pen support (after the Rust migration)

## What exists
- `web/src/lib/notesCanvas.ts`, `Notes.svelte` (canvas): pen, highlighter, eraser, lasso; strokes use `getCoalescedEvents()` (Notes.svelte ~1105).
- `web/src/lib/mapInk.ts`, `SchemaMaps.svelte` (mind maps): ink points are `[x, y, pressure]`; the pen's eraser end (`buttons & 32`) erases, the barrel button lassos.
- Build setting `ui.notes.pen_only`.

## Gaps against the best apps (GoodNotes, Notability, OneNote, Concepts, Xournal++)
1. Latency: no `getPredictedEvents()`, no `desynchronized` canvas context, no `pointerrawupdate`; the wet stroke is redrawn with the scene.
2. Stroke quality: no tilt (`tiltX/Y`, `altitudeAngle`), no twist; no velocity-aware smoothing or taper; mouse and touch get constant width.
3. Palm rejection: `pen_only` is a manual switch; nothing rejects a palm that lands while a pen is in range (hover or contact).
4. Hover: no preview of the nib or the eraser radius under the pen.
5. Tools: no shape snap (hold at the end of a stroke), no ruler, no stroke eraser vs pixel eraser choice, no lasso resize/recolour, no insert-space tool.
6. Pen buttons: barrel button and eraser end work in mind maps only; not configurable.
7. Handwriting: no ink-to-text, no handwriting search.
8. Notes text editor: the pen cannot write into a text block (scribble) or annotate over a page or PDF.
9. Platform: WebKitGTK on Wayland must deliver pen pressure/tilt (zwp_tablet_v2 through GTK); unverified on this machine. Windows needs WebView2 pen checks.

## Plan, in order
1. One shared ink engine (`web/src/lib/ink/`): input (pointer capture, coalesced + predicted points, raw updates where available), palm rejection, stroke model `[x, y, pressure, tiltX, tiltY, t]`, smoothing (one-euro filter on position, pressure curve, taper), outline generation, incremental wet-ink layer on its own desynchronized canvas. Notes canvas and mind maps both use it.
2. Palm rejection: while a pen is in range or was within 500 ms, touch draws nothing and may only pan with two fingers; a touch that began before the pen cancels when the pen lands. `pen_only` becomes automatic once a pen is seen.
3. Hover cursor: nib size and colour; eraser ring; shown from `pointermove` with `buttons === 0` and `pointerType === "pen"`.
4. Tools: hold-to-snap shapes (line, arrow, rectangle, ellipse, triangle), stroke eraser and pixel eraser, lasso with move/resize/recolour/duplicate, ruler, insert space.
5. Pen buttons everywhere: eraser end erases, barrel = lasso by default, both configurable; double-tap where the platform reports it.
6. Ink over notes and PDFs: an ink layer per page and per PDF page, anchored to blocks.
7. Handwriting recognition on device (no cloud): ink-to-text and search over ink. Needs a model choice; measure before promising.
8. Measure: pen-to-ink latency with a high-speed camera or the photodiode-free method (draw at constant speed, measure gap in px, divide by speed) on WebKitGTK; 120 Hz sample completeness (coalesced count per frame); palm false-positive count in a scripted session.

## Tests
- Engine: pure Node tests for smoothing, taper, snapping, palm-rejection state machine, eraser geometry.
- Browser: synthetic PointerEvents with pen type, pressure, tilt, eraser button, hover, concurrent touch.
- Real hardware is needed for latency and for WebKitGTK pen delivery; none attached here.

## Research notes (sources checked)
- Pointer Events 3 is a W3C Recommendation (June 2026): `getCoalescedEvents()`, `getPredictedEvents()`, `pointerrawupdate` (secure contexts), `altitudeAngle`/`azimuthAngle`, `twist`, `tangentialPressure`. Level 4 draft adds `persistentDeviceId` (per-session pen identity: lets a second pen keep its own tool). https://www.w3.org/TR/pointerevents3/
- Every optional member is independent per engine: detect each at runtime, never by user agent. With raw updates on, keep `pointermove` only to read its predicted list, or the same sample is drawn twice.
- A prediction is a disposable preview: erase it before the next real segment; give predicted points the latest real pressure and tilt (engines only predict position and time).
- Delegated ink (`navigator.ink.requestPresenter`, `updateInkTrailStartPoint`): the compositor draws the last stretch to the nib. Chromium only, so it applies to the Windows build (WebView2), not to WebKitGTK; keep the normal path always. https://developer.mozilla.org/docs/Web/API/Ink_API
- Reference write-up of the same pipeline in another toolkit: https://github.com/focale-editor/stylet/blob/main/docs/web_support.md
- Not verified here: what WebKitGTK delivers for a Wayland tablet (pressure, tilt, eraser, hover). No pen is attached to this machine; the first step of the build is a probe page that prints what arrives.
