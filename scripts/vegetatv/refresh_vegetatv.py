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

def norm(name):
    s = unicodedata.normalize("NFD", name)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"\b(fhd|uhd|4k|hd|sd|hevc|h265|multi|vip|backup|full ?hd)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)

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
        if not is_fr and not is_global:
            continue
        url = o.get("url", "") or ""
        if not url.startswith("http"):
            continue
        out.append({"pos": pos, "url": url, "isFr": is_fr,
                    "ping": int(o.get("response_time_ms", 9999))})
    out.sort(key=lambda s: (0 if s["isFr"] else 1, s["ping"]))
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
                if all(s["url"] != line for s in info["streams"]) and len(info["streams"]) < MAX_STREAMS:
                    info["streams"].append({
                        "serverIdx": srv["pos"],
                        "label": variant_label(cleaned) or ("Server %s" % srv["pos"]),
                        "url": line,
                    })
                    added += 1
    print("[Server %s] +%d flux (isFr=%s)" % (srv["pos"], added, srv["isFr"]), file=sys.stderr)
    return added

def main():
    servers = fetch_servers()
    print("%d serveurs FR/GLOBAL a scanner" % len(servers), file=sys.stderr)
    registry, lock, total = {}, threading.Lock(), 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(ingest_server, s, registry, lock) for s in servers]
        for f in as_completed(futs):
            total += f.result() or 0
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
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_vegetatv.py — genere data/vegetatv/vegeta-fr.json (registre pret a charger)

Modele sur scripts/olatv/refresh_olatv.py. Le GROS boulot (fetch server_status.json +
telechargement/parse des m3u Xtream geants + filtre FR) est fait ICI, cote GitHub runner.
L'app Android n'a plus qu'a fetch ce petit JSON -> registre instantane, zero parse m3u.

Format de sortie = celui de VegetaTvProvider.saveRegistryCache() (loadRegistryCache le lit) :
{
  "savedAt": <epoch_ms>,
  "generatedBy": "nx-data cron",
  "channels": {
     "<key_normalise>": {
        "displayName": "...", "category": "...", "logo": "",
        "streams": [ {"serverIdx": <pos>, "label": "...", "url": "https://..."} , ... ]
     }, ...
  }
}
"""
import os, re, sys, json, time, unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

SERVERS_URL   = "http://vegetatv.duckdns.org/data/server_status.json"
OUT_PATH      = os.environ.get("VEGETA_OUT", "data/vegetatv/vegeta-fr.json")
WORKERS       = int(os.environ.get("VEGETA_WORKERS", "8"))
M3U_TIMEOUT   = int(os.environ.get("VEGETA_M3U_TIMEOUT", "40"))
API_TIMEOUT   = int(os.environ.get("VEGETA_API_TIMEOUT", "25"))
MAX_STREAMS   = int(os.environ.get("VEGETA_MAX_STREAMS", "6"))   # par chaine
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

# --- Filtre FR (identique a VegetaTvProvider.frNameRegex, indicatif) ---
FR_NAME_RE = re.compile(
    r"\b(TF1|TMC|TFX|LCI|France\s*[1-9]|FR[1-9]|M6|W9|6ter|Gulli|BFM|CNews|LCP|"
    r"FranceInfo|France\s*Info|Arte|RTL9?|NRJ\s*12|NT1|Canal\+|OCS|Cin[ée]\+|"
    r"Paramount\+|TV5|TV\s*5|L'?[ÉEée]quipe|beIN|RMC|Eurosport|13[èe]me\s*Rue|"
    r"Syfy|Discovery|National\s*Geo|Histoire|MCM|Trace|Nostalgie|Disney|Boomerang|"
    r"Cartoon|Tiji|Piwi|Nickelodeon|T[ée]l[ée]toon|TFOU|Mangas|AB1|AB3|RTBF|"
    r"TV5MONDE|France\s*24|FR:|FRENCH|Fran[çc]ais)\b", re.IGNORECASE)

# Exclusion langues non-FR (equivalent simplifie d'IptvLangFilter.isFrCompatible)
NON_FR_TAG = re.compile(r"(?i)(^|[|\[\(\s])(AR|TR|DE|ES|IT|PT|NL|PL|RO|EN|UK|US|RU|"
                        r"AL|GR|IN|PK|BR|MX|SE|NO|FI|DK|CZ|HU|BG|HR|SRB?|MK|BE-NL)([|\]\)\s]|$)")

def norm(name: str) -> str:
    s = unicodedata.normalize("NFD", name)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"\b(fhd|uhd|4k|hd|sd|hevc|h265|multi|vip|backup|full ?hd)\b", " ", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s

def base_display(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"(?i)\b(fhd|uhd|4k|hd|sd|hevc|h265|vip)\b", "", name)).strip() or name

def variant_label(name: str) -> str:
    m = re.search(r"(?i)\b(4K|UHD|FHD|1080|720|HD|SD)\b", name)
    return m.group(1).upper() if m else ""

def is_fr_compatible(raw: str) -> bool:
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
        is_fr = ("🇫🇷" in flag) or (32 <= pos <= 37)
        is_global = "🌐" in flag
        if not is_fr and not is_global:
            continue
        url = o.get("url", "") or ""
        if not url.startswith("http"):
            continue
        out.append({"pos": pos, "url": url, "isFr": is_fr,
                    "ping": int(o.get("response_time_ms", 9999))})
    out.sort(key=lambda s: (0 if s["isFr"] else 1, s["ping"]))
    return out

def ingest_server(srv, registry, lock):
    """Fetch le m3u Xtream du serveur, parse les chaines FR -> registry."""
    added = 0
    try:
        r = requests.get(srv["url"], headers={"User-Agent": UA}, timeout=M3U_TIMEOUT)
        m3u = r.text
    except Exception as e:
        print(f"[Server {srv['pos']}] m3u fetch KO: {e}", file=sys.stderr)
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
            keep = True if srv["isFr"] else bool(FR_NAME_RE.search(raw_name))
            if not keep or not is_fr_compatible(raw_name):
                continue
            cleaned = re.sub(r"^(FR|ES|PT|EN|DE|IT|AR|TR|NL|PL|RO|US|UK|BE|CH)[|:\s]+", "",
                             raw_name, flags=re.IGNORECASE)
            cleaned = re.sub(r"^[\-•●○▪►‣›»•]+\s*", "", cleaned)
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
                if all(s["url"] != line for s in info["streams"]) and len(info["streams"]) < MAX_STREAMS:
                    info["streams"].append({
                        "serverIdx": srv["pos"],
                        "label": variant_label(cleaned) or f"Server {srv['pos']}",
                        "url": line,
                    })
                    added += 1
    print(f"[Server {srv['pos']}] +{added} flux (isFr={srv['isFr']})", file=sys.stderr)
    return added

def main():
    import threading
    servers = fetch_servers()
    print(f"{len(servers)} serveurs FR/GLOBAL a scanner", file=sys.stderr)
    registry, lock, total = {}, threading.Lock(), 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(ingest_server, s, registry, lock) for s in servers]
        for f in as_completed(futs):
            total += f.result() or 0
    payload = {
        "savedAt": int(time.time() * 1000),
        "generatedBy": "nx-data cron (refresh_vegetatv.py)",
        "channels": registry,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"OK: {len(registry)} chaines, {total} flux -> {OUT_PATH}", file=sys.stderr)

if __name__ == "__main__":
    main()
