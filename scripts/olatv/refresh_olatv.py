#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_olatv.py — Probe + CLASSEMENT des serveurs Ola TV (nx-data).

Publie data/olatv/live-cids.json : les cids qui ont REELLEMENT des chaines FR,
CLASSES par richesse (nb de chaines FR, du plus riche au moins riche). L'app pioche
les meilleurs en premier au lieu de tester au hasard -> plus rapide, plus de FR trouve.

Protocole (repris de OlaTvProvider.kt) :
  - API   : POST http://iptvdroid.monster/IP11/api.php  data=base64(JSON clair)
            JSON={salt, sign=md5("MRZEREZIS"+salt), method_name, ...}. Reponse AES-CBC.
  - getServers  : newolatvcategory0326 -> [{cid, category_name}]
  - getMac      : getToken128910 + cid -> token1(portal)/token2(MAC)
  - FR = portail Stalker : handshake -> get_genres (genre FR: title 'fr|','fr ',
    'france','french','francais') -> get_ordered_list p=1 -> total_items par genre.
    fr_count = somme des total_items des genres FR. On garde fr_count>0.
Env: OLA_WORKERS(16), OLA_PROBE_TIMEOUT(8), OLA_API_TIMEOUT(25), OLA_OUT.
"""
import base64, hashlib, json, os, random, re, sys, time, unicodedata, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

API_URL   = "http://iptvdroid.monster/IP11/api.php"
AES_KEY   = b"3234567890123453"
SECRET    = "MRZEREZIS"

OUT_PATH    = os.environ.get("OLA_OUT", "data/olatv/live-cids.json")
MAX_WORKERS = int(os.environ.get("OLA_WORKERS", "16"))
PROBE_TMO   = int(os.environ.get("OLA_PROBE_TIMEOUT", "8"))
API_TMO     = int(os.environ.get("OLA_API_TIMEOUT", "25"))

MAG_UA = ("Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 "
          "(KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3")

def _b(x): return unicodedata.normalize("NFKD", x)

def build_payload(method_name, extras=None):
    salt = str(random.randint(0, 899))
    sign = hashlib.md5((SECRET + salt).encode()).hexdigest()
    obj = {"salt": salt, "sign": sign, "method_name": method_name}
    if extras: obj.update(extras)
    return base64.b64encode(json.dumps(obj).encode("utf-8")).decode()

def api_json(method_name, extras=None, tries=4):
    for attempt in range(1, tries + 1):
        try:
            r = requests.post(API_URL, data={"data": build_payload(method_name, extras)},
                              headers={"User-Agent": "okhttp/3.12.1"}, timeout=API_TMO)
            if r.status_code != 200:
                time.sleep(1.5 * attempt); continue
            body = r.text.strip()
            if body.startswith("[") or body.startswith("{"):
                txt = body
            else:
                txt = unpad(AES.new(AES_KEY, AES.MODE_CBC, AES_KEY).decrypt(base64.b64decode(body)), 16).decode("utf-8", "replace")
            return json.loads(txt)
        except Exception:
            time.sleep(1.5 * attempt)
    return None

def get_servers():
    resp = api_json("newolatvcategory0326")
    items = resp if isinstance(resp, list) else (resp.get("LIVETV") if isinstance(resp, dict) else None) or []
    out, seen = [], set()
    for s in items:
        if isinstance(s, dict):
            cid = str(s.get("cid", "")).strip()
            if cid and cid not in seen:
                seen.add(cid); out.append(cid)
    print(f"[servers] {len(out)} cids", flush=True)
    return out

def _decode_token1(t1):
    try:
        text = base64.b64decode(t1).decode("iso-8859-1"); i = text.find("http")
        return text[i:].strip() if i >= 0 else None
    except Exception: return None

def _decode_token2(t2):
    try:
        text = base64.b64decode(t2).decode("iso-8859-1")
        m = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", text)
        return m.group(1) if m else None
    except Exception: return None

def get_mac(cid):
    resp = api_json("getToken128910", {"cid": cid})
    if not resp: return None
    tgt = None
    if isinstance(resp, dict):
        lv = resp.get("LIVETV"); tgt = (lv[0] if isinstance(lv, list) and lv else resp)
    elif isinstance(resp, list) and resp:
        tgt = resp[0]
    if not isinstance(tgt, dict): return None
    t1, t2 = tgt.get("token1", ""), tgt.get("token2", "")
    if not t1 or not t2: return None
    base, mac = _decode_token1(t1), _decode_token2(t2)
    return (base, mac) if base and mac else None

def _is_fr_genre(title):
    t = title.lower()
    return t.startswith("fr|") or t.startswith("fr ") or "france" in t or "french" in t or "français" in t or "francais" in t

def fr_channel_count(base, mac):
    """handshake -> get_genres FR -> somme des total_items. 0 si mort/non-FR."""
    portal = base.rstrip("/") + "/portal.php"
    cookie = f"mac={urllib.parse.quote(mac)}; stb_lang=en; timezone=Europe%2FLondon"
    H = {"User-Agent": MAG_UA, "Cookie": cookie}
    sess = requests.Session()
    try:
        r = sess.get(portal + "?type=stb&action=handshake&token=&JsHttpRequest=1-xml", headers=H, timeout=PROBE_TMO)
        token = json.loads(r.text).get("js", {}).get("token", "")
        if not token: return 0
        Hb = dict(H); Hb["Authorization"] = "Bearer " + str(token)
        r = sess.get(portal + "?type=itv&action=get_genres&JsHttpRequest=1-xml", headers=Hb, timeout=PROBE_TMO)
        genres = json.loads(r.text).get("js", []) or []
        fr_ids = [str(g.get("id", "")) for g in genres if isinstance(g, dict) and _is_fr_genre(g.get("title", ""))]
        if not fr_ids: return 0
        total = 0
        for gid in fr_ids[:12]:
            try:
                u = portal + f"?type=itv&action=get_ordered_list&genre={gid}&force_ch_link_check=&fav=0&sortby=name&p=1&JsHttpRequest=1-xml"
                r = sess.get(u, headers=Hb, timeout=PROBE_TMO)
                total += int(json.loads(r.text).get("js", {}).get("total_items", 0) or 0)
            except Exception:
                continue
        return total
    except Exception:
        return 0
    finally:
        sess.close()

def probe(cid):
    """Retourne (cid, fr_count). fr_count=0 => mort ou pas de FR."""
    creds = get_mac(cid)
    if not creds: return (cid, 0)
    base, mac = creds
    return (cid, fr_channel_count(base, mac))

def main():
    t0 = time.time()
    cids = get_servers()
    if not cids:
        print("ERREUR : aucun cid recupere. Sortie sans ecraser.", flush=True)
        sys.exit(1)
    counts, done = {}, 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(probe, c): c for c in cids}
        for fut in as_completed(futs):
            done += 1
            try: cid, n = fut.result()
            except Exception: cid, n = futs[fut], 0
            if n > 0: counts[cid] = n
            if done % 100 == 0 or done == len(cids):
                print(f"  probe {done}/{len(cids)} — FR cids={len(counts)}", flush=True)
    if not counts:
        print("ATTENTION : 0 cid FR ce run (API/portails instables ?) -> on n'ecrase pas.", flush=True)
        if os.path.exists(OUT_PATH):
            print(f"  -> {OUT_PATH} conserve intact.", flush=True)
            return
        sys.exit(1)
    ranked = sorted(counts.keys(), key=lambda c: (-counts[c], len(c), c))
    payload = {"generated_at": int(time.time()), "category": "fr-ranked",
               "total": len(cids), "alive": len(ranked),
               "cids": ranked, "fr_counts": {c: counts[c] for c in ranked}}
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    top = [(c, counts[c]) for c in ranked[:8]]
    print(f"[done] {len(ranked)} cids FR classes en {int(time.time()-t0)}s. Top: {top} -> {OUT_PATH}", flush=True)

if __name__ == "__main__":
    main()
