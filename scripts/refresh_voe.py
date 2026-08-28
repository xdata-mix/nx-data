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
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

BASE = "https://voe.sx"
SORTIE = os.path.join(os.path.dirname(__file__), "..", "data", "voe.json")
PAR_PAGE = 100


# ⚠ 2026-08-28 — ESPACEMENT OBLIGATOIRE entre deux appels VOE.
#   Sans lui, le parcours dossier par dossier (une requete par dossier ET par page)
#   se prend un « HTTP 429 Too Many Requests » et le run rend zero fichier. C'est
#   exactement le piege deja documente cote application dans VoeLibrary, qui impose
#   300 ms entre TOUS ses appels, quelle qu'en soit l'origine. On est un peu plus
#   large ici : le workflow n'est presse par personne.
ESPACEMENT_S = 0.5
_dernier_appel = 0.0


def appel(chemin, params, essais=5):
    global _dernier_appel
    url = f"{BASE}{chemin}?" + urllib.parse.urlencode(params)
    attente = 2.0
    for n in range(essais):
        ecart = time.monotonic() - _dernier_appel
        if ecart < ESPACEMENT_S:
            time.sleep(ESPACEMENT_S - ecart)
        _dernier_appel = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nx-data/refresh_voe"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"    429 sur {chemin}, nouvel essai dans {attente:.0f}s", file=sys.stderr)
            elif n == essais - 1:
                raise
        except Exception:
            if n == essais - 1:
                raise
        if n < essais - 1:
            time.sleep(attente)
            attente *= 2
    return None


def dossiers_du_compte(cle):
    """Arborescence complete : {fld_id: "Chemin/Sous-chemin"}.

    ⚠ PARCOURS RECURSIF OBLIGATOIRE. Mesure sur le compte de reference : les trois
      dossiers de premier niveau (Films, Serie, Clip video musique) contiennent
      ZERO fichier en propre — tout est dans leurs sous-dossiers. Un simple
      `folder/list` sans fld_id ne rend que le premier niveau, d'ou un index ou
      chaque entree ressortait sans dossier. C'est le meme parcours que
      VoeLibrary.explorer cote application.
    """
    out = {}

    def descendre(fid, chemin, profondeur):
        if profondeur > 4:
            return
        params = {"key": cle}
        if fid is not None:
            params["fld_id"] = fid
        d = appel("/api/folder/list", params)
        if not d or not d.get("success"):
            return
        for f in (d.get("result") or {}).get("folders") or []:
            sous_id = f.get("fld_id")
            nom = (f.get("name") or "").strip()
            if sous_id is None or not nom:
                continue
            complet = f"{chemin}/{nom}" if chemin else nom
            out[str(sous_id)] = complet
            descendre(sous_id, complet, profondeur + 1)

    descendre(None, "", 0)
    return out

def fichiers_du_dossier(cle, libelle, fld_id, nom_dossier):
    out, page = [], 1
    while True:
        params = {"key": cle, "page": page, "per_page": PAR_PAGE, "details": "true"}
        if fld_id is not None:
            params["fld_id"] = fld_id
        d = appel("/api/file/list", params)

        if not d or not d.get("success"):
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
                "dossier": nom_dossier,
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
            dossiers = dossiers_du_compte(cle)
            # ⚠ ORDRE IMPORTANT — les DOSSIERS d'abord, la racine ENSUITE.
            #   Mesure : `file/list?fld_id=0` ne rend pas la racine mais TOUT le
            #   compte (2401 fichiers). Lance en premier, il raflait tout et la
            #   dedup laissait les passes par dossier sans effet : chaque entree
            #   ressortait avec un dossier vide. En finissant par lui, les
            #   fichiers ranges gardent leur dossier et il ne ramasse que le
            #   reliquat, c'est-a-dire ce qui n'est dans aucun dossier.
            lot = []
            for fid, nom in sorted(dossiers.items(), key=lambda kv: kv[1].lower()):
                lot += fichiers_du_dossier(cle, libelle, fid, nom)
            lot += fichiers_du_dossier(cle, libelle, 0, "")
            print(f"  [{libelle}] {len(dossiers)} dossier(s) : "
                  + ", ".join(sorted(dossiers.values())))
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