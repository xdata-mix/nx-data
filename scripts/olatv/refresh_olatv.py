#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_olatv.py — Ola TV (nx-data). Deux sorties :
  1) data/olatv/live-cids.json  : cids FR classes par richesse (fallback / filtre app).
  2) data/olatv/fr-channels.json: chaines FR BRUTES {cid, name, cmd} des meilleurs cids,
     UNIQUEMENT depuis les portails dont les liens sont VALIDES (checker M3U : create_link
     + statut HTTP). L'app telecharge et REGROUPE avec SA propre logique (matching identique).
Env: OLA_WORKERS(16), TOP_CIDS(40), MAX_PAGES(40), MAX_SRC(8), timeouts.
"""
import base64, hashlib, json, os, random, re, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

API_URL   = "http://iptvdroid.monster/IP11/api.php"
AES_KEY   = b"3234567890123453"
SECRET    = "MRZEREZIS"
OUT_CIDS   = os.environ.get("OLA_OUT", "data/olatv/live-cids.json")
OUT_CHANS  = os.environ.get("OLA_OUT_CHANS", "data/olatv/fr-channels.json")
MAX_WORKERS= int(os.environ.get("OLA_WORKERS", "16"))
TOP_CIDS   = int(os.environ.get("OLA_TOP_CIDS", "40"))
MAX_PAGES  = int(os.environ.get("OLA_MAX_PAGES", "40"))
MAX_SRC    = int(os.environ.get("OLA_MAX_SRC", "8"))
PROBE_TMO  = int(os.environ.get("OLA_PROBE_TIMEOUT", "8"))
API_TMO    = int(os.environ.get("OLA_API_TIMEOUT", "25"))
MAG_UA = ("Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 "
          "(KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3")

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
            txt = body if (body.startswith("[") or body.startswith("{")) else \
                unpad(AES.new(AES_KEY, AES.MODE_CBC, AES_KEY).decrypt(base64.b64decode(body)), 16).decode("utf-8", "replace")
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

def _dec1(t1):
    try:
        t = base64.b64decode(t1).decode("iso-8859-1"); i = t.find("http")
        return t[i:].strip() if i >= 0 else None
    except Exception: return None

def _dec2(t2):
    try:
        t = base64.b64decode(t2).decode("iso-8859-1")
        m = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", t)
        return m.group(1) if m else None
    except Exception: return None

def get_mac(cid):
    resp = api_json("getToken128910", {"cid": cid})
    if not resp: return None
    tgt = None
    if isinstance(resp, dict):
        lv = resp.get("LIVETV"); tgt = (lv[0] if isinstance(lv, list) and lv else resp)
    elif isinstance(resp, list) and resp: tgt = resp[0]
    if not isinstance(tgt, dict): return None
    t1, t2 = tgt.get("token1", ""), tgt.get("token2", "")
    if not t1 or not t2: return None
    base, mac = _dec1(t1), _dec2(t2)
    return (base, mac) if base and mac else None

def _is_fr(title):
    t = title.lower()
    return t.startswith("fr|") or t.startswith("fr ") or "france" in t or "french" in t or "français" in t or "francais" in t

def _portal(base): return base.rstrip("/") + "/portal.php"

def _handshake(sess, portal, cookie):
    r = sess.get(portal + "?type=stb&action=handshake&token=&JsHttpRequest=1-xml",
                 headers={"User-Agent": MAG_UA, "Cookie": cookie}, timeout=PROBE_TMO)
    return json.loads(r.text).get("js", {}).get("token", "")

def fr_genres(sess, portal, Hb):
    r = sess.get(portal + "?type=itv&action=get_genres&JsHttpRequest=1-xml", headers=Hb, timeout=PROBE_TMO)
    g = json.loads(r.text).get("js", []) or []
    return [(str(x.get("id", "")), x.get("title", "")) for x in g if isinstance(x, dict) and _is_fr(x.get("title", ""))]

def create_link(sess, portal, Hb, cmd):
    try:
        u = portal + "?type=itv&action=create_link&cmd=" + urllib.parse.quote(cmd) + "&JsHttpRequest=1-xml"
        r = sess.get(u, headers=Hb, timeout=PROBE_TMO)
        c = str(json.loads(r.text).get("js", {}).get("cmd", ""))
        m = re.search(r"https?://\S+", c)
        return m.group(0) if m else None
    except Exception:
        return None

def url_valid(url):
    """Checker M3U leger : le lien resout-il vers un endpoint VIVANT ? (statut, pas de lecture).
    Vire les vraiment HS (404, domaine mort, connexion refusee). status 200/206/3xx = valide."""
    r = None
    try:
        r = requests.get(url, headers={"User-Agent": MAG_UA}, stream=True,
                         timeout=PROBE_TMO, allow_redirects=True)
        return r.status_code in (200, 206, 301, 302, 303, 307, 308)
    except Exception:
        return False
    finally:
        try:
            if r is not None: r.close()
        except Exception:
            pass

def cid_ok(base, mac, sample_cmds):
    """True si AU MOINS un cmd echantillon se resout ET repond un statut valide (lien non casse)."""
    portal = _portal(base)
    cookie = f"mac={urllib.parse.quote(mac)}; stb_lang=en; timezone=Europe%2FLondon"
    sess = requests.Session()
    try:
        tok = _handshake(sess, portal, cookie)
        if not tok: return False
        Hb = {"User-Agent": MAG_UA, "Cookie": cookie, "Authorization": "Bearer " + str(tok)}
        for cmd in sample_cmds:
            url = create_link(sess, portal, Hb, cmd)
            if url and url_valid(url):
                return True
        return False
    except Exception:
        return False
    finally:
        sess.close()

def fr_count(base, mac):
    portal = _portal(base)
    cookie = f"mac={urllib.parse.quote(mac)}; stb_lang=en; timezone=Europe%2FLondon"
    sess = requests.Session()
    try:
        tok = _handshake(sess, portal, cookie)
        if not tok: return 0
        Hb = {"User-Agent": MAG_UA, "Cookie": cookie, "Authorization": "Bearer " + str(tok)}
        gs = fr_genres(sess, portal, Hb)
        if not gs: return 0
        total = 0
        for gid, _ in gs[:12]:
            try:
                u = portal + f"?type=itv&action=get_ordered_list&genre={gid}&force_ch_link_check=&fav=0&sortby=name&p=1&JsHttpRequest=1-xml"
                r = sess.get(u, headers=Hb, timeout=PROBE_TMO)
                total += int(json.loads(r.text).get("js", {}).get("total_items", 0) or 0)
            except Exception: continue
        return total
    except Exception: return 0
    finally: sess.close()

def probe(cid):
    creds = get_mac(cid)
    if not creds: return (cid, 0, None)
    base, mac = creds
    return (cid, fr_count(base, mac), (base, mac))

def fetch_fr_channels(cid, base, mac):
    portal = _portal(base)
    cookie = f"mac={urllib.parse.quote(mac)}; stb_lang=en; timezone=Europe%2FLondon"
    sess = requests.Session()
    out = []
    try:
        tok = _handshake(sess, portal, cookie)
        if not tok: return out
        Hb = {"User-Agent": MAG_UA, "Cookie": cookie, "Authorization": "Bearer " + str(tok)}
        for gid, _ in fr_genres(sess, portal, Hb):
            page, pages = 1, 1
            while page <= min(pages, MAX_PAGES):
                try:
                    u = portal + f"?type=itv&action=get_ordered_list&genre={gid}&force_ch_link_check=&fav=0&sortby=name&p={page}&JsHttpRequest=1-xml"
                    r = sess.get(u, headers=Hb, timeout=PROBE_TMO)
                    js = json.loads(r.text).get("js", {})
                    data = js.get("data", []) or []
                    if page == 1:
                        ti = int(js.get("total_items", 0) or 0)
                        mpi = int(js.get("max_page_items", 14) or 14) or 14
                        pages = max(1, (ti + mpi - 1) // mpi)
                    for ch in data:
                        if not isinstance(ch, dict): continue
                        name = str(ch.get("name", "")).strip()
                        cmd = str(ch.get("cmd", "")).strip()
                        if name and cmd and not name.startswith("#"):
                            out.append((name, cmd))
                except Exception:
                    break
                page += 1
        return out
    except Exception:
        return out
    finally:
        sess.close()

def _lightnorm(name):
    s = name.lower()
    s = re.sub(r"^(fr|es|pt|en|de|it|ar|tr|nl|pl|ro|us|uk|be|ch)[|:\s]+", "", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s

def main():
    t0 = time.time()
    cids = get_servers()
    if not cids:
        print("ERREUR : aucun cid.", flush=True); sys.exit(1)
    counts, creds_map, done = {}, {}, 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(probe, c): c for c in cids}
        for fut in as_completed(futs):
            done += 1
            try: cid, n, creds = fut.result()
            except Exception: cid, n, creds = futs[fut], 0, None
            if n > 0:
                counts[cid] = n
                if creds: creds_map[cid] = creds
            if done % 100 == 0 or done == len(cids):
                print(f"  passe1 {done}/{len(cids)} — FR cids={len(counts)}", flush=True)
    if not counts:
        print("ATTENTION : 0 cid FR -> on n'ecrase rien.", flush=True)
        if os.path.exists(OUT_CIDS): return
        sys.exit(1)
    ranked = sorted(counts.keys(), key=lambda c: (-counts[c], len(c), c))
    os.makedirs(os.path.dirname(OUT_CIDS), exist_ok=True)
    with open(OUT_CIDS, "w", encoding="utf-8") as f:
        json.dump({"generated_at": int(time.time()), "category": "fr-ranked",
                   "total": len(cids), "alive": len(ranked),
                   "cids": ranked, "fr_counts": {c: counts[c] for c in ranked}},
                  f, ensure_ascii=False, indent=1)
    print(f"[cids] {len(ranked)} cids FR classes -> {OUT_CIDS}", flush=True)

    top = [c for c in ranked if c in creds_map][:TOP_CIDS]
    print(f"[chans] fetch chaines FR des {len(top)} meilleurs cids…", flush=True)
    per_channel = {}
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_fr_channels, c, *creds_map[c]): c for c in top}
        for fut in as_completed(futs):
            cid = futs[fut]; done += 1
            try: chans = fut.result()
            except Exception: chans = []
            base, mac = creds_map[cid]
            samples = [c for _, c in chans[:6]]
            if chans and not cid_ok(base, mac, samples):
                print(f"  passe2 {done}/{len(top)} cid={cid} LIENS HS -> rejete", flush=True)
                continue
            for name, cmd in chans:
                k = _lightnorm(name)
                if not k: continue
                lst = per_channel.setdefault(k, [])
                if len(lst) < MAX_SRC and not any(e[0] == cid and e[2] == cmd for e in lst):
                    lst.append((cid, name, cmd))
            print(f"  passe2 {done}/{len(top)} cid={cid} (+{len(chans)}) — chaines uniq={len(per_channel)}", flush=True)

    entries = []
    for k, lst in per_channel.items():
        for cid, name, cmd in lst:
            entries.append({"cid": cid, "name": name, "cmd": cmd})
    payload = {"generated_at": int(time.time()), "cid_count": len(top),
               "channel_count": len(per_channel), "entry_count": len(entries), "entries": entries}
    os.makedirs(os.path.dirname(OUT_CHANS), exist_ok=True)
    with open(OUT_CHANS, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    sz = os.path.getsize(OUT_CHANS)
    print(f"[done] {len(per_channel)} chaines / {len(entries)} sources ({sz//1024} Ko) "
          f"en {int(time.time()-t0)}s -> {OUT_CHANS}", flush=True)

if __name__ == "__main__":
    main()
