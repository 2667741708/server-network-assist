from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend" / "dist" / "frontend" / "browser"
TARGET = ROOT / "src" / "server_network_assist" / "ui"

if not (SOURCE / "index.html").is_file():
    raise SystemExit("Frontend build not found. Run `npm ci` and `npm run build` in frontend first.")
TARGET.mkdir(parents=True, exist_ok=True)
for entry in TARGET.iterdir():
    if entry.is_dir():
        shutil.rmtree(entry)
    else:
        entry.unlink()
for entry in SOURCE.iterdir():
    destination = TARGET / entry.name
    shutil.copytree(entry, destination) if entry.is_dir() else shutil.copy2(entry, destination)
print(f"Copied Angular build to {TARGET}")
