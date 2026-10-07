# PixelBench — pickup notes (backup copy per the 2-copies doctrine)

**What this is:** the companion `.md` to the PixelBench source in this repo. Primary artifact = the code
(`pixelbench/`), this file = the "where were we / what's not great yet" copy so any device can pick up cold.

Written 2026-10-07 in a Claude Code cloud session (branch `claude/focused-feynman-rpjnuv`).
No CLAUDE.md exists in this repo (checked the whole tree), so nothing was deviated from. This session has a
file system (Linux cloud container), not a phone.

## What was asked

"Build me an app like Photopea that's non-browser based."

## What was built

A native desktop image editor: Python 3.10+, PySide6 (Qt 6), NumPy, Pillow, SciPy, psd-tools.
Launch with `python pixelbench.py`, package with `python build.py`. See README.md for the full feature list.

Key design decisions (so they're not re-litigated):

- **Pixel model**: every layer is a canvas-sized `(H, W, 4)` uint8 straight-alpha NumPy array. No per-layer
  offsets. Simple, fast enough for typical photo sizes, and lets tools paint straight into memory through a
  zero-copy `QImage` wrapper (`qtutil.qimage_from_array`).
- **Compositing**: W3C/Photoshop separable blend formulas in float32 (`blend.py`), cached per document version.
- **Undo**: snapshot-based with copy-on-write. `History.push(label, detach=...)` stores references to the
  current layer arrays and detaches (copies) only the layer about to be mutated in place. Any code that
  assigns a *new* array to `layer.pixels` needs no detach. Rule is documented at the top of `document.py`.
- **Strokes**: brush/eraser/clone accumulate coverage into a scratch buffer and recompute
  `alpha_over(original, colour * coverage * opacity * selection)` in the dirty rectangle only. That gives
  Photoshop-style stroke opacity (no build-up past the setting) and selection-awareness for free.
- **Selection**: 8-bit mask or `None` (= everything). Feather/anti-aliasing fall out naturally.
- **Native format `.pxb`**: zip of `document.json` + one PNG per layer (+ optional `selection.png`).
  Human-inspectable, no custom binary format to maintain.
- **Filters**: one registry list (`filters.FILTERS`) drives the whole Filter menu and the generic live-preview
  dialog, so adding a filter is one function + one tuple.

## Verified

- 37 pytest tests pass (engine + UI smoke tests on Qt offscreen).
- A scripted session drove every tool, every filter, undo/redo, clipboard, tabs and took a screenshot
  (`docs/screenshot.png`).
- PyInstaller build: see the "Build" line at the bottom of this file for whether the Linux trial build in the
  session succeeded.

## Not verified / known gaps (be honest with Jesse)

- **PSD import is untested on a real file.** The code path uses psd-tools' documented API, but no PSD was
  available in the session. First real PSD opened = first real test. Groups are flattened; layer masks are
  baked in; adjustment layers / smart objects / text layers import as their rasterised composite.
- **No PSD export.** psd-tools can't write new files cleanly. Options later: `pytoshop` or write a minimal PSD
  writer (format is documented, raw/RLE channels only, doable in ~300 lines).
- **Performance on very large images (20+ MP)**: full-canvas compositing in float32 per change. Fine up to
  ~10 MP with a few layers; beyond that it will feel sluggish. Fix path: composite only the dirty rect, and/or
  keep premultiplied layers so Normal-mode compositing is one multiply-add.
- **Undo memory**: each detach copies the whole active layer (4 bytes/px). 40-step limit. Fix path: tile-based
  undo (store only changed 64×64 tiles).
- **Marching ants** are drawn as 1-document-pixel-wide edges scaled with the zoom, so they get chunky above
  ~4×. Cosmetic.
- **No free-transform handles on canvas** — transform is a dialog (scale/rotate/flip/offset with live preview).
  Handles are the single biggest UX upgrade on the list.
- **No layer masks, adjustment layers, layer groups, smart objects, editable text layers** (text rasterises).
- **No dodge/burn/smudge/blur brushes, no pen/path tool, no curves dialog** (Levels is there).
- **No colour management / ICC.** Everything is sRGB-as-is.
- **Tool icons are Unicode glyphs** (render with colour emoji on Linux). Real SVG icons would look more
  professional; drop them in `panels._glyph_icon`.
- Single-letter tool shortcuts fire even when a spin box has focus (same as Photoshop; could be annoying).

## Roadmap suggestion (in order of bang-for-buck for AAA Graphic Co use)

1. On-canvas free transform with handles (Ctrl+T).
2. Curves dialog.
3. Layer masks.
4. Tile-based undo + dirty-rect compositing (big-file comfort).
5. PSD export (so files round-trip with clients who use Photoshop).
6. Proper icon set + app icon (`assets/pixelbench.ico` is picked up automatically by `build.py` if present).
7. Editable text layers.

## Where things live

- Repo: `drjesticles/hello-world`, branch `claude/focused-feynman-rpjnuv`, folder `pixelbench/`.
- This note: `docs/PIXELBENCH_NOTES.md`. Second copy saved 2026-10-07 to Google Drive `Valhalla/PixelBench/`
  (this file as `PIXELBENCH_NOTES.md` plus a Google Doc "PixelBench Handoff" = README + these notes, exportable to PDF).
- Still to do: copy to the server (`~rexS/`) on Alex once the branch is pulled there.

## Build

- 2026-10-07: `python build.py` on Linux (Python 3.13, PySide6 6.11, PyInstaller 6) succeeded. One-folder output
  `dist/PixelBench/` is ~313 MB (Qt + SciPy + NumPy are the bulk); the binary launched and ran headless.
  Shrink ideas: drop SciPy by writing the flood fill in NumPy/Pillow, or use `--onefile` with UPX.
- Windows build not yet run (needs to be done on Orcastrader or aaagc: `pip install -r requirements.txt pyinstaller`
  then `python build.py`).
