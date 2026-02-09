from nicegui import ui

# Paramètres de l'optimisation
TILE_SIZE = 40
MAP_LAYOUT = [
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 2, 2, 0, 0, 0, 0, 0, 1], # Table de l'auberge
    [1, 0, 0, 0, 0, 0, 2, 2, 0, 1], # Autre table
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 3, 3, 3, 3, 0, 0, 0, 0, 1], # Comptoir
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 1, 1, 0, 1, 1, 1, 1, 1, 1], # Sortie en bas
]

class Auberge:
    def __init__(self):
        self.player_x = 1
        self.player_y = 1
        
    def render(self, canvas):
        canvas.clear()
        for y, row in enumerate(MAP_LAYOUT):
            for x, tile in enumerate(row):
                # Dessin des textures simplifiées
                if tile == 1: color = '#5d4037' # Murs (Bois sombre)
                elif tile == 2: color = '#8d6e63' # Tables
                elif tile == 3: color = '#d7ccc8' # Comptoir
                else: color = '#efebe9' # Sol
                
                canvas.fill_style(color)
                canvas.fill_rect(x * TILE_SIZE, y * TILE_SIZE, TILE_SIZE, TILE_SIZE)
        
        # Dessin du joueur (ton futur sprite 2D)
        canvas.fill_style('#e91e63')
        canvas.fill_circle(self.player_x * TILE_SIZE + 20, self.player_y * TILE_SIZE + 20, 15)

    def move(self, dx, dy, canvas):
        new_x, new_y = self.player_x + dx, self.player_y + dy
        # Gestion des collisions (si c'est du sol (0), on passe)
        if 0 <= new_y < len(MAP_LAYOUT) and 0 <= new_x < len(MAP_LAYOUT[0]):
            if MAP_LAYOUT[new_y][new_x] == 0:
                self.player_x, self.player_y = new_x, new_y
                self.render(canvas)

# Interface utilisateur
auberge = Auberge()

with ui.row().classes('w-full justify-center'):
    with ui.column():
        ui.label('🏰 L\'Auberge du Poney qui Tousse').classes('text-2xl font-bold')
        
        # Le Canvas de jeu
        canvas = ui.canvas(width=400, height=320).classes('border-4 border-amber-900 shadow-xl')
        auberge.render(canvas)

        # Contrôles (En plus du clavier)
        with ui.row():
            ui.button(icon='arrow_upward', on_click=lambda: auberge.move(0, -1, canvas))
            ui.button(icon='arrow_downward', on_click=lambda: auberge.move(0, 1, canvas))
            ui.button(icon='arrow_back', on_click=lambda: auberge.move(-1, 0, canvas))
            ui.button(icon='arrow_forward', on_click=lambda: auberge.move(1, 0, canvas))

# Gestion du clavier sous Ubuntu (Z,Q,S,D)
ui.keyboard(on_key=lambda e: auberge.move(1,0,canvas) if e.key.arrow_right or e.key == 'd' else 
                             auberge.move(-1,0,canvas) if e.key.arrow_left or e.key == 'q' else
                             auberge.move(0,-1,canvas) if e.key.arrow_up or e.key == 'z' else
                             auberge.move(0,1,canvas) if e.key.arrow_down or e.key == 's' else None)

ui.run(title="Grok RPG")