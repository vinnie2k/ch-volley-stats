"""Récupère les matchs de LNA Hommes sur volleyball.ch, télécharge les feuilles de
statistiques officielles (PDF) et les importe dans la base SQLite.

Usage :  python scraper.py [--season 2026] [--league 7088] [--reparse]
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

import db
import parse_stats
import positions

BASE = "https://www.volleyball.ch"
PDF_DIR = Path(__file__).parent / "data" / "pdf"
UA = "Mozilla/5.0 (ch-volley-stats; consultation statistiques LNA)"
# Identifiant de la « server action » Next.js qui renvoie la liste des matchs.
# Il change quand le site est redéployé : on le redécouvre alors automatiquement.
GAMES_ACTION = "4f4a04331e8b7f7726ca7b74caf8b2e1358570d4"
ACTION_CACHE = Path(__file__).parent / "data" / "action_id.txt"
# Caractères perdus dans les PDF sources eux-mêmes (le « ñ » devient « ? »)
NAME_FIXES = {"Urue?a Daniel": "Urueña Daniel"}


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def call_action(action_id, args, route="/fr/game-center"):
    body = http(BASE + route, json.dumps(args).encode(), {
        "Accept": "text/x-component",
        "Next-Action": action_id,
        "Content-Type": "text/plain;charset=UTF-8",
    }).decode("utf-8")
    # Réponse RSC : lignes « 0:... », « 1:<json> »
    for line in body.splitlines():
        if line.startswith("1:"):
            return json.loads(line[2:])
    return None


def _looks_like_games(res):
    return isinstance(res, list) and (not res or "gameId" in res[0])


def fetch_games(season, league):
    args = [f"?leagueId={league}&includeCup=0&dateStart={season}-05-21&dateEnd={season + 1}-05-20"]
    candidates = [ACTION_CACHE.read_text().strip()] if ACTION_CACHE.exists() else []
    candidates.append(GAMES_ACTION)
    for aid in candidates:
        try:
            res = call_action(aid, args)
            if _looks_like_games(res):
                return res
        except Exception:
            pass
    # Redécouverte : on essaie chaque identifiant d'action présent dans les scripts du site
    print("Identifiant d'action périmé, recherche dans les scripts du site…")
    html = http(BASE + "/fr/game-center").decode("utf-8", "replace")
    for chunk in sorted(set(re.findall(r'/_next/static/chunks/[^"]+\.js', html))):
        js = http(BASE + chunk).decode("utf-8", "replace")
        for aid in dict.fromkeys(re.findall(r'\("([0-9a-f]{40})"\)', js)):
            try:
                res = call_action(aid, args)
            except Exception:
                continue
            if res and _looks_like_games(res):
                ACTION_CACHE.parent.mkdir(parents=True, exist_ok=True)
                ACTION_CACHE.write_text(aid)
                return res
    raise RuntimeError("Impossible de récupérer la liste des matchs")


def upsert_game(con, g, season, league):
    for side in ("home", "away"):
        t = g["teams"][side]
        con.execute("INSERT INTO teams(team_id, name, club_id, logo) VALUES (?,?,?,?) "
                    "ON CONFLICT(team_id) DO UPDATE SET name=excluded.name, logo=excluded.logo",
                    (t["teamId"], t["caption"], t["clubId"], t["logo"]))
    rs = g.get("resultSummary") or {}
    sets = [[s["home"], s["away"]] for s in g.get("setResults") or []]
    refs = ", ".join(f'{r["firstName"]} {r["lastName"]}' for k, r in sorted((g.get("referees") or {}).items())
                     if k in ("1", "2") and r)
    hall = g.get("hall") or {}
    con.execute("""
        INSERT INTO games(game_id, season, league_id, phase, group_name, play_date, home_team_id, away_team_id,
                          home_sets, away_sets, set_results, hall, city, referees)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(game_id) DO UPDATE SET play_date=excluded.play_date, home_sets=excluded.home_sets,
            away_sets=excluded.away_sets, set_results=excluded.set_results, hall=excluded.hall,
            city=excluded.city, referees=excluded.referees, phase=excluded.phase, group_name=excluded.group_name
    """, (g["gameId"], season, league,
          (g.get("phase") or {}).get("translations", {}).get("f") or (g.get("phase") or {}).get("caption"),
          (g.get("group") or {}).get("caption"),
          g["playDate"], g["teams"]["home"]["teamId"], g["teams"]["away"]["teamId"],
          rs.get("wonSetsHomeTeam") if sets else None, rs.get("wonSetsAwayTeam") if sets else None,
          json.dumps(sets), hall.get("caption"), hall.get("city"), refs))
    return bool(sets)


def download_pdf(season, game_id):
    path = PDF_DIR / f"{game_id}.pdf"
    if path.exists() and path.stat().st_size > 1000:
        return path
    url = f"{BASE}/api/game-center/download/statistics/{season}{(season + 1) % 100:02d}/Men/{game_id}"
    try:
        data = http(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    if not data.startswith(b"%PDF"):
        return None
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    time.sleep(0.5)  # politesse envers le serveur
    return path


def import_stats(con, game_id, home_id, away_id, pdf_path):
    teams = parse_stats.parse(pdf_path)
    con.execute("DELETE FROM player_game WHERE game_id=?", (game_id,))
    con.execute("DELETE FROM team_game WHERE game_id=?", (game_id,))
    con.execute("DELETE FROM team_phase WHERE game_id=?", (game_id,))
    for team, team_id in zip(teams, (home_id, away_id)):  # le PDF liste l'équipe recevante en premier
        for p in team["players"]:
            p["name"] = NAME_FIXES.get(p["name"], p["name"])
            con.execute("INSERT OR IGNORE INTO players(team_id, name) VALUES (?,?)", (team_id, p["name"]))
            pid = con.execute("SELECT player_id FROM players WHERE team_id=? AND name=?",
                              (team_id, p["name"])).fetchone()[0]
            cols = ["game_id", "player_id", "team_id"] + [k for k in p if k != "name"]
            con.execute(f"INSERT INTO player_game({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [game_id, pid, team_id] + [p[k] for k in p if k != "name"])
        ph = team.get("phase") or {}
        if ph:
            cols = ["game_id", "team_id"] + list(ph)
            con.execute(f"INSERT INTO team_phase({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [game_id, team_id] + list(ph.values()))
        for set_no, row in [(0, team["total"] or {})] + sorted(team["sets"].items()):
            cols = ["game_id", "team_id", "set_no"] + list(row)
            con.execute(f"INSERT INTO team_game({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [game_id, team_id, set_no] + list(row.values()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026, help="année de début de saison (2026 = 2026/27)")
    ap.add_argument("--league", type=int, default=7088, help="id de ligue (7088 = LNA Hommes 2026/27)")
    ap.add_argument("--reparse", action="store_true", help="réimporter aussi les matchs déjà traités")
    a = ap.parse_args()

    con = db.connect()
    games = fetch_games(a.season, a.league)
    print(f"{len(games)} matchs trouvés")
    played = [g for g in games if upsert_game(con, g, a.season, a.league)]
    con.commit()

    done = {r[0] for r in con.execute("SELECT game_id FROM games WHERE stats_status='ok'")}
    new = 0
    for g in played:
        gid = g["gameId"]
        if gid in done and not a.reparse:
            continue
        label = f'{gid} {g["teams"]["home"]["caption"]} – {g["teams"]["away"]["caption"]}'
        try:
            pdf = download_pdf(a.season, gid)
            if pdf is None:
                status = "missing"
            else:
                import_stats(con, gid, g["teams"]["home"]["teamId"], g["teams"]["away"]["teamId"], pdf)
                status = "ok"
                new += 1
        except Exception as e:
            status = f"error: {e}"
        con.execute("UPDATE games SET stats_status=? WHERE game_id=?", (status, gid))
        con.commit()
        print(f"  {label}: {status}")
    print(f"Terminé : {len(played)} matchs joués, {new} feuille(s) importée(s).")
    # Postes, nationalité et fiche : effectifs officiels, puis déduction des postes manquants
    linked = positions.apply_rosters(con)
    guessed = positions.infer_positions(con)
    con.commit()
    missing = con.execute("SELECT COUNT(*) FROM players WHERE position IS NULL").fetchone()[0]
    print(f"Effectifs : {linked} joueurs reliés, {len(guessed)} postes déduits des feuilles, {missing} sans poste.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
