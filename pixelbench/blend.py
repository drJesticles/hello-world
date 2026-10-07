"""Layer blend modes and compositing.

All math is done on float32 arrays in [0, 1]. Compositing follows the W3C
compositing spec (the same formulas Photoshop/Photopea use for separable modes).
"""
from __future__ import annotations

import numpy as np

BLEND_MODES = [
    "Normal",
    "Multiply",
    "Screen",
    "Overlay",
    "Darken",
    "Lighten",
    "Color Dodge",
    "Color Burn",
    "Hard Light",
    "Soft Light",
    "Difference",
    "Exclusion",
    "Add",
    "Subtract",
]


def _screen(cb, cs):
    return cb + cs - cb * cs


def _hard_light(cb, cs):
    return np.where(cs <= 0.5, 2.0 * cs * cb, _screen(cb, 2.0 * cs - 1.0))


def _soft_light(cb, cs):
    d = np.where(cb <= 0.25, ((16.0 * cb - 12.0) * cb + 4.0) * cb, np.sqrt(cb))
    return np.where(
        cs <= 0.5,
        cb - (1.0 - 2.0 * cs) * cb * (1.0 - cb),
        cb + (2.0 * cs - 1.0) * (d - cb),
    )


def _color_dodge(cb, cs):
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(cs >= 1.0, 1.0, np.minimum(1.0, cb / (1.0 - cs)))
    return np.where(cb <= 0.0, 0.0, r)


def _color_burn(cb, cs):
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(cs <= 0.0, 0.0, 1.0 - np.minimum(1.0, (1.0 - cb) / cs))
    return np.where(cb >= 1.0, 1.0, r)


_FUNCS = {
    "Normal": lambda cb, cs: cs,
    "Multiply": lambda cb, cs: cb * cs,
    "Screen": _screen,
    "Overlay": lambda cb, cs: _hard_light(cs, cb),
    "Darken": np.minimum,
    "Lighten": np.maximum,
    "Color Dodge": _color_dodge,
    "Color Burn": _color_burn,
    "Hard Light": _hard_light,
    "Soft Light": _soft_light,
    "Difference": lambda cb, cs: np.abs(cb - cs),
    "Exclusion": lambda cb, cs: cb + cs - 2.0 * cb * cs,
    "Add": lambda cb, cs: np.minimum(1.0, cb + cs),
    "Subtract": lambda cb, cs: np.maximum(0.0, cb - cs),
}


def blend_rgb(mode: str, cb: np.ndarray, cs: np.ndarray) -> np.ndarray:
    """Blend straight (non-premultiplied) RGB float arrays."""
    fn = _FUNCS.get(mode, _FUNCS["Normal"])
    return np.clip(fn(cb, cs), 0.0, 1.0)


def composite_layers(layers, width: int, height: int) -> np.ndarray:
    """Composite an iterable of Layer objects bottom-to-top into an RGBA uint8 array."""
    rgb_p = np.zeros((height, width, 3), dtype=np.float32)  # premultiplied colour
    alpha = np.zeros((height, width, 1), dtype=np.float32)

    for layer in layers:
        if not layer.visible or layer.opacity <= 0.0:
            continue
        src = layer.pixels.astype(np.float32) / 255.0
        cs = src[..., :3]
        sa = src[..., 3:4] * float(layer.opacity)
        if layer.blend_mode == "Normal":
            rgb_p = sa * cs + (1.0 - sa) * rgb_p
            alpha = sa + alpha * (1.0 - sa)
            continue

        with np.errstate(divide="ignore", invalid="ignore"):
            cb = np.where(alpha > 0.0, rgb_p / np.maximum(alpha, 1e-6), 0.0)
        b = blend_rgb(layer.blend_mode, cb, cs)
        rgb_p = sa * ((1.0 - alpha) * cs + alpha * b) + (1.0 - sa) * rgb_p
        alpha = sa + alpha * (1.0 - sa)

    out = np.empty((height, width, 4), dtype=np.uint8)
    with np.errstate(divide="ignore", invalid="ignore"):
        straight = np.where(alpha > 0.0, rgb_p / np.maximum(alpha, 1e-6), 0.0)
    out[..., :3] = np.clip(straight * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(alpha[..., 0] * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return out


def alpha_over(dst: np.ndarray, src: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    """Source-over composite of two RGBA uint8 arrays, optionally weighted by a uint8 mask."""
    d = dst.astype(np.float32) / 255.0
    s = src.astype(np.float32) / 255.0
    sa = s[..., 3:4]
    if mask is not None:
        sa = sa * (mask.astype(np.float32)[..., None] / 255.0)
    da = d[..., 3:4]
    oa = sa + da * (1.0 - sa)
    with np.errstate(divide="ignore", invalid="ignore"):
        orgb = np.where(oa > 0, (s[..., :3] * sa + d[..., :3] * da * (1.0 - sa)) / np.maximum(oa, 1e-6), 0.0)
    out = np.empty_like(dst)
    out[..., :3] = np.clip(orgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(oa[..., 0] * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return out
