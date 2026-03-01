# export_fcpxml.py
import xml.etree.ElementTree as ET
from pathlib import Path


# Mapping type_plan / marker_type → élément FCPXML
# chapter-marker = orange (structure narrative)  → rupture, revelation
# marker completed="0" = rouge/to-do (plan fort) → fort
# marker standard = bleu                          → standard
# marker completed="1" = vert (artefact)          → glitch

def _tc_to_rational(tc_str: str, fps: float = 25.0) -> str:
    """
    Convertit HH:MM:SS en time rationnel FCPXML.
    Ex : "00:02:14" → "3350/25s"
    """
    parts = tc_str.strip().split(":")
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    elif len(parts) == 2:
        h, m, s = 0, int(parts[0]), int(parts[1])
    else:
        return "0s"
    total_s = h * 3600 + m * 60 + s
    # Représentation rationnelle standard FCPXML
    frames = int(total_s * fps)
    return f"{frames}/{int(fps)}s" if frames > 0 else "0s"


def _duration_rational(tc_start: str, tc_end: str, fps: float = 25.0) -> str:
    """Durée entre deux timecodes en format rationnel."""
    def to_s(tc):
        parts = tc.strip().split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0
    dur = max(1, to_s(tc_end) - to_s(tc_start))
    frames = int(dur * fps)
    return f"{frames}/{int(fps)}s"


def _make_marker(parent: ET.Element, segment: dict, fps: float = 25.0):
    """Ajoute un marker ou chapter-marker au parent XML selon le type du segment."""
    tc_start  = segment.get("tc_start", "00:00:00")
    tc_end    = segment.get("tc_end",   "00:00:01")
    marker_type = segment.get("marker_type", "standard")
    interet     = segment.get("interet_film", "moyen")
    type_plan   = segment.get("type_plan", "banal")

    start_r    = _tc_to_rational(tc_start, fps)
    duration_r = _duration_rational(tc_start, tc_end, fps)

    # ── Titre : signal analytique + ancrage mémorable (couleur = déjà le type)
    signal = segment.get("signal_pertinent", "")
    quote  = segment.get("transcription_joshua", "")
    if quote:
        anchor = f'· "{quote[:30]}"'
    elif segment.get("note_montage"):
        anchor = f'· {segment["note_montage"][:30]}'
    else:
        anchor = ""
    title = f"{signal} {anchor}".strip() or type_plan

    # ── Note : du plus au moins actionnable pour le monteur
    parts = []
    if segment.get("interpretation_monteur"):
        parts.append(segment["interpretation_monteur"])
    if segment.get("note_montage"):
        parts.append(f"→ {segment['note_montage']}")
    if quote:
        parts.append(f'🗣 "{quote}"')
    if segment.get("comportement_joshua"):
        parts.append(segment["comportement_joshua"])
    if segment.get("signaux_langage"):
        parts.append(f"[langage] {segment['signaux_langage']}")
    if segment.get("blind_ce_qui_me_retient"):
        parts.append(f"[aveugle] {segment['blind_ce_qui_me_retient']}")
    note = "\n\n".join(parts)

    attribs = {
        "start":    start_r,
        "duration": duration_r,
        "value":    title[:64],  # FCP limite les noms de markers
        "note":     note[:512],
    }

    # Choix du type d'élément XML selon l'intérêt
    if marker_type == "glitch" or type_plan == "glitch":
        # Marker complété = vert dans FCP
        el = ET.SubElement(parent, "marker", {**attribs, "completed": "1"})
    elif interet == "fort" or marker_type == "todo":
        # To-do = rouge dans FCP → plans à absolument conserver
        el = ET.SubElement(parent, "marker", {**attribs, "completed": "0"})
    elif type_plan in ("revelation", "rupture") or marker_type == "chapter":
        # Chapter marker = orange → moments de structure narrative
        ET.SubElement(parent, "chapter-marker", attribs)
    else:
        # Standard = bleu
        ET.SubElement(parent, "marker", attribs)


def generate_fcpxml(
    video_path: Path,
    analysis: dict,
    output_path: Path,
    fps: float = 25.0,
    duration_s: float = None,
):
    """
    Génère un fichier FCPXML 1.11 avec tous les markers de l'analyse.
    Crée une séquence complète importable dans FCP 12.

    video_path   : chemin absolu du fichier vidéo ORIGINAL (pas le proxy)
    analysis     : dict retourné par analyze.merge_chunk_results()
    output_path  : chemin du .fcpxml à écrire
    fps          : framerate pour la conversion de timecodes (25 par défaut)
    duration_s   : durée totale de la vidéo (auto-détectée si None)
    """
    segments = analysis.get("segments", [])
    video_name = video_path.stem

    # Durée totale : utilise la dernière fin de segment si non fournie
    if duration_s is None and segments:
        last_tc = segments[-1].get("tc_end", "00:00:00")
        parts = last_tc.split(":")
        duration_s = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + 60
    duration_s = duration_s or 3600

    duration_r = f"{int(duration_s * fps)}/{int(fps)}s"
    fmt_name   = f"FFVideoFormat480p{int(fps)}"

    # ── Racine ─────────────────────────────────────────────────────────────────
    root = ET.Element("fcpxml", {"version": "1.11"})

    # ── Resources ──────────────────────────────────────────────────────────────
    resources = ET.SubElement(root, "resources")

    ET.SubElement(resources, "format", {
        "id":            "r1",
        "name":          fmt_name,
        "frameDuration": f"1/{int(fps)}s",
        "width":         "854",
        "height":        "480",
    })

    asset = ET.SubElement(resources, "asset", {
        "id":             "r2",
        "name":           video_name,
        "start":          "0s",
        "duration":       duration_r,
        "hasVideo":       "1",
        "hasAudio":       "1",
        "audioSources":   "1",
        "audioChannels":  "2",
    })
    ET.SubElement(asset, "media-rep", {
        "kind": "original-media",
        "src":  video_path.as_uri(),
    })

    # ── Library → Event → Project → Sequence ───────────────────────────────────
    library = ET.SubElement(root, "library")
    event   = ET.SubElement(library, "event", {"name": "VHS Goldberg Analysis"})
    project = ET.SubElement(event, "project", {"name": f"{video_name} — Analysis"})
    sequence = ET.SubElement(project, "sequence", {
        "duration": duration_r,
        "format":   "r1",
        "tcStart":  "0s",
    })
    spine = ET.SubElement(sequence, "spine")

    # Clip principal avec les markers
    clip = ET.SubElement(spine, "asset-clip", {
        "name":     video_name,
        "ref":      "r2",
        "offset":   "0s",
        "duration": duration_r,
        "start":    "0s",
        "format":   "r1",
    })

    # ── Markers ────────────────────────────────────────────────────────────────
    marker_count = 0
    for seg in segments:
        if seg.get("tc_start"):
            _make_marker(clip, seg, fps)
            marker_count += 1

    # ── Écriture fichier ───────────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")

    with open(output_path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(b'<!DOCTYPE fcpxml>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)

    print(f"  [fcpxml] {output_path.name} — {marker_count} markers")
    return output_path
