#!/usr/bin/env python3
"""
refresh_stream4free.py - Genere data-stream4free.m3u avec des URLs stream4free://<slug>.

Le site stream4free.tv bloque les IPs datacenter (403 Forbidden) donc ce scraper
NE FETCHE PAS les pages. Il genere le m3u depuis un catalogue hardcode.
L'app ONYX resout le vrai flux m3u8 a la lecture via Stream4FreeResolver.

Pour ajouter une chaine : ajouter une entree dans CATALOG ci-dessous.
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
    # === EMISSIONS / SERIES EN BOUCLE 24/7 ===
    ("kaamelott-hd",        "Kaamelott",        f"{AVATAR}/kaamelott.jpg"),
    ("simpsons-vf",         "Les Simpsons VF",  f"{AVATAR}/simpsons.jpg"),
    ("the-simpsons",        "The Simpsons",     f"{AVATAR}/simpsons.jpg"),
    ("south-park-fr",       "South Park FR",    f"{AVATAR}/southpark.jpg"),
    ("south-park-us",       "South Park US",    f"{AVATAR}/southpark.jpg"),
    ("friends-live",        "Friends",          f"{AVATAR}/friends.jpg"),
    ("family-guy-hd",       "Family Guy",       f"{AVATAR}/family.jpg"),
    ("the-big-bang-theory",  "The Big Bang Theory", f"{AVATAR}/TBBT.jpg"),
    ("himym",               "HIMYM",            f"{AVATAR}/himym.jpg"),
    ("the-office",          "The Office",       f"{AVATAR}/theoffice.jpg"),
    ("breaking-bad",        "Breaking Bad",     f"{AVATAR}/bb.jpg"),
    ("game-of-thrones-hd",  "Game of Thrones",  f"{AVATAR}/game-of-thrones-logo.png"),
    ("the-walking-dead",    "The Walking Dead",  f"{AVATAR}/twd.jpg"),
    ("rick-and-morty",      "Rick and Morty",   f"{AVATAR}/rickmorty.jpg"),
    ("futurama",            "Futurama",         f"{AVATAR}/futurama.jpg"),
    ("american-dad-hd",     "American Dad",     f"{AVATAR}/american_dad.gif"),
    ("bobs-burgers",        "Bob's Burgers",    f"{AVATAR}/bobs.jpg"),
    ("archer",              "Archer",           f"{AVATAR}/archer.jpg"),
    ("sons-of-anarchy",     "Sons of Anarchy",  f"{AVATAR}/soa.jpg"),
    ("house-md",            "House MD",         f"{AVATAR}/house.jpg"),
    ("scrubs",              "Scrubs",           f"{AVATAR}/scrubs.jpg"),
    ("seinfeld",            "Seinfeld",         f"{AVATAR}/seinfeld.jpg"),
    ("h-integrale",         "H Integrale",      f"{AVATAR}/h.jpg"),
    ("camera-cafe-stream",  "Camera Cafe",      f"{AVATAR}/cameracafe.jpg"),
    ("greendale-college",   "Community",        f"{AVATAR}/community.jpg"),
    ("the-cleveland-show",  "Cleveland Show",   f"{AVATAR}/cleveland.jpg"),
    ("workaholics",         "Workaholics",      f"{AVATAR}/workaholics.jpg"),
    ("king-of-the-hill",    "King of the Hill", f"{AVATAR}/kinghill.jpg"),
    ("aqua-teen-hunger-force", "Aqua Teen",     f"{AVATAR}/aqua.jpg"),
    ("triptank",            "Triptank",         f"{AVATAR}/triptank.jpg"),
    ("70-show",             "That 70s Show",    f"{AVATAR}/70show.jpg"),
    ("always-sunny-in-philadelphia", "Always Sunny", f"{AVATAR}/sunny.jpg"),
    ("stargate-sg1-sga",    "Stargate",         f"{AVATAR}/stargate.jpg"),
    ("dragonball-dbz",      "Dragon Ball Z",    f"{AVATAR}/dragonball.jpg"),
    ("ddc",                 "DDC",              ""),
    ("divers-docs",         "Docs Divers",      ""),
    ("enquete-exclusive",   "Enquete Exclusive", f"{AVATAR}/specialinvestigation.jpg"),
    ("special-investigation","Special Investigation", f"{AVATAR}/specialinvestigation.jpg"),
    ("l-univers-et-ses-mysteres", "L'Univers",  f"{AVATAR}/univers.jpg"),
    ("tv-sciences",         "TV Sciences",      f"{AVATAR}/science.jpg"),
    ("poker-stream",        "Poker",            ""),
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
