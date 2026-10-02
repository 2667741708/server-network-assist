"""Incrementally package navigation changes without deleting other UI assets."""
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'frontend/dist/frontend/browser'
TARGET = ROOT / 'src/server_network_assist/ui'
ADMIN = ROOT / 'src/server_network_assist/subscription_admin_ui'


def main():
    if not (SOURCE / 'index.html').is_file():
        raise SystemExit('Run the frontend production build first.')
    TARGET.mkdir(parents=True, exist_ok=True)
    for source in SOURCE.rglob('*'):
        if source.is_file():
            destination = TARGET / source.relative_to(SOURCE)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    for name in ['subscriptions.html','subscriptions.js','dashboard.js',
                 'smooth-navigation.js','smooth-navigation.css']:
        shutil.copy2(ADMIN / name, TARGET / name)
    print('Packaged Angular and subscription navigation assets; existing files retained.')


if __name__ == '__main__':
    main()
