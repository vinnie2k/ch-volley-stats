"""Postes, nationalité et fiche des joueurs.

1. Effectif officiel : la page « Équipe » de chaque club sur volleyball.ch embarque la liste
   des joueurs (poste, nationalité, taille, naissance, photo) dans son flux RSC.
2. Déduction quand le poste officiel manque, à partir des feuilles de stats :
   - libéro : marqué sur la feuille ;
   - passeur : case blanche (au lieu de grise) dans la colonne du set ;
   - les autres titulaires selon leur zone de départ par rapport au passeur
     (ordre classique P, ailier, central, diagonal, ailier, central) : diagonal en face (+3),
     ailiers et centraux sur les paires {+1, +4} et {+2, +5}, la paire qui réceptionne le plus
     étant celle des ailiers.
"""
import json
import re
import unicodedata
import urllib.request
from collections import Counter, defaultdict

BASE = "https://www.volleyball.ch"
UA = "Mozilla/5.0 (lna-stats; consultation statistiques LNA)"
LABELS = {"pass": "Passeur", "diagonal": "Diagonal", "out": "Ailier", "middle": "Central", "libero": "Libéro"}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def fetch_roster(club_id, team_id):
    req = urllib.request.Request(f"{BASE}/fr/game-center/club/{club_id}/team/{team_id}", headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
    # Le flux RSC est découpé en chaînes JSON dans des appels self.__next_f.push([1, "..."])
    rsc = "".join(json.loads(s) for s in re.findall(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', html))
    dec, players = json.JSONDecoder(), []
    for m in re.finditer(r'\{"image"', rsc):
        try:
            o, _ = dec.raw_decode(rsc, m.start())
        except ValueError:
            continue
        if "lastName" in o and "function" not in o and "number" in o:   # « function » = staff
            players.append(o)
    return players


def apply_rosters(con):
    """Renseigne les joueurs à partir des effectifs officiels. Renvoie le nombre de joueurs reliés."""
    con.execute("UPDATE players SET position=NULL, position_src=NULL")
    linked = 0
    for team in con.execute("SELECT team_id, club_id FROM teams").fetchall():
        try:
            roster = fetch_roster(team["club_id"], team["team_id"])
        except Exception as e:
            print(f"  effectif {team['team_id']} indisponible : {e}")
            continue
        ours = con.execute("""
            SELECT p.player_id, p.name,
                   (SELECT number FROM player_game x WHERE x.player_id = p.player_id ORDER BY game_id DESC LIMIT 1) number
            FROM players p WHERE team_id = ?""", (team["team_id"],)).fetchall()
        by_name = {_norm(p["name"]): p["player_id"] for p in ours}
        by_number = defaultdict(list)
        for p in ours:
            by_number[p["number"]].append(p["player_id"])
        for o in roster:
            pid = by_name.get(_norm(f'{o["lastName"]} {o["firstName"]}'))
            if pid is None and len(by_number.get(o.get("number"), [])) == 1:
                pid = by_number[o["number"]][0]   # nom orthographié autrement, même numéro
            if pid is None:
                continue
            nat = o.get("nationality") or {}
            pos = o.get("position")
            pos = "libero" if pos in ("libero", "libero2") else pos
            con.execute("""UPDATE players SET position=?, position_src=?, nat_code=?, nationality=?,
                           height=?, birth_year=?, photo=? WHERE player_id=?""",
                        (pos, "officiel" if pos else None, nat.get("iocCode"),
                         ((nat.get("translations") or {}).get("f") or {}).get("countryName"),
                         o.get("bodyHeight"), int(o["birthday"][:4]) if o.get("birthday") else None,
                         o.get("image"), pid))
            linked += 1
    return linked


def infer_positions(con, only_missing=True):
    """Déduit le poste depuis les feuilles. Renvoie {player_id: poste}."""
    rows = con.execute("""SELECT game_id, team_id, player_id, libero, start_zones, rec_tot
                          FROM player_game""").fetchall()
    by_match = defaultdict(list)
    for r in rows:
        by_match[(r["game_id"], r["team_id"])].append(r)

    votes = defaultdict(Counter)
    for players in by_match.values():
        starts = []   # (player_id, set, zone, est_passeur)
        for r in players:
            for item in filter(None, (r["start_zones"] or "").split(",")):
                n, z = item.split(":")
                starts.append((r["player_id"], int(n), int(z.rstrip("S")), z.endswith("S")))
        setter_zone = {n: z for _, n, z, s in starts if s}
        offsets = defaultdict(set)   # joueur -> décalages par rapport au passeur
        for pid, n, z, s in starts:
            if n in setter_zone:
                offsets[pid].add((z - setter_zone[n]) % 6)
        rec = {r["player_id"]: r["rec_tot"] or 0 for r in players}
        pair_a = sum(rec[p] for p, offs in offsets.items() if offs & {1, 4})
        pair_b = sum(rec[p] for p, offs in offsets.items() if offs & {2, 5})
        out_pair, mid_pair = ({1, 4}, {2, 5}) if pair_a >= pair_b else ({2, 5}, {1, 4})
        for pid, n, z, s in starts:
            if n not in setter_zone:
                continue
            off = (z - setter_zone[n]) % 6
            role = "pass" if off == 0 else "diagonal" if off == 3 else "out" if off in out_pair else "middle"
            votes[pid][role] += 1
        for r in players:
            if r["libero"]:
                votes[r["player_id"]]["libero"] += 10

    guessed = {pid: c.most_common(1)[0][0] for pid, c in votes.items()}
    if only_missing:
        missing = {r[0] for r in con.execute("SELECT player_id FROM players WHERE position IS NULL")}
        for pid, pos in guessed.items():
            if pid in missing:
                con.execute("UPDATE players SET position=?, position_src='déduit' WHERE player_id=?", (pos, pid))
    return guessed
