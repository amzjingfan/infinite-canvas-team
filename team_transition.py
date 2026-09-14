"""One-time migration from the private Windows bundle; run from an external stage."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parent))
from team_update import inspect_archive, atomic_write, busy_record
from team_update_worker import installation_task, ps, quote, run

def check_saved_tasks(root):
    for f in (Path(root)/'data').rglob('*.json'):
        if 'team-update' in f.relative_to(root).parts: continue
        if busy_record(json.loads(f.read_text(encoding='utf-8-sig'))):
            raise RuntimeError('Saved data contains unfinished tasks; finish/retrieve them first: '+str(f.relative_to(root)))

def transition(root,archive,port):
    root=Path(root).resolve()
    if not (root/'main.py').is_file() or not (root/'python/python.exe').is_file():
        raise RuntimeError('Not a supported existing Windows installation')
    statefile=root/'data/team-update/status.json'
    if statefile.exists() and json.loads(statefile.read_text(encoding='utf-8')).get('phase') in ('prepared','stopping','applying','restarting','recovery_required'):
        raise RuntimeError('Previous update requires recovery; do not overwrite its journal')
    manifest,files=inspect_archive(archive)
    if files['requirements.txt'].strip()!=(root/'requirements.txt').read_bytes().strip():
        raise RuntimeError('Installed dependencies differ; original installation has not been changed')
    installation_task(str(root))
    # Verify that this port belongs to the chosen installation, never another app.
    code="$ErrorActionPreference='Stop'; $c=Get-NetTCPConnection -State Listen -LocalPort "+str(port)+" | Select-Object -First 1; $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$c.OwningProcess); if($p.ExecutablePath -ne "+quote(str(root/'python/python.exe'))+"){throw 'Port belongs to another installation'}"
    ps(code)
    def idle():
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/queue_status?client_id=team-transition',timeout=5) as response:
            queue=json.load(response)
        if queue.get('total')!=0: raise RuntimeError('Generation queue is not empty; save and finish tasks first')
        check_saved_tasks(root)
    idle()
    preview=Path(archive).resolve().parent/'preview'
    for name,value in files.items(): atomic_write(preview/name,value)
    result=subprocess.run([str(root/'python/python.exe'),'-B','-c','import sys; sys.path.insert(0,'+repr(str(preview))+'); import main'],cwd=preview,capture_output=True,timeout=60,creationflags=0x08000000)
    if result.returncode: raise RuntimeError('New application import failed; original service was not stopped')
    idle()
    atomic_write(statefile,json.dumps({'operation':uuid.uuid4().hex,'phase':'prepared','legacy':True,'version':manifest['version'],'updated_at':time.time()}).encode())
    run(root,archive,port)
    state=json.loads(statefile.read_text(encoding='utf-8'))
    print(json.dumps({'phase':state['phase'],'version':manifest['version'],'backup_directory':str(statefile.parent)},ensure_ascii=True))
    if state['phase']!='completed': raise RuntimeError('Transition did not complete: '+state['phase'])

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--archive',required=True);parser.add_argument('--port',type=int,default=3000)
    args=parser.parse_args();transition(args.root,args.archive,args.port)
