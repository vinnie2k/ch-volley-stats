# LNA Hommes – statistiques

Récupère les feuilles de statistiques officielles (PDF Data Volley) des matchs de LNA Hommes
publiées sur le Game Center de volleyball.ch, les met dans une base SQLite et les présente
dans une petite app web.

## Installation

```
pip install -r requirements.txt
```

## Mise à jour des données

```
python scraper.py
```

- Récupère la liste des matchs (résultats, sets, salle, arbitres) de la saison.
- Télécharge la feuille de stats de chaque match joué dans `data/pdf/<id>.pdf` (une seule fois).
- Importe les stats joueur par joueur et équipe par set dans `data/lna.db`.

À relancer après chaque journée : seuls les nouveaux matchs sont traités.
Options : `--season 2027 --league <id>` pour une autre saison (l'id de ligue est dans l'URL du
Game Center, paramètre `i_league`), `--reparse` pour réimporter depuis les PDF déjà téléchargés.

## Consultation

```
python server.py            # puis http://localhost:8000
```

Onglets : classement, matchs (avec feuille détaillée et lien vers le PDF officiel), joueurs
(classements par catégorie : général, attaque, service, réception, bloc ; fiche match par match),
équipes.

## Fichiers

| Fichier | Rôle |
|---|---|
| `scraper.py` | liste des matchs, téléchargement des PDF, import |
| `parse_stats.py` | lecture du PDF par coordonnées (`python parse_stats.py data/pdf/401087.pdf` pour tester) |
| `db.py` | schéma SQLite |
| `server.py` | API JSON + fichiers statiques |
| `static/index.html` | interface |

## Points d'attention

- La liste des matchs vient d'une « server action » Next.js du site. Son identifiant change quand
  volleyball.ch est redéployé ; le scraper le redécouvre alors automatiquement (et le mémorise dans
  `data/action_id.txt`).
- Les % de réception agrégés sont recalculés en pondérant par le nombre de réceptions, à partir des
  % arrondis du PDF : ils peuvent différer d'un point des valeurs exactes.
- Un joueur est identifié par (équipe, nom) : un transfert en cours de saison crée deux fiches.
- « Sets tit. » = sets commencés dans le six de base ; les entrées en cours de set ne sont pas
  comptées (le PDF ne les indique pas sous forme de texte).
