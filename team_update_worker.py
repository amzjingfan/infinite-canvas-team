"""Detached updater: stop the exact scheduled task, transact code, health-check, recover."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parent))
from team_update import apply_files, restore_files, inspect_archive, atomic_write

def ps(code):
    encoded=base64.b64encode(code.encode('utf-16-le')).decode()
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
        capture_output=True,timeout=40,creationflags=0x08000000)
    if result.returncode: raise RuntimeError('Windows task operation failed; check task permissions')
    return result.stdout.decode('utf-8-sig',errors='replace').strip()

def quote(s): return "'"+str(s).replace("'","''")+"'"

def installation_task(root):
    if os.name!='nt': raise RuntimeError('Automatic updates currently require Windows scheduled-task installation')
    code="$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
    code+="$tasks=@(Get-ScheduledTask | Where-Object { @($_.Actions | Where-Object { $_.WorkingDirectory -eq "+quote(root)+" -and $_.Arguments.Contains("+quote(root)+") }).Count -gt 0 }); "
    code+="if($tasks.Count -ne 1){throw 'Expected exactly one task'}; $tasks[0] | Select-Object TaskName,TaskPath | ConvertTo-Json -Compress"
    return json.loads(ps(code))

def task_action(task,action):
    ps("$ErrorActionPreference='Stop'; "+action+'-ScheduledTask -TaskName '+quote(task['TaskName'])+' -TaskPath '+quote(task['TaskPath']))

def healthy(port,version,attempts=40):
    for _ in range(attempts):
        try:
            route='/' if version is None else '/api/team-update/health'
            with urllib.request.urlopen(f'http://127.0.0.1:{port}{route}',timeout=2) as r:
                if (version is None and r.status==200) or (version is not None and json.load(r).get('version')==version): return True
        except Exception: pass
        time.sleep(.5)
    return False

def run(root,archive,port):
    root=Path(root).resolve(); statefile=root/'data/team-update/status.json'
    state=json.loads(statefile.read_text(encoding='utf-8'))
    def status(phase,message):
        state.update(phase=phase,message=message,updated_at=time.time())
        atomic_write(statefile,json.dumps(state,ensure_ascii=False).encode())
    backup=root/'data/team-update'/('backup-'+state['operation'])
    task=None;changed=False;stopped=False
    previous=None if state.get('legacy') else (root/'VERSION').read_text().strip()
    try:
        manifest,files=inspect_archive(archive)
        task=installation_task(str(root))
        time.sleep(2)
        status('stopping','正在停止后台任务')
        task_action(task,'Stop');stopped=True
        # Never write code while the old listener is still alive.
        import socket
        for _ in range(30):
            with socket.socket() as s:
                if s.connect_ex(('127.0.0.1',port))!=0: break
            time.sleep(.3)
        else: raise RuntimeError('Service did not release its port; no program files replaced')
        status('applying','正在备份并更新程序，用户资料不参与覆盖')
        apply_files(root,files,backup);changed=True
        status('restarting','正在重启并检查新版本')
        task_action(task,'Start')
        if not healthy(port,manifest['version']): raise RuntimeError('New version failed its startup health check')
        status('completed','更新完成，本地画布、素材、配置保持不变')
    except Exception as error:
        if changed or (backup/'journal.json').exists():
            try:
                task_action(task,'Stop');time.sleep(1)
                restore_files(root,backup)
                task_action(task,'Start')
                if not healthy(port,previous): raise RuntimeError('Restored version did not start')
                status('rolled_back','新版本启动失败，已恢复更新前程序')
            except Exception:
                status('recovery_required','自动恢复未完成，请让 Codex 检查 data/team-update 的备份；不要重试生成')
        else:
            if task and stopped:
                try: task_action(task,'Start')
                except Exception: pass
            status('failed',str(error))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--archive',required=True);parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args();run(args.root,args.archive,args.port)
