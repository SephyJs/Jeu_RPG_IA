from nicegui import app, ui
from app.ui.pages.game_page import game_page
from app.ui.pages.prototype_2d_page import prototype_2d_page


app.add_static_files('/assets', 'assets')  # dossier local ./assets
ui.add_head_html(
    """
    <style>
      html, body, #q-app {
        background: #0f1115 !important;
        color: #e5e7eb;
        color-scheme: dark;
      }
    </style>
    """,
    shared=True,
)

# Redirige la racine vers /game
@ui.page('/')
def index():
    ui.navigate.to('/game')

ui.run(title="Jeu Ataryxia", reload=True)
