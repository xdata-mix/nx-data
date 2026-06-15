#!/usr/bin/env python3
"""
Auto-refresh data.m3u : détecte les URLs mortes ParaTV et les remplace
par les URLs fraîches de la playlist ParaTV main.

Catégories protégées (URLs JAMAIS modifiées) :
  - Premium FR  → host 185.160.192.14    (= redirect off20 prime-tv)
  - Live Canal  → host live.aab1.top     (= Xtream Codes statique)
  - prime-tv    → host off20.lynxcontents.click
"""
import os, re, sys, requests
from urllib.parse import urlparse

PARATV_MAIN = "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/playlists/paratv/main/paratv.m3u"
LOCAL_M3U = os.environ.get("LOCAL_M3U", "data.m3u")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
TIMEOUT = 8
SKIP_HOSTS = ("185.160.192.14", "live.aab1.top", "off20.lynxcontents.click",
              "47.237.205.89")


def normalize_name(s: str) -> str:
    """Strip '1. ' prefix + '[1080p-france.tv]' suffix + qualité suffixe (HD/FHD/4K/...)."""
    s = re.sub(r'^\d+\.\s*', '', s)
    s = re.sub(r'\[.*?\]', '', s)
    s = s.upper().strip()
    # Strip qualité au bout du nom : "M6 HD" → "M6", "TF1 FHD" → "TF1"
    s = re.sub(r'\s+(HD|FHD|UHD|4K|SD|1080P|720P|480P)\s*$', '', s)
    return s.strip()


def fetch_paratv_index() -> dict:
    r = requests.get(PARATV_MAIN, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    index = {}
    blocks = re.split(r'(?=#EXTINF)', r.text)
    for b in blocks:
        name_m = re.search(r',([^,\n]+)', b)
        url_m = re.search(r'\n(https?://\S+)', b)
        if name_m and url_m:
            name = normalize_name(name_m.group(1))
            url = url_m.group(1).strip()
            # Garde la 1ère occurrence (= souvent la meilleure source)
            index.setdefault(name, url)
    return index


def skip_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(s in host for s in SKIP_HOSTS)


def is_alive(url: str) -> bool:
    """HEAD d'abord ; si 405 (méthode non supportée), GET stream."""
    try:
        r = requests.head(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                          allow_redirects=True)
        if r.status_code == 405:
            with requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                              allow_redirects=True, stream=True) as g:
                return g.status_code < 400
        return r.status_code < 400
    except Exception:
        return False


def main():
    print(f"=== Auto-refresh {LOCAL_M3U} ===")
    print(f"Fetching ParaTV index from {PARATV_MAIN}…")
    paratv = fetch_paratv_index()
    print(f"  -> {len(paratv)} channels indexed")

    with open(LOCAL_M3U, encoding='utf-8') as f:
        content = f.read()

    blocks = re.split(r'(?=#EXTINF)', content)
    out = [blocks[0]]
    n_check = n_skip = n_alive = n_dead = n_replaced = 0

    for b in blocks[1:]:
        if not b.startswith('#EXTINF'):
            out.append(b); continue
        name_m = re.search(r',([^,\n]+)', b)
        url_m = re.search(r'\n(https?://\S+)', b)
        if not name_m or not url_m:
            out.append(b); continue
        name = name_m.group(1).strip()
        url = url_m.group(1).strip()

        if skip_url(url):
            n_skip += 1
            out.append(b); continue

        n_check += 1
        if is_alive(url):
            n_alive += 1
            out.append(b); continue

        n_dead += 1
        normalized = normalize_name(name)
        new_url = paratv.get(normalized)
        if new_url and new_url != url and is_alive(new_url):
            b = b.replace(url, new_url)
            n_replaced += 1
            print(f"  OK {name[:35]:35} -> {new_url[-40:]}")
        else:
            print(f"  KO {name[:35]:35} dead, no replacement found")
        out.append(b)

    new_content = ''.join(out)
    print(f"\n=== Stats ===")
    print(f"Skip (Premium FR / Live Canal / prime-tv): {n_skip}")
    print(f"Checked: {n_check}  Alive: {n_alive}  Dead: {n_dead}  Replaced: {n_replaced}")

    if new_content == content:
        print("\n-> No change, exiting.")
        sys.exit(0)

    with open(LOCAL_M3U, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"\n-> {LOCAL_M3U} updated ({n_replaced} URLs replaced)")


if __name__ == "__main__":
    main()
