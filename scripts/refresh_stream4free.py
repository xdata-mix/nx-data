#!/usr/bin/env python3
"""
refresh_stream4free.py - Genere data-stream4free.m3u avec des URLs stream4free://<slug>.

Le site stream4free.tv bloque les IPs datacenter (403 Forbidden) donc ce scraper
NE FETCHE PAS les pages. Il genere le m3u depuis un catalogue hardcode.
L'app ONYX resout le vrai flux m3u8 a la lecture via Stream4FreeResolver.

Pour ajouter une chaine : ajouter une entree dans CATALOG ci-dessous.

2026-06-28 (user "on a mis du contenu anglais sans faire expres dans la
playlist, retire-les") : retirees Les Simpsons (EN), South Park US,
Friends, Family Guy, The Big Bang Theory, HIMYM, The Office, Breaking Bad,
Game of Thrones, Rick and Morty, Futurama, American Dad, Bob's Burgers,
Archer, Sons of Anarchy, House MD, Scrubs, Seinfeld, Community, Cleveland
Show, Workaholics, King of the Hill, Aqua Teen, Always Sunny, Poker. On
garde Les Simpsons VF et South Park FR (= doublees francais).
"""

import sys

GROUP = "Stream4Free - Television en direct"

# Catalogue : (slug_page, titre, logo_filename_or_url)
# slug_page = le path sur stream4free.tv (ex: "kaamelott-hd" -> stream4free.tv/kaamelott-hd)
# logo = nom de fichier dans /images/avatars/ OU URL complete
AVATAR = "https://www.stream4free.tv/images/avatars"

CATALOG = [
    # === CHAINES TV LIVE ===
    ("tf1-live-streaming",  "TF1",              f"{AVATAR}/tf1.jpg"),
    ("france-3-live",       "France 3",         f"{AVATAR}/france3.png"),
    ("france-4",            "France 4",         f"{AVATAR}/france4.png"),
    # === EMISSIONS / SERIES EN BOUCLE 24/7 (uniquement contenu VF) ===
    ("kaamelott-hd",        "Kaamelott",        f"{AVATAR}/kaamelott.jpg"),
    ("simpsons-vf",         "Les Simpsons VF",  f"{AVATAR}/simpsons.jpg"),
    ("south-park-fr",       "South Park FR",    f"{AVATAR}/southpark.jpg"),
    ("the-walking-dead",    "The Walking Dead",  f"{AVATAR}/twd.jpg"),
    ("h-integrale",         "H Integrale",      f"{AVATAR}/h.jpg"),
    ("camera-cafe-stream",  "Camera Cafe",      f"{AVATAR}/cameracafe.jpg"),
    ("triptank",            "Triptank",         f"{AVATAR}/triptank.jpg"),
    ("70-show",             "That 70s Show",    f"{AVATAR}/70show.jpg"),
    ("stargate-sg1-sga",    "Stargate",         f"{AVATAR}/stargate.jpg"),
    ("dragonball-dbz",      "Dragon Ball Z",    f"{AVATAR}/dragonball.jpg"),
    ("ddc",                 "DDC",              ""),
    ("divers-docs",         "Docs Divers",      ""),
    ("enquete-exclusive",   "Enquete Exclusive", f"{AVATAR}/specialinvestigation.jpg"),
    ("special-investigation","Special Investigation", f"{AVATAR}/specialinvestigation.jpg"),
    ("l-univers-et-ses-mysteres", "L'Univers",  f"{AVATAR}/univers.jpg"),
    ("tv-sciences",         "TV Sciences",      f"{AVATAR}/science.jpg"),
]


def main():
    print("Stream4Free m3u generator (catalogue hardcode, stream4free:// URLs)", file=sys.stderr)

    lines = ["#EXTM3U"]
    count = 0
    for slug, title, logo in sorted(CATALOG, key=lambda x: x[1].lower()):
        logo_attr = ' tvg-logo="%s"' % logo if logo else ""
        lines.append('#EXTINF:-1 group-title="%s"%s,%s' % (GROUP, logo_attr, title))
        lines.append("stream4free://" + slug)
        count += 1

    m3u = "\n".join(lines) + "\n"

    with open("data-stream4free.m3u", "w", encoding="utf-8") as f:
        f.write(m3u)

    print("  %d chaines ecrites dans data-stream4free.m3u" % count, file=sys.stderr)


if __name__ == "__main__":
    main()
