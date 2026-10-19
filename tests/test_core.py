"""Run with:  python -m pytest -q"""
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def data_url(raw, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def test_candidate_urls_prefers_original_then_736():
    urls = core.candidate_urls("https://i.pinimg.com/236x/ab/cd/ef/abcdef.jpg")
    assert urls == [
        "https://i.pinimg.com/originals/ab/cd/ef/abcdef.jpg",
        "https://i.pinimg.com/736x/ab/cd/ef/abcdef.jpg",
        "https://i.pinimg.com/236x/ab/cd/ef/abcdef.jpg",
    ]


@pytest.mark.parametrize("bad", [
    "http://i.pinimg.com/236x/a.jpg",           # not https
    "https://evil.com/i.pinimg.com/a.jpg",      # wrong host
    "https://i.pinimg.com.evil.com/236x/a.jpg", # look-alike host
    "file:///etc/passwd",
])
def test_candidate_urls_rejects_other_hosts(bad):
    with pytest.raises(ValueError):
        core.candidate_urls(bad)


def test_safe_name_strips_folders_and_odd_chars():
    assert core.safe_name("../../secret/a b<c>.png") == "a b_c_.png"
    assert core.safe_name("..\\..\\x.png") == "x.png"
    assert core.safe_name("") == "file"


def test_save_and_list_roundtrip(tmp_path):
    p = core.save_data_url(tmp_path, "cat.png", data_url(PNG), core.IMG_EXT)
    p2 = core.save_data_url(tmp_path, "cat.png", data_url(PNG), core.IMG_EXT)
    assert p.read_bytes() == PNG and p2.name == "cat_1.png"
    assert {x.name for x in core.list_files(tmp_path, core.IMG_EXT)} == {"cat.png", "cat_1.png"}
    assert core.from_data_url(core.item(p)["src"]) == PNG


def test_save_rejects_wrong_type_and_bad_data(tmp_path):
    with pytest.raises(ValueError):
        core.save_data_url(tmp_path, "x.exe", data_url(PNG), core.IMG_EXT)
    with pytest.raises(ValueError):
        core.save_data_url(tmp_path, "x.png", "not a data url", core.IMG_EXT)


def test_existing_file_blocks_path_traversal(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (tmp_path / "outside.png").write_bytes(PNG)
    with pytest.raises(FileNotFoundError):
        core.existing_file(lib, "../outside.png")
    (lib / "in.png").write_bytes(PNG)
    assert core.existing_file(lib, "in.png").name == "in.png"


def test_trash_moves_file_out_of_library(tmp_path):
    (tmp_path / "trash").mkdir()
    (tmp_path / "a.png").write_bytes(PNG)
    t = core.trash_file(tmp_path, "a.png")
    assert t == tmp_path / "trash" / "a.png" and t.read_bytes() == PNG
    assert core.list_files(tmp_path, core.IMG_EXT) == []


class FakeResp:
    def __init__(self, status, ctype="image/jpeg", content=PNG):
        self.status_code, self.headers, self.content = status, {"Content-Type": ctype}, content


def test_download_pin_falls_back_when_original_missing(tmp_path):
    tried = []

    def get(url, **kw):
        tried.append(url)
        return FakeResp(403, "text/html") if "/originals/" in url else FakeResp(200)

    p, new = core.download_pin("https://i.pinimg.com/236x/ab/cd/ef/abcdef.jpg", tmp_path, get)
    assert new and p.name == "pin_abcdef.jpg" and p.read_bytes() == PNG
    assert tried[0].startswith("https://i.pinimg.com/originals/")
    # saving the same pin again (even from another size) reuses the file, no download, no copy
    n = len(tried)
    assert core.download_pin("https://i.pinimg.com/736x/ab/cd/ef/abcdef.jpg", tmp_path, get) == (p, False)
    assert len(tried) == n and len(list(tmp_path.iterdir())) == 1


def test_download_pin_raises_when_nothing_works(tmp_path):
    with pytest.raises(RuntimeError):
        core.download_pin("https://i.pinimg.com/236x/a/b.jpg", tmp_path, lambda u, **k: FakeResp(404, "text/html"))


# ---------- designs ----------

def designs_dir(tmp_path):
    d = tmp_path / "designs"
    (d / "trash").mkdir(parents=True)
    return d


def test_save_design_keeps_created_and_star_and_hides_canvas(tmp_path):
    d = designs_dir(tmp_path)
    doc = {"id": "d1", "name": "โปสเตอร์", "kind": "A4", "w": 794, "h": 1123, "canvas": {"objects": [{}, {}]}}
    m = core.save_design(d, doc, now=100)
    assert m["created"] == m["updated"] == 100 and m["objects"] == 2 and "canvas" not in m
    core.update_design(d, "d1", starred=True)
    m = core.save_design(d, {**doc, "canvas": {"objects": []}}, now=200)
    assert (m["created"], m["updated"], m["starred"], m["objects"]) == (100, 200, True, 0)
    assert core.load_design(d, "d1")["canvas"] == {"objects": []}


def test_update_design_renames_without_counting_as_an_edit(tmp_path):
    d = designs_dir(tmp_path)
    core.save_design(d, {"id": "d1", "name": "a"}, now=100)
    m = core.update_design(d, "d1", name="  ใหม่  ")
    assert m["name"] == "ใหม่" and m["updated"] == 100
    assert core.update_design(d, "d1", name="   ")["name"] == "ใหม่"  # blank name is ignored


def test_list_designs_newest_first_and_skips_broken_files(tmp_path):
    d = designs_dir(tmp_path)
    core.save_design(d, {"id": "old"}, now=1)
    core.save_design(d, {"id": "new"}, now=2)
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    assert [m["id"] for m in core.list_designs(d)] == ["new", "old"]
    assert core.list_designs(d)[0]["name"] == "ดีไซน์ที่ไม่มีชื่อ"


def test_trash_and_restore_design(tmp_path):
    d = designs_dir(tmp_path)
    core.save_design(d, {"id": "d1", "name": "x"})
    core.trash_design(d, "d1")
    assert core.list_designs(d) == [] and (d / "trash" / "d1.json").exists()
    assert core.restore_design(d, "d1")["name"] == "x"
    assert [m["id"] for m in core.list_designs(d)] == ["d1"]


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "a.json", "x" * 65])
def test_design_ids_cannot_escape_the_folder(tmp_path, bad):
    with pytest.raises(ValueError):
        core.save_design(designs_dir(tmp_path), {"id": bad})


def test_export_name_keeps_thai_and_drops_path_chars():
    assert core.export_name("ดีไซน์ที่ไม่มีชื่อ", "png") == "ดีไซน์ที่ไม่มีชื่อ.png"
    assert core.export_name('../a:b*"c', "pdf") == "_a_b__c.pdf"
    assert core.export_name("  ", "jpg") == "design.jpg"


def test_save_design_keeps_extra_pages_and_can_drop_them(tmp_path):
    d = designs_dir(tmp_path)
    core.save_design(d, {"id": "d1", "canvas": {"objects": []}, "more": [{"objects": [{}]}, {"objects": []}]})
    assert len(core.load_design(d, "d1")["more"]) == 2
    core.save_design(d, {"id": "d1", "canvas": {"objects": []}, "more": []})
    assert core.load_design(d, "d1")["more"] == []
