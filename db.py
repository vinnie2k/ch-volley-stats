"""Schéma SQLite de la base LNA."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "lna.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    team_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    club_id     TEXT,
    logo        TEXT
);

CREATE TABLE IF NOT EXISTS games (
    game_id       INTEGER PRIMARY KEY,
    season        INTEGER NOT NULL,
    league_id     INTEGER NOT NULL,
    phase         TEXT,
    group_name    TEXT,
    play_date     TEXT,
    home_team_id  INTEGER REFERENCES teams(team_id),
    away_team_id  INTEGER REFERENCES teams(team_id),
    home_sets     INTEGER,
    away_sets     INTEGER,
    set_results   TEXT,            -- JSON [[home, away], ...]
    hall          TEXT,
    city          TEXT,
    referees      TEXT,
    stats_status  TEXT             -- NULL = pas encore traité, 'ok', 'missing', 'error: ...'
);

CREATE TABLE IF NOT EXISTS players (
    player_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id    INTEGER NOT NULL REFERENCES teams(team_id),
    name       TEXT NOT NULL COLLATE NOCASE,   -- « von Burg » / « Von Burg » selon les feuilles
    UNIQUE (team_id, name)
);

-- Une ligne par joueur et par match (feuille de statistiques officielle)
CREATE TABLE IF NOT EXISTS player_game (
    game_id      INTEGER NOT NULL REFERENCES games(game_id),
    player_id    INTEGER NOT NULL REFERENCES players(player_id),
    team_id      INTEGER NOT NULL,
    number       INTEGER,
    libero       INTEGER DEFAULT 0,
    sets_started INTEGER DEFAULT 0,
    vote         REAL,
    pts_tot      INTEGER, bp INTEGER, wl INTEGER,
    srv_tot      INTEGER, srv_err INTEGER, srv_pts INTEGER,
    rec_tot      INTEGER, rec_err INTEGER, rec_pos_pct INTEGER, rec_exc_pct INTEGER,
    att_tot      INTEGER, att_err INTEGER, att_blo INTEGER, att_pts INTEGER, att_pct INTEGER,
    blk_pts      INTEGER,
    PRIMARY KEY (game_id, player_id)
);

-- Totaux d'équipe : set_no = 0 pour le match entier, 1..5 par set
CREATE TABLE IF NOT EXISTS team_game (
    game_id      INTEGER NOT NULL REFERENCES games(game_id),
    team_id      INTEGER NOT NULL,
    set_no       INTEGER NOT NULL,
    won_srv      INTEGER, won_att INTEGER, won_blk INTEGER, won_op_err INTEGER,
    pts_tot      INTEGER, bp INTEGER, wl INTEGER,
    srv_tot      INTEGER, srv_err INTEGER, srv_pts INTEGER,
    rec_tot      INTEGER, rec_err INTEGER, rec_pos_pct INTEGER, rec_exc_pct INTEGER,
    att_tot      INTEGER, att_err INTEGER, att_blo INTEGER, att_pts INTEGER, att_pct INTEGER,
    blk_pts      INTEGER,
    PRIMARY KEY (game_id, team_id, set_no)
);
"""


def connect(path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con
