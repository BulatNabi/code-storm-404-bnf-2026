import os
import re
import time
import shutil
from pathlib import Path

BASE_DIR = Path("data/jira")

def get_issue_dir(issue_key: str) -> Path:
    path = BASE_DIR / issue_key
    path.mkdir(parents=True, exist_ok=True)
    return path

def is_cache_valid(issue_key: str) -> bool:
    dir_path = get_issue_dir(issue_key)
    return dir_path.exists() and any(dir_path.iterdir())

def save_file_locally(issue_key: str, original_filename: str, content: bytes) -> str:
    safe_name = re.sub(r'[^\w\-.]', '_', original_filename)
    safe_name = re.sub(r'_+', '_', safe_name).strip('_ ')
    safe_name = safe_name or "attachment"
    name_part, ext = os.path.splitext(safe_name)
    final_filename = f"{name_part}_{issue_key}{ext}"
    path = get_issue_dir(issue_key) / final_filename
    path.write_bytes(content)
    return str(path)

def cleanup_old_files(days: int = 7) -> int:
    if not BASE_DIR.exists():
        return 0
    cutoff_time = time.time() - (days * 24 * 60 * 60)
    removed_count = 0
    for issue_dir in BASE_DIR.iterdir():
        if issue_dir.is_dir():
            try:
                files = [f for f in issue_dir.iterdir() if f.is_file()]
                if not files:
                    shutil.rmtree(issue_dir, ignore_errors=True)
                    removed_count += 1
                    continue
                latest_mod = max(f.stat().st_mtime for f in files)
                if latest_mod < cutoff_time:
                    shutil.rmtree(issue_dir)
                    removed_count += 1
            except Exception:
                shutil.rmtree(issue_dir, ignore_errors=True)
                removed_count += 1
    return removed_count