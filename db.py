"""Schéma SQLite de la base LNA."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "ch-volley-stats.db"

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
    -- Effectif officiel volleyball.ch ; poste déduit des feuilles quand il manque
    position     TEXT,      -- pass, diagonal, out, middle, libero
    position_src TEXT,      -- 'officiel' ou 'déduit'
    nat_code     TEXT,      -- code CIO (SUI, ARG…)
    nationality  TEXT,
    height       INTEGER,
    birth_year   INTEGER,
    photo        TEXT,
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
    sets_played  INTEGER DEFAULT 0,  -- titulaire ou entré en jeu dans le set
    start_zones  TEXT,               -- « 1:3,2:1S » = set:zone de départ, S = passeur titulaire
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

-- Bas de la feuille officielle : phases de jeu et bilan par rotation (set_no 0 uniquement)
CREATE TABLE IF NOT EXISTS team_phase (
    game_id   INTEGER NOT NULL REFERENCES games(game_id),
    team_id   INTEGER NOT NULL,
    rec       INTEGER, so_pts INTEGER,   -- réceptions / points marqués en side-out
    srv       INTEGER, bp_pts INTEGER,   -- services / points marqués en break-point
    pos_tot INTEGER, pos_pct INTEGER, pos_err INTEGER, pos_blo INTEGER,   -- 1re attaque après réception positive
    neg_tot INTEGER, neg_pct INTEGER, neg_err INTEGER, neg_blo INTEGER,   -- 1re attaque après réception négative
    dig_tot INTEGER, dig_pct INTEGER, dig_err INTEGER, dig_blo INTEGER,   -- attaque sur défense (contre-attaque)
    rot1 INTEGER, rot2 INTEGER, rot3 INTEGER, rot4 INTEGER, rot5 INTEGER, rot6 INTEGER,
    PRIMARY KEY (game_id, team_id)
);

-- Suivi des mises à jour automatiques (last_check, last_ok, last_import, last_error, rosters_at)
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""


def set_meta(con, key, value):
    con.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))


def get_meta(con, key):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def connect(path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    # Migration des bases créées avant l'ajout des colonnes (puis : scraper.py --reparse)
    for table, col, decl in MIGRATIONS:
        if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    return con


MIGRATIONS = [
    ("player_game", "sets_played", "INTEGER DEFAULT 0"),
    ("player_game", "start_zones", "TEXT"),
    ("players", "position", "TEXT"),
    ("players", "position_src", "TEXT"),
    ("players", "nat_code", "TEXT"),
    ("players", "nationality", "TEXT"),
    ("players", "height", "INTEGER"),
    ("players", "birth_year", "INTEGER"),
    ("players", "photo", "TEXT"),
]
