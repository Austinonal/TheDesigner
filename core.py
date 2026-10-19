"""PinCut core: file and image logic with no GUI, so it can be tested on its own."""
import base64
import os
import re
from pathlib import Path
from urllib.parse import urlparse

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
FONT_EXT = {".ttf", ".otf", ".woff", ".woff2"}
MAX_BYTES = 50 * 1024 * 1024  # refuse anything bigger than 50 MB
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
        ".gif": "image/gif", ".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2"}


def library_dir() -> Path:
    """Where downloaded and uploaded images live. Override with the PINCUT_DIR environment variable."""
    d = Path(os.environ.get("PINCUT_DIR") or Path.home() / "Downloads" / "PinCut")
    (d / "fonts").mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name: str) -> str:
    """Keep only a plain file name: no folders, no odd characters."""
    name = Path(str(name).replace("\\", "/")).name
    name = re.sub(r"[^\w\-. ]", "_", name).strip(" .")
    return name[:80] or "file"


def unique_path(folder: Path, name: str) -> Path:
    p = folder / name
    n = 1
    while p.exists():
        p = folder / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return p


def existing_file(folder: Path, file_id: str) -> Path:
    """Resolve an id sent from the UI to a file that really is inside `folder`."""
    p = (folder / safe_name(file_id)).resolve()
    if p.parent != folder.resolve() or not p.is_file():
        raise FileNotFoundError(f"ไม่พบไฟล์: {file_id}")
    return p


def to_data_url(path: Path) -> str:
    mime = MIME.get(path.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def from_data_url(data: str) -> bytes:
    head, sep, body = str(data).partition(",")
    if not sep or not head.startswith("data:") or not head.endswith(";base64"):
        raise ValueError("ข้อมูลไฟล์ไม่ถูกต้อง")
    raw = base64.b64decode(body, validate=True)
    if len(raw) > MAX_BYTES:
        raise ValueError("ไฟล์ใหญ่เกิน 50 MB")
    return raw


def save_data_url(folder: Path, name: str, data: str, allowed: set) -> Path:
    name = safe_name(name)
    if Path(name).suffix.lower() not in allowed:
        raise ValueError(f"ไม่รองรับไฟล์ชนิดนี้: {name}")
    p = unique_path(folder, name)
    p.write_bytes(from_data_url(data))
    return p


def item(path: Path) -> dict:
    """What the UI gets for one library image."""
    return {"id": path.name, "src": to_data_url(path)}


def list_files(folder: Path, allowed: set) -> list:
    """Files in `folder` with an allowed extension, newest first."""
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in allowed]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


# ---------- Pinterest images ----------

_SIZE = re.compile(r"^/(?:originals|\d+x\d*(?:_[A-Za-z0-9]+)?)/(.+)$")


def candidate_urls(url: str) -> list:
    """Pinterest image URLs to try, biggest first. Only i.pinimg.com is accepted."""
    u = urlparse(str(url))
    if u.scheme != "https" or u.hostname != "i.pinimg.com":
        raise ValueError("รับเฉพาะรูปจาก i.pinimg.com")
    m = _SIZE.match(u.path)
    if not m:
        return [url]
    rest = m.group(1)
    out = []
    for c in (f"https://i.pinimg.com/originals/{rest}", f"https://i.pinimg.com/736x/{rest}", url):
        if c not in out:
            out.append(c)
    return out


def download_pin(url: str, folder: Path, get) -> Path:
    """Download the biggest available version of a Pinterest image into `folder`.
    `get` is requests.get (passed in so tests can fake it)."""
    last = "ไม่มี URL ให้ลอง"
    for u in candidate_urls(url):
        name = "pin_" + safe_name(Path(urlparse(u).path).name)
        if Path(name).suffix.lower() not in IMG_EXT:
            name += ".jpg"
        target = folder / name
        if target.exists():  # already downloaded: don't make a copy
            return target
        r = get(u, timeout=30, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.pinterest.com/"})
        ctype = r.headers.get("Content-Type", "")
        if r.status_code == 200 and ctype.startswith("image/"):
            if len(r.content) > MAX_BYTES:
                raise ValueError("รูปใหญ่เกิน 50 MB")
            target.write_bytes(r.content)
            return target
        last = f"{u} -> {r.status_code} {ctype}"
    raise RuntimeError(f"โหลดรูปไม่สำเร็จ ({last})")


def cut_background(path: Path) -> Path:
    """Remove the background with rembg. The model downloads itself on first use (~170 MB)."""
    try:
        from rembg import remove
    except ImportError as e:
        raise RuntimeError('ยังไม่ได้ติดตั้ง rembg — รัน: pip install "rembg[cpu]"') from e
    out = unique_path(path.parent, f"{path.stem}_nobg.png")
    out.write_bytes(remove(path.read_bytes()))
    return out
