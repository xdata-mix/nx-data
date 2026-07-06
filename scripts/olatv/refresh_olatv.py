#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_olatv.py — Probe de liveness des serveurs Ola TV (nx-data).

Objectif : l'app teste ~1269 cids à chaque ouverture alors que la plupart sont
morts. Ce script tourne en amont (GitHub Actions), teste chaque serveur, et publie
data/olatv/live-cids.json avec la liste des cids VIVANTS. L'app filtre dessus →
chargement rapide, plus de serveurs morts.

Protocole Ola TV (reproduit) :
  - Endpoint : http://iptvdroid.monster/IP11/api.php
  - Crypto   : AES-128-CBC, key=iv="3234567890123453", padding PKCS7
  - Requête  : POST form field data=base64(AES(JSON))
               JSON = {"salt": <0-899>, "sign": MD5("MRZEREZIS"+salt), + champs méthode}
  - Réponse  : base64(AES(JSON)) → déchiffrer → JSON
  - Méthodes :
      * newolatvcategory0326 → liste serveurs [{cid, category_name, token1}]
        (category_name "2020" = France)
      * getToken128910 + cid → {token1: portal URL, token2: MAC}
  - Liveness (Stalker) : GET <portal>/portal.php?type=stb&action=handshake&token=&JsHttpRequest=1-xml
        Cookie: mac=<MAC>; stb_lang=en; timezone=Europe/London
        → js.token non vide = serveur VIVANT.
"""
import base64, hashlib, json, os, random, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

API_URL   = "http://iptvdroid.monster/IP11/api.php"
AES_KEY   = b"3234567890123453"
AES_IV    = b"3234567890123453"
SIGN_SALT = "MRZEREZIS"

FR_CATEGORY = os.environ.get("OLA_FR_CATEGORY", "2020")  # "2020" = France
ONLY_FR     = os.environ.get("OLA_ONLY_FR", "1") == "1"
OUT_PATH    = os.environ.get("OLA_OUT", "data/olatv/live-cids.json")
MAX_WORKERS = int(os.environ.get("OLA_WORKERS", "24"))
PROBE_TMO   = int(os.environ.get("OLA_PROBE_TIMEOUT", "8"))
API_TMO     = int(os.environ.get("OLA_API_TIMEOUT", "25"))

MAG_UA = ("Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 "
          "(KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3")

def enc(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    ct = AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(raw, 16))
    return base64.b64encode(ct).decode()

def dec(b64: str) -> dict:
    ct = base64.b64decode(b64)
    pt = unpad(AES.new(AES_KEY, AES.MODE_CBC, AES_IV).decrypt(ct), 16)
    return json.loads(pt.decode("utf-8", "replace"))

def api_call(extra: dict, tries: int = 4) -> dict | None:
    salt = random.randint(0, 899)
    body = {"salt": salt, "sign": hashlib.md5((SIGN_SALT + str(salt)).encode()).hexdigest()}
    body.update(extra)
    data = enc(body)
    for attempt in range(1, tries + 1):
        try:
            r = requests.post(API_URL, data={"data": data},
                              headers={"User-Agent": "okhttp/3.12.1",
                                       "Content-Type": "application/x-www-form-urlencoded"},
                              timeout=API_TMO)
            if r.status_code != 200:
                print(f"  api {extra} → HTTP {r.status_code} (try {attempt})", flush=True)
                time.sleep(2 * attempt); continue
            txt = r.text.strip()
            decoded = None
            try:
                decoded = dec(txt)
            except Exception:
                try:
                    decoded = json.loads(txt)
                except Exception as e:
                    print(f"  [diag] api {extra} status={r.status_code} rawlen={len(txt)} DECODE_KO={e} head={txt[:160]!r}", flush=True)
                    return None
            # DIAG : montre la structure reelle renvoyee par l'API
            try:
                if isinstance(decoded, dict):
                    ks = list(decoded.keys())
                else:
                    ks = f"type={type(decoded).__name__} len={len(decoded) if hasattr(decoded,'__len__') else '?'}"
                print(f"  [diag] api {extra} status={r.status_code} rawlen={len(txt)} decoded_keys={ks} sample={str(decoded)[:220]}", flush=True)
            except Exception:
                pass
            return decoded
        except Exception as e:
            print(f"  api {extra} → net KO: {e} (try {attempt})", flush=True)
            time.sleep(2 * attempt)
    return None

def get_servers() -> list[dict]:
    """Retourne [{cid, category, token1}]."""
    for method_key in ("method", "action", "type", "0"):
        resp = api_call({method_key: "newolatvcategory0326"})
        if resp:
            items = _extract_servers(resp)
            if items:
                print(f"[servers] méthode via clé '{method_key}' → {len(items)} serveurs", flush=True)
                return items
    print("[servers] AUCUN serveur récupéré (format à ajuster)", flush=True)
    return []

def _extract_servers(resp) -> list[dict]:
    # Cherche récursivement une liste de dicts contenant cid.
    out = []
    def walk(node):
        if isinstance(node, list):
            for x in node: walk(x)
        elif isinstance(node, dict):
            if "cid" in node:
                out.append({
                    "cid": str(node.get("cid")),
                    "category": str(node.get("category_name", node.get("cat", ""))),
                    "token1": node.get("token1", ""),
                })
            for v in node.values(): walk(v)
    walk(resp)
    # dédup par cid
    seen, uniq = set(), []
    for s in out:
        if s["cid"] and s["cid"] not in seen:
            seen.add(s["cid"]); uniq.append(s)
    return uniq

def get_token(cid: str) -> tuple[str, str] | None:
    for method_key in ("method", "action", "type", "0"):
        resp = api_call({method_key: "getToken128910", "cid": cid})
        if resp:
            portal, mac = _extract_token(resp)
            if portal and mac:
                return portal, mac
    return None

def _extract_token(resp):
    portal = mac = ""
    def walk(node):
        nonlocal portal, mac
        if isinstance(node, dict):
            if node.get("token1"): portal = node["token1"]
            if node.get("token2"): mac = node["token2"]
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for x in node: walk(x)
    walk(resp)
    return portal, mac

def is_alive(cid: str, token1: str) -> bool:
    """token1 peut être direct (portal+mac) ou nécessiter getToken. Probe Stalker handshake."""
    tok = None
    if isinstance(token1, str) and token1.startswith("http"):
        # token1 = portal, MAC inconnu → passer par getToken
        tok = get_token(cid)
    else:
        tok = get_token(cid)
    if not tok:
        return False
    portal, mac = tok
    base = portal.rstrip("/")
    if not base.endswith("/c") and "/portal.php" not in base and "/stalker_portal" not in base:
        base = base + "/c"
    # essaie quelques chemins de portal courants
    paths = ["/portal.php", "/stalker_portal/server/load.php", "/server/load.php"]
    root = base[:-2] if base.endswith("/c") else base
    for p in paths:
        url = root + p + "?type=stb&action=handshake&token=&JsHttpRequest=1-xml"
        try:
            r = requests.get(url, headers={
                "User-Agent": MAG_UA,
                "Cookie": f"mac={mac}; stb_lang=en; timezone=Europe/London",
                "Referer": root + "/c/",
            }, timeout=PROBE_TMO)
            if r.status_code == 200 and '"token"' in r.text:
                j = json.loads(r.text)
                if str(j.get("js", {}).get("token", "")).strip():
                    return True
        except Exception:
            continue
    return False

def main():
    t0 = time.time()
    servers = get_servers()
    if ONLY_FR:
        servers = [s for s in servers if s["category"] == FR_CATEGORY]
        print(f"[filter] France ({FR_CATEGORY}) → {len(servers)} serveurs", flush=True)
    if not servers:
        print("ERREUR : aucun serveur à tester. Sortie sans écraser la liste.", flush=True)
        sys.exit(1)

    alive = []
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(is_alive, s["cid"], s["token1"]): s for s in servers}
        for fut in as_completed(futs):
            s = futs[fut]
            done += 1
            try:
                ok = fut.result()
            except Exception:
                ok = False
            if ok:
                alive.append(s["cid"])
            if done % 50 == 0 or done == len(servers):
                print(f"  probe {done}/{len(servers)} — vivants={len(alive)}", flush=True)

    alive = sorted(set(alive), key=lambda x: (len(x), x))
    payload = {
        "generated_at": int(time.time()),
        "category": FR_CATEGORY if ONLY_FR else "all",
        "total": len(servers),
        "alive": len(alive),
        "cids": alive,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"[done] {len(alive)}/{len(servers)} vivants en {int(time.time()-t0)}s → {OUT_PATH}", flush=True)

if __name__ == "__main__":
    main()
