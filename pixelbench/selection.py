"""Selection helpers: shape masks, flood/magic-wand regions, feathering, outline for marching ants."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def rect_mask(w: int, h: int, x0: float, y0: float, x1: float, y1: float, antialias: bool = False) -> np.ndarray:
    m = np.zeros((h, w), dtype=np.uint8)
    xa, xb = sorted((int(round(x0)), int(round(x1))))
    ya, yb = sorted((int(round(y0)), int(round(y1))))
    xa, ya = max(0, xa), max(0, ya)
    xb, yb = min(w, xb), min(h, yb)
    if xb > xa and yb > ya:
        m[ya:yb, xa:xb] = 255
    return m


def ellipse_mask(w: int, h: int, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    img = Image.new("L", (w, h), 0)
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    ImageDraw.Draw(img).ellipse([xa, ya, xb - 1, yb - 1], fill=255)
    return np.asarray(img).copy()


def polygon_mask(w: int, h: int, points: list[tuple[float, float]]) -> np.ndarray:
    img = Image.new("L", (w, h), 0)
    if len(points) >= 3:
        ImageDraw.Draw(img).polygon([(float(x), float(y)) for x, y in points], fill=255)
    return np.asarray(img).copy()


def combine(existing: np.ndarray | None, new: np.ndarray, mode: str) -> np.ndarray | None:
    """mode: 'new' | 'add' | 'subtract' | 'intersect'."""
    if mode == "new" or existing is None:
        if mode == "subtract" and existing is None:
            return (255 - new).astype(np.uint8)
        if mode == "intersect" and existing is None:
            return new
        return new
    if mode == "add":
        return np.maximum(existing, new)
    if mode == "subtract":
        return np.minimum(existing, 255 - new).astype(np.uint8)
    if mode == "intersect":
        return np.minimum(existing, new)
    return new


def feather(mask: np.ndarray, radius: float) -> np.ndarray:
    if radius <= 0:
        return mask
    return np.asarray(Image.fromarray(mask, "L").filter(ImageFilter.GaussianBlur(radius))).copy()


def grow(mask: np.ndarray, px: int) -> np.ndarray:
    if px == 0:
        return mask
    from scipy import ndimage
    b = mask > 127
    if px > 0:
        b = ndimage.binary_dilation(b, iterations=int(px))
    else:
        b = ndimage.binary_erosion(b, iterations=int(-px))
    return (b.astype(np.uint8) * 255)


def color_region(pixels: np.ndarray, x: int, y: int, tolerance: int = 32, contiguous: bool = True,
                 sample_alpha: bool = True) -> np.ndarray:
    """Pixels similar to the colour at (x, y). Returns uint8 mask (255 inside)."""
    h, w = pixels.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        return np.zeros((h, w), dtype=np.uint8)
    seed = pixels[y, x].astype(np.int32)
    ch = 4 if sample_alpha else 3
    diff = np.abs(pixels[..., :ch].astype(np.int32) - seed[:ch]).max(axis=-1)
    within = diff <= int(tolerance)
    if contiguous:
        from scipy import ndimage
        labels, _ = ndimage.label(within)
        within = labels == labels[y, x]
    return within.astype(np.uint8) * 255


def outline_points(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (ys, xs) of mask pixels on the boundary of the selected region."""
    b = mask > 127
    if not b.any():
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    inner = b.copy()
    inner[1:, :] &= b[:-1, :]
    inner[:-1, :] &= b[1:, :]
    inner[:, 1:] &= b[:, :-1]
    inner[:, :-1] &= b[:, 1:]
    edge = b & ~inner
    ys, xs = np.nonzero(edge)
    return ys, xs
