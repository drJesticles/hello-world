"""Snapshot-based undo/redo with copy-on-write layer arrays (see document.py docstring)."""
from __future__ import annotations

from .document import Document, DocState


class History:
    def __init__(self, doc: Document, limit: int = 40):
        self.doc = doc
        self.limit = limit
        self.undo_stack: list[tuple[str, DocState]] = []   # (label, state BEFORE the action)
        self.redo_stack: list[tuple[str, DocState]] = []   # (label, state AFTER the action)
        self.listeners: list = []

    def _notify(self):
        for cb in list(self.listeners):
            cb()

    def push(self, label: str, detach: str | list[int] | None = "active") -> None:
        """Record the current state as the 'before' of an action about to happen.

        detach: "active" / "all" / list of layer indices whose arrays will be mutated in place.
        """
        self.undo_stack.append((label, self.doc.snapshot()))
        self.redo_stack.clear()
        if len(self.undo_stack) > self.limit:
            del self.undo_stack[0]
        if detach == "active":
            if self.doc.active_layer is not None:
                self.doc.active_layer.detach()
        elif detach == "all":
            for l in self.doc.layers:
                l.detach()
        elif isinstance(detach, (list, tuple)):
            for i in detach:
                if 0 <= i < len(self.doc.layers):
                    self.doc.layers[i].detach()
        self._notify()

    def pop_last(self) -> None:
        """Discard the most recent push (e.g. a cancelled tool action)."""
        if self.undo_stack:
            self.undo_stack.pop()
            self._notify()

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo(self) -> str | None:
        if not self.undo_stack:
            return None
        label, before = self.undo_stack.pop()
        self.redo_stack.append((label, self.doc.snapshot()))
        self.doc.restore(before)
        self.doc.changed(structure=True)
        self._notify()
        return label

    def redo(self) -> str | None:
        if not self.redo_stack:
            return None
        label, after = self.redo_stack.pop()
        self.undo_stack.append((label, self.doc.snapshot()))
        self.doc.restore(after)
        self.doc.changed(structure=True)
        self._notify()
        return label

    def labels(self) -> tuple[list[str], list[str]]:
        return [l for l, _ in self.undo_stack], [l for l, _ in reversed(self.redo_stack)]

    def clear(self) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._notify()
