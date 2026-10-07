"""Shared editor state: foreground/background colours, active tool, per-tool options."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor


class EditorContext(QObject):
    colorsChanged = Signal()
    toolChanged = Signal(object)
    optionChanged = Signal(str, str)      # tool name, option key
    statusMessage = Signal(str)

    def __init__(self):
        super().__init__()
        self.fg = QColor(0, 0, 0)
        self.bg = QColor(255, 255, 255)
        self.tools: dict[str, object] = {}
        self.tool = None
        self.options: dict[str, dict] = {}
        self.window = None  # MainWindow, set by the window

    # -- colours ------------------------------------------------------------
    def set_fg(self, c: QColor):
        self.fg = QColor(c)
        self.colorsChanged.emit()

    def set_bg(self, c: QColor):
        self.bg = QColor(c)
        self.colorsChanged.emit()

    def swap_colors(self):
        self.fg, self.bg = self.bg, self.fg
        self.colorsChanged.emit()

    def reset_colors(self):
        self.fg, self.bg = QColor(0, 0, 0), QColor(255, 255, 255)
        self.colorsChanged.emit()

    # -- tools --------------------------------------------------------------
    def register_tool(self, tool):
        self.tools[tool.name] = tool
        self.options.setdefault(tool.name, {})
        for spec in tool.option_specs():
            self.options[tool.name].setdefault(spec["key"], spec["default"])

    def set_tool(self, name: str):
        new = self.tools.get(name)
        if new is None or new is self.tool:
            return
        view = self.window.current_view() if self.window else None
        if self.tool is not None:
            self.tool.deactivate(view)
        self.tool = new
        new.activate(view)
        self.toolChanged.emit(new)

    def opt(self, tool_name: str, key: str, default=None):
        return self.options.get(tool_name, {}).get(key, default)

    def set_opt(self, tool_name: str, key: str, value):
        self.options.setdefault(tool_name, {})[key] = value
        self.optionChanged.emit(tool_name, key)

    def status(self, msg: str):
        self.statusMessage.emit(msg)
