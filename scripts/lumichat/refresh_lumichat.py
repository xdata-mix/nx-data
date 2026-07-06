#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_lumichat : recupere les chaines LumiChat (gateway.lumichat.fun),
filtre par pays, vire les sources connues mortes, et regenere data-lumichat.m3u.

IMPORTANT : on NE PROBE PLUS la gateway resolve (elle est trop instable,
renvoie success=false pour tout depuis un runner cloud). On fait confiance
a la liste API : si la gateway liste une chaine, on l'inclut. La resolution
se fait a la volee dans l'app (LumiChatResolver). Les chaines mortes echouent
au runtime — le multi-serveur de l'app essaie la source suivante.

RESILIENCE :
  - API OK  -> regenere le M3U complet (toutes les chaines FR).
  - API KO  -> conserve le M3U existant intact (exit 0).
  - 0 chaines apres filtre -> conserve le M3U existant (anormal, exit 0).

Env: LUMI_OUT, LUMI_COUNTRY(FR), LUMI_SKIP_DEAD(1), LUMI_API_TIMEOUT(25),
     LUMI_API_TRIES(3)
"""
import os, time
import requests

BASE = "https://gateway.lumichat.fun"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
OUT = os.environ.get("LUMI_OUT", "data-lumichat.m3u")
COUNTRY = os.environ.get("LUMI_COUNTRY", "FR").upper()
SKIP_DEAD = os.environ.get("LUMI_SKIP_DEAD", "1") == "1"
DEAD_SRC_PREFIXES = ("vavoo", "livewatch")
API_TIMEOUT = int(os.environ.get("LUMI_API_TIMEOUT", "25"))
API_TRIES = int(os.environ.get("LUMI_API_TRIES", "3"))


def parse_existing_m3u(path):
    """Compte les chaines du M3U existant (pour le garde-fou)."""
    if not os.path.exists(path):
        return 0
    count = 0
    for line in open(path, encoding="utf-8"):
        if line.strip().startswith("lumichat://"):
            count += 1
    return count


def fetch_channels():
    """Retourne (channels, from_api). from_api=False => gateway injoignable."""
    last = None
    for attempt in range(API_TRIES):
        try:
            r = requests.get(
                f"{BASE}/api/categories",
                headers={
                    "Accept": "application/json",
                    "User-Agent": UA,
                    "Origin": "https://frenchtv.vdfr.uk",
                    "Referer": "https://frenchtv.vdfr.uk/",
                },
                timeout=API_TIMEOUT,
            )
            r.raise_for_status()
            cats = r.json().get("categories") or {}
            it = cats.values() if isinstance(cats, dict) else cats
            out = []
            for cat in it:
                for ch in cat.get("channels", []):
                    cid = ch.get("channel_id")
                    if not cid:
                        continue
                    out.append({
                        "id": cid,
                        "name": ch.get("channel_name") or cid,
                        "group": ch.get("category_name") or cat.get("name") or "LumiChat",
                        "logo": ch.get("logo_url") or "",
                        "cc": ch.get("country_code") or "",
                    })
            print(f"[lumichat] API OK: {len(out)} chaines recuperees", flush=True)
            return out, True
        except Exception as e:
            last = e
            print(f"[lumichat] fetch essai {attempt+1}/{API_TRIES} KO ({e}) - retry {5*(attempt+1)}s", flush=True)
            time.sleep(5 * (attempt + 1))
    print(f"[lumichat] API INJOIGNABLE ({last}). Gateway down/filtree -> pas de regeneration.", flush=True)
    return [], False


def keep_existing(reason):
    """Ne touche PAS au M3U si present & non vide ; sinon ecrit un placeholder."""
    existing = parse_existing_m3u(OUT)
    if os.path.exists(OUT) and existing > 0:
        print(f"[lumichat] {reason} -> M3U existant conserve intact ({existing} chaines). exit 0", flush=True)
        return
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    if not os.path.exists(OUT):
        open(OUT, "w", encoding="utf-8").write("#EXTM3U\n")
        print(f"[lumichat] {reason} et aucun M3U -> placeholder ecrit. exit 0", flush=True)
    else:
        print(f"[lumichat] {reason} -> M3U existant (vide) laisse tel quel. exit 0", flush=True)


def main():
    t0 = time.time()

    # 1. Recuperer toutes les chaines depuis l'API
    chans, from_api = fetch_channels()
    if not from_api:
        keep_existing("Gateway injoignable")
        return

    # 2. Filtre pays
    if COUNTRY and COUNTRY != "ALL":
        before = len(chans)
        chans = [c for c in chans if (c.get("cc") or "").upper() == COUNTRY]
        print(f"[lumichat] filtre pays={COUNTRY}: {before} -> {len(chans)} chaines", flush=True)

    # 3. Virer les sources connues mortes (vavoo, livewatch = 0% de succes)
    if SKIP_DEAD:
        before = len(chans)
        chans = [c for c in chans if not c["id"].lower().startswith(DEAD_SRC_PREFIXES)]
        print(f"[lumichat] skip sources mortes ({', '.join(DEAD_SRC_PREFIXES)}): {before} -> {len(chans)} chaines", flush=True)

    if not chans:
        keep_existing("API OK mais 0 chaine apres filtre (anormal)")
        return

    # 4. Garde-fou : si le nouveau M3U a BEAUCOUP MOINS que l'existant,
    #    c'est peut-etre un bug API -> on garde l'ancien.
    existing_count = parse_existing_m3u(OUT)
    if existing_count > 50 and len(chans) < existing_count * 0.3:
        keep_existing(f"API a renvoye seulement {len(chans)} chaines vs {existing_count} existantes (baisse >70%, suspect)")
        return

    # 5. Generer le M3U (TOUTES les chaines, pas de probe)
    lines = ["#EXTM3U"]
    for c in chans:
        lines.append(
            f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-logo="{c["logo"]}" '
            f'group-title="{c["group"]}" tvg-country="{c["cc"]}",{c["name"]}'
        )
        lines.append(f'lumichat://{c["id"]}')

    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    dt = int(time.time() - t0)
    print(f"[lumichat] TERMINE : {len(chans)} chaines FR ecrites en {dt}s -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_lumichat : teste EN AMONT les chaines LumiChat (gateway.lumichat.fun),
ne garde que celles qui RESOLVENT (success + url), et regenere data-lumichat.m3u.
Meme fingerprint que l'app (Chrome/131 + Origin/Referer frenchtv.vdfr.uk).

RESILIENCE (2026-07-05) : la gateway est souvent injoignable depuis un runner
cloud (IP datacenter filtree) OU carrement down. Dans ce cas le script NE DOIT
JAMAIS :
  - planter (exit != 0)  -> plus de run rouge
  - ecraser le M3U existant par du vide -> on garde toujours la derniere bonne liste
Comportement :
  - gateway OK    -> re-probe + regenere le M3U trie.
  - gateway KO    -> conserve le M3U existant intact (exit 0). Si aucun M3U -> ecrit
                     un placeholder "#EXTM3U" valide (exit 0).
Env: LUMI_LIMIT(0), LUMI_WORKERS(20), LUMI_TIMEOUT(30), LUMI_OUT, LUMI_COUNTRY(FR),
     LUMI_SKIP_DEAD(1), LUMI_API_TIMEOUT(25), LUMI_API_TRIES(3)
"""
import os, re, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

BASE = "https://gateway.lumichat.fun"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HEADERS = {"Accept": "*/*", "User-Agent": UA, "Origin": "https://frenchtv.vdfr.uk", "Referer": "https://frenchtv.vdfr.uk/"}
LIMIT   = int(os.environ.get("LUMI_LIMIT", "0"))
WORKERS = int(os.environ.get("LUMI_WORKERS", "20"))
TIMEOUT = int(os.environ.get("LUMI_TIMEOUT", "30"))
OUT     = os.environ.get("LUMI_OUT", "data-lumichat.m3u")
COUNTRY = os.environ.get("LUMI_COUNTRY", "FR").upper()
SKIP_DEAD = os.environ.get("LUMI_SKIP_DEAD", "1") == "1"
DEAD_SRC_PREFIXES = ("vavoo", "livewatch")
API_TIMEOUT = int(os.environ.get("LUMI_API_TIMEOUT", "25"))
API_TRIES   = int(os.environ.get("LUMI_API_TRIES", "3"))

def parse_existing_m3u(path):
    if not os.path.exists(path):
        return []
    out, pending = [], None
    for line in open(path, encoding="utf-8"):
        t = line.strip()
        if t.startswith("#EXTINF"):
            pending = t
        elif pending and t.startswith("lumichat://"):
            cid = t[len("lumichat://"):]
            def g(a):
                m = re.search(a + r'="([^"]*)"', pending); return m.group(1) if m else ""
            name = pending.rsplit(",", 1)[-1].strip()
            out.append({"id": cid, "name": name or cid, "group": g("group-title") or "LumiChat",
                        "logo": g("tvg-logo"), "cc": g("tvg-country")})
            pending = None
        elif not t.startswith("#"):
            pending = None
    return out

def fetch_channels():
    """Retourne (channels, from_api). from_api=False => gateway injoignable."""
    last = None
    for attempt in range(API_TRIES):
        try:
            r = requests.get(f"{BASE}/api/categories",
                             headers={"Accept": "application/json", "User-Agent": UA,
                                      "Origin": "https://frenchtv.vdfr.uk", "Referer": "https://frenchtv.vdfr.uk/"},
                             timeout=API_TIMEOUT)
            r.raise_for_status()
            cats = (r.json().get("categories") or {})
            it = cats.values() if isinstance(cats, dict) else cats
            out = []
            for cat in it:
                for ch in cat.get("channels", []):
                    cid = ch.get("channel_id")
                    if not cid: continue
                    out.append({"id": cid, "name": ch.get("channel_name") or cid,
                                "group": ch.get("category_name") or cat.get("name") or "LumiChat",
                                "logo": ch.get("logo_url") or "", "cc": ch.get("country_code") or ""})
            print(f"[lumichat] API OK: {len(out)} chaines recuperees", flush=True)
            return out, True
        except Exception as e:
            last = e
            print(f"[lumichat] fetch essai {attempt+1}/{API_TRIES} KO ({e}) - retry {5*(attempt+1)}s", flush=True)
            time.sleep(5 * (attempt + 1))
    print(f"[lumichat] API INJOIGNABLE ({last}). Gateway down/filtree -> pas de regeneration.", flush=True)
    return [], False

def resolve_ok(cid):
    enc = urllib.parse.quote(cid, safe="")
    u = f"{BASE}/api/python-stream/{enc}?force=1&_={int(time.time()*1000)}"
    for _ in range(2):
        try:
            r = requests.get(u, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200: return False
            j = r.json()
            return bool(j.get("success") and j.get("url"))
        except Exception:
            time.sleep(1)
    return False

def keep_existing(reason):
    """Ne touche PAS au M3U si present & non vide ; sinon ecrit un placeholder valide."""
    existing = parse_existing_m3u(OUT)
    if os.path.exists(OUT) and existing:
        print(f"[lumichat] {reason} -> M3U existant conserve intact ({len(existing)} chaines). exit 0", flush=True)
        return
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    if not os.path.exists(OUT):
        open(OUT, "w", encoding="utf-8").write("#EXTM3U\n")
        print(f"[lumichat] {reason} et aucun M3U -> placeholder '#EXTM3U' ecrit. exit 0", flush=True)
    else:
        print(f"[lumichat] {reason} -> M3U existant (vide) laisse tel quel. exit 0", flush=True)

def main():
    t0 = time.time()
    chans, from_api = fetch_channels()
    if not from_api:
        keep_existing("Gateway injoignable")
        return
    if COUNTRY and COUNTRY != "ALL":
        chans = [c for c in chans if (c.get("cc") or "").upper() == COUNTRY]
    if SKIP_DEAD:
        chans = [c for c in chans if not c["id"].lower().startswith(DEAD_SRC_PREFIXES)]
    print(f"[lumichat] pre-filtre pays={COUNTRY} skip_morts={SKIP_DEAD} -> {len(chans)} a tester", flush=True)
    if LIMIT > 0: chans = chans[:LIMIT]
    total = len(chans)
    if total == 0:
        keep_existing("API OK mais 0 chaine apres filtre (anormal)")
        return
    print(f"[lumichat] {total} chaines a tester (workers={WORKERS}, timeout={TIMEOUT}s)", flush=True)
    working, done = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(resolve_ok, c["id"]): c for c in chans}
        for fut in as_completed(futs):
            c = futs[fut]
            try: ok = fut.result()
            except Exception: ok = False
            if ok: working.append(c)
            done += 1
            if done % 200 == 0 or done == total:
                print(f"[lumichat] {done}/{total} testees, {len(working)} OK ({int(time.time()-t0)}s)", flush=True)
    if not working:
        keep_existing("API OK mais 0 vivante ce run (gateway instable ?)")
        return
    order = {c["id"]: i for i, c in enumerate(chans)}
    working.sort(key=lambda c: order[c["id"]])
    lines = ["#EXTM3U"]
    for c in working:
        lines.append(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-logo="{c["logo"]}" group-title="{c["group"]}" tvg-country="{c["cc"]}",{c["name"]}')
        lines.append(f'lumichat://{c["id"]}')
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"[lumichat] TERMINE : {len(working)}/{total} vivantes gardees en {int(time.time()-t0)}s -> {OUT}", flush=True)

if __name__ == "__main__":
    main()
