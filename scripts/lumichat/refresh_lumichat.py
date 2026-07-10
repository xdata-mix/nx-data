#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_lumichat : recupere les chaines LumiChat (gateway.lumichat.fun),
filtre par pays, vire les sources connues mortes, et regenere data-lumichat.m3u.

IMPORTANT : on NE PROBE PLUS la gateway resolve (elle est trop instable,
renvoie success=false pour tout depuis un runner cloud). On fait confiance
a la liste API : si la gateway liste une chaine, on l'inclut. La resolution
se fait a la volee dans l'app (LumiChatResolver). Les chaines mortes echouent
au runtime -- le multi-serveur de l'app essaie la source suivante.

RESILIENCE :
  - API OK  -> regenere le M3U complet (toutes les chaines FR).
  - API KO  -> conserve le M3U existant intact (exit 0).
  - 0 chaines apres filtre -> conserve le M3U existant (anormal, exit 0).

Env: LUMI_OUT, LUMI_COUNTRY(FR), LUMI_SKIP_DEAD(1), LUMI_API_TIMEOUT(25),
     LUMI_API_TRIES(3)
"""
import os, time
import requests

BASE  = "https://gateway.lumichat.fun"
UA    = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
OUT   = os.environ.get("LUMI_OUT", "data-lumichat.m3u")
COUNTRY   = os.environ.get("LUMI_COUNTRY", "FR").upper()
SKIP_DEAD = os.environ.get("LUMI_SKIP_DEAD", "1") == "1"
PROVIDERS = [p.strip() for p in os.environ.get("LUMI_PROVIDERS", "ELITE").upper().split(",") if p.strip()]
DEAD_SRC_PREFIXES = ("vavoo",)
# 2026-07-10 : filtre par source_type en plus du prefixe ID.
# Les IDs sont tous livewatch-* donc le prefixe ne capte rien.
# Le champ source_type de l'API indique la source upstream reelle.
DEAD_SOURCE_TYPES = ("vavoo",)

# Ordre de tri des groupes (groupes francais prioritaires en tete)
GROUP_ORDER = {
  "Info": 0,
  "Divertissement": 1,
  "Cinema": 2,
  "Sports": 3,
  "Documentaires": 4,
  "Musique": 5,
  "General": 6,
}
API_TIMEOUT = int(os.environ.get("LUMI_API_TIMEOUT", "25"))
API_TRIES   = int(os.environ.get("LUMI_API_TRIES",   "3"))

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
            "id":    cid,
            "name":  ch.get("channel_name") or cid,
            "group": ch.get("category_name") or cat.get("name") or "LumiChat",
            "logo":  ch.get("logo_url") or "",
            "cc":    ch.get("country_code") or "",
            "provider": (ch.get("provider") or "").upper(),
            "source_type": (ch.get("source_type") or "").lower(),
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

  # 1b. Log distribution source_type (diagnostic)
  st_dist = {}
  for c in chans:
    st = c.get("source_type") or "unknown"
    st_dist[st] = st_dist.get(st, 0) + 1
  print(f"[lumichat] source_type distribution: {st_dist}", flush=True)

  # 2. Filtre pays
  if COUNTRY and COUNTRY != "ALL":
    before = len(chans)
    chans = [c for c in chans if (c.get("cc") or "").upper() == COUNTRY]
    print(f"[lumichat] filtre pays={COUNTRY}: {before} -> {len(chans)} chaines", flush=True)

  # 3. Filtre par provider (seul ELITE resolve, les autres = success=false)
  if PROVIDERS:
    before_prov = len(chans)
    chans = [c for c in chans if c.get("provider", "") in PROVIDERS]
    print(f"[lumichat] filtre providers={PROVIDERS}: {before_prov} -> {len(chans)} chaines", flush=True)

  # 4. Virer les sources connues mortes
  if SKIP_DEAD:
    before = len(chans)
    # 4a. Par prefixe d'ID (ancien filtre, garde pour compat)
    chans = [c for c in chans if not c["id"].lower().startswith(DEAD_SRC_PREFIXES)]
    after_id = len(chans)
    # 4b. Par source_type (nouveau filtre 2026-07-10 -- les IDs sont tous
    #     livewatch-* donc le prefixe ne capte rien ; source_type est fiable)
    chans = [c for c in chans if c.get("source_type", "") not in DEAD_SOURCE_TYPES]
    after_st = len(chans)
    print(f"[lumichat] skip sources mortes: {before} -> {after_id} (prefixe ID) -> {after_st} (source_type)", flush=True)

  if not chans:
    keep_existing("API OK mais 0 chaine apres filtre (anormal)")
    return

  # 5. Deduplication par nom (premiere occurrence gardee, les doublons
  #    pointent souvent vers des flux etrangers avec le meme nom)
  seen_names = set()
  deduped = []
  for c in chans:
    key = c["name"].strip().upper()
    if key not in seen_names:
      seen_names.add(key)
      deduped.append(c)
  if len(deduped) < len(chans):
    print(f"[lumichat] dedup noms: {len(chans)} -> {len(deduped)} chaines", flush=True)
  chans = deduped

  # 6. Tri par categorie (groupes francais prioritaires) puis par nom
  chans.sort(key=lambda c: (GROUP_ORDER.get(c["group"], 99), c["name"].upper()))

  # 7. Garde-fou : si le nouveau M3U a BEAUCOUP MOINS que l'existant,
  #    c'est peut-etre un bug API -> on garde l'ancien.
  existing_count = parse_existing_m3u(OUT)
  if existing_count > 50 and len(chans) < existing_count * 0.3:
    keep_existing(f"API a renvoye seulement {len(chans)} chaines vs {existing_count} existantes (baisse >70%, suspect)")
    return

  # 8. Generer le M3U (TOUTES les chaines, pas de probe)
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
refresh_lumichat : recupere les chaines LumiChat (gateway.lumichat.fun),
filtre par pays, vire les sources connues mortes, et regenere data-lumichat.m3u.

IMPORTANT : on NE PROBE PLUS la gateway resolve (elle est trop instable,
renvoie success=false pour tout depuis un runner cloud). On fait confiance
a la liste API : si la gateway liste une chaine, on l'inclut. La resolution
se fait a la volee dans l'app (LumiChatResolver). Les chaines mortes echouent
au runtime -- le multi-serveur de l'app essaie la source suivante.

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
PROVIDERS = [p.strip() for p in os.environ.get("LUMI_PROVIDERS", "ELITE").upper().split(",") if p.strip()]
DEAD_SRC_PREFIXES = ("vavoo",)

# Ordre de tri des groupes (groupes francais prioritaires en tete)
GROUP_ORDER = {
  "Info": 0,
  "Divertissement": 1,
  "Cinema": 2,
  "Sports": 3,
  "Documentaires": 4,
  "Musique": 5,
  "General": 6,
}
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
                        "provider": (ch.get("provider") or "").upper(),
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

    # 3. Filtre par provider (seul ELITE resolve, les autres = success=false)
    if PROVIDERS:
        before_prov = len(chans)
        chans = [c for c in chans if c.get("provider", "") in PROVIDERS]
        print(f"[lumichat] filtre providers={PROVIDERS}: {before_prov} -> {len(chans)} chaines", flush=True)

    # 4. Virer les sources connues mortes (vavoo = 0% de succes)
    if SKIP_DEAD:
        before = len(chans)
        chans = [c for c in chans if not c["id"].lower().startswith(DEAD_SRC_PREFIXES)]
        print(f"[lumichat] skip sources mortes ({', '.join(DEAD_SRC_PREFIXES)}): {before} -> {len(chans)} chaines", flush=True)

    if not chans:
        keep_existing("API OK mais 0 chaine apres filtre (anormal)")
        return

    # 5. Deduplication par nom (premiere occurrence gardee, les doublons
    #    pointent souvent vers des flux etrangers avec le meme nom)
    seen_names = set()
    deduped = []
    for c in chans:
        key = c["name"].strip().upper()
        if key not in seen_names:
            seen_names.add(key)
            deduped.append(c)
    if len(deduped) < len(chans):
        print(f"[lumichat] dedup noms: {len(chans)} -> {len(deduped)} chaines", flush=True)
    chans = deduped

    # 6. Tri par categorie (groupes francais prioritaires) puis par nom
    chans.sort(key=lambda c: (GROUP_ORDER.get(c["group"], 99), c["name"].upper()))

    # 7. Garde-fou : si le nouveau M3U a BEAUCOUP MOINS que l'existant,
    #    c'est peut-etre un bug API -> on garde l'ancien.
    existing_count = parse_existing_m3u(OUT)
    if existing_count > 50 and len(chans) < existing_count * 0.3:
        keep_existing(f"API a renvoye seulement {len(chans)} chaines vs {existing_count} existantes (baisse >70%, suspect)")
        return

    # 8. Generer le M3U (TOUTES les chaines, pas de probe)
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
