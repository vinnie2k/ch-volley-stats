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
    g["players"] = q(con, """SELECT pg.*, p.name FROM player_game pg JOIN players p USING(player_id)
                             WHERE game_id=? ORDER BY pg.team_id, pg.number""", gid)
    g["team_sets"] = q(con, "SELECT * FROM team_game WHERE game_id=? ORDER BY team_id, set_no", gid)
    return g


def players(con, team_id=None):
    where, args = ("WHERE pg.team_id = ?", (team_id,)) if team_id else ("", ())
    return q(con, f"""
        SELECT p.player_id, p.name, t.name team, t.team_id, MAX(pg.libero) libero,
               (SELECT number FROM player_game x WHERE x.player_id = p.player_id ORDER BY game_id DESC LIMIT 1) number,
               COUNT(*) games, SUM(sets_started) sets_started, ROUND(AVG(vote), 1) vote, {AGG}
        FROM player_game pg JOIN players p USING(player_id) JOIN teams t ON t.team_id = pg.team_id
        {where}
        GROUP BY p.player_id
        HAVING SUM(pts_tot + srv_tot + rec_tot + att_tot) > 0 OR SUM(sets_started) > 0
        """, *args)


def player(con, pid):
    info = q(con, "SELECT p.player_id, p.name, t.name team, t.team_id FROM players p JOIN teams t USING(team_id) "
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


def teams(con):
    return q(con, f"""
        SELECT t.team_id, t.name, COUNT(*) games, {AGG}
        FROM team_game tg JOIN teams t USING(team_id)
        WHERE set_no = 0 GROUP BY t.team_id""")


def team_points_won(con):
    # La ligne « Players total » ne contient pas « Points won » : on somme les sets.
    return {r["team_id"]: r for r in q(con, """
        SELECT team_id, SUM(won_srv) won_srv, SUM(won_att) won_att, SUM(won_blk) won_blk, SUM(won_op_err) won_op_err
        FROM team_game WHERE set_no > 0 GROUP BY team_id""")}


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
                won = team_points_won(con)
                rows = teams(con)
                for r in rows:
                    r.update(won.get(r["team_id"], {}))
                return self.send_json(rows)
            if parts == ["meta"]:
                return self.send_json(dict(con.execute("""
                    SELECT COUNT(*) games_total,
                           SUM(home_sets IS NOT NULL) games_played,
                           SUM(stats_status = 'ok') games_with_stats,
                           MAX(CASE WHEN stats_status = 'ok' THEN play_date END) last_game
                    FROM games""").fetchone()))
            return self.send_json({"error": "route inconnue"}, 404)
        finally:
            con.close()


def update_loop(hours):
    """Lance scraper.py au démarrage puis chaque jour aux heures données (heure locale, TZ)."""
    while True:
        print(time.strftime("%Y-%m-%d %H:%M"), "mise à jour des données", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scraper.py")], cwd=ROOT)
        now = datetime.now()
        nxt = min(now.replace(hour=h, minute=0, second=0, microsecond=0) + timedelta(days=d)
                  for h in hours for d in (0, 1)
                  if now.replace(hour=h, minute=0, second=0, microsecond=0) + timedelta(days=d) > now)
        time.sleep((nxt - now).total_seconds())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--update-hours", default="",
                    help="heures de mise à jour automatique, ex. « 6,23 » (vide = aucune)")
    a = ap.parse_args()
    if a.update_hours:
        hours = [int(h) for h in a.update_hours.split(",")]
        threading.Thread(target=update_loop, args=(hours,), daemon=True).start()
    print(f"LNA stats : http://localhost:{a.port}", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
