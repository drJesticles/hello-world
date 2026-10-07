"""Filters and adjustments. Every function takes an RGBA uint8 array and returns a new one.

Alpha is preserved unless the filter is explicitly about transparency.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


def _rgb(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.ascontiguousarray(arr[..., :3]), "RGB")


def _with_alpha(rgb: Image.Image, arr: np.ndarray) -> np.ndarray:
    out = np.empty_like(arr)
    out[..., :3] = np.asarray(rgb.convert("RGB"))
    out[..., 3] = arr[..., 3]
    return out


def _rgba(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.ascontiguousarray(arr), "RGBA")


# --- blur / sharpen -----------------------------------------------------------
def gaussian_blur(arr, radius=3.0):
    # Blur premultiplied so transparent edges don't bleed dark fringes.
    f = arr.astype(np.float32) / 255.0
    a = f[..., 3:4]
    prem = np.concatenate([f[..., :3] * a, a], axis=-1)
    img = Image.fromarray(np.clip(prem * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA")
    img = img.filter(ImageFilter.GaussianBlur(radius))
    p = np.asarray(img).astype(np.float32) / 255.0
    a2 = p[..., 3:4]
    with np.errstate(divide="ignore", invalid="ignore"):
        rgb = np.where(a2 > 0, p[..., :3] / np.maximum(a2, 1e-6), 0)
    out = np.empty_like(arr)
    out[..., :3] = np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(a2[..., 0] * 255 + 0.5, 0, 255).astype(np.uint8)
    return out


def box_blur(arr, radius=2):
    return np.asarray(_rgba(arr).filter(ImageFilter.BoxBlur(radius))).copy()


def motion_blur(arr, length=15, angle=0.0):
    """Average the image along a line of `length` pixels at `angle` degrees (premultiplied)."""
    length = max(1, int(length))
    f = arr.astype(np.float32) / 255.0
    a = f[..., 3:4]
    prem = np.concatenate([f[..., :3] * a, a], axis=-1)
    acc = np.zeros_like(prem)
    t = np.deg2rad(angle)
    n = 0
    for i in range(length):
        off = i - length // 2
        dx = int(round(off * np.cos(t)))
        dy = int(round(-off * np.sin(t)))
        from .document import shift_pixels  # local import to avoid a cycle at module load
        acc += shift_pixels(prem, dx, dy)
        n += 1
    acc /= max(n, 1)
    a2 = acc[..., 3:4]
    with np.errstate(divide="ignore", invalid="ignore"):
        rgb = np.where(a2 > 0, acc[..., :3] / np.maximum(a2, 1e-6), 0)
    out = np.empty_like(arr)
    out[..., :3] = np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(a2[..., 0] * 255 + 0.5, 0, 255).astype(np.uint8)
    return out


def sharpen(arr, amount=1.0):
    img = _rgb(arr)
    out = ImageEnhance.Sharpness(img).enhance(1.0 + float(amount))
    return _with_alpha(out, arr)


def unsharp_mask(arr, radius=2.0, percent=150, threshold=3):
    out = _rgb(arr).filter(ImageFilter.UnsharpMask(radius=radius, percent=int(percent), threshold=int(threshold)))
    return _with_alpha(out, arr)


def median(arr, size=3):
    size = int(size) | 1
    out = _rgb(arr).filter(ImageFilter.MedianFilter(size))
    return _with_alpha(out, arr)


# --- adjustments --------------------------------------------------------------
def brightness_contrast(arr, brightness=0.0, contrast=0.0):
    """brightness, contrast in -100..100."""
    f = arr[..., :3].astype(np.float32)
    f = f + brightness * 2.55
    c = (contrast + 100.0) / 100.0
    c = c * c  # make the slider feel more like Photoshop's
    f = (f - 127.5) * c + 127.5
    out = arr.copy()
    out[..., :3] = np.clip(f + 0.5, 0, 255).astype(np.uint8)
    return out


def exposure(arr, stops=0.0, gamma=1.0):
    f = arr[..., :3].astype(np.float32) / 255.0
    f = f * (2.0 ** stops)
    f = np.clip(f, 0, 1) ** (1.0 / max(gamma, 0.01))
    out = arr.copy()
    out[..., :3] = np.clip(f * 255 + 0.5, 0, 255).astype(np.uint8)
    return out


def hue_saturation(arr, hue=0.0, saturation=0.0, lightness=0.0):
    """hue in -180..180 degrees, saturation/lightness -100..100."""
    img = _rgb(arr).convert("HSV")
    h, s, v = [np.asarray(c).astype(np.int32) for c in img.split()]
    h = (h + int(round(hue * 255.0 / 360.0))) % 256
    s = np.clip(s * (1.0 + saturation / 100.0), 0, 255)
    if lightness >= 0:
        v = v + (255 - v) * (lightness / 100.0)
    else:
        v = v * (1.0 + lightness / 100.0)
    hsv = Image.merge("HSV", [Image.fromarray(np.clip(c, 0, 255).astype(np.uint8), "L") for c in (h, s, v)])
    return _with_alpha(hsv.convert("RGB"), arr)


def color_balance(arr, red=0.0, green=0.0, blue=0.0):
    f = arr[..., :3].astype(np.float32)
    f[..., 0] += red * 2.55
    f[..., 1] += green * 2.55
    f[..., 2] += blue * 2.55
    out = arr.copy()
    out[..., :3] = np.clip(f + 0.5, 0, 255).astype(np.uint8)
    return out


def levels(arr, in_low=0, in_high=255, gamma=1.0, out_low=0, out_high=255):
    f = arr[..., :3].astype(np.float32)
    f = (f - in_low) / max(1.0, float(in_high - in_low))
    f = np.clip(f, 0, 1) ** (1.0 / max(gamma, 0.01))
    f = out_low + f * (out_high - out_low)
    out = arr.copy()
    out[..., :3] = np.clip(f + 0.5, 0, 255).astype(np.uint8)
    return out


def auto_levels(arr, cutoff=0.5):
    out = ImageOps.autocontrast(_rgb(arr), cutoff=cutoff)
    return _with_alpha(out, arr)


def equalize(arr):
    return _with_alpha(ImageOps.equalize(_rgb(arr)), arr)


def invert(arr):
    out = arr.copy()
    out[..., :3] = 255 - arr[..., :3]
    return out


def desaturate(arr):
    g = _rgb(arr).convert("L")
    return _with_alpha(Image.merge("RGB", [g, g, g]), arr)


def threshold(arr, level=128):
    g = np.asarray(_rgb(arr).convert("L"))
    v = np.where(g >= level, 255, 0).astype(np.uint8)
    out = arr.copy()
    out[..., :3] = v[..., None]
    return out


def posterize(arr, levels_=4):
    n = max(2, int(levels_))
    f = arr[..., :3].astype(np.float32)
    q = np.round(f / 255.0 * (n - 1)) / (n - 1) * 255.0
    out = arr.copy()
    out[..., :3] = q.astype(np.uint8)
    return out


def sepia(arr, strength=1.0):
    f = arr[..., :3].astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    tr = 0.393 * r + 0.769 * g + 0.189 * b
    tg = 0.349 * r + 0.686 * g + 0.168 * b
    tb = 0.272 * r + 0.534 * g + 0.131 * b
    s = np.stack([tr, tg, tb], axis=-1)
    f = f * (1 - strength) + s * strength
    out = arr.copy()
    out[..., :3] = np.clip(f + 0.5, 0, 255).astype(np.uint8)
    return out


def vibrance(arr, amount=0.0):
    """Saturate muted colours more than already-vivid ones. amount -100..100."""
    f = arr[..., :3].astype(np.float32) / 255.0
    mx = f.max(axis=-1, keepdims=True)
    mn = f.min(axis=-1, keepdims=True)
    sat = mx - mn
    avg = f.mean(axis=-1, keepdims=True)
    k = (amount / 100.0) * (1.0 - sat)
    f = avg + (f - avg) * (1.0 + k)
    out = arr.copy()
    out[..., :3] = np.clip(f * 255 + 0.5, 0, 255).astype(np.uint8)
    return out


# --- stylize ------------------------------------------------------------------
def pixelate(arr, size=8):
    size = max(1, int(size))
    h, w = arr.shape[:2]
    img = _rgba(arr).resize((max(1, w // size), max(1, h // size)), Image.BILINEAR)
    return np.asarray(img.resize((w, h), Image.NEAREST)).copy()


def add_noise(arr, amount=20.0, monochrome=False, seed=None):
    rng = np.random.default_rng(seed)
    f = arr[..., :3].astype(np.float32)
    if monochrome:
        n = rng.normal(0, amount, arr.shape[:2])[..., None]
    else:
        n = rng.normal(0, amount, f.shape)
    out = arr.copy()
    out[..., :3] = np.clip(f + n + 0.5, 0, 255).astype(np.uint8)
    return out


def emboss(arr):
    return _with_alpha(_rgb(arr).filter(ImageFilter.EMBOSS), arr)


def find_edges(arr):
    return _with_alpha(_rgb(arr).filter(ImageFilter.FIND_EDGES), arr)


def solarize(arr, thresh=128):
    return _with_alpha(ImageOps.solarize(_rgb(arr), threshold=int(thresh)), arr)


def vignette(arr, strength=0.6, radius=1.0):
    h, w = arr.shape[:2]
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    d = np.sqrt(((x - cx) / (w / 2.0)) ** 2 + ((y - cy) / (h / 2.0)) ** 2) / max(radius, 0.01)
    m = np.clip(1.0 - strength * np.clip(d - 0.4, 0, None) ** 2, 0, 1)
    out = arr.copy()
    out[..., :3] = np.clip(arr[..., :3].astype(np.float32) * m[..., None] + 0.5, 0, 255).astype(np.uint8)
    return out


def apply_with_mask(original: np.ndarray, filtered: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
    """Blend the filtered result back into the original where mask is set (feathered)."""
    if mask is None:
        return filtered
    m = mask.astype(np.float32)[..., None] / 255.0
    out = original.astype(np.float32) * (1 - m) + filtered.astype(np.float32) * m
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


# Registry used by the Filter menu: (menu, name, function, [(param, label, min, max, default, step)])
FILTERS = [
    ("Adjustments", "Brightness/Contrast...", brightness_contrast,
     [("brightness", "Brightness", -100, 100, 0, 1), ("contrast", "Contrast", -100, 100, 0, 1)]),
    ("Adjustments", "Levels...", levels,
     [("in_low", "Input black", 0, 254, 0, 1), ("in_high", "Input white", 1, 255, 255, 1),
      ("gamma", "Gamma", 0.1, 5.0, 1.0, 0.01), ("out_low", "Output black", 0, 255, 0, 1),
      ("out_high", "Output white", 0, 255, 255, 1)]),
    ("Adjustments", "Exposure...", exposure,
     [("stops", "Exposure (stops)", -4.0, 4.0, 0.0, 0.05), ("gamma", "Gamma", 0.1, 4.0, 1.0, 0.01)]),
    ("Adjustments", "Hue/Saturation...", hue_saturation,
     [("hue", "Hue", -180, 180, 0, 1), ("saturation", "Saturation", -100, 100, 0, 1),
      ("lightness", "Lightness", -100, 100, 0, 1)]),
    ("Adjustments", "Vibrance...", vibrance, [("amount", "Vibrance", -100, 100, 0, 1)]),
    ("Adjustments", "Color Balance...", color_balance,
     [("red", "Cyan / Red", -100, 100, 0, 1), ("green", "Magenta / Green", -100, 100, 0, 1),
      ("blue", "Yellow / Blue", -100, 100, 0, 1)]),
    ("Adjustments", "Auto Levels", auto_levels, []),
    ("Adjustments", "Equalize", equalize, []),
    ("Adjustments", "Invert", invert, []),
    ("Adjustments", "Desaturate", desaturate, []),
    ("Adjustments", "Threshold...", threshold, [("level", "Level", 0, 255, 128, 1)]),
    ("Adjustments", "Posterize...", posterize, [("levels_", "Levels", 2, 32, 4, 1)]),
    ("Adjustments", "Sepia...", sepia, [("strength", "Strength", 0.0, 1.0, 1.0, 0.01)]),
    ("Blur", "Gaussian Blur...", gaussian_blur, [("radius", "Radius", 0.1, 100.0, 3.0, 0.1)]),
    ("Blur", "Box Blur...", box_blur, [("radius", "Radius", 1, 50, 2, 1)]),
    ("Blur", "Motion Blur...", motion_blur,
     [("length", "Length", 1, 101, 15, 2), ("angle", "Angle", -180.0, 180.0, 0.0, 1.0)]),
    ("Blur", "Median...", median, [("size", "Size", 3, 15, 3, 2)]),
    ("Sharpen", "Sharpen...", sharpen, [("amount", "Amount", 0.0, 5.0, 1.0, 0.05)]),
    ("Sharpen", "Unsharp Mask...", unsharp_mask,
     [("radius", "Radius", 0.1, 50.0, 2.0, 0.1), ("percent", "Amount %", 1, 500, 150, 1),
      ("threshold", "Threshold", 0, 255, 3, 1)]),
    ("Stylize", "Pixelate...", pixelate, [("size", "Cell size", 2, 100, 8, 1)]),
    ("Stylize", "Add Noise...", add_noise, [("amount", "Amount", 0.0, 100.0, 20.0, 0.5)]),
    ("Stylize", "Emboss", emboss, []),
    ("Stylize", "Find Edges", find_edges, []),
    ("Stylize", "Solarize...", solarize, [("thresh", "Threshold", 0, 255, 128, 1)]),
    ("Stylize", "Vignette...", vignette,
     [("strength", "Strength", 0.0, 2.0, 0.6, 0.01), ("radius", "Radius", 0.2, 2.0, 1.0, 0.01)]),
]
