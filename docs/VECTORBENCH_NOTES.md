# VectorBench — pickup notes (backup copy per the 2-copies doctrine)

**What this is:** the companion `.md` to the VectorBench source in this repo (`vectorbench/`). Primary artifact =
the code; this file = the "where were we / what's not great yet" copy so any device can pick up cold.

Written 2026-10-07 in the same Claude Code cloud session that built PixelBench (branch `claude/focused-feynman-rpjnuv`).
Source of truth: https://github.com/drJesticles/hello-world (PR #1).

## What was asked

"The one I would use more often is Vectorpea (the one similar to Illustrator). Build it out to ~50% of the most used
features of Illustrator. Must work without an internet connection. Save to `Q:\wikitemp\valhalla\vectorBench`."

The `Q:` path is a drive on Jesse's machine; the cloud session can't write there. Code is on GitHub, notes are in
Google Drive `Valhalla/VectorBench/`. To land it in the Q: folder on a Windows machine:

```
git clone -b claude/focused-feynman-rpjnuv https://github.com/drJesticles/hello-world "Q:\wikitemp\valhalla\vectorBench"
cd /d Q:\wikitemp\valhalla\vectorBench
pip install -r requirements.txt
python vectorbench.py
```

## What was built

A native, offline, Illustrator-style vector editor: Python 3.10+, PySide6 (Qt 6). No network code anywhere.
Shares the repo, `requirements.txt`, build script and dark theme with PixelBench; the two apps are independent packages.

Illustrator features covered (the "most used 50%"):

- Selection (V) with bounding-box scale handles, rotate zone outside corners, marquee, Shift-add, Alt-drag duplicate,
  arrow-key nudge. Direct Selection (A) for anchors and bezier handles (Alt breaks a smooth pair), anchor marquee,
  Delete removes anchors.
- Pen (P) with click/drag/close/continue, Add (+) / Delete (−) / Convert (Shift+C) anchor tools, Pencil (N) with
  simplification and smoothing.
- Rectangle (M, with corner radius), Ellipse (L), Polygon/Star, Line (\), Shift/Alt constrain and from-centre,
  click-for-exact-size.
- Type (T): point text edited inline on the canvas; font, size, bold, italic, alignment, leading, tracking.
  Create Outlines (Ctrl+Shift+O).
- Rotate (R), Scale (S), Gradient (G), Eyedropper (I), Hand (H), Zoom (Z).
- Appearance: fill and stroke each none / solid / linear / radial gradient, stroke width, cap, join, dashes,
  opacity, 12 blend modes. Swatches panel. X swaps fill/stroke.
- Object: group/ungroup, lock/hide, arrange (front/forward/backward/back), Transform (move/rotate/scale/reflect/shear
  dialogs with live preview and "copy"), Transform Again (Ctrl+D), Expand.
- Path: join, average, outline stroke, offset path, simplify, add anchor points, reverse.
- Pathfinder: unite, minus front, intersect, exclude, minus back.
- Align (to selection or artboard) and distribute.
- Layers panel (tree of layers and objects with visibility/lock, rename, reorder).
- View: rulers, drag-out guides, grid, snap to grid / point / guides, outline mode, zoom to selection.
- Files: native `.vbx` (plain JSON), SVG import and export (paths incl. arcs, shapes, text, images, gradients,
  transforms, groups → layers), PDF export (true vector), PNG/JPEG/WebP/TIFF/BMP raster export at any scale, Place image,
  clipboard copy as SVG text (pastes into Inkscape/Illustrator/browsers) and paste of SVG text or images.

## Design decisions (so they're not re-litigated)

- **Model is plain Python + Qt value types**, serialised to JSON (`model.py`). That JSON is the native file format and
  the undo snapshot. Vector docs are tiny, so full-snapshot undo (100 steps) is simplest and bullet-proof.
- **Every item has a `QTransform`.** Shapes (rect/ellipse/polygon) and text stay parametric and editable until you
  Expand them; paths are editable anchors. Direct-selection edits map through the inverse transform.
- **Gradients are stored in bbox-relative coordinates**, so they follow the object through any transform and
  export cleanly to SVG `userSpaceOnUse` on write.
- **Pathfinder, outline stroke, offset and simplify use Qt's path booleans** (`QPainterPath.united/subtracted/
  intersected`, `QPainterPathStroker`). Results are converted back to editable anchors. Qt's booleans can produce more
  anchors than Illustrator would; Simplify helps.
- **Rendering**: one `QPainter` pass per document; blend modes map to Qt composition modes. Group opacity is applied
  per child (approximation; a proper group needs an offscreen layer).
- **Text** rasterises via `QPainterPath.addText` for hit-testing/outlines; editing is an inline `QPlainTextEdit`
  overlay scaled with the zoom.

## Verified

- Headless driver exercised every tool (shapes, pen, pencil, type, select move/scale/rotate/duplicate, direct
  selection, anchor tools, gradient, eyedropper, rotate/scale tools), every Object/Path/Pathfinder/Align command,
  clipboard, layers, properties panel, guides/grid/outline, save/export/open in all formats, place image, undo.
- Screenshot: `docs/screenshot_vectorbench.png`; sample file `docs/sample.vbx`.
- pytest suite (`tests/test_vector_*.py`).

## Not verified / known gaps (be honest with Jesse)

- **Real Illustrator SVG files** were not available to test. Simple SVGs round-trip; Illustrator's exports with
  `<use>`, `<symbol>`, clip paths, masks, patterns, and CSS classes in `<style>` blocks will import partially
  (`<style>` blocks are ignored, clipPath/mask are skipped).
- **No AI/EPS/PDF import.** Illustrator can export SVG, which is the bridge.
- **No area text / text on a path / text wrapping**; point text only. No vertical text.
- **No live shapes after rotation**: a rotated rectangle keeps its transform (fine), but W/H in the panel report the
  rotated bounding box.
- **No compound paths UI** (a PathItem can hold multiple subpaths, and pathfinder creates them, but there's no
  "Make Compound Path" command; use Unite/Minus Front).
- **No symbols, brushes, blends, meshes, live effects, appearance stacks, artboards (multiple), colour management
  or CMYK.** Everything is sRGB/RGB.
- **Group opacity** multiplies into children rather than compositing the group as one layer.
- **Smart guides** are limited to point snapping and guide/grid/artboard edges; no alignment lines drawn while dragging.
- Tool icons are Unicode glyphs; proper SVG icons would look better.
- Windows build not run yet (same `python build.py vectorbench` on Orcastrader or aaagc).

## Roadmap suggestion (bang-for-buck for AAA Graphic Co)

1. Smart guides drawn while dragging (centre/edge alignment lines).
2. Area text with wrapping + text on a path.
3. Multiple artboards and per-artboard export.
4. Import of Illustrator CSS `<style>` classes and `<use>`/`<symbol>` (most logo SVGs from clients).
5. Compound path command, Divide/Trim/Crop pathfinder modes.
6. Live corner radius widget on rectangles; polygon side count widget.
7. Proper icon set; app icon (`assets/vectorbench.ico` is picked up automatically by `build.py`).

## Where things live

- Repo: `drJesticles/hello-world`, branch `claude/focused-feynman-rpjnuv`, folder `vectorbench/`, launcher `vectorbench.py`.
- This note: `docs/VECTORBENCH_NOTES.md` plus a copy in Google Drive `Valhalla/VectorBench/`.
- Local target per Jesse: `Q:\wikitemp\valhalla\vectorBench` (clone command above).
