import os

import numpy as np
import pytest

from pixelbench import fileio, filters, selection
from pixelbench.blend import BLEND_MODES, alpha_over, blend_rgb, composite_layers
from pixelbench.document import Document, Layer, shift_pixels
from pixelbench.history import History


def make_doc():
    d = Document(64, 48)
    d.add_layer("Background", fill=(255, 255, 255, 255))
    red = d.add_layer("Red")
    red.pixels[10:20, 10:30] = (255, 0, 0, 255)
    return d


def test_composite_normal_and_multiply():
    d = make_doc()
    assert tuple(d.composite()[15, 15]) == (255, 0, 0, 255)
    assert tuple(d.composite()[0, 0]) == (255, 255, 255, 255)
    d.layers[1].blend_mode = "Multiply"
    d.layers[1].opacity = 0.5
    d.changed()
    assert tuple(d.composite()[15, 15]) == (255, 128, 128, 255)


@pytest.mark.parametrize("mode", BLEND_MODES)
def test_every_blend_mode_runs_and_is_bounded(mode):
    cb = np.random.default_rng(1).random((8, 8, 3)).astype(np.float32)
    cs = np.random.default_rng(2).random((8, 8, 3)).astype(np.float32)
    out = blend_rgb(mode, cb, cs)
    assert out.shape == cb.shape and out.min() >= 0 and out.max() <= 1


def test_transparent_layers_do_not_darken():
    d = Document(4, 4)
    d.add_layer("A", fill=(0, 0, 255, 255))
    d.add_layer("B")  # fully transparent
    assert tuple(d.composite()[0, 0]) == (0, 0, 255, 255)


def test_alpha_over_with_mask():
    dst = np.full((2, 2, 4), (0, 0, 0, 255), np.uint8)
    src = np.full((2, 2, 4), (255, 255, 255, 255), np.uint8)
    mask = np.array([[255, 0], [0, 255]], np.uint8)
    out = alpha_over(dst, src, mask)
    assert tuple(out[0, 0, :3]) == (255, 255, 255) and tuple(out[0, 1, :3]) == (0, 0, 0)


def test_history_copy_on_write_undo_redo():
    d = make_doc()
    h = History(d)
    h.push("paint", detach="active")
    d.active_layer.pixels[:] = 0  # in-place mutation after detach
    d.changed()
    assert tuple(d.composite()[15, 15]) == (255, 255, 255, 255)
    assert h.undo() == "paint"
    assert tuple(d.composite()[15, 15]) == (255, 0, 0, 255)
    assert h.redo() == "paint"
    assert tuple(d.composite()[15, 15]) == (255, 255, 255, 255)
    h.undo()
    h.push("second", detach="active")
    assert not h.can_redo()


def test_history_limit():
    d = make_doc()
    h = History(d, limit=3)
    for i in range(6):
        h.push(f"s{i}")
    assert [l for l, _ in h.undo_stack] == ["s3", "s4", "s5"]


def test_layer_ops():
    d = make_doc()
    d.duplicate_layer(1)
    assert len(d.layers) == 3 and d.layers[2].name == "Red copy"
    d.move_layer(2, 0)
    assert d.layers[0].name == "Red copy" and d.active_index == 0
    d.merge_down(2)
    assert len(d.layers) == 2
    d.flatten()
    assert len(d.layers) == 1 and tuple(d.composite()[15, 15]) == (255, 0, 0, 255)


def test_geometry_ops():
    d = make_doc()
    d.resize_canvas(100, 100, (0.5, 0.5))
    assert d.width == 100 and tuple(d.composite()[0, 0]) == (0, 0, 0, 0)
    d.rotate(1)
    assert (d.width, d.height) == (100, 100)
    d.resize_image(50, 50)
    d.crop(5, 5, 30, 30)
    assert (d.width, d.height) == (25, 25) and d.layers[0].pixels.shape == (25, 25, 4)
    d2 = Document(10, 6)
    d2.add_layer("a")
    d2.layers[0].pixels[2:4, 3:7] = 255
    d2.trim()
    assert (d2.width, d2.height) == (4, 2)


def test_shift_pixels():
    a = np.zeros((4, 4, 4), np.uint8)
    a[0, 0] = 255
    b = shift_pixels(a, 2, 1)
    assert b[1, 2, 0] == 255 and b[0, 0, 0] == 0
    assert shift_pixels(a, 10, 10).sum() == 0


def test_native_roundtrip(tmp_path):
    d = make_doc()
    d.layers[1].blend_mode = "Screen"
    d.layers[1].opacity = 0.25
    d.layers[1].locked = True
    d.selection = np.zeros((48, 64), np.uint8)
    d.selection[5:10, 5:10] = 255
    p = str(tmp_path / "t.pxb")
    fileio.save_native(d, p)
    d2 = fileio.open_document(p)
    assert len(d2.layers) == 2
    assert d2.layers[1].blend_mode == "Screen" and abs(d2.layers[1].opacity - 0.25) < 1e-6 and d2.layers[1].locked
    assert np.array_equal(d2.layers[1].pixels, d.layers[1].pixels)
    assert d2.selection is not None and d2.selection[7, 7] == 255
    assert not d2.dirty


@pytest.mark.parametrize("ext", ["png", "jpg", "webp", "bmp", "gif", "tif"])
def test_export_and_reopen(tmp_path, ext):
    d = make_doc()
    p = str(tmp_path / f"t.{ext}")
    fileio.export_image(d, p, quality=90)
    assert os.path.getsize(p) > 0
    d2 = fileio.open_document(p)
    assert (d2.width, d2.height) == (64, 48) and len(d2.layers) == 1


def test_all_filters_run_with_defaults():
    d = make_doc()
    px = d.layers[1].pixels
    for _, name, fn, params in filters.FILTERS:
        kw = {p[0]: p[4] for p in params}
        out = fn(px, **kw)
        assert out.shape == px.shape and out.dtype == np.uint8, name
    assert tuple(filters.invert(px)[15, 15]) == (0, 255, 255, 255)
    assert filters.gaussian_blur(px, 2)[0, 0, 3] == 0  # transparent stays transparent


def test_apply_with_mask():
    px = np.full((4, 4, 4), (100, 100, 100, 255), np.uint8)
    inv = filters.invert(px)
    m = np.zeros((4, 4), np.uint8); m[0, 0] = 255
    out = filters.apply_with_mask(px, inv, m)
    assert out[0, 0, 0] == 155 and out[1, 1, 0] == 100


def test_selection_masks_and_wand():
    d = make_doc()
    m = selection.rect_mask(64, 48, 10, 10, 20, 20)
    assert m[15, 15] == 255 and m[5, 5] == 0 and m.sum() == 255 * 100
    e = selection.ellipse_mask(64, 48, 0, 0, 20, 20)
    assert e[10, 10] == 255 and e[0, 0] == 0
    poly = selection.polygon_mask(64, 48, [(0, 0), (20, 0), (0, 20)])
    assert poly[2, 2] == 255 and poly[19, 19] == 0
    wand = selection.color_region(d.composite(), 15, 15, 10)
    assert (wand > 0).sum() == 200
    wand_all = selection.color_region(d.composite(), 0, 0, 10)
    assert (wand_all > 0).sum() == 64 * 48 - 200
    assert selection.combine(m, e, "add")[10, 10] == 255
    assert selection.combine(m, m, "subtract").sum() == 0
    assert selection.combine(None, m, "subtract")[15, 15] == 0
    assert selection.grow(m, 1).sum() > m.sum() and selection.grow(m, -1).sum() < m.sum()
    ys, xs = selection.outline_points(m)
    assert len(ys) == 36
