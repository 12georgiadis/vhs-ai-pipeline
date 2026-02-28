# config.py
import os
import platform
from pathlib import Path

# ── API ────────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Phase 1 : pré-analyse rapide (pas cher)
MODEL_PREANALYSIS  = "gemini-2.5-flash"          # Phase 1 : scan rapide, stable, cheap
MODEL_ANALYSIS     = "gemini-3-flash-preview"    # Phase 2 : qualité proche 2.5-pro, output 3x moins cher
MODEL_DEEP_PASS    = "gemini-2.5-pro"            # Phase 3 : deep pass sur les "fort" uniquement (2M ctx + low res)

# Résolution vidéo pour gemini-2.5-pro (phase 3)
# LOW : ~92 tokens/s → 6h de vidéo dans 2M context, coût ÷3
# MEDIUM : ~175 tokens/s (défaut si absent)
# HIGH : ~263 tokens/s (qualité maximale)
USE_LOW_RESOLUTION = True  # appliqué uniquement sur MODEL_DEEP_PASS

# ── Vidéo ──────────────────────────────────────────────────────────────────────
# Durée max d'un chunk avant d'envoyer à Gemini (secondes)
CHUNK_DURATION_S = 50 * 60       # 50 minutes
CHUNK_OVERLAP_S  = 30            # overlap entre chunks pour ne pas couper un plan
PROXY_HEIGHT     = 480           # hauteur proxy (SD VHS natif)
PROXY_FPS        = 1             # 1 image/seconde → réduit les tokens de ×25-30

# ── Retry ──────────────────────────────────────────────────────────────────────
MAX_RETRIES     = 3
RETRY_BACKOFF   = [30, 90, 270]  # secondes entre chaque tentative

# ── Fichiers système ───────────────────────────────────────────────────────────
OUTPUT_DIR_NAME = "vhs_analysis_output"
PROGRESS_FILE   = ".progress.json"
ERRORS_FILE     = "errors.json"
CORPUS_FILE     = "corpus_context.json"

# Extensions vidéo acceptées
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".mpg", ".mpeg", ".m4v", ".mts"}

# ── Font BITC (Burn-In Timecode) cross-platform ────────────────────────────────
def _get_bitc_font() -> str:
    """Retourne un chemin de police valide selon l'OS."""
    system = platform.system()
    candidates = {
        "Darwin": [
            "/System/Library/Fonts/Monaco.ttf",
            "/Library/Fonts/Courier New.ttf",
            "/System/Library/Fonts/Supplemental/Courier New.ttf",
        ],
        "Windows": [
            "C:/Windows/Fonts/cour.ttf",
            "C:/Windows/Fonts/consola.ttf",
        ],
        "Linux": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        ],
    }
    for path in candidates.get(system, []):
        if Path(path).exists():
            return path
    return ""  # ffmpeg utilisera la police par défaut

BITC_FONT = _get_bitc_font()
