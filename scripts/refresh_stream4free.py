#!/usr/bin/env python3
"""
refresh_stream4free.py -- Scraper 100% dynamique pour stream4free.tv
Utilise Playwright (navigateur headless) pour charger /tv-live-france,
extraire TOUS les items du DOM rendu par JS, puis fetch chaque page
pour recuperer l'URL m3u8.

Zero liste hardcodee. Les items sont decouverts a chaque execution.
"""

import os
import re
import sys
import ssl
import time
import urllib.request

# -- Config ---------------------------------------------------------------
PAGE_URL = "https://www.stream4free.tv/tv-live-france"
BASE_URL = "https://www.stream4free.tv"
OUT_FILE = "data-stream4free.m3u"
GROUP    = "Stream4Free"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")

M3U8_RE = re.compile(
    r'https?://sv\d+\.data-stream\.top/[a-f0-9]+/hls/[\w._-]+\.m3u8')

TITLE_CLEAN = [
    " - Stream4Free", " | Stream4Free",
    " en streaming gratuit", " en direct gratuit",
    " en streaming", " en direct",
    " Live Streaming", " Stream", " Live",
]

# SSL bypass (certains CDN ont des certs flaky)
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# Slugs systeme a ignorer
SKIP_SLUGS = {
    "tv-live-france", "tv-show-series",
    "privacy-policy", "register-account", "forum",
}


# -- Decouverte via Playwright --------------------------------------------
def discover_items():
    """Charge /tv-live-france dans Chromium headless, attend le rendu JS,
    et extrait tous les items a.pbitem_cont du DOM."""
    from playwright.sync_api import sync_playwright

    items = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()

        print("  Playwright: chargement de /tv-live-france ...", file=sys.stderr)
        page.goto(PAGE_URL, wait_until="networkidle", timeout=90000)

        try:
            page.wait_for_selector("a.pbitem_cont", timeout=30000)
        except Exception:
            print("  WARN: timeout sur wait_for_selector, on continue",
                  file=sys.stderr)

        time.sleep(3)

        elements = page.query_selector_all("a.pbitem_cont")
        for el in elements:
            href = el.get_attribute("href") or ""
            slug = href.rstrip("/").split("/")[-1] if href else ""
            if not slug or slug in SKIP_SLUGS:
                continue

            title_el = el.query_selector(".pbitem_title span")
            if not title_el:
                title_el = el.query_selector(".pbitem_title")
            title = title_el.inner_text().strip() if title_el else ""

            img_el = el.query_selector("img")
            logo = img_el.get_attribute("src") or "" if img_el else ""
            if logo and not logo.startswith("http"):
                logo = BASE_URL + ("" if logo.startswith("/") else "/") + logo

            items.append({"slug": slug, "title": title, "logo": logo})

        browser.close()

    print(f"  Playwright: {len(items)} items decouverts", file=sys.stderr)
    return items


# -- Fetch m3u8 depuis une page individuelle ------------------------------
def fetch_m3u8(slug):
    """Fetch la page d'une chaine et en extrait l'URL m3u8."""
    url = f"{BASE_URL}/{slug}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20, context=_ctx) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        m = M3U8_RE.search(html)
        return m.group(0) if m else None
    except Exception as e:
        print(f"    fetch {slug}: {e}", file=sys.stderr)
        return None


def clean_title(raw, slug):
    """Nettoie le titre extrait du DOM."""
    t = raw.strip() if raw else slug.replace("-", " ").title()
    for suffix in TITLE_CLEAN:
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    if t.lower().startswith("regarder "):
        t = t[9:].strip()
    return t[0].upper() + t[1:] if t else slug


# -- Main -----------------------------------------------------------------
def main():
    items = discover_items()
    if not items:
        if os.path.exists(OUT_FILE):
            print("WARN: 0 items decouverts, on GARDE l'ancien fichier",
                  file=sys.stderr)
            sys.exit(0)
        print("ERREUR: 0 items et pas de fichier existant", file=sys.stderr)
        sys.exit(1)

    entries = []
    skipped = []
    for item in items:
        slug = item["slug"]
        m3u8 = fetch_m3u8(slug)
        if m3u8:
            title = clean_title(item["title"], slug)
            entries.append((title, m3u8, item["logo"]))
            print(f"  OK  {slug} -> {title}", file=sys.stderr)
        else:
            skipped.append(slug)
            print(f"  SKIP {slug}: pas de m3u8", file=sys.stderr)

    if not entries:
        if os.path.exists(OUT_FILE):
            print("WARN: 0 m3u8 trouves, on GARDE l'ancien fichier",
                  file=sys.stderr)
            sys.exit(0)
        print("ERREUR: 0 m3u8 et pas de fichier existant", file=sys.stderr)
        sys.exit(1)

    lines = ["#EXTM3U"]
    for title, m3u8, logo in entries:
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        lines.append(
            f'#EXTINF:-1 group-title="{GROUP}"{logo_attr},{title}')
        lines.append(m3u8)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nResultat: {len(entries)} chaines -> {OUT_FILE}", file=sys.stderr)
    if skipped:
        print(f"  {len(skipped)} sans m3u8: {', '.join(skipped)}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
