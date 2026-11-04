# -*- coding: utf-8 -*-
"""
MangaMaid (Portable) - All-in-One Portable Manga, Doujin & Hentai Downloader & CBZ Packager
Version: Beta v0.5.0
Window Size: 1280x720 (16:9 Widescreen Split-Column Layout)
"""

import base64
import concurrent.futures
import ctypes
import html
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageTk

try:
    import winsound
except ImportError:
    winsound = None

import requests
import gallery_dl
from gallery_dl import config, exception, extractor, job, output
from gallery_dl.extractor.message import Message
from gallery_dl.extractor.utils import mangafire_vrf
from gallery_dl.postprocessor import zip as pp_zip


APP_VERSION = "Beta v0.5.0"

UA_STRING = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def resource_path(relative_path: str) -> str:
    """Resolve resource path for both dev mode and PyInstaller --onefile bundle."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def get_user_downloads_folder() -> str:
    """Dynamically resolves the current Windows user's Downloads folder on any PC."""
    try:
        user_profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        dl_dir = os.path.join(user_profile, "Downloads")
        if os.path.isdir(dl_dir):
            return os.path.normpath(dl_dir)
    except Exception:
        pass
    return os.path.normpath(os.getcwd())


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """Cleans a manga/doujin title for safe Windows folder and .cbz naming."""
    clean = html.unescape(name or "").strip()
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" ._")
    if len(clean) > max_len:
        clean = clean[:max_len].strip(" ._")
    return clean or "MangaMaid_Download"


class SoundManager:
    """
    Cross-platform SoundManager for Windows and macOS:
    - Windows: Looping Chiptune BGM via `winsound` + MP3 SFX via `winmm` MCI.
    - macOS: Looping Chiptune BGM & MP3 SFX via native `afplay` (`-v 0.6`).
    """

    def __init__(self, bgm_wav: str, click_mp3: str, complete_mp3: str):
        self.bgm_wav = bgm_wav
        self.click_mp3 = click_mp3
        self.complete_mp3 = complete_mp3
        self.bgm_enabled = True
        self.sfx_enabled = True
        self._lock = threading.Lock()
        self._bgm_stop_evt = threading.Event()
        self._bgm_proc = None

    def start_bgm(self):
        if not self.bgm_enabled or not os.path.exists(self.bgm_wav):
            return
        if sys.platform == "win32" and winsound is not None:
            try:
                winsound.PlaySound(
                    self.bgm_wav,
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
                )
            except Exception:
                pass
        elif sys.platform == "darwin":
            self._bgm_stop_evt.clear()
            threading.Thread(target=self._mac_bgm_loop, daemon=True).start()

    def _mac_bgm_loop(self):
        while not self._bgm_stop_evt.is_set() and self.bgm_enabled:
            try:
                self._bgm_proc = subprocess.Popen(
                    ["afplay", "-v", "0.85", self.bgm_wav],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._bgm_proc.wait()
            except Exception:
                break

    def stop_bgm(self):
        if sys.platform == "win32" and winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        elif sys.platform == "darwin":
            self._bgm_stop_evt.set()
            if self._bgm_proc is not None:
                try:
                    self._bgm_proc.terminate()
                except Exception:
                    pass
                self._bgm_proc = None

    def set_bgm_enabled(self, enabled: bool):
        self.bgm_enabled = enabled
        if enabled:
            self.start_bgm()
        else:
            self.stop_bgm()

    def set_sfx_enabled(self, enabled: bool):
        self.sfx_enabled = enabled

    def play_click(self):
        if self.sfx_enabled:
            self._play_mp3_async(self.click_mp3, "mangamaid_click")

    def play_complete(self):
        if self.sfx_enabled:
            self._play_mp3_async(self.complete_mp3, "mangamaid_complete")

    def _play_mp3_async(self, mp3_path: str, alias: str):
        if not os.path.exists(mp3_path):
            return
        threading.Thread(
            target=self._play_mp3_worker, args=(mp3_path, alias), daemon=True
        ).start()

    def _play_mp3_worker(self, mp3_path: str, alias: str):
        if sys.platform == "darwin":
            try:
                subprocess.run(
                    ["afplay", "-v", "0.6", mp3_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                pass
            return

        with self._lock:
            try:
                winmm = ctypes.windll.winmm
                buf = ctypes.create_unicode_buffer(256)
                winmm.mciSendStringW(f"close {alias}", buf, 255, 0)
                q = chr(34)
                winmm.mciSendStringW(
                    f"open {q}{mp3_path}{q} type mpegvideo alias {alias}",
                    buf,
                    255,
                    0,
                )
                # Reduce SFX volume by ~40% (600 out of 1000 max MCI volume)
                winmm.mciSendStringW(f"setaudio {alias} volume to 600", buf, 255, 0)
                winmm.mciSendStringW(f"play {alias} from 0", buf, 255, 0)
            except Exception:
                pass


class GUILogCaptureHandler(logging.Handler):
    """Captures gallery-dl error logs during a job so we can display detailed error popups."""

    def __init__(self):
        super().__init__()
        self.errors: list[str] = []

    def clear(self):
        self.errors.clear()

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            msg = record.getMessage()
            if msg and msg not in self.errors:
                self.errors.append(msg)


class GUIOutput:
    """Custom gallery-dl output handler to stream real-time page/chapter progress to GUI."""

    def __init__(self, on_page_cb):
        self.on_page_cb = on_page_cb

    def start(self, path):
        pass

    def skip(self, path):
        self.on_page_cb(path, skipped=True)

    def success(self, path):
        self.on_page_cb(path, skipped=False)

    def progress(self, bytes_total, bytes_downloaded, bytes_per_second):
        pass


class MangaMaidDownloadJob(job.DownloadJob):
    """
    Custom DownloadJob that pre-collects all queued chapters (Message.Queue) before
    starting chapter downloads so we know the exact total chapter count for the
    overall job progress bar (0% -> 100%). Also reports single-gallery page counts.
    """

    def __init__(
        self,
        url,
        parent=None,
        on_total_chapters=None,
        on_chapter_start=None,
        on_expected_pages=None,
    ):
        super().__init__(url, parent)
        self._is_root = parent is None
        self._on_total_chapters = on_total_chapters
        self._on_chapter_start = on_chapter_start
        self._on_expected_pages = on_expected_pages

    def dispatch(self, messages):
        if not self._is_root:
            return super().dispatch(messages)

        msg = None
        process = True
        metadata_url = self.metadata_url
        queued_chapters = []

        if follow := self.extractor.config("follow"):
            follow = self._follow_parse(follow)
            follow_urls = follow_kwdict = None
        else:
            follow = follow_urls = None

        for msg, url, kwdict in messages:
            if msg == Message.Directory:
                if follow_urls is not None:
                    for furl in follow_urls:
                        if metadata_url is not None:
                            follow_kwdict[metadata_url] = furl
                        if self.pred_queue(furl, follow_kwdict):
                            queued_chapters.append((furl, follow_kwdict.copy()))
                    follow_urls = None

                # Ensure "{manga}" is always populated even on single-gallery extractors
                if not kwdict.get("manga"):
                    fallback_title = (
                        kwdict.get("title")
                        or kwdict.get("title_en")
                        or str(kwdict.get("gallery_id") or "Gallery")
                    )
                    kwdict["manga"] = sanitize_filename(fallback_title)

                if self._on_expected_pages and kwdict.get("count"):
                    try:
                        self._on_expected_pages(int(kwdict["count"]))
                    except Exception:
                        pass

                self.update_kwdict(kwdict)
                if self.pred_post(url, kwdict):
                    process = True
                    self.handle_directory(kwdict)
                    if follow is not None:
                        follow_urls = self._follow_collect(follow(kwdict))
                        if follow_urls is not None:
                            follow_kwdict = kwdict.copy()
                else:
                    process = None

            elif process is None:
                continue

            elif msg == Message.Url:
                if self._on_total_chapters:
                    self._on_total_chapters(1)
                    self._on_total_chapters = None
                if metadata_url is not None:
                    kwdict[metadata_url] = url
                if not kwdict.get("manga"):
                    fallback_title = (
                        kwdict.get("title")
                        or kwdict.get("title_en")
                        or str(kwdict.get("gallery_id") or "Gallery")
                    )
                    kwdict["manga"] = sanitize_filename(fallback_title)
                if "page" not in kwdict and "num" in kwdict:
                    kwdict["page"] = kwdict["num"]
                self.update_kwdict(kwdict)
                if self.pred_url(url, kwdict):
                    self.handle_url(url, kwdict)

            elif msg == Message.Queue:
                self.update_kwdict(kwdict)
                if metadata_url is not None:
                    kwdict[metadata_url] = url
                if self.pred_queue(url, kwdict):
                    queued_chapters.append((url, kwdict.copy()))

        if follow_urls is not None:
            for furl in follow_urls:
                if metadata_url is not None:
                    follow_kwdict[metadata_url] = furl
                if self.pred_queue(furl, follow_kwdict):
                    queued_chapters.append((furl, follow_kwdict.copy()))

        if queued_chapters:
            total = len(queued_chapters)
            if self._on_total_chapters:
                self._on_total_chapters(total)
            for idx, (q_url, q_kwdict) in enumerate(queued_chapters, 1):
                if self._on_chapter_start:
                    self._on_chapter_start(idx, total, q_kwdict)
                self.handle_queue(q_url, q_kwdict)

        return msg


# ==============================================================================
# UNIVERSAL DOUJIN & GALLERY EXTRACTOR ENGINE
# Supports HentaiVox, HentaiPaw, RokuHentai, HentaiEnvy, HentaiRox, HentaiZap,
# HentaiEra, HentaiFox, ImHentai, AsmHentai, 3Hentai, nhentai.net / .xxx / .to
# ==============================================================================

ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp", "a": "avif"}


def extract_custom_gallery_pages(
    url: str, status_cb=None
) -> tuple[str, str, list[tuple[int, str, str]], str] | None:
    """
    Checks if `url` belongs to one of our custom-supported Doujin/Hentai gallery sites
    (or uses a known reader format like readerPages / readerPagesJson / g_th).
    Returns `(source_name, clean_title, page_list, referer_url)` where `page_list` is
    a sorted list of `(page_number, extension_without_dot, full_image_url)`,
    or `None` if `url` should be handled by standard `gallery-dl`.
    """
    if not url.startswith(("http://", "https://")):
        return None

    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    headers = {"User-Agent": UA_STRING, "Referer": f"{parsed.scheme}://{parsed.netloc}/"}

    # 1. HentaiVox (hentaivox.com/view/<id>)
    m_hv = re.match(r"https?://(?:www\.)?hentaivox\.com/(?:view|gallery)/(\d+)", url, re.I)
    if m_hv:
        gid = m_hv.group(1)
        reader_url = f"https://hentaivox.com/view/{gid}/1"
        if status_cb:
            status_cb(f"Connecting to HentaiVox (#{gid})...")
        r = requests.get(reader_url, headers=headers, timeout=15)
        r.raise_for_status()
        m_b64 = re.search(
            r'var\s+readerPages\s*=\s*JSON\.parse\(atob\("([^"]+)"\)\)', r.text
        )
        if m_b64:
            data = json.loads(base64.b64decode(m_b64.group(1)).decode("utf-8"))
            raw_title = data.get("title", f"HentaiVox_{gid}")
            clean_title = re.sub(
                r"\s*-\s*Page\s*\{:page\}\s*-\s*HentaiVox$", "", raw_title, flags=re.I
            ).strip()
            base_img = data["baseUriImg"]
            pages_dict = data["pages"]
            pages = []
            for p_str in sorted(pages_dict.keys(), key=int):
                p_num = int(p_str)
                fname = pages_dict[p_str]["f"]
                ext = os.path.splitext(fname)[1].lstrip(".") or "jpg"
                pages.append((p_num, ext, base_img % fname))
            return "HentaiVox", clean_title, pages, reader_url

    # 2. HentaiPaw (hentaipaw.com/articles/<id> or /viewer?articleId=<id>)
    m_hp = re.search(
        r"hentaipaw\.com/(?:articles/|viewer\?articleId=)(\d+)", url, re.I
    )
    if m_hp:
        aid = m_hp.group(1)
        viewer_url = f"https://hentaipaw.com/viewer?articleId={aid}&page=1"
        if status_cb:
            status_cb(f"Connecting to HentaiPaw (#{aid})...")
        r = requests.get(viewer_url, headers=headers, timeout=15)
        r.raise_for_status()
        m_title = re.search(r"<title>([^<]+)</title>", r.text)
        raw_title = m_title.group(1) if m_title else f"HentaiPaw_{aid}"
        clean_title = re.sub(
            r"\s*(?:-\s*Page\s*\d+)?\s*(?:-\s*HentaiPaw.*)$", "", raw_title, flags=re.I
        ).strip()
        cdn_urls = list(
            dict.fromkeys(
                re.findall(
                    rf"https://cdn\.imagedeliveries\.com/{aid}/[a-f0-9]+/(\d+)\.(webp|jpg|jpeg|png|gif)",
                    r.text,
                    re.I,
                )
            )
        )
        # Re-extract full URLs preserving order
        full_matches = re.findall(
            rf"(https://cdn\.imagedeliveries\.com/{aid}/[a-f0-9]+/(\d+)\.(webp|jpg|jpeg|png|gif))",
            r.text,
            re.I,
        )
        seen_p = set()
        pages = []
        for full_u, p_str, ext in full_matches:
            p_num = int(p_str)
            if p_num not in seen_p:
                seen_p.add(p_num)
                pages.append((p_num, ext.lower(), full_u))
        pages.sort(key=lambda x: x[0])
        if pages:
            return "HentaiPaw", clean_title or f"HentaiPaw_{aid}", pages, viewer_url

    # 3. RokuHentai (rokuhentai.com/<6char_id>)
    m_rk = re.match(
        r"https?://(?:www\.)?rokuhentai\.com/([a-z0-9]{5,8})(?:/\d+)?", url, re.I
    )
    if m_rk and m_rk.group(1).lower() not in ("search", "faq", "about", "dmca"):
        gid = m_rk.group(1)
        gal_url = f"https://rokuhentai.com/{gid}"
        if status_cb:
            status_cb(f"Connecting to RokuHentai ({gid})...")
        r = requests.get(gal_url, headers=headers, timeout=15)
        r.raise_for_status()
        m_title = re.search(r"<title>([^<]+)</title>", r.text)
        raw_title = m_title.group(1) if m_title else f"RokuHentai_{gid}"
        clean_title = re.sub(r"\s*-\s*Roku Hentai$", "", raw_title, flags=re.I).strip()
        m_cnt = re.search(r"(\d+)\s+images?", r.text, re.I)
        count = int(m_cnt.group(1)) if m_cnt else 0
        if count == 0:
            thumbs = re.findall(
                rf"_images/page-thumbnails/{gid}/(\d+)\.jpg", r.text, re.I
            )
            if thumbs:
                count = max(int(x) for x in thumbs) + 1
        if count > 0:
            pages = [
                (i + 1, "jpg", f"https://rokuhentai.com/_images/pages/{gid}/{i}.jpg")
                for i in range(count)
            ]
            return "RokuHentai", clean_title, pages, f"https://rokuhentai.com/{gid}/0"

    # 4. 3Hentai (3hentai.net/d/<id>)
    m_3h = re.match(r"https?://(?:www\.)?3hentai\.net/d/(\d+)", url, re.I)
    if m_3h:
        gid = m_3h.group(1)
        gal_url = f"https://3hentai.net/d/{gid}"
        if status_cb:
            status_cb(f"Connecting to 3Hentai (#{gid})...")
        r = requests.get(gal_url, headers=headers, timeout=15)
        r.raise_for_status()
        m_title = re.search(r"<title>([^<]+)</title>", r.text)
        raw_title = m_title.group(1) if m_title else f"3Hentai_{gid}"
        clean_title = re.sub(r"\s*-\s*3Hentai$", "", raw_title, flags=re.I).strip()
        thumbs = re.findall(
            r"(https://s\d+\.3hentai\.xyz/d\d+/(\d+)t\.(jpg|jpeg|png|webp|gif))",
            r.text,
            re.I,
        )
        seen_p = set()
        pages = []
        for thumb_u, p_str, ext in thumbs:
            p_num = int(p_str)
            if p_num not in seen_p:
                seen_p.add(p_num)
                full_u = re.sub(r"(\d+)t\.([a-z]+)$", r"\1.\2", thumb_u, flags=re.I)
                pages.append((p_num, ext.lower(), full_u))
        pages.sort(key=lambda x: x[0])
        if pages:
            return "3Hentai", clean_title, pages, gal_url

    # 5. AsmHentai (asmhentai.com/g/<id> or /gallery/<id>)
    m_asm = re.match(
        r"https?://(?:www\.)?asmhentai\.com/(?:g|gallery)/(\d+)", url, re.I
    )
    if m_asm:
        gid = m_asm.group(1)
        gal_url = f"https://asmhentai.com/g/{gid}/"
        if status_cb:
            status_cb(f"Connecting to AsmHentai (#{gid})...")
        r = requests.get(gal_url, headers=headers, timeout=15)
        r.raise_for_status()
        m_h1 = re.search(r"<h1>(.*?)</h1>", r.text, re.S)
        clean_title = (
            re.sub(r"<[^>]+>", "", m_h1.group(1)).strip()
            if m_h1
            else f"AsmHentai_{gid}"
        )
        m_cnt = re.search(r"Pages:\s*(?:</span>)?\s*(\d+)", r.text, re.I)
        total_pages = int(m_cnt.group(1)) if m_cnt else 0
        m_img = re.search(
            r"(?:https?:)?//images\.asmhentai\.com/(\d+)/(\d+)/(?:cover|1t)\.(jpg|png|webp|gif)",
            r.text,
            re.I,
        )
        if m_img and total_pages > 0:
            dir_id, img_gid, ext = m_img.group(1), m_img.group(2), m_img.group(3)
            pages = [
                (
                    p,
                    ext.lower(),
                    f"https://images.asmhentai.com/{dir_id}/{img_gid}/{p}.{ext}",
                )
                for p in range(1, total_pages + 1)
            ]
            return "AsmHentai", clean_title, pages, gal_url

    # 6. ImHentai / HentaiEnvy / HentaiRox / HentaiZap / HentaiEra / HentaiFox / nhentai.xxx / nhentai.to
    m_im = re.match(
        r"https?://(?:www\.)?(imhentai\.xxx|hentaienvy\.com|hentairox\.com|hentaizap\.com|hentaiera\.com|hentaifox\.com|nhentai\.xxx|nhentai\.to)/(?:gallery|view|g)/(\d+)",
        url,
        re.I,
    )
    if m_im:
        domain, gid = m_im.group(1).lower(), m_im.group(2)
        site_label = domain.split(".")[0].capitalize()
        if status_cb:
            status_cb(f"Connecting to {site_label} (#{gid})...")

        # Check viewer /view/<id>/1/ or /g/<id>/1/ first for modern readerPagesJson or g_th
        view_prefix = "g" if "nhentai" in domain else "view"
        gal_prefix = "g" if "nhentai" in domain else "gallery"
        viewer_url = f"https://{domain}/{view_prefix}/{gid}/1/"
        gal_url = f"https://{domain}/{gal_prefix}/{gid}/"

        rv = requests.get(viewer_url, headers=headers, timeout=15)
        # Case A: Modern readerApp + readerPagesJson (HentaiEnvy, HentaiRox, HentaiZap)
        m_json = re.search(
            r'id="readerPagesJson"[^>]*>(.*?)</script>', rv.text, re.S | re.I
        )
        m_base = re.search(r'data-reader-image-base="([^"]+)"', rv.text, re.I)
        if m_json and m_base:
            img_base = m_base.group(1).rstrip("/")
            m_seo = re.search(r'data-reader-seo-title="([^"]+)"', rv.text, re.I)
            clean_title = m_seo.group(1) if m_seo else f"{site_label}_{gid}"
            pages_list = json.loads(m_json.group(1))
            pages = [
                (
                    int(item["page"]),
                    item.get("ext", "webp").lstrip("."),
                    f"{img_base}/{item['page']}.{item.get('ext', 'webp').lstrip('.')}",
                )
                for item in pages_list
            ]
            if pages:
                return site_label, clean_title, pages, viewer_url

        # Case B: Classic g_th JSON dictionary on gallery page (ImHentai, HentaiEra, HentaiFox, nhentai.xxx)
        rg = requests.get(gal_url, headers=headers, timeout=15)
        m_gth = re.search(r"var\s+g_th\s*=\s*\$\.parseJSON\('([^']+)'\)", rg.text)
        if m_gth:
            gth_dict = json.loads(m_gth.group(1))
            # If g_th is nested under "fl" (e.g. {"fl": {"1": "j,1280,1800", ...}}), unwrap it
            if "fl" in gth_dict and isinstance(gth_dict["fl"], dict):
                gth_dict = gth_dict["fl"]
            m_h1 = re.search(r"<h1>([^<]+)</h1>", rg.text)
            clean_title = m_h1.group(1).strip() if m_h1 else f"{site_label}_{gid}"
            # Locate image base from cover or 1t thumbnail
            m_base_u = re.search(
                r'(https?://[a-z0-9.]+\.[a-z]+/\d+/[a-z0-9_]+)/(?:cover|1t)\.(?:jpg|png|webp|gif)',
                rg.text,
                re.I,
            )
            if m_base_u:
                img_base = m_base_u.group(1)
                pages = []
                numeric_keys = [k for k in gth_dict.keys() if str(k).isdigit()]
                for p_str in sorted(numeric_keys, key=int):
                    p_num = int(p_str)
                    ext_code = str(gth_dict[p_str]).split(",")[0]
                    ext = ext_map.get(ext_code, "jpg")
                    pages.append((p_num, ext, f"{img_base}/{p_num}.{ext}"))
                if pages:
                    return site_label, clean_title, pages, gal_url

    return None


def search_mangafire(keyword: str) -> str | None:
    """Searches MangaFire API using VRF signature and returns the best matching title URL."""
    try:
        clean_kw = re.sub(r"[^\w\s-]", " ", keyword).strip()
        clean_kw = re.sub(r"\s+", " ", clean_kw)
        vrf = mangafire_vrf.generate(f"/titles?keyword={clean_kw}")
        r = requests.get(
            "https://mangafire.to/api/titles",
            params={"keyword": clean_kw, "vrf": vrf},
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": UA_STRING,
            },
            timeout=10,
        )
        if r.status_code == 200:
            items = r.json().get("items") or []
            if items:
                first = items[0]
                return f"https://mangafire.to/title/{first['hid']}-{first['slug']}"
    except Exception:
        pass
    return None


def search_mangadex(keyword: str) -> str | None:
    """Searches MangaDex API for a title with English chapters."""
    try:
        r = requests.get(
            "https://api.mangadex.org/manga",
            params={"title": keyword, "limit": 5},
            timeout=10,
        ).json()
        for item in r.get("data", []):
            uuid = item["id"]
            feed = requests.get(
                f"https://api.mangadex.org/manga/{uuid}/feed?translatedLanguage[]=en&limit=1",
                timeout=10,
            ).json()
            if feed.get("data"):
                return f"https://mangadex.org/title/{uuid}"
    except Exception:
        pass
    return None


def resolve_manga_url(raw_input: str, status_cb=None) -> tuple[str, str]:
    """
    Normalizes and resolves input URLs (or manga titles / 6-digit codes) across common
    manga, webtoon, hentai, and doujin sites.
    Returns (resolved_url, source_note).
    """
    url = raw_input.strip()
    if not url:
        raise ValueError("Please paste a Manga or Doujin URL into the input box first!")

    # 0. Support 5-6 digit nhentai nuclear codes directly (e.g., "177013" or "#177013")
    if re.fullmatch(r"#?\d{5,6}", url):
        code = url.lstrip("#")
        return f"https://nhentai.net/g/{code}/", f"NHentai (#{code})"

    # 1. Normalize MangaDex URLs (strip ?tab=art, ?tab=chapters, etc.)
    md_match = re.match(
        r"https?://(?:www\.)?mangadex\.org/(?:title|manga)/([0-9a-fA-F-]+)", url
    )
    if md_match:
        uuid = md_match.group(1)
        clean_md_url = f"https://mangadex.org/title/{uuid}"
        try:
            r = requests.get(
                f"https://api.mangadex.org/manga/{uuid}/feed?translatedLanguage[]=en&limit=1",
                timeout=10,
            ).json()
            if r.get("total", 1) == 0 and len(r.get("data", [])) == 0:
                r_info = requests.get(
                    f"https://api.mangadex.org/manga/{uuid}", timeout=10
                ).json()
                titles = r_info.get("data", {}).get("attributes", {}).get("title", {})
                title_str = (
                    titles.get("en")
                    or titles.get("ja-ro")
                    or next(iter(titles.values()), "")
                )
                if title_str:
                    if status_cb:
                        status_cb(
                            f"No English chapters on MangaDex for '{title_str}', routing to MangaFire..."
                        )
                    mf_url = search_mangafire(title_str)
                    if mf_url:
                        return mf_url, f"MangaFire ({title_str})"
        except Exception:
            pass
        return clean_md_url, "MangaDex"

    # 2. Normalize MangaFire /read/ URLs to /title/ URLs
    mf_read_match = re.match(
        r"https?://(?:www\.)?mangafire\.to/read/([\w-]+)\.(\w+)", url
    )
    if mf_read_match:
        slug, hid = mf_read_match.group(1), mf_read_match.group(2)
        return f"https://mangafire.to/title/{hid}-{slug}", "MangaFire"

    # 3. Handle comix.to links automatically
    comix_match = re.match(
        r"https?://(?:www\.)?comix\.to/(?:title|manga)/(\w+)-([\w-]+)", url
    )
    if comix_match:
        hid, slug = comix_match.group(1), comix_match.group(2)
        title_guess = slug.replace("-", " ")
        try:
            if status_cb:
                status_cb("Resolving comix.to manga title...")
            resp = requests.get(url, headers={"User-Agent": UA_STRING}, timeout=10)
            m_title = re.search(r"<title>([^<]+)</title>", resp.text)
            if m_title:
                title_guess = m_title.group(1).strip()
        except Exception:
            pass

        mf_url = search_mangafire(title_guess)
        if mf_url:
            return mf_url, f"MangaFire ({title_guess})"
        md_url = search_mangadex(title_guess)
        if md_url:
            return md_url, f"MangaDex ({title_guess})"

    # 4. Check if gallery-dl directly supports the URL
    if url.startswith(("http://", "https://")):
        ext = extractor.find(url)
        if ext is not None:
            return url, ext.category.capitalize()

        # Fallback: extract slug from unsupported URL path and search MangaFire / MangaDex
        parsed = urllib.parse.urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            slug = re.sub(r"^[0-9a-z]{3,6}-", "", parts[-1]).replace("-", " ")
            if len(slug) > 2 and not slug.isdigit():
                if status_cb:
                    status_cb(f"Searching supported sources for '{slug}'...")
                mf_url = search_mangafire(slug)
                if mf_url:
                    return mf_url, f"MangaFire ({slug})"
                md_url = search_mangadex(slug)
                if md_url:
                    return md_url, f"MangaDex ({slug})"
        raise ValueError(
            f"Unsupported site or gallery not found for URL:\n{url}\n\n"
            "Click 'Supported Sites ▼' to see supported sites, or paste the manga's title directly!"
        )

    # 5. Plain text title search!
    if status_cb:
        status_cb(f"Searching for manga '{url}'...")
    mf_url = search_mangafire(url)
    if mf_url:
        return mf_url, f"MangaFire ({url})"
    md_url = search_mangadex(url)
    if md_url:
        return md_url, f"MangaDex ({url})"

    raise ValueError(
        f"Could not find any manga with English chapters matching:\n'{url}'\n\n"
        "Please check the spelling or paste a direct link from MangaDex, MangaFire, NHentai, HentaiVox, etc."
    )


def get_supported_sites_list() -> list[tuple[str, str, str]]:
    """
    Returns a categorized list of (DisplayName, DomainOrURL, CategoryGroup)
    where CategoryGroup is 'MANGA', 'DOUJIN', or 'OTHER'.
    """
    manga_featured = [
        ("MangaDex", "https://mangadex.org", "MANGA"),
        ("MangaFire", "https://mangafire.to", "MANGA"),
        ("Comix.to (Auto-Resolved)", "https://comix.to", "MANGA"),
        ("WeebCentral", "https://weebcentral.com", "MANGA"),
        ("MangaPark", "https://mangapark.net", "MANGA"),
        ("MangaSee / Manga4Life", "https://mangasee123.com", "MANGA"),
        ("MangaReader", "https://mangareader.to", "MANGA"),
        ("MangaHere", "https://www.mangahere.cc", "MANGA"),
        ("MangaFox (FanFox)", "https://fanfox.net", "MANGA"),
        ("MangaTown", "https://www.mangatown.com", "MANGA"),
        ("MangaRead", "https://www.mangaread.org", "MANGA"),
        ("MangaFreak", "https://mangafreak.me", "MANGA"),
        ("MangaTaro", "https://mangataro.org", "MANGA"),
        ("TCB Scans", "https://tcbonepiecechapters.com", "MANGA"),
        ("Dynasty Scans", "https://dynasty-scans.com", "MANGA"),
        ("Webtoons", "https://www.webtoons.com", "MANGA"),
        ("Naver Webtoon", "https://comic.naver.com", "MANGA"),
        ("Tapas", "https://tapas.io", "MANGA"),
        ("SenManga", "https://raw.senmanga.com", "MANGA"),
        ("RawKuma", "https://rawkuma.net", "MANGA"),
        ("KaliScan", "https://kaliscan.me", "MANGA"),
        ("KomikCast", "https://komikcast.li", "MANGA"),
        ("Danke fürs Lesen", "https://danke.moe", "MANGA"),
        ("Cubari / Imgur / Chest", "https://cubari.moe", "MANGA"),
    ]

    doujin_featured = [
        ("NHentai (.net / .xxx / 6-digit codes)", "https://nhentai.net", "DOUJIN"),
        ("HentaiVox", "https://hentaivox.com", "DOUJIN"),
        ("HentaiPaw", "https://hentaipaw.com", "DOUJIN"),
        ("RokuHentai", "https://rokuhentai.com", "DOUJIN"),
        ("IMHentai", "https://imhentai.xxx", "DOUJIN"),
        ("HentaiEra", "https://hentaiera.com", "DOUJIN"),
        ("HentaiFox", "https://hentaifox.com", "DOUJIN"),
        ("HentaiEnvy", "https://hentaienvy.com", "DOUJIN"),
        ("HentaiRox", "https://hentairox.com", "DOUJIN"),
        ("HentaiZap", "https://hentaizap.com", "DOUJIN"),
        ("AsmHentai", "https://asmhentai.com", "DOUJIN"),
        ("3Hentai", "https://3hentai.net", "DOUJIN"),
        ("E-Hentai / ExHentai", "https://e-hentai.org", "DOUJIN"),
        ("Hitomi.la", "https://hitomi.la", "DOUJIN"),
        ("Hentai2Read", "https://hentai2read.com", "DOUJIN"),
        ("HentaiHere", "https://hentaihere.com", "DOUJIN"),
        ("Simply Hentai", "https://www.simply-hentai.com", "DOUJIN"),
        ("Hiperdex", "https://hiperdex.com", "DOUJIN"),
        ("AllPornComic", "https://allporncomic.com", "DOUJIN"),
        ("Pixiv (Comics & Art)", "https://www.pixiv.net", "DOUJIN"),
    ]

    seen_keys = {
        name.lower().split()[0] for name, _, _ in (manga_featured + doujin_featured)
    }

    other_sites = {}
    for ext in extractor.extractors():
        cat = getattr(ext, "category", "")
        if not cat or cat.lower() in seen_keys:
            continue
        root = getattr(ext, "root", "") or ""
        if not root:
            ex = getattr(ext, "example", "")
            if ex and ex.startswith("http"):
                parsed = urllib.parse.urlparse(ex)
                root = f"{parsed.scheme}://{parsed.netloc}"
        if cat not in other_sites or (not other_sites[cat] and root):
            other_sites[cat] = root

    result = list(manga_featured) + list(doujin_featured)
    for cat in sorted(other_sites.keys(), key=lambda s: s.lower()):
        result.append(
            (cat.capitalize(), other_sites[cat] or "Supported Extractor", "OTHER")
        )
    return result


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Loads a clean UI font on both Windows and macOS with fallback."""
    if sys.platform == "darwin":
        mac_paths = (
            [
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/System/Library/Fonts/HelveticaNeue.ttc",
                "/System/Library/Fonts/SFNS.ttf",
            ]
            if bold
            else [
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/System/Library/Fonts/HelveticaNeue.ttc",
                "/System/Library/Fonts/SFNS.ttf",
            ]
        )
        for p in mac_paths:
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass

    candidates = (
        ["segoeuib.ttf", "arialbd.ttf", "calibrib.ttf"]
        if bold
        else ["segoeui.ttf", "arial.ttf", "calibri.ttf"]
    )
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for name in candidates:
        p = os.path.join(windir, "Fonts", name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


class MangaMaidApp:
    """Main 1280x720 (16:9) MangaMaid (Portable) Desktop Application."""

    WIN_W = 1280
    WIN_H = 720
    PLACEHOLDER = "Paste manga/doujin link or code (MangaDex, NHentai, HentaiVox, etc.)..."

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"MangaMaid (Portable) — {APP_VERSION}")
        self.root.resizable(False, False)

        # Center 1280x720 window on screen
        scr_w = self.root.winfo_screenwidth()
        scr_h = self.root.winfo_screenheight()
        x_pos = max(0, (scr_w - self.WIN_W) // 2)
        y_pos = max(0, (scr_h - self.WIN_H) // 2 - 20)
        self.root.geometry(f"{self.WIN_W}x{self.WIN_H}+{x_pos}+{y_pos}")

        # Set window icon (Windows .ico + cross-platform macOS iconphoto fallback)
        icon_path = resource_path(os.path.join("assets", "icon.ico"))
        mascot_icon_path = resource_path(os.path.join("assets", "mascot.png"))
        if sys.platform == "win32" and os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except Exception:
                pass
        elif os.path.exists(mascot_icon_path):
            try:
                icon_im = Image.open(mascot_icon_path).convert("RGBA").resize(
                    (256, 256), Image.Resampling.LANCZOS
                )
                self._app_icon_photo = ImageTk.PhotoImage(icon_im)
                self.root.iconphoto(True, self._app_icon_photo)
            except Exception:
                pass

        # Sound Manager (Chiptune BGM + Click MP3 + Completion Anime Wow MP3)
        self.sound = SoundManager(
            bgm_wav=resource_path(os.path.join("assets", "bgm.wav")),
            click_mp3=resource_path(os.path.join("assets", "sfx_click.mp3")),
            complete_mp3=resource_path(os.path.join("assets", "sfx_complete.mp3")),
        )

        # Log Capture Handler for friendly error dialogs
        self.log_capture = GUILogCaptureHandler()
        logging.getLogger().addHandler(self.log_capture)

        # State (Dynamically resolves the current user's Downloads folder on any PC)
        self.dest_dir = get_user_downloads_folder()
        self.last_manga_dir = self.dest_dir
        self.is_downloading = False
        self.pages_downloaded = 0
        self.cbz_created = 0
        self.total_chapters = 0
        self.current_chapter_idx = 0
        self.pages_in_current_chapter = 0
        self.expected_pages_in_chapter = 28
        self._sites_popup = None
        self._error_popup = None

        # Build composite background, sprites, and UI controls
        self._prepare_visuals()
        self._build_ui()

        # Start Maid Cafe pixel BGM automatically (unless running headless screenshot)
        if "--screenshot" not in sys.argv:
            self.root.after(250, self.sound.start_bgm)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self.sound.stop_bgm()
        self.root.destroy()

    def _prepare_visuals(self):
        """Composites the 16:9 Split-Column Layout."""
        bg_path = resource_path(os.path.join("assets", "bg_16x9.jpg"))
        if not os.path.exists(bg_path):
            bg_path = resource_path(os.path.join("assets", "bg.jpg"))
        logo_path = resource_path(os.path.join("assets", "logo.png"))
        mascot_path = resource_path(os.path.join("assets", "mascot.png"))

        bg = Image.open(bg_path).convert("RGBA").resize(
            (self.WIN_W, self.WIN_H), Image.Resampling.LANCZOS
        )

        # Drop shadows for cards & pills
        shadow_layer = Image.new("RGBA", (self.WIN_W, self.WIN_H), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow_layer)
        sdraw.rounded_rectangle((28, 22, 344, 68), radius=22, fill=(90, 20, 55, 55))
        sdraw.rounded_rectangle((134, 650, 484, 692), radius=20, fill=(90, 20, 55, 55))
        sdraw.rounded_rectangle((648, 434, 1250, 548), radius=24, fill=(90, 20, 55, 60))
        sdraw.rounded_rectangle((648, 561, 936, 599), radius=19, fill=(90, 20, 55, 55))
        sdraw.rounded_rectangle((648, 614, 1250, 706), radius=22, fill=(90, 20, 55, 60))
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(7))
        bg = Image.alpha_composite(bg, shadow_layer)

        # Frosted glass overlay
        overlay = Image.new("RGBA", (self.WIN_W, self.WIN_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # 1. Top-Left Audio Control Pill (BGM / SFX Toggles) (x: 24..340, y: 18..64)
        draw.rounded_rectangle(
            (24, 18, 340, 64),
            radius=23,
            fill=(255, 246, 251, 236),
            outline=(236, 72, 153, 220),
            width=2,
        )
        font_audio = load_font(13, bold=True)
        draw.text(
            (44, 41),
            "AUDIO",
            font=font_audio,
            fill=(157, 23, 77, 255),
            anchor="lm",
        )

        # 2. Bottom-Left Beta Version Badge under Big Logo (x: 130..480, y: 646..688)
        draw.rounded_rectangle(
            (130, 646, 480, 688),
            radius=21,
            fill=(255, 244, 250, 235),
            outline=(236, 72, 153, 215),
            width=2,
        )
        self._draw_sparkle(draw, 152, 667, 6, (236, 72, 153, 255))
        self._draw_sparkle(draw, 458, 667, 6, (236, 72, 153, 255))
        font_ver = load_font(14, bold=True)
        draw.text(
            (305, 667),
            f"MangaMaid (Portable)  •  {APP_VERSION}",
            font=font_ver,
            fill=(157, 23, 77, 255),
            anchor="mm",
        )

        # 3. Right Input Card (x: 644..1246, y: 428..542)
        draw.rounded_rectangle(
            (644, 428, 1246, 542),
            radius=24,
            fill=(255, 246, 251, 238),
            outline=(236, 72, 153, 225),
            width=3,
        )
        draw.rounded_rectangle(
            (648, 432, 1242, 538),
            radius=21,
            fill=None,
            outline=(255, 255, 255, 210),
            width=2,
        )

        # Row 1: MANGA URL Pill (x: 660..782, y: 442..480)
        draw.rounded_rectangle(
            (660, 442, 782, 480),
            radius=13,
            fill=(253, 226, 240, 255),
            outline=(244, 114, 182, 190),
            width=2,
        )
        # URL Input Box (x: 790..1046, y: 442..480)
        draw.rounded_rectangle(
            (790, 442, 1046, 480),
            radius=13,
            fill=(255, 255, 255, 255),
            outline=(244, 114, 182, 225),
            width=2,
        )

        # Row 2: SAVE FOLDER Pill (x: 660..782, y: 490..528)
        draw.rounded_rectangle(
            (660, 490, 782, 528),
            radius=13,
            fill=(253, 226, 240, 255),
            outline=(244, 114, 182, 190),
            width=2,
        )
        # Save Folder Path Box (x: 790..1046, y: 490..528)
        draw.rounded_rectangle(
            (790, 490, 1046, 528),
            radius=13,
            fill=(255, 250, 253, 255),
            outline=(244, 114, 182, 190),
            width=2,
        )

        font_lbl = load_font(13, bold=True)
        draw.text(
            (721, 461),
            "MANGA URL",
            font=font_lbl,
            fill=(157, 23, 77, 255),
            anchor="mm",
        )
        draw.text(
            (721, 509),
            "SAVE FOLDER",
            font=font_lbl,
            fill=(157, 23, 77, 255),
            anchor="mm",
        )

        # 4. Subtitle Pill on Left of Middle Row: "MangaMaid Downloader & Packager" (x: 644..934, y: 556..594)
        draw.rounded_rectangle(
            (644, 556, 934, 594),
            radius=19,
            fill=(255, 245, 250, 242),
            outline=(236, 72, 153, 220),
            width=2,
        )
        self._draw_sparkle(draw, 662, 575, 6, (236, 72, 153, 255))
        self._draw_sparkle(draw, 916, 575, 6, (236, 72, 153, 255))
        font_sub = load_font(13, bold=True)
        draw.text(
            (789, 575),
            "MangaMaid Downloader & Packager",
            font=font_sub,
            fill=(157, 23, 77, 255),
            anchor="mm",
        )

        # 5. Bottom Status & Overall Progress Card (x: 644..1246, y: 608..700)
        draw.rounded_rectangle(
            (644, 608, 1246, 700),
            radius=22,
            fill=(255, 246, 251, 240),
            outline=(236, 72, 153, 225),
            width=3,
        )
        # Progress Bar Track (x: 664..1100, y: 658..684)
        draw.rounded_rectangle(
            (664, 658, 1100, 684),
            radius=13,
            fill=(252, 228, 239, 255),
            outline=(244, 114, 182, 185),
            width=2,
        )

        bg = Image.alpha_composite(bg, overlay)

        # 6. Composite Large Manga Maid Logo on the Left Side
        logo_raw = Image.open(logo_path).convert("RGBA")
        logo_w = 576
        logo_h = int(logo_raw.height * (logo_w / logo_raw.width))
        logo_img = logo_raw.resize((logo_w, logo_h), Image.Resampling.LANCZOS)
        bg.alpha_composite(logo_img, (18, 92))

        self.bg_image = bg
        self.bg_photo = ImageTk.PhotoImage(bg)

        # 7. Prepare Mascot Character Sprites
        mascot_raw = Image.open(mascot_path).convert("RGBA")
        m_h = 416
        m_w = int(mascot_raw.width * (m_h / mascot_raw.height))
        self.mascot_base = mascot_raw.resize((m_w, m_h), Image.Resampling.LANCZOS)

        self.mascot_normal_im, self.mascot_normal_tk = self._build_mascot_char_sprite(
            scale=1.0, brightness=1.0
        )
        self.mascot_hover_im, self.mascot_hover_tk = self._build_mascot_char_sprite(
            scale=1.03, brightness=1.07
        )
        self.mascot_busy_im, self.mascot_busy_tk = self._build_mascot_char_sprite(
            scale=1.0, brightness=1.03
        )

        # 8. Prepare Action CTA Pill Sprites
        self.cta_normal_im, self.cta_normal_tk = self._build_cta_pill_sprite(
            text="Touch Me to Get Started!", mode="normal"
        )
        self.cta_hover_im, self.cta_hover_tk = self._build_cta_pill_sprite(
            text="Touch Me to Get Started!", mode="hover"
        )
        self.cta_busy_im, self.cta_busy_tk = self._build_cta_pill_sprite(
            text="Downloading Your Manga...", mode="busy"
        )

        # 9. Prepare Toggle Switch Sprites for BGM & SFX
        self.toggle_bgm_on_im, self.toggle_bgm_on_tk = self._build_toggle_sprite(
            "BGM", True
        )
        self.toggle_bgm_off_im, self.toggle_bgm_off_tk = self._build_toggle_sprite(
            "BGM", False
        )
        self.toggle_sfx_on_im, self.toggle_sfx_on_tk = self._build_toggle_sprite(
            "SFX", True
        )
        self.toggle_sfx_off_im, self.toggle_sfx_off_tk = self._build_toggle_sprite(
            "SFX", False
        )

    @staticmethod
    def _draw_sparkle(
        draw: ImageDraw.ImageDraw,
        cx: int,
        cy: int,
        r: int,
        color=(255, 235, 130, 255),
    ):
        """Draws a cute 4-point anime sparkle star at (cx, cy)."""
        ri = max(2, int(r * 0.28))
        pts = [
            (cx, cy - r),
            (cx + ri, cy - ri),
            (cx + r, cy),
            (cx + ri, cy + ri),
            (cx, cy + r),
            (cx - ri, cy + ri),
            (cx - r, cy),
            (cx - ri, cy - ri),
        ]
        draw.polygon(pts, fill=color)

    def _build_mascot_char_sprite(
        self, scale: float, brightness: float
    ) -> tuple[Image.Image, ImageTk.PhotoImage]:
        cw, ch = 380, 432
        canvas_im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        m_im = self.mascot_base
        if brightness != 1.0:
            rgb = m_im.convert("RGB")
            rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
            r, g, b = rgb.split()
            m_im = Image.merge("RGBA", (r, g, b, m_im.split()[3]))
        if scale != 1.0:
            nw = int(m_im.width * scale)
            nh = int(m_im.height * scale)
            m_im = m_im.resize((nw, nh), Image.Resampling.LANCZOS)
        mx = (cw - m_im.width) // 2
        my = (ch - m_im.height) // 2
        canvas_im.alpha_composite(m_im, (mx, my))
        return canvas_im, ImageTk.PhotoImage(canvas_im)

    def _build_cta_pill_sprite(
        self, text: str, mode: str
    ) -> tuple[Image.Image, ImageTk.PhotoImage]:
        cw, ch = 304, 44
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        draw = ImageDraw.Draw(im)

        draw.rounded_rectangle(
            (4, 5, cw - 2, ch - 1), radius=19, fill=(80, 10, 45, 80)
        )
        im = im.filter(ImageFilter.GaussianBlur(2))
        draw = ImageDraw.Draw(im)

        if mode == "busy":
            fill_col = (139, 92, 246, 250)
            inner_col = (221, 214, 254, 170)
        elif mode == "hover":
            fill_col = (236, 72, 153, 255)
            inner_col = (255, 205, 230, 180)
        else:
            fill_col = (219, 39, 119, 250)
            inner_col = (255, 192, 224, 160)

        draw.rounded_rectangle(
            (2, 2, cw - 4, ch - 4),
            radius=19,
            fill=fill_col,
            outline=(255, 255, 255, 250),
            width=2,
        )
        draw.rounded_rectangle(
            (5, 5, cw - 7, ch - 7),
            radius=16,
            fill=None,
            outline=inner_col,
            width=1,
        )

        cy = (ch - 2) // 2
        self._draw_sparkle(draw, 22, cy, 7, (255, 240, 150, 255))
        self._draw_sparkle(draw, 32, cy - 5, 4, (255, 255, 255, 235))
        self._draw_sparkle(draw, cw - 24, cy, 7, (255, 240, 150, 255))
        self._draw_sparkle(draw, cw - 34, cy - 5, 4, (255, 255, 255, 235))

        font_cta = load_font(14, bold=True)
        draw.text(
            (cw // 2 + 1, cy + 1),
            text,
            font=font_cta,
            fill=(95, 12, 55, 210),
            anchor="mm",
        )
        draw.text(
            (cw // 2, cy),
            text,
            font=font_cta,
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
        return im, ImageTk.PhotoImage(im)

    def _build_toggle_sprite(
        self, label: str, is_on: bool
    ) -> tuple[Image.Image, ImageTk.PhotoImage]:
        cw, ch = 108, 32
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        draw = ImageDraw.Draw(im)

        bg_col = (219, 39, 119, 255) if is_on else (229, 212, 222, 255)
        border_col = (190, 24, 93, 255) if is_on else (180, 160, 172, 255)
        txt_col = (255, 255, 255, 255) if is_on else (115, 95, 108, 255)

        draw.rounded_rectangle(
            (1, 1, cw - 2, ch - 2),
            radius=15,
            fill=bg_col,
            outline=border_col,
            width=2,
        )

        knob_r = 11
        knob_x = cw - 16 if is_on else 16
        knob_y = ch // 2
        draw.ellipse(
            (knob_x - knob_r, knob_y - knob_r, knob_x + knob_r, knob_y + knob_r),
            fill=(255, 255, 255, 255),
        )

        state_txt = f"{label}: ON" if is_on else f"{label}: OFF"
        text_x = (cw - 24) // 2 + 4 if is_on else (cw + 20) // 2 - 2
        font_t = load_font(11, bold=True)
        draw.text(
            (text_x, ch // 2),
            state_txt,
            font=font_t,
            fill=txt_col,
            anchor="mm",
        )
        return im, ImageTk.PhotoImage(im)

    def _build_ui(self):
        """Creates the 1280x720 canvas and embeds all interactive controls."""
        self.canvas = tk.Canvas(
            self.root,
            width=self.WIN_W,
            height=self.WIN_H,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self.bg_photo, anchor="nw")

        # 1. Top-Left BGM & SFX Toggle Switches
        self.bgm_toggle_item = self.canvas.create_image(
            156,
            41,
            image=self.toggle_bgm_on_tk,
            anchor="center",
            tags=("bgm_toggle",),
        )
        self.canvas.tag_bind("bgm_toggle", "<Button-1>", self._on_toggle_bgm)
        self.canvas.tag_bind(
            "bgm_toggle", "<Enter>", lambda e: self.canvas.config(cursor="hand2")
        )
        self.canvas.tag_bind(
            "bgm_toggle", "<Leave>", lambda e: self.canvas.config(cursor="")
        )

        self.sfx_toggle_item = self.canvas.create_image(
            274,
            41,
            image=self.toggle_sfx_on_tk,
            anchor="center",
            tags=("sfx_toggle",),
        )
        self.canvas.tag_bind("sfx_toggle", "<Button-1>", self._on_toggle_sfx)
        self.canvas.tag_bind(
            "sfx_toggle", "<Enter>", lambda e: self.canvas.config(cursor="hand2")
        )
        self.canvas.tag_bind(
            "sfx_toggle", "<Leave>", lambda e: self.canvas.config(cursor="")
        )

        # 2. Interactive Mascot Character (Top Right: center x=945, y=214)
        self.mascot_item = self.canvas.create_image(
            945,
            214,
            image=self.mascot_normal_tk,
            anchor="center",
            tags=("mascot_btn",),
        )
        # 3. Interactive CTA Action Pill (Middle Right: center x=1095, y=575)
        self.cta_item = self.canvas.create_image(
            1095,
            575,
            image=self.cta_normal_tk,
            anchor="center",
            tags=("mascot_btn",),
        )
        self.canvas.tag_bind("mascot_btn", "<Enter>", self._on_mascot_enter)
        self.canvas.tag_bind("mascot_btn", "<Leave>", self._on_mascot_leave)
        self.canvas.tag_bind("mascot_btn", "<Button-1>", self.on_mascot_click)

        # 4. URL Paste Entry Field (inside x: 790..1046, y: 442..480)
        self.url_var = tk.StringVar()
        self.url_entry = tk.Entry(
            self.root,
            textvariable=self.url_var,
            font=("Segoe UI", 10),
            bg="#FFFFFF",
            fg="#888888",
            insertbackground="#DB2777",
            relief="flat",
            bd=0,
        )
        self.url_entry.insert(0, self.PLACEHOLDER)
        self.url_entry.bind("<FocusIn>", self._on_url_focus_in)
        self.url_entry.bind("<FocusOut>", self._on_url_focus_out)
        self.url_entry.bind("<Return>", lambda e: self.on_mascot_click())
        self._setup_entry_context_menu()
        self.canvas.create_window(
            798, 461, window=self.url_entry, width=240, height=26, anchor="w"
        )

        # Paste Button (x: 1054..1118, y: 442..480)
        self.paste_btn = tk.Button(
            self.root,
            text="Paste",
            font=("Segoe UI", 9, "bold"),
            bg="#FCE7F3",
            fg="#9D174D",
            activebackground="#FBCFE8",
            activeforeground="#831843",
            relief="flat",
            bd=0,
            cursor="hand2",
            command=self._paste_from_clipboard,
        )
        self.canvas.create_window(
            1054, 461, window=self.paste_btn, width=64, height=34, anchor="w"
        )

        # Supported Sites Button (x: 1124..1232, y: 442..480)
        self.sites_btn = tk.Button(
            self.root,
            text="Supported Sites ▼",
            font=("Segoe UI", 9, "bold"),
            bg="#FDF2F8",
            fg="#BE185D",
            activebackground="#FCE7F3",
            activeforeground="#831843",
            relief="flat",
            bd=0,
            cursor="hand2",
            command=self.show_supported_sites_popup,
        )
        self.canvas.create_window(
            1124, 461, window=self.sites_btn, width=108, height=34, anchor="w"
        )

        # 5. Destination Path Label & Select Destination Button (Row 2)
        self.dest_label_id = self.canvas.create_text(
            800,
            509,
            text=self._truncate_path(self.dest_dir, 34),
            font=("Segoe UI", 10),
            fill="#831843",
            anchor="w",
        )

        self.dest_btn = tk.Button(
            self.root,
            text="Select Destination...",
            font=("Segoe UI", 10, "bold"),
            bg="#DB2777",
            fg="#FFFFFF",
            activebackground="#BE185D",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            cursor="hand2",
            command=self.choose_destination,
        )
        self.canvas.create_window(
            1054, 509, window=self.dest_btn, width=178, height=34, anchor="w"
        )

        # 6. Bottom Status & Overall Progress Bar Elements (inside x: 644..1246, y: 608..700)
        self.status_text_id = self.canvas.create_text(
            668,
            634,
            text="Ready! Touch the Manga Maid to start~",
            font=("Segoe UI", 10, "bold"),
            fill="#831843",
            anchor="w",
        )
        self.counter_text_id = self.canvas.create_text(
            1098,
            634,
            text="0% (0 CBZ | 0 pgs)",
            font=("Segoe UI", 10, "bold"),
            fill="#BE185D",
            anchor="e",
        )

        # Progress bar fill rectangle inside track (666, 660, 1098, 682)
        self.prog_fill_id = self.canvas.create_rectangle(
            666, 660, 666, 682, fill="#EC4899", outline="", width=0
        )

        # Open Folder Button in bottom right of status card (x: 1112..1232, y: 626..684)
        self.open_btn = tk.Button(
            self.root,
            text="Open Folder",
            font=("Segoe UI", 10, "bold"),
            bg="#FCE7F3",
            fg="#9D174D",
            activebackground="#FBCFE8",
            activeforeground="#831843",
            relief="flat",
            bd=0,
            cursor="hand2",
            command=self.open_destination_folder,
        )
        self.canvas.create_window(
            1112, 655, window=self.open_btn, width=118, height=54, anchor="w"
        )

    def _on_toggle_bgm(self, _event=None):
        new_state = not self.sound.bgm_enabled
        self.sound.set_bgm_enabled(new_state)
        self.canvas.itemconfig(
            self.bgm_toggle_item,
            image=self.toggle_bgm_on_tk if new_state else self.toggle_bgm_off_tk,
        )

    def _on_toggle_sfx(self, _event=None):
        new_state = not self.sound.sfx_enabled
        self.sound.set_sfx_enabled(new_state)
        self.canvas.itemconfig(
            self.sfx_toggle_item,
            image=self.toggle_sfx_on_tk if new_state else self.toggle_sfx_off_tk,
        )
        if new_state:
            self.sound.play_click()

    def show_error_popup(self, title: str, message: str, details: str = ""):
        """Displays a themed MangaMaid error/failure popup dialog so issues are immediately clear."""
        if self._error_popup is not None and self._error_popup.winfo_exists():
            try:
                self._error_popup.destroy()
            except Exception:
                pass

        popup = tk.Toplevel(self.root)
        popup.title(f"MangaMaid — {title}")
        popup.configure(bg="#FFF5F9")
        popup.resizable(False, False)
        popup.transient(self.root)
        self._error_popup = popup

        pw, ph = 500, 310 if details else 240
        rx = self.root.winfo_rootx() + (self.WIN_W - pw) // 2
        ry = self.root.winfo_rooty() + (self.WIN_H - ph) // 2
        popup.geometry(f"{pw}x{ph}+{rx}+{ry}")

        icon_path = resource_path(os.path.join("assets", "icon.ico"))
        if os.path.exists(icon_path):
            try:
                popup.iconbitmap(icon_path)
            except Exception:
                pass

        hdr = tk.Frame(popup, bg="#BE185D", height=48)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=f"Oops! {title}",
            font=("Segoe UI", 12, "bold"),
            bg="#BE185D",
            fg="#FFFFFF",
            pady=10,
        ).pack()

        body = tk.Frame(popup, bg="#FFF5F9", padx=20, pady=14)
        body.pack(fill="both", expand=True)

        tk.Label(
            body,
            text=message,
            font=("Segoe UI", 10, "bold"),
            bg="#FFF5F9",
            fg="#831843",
            wraplength=455,
            justify="left",
        ).pack(anchor="w")

        if details:
            det_box = tk.Text(
                body,
                height=4,
                font=("Consolas", 9),
                bg="#FFFFFF",
                fg="#4B5563",
                relief="solid",
                bd=1,
                wrap="word",
            )
            det_box.insert("1.0", details)
            det_box.config(state="disabled")
            det_box.pack(fill="x", pady=(10, 0))

        ftr = tk.Frame(popup, bg="#FFF5F9", padx=20, pady=12)
        ftr.pack(fill="x")
        tk.Button(
            ftr,
            text="View Supported Sites",
            font=("Segoe UI", 9, "bold"),
            bg="#FCE7F3",
            fg="#9D174D",
            activebackground="#FBCFE8",
            activeforeground="#831843",
            relief="flat",
            padx=12,
            pady=5,
            cursor="hand2",
            command=lambda: (popup.destroy(), self.show_supported_sites_popup()),
        ).pack(side="left")

        tk.Button(
            ftr,
            text="OK, Got It",
            font=("Segoe UI", 10, "bold"),
            bg="#DB2777",
            fg="#FFFFFF",
            activebackground="#BE185D",
            activeforeground="#FFFFFF",
            relief="flat",
            padx=20,
            pady=5,
            cursor="hand2",
            command=popup.destroy,
        ).pack(side="right")

        popup.lift()
        popup.focus_force()

    def show_supported_sites_popup(self):
        """Opens a scrollable, searchable popup window listing all supported manga, doujin, & hentai sites."""
        if self._sites_popup is not None and self._sites_popup.winfo_exists():
            self._sites_popup.lift()
            self._sites_popup.focus_force()
            return

        popup = tk.Toplevel(self.root)
        popup.title(
            f"MangaMaid ({APP_VERSION}) — Supported Manga, Doujin & Hentai Sites"
        )
        popup.configure(bg="#FFF5F9")
        popup.resizable(False, False)
        self._sites_popup = popup

        pw, ph = 580, 540
        rx = self.root.winfo_rootx() + (self.WIN_W - pw) // 2
        ry = self.root.winfo_rooty() + (self.WIN_H - ph) // 2
        popup.geometry(f"{pw}x{ph}+{rx}+{ry}")

        icon_path = resource_path(os.path.join("assets", "icon.ico"))
        if os.path.exists(icon_path):
            try:
                popup.iconbitmap(icon_path)
            except Exception:
                pass

        hdr = tk.Frame(popup, bg="#DB2777", height=54)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="Supported Manga, Doujin & Hentai Sites",
            font=("Segoe UI", 13, "bold"),
            bg="#DB2777",
            fg="#FFFFFF",
            pady=12,
        ).pack()

        search_frame = tk.Frame(popup, bg="#FFF5F9", padx=16, pady=10)
        search_frame.pack(fill="x")
        tk.Label(
            search_frame,
            text="Search Sites:",
            font=("Segoe UI", 10, "bold"),
            bg="#FFF5F9",
            fg="#9D174D",
        ).pack(side="left")

        search_var = tk.StringVar()
        search_entry = tk.Entry(
            search_frame,
            textvariable=search_var,
            font=("Segoe UI", 10),
            bg="#FFFFFF",
            fg="#1F2937",
            relief="solid",
            bd=1,
        )
        search_entry.pack(side="left", fill="x", expand=True, padx=(10, 0), ipady=4)

        list_frame = tk.Frame(popup, bg="#FFF5F9", padx=16, pady=4)
        list_frame.pack(fill="both", expand=True)

        scrollbar = tk.Scrollbar(list_frame, orient="vertical")
        listbox = tk.Listbox(
            list_frame,
            font=("Segoe UI", 10),
            bg="#FFFFFF",
            fg="#374151",
            selectbackground="#FCE7F3",
            selectforeground="#9D174D",
            activestyle="none",
            relief="solid",
            bd=1,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=listbox.yview)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        all_sites = get_supported_sites_list()

        def _populate(filter_str: str = ""):
            listbox.delete(0, tk.END)
            q = filter_str.strip().lower()
            manga_added = False
            doujin_added = False
            others_added = False
            for name, domain, group in all_sites:
                if q and (q not in name.lower() and q not in domain.lower()):
                    continue
                if group == "MANGA" and not manga_added:
                    listbox.insert(tk.END, "  ★ POPULAR MANGA & WEBTOON SITES ★")
                    listbox.itemconfig(listbox.size() - 1, bg="#FCE7F3", fg="#9D174D")
                    manga_added = True
                elif group == "DOUJIN" and not doujin_added:
                    if manga_added:
                        listbox.insert(tk.END, "")
                    listbox.insert(tk.END, "  ★ POPULAR DOUJIN & HENTAI SITES ★")
                    listbox.itemconfig(listbox.size() - 1, bg="#FCE7F3", fg="#9D174D")
                    doujin_added = True
                elif group == "OTHER" and not others_added:
                    if manga_added or doujin_added:
                        listbox.insert(tk.END, "")
                    listbox.insert(
                        tk.END,
                        f"  ★ ALL OTHER SUPPORTED EXTRACTORS ({len(all_sites)}+ TOTAL) ★",
                    )
                    listbox.itemconfig(listbox.size() - 1, bg="#FCE7F3", fg="#9D174D")
                    others_added = True

                disp = f"   •  {name}   —   {domain}" if domain else f"   •  {name}"
                listbox.insert(tk.END, disp)

        _populate()
        search_var.trace_add("write", lambda *_: _populate(search_var.get()))

        ftr = tk.Frame(popup, bg="#FFF5F9", padx=16, pady=10)
        ftr.pack(fill="x")
        tk.Label(
            ftr,
            text="Tip: Paste any URL, manga title, or 6-digit NHentai code!",
            font=("Segoe UI", 9, "italic"),
            bg="#FFF5F9",
            fg="#831843",
        ).pack(side="left")
        tk.Button(
            ftr,
            text="Close",
            font=("Segoe UI", 10, "bold"),
            bg="#DB2777",
            fg="#FFFFFF",
            activebackground="#BE185D",
            activeforeground="#FFFFFF",
            relief="flat",
            padx=16,
            pady=4,
            cursor="hand2",
            command=popup.destroy,
        ).pack(side="right")

        search_entry.focus_set()

    def _setup_entry_context_menu(self):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Paste", command=self._paste_from_clipboard)
        menu.add_command(
            label="Clear",
            command=lambda: (
                self.url_entry.delete(0, tk.END),
                self.url_entry.config(fg="#1F2937"),
            ),
        )
        self.url_entry.bind(
            "<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root)
        )
        self.url_entry.bind(
            "<Button-2>", lambda e: menu.tk_popup(e.x_root, e.y_root)
        )
        self.url_entry.bind(
            "<Command-v>", lambda e: (self._paste_from_clipboard(), "break")[1]
        )

    def _on_url_focus_in(self, _event=None):
        if self.url_var.get() == self.PLACEHOLDER:
            self.url_entry.delete(0, tk.END)
            self.url_entry.config(fg="#1F2937")

    def _on_url_focus_out(self, _event=None):
        if not self.url_var.get().strip():
            self.url_entry.insert(0, self.PLACEHOLDER)
            self.url_entry.config(fg="#888888")

    def _paste_from_clipboard(self):
        try:
            clip = self.root.clipboard_get().strip()
            if clip:
                self.url_entry.delete(0, tk.END)
                self.url_entry.config(fg="#1F2937")
                self.url_entry.insert(0, clip)
        except Exception:
            pass

    def _truncate_path(self, path: str, max_chars: int = 34) -> str:
        if len(path) <= max_chars:
            return path
        return "..." + path[-(max_chars - 3) :]

    def choose_destination(self):
        selected = filedialog.askdirectory(
            parent=self.root,
            initialdir=self.dest_dir,
            title="Select Manga Download Destination Folder",
        )
        if selected:
            self.dest_dir = os.path.normpath(selected)
            self.last_manga_dir = self.dest_dir
            self.canvas.itemconfig(
                self.dest_label_id, text=self._truncate_path(self.dest_dir, 34)
            )

    def open_destination_folder(self):
        target = (
            self.last_manga_dir
            if os.path.exists(self.last_manga_dir)
            else self.dest_dir
        )
        if os.path.exists(target):
            if hasattr(os, "startfile"):
                os.startfile(target)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])

    def _on_mascot_enter(self, _event=None):
        self.canvas.config(cursor="hand2")
        if not self.is_downloading:
            self.canvas.itemconfig(self.mascot_item, image=self.mascot_hover_tk)
            self.canvas.itemconfig(self.cta_item, image=self.cta_hover_tk)

    def _on_mascot_leave(self, _event=None):
        self.canvas.config(cursor="")
        if not self.is_downloading:
            self.canvas.itemconfig(self.mascot_item, image=self.mascot_normal_tk)
            self.canvas.itemconfig(self.cta_item, image=self.cta_normal_tk)

    def set_status(self, text: str):
        """Sets the left-side status message, strictly capped at 36 chars so it never touches the right counter."""
        clean = re.sub(r"\s+", " ", text).strip()
        if len(clean) > 36:
            clean = clean[:33] + "..."
        self.root.after(
            0, lambda: self.canvas.itemconfig(self.status_text_id, text=clean)
        )

    def set_progress(self, fraction: float, counter_str: str | None = None):
        """Updates the overall progress bar (monotonic 0.0 -> 1.0) and right-aligned counter label."""
        fraction = max(0.0, min(1.0, fraction))
        x_end = int(666 + (1098 - 666) * fraction)

        def _upd():
            self.canvas.coords(self.prog_fill_id, 666, 660, x_end, 682)
            if counter_str is not None:
                self.canvas.itemconfig(self.counter_text_id, text=counter_str)

        self.root.after(0, _upd)

    def on_mascot_click(self, _event=None):
        """Triggered when the user touches the Manga Maid mascot or CTA button."""
        self.sound.play_click()

        if self.is_downloading:
            self.set_status("Already downloading! Please wait~")
            return

        raw_url = self.url_var.get().strip()
        if not raw_url or raw_url == self.PLACEHOLDER:
            try:
                clip = self.root.clipboard_get().strip()
                if clip.startswith(("http://", "https://")) or re.fullmatch(
                    r"#?\d{5,6}", clip
                ):
                    self._paste_from_clipboard()
                    raw_url = clip
            except Exception:
                pass

        if not raw_url or raw_url == self.PLACEHOLDER:
            self.set_status("Paste a Manga URL above first!")
            self.url_entry.focus_set()
            self.show_error_popup(
                "Missing Manga / Doujin URL",
                "Please paste a manga/doujin link, title, or 6-digit code into the 'MANGA URL' box before touching the Manga Maid!",
            )
            return

        # Reset job counters and start worker thread
        self.is_downloading = True
        self.pages_downloaded = 0
        self.cbz_created = 0
        self.total_chapters = 0
        self.current_chapter_idx = 0
        self.pages_in_current_chapter = 0
        self.expected_pages_in_chapter = 28
        self._max_progress = 0.02
        self.log_capture.clear()

        self.canvas.itemconfig(self.mascot_item, image=self.mascot_busy_tk)
        self.canvas.itemconfig(self.cta_item, image=self.cta_busy_tk)
        self.set_progress(0.02, "2% (Starting...)")
        self.set_status("Connecting to source...")

        threading.Thread(
            target=self._download_worker,
            args=(raw_url, self.dest_dir),
            daemon=True,
        ).start()

    def _update_overall_progress(self):
        """Computes smooth, strictly non-decreasing overall job progress from 0% to 100%."""
        denom = max(10.0, float(self.expected_pages_in_chapter))
        if self.total_chapters > 0:
            completed_ch = max(0, self.current_chapter_idx - 1, self.cbz_created)
            ch_partial = min(0.94, self.pages_in_current_chapter / denom)
            raw_frac = 0.05 + 0.93 * (
                (completed_ch + ch_partial) / float(self.total_chapters)
            )
        else:
            raw_frac = min(0.92, 0.05 + (self.pages_downloaded / denom) * 0.90)

        self._max_progress = max(
            getattr(self, "_max_progress", 0.0), min(0.99, raw_frac)
        )
        pct = int(round(self._max_progress * 100))

        if self.total_chapters > 0:
            counter = (
                f"{pct}% ({self.cbz_created}/{self.total_chapters} CBZ | "
                f"{self.pages_downloaded} pgs)"
            )
        else:
            counter = f"{pct}% ({self.cbz_created} CBZ | {self.pages_downloaded} pgs)"

        self.set_progress(self._max_progress, counter)

    def _on_total_chapters(self, total: int):
        self.total_chapters = max(1, total)
        self.set_status(f"Found {self.total_chapters} chapters/galleries!")
        self._update_overall_progress()

    def _on_expected_pages(self, page_count: int):
        if page_count > 0:
            self.expected_pages_in_chapter = page_count

    def _on_chapter_start(self, idx: int, total: int, kwdict: dict):
        self.current_chapter_idx = idx
        self.total_chapters = max(1, total)
        self.pages_in_current_chapter = 0
        self.expected_pages_in_chapter = int(kwdict.get("count") or 28)
        ch_num = kwdict.get("chapter", idx)
        self.set_status(f"Downloading [{idx}/{total}]: Ch.{ch_num}")
        self._update_overall_progress()

    def _run_custom_gallery_download(
        self,
        source_name: str,
        clean_title: str,
        pages: list[tuple[int, str, str]],
        referer_url: str,
        dest_dir: str,
    ):
        """Downloads all pages from our custom Doujin/Hentai extractor in parallel and packs into .cbz."""
        total_pages = len(pages)
        self.total_chapters = 1
        self.current_chapter_idx = 1
        self.expected_pages_in_chapter = max(1, total_pages)

        folder_name = sanitize_filename(clean_title)
        gallery_dir = os.path.join(dest_dir, folder_name)
        os.makedirs(gallery_dir, exist_ok=True)
        self.last_manga_dir = gallery_dir
        cbz_path = os.path.join(gallery_dir, f"{folder_name}.cbz")

        self.set_status(f"{source_name}: {total_pages} pages found!")
        self._update_overall_progress()

        session = requests.Session()
        session.headers.update({"User-Agent": UA_STRING, "Referer": referer_url})

        def _fetch_one(item: tuple[int, str, str]):
            p_num, ext, img_url = item
            local_name = f"{p_num:03d}.{ext}"
            local_path = os.path.join(gallery_dir, local_name)
            if os.path.exists(local_path) and os.path.getsize(local_path) > 500:
                return p_num, local_name, local_path, True

            for attempt in range(4):
                try:
                    resp = session.get(img_url, timeout=25)
                    if resp.status_code == 200 and len(resp.content) > 400:
                        with open(local_path, "wb") as f:
                            f.write(resp.content)
                        return p_num, local_name, local_path, False
                except Exception:
                    time.sleep(0.8 * (attempt + 1))
            raise RuntimeError(f"Failed to download page {p_num} from {source_name}")

        downloaded_files = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_one, item): item for item in pages}
            for fut in concurrent.futures.as_completed(futures):
                p_num, local_name, local_path, skipped = fut.result()
                downloaded_files.append((p_num, local_name, local_path))
                self.pages_downloaded += 1
                self.pages_in_current_chapter += 1
                self._update_overall_progress()
                action = "Skipped" if skipped else "Downloading"
                self.set_status(
                    f"{action} [1/1]: Page {self.pages_downloaded}/{total_pages}"
                )

        downloaded_files.sort(key=lambda x: x[0])
        self.set_status("Packing pages into CBZ archive...")

        with zipfile.ZipFile(cbz_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for _, local_name, local_path in downloaded_files:
                zf.write(local_path, arcname=local_name)

        for _, _, local_path in downloaded_files:
            try:
                os.remove(local_path)
            except Exception:
                pass

        self._on_cbz_finalized(cbz_path)

    def _download_worker(self, raw_url: str, dest_dir: str):
        try:
            # 1. First check if our Universal Doujin/Gallery Engine handles this URL directly
            custom_match = extract_custom_gallery_pages(
                raw_url.strip(), status_cb=self.set_status
            )
            if custom_match is not None:
                source_name, clean_title, pages, referer_url = custom_match
                self._run_custom_gallery_download(
                    source_name, clean_title, pages, referer_url, dest_dir
                )
                self.set_progress(
                    1.0,
                    f"100% (1/1 CBZ | {self.pages_downloaded} pgs)",
                )
                self.set_status("Complete! Packed 1 CBZ archive")
                self.sound.play_complete()
                return

            # 2. Otherwise resolve via standard Manga / Gallery-dl pipeline
            resolved_url, source_note = resolve_manga_url(
                raw_url, status_cb=self.set_status
            )

            # Check again in case resolve_manga_url turned a 6-digit code or redirect into a custom gallery URL
            custom_match = extract_custom_gallery_pages(
                resolved_url, status_cb=self.set_status
            )
            if custom_match is not None:
                source_name, clean_title, pages, referer_url = custom_match
                self._run_custom_gallery_download(
                    source_name, clean_title, pages, referer_url, dest_dir
                )
                self.set_progress(
                    1.0,
                    f"100% (1/1 CBZ | {self.pages_downloaded} pgs)",
                )
                self.set_status("Complete! Packed 1 CBZ archive")
                self.sound.play_complete()
                return

            self.set_status(f"Scanning {source_note} chapters...")
            self._max_progress = 0.05
            self.set_progress(0.05, "5% (Scanning...)")

            # Configure gallery-dl for both multi-chapter manga AND single-gallery doujin sites
            config.clear()
            config.set((), "base-directory", dest_dir)
            config.set(
                ("extractor",),
                "directory",
                [
                    "{manga}",
                    "{volume:?v/ />02}{chapter:?c//>03}{chapter_minor:?//}{title:? - //}",
                ],
            )
            config.set(
                ("extractor",),
                "filename",
                "{manga}_{chapter:?c//>03}{chapter_minor:?//}_{page:>03}.{extension}",
            )
            config.set((), "lang", "en")
            config.set((), "chapter-reverse", False)
            # Only apply English child-filter on multi-language manga sites like MangaDex/MangaFire
            if any(
                k in resolved_url.lower()
                for k in ("mangadex.org", "mangafire.to", "weebcentral.com", "mangapark")
            ):
                config.set((), "child-filter", "lang in ('en', 'eng', None, '')")

            config.setg(
                "postprocessors",
                [
                    {
                        "name": "zip",
                        "extension": "cbz",
                        "compression": "store",
                        "keep-files": False,
                    }
                ],
            )

            orig_select = output.select
            gui_out = GUIOutput(self._on_page_event)
            output.select = lambda *a, **kw: gui_out

            orig_finalize = pp_zip.ZipPP.finalize

            def tracked_finalize(zself, pathfmt):
                orig_finalize(zself, pathfmt)
                self._on_cbz_finalized(zself.path + ".cbz")

            pp_zip.ZipPP.finalize = tracked_finalize

            try:
                dl_job = MangaMaidDownloadJob(
                    resolved_url,
                    on_total_chapters=self._on_total_chapters,
                    on_chapter_start=self._on_chapter_start,
                    on_expected_pages=self._on_expected_pages,
                )
                status = dl_job.run()
            finally:
                output.select = orig_select
                pp_zip.ZipPP.finalize = orig_finalize

            if self.cbz_created > 0 or self.pages_downloaded > 0:
                total_ch = max(self.total_chapters, self.cbz_created)
                self.set_progress(
                    1.0,
                    f"100% ({self.cbz_created}/{total_ch} CBZ | {self.pages_downloaded} pgs)",
                )
                self.set_status(f"Complete! Packed {self.cbz_created} CBZ files")
                self.sound.play_complete()
            elif status != 0 or self.log_capture.errors:
                self.set_progress(0.0, "Error")
                self.set_status("Download failed — see error popup")
                err_details = "\n".join(self.log_capture.errors[-4:])
                self.root.after(
                    0,
                    lambda: self.show_error_popup(
                        "Download Failed",
                        f"MangaMaid encountered an error while trying to download from {source_note}. "
                        "No chapters or pages could be retrieved.",
                        details=err_details or f"Source URL: {resolved_url}",
                    ),
                )
            elif self.total_chapters == 0:
                self.set_progress(0.0, "0 CBZ found")
                self.set_status("No chapters found!")
                self.root.after(
                    0,
                    lambda: self.show_error_popup(
                        "No Chapters Found",
                        f"We connected to {source_note}, but no downloadable chapters or pages were found for this link.",
                        details=f"Resolved URL: {resolved_url}",
                    ),
                )
            else:
                self.set_progress(1.0, "100% (Up to date!)")
                self.set_status("All chapters already downloaded!")
                self.sound.play_complete()

        except Exception as exc:
            self.set_progress(0.0, "Failed")
            self.set_status("Error — see popup for details")
            err_msg = str(exc)
            err_log = "\n".join(self.log_capture.errors[-4:])
            self.root.after(
                0,
                lambda: self.show_error_popup(
                    "Unable to Download Manga / Doujin",
                    err_msg,
                    details=err_log,
                ),
            )
        finally:
            self.is_downloading = False
            self.root.after(
                0,
                lambda: (
                    self.canvas.itemconfig(
                        self.mascot_item, image=self.mascot_normal_tk
                    ),
                    self.canvas.itemconfig(self.cta_item, image=self.cta_normal_tk),
                ),
            )

    def _on_page_event(self, path: str, skipped: bool = False):
        self.pages_downloaded += 1
        self.pages_in_current_chapter += 1
        parent_dir = os.path.dirname(path)
        manga_dir = os.path.dirname(parent_dir)
        if os.path.isdir(manga_dir):
            self.last_manga_dir = manga_dir
        ch_name = os.path.basename(parent_dir)
        short_ch = ch_name.split(" - ")[0].strip()

        self._update_overall_progress()
        action = "Skipped" if skipped else "Downloading"
        if self.total_chapters > 0:
            self.set_status(
                f"{action} [{max(1, self.current_chapter_idx)}/{self.total_chapters}]: {short_ch} (p.{self.pages_in_current_chapter})"
            )
        else:
            self.set_status(
                f"{action}: {short_ch} (p.{self.pages_in_current_chapter})"
            )

    def _on_cbz_finalized(self, cbz_path: str):
        if os.path.exists(cbz_path):
            self.cbz_created += 1
            self.last_manga_dir = os.path.dirname(cbz_path)
            cbz_name = os.path.basename(cbz_path)
            short_cbz = cbz_name.split(" - ")[0].strip()
            if not short_cbz.endswith(".cbz"):
                short_cbz += ".cbz"
            self._update_overall_progress()
            if self.total_chapters > 0:
                self.set_status(
                    f"Packed [{self.cbz_created}/{self.total_chapters}]: {short_cbz}"
                )
            else:
                self.set_status(f"Packed #{self.cbz_created}: {short_cbz}")

    def render_preview(self, out_file: str):
        """Renders an exact 1280x720 preview of the full split-column UI."""
        im = self.bg_image.copy()
        draw = ImageDraw.Draw(im)

        # 1. Top-left BGM & SFX Toggle switches
        tw, th = self.toggle_bgm_on_im.size
        im.alpha_composite(self.toggle_bgm_on_im, (156 - tw // 2, 41 - th // 2))
        im.alpha_composite(self.toggle_sfx_on_im, (274 - tw // 2, 41 - th // 2))

        # 2. Top-Right Mascot Character
        mw, mh = self.mascot_normal_im.size
        im.alpha_composite(self.mascot_normal_im, (945 - mw // 2, 214 - mh // 2))

        # 3. Middle-Right CTA Action Pill
        cw, ch = self.cta_normal_im.size
        im.alpha_composite(self.cta_normal_im, (1095 - cw // 2, 575 - ch // 2))

        # 4. Input Card texts & buttons
        font_entry = load_font(13, bold=False)
        draw.text(
            (800, 461),
            "Paste manga or doujin link here...",
            font=font_entry,
            fill=(140, 140, 140, 255),
            anchor="lm",
        )

        # Paste button (1054..1118, 444..478)
        draw.rounded_rectangle(
            (1054, 444, 1118, 478),
            radius=9,
            fill=(252, 231, 243, 255),
            outline=(244, 114, 182, 180),
            width=1,
        )
        font_btn_sm = load_font(12, bold=True)
        draw.text(
            (1086, 461), "Paste", font=font_btn_sm, fill=(157, 23, 77, 255), anchor="mm"
        )

        # Supported Sites button (1124..1232, 444..478)
        draw.rounded_rectangle(
            (1124, 444, 1232, 478),
            radius=9,
            fill=(253, 242, 248, 255),
            outline=(244, 114, 182, 200),
            width=1,
        )
        draw.text(
            (1178, 461),
            "Supported Sites ▼",
            font=font_btn_sm,
            fill=(190, 24, 93, 255),
            anchor="mm",
        )

        # Destination path text (generic path when generating public screenshot)
        preview_dest = self.dest_dir
        if "--screenshot" in sys.argv:
            preview_dest = r"C:\Users\User\Downloads"
        draw.text(
            (800, 509),
            self._truncate_path(preview_dest, 34),
            font=font_entry,
            fill=(131, 24, 67, 255),
            anchor="lm",
        )

        # Select Destination button (1054..1232, 492..526)
        draw.rounded_rectangle(
            (1054, 492, 1232, 526),
            radius=9,
            fill=(219, 39, 119, 255),
        )
        font_btn = load_font(13, bold=True)
        draw.text(
            (1143, 509),
            "Select Destination...",
            font=font_btn,
            fill=(255, 255, 255, 255),
            anchor="mm",
        )

        # 5. Bottom Status & Progress bar preview
        draw.text(
            (668, 634),
            "Ready! Touch the Manga Maid to start~",
            font=load_font(13, bold=True),
            fill=(131, 24, 67, 255),
            anchor="lm",
        )
        draw.text(
            (1098, 634),
            "0% (0 CBZ | 0 pgs)",
            font=load_font(13, bold=True),
            fill=(190, 24, 93, 255),
            anchor="rm",
        )

        # Open Folder button (1112..1230, 628..682)
        draw.rounded_rectangle(
            (1112, 628, 1230, 682),
            radius=11,
            fill=(252, 231, 243, 255),
            outline=(244, 114, 182, 180),
            width=1,
        )
        draw.text(
            (1171, 655),
            "Open Folder",
            font=font_btn,
            fill=(157, 23, 77, 255),
            anchor="mm",
        )

        im.convert("RGB").save(out_file, quality=95)


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    app = MangaMaidApp(root)

    if "--screenshot" in sys.argv:
        idx = sys.argv.index("--screenshot")
        out_file = sys.argv[idx + 1]

        def _capture():
            root.update_idletasks()
            root.update()
            app.render_preview(out_file)
            root.destroy()

        root.after(200, _capture)

    root.mainloop()


if __name__ == "__main__":
    main()
