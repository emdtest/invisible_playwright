"""The helpers of tests/test_software_compositing_is_declared_e2e.py, on known inputs (no browser).

* a PNG is compared by the pixels it decodes to: the same pixels with another per-launch chunk (`deBG`) and another compression are the
  same; one pixel changed is not;
* the comparison with the reference names every key that differs, is missing, or is extra, and a missing one carries the entry to record;
* the process type is read from the last argument of `-contentproc`; the CPU of a window is the last value minus the first, a process
  born inside it counted whole.

The committed reference of Windows is held to its own shape: every key of the stable part has a value, and every PNG entry has a length.
"""
from __future__ import annotations

import base64
import json
import struct
import zlib

import pytest

import _cpu_by_type as cpu
import _page_bytes as pb

pytestmark = pytest.mark.unit


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _png(w, h, rgba, extra=b"", level=6, ftype=0):
    rows = b"".join(bytes([ftype]) + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return (pb.PNG_SIGNATURE + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(rows, level)) + extra + _chunk(b"IEND", b""))


PIXELS = bytes((7 * i + 3) % 256 for i in range(4 * 5 * 4))


def _value(png, data_url=False):
    if data_url:
        return {"h": "x", "n": len(png) * 2, "b64": base64.b64encode(png).decode()}
    return {"h": "x", "n": len(png), "b64": base64.b64encode(png).decode()}


def test_the_same_pixels_with_another_chunk_and_another_compression_are_the_same():
    a = pb.decode_png(_png(4, 5, PIXELS, extra=_chunk(b"deBG", b"4620504988720C77"), level=6))
    b = pb.decode_png(_png(4, 5, PIXELS, extra=_chunk(b"deBG", b"D5E4439F1FAC5913"), level=1))
    assert a == b and a["w"] == 4 and a["h"] == 5 and a["ct"] == 6


def test_one_pixel_changed_is_another_png():
    changed = bytearray(PIXELS)
    changed[17] ^= 1
    assert pb.decode_png(_png(4, 5, PIXELS))["sha256"] != pb.decode_png(_png(4, 5, bytes(changed)))["sha256"]


@pytest.mark.parametrize("ftype", [1, 2, 3, 4])
def test_every_scanline_filter_decodes_to_the_pixels(ftype):
    """The encoder of the engine chooses a filter per row; the decoder must undo each of them. The rows are filtered by hand."""
    w, h, bpp = 4, 5, 4
    stride = w * bpp
    rows = [PIXELS[y * stride:(y + 1) * stride] for y in range(h)]
    out = bytearray()
    prev = bytes(stride)
    for row in rows:
        line = bytearray()
        for i in range(stride):
            left = row[i - bpp] if i >= bpp else 0
            up = prev[i]
            upleft = prev[i - bpp] if i >= bpp else 0
            if ftype == 1:
                pred = left
            elif ftype == 2:
                pred = up
            elif ftype == 3:
                pred = (left + up) >> 1
            else:
                pred = pb._paeth(left, up, upleft)
            line.append((row[i] - pred) & 255)
        out += bytes([ftype]) + line
        prev = row
    png = (pb.PNG_SIGNATURE + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(bytes(out))) + _chunk(b"IEND", b""))
    assert pb.decode_png(png) == pb.decode_png(_png(w, h, PIXELS))


def test_something_that_is_not_a_png_is_refused():
    with pytest.raises(pb.NotAPng):
        pb.decode_png(b"GIF89a....")


def _reference_of(measured):
    ref = {"keys": {}, "pngs": {}}
    for key, value in measured.items():
        (ref["pngs"] if pb.is_png_key(key) else ref["keys"])[key] = pb.entry_of(key, value)
    return ref


def _measured(png_extra=b"", pixels=PIXELS):
    return {
        "env.ua": {"s": "Mozilla"},
        "c2d.shapes.getImageData": {"h": "abc", "n": 80, "w": 4, "b64": "AAAA"},
        "c2d.shapes.png": _value(_png(4, 5, pixels, extra=png_extra), data_url=True),
        "c2d.shapes.toBlob": _value(_png(4, 5, pixels, extra=png_extra)),
    }


def test_a_launch_equal_to_its_reference_has_no_problem_whatever_its_png_chunk():
    reference = _reference_of(_measured(png_extra=_chunk(b"deBG", b"4620504988720C77")))
    problems, record = pb.compare(reference, _measured(png_extra=_chunk(b"deBG", b"D5E4439F1FAC5913")))
    assert problems == [] and record == {}


def test_a_changed_stable_value_a_changed_pixel_a_missing_and_an_extra_key_are_each_named():
    reference = _reference_of(_measured())
    changed = bytearray(PIXELS)
    changed[0] ^= 9
    measured = _measured(pixels=bytes(changed))
    measured["env.ua"] = {"s": "Other"}
    measured["c2d.scale.off.getImageData"] = {"h": "n", "n": 1}
    del measured["c2d.shapes.getImageData"]
    problems, record = pb.compare(reference, measured)
    text = "\n".join(problems)
    assert "env.ua" in text
    assert "c2d.shapes.png: the decoded pixels differ" in text and "c2d.shapes.toBlob: the decoded pixels differ" in text
    assert "c2d.shapes.getImageData: in the reference and not read by the probe" in text
    assert "c2d.scale.off.getImageData: not in the reference" in text
    assert list(record) == ["c2d.scale.off.getImageData"]
    assert "RECORD-BEGIN" in pb.message("linux", problems, record)


def test_a_reference_that_holds_only_the_length_of_a_png_asks_for_its_pixels():
    measured = _measured()
    reference = _reference_of(measured)
    del reference["pngs"]["c2d.shapes.png"]["sha256"]
    problems, record = pb.compare(reference, measured)
    assert problems == ["c2d.shapes.png: the reference has the length of this PNG and not yet its decoded pixels"]
    assert record["c2d.shapes.png"]["sha256"] == pb.entry_of("c2d.shapes.png", measured["c2d.shapes.png"])["sha256"]


def test_no_reference_file_makes_every_key_a_problem_with_its_entry():
    problems, record = pb.compare({"keys": {}, "pngs": {}}, _measured())
    assert len(problems) == 4 and sorted(record) == sorted(_measured())


def test_a_png_the_page_read_that_does_not_decode_is_a_problem_not_a_pass():
    measured = {"c2d.shapes.png": {"h": "x", "n": 10, "b64": base64.b64encode(b"not a png at all").decode()}}
    problems, record = pb.compare({"keys": {}, "pngs": {}}, measured)
    assert len(problems) == 1 and "does not decode" in problems[0] and record == {}


def test_a_png_data_url_short_enough_to_be_a_string_is_decoded_too():
    png = _png(4, 5, PIXELS)
    value = {"s": pb.DATA_PREFIX + base64.b64encode(png).decode()}
    entry = pb.png_entry(value)
    assert entry["n"] == len(value["s"]) and entry["sha256"] == pb.decode_png(png)["sha256"]


def test_the_keys_that_are_png_files_and_the_ones_that_are_not():
    for key in ("c2d.shapes.png", "c2d.shapes.toBlob", "c2d.offscreen.convertToBlob", "gl1.triangle.toDataURL", "c2d.scale.high.png"):
        assert pb.is_png_key(key), key
    for key in ("c2d.shapes.jpeg", "c2d.shapes.webp", "gl1.triangle.toDataURL_jpeg", "c2d.scale.high.getImageData", "env.ua"):
        assert not pb.is_png_key(key), key


def test_the_type_of_a_process_is_the_last_argument_of_contentproc():
    assert cpu.classify(["firefox.exe", "-no-remote", "-profile", "p"]) == "parent"
    assert cpu.classify(["firefox.exe", "-contentproc", "-parentBuildID", "1", "gpu"]) == "gpu"
    assert cpu.classify(["firefox.exe", "-contentproc", "-isForBrowser", '"tab"']) == "tab"
    assert cpu.classify(["firefox", "-contentproc", "x", "forkserver"]) == "forkserver"
    assert cpu.classify(["firefox.exe", "-contentproc", "weird"]) == "other"


def test_the_cpu_of_a_window_is_the_last_value_minus_the_first_and_a_new_process_counts_whole():
    start = {1: ("parent", 10.0), 2: ("gpu", 1.0)}
    last = {1: ("parent", 12.5), 2: ("gpu", 1.25), 3: ("gpu", 0.5), 4: ("tab", 2.0)}
    out = cpu.delta_by_type(start, last)
    assert out["parent"] == 2.5 and out["gpu"] == 0.75 and out["tab"] == 2.0 and out["socket"] == 0.0


def test_the_reference_committed_for_windows_has_the_shape_the_comparison_reads():
    ref = json.loads(pb.reference_path("windows").read_text(encoding="utf-8"))
    assert ref["os"] == "windows" and ref["source"]
    assert ref["keys"] and all(("s" in v) != ("h" in v) for v in ref["keys"].values())
    assert all(not pb.is_png_key(k) for k in ref["keys"])
    assert ref["pngs"] and all(pb.is_png_key(k) and isinstance(v.get("n"), int) for k, v in ref["pngs"].items())
    assert all(len(v["sha256"]) == 64 for v in ref["pngs"].values() if "sha256" in v)
