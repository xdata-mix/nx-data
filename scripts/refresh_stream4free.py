#!/usr/bin/env python3
"""
refresh_stream4free.py â Scraper 100% dynamique pour stream4free.tv
Utilise Playwright (navigateur headless) pour TOUT :
  1) Decouverte des items sur /tv-live-france (JS-rendered)
  2) Extraction des URLs m3u8 via fetch() dans le contexte browser
     (bypass Cloudflare nativement â urllib seul recoit 403)

Zero liste hardcodee. Les items sont decouverts a chaque execution.
"""

import os
import re
import sys
import time

# ââ Config ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

# Slugs systeme a ignorer
SKIP_SLUGS = {
    "tv-live-france", "tv-show-series",
    "privacy-policy", "register-account", "forum",
}


def clean_title(raw, slug):
    """Nettoie le titre extrait du DOM."""
    t = raw.strip() if raw else slug.replace("-", " ").title()
    for suffix in TITLE_CLEAN:
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    if t.lower().startswith("regarder "):
        t = t[9:].strip()
    return t[0].upper() + t[1:] if t else slug


def run_scraper():
    """Playwright fait TOUT : decouverte + extraction m3u8."""
    from playwright.sync_api import sync_playwright

    entries = []
    skipped = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()

        # ââ 1) Decouverte des items sur /tv-live-france ââââââââââ
        print("  Playwright: chargement de /tv-live-france ...",
              file=sys.stderr)
        page.goto(PAGE_URL, wait_until="networkidle", timeout=90000)
        try:
            page.wait_for_selector("a.pbitem_cont", timeout=30000)
        except Exception:
            print("  WARN: timeout wait_for_selector, on continue",
                  file=sys.stderr)
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
                logo = BASE_URL + \
                    ("" if logo.startswith("/") else "/") + logo

            items.append({"slug": slug, "title": title, "logo": logo})

        print(f"  Playwright: {len(items)} items decouverts",
              file=sys.stderr)

        if not items:
            browser.close()
            return [], []

        # ââ 2) Extraction m3u8 via fetch() dans le browser âââââââ
        # Le browser a deja passe le challenge CF lors de la decouverte.
        # On reutilise sa session (cookies CF) pour fetch() chaque page.
        for item in items:
            slug = item["slug"]
            url = f"{BASE_URL}/{slug}"
            try:
                # Methode rapide : fetch() same-origin dans le browser
                html = page.evaluate("""
                    async (url) => {
                        const r = await fetch(url, {credentials: 'include'});
                        return await r.text();
                    }
                """, url)
                m = M3U8_RE.search(html)
                if m:
                    title = clean_title(item["title"], slug)
                    entries.append((title, m.group(0), item["logo"]))
                    print(f"  OK  {slug} -> {title}", file=sys.stderr)
                    continue
            except Exception as e:
                print(f"    fetch() {slug}: {e}", file=sys.stderr)

            # Fallback : navigation complete (si le m3u8 est injecte par JS)
            try:
                page.goto(url, wait_until="domcontentloaded",
                          timeout=25000)
                time.sleep(2)
                dom = page.content()
                m2 = M3U8_RE.search(dom)
                if m2:
                    title = clean_title(item["title"], slug)
                    entries.append((title, m2.group(0), item["logo"]))
                    print(f"  OK  {slug} -> {title} (via nav)",
                          file=sys.stderr)
                    continue
            except Exception as e2:
                print(f"    nav {slug}: {e2}", file=sys.stderr)

            skipped.append(slug)
            print(f"  SKIP {slug}: pas de m3u8", file=sys.stderr)

        browser.close()

    return entries, skipped


# ââ Main ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def main():
    entries, skipped = run_scraper()

    if not entries:
        # Garde-fou : si rien trouve, on preserve l'ancien fichier
        if os.path.exists(OUT_FILE):
            print("WARN: 0 m3u8 trouves, on GARDE l'ancien fichier",
                  file=sys.stderr)
            sys.exit(0)
        print("ERREUR: 0 m3u8 et pas de fichier existant",
              file=sys.stderr)
        sys.exit(1)

    # Ecrire le M3U
    lines = ["#EXTM3U"]
    for title, m3u8, logo in entries:
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        lines.append(
            f'#EXTINF:-1 group-title="{GROUP}"{logo_attr},{title}')
        lines.append(m3u8)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nResultat: {len(entries)} chaines -> {OUT_FILE}",
          file=sys.stderr)
    if skipped:
        print(f"  {len(skipped)} sans m3u8: {', '.join(skipped)}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
