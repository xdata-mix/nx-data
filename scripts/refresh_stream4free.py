#!/usr/bin/env python3
"""
refresh_stream4free.py - Scraper 100% dynamique pour stream4free.tv
Utilise le binaire Camoufox (Firefox patche C++ anti-detection)
lance via Playwright pour bypasser CF Turnstile.
"""
import os, sys, time, re

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

def get_camoufox_path():
    try:
        from camoufox.pkgman import get_path
        return str(get_path("camoufox"))
    except Exception:
        return None

def run_scraper():
    from playwright.sync_api import sync_playwright
    cfx_path = get_camoufox_path()
    entries = []
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        print(f"Tentative {attempt}/{max_retries}...")
        try:
            with sync_playwright() as p:
                launch_opts = {"headless": True}
                if cfx_path:
                    launch_opts["executable_path"] = cfx_path
                    print(f"  Camoufox binary: {cfx_path}")
                else:
                    print("  WARN: no Camoufox binary, using default Firefox")
                    p.firefox.install()
                browser = p.firefox.launch(**launch_opts)
                context = browser.new_context(
                    no_viewport=True,
                    locale="fr-FR",
                    timezone_id="Europe/Paris",
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
                )
                page = context.new_page()
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                """)
                page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
                title = page.title()
                if is_cf_challenge(title):
                    print(f"  CF detecte ({title}), attente...")
                    for i in range(30):
                        time.sleep(2)
                        title = page.title()
                        if not is_cf_challenge(title):
                            print(f"  CF resolu apres {(i+1)*2}s")
                            break
                    else:
                        print("  WARN: CF non resolve apres 60s")
                        browser.close()
                        if attempt < max_retries: time.sleep(5)
                        continue
                try:
                    page.wait_for_selector("a[href*='/']", timeout=30000)
                    time.sleep(2)
                except: pass
                links = page.query_selector_all("a")
                seen = set()
                for link in links:
                    try:
                        href = link.get_attribute("href") or ""
                        if not href or href == "#": continue
                        if href.startswith("/"):
                            slug = href.strip("/").split("/")[-1]
                        elif href.startswith(BASE_URL):
                            slug = href.replace(BASE_URL, "").strip("/").split("/")[-1]
                        else: continue
                        if not slug or slug in SKIP_SLUGS or slug in seen: continue
                        if slug.startswith("category") or slug.startswith("tag"): continue
                        img = link.query_selector("img")
                        raw_title = (img.get_attribute("alt") if img else "") or link.inner_text().strip()
                        title_clean = clean_title(raw_title, slug)
                        if not title_clean or len(title_clean) < 2: continue
                        logo = (img.get_attribute("src") or img.get_attribute("data-src") or "") if img else ""
                        seen.add(slug)
                        entries.append({"slug": slug, "title": title_clean, "logo": logo})
                    except: continue
                if entries:
                    print(f"  OK: {len(entries)} chaines")
                    browser.close()
                    break
                else:
                    print("  0 chaines, retry pbitem_cont...")
                    page2 = context.new_page()
                    page2.goto(PAGE_URL, wait_until="networkidle", timeout=60000)
                    time.sleep(3)
                    for el in page2.query_selector_all("a.pbitem_cont"):
                        try:
                            href = el.get_attribute("href") or ""
                            if not href or href == "#": continue
                            slug = (href.strip("/").split("/")[-1] if href.startswith("/") else
                                    href.replace(BASE_URL,"").strip("/").split("/")[-1] if href.startswith(BASE_URL) else "")
                            if not slug or slug in SKIP_SLUGS or slug in seen: continue
                            img = el.query_selector("img")
                            raw_title = (img.get_attribute("alt") if img else "") or el.inner_text().strip()
                            logo = (img.get_attribute("src") or "") if img else ""
                            title_clean = clean_title(raw_title, slug)
                            if not title_clean or len(title_clean) < 2: continue
                            seen.add(slug)
                            entries.append({"slug": slug, "title": title_clean, "logo": logo})
                        except: continue
                    browser.close()
                    if entries: break
                    else:
                        if attempt < max_retries: time.sleep(5)
        except Exception as e:
            print(f"  ERR {attempt}: {e}")
            if attempt < max_retries: time.sleep(10)
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
        if e["logo"]: extinf += f' tvg-logo="{e["logo"]}"'
        extinf += f', {e["title"]}'
        lines.append(extinf)
        lines.append(f'stream4free://{e["slug"]}')
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"OK: {len(entries)} chaines ecrites dans {OUT_FILE}")

if __name__ == "__main__":
    main()
