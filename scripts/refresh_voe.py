#!/usr/bin/env python3
"""
refresh_voe.py — index des bibliotheques VOE -> data/voe.json

Publie UNIQUEMENT une table titre -> filecode. Aucun fichier n'est deplace :
la lecture se fait sur https://voe.sx/e/{filecode}, lien public qui ne demande
aucune authentification. Les cles API, elles, ne quittent jamais les secrets
GitHub — c'est tout l'interet de generer l'index ici plutot que de la mettre
en dur dans l'APK (ou elle serait extractible, et autorise /api/file/delete).

Secret attendu : VOE_KEYS = "perso:CLE1,ami:CLE2"  (libelle:cle, virgules)
Le libelle sert au champ "lib" : l'app n'affiche un dossier TV Hub que pour
"perso", et fabrique des serveurs de secours pour tous les libelles.
"""
import json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

BASE = "https://voe.sx"
SORTIE = os.path.join(os.path.dirname(__file__), "..", "data", "voe.json")
PAR_PAGE = 100


def appel(chemin, params, essais=3):
    url = f"{BASE}{chemin}?" + urllib.parse.urlencode(params)
    for n in range(essais):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nx-data/refresh_voe"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if n == essais - 1:
                raise
            time.sleep(2 * (n + 1))


def fichiers_du_compte(cle, libelle):
    out, page = [], 1
    while True:
        d = appel("/api/file/list", {"key": cle, "page": page, "per_page": PAR_PAGE, "details": "true"})
        if not d.get("success"):
            print(f"  [{libelle}] reponse en echec : {d.get('message')}", file=sys.stderr)
            break
        res = d.get("result") or {}
        for f in res.get("data") or []:
            code = (f.get("filecode") or "").strip()
            if not code:
                continue
            video = f.get("video") or {}
            out.append({
                "code": code,
                "titre": (f.get("title") or f.get("name") or "").strip(),
                "nom": (f.get("name") or "").strip(),
                "q": int(video.get("height") or 0),
                "taille": int(f.get("size") or 0),
                "lib": libelle,
            })
        derniere = int(res.get("last_page") or 1)
        if page >= derniere:
            break
        page += 1
    return out


def main():
    brut = os.environ.get("VOE_KEYS", "").strip()
    if not brut:
        print("VOE_KEYS absent — rien a faire.", file=sys.stderr)
        return 1

    comptes = []
    for morceau in brut.split(","):
        morceau = morceau.strip()
        if not morceau:
            continue
        libelle, _, cle = morceau.partition(":")
        if not cle:
            libelle, cle = "perso", libelle
        comptes.append((libelle.strip(), cle.strip()))

    items, vus = [], set()
    for libelle, cle in comptes:
        try:
            lot = fichiers_du_compte(cle, libelle)
        except Exception as e:
            print(f"  [{libelle}] ECHEC : {e}", file=sys.stderr)
            continue
        neufs = 0
        for it in lot:
            if it["code"] in vus:
                continue
            vus.add(it["code"])
            items.append(it)
            neufs += 1
        print(f"  [{libelle}] {len(lot)} fichiers, {neufs} retenus")

    if not items:
        print("aucun fichier — index NON reecrit (on ne remplace pas par du vide)", file=sys.stderr)
        return 1

    items.sort(key=lambda x: (x["lib"], x["titre"].lower()))
    doc = {
        "genere": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(items),
        "items": items,
    }
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    print(f"data/voe.json : {len(items)} entrees")
    return 0


if __name__ == "__main__":
    sys.exit(main())