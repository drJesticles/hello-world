"""Dialogs for VectorBench."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from .model import Paint

PRESETS = [("Custom", 0, 0), ("HD 1920 × 1080", 1920, 1080), ("4K 3840 × 2160", 3840, 2160), ("Square 1080 × 1080", 1080, 1080),
           ("Instagram story 1080 × 1920", 1080, 1920), ("Letter 612 × 792 pt", 612, 792), ("A4 595 × 842 pt", 595, 842),
           ("Business card 252 × 144 pt (3.5×2in)", 252, 144), ("Yard sign 1728 × 1296 pt (24×18in)", 1728, 1296),
           ("Banner 4320 × 1440 pt (60×20in)", 4320, 1440), ("Logo 1000 × 1000", 1000, 1000)]


def _ok_cancel(dlg, layout):
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
    layout.addRow(bb) if isinstance(layout, QFormLayout) else layout.addWidget(bb)


class NewDocumentDialog(QDialog):
    def __init__(self, parent=None, last=(1920, 1080)):
        super().__init__(parent)
        self.setWindowTitle("New Document")
        form = QFormLayout(self)
        self.preset = QComboBox(); self.preset.addItems([p[0] for p in PRESETS]); self.preset.currentIndexChanged.connect(self._preset)
        self.w = QDoubleSpinBox(); self.w.setRange(1, 50000); self.w.setValue(last[0]); self.w.setSuffix(" px")
        self.h = QDoubleSpinBox(); self.h.setRange(1, 50000); self.h.setValue(last[1]); self.h.setSuffix(" px")
        form.addRow("Preset", self.preset); form.addRow("Width", self.w); form.addRow("Height", self.h)
        form.addRow(QLabel("1 px = 1 pt in PDF export (72 per inch)."))
        _ok_cancel(self, form)

    def _preset(self, i):
        _, w, h = PRESETS[i]
        if w:
            self.w.setValue(w); self.h.setValue(h)

    def values(self):
        return self.w.value(), self.h.value()


class DocumentSetupDialog(QDialog):
    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Document Setup")
        form = QFormLayout(self)
        self.w = QDoubleSpinBox(); self.w.setRange(1, 50000); self.w.setValue(doc.width); self.w.setSuffix(" px")
        self.h = QDoubleSpinBox(); self.h.setRange(1, 50000); self.h.setValue(doc.height); self.h.setSuffix(" px")
        self.grid = QDoubleSpinBox(); self.grid.setRange(1, 1000); self.grid.setValue(doc.grid_size); self.grid.setSuffix(" px")
        form.addRow("Artboard width", self.w); form.addRow("Artboard height", self.h); form.addRow("Grid spacing", self.grid)
        _ok_cancel(self, form)


class ShapeSizeDialog(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.w = QDoubleSpinBox(); self.w.setRange(0.1, 50000); self.w.setValue(100); self.w.setSuffix(" px")
        self.h = QDoubleSpinBox(); self.h.setRange(0.1, 50000); self.h.setValue(100); self.h.setSuffix(" px")
        form.addRow("Width", self.w); form.addRow("Height", self.h)
        _ok_cancel(self, form)

    def values(self):
        return self.w.value(), self.h.value()


class TransformDialog(QDialog):
    """Move / Rotate / Scale / Reflect / Shear with a live preview on the selection."""

    def __init__(self, kind, apply_cb, parent=None):
        super().__init__(parent)
        self.setWindowTitle({"move": "Move", "rotate": "Rotate", "scale": "Scale", "reflect": "Reflect", "shear": "Shear"}[kind])
        self.kind = kind; self._apply = apply_cb
        form = QFormLayout(self)
        self.fields = {}
        if kind == "move":
            self._add(form, "dx", "Horizontal", 0.0, -50000, 50000, " px"); self._add(form, "dy", "Vertical", 0.0, -50000, 50000, " px")
        elif kind == "rotate":
            self._add(form, "angle", "Angle", 0.0, -360, 360, " °")
        elif kind == "scale":
            self._add(form, "sx", "Horizontal", 100.0, 1, 10000, " %"); self._add(form, "sy", "Vertical", 100.0, 1, 10000, " %")
            self.uniform = QCheckBox("Uniform"); self.uniform.setChecked(True); form.addRow(self.uniform)
            self.fields["sx"].valueChanged.connect(lambda v: self.uniform.isChecked() and self.fields["sy"].setValue(v))
        elif kind == "reflect":
            self.axis = QComboBox(); self.axis.addItems(["Vertical axis (flip horizontally)", "Horizontal axis (flip vertically)"])
            self.axis.currentIndexChanged.connect(self._preview); form.addRow("Axis", self.axis)
        elif kind == "shear":
            self._add(form, "angle", "Shear angle", 0.0, -89, 89, " °")
            self.axis = QComboBox(); self.axis.addItems(["Horizontal", "Vertical"]); self.axis.currentIndexChanged.connect(self._preview); form.addRow("Axis", self.axis)
        self.copy = QCheckBox("Copy (transform a duplicate)"); form.addRow(self.copy)
        _ok_cancel(self, form)
        self._preview()

    def _add(self, form, key, label, val, lo, hi, suffix):
        sb = QDoubleSpinBox(); sb.setRange(lo, hi); sb.setValue(val); sb.setSuffix(suffix); sb.setDecimals(2)
        sb.valueChanged.connect(self._preview); form.addRow(label, sb); self.fields[key] = sb

    def params(self):
        p = {k: v.value() for k, v in self.fields.items()}
        if hasattr(self, "axis"):
            p["axis"] = self.axis.currentIndex()
        return p

    def _preview(self, *_):
        self._apply(self.params(), preview=True)


class OffsetPathDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Offset Path")
        form = QFormLayout(self)
        self.offset = QDoubleSpinBox(); self.offset.setRange(-5000, 5000); self.offset.setValue(10); self.offset.setSuffix(" px")
        self.join = QComboBox(); self.join.addItems(["Round", "Miter", "Bevel"])
        form.addRow("Offset", self.offset); form.addRow("Joins", self.join)
        _ok_cancel(self, form)


class ExportDialog(QDialog):
    def __init__(self, ext, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Options")
        form = QFormLayout(self)
        self.scale = QDoubleSpinBox(); self.scale.setRange(1, 3200); self.scale.setValue(100); self.scale.setSuffix(" %")
        self.transparent = QCheckBox("Transparent background"); self.transparent.setChecked(ext in (".png", ".webp"))
        self.quality = QSlider(Qt.Orientation.Horizontal); self.quality.setRange(1, 100); self.quality.setValue(92)
        form.addRow("Scale", self.scale)
        if ext in (".png", ".webp", ".tif", ".tiff"):
            form.addRow(self.transparent)
        if ext in (".jpg", ".jpeg", ".webp"):
            form.addRow("Quality", self.quality)
        _ok_cancel(self, form)


class GradientDialog(QDialog):
    """Simple two-stop (or more) gradient editor."""

    def __init__(self, paint: Paint, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gradient")
        self.paint = paint.copy() if paint.kind in ("linear", "radial") else Paint("linear", stops=[(0.0, QColor(paint.color) if paint.kind == "solid" else QColor(0, 0, 0)), (1.0, QColor(255, 255, 255))])
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox(); self.kind.addItems(["Linear", "Radial"]); self.kind.setCurrentIndex(1 if self.paint.kind == "radial" else 0)
        form.addRow("Type", self.kind)
        self.angle = QDoubleSpinBox(); self.angle.setRange(-360, 360); self.angle.setSuffix(" °")
        import math
        dx, dy = self.paint.end[0] - self.paint.start[0], self.paint.end[1] - self.paint.start[1]
        self.angle.setValue(math.degrees(math.atan2(dy, dx)))
        form.addRow("Angle", self.angle)
        lay.addLayout(form)
        self.stop_widgets = []
        self.stops_box = QVBoxLayout(); lay.addLayout(self.stops_box)
        for t, c in self.paint.stops:
            self._add_stop(t, c)
        row = QHBoxLayout()
        add = QPushButton("Add stop"); add.clicked.connect(lambda: self._add_stop(0.5, QColor(128, 128, 128)))
        row.addWidget(add); lay.addLayout(row)
        _ok_cancel(self, lay)

    def _add_stop(self, t, c):
        row = QHBoxLayout()
        pos = QDoubleSpinBox(); pos.setRange(0, 1); pos.setSingleStep(0.05); pos.setValue(t)
        btn = QPushButton(); btn._color = QColor(c)
        def paint_btn(b=btn):
            b.setStyleSheet(f"background:{b._color.name()}; min-width: 60px;")
        def pick(_=False, b=btn):
            col = QColorDialog.getColor(b._color, self, "Stop colour", QColorDialog.ColorDialogOption.ShowAlphaChannel)
            if col.isValid():
                b._color = col; paint_btn(b)
        btn.clicked.connect(pick); paint_btn()
        rm = QPushButton("×"); rm.setFixedWidth(28)
        w = QWidget(); w.setLayout(row)
        row.addWidget(QLabel("Position")); row.addWidget(pos); row.addWidget(btn); row.addWidget(rm)
        entry = (pos, btn, w)
        rm.clicked.connect(lambda: self._remove(entry))
        self.stops_box.addWidget(w); self.stop_widgets.append(entry)

    def _remove(self, entry):
        if len(self.stop_widgets) <= 2:
            return
        self.stop_widgets.remove(entry); entry[2].setParent(None); entry[2].deleteLater()

    def result_paint(self) -> Paint:
        import math
        stops = sorted(((p.value(), QColor(b._color)) for p, b, _ in self.stop_widgets), key=lambda s: s[0])
        p = Paint("radial" if self.kind.currentIndex() == 1 else "linear", stops=stops)
        a = math.radians(self.angle.value())
        if p.kind == "radial":
            p.start, p.end = (0.5, 0.5), (0.5 + 0.5 * math.cos(a), 0.5 + 0.5 * math.sin(a))
        else:
            cx, cy = 0.5, 0.5
            p.start = (cx - 0.5 * math.cos(a), cy - 0.5 * math.sin(a)); p.end = (cx + 0.5 * math.cos(a), cy + 0.5 * math.sin(a))
        return p
