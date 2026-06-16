#!/usr/bin/env python3
"""
Auto-refresh data.m3u v2 : détecte les URLs mortes ParaTV et les remplace
par les URLs fraîches de la playlist ParaTV main.

v2 = HEAD requests en parallèle via asyncio/aiohttp (= 30-60s au lieu de 5+ min).

Catégories protégées (URLs JAMAIS modifiées) :
  - Premium FR  → host 185.160.192.14    (= redirect off20 prime-tv)
  - Live Canal  → host live.aab1.top     (= Xtream Codes statique)
  - prime-tv    → host off20.lynxcontents.click
  - PocketBase  → host 47.237.205.89
"""
import os, re, sys, asyncio, aiohttp
from urllib.parse import urlparse

PARATV_MAIN = "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/playlists/paratv/main/paratv.m3u"
LOCAL_M3U = os.environ.get("LOCAL_M3U", "data.m3u")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
TIMEOUT = 8
CONCURRENCY = 8  # HEAD requehsts parallèles. 20 = raisonnable, n'overload pas.
SKIP_HOSTS = ("185.160.192.14", "live.aab1.top", "off20.lynxcontents.click",
              "47.237.205.89")


def normalize_name(s: str) -> str:
    """Strip '1. ' prefix + '[1080p-france.tv]' suffix + qualité suffixe (HD/FHD/4K/...)."""
    s = re.sub(r'^\d+\.\s*', '', s)
    s = re.sub(r'\[.*?\]', '', s)
    s = s.upper().strip()
    s = re.sub(r'\s+(HD|FHD|UHD|4K|SD|1080P|720P|480P)\s*$', '', s)
    return s.strip()


async def fetch_paratv_index(session) -> dict:
    async with session.get(PARATV_MAIN, headers={"User-Agent": UA},
                           timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
        text = await r.text()
    index = {}
    blocks = re.split(r'(?=#EXTINF)', text)
    for b in blocks:
        name_m = re.search(r',([^,\n]+)', b)
        url_m = re.search(r'\n(https?://\S+)', b)
        if name_m and url_m:
            name = normalize_name(name_m.group(1))
            url = url_m.group(1).strip()
            index.setdefault(name, url)
    return index


def skip_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(s in host for s in SKIP_HOSTS)


async def is_alive(session, sem, url: str) -> bool:
    async with sem:
        try:
            async with session.head(url, headers={"User-Agent": UA},
                                    timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                    allow_redirects=True) as r:
                if r.status == 405:
                    async with session.get(url, headers={"User-Agent": UA},
                                            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                            allow_redirects=True) as g:
                        return g.status < 400
                return r.status < 400
        except Exception:
            return False


async def main_async():
    print(f"=== Auto-refresh {LOCAL_M3U} (v2 asyncio, concurrency={CONCURRENCY}) ===")
    conn = aiohttp.TCPConnector(limit=CONCURRENCY * 2, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        print(f"Fetching ParaTV index from {PARATV_MAIN}...")
        paratv = await fetch_paratv_index(session)
        print(f"  -> {len(paratv)} channels indexed")

        with open(LOCAL_M3U, encoding='utf-8') as f:
            content = f.read()

        blocks = re.split(r'(?=#EXTINF)', content)
        # Parse tous les blocs pour collecter (idx, name, url)
        entries = []  # list of (block_idx, name, url)
        for i, b in enumerate(blocks[1:], 1):
            if not b.startswith('#EXTINF'): continue
            name_m = re.search(r',([^,\n]+)', b)
            url_m = re.search(r'\n(https?://\S+)', b)
            if not name_m or not url_m: continue
            name = name_m.group(1).strip()
            url = url_m.group(1).strip()
            if skip_url(url): continue
            entries.append((i, name, url))

        print(f"  -> {len(entries)} URLs to check (skip-list filtered)")

        # HEAD requests en parallèle
        sem = asyncio.Semaphore(CONCURRENCY)
        alive_results = await asyncio.gather(
            *(is_alive(session, sem, e[2]) for e in entries),
            return_exceptions=True
        )

        # Identify les URLs mortes
        dead_entries = [(i, name, url) for (i, name, url), alive in zip(entries, alive_results) if alive is False or isinstance(alive, Exception)]
        n_alive = len(alive_results) - len(dead_entries)
        print(f"\nAlive: {n_alive}  Dead: {len(dead_entries)}")

        # Pour chaque URL morte, cherche un replacement dans ParaTV index
        # Test si le replacement est vivant (= en parallèle aussi)
        candidates = []  # list of (block_idx, name, url, candidate_url)
        for i, name, url in dead_entries:
            new_url = paratv.get(normalize_name(name))
            if new_url and new_url != url:
                candidates.append((i, name, url, new_url))

        candidate_alives = await asyncio.gather(
            *(is_alive(session, sem, c[3]) for c in candidates),
            return_exceptions=True
        )

        # Apply les remplacements valides
        n_replaced = 0
        for (i, name, url, new_url), alive in zip(candidates, candidate_alives):
            if alive is True:
                blocks[i] = blocks[i].replace(url, new_url)
                n_replaced += 1
                print(f"  OK {name[:35]:35} -> {new_url[-45:]}")

        # Log les vraiment dead-no-replacement
        replaced_indices = {c[0] for (c, a) in zip(candidates, candidate_alives) if a is True}
        for i, name, url in dead_entries:
            if i not in replaced_indices:
                print(f"  KO {name[:35]:35} dead, no replacement found")

    new_content = ''.join(blocks)

    print(f"\n=== Stats ===")
    print(f"Checked: {len(entries)}  Alive: {n_alive}  Dead: {len(dead_entries)}  Replaced: {n_replaced}")

    if new_content == content:
        print("\n-> No change, exiting.")
        return

    with open(LOCAL_M3U, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"\n-> {LOCAL_M3U} updated ({n_replaced} URLs replaced)")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
