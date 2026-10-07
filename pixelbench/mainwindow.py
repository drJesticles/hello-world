"""Main window: menus, document tabs, dock panels, and all the Image/Layer/Select/Filter commands."""
from __future__ import annotations

import os
import sys

import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPalette
from PySide6.QtWidgets import (QApplication, QColorDialog, QDockWidget, QFileDialog, QInputDialog, QLabel, QMainWindow,
                               QMessageBox, QTabWidget)

from . import __version__, fileio, filters
from .canvas import CanvasView
from .context import EditorContext
from .dialogs import (CanvasSizeDialog, ExportDialog, FilterDialog, LayerPropertiesDialog, NewDocumentDialog,
                      ResizeImageDialog, TransformDialog)
from .document import Document
from .filters import apply_with_mask
from .panels import ColorSwatches, HistoryPanel, LayersPanel, OptionsBar, ToolBox
from .qtutil import array_from_qimage, qimage_from_array
from .selection import feather, grow
from .tools import ALL_TOOLS


def apply_dark_palette(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    base, mid, text = QColor(45, 45, 45), QColor(58, 58, 58), QColor(225, 225, 225)
    pal.setColor(QPalette.ColorRole.Window, base)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    pal.setColor(QPalette.ColorRole.AlternateBase, mid)
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(60, 60, 60))
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, mid)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.BrightText, QColor(255, 90, 90))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(140, 140, 140))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(120, 120, 120))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(120, 120, 120))
    app.setPalette(pal)
    app.setStyleSheet("QToolTip { color: #eee; background: #333; border: 1px solid #555; }")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"PixelBench {__version__}")
        self.resize(1400, 900)
        self.settings = QSettings("AAAGraphicCo", "PixelBench")

        self.ctx = EditorContext()
        self.ctx.window = self
        for cls in ALL_TOOLS:
            self.ctx.register_tool(cls(self.ctx))
        self.ctx.statusMessage.connect(self._status)
        self.ctx.toolChanged.connect(self._tool_changed)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)

        self.toolbox = ToolBox(self.ctx)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.toolbox)
        self.swatches = ColorSwatches(self.ctx)
        self.toolbox.addSeparator()
        self.toolbox.addWidget(self.swatches)
        self.options_bar = OptionsBar(self.ctx)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.options_bar)

        self.layers_panel = LayersPanel(self)
        self.history_panel = HistoryPanel(self)
        self._dock("Layers", self.layers_panel, "layers")
        self._dock("History", self.history_panel, "history")

        self.status_pos = QLabel("")
        self.status_zoom = QLabel("")
        self.status_doc = QLabel("")
        self.statusBar().addPermanentWidget(self.status_doc)
        self.statusBar().addPermanentWidget(self.status_pos)
        self.statusBar().addPermanentWidget(self.status_zoom)

        self._build_menus()
        self.ctx.set_tool("brush")
        self.setAcceptDrops(True)
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        state = self.settings.value("state")
        if state:
            self.restoreState(state)
        self._update_actions()

    def _dock(self, title, widget, name):
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        d.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)
        return d

    # ------------------------------------------------------------------ menus
    def _act(self, menu, text, slot, shortcut=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _build_menus(self):
        mb = self.menuBar()
        self.doc_actions = []   # actions that need an open document

        f = mb.addMenu("&File")
        self._act(f, "&New…", self.file_new, "Ctrl+N")
        self._act(f, "&Open…", self.file_open, "Ctrl+O")
        self._act(f, "Open from Clipboard", self.file_open_clipboard, "Ctrl+Alt+V")
        f.addSeparator()
        self.doc_actions += [
            self._act(f, "&Save", self.file_save, "Ctrl+S"),
            self._act(f, "Save &As…", self.file_save_as, "Ctrl+Shift+S"),
            self._act(f, "&Export As…", self.file_export, "Ctrl+Shift+E"),
        ]
        f.addSeparator()
        self.doc_actions.append(self._act(f, "&Close", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W"))
        self._act(f, "E&xit", self.close, "Ctrl+Q")

        e = mb.addMenu("&Edit")
        self.undo_act = self._act(e, "&Undo", self.edit_undo, "Ctrl+Z")
        self.redo_act = self._act(e, "&Redo", self.edit_redo, "Ctrl+Y")
        self.redo_act.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        e.addSeparator()
        self.doc_actions += [
            self._act(e, "Cu&t", self.edit_cut, "Ctrl+X"),
            self._act(e, "&Copy", self.edit_copy, "Ctrl+C"),
            self._act(e, "Copy &Merged", self.edit_copy_merged, "Ctrl+Shift+C"),
            self._act(e, "&Paste", self.edit_paste, "Ctrl+V"),
            self._act(e, "C&lear", self.edit_clear, "Delete"),
        ]
        e.addSeparator()
        self.doc_actions += [
            self._act(e, "Fill with &Foreground", lambda: self.edit_fill(self.ctx.fg), "Alt+Backspace"),
            self._act(e, "Fill with &Background", lambda: self.edit_fill(self.ctx.bg), "Ctrl+Backspace"),
            self._act(e, "Transform Layer…", self.layer_transform, "Ctrl+T"),
        ]

        im = mb.addMenu("&Image")
        self.doc_actions += [
            self._act(im, "Image &Size…", self.image_resize, "Ctrl+Alt+I"),
            self._act(im, "&Canvas Size…", self.image_canvas_size, "Ctrl+Alt+C"),
            self._act(im, "Crop to Selection", self.image_crop_selection, "Ctrl+Shift+X"),
            self._act(im, "Trim Transparent Edges", self.image_trim),
        ]
        im.addSeparator()
        self.doc_actions += [
            self._act(im, "Rotate 90° Clockwise", lambda: self.image_rotate(1)),
            self._act(im, "Rotate 90° Counter-clockwise", lambda: self.image_rotate(3)),
            self._act(im, "Rotate 180°", lambda: self.image_rotate(2)),
            self._act(im, "Flip Horizontal", lambda: self.image_flip(True)),
            self._act(im, "Flip Vertical", lambda: self.image_flip(False)),
        ]
        im.addSeparator()
        self.doc_actions.append(self._act(im, "Flatten Image", self.layer_flatten))

        l = mb.addMenu("&Layer")
        self.doc_actions += [
            self._act(l, "&New Layer", self.layer_new, "Ctrl+Shift+N"),
            self._act(l, "&Duplicate Layer", self.layer_duplicate, "Ctrl+J"),
            self._act(l, "Delete Layer", self.layer_delete),
            self._act(l, "Layer &Properties…", self.layer_properties),
        ]
        l.addSeparator()
        self.doc_actions += [
            self._act(l, "Move Layer Up", lambda: self.layer_move(1), "Ctrl+]"),
            self._act(l, "Move Layer Down", lambda: self.layer_move(-1), "Ctrl+["),
            self._act(l, "Merge &Down", self.layer_merge_down, "Ctrl+E"),
            self._act(l, "Merge Visible", self.layer_merge_visible),
            self._act(l, "&Flatten Image", self.layer_flatten),
        ]
        l.addSeparator()
        self.doc_actions += [
            self._act(l, "Flip Layer Horizontal", lambda: self.layer_flip(True)),
            self._act(l, "Flip Layer Vertical", lambda: self.layer_flip(False)),
            self._act(l, "Layer to Image Size (clear outside)", self.layer_clear_outside_selection),
        ]

        s = mb.addMenu("&Select")
        self.doc_actions += [
            self._act(s, "&All", self.select_all, "Ctrl+A"),
            self._act(s, "&Deselect", self.select_none, "Ctrl+D"),
            self._act(s, "&Invert", self.select_invert, "Ctrl+Shift+I"),
            self._act(s, "Select Layer Contents", self.select_layer_alpha),
        ]
        s.addSeparator()
        self.doc_actions += [
            self._act(s, "&Feather…", self.select_feather),
            self._act(s, "&Grow…", lambda: self.select_grow(1)),
            self._act(s, "&Shrink…", lambda: self.select_grow(-1)),
        ]

        fm = mb.addMenu("Fi&lter")
        submenus = {}
        for group, name, fn, params in filters.FILTERS:
            if group not in submenus:
                submenus[group] = fm.addMenu(group)
            self.doc_actions.append(self._act(submenus[group], name, lambda _=False, n=name, f=fn, p=params: self.run_filter(n, f, p)))
        fm.addSeparator()
        self.last_filter_act = self._act(fm, "Repeat Last Filter", self.repeat_filter, "Ctrl+F")
        self._last_filter = None

        v = mb.addMenu("&View")
        self.doc_actions += [
            self._act(v, "Zoom &In", lambda: self.current_view().zoom_in(), "Ctrl+="),
            self._act(v, "Zoom &Out", lambda: self.current_view().zoom_out(), "Ctrl+-"),
            self._act(v, "&Fit on Screen", lambda: self.current_view().fit_in_view(), "Ctrl+0"),
            self._act(v, "&Actual Pixels", lambda: self.current_view().set_zoom(1.0), "Ctrl+1"),
            self._act(v, "200%", lambda: self.current_view().set_zoom(2.0), "Ctrl+2"),
        ]
        v.addSeparator()
        for d in self.findChildren(QDockWidget):
            v.addAction(d.toggleViewAction())

        h = mb.addMenu("&Help")
        self._act(h, "Keyboard Shortcuts", self.help_shortcuts, "F1")
        self._act(h, "About PixelBench", self.help_about)

        # colour shortcuts live on the window so they work regardless of focus
        for key, slot in (("X", self.ctx.swap_colors), ("D", self.ctx.reset_colors)):
            a = QAction(self); a.setShortcut(QKeySequence(key)); a.triggered.connect(slot); self.addAction(a)
        a = QAction(self); a.setShortcut(QKeySequence("Escape")); a.triggered.connect(self._escape); self.addAction(a)

    # --------------------------------------------------------------- helpers
    def current_view(self) -> CanvasView | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, CanvasView) else None

    def doc(self) -> Document | None:
        v = self.current_view()
        return v.doc if v else None

    def history(self):
        v = self.current_view()
        return v.history if v else None

    def _status(self, msg):
        self.statusBar().showMessage(msg, 4000)

    def _escape(self):
        v = self.current_view()
        if v and v.doc.selection is not None:
            self.select_none()

    def _update_actions(self):
        has = self.current_view() is not None
        for a in self.doc_actions:
            a.setEnabled(has)
        h = self.history()
        self.undo_act.setEnabled(bool(h and h.can_undo()))
        self.redo_act.setEnabled(bool(h and h.can_redo()))
        if h:
            u, r = h.labels()
            self.undo_act.setText(f"&Undo {u[-1]}" if u else "&Undo")
            self.redo_act.setText(f"&Redo {r[0]}" if r else "&Redo")
        self.last_filter_act.setEnabled(has and self._last_filter is not None)

    def sync_title(self):
        d = self.doc()
        if d is None:
            self.setWindowTitle(f"PixelBench {__version__}")
            self.status_doc.setText("")
            return
        star = "*" if d.dirty else ""
        self.setWindowTitle(f"{d.name}{star} — PixelBench")
        i = self.tabs.currentIndex()
        self.tabs.setTabText(i, f"{d.name}{star}")
        layer = d.active_layer.name if d.active_layer else "-"
        self.status_doc.setText(f"{d.width} × {d.height} px   layer: {layer}   ")

    def _tool_changed(self, tool):
        v = self.current_view()
        if v:
            v.tool_changed()

    def _tab_changed(self, i):
        v = self.current_view()
        self.layers_panel.set_document(v.doc if v else None)
        self.history_panel.set_history(v.history if v else None)
        if v:
            v.tool_changed()
            self.status_zoom.setText(f"{v.zoom * 100:.0f}%")
        self.sync_title()
        self._update_actions()

    # ------------------------------------------------------------ documents
    def add_document(self, doc: Document) -> CanvasView:
        view = CanvasView(doc, self.ctx)
        view.zoomChanged.connect(lambda z: self.status_zoom.setText(f"{z * 100:.0f}%"))
        view.cursorMoved.connect(lambda x, y: self.status_pos.setText(f"x {int(x)}, y {int(y)}   "))
        doc.listeners.append(lambda structure, v=view: self._doc_changed(v, structure))
        view.history.listeners.append(lambda v=view: self._history_changed(v))
        i = self.tabs.addTab(view, doc.name)
        self.tabs.setCurrentIndex(i)
        view.fit_in_view()
        view.setFocus()
        return view

    def _doc_changed(self, view, structure):
        if view is self.current_view():
            if structure:
                self.layers_panel.refresh()
            self.sync_title()
            self._update_actions()

    def _history_changed(self, view):
        if view is self.current_view():
            self.history_panel.refresh()
            self._update_actions()

    def close_tab(self, i):
        w = self.tabs.widget(i)
        if not isinstance(w, CanvasView):
            return
        if w.doc.dirty:
            self.tabs.setCurrentIndex(i)
            r = QMessageBox.question(self, "Close", f"Save changes to {w.doc.name}?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return False
            if r == QMessageBox.StandardButton.Save and not self.file_save():
                return False
        self.tabs.removeTab(i)
        w.deleteLater()
        self._tab_changed(self.tabs.currentIndex())
        return True

    def closeEvent(self, ev):
        while self.tabs.count():
            if self.close_tab(0) is False:
                ev.ignore()
                return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        ev.accept()

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        for url in ev.mimeData().urls():
            self.open_path(url.toLocalFile())

    # ------------------------------------------------------------------ file
    def file_new(self):
        last = (int(self.settings.value("new/w", 1920)), int(self.settings.value("new/h", 1080)))
        dlg = NewDocumentDialog(self, last)
        if not dlg.exec():
            return
        w, h, bg = dlg.values()
        self.settings.setValue("new/w", w); self.settings.setValue("new/h", h)
        self.new_document(w, h, bg)

    def new_document(self, w, h, bg="White"):
        doc = Document(w, h)
        fill = {"White": (255, 255, 255, 255), "Black": (0, 0, 0, 255), "Transparent": (0, 0, 0, 0),
                "Background colour": (self.ctx.bg.red(), self.ctx.bg.green(), self.ctx.bg.blue(), 255)}[bg]
        doc.add_layer("Background", fill=fill)
        doc.dirty = False
        return self.add_document(doc)

    def file_open(self):
        start = self.settings.value("lastdir", os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(self, "Open", start, fileio.OPEN_FILTER)
        for p in paths:
            self.open_path(p)

    def open_path(self, path):
        if not path or not os.path.isfile(path):
            return None
        try:
            doc = fileio.open_document(path)
        except Exception as e:
            QMessageBox.critical(self, "Open failed", f"Could not open {path}:\n{e}")
            return None
        self.settings.setValue("lastdir", os.path.dirname(path))
        self._status(f"Opened {path}")
        return self.add_document(doc)

    def file_open_clipboard(self):
        img = QApplication.clipboard().image()
        if img.isNull():
            self._status("Clipboard has no image.")
            return
        arr = array_from_qimage(img)
        doc = Document(arr.shape[1], arr.shape[0])
        doc.add_layer("Background", pixels=arr)
        doc.dirty = False
        self.add_document(doc)

    def file_save(self) -> bool:
        d = self.doc()
        if d is None:
            return False
        if d.path and d.path.lower().endswith(fileio.NATIVE_EXT):
            fileio.save_native(d, d.path)
            self.sync_title(); self._status(f"Saved {d.path}")
            return True
        return self.file_save_as()

    def file_save_as(self) -> bool:
        d = self.doc()
        if d is None:
            return False
        start = os.path.join(self.settings.value("lastdir", os.path.expanduser("~")),
                             os.path.splitext(d.name)[0] + fileio.NATIVE_EXT)
        path, _ = QFileDialog.getSaveFileName(self, "Save As", start,
                                              f"PixelBench document (*{fileio.NATIVE_EXT});;" + fileio.EXPORT_FILTER)
        if not path:
            return False
        if not os.path.splitext(path)[1]:
            path += fileio.NATIVE_EXT
        try:
            if path.lower().endswith(fileio.NATIVE_EXT):
                fileio.save_native(d, path)
            else:
                if len(d.layers) > 1:
                    QMessageBox.information(self, "Flattened export",
                                            "That format has no layers, so a flattened copy is written. "
                                            f"Use {fileio.NATIVE_EXT} to keep layers.")
                fileio.export_image(d, path)
                d.path, d.dirty = path, False
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return False
        self.settings.setValue("lastdir", os.path.dirname(path))
        self.sync_title(); self._status(f"Saved {path}")
        return True

    def file_export(self):
        d = self.doc()
        if d is None:
            return
        start = os.path.join(self.settings.value("lastdir", os.path.expanduser("~")), os.path.splitext(d.name)[0] + ".png")
        path, _ = QFileDialog.getSaveFileName(self, "Export As", start, fileio.EXPORT_FILTER)
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".png"
        quality = 92
        if path.lower().endswith((".jpg", ".jpeg", ".webp")):
            dlg = ExportDialog(self)
            if not dlg.exec():
                return
            quality = dlg.quality.value()
        try:
            fileio.export_image(d, path, quality=quality)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return
        self.settings.setValue("lastdir", os.path.dirname(path))
        self._status(f"Exported {path}")

    # ------------------------------------------------------------------ edit
    def edit_undo(self):
        h = self.history()
        if h and h.can_undo():
            self._status(f"Undo {h.undo()}")

    def edit_redo(self):
        h = self.history()
        if h and h.can_redo():
            self._status(f"Redo {h.redo()}")

    def _selected_pixels(self, merged=False):
        d = self.doc()
        src = d.composite() if merged else d.active_layer.pixels
        x0, y0, x1, y1 = d.selection_bounds()
        if x1 <= x0 or y1 <= y0:
            return None
        crop = src[y0:y1, x0:x1].copy()
        if d.selection is not None:
            m = d.selection[y0:y1, x0:x1].astype(np.float32) / 255.0
            crop[..., 3] = (crop[..., 3] * m + 0.5).astype(np.uint8)
        return crop

    def edit_copy(self, merged=False):
        d = self.doc()
        if d is None or d.active_layer is None:
            return
        crop = self._selected_pixels(merged)
        if crop is None:
            return
        self._clip_array = crop
        QApplication.clipboard().setImage(qimage_from_array(np.ascontiguousarray(crop)).copy())
        self._status("Copied")

    def edit_copy_merged(self):
        self.edit_copy(merged=True)

    def edit_cut(self):
        self.edit_copy()
        self.edit_clear()

    def edit_clear(self):
        d = self.doc()
        if d is None or d.active_layer is None or d.active_layer.locked:
            return
        self.history().push("Clear", detach=None)
        layer = d.active_layer
        px = layer.pixels.copy()
        if d.selection is None:
            px[...] = 0
        else:
            m = d.selection.astype(np.float32) / 255.0
            px[..., 3] = (px[..., 3] * (1 - m) + 0.5).astype(np.uint8)
        layer.pixels = px
        d.changed()

    def edit_paste(self):
        img = QApplication.clipboard().image()
        if img.isNull():
            self._status("Clipboard has no image.")
            return
        arr = array_from_qimage(img)
        d = self.doc()
        if d is None:
            doc = Document(arr.shape[1], arr.shape[0])
            doc.add_layer("Background", pixels=arr)
            self.add_document(doc)
            return
        self.history().push("Paste", detach=None)
        layer = d.add_layer("Pasted")
        h, w = arr.shape[:2]
        # centre on the selection (if any) or the canvas
        x0, y0, x1, y1 = d.selection_bounds()
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        from .document import _paste
        _paste(layer.pixels, arr, cx - w // 2, cy - h // 2)
        d.changed(structure=True)
        self.ctx.set_tool("move")

    def edit_fill(self, color: QColor):
        d = self.doc()
        if d is None or d.active_layer is None or d.active_layer.locked:
            return
        paint = np.zeros((d.height, d.width, 4), dtype=np.uint8)
        paint[...] = (color.red(), color.green(), color.blue(), 255)
        from .tools import stamp_paint
        stamp_paint(self.current_view(), paint, "Fill")

    # ----------------------------------------------------------------- image
    def image_resize(self):
        d = self.doc()
        dlg = ResizeImageDialog(d.width, d.height, self)
        if dlg.exec():
            w, h, method = dlg.values()
            self.history().push("Image Size", detach=None)
            d.resize_image(w, h, method)
            d.changed(structure=True)
            self.current_view().fit_in_view()

    def image_canvas_size(self):
        d = self.doc()
        dlg = CanvasSizeDialog(d.width, d.height, self)
        if dlg.exec():
            w, h, anchor = dlg.values()
            self.history().push("Canvas Size", detach=None)
            d.resize_canvas(w, h, anchor)
            d.changed(structure=True)
            self.current_view().fit_in_view()

    def image_crop_selection(self):
        d = self.doc()
        if d.selection is None:
            self._status("Nothing selected.")
            return
        x0, y0, x1, y1 = d.selection_bounds()
        if x1 <= x0 or y1 <= y0:
            return
        self.history().push("Crop", detach=None)
        d.crop(x0, y0, x1, y1)
        d.changed(structure=True)
        self.current_view().fit_in_view()

    def image_trim(self):
        d = self.doc()
        self.history().push("Trim", detach=None)
        d.trim()
        d.changed(structure=True)
        self.current_view().fit_in_view()

    def image_rotate(self, k):
        d = self.doc()
        self.history().push("Rotate", detach=None)
        d.rotate(k)
        d.changed(structure=True)
        self.current_view().fit_in_view()

    def image_flip(self, horizontal):
        d = self.doc()
        self.history().push("Flip", detach=None)
        d.flip(horizontal)
        d.changed(structure=True)

    # ----------------------------------------------------------------- layer
    def layer_new(self):
        d = self.doc()
        if d is None:
            return
        self.history().push("New Layer", detach=None)
        d.add_layer()
        d.changed(structure=True)

    def layer_duplicate(self):
        d = self.doc()
        if d is None or not d.layers:
            return
        self.history().push("Duplicate Layer", detach=None)
        d.duplicate_layer(d.active_index)
        d.changed(structure=True)

    def layer_delete(self):
        d = self.doc()
        if d is None or len(d.layers) <= 1:
            self._status("Can't delete the only layer.")
            return
        self.history().push("Delete Layer", detach=None)
        d.remove_layer(d.active_index)
        d.changed(structure=True)

    def layer_move(self, delta):
        d = self.doc()
        if d is None:
            return
        i = d.active_index
        j = i + delta
        if not (0 <= j < len(d.layers)):
            return
        self.history().push("Move Layer", detach=None)
        d.move_layer(i, j)
        d.changed(structure=True)

    def layer_merge_down(self):
        d = self.doc()
        if d is None or d.active_index <= 0:
            return
        self.history().push("Merge Down", detach=None)
        d.merge_down(d.active_index)
        d.changed(structure=True)

    def layer_merge_visible(self):
        d = self.doc()
        if d is None:
            return
        self.history().push("Merge Visible", detach=None)
        d.merge_visible()
        d.changed(structure=True)

    def layer_flatten(self):
        d = self.doc()
        if d is None:
            return
        self.history().push("Flatten", detach=None)
        d.flatten()
        d.changed(structure=True)

    def layer_flip(self, horizontal):
        d = self.doc()
        if d is None or d.active_layer is None:
            return
        self.history().push("Flip Layer", detach=None)
        ax = 1 if horizontal else 0
        d.active_layer.pixels = np.ascontiguousarray(np.flip(d.active_layer.pixels, axis=ax))
        d.changed()

    def layer_clear_outside_selection(self):
        d = self.doc()
        if d is None or d.selection is None:
            self._status("Needs a selection.")
            return
        self.history().push("Clear Outside", detach=None)
        px = d.active_layer.pixels.copy()
        px[..., 3] = (px[..., 3] * (d.selection.astype(np.float32) / 255.0) + 0.5).astype(np.uint8)
        d.active_layer.pixels = px
        d.changed()

    def layer_properties(self):
        d = self.doc()
        if d is None or d.active_layer is None:
            return
        layer = d.active_layer
        dlg = LayerPropertiesDialog(layer, self)
        if dlg.exec():
            self.history().push("Layer Properties", detach=None)
            layer.name = dlg.name.text() or layer.name
            layer.opacity = dlg.opacity.value() / 100.0
            layer.blend_mode = dlg.blend.currentText()
            layer.locked = dlg.locked.isChecked()
            d.changed(structure=True)

    def layer_transform(self):
        d = self.doc()
        if d is None or d.active_layer is None:
            return
        self.history().push("Transform", detach=None)
        dlg = TransformDialog(d, d.active_layer, self)
        if not dlg.exec():
            self.history().pop_last()

    # ---------------------------------------------------------------- select
    def select_all(self):
        d = self.doc()
        self.history().push("Select All", detach=None)
        d.selection = np.full((d.height, d.width), 255, dtype=np.uint8)
        d.changed()

    def select_none(self):
        d = self.doc()
        if d.selection is None:
            return
        self.history().push("Deselect", detach=None)
        d.selection = None
        d.changed()

    def select_invert(self):
        d = self.doc()
        self.history().push("Invert Selection", detach=None)
        d.invert_selection()
        d.changed()

    def select_layer_alpha(self):
        d = self.doc()
        self.history().push("Select Layer Contents", detach=None)
        d.selection = d.active_layer.pixels[..., 3].copy()
        d.changed()

    def select_feather(self):
        d = self.doc()
        if d.selection is None:
            return
        r, ok = QInputDialog.getDouble(self, "Feather", "Radius (px):", 5.0, 0.1, 500.0, 1)
        if ok:
            self.history().push("Feather", detach=None)
            d.selection = feather(d.selection, r)
            d.changed()

    def select_grow(self, sign):
        d = self.doc()
        if d.selection is None:
            return
        n, ok = QInputDialog.getInt(self, "Grow" if sign > 0 else "Shrink", "Pixels:", 2, 1, 500)
        if ok:
            self.history().push("Grow" if sign > 0 else "Shrink", detach=None)
            d.selection = grow(d.selection, sign * n)
            d.changed()

    # ---------------------------------------------------------------- filter
    def run_filter(self, name, fn, params, values=None):
        d = self.doc()
        if d is None or d.active_layer is None or d.active_layer.locked:
            return
        layer = d.active_layer
        original = layer.pixels
        mask = d.selection
        clean = name.rstrip(".")

        def apply(p):
            return apply_with_mask(original, fn(original, **p), mask)

        self.history().push(clean, detach=None)
        if params and values is None:
            dlg = FilterDialog(clean, params, apply, d, layer, self)
            if not dlg.exec():
                self.history().pop_last()
                return
            values = dlg.params()
        else:
            layer.pixels = apply(values or {})
            d.changed()
        self._last_filter = (name, fn, params, values or {})
        self._update_actions()

    def repeat_filter(self):
        if self._last_filter:
            name, fn, params, values = self._last_filter
            self.run_filter(name, fn, params, values)

    # ------------------------------------------------------------------ help
    def help_shortcuts(self):
        lines = ["<b>Tools</b>"]
        for t in self.ctx.tools.values():
            lines.append(f"{t.shortcut or '-'} &nbsp; {t.label}")
        lines += ["", "<b>Canvas</b>", "Space+drag / middle-drag: pan", "Ctrl+wheel: zoom at cursor", "Ctrl+0 fit, Ctrl+1 100%",
                  "[ ] brush size", "X swap colours, D default colours", "Esc deselect / cancel",
                  "", "<b>Selection modifiers</b>", "Shift: add, Alt: subtract, Shift+Alt: intersect, Ctrl: constrain square/circle"]
        QMessageBox.information(self, "Keyboard Shortcuts", "<br>".join(lines))

    def help_about(self):
        QMessageBox.about(self, "About PixelBench",
                          f"<b>PixelBench {__version__}</b><br>A native, layer-based image editor.<br>"
                          "Built with Python, PySide6 (Qt), NumPy, Pillow and SciPy.<br><br>"
                          "AAA Graphic Co — Russellville, AR")


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("PixelBench")
    app.setOrganizationName("AAAGraphicCo")
    apply_dark_palette(app)
    win = MainWindow()
    win.show()
    opened = 0
    for p in argv[1:]:
        if win.open_path(p):
            opened += 1
    if not opened:
        win.new_document(1280, 800, "White")
    return app.exec()
