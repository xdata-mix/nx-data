#!/usr/bin/env python3
"""
refresh_replays.py — génère data-replay.m3u (catalogue replay FR du jour)

Plateformes supportées :
  - France.tv (France 2/3/4/5/Info)   → URLs francetv://<si_id>
  - Arte+7 (Cinéma/Séries/Docus/…)    → URLs arte://<programId>
  - TF1+ (TF1/TMC/TFX/TF1SF/LCI)      → URLs tf1plus://<chan>/<program-slug>

Les URLs spéciales sont résolues à la lecture par l'app ONYX :
  - FrancetvResolver (k7.ftven.fr + hdfauth.ftven.fr)
  - ArteResolver (api.arte.tv/api/player/v2/config)
  - TF1Resolver (mediainfo.tf1.fr/mediainfocombo) — login compte TF1 requis

⚠️ Auth/JWT signés sur IP cliente FR : résolution OBLIGATOIRE côté app FR.
⚠️ TF1+ : le token de session est obtenu via WebView de login dans ONYX
   (= LoginWebViewActivity → cookies Gigya capturés → TF1Auth.saveToken).
"""
import os, sys, json, time, re, urllib.request, gzip
from pathlib import Path

UA = ("Mozilla/5.0 (Linux; Android 14; AndroidTV) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 15
MAX_ITEMS_PER_CHAN = 999      # TF1+ ItemList = 96, France.tv jusqu'à ~80
MAX_ITEMS_PER_ARTE_CAT = 500  # Arte page catégorie typiquement 40-60

# ───── Catalogues ─────

FRANCETV_CHANNELS = [
    ("france-2",   "France 2",   "https://i.imgur.com/sJZBuY4.png"),
    ("france-3",   "France 3",   "https://i.imgur.com/PWbIICf.png"),
    ("france-4",   "France 4",   "https://i.imgur.com/wEsxQLP.png"),
    ("france-5",   "France 5",   "https://i.imgur.com/X4Y5jKR.png"),
    ("franceinfo", "Franceinfo", "https://i.imgur.com/eITXz6A.png"),
]

ARTE_CATEGORIES = [
    ("cinema",              "Cinéma"),
    ("series-et-fictions",  "Séries et fictions"),
    ("documentaires",       "Documentaires"),
    ("sciences",            "Sciences"),
    ("culture-et-pop",      "Culture et pop"),
    ("histoire",            "Histoire"),
    ("arts",                "Arts"),
    ("societe",             "Société"),
]
ARTE_LOGO = ("https://i.imgur.com/ecXMjNl.png")

# TF1+ : 5 chaînes du groupe. Logins compte TF1 requis pour résoudre les
#   streams. Côté script on liste les programmes (= page replay scrapée via
#   JSON-LD ItemList Schema.org).
TF1_CHANNELS = [
    ("tf1",              "TF1",              "https://i.imgur.com/qkOSt0o.png"),
    ("tmc",              "TMC",              "https://i.imgur.com/RY3iEMb.png"),
    ("tfx",              "TFX",              "https://i.imgur.com/JJVZJqL.png"),
    ("tf1-series-films", "TF1 Séries Films", "https://i.imgur.com/3OZdMb9.png"),
    ("lci",              "LCI",              "https://i.imgur.com/jVxzNHL.png"),
]

# v2 (gain marginal) : catégories /programmes-tv/* pour récupérer programmes
#   qui ne sont pas dans /<chan>/replay (ex: sport, reportages, jeunesse)
TF1_CATEGORIES = [
    ("sport",                "Sport"),
    ("reportages",           "Reportages"),
    ("telefilms",            "Téléfilms"),
    ("people-43944072",      "People"),
    ("podcasts-70045207",    "Podcasts"),
    ("impact",               "Impact"),
    ("jeunesse",             "Jeunesse"),
    ("divertissement",       "Divertissement"),
    ("films",                "Films"),
    ("info",                 "Info"),
]

TF1_REPLAY_URL = "https://www.tf1.fr/{slug}/replay"

# Live TF1 (36 chaînes directes TF1+). Résolution côté ONYX via `tf1live://<slug>`
#   → mediainfo.tf1.fr/mediainfocombo/L_<CHAN_ID>. Login compte TF1 requis.
#   Toutes les chaînes sont BASIC = gratuites (pas de Premium requis).

# Traditionnelles (slug → LIVE_CHANNEL_IDS map dans TF1Resolver.kt)
TF1_LIVE_CHANNELS = [
    ("tf1",              "TF1 Direct",              "https://i.imgur.com/qkOSt0o.png"),
    ("tmc",              "TMC Direct",              "https://i.imgur.com/RY3iEMb.png"),
    ("tfx",              "TFX Direct",              "https://i.imgur.com/JJVZJqL.png"),
    ("tf1-series-films", "TF1 Séries Films Direct", "https://i.imgur.com/3OZdMb9.png"),
    ("lci",              "LCI Direct",              "https://i.imgur.com/jVxzNHL.png"),
]

# Chaînes externes gratuites sur TF1+ Direct (slug → map)
TF1_LIVE_EXTERNAL = [
    ("arte",             "ARTE",               "https://i.imgur.com/qkOSt0o.png"),
    ("l-equipe",         "L'Equipe",            "https://i.imgur.com/qkOSt0o.png"),
    ("lcp-public-senat", "LCP / Public Sénat",  "https://i.imgur.com/qkOSt0o.png"),
    ("le-figaro",        "Le Figaro TV",        "https://i.imgur.com/qkOSt0o.png"),
    ("novo19",           "Paris Première",      "https://i.imgur.com/qkOSt0o.png"),
    ("redbulltv",        "Red Bull TV",         "https://i.imgur.com/qkOSt0o.png"),
]

# Chaînes FAST TF1+ (replay 24/7). ID direct L_FAST_* passthrough dans TF1Resolver
# Format: (channel_id, display_name, group_suffix)
TF1_LIVE_FAST = [
    # Fictions
    ("L_FAST_v2l-ad-demain-nous-appartient-38296145", "Demain nous appartient 24/7",  "Fictions FAST"),
    ("L_FAST_v2l-ad-ici-tout-commence-53671915",      "Ici tout commence 24/7",       "Fictions FAST"),
    ("L_FAST_v2l-ad-plus-belle-la-vie-86242005",      "Plus belle la vie 24/7",       "Fictions FAST"),
    ("L_FAST_v2l-ad-comedie-fiction-25247701",         "Comédie & Fiction 24/7",       "Fictions FAST"),
    ("L_FAST_v2l-ad-pas-de-ca-entre-nous-33100936",   "Pas de ça entre nous 24/7",    "Fictions FAST"),
    ("L_FAST_v2l-ad-chante-69061019",                  "Chanté ! 24/7",                "Fictions FAST"),
    ("L_FAST_v2l-ad-sous-le-soleil-18693784",          "Sous le soleil 24/7",          "Fictions FAST"),
    ("L_FAST_v2l-ad-foudre-27131861",                  "Foudre 24/7",                  "Fictions FAST"),
    ("L_FAST_v2l-ad-camping-paradis-42908515",         "Camping Paradis 24/7",         "Fictions FAST"),
    ("L_FAST_v2l-ad-josephine-ange-gardien-04343471",  "Joséphine ange gardien 24/7",  "Fictions FAST"),
    ("L_FAST_v2l-ad-les-mysteres-de-lamour-99639599",  "Les Mystères de l'amour 24/7", "Fictions FAST"),
    ("L_FAST_v2l-ad-les-bracelets-rouges-18062915",    "Les Bracelets rouges 24/7",    "Fictions FAST"),
    ("L_FAST_v2l-ad-je-te-promets-34143660",           "Je te promets 24/7",           "Fictions FAST"),
    ("L_FAST_v2l-ad-alice-nevers-78424271",             "Alice Nevers 24/7",             "Fictions FAST"),
    ("L_FAST_v2l-ad-le-destin-de-lisa-90714215",        "Le Destin de Lisa 24/7",        "Fictions FAST"),
    # Divertissement
    ("L_FAST_v2l-ad-mamans-and-celebres-08240458",     "Mamans & Célèbres 24/7",       "Divertissement FAST"),
    ("L_FAST_v2l-ad-star-academy-70671668",             "Star Academy 24/7",             "Divertissement FAST"),
    ("L_FAST_v2l-ad-danse-avec-les-stars-00457635",     "Danse avec les stars 24/7",     "Divertissement FAST"),
    ("L_FAST_v2l-ad-lolywood-16739451",                 "Lolywood 24/7",                 "Divertissement FAST"),
    ("L_FAST_v2l-ad-revivez-lintegral-mask-singer-91828794", "Mask Singer 24/7",         "Divertissement FAST"),
    ("L_FAST_v2l-ad-super-nanny-14977255",              "Super Nanny 24/7",              "Divertissement FAST"),
    ("L_FAST_v2l-ad-baby-boom-88288927",                "Baby Boom 24/7",                "Divertissement FAST"),
    ("L_FAST_v2l-ad-les-enfoires-35654015",             "Les Enfoirés 24/7",             "Divertissement FAST"),
    ("L_FAST_v2l-ad-les-restos-du-coeur-59894021",     "Les Restos du cœur 24/7",      "Divertissement FAST"),
    # Jeunesse
    ("L_FAST_v2l-ad-mighty-express-44092248",            "Mighty Express 24/7",           "Jeunesse FAST"),
]


# ───── VPN / Proxy (Cloudflare WARP) ─────
# Sur GitHub Actions, WARP est installé en mode proxy SOCKS5 (port 40000).
# Le script détecte la variable WARP_PROXY et l'utilise pour les requêtes
# tf1.fr qui bloquent les IPs datacenter (403). En local (ta machine),
# pas de proxy = accès direct résidentiel, ça marche tel quel.
WARP_PROXY = os.environ.get("WARP_PROXY", "")  # ex: "socks5h://127.0.0.1:40000"

def http_get_via_proxy(url, headers=None):
    """Fetch URL via curl + SOCKS5 proxy (Cloudflare WARP)."""
    import subprocess
    cmd = ["curl", "-sL", "-x", WARP_PROXY,
           "-A", UA, "--max-time", str(TIMEOUT)]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise Exception(f"curl proxy error (rc={result.returncode}): {result.stderr.decode()}")
    raw = result.stdout
    # curl -sL suit les redirects et décompresse gzip automatiquement
    return raw.decode("utf-8", errors="replace")


# ───── HTTP helper ─────

def http_get(url, headers=None):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Accept-Encoding": "gzip", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")


def http_get_tf1(url, headers=None):
    """Accès tf1.fr : direct d'abord, VPN si 403."""
    try:
        return http_get(url, headers=headers)
    except Exception as e:
        if ("403" in str(e) or "Forbidden" in str(e)) and WARP_PROXY:
            print(f"  [VPN] 403 direct → retry via WARP...", file=sys.stderr)
            return http_get_via_proxy(url, headers=headers)
        raise


# ───── France.tv ─────

def francetv_channel_programs(channel_path):
    url = (f"https://api-mobile.yatta.francetv.fr/apps/channels/{channel_path}"
           f"?platform=apps")
    try:
        raw = http_get(url)
    except Exception as e:
        print(f"[!] Fetch error {channel_path}: {e}", file=sys.stderr)
        return []
    data = json.loads(raw)
    seen = set()
    out = []
    for coll in data.get("collections", []):
        if coll.get("type") in ("live", "link"):
            continue
        for item in coll.get("items", []):
            si = item.get("si_id")
            if not si or si in seen:
                continue
            seen.add(si)
            title = (item.get("title") or "").strip()
            program = item.get("program") or {}
            program_title = program.get("label") or program.get("title") or ""
            if not title:
                title = program_title
            elif program_title and program_title.lower() not in title.lower():
                title = f"{program_title} — {title}"
            if not title:
                continue
            logo = ""
            for k in ("image_url", "background_url"):
                v = item.get(k)
                if v and isinstance(v, str):
                    logo = v
                    break
            if not logo:
                imgs = item.get("media_image") or {}
                if isinstance(imgs, dict):
                    logo = imgs.get("url", "")
            out.append({
                "si_id": si,
                "title": title[:140],
                "logo": logo,
            })
            if len(out) >= MAX_ITEMS_PER_CHAN:
                break
        if len(out) >= MAX_ITEMS_PER_CHAN:
            break
    return out


# ───── Arte+7 ─────

ARTE_HREF_RE = re.compile(r"href=\"/fr/videos/([^/\"]+)/([^\"#]+?)/\"")
# v3 : filtre PID Arte valide pour exclure les slugs catégorie (culture-et-pop, etc.)
ARTE_PID_VALID = re.compile(r"^(\d{6}-\d{3}-[A-Z]|RC-[A-Za-z0-9]+)$")

def slug_to_title(slug):
    SHORT = {"et", "le", "la", "les", "de", "du", "des", "un", "une", "à"}
    words = []
    for i, w in enumerate(slug.replace('-', ' ').split()):
        if i == 0 or w not in SHORT:
            words.append(w.capitalize())
        else:
            words.append(w)
    return ' '.join(words)

# v4 (2026-06-19) : check de disponibilité du stream avant d'ajouter un PID
#   au M3U. L'API Arte renvoie ERROR_NO_RIGHTS sur les contenus retirés du
#   catalogue (= droits expirés, ex: Spartacus). On les filtre ici pour
#   éviter qu'ils traînent dans la liste sans pouvoir être joués.
ARTE_API_CFG = "https://api.arte.tv/api/player/v2/config/fr/{}"

def arte_check_available(pid):
    """Retourne True si le stream est jouable (= streams non-vide, pas
    d'erreur ERROR_NO_RIGHTS). Timeout 6s pour ne pas plomber le scrape."""
    try:
        raw = http_get(ARTE_API_CFG.format(pid),
                       headers={"Accept": "application/json"})
        j = json.loads(raw)
        data = j.get("data") or {}
        attrs = data.get("attributes") or {}
        streams = attrs.get("streams") or []
        if not streams:
            return False
        # double check : si error block présent → indispo
        err = attrs.get("error") or data.get("error")
        if err and isinstance(err, dict) and err.get("code"):
            return False
        return True
    except Exception:
        # Sur timeout/erreur réseau on garde par défaut (= pas pénaliser)
        return True


def arte_category_programs(category_slug, max_items=MAX_ITEMS_PER_ARTE_CAT):
    url = f"https://www.arte.tv/fr/videos/{category_slug}/"
    try:
        raw = http_get(url, headers={"Accept": "text/html"})
    except Exception as e:
        print(f"[!] Arte fetch error {category_slug}: {e}", file=sys.stderr)
        return []
    seen = set()
    candidates = []
    for m in ARTE_HREF_RE.finditer(raw):
        pid = m.group(1)
        slug = m.group(2)
        # v3 : exclut les slugs catégorie (= ne sont pas des PIDs valides)
        if not ARTE_PID_VALID.match(pid):
            continue
        if pid in seen:
            continue
        seen.add(pid)
        title = slug_to_title(slug)[:140]
        if not title:
            continue
        candidates.append({"program_id": pid, "title": title})
        # Surdimensionne (×2) pour absorber les futurs filtrés indispo
        if len(candidates) >= max_items * 2:
            break
    # v4 : check parallèle de disponibilité (ThreadPool=8) — on garde les
    #   programmes dont le stream Arte est encore actif.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        availability = list(ex.map(lambda c: arte_check_available(c["program_id"]), candidates))
    out = [c for c, ok in zip(candidates, availability) if ok][:max_items]
    dropped = sum(1 for ok in availability if not ok)
    if dropped:
        print(f"[arte] {category_slug}: {dropped} programmes filtrés (ERROR_NO_RIGHTS)", file=sys.stderr)
    return out


# ───── TF1+ ─────

def tf1plus_channel_programs(channel_slug, max_items=MAX_ITEMS_PER_CHAN):
    """Scrape la page replay TF1+. Extrait les programmes via JSON-LD."""
    url = TF1_REPLAY_URL.format(slug=channel_slug)
    try:
        raw = http_get_tf1(url, headers={"Accept": "text/html"})
    except Exception as e:
        print(f"[!] TF1+ fetch error {channel_slug}: {e}", file=sys.stderr)
        return []
    blocks = re.findall(
        r'<script type="application/ld\+json"[^>]*>([^<]+)</script>',
        raw, re.DOTALL)
    out = []
    seen = set()
    for b in blocks:
        try:
            data = json.loads(b)
        except Exception:
            continue
        if data.get("@type") != "ItemList":
            continue
        for elem in data.get("itemListElement", []):
            if len(out) >= max_items:
                return out
            if not isinstance(elem, dict):
                continue
            item = elem.get("item")
            if not isinstance(item, dict):
                continue
            prog_url = item.get("url") or ""
            name = item.get("name") or ""
            image = item.get("image") or ""
            if not prog_url or not name:
                continue
            path = prog_url.replace("https://www.tf1.fr/", "").strip("/")
            if "/" not in path or len(path.split("/")) > 2:
                continue
            if path in seen:
                continue
            seen.add(path)
            # 2026-06-19 : capture @type JSON-LD (TVSeries/Movie/TVEpisode/etc.)
            item_type = item.get("@type", "")
            out.append({
                "tf1_type": item_type,
                "si_id": path,
                "title": name.strip()[:140],
                "logo": image,
            })
    return out



TF1_CAT_HREF_RE = re.compile(r'href="/(tf1|tmc|tfx|tf1-series-films|lci)/([a-z0-9-]+)"')

def tf1_category_programs(category_slug, max_items=MAX_ITEMS_PER_CHAN):
    """Scrape /programmes-tv/<cat>. Format : hrefs `/(chan)/(slug)`.
    Retourne liste programmes avec si_id `<chan>/<slug>`. Pas de logo/title précis,
    on prendra ceux qu'on trouvera via fetch ultérieur ou on laissera vide."""
    url = f"https://www.tf1.fr/programmes-tv/{category_slug}"
    try:
        raw = http_get_tf1(url, headers={"Accept": "text/html"})
    except Exception as e:
        print(f"[!] TF1 cat fetch error {category_slug}: {e}", file=sys.stderr)
        return []
    out = []
    seen = set()
    # Liens exclus = pages réservées (replay, direct, news, videos, programmes-tv)
    excluded = {"replay", "direct", "news", "videos", "programmes-tv"}
    for m in TF1_CAT_HREF_RE.finditer(raw):
        chan = m.group(1)
        slug = m.group(2)
        if slug in excluded:
            continue
        si_id = f"{chan}/{slug}"
        if si_id in seen:
            continue
        seen.add(si_id)
        title = slug_to_title(slug)[:140]
        out.append({"si_id": si_id, "title": title, "logo": ""})
        if len(out) >= max_items:
            break
    return out


# ───── M6+ Replay (2026-06-19) ─────
# API publique pc.middleware.6play.fr (= equiv web du middleware Android qui ne
#   marche plus). Pas besoin d'auth Gigya pour le CATALOGUE — seul le PLAYBACK
#   exige Widevine + Gigya. Chaque chaîne M6+ a son propre service_id :
#     M6=m6replay, W9=w9replay, 6ter=6terreplay, Gulli=gulli,
#     Téva=tevareplay, Paris Première=parispremierereplay.
M6_BASE = "https://pc.middleware.6play.fr/6play/v2/platforms/m6group_web/services"
# (service_id, chan_label, logo_url)
M6_CHANNELS = [
    ("m6replay",              "M6",              "https://i.imgur.com/4lhxLPB.png"),
    ("w9replay",              "W9",              "https://i.imgur.com/oFGn1On.png"),
    ("6terreplay",            "6ter",            "https://i.imgur.com/M7vGd6Y.png"),
    ("gulli",                 "Gulli",           "https://i.imgur.com/tFNzQQM.png"),
    ("tevareplay",            "Téva",            "https://i.imgur.com/HuLNVjC.png"),
    ("parispremierereplay",   "Paris Première",  "https://i.imgur.com/oCBzd0e.png"),
]
MAX_ITEMS_PER_M6_CHAN = 999

def m6_channel_programs(service_id, max_items=MAX_ITEMS_PER_M6_CHAN):
    """Liste les programmes (= émissions/séries) disponibles pour une chaîne M6+
    via /services/{svc}/programs?limit=N&offset=0&csa=6. Retourne une liste de
    dicts {program_id, title, image}. Le code (slug) est utilisé comme title
    fallback car le champ name est souvent vide."""
    url = f"{M6_BASE}/{service_id}/programs?limit={max_items}&offset=0&csa=6"
    try:
        raw = http_get(url, headers={"Accept": "application/json"})
    except Exception as e:
        print(f"[!] M6 fetch error {service_id}: {e}", file=sys.stderr)
        return []
    try:
        arr = json.loads(raw)
    except Exception as e:
        print(f"[!] M6 JSON parse error {service_id}: {e}", file=sys.stderr)
        return []
    out = []
    for p in arr:
        pid = p.get("id")
        if not pid:
            continue
        code = (p.get("code") or "").strip()
        name = (p.get("name") or "").strip()
        title = name if name else slug_to_title(code)
        if not title:
            continue
        # image preview (img[0].external_key) si dispo
        img = ""
        imgs = p.get("images") or []
        if imgs and isinstance(imgs, list):
            img = imgs[0].get("external_key", "") if isinstance(imgs[0], dict) else ""
        # 2026-06-19 : classification série vs film/unitaire via program_type_wording.code
        ptype = (p.get("program_type_wording") or {}).get("code", "")
        is_series = ptype in {"episode", "emission", "magazine", "journal", "dessin-anime"}
        tvg_type = "series" if is_series else "movie"
        out.append({"program_id": pid, "title": title[:140], "image": img, "service": service_id, "tvg_type": tvg_type})
    return out


# ───── Generation ─────

def generate_m3u(output_path):
    lines = ["#EXTM3U"]
    total = 0

    # France.tv
    print("\n=== France.tv ===")
    for channel_path, channel_label, channel_logo in FRANCETV_CHANNELS:
        progs = francetv_channel_programs(channel_path)
        print(f"  {channel_label}: {len(progs)} programmes")
        for p in progs:
            logo = p["logo"] or channel_logo
            # 2026-06-19 : classification basique pour France TV. p peut avoir
            #   un champ "program_type" si parsé. Sinon on garde "series" par
            #   défaut (= les Replay France TV sont surtout des émissions
            #   régulières / journaux / magazines).
            tvg_type = p.get("tvg_type", "series")
            extinf = (
                f'#EXTINF:-1 tvg-id="francetv-{p["si_id"]}" '
                f'tvg-logo="{logo}" '
                f'tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Replay {channel_label}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'francetv://{p["si_id"]}')
            total += 1
        time.sleep(0.4)

    # Arte+7
    print("\n=== Arte+7 ===")
    # 2026-06-19 : classification par catégorie Arte. Films/Documentaires sont
    #   des unitaires ; Séries et fictions sont des séries ; les autres
    #   catégories (Arts, Culture, Histoire, Sciences) sont surtout des
    #   programmes one-shot → movie par défaut.
    ARTE_SERIES_CATS = {"series-et-fictions"}  # par slug
    for cat_slug, cat_label in ARTE_CATEGORIES:
        progs = arte_category_programs(cat_slug)
        print(f"  {cat_label}: {len(progs)} programmes")
        tvg_type = "series" if cat_slug in ARTE_SERIES_CATS else "movie"
        for p in progs:
            extinf = (
                f'#EXTINF:-1 tvg-id="arte-{p["program_id"]}" '
                f'tvg-logo="{ARTE_LOGO}" '
                f'tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Arte {cat_label}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'arte://{p["program_id"]}')
            total += 1
        time.sleep(0.5)

    # TF1 Live (chaînes directes, 1 entrée par chaîne)
    print("\n=== TF1 Live ===")
    for chan_slug, chan_label, chan_logo in TF1_LIVE_CHANNELS:
        extinf = (
            f'#EXTINF:-1 tvg-id="tf1live-{chan_slug}" '
            f'tvg-logo="{chan_logo}" '
            f'tvg-country="FR" '
            f'group-title="Live TF1+",{chan_label}'
        )
        lines.append(extinf)
        lines.append(f'tf1live://{chan_slug}')
        total += 1

    # TF1+ Live — chaînes externes (ARTE, L'Equipe, etc.)
    print("\n=== TF1+ Live Chaînes externes ===")
    for chan_slug, chan_label, chan_logo in TF1_LIVE_EXTERNAL:
        extinf = (
            f'#EXTINF:-1 tvg-id="tf1live-{chan_slug}" '
            f'tvg-logo="{chan_logo}" '
            f'tvg-country="FR" '
            f'group-title="Live TF1+ Chaînes",{chan_label}'
        )
        lines.append(extinf)
        lines.append(f'tf1live://{chan_slug}')
        total += 1

    # TF1+ Live — chaînes FAST (replay 24/7)
    print("\n=== TF1+ Live FAST ===")
    for chan_id, chan_label, group_suffix in TF1_LIVE_FAST:
        extinf = (
            f'#EXTINF:-1 tvg-id="tf1live-fast-{chan_id}" '
            f'tvg-logo="https://i.imgur.com/qkOSt0o.png" '
            f'tvg-country="FR" '
            f'group-title="Live TF1+ {group_suffix}",{chan_label}'
        )
        lines.append(extinf)
        # ID direct L_FAST_* : passthrough dans TF1Resolver
        lines.append(f'tf1live://{chan_id}')
        total += 1

    # Pré-scan /programmes-tv/telefilms pour savoir quels si_id sont des
    # téléfilms (= unitaires TV, ni films ni séries). On construit un set
    # AVANT la boucle per-channel pour tagger correctement dès le 1er pass.
    print("\n=== TF1+ pré-scan /telefilms ===")
    tf1_telefilm_ids = set()
    for p in tf1_category_programs("telefilms"):
        tf1_telefilm_ids.add(p["si_id"])
    print(f"  {len(tf1_telefilm_ids)} téléfilms identifiés")

    # TF1+ Replay (login compte TF1 requis pour la résolution côté app)
    print("\n=== TF1+ Replay ===")
    for chan_slug, chan_label, chan_logo in TF1_CHANNELS:
        progs = tf1plus_channel_programs(chan_slug)
        print(f"  {chan_label}: {len(progs)} programmes")
        for p in progs:
            ilogo = p.get("logo") or chan_logo
            # 2026-06-19 v2 : classification JSON-LD @type d'abord, heuristique en fallback.
            #   TVSeries/TVSeason → series ; Movie/CreativeWork/Episode → movie selon context.
            si_path = (p.get("si_id") or "").lower()
            tf1_type = (p.get("tf1_type") or "").lower()
            tvg_type = (
                "telefilm" if p.get("si_id", "") in tf1_telefilm_ids else
                "series" if tf1_type in ("tvseries", "tvseason") else
                "movie" if tf1_type == "movie" else
                ("movie" if (chan_slug == "tf1seriesfilms" or "film" in si_path or "/cinema/" in si_path) else "series")
            )
            extinf = (
                f'#EXTINF:-1 tvg-id="tf1plus-{p["si_id"]}" '
                f'tvg-logo="{ilogo}" '
                f'tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'tf1plus://{p["si_id"]}')
            total += 1
        time.sleep(0.5)

    # v2 (gain marginal) : catégories /programmes-tv/* (sport, reportages, etc.)
    #   Route chaque prog vers SA chaîne (= "Replay TF1", "Replay TMC", etc.)
    #   pour bénéficier de la card connexion TF1+ existante.
    print("\n=== TF1+ /programmes-tv/* (gain marginal) ===")
    tf1_cat_seen = set()
    for chan_slug, _, _ in TF1_CHANNELS:
        for p in tf1plus_channel_programs(chan_slug):
            tf1_cat_seen.add(p["si_id"])
    # Map chan_slug → (label, logo)
    chan_meta = {c[0]: (c[1], c[2]) for c in TF1_CHANNELS}
    for cat_slug, cat_label in TF1_CATEGORIES:
        progs = tf1_category_programs(cat_slug)
        added = 0
        for p in progs:
            if p["si_id"] in tf1_cat_seen:
                continue
            tf1_cat_seen.add(p["si_id"])
            # Extract chan depuis si_id "chan/slug"
            chan = p["si_id"].split("/")[0]
            meta = chan_meta.get(chan)
            if not meta:
                continue
            chan_label, chan_logo = meta
            si_path = (p.get("si_id") or "").lower()
            if cat_slug == "telefilms":
                tvg_type = "telefilm"
            else:
                is_film = "film" in si_path or "/cinema/" in si_path
                tvg_type = "movie" if is_film else "series"
            extinf = (
                f'#EXTINF:-1 tvg-id="tf1plus-{p["si_id"].replace(chr(47), chr(45))}" '
                f'tvg-logo="{chan_logo}" '
                f'tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'tf1plus://{p["si_id"]}')
            added += 1
            total += 1
        print(f"  {cat_label}: +{added} nouveaux")
        time.sleep(0.3)

    lines.append("")
    # M6+ Replay (2026-06-19) — login compte M6 requis pour PLAYBACK (Widevine
    #   DRM). Le catalogue est public. Une catégorie M3U par chaîne (= 6 cards
    #   "🔓 Connexion 6play" côté LiveTvHubProvider quand non loggé).
    print("\n=== M6+ Replay ===")
    for service_id, chan_label, chan_logo in M6_CHANNELS:
        progs = m6_channel_programs(service_id)
        print(f"  {chan_label}: {len(progs)} programmes")
        for p in progs:
            ilogo = p.get("image") or chan_logo
            # Si image = external_key d'images.6play.fr, construire URL CDN
            if ilogo and not ilogo.startswith("http"):
                ilogo = f"https://images.6play.fr/v1/images/{ilogo}/raw"
            extinf = (
                f'#EXTINF:-1 tvg-id="m6plus-{p["program_id"]}" '
                f'tvg-logo="{ilogo}" '
                f'tvg-country="FR" '
                f'tvg-type="{p.get("tvg_type", "series")}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(extinf)
            # v2 : URL inclut le service (= chaîne API) pour que M6Resolver
            #   sache quel endpoint /services/{service}/... appeler côté Kotlin
            #   sans devoir essayer les 6 services à chaque clic.
            lines.append(f'm6play://{service_id}/{p["program_id"]}')
            total += 1
        time.sleep(0.4)

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OK] Wrote {total} replay programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay.m3u")
    n = generate_m3u(out)
    if n == 0:
        print("[!] No replays found, exiting with error", file=sys.stderr)
        sys.exit(1)
