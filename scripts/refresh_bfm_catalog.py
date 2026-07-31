#!/usr/bin/env python3
"""refresh_bfm_catalog.py — COMPLETE data-replay-bfm.m3u (BFM/RMC).

Tourne APRES refresh_bfm.py. Le scraper principal plafonne chaque chaine a
max_items=500 et dedup les carrousels -> des programmes hors du top-500 courant
(ex: "Alaska, la ruee vers l'or") disparaissent, et comme le fichier est
reecrit a chaque run, une reponse API throttlee peut en perdre.

Ce complement RE-ENUMERE le catalogue de chaque chaine SANS PLAFOND (tous les
spots du menu d'accueil + toutes les thematiques paginees), puis AJOUTE a
data-replay-bfm.m3u uniquement les programmes ABSENTS (dedup par l'id
`bfmplay://<productId>`). Il ne retire jamais rien.

GARDE-FOUS :
  - si l'enumeration ramene moins de MIN_ENUM programmes (API throttlee/panne)
    -> on N'AJOUTE RIEN, fichier intact.
  - append avec saut de ligne en tete (ne colle pas a la derniere ligne).
"""
import re, sys, os, time, json, urllib.request

HERE = os.path.dirname(__file__)
M3U = os.path.join(HERE, "..", "data-replay-bfm.m3u")
CDN  = "https://ws-cdn.tv.sfr.net/gaia-core/rest/api/web/v1"
CDN2 = "https://ws-cdn.tv.sfr.net/gaia-core/rest/api/web/v2"
PAR  = "app=bfmrmc&device=browser&operators=NEXTTV"
UA   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
MIN_ENUM = 300

BROKEN_PREFIXES = ["NEUF_CINE_PLUS_OCS", "NEUF_01NET", "NEUF_LEQUIPETV",
                   "NEUF_VIRGIN17", "NEUF_UNIVERSAL", "NEUF_KITCHEN_MANIA",
                   "NEUF_USHUAIA", "NEUF_FILMDAFRIQUE"]

# chaines (menu d'accueil) — memes ids que refresh_bfm.py
CHANNELS = [
    ("rmcgo_home_bfmtv",         "BFM TV"),
    ("rmcgo_home_rmcstory",      "RMC Story"),
    ("rmcgo_home_rmcdecouverte", "RMC Découverte"),
    ("rmcgo_home_bfmbusiness",   "BFM Business"),
    ("rmcgo_home_rmclife",       "RMC Life"),
    ("rmcgo_home_01TV",          "Tech & Co"),
    ("rmcgo_home_radios",        "RMC Radio"),
    ("rmcgo_home_bfmavod",       "Exclus BFM Play"),
    ("rmcgo_home_rmccrime",      "100% Crime"),
    ("c67c4f5e-73ae-40fe-b562-35391a9f5931", "Top Mecanic"),
    ("2d0d7898-fad8-47db-a87a-eb1b62c11ef9", "100% DOCS"),
]
THEMES = [
    ("02179209-fc21-4001-8593-d2d8b7696788", "Crime & Investigation"),
    ("f2e897a0-76d8-40c9-89f4-148411aca185", "Cinéma & Fiction"),
    ("8055d4b0-47b1-42b8-8686-a6861cd8ea9b", "Moteur & Mécanique"),
    ("09cbd302-808a-4724-a591-18a17d17455f", "Aventure & Survie"),
    ("1fba40d2-820d-470e-ad70-5e1be1cb2f4c", "Divertissement"),
    ("5fc555aa-4f58-4372-ba6e-2a1a3ab2707c", "Documentaire"),
    ("4d5db435-cfce-4024-9580-b0b21331a5d0", "Mystère & Étrange"),
    ("91e978e9-bc32-4f56-9bc3-1028c333fd20", "Histoire & Civilisation"),
    ("a296a74f-7bd0-45f9-aceb-bbb7609d5dba", "Science & Technologie"),
    ("d4fd74f7-2587-4eba-a26e-3f00e4ae992f", "Société & Immersion"),
    ("2d39f387-9593-414c-9089-01e3b6ef7b1e", "Docu-Réalité"),
    ("5af91e75-a280-454b-beef-6fdba4f81598", "Sport & Combat"),
    ("bf31206d-3bdb-40d6-b5f2-475032d7797b", "Info & Talk"),
    ("d952ba56-c92c-4114-981b-2a68c53cf5b6", "Grand Reportage"),
]


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == tries - 1:
                return None
            time.sleep(1.2)
    return None


def pick_image(tile):
    for img in (tile.get("images") or []):
        fmt = img.get("format", ""); u = img.get("url", ""); wt = img.get("withTitle", False)
        if fmt in ("2/3", "16/9") and not wt and u:
            return u
    for img in (tile.get("images") or []):
        u = img.get("url", "")
        if u and not img.get("withTitle", False):
            return u
    return ""


def add_tile(tile, group, catalog):
    pid = (tile.get("productId") or (tile.get("action") or {}).get("actionIds", {}).get("contentId", "") or "").replace("Product::", "")
    if not pid or "NEUF_" not in pid or pid in catalog:
        return
    if any(pid.startswith(p) for p in BROKEN_PREFIXES):
        return
    title = (tile.get("title") or "").strip()
    if not title:
        return
    ct = tile.get("contentType", "")
    tvg = "series" if ct in ("Season", "Series", "Episode") else "movie"
    catalog[pid] = {"title": title[:140], "image": pick_image(tile), "tvg": tvg, "group": group}


def enumerate_catalog():
    catalog = {}
    # chaines : tous les spots du menu d'accueil, sans plafond
    for menu_id, chan in CHANNELS:
        menu = get(f"{CDN}/menu/RefMenuItem::{menu_id}/structure?{PAR}")
        if not menu:
            print(f"  [warn] menu {chan} KO", file=sys.stderr); continue
        n0 = len(catalog)
        for spot in menu.get("spots", []):
            sid = spot.get("id"); stitle = (spot.get("title") or "").strip()
            if not sid:
                continue
            sdata = get(f"{CDN2}/spot/{sid}/content?{PAR}&page=0&size=200")
            if not sdata:
                continue
            group = f"Replay {chan} - {stitle}" if stitle else f"Replay {chan}"
            for tile in sdata.get("tiles", []):
                add_tile(tile, group, catalog)
            time.sleep(0.1)
        print(f"  {chan}: +{len(catalog)-n0}")
    # thematiques transverses, paginees a fond
    for tid, label in THEMES:
        n0 = len(catalog)
        page = 0
        while page < 30:
            d = get(f"{CDN}/tile/RefTile::{tid}/content?{PAR}&page={page}&size=200")
            items = (d or {}).get("content") or (d or {}).get("tiles") or []
            if not items:
                break
            for tile in items:
                add_tile(tile, f"Thématique BFM Play - {label}", catalog)
            if len(items) < 200:
                break
            page += 1
            time.sleep(0.1)
        print(f"  Thème {label}: +{len(catalog)-n0}")
    return catalog


def main():
    if not os.path.exists(M3U):
        print(f"[STOP] {M3U} absent -> rien a faire."); return
    content = open(M3U, encoding="utf-8").read()
    existing = set(re.findall(r'^bfmplay://(\S+)$', content, re.M))
    print(f"Existant : {len(existing)} programmes bfmplay")

    catalog = enumerate_catalog()
    print(f"Catalogue enumere : {len(catalog)} programmes uniques")
    if len(catalog) < MIN_ENUM:
        print(f"[STOP] enumeration faible ({len(catalog)} < {MIN_ENUM}) -> fichier INTACT (API throttlee ?)."); return

    added = []
    for pid, info in catalog.items():
        if pid in existing:
            continue
        added.append(f'#EXTINF:-1 tvg-id="bfmplay-{pid}" tvg-logo="{info["image"]}" '
                     f'tvg-country="FR" tvg-type="{info["tvg"]}" '
                     f'group-title="{info["group"]}",{info["title"]}')
        added.append(f'bfmplay://{pid}')
    if not added:
        print("Rien a ajouter (deja complet).")
        return
    with open(M3U, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(added) + "\n")
    print(f"Programmes AJOUTES (manquants du scraper) : {len(added)//2}")


if __name__ == "__main__":
    main()
