#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_olatv.py — Probe de liveness des serveurs Ola TV (nx-data).

Publie data/olatv/live-cids.json (cids VIVANTS) pour que l'app filtre dessus et
arrete de tester ~1269 serveurs a chaque ouverture.

Protocole EXACT (repris de OlaTvProvider.kt de l'app) :
  - Endpoint : http://iptvdroid.monster/IP11/api.php
  - Requete  : POST form data = base64(JSON_PLAINTEXT)   <-- PAS d'AES sur la requete !
               JSON = {"salt": <str 0-899>, "sign": md5("MRZEREZIS"+salt), "method_name": <m>, ...}
  - Reponse  : si commence par [ ou { => JSON clair ; sinon AES-CBC decrypt
               (key=iv="3234567890123453" PKCS5) puis JSON.  -> {"LIVETV":[...]} ou [ ... ]
  - Methodes : newolatvcategory0326 -> liste [{cid, category_name}]
               getToken128910 (+cid) -> LIVETV[0]{token1, token2}
  - token1 : base64 -> ISO-8859-1 -> substring depuis "http" = portal baseUrl
  - token2 : base64 -> ISO-8859-1 -> regex MAC XX:XX:XX:XX:XX:XX
  - Liveness : GET <baseUrl>/portal.php?type=stb&action=handshake&token=&JsHttpRequest=1-xml
               Cookie mac=<urlenc>; stb_lang=en; timezone=Europe%2FLondon
               -> js.token non vide = VIVANT.
"""
import base64, hashlib, json, os, random, re, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

API_URL   = "http://iptvdroid.monster/IP11/api.php"
AES_KEY   = b"3234567890123453"
SECRET    = "MRZEREZIS"

FR_CATEGORY = os.environ.get("OLA_FR_CATEGORY", "2020")
ONLY_FR     = os.environ.get("OLA_ONLY_FR", "1") == "1"
OUT_PATH    = os.environ.get("OLA_OUT", "data/olatv/live-cids.json")
MAX_WORKERS = int(os.environ.get("OLA_WORKERS", "24"))
PROBE_TMO   = int(os.environ.get("OLA_PROBE_TIMEOUT", "8"))
API_TMO     = int(os.environ.get("OLA_API_TIMEOUT", "25"))

MAG_UA = ("Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 "
          "(KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3")

def build_payload(method_name: str, extras: dict | None = None) -> str:
    salt = str(random.randint(0, 899))
    sign = hashlib.md5((SECRET + salt).encode()).hexdigest()
    obj = {"salt": salt, "sign": sign, "method_name": method_name}
    if extras:
        obj.update(extras)
    return base64.b64encode(json.dumps(obj).encode("utf-8")).decode()

def api_json(method_name: str, extras: dict | None = None, tries: int = 4):
    for attempt in range(1, tries + 1):
        try:
            b64 = build_payload(method_name, extras)
            r = requests.post(API_URL, data={"data": b64},
                              headers={"User-Agent": "okhttp/3.12.1"}, timeout=API_TMO)
            if r.status_code != 200:
                time.sleep(1.5 * attempt); continue
            body = r.text.strip()
            if body.startswith("[") or body.startswith("{"):
                txt = body
            else:
                ct = base64.b64decode(body)
                txt = unpad(AES.new(AES_KEY, AES.MODE_CBC, AES_KEY).decrypt(ct), 16).decode("utf-8", "replace")
            return json.loads(txt)
        except Exception as e:
            if attempt == tries:
                print(f"  api {method_name} KO: {e}", flush=True)
            time.sleep(1.5 * attempt)
    return None

def _as_list(resp):
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        lv = resp.get("LIVETV")
        if isinstance(lv, list):
            return lv
        return [resp]
    return []

def get_servers() -> list:
    resp = api_json("newolatvcategory0326")
    items = _as_list(resp)
    out, cats = [], {}
    for srv in items:
        if not isinstance(srv, dict):
            continue
        cid = str(srv.get("cid", "")).strip()
        cat = str(srv.get("category_name", "")).strip()
        if cid and cat:
            out.append({"cid": cid, "category": cat})
            cats[cat] = cats.get(cat, 0) + 1
    if out:
        top = sorted(cats.items(), key=lambda kv: -kv[1])[:12]
        print(f"[servers] {len(out)} serveurs ; categories (top): {top}", flush=True)
    else:
        print(f"[servers] AUCUN serveur. Reponse brute: {str(resp)[:300]}", flush=True)
    return out

def _decode_token1(t1: str):
    try:
        text = base64.b64decode(t1).decode("iso-8859-1")
        i = text.find("http")
        return text[i:].strip() if i >= 0 else None
    except Exception:
        return None

def _decode_token2(t2: str):
    try:
        text = base64.b64decode(t2).decode("iso-8859-1")
        m = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", text)
        return m.group(1) if m else None
    except Exception:
        return None

def get_mac(cid: str):
    resp = api_json("getToken128910", {"cid": cid})
    if not resp:
        return None
    target = None
    if isinstance(resp, dict):
        lv = resp.get("LIVETV")
        target = (lv[0] if isinstance(lv, list) and lv else resp)
    elif isinstance(resp, list) and resp:
        target = resp[0]
    if not isinstance(target, dict):
        return None
    t1, t2 = target.get("token1", ""), target.get("token2", "")
    if not t1 or not t2:
        return None
    base = _decode_token1(t1)
    mac = _decode_token2(t2)
    if not base or not mac:
        return None
    return base, mac

def is_alive(cid: str) -> bool:
    creds = get_mac(cid)
    if not creds:
        return False
    base, mac = creds
    portal = base.rstrip("/") + "/portal.php"
    cookie = f"mac={urllib.parse.quote(mac)}; stb_lang=en; timezone=Europe%2FLondon"
    try:
        r = requests.get(portal + "?type=stb&action=handshake&token=&JsHttpRequest=1-xml",
                         headers={"User-Agent": MAG_UA, "Cookie": cookie}, timeout=PROBE_TMO)
        if r.status_code == 200 and '"token"' in r.text:
            j = json.loads(r.text)
            return bool(str(j.get("js", {}).get("token", "")).strip())
    except Exception:
        pass
    return False

def main():
    t0 = time.time()
    servers = get_servers()
    if ONLY_FR:
        fr = [s for s in servers if s["category"] == FR_CATEGORY]
        print(f"[filter] France (category_name='{FR_CATEGORY}') -> {len(fr)}/{len(servers)} serveurs", flush=True)
        servers = fr
    if not servers:
        print("ERREUR : aucun serveur a tester. Sortie sans ecraser la liste.", flush=True)
        sys.exit(1)

    alive, done = [], 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(is_alive, s["cid"]): s for s in servers}
        for fut in as_completed(futs):
            s = futs[fut]; done += 1
            try: ok = fut.result()
            except Exception: ok = False
            if ok: alive.append(s["cid"])
            if done % 50 == 0 or done == len(servers):
                print(f"  probe {done}/{len(servers)} vivants={len(alive)}", flush=True)

    alive = sorted(set(alive), key=lambda x: (len(x), x))
    payload = {"generated_at": int(time.time()),
               "category": FR_CATEGORY if ONLY_FR else "all",
               "total": len(servers), "alive": len(alive), "cids": alive}
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"[done] {len(alive)}/{len(servers)} vivants en {int(time.time()-t0)}s -> {OUT_PATH}", flush=True)

if __name__ == "__main__":
    main()
