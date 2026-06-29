#!/usr/bin/env python3
"""
refresh_stream4free.py - Scraper 100% dynamique pour stream4free.tv
Utilise Playwright (navigateur headless) pour :
  1) Decouverte des items sur /tv-live-france (JS-rendered)
  2) Navigation sur chaque page + interception reseau pour capturer les m3u8
     (les URLs m3u8 sont injectees par le player JS, pas dans le HTML statique)

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

# Pattern principal: CDN data-stream.top
M3U8_RE = re.compile(
    r'https?://sv\d+\.data-stream\.top/[a-f0-9]+/hls/[\w._-]+\.m3u8')

# Fallback: tout m3u8 sur n'importe quel CDN
M3U8_ANY = re.compile(r'https?://[^\s"\'<>]+\.m3u8')

TITLE_CLEAN = [
    " - Stream4Free", " | Stream4Free",
    " en streaming gratuit", " en direct gratuit",
    " en streaming", " en direct",
    " Live Streaming", " Stream", " Live",
]

SKIP_SLUGS = {
    "tv-live-france", "tv-show-series",
    "privacy-policy", "register-account", "forum",
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
    skipped = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)

        # ---- 1) Discovery sur /tv-live-france ----
        page = ctx.new_page()
        print("Playwright: chargement de /tv-live-france ...", flush=True)
        page.goto(PAGE_URL, wait_until="networkidle", timeout=90000)
        try:
            page.wait_for_selector("a.pbitem_cont", timeout=30000)
        except Exception:
            pass
        time.sleep(3)

        elements = page.query_selector_all("a.pbitem_cont")
        items = []
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

        print(f"Playwright: {len(items)} items decouverts", flush=True)
        page.close()

        if not items:
            browser.close()
            return [], []

        # ---- 2) m3u8 extraction via navigation + interception reseau ----
        for item in items:
            slug = item["slug"]
            url = f"{BASE_URL}/{slug}"
            m3u8_found = {"url": None}

            ch_page = ctx.new_page()

            def make_handler(container):
                def on_request(request):
                    if container["url"]:
                        return
                    req_url = request.url
                    m = M3U8_RE.search(req_url)
                    if m:
                        container["url"] = m.group(0)
                        return
                    if ".m3u8" in req_url and "ad" not in req_url.split("/")[-1].lower():
                        m2 = M3U8_ANY.search(req_url)
                        if m2:
                            container["url"] = m2.group(0)
                return on_request

            ch_page.on("request", make_handler(m3u8_found))

            try:
                ch_page.goto(url, wait_until="domcontentloaded", timeout=30000)
                for _ in range(24):
                    if m3u8_found["url"]:
                        break
                    time.sleep(0.5)

                if not m3u8_found["url"]:
                    try:
                        dom = ch_page.content()
                        m = M3U8_RE.search(dom)
                        if m:
                            m3u8_found["url"] = m.group(0)
                        else:
                            for frame in ch_page.frames:
                                try:
                                    fc = frame.content()
                                    mf = M3U8_RE.search(fc)
                                    if mf:
                                        m3u8_found["url"] = mf.group(0)
                                        break
                                except Exception:
                                    pass
                    except Exception:
                        pass

            except Exception as e:
                print(f"  ERR {slug}: {e}", flush=True)
            finally:
                try:
                    ch_page.close()
                except Exception:
                    pass

            if m3u8_found["url"]:
                title = clean_title(item["title"], slug)
                entries.append((title, m3u8_found["url"], item["logo"]))
                short = m3u8_found["url"]
                if len(short) > 90:
                    short = short[:90] + "..."
                print(f"  OK {slug}: {short}", flush=True)
            else:
                skipped.append(slug)
                print(f"  SKIP {slug}: pas de m3u8", flush=True)

        browser.close()

    return entries, skipped


def main():
    entries, skipped = run_scraper()

    print(f"\nResultat: {len(entries)} OK, {len(skipped)} SKIP", flush=True)
    if skipped:
        print(f"SKIP: {', '.join(skipped)}", flush=True)

    if not entries:
        if os.path.exists(OUT_FILE):
            print("WARN: 0 m3u8 trouves, on GARDE l'ancien fichier",
                  flush=True)
            sys.exit(0)
        sys.exit(1)

    lines = ["#EXTM3U"]
    for title, m3u8, logo in entries:
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        lines.append(
            f'#EXTINF:-1 group-title="{GROUP}"{logo_attr},{title}')
        lines.append(m3u8)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Ecrit {OUT_FILE}: {len(entries)} chaines", flush=True)


if __name__ == "__main__":
    main()
