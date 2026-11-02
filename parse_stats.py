"""Parseur des feuilles de statistiques officielles (rapport Data Volley, PDF 1 page).

Le PDF est lu par coordonnées : chaque mot est rattaché à la colonne d'en-tête
la plus proche (Vote, Tot, BP, W-L, Serve..., Reception..., Attack..., BK).
"""
import re
import sys

import pdfplumber

# Clés des colonnes, dans l'ordre de l'en-tête « Vote Tot BP W-L Tot Err Pts ... »
STAT_KEYS = ["vote", "pts_tot", "bp", "wl",
             "srv_tot", "srv_err", "srv_pts",
             "rec_tot", "rec_err", "rec_pos_pct", "rec_exc_pct",
             "att_tot", "att_err", "att_blo", "att_pts", "att_pct",
             "blk_pts"]
WON_KEYS = ["won_srv", "won_att", "won_blk", "won_op_err"]
NUM_RE = re.compile(r"^(\d+)([LC]*)$")  # « 22L » libéro, « 21C » capitaine


def _lines(page, tol=3.0):
    words = sorted(page.extract_words(x_tolerance=1.5), key=lambda w: (w["top"], w["x0"]))
    lines = []
    for w in words:
        if lines and abs(lines[-1]["top"] - w["top"]) < tol:
            lines[-1]["words"].append(w)
        else:
            lines.append({"top": w["top"], "words": [w]})
    for ln in lines:
        ln["words"].sort(key=lambda w: w["x0"])
        ln["text"] = " ".join(w["text"] for w in ln["words"])
    return lines


def _center(w):
    return (w["x0"] + w["x1"]) / 2


def _value(text, key):
    t = text.strip("()%")
    if t in (".", ""):
        return None if key == "vote" else 0
    try:
        return float(t) if key == "vote" else int(t)
    except ValueError:
        return None


def _assign(words, columns):
    """columns: liste (clé, centre_x). Renvoie {clé: valeur}."""
    out = {}
    for w in words:
        if w["text"] == "(":
            continue
        key = min(columns, key=lambda c: abs(c[1] - _center(w)))[0]
        out[key] = _value(w["text"], key)
    return out


def _nearest_set(x, set_cols):
    n, cx = min(set_cols, key=lambda c: abs(c[1] - x))
    return n if abs(cx - x) < 6 else None


def parse(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        lines = _lines(page)
        # Remplaçant entré en jeu : petit rectangle vide dans la colonne du set
        sub_marks = [((r["x0"] + r["x1"]) / 2, (r["top"] + r["bottom"]) / 2)
                     for r in page.rects if r["bottom"] - r["top"] < 5 and r["x1"] - r["x0"] < 12]

    # En-têtes de colonnes de stats (ligne contenant « Vote »)
    header_idx = [i for i, ln in enumerate(lines) if ln["words"][0]["text"] != "Vote"
                  and any(w["text"] == "Vote" for w in ln["words"])]
    if len(header_idx) != 2:
        raise ValueError(f"{len(header_idx)} blocs d'équipe trouvés au lieu de 2")

    teams = []
    for b, hi in enumerate(header_idx):
        hdr = lines[hi]["words"]
        vote_pos = next(i for i, w in enumerate(hdr) if w["text"] == "Vote")
        set_cols = [(int(w["text"]), _center(w)) for w in hdr[:vote_pos]]
        stat_cols = list(zip(STAT_KEYS, [_center(w) for w in hdr[vote_pos:]]))
        if len(stat_cols) != len(STAT_KEYS):
            raise ValueError("en-tête de colonnes inattendu")
        first_stat_x = hdr[vote_pos]["x0"] - 15
        last_set_x = set_cols[-1][1] + 8 if set_cols else first_stat_x

        end = header_idx[b + 1] if b + 1 < len(header_idx) else len(lines)
        players, total, sets, won_cols = [], None, {}, None
        for ln in lines[hi + 1:end]:
            ws = ln["words"]
            if ln["text"].startswith("Players total"):
                total = _assign([w for w in ws if w["x0"] > first_stat_x], stat_cols)
                continue
            if "won:" in ln["text"]:
                won_x = [w for w in ws if w["text"] in ("Ser", "Att", "BK", "Op.Er")]
                won_cols = list(zip(WON_KEYS, [_center(w) for w in won_x]))
                continue
            set_w = [w for w in ws if w["x0"] > 180]
            if len(set_w) > 2 and set_w[0]["text"] == "Set" and won_cols:
                n = int(set_w[1]["text"])
                rest = set_w[2:]
                won_limit = won_cols[-1][1] + 12
                row = _assign([w for w in rest if _center(w) <= won_limit], won_cols)
                row.update(_assign([w for w in rest if _center(w) > won_limit], stat_cols))
                sets[n] = row
                continue
            m = NUM_RE.match(ws[0]["text"])
            if total is None and m and ws[0]["x0"] < 40:
                libero = "L" in m.group(2) or any(
                    w["text"] == "L" and w["x0"] < 44 for w in ws[1:3])
                name = " ".join(w["text"] for w in ws
                                if 43 < w["x0"] < min(c[1] for c in set_cols) - 6)
                starts = [w for w in ws if w["x0"] < last_set_x and _center(w) > set_cols[0][1] - 6]
                p = {"number": int(m.group(1)), "name": name,
                     "libero": int(libero), "sets_started": len(starts)}
                p.update(_assign([w for w in ws if w["x0"] > first_stat_x], stat_cols))
                p["_y"] = (ws[0]["top"] + ws[0]["bottom"]) / 2
                p["_sets"] = {_nearest_set(_center(w), set_cols) for w in starts}
                players.append(p)

        # Rattache chaque marque de remplacement à la ligne de joueur la plus proche
        for x, y in sub_marks:
            n = _nearest_set(x, set_cols)
            near = min(players, key=lambda p: abs(p["_y"] - y), default=None)
            if n is not None and near and abs(near["_y"] - y) < 5:
                near["_sets"].add(n)
        n_sets = len(set_cols)
        for p in players:
            played = len(p.pop("_sets"))
            p.pop("_y")
            # Actions sans marque d'entrée : libéro (souvent non marqué) = tout le match,
            # autre joueur (erreur de saisie) = 1 set
            if not played and any(p[k] for k in STAT_KEYS if k != "vote"):
                played = n_sets if p["libero"] else 1
            p["sets_played"] = played
        teams.append({"players": players, "total": total, "sets": sets})
    for team, phase in zip(teams, _phases(lines)):
        team["phase"] = phase
    return teams


# Bloc du bas de la feuille : équipe recevante à gauche, visiteuse à droite.
# Les trois tableaux d'attaque du milieu ont l'en-tête « Err Blo Pts% Tot | Tot Pts% Blo Err ».
ATTACK_BLOCKS = ["pos", "neg", "dig"]   # après réception positive, négative, sur défense
SIDE_SPLIT_X = 300


def _num(text):
    t = text.strip("%")
    if t in (".", ""):
        return 0
    try:
        return int(t)
    except ValueError:
        return None


def _phases(lines):
    start = next((i for i, ln in enumerate(lines) if "KILL" in ln["text"]), None)
    if start is None:
        raise ValueError("section side-out / break-point introuvable")
    region = lines[start:]
    out = [{}, {}]

    # Totaux : nombre placé juste après « Receptions », « SO », « Serve », « BP »
    labels = {"Receptions": "rec", "SO": "so_pts", "Serve": "srv", "BP": "bp_pts"}
    for ln in region:
        ws = ln["words"]
        for a, b in zip(ws, ws[1:]):
            if a["text"] in labels and a["x0"] < 450 and _num(b["text"]) is not None and "." not in b["text"]:
                out[0 if a["x0"] < SIDE_SPLIT_X else 1][labels[a["text"]]] = _num(b["text"])

    # Tableaux d'attaque : en-tête puis première ligne de valeurs en dessous
    headers = [ln for ln in region if [w["text"] for w in ln["words"] if 120 < w["x0"] < 340]
               == ["Err", "Blo", "Pts%", "Tot", "Tot", "Pts%", "Blo", "Err"]]
    if len(headers) != 3:
        raise ValueError(f"{len(headers)} tableaux d'attaque au lieu de 3")
    keys = [(0, "err"), (0, "blo"), (0, "pct"), (0, "tot"), (1, "tot"), (1, "pct"), (1, "blo"), (1, "err")]
    for block, hdr in zip(ATTACK_BLOCKS, headers):
        cols = [_center(w) for w in hdr["words"] if 120 < w["x0"] < 340]
        vals = next(ln for ln in region if ln["top"] > hdr["top"] + 3
                    and sum(120 < w["x0"] < 340 for w in ln["words"]) >= 4)
        for side, k in keys:
            out[side][f"{block}_{k}"] = 0
        for w in vals["words"]:
            if 120 < w["x0"] < 340:
                i = min(range(8), key=lambda j: abs(cols[j] - _center(w)))
                side, k = keys[i]
                out[side][f"{block}_{k}"] = _num(w["text"])

    # Bilan par rotation (« S in » = position du passeur, « Diff » = points gagnés − perdus)
    for ln in region:
        ws = ln["words"]
        for i, (a, b) in enumerate(zip(ws, ws[1:])):
            side = 0 if a["x0"] < 35 else 1 if 405 < a["x0"] < 420 else None
            if side is not None and a["text"] in "123456" and len(a["text"]) == 1 \
                    and b["x0"] - a["x1"] < 20 and re.fullmatch(r"[+-]?\d+|\.", b["text"]):
                out[side][f"rot{a['text']}"] = _num(b["text"])
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(parse(sys.argv[1]), indent=1, ensure_ascii=False))
