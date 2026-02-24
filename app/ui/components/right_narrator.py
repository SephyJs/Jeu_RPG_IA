import html
import json
import os
import random
from nicegui import ui
from app.gamemaster.npc_manager import (
    profile_display_name,
    profile_corruption_level,
    profile_tension_level,
    resolve_profile_role,
    tension_tier_label,
)
from app.ui.components.npc_world import resolve_scene_npc_key
from app.ui.state.game_state import GameState

IDLE_URL = "/assets/videos/idle.mp4"  # <- idle est dans assets/videos
VIDEOS_DIR = os.path.join("assets", "videos")


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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

    with ui.card().classes("w-full rounded-2xl shadow-md narrator-video-card h-64 md:h-[55vh]").style(
        "position: relative; overflow: hidden;"
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
    ui.separator()

    selected_npc = str(getattr(state, "selected_npc", "") or "").strip()
    if selected_npc:
        ui.label("PNJ en discussion").classes("font-semibold")
        with ui.card().classes("w-full rounded-2xl shadow-sm"):
            npc_key = resolve_scene_npc_key(state, selected_npc, scene.id)
            profile = state.npc_profiles.get(npc_key) if isinstance(state.npc_profiles, dict) else None
            if isinstance(profile, dict):
                display_name = profile_display_name(profile, selected_npc)
                role = resolve_profile_role(profile, selected_npc)
                ui.label(f"{display_name} ({role})").classes("text-sm")

                identity = profile.get("identity", {}) if isinstance(profile.get("identity"), dict) else {}
                species = str(identity.get("species") or "").strip()
                gender = str(identity.get("gender") or "").strip()
                if species or gender:
                    bits = [x for x in (species, gender) if x]
                    ui.label("Identite: " + " | ".join(bits)).classes("text-xs opacity-80")

                persona = str(profile.get("char_persona") or "").strip()
                if persona:
                    ui.label("Profil: " + persona[:220]).classes("text-xs opacity-80")

                emotional = profile.get("emotional_state", {}) if isinstance(profile.get("emotional_state"), dict) else {}
                flags = profile.get("dynamic_flags", {}) if isinstance(profile.get("dynamic_flags"), dict) else {}
                mood = str(emotional.get("dominant_emotion") or flags.get("current_mood") or "neutre").strip()
                toward = str(emotional.get("toward_player") or "neutre").strip()
                ui.label(f"Humeur: {mood} | Attitude joueur: {toward}").classes("text-xs opacity-80")
                tension = profile_tension_level(profile)
                ui.label(f"Tension: {tension_tier_label(tension)}").classes("text-xs opacity-75")
                corruption = profile_corruption_level(profile)
                ui.label(f"Corruption: {corruption}/100").classes("text-xs opacity-75")
                attraction_map = profile.get("attraction_map") if isinstance(profile.get("attraction_map"), dict) else {}
                player_name = str(getattr(state.player, "name", "") or "").strip()
                attraction = 0
                if player_name:
                    try:
                        attraction = max(0, min(100, int(attraction_map.get(player_name) or 0)))
                    except (TypeError, ValueError):
                        attraction = 0
                ui.label(f"Attraction: {attraction}/100").classes("text-xs opacity-75")

                needs = profile.get("needs") if isinstance(profile.get("needs"), list) else []
                desires = profile.get("desires") if isinstance(profile.get("desires"), list) else []
                if needs:
                    ui.label("Besoins: " + ", ".join(str(x) for x in needs[:3])).classes("text-xs opacity-70")
                if desires:
                    ui.label("Envies: " + ", ".join(str(x) for x in desires[:3])).classes("text-xs opacity-70")
            else:
                ui.label(selected_npc).classes("text-sm")
                ui.label("Fiche en preparation...").classes("text-xs opacity-70")
    else:
        ui.label("PNJ en discussion: aucun").classes("text-xs opacity-70")
    ui.separator()
    player_corruption = max(0, min(100, _safe_int(getattr(state, "player_corruption_level", 0), 0)))
    ui.label(f"Corruption joueur: {player_corruption}/100").classes("text-xs opacity-75")
    world_state = state.world_state if isinstance(getattr(state, "world_state", None), dict) else {}
    global_tension = max(0, min(100, _safe_int(world_state.get("global_tension"), 0)))
    instability = max(0, min(100, _safe_int(world_state.get("instability_level"), 0)))
    danger_score = max(global_tension, instability)
    if danger_score >= 75:
        danger_label = "extreme"
    elif danger_score >= 50:
        danger_label = "elevee"
    elif danger_score >= 25:
        danger_label = "moderee"
    else:
        danger_label = "faible"
    ui.label(f"Danger zone: {danger_label} | Tension monde {global_tension}/100").classes("text-xs opacity-75")
