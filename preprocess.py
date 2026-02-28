# preprocess.py
import json
import platform
import shutil
import subprocess
from pathlib import Path

from config import BITC_FONT, CHUNK_DURATION_S, CHUNK_OVERLAP_S, PROXY_FPS, PROXY_HEIGHT


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Lance une commande ffmpeg/ffprobe et lève une exception si échec."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error:\n{result.stderr[-500:]}")
    return result


def get_duration(video_path: Path) -> float:
    """Retourne la durée en secondes via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path),
    ]
    result = _run(cmd)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def _bitc_filter(start_offset_s: int = 0) -> str:
    """Construit le filtre drawtext pour le Burn-In Timecode."""
    # pts_start_time = offset en secondes pour afficher le TC absolu
    pts_expr = f"pts+{start_offset_s}"
    font_part = f"fontfile='{BITC_FONT}':" if BITC_FONT else ""
    return (
        f"drawtext={font_part}"
        f"text='%{{pts\\:hms\\:{start_offset_s}}}':fontsize=20:"
        f"fontcolor=white:x=10:y=10:box=1:boxcolor=black@0.6:boxborderw=4"
    )


def create_proxy(video_path: Path, output_dir: Path, start_offset_s: int = 0) -> Path:
    """
    Crée un proxy 480p à 1fps avec timecode brûlé dans l'image.
    L'audio est conservé à qualité normale (Gemini l'analyse aussi).
    Retourne le chemin du proxy généré.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_offset{start_offset_s}" if start_offset_s > 0 else ""
    proxy_path = output_dir / f"{video_path.stem}{suffix}_proxy.mp4"

    if proxy_path.exists():
        print(f"  [proxy] Déjà existant : {proxy_path.name}")
        return proxy_path

    vf = f"fps={PROXY_FPS},scale=-2:{PROXY_HEIGHT},{_bitc_filter(start_offset_s)}"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "28", "-preset", "fast",
        "-c:a", "aac", "-b:a", "96k",   # audio conservé pour analyse Gemini
        str(proxy_path),
    ]
    print(f"  [proxy] Création : {proxy_path.name} ...")
    _run(cmd)
    return proxy_path


def chunk_video(video_path: Path, output_dir: Path, duration_s: float) -> list[dict]:
    """
    Découpe une vidéo longue en segments avec overlap.
    Retourne une liste de dicts : {path, start_s, end_s}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    start = 0

    while start < duration_s:
        end = min(start + CHUNK_DURATION_S, duration_s)
        idx = len(chunks) + 1
        chunk_path = output_dir / f"{video_path.stem}_chunk{idx:03d}.mp4"
        meta_path  = output_dir / f"{video_path.stem}_chunk{idx:03d}.meta.json"

        if not chunk_path.exists():
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", str(video_path),
                "-t", str(end - start),
                "-c", "copy",           # copie rapide, pas de re-encoding
                str(chunk_path),
            ]
            print(f"  [chunk] Segment {idx} ({_fmt_tc(start)}→{_fmt_tc(end)}) ...")
            _run(cmd)

        # Sauvegarde des métadonnées de chunk pour reconstruire les TC absolus
        meta = {"start_s": start, "end_s": end, "chunk_index": idx}
        meta_path.write_text(json.dumps(meta))

        chunks.append({"path": chunk_path, "start_s": start, "end_s": end})
        start = end - CHUNK_OVERLAP_S  # overlap de 30s

    return chunks


def prepare_video(video_path: Path, work_dir: Path) -> list[dict]:
    """
    Pipeline complet pour une vidéo :
    1. Mesure la durée
    2. Si > CHUNK_DURATION_S : découpe en chunks puis crée un proxy par chunk
    3. Sinon : crée directement un proxy

    Retourne une liste de segments à envoyer à Gemini :
    [{path: proxy_path, start_s: float, end_s: float}]
    """
    print(f"\n[preprocess] {video_path.name}")
    proxy_dir = work_dir / "proxies"

    duration = get_duration(video_path)
    print(f"  Durée : {_fmt_tc(duration)} ({duration:.0f}s)")

    if duration <= CHUNK_DURATION_S:
        proxy = create_proxy(video_path, proxy_dir)
        return [{"path": proxy, "start_s": 0, "end_s": duration}]

    # Vidéo longue → chunk d'abord, proxy ensuite
    print(f"  Vidéo > {CHUNK_DURATION_S//60} min → chunking")
    chunks_dir = work_dir / "chunks"
    chunks = chunk_video(video_path, chunks_dir, duration)

    segments = []
    for chunk in chunks:
        proxy = create_proxy(chunk["path"], proxy_dir, start_offset_s=int(chunk["start_s"]))
        segments.append({"path": proxy, "start_s": chunk["start_s"], "end_s": chunk["end_s"]})

    return segments


def extract_sample(video_path: Path, work_dir: Path, start_s: int = 120, duration_s: int = 180) -> Path:
    """
    Extrait un échantillon de 3 minutes pour la pré-analyse (Phase 1).
    Commence à 2 minutes pour éviter les génériques de début.
    """
    sample_dir = work_dir / "samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_path = sample_dir / f"{video_path.stem}_sample.mp4"

    if sample_path.exists():
        return sample_path

    # Durée réelle (la vidéo peut être plus courte que start_s + duration_s)
    total = get_duration(video_path)
    actual_start = min(start_s, max(0, total - duration_s))
    actual_dur = min(duration_s, total - actual_start)

    vf = f"fps={PROXY_FPS},scale=-2:{PROXY_HEIGHT}"
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(actual_start),
        "-i", str(video_path),
        "-t", str(actual_dur),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k",
        str(sample_path),
    ]
    print(f"  [sample] Extraction ({_fmt_tc(actual_start)}+{actual_dur:.0f}s) ...")
    _run(cmd)
    return sample_path


def _fmt_tc(seconds: float) -> str:
    """Formate des secondes en HH:MM:SS."""
    s = int(seconds)
    h, remainder = divmod(s, 3600)
    m, sec = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"
