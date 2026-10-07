"""Open / save / export for VectorBench documents."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QMarginsF, QRectF, QSizeF
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter

from .model import Document, ImageItem
from .svg import document_from_svg, export_svg

NATIVE_EXT = ".vbx"
OPEN_FILTER = ("All supported (*.vbx *.svg *.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff);;"
               "VectorBench document (*.vbx);;SVG (*.svg);;Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff);;All files (*)")
EXPORT_FILTER = "SVG (*.svg);;PDF (*.pdf);;PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;BMP (*.bmp);;TIFF (*.tif *.tiff)"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff")


def open_document(path: str) -> Document:
    ext = os.path.splitext(path)[1].lower()
    if ext == NATIVE_EXT:
        with open(path, "r", encoding="utf-8") as f:
            doc = Document.from_dict(json.load(f))
    elif ext == ".svg":
        with open(path, "r", encoding="utf-8") as f:
            doc = document_from_svg(f.read())
    elif ext in IMAGE_EXTS:
        img = QImage(path)
        if img.isNull():
            raise ValueError("Not a readable image")
        doc = Document(img.width(), img.height())
        doc.add_item(ImageItem(img))
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    doc.path = path
    doc.dirty = False
    return doc


def save_native(doc: Document, path: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc.to_dict(), f)
    os.replace(tmp, path)
    doc.path = path
    doc.dirty = False


def export(doc: Document, path: str, scale: float = 1.0, quality: int = 92, transparent: bool = False) -> None:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".svg":
        with open(path, "w", encoding="utf-8") as f:
            f.write(export_svg(doc))
    elif ext == ".pdf":
        export_pdf(doc, path)
    else:
        bg = None if (transparent and ext in (".png", ".webp", ".tif", ".tiff")) else QColor(255, 255, 255)
        img = doc.render_image(scale, bg)
        if ext in (".jpg", ".jpeg"):
            img = img.convertToFormat(QImage.Format.Format_RGB32)
        if not img.save(path, None, quality if ext in (".jpg", ".jpeg", ".webp") else -1):
            raise IOError(f"Could not write {path}")


def export_pdf(doc: Document, path: str) -> None:
    """True vector PDF: 1 document px = 1 pt (72 dpi)."""
    w = QPdfWriter(path)
    w.setResolution(72)
    w.setPageSize(QPageSize(QSizeF(doc.width, doc.height), QPageSize.Unit.Point))
    w.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
    w.setTitle(doc.name)
    w.setCreator("VectorBench")
    p = QPainter(w)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    doc.paint(p)
    p.end()


def place_image(path: str) -> ImageItem | None:
    img = QImage(path)
    if img.isNull():
        return None
    it = ImageItem(img)
    it.name = os.path.basename(path)
    return it
