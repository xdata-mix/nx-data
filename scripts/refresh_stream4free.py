#!/usr/bin/env python3
"""
refresh_stream4free.py — Scraper 100% DYNAMIQUE pour stream4free.tv
ZERO liste hardcodee. Scanne les pages listing + mega-menu pour decouvrir
TOUTES les chaines. Si le site ajoute/retire/deplace des chaines, le
prochain refresh s'adapte automatiquement.
"""

import os, re, sys, time

try:
    import cloudscraper
    _cs = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    def fetch(url):
        try:
            r = _cs.get(url, timeout=20)
            r.raise_for_status()
            return r.text, None
        except Exception as e:
            return None, str(e)
except ImportError:
    import ssl, urllib.request
    _ctx = ssl.create_default_context()
    _ctx.check_hostname = False
    _ctx.verify_mode = ssl.CERT_NONE
    _UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
           "AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/131.0.0.0 Safari/537.36")
    def fetch(url):
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=20, context=_ctx) as r:
                return r.read().decode("utf-8", errors="replace"), None
        except Exception as e:
            return None, str(e)


BASE = "https://www.stream4free.tv"

# Pages systeme/navigation a ignorer (jamais des chaines)
SYSTEM_SLUGS = frozenset({
    "tv-show-series", "tv-live-france", "tv-replay",
    "profile", "change-profile-picture", "change-profile-video",
    "edit-profile", "edit-details", "privacy", "preferences",
    "customize-my-page", "inbox", "friends", "search",
    "advanced-search", "invite-friends", "pending-approval",
    "request-sent", "groups", "photos", "videos", "events",
    "forum", "community", "register-account", "lost-password",
    "privacy-policy", "favicons", "images",
})

M3U8_RE = re.compile(r'https://sv\d+\.data-stream\.top/[a-f0-9]+/hls/[\w._-]+\.m3u8')

LINK_RE = re.compile(
    r'href=["\'"](?:https?://(?:www\.)?stream4free\.tv)?/([a-zA-Z0-9][a-zA-Z0-9_-]*)(?:/[^"\'"]*)?["\'"]',
    re.IGNORECASE
)

OG_IMG = re.compile(
    r'(?:property=["\'"]og:image["\'"]\s+content=["\'"]([^"\'"]+)|content=["\'"]([^"\'"]+)["\'"]\s+property=["\'"]og:image)',
    re.IGNORECASE
)

TITLE_RE = re.compile(r'<title[^>]*>([^<]+)</title>', re.IGNORECASE)


def discover_all(html):
    """Extrait tous les slugs de chaines depuis le HTML (mega-menu + body)."""
    slugs = set()
    names = {}

    # 1) Tous les liens internes
    for m in LINK_RE.finditer(html):
        s = m.group(1).strip().lower()
        if s not in SYSTEM_SLUGS and "." not in s and len(s) >= 2:
            slugs.add(s)

    # 2) Liens avec texte visible = noms de chaines
    for m in re.finditer(
        r'<a[^>]+href=["\'"](?:https?://(?:www\.)?stream4free\.tv)?/([a-zA-Z0-9][a-zA-Z0-9_-]*)["\'"][^>]*>\s*(?:<[^>]+>\s*)*([^<]{2,}?)(?:\s*<)',
        html, re.IGNORECASE
    ):
        s = m.group(1).strip().lower()
        n = m.group(2).strip()
        if s not in SYSTEM_SLUGS and n and not n.startswith("http"):
            names.setdefault(s, n)

    return slugs, names


def clean_title(raw, slug):
    """Nettoie le <title> de la page pour en extraire le nom de la chaine."""
    t = raw.strip() if raw else ""
    for suf in [" - Stream4Free", " | Stream4Free", " - Stream4free Live",
                " | Stream4free", " en streaming gratuit", " en direct gratuit",
                " en streaming", " en direct", " Live Streaming",
                " Live Stream", " Stream", " Live"]:
        if t.lower().endswith(suf.lower()):
            t = t[:-len(suf)].strip()
    for pref in ["Stream4free Live - ", "Regarder ", "Stream4Free - "]:
        if t.lower().startswith(pref.lower()):
            t = t[len(pref):].strip()
    return (t[0].upper() + t[1:]) if t else slug.replace("-", " ").title()


def extract_channel_info(html, slug, known_name=None):
    """Extrait m3u8 URL + titre + logo depuis la page d'une chaine."""
    m3 = M3U8_RE.search(html)
    if not m3:
        return None, None, None

    title = known_name
    if not title:
        tm = TITLE_RE.search(html)
        title = clean_title(tm.group(1) if tm else None, slug)
    if title:
        title = title.strip()
        if not title:
            title = slug.replace("-", " ").title()

    logo = ""
    lm = OG_IMG.search(html)
    if lm:
        logo = (lm.group(1) or lm.group(2) or "").strip()
        if logo and not logo.startswith("http"):
            logo = f"{BASE}{logo}" if logo.startswith("/") else f"{BASE}/{logo}"

    return m3.group(0), title, logo


def main():
    print("=== Stream4Free dynamic scraper ===", file=sys.stderr)

    # --- Phase 1 : decouverte des slugs depuis 3 pages ---
    all_slugs = set()
    all_names = {}

    for url in [f"{BASE}/tv-live-france", f"{BASE}/tv-show-series", BASE]:
        html, err = fetch(url)
        if not html:
            print(f"  WARN {url}: {err}", file=sys.stderr)
            continue
        found, names = discover_all(html)
        print(f"  {url} -> {len(found)} slugs", file=sys.stderr)
        all_slugs |= found
        for k, v in names.items():
            all_names.setdefault(k, v)

    print(f"\n  Total slugs uniques : {len(all_slugs)}", file=sys.stderr)
    if not all_slugs:
        print("  ERREUR : 0 slugs decouverts (site bloque ?)", file=sys.stderr)
        sys.exit(0)  # exit 0 = ne pas casser le workflow, on garde l'ancien fichier

    # --- Phase 2 : fetch chaque slug pour extraire m3u8 + titre + logo ---
    entries = []
    skipped = []

    for slug in sorted(all_slugs):
        html, err = fetch(f"{BASE}/{slug}")
        if not html:
            print(f"  FAIL  {slug}: {err}", file=sys.stderr)
            continue

        m3u8, title, logo = extract_channel_info(html, slug, all_names.get(slug))
        if not m3u8:
            print(f"  SKIP  {slug} (pas de m3u8)", file=sys.stderr)
            skipped.append(slug)
            continue

        entries.append((slug, title, m3u8, logo))
        print(f"  OK    {slug} -> {title}", file=sys.stderr)
        time.sleep(0.3)  # politesse anti-rate-limit

    # --- Phase 3 : ecriture du m3u ---
    out_path = "data-stream4free.m3u"
    total = len(entries)

    if total == 0:
        print(f"\n  0 chaines trouvees -> on GARDE l'ancien fichier", file=sys.stderr)
        sys.exit(0)

    lines = ["#EXTM3U"]
    for slug, title, m3u8, logo in entries:
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        lines.append(f'#EXTINF:-1 group-title="Stream4Free"{logo_attr},{title}')
        lines.append(m3u8)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n  Resultat : {total} chaines -> {out_path}", file=sys.stderr)
    if skipped:
        print(f"  {len(skipped)} pages sans m3u8 : {', '.join(skipped)}", file=sys.stderr)


if __name__ == "__main__":
    main()
