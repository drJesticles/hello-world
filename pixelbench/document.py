"""Document and Layer model.

Pixel data is stored as numpy uint8 arrays of shape (H, W, 4), straight (non-premultiplied)
RGBA. Every layer is canvas-sized; there are no per-layer offsets, which keeps the maths
simple and lets painting tools draw straight into the buffer through a QImage view.

Copy-on-write rule: snapshots held by the undo history reference layer arrays directly.
Any code that mutates pixels *in place* (the brush, eraser, gradient tool) must first call
``History.push(..., detach=...)`` which swaps the affected layer's array for a fresh copy.
Code that produces a brand-new array and assigns it to ``layer.pixels`` needs no detach.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .blend import BLEND_MODES, composite_layers

Resample = {
    "Nearest": Image.NEAREST,
    "Bilinear": Image.BILINEAR,
    "Bicubic": Image.BICUBIC,
    "Lanczos": Image.LANCZOS,
}


class Layer:
    def __init__(self, width: int, height: int, name: str = "Layer", pixels: np.ndarray | None = None):
        if pixels is None:
            pixels = np.zeros((height, width, 4), dtype=np.uint8)
        assert pixels.shape == (height, width, 4), (pixels.shape, (height, width, 4))
        self.pixels: np.ndarray = np.ascontiguousarray(pixels, dtype=np.uint8)
        self.name = name
        self.visible = True
        self.opacity = 1.0
        self.blend_mode = "Normal"
        self.locked = False

    # -- geometry -----------------------------------------------------------
    @property
    def width(self) -> int:
        return self.pixels.shape[1]

    @property
    def height(self) -> int:
        return self.pixels.shape[0]

    # -- copying ------------------------------------------------------------
    def copy(self, deep: bool = True) -> "Layer":
        l = Layer(self.width, self.height, self.name, self.pixels.copy() if deep else self.pixels)
        l.visible, l.opacity, l.blend_mode, l.locked = self.visible, self.opacity, self.blend_mode, self.locked
        return l

    def detach(self) -> None:
        """Replace the pixel array with a private copy (copy-on-write for undo)."""
        self.pixels = self.pixels.copy()

    # -- conversions --------------------------------------------------------
    def to_pil(self) -> Image.Image:
        return Image.fromarray(self.pixels, "RGBA")

    def set_from_pil(self, img: Image.Image) -> None:
        self.pixels = np.ascontiguousarray(np.asarray(img.convert("RGBA")), dtype=np.uint8)

    def bbox(self) -> tuple[int, int, int, int] | None:
        """Bounding box (x0, y0, x1, y1) of non-transparent pixels, or None if empty."""
        a = self.pixels[..., 3]
        rows = np.any(a, axis=1)
        cols = np.any(a, axis=0)
        if not rows.any():
            return None
        y0, y1 = np.where(rows)[0][[0, -1]]
        x0, x1 = np.where(cols)[0][[0, -1]]
        return int(x0), int(y0), int(x1) + 1, int(y1) + 1


def shift_pixels(pixels: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Translate an RGBA array by (dx, dy), filling exposed areas with transparency."""
    h, w = pixels.shape[:2]
    out = np.zeros_like(pixels)
    sx0, sx1 = max(0, -dx), min(w, w - dx)
    sy0, sy1 = max(0, -dy), min(h, h - dy)
    if sx1 <= sx0 or sy1 <= sy0:
        return out
    out[sy0 + dy:sy1 + dy, sx0 + dx:sx1 + dx] = pixels[sy0:sy1, sx0:sx1]
    return out


@dataclass
class DocState:
    width: int
    height: int
    layers: list = field(default_factory=list)   # list of Layer (shallow: arrays shared)
    active_index: int = 0
    selection: np.ndarray | None = None


class Document:
    _untitled = 0

    def __init__(self, width: int, height: int, name: str | None = None):
        self.width = int(width)
        self.height = int(height)
        self.layers: list[Layer] = []
        self.active_index = 0
        self.selection: np.ndarray | None = None  # (H, W) uint8, 255 = selected
        self.path: str | None = None
        self.dirty = False
        if name is None:
            Document._untitled += 1
            name = f"Untitled-{Document._untitled}"
        self._name = name
        self.version = 0
        self._composite_cache: tuple[int, np.ndarray] | None = None
        self.listeners: list = []

    # -- naming -------------------------------------------------------------
    @property
    def name(self) -> str:
        if self.path:
            return os.path.basename(self.path)
        return self._name

    # -- change notification ------------------------------------------------
    def changed(self, structure: bool = False) -> None:
        """Call after any modification. ``structure`` = layer list/order/properties changed."""
        self.version += 1
        self.dirty = True
        for cb in list(self.listeners):
            cb(structure)

    # -- layers -------------------------------------------------------------
    @property
    def active_layer(self) -> Layer | None:
        if not self.layers:
            return None
        self.active_index = max(0, min(self.active_index, len(self.layers) - 1))
        return self.layers[self.active_index]

    def add_layer(self, name: str | None = None, index: int | None = None,
                  pixels: np.ndarray | None = None, fill: tuple | None = None) -> Layer:
        if name is None:
            name = f"Layer {len(self.layers) + 1}"
        layer = Layer(self.width, self.height, name, pixels)
        if fill is not None:
            layer.pixels[...] = np.array(fill, dtype=np.uint8)
        if index is None:
            index = self.active_index + 1 if self.layers else 0
        index = max(0, min(index, len(self.layers)))
        self.layers.insert(index, layer)
        self.active_index = index
        return layer

    def remove_layer(self, index: int) -> None:
        if 0 <= index < len(self.layers):
            self.layers.pop(index)
            self.active_index = min(self.active_index, len(self.layers) - 1)

    def duplicate_layer(self, index: int) -> Layer:
        src = self.layers[index]
        dup = src.copy(deep=True)
        dup.name = src.name + " copy"
        self.layers.insert(index + 1, dup)
        self.active_index = index + 1
        return dup

    def move_layer(self, index: int, new_index: int) -> None:
        if not (0 <= index < len(self.layers)):
            return
        new_index = max(0, min(new_index, len(self.layers) - 1))
        layer = self.layers.pop(index)
        self.layers.insert(new_index, layer)
        self.active_index = new_index

    def merge_down(self, index: int) -> None:
        if index <= 0 or index >= len(self.layers):
            return
        top, bottom = self.layers[index], self.layers[index - 1]
        merged = composite_layers([bottom, top], self.width, self.height)
        new = Layer(self.width, self.height, bottom.name, merged)
        new.visible = True
        self.layers[index - 1] = new
        self.layers.pop(index)
        self.active_index = index - 1

    def flatten(self) -> None:
        flat = self.composite()
        name = self.layers[0].name if self.layers else "Background"
        self.layers = [Layer(self.width, self.height, name, flat.copy())]
        self.active_index = 0

    def merge_visible(self) -> None:
        vis = [l for l in self.layers if l.visible]
        if len(vis) < 2:
            return
        flat = composite_layers(vis, self.width, self.height)
        keep = [l for l in self.layers if not l.visible]
        idx = self.layers.index(vis[-1])
        new = Layer(self.width, self.height, vis[0].name, flat)
        self.layers = [l for l in self.layers if not l.visible]
        self.layers.insert(min(idx, len(self.layers)), new)
        self.active_index = self.layers.index(new)

    # -- compositing --------------------------------------------------------
    def composite(self) -> np.ndarray:
        if self._composite_cache is not None and self._composite_cache[0] == self.version:
            return self._composite_cache[1]
        img = composite_layers(self.layers, self.width, self.height)
        self._composite_cache = (self.version, img)
        return img

    def composite_pil(self) -> Image.Image:
        return Image.fromarray(self.composite(), "RGBA")

    # -- selection ----------------------------------------------------------
    def select_all(self) -> None:
        self.selection = None

    def select_none(self) -> None:
        self.selection = None

    def has_selection(self) -> bool:
        return self.selection is not None

    def invert_selection(self) -> None:
        if self.selection is None:
            self.selection = np.zeros((self.height, self.width), dtype=np.uint8)
        else:
            self.selection = (255 - self.selection).astype(np.uint8)

    def selection_bounds(self) -> tuple[int, int, int, int]:
        """(x0, y0, x1, y1) of selection, or whole canvas when nothing is selected."""
        if self.selection is None:
            return 0, 0, self.width, self.height
        rows = np.any(self.selection, axis=1)
        cols = np.any(self.selection, axis=0)
        if not rows.any():
            return 0, 0, 0, 0
        y0, y1 = np.where(rows)[0][[0, -1]]
        x0, x1 = np.where(cols)[0][[0, -1]]
        return int(x0), int(y0), int(x1) + 1, int(y1) + 1

    def selection_mask(self) -> np.ndarray:
        """Always returns an (H, W) uint8 mask (all 255 when no selection)."""
        if self.selection is None:
            return np.full((self.height, self.width), 255, dtype=np.uint8)
        return self.selection

    # -- whole-image operations --------------------------------------------
    def _set_size(self, w: int, h: int) -> None:
        self.width, self.height = int(w), int(h)

    def resize_image(self, w: int, h: int, method: str = "Lanczos") -> None:
        res = Resample.get(method, Image.LANCZOS)
        for layer in self.layers:
            layer.set_from_pil(layer.to_pil().resize((w, h), res))
        if self.selection is not None:
            self.selection = np.asarray(Image.fromarray(self.selection, "L").resize((w, h), Image.NEAREST)).copy()
        self._set_size(w, h)

    def resize_canvas(self, w: int, h: int, anchor: tuple[float, float] = (0.5, 0.5)) -> None:
        """Change canvas size without scaling. anchor (ax, ay) in 0..1 positions old content."""
        ox = int(round((w - self.width) * anchor[0]))
        oy = int(round((h - self.height) * anchor[1]))
        for layer in self.layers:
            new = np.zeros((h, w, 4), dtype=np.uint8)
            _paste(new, layer.pixels, ox, oy)
            layer.pixels = new
        if self.selection is not None:
            sel = np.zeros((h, w), dtype=np.uint8)
            _paste(sel, self.selection, ox, oy)
            self.selection = sel
        self._set_size(w, h)

    def crop(self, x0: int, y0: int, x1: int, y1: int) -> None:
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.width, x1), min(self.height, y1)
        if x1 <= x0 or y1 <= y0:
            return
        for layer in self.layers:
            layer.pixels = np.ascontiguousarray(layer.pixels[y0:y1, x0:x1])
        self.selection = None
        self._set_size(x1 - x0, y1 - y0)

    def rotate(self, k: int) -> None:
        """Rotate whole image by k*90 degrees clockwise."""
        k = k % 4
        if k == 0:
            return
        for layer in self.layers:
            layer.pixels = np.ascontiguousarray(np.rot90(layer.pixels, -k))
        if self.selection is not None:
            self.selection = np.ascontiguousarray(np.rot90(self.selection, -k))
        if k % 2 == 1:
            self._set_size(self.height, self.width)

    def flip(self, horizontal: bool) -> None:
        ax = 1 if horizontal else 0
        for layer in self.layers:
            layer.pixels = np.ascontiguousarray(np.flip(layer.pixels, axis=ax))
        if self.selection is not None:
            self.selection = np.ascontiguousarray(np.flip(self.selection, axis=ax))

    def trim(self) -> None:
        """Crop to the union bounding box of all layers' opaque pixels."""
        boxes = [l.bbox() for l in self.layers]
        boxes = [b for b in boxes if b]
        if not boxes:
            return
        x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes); y1 = max(b[3] for b in boxes)
        self.crop(x0, y0, x1, y1)

    # -- undo snapshots -----------------------------------------------------
    def snapshot(self) -> DocState:
        return DocState(self.width, self.height, [l.copy(deep=False) for l in self.layers],
                        self.active_index, self.selection)

    def restore(self, st: DocState) -> None:
        self.width, self.height = st.width, st.height
        self.layers = [l.copy(deep=False) for l in st.layers]
        self.active_index = st.active_index
        self.selection = st.selection


def _paste(dst: np.ndarray, src: np.ndarray, ox: int, oy: int) -> None:
    """Paste src into dst at (ox, oy) with clipping."""
    H, W = dst.shape[:2]
    h, w = src.shape[:2]
    dx0, dy0 = max(0, ox), max(0, oy)
    dx1, dy1 = min(W, ox + w), min(H, oy + h)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    dst[dy0:dy1, dx0:dx1] = src[dy0 - oy:dy1 - oy, dx0 - ox:dx1 - ox]


__all__ = ["Document", "Layer", "DocState", "BLEND_MODES", "shift_pixels", "Resample"]
