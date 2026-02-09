import os
import subprocess

def trim_videos(dossier_entree, dossier_sortie, temps_a_supprimer=0.5):
    # Création du dossier de sortie s'il n'existe pas
    if not os.path.exists(dossier_sortie):
        os.makedirs(dossier_sortie)

    fichiers = [f for f in os.listdir(dossier_entree) if f.endswith('.mp4')]
    
    print(f"✂️  Découpe de {len(fichiers)} vidéos en cours...")

    for f in fichiers:
        entree = os.path.join(dossier_entree, f)
        sortie = os.path.join(dossier_sortie, f)

        # Commande FFmpeg :
        # -ss : définit le point de départ
        # -c copy : recopie le flux sans ré-encoder (ultra rapide)
        commande = [
            'ffmpeg', '-y', 
            '-ss', str(temps_a_supprimer), 
            '-i', entree, 
            '-c', 'copy', 
            sortie
        ]

        try:
            subprocess.run(commande, check=True, capture_output=True)
            print(f"✅ Terminé : {f}")
        except subprocess.CalledProcessError as e:
            print(f"❌ Erreur sur {f} : {e}")

# --- CONFIGURATION ---
dossier_source = "./mes_videos_brutes"
dossier_propre = "./mes_videos_coupees"

trim_videos(dossier_source, dossier_propre)