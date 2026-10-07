"""VectorBench main window."""
from __future__ import annotations

import json
import os
import sys

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette, QTransform
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QInputDialog, QLabel, QMainWindow, QMessageBox,
                               QScrollArea, QTabWidget)

from . import __version__, fileio, pathops
from .canvas import VectorCanvas
from .context import VContext
from .dialogs import DocumentSetupDialog, ExportDialog, NewDocumentDialog, OffsetPathDialog, TransformDialog
from .model import Document, GroupItem, ImageItem, Item, PathItem, TextItem, item_from_dict
from .panels import AlignPanel, LayersPanel, OptionsBar, PropertiesPanel, SwatchesPanel, ToolBox
from .svg import export_svg, import_svg
from .tools import ALL_TOOLS


def apply_dark_palette(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    base, mid, text = QColor(45, 45, 45), QColor(58, 58, 58), QColor(225, 225, 225)
    for role, c in [(QPalette.ColorRole.Window, base), (QPalette.ColorRole.WindowText, text), (QPalette.ColorRole.Base, QColor(35, 35, 35)),
                    (QPalette.ColorRole.AlternateBase, mid), (QPalette.ColorRole.ToolTipBase, QColor(60, 60, 60)), (QPalette.ColorRole.ToolTipText, text),
                    (QPalette.ColorRole.Text, text), (QPalette.ColorRole.Button, mid), (QPalette.ColorRole.ButtonText, text),
                    (QPalette.ColorRole.Highlight, QColor(42, 130, 218)), (QPalette.ColorRole.HighlightedText, QColor(255, 255, 255)),
                    (QPalette.ColorRole.PlaceholderText, QColor(140, 140, 140))]:
        pal.setColor(role, c)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(120, 120, 120))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(120, 120, 120))
    app.setPalette(pal)
    app.setStyleSheet("QToolTip { color: #eee; background: #333; border: 1px solid #555; }")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"VectorBench {__version__}")
        self.resize(1500, 950)
        self.settings = QSettings("AAAGraphicCo", "VectorBench")
        self.ctx = VContext(); self.ctx.window = self
        for cls in ALL_TOOLS:
            self.ctx.register_tool(cls(self.ctx))
        self.ctx.statusMessage.connect(self._status)
        self.ctx.toolChanged.connect(self._tool_changed)
        self.last_transform: tuple[QTransform, QPointF] | None = None
        self._clip: list[dict] = []

        self.tabs = QTabWidget(); self.tabs.setTabsClosable(True); self.tabs.setMovable(True); self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab); self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)

        self.toolbox = ToolBox(self.ctx); self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.toolbox)
        self.options_bar = OptionsBar(self.ctx); self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.options_bar)

        self.properties = PropertiesPanel(self)
        scroll = QScrollArea(); scroll.setWidget(self.properties); scroll.setWidgetResizable(True)
        self.layers_panel = LayersPanel(self)
        self.swatches = SwatchesPanel(self)
        self.align_panel = AlignPanel(self)
        d_props = self._dock("Properties", scroll, "properties")
        d_layers = self._dock("Layers", self.layers_panel, "layers")
        d_sw = self._dock("Swatches", self.swatches, "swatches")
        d_al = self._dock("Align & Pathfinder", self.align_panel, "align")
        self.tabifyDockWidget(d_layers, d_al)
        self.tabifyDockWidget(d_layers, d_sw)
        d_layers.raise_()
        self.resizeDocks([d_props, d_layers], [520, 380], Qt.Orientation.Vertical)

        self.status_pos = QLabel(""); self.status_zoom = QLabel(""); self.status_doc = QLabel("")
        for w in (self.status_doc, self.status_pos, self.status_zoom):
            self.statusBar().addPermanentWidget(w)
        self._build_menus()
        self.ctx.set_tool("select")
        self.setAcceptDrops(True)
        geo = self.settings.value("geometry"); state = self.settings.value("state")
        if geo: self.restoreGeometry(geo)
        if state: self.restoreState(state)
        self._update_actions()

    def _dock(self, title, widget, name):
        d = QDockWidget(title, self); d.setObjectName(name); d.setWidget(widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)
        return d

    # ------------------------------------------------------------------ menus
    def _act(self, menu, text, slot, shortcut=None, checkable=False, checked=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable); a.setChecked(checked); a.triggered.connect(slot); menu.addAction(a)
        return a

    def _build_menus(self):
        mb = self.menuBar(); self.doc_actions = []; D = self.doc_actions
        f = mb.addMenu("&File")
        self._act(f, "&New…", self.file_new, "Ctrl+N"); self._act(f, "&Open…", self.file_open, "Ctrl+O")
        D.append(self._act(f, "&Place Image…", self.file_place, "Ctrl+Shift+P"))
        f.addSeparator()
        D += [self._act(f, "&Save", self.file_save, "Ctrl+S"), self._act(f, "Save &As…", self.file_save_as, "Ctrl+Shift+S"),
              self._act(f, "&Export…", self.file_export, "Ctrl+E"), self._act(f, "Export Selection as SVG…", self.file_export_selection)]
        f.addSeparator()
        D.append(self._act(f, "Document Setup…", self.document_setup, "Ctrl+Alt+P"))
        f.addSeparator()
        D.append(self._act(f, "&Close", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")); self._act(f, "E&xit", self.close, "Ctrl+Q")

        e = mb.addMenu("&Edit")
        self.undo_act = self._act(e, "&Undo", self.edit_undo, "Ctrl+Z"); self.redo_act = self._act(e, "&Redo", self.edit_redo, "Ctrl+Shift+Z")
        self.redo_act.setShortcuts([QKeySequence("Ctrl+Shift+Z"), QKeySequence("Ctrl+Y")])
        e.addSeparator()
        D += [self._act(e, "Cu&t", self.edit_cut, "Ctrl+X"), self._act(e, "&Copy", self.edit_copy, "Ctrl+C"), self._act(e, "&Paste", self.edit_paste, "Ctrl+V"),
              self._act(e, "Paste in &Place", lambda: self.edit_paste(in_place=True), "Ctrl+Shift+V"), self._act(e, "Paste in Front", lambda: self.edit_paste(in_place=True, front=True), "Ctrl+F"),
              self._act(e, "Paste in Back", lambda: self.edit_paste(in_place=True, back=True), "Ctrl+B"),
              self._act(e, "Dup&licate", self.edit_duplicate, "Ctrl+Shift+D"), self._act(e, "Cl&ear", self.edit_delete, "Delete")]

        o = mb.addMenu("&Object")
        tr = o.addMenu("&Transform")
        D += [self._act(tr, "Transform &Again", self.transform_again, "Ctrl+D"), self._act(tr, "&Move…", lambda: self.transform_dialog("move"), "Ctrl+Shift+M"),
              self._act(tr, "&Rotate…", lambda: self.transform_dialog("rotate")), self._act(tr, "&Scale…", lambda: self.transform_dialog("scale")),
              self._act(tr, "Re&flect…", lambda: self.transform_dialog("reflect")), self._act(tr, "S&hear…", lambda: self.transform_dialog("shear")),
              self._act(tr, "Reset Transform", self.transform_reset)]
        ar = o.addMenu("&Arrange")
        D += [self._act(ar, "Bring to &Front", lambda: self.reorder("front"), "Ctrl+Shift+]"), self._act(ar, "Bring F&orward", lambda: self.reorder("forward"), "Ctrl+]"),
              self._act(ar, "Send &Backward", lambda: self.reorder("backward"), "Ctrl+["), self._act(ar, "Send to Bac&k", lambda: self.reorder("back"), "Ctrl+Shift+[")]
        o.addSeparator()
        D += [self._act(o, "&Group", self.group, "Ctrl+G"), self._act(o, "&Ungroup", self.ungroup, "Ctrl+Shift+G"),
              self._act(o, "&Lock Selection", self.lock_selection, "Ctrl+2"), self._act(o, "Unlock &All", self.unlock_all, "Ctrl+Alt+2"),
              self._act(o, "&Hide Selection", self.hide_selection, "Ctrl+3"), self._act(o, "Show A&ll", self.show_all, "Ctrl+Alt+3")]
        o.addSeparator()
        D.append(self._act(o, "&Expand (shapes/text to paths)", self.expand, "Ctrl+Shift+O"))
        pa = o.addMenu("&Path")
        D += [self._act(pa, "&Join", self.path_join, "Ctrl+J"), self._act(pa, "&Average Anchors", self.path_average, "Ctrl+Alt+J"),
              self._act(pa, "&Outline Stroke", self.path_outline_stroke), self._act(pa, "O&ffset Path…", self.path_offset),
              self._act(pa, "&Simplify", self.path_simplify), self._act(pa, "Add Anchor &Points", self.path_add_anchors),
              self._act(pa, "&Reverse Direction", self.path_reverse)]
        pf = o.addMenu("Path&finder")
        for label, op in [("&Unite", "unite"), ("&Minus Front", "minus_front"), ("&Intersect", "intersect"), ("&Exclude", "exclude"), ("Minus &Back", "minus_back")]:
            D.append(self._act(pf, label, lambda _=False, op=op: self.pathfinder(op)))
        al = o.addMenu("A&lign")
        for label, m in [("Left", "left"), ("Horizontal Centre", "hcenter"), ("Right", "right"), ("Top", "top"), ("Vertical Centre", "vcenter"), ("Bottom", "bottom")]:
            D.append(self._act(al, label, lambda _=False, m=m: self.align(m)))
        al.addSeparator()
        for label, m in [("Distribute Horizontal Spacing", "hspace"), ("Distribute Vertical Spacing", "vspace")]:
            D.append(self._act(al, label, lambda _=False, m=m: self.distribute(m)))

        t = mb.addMenu("&Type")
        D += [self._act(t, "Create &Outlines", self.expand, "Ctrl+Shift+O")]
        for label, attr, val in [("Align Left", "align", "Left"), ("Align Centre", "align", "Center"), ("Align Right", "align", "Right")]:
            D.append(self._act(t, label, lambda _=False, a=attr, v=val: self._set_text_attr(a, v)))
        D.append(self._act(t, "Bigger", lambda: self._bump_text(2), "Ctrl+Shift+."))
        D.append(self._act(t, "Smaller", lambda: self._bump_text(-2), "Ctrl+Shift+,"))

        s = mb.addMenu("&Select")
        D += [self._act(s, "&All", self.select_all, "Ctrl+A"), self._act(s, "&Deselect", self.select_none, "Ctrl+Shift+A"),
              self._act(s, "&Inverse", self.select_inverse, "Ctrl+Alt+I"), self._act(s, "Same &Fill Colour", lambda: self.select_same("fill")),
              self._act(s, "Same &Stroke Colour", lambda: self.select_same("stroke")), self._act(s, "All &Text Objects", lambda: self.select_kind(TextItem)),
              self._act(s, "All on Active &Layer", self.select_layer)]

        v = mb.addMenu("&View")
        D += [self._act(v, "Zoom &In", lambda: self.current_view().zoom_in(), "Ctrl+="), self._act(v, "Zoom &Out", lambda: self.current_view().zoom_out(), "Ctrl+-"),
              self._act(v, "&Fit Artboard", lambda: self.current_view().fit_in_view(), "Ctrl+0"), self._act(v, "&Actual Size", lambda: self.current_view().set_zoom(1.0), "Ctrl+1"),
              self._act(v, "Zoom to &Selection", self.zoom_selection, "Ctrl+Alt+0")]
        v.addSeparator()
        self.view_toggles = {}
        for label, attr, key in [("&Outline Mode", "outline_mode", "Ctrl+Y"), ("Show &Rulers", "show_rulers", "Ctrl+R"), ("Show &Grid", "show_grid", "Ctrl+'"),
                                 ("Snap to Gr&id", "snap_grid", "Ctrl+Shift+'"), ("Snap to &Point", "snap_points", "Ctrl+Alt+'"),
                                 ("Show G&uides", "show_guides", "Ctrl+;"), ("&Lock Guides", "lock_guides", "Ctrl+Alt+;")]:
            a = self._act(v, label, lambda checked, at=attr: self._toggle_view(at, checked), key, checkable=True)
            self.view_toggles[attr] = a; D.append(a)
        D.append(self._act(v, "Clear Guides", self.clear_guides))
        v.addSeparator()
        for d in self.findChildren(QDockWidget):
            v.addAction(d.toggleViewAction())

        h = mb.addMenu("&Help")
        self._act(h, "Keyboard Shortcuts", self.help_shortcuts, "F1"); self._act(h, "About VectorBench", self.help_about)
        a = QAction(self); a.setShortcut(QKeySequence("Escape")); a.triggered.connect(self._escape); self.addAction(a)
        a = QAction(self); a.setShortcut(QKeySequence("X")); a.triggered.connect(self.properties._swap); self.addAction(a)

    # ---------------------------------------------------------------- helpers
    def current_view(self) -> VectorCanvas | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, VectorCanvas) else None

    def doc(self):
        v = self.current_view(); return v.doc if v else None

    def sel(self) -> list[Item]:
        v = self.current_view(); return list(v.selection) if v else []

    def _status(self, msg):
        self.statusBar().showMessage(msg, 4000)

    def _escape(self):
        v = self.current_view()
        if v is None:
            return
        if v.is_editing_text():
            v.end_text_edit()
        elif self.ctx.tool and self.ctx.tool.name == "pen" and self.ctx.tool.item is not None:
            self.ctx.tool.finish(v)
        else:
            v.clear_selection()

    def _update_actions(self):
        has = self.current_view() is not None
        for a in self.doc_actions:
            a.setEnabled(has)
        v = self.current_view()
        h = v.history if v else None
        self.undo_act.setEnabled(bool(h and h.can_undo())); self.redo_act.setEnabled(bool(h and h.can_redo()))
        if h:
            u, r = h.labels()
            self.undo_act.setText(f"&Undo {u[-1]}" if u else "&Undo"); self.redo_act.setText(f"&Redo {r[0]}" if r else "&Redo")
        if v:
            for attr, a in self.view_toggles.items():
                a.blockSignals(True); a.setChecked(getattr(v, attr)); a.blockSignals(False)

    def _toggle_view(self, attr, checked):
        v = self.current_view()
        if v:
            setattr(v, attr, checked); v.update()

    def sync_title(self):
        d = self.doc()
        if d is None:
            self.setWindowTitle(f"VectorBench {__version__}"); self.status_doc.setText(""); return
        star = "*" if d.dirty else ""
        self.setWindowTitle(f"{d.name}{star} — VectorBench")
        self.tabs.setTabText(self.tabs.currentIndex(), f"{d.name}{star}")
        self.status_doc.setText(f"{d.width:g} × {d.height:g} px   layer: {d.active_layer.name}   ")

    def _tool_changed(self, tool):
        v = self.current_view()
        if v:
            v.tool_changed()
        self.properties.refresh()

    def _tab_changed(self, i):
        v = self.current_view()
        self.layers_panel.set_document(v.doc if v else None)
        if v:
            v.tool_changed(); self.status_zoom.setText(f"{v.zoom * 100:.0f}%")
        self.properties.refresh(); self.sync_title(); self._update_actions()

    # -------------------------------------------------------------- documents
    def add_document(self, doc: Document) -> VectorCanvas:
        view = VectorCanvas(doc, self.ctx)
        view.zoomChanged.connect(lambda z: self.status_zoom.setText(f"{z * 100:.0f}%"))
        view.cursorMoved.connect(lambda x, y: self.status_pos.setText(f"x {x:.1f}, y {y:.1f}   "))
        view.selectionChanged.connect(lambda v=view: self._selection_changed(v))
        doc.listeners.append(lambda structure, v=view: self._doc_changed(v, structure))
        view.history.listeners.append(lambda v=view: self._history_changed(v))
        i = self.tabs.addTab(view, doc.name); self.tabs.setCurrentIndex(i)
        view.fit_in_view(); view.setFocus()
        return view

    def _selection_changed(self, view):
        if view is self.current_view():
            if view.selection and not isinstance(view.selection[0], (GroupItem, ImageItem)):
                self.ctx.style = view.selection[0].style.copy()
            self.properties.refresh(); self.layers_panel.sync_selection(); self.sync_title()

    def _doc_changed(self, view, structure):
        if view is self.current_view():
            if structure:
                self.layers_panel.refresh()
            self.sync_title(); self._update_actions()

    def _history_changed(self, view):
        if view is self.current_view():
            self._update_actions()

    def close_tab(self, i):
        w = self.tabs.widget(i)
        if not isinstance(w, VectorCanvas):
            return
        if w.doc.dirty:
            self.tabs.setCurrentIndex(i)
            r = QMessageBox.question(self, "Close", f"Save changes to {w.doc.name}?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return False
            if r == QMessageBox.StandardButton.Save and not self.file_save():
                return False
        self.tabs.removeTab(i); w.deleteLater(); self._tab_changed(self.tabs.currentIndex())
        return True

    def closeEvent(self, ev):
        while self.tabs.count():
            if self.close_tab(0) is False:
                ev.ignore(); return
        self.settings.setValue("geometry", self.saveGeometry()); self.settings.setValue("state", self.saveState())
        ev.accept()

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        for url in ev.mimeData().urls():
            self.open_or_place(url.toLocalFile(), None)

    # ------------------------------------------------------------------ file
    def file_new(self):
        last = (float(self.settings.value("new/w", 1920)), float(self.settings.value("new/h", 1080)))
        dlg = NewDocumentDialog(self, last)
        if dlg.exec():
            w, h = dlg.values(); self.settings.setValue("new/w", w); self.settings.setValue("new/h", h)
            self.new_document(w, h)

    def new_document(self, w=1920, h=1080):
        doc = Document(w, h); doc.dirty = False
        return self.add_document(doc)

    def file_open(self):
        start = self.settings.value("lastdir", os.path.expanduser("~"))
        for p in QFileDialog.getOpenFileNames(self, "Open", start, fileio.OPEN_FILTER)[0]:
            self.open_path(p)

    def open_path(self, path):
        if not path or not os.path.isfile(path):
            return None
        try:
            doc = fileio.open_document(path)
        except Exception as e:
            QMessageBox.critical(self, "Open failed", f"Could not open {path}:\n{e}"); return None
        self.settings.setValue("lastdir", os.path.dirname(path)); self._status(f"Opened {path}")
        return self.add_document(doc)

    def open_or_place(self, path, pos: QPointF | None):
        ext = os.path.splitext(path)[1].lower()
        if self.doc() is not None and ext in fileio.IMAGE_EXTS:
            it = fileio.place_image(path)
            if it:
                v = self.current_view(); v.history.push("Place")
                if pos is not None:
                    it.transform = QTransform.fromTranslate(pos.x(), pos.y())
                v.doc.add_item(it); v.doc.changed(structure=True); v.set_selection([it])
            return
        self.open_path(path)

    def file_place(self):
        start = self.settings.value("lastdir", os.path.expanduser("~"))
        p, _ = QFileDialog.getOpenFileName(self, "Place Image", start, "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff *.svg)")
        if p:
            if p.lower().endswith(".svg"):
                with open(p, "r", encoding="utf-8") as f:
                    items, _, _ = import_svg(f.read())
                v = self.current_view(); v.history.push("Place SVG")
                g = GroupItem(items); g.name = os.path.basename(p)
                v.doc.add_item(g); v.doc.changed(structure=True); v.set_selection([g])
            else:
                self.open_or_place(p, None)

    def file_save(self) -> bool:
        d = self.doc()
        if d is None:
            return False
        if d.path and d.path.lower().endswith(fileio.NATIVE_EXT):
            fileio.save_native(d, d.path); self.sync_title(); self._status(f"Saved {d.path}"); return True
        return self.file_save_as()

    def file_save_as(self) -> bool:
        d = self.doc()
        if d is None:
            return False
        start = os.path.join(self.settings.value("lastdir", os.path.expanduser("~")), os.path.splitext(d.name)[0] + fileio.NATIVE_EXT)
        path, _ = QFileDialog.getSaveFileName(self, "Save As", start, f"VectorBench document (*{fileio.NATIVE_EXT});;SVG (*.svg)")
        if not path:
            return False
        if not os.path.splitext(path)[1]:
            path += fileio.NATIVE_EXT
        try:
            if path.lower().endswith(".svg"):
                fileio.export(d, path); d.path = path; d.dirty = False
            else:
                fileio.save_native(d, path)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e)); return False
        self.settings.setValue("lastdir", os.path.dirname(path)); self.sync_title(); self._status(f"Saved {path}")
        return True

    def file_export(self):
        d = self.doc()
        if d is None:
            return
        start = os.path.join(self.settings.value("lastdir", os.path.expanduser("~")), os.path.splitext(d.name)[0] + ".svg")
        path, _ = QFileDialog.getSaveFileName(self, "Export", start, fileio.EXPORT_FILTER)
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        if not ext:
            path += ".svg"; ext = ".svg"
        scale, quality, transparent = 1.0, 92, False
        if ext not in (".svg", ".pdf"):
            dlg = ExportDialog(ext, self)
            if not dlg.exec():
                return
            scale, quality, transparent = dlg.scale.value() / 100.0, dlg.quality.value(), dlg.transparent.isChecked()
        try:
            fileio.export(d, path, scale, quality, transparent)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e)); return
        self.settings.setValue("lastdir", os.path.dirname(path)); self._status(f"Exported {path}")

    def file_export_selection(self):
        d, items = self.doc(), self.sel()
        if d is None or not items:
            self._status("Nothing selected."); return
        path, _ = QFileDialog.getSaveFileName(self, "Export Selection", self.settings.value("lastdir", os.path.expanduser("~")), "SVG (*.svg)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(export_svg(d, items))
            self._status(f"Exported {path}")

    def document_setup(self):
        d = self.doc()
        dlg = DocumentSetupDialog(d, self)
        if dlg.exec():
            self.current_view().history.push("Document Setup")
            d.width, d.height, d.grid_size = dlg.w.value(), dlg.h.value(), dlg.grid.value()
            d.changed(structure=True)

    # ------------------------------------------------------------------ edit
    def edit_undo(self):
        v = self.current_view()
        if v and v.history.can_undo():
            v.end_text_edit(); self._status(f"Undo {v.history.undo()}"); v.selectionChanged.emit()

    def edit_redo(self):
        v = self.current_view()
        if v and v.history.can_redo():
            self._status(f"Redo {v.history.redo()}"); v.selectionChanged.emit()

    def edit_copy(self):
        items = self.sel()
        if not items:
            return
        self._clip = [i.to_dict() for i in items]
        QApplication.clipboard().setText(export_svg(self.doc(), items))
        self._status(f"Copied {len(items)} object(s)")

    def edit_cut(self):
        self.edit_copy(); self.edit_delete()

    def edit_paste(self, in_place=False, front=False, back=False):
        v = self.current_view()
        if v is None:
            return
        items: list[Item] = []
        if self._clip:
            items = [item_from_dict(d, new_ids=True) for d in self._clip]
        else:
            txt = QApplication.clipboard().text()
            if txt.lstrip().startswith("<"):
                try:
                    items, _, _ = import_svg(txt)
                except Exception:
                    items = []
            if not items:
                img = QApplication.clipboard().image()
                if not img.isNull():
                    items = [ImageItem(img)]
        if not items:
            self._status("Nothing to paste."); return
        v.history.push("Paste")
        if not in_place:
            for it in items:
                it.transform = it.transform * QTransform.fromTranslate(20, 20)
        layer = v.doc.active_layer
        index = None
        if (front or back) and v.selection:
            ref = v.selection[-1 if front else 0]
            layer = v.doc.layer_of(ref) or layer
            index = layer.items.index(ref) + (1 if front else 0)
        for it in items:
            v.doc.add_item(it, layer, index)
            if index is not None:
                index += 1
        v.doc.changed(structure=True); v.set_selection(items)

    def edit_duplicate(self):
        v = self.current_view()
        if v is None or not v.selection:
            return
        v.history.push("Duplicate")
        clones = []
        for it in v.selection:
            c = it.copy(); c.transform = c.transform * QTransform.fromTranslate(10, 10)
            v.doc.add_item(c, v.doc.layer_of(it)); clones.append(c)
        v.doc.changed(structure=True); v.set_selection(clones)

    def edit_delete(self):
        v = self.current_view()
        if v is None or not v.selection:
            return
        v.history.push("Delete")
        for it in v.selection:
            v.doc.remove_item(it)
        v.set_selection([]); v.doc.changed(structure=True)

    # ---------------------------------------------------------------- object
    def _with_selection(self, label):
        v = self.current_view()
        if v is None or not v.selection:
            self._status("Select something first."); return None
        v.history.push(label)
        return v

    def transform_again(self):
        v = self.current_view()
        if v is None or not v.selection or self.last_transform is None:
            return
        v.history.push("Transform Again")
        t, c = self.last_transform
        pathops.transform_about(v.selection, t, pathops.selection_bounds(v.selection).center() if c is None else c)
        v.doc.changed(); v.selectionChanged.emit()

    def transform_dialog(self, kind):
        v = self.current_view()
        if v is None or not v.selection:
            self._status("Select something first."); return
        snap = [(it, QTransform(it.transform)) for it in v.selection]
        center = pathops.selection_bounds(v.selection).center()
        state = {"t": QTransform()}

        def build(p):
            t = QTransform()
            if kind == "move": t = QTransform.fromTranslate(p["dx"], p["dy"])
            elif kind == "rotate": t.rotate(p["angle"])
            elif kind == "scale": t = QTransform.fromScale(p["sx"] / 100.0, p["sy"] / 100.0)
            elif kind == "reflect": t = QTransform.fromScale(-1, 1) if p["axis"] == 0 else QTransform.fromScale(1, -1)
            elif kind == "shear":
                import math
                s = math.tan(math.radians(p["angle"]))
                t.shear(s if p["axis"] == 0 else 0, s if p["axis"] == 1 else 0)
            return t

        def apply(p, preview=False):
            t = build(p); state["t"] = t
            for it, t0 in snap:
                it.transform = t0
            pathops.transform_about([it for it, _ in snap], t, center)
            v.doc.changed()

        v.history.begin(kind.capitalize())
        dlg = TransformDialog(kind, apply, self)
        if dlg.exec():
            if dlg.copy.isChecked():
                for it, t0 in snap:
                    it.transform = t0
                clones = []
                for it, _ in snap:
                    c = it.copy(); v.doc.add_item(c, v.doc.layer_of(it)); clones.append(c)
                pathops.transform_about(clones, state["t"], center)
                v.set_selection(clones)
            v.history.commit(); self.last_transform = (state["t"], None)
            v.doc.changed(structure=True); v.selectionChanged.emit()
        else:
            v.history.cancel()

    def transform_reset(self):
        v = self._with_selection("Reset Transform")
        if v is None:
            return
        for it in v.selection:
            if isinstance(it, PathItem):
                it.subpaths = PathItem.from_painter_path(it.doc_path()).subpaths
            it.transform = QTransform()
        v.doc.changed(); v.selectionChanged.emit()

    def reorder(self, how):
        v = self._with_selection("Arrange")
        if v is None:
            return
        for it in (v.selection if how in ("forward", "front") else list(v.selection)):
            layer = v.doc.layer_of(it)
            if layer is None:
                continue
            i = layer.items.index(it); layer.items.pop(i)
            j = {"front": len(layer.items), "back": 0, "forward": min(len(layer.items), i + 1), "backward": max(0, i - 1)}[how]
            layer.items.insert(j, it)
        v.doc.changed(structure=True)

    def group(self):
        v = self._with_selection("Group")
        if v is None or len(v.selection) < 1:
            return
        ordered = sorted(v.selection, key=lambda i: v.doc.z_order(i))
        layer = v.doc.layer_of(ordered[-1]); idx = layer.items.index(ordered[-1])
        for it in ordered:
            v.doc.remove_item(it)
        g = GroupItem(ordered); g.name = f"Group"
        layer.items.insert(min(idx, len(layer.items)), g)
        v.doc.changed(structure=True); v.set_selection([g])

    def ungroup(self):
        v = self._with_selection("Ungroup")
        if v is None:
            return
        out = []
        for it in list(v.selection):
            if not isinstance(it, GroupItem):
                out.append(it); continue
            layer = v.doc.layer_of(it); idx = layer.items.index(it); layer.items.pop(idx)
            for c in it.children:
                c.transform = c.transform * it.transform
                if it.style.opacity < 1:
                    c.style.opacity *= it.style.opacity
                layer.items.insert(idx, c); idx += 1; out.append(c)
        v.doc.changed(structure=True); v.set_selection(out)

    def lock_selection(self):
        v = self._with_selection("Lock")
        if v is None: return
        for it in v.selection: it.locked = True
        v.set_selection([]); v.doc.changed(structure=True)

    def unlock_all(self):
        v = self.current_view()
        if v is None: return
        v.history.push("Unlock All")
        for it in v.doc.all_items(include_hidden=True, top_level_only=False): it.locked = False
        v.doc.changed(structure=True)

    def hide_selection(self):
        v = self._with_selection("Hide")
        if v is None: return
        for it in v.selection: it.visible = False
        v.set_selection([]); v.doc.changed(structure=True)

    def show_all(self):
        v = self.current_view()
        if v is None: return
        v.history.push("Show All")
        for it in v.doc.all_items(include_hidden=True, top_level_only=False): it.visible = True
        v.doc.changed(structure=True)

    def expand(self):
        v = self._with_selection("Expand")
        if v is None: return
        out = []
        for it in v.selection:
            if isinstance(it, (PathItem, ImageItem, GroupItem)):
                out.append(it); continue
            p = it.to_path_item()
            self._replace(v, it, p); out.append(p)
        v.doc.changed(structure=True); v.set_selection(out)

    def _replace(self, v, old: Item, new: Item):
        layer = v.doc.layer_of(old)
        if layer is None:
            v.doc.add_item(new); return
        i = layer.items.index(old); layer.items[i] = new

    # ------------------------------------------------------------------ path
    def _paths(self, v):
        return [i for i in v.selection if isinstance(i, PathItem)]

    def path_join(self):
        v = self._with_selection("Join")
        if v is None: return
        ps = self._paths(v)
        if len(ps) == 2:
            j = pathops.join_paths(ps[0], ps[1])
            self._replace(v, ps[0], j); v.doc.remove_item(ps[1]); v.set_selection([j])
        elif len(ps) == 1:
            j = pathops.join_paths(ps[0], None); self._replace(v, ps[0], j); v.set_selection([j])
        else:
            v.history.pop_last(); self._status("Select one or two open paths."); return
        v.doc.changed(structure=True)

    def path_average(self):
        v = self.current_view()
        if v is None or not v.selected_nodes:
            self._status("Select anchors with the Direct Selection tool first."); return
        v.history.push("Average")
        for iid in {k[0] for k in v.selected_nodes}:
            it = v.doc.find(iid)
            nodes = [it.subpaths[si].nodes[ni] for (i2, si, ni) in v.selected_nodes if i2 == iid]
            pathops.average_points(nodes)
        v.doc.changed()

    def path_outline_stroke(self):
        v = self._with_selection("Outline Stroke")
        if v is None: return
        out = []
        for it in v.selection:
            o = pathops.outline_stroke(it)
            if o is not None:
                self._replace(v, it, o); out.append(o)
        v.doc.changed(structure=True); v.set_selection(out)

    def path_offset(self):
        v = self.current_view()
        if v is None or not v.selection: return
        dlg = OffsetPathDialog(self)
        if dlg.exec():
            v.history.push("Offset Path")
            out = []
            for it in v.selection:
                if isinstance(it, (ImageItem, GroupItem)): continue
                o = pathops.offset_path(it, dlg.offset.value(), dlg.join.currentText())
                layer = v.doc.layer_of(it); layer.items.insert(layer.items.index(it) + 1, o); out.append(o)
            v.doc.changed(structure=True); v.set_selection(out)

    def path_simplify(self):
        v = self._with_selection("Simplify")
        if v is None: return
        out = []
        for it in v.selection:
            if isinstance(it, (ImageItem, GroupItem)): continue
            s = pathops.simplify(it); self._replace(v, it, s); out.append(s)
        v.doc.changed(structure=True); v.set_selection(out)

    def path_add_anchors(self):
        v = self._with_selection("Add Anchor Points")
        if v is None: return
        out = []
        for it in v.selection:
            if isinstance(it, PathItem):
                a = pathops.add_anchor_points(it); self._replace(v, it, a); out.append(a)
        v.doc.changed(structure=True); v.set_selection(out)

    def path_reverse(self):
        v = self._with_selection("Reverse")
        if v is None: return
        out = []
        for it in v.selection:
            if isinstance(it, PathItem):
                r = pathops.reverse_path(it); self._replace(v, it, r); out.append(r)
        v.doc.changed(structure=True); v.set_selection(out)

    def pathfinder(self, op):
        v = self.current_view()
        if v is None or len(v.selection) < 2:
            self._status("Select two or more objects."); return
        ordered = sorted([i for i in v.selection if not isinstance(i, (ImageItem,))], key=lambda i: v.doc.z_order(i))
        res = pathops.pathfinder(ordered, op)
        if res is None:
            return
        v.history.push("Pathfinder")
        layer = v.doc.layer_of(ordered[-1]); idx = layer.items.index(ordered[-1])
        for it in ordered:
            v.doc.remove_item(it)
        layer.items.insert(min(idx, len(layer.items)), res)
        v.doc.changed(structure=True); v.set_selection([res])

    def align(self, mode):
        v = self.current_view()
        if v is None or not v.selection: return
        target = v.doc.rect if self.align_panel.to_artboard.isChecked() or len(v.selection) == 1 else None
        v.history.push("Align"); pathops.align(v.selection, mode, target); v.doc.changed(); v.selectionChanged.emit()

    def distribute(self, mode):
        v = self.current_view()
        if v is None or len(v.selection) < 3:
            self._status("Select three or more objects."); return
        v.history.push("Distribute"); pathops.distribute(v.selection, mode); v.doc.changed(); v.selectionChanged.emit()

    # ------------------------------------------------------------------ type
    def _set_text_attr(self, attr, val):
        self.properties._apply_text(attr, val); self.properties.refresh()

    def _bump_text(self, d):
        v = self.current_view()
        texts = [i for i in self.sel() if isinstance(i, TextItem)]
        if not texts: return
        v.history.push("Text Size")
        for t in texts: t.size = max(1.0, t.size + d)
        v.doc.changed(); v.selectionChanged.emit()

    # ---------------------------------------------------------------- select
    def select_all(self):
        v = self.current_view()
        if v: v.set_selection([i for i in v.doc.all_items() if not i.locked])

    def select_none(self):
        v = self.current_view()
        if v: v.clear_selection()

    def select_inverse(self):
        v = self.current_view()
        if v:
            cur = set(id(i) for i in v.selection)
            v.set_selection([i for i in v.doc.all_items() if id(i) not in cur and not i.locked])

    def select_same(self, which):
        v = self.current_view()
        if v is None or not v.selection: return
        ref = getattr(v.selection[0].style, which)
        def same(p):
            return p.kind == ref.kind and (p.kind != "solid" or p.color == ref.color)
        v.set_selection([i for i in v.doc.all_items() if not i.locked and same(getattr(i.style, which))])

    def select_kind(self, cls):
        v = self.current_view()
        if v: v.set_selection([i for i in v.doc.all_items() if isinstance(i, cls) and not i.locked])

    def select_layer(self):
        v = self.current_view()
        if v: v.set_selection([i for i in v.doc.active_layer.items if i.visible and not i.locked])

    def zoom_selection(self):
        v = self.current_view()
        if v and v.selection: v.zoom_to_rect(v.selection_bounds())

    # ---------------------------------------------------------------- layers
    def layer_new(self):
        v = self.current_view()
        if v is None: return
        v.history.push("New Layer"); v.doc.add_layer(); v.doc.changed(structure=True)

    def layer_delete(self):
        v = self.current_view()
        if v is None or len(v.doc.layers) <= 1:
            self._status("Can't delete the only layer."); return
        v.history.push("Delete Layer"); v.doc.layers.pop(v.doc.active_layer_index); v.doc.active_layer_index = max(0, v.doc.active_layer_index - 1)
        v.doc.changed(structure=True)

    def clear_guides(self):
        v = self.current_view()
        if v: v.history.push("Clear Guides"); v.doc.guides.clear(); v.doc.changed()

    # ------------------------------------------------------------------ help
    def help_shortcuts(self):
        lines = ["<b>Tools</b>"] + [f"{t.shortcut or '-'} &nbsp; {t.label}" for t in self.ctx.tools.values()]
        lines += ["", "<b>Canvas</b>", "Space+drag / middle-drag: pan · Ctrl+wheel: zoom", "Ctrl+0 fit · Ctrl+1 100% · Ctrl+Y outline mode",
                  "Drag from a ruler to make a guide", "X swaps fill/stroke · Esc deselects / ends pen path",
                  "", "<b>Selection</b>", "Shift+click adds · Alt+drag duplicates · Shift constrains · Alt scales from centre",
                  "Corner handles scale; just outside a corner rotates", "Arrow keys nudge (Shift = 10 px)"]
        QMessageBox.information(self, "Keyboard Shortcuts", "<br>".join(lines))

    def help_about(self):
        QMessageBox.about(self, "About VectorBench", f"<b>VectorBench {__version__}</b><br>A native, offline vector editor.<br>"
                          "Built with Python and PySide6 (Qt).<br><br>AAA Graphic Co — Russellville, AR")


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("VectorBench"); app.setOrganizationName("AAAGraphicCo")
    apply_dark_palette(app)
    win = MainWindow(); win.show()
    opened = sum(1 for p in argv[1:] if win.open_path(p))
    if not opened:
        win.new_document(1920, 1080)
    return app.exec()
