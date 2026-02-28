# progress.py
import json
import time
from datetime import datetime
from pathlib import Path

import config


class ProgressTracker:
    """
    Gère le checkpoint (.progress.json) et le log d'erreurs.
    Permet de reprendre un batch interrompu sans tout relancer.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.progress_file = output_dir / config.PROGRESS_FILE
        self.errors_file   = output_dir / config.ERRORS_FILE
        self._data = self._load()

    def _load(self) -> dict:
        if self.progress_file.exists():
            return json.loads(self.progress_file.read_text())
        return {
            "started_at": datetime.now().isoformat(),
            "processed": [],   # noms de fichiers traités avec succès
            "failed": [],      # {file, error, attempts}
            "pending": [],
        }

    def _save(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.progress_file.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))

    def is_done(self, video_name: str) -> bool:
        return video_name in self._data["processed"]

    def mark_done(self, video_name: str):
        if video_name not in self._data["processed"]:
            self._data["processed"].append(video_name)
        self._data["failed"] = [f for f in self._data["failed"] if f["file"] != video_name]
        self._save()

    def mark_failed(self, video_name: str, error: str, attempts: int):
        existing = next((f for f in self._data["failed"] if f["file"] == video_name), None)
        if existing:
            existing["error"] = error
            existing["attempts"] = attempts
        else:
            self._data["failed"].append({"file": video_name, "error": error, "attempts": attempts})
        self._save()

    @property
    def failed_files(self) -> list[str]:
        return [f["file"] for f in self._data["failed"]]

    def summary(self) -> str:
        done = len(self._data["processed"])
        failed = len(self._data["failed"])
        return f"Traités : {done} | Échecs : {failed}"


def with_retry(fn, video_name: str, tracker: ProgressTracker):
    """
    Exécute fn() avec retry automatique (3 tentatives, backoff exponentiel).
    Enregistre les échecs dans le tracker.
    Retourne le résultat ou None si toutes les tentatives échouent.
    """
    last_error = ""
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            result = fn()
            tracker.mark_done(video_name)
            return result
        except Exception as e:
            last_error = str(e)
            print(f"  [retry] Tentative {attempt}/{config.MAX_RETRIES} échouée : {last_error[:120]}")
            if attempt < config.MAX_RETRIES:
                wait = config.RETRY_BACKOFF[attempt - 1]
                print(f"  [retry] Attente {wait}s ...")
                time.sleep(wait)

    print(f"  [SKIP] {video_name} — abandon après {config.MAX_RETRIES} tentatives")
    tracker.mark_failed(video_name, last_error, config.MAX_RETRIES)
    return None
