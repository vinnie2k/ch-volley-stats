"""Petit serveur web de consultation (bibliothèque standard uniquement).

Usage :  python server.py [--port 8000]   puis ouvrir http://localhost:8000
"""
import argparse
import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import db

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
PDF_DIR = ROOT / "data" / "pdf"

# Agrégats communs joueurs / équipes. Les % de réception sont recalculés en
# pondérant par le nombre de réceptions (les PDF ne donnent que des % arrondis).
AGG = """
    SUM(pts_tot) pts, SUM(bp) bp, SUM(wl) wl,
    SUM(srv_tot) srv_tot, SUM(srv_err) srv_err, SUM(srv_pts) srv_pts,
    SUM(rec_tot) rec_tot, SUM(rec_err) rec_err,
    ROUND(1.0 * SUM(rec_tot * rec_pos_pct) / NULLIF(SUM(rec_tot), 0)) rec_pos_pct,
    ROUND(1.0 * SUM(rec_tot * rec_exc_pct) / NULLIF(SUM(rec_tot), 0)) rec_exc_pct,
    SUM(att_tot) att_tot, SUM(att_err) att_err, SUM(att_blo) att_blo, SUM(att_pts) att_pts,
    ROUND(100.0 * SUM(att_pts) / NULLIF(SUM(att_tot), 0)) att_pct,
    ROUND(100.0 * (SUM(att_pts) - SUM(att_err) - SUM(att_blo)) / NULLIF(SUM(att_tot), 0)) att_eff,
    SUM(blk_pts) blk_pts
"""


# Fiche joueur issue de l'effectif officiel (poste éventuellement déduit des feuilles)
PROFILE = "p.position, p.position_src, p.nat_code, p.nationality, p.height, p.birth_year, p.photo"


def q(con, sql, *args):
    return [dict(r) for r in con.execute(sql, args)]


def standings(con):
    table = {}
    for t in con.execute("SELECT team_id, name FROM teams"):
        table[t["team_id"]] = dict(team_id=t["team_id"], name=t["name"], games=0, pts=0, w=0, l=0,
                                   sets_w=0, sets_l=0, balls_w=0, balls_l=0)
    for g in con.execute("SELECT * FROM games WHERE home_sets IS NOT NULL"):
        sets = json.loads(g["set_results"])
        for tid, mine, theirs, side in ((g["home_team_id"], g["home_sets"], g["away_sets"], 0),
                                        (g["away_team_id"], g["away_sets"], g["home_sets"], 1)):
            r = table[tid]
            r["games"] += 1
            r["sets_w"] += mine
            r["sets_l"] += theirs
            r["balls_w"] += sum(s[side] for s in sets)
            r["balls_l"] += sum(s[1 - side] for s in sets)
            won = mine > theirs
            r["w" if won else "l"] += 1
            # Barème : 3-0 / 3-1 = 3 pts ; 3-2 = 2 pts ; 2-3 = 1 pt
            r["pts"] += (3 if theirs < 2 else 2) if won else (1 if mine == 2 else 0)
    rows = [r for r in table.values() if r["games"]]
    rows.sort(key=lambda r: (-r["pts"], -r["w"], -(r["sets_w"] / max(r["sets_l"], 1)),
                             -(r["balls_w"] / max(r["balls_l"], 1))))
    return rows


def games(con):
    return q(con, """
        SELECT g.game_id, g.play_date, g.phase, g.home_sets, g.away_sets, g.set_results, g.city, g.stats_status,
               g.home_team_id, h.name home, g.away_team_id, a.name away
        FROM games g JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id
        ORDER BY g.play_date""")


def game(con, gid):
    rows = games(con)
    g = next((r for r in rows if r["game_id"] == gid), None)
    if not g:
        return None
    full = dict(con.execute("SELECT hall, referees FROM games WHERE game_id=?", (gid,)).fetchone())
    g.update(full)
    g["players"] = q(con, """SELECT pg.*, p.name, p.position, p.position_src FROM player_game pg JOIN players p USING(player_id)
                             WHERE game_id=? ORDER BY pg.team_id, pg.number""", gid)
    g["team_sets"] = q(con, "SELECT * FROM team_game WHERE game_id=? ORDER BY team_id, set_no", gid)
    return g


def players(con, team_id=None):
    where, args = ("WHERE pg.team_id = ?", (team_id,)) if team_id else ("", ())
    return q(con, f"""
        SELECT p.player_id, p.name, t.name team, t.team_id, MAX(pg.libero) libero, {PROFILE},
               (SELECT number FROM player_game x WHERE x.player_id = p.player_id ORDER BY game_id DESC LIMIT 1) number,
               -- Matchs / sets joués : la feuille liste aussi les remplaçants restés sur le banc
               SUM(sets_played > 0) games, SUM(sets_played) sets, SUM(sets_started) sets_started,
               ROUND(AVG(vote), 1) vote, {AGG},
               -- Sets et matchs disputés par l'équipe (feuilles importées), pour les minimums
               (SELECT SUM(home_sets + away_sets) FROM games g WHERE g.stats_status = 'ok'
                  AND t.team_id IN (g.home_team_id, g.away_team_id)) team_sets,
               (SELECT COUNT(*) FROM games g WHERE g.stats_status = 'ok'
                  AND t.team_id IN (g.home_team_id, g.away_team_id)) team_games
        FROM player_game pg JOIN players p USING(player_id) JOIN teams t ON t.team_id = pg.team_id
        {where}
        GROUP BY p.player_id
        HAVING SUM(sets_played) > 0
        """, *args)


def player(con, pid):
    info = q(con, f"SELECT p.player_id, p.name, t.name team, t.team_id, {PROFILE} FROM players p JOIN teams t USING(team_id) "
                  "WHERE player_id=?", pid)
    if not info:
        return None
    info = info[0]
    info["games"] = q(con, """
        SELECT pg.*, g.play_date, g.home_team_id, g.home_sets, g.away_sets,
               CASE WHEN g.home_team_id = pg.team_id THEN a.name ELSE h.name END opponent
        FROM player_game pg JOIN games g USING(game_id)
        JOIN teams h ON h.team_id = g.home_team_id JOIN teams a ON a.team_id = g.away_team_id
        WHERE player_id = ? ORDER BY g.play_date""", pid)
    return info


PHASE_SUMS = ", ".join(
    f"SUM({b}_tot) {b}_tot, SUM(ROUND({b}_pct * {b}_tot / 100.0)) {b}_pts, "
    f"SUM({b}_err) {b}_err, SUM({b}_blo) {b}_blo" for b in ("pos", "neg", "dig"))


def teams(con):
    # Les « Points won » n'existent que sur les lignes de set ; l'adversaire de chaque
    # match fournit les stats concédées (fautes données, attaque adverse, aces subis).
    return q(con, f"""
        WITH won AS (
            SELECT game_id, team_id, SUM(won_srv) won_srv, SUM(won_att) won_att,
                   SUM(won_blk) won_blk, SUM(won_op_err) won_op_err
            FROM team_game WHERE set_no > 0 GROUP BY game_id, team_id),
        pair AS (
            SELECT a.game_id, a.team_id, b.team_id opp_id
            FROM team_game a JOIN team_game b
              ON b.game_id = a.game_id AND b.set_no = 0 AND b.team_id <> a.team_id
            WHERE a.set_no = 0),
        own AS (SELECT team_id, COUNT(*) games, {AGG} FROM team_game WHERE set_no = 0 GROUP BY team_id),
        mine AS (SELECT team_id, SUM(won_srv) won_srv, SUM(won_att) won_att, SUM(won_blk) won_blk,
                        SUM(won_op_err) won_op_err FROM won GROUP BY team_id),
        opp AS (
            SELECT p.team_id, SUM(o.att_tot) opp_att_tot, SUM(o.att_pts) opp_att_pts,
                   SUM(o.att_err + o.att_blo) opp_att_fail, SUM(o.srv_pts) opp_srv_pts,
                   SUM(o.blk_pts) opp_blk_pts, SUM(ow.won_op_err) err_given,
                   SUM(ow.won_srv + ow.won_att + ow.won_blk + ow.won_op_err) pts_lost
            FROM pair p
            JOIN team_game o ON o.game_id = p.game_id AND o.team_id = p.opp_id AND o.set_no = 0
            JOIN won ow ON ow.game_id = p.game_id AND ow.team_id = p.opp_id
            GROUP BY p.team_id),
        sets AS (SELECT p.team_id, SUM(g.home_sets + g.away_sets) sets
                 FROM pair p JOIN games g USING(game_id) GROUP BY p.team_id),
        -- Phases de jeu (bas de la feuille). La feuille donne un % de points par tableau :
        -- on reconstitue le nombre de points match par match avant de sommer.
        ph AS (
            SELECT team_id, SUM(rec) rec, SUM(so_pts) so_pts, SUM(srv) srv, SUM(bp_pts) bp_pts,
                   {PHASE_SUMS},
                   SUM(rot1) rot1, SUM(rot2) rot2, SUM(rot3) rot3, SUM(rot4) rot4, SUM(rot5) rot5, SUM(rot6) rot6
            FROM team_phase GROUP BY team_id)
        SELECT t.name, own.*, mine.won_srv, mine.won_att, mine.won_blk, mine.won_op_err,
               mine.won_srv + mine.won_att + mine.won_blk + mine.won_op_err pts_won,
               opp.opp_att_tot, opp.opp_att_pts, opp.opp_att_fail, opp.opp_srv_pts, opp.opp_blk_pts,
               opp.err_given, opp.pts_lost, sets.sets, ph.*
        FROM teams t JOIN own USING(team_id) JOIN mine USING(team_id)
        JOIN opp USING(team_id) JOIN sets USING(team_id) LEFT JOIN ph USING(team_id)""")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC), **kw)

    def log_message(self, *a):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        path, qs = url.path, parse_qs(url.query)
        if path.startswith("/pdf/"):
            m = re.fullmatch(r"/pdf/(\d+)", path)
            f = PDF_DIR / f"{m.group(1)}.pdf" if m else None
            if not f or not f.exists():
                return self.send_error(404)
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if not path.startswith("/api/"):
            return super().do_GET()

        con = db.connect()
        try:
            parts = path.strip("/").split("/")[1:]
            if parts == ["standings"]:
                return self.send_json(standings(con))
            if parts == ["games"]:
                return self.send_json(games(con))
            if len(parts) == 2 and parts[0] == "games" and parts[1].isdigit():
                g = game(con, int(parts[1]))
                return self.send_json(g) if g else self.send_json({"error": "introuvable"}, 404)
            if parts == ["players"]:
                tid = qs.get("team", [None])[0]
                return self.send_json(players(con, int(tid) if tid else None))
            if len(parts) == 2 and parts[0] == "players" and parts[1].isdigit():
                p = player(con, int(parts[1]))
                return self.send_json(p) if p else self.send_json({"error": "introuvable"}, 404)
            if parts == ["teams"]:
                return self.send_json(teams(con))
            if parts == ["meta"]:
                meta = dict(con.execute("""
                    SELECT COUNT(*) games_total,
                           SUM(home_sets IS NOT NULL) games_played,
                           SUM(stats_status = 'ok') games_with_stats,
                           -- matchs joués dont la feuille n'est pas (encore) intégrée
                           SUM(home_sets IS NOT NULL AND COALESCE(stats_status, '') <> 'ok') games_pending,
                           MAX(CASE WHEN stats_status = 'ok' THEN play_date END) last_game
                    FROM games""").fetchone())
                meta.update({r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")})
                return self.send_json(meta)
            return self.send_json({"error": "route inconnue"}, 404)
        finally:
            con.close()


def update_loop(every):
    """Lance scraper.py au démarrage puis toutes les `every` minutes, calé sur hh:05 (heure locale, TZ).
    Un passage sans nouveau match ne coûte qu'un appel au site ; les feuilles publiées tard dans la
    soirée sont ainsi intégrées dans l'heure."""
    while True:
        print(time.strftime("%Y-%m-%d %H:%M"), "mise à jour des données", flush=True)
        try:
            subprocess.run([sys.executable, str(ROOT / "scraper.py")], cwd=ROOT, timeout=20 * 60)
        except subprocess.TimeoutExpired:
            print("mise à jour interrompue après 20 min", flush=True)
        now = datetime.now()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=5)
        slots = int((now - day).total_seconds() // (every * 60)) + 1
        time.sleep(max(60, (day + timedelta(minutes=every * slots) - now).total_seconds()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--update-every", type=int, default=0,
                    help="mise à jour automatique toutes les N minutes (0 = aucune)")
    a = ap.parse_args()
    if a.update_every:
        threading.Thread(target=update_loop, args=(a.update_every,), daemon=True).start()
    print(f"ch-volley-stats : http://localhost:{a.port}", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
