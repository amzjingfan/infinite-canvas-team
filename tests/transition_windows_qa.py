"""Manual isolated Windows check against the earlier private bundle's CODE only."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from team_update import allowed,atomic_write
import team_update_worker as worker
from team_transition import transition

base=Path(__file__).resolve().parents[1]
root=base/'dist/team-update-qa/Infinite-Canvas'
old=base/'dist/Infinite-Canvas-Windows-x64-PRIVATE-20260914-120358'
archive=base/'dist/public-final-2026.09.14.2/infinite-canvas-update.zip'
task={'TaskName':'Infinite Canvas Public Transition QA','TaskPath':'\\'}
if not (root/'python/python.exe').is_file(): raise SystemExit('QA runtime missing')
for f in old.rglob('*'):
    name=f.relative_to(old).as_posix()
    if f.is_file() and allowed(name): atomic_write(root/name,f.read_bytes())
fixtures={'data/canvases/sentinel.json':b'{"title":"keep","nodes":[]}',
          'API/.env':b'# fixture only\nAPI_PROVIDER_QA_KEY=not-a-real-key\n',
          'assets/input/keep.bin':b'pixels remain unchanged',
          'static/system-prompts/qa.md':b'local prompt must remain'}
for name,value in fixtures.items(): atomic_write(root/name,value)
shell='C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'
arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+str(root/'tools/deploy/start-background.ps1')+'" -Port 3098'
worker.ps("$ErrorActionPreference='Stop'; $a=New-ScheduledTaskAction -Execute "+worker.quote(shell)+" -Argument "+worker.quote(arguments)+" -WorkingDirectory "+worker.quote(root)+"; Register-ScheduledTask -TaskName "+worker.quote(task['TaskName'])+" -Action $a -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero)) | Out-Null")
try:
    worker.task_action(task,'Start')
    assert worker.healthy(3098,None), 'Old bundle must start'
    bad=archive.parent/'qa-bad-startup.zip'
    with zipfile.ZipFile(archive) as z: contents={name:z.read(name) for name in z.namelist()}
    manifest=json.loads(contents['release-manifest.json'])
    contents['main.py']+=b'\nraise RuntimeError("intentional QA startup failure")\n'
    for item in manifest['files']: item['sha256']=hashlib.sha256(contents[item['path']]).hexdigest()
    contents['release-manifest.json']=json.dumps(manifest).encode()
    with zipfile.ZipFile(bad,'w') as z:
        for name,value in contents.items(): z.writestr(name,value)
    state=root/'data/team-update/status.json'
    atomic_write(state,json.dumps({'operation':'public-legacy-rollback','phase':'prepared','legacy':True}).encode())
    worker.run(root,bad,3098)
    assert json.loads(state.read_text(encoding='utf-8'))['phase']=='rolled_back'
    assert (root/'main.py').read_bytes()==(old/'main.py').read_bytes()
    print('REAL_LEGACY_ROLLBACK_OK',flush=True)
    bad.unlink()
    transition(root,archive,3098)
    for name,value in fixtures.items(): assert (root/name).read_bytes()==value,name
    assert worker.healthy(3098,'2026.09.14.2',2)
    print('REAL_LEGACY_TRANSITION_AND_DATA_PRESERVATION_OK',flush=True)
finally:
    worker.task_action(task,'Stop')
    worker.ps('Unregister-ScheduledTask -TaskName '+worker.quote(task['TaskName'])+' -Confirm:$false')
