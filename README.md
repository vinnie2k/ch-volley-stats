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
- Importe les stats joueur par joueur et équipe par set dans `data/ch-volley-stats.db`.

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
- Sets joués = sets commencés dans le six de base (chiffre dans la colonne du set) ou entrés en
  remplacement (petit rectangle dessiné dans la colonne). Un libéro sans marque est compté sur tout
  le match s'il a des actions ; un autre joueur avec des actions mais sans marque (erreur de saisie)
  compte 1 set.
- Phases de jeu (onglet Stats équipes) : lues dans le bas de la feuille Data Volley — side-out
  (points marqués par action en réception), break-point (au service), 1re attaque après réception
  positive / négative, contre-attaque, bilan par rotation. La feuille ne donne qu'un % de points par
  tableau d'attaque : le nombre de points est reconstitué match par match (arrondi). Quelques feuilles
  présentent de petits écarts internes (1 à 3 unités) que l'on reprend tels quels.
- Classements par set / par match : un joueur doit avoir joué une part minimale des sets de son
  équipe (50 % par défaut, réglable dans l'onglet).
