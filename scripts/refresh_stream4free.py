#!/usr/bin/env python3
"""
refresh_stream4free.py - Scraper pour stream4free.tv
Utilise Scrapling StealthyFetcher (Patchright/Chromium furtif)
avec solve_cloudflare=True pour bypasser CF Turnstile nativement.
"""
import os, sys, time

PAGE_URL = "https://www.stream4free.tv/tv-live-france"
BASE_URL = "https://www.stream4free.tv"
OUT_FILE = "data-stream4free.m3u"
GROUP = "Stream4Free"

TITLE_CLEAN = [" - Stream4Free", " | Stream4Free", " en streaming gratuit",
               " en direct gratuit", " en streaming", " en direct",
               " Live Streaming", " Stream", " Live"]
SKIP_SLUGS = {"tv-live-france", "tv-show-series", "privacy-policy",
              "register-account", "forum", "#"}
CF_TITLES = ["un instant", "just a moment", "checking", "attention required",
             "please wait", "verify"]

def clean_title(raw, slug):
    t = raw.strip()
    for s in TITLE_CLEAN:
        if t.lower().endswith(s.lower()):
            t = t[:-len(s)].strip()
    if not t:
        t = slug.replace("-", " ").title()
    return t

def is_cf_challenge(title):
    low = (title or "").lower()
    return any(k in low for k in CF_TITLES)

def extract_channels(page):
    """Extract channel entries from a Scrapling Response."""
    entries = []
    seen = set()
    for link in page.css('a'):
        try:
            href = link.attrib.get('href', '')
            if not href or href == '#':
                continue
            if href.startswith('/'):
                slug = href.strip('/').split('/')[-1]
            elif href.startswith(BASE_URL):
                slug = href.replace(BASE_URL, '').strip('/').split('/')[-1]
            else:
                continue
            if not slug or slug in SKIP_SLUGS or slug in seen:
                continue
            if slug.startswith('category') or slug.startswith('tag'):
                continue
            imgs = link.css('img')
            raw_title = ""
            logo = ""
            if imgs:
                raw_title = imgs[0].attrib.get('alt', '')
                logo = imgs[0].attrib.get('src', '') or imgs[0].attrib.get('data-src', '')
            if not raw_title:
                raw_title = link.get_all_text(strip=True)
            title_clean = clean_title(raw_title, slug)
            if not title_clean or len(title_clean) < 2:
                continue
            seen.add(slug)
            entries.append({"slug": slug, "title": title_clean, "logo": logo})
        except Exception:
            continue
    return entries

def run_scraper():
    from scrapling.fetchers import StealthyFetcher
    entries = []
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        print(f"Tentative {attempt}/{max_retries} (Scrapling StealthyFetcher)...")
        try:
            page = StealthyFetcher.fetch(
                PAGE_URL,
                headless=True,
                network_idle=True,
                solve_cloudflare=True,
                timeout=120000,
                wait=3000,
                locale="fr-FR",
                timezone_id="Europe/Paris",
                hide_canvas=True,
                block_webrtc=True,
                dns_over_https=True,
            )
            title = page.css('title::text').get() or ""
            print(f"  Page title: {title}")
            if is_cf_challenge(title):
                print(f"  WARN: CF challenge still active after solve_cloudflare")
                if attempt < max_retries:
                    time.sleep(10)
                continue
            entries = extract_channels(page)
            if entries:
                print(f"  OK: {len(entries)} chaines")
                break
            else:
                print("  0 chaines via liens generiques, essai pbitem_cont...")
                seen = set()
                for el in page.css('a.pbitem_cont'):
                    try:
                        href = el.attrib.get('href', '')
                        if not href or href == '#':
                            continue
                        if href.startswith('/'):
                            slug = href.strip('/').split('/')[-1]
                        elif href.startswith(BASE_URL):
                            slug = href.replace(BASE_URL, '').strip('/').split('/')[-1]
                        else:
                            continue
                        if not slug or slug in SKIP_SLUGS or slug in seen:
                            continue
                        imgs = el.css('img')
                        raw_title = imgs[0].attrib.get('alt', '') if imgs else el.get_all_text(strip=True)
                        logo = imgs[0].attrib.get('src', '') if imgs else ""
                        title_clean = clean_title(raw_title, slug)
                        if not title_clean or len(title_clean) < 2:
                            continue
                        seen.add(slug)
                        entries.append({"slug": slug, "title": title_clean, "logo": logo})
                    except Exception:
                        continue
                if entries:
                    print(f"  OK (pbitem_cont): {len(entries)} chaines")
                    break
                if attempt < max_retries:
                    time.sleep(10)
        except Exception as e:
            print(f"  ERR {attempt}: {e}")
            import traceback
            traceback.print_exc()
            if attempt < max_retries:
                time.sleep(10)
    return entries

def main():
    entries = run_scraper()
    print(f"\nResultat: {len(entries)} chaines")
    if not entries:
        if os.path.exists(OUT_FILE):
            print("WARN: 0 items, on GARDE l'ancien fichier")
            sys.exit(0)
        else:
            print("ERR: 0 items et pas d'ancien fichier")
            sys.exit(1)
    lines = ["#EXTM3U"]
    for e in entries:
        extinf = f'#EXTINF:-1 group-title="{GROUP}"'
        if e["logo"]:
            extinf += f' tvg-logo="{e["logo"]}"'
        extinf += f', {e["title"]}'
        lines.append(extinf)
        lines.append(f'stream4free://{e["slug"]}')
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"OK: {len(entries)} chaines ecrites dans {OUT_FILE}")

if __name__ == "__main__":
    main()
