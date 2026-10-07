"""Opening, saving and exporting documents.

* Flat raster formats (PNG, JPEG, WebP, BMP, GIF, TIFF, ICO...) go through Pillow.
* PSD / PSB files are imported with psd-tools, keeping layers, names, opacity,
  visibility and blend modes (groups are flattened into a plain list).
* The native layered format is ``.pxb``: a zip containing ``document.json`` and one PNG per layer.
"""
from __future__ import annotations

import io
import json
import os
import zipfile

import numpy as np
from PIL import Image

from .blend import BLEND_MODES
from .document import Document, Layer, _paste

NATIVE_EXT = ".pxb"
OPEN_FILTER = (
    "All supported (*.pxb *.psd *.psb *.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.ico *.tga);;"
    "PixelBench document (*.pxb);;Photoshop (*.psd *.psb);;PNG (*.png);;JPEG (*.jpg *.jpeg);;"
    "WebP (*.webp);;BMP (*.bmp);;GIF (*.gif);;TIFF (*.tif *.tiff);;All files (*)"
)
EXPORT_FILTER = "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;BMP (*.bmp);;GIF (*.gif);;TIFF (*.tif *.tiff);;ICO (*.ico)"

_PSD_BLEND = {
    "NORMAL": "Normal", "MULTIPLY": "Multiply", "SCREEN": "Screen", "OVERLAY": "Overlay",
    "DARKEN": "Darken", "LIGHTEN": "Lighten", "COLOR_DODGE": "Color Dodge", "COLOR_BURN": "Color Burn",
    "HARD_LIGHT": "Hard Light", "SOFT_LIGHT": "Soft Light", "DIFFERENCE": "Difference",
    "EXCLUSION": "Exclusion", "LINEAR_DODGE": "Add", "SUBTRACT": "Subtract", "PASS_THROUGH": "Normal",
}


def open_document(path: str) -> Document:
    ext = os.path.splitext(path)[1].lower()
    if ext == NATIVE_EXT:
        doc = load_native(path)
    elif ext in (".psd", ".psb"):
        doc = load_psd(path)
    else:
        img = Image.open(path)
        img = _flatten_pil(img)
        doc = Document(img.width, img.height)
        doc.add_layer("Background", pixels=np.asarray(img.convert("RGBA")))
    doc.path = path
    doc.dirty = False
    return doc


def _flatten_pil(img: Image.Image) -> Image.Image:
    """Handle palette/alpha/exif-orientation and return an RGBA image."""
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    if img.mode in ("P", "PA", "LA", "L", "1", "RGB", "CMYK", "I", "I;16", "F", "YCbCr"):
        img = img.convert("RGBA")
    elif img.mode != "RGBA":
        img = img.convert("RGBA")
    return img


def load_psd(path: str) -> Document:
    from psd_tools import PSDImage

    psd = PSDImage.open(path)
    doc = Document(psd.width, psd.height)

    def walk(container):
        for layer in container:
            if layer.is_group():
                yield from walk(layer)
            else:
                yield layer

    leaves = list(walk(psd))
    if not leaves:
        img = psd.composite()
        doc.add_layer("Background", pixels=np.asarray(img.convert("RGBA")))
        return doc

    for pl in leaves:  # psd-tools iterates bottom-to-top
        arr = np.zeros((doc.height, doc.width, 4), dtype=np.uint8)
        try:
            img = pl.composite()  # applies the layer's own mask; returns PIL image in bbox
        except Exception:
            img = None
        if img is not None:
            x0, y0 = pl.bbox[0], pl.bbox[1]
            _paste(arr, np.asarray(img.convert("RGBA")), int(x0), int(y0))
        layer = doc.add_layer(pl.name or "Layer", index=len(doc.layers), pixels=arr)
        layer.visible = bool(pl.visible)
        layer.opacity = float(pl.opacity) / 255.0
        try:
            layer.blend_mode = _PSD_BLEND.get(pl.blend_mode.name, "Normal")
        except Exception:
            layer.blend_mode = "Normal"
    doc.active_index = len(doc.layers) - 1
    return doc


def load_native(path: str) -> Document:
    with zipfile.ZipFile(path, "r") as zf:
        meta = json.loads(zf.read("document.json").decode("utf-8"))
        doc = Document(meta["width"], meta["height"])
        for i, lm in enumerate(meta["layers"]):
            data = zf.read(lm["file"])
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            layer = doc.add_layer(lm.get("name", f"Layer {i+1}"), index=i, pixels=np.asarray(img))
            layer.visible = bool(lm.get("visible", True))
            layer.opacity = float(lm.get("opacity", 1.0))
            bm = lm.get("blend_mode", "Normal")
            layer.blend_mode = bm if bm in BLEND_MODES else "Normal"
            layer.locked = bool(lm.get("locked", False))
        doc.active_index = int(meta.get("active_index", len(doc.layers) - 1))
        if "selection.png" in zf.namelist():
            sel = Image.open(io.BytesIO(zf.read("selection.png"))).convert("L")
            doc.selection = np.asarray(sel).copy()
    return doc


def save_native(doc: Document, path: str) -> None:
    meta = {
        "app": "PixelBench", "format": 1,
        "width": doc.width, "height": doc.height, "active_index": doc.active_index, "layers": [],
    }
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, layer in enumerate(doc.layers):
            fname = f"layer_{i:03d}.png"
            buf = io.BytesIO()
            layer.to_pil().save(buf, "PNG", compress_level=6)
            zf.writestr(fname, buf.getvalue())
            meta["layers"].append({
                "file": fname, "name": layer.name, "visible": layer.visible,
                "opacity": layer.opacity, "blend_mode": layer.blend_mode, "locked": layer.locked,
            })
        if doc.selection is not None:
            buf = io.BytesIO()
            Image.fromarray(doc.selection, "L").save(buf, "PNG")
            zf.writestr("selection.png", buf.getvalue())
        zf.writestr("document.json", json.dumps(meta, indent=2))
    os.replace(tmp, path)
    doc.path = path
    doc.dirty = False


def export_image(doc: Document, path: str, quality: int = 92, background=(255, 255, 255)) -> None:
    ext = os.path.splitext(path)[1].lower()
    img = doc.composite_pil()
    if ext in (".jpg", ".jpeg", ".bmp"):
        bg = Image.new("RGB", img.size, background)
        bg.paste(img, mask=img.split()[3])
        if ext == ".bmp":
            bg.save(path, "BMP")
        else:
            bg.save(path, "JPEG", quality=int(quality), optimize=True, subsampling=0 if quality >= 90 else 2)
    elif ext == ".webp":
        img.save(path, "WEBP", quality=int(quality), method=4)
    elif ext == ".gif":
        img.convert("P", palette=Image.ADAPTIVE).save(path, "GIF")
    elif ext in (".tif", ".tiff"):
        img.save(path, "TIFF", compression="tiff_lzw")
    elif ext == ".ico":
        img.save(path, "ICO")
    else:
        img.save(path, "PNG", compress_level=6)


def save_document(doc: Document, path: str, quality: int = 92) -> None:
    """Save to any supported path; layered when the extension is the native one."""
    if os.path.splitext(path)[1].lower() == NATIVE_EXT:
        save_native(doc, path)
    else:
        export_image(doc, path, quality=quality)
        doc.path = path
        doc.dirty = False
