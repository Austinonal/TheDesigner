"""PinCut — Pinterest image search + a small Canva-style editor, as one desktop app.

Run:  python app.py
"""
import json
from pathlib import Path

import requests
import webview

import core

LIB = core.library_dir()
FONTS = LIB / "fonts"
DESIGNS = LIB / "designs"
STATE = LIB / ".state.json"
PROFILE = Path.home() / ".pincut"  # browser profile: keeps the Pinterest login between runs
UI = Path(__file__).parent / "ui" / "index.html"

# Injected into the Pinterest window: a floating "save this image" button.
# It picks the biggest Pinterest image visible on screen (the open Pin) and sends its URL to Python.
PIN_JS = r"""
(() => {
  if (document.getElementById('pincut-save')) return;
  const b = document.createElement('button');
  b.id = 'pincut-save';
  b.textContent = '⬇ เก็บรูปนี้เข้า PinCut';
  b.style.cssText = 'position:fixed;right:24px;bottom:24px;z-index:2147483647;padding:14px 22px;border:0;' +
    'border-radius:999px;background:#ef5a4c;color:#fff;font:600 16px sans-serif;box-shadow:0 6px 20px #0005;cursor:pointer';
  const say = (t) => { b.textContent = t; setTimeout(() => b.textContent = '⬇ เก็บรูปนี้เข้า PinCut', 2500); };
  const big = (img) => {  // the largest candidate from srcset, else src
    const set = (img.srcset || '').split(',').map(s => s.trim().split(' ')[0]).filter(Boolean);
    return set.length ? set[set.length - 1] : (img.currentSrc || img.src);
  };
  b.onclick = async () => {
    const vh = innerHeight, vw = innerWidth;
    const imgs = [...document.images].filter(i => /i\.pinimg\.com/.test(i.currentSrc || i.src)).map(i => {
      const r = i.getBoundingClientRect();
      const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
      const h = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
      return { i, area: w * h };
    }).filter(x => x.area > 0).sort((a, b) => b.area - a.area);
    if (!imgs.length) return say('ไม่เจอรูป — เปิด Pin ที่ต้องการก่อน');
    if (!window.pywebview) return say('รอสักครู่แล้วลองใหม่');
    say('กำลังโหลด…');
    const r = await window.pywebview.api.grab(big(imgs[0].i));
    say(!r.ok ? '✕ ' + r.error : r.dup ? '✓ รูปนี้เก็บไว้แล้ว ไม่เก็บซ้ำ' : '✓ เก็บแล้ว อยู่ในแท็บอัพโหลด');
  };
  document.body.appendChild(b);
})();
"""

main = None  # the PinCut window


def read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(**kw):
    STATE.write_text(json.dumps({**read_state(), **kw}), encoding="utf-8")


class PinApi:
    """Exposed to the Pinterest window. Kept separate from Api so Pinterest's own page
    scripts can only call grab(), not read or write the rest of the library."""

    def grab(self, url):
        try:
            path, new = core.download_pin(url, LIB, requests.get)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if new:
            main.evaluate_js(f"window.onLibraryAdded({json.dumps(core.item(path))})")
        return {"ok": True, "dup": not new}


class Api:
    """Exposed to the PinCut UI as window.pywebview.api (every call returns a Promise in JS)."""

    def __init__(self):
        self._pin = None  # underscore: pywebview does not expose it to JS

    # ----- Pinterest window -----
    def _open(self, url):
        if self._pin is None:
            self._pin = webview.create_window("Pinterest — PinCut", url, js_api=PinApi(), width=1100, height=850)
            self._pin.events.loaded += lambda: self._pin and self._pin.evaluate_js(PIN_JS)
            self._pin.events.closed += self._closed
        else:
            self._pin.load_url(url)
            self._pin.show()
        return self._pin

    def _closed(self):
        self.login_status()
        self._pin = None

    def open_login(self):
        self._open("https://www.pinterest.com/login/")

    def open_search(self, q):
        from urllib.parse import quote
        self._open("https://www.pinterest.com/search/pins/?q=" + quote(str(q)[:200]))

    def login_status(self):
        """True once Pinterest's login cookie is present. Last known answer is remembered between runs."""
        if self._pin is not None:
            try:
                cookies = self._pin.get_cookies()
                # shortcut: relies on Pinterest's "_auth=1" cookie; if Pinterest renames it, detection fails — then check for another session cookie
                logged = any("_auth" in c and c["_auth"].value == "1" for c in cookies)
                write_state(logged_in=logged)
            except Exception:
                pass
        return bool(read_state().get("logged_in"))

    def logout(self):
        w = self._open("https://www.pinterest.com/")
        w.clear_cookies()
        write_state(logged_in=False)
        w.destroy()
        return False

    # ----- image library (Downloads/PinCut) -----
    def list_library(self):
        return [core.item(p) for p in core.list_files(LIB, core.IMG_EXT)]

    def save_upload(self, name, data):
        return core.item(core.save_data_url(LIB, name, data, core.IMG_EXT))

    def file_info(self, file_id):
        st = core.existing_file(LIB, file_id).stat()
        return {"size": st.st_size, "mtime": st.st_mtime}

    def save_copy(self, file_id):
        p = core.existing_file(LIB, file_id)
        path = save_dialog(p.name)
        if path:
            Path(path).write_bytes(p.read_bytes())
        return path

    def trash(self, file_id):
        core.trash_file(LIB, file_id)

    def cut_background(self, file_id):
        return core.item(core.cut_background(core.existing_file(LIB, file_id)))

    # ----- fonts (Downloads/PinCut/fonts) -----
    def list_fonts(self):
        return [{"name": p.stem, "data": core.to_data_url(p)} for p in core.list_files(FONTS, core.FONT_EXT)]

    def save_font(self, name, data):
        return core.save_data_url(FONTS, name, data, core.FONT_EXT).stem

    # ----- designs: the "ล่าสุด" history on the home screen (Downloads/PinCut/designs) -----
    def list_designs(self):
        return core.list_designs(DESIGNS)

    def load_design(self, design_id):
        return core.load_design(DESIGNS, design_id)

    def save_design(self, doc):
        return core.save_design(DESIGNS, doc)

    def update_design(self, design_id, name=None, starred=None):
        return core.update_design(DESIGNS, design_id, name, starred)

    def trash_design(self, design_id):
        core.trash_design(DESIGNS, design_id)

    def restore_design(self, design_id):
        return core.restore_design(DESIGNS, design_id)

    # ----- export -----
    def save_export(self, data, ext, name="design"):
        ext = {"png": "png", "jpg": "jpg", "pdf": "pdf"}[ext]
        path = save_dialog(core.export_name(name, ext))
        if path:
            Path(path).write_bytes(core.from_data_url(data))
        return path


def save_dialog(name):
    """Native "Save as" dialog; returns the chosen path or None."""
    kind = webview.FileDialog.SAVE if hasattr(webview, "FileDialog") else webview.SAVE_DIALOG
    path = main.create_file_dialog(kind, save_filename=name)
    if not path:
        return None
    return path if isinstance(path, str) else path[0]


if __name__ == "__main__":
    PROFILE.mkdir(exist_ok=True)
    main = webview.create_window("PinCut", str(UI), js_api=Api(), width=1400, height=900, min_size=(1000, 680))
    webview.start(private_mode=False, storage_path=str(PROFILE))
