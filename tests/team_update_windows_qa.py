"""Explicit isolated Windows smoke test, never implicitly targets production."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from team_update import atomic_write
from team_update_worker import run,healthy
parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--archive',required=True);args=parser.parse_args()
root=Path(args.root).resolve()
if 'team-update-qa' not in root.parts: raise SystemExit('Only isolated QA install allowed')
archive=Path(args.archive)
fixtures={'data/canvases/sentinel.json':b'{"title":"keep","nodes":[]}',
          'API/.env':b'# fixture only\nAPI_PROVIDER_QA_KEY=not-a-real-key\n',
          'assets/input/keep.bin':b'pixels remain unchanged',
          'static/system-prompts/qa.md':b'local prompt must remain'}
for name,value in fixtures.items(): atomic_write(root/name,value)
atomic_write(root/'VERSION',b'2026.09.13.1')
atomic_write(root/'main.py',(root/'main.py').read_bytes()+b'\n# previous QA version\n')
state=root/'data/team-update/status.json'
def begin(operation): atomic_write(state,json.dumps({'operation':operation,'phase':'prepared'}).encode())
def verify():
    for name,value in fixtures.items(): assert (root/name).read_bytes()==value,name
begin('windows-success')
run(root,archive,3098)
assert json.loads(state.read_text(encoding='utf-8'))['phase']=='completed'
assert healthy(3098,'2026.09.14.1',2)
verify()
print('REAL_WINDOWS_UPDATE_PRESERVES_DATA_OK',flush=True)
bad=archive.parent/'qa-bad-startup.zip'
with zipfile.ZipFile(archive) as z: contents={name:z.read(name) for name in z.namelist()}
manifest=json.loads(contents['release-manifest.json']);manifest['version']='2026.09.14.2'
contents['VERSION']=b'2026.09.14.2';contents['main.py']+=b'\nraise RuntimeError("intentional QA startup failure")\n'
for item in manifest['files']: item['sha256']=hashlib.sha256(contents[item['path']]).hexdigest()
contents['release-manifest.json']=json.dumps(manifest).encode()
with zipfile.ZipFile(bad,'w') as z:
    for name,value in contents.items(): z.writestr(name,value)
begin('windows-rollback')
run(root,bad,3098)
assert json.loads(state.read_text(encoding='utf-8'))['phase']=='rolled_back'
assert healthy(3098,'2026.09.14.1',2)
verify();bad.unlink()
print('REAL_WINDOWS_STARTUP_FAILURE_ROLLBACK_OK',flush=True)
