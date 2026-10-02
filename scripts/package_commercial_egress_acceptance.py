"""Package Python-only source for isolated server acceptance; no credentials."""
from pathlib import Path
import zipfile

root=Path(__file__).resolve().parents[1]
destination=root/'dist'/'commercial-egress-acceptance.zip'
destination.parent.mkdir(exist_ok=True)
files=list((root/'src'/'server_network_assist').rglob('*.py'))
files.extend(path for path in (root/'src'/'server_network_assist'/'ui').rglob('*') if path.is_file())
files.append(root/'scripts'/'verify_commercial_egress_netns.py')
with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED) as archive:
    for path in files:archive.write(path,path.relative_to(root).as_posix())
print(destination)
