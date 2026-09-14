"""Data-preserving team release format. No user directories are writable here."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import zipfile

REPO = 'amzjingfan/infinite-canvas-team'
ASSET = 'infinite-canvas-update.zip'

def allowed(name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name: return False
    parts=name.split('/')
    if any(not p or p in ('.','..') or p.endswith(('.', ' ')) or
           re.search(r'[<>"|?*\x00-\x1f]',p) or
           re.fullmatch(r'(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?',p,re.I) for p in parts): return False
    if any(p.startswith('.') for p in parts): return False
    if len(parts)==1:
        return name in ('VERSION','requirements.txt','LICENSE','README.md') or bool(re.fullmatch(r'[a-z][a-z0-9_]*\.py',name))
    return parts[0] in ('static','workflows') or name in (
        'tools/deploy/serve.py','tools/deploy/start-background.ps1','tools/deploy/install.ps1',
        'tools/team-release/build.py','tools/team-release/publish.ps1','tools/team-release/transition-upgrade.ps1')

def preserve_existing(name):
    return name.startswith(('workflows/','static/runninghub/','static/system-prompts/'))

def safe_target(root,name):
    if not allowed(name): raise ValueError('Protected or invalid release path: '+name)
    root=Path(root).resolve(); target=root/name
    if not target.resolve().is_relative_to(root): raise ValueError('Release path escapes install')
    for parent in [target,*target.parents]:
        if parent==root: break
        if parent.is_symlink() or (parent.exists() and parent.resolve()!=parent.absolute()):
            raise ValueError('Release path contains link')
    return target

def inspect_archive(archive):
    with zipfile.ZipFile(archive) as z:
        entries=z.infolist(); names=[i.filename for i in entries]
        if len(names)!=len(set(n.lower() for n in names)) or len(names)>10000: raise ValueError('Duplicate or excessive entries')
        if sum(i.file_size for i in entries)>150*1024*1024: raise ValueError('Release too large')
        if any((i.external_attr>>16)&0o170000==0o120000 for i in entries): raise ValueError('Symlink in release')
        manifest=json.loads(z.read('release-manifest.json'))
        if manifest.get('format')!=1 or not re.fullmatch(r'\d+(?:\.\d+){2,4}',manifest.get('version','')): raise ValueError('Invalid release version')
        paths=[f['path'] for f in manifest['files']]
        if len(paths)!=len(set(p.lower() for p in paths)) or set(names)!=set(paths)|{'release-manifest.json'}: raise ValueError('Manifest does not match archive')
        if not {'main.py','VERSION','requirements.txt','static/index.html'}.issubset(paths): raise ValueError('Incomplete release')
        files={}
        for entry in manifest['files']:
            name=entry['path']
            if not allowed(name): raise ValueError('Protected release path: '+name)
            value=z.read(name)
            if hashlib.sha256(value).hexdigest()!=entry['sha256']: raise ValueError('Hash mismatch: '+name)
            if name.endswith('.py'): compile(value,name,'exec')
            files[name]=value
        if files['VERSION'].decode().strip()!=manifest['version']: raise ValueError('Version mismatch')
        return manifest,files

def atomic_write(path,content):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.team-update-tmp')
    with temp.open('wb') as f:
        f.write(content);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)

def restore_files(root,backup):
    backup=Path(backup)
    journal=json.loads((backup/'journal.json').read_text(encoding='utf-8'))
    for name,existed in reversed(list(journal.items())):
        target=safe_target(root,name)
        if existed: atomic_write(target,(backup/name).read_bytes())
        else: target.unlink(missing_ok=True)

def apply_files(root,files,backup):
    root=Path(root);backup=Path(backup);backup.mkdir(parents=True,exist_ok=False)
    journal={}
    for name in files:
        target=safe_target(root,name)
        if preserve_existing(name) and target.exists(): continue
        journal[name]=target.exists()
        if target.exists():
            dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,dest)
    atomic_write(backup/'journal.json',json.dumps(journal).encode())
    try:
        for name in journal: atomic_write(safe_target(root,name),files[name])
    except BaseException:
        restore_files(root,backup)
        raise

def busy_record(value):
    if isinstance(value,list): return any(busy_record(v) for v in value)
    if not isinstance(value,dict): return False
    if value.get('running') or value.get('queued') or value.get('pending') or value.get('pendingTasks') or value.get('autodlTaskId') or value.get('vintedOperationId'): return True
    if value.get('status') in ('submitting','running','queued','pending','executing'): return True
    return any(busy_record(v) for v in value.values())
