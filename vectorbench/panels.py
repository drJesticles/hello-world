"""Panels: tool box, options bar, properties (transform/appearance/character), layers, swatches, align/pathfinder."""
from __future__ import annotations

import math

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFontComboBox, QFormLayout, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .model import BLEND_MODES, GroupItem, ImageItem, Item, Paint, PathItem, TextItem


def _glyph_icon(glyph: str, size: int = 24) -> QIcon:
    pm = QPixmap(size, size); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    f = QFont(); f.setPixelSize(size - 7); p.setFont(f); p.setPen(QColor(230, 230, 230))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, glyph); p.end()
    return QIcon(pm)


class ToolBox(QToolBar):
    def __init__(self, ctx, parent=None):
        super().__init__("Tools", parent)
        self.ctx = ctx
        self.setOrientation(Qt.Orientation.Vertical); self.setMovable(False); self.setIconSize(QSize(22, 22))
        self.group = QActionGroup(self); self.group.setExclusive(True)
        self.actions_by_name = {}
        for name, tool in ctx.tools.items():
            act = QAction(_glyph_icon(tool.glyph), tool.label, self)
            act.setCheckable(True); act.setToolTip(tool.tooltip or tool.label)
            if tool.shortcut:
                act.setShortcut(QKeySequence(tool.shortcut))
            act.triggered.connect(lambda _=False, n=name: ctx.set_tool(n))
            self.group.addAction(act); self.addAction(act); self.actions_by_name[name] = act
        ctx.toolChanged.connect(self._sync)

    def _sync(self, tool):
        a = self.actions_by_name.get(tool.name)
        if a and not a.isChecked():
            a.setChecked(True)


class OptionsBar(QToolBar):
    def __init__(self, ctx, parent=None):
        super().__init__("Tool Options", parent)
        self.ctx = ctx; self.setMovable(False); self._widgets = {}
        ctx.toolChanged.connect(self.rebuild); ctx.optionChanged.connect(self._sync)

    def rebuild(self, tool):
        self.clear(); self._widgets = {}
        t = QLabel(f"  {tool.label}:  "); t.setStyleSheet("font-weight:bold"); self.addWidget(t)
        for spec in tool.option_specs():
            key, typ = spec["key"], spec["type"]
            val = self.ctx.opt(tool.name, key, spec["default"])
            if typ != "bool":
                self.addWidget(QLabel(spec["label"] + " "))
            if typ == "int":
                w = QSpinBox(); w.setRange(spec.get("min", 0), spec.get("max", 100)); w.setValue(int(val)); w.setSuffix(spec.get("suffix", ""))
                w.valueChanged.connect(lambda v, k=key, n=tool.name: self.ctx.set_opt(n, k, int(v)))
            elif typ == "float":
                w = QDoubleSpinBox(); w.setRange(spec.get("min", 0), spec.get("max", 100)); w.setValue(float(val)); w.setSingleStep(spec.get("step", 1)); w.setSuffix(spec.get("suffix", ""))
                w.valueChanged.connect(lambda v, k=key, n=tool.name: self.ctx.set_opt(n, k, float(v)))
            elif typ == "bool":
                w = QCheckBox(spec["label"]); w.setChecked(bool(val)); w.toggled.connect(lambda v, k=key, n=tool.name: self.ctx.set_opt(n, k, bool(v)))
            elif typ == "choice":
                w = QComboBox(); w.addItems(spec["choices"]); w.setCurrentText(str(val)); w.currentTextChanged.connect(lambda v, k=key, n=tool.name: self.ctx.set_opt(n, k, v))
            elif typ == "font":
                w = QFontComboBox(); w.setCurrentFont(QFont(str(val))); w.currentFontChanged.connect(lambda f, k=key, n=tool.name: self.ctx.set_opt(n, k, f.family()))
            else:
                continue
            self.addWidget(w); self._widgets[key] = w; self.addSeparator()

    def _sync(self, tool_name, key):
        if self.ctx.tool is None or tool_name != self.ctx.tool.name:
            return
        w = self._widgets.get(key)
        if w is None:
            return
        val = self.ctx.opt(tool_name, key)
        w.blockSignals(True)
        if isinstance(w, (QSpinBox, QDoubleSpinBox)): w.setValue(val)
        elif isinstance(w, QCheckBox): w.setChecked(bool(val))
        elif isinstance(w, QComboBox) and not isinstance(w, QFontComboBox): w.setCurrentText(str(val))
        w.blockSignals(False)


# ---------------------------------------------------------------------------
class PaintButton(QPushButton):
    """Shows a Paint (none / colour / gradient). Click = colour dialog; right-click menu for none/gradient."""
    changed = Signal(object)

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.label = label
        self.paint = Paint.none()
        self.setFixedSize(44, 28)
        self.setToolTip(f"{label}: click to pick a colour, right-click for None / Gradient")
        self.clicked.connect(self._pick)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def set_paint(self, p: Paint):
        self.paint = p.copy(); self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        r = self.rect().adjusted(5, 5, -5, -5)
        if self.paint.is_none():
            p.fillRect(r, QColor(255, 255, 255)); p.setPen(QColor(220, 0, 0)); p.drawLine(r.bottomLeft(), r.topRight())
        else:
            p.fillRect(r, self.paint.brush(r.toRectF()))
        p.setPen(QColor(0, 0, 0)); p.drawRect(r)
        p.end()

    def _pick(self):
        start = self.paint.color if self.paint.kind == "solid" else QColor(0, 0, 0)
        c = QColorDialog.getColor(start, self, f"{self.label} colour", QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self.set_paint(Paint.solid(c)); self.changed.emit(self.paint)

    def _menu(self, pos):
        m = QMenu(self)
        m.addAction("None", lambda: (self.set_paint(Paint.none()), self.changed.emit(self.paint)))
        m.addAction("Colour…", self._pick)
        m.addAction("Gradient…", self._gradient)
        m.addAction("Black", lambda: (self.set_paint(Paint.solid(QColor(0, 0, 0))), self.changed.emit(self.paint)))
        m.addAction("White", lambda: (self.set_paint(Paint.solid(QColor(255, 255, 255))), self.changed.emit(self.paint)))
        m.exec(self.mapToGlobal(pos))

    def _gradient(self):
        from .dialogs import GradientDialog
        dlg = GradientDialog(self.paint, self)
        if dlg.exec():
            self.set_paint(dlg.result_paint()); self.changed.emit(self.paint)


class PropertiesPanel(QWidget):
    """Transform + Appearance + Character. Edits the selection, or the default appearance when nothing is selected."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window; self.ctx = window.ctx
        self._updating = False
        outer = QVBoxLayout(self); outer.setContentsMargins(4, 4, 4, 4)
        # transform
        tb = QGroupBox("Transform"); tf = QGridLayout(tb)
        self.x = self._spin(-100000, 100000); self.y = self._spin(-100000, 100000)
        self.w = self._spin(0.01, 100000); self.h = self._spin(0.01, 100000); self.angle = self._spin(-360, 360, " °")
        self.lock = QCheckBox("⚭"); self.lock.setToolTip("Constrain width/height"); self.lock.setChecked(True)
        for i, (lbl, wdg) in enumerate([("X", self.x), ("Y", self.y), ("W", self.w), ("H", self.h)]):
            tf.addWidget(QLabel(lbl), i // 2, (i % 2) * 2); tf.addWidget(wdg, i // 2, (i % 2) * 2 + 1)
        tf.addWidget(QLabel("∠"), 2, 0); tf.addWidget(self.angle, 2, 1); tf.addWidget(self.lock, 2, 2)
        for s in (self.x, self.y, self.w, self.h):
            s.editingFinished.connect(self._apply_geometry)
        self.angle.editingFinished.connect(self._apply_angle)
        outer.addWidget(tb)
        # appearance
        ab = QGroupBox("Appearance"); af = QFormLayout(ab)
        row = QHBoxLayout()
        self.fill_btn = PaintButton("Fill"); self.stroke_btn = PaintButton("Stroke")
        self.fill_btn.changed.connect(lambda p: self._apply_style("fill", p)); self.stroke_btn.changed.connect(lambda p: self._apply_style("stroke", p))
        swap = QToolButton(); swap.setText("⇄"); swap.setToolTip("Swap fill and stroke"); swap.clicked.connect(self._swap)
        row.addWidget(QLabel("Fill")); row.addWidget(self.fill_btn); row.addWidget(QLabel("Stroke")); row.addWidget(self.stroke_btn); row.addWidget(swap); row.addStretch()
        af.addRow(row)
        self.stroke_w = self._spin(0, 1000, " px"); self.stroke_w.valueChanged.connect(lambda v: self._apply_style("stroke_width", float(v)))
        af.addRow("Stroke width", self.stroke_w)
        r2 = QHBoxLayout()
        self.cap = QComboBox(); self.cap.addItems(["Butt", "Round", "Square"]); self.cap.currentTextChanged.connect(lambda v: self._apply_style("cap", v))
        self.join = QComboBox(); self.join.addItems(["Miter", "Round", "Bevel"]); self.join.currentTextChanged.connect(lambda v: self._apply_style("join", v))
        r2.addWidget(QLabel("Cap")); r2.addWidget(self.cap); r2.addWidget(QLabel("Join")); r2.addWidget(self.join); af.addRow(r2)
        self.dash = QLineEdit(); self.dash.setPlaceholderText("dash gap … e.g. 6 3"); self.dash.editingFinished.connect(self._apply_dash)
        af.addRow("Dashes", self.dash)
        self.opacity = QSpinBox(); self.opacity.setRange(0, 100); self.opacity.setValue(100); self.opacity.setSuffix(" %")
        self.opacity.valueChanged.connect(lambda v: self._apply_style("opacity", v / 100.0))
        self.blend = QComboBox(); self.blend.addItems(BLEND_MODES); self.blend.currentTextChanged.connect(lambda v: self._apply_style("blend", v))
        r3 = QHBoxLayout(); r3.addWidget(self.opacity); r3.addWidget(self.blend); af.addRow("Opacity", r3)
        outer.addWidget(ab)
        # character
        self.char_box = QGroupBox("Character"); cf = QFormLayout(self.char_box)
        self.font = QFontComboBox(); self.font.currentFontChanged.connect(lambda f: self._apply_text("family", f.family()))
        self.size = self._spin(1, 5000, " px"); self.size.valueChanged.connect(lambda v: self._apply_text("size", float(v)))
        r4 = QHBoxLayout()
        self.bold = QCheckBox("B"); self.bold.setStyleSheet("font-weight:bold"); self.bold.toggled.connect(lambda v: self._apply_text("bold", v))
        self.italic = QCheckBox("I"); self.italic.setStyleSheet("font-style:italic"); self.italic.toggled.connect(lambda v: self._apply_text("italic", v))
        self.align = QComboBox(); self.align.addItems(["Left", "Center", "Right"]); self.align.currentTextChanged.connect(lambda v: self._apply_text("align", v))
        r4.addWidget(self.bold); r4.addWidget(self.italic); r4.addWidget(self.align)
        self.leading = self._spin(0.5, 5, "×"); self.leading.setSingleStep(0.1); self.leading.valueChanged.connect(lambda v: self._apply_text("line_height", float(v)))
        self.tracking = self._spin(-50, 200, " px"); self.tracking.valueChanged.connect(lambda v: self._apply_text("letter_spacing", float(v)))
        cf.addRow("Font", self.font); cf.addRow("Size", self.size); cf.addRow("Style", r4); cf.addRow("Leading", self.leading); cf.addRow("Tracking", self.tracking)
        outer.addWidget(self.char_box)
        self.info = QLabel(""); self.info.setWordWrap(True); self.info.setStyleSheet("color:#aaa"); outer.addWidget(self.info)
        outer.addStretch()
        self.ctx.styleChanged.connect(self.refresh)

    def _spin(self, lo, hi, suffix=""):
        s = QDoubleSpinBox(); s.setRange(lo, hi); s.setDecimals(2); s.setSuffix(suffix); s.setKeyboardTracking(False); return s

    def _sel(self) -> list[Item]:
        v = self.window.current_view()
        return list(v.selection) if v else []

    # -- refresh from selection / defaults ------------------------------------
    def refresh(self):
        self._updating = True
        sel = self._sel()
        from .pathops import selection_bounds
        if sel:
            b = selection_bounds(sel)
            self.x.setValue(b.x()); self.y.setValue(b.y()); self.w.setValue(b.width()); self.h.setValue(b.height())
            t = sel[0].transform
            self.angle.setValue(math.degrees(math.atan2(t.m12(), t.m11())))
            st = sel[0].style
            self.info.setText(f"{len(sel)} object(s): " + ", ".join(i.display_name() for i in sel[:4]) + ("…" if len(sel) > 4 else ""))
        else:
            self.x.setValue(0); self.y.setValue(0); self.w.setValue(0); self.h.setValue(0); self.angle.setValue(0)
            st = self.ctx.style
            self.info.setText("Nothing selected: editing the default appearance for new objects.")
        for s in (self.x, self.y, self.w, self.h, self.angle):
            s.setEnabled(bool(sel))
        self.fill_btn.set_paint(st.fill); self.stroke_btn.set_paint(st.stroke)
        self.stroke_w.setValue(st.stroke_width); self.cap.setCurrentText(st.cap); self.join.setCurrentText(st.join)
        self.dash.setText(" ".join(f"{d:g}" for d in st.dash)); self.opacity.setValue(int(round(st.opacity * 100))); self.blend.setCurrentText(st.blend)
        texts = [i for i in sel if isinstance(i, TextItem)]
        self.char_box.setVisible(bool(texts) or (self.ctx.tool is not None and self.ctx.tool.name == "type"))
        if texts:
            t = texts[0]
            self.font.setCurrentFont(QFont(t.family)); self.size.setValue(t.size); self.bold.setChecked(t.bold); self.italic.setChecked(t.italic)
            self.align.setCurrentText(t.align); self.leading.setValue(t.line_height); self.tracking.setValue(t.letter_spacing)
        self._updating = False

    # -- apply -------------------------------------------------------------------
    def _apply_style(self, attr, value):
        if self._updating:
            return
        sel = [i for i in self._sel() if not isinstance(i, (ImageItem,)) or attr in ("opacity", "blend", "stroke", "stroke_width")]
        if attr in ("fill", "stroke"):
            value = value.copy()
        setattr(self.ctx.style, attr, value.copy() if isinstance(value, Paint) else value)
        if sel:
            v = self.window.current_view()
            v.history.push("Appearance")
            for it in sel:
                targets = list(it.walk()) + [it] if isinstance(it, GroupItem) else [it]
                for t in targets:
                    if isinstance(t, GroupItem) and attr not in ("opacity", "blend"):
                        continue
                    setattr(t.style, attr, value.copy() if isinstance(value, Paint) else value)
            v.doc.changed()

    def _apply_dash(self):
        if self._updating:
            return
        try:
            vals = [float(x) for x in self.dash.text().replace(",", " ").split()]
        except ValueError:
            return
        self._apply_style("dash", vals)

    def _swap(self):
        f, s = self.fill_btn.paint.copy(), self.stroke_btn.paint.copy()
        self._apply_style("fill", s); self._apply_style("stroke", f)
        self.refresh()

    def _apply_text(self, attr, value):
        if self._updating:
            return
        sel = [i for i in self._sel() if isinstance(i, TextItem)]
        if attr in ("family", "size", "bold", "italic", "align"):
            self.ctx.set_opt("type", {"family": "font"}.get(attr, attr), value)
        if not sel:
            return
        v = self.window.current_view(); v.history.push("Character")
        for t in sel:
            setattr(t, attr, value)
        v.doc.changed(); v.selectionChanged.emit()

    def _apply_geometry(self):
        if self._updating:
            return
        sel = self._sel()
        if not sel:
            return
        from .pathops import selection_bounds, transform_about
        from PySide6.QtGui import QTransform
        b = selection_bounds(sel)
        v = self.window.current_view(); v.history.push("Transform")
        nx, ny, nw, nh = self.x.value(), self.y.value(), self.w.value(), self.h.value()
        if self.lock.isChecked() and abs(nw - b.width()) > 1e-6 and abs(nh - b.height()) < 1e-6:
            nh = b.height() * nw / b.width()
        elif self.lock.isChecked() and abs(nh - b.height()) > 1e-6 and abs(nw - b.width()) < 1e-6:
            nw = b.width() * nh / b.height()
        sx, sy = nw / b.width() if b.width() else 1, nh / b.height() if b.height() else 1
        transform_about(sel, QTransform.fromScale(sx, sy), b.topLeft())
        for it in sel:
            it.transform = it.transform * QTransform.fromTranslate(nx - b.x(), ny - b.y())
        v.doc.changed(); v.selectionChanged.emit()

    def _apply_angle(self):
        if self._updating:
            return
        sel = self._sel()
        if not sel:
            return
        from .pathops import selection_bounds, transform_about
        from PySide6.QtGui import QTransform
        t = sel[0].transform
        cur = math.degrees(math.atan2(t.m12(), t.m11()))
        d = self.angle.value() - cur
        if abs(d) < 1e-6:
            return
        v = self.window.current_view(); v.history.push("Rotate")
        r = QTransform(); r.rotate(d)
        transform_about(sel, r, selection_bounds(sel).center())
        v.doc.changed(); v.selectionChanged.emit()


# ---------------------------------------------------------------------------
class LayersPanel(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window; self._doc = None; self._updating = False
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["Name", "👁", "🔒"]); self.tree.setColumnWidth(0, 160); self.tree.setColumnWidth(1, 28); self.tree.setColumnWidth(2, 28)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemChanged.connect(self._item_changed); self.tree.itemSelectionChanged.connect(self._tree_selection)
        self.tree.itemDoubleClicked.connect(self._double)
        lay.addWidget(self.tree, 1)
        btns = QHBoxLayout()
        for text, tip, slot in [("＋", "New layer", self.window.layer_new), ("▲", "Move up", lambda: self.window.reorder("forward")),
                                ("▼", "Move down", lambda: self.window.reorder("backward")), ("🗑", "Delete layer", self.window.layer_delete)]:
            b = QToolButton(); b.setText(text); b.setToolTip(tip); b.clicked.connect(slot); btns.addWidget(b)
        lay.addLayout(btns)

    def set_document(self, doc):
        self._doc = doc; self.refresh()

    def _add_item_rows(self, parent, items):
        for it in reversed(items):
            row = QTreeWidgetItem(parent, [it.display_name(), "", ""])
            row.setData(0, Qt.ItemDataRole.UserRole, ("item", it.id))
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            row.setCheckState(1, Qt.CheckState.Checked if it.visible else Qt.CheckState.Unchecked)
            row.setCheckState(2, Qt.CheckState.Checked if it.locked else Qt.CheckState.Unchecked)
            if isinstance(it, GroupItem):
                self._add_item_rows(row, it.children)

    def refresh(self):
        self._updating = True
        self.tree.clear()
        if self._doc is not None:
            for li in range(len(self._doc.layers) - 1, -1, -1):
                layer = self._doc.layers[li]
                row = QTreeWidgetItem(self.tree, [layer.name, "", ""])
                row.setData(0, Qt.ItemDataRole.UserRole, ("layer", layer.id))
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
                row.setCheckState(1, Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked)
                row.setCheckState(2, Qt.CheckState.Checked if layer.locked else Qt.CheckState.Unchecked)
                f = row.font(0); f.setBold(li == self._doc.active_layer_index); row.setFont(0, f)
                row.setForeground(0, layer.color)
                self._add_item_rows(row, layer.items)
                row.setExpanded(True)
        self.sync_selection()
        self._updating = False

    def sync_selection(self):
        v = self.window.current_view()
        if v is None:
            return
        self._updating = True
        ids = {i.id for i in v.selection}
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            row = it.value()
            kind, iid = row.data(0, Qt.ItemDataRole.UserRole)
            row.setSelected(kind == "item" and iid in ids)
            it += 1
        self._updating = False

    def _tree_selection(self):
        if self._updating or self._doc is None:
            return
        v = self.window.current_view()
        if v is None:
            return
        items = []
        for row in self.tree.selectedItems():
            kind, iid = row.data(0, Qt.ItemDataRole.UserRole)
            if kind == "item":
                it = self._doc.find(iid)
                if it is not None:
                    items.append(it)
            elif kind == "layer":
                for li, layer in enumerate(self._doc.layers):
                    if layer.id == iid:
                        self._doc.active_layer_index = li
        self._updating = True
        v.set_selection(items)
        self._updating = False

    def _item_changed(self, row, col):
        if self._updating or self._doc is None:
            return
        kind, iid = row.data(0, Qt.ItemDataRole.UserRole)
        target = None
        if kind == "layer":
            target = next((l for l in self._doc.layers if l.id == iid), None)
        else:
            target = self._doc.find(iid)
            if target is None:
                for it in self._doc.all_items(include_hidden=True, top_level_only=False):
                    if it.id == iid:
                        target = it
        if target is None:
            return
        v = self.window.current_view()
        if col == 0:
            v.history.push("Rename"); target.name = row.text(0)
        elif col == 1:
            v.history.push("Visibility"); target.visible = row.checkState(1) == Qt.CheckState.Checked
        elif col == 2:
            v.history.push("Lock"); target.locked = row.checkState(2) == Qt.CheckState.Checked
        self._doc.changed(structure=False)

    def _double(self, row, col):
        if col == 0:
            self.tree.editItem(row, 0)


from PySide6.QtWidgets import QTreeWidgetItemIterator  # noqa: E402


# ---------------------------------------------------------------------------
DEFAULT_SWATCHES = ["#000000", "#ffffff", "#7f7f7f", "#c3c3c3", "#ed1c24", "#ff7f27", "#fff200", "#22b14c", "#00a2e8", "#3f48cc",
                    "#a349a4", "#b97a57", "#880015", "#ffaec9", "#ffc90e", "#efe4b0", "#b5e61d", "#99d9ea", "#7092be", "#c8bfe7",
                    "#1d3557", "#457b9d", "#a8dadc", "#f1faee", "#e63946", "#2a9d8f", "#e9c46a", "#f4a261", "#e76f51", "#264653"]


class SwatchesPanel(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.colors = list(DEFAULT_SWATCHES)
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4)
        self.grid = QGridLayout(); self.grid.setSpacing(2); lay.addLayout(self.grid)
        hint = QLabel("Click: fill · Right-click: stroke · + adds the current fill"); hint.setStyleSheet("color:#aaa; font-size:10px"); hint.setWordWrap(True)
        lay.addWidget(hint)
        add = QPushButton("+ Add current fill"); add.clicked.connect(self._add); lay.addWidget(add)
        lay.addStretch()
        self.rebuild()

    def rebuild(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w: w.deleteLater()
        for i, c in enumerate(self.colors):
            b = QToolButton(); b.setFixedSize(22, 22); b.setStyleSheet(f"background:{c}; border:1px solid #222;"); b.setToolTip(c)
            b.clicked.connect(lambda _=False, col=c: self._apply(col, "fill"))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _p, col=c: self._apply(col, "stroke"))
            self.grid.addWidget(b, i // 8, i % 8)

    def _apply(self, col, which):
        self.window.properties._apply_style(which, Paint.solid(QColor(col)))
        self.window.properties.refresh()

    def _add(self):
        p = self.window.ctx.style.fill
        if p.kind == "solid" and p.color.name() not in self.colors:
            self.colors.append(p.color.name()); self.rebuild()


# ---------------------------------------------------------------------------
class AlignPanel(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4)
        g = QGroupBox("Align"); gl = QGridLayout(g)
        for i, (glyph, mode, tip) in enumerate([("⫷", "left", "Align left"), ("⫿", "hcenter", "Align horizontal centres"), ("⫸", "right", "Align right"),
                                                ("⫠", "top", "Align top"), ("⫨", "vcenter", "Align vertical centres"), ("⫡", "bottom", "Align bottom")]):
            b = QToolButton(); b.setText(glyph); b.setToolTip(tip); b.clicked.connect(lambda _=False, m=mode: self.window.align(m)); gl.addWidget(b, i // 3, i % 3)
        self.to_artboard = QCheckBox("Align to artboard"); gl.addWidget(self.to_artboard, 2, 0, 1, 3)
        lay.addWidget(g)
        d = QGroupBox("Distribute"); dl = QGridLayout(d)
        for i, (glyph, mode, tip) in enumerate([("⫼", "hspace", "Equal horizontal spacing"), ("⫽", "vspace", "Equal vertical spacing"),
                                                ("⋯", "hcenter", "Horizontal centres"), ("⋮", "vcenter", "Vertical centres")]):
            b = QToolButton(); b.setText(glyph); b.setToolTip(tip); b.clicked.connect(lambda _=False, m=mode: self.window.distribute(m)); dl.addWidget(b, 0, i)
        lay.addWidget(d)
        pf = QGroupBox("Pathfinder"); pl = QGridLayout(pf)
        for i, (label, op) in enumerate([("Unite", "unite"), ("Minus Front", "minus_front"), ("Intersect", "intersect"), ("Exclude", "exclude"), ("Minus Back", "minus_back")]):
            b = QPushButton(label); b.clicked.connect(lambda _=False, o=op: self.window.pathfinder(o)); pl.addWidget(b, i // 2, i % 2)
        lay.addWidget(pf)
        lay.addStretch()
