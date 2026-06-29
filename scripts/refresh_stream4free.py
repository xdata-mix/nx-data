#!/usr/bin/env python3
"""
refresh_stream4free.py - Scraper 100% dynamique pour stream4free.tv

Decouvre tous les items sur /tv-live-france (page JS-rendered) via Playwright,
puis emet des URLs `stream4free://<slug>` dans le m3u.

L'app resout les m3u8 AU MOMENT DE LA LECTURE via Stream4FreeResolver
(fetch HTML de la page + extraction du <source> tag).
Meme pattern que plex://, plutolive://, francetv://program/.

Zero liste hardcodee. Les items sont decouverts a chaque execution.
"""

import os
import re
import sys
import time

PAGE_URL = "https://www.stream4free.tv/tv-live-france"
BASE_URL = "https://www.stream4free.tv"
OUT_FILE = "data-stream4free.m3u"
GROUP    = "Stream4Free"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")

TITLE_CLEAN = [
    " - Stream4Free", " | Stream4Free",
    " en streaming gratuit", " en direct gratuit",
    " en streaming", " en direct",
    " Live Streaming", " Stream", " Live",
]

SKIP_SLUGS = {
    "tv-live-france", "tv-show-series",
    "privacy-policy", "register-account", "forum",
    "#",
}


def clean_title(raw, slug):
    t = raw.strip() if raw else slug.replace("-", " ").title()
    for suffix in TITLE_CLEAN:
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    if t.lower().startswith("regarder "):
        t = t[9:].strip()
    return t[0].upper() + t[1:] if t else slug


def run_scraper():
    from playwright.sync_api import sync_playwright
    entries = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)

        # ---- Decouverte sur /tv-live-france ----
        page = ctx.new_page()
        print("Playwright: chargement de /tv-live-france ...", flush=True)
        page.goto(PAGE_URL, wait_until="networkidle", timeout=90000)
        try:
            page.wait_for_selector("a.pbitem_cont", timeout=30000)
        except Exception:
            pass
        time.sleep(3)

        elements = page.query_selector_all("a.pbitem_cont")
        print(f"Playwright: {len(elements)} elements <a.pbitem_cont>", flush=True)

        seen_slugs = set()
        for el in elements:
            href = el.get_attribute("href") or ""
            slug = href.rstrip("/").split("/")[-1] if href else ""
            if not slug or slug in SKIP_SLUGS or slug in seen_slugs:
                continue
            # Skip slugs with tv-show / english content
            if "tv-show" in slug:
                continue
            seen_slugs.add(slug)

            title_el = el.query_selector(".pbitem_title span")
            if not title_el:
                title_el = el.query_selector(".pbitem_title")
            title = title_el.inner_text().strip() if title_el else ""
            title = clean_title(title, slug)

            img_el = el.query_selector("img")
            logo = img_el.get_attribute("src") or "" if img_el else ""
            if logo and not logo.startswith("http"):
                logo = BASE_URL + ("" if logo.startswith("/") else "/") + logo

            # URL protocol : l'app resoudra au moment de la lecture
            stream_url = f"stream4free://{slug}"

            entries.append((title, stream_url, logo))
            print(f"  OK {slug} -> {title}", flush=True)

        page.close()
        browser.close()

    return entries


def main():
    entries = run_scraper()

    print(f"\nResultat: {len(entries)} chaines decouvertes", flush=True)

    if not entries:
        if os.path.exists(OUT_FILE):
            print("WARN: 0 items decouverts, on GARDE l'ancien fichier",
                  flush=True)
            sys.exit(0)
        print("ERR: 0 items et pas d'ancien fichier", flush=True)
        sys.exit(1)

    lines = ["#EXTM3U"]
    for title, url, logo in entries:
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        lines.append(
            f'#EXTINF:-1 group-title="{GROUP}"{logo_attr},{title}')
        lines.append(url)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Ecrit {OUT_FILE}: {len(entries)} chaines", flush=True)


if __name__ == "__main__":
    main()
