# PixelBench & VectorBench

Two native, offline design apps for AAA Graphic Co. Both are plain desktop programs built with Python + Qt (PySide6).
Neither touches the network.

| App | In the spirit of | Launch | Notes |
|---|---|---|---|
| **PixelBench** | Photopea / Photoshop (raster, layers, brushes, filters) | `python pixelbench.py` | `docs/PIXELBENCH_NOTES.md` |
| **VectorBench** | Vectorpea / Illustrator (paths, pen, type, pathfinder, SVG/PDF) | `python vectorbench.py` | `docs/VECTORBENCH_NOTES.md` |

Install once with `pip install -r requirements.txt` (Python 3.10+). Build standalone executables with
`python build.py` (PixelBench) or `python build.py vectorbench`.

---

# VectorBench

An Illustrator-style vector editor. Native, offline, no browser.

![VectorBench](docs/screenshot_vectorbench.png)

**Tools** (shortcut): Selection (V), Direct Selection (A), Pen (P), Add/Delete anchor (+/−), Anchor Point (Shift+C), Pencil (N), Line (\\), Rectangle (M, with corner radius), Ellipse (L), Polygon/Star, Type (T, edited inline on the canvas), Rotate (R), Scale (S), Gradient (G), Eyedropper (I), Hand (H), Zoom (Z).

**Appearance**: fill and stroke as none / solid / linear or radial gradient, stroke width, cap, join, dashes, opacity, 12 blend modes, swatches panel.

**Object & Path**: group/ungroup, lock/hide, arrange, transform dialogs (move/rotate/scale/reflect/shear with live preview and copy), Transform Again (Ctrl+D), Expand/Create Outlines, join, average, outline stroke, offset path, simplify, add anchor points, reverse, Pathfinder (unite, minus front, intersect, exclude, minus back), align and distribute (to selection or artboard).

**Workspace**: layers tree, properties panel (transform / appearance / character), rulers with drag-out guides, grid, snapping, outline mode, tabs, 100-step undo.

**Files**: native `.vbx` (JSON), SVG import/export, true vector PDF export, PNG/JPEG/WebP/TIFF export at any scale, place images, copy/paste as SVG with other apps.

---

# PixelBench

A native, layer-based raster image editor in the spirit of Photopea / Photoshop, with **no browser involved**.
It is a plain desktop app built with Python + Qt (PySide6), NumPy, Pillow and SciPy. Runs on Windows, Linux and macOS.

![PixelBench](docs/screenshot.png)

## Features

**Layers**: unlimited layers, visibility, opacity, 14 blend modes (Normal, Multiply, Screen, Overlay, Darken,
Lighten, Color Dodge, Color Burn, Hard Light, Soft Light, Difference, Exclusion, Add, Subtract), lock, rename,
reorder, duplicate, merge down, merge visible, flatten, transform (scale / rotate / flip / offset with live preview).

**Tools** (keyboard shortcut in brackets):

| Tool | Key | Notes |
|---|---|---|
| Move | V | drags the layer or just the selected pixels; arrow keys nudge; auto-select layer option |
| Marquee | M | rectangle or ellipse; Shift add, Alt subtract, Shift+Alt intersect, Ctrl constrains; feather |
| Lasso | L | freehand or polygonal |
| Magic wand | W | tolerance, contiguous, sample all layers |
| Crop | C | drag, Enter / double-click to apply, Esc cancels |
| Eyedropper | I | Alt+click sets the background colour |
| Brush | B | size, hardness, opacity; `[` `]` resize; Shift+click draws straight lines |
| Eraser | E | same engine as the brush, erases to transparency |
| Clone stamp | S | Alt+click to set the source |
| Paint bucket | G | tolerance / contiguous / opacity, honours the selection |
| Gradient | Shift+G | linear, radial, reflected; FG→BG or FG→transparent |
| Shape | U | rectangle, rounded rectangle, ellipse, line; fill + stroke; optional new layer |
| Text | T | any installed font, size, bold/italic, alignment, colour; rasterised onto its own layer |
| Hand | H | or hold Space with any tool, or middle-drag |
| Zoom | Z | click / Alt+click / drag a box; Ctrl+wheel zooms at the cursor |

**Selections** are 8-bit masks, so feathered and anti-aliased edges work everywhere: painting, fills, filters,
clear, copy and paste all respect them. Select menu: all, deselect, invert, select layer contents, feather, grow, shrink.
Marching ants are animated.

**Filters & adjustments** (all with live preview and selection masking): Brightness/Contrast, Levels, Exposure,
Hue/Saturation, Vibrance, Color Balance, Auto Levels, Equalize, Invert, Desaturate, Threshold, Posterize, Sepia,
Gaussian / Box / Motion blur, Median, Sharpen, Unsharp Mask, Pixelate, Add Noise, Emboss, Find Edges, Solarize,
Vignette. Ctrl+F repeats the last filter.

**Image**: resize (Nearest/Bilinear/Bicubic/Lanczos), canvas size with anchor, crop to selection, trim transparent
edges, rotate 90/180, flip.

**Files**: opens PNG, JPEG, WebP, BMP, GIF, TIFF, ICO, TGA and **PSD/PSB** (layers, names, opacity, visibility and blend
modes are kept; groups are flattened to a list). Saves layered documents as `.pxb` (a zip of PNGs + JSON) and exports
flattened PNG / JPEG / WebP / BMP / GIF / TIFF / ICO. Drag and drop files onto the window. Paste an image from the
clipboard as a new layer or a new document.

**Workspace**: multiple documents in tabs, unlimited undo/redo with a clickable History panel, dark theme, status bar
with cursor position and zoom, layout is remembered between runs.

## Run from source

```bash
pip install -r requirements.txt
python pixelbench.py                 # or: python -m pixelbench
python pixelbench.py photo.jpg ...   # open files on launch
```

Python 3.10+ is required. On a bare Linux server you also need the usual Qt system libraries
(`libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3`).

## Build a standalone executable

```bash
pip install pyinstaller
python build.py            # dist/PixelBench/  (one-folder, fastest start)
python build.py --onefile  # single file
```

Build on the OS you are targeting (Windows build for the Windows machines, Linux build for the server).

## Tests

```bash
pip install pytest
pytest
```

The suite covers both apps: compositing and blend modes, undo/redo, file round-trips, every filter, selection maths, and a set
of UI smoke tests that drive the real widgets through synthetic mouse events on Qt's offscreen platform.

## Layout

```
vectorbench/
  model.py       items (path/rect/ellipse/polygon/text/image/group), layers, document, JSON serialisation
  geometry.py    bezier maths, arc conversion, simplification
  pathops.py     pathfinder, outline stroke, offset, simplify, join, align/distribute
  svg.py         SVG import and export
  fileio.py      .vbx, SVG, PDF and raster export, place image
  history.py     snapshot undo/redo
  canvas.py      artboard view: rulers, guides, grid, snapping, selection overlay, inline text editor
  tools.py       all tools
  panels.py      tool box, options bar, properties, layers, swatches, align/pathfinder
  dialogs.py     new/setup/transform/offset/export/gradient dialogs
  mainwindow.py  menus, tabs, docks, commands
pixelbench/
  blend.py       blend modes + compositing (NumPy, W3C formulas)
  document.py    Document / Layer model, whole-image operations, undo snapshots
  history.py     snapshot undo/redo with copy-on-write layers
  fileio.py      open / save / export, PSD import, native .pxb format
  filters.py     adjustments and filters + the Filter menu registry
  selection.py   selection masks, magic wand, feather/grow, outline for ants
  tools.py       all interactive tools (stroke engine, selection tools, move, crop, text, shapes...)
  canvas.py      the zoomable document view
  panels.py      tool box, options bar, swatches, Layers and History panels
  dialogs.py     new / resize / canvas / filter / text / transform / export dialogs
  mainwindow.py  menus, tabs, docks, commands
tests/           pytest suite
build.py         PyInstaller build (either app)
docs/            notes and screenshot
```

See `docs/PIXELBENCH_NOTES.md` and `docs/VECTORBENCH_NOTES.md` for design notes, known gaps and roadmaps.

---
*This repository started life as a "random thoughts" scratch repo; PixelBench now lives here.*
