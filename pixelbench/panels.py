"""Dock panels and bars: tool box, tool options bar, colour swatches, layers, history."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFontComboBox,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QPushButton, QSlider, QSpinBox,
                               QToolBar, QToolButton, QVBoxLayout, QWidget)

from .blend import BLEND_MODES
from .qtutil import qimage_from_array


# ---------------------------------------------------------------------------
class ToolBox(QToolBar):
    def __init__(self, ctx, parent=None):
        super().__init__("Tools", parent)
        self.ctx = ctx
        self.setOrientation(Qt.Orientation.Vertical)
        self.setMovable(False)
        self.setIconSize(QSize(22, 22))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.group = QActionGroup(self)
        self.group.setExclusive(True)
        self.actions_by_name = {}
        for name, tool in ctx.tools.items():
            act = QAction(_glyph_icon(tool.glyph), tool.label, self)
            act.setCheckable(True)
            act.setToolTip(f"{tool.tooltip or tool.label}")
            if tool.shortcut:
                act.setShortcut(QKeySequence(tool.shortcut))
                act.setToolTip(f"{tool.tooltip or tool.label}")
            act.triggered.connect(lambda _=False, n=name: ctx.set_tool(n))
            self.group.addAction(act)
            self.addAction(act)
            self.actions_by_name[name] = act
        ctx.toolChanged.connect(self._sync)

    def _sync(self, tool):
        act = self.actions_by_name.get(tool.name)
        if act and not act.isChecked():
            act.setChecked(True)


def _glyph_icon(glyph: str, size: int = 24) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    f = QFont()
    f.setPixelSize(size - 6)
    p.setFont(f)
    p.setPen(QColor(230, 230, 230))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
class ColorSwatches(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setFixedSize(56, 56)
        self.setToolTip("Foreground / background colours. Click to change, X swaps, D resets.")
        ctx.colorsChanged.connect(self.update)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QColor(20, 20, 20))
        p.setBrush(self.ctx.bg)
        p.drawRect(20, 20, 30, 30)
        p.setBrush(self.ctx.fg)
        p.drawRect(4, 4, 30, 30)
        p.setPen(QColor(200, 200, 200))
        p.drawText(42, 14, "⇄")
        p.drawText(2, 54, "▪")
        p.end()

    def mousePressEvent(self, ev):
        x, y = ev.position().x(), ev.position().y()
        if 4 <= x <= 34 and 4 <= y <= 34:
            c = QColorDialog.getColor(self.ctx.fg, self, "Foreground colour")
            if c.isValid():
                self.ctx.set_fg(c)
        elif 20 <= x <= 50 and 20 <= y <= 50:
            c = QColorDialog.getColor(self.ctx.bg, self, "Background colour")
            if c.isValid():
                self.ctx.set_bg(c)
        elif x > 38 and y < 18:
            self.ctx.swap_colors()
        elif x < 14 and y > 42:
            self.ctx.reset_colors()


# ---------------------------------------------------------------------------
class OptionsBar(QToolBar):
    """Rebuilt whenever the tool changes; writes values into ctx.options[tool][key]."""

    def __init__(self, ctx, parent=None):
        super().__init__("Tool Options", parent)
        self.ctx = ctx
        self.setMovable(False)
        self._widgets = {}
        ctx.toolChanged.connect(self.rebuild)
        ctx.optionChanged.connect(self._sync)

    def rebuild(self, tool):
        self.clear()
        self._widgets = {}
        title = QLabel(f"  {tool.label}:  ")
        title.setStyleSheet("font-weight: bold;")
        self.addWidget(title)
        for spec in tool.option_specs():
            key, typ = spec["key"], spec["type"]
            val = self.ctx.opt(tool.name, key, spec["default"])
            if typ != "bool":
                self.addWidget(QLabel(spec["label"] + " "))
            if typ == "int":
                w = QSpinBox(); w.setRange(spec.get("min", 0), spec.get("max", 100)); w.setValue(int(val))
                w.setSuffix(spec.get("suffix", ""))
                w.valueChanged.connect(lambda v, k=key, t=tool.name: self.ctx.set_opt(t, k, int(v)))
                if spec.get("max", 100) - spec.get("min", 0) > 4:
                    sl = QSlider(Qt.Orientation.Horizontal); sl.setRange(spec.get("min", 0), spec.get("max", 100))
                    sl.setValue(int(val)); sl.setFixedWidth(90)
                    sl.valueChanged.connect(w.setValue); w.valueChanged.connect(sl.setValue)
                    self.addWidget(sl)
            elif typ == "float":
                w = QDoubleSpinBox(); w.setRange(spec.get("min", 0.0), spec.get("max", 100.0)); w.setValue(float(val))
                w.setSingleStep(spec.get("step", 0.1)); w.setSuffix(spec.get("suffix", ""))
                w.valueChanged.connect(lambda v, k=key, t=tool.name: self.ctx.set_opt(t, k, float(v)))
            elif typ == "bool":
                w = QCheckBox(spec["label"]); w.setChecked(bool(val))
                w.toggled.connect(lambda v, k=key, t=tool.name: self.ctx.set_opt(t, k, bool(v)))
            elif typ == "choice":
                w = QComboBox(); w.addItems(spec["choices"]); w.setCurrentText(str(val))
                w.currentTextChanged.connect(lambda v, k=key, t=tool.name: self.ctx.set_opt(t, k, v))
            elif typ == "font":
                w = QFontComboBox(); w.setCurrentFont(QFont(str(val)))
                w.currentFontChanged.connect(lambda f, k=key, t=tool.name: self.ctx.set_opt(t, k, f.family()))
            else:
                continue
            self.addWidget(w)
            self._widgets[key] = w
            self.addSeparator()

    def _sync(self, tool_name, key):
        if self.ctx.tool is None or tool_name != self.ctx.tool.name:
            return
        w = self._widgets.get(key)
        if w is None:
            return
        val = self.ctx.opt(tool_name, key)
        w.blockSignals(True)
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            w.setValue(val)
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(val))
        elif isinstance(w, QComboBox) and not isinstance(w, QFontComboBox):
            w.setCurrentText(str(val))
        w.blockSignals(False)


# ---------------------------------------------------------------------------
def layer_thumbnail(layer, size: int = 40) -> QIcon:
    img = qimage_from_array(layer.pixels)
    scaled = img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    pm = QPixmap(size, size)
    pm.fill(QColor(90, 90, 90))
    p = QPainter(pm)
    # checker
    for y in range(0, size, 8):
        for x in range(0, size, 8):
            if (x // 8 + y // 8) % 2 == 0:
                p.fillRect(x, y, 8, 8, QColor(140, 140, 140))
    p.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    p.end()
    return QIcon(pm)


class LayersPanel(QWidget):
    """Layer list with visibility, opacity, blend mode and the usual buttons."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self._doc = None
        self._updating = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        self.blend = QComboBox(); self.blend.addItems(BLEND_MODES)
        self.blend.currentTextChanged.connect(self._blend_changed)
        self.opacity = QSlider(Qt.Orientation.Horizontal); self.opacity.setRange(0, 100); self.opacity.setValue(100)
        self.opacity.valueChanged.connect(self._opacity_changed)
        self.opacity.sliderReleased.connect(self._opacity_commit)
        self.opacity_lbl = QLabel("100%"); self.opacity_lbl.setFixedWidth(40)
        top.addWidget(self.blend, 2); top.addWidget(self.opacity, 3); top.addWidget(self.opacity_lbl)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setIconSize(QSize(40, 40))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemChanged.connect(self._item_changed)
        self.list.currentRowChanged.connect(self._row_changed)
        self.list.itemDoubleClicked.connect(lambda _: self.window.layer_properties())
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context)
        lay.addWidget(self.list, 1)
        btns = QHBoxLayout()
        for text, tip, slot in [
            ("＋", "New layer", self.window.layer_new), ("⧉", "Duplicate layer", self.window.layer_duplicate),
            ("▲", "Move layer up", lambda: self.window.layer_move(1)), ("▼", "Move layer down", lambda: self.window.layer_move(-1)),
            ("⤓", "Merge down", self.window.layer_merge_down), ("🗑", "Delete layer", self.window.layer_delete),
        ]:
            b = QToolButton(); b.setText(text); b.setToolTip(tip); b.clicked.connect(slot)
            btns.addWidget(b)
        lay.addLayout(btns)

    def set_document(self, doc):
        self._doc = doc
        self.refresh()

    def refresh(self):
        self._updating = True
        self.list.clear()
        doc = self._doc
        if doc is None:
            self._updating = False
            return
        for i in range(len(doc.layers) - 1, -1, -1):
            layer = doc.layers[i]
            it = QListWidgetItem(layer_thumbnail(layer), layer.name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, i)
            extra = []
            if layer.locked: extra.append("🔒")
            if layer.blend_mode != "Normal": extra.append(layer.blend_mode)
            if layer.opacity < 1: extra.append(f"{int(layer.opacity*100)}%")
            if extra:
                it.setToolTip("  ".join(extra))
            self.list.addItem(it)
        row = len(doc.layers) - 1 - doc.active_index
        self.list.setCurrentRow(row)
        al = doc.active_layer
        if al:
            self.blend.setCurrentText(al.blend_mode)
            self.opacity.setValue(int(round(al.opacity * 100)))
            self.opacity_lbl.setText(f"{int(round(al.opacity * 100))}%")
        self._updating = False

    def _item_changed(self, item):
        if self._updating or self._doc is None:
            return
        i = item.data(Qt.ItemDataRole.UserRole)
        layer = self._doc.layers[i]
        vis = item.checkState() == Qt.CheckState.Checked
        if vis != layer.visible:
            self.window.history().push("Layer visibility", detach=None)
            layer.visible = vis
            self._doc.changed(structure=True)
        elif item.text() != layer.name:
            self.window.history().push("Rename layer", detach=None)
            layer.name = item.text()
            self._doc.changed(structure=True)

    def _row_changed(self, row):
        if self._updating or self._doc is None or row < 0:
            return
        it = self.list.item(row)
        i = it.data(Qt.ItemDataRole.UserRole)
        if i != self._doc.active_index:
            self._doc.active_index = i
            al = self._doc.active_layer
            self._updating = True
            self.blend.setCurrentText(al.blend_mode)
            self.opacity.setValue(int(round(al.opacity * 100)))
            self.opacity_lbl.setText(f"{int(round(al.opacity * 100))}%")
            self._updating = False
            self.window.sync_title()

    def _blend_changed(self, text):
        if self._updating or self._doc is None or self._doc.active_layer is None:
            return
        if self._doc.active_layer.blend_mode != text:
            self.window.history().push("Blend mode", detach=None)
            self._doc.active_layer.blend_mode = text
            self._doc.changed(structure=True)

    def _opacity_changed(self, v):
        self.opacity_lbl.setText(f"{v}%")
        if self._updating or self._doc is None or self._doc.active_layer is None:
            return
        if not self.opacity.isSliderDown():
            self._opacity_commit()
        else:
            self._doc.active_layer.opacity = v / 100.0
            self._doc.changed()

    def _opacity_commit(self):
        if self._doc is None or self._doc.active_layer is None or self._updating:
            return
        v = self.opacity.value() / 100.0
        self.window.history().push("Layer opacity", detach=None)
        self._doc.active_layer.opacity = v
        self._doc.changed(structure=True)

    def _context(self, pos):
        m = QMenu(self)
        for label, slot in [("New Layer", self.window.layer_new), ("Duplicate", self.window.layer_duplicate),
                            ("Delete", self.window.layer_delete), ("Merge Down", self.window.layer_merge_down),
                            ("Properties…", self.window.layer_properties)]:
            m.addAction(label, slot)
        m.exec(self.list.mapToGlobal(pos))


# ---------------------------------------------------------------------------
class HistoryPanel(QListWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self._history = None
        self.itemClicked.connect(self._jump)

    def set_history(self, h):
        self._history = h
        self.refresh()

    def refresh(self):
        self.clear()
        if self._history is None:
            return
        undo, redo = self._history.labels()
        self.addItem("Open")
        for l in undo:
            self.addItem(l)
        self.setCurrentRow(self.count() - 1)
        for l in redo:
            it = QListWidgetItem(l)
            it.setForeground(QColor(130, 130, 130))
            self.addItem(it)

    def _jump(self, item):
        if self._history is None:
            return
        target = self.row(item)
        current = len(self._history.undo_stack)
        while current > target and self._history.can_undo():
            self._history.undo(); current -= 1
        while current < target and self._history.can_redo():
            self._history.redo(); current += 1
