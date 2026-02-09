from pathlib import Path
import random

from moviepy import VideoFileClip, ImageClip, concatenate_videoclips
from moviepy.video.fx import CrossFadeIn


def freeze_bridge_join(
    src_dir="./mes_videos_brutes",
    out_file="final.mp4",
    nb=10,
    target_h=688,
    fade_s=0.9,       # durée du fondu entre images/vidéos
    hold_s=0.4,      # durée de "pause" sur la dernière/première frame
    fps=24,
    seed=None,
):
    rng = random.Random(seed)

    src = Path(src_dir)
    files = [p for p in src.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"]
    if len(files) < 2:
        raise ValueError("Il faut au moins 2 vidéos .mp4 dans le dossier.")

    order = rng.sample(files, k=min(nb, len(files)))
    rng.shuffle(order)
    print("🎲 Ordre :", [p.name for p in order])

    built = []
    opened = []  # pour fermer proprement les VideoFileClip

    try:
        # On ouvre le premier
        prev = VideoFileClip(str(order[0])).resized(height=target_h).with_fps(fps)
        opened.append(prev)
        built.append(prev)

        for p in order[1:]:
            nxt = VideoFileClip(str(p)).resized(height=target_h).with_fps(fps)
            opened.append(nxt)

            # --- freeze dernière frame de prev ---
            t_last = max(0, prev.duration - 1 / fps)
            last_frame = prev.get_frame(t_last)
            last_freeze = ImageClip(last_frame).with_duration(hold_s).with_fps(fps)

            # --- freeze première frame de nxt ---
            first_frame = nxt.get_frame(0)
            first_freeze = ImageClip(first_frame).with_duration(hold_s).with_fps(fps)

            # On rend les transitions jolies:
            # last_freeze -> first_freeze (crossfade)
            first_freeze = first_freeze.with_effects([CrossFadeIn(fade_s)])

            # first_freeze -> nxt (crossfade vers la vidéo en mouvement)
            nxt_fadein = nxt.with_effects([CrossFadeIn(fade_s)])

            built.extend([last_freeze, first_freeze, nxt_fadein])

            prev = nxt

        # Concat avec chevauchement: padding négatif = crossfade réel
        final = concatenate_videoclips(
            built,
            method="compose",
            padding=-fade_s
        )

        final.write_videofile(
            out_file,
            codec="libx264",
            fps=fps,
            audio=False,
            threads=8,
            preset="slow",
            ffmpeg_params=["-tune", "stillimage"],
        )
        print(f"✨ OK : {out_file}")

    finally:
        # fermer uniquement les VideoFileClip (ImageClip n’a pas besoin)
        for c in opened:
            try:
                c.close()
            except Exception:
                pass


if __name__ == "__main__":
    freeze_bridge_join(
        src_dir="./mes_videos_brutes",
        out_file="cycle_freeze_bridge.mp4",
        nb=10,
        fade_s=0.9,
        hold_s=0.4,
        fps=24,
    )
