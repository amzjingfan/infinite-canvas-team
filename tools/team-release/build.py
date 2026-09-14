"""Build clean source, update ZIP and first-install ZIP without Git history or user data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from team_update import allowed,ASSET

def build(root,out,full=False):
    root=Path(root).resolve();out=Path(out).resolve()
    if out.exists(): raise ValueError('Output already exists; choose a new release directory')
    out.mkdir(parents=True);source=out/'source';source.mkdir()
    paths=[p for p in root.iterdir() if p.is_file() and allowed(p.name)]
    for prefix in ('static','workflows','tools/deploy','tools/team-release'):
        paths += [p for p in (root/prefix).rglob('*') if p.is_file() and not p.is_symlink() and allowed(p.relative_to(root).as_posix())]
    # Export from filesystem, never from git archive (the old history tracked secrets).
    secrets=[]
    env=root/'API/.env'
    if env.exists():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                name,value=line.split('=',1);value=value.strip().strip('"').strip("'")
                if any(t in name.upper() for t in ('KEY','TOKEN','SECRET','PASSWORD')) and len(value)>=8: secrets.append(value.encode())
    local=root/'API/autodl.local.json'
    if local.exists():
        value=json.loads(local.read_text()).get('api_key','')
        if len(value)>=8: secrets.append(value.encode())
    import re
    manifest={'format':1,'version':(root/'VERSION').read_text().strip(),'files':[]}
    for p in sorted(set(paths)):
        name=p.relative_to(root).as_posix();content=p.read_bytes()
        if any(s in content for s in secrets) or re.search(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{24,})',content):
            raise ValueError('Credential detected in '+name)
        target=source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        manifest['files'].append({'path':name,'sha256':hashlib.sha256(content).hexdigest()})
    archive=out/ASSET
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for item in manifest['files']: z.write(source/item['path'],item['path'])
        z.writestr('release-manifest.json',json.dumps(manifest))
    (out/(ASSET+'.sha256')).write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+ASSET)
    # Source-only repository: tests and maintainer guide are not runtime payloads.
    shutil.copy2(root/'.gitignore',source/'.gitignore')
    shutil.copytree(root/'tests',source/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (source/'docs').mkdir()
    for name in ('team-releases.md','transition-upgrade.md'):
        shutil.copy2(root/'docs'/name,source/'docs'/name)
    shutil.copy2(root/'tools/team-release/transition-upgrade.ps1',out/'transition-upgrade.ps1')
    shutil.copy2(root/'docs/transition-upgrade.md',out/'transition-upgrade.md')
    for p in source.rglob('*'):
        if not p.is_file(): continue
        content=p.read_bytes()
        if any(s in content for s in secrets) or re.search(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{24,})',content):
            raise ValueError('Credential detected in source export: '+str(p.relative_to(source)))
    if full:
        install=out/'install';shutil.copytree(source,install,ignore=shutil.ignore_patterns('tests','.gitignore'))
        shutil.copytree(root/'python',install/'python',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        skill=Path.home()/'.agents/skills/gpt-image-2-style-library'
        if skill.exists(): shutil.copytree(skill,install/'bundled-skills/gpt-image-2-style-library',ignore=shutil.ignore_patterns('__pycache__','.git','*.pyc'))
        entries=[{'path':p.relative_to(install).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                  'mutable':p.suffix=='.html' or p.relative_to(install).as_posix().startswith('static/runninghub/')}
                 for p in install.rglob('*') if p.is_file()]
        (install/'package-manifest.json').write_text(json.dumps({'format':1,'private_keys':False,'files':entries}))
        fullzip=out/'infinite-canvas-windows-x64.zip'
        with zipfile.ZipFile(fullzip,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for p in install.rglob('*'):
                if p.is_file(): z.write(p,'Infinite-Canvas/'+p.relative_to(install).as_posix())
        (out/(fullzip.name+'.sha256')).write_text(hashlib.sha256(fullzip.read_bytes()).hexdigest()+'  '+fullzip.name)
    return {'source':str(source),'update':str(archive),'files':len(manifest['files']),'version':manifest['version']}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);parser.add_argument('--full',action='store_true')
    args=parser.parse_args();print(json.dumps(build(Path(__file__).resolve().parents[2],args.out,args.full)))
