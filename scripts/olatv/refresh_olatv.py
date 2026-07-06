#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_olatv.py — Probe de liveness des serveurs Ola TV (nx-data).

Publie data/olatv/live-cids.json (liste des cids VIVANTS) pour que l'app filtre
dessus et arrete de tester ~1269 serveurs a chaque ouverture.

Protocole (verifie via diag GitHub Actions) :
  - Endpoint : http://iptvdroid.monster/IP11/api.php
  - Crypto   : AES-128-CBC key=iv="3234567890123453" PKCS7  (VALIDE : reponse decode OK)
  - Requete  : POST form data=base64(AES(JSON))   JSON = {salt, sign, <method_key>:<method>}
  - Reponse  : base64(AES(JSON)) -> {"LIVETV":[...]}
  - Le serveur valide d'abord le 'sign' (derive du salt). On SONDE les schemas de
    sign/salt/method_key jusqu'a en trouver un accepte (plus de "Invalid sign salt").
  - Methodes : newolatvcategory0326 (liste serveurs cid/category/token1 ; "2020"=FR)
               getToken128910 + cid  (token1=portal, token2=MAC)
  - Liveness : Stalker handshake GET <portal>/portal.php?...action=handshake...
               Cookie mac=<MAC> -> js.token non vide = VIVANT.
"""
import base64, hashlib, json, os, random, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

API_URL   = "http://iptvdroid.monster/IP11/api.php"
AES_KEY   = b"3234567890123453"
AES_IV    = b"3234567890123453"
SIGN_SALT = "MRZEREZIS"

FR_CATEGORY = os.environ.get("OLA_FR_CATEGORY", "2020")
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

def _md5(x: str) -> str:
    return hashlib.md5(x.encode()).hexdigest()

SALT_REPRS = {
    "int": lambda s: s,
    "str": lambda s: str(s),
}
SIGN_FNS = {
    "secret+salt":        lambda s: _md5(SIGN_SALT + str(s)),
    "salt+secret":        lambda s: _md5(str(s) + SIGN_SALT),
    "salt_only":          lambda s: _md5(str(s)),
    "secret_only":        lambda s: _md5(SIGN_SALT),
    "secret+salt+secret": lambda s: _md5(SIGN_SALT + str(s) + SIGN_SALT),
    "md5secret+salt":     lambda s: _md5(_md5(SIGN_SALT) + str(s)),
    "salt+md5secret":     lambda s: _md5(str(s) + _md5(SIGN_SALT)),
}
METHOD_KEYS = ("method", "action", "type", "0", "name", "func", "req", "do")

_WIN = {"salt": None, "sign": None, "mkey": None}

def _post(body: dict, tries: int = 3):
    data = enc(body)
    for attempt in range(1, tries + 1):
        try:
            r = requests.post(API_URL, data={"data": data},
                              headers={"User-Agent": "okhttp/3.12.1",
                                       "Content-Type": "application/x-www-form-urlencoded"},
                              timeout=API_TMO)
            if r.status_code != 200:
                time.sleep(1.5 * attempt); continue
            txt = r.text.strip()
            try:
                return dec(txt)
            except Exception:
                try:
                    return json.loads(txt)
                except Exception:
                    return None
        except Exception:
            time.sleep(1.5 * attempt)
    return None

def _bad_sign(resp) -> bool:
    if resp is None:
        return True
    return "invalid sign" in json.dumps(resp, ensure_ascii=False).lower()

def _remap(extra: dict) -> dict:
    out = {}
    for k, v in extra.items():
        out[_WIN["mkey"] if k == "__method__" else k] = v
    return out

def api_call(extra: dict, tries: int = 4):
    salt = random.randint(0, 899)
    if _WIN["sign"] is None:
        return _probe(extra, salt)
    body = {"salt": SALT_REPRS[_WIN["salt"]](salt), "sign": SIGN_FNS[_WIN["sign"]](salt)}
    body.update(_remap(extra))
    return _post(body, tries)

def _probe(extra: dict, salt: int):
    method_val = extra.get("__method__", "newolatvcategory0326")
    rest = {k: v for k, v in extra.items() if k != "__method__"}
    for mkey in METHOD_KEYS:
        for sname, sfn in SIGN_FNS.items():
            for rname, rfn in SALT_REPRS.items():
                body = {"salt": rfn(salt), "sign": sfn(salt), mkey: method_val}
                body.update(rest)
                resp = _post(body, tries=1)
                if not _bad_sign(resp):
                    _WIN["salt"], _WIN["sign"], _WIN["mkey"] = rname, sname, mkey
                    print(f"[probe] COMBO OK -> mkey='{mkey}' sign='{sname}' salt='{rname}' | sample={str(resp)[:220]}", flush=True)
                    return resp
    print("[probe] AUCUN combo accepte (sign toujours invalide).", flush=True)
    return None

def _extract_servers(resp) -> list:
    out = []
    def walk(node):
        if isinstance(node, list):
            for x in node: walk(x)
        elif isinstance(node, dict):
            if "cid" in node:
                out.append({"cid": str(node.get("cid")),
                            "category": str(node.get("category_name", node.get("cat", ""))),
                            "token1": node.get("token1", "")})
            for v in node.values(): walk(v)
    walk(resp)
    seen, uniq = set(), []
    for s in out:
        if s["cid"] and s["cid"] not in seen:
            seen.add(s["cid"]); uniq.append(s)
    return uniq

def get_servers() -> list:
    resp = api_call({"__method__": "newolatvcategory0326"})
    if resp:
        items = _extract_servers(resp)
        if items:
            print(f"[servers] {len(items)} serveurs recuperes", flush=True)
            return items
        print(f"[servers] combo sign OK mais 0 cid extrait. Reponse: {str(resp)[:300]}", flush=True)
    print("[servers] AUCUN serveur (voir [probe] ci-dessus)", flush=True)
    return []

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

def get_token(cid: str):
    resp = api_call({"__method__": "getToken128910", "cid": cid})
    if resp:
        portal, mac = _extract_token(resp)
        if portal and mac:
            return portal, mac
    return None

def is_alive(cid: str, token1: str) -> bool:
    tok = get_token(cid)
    if not tok:
        return False
    portal, mac = tok
    base = portal.rstrip("/")
    root = base[:-2] if base.endswith("/c") else base
    for p in ("/portal.php", "/stalker_portal/server/load.php", "/server/load.php"):
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
        print(f"[filter] France ({FR_CATEGORY}) -> {len(servers)} serveurs", flush=True)
    if not servers:
        print("ERREUR : aucun serveur a tester. Sortie sans ecraser la liste.", flush=True)
        sys.exit(1)

    alive, done = [], 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(is_alive, s["cid"], s["token1"]): s for s in servers}
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
