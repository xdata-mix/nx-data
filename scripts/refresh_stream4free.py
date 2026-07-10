#!/usr/bin/env python3
"""
refresh_stream4free.py - Scraper 100% dynamique pour stream4free.tv

Decouvre tous les items sur /tv-live-france via SeleniumBase UC mode
(bypass CF Turnstile par deconnexion/reconnexion CDP),
puis emet des URLs `stream4free://<slug>` dans le m3u.

L'app resout les m3u8 AU MOMENT DE LA LECTURE via Stream4FreeResolver
(fetch HTML de la page + extraction du <source> tag).
Meme pattern que plex://, plutolive://, francetv://program/.

Zero liste hardcodee. Les items sont decouverts a chaque execution.
"""

import os
import sys
import time

PAGE_URL = "https://www.stream4free.tv/tv-live-france"
BASE_URL = "https://www.stream4free.tv"
OUT_FILE = "data-stream4free.m3u"
GROUP = "Stream4Free"

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

CF_TITLES = ["un instant", "just a moment", "checking", "attention required",
             "please wait", "verify"]


def clean_title(raw, slug):
    t = raw.strip() if raw else slug.replace("-", " ").title()
    for suffix in TITLE_CLEAN:
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    if t.lower().startswith("regarder "):
        t = t[9:].strip()
    return t[0].upper() + t[1:] if t else slug


def is_cf_challenge(sb):
    """Check if current page is a CF challenge."""
    try:
        title = sb.get_title().lower().strip()
        return any(t in title for t in CF_TITLES)
    except Exception:
        return True


def run_scraper():
    from seleniumbase import SB

    entries = []

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        reconnect_time = 8 + (attempt - 1) * 4
        print(f"Tentative {attempt}/{max_retries} (reconnect={reconnect_time}s) ...",
              flush=True)
        try:
            with SB(uc=True, headless=False, locale_code="fr",
                    chromium_arg="--disable-dev-shm-usage,--no-sandbox") as sb:

                # UC disconnect/reconnect: Chrome sans CDP = normal pour CF
                sb.uc_open_with_reconnect(PAGE_URL, reconnect_time=reconnect_time)

                if is_cf_challenge(sb):
                    print("  CF encore present, uc_gui_click_captcha...", flush=True)
                    try:
                        sb.uc_gui_click_captcha()
                        time.sleep(5)
                    except Exception as ce:
                        print(f"  click captcha err: {ce}", flush=True)

                if is_cf_challenge(sb):
                    print("  Attente longue CF...", flush=True)
                    for _ in range(15):
                        time.sleep(3)
                        if not is_cf_challenge(sb):
                            break

                if is_cf_challenge(sb):
                    raise Exception(f"CF non resolve (titre: {sb.get_title()})")

                print(f"  CF OK! titre: {sb.get_title()}", flush=True)

                sb.wait_for_element("a.pbitem_cont", timeout=30)
                print("Contenu trouve!", flush=True)

                time.sleep(3)

                elements = sb.find_elements("a.pbitem_cont")
                print(f"{len(elements)} elements <a.pbitem_cont>", flush=True)

                seen_slugs = set()
                for el in elements:
                    href = el.get_attribute("href") or ""
                    slug = href.rstrip("/").split("/")[-1] if href else ""
                    if not slug or slug in SKIP_SLUGS or slug in seen_slugs:
                        continue
                    if "tv-show" in slug:
                        continue
                    seen_slugs.add(slug)

                    title_el = None
                    try:
                        title_el = el.find_element(
                            "css selector", ".pbitem_title span")
                    except Exception:
                        try:
                            title_el = el.find_element(
                                "css selector", ".pbitem_title")
                        except Exception:
                            pass

                    raw_title = title_el.text.strip() if title_el else ""
                    title = clean_title(raw_title, slug)

                    logo = ""
                    try:
                        img_el = el.find_element("css selector", "img")
                        logo = img_el.get_attribute("src") or ""
                    except Exception:
                        pass
                    if logo and not logo.startswith("http"):
                        logo = BASE_URL + \
                            ("" if logo.startswith("/") else "/") + logo

                    stream_url = f"stream4free://{slug}"
                    entries.append((title, stream_url, logo))
                    print(f"  OK {slug} -> {title}", flush=True)

                break

        except Exception as e:
            print(f"WARN tentative {attempt}: {e}", flush=True)
            if attempt == max_retries:
                print("ERR: toutes les tentatives echouees", flush=True)
            else:
                time.sleep(10)

    return entries


def main():
    entries = run_scraper()
    print(f"\nResultat: {len(entries)} chaines decouvertes", flush=True)

    if not entries:
        if os.path.exists(OUT_FILE):
            print("WARN: 0 items, on GARDE l'ancien fichier", flush=True)
            sys.exit(0)
        print("ERR: 0 items et pas d'ancien fichier", flush=True)
        sys.exit(1)

    lines = ["#EXTM3U"]
    for title, url, logo in entries:
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        lines.append(f'#EXTINF:-1 group-title="{GROUP}"{logo_attr},{title}')
        lines.append(url)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Ecrit {OUT_FILE}: {len(entries)} chaines", flush=True)


if __name__ == "__main__":
    main()
