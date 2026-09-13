#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_vegetatv.py — genere data/vegetatv/vegeta-fr.json (registre pret a charger)

Le GROS boulot (fetch server_status.json + telechargement/parse des m3u Xtream geants +
filtre FR) est fait ICI, cote GitHub runner. L'app Android n'a plus qu'a fetch ce petit
JSON -> registre instantane, zero parse m3u.

Format de sortie = celui de VegetaTvProvider.saveRegistryCache() (loadRegistryCache le lit).
"""
import os, re, sys, json, time, unicodedata, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

SERVERS_URL   = "http://vegetatv.duckdns.org/data/server_status.json"
OUT_PATH      = os.environ.get("VEGETA_OUT", "data/vegetatv/vegeta-fr.json")
WORKERS       = int(os.environ.get("VEGETA_WORKERS", "8"))
M3U_TIMEOUT   = int(os.environ.get("VEGETA_M3U_TIMEOUT", "40"))
API_TIMEOUT   = int(os.environ.get("VEGETA_API_TIMEOUT", "25"))
MAX_STREAMS   = int(os.environ.get("VEGETA_MAX_STREAMS", "4"))   # par chaine
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

# Filtre FR de MARQUE (indicatif de VegetaTvProvider.frNameRegex). On RESTREINT a ces marques
#   MEME sur les serveurs FR : sinon on garde les 25000 chaines de chaque serveur -> JSON 192 MB
#   (> limite GitHub 100 MB, push rejete). Le regex ramene les ~centaines de chaines qui comptent.
FR_NAME_RE = re.compile(
    r"\b(TF1|TMC|TFX|LCI|France\s*[1-9]|FR[1-9]|M6|W9|6ter|Gulli|BFM|CNews|LCP|"
    r"FranceInfo|France\s*Info|Arte|RTL9?|NRJ\s*12|NT1|Canal\+|OCS|Cin[ée]\+|"
    r"Paramount\+|TV5|TV\s*5|L'?[ÉEée]quipe|beIN|RMC|Eurosport|13[èe]me\s*Rue|"
    r"Syfy|Discovery|National\s*Geo|Histoire|MCM|Trace|Nostalgie|Disney|Boomerang|"
    r"Cartoon|Tiji|Piwi|Nickelodeon|T[ée]l[ée]toon|TFOU|Mangas|AB1|AB3|RTBF|"
    r"TV5MONDE|France\s*24|FR:|FRENCH|Fran[çc]ais)\b", re.IGNORECASE)

NON_FR_TAG = re.compile(r"(?i)(^|[|\[\(\s])(AR|TR|DE|ES|IT|PT|NL|PL|RO|EN|UK|US|RU|"
                        r"AL|GR|IN|PK|BR|MX|SE|NO|FI|DK|CZ|HU|BG|HR|SRB?|MK)([|\]\)\s]|$)")

# 2026-09-13 : PORT FIDELE de VegetaTvProvider.norm() (Kotlin). L'ancienne version
#   supprimait le "+" au lieu de le convertir en "plus" : "Canal+ Cinema" donnait la cle
#   "canalcinema" alors que l'app cherche "canalpluscinema" -> TOUT Canal+/Cine+ etait
#   bien dans le JSON mais introuvable pour l'app. Toute divergence entre les deux norm()
#   = chaine invisible : ne modifier celle-ci qu'en meme temps que le Kotlin.
_NORM_MID = re.compile(
    r"\b(hd|sd|fhd|uhd|4k|raw|hevc|h\.?265|ppv|ott|test|backup|fhdr|sdr|multi|vip|full ?hd)\b"
    r"|\blive\b(?!\s*\d)|(?:\+\s?1)|(?:1080p|720p|480p|360p)")
_NORM_END = re.compile(r"\s+(fr|french|francais|belgique|be|suisse|ch|lux)\s*$")

def norm(name):
    s = name.lower()
    s = re.sub(r"[\u00e9\u00e8\u00ea\u00eb]", "e", s)
    s = re.sub(r"[\u00e0\u00e2\u00e4]", "a", s)
    s = re.sub(r"[\u00f9\u00fb\u00fc]", "u", s)
    s = re.sub(r"[\u00ee\u00ef]", "i", s)
    s = re.sub(r"[\u00f4\u00f6]", "o", s)
    s = s.replace("\u00e7", "c")
    s = re.sub(r"\[.*?\]", " ", s)
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"^\s*(fr|france)\s*[:|\-]\s*", "", s)
    while True:
        n = _NORM_MID.sub(" ", s)
        n = _NORM_END.sub("", n)
        n = re.sub(r"\.fr\b", "", n)
        n = re.sub(r"\s+", " ", n).strip()
        if n == s:
            break
        s = n
    s = s.replace("+", "plus").replace("&", "and")
    s = re.sub(r"[^a-z0-9]", "", s)
    return s.replace("sports", "sport")

def base_display(name):
    return re.sub(r"\s+", " ", re.sub(r"(?i)\b(fhd|uhd|4k|hd|sd|hevc|h265|vip)\b", "", name)).strip() or name

def variant_label(name):
    m = re.search(r"(?i)\b(4K|UHD|FHD|1080|720|HD|SD)\b", name)
    return m.group(1).upper() if m else ""

def is_fr_compatible(raw):
    return not NON_FR_TAG.search(raw)

def fetch_servers():
    r = requests.get(SERVERS_URL, headers={"User-Agent": UA}, timeout=API_TIMEOUT)
    r.raise_for_status()
    lst = (r.json() or {}).get("list") or []
    out = []
    for i, o in enumerate(lst):
        if not (o.get("up") or o.get("status") == "up"):
            continue
        flag = o.get("flag", "") or ""
        pos = int(o.get("pos", i + 1))
        is_fr = ("\U0001F1EB\U0001F1F7" in flag) or (32 <= pos <= 37)
        is_global = "\U0001F310" in flag
        # 2026-09-13 : on ne jette PLUS les serveurs a drapeau etranger (MX/ES/TR/IT...).
        #   Avant : `if not is_fr and not is_global: continue` ecartait 32 des 46 serveurs
        #   UP. Or ces panels sont des revendeurs multi-pays qui diffusent aussi du FR :
        #   resultat, tf1/m6 etaient dans le JSON mais TOUT Canal+/Cine+ manquait.
        #   FR_NAME_RE en aval ne retient de toute facon que les marques FR, quel que
        #   soit le serveur. rank : FR=0, global=1, etranger=2 (cf. tri final dans main).
        rank = 0 if is_fr else (1 if is_global else 2)
        url = o.get("url", "") or ""
        if not url.startswith("http"):
            continue
        out.append({"pos": pos, "url": url, "isFr": is_fr, "rank": rank,
                    "ping": int(o.get("response_time_ms", 9999))})
    out.sort(key=lambda s: (s["rank"], s["ping"]))
    return out

def ingest_server(srv, registry, lock):
    added = 0
    try:
        r = requests.get(srv["url"], headers={"User-Agent": UA}, timeout=M3U_TIMEOUT)
        m3u = r.text
    except Exception as e:
        print("[Server %s] m3u KO: %s" % (srv["pos"], e), file=sys.stderr)
        return 0
    if "#EXTINF" not in m3u:
        return 0
    pending = None
    for raw in m3u.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            pending = line
            continue
        if line.startswith("#"):
            if pending:
                pending += " " + line
            continue
        if line.startswith("http"):
            if not pending:
                continue
            ext = pending; pending = None
            raw_name = ext.rsplit(",", 1)[-1].strip()
            if not raw_name:
                continue
            # RESTREINT aux chaines FR de MARQUE (regex) partout (cf. entete).
            if not FR_NAME_RE.search(raw_name) or not is_fr_compatible(raw_name):
                continue
            cleaned = re.sub(r"^(FR|ES|PT|EN|DE|IT|AR|TR|NL|PL|RO|US|UK|BE|CH)[|:\s]+", "",
                             raw_name, flags=re.IGNORECASE)
            cleaned = re.sub(r"^[\-•●○▪►‣›»]+\s*", "", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if not cleaned:
                continue
            key = norm(cleaned)
            if not key:
                continue
            with lock:
                info = registry.setdefault(key, {
                    "displayName": base_display(cleaned), "category": "", "logo": "", "streams": []
                })
                # 2026-09-13 : le plafond MAX_STREAMS n'est PLUS applique ici (ordre
                #   d'arrivee des threads = un serveur etranger rapide pouvait remplir les
                #   4 places avant les serveurs FR). On collecte tout avec le rang du
                #   serveur ("_rank", champ TEMPORAIRE retire avant ecriture) ; le tri +
                #   plafond se font dans main(), FR d'abord.
                if all(s["url"] != line for s in info["streams"]):
                    info["streams"].append({
                        "serverIdx": srv["pos"],
                        "label": variant_label(cleaned) or ("Server %s" % srv["pos"]),
                        "url": line,
                        "_rank": srv["rank"],
                    })
                    added += 1
    print("[Server %s] +%d flux (isFr=%s)" % (srv["pos"], added, srv["isFr"]), file=sys.stderr)
    return added

def main():
    servers = fetch_servers()
    print("%d serveurs a scanner (FR=%d, global=%d, etranger=%d)" % (
        len(servers),
        sum(1 for s in servers if s["rank"] == 0),
        sum(1 for s in servers if s["rank"] == 1),
        sum(1 for s in servers if s["rank"] == 2)), file=sys.stderr)
    registry, lock, total = {}, threading.Lock(), 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(ingest_server, s, registry, lock) for s in servers]
        for f in as_completed(futs):
            total += f.result() or 0
    # 2026-09-13 : tri des flux par rang de serveur (FR -> global -> etranger) PUIS
    #   plafond MAX_STREAMS. Garantit que les flux FR fiables restent en tete et ne
    #   sont jamais evinces par un serveur etranger plus rapide a repondre.
    total = 0
    for info in registry.values():
        streams = sorted(info["streams"], key=lambda s: s.get("_rank", 2))[:MAX_STREAMS]
        for st in streams:
            st.pop("_rank", None)
        info["streams"] = streams
        total += len(streams)
    payload = {
        "savedAt": int(time.time() * 1000),
        "generatedBy": "nx-data cron (refresh_vegetatv.py)",
        "channels": registry,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print("OK: %d chaines, %d flux -> %s" % (len(registry), total, OUT_PATH), file=sys.stderr)

if __name__ == "__main__":
    main()
