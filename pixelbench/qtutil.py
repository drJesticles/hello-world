"""Small helpers bridging numpy arrays and Qt images."""
from __future__ import annotations

import numpy as np
from PySide6.QtGui import QColor, QImage


def qimage_from_array(arr: np.ndarray) -> QImage:
    """Wrap a C-contiguous (H, W, 4) uint8 RGBA array as a QImage *without copying*.

    The caller must keep ``arr`` alive for as long as the QImage is used.
    """
    assert arr.flags["C_CONTIGUOUS"] and arr.dtype == np.uint8 and arr.ndim == 3 and arr.shape[2] == 4
    h, w = arr.shape[:2]
    img = QImage(arr.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    img._keepalive = arr  # type: ignore[attr-defined]
    return img


def gray_qimage_from_array(arr: np.ndarray) -> QImage:
    assert arr.flags["C_CONTIGUOUS"] and arr.dtype == np.uint8 and arr.ndim == 2
    h, w = arr.shape
    img = QImage(arr.data, w, h, w, QImage.Format.Format_Grayscale8)
    img._keepalive = arr  # type: ignore[attr-defined]
    return img


def array_from_qimage(img: QImage) -> np.ndarray:
    """Copy any QImage into an (H, W, 4) straight RGBA uint8 array."""
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h).reshape(h, bpl)
    return np.ascontiguousarray(buf[:, : w * 4].reshape(h, w, 4)).copy()


def color_to_rgba(c: QColor) -> tuple[int, int, int, int]:
    return c.red(), c.green(), c.blue(), c.alpha()
