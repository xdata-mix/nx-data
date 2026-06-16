#!/usr/bin/env python3
"""
Auto-refresh data.m3u v3 (2026-06-15) : 
- A. SMART CHECK des HLS .m3u8 : ouvre le contenu, valide #EXTM3U, descend dans
     le 1er variant des master playlists pour détecter les CDN géo-bloqués
     (= cas Canal+ en clair : master 200 OK mais variant CDN 403).
- B. AUTO-ADD nouvelles chaînes ParaTV : compare notre data.m3u avec ParaTV main,
     ajoute les chaînes manquantes dans la catégorie "Nouveautés ParaTV".

Catégories protégées (URLs JAMAIS modifiées) :
  - Premium FR  → host 185.160.192.14    (= redirect off20 prime-tv)
  - Live Canal  → host live.aab1.top     (= Xtream Codes statique)
  - prime-tv    → host off20.lynxcontents.click
  - PocketBase  → host 47.237.205.89
"""
import os, re, sys, asyncio, aiohttp
from urllib.parse import urlparse, urljoin

PARATV_MAIN = "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/playlists/paratv/main/paratv.m3u"
LOCAL_M3U = os.environ.get("LOCAL_M3U", "data.m3u")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
TIMEOUT = 8
CONCURRENCY = 8
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
    """Index simple {nom_norm: url} pour les remplacements."""
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


async def fetch_paratv_full(session) -> list:
    """
    Phase B : récupère TOUS les blocs ParaTV avec name + url + group_title + bloc EXTINF complet.
    Retourne list[(name, url, group, full_block_text)].
    """
    async with session.get(PARATV_MAIN, headers={"User-Agent": UA},
                           timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
        text = await r.text()
    blocks = re.split(r'(?=#EXTINF)', text)
    result = []
    for b in blocks:
        if not b.startswith('#EXTINF'):
            continue
        name_m = re.search(r',([^,\n]+)', b)
        url_m = re.search(r'\n(https?://\S+)', b)
        group_m = re.search(r'group-title="([^"]*)"', b)
        if name_m and url_m:
            result.append((
                name_m.group(1).strip(),
                url_m.group(1).strip(),
                group_m.group(1) if group_m else "",
                b
            ))
    return result


def skip_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(s in host for s in SKIP_HOSTS)


async def is_alive_smart(session, sem, url: str) -> bool:
    """
    Phase A : check SMART (vs HEAD bête).
    - .m3u8 : GET (limite 8KB), valide #EXTM3U, descend dans 1er variant si master.
    - Autres : HEAD classique (+ fallback GET sur 405).
    """
    async with sem:
        try:
            url_no_q = url.split('?')[0].lower()
            is_m3u8 = url_no_q.endswith('.m3u8')
            
            if is_m3u8:
                # GET le master/playlist, max 8KB pour rester rapide
                async with session.get(url, headers={"User-Agent": UA},
                                       timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                       allow_redirects=True) as r:
                    if r.status >= 400:
                        return False
                    chunk = await r.content.read(8192)
                    try:
                        text = chunk.decode('utf-8', errors='replace')
                    except Exception:
                        return False
                
                # Valide signature HLS
                if not text.lstrip().startswith('#EXTM3U'):
                    # Body invalide (Access denied / HTML / etc.) → mort
                    return False
                
                # Master playlist : trouve le 1er variant et le check aussi
                if '#EXT-X-STREAM-INF' in text:
                    lines = text.splitlines()
                    variant_url = None
                    for j, line in enumerate(lines):
                        if line.startswith('#EXT-X-STREAM-INF'):
                            # La ligne suivante (skip blanches) est le variant
                            for k in range(j + 1, min(j + 4, len(lines))):
                                if lines[k].strip() and not lines[k].startswith('#'):
                                    variant_url = lines[k].strip()
                                    break
                            break
                    if variant_url:
                        if not variant_url.startswith('http'):
                            variant_url = urljoin(url, variant_url)
                        try:
                            async with session.head(variant_url, headers={"User-Agent": UA},
                                                    timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                                    allow_redirects=True) as vr:
                                if vr.status == 405:
                                    async with session.get(variant_url, headers={"User-Agent": UA},
                                                           timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                                           allow_redirects=True) as vg:
                                        chunk2 = await vg.content.read(2048)
                                        # Si body trop court ou contient "denied"/"forbidden" → mort
                                        body2 = chunk2.decode('utf-8', errors='replace').lower()
                                        if 'denied' in body2 or 'forbidden' in body2 or vg.status >= 400:
                                            return False
                                        return True
                                if vr.status >= 400:
                                    return False
                        except Exception:
                            return False
                
                return True
            else:
                # Non-HLS : HEAD classique
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
    print(f"=== Auto-refresh {LOCAL_M3U} (v3 smart-check + auto-add, concurrency={CONCURRENCY}) ===")
    conn = aiohttp.TCPConnector(limit=CONCURRENCY * 2, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        print(f"Fetching ParaTV index from {PARATV_MAIN}...")
        paratv_simple = await fetch_paratv_index(session)
        print(f"  -> {len(paratv_simple)} channels indexed (simple)")
        paratv_full = await fetch_paratv_full(session)
        print(f"  -> {len(paratv_full)} channels indexed (full = pour auto-add)")

        with open(LOCAL_M3U, encoding='utf-8') as f:
            content = f.read()

        blocks = re.split(r'(?=#EXTINF)', content)
        entries = []
        for i, b in enumerate(blocks[1:], 1):
            if not b.startswith('#EXTINF'):
                continue
            name_m = re.search(r',([^,\n]+)', b)
            url_m = re.search(r'\n(https?://\S+)', b)
            if not name_m or not url_m:
                continue
            name = name_m.group(1).strip()
            url = url_m.group(1).strip()
            if skip_url(url):
                continue
            entries.append((i, name, url))

        print(f"  -> {len(entries)} URLs to check (skip-list filtered)")

        sem = asyncio.Semaphore(CONCURRENCY)
        
        # === Phase 1 : check vivants/morts (SMART) ===
        alive_results = await asyncio.gather(
            *(is_alive_smart(session, sem, e[2]) for e in entries),
            return_exceptions=True
        )
        dead_entries = [(i, name, url) for (i, name, url), alive in zip(entries, alive_results)
                        if alive is False or isinstance(alive, Exception)]
        n_alive = len(alive_results) - len(dead_entries)
        print(f"\nAlive: {n_alive}  Dead: {len(dead_entries)}")
        
        # === Phase 2 : remplacer les morts par ParaTV index ===
        candidates = []
        for i, name, url in dead_entries:
            new_url = paratv_simple.get(normalize_name(name))
            if new_url and new_url != url:
                candidates.append((i, name, url, new_url))
        candidate_alives = await asyncio.gather(
            *(is_alive_smart(session, sem, c[3]) for c in candidates),
            return_exceptions=True
        )
        n_replaced = 0
        for (i, name, url, new_url), alive in zip(candidates, candidate_alives):
            if alive is True:
                blocks[i] = blocks[i].replace(url, new_url)
                n_replaced += 1
                print(f"  OK {name[:35]:35} -> {new_url[-45:]}")
        
        replaced_indices = {c[0] for (c, a) in zip(candidates, candidate_alives) if a is True}
        for i, name, url in dead_entries:
            if i not in replaced_indices:
                print(f"  KO {name[:35]:35} dead, no replacement found")

        new_content = ''.join(blocks)
        
        # === Phase 3 : AUTO-ADD nouvelles chaînes ParaTV ===
        # On normalise nos noms (post-replacement) pour ne pas dupliquer
        our_names_norm = set()
        for b in re.split(r'(?=#EXTINF)', new_content):
            if not b.startswith('#EXTINF'):
                continue
            name_m = re.search(r',([^,\n]+)', b)
            if name_m:
                our_names_norm.add(normalize_name(name_m.group(1).strip()))
        
        new_candidates = []
        for (name, url, group, full_block) in paratv_full:
            if normalize_name(name) not in our_names_norm:
                new_candidates.append((name, url, group, full_block))
        
        n_added = 0
        if new_candidates:
            print(f"\n=== {len(new_candidates)} chaînes ParaTV non présentes chez nous — check vivantes ===")
            new_alives = await asyncio.gather(
                *(is_alive_smart(session, sem, c[1]) for c in new_candidates),
                return_exceptions=True
            )
            alive_new = [c for c, a in zip(new_candidates, new_alives) if a is True]
            print(f"  -> {len(alive_new)}/{len(new_candidates)} vivantes à ajouter")
            
            if alive_new:
                # Construire les blocs à ajouter dans "Nouveautés ParaTV"
                addition_lines = []
                addition_lines.append("\n# === Nouveautés ParaTV (auto-ajouté par refresh_urls.py) ===\n")
                for (name, url, group, full_block) in alive_new:
                    modified = full_block.rstrip()
                    if 'group-title=' in modified:
                        modified = re.sub(r'group-title="[^"]*"',
                                          'group-title="Nouveautés ParaTV"', modified)
                    else:
                        modified = re.sub(r'#EXTINF:([-\d.]+)',
                                          r'#EXTINF:\1 group-title="Nouveautés ParaTV"', modified, count=1)
                    addition_lines.append(modified + "\n")
                    print(f"  + {name[:50]}")
                    n_added += 1
                
                # Append au new_content
                new_content = new_content.rstrip() + "\n" + "".join(addition_lines)
        else:
            print(f"\n=== Aucune nouvelle chaîne ParaTV (notre liste est à jour) ===")

    # === Stats finales ===
    print(f"\n=== Stats ===")
    print(f"Checked: {len(entries)}  Alive: {n_alive}  Dead: {len(dead_entries)}  Replaced: {n_replaced}  Added: {n_added}")

    if new_content == content:
        print("\n-> No change, exiting.")
        return

    with open(LOCAL_M3U, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"\n-> {LOCAL_M3U} updated ({n_replaced} replaced, {n_added} added)")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
