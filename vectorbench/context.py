"""Shared editor state for VectorBench: current appearance, active tool, tool options."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor

from .model import Paint, Style


class VContext(QObject):
    styleChanged = Signal()
    toolChanged = Signal(object)
    optionChanged = Signal(str, str)
    statusMessage = Signal(str)

    def __init__(self):
        super().__init__()
        self.style = Style()                 # appearance applied to newly drawn objects
        self.style.fill = Paint.solid(QColor(230, 230, 230))
        self.style.stroke = Paint.solid(QColor(0, 0, 0))
        self.fill_active = True              # which swatch the colour picker edits (fill or stroke)
        self.tools: dict[str, object] = {}
        self.tool = None
        self.options: dict[str, dict] = {}
        self.window = None
        self.text_font = "Arial"
        self.text_size = 24.0

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

    def opt(self, tool, key, default=None):
        return self.options.get(tool, {}).get(key, default)

    def set_opt(self, tool, key, value):
        self.options.setdefault(tool, {})[key] = value
        self.optionChanged.emit(tool, key)

    def status(self, msg):
        self.statusMessage.emit(msg)
