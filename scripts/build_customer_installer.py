"""Build offline customer installer; does not execute it."""
import argparse
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
    '--windowed', '--name', 'Install', '--paths', str(root / 'src'),
    '--distpath', str(args.output.resolve()), '--workpath', str(root / 'artifacts/installer-build'),
    '--specpath', str(root / 'artifacts'),
    '--icon', str(root / 'src/server_network_assist/desktop_ui/icon.ico'),
    '--add-data', str(root / 'scripts/customer_install_windows.ps1') + ';.',
    '--add-data', str(root / 'scripts/customer_dependencies_windows.ps1') + ';.',
    str(root / 'scripts/customer_installer_entry.py')], cwd=root, check=True)
