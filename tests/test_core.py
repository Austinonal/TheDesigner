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


class FakeResp:
    def __init__(self, status, ctype="image/jpeg", content=PNG):
        self.status_code, self.headers, self.content = status, {"Content-Type": ctype}, content


def test_download_pin_falls_back_when_original_missing(tmp_path):
    tried = []

    def get(url, **kw):
        tried.append(url)
        return FakeResp(403, "text/html") if "/originals/" in url else FakeResp(200)

    p = core.download_pin("https://i.pinimg.com/236x/ab/cd/ef/abcdef.jpg", tmp_path, get)
    assert p.name == "pin_abcdef.jpg" and p.read_bytes() == PNG
    assert tried[0].startswith("https://i.pinimg.com/originals/")
    # second download of the same pin reuses the file instead of copying
    assert core.download_pin("https://i.pinimg.com/236x/ab/cd/ef/abcdef.jpg", tmp_path, get) == p


def test_download_pin_raises_when_nothing_works(tmp_path):
    with pytest.raises(RuntimeError):
        core.download_pin("https://i.pinimg.com/236x/a/b.jpg", tmp_path, lambda u, **k: FakeResp(404, "text/html"))
