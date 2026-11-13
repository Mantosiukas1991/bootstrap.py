"""Headless-browser helpers for share pages that tools like yt-dlp can't read (Frame.io, MediaSilo, Notion...)."""

from __future__ import annotations

import os
import re
from pathlib import Path

MEDIA_EXT = re.compile(r"\.(mp4|mov|m4v|webm|mkv|png|jpe?g|webp|zip)(\?|$)", re.I)


def _launch(p, headless: bool = True):
    exe = os.environ.get("CLIPPER_CHROMIUM")  # optional path to an existing Chromium
    return p.chromium.launch(headless=headless, executable_path=exe or None)


def render_page(url: str, wait_ms: int = 3000) -> tuple[str, str]:
    """Return (html, final_url) after JavaScript has run."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(wait_ms)
        html, final = page.content(), page.url
        browser.close()
    return html, final


def _unique(dest: Path, name: str) -> Path:
    path = dest / name
    i = 2
    while path.exists():
        path = dest / f"{Path(name).stem}-{i}{Path(name).suffix}"
        i += 1
    return path


def browser_download(url: str, dest: Path, interactive: bool = False, timeout_s: int = 900) -> list[Path]:
    """Open a share page and save the files it offers.

    1. Click every per-file "Download" button (falls back to "Download all").
    2. If nothing downloaded, save video/image/zip files the page streamed.
    3. If still nothing and interactive=True, open a visible window and save whatever you download by hand.
    """
    from playwright.sync_api import sync_playwright

    dest.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    def on_download(d):
        target = _unique(dest, d.suggested_filename or "download.bin")
        d.save_as(str(target))
        saved.append(target)
        print(f"      saved {target.name}")

    with sync_playwright() as p:
        browser = _launch(p)
        ctx = browser.new_context(accept_downloads=True, user_agent=None)
        page = ctx.new_page()
        streamed: dict[str, int] = {}

        def on_response(r):
            ct = r.headers.get("content-type", "")
            size = int(r.headers.get("content-length", "0") or 0)
            if (ct.startswith(("video/", "application/zip")) or MEDIA_EXT.search(r.url)) and size > 200_000:
                streamed[r.url] = max(size, streamed.get(r.url, 0))

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
        except Exception:
            pass  # analytics requests often never settle; the page is usually usable anyway
        page.wait_for_timeout(3000)

        buttons = page.get_by_role("button", name=re.compile(r"download", re.I))
        links = page.get_by_role("link", name=re.compile(r"download", re.I))
        candidates = [buttons.nth(i) for i in range(buttons.count())] + [links.nth(i) for i in range(links.count())]
        singles = [c for c in candidates if not re.search(r"all", c.inner_text() or c.get_attribute("aria-label") or "", re.I)]
        for c in singles or candidates[:1]:
            try:
                with page.expect_download(timeout=30000) as info:
                    c.click()
                on_download(info.value)
            except Exception:
                # some pages open a menu first; try the first menu item that mentions download/original
                try:
                    item = page.get_by_role("menuitem", name=re.compile(r"download|original", re.I)).first
                    with page.expect_download(timeout=30000) as info:
                        item.click()
                    on_download(info.value)
                except Exception:
                    continue

        if not saved and streamed:
            for media_url, _ in sorted(streamed.items(), key=lambda kv: -kv[1])[:10]:
                name = Path(media_url.split("?")[0]).name or "media.bin"
                target = _unique(dest, name)
                resp = ctx.request.get(media_url, timeout=600000)
                if resp.ok:
                    target.write_bytes(resp.body())
                    saved.append(target)
                    print(f"      saved streamed file {target.name}")
        browser.close()

    if not saved and interactive:
        saved += _manual_download(url, dest, timeout_s)
    return saved


def _manual_download(url: str, dest: Path, timeout_s: int) -> list[Path]:
    from playwright.sync_api import sync_playwright

    print("      Couldn't download automatically. A browser window is opening:")
    print("      download the files there (log in if needed), then close the window.")
    saved: list[Path] = []
    with sync_playwright() as p:
        browser = _launch(p, headless=False)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        def on_download(d):
            target = _unique(dest, d.suggested_filename or "download.bin")
            d.save_as(str(target))
            saved.append(target)
            print(f"      saved {target.name}")

        ctx.on("page", lambda pg: pg.on("download", on_download))
        page.on("download", on_download)
        page.goto(url)
        try:
            page.wait_for_event("close", timeout=timeout_s * 1000)
        except Exception:
            pass
        browser.close()
    return saved
