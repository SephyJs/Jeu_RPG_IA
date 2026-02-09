# Tiles Named

Ce dossier contient les 132 tiles renommees avec des noms lisibles.

## Mapping complet

- Fichier: `tiles_mapping.tsv`
- Format: `ancien_id nouveau_nom`
- Exemple: `0039 ground_path_01.png`

## Tiles conseillees pour generation de map

- Sol herbe (`.`):
  - `ground_grass_01.png`
  - `ground_grass_02.png`
  - `ground_grass_03.png`
- Sol chemin/terre (`,`):
  - `ground_path_01.png`
  - `ground_path_02.png`
  - `ground_path_03.png`
  - `ground_path_04.png`
- Eau bloquante (`~`):
  - `water_01.png`
  - `water_02.png`
  - `water_03.png`
  - `water_04.png`
  - `water_05.png`

## Mur autotile (symbole `#`)

- Coin haut gauche: `wall_corner_outer_top_left.png`
- Cote haut: `wall_side_top.png`
- Coin haut droite: `wall_corner_outer_top_right.png`
- Cote gauche: `wall_side_left.png`
- Remplissage/centre: `wall_fill_center.png`
- Cote droit: `wall_side_right.png`
- Coin bas gauche: `wall_corner_outer_bottom_left.png`
- Cote bas: `wall_side_bottom.png`
- Coin bas droite: `wall_corner_outer_bottom_right.png`

Convention de nommage mur:

- `wall_corner_outer_*`: tuiles de coin externes.
- `wall_side_*`: tuiles de cote/bord (haut, bas, gauche, droite).
- `wall_fill_center`: tuile de remplissage interieur.
