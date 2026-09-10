"""Prepare one deployable static tree plus the separately reviewed cloud installer."""
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/project-deployment'
OUT.mkdir(parents=True,exist_ok=True)
site=OUT/'site'
if site.exists():
    if site.resolve() != (ROOT/'artifacts/project-deployment/site').absolute() or site.is_symlink():
        raise ValueError('Unexpected staging directory')
    shutil.rmtree(site)
shutil.copytree(ROOT/'docs/projects',site)
shutil.copyfile(ROOT/'scripts/deploy_project_hub.py',OUT/'deploy_project_hub.py')
print('Prepared static project deployment at '+str(OUT))
