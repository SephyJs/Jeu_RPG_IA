import os
import random
import json
import html
from nicegui import ui
from app.ui.state.game_state import GameState

IDLE_URL = "/assets/videos/idle.mp4"  # <- idle est dans assets/videos
VIDEOS_DIR = os.path.join("assets", "videos")


def pick_random_video_url() -> str | None:
    """Choisit une vidéo action dans assets/videos (PAS l'idle)."""
    if not os.path.isdir(VIDEOS_DIR):
        return None
    vids = [f for f in os.listdir(VIDEOS_DIR) if f.lower().endswith(".mp4") and f.lower() != "idle.mp4"]
    if not vids:
        return None
    return f"/assets/videos/{random.choice(vids)}"


def set_narrator_text_js(client, text: str) -> None:
    """Met à jour le texte du narrateur via JS."""
    safe = json.dumps(text or "")
    client.run_javascript(f"""
    (function() {{
      const el = document.getElementById('ataryxia_narrator_text');
      if (el) el.textContent = {safe};
    }})();
    """)


def play_action_video_js(client, url: str) -> None:
    """Joue une vidéo action par overlay."""
    if not url:
        return
    safe = json.dumps(url)
    client.run_javascript(f"""
    (function() {{
      const root = document.getElementById('ataryxia_video_root');
      if (!root) return;

      const old = document.getElementById('ataryxia_action');
      if (old) old.remove();

      const v = document.createElement('video');
      v.id = 'ataryxia_action';
      v.src = {safe};
      v.autoplay = true;
      v.muted = true;
      v.playsInline = true;
      v.preload = 'auto';
      v.style.position = 'absolute';
      v.style.inset = '0';
      v.style.width = '100%';
      v.style.height = '100%';
      v.style.objectFit = 'contain';
      v.style.opacity = '1';
      v.style.transition = 'opacity 0.7s ease';
      v.onended = () => {{ v.style.opacity = '0'; setTimeout(()=>v.remove(), 700); }};
      root.appendChild(v);
    }})();
    """)


def right_narrator(state: GameState) -> None:
    scene = state.current_scene()

    ui.label("Ataryxia").classes("text-lg font-semibold")
    ui.separator()

    # Bulle narratrice
    with ui.card().classes("w-full rounded-2xl shadow-sm"):
        ui.html(
            f'<div id="ataryxia_narrator_text" style="white-space: pre-wrap;">{html.escape(scene.narrator_text)}</div>'
        )

    # Vidéo action éventuelle (jouée une fois au-dessus de l'idle)
    action_url = getattr(state, "narrator_media_url", None)

    with ui.card().classes("w-full rounded-2xl shadow-md narrator-video-card").style(
        "height: 55vh; position: relative; overflow: hidden;"
    ):
        with ui.element("div").props("id=ataryxia_video_root").classes("w-full h-full").style(
            "position: relative; overflow: hidden;"
        ):
            # 1) IDLE en fond, toujours présent, toujours loop
            ui.html(f"""
            <video
              id="ataryxia_idle"
              src="{IDLE_URL}"
              autoplay
              muted
              loop
              playsinline
              preload="metadata"
              style="
                position:absolute; inset:0;
                width:100%; height:100%;
                object-fit:contain;
                will-change: transform;
                transform: translate3d(0, 0, 0);
              "
              onloadeddata="this.play();"
            ></video>
            """)

            # 2) OVERLAY action au-dessus, si présent (pas loop)
            if action_url and isinstance(action_url, str) and action_url.lower().endswith(".mp4"):
                ui.html(f"""
                <video
                  id="ataryxia_action"
                  src="{action_url}"
                  autoplay
                  muted
                  playsinline
                  preload="auto"
                  onended="
                    this.remove(); 
                  "
                  onerror="console.error('Erreur vidéo action:', this.src)"
                  style="
                    position:absolute; inset:0;
                    width:100%; height:100%;
                    object-fit:contain;
                    will-change: transform;
                    transform: translate3d(0, 0, 0);
                  "
                ></video>
                """)

    ui.label(f"Lieu : {scene.title}").classes("text-sm opacity-70")

    if scene.npc_names:
        ui.separator()
        ui.label("PNJ présents :").classes("font-semibold")
        for n in scene.npc_names:
            ui.label(f"• {n}")
