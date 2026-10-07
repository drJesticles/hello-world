"""Dialogs: new document, image/canvas size, filter parameters (live preview), text, transform, export."""
from __future__ import annotations

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFontComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from .document import Resample
from .filters import apply_with_mask

PRESETS = [
    ("Custom", 0, 0),
    ("HD 1920 × 1080", 1920, 1080),
    ("4K 3840 × 2160", 3840, 2160),
    ("Square 1080 × 1080", 1080, 1080),
    ("Instagram story 1080 × 1920", 1080, 1920),
    ("Facebook cover 820 × 312", 820, 312),
    ("Business card 1050 × 600 (3.5×2in @300)", 1050, 600),
    ("Letter 2550 × 3300 (8.5×11in @300)", 2550, 3300),
    ("A4 2480 × 3508 (@300)", 2480, 3508),
    ("Icon 512 × 512", 512, 512),
]


class NewDocumentDialog(QDialog):
    def __init__(self, parent=None, last=(1920, 1080)):
        super().__init__(parent)
        self.setWindowTitle("New Document")
        form = QFormLayout(self)
        self.preset = QComboBox()
        for name, _, _ in PRESETS:
            self.preset.addItem(name)
        self.preset.currentIndexChanged.connect(self._preset)
        self.w = QSpinBox(); self.w.setRange(1, 20000); self.w.setValue(last[0]); self.w.setSuffix(" px")
        self.h = QSpinBox(); self.h.setRange(1, 20000); self.h.setValue(last[1]); self.h.setSuffix(" px")
        self.bg = QComboBox(); self.bg.addItems(["White", "Transparent", "Background colour", "Black"])
        form.addRow("Preset", self.preset)
        form.addRow("Width", self.w)
        form.addRow("Height", self.h)
        form.addRow("Background", self.bg)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _preset(self, i):
        _, w, h = PRESETS[i]
        if w:
            self.w.setValue(w); self.h.setValue(h)

    def values(self):
        return self.w.value(), self.h.value(), self.bg.currentText()


class ResizeImageDialog(QDialog):
    def __init__(self, w, h, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Image Size")
        self._w0, self._h0 = w, h
        form = QFormLayout(self)
        self.w = QSpinBox(); self.w.setRange(1, 30000); self.w.setValue(w); self.w.setSuffix(" px")
        self.h = QSpinBox(); self.h.setRange(1, 30000); self.h.setValue(h); self.h.setSuffix(" px")
        self.pct = QDoubleSpinBox(); self.pct.setRange(1, 2000); self.pct.setValue(100); self.pct.setSuffix(" %")
        self.lock = QCheckBox("Constrain proportions"); self.lock.setChecked(True)
        self.method = QComboBox(); self.method.addItems(list(Resample.keys())); self.method.setCurrentText("Lanczos")
        self.w.valueChanged.connect(self._w_changed)
        self.h.valueChanged.connect(self._h_changed)
        self.pct.valueChanged.connect(self._pct_changed)
        form.addRow("Width", self.w); form.addRow("Height", self.h); form.addRow("Scale", self.pct)
        form.addRow(self.lock); form.addRow("Resample", self.method)
        form.addRow(QLabel(f"Current: {w} × {h} px"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)
        self._busy = False

    def _w_changed(self, v):
        if self._busy: return
        self._busy = True
        if self.lock.isChecked():
            self.h.setValue(max(1, round(v * self._h0 / self._w0)))
        self.pct.setValue(100.0 * v / self._w0)
        self._busy = False

    def _h_changed(self, v):
        if self._busy: return
        self._busy = True
        if self.lock.isChecked():
            self.w.setValue(max(1, round(v * self._w0 / self._h0)))
        self.pct.setValue(100.0 * self.w.value() / self._w0)
        self._busy = False

    def _pct_changed(self, v):
        if self._busy: return
        self._busy = True
        self.w.setValue(max(1, round(self._w0 * v / 100)))
        self.h.setValue(max(1, round(self._h0 * v / 100)))
        self._busy = False

    def values(self):
        return self.w.value(), self.h.value(), self.method.currentText()


class CanvasSizeDialog(QDialog):
    def __init__(self, w, h, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Canvas Size")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.w = QSpinBox(); self.w.setRange(1, 30000); self.w.setValue(w); self.w.setSuffix(" px")
        self.h = QSpinBox(); self.h.setRange(1, 30000); self.h.setValue(h); self.h.setSuffix(" px")
        form.addRow("Width", self.w); form.addRow("Height", self.h)
        lay.addLayout(form)
        box = QGroupBox("Anchor")
        grid = QGridLayout(box)
        self._anchor = (0.5, 0.5)
        self._buttons = {}
        for r in range(3):
            for c in range(3):
                b = QPushButton(["↖", "↑", "↗", "←", "·", "→", "↙", "↓", "↘"][r * 3 + c])
                b.setCheckable(True)
                b.setFixedSize(32, 32)
                b.clicked.connect(lambda _=False, rc=(r, c): self._set_anchor(rc))
                grid.addWidget(b, r, c)
                self._buttons[(r, c)] = b
        self._buttons[(1, 1)].setChecked(True)
        lay.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _set_anchor(self, rc):
        for k, b in self._buttons.items():
            b.setChecked(k == rc)
        self._anchor = (rc[1] / 2.0, rc[0] / 2.0)

    def values(self):
        return self.w.value(), self.h.value(), self._anchor


class FilterDialog(QDialog):
    """Generic parameter dialog with live preview on the active layer.

    ``apply`` is called with a dict of parameters and must return the new layer pixels.
    The layer is restored on cancel.
    """

    def __init__(self, title, params, apply, doc, layer, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._apply = apply
        self._doc, self._layer = doc, layer
        self._original = layer.pixels
        self._widgets = {}
        form = QFormLayout(self)
        for key, label, lo, hi, default, step in params:
            row = QHBoxLayout()
            is_float = isinstance(step, float) or isinstance(default, float) or isinstance(lo, float)
            if is_float:
                sb = QDoubleSpinBox(); sb.setDecimals(2); sb.setSingleStep(step)
            else:
                sb = QSpinBox(); sb.setSingleStep(int(step))
            sb.setRange(lo, hi); sb.setValue(default)
            sl = QSlider(Qt.Orientation.Horizontal)
            scale = 100.0 if is_float else 1
            sl.setRange(int(lo * scale), int(hi * scale)); sl.setValue(int(default * scale))
            sl.valueChanged.connect(lambda v, sb=sb, s=scale: sb.setValue(v / s if s != 1 else v))
            sb.valueChanged.connect(lambda v, sl=sl, s=scale: sl.setValue(int(v * s)))
            sb.valueChanged.connect(self._preview)
            row.addWidget(sl, 3); row.addWidget(sb, 1)
            form.addRow(label, row)
            self._widgets[key] = sb
        self.preview_cb = QCheckBox("Preview"); self.preview_cb.setChecked(True)
        self.preview_cb.toggled.connect(self._preview)
        form.addRow(self.preview_cb)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)
        self._preview()

    def params(self):
        return {k: w.value() for k, w in self._widgets.items()}

    def _preview(self, *_):
        if self.preview_cb.isChecked():
            try:
                self._layer.pixels = self._apply(self.params())
            except Exception as e:  # keep the UI alive on bad params
                self._layer.pixels = self._original
                self.setWindowTitle(f"{self.windowTitle().split(' — ')[0]} — error: {e}")
        else:
            self._layer.pixels = self._original
        self._doc.changed()

    def reject(self):
        self._layer.pixels = self._original
        self._doc.changed()
        super().reject()

    def accept(self):
        self._layer.pixels = self._apply(self.params())
        self._doc.changed()
        super().accept()


class TextDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Text")
        self.ctx = ctx
        lay = QVBoxLayout(self)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("Type your text here (multi-line OK)")
        lay.addWidget(self.edit)
        form = QFormLayout()
        self.font = QFontComboBox(); self.font.setCurrentFont(QFont(ctx.opt("text", "font", "Arial")))
        self.size = QSpinBox(); self.size.setRange(4, 2000); self.size.setValue(int(ctx.opt("text", "size", 48))); self.size.setSuffix(" px")
        self.bold = QCheckBox("Bold"); self.bold.setChecked(bool(ctx.opt("text", "bold", False)))
        self.italic = QCheckBox("Italic"); self.italic.setChecked(bool(ctx.opt("text", "italic", False)))
        self.align = QComboBox(); self.align.addItems(["Left", "Center", "Right"]); self.align.setCurrentText(ctx.opt("text", "align", "Left"))
        self.color_btn = QPushButton("Colour…"); self._color = QColor(ctx.fg)
        self.color_btn.clicked.connect(self._pick)
        self._update_color_btn()
        form.addRow("Font", self.font); form.addRow("Size", self.size)
        style = QHBoxLayout(); style.addWidget(self.bold); style.addWidget(self.italic); style.addWidget(self.align)
        form.addRow("Style", style); form.addRow("Colour", self.color_btn)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(420, 320)

    def _update_color_btn(self):
        self.color_btn.setStyleSheet(f"background:{self._color.name()}; color:{'#000' if self._color.lightness() > 128 else '#fff'};")

    def _pick(self):
        c = QColorDialog.getColor(self._color, self, "Text colour")
        if c.isValid():
            self._color = c
            self._update_color_btn()

    def accept(self):
        self.ctx.set_opt("text", "font", self.font.currentFont().family())
        self.ctx.set_opt("text", "size", self.size.value())
        self.ctx.set_opt("text", "bold", self.bold.isChecked())
        self.ctx.set_opt("text", "italic", self.italic.isChecked())
        self.ctx.set_opt("text", "align", self.align.currentText())
        self.ctx.set_fg(self._color)
        super().accept()

    def text(self):
        return self.edit.toPlainText()


class TransformDialog(QDialog):
    """Scale / rotate / flip the active layer with live preview."""

    def __init__(self, doc, layer, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transform Layer")
        self._doc, self._layer = doc, layer
        self._original = layer.pixels
        form = QFormLayout(self)
        self.scale_w = QDoubleSpinBox(); self.scale_w.setRange(1, 2000); self.scale_w.setValue(100); self.scale_w.setSuffix(" %")
        self.scale_h = QDoubleSpinBox(); self.scale_h.setRange(1, 2000); self.scale_h.setValue(100); self.scale_h.setSuffix(" %")
        self.lock = QCheckBox("Constrain proportions"); self.lock.setChecked(True)
        self.angle = QDoubleSpinBox(); self.angle.setRange(-360, 360); self.angle.setValue(0); self.angle.setSuffix(" °")
        self.flip_h = QCheckBox("Flip horizontal"); self.flip_v = QCheckBox("Flip vertical")
        self.dx = QSpinBox(); self.dx.setRange(-30000, 30000); self.dx.setSuffix(" px")
        self.dy = QSpinBox(); self.dy.setRange(-30000, 30000); self.dy.setSuffix(" px")
        form.addRow("Width", self.scale_w); form.addRow("Height", self.scale_h); form.addRow(self.lock)
        form.addRow("Rotate", self.angle)
        fl = QHBoxLayout(); fl.addWidget(self.flip_h); fl.addWidget(self.flip_v); form.addRow("Flip", fl)
        form.addRow("Offset X", self.dx); form.addRow("Offset Y", self.dy)
        self._busy = False
        self.scale_w.valueChanged.connect(self._sw); self.scale_h.valueChanged.connect(self._sh)
        for w in (self.angle, self.dx, self.dy):
            w.valueChanged.connect(self._preview)
        for w in (self.flip_h, self.flip_v):
            w.toggled.connect(self._preview)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _sw(self, v):
        if self._busy: return
        self._busy = True
        if self.lock.isChecked(): self.scale_h.setValue(v)
        self._busy = False
        self._preview()

    def _sh(self, v):
        if self._busy: return
        self._busy = True
        if self.lock.isChecked(): self.scale_w.setValue(v)
        self._busy = False
        self._preview()

    def compute(self) -> np.ndarray:
        src = self._original
        H, W = src.shape[:2]
        bbox = self._layer.bbox() if self._layer.pixels is src else _bbox(src)
        if bbox is None:
            return src
        x0, y0, x1, y1 = bbox
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        img = Image.fromarray(np.ascontiguousarray(src[y0:y1, x0:x1]), "RGBA")
        if self.flip_h.isChecked():
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        if self.flip_v.isChecked():
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        sw, sh = self.scale_w.value() / 100.0, self.scale_h.value() / 100.0
        nw, nh = max(1, int(round(img.width * sw))), max(1, int(round(img.height * sh)))
        if (nw, nh) != img.size:
            img = img.resize((nw, nh), Image.LANCZOS if (nw < img.width or nh < img.height) else Image.BICUBIC)
        if abs(self.angle.value()) > 1e-6:
            img = img.rotate(-self.angle.value(), resample=Image.BICUBIC, expand=True)
        out = np.zeros_like(src)
        ox = int(round(cx - img.width / 2.0)) + self.dx.value()
        oy = int(round(cy - img.height / 2.0)) + self.dy.value()
        from .document import _paste
        _paste(out, np.asarray(img), ox, oy)
        return out

    def _preview(self, *_):
        self._layer.pixels = self.compute()
        self._doc.changed()

    def reject(self):
        self._layer.pixels = self._original
        self._doc.changed()
        super().reject()

    def accept(self):
        self._layer.pixels = self.compute()
        self._doc.changed()
        super().accept()


def _bbox(src):
    a = src[..., 3]
    rows, cols = np.any(a, axis=1), np.any(a, axis=0)
    if not rows.any():
        return None
    y0, y1 = np.where(rows)[0][[0, -1]]
    x0, x1 = np.where(cols)[0][[0, -1]]
    return int(x0), int(y0), int(x1) + 1, int(y1) + 1


class ExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Options")
        form = QFormLayout(self)
        self.quality = QSlider(Qt.Orientation.Horizontal); self.quality.setRange(1, 100); self.quality.setValue(92)
        self.qlabel = QLabel("92")
        self.quality.valueChanged.connect(lambda v: self.qlabel.setText(str(v)))
        row = QHBoxLayout(); row.addWidget(self.quality); row.addWidget(self.qlabel)
        form.addRow("JPEG / WebP quality", row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)


class LayerPropertiesDialog(QDialog):
    def __init__(self, layer, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Layer Properties")
        from .blend import BLEND_MODES
        form = QFormLayout(self)
        self.name = QLineEdit(layer.name)
        self.opacity = QSpinBox(); self.opacity.setRange(0, 100); self.opacity.setValue(int(round(layer.opacity * 100))); self.opacity.setSuffix(" %")
        self.blend = QComboBox(); self.blend.addItems(BLEND_MODES); self.blend.setCurrentText(layer.blend_mode)
        self.locked = QCheckBox("Lock pixels"); self.locked.setChecked(layer.locked)
        form.addRow("Name", self.name); form.addRow("Opacity", self.opacity); form.addRow("Blend mode", self.blend); form.addRow(self.locked)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)
