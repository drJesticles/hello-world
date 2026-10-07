"""Snapshot undo/redo. Vector documents are small, so a full JSON snapshot per step is cheap."""
from __future__ import annotations

from .model import Document


class History:
    def __init__(self, doc: Document, limit: int = 100):
        self.doc = doc
        self.limit = limit
        self.undo_stack: list[tuple[str, dict]] = []
        self.redo_stack: list[tuple[str, dict]] = []
        self.listeners: list = []
        self._open: tuple[str, dict] | None = None

    def _notify(self):
        for cb in list(self.listeners):
            cb()

    def push(self, label: str):
        """Record the current state as the 'before' of the action about to happen."""
        self.undo_stack.append((label, self.doc.snapshot()))
        self.redo_stack.clear()
        if len(self.undo_stack) > self.limit:
            del self.undo_stack[0]
        self._notify()

    def begin(self, label: str):
        """Start a drag-style action: snapshot once, commit or cancel later."""
        if self._open is None:
            self._open = (label, self.doc.snapshot())

    def commit(self):
        if self._open is not None:
            self.undo_stack.append(self._open)
            self.redo_stack.clear()
            if len(self.undo_stack) > self.limit:
                del self.undo_stack[0]
            self._open = None
            self._notify()

    def cancel(self):
        if self._open is not None:
            self.doc.restore(self._open[1])
            self._open = None
            self.doc.changed(structure=True)

    def pop_last(self):
        if self.undo_stack:
            self.undo_stack.pop()
            self._notify()

    def can_undo(self):
        return bool(self.undo_stack)

    def can_redo(self):
        return bool(self.redo_stack)

    def undo(self):
        if not self.undo_stack:
            return None
        label, before = self.undo_stack.pop()
        self.redo_stack.append((label, self.doc.snapshot()))
        self.doc.restore(before)
        self.doc.changed(structure=True)
        self._notify()
        return label

    def redo(self):
        if not self.redo_stack:
            return None
        label, after = self.redo_stack.pop()
        self.undo_stack.append((label, self.doc.snapshot()))
        self.doc.restore(after)
        self.doc.changed(structure=True)
        self._notify()
        return label

    def labels(self):
        return [l for l, _ in self.undo_stack], [l for l, _ in reversed(self.redo_stack)]
