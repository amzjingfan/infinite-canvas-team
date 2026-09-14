"""GitHub Releases API and maintenance gate; secrets stay in each user's gh keyring."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from team_update import REPO, ASSET, inspect_archive, atomic_write, busy_record
from team_update_worker import installation_task

def gh(args,timeout=45):
    exe=shutil.which('gh') or str(Path(os.environ.get('ProgramFiles','C:/Program Files'))/'GitHub CLI/gh.exe')
    try:
        result=subprocess.run([exe,*args],capture_output=True,timeout=timeout,
            creationflags=0x08000000 if os.name=='nt' else 0)
    except (OSError,subprocess.TimeoutExpired):
        raise RuntimeError('请安装 GitHub CLI，并用获仓库权限的个人账号执行 gh auth login') from None
    if result.returncode: raise RuntimeError('GitHub 访问失败：请检查网络、gh auth login 和团队私有仓库权限')
    return result.stdout

def install_team_updates(app,base,busy_callback):
    root=Path(base);statusfile=root/'data/team-update/status.json'
    guard=threading.Lock();state={'maintenance':False,'writes':0}
    def status():
        if not statusfile.exists(): return {'phase':'idle','message':'尚未执行团队更新'}
        try: return json.loads(statusfile.read_text(encoding='utf-8'))
        except Exception: return {'phase':'recovery_required','message':'更新记录无法读取，请检查备份'}
    def maintaining():
        return state['maintenance'] or status().get('phase') in ('stopping','applying','restarting','recovery_required','prepared')
    @app.middleware('http')
    async def maintenance(request,call_next):
        mutation=request.method not in ('GET','HEAD','OPTIONS') and request.url.path not in ('/api/team-update/apply','/api/update-from-github')
        with guard:
            if mutation and maintaining(): return JSONResponse({'detail':'正在更新程序，暂时不能修改画布或生成，请稍后重试'},status_code=503)
            if mutation: state['writes']+=1
        try: return await call_next(request)
        finally:
            if mutation:
                with guard: state['writes']-=1
    # Retire every legacy mutation path and third-party source fallback.
    retired={'/api/check-update','/api/update-from-github','/api/update-rollback','/api/update-backups','/api/update-connectivity','/api/update-connectivity/probe'}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path','') not in retired]

    def current(): return (root/'VERSION').read_text().strip()
    def latest():
        release=json.loads(gh(['api',f'repos/{REPO}/releases/latest']))
        tag=release.get('tag_name','');version=tag.removeprefix('v')
        import re
        if not re.fullmatch(r'\d+(?:\.\d+){2,4}',version): raise RuntimeError('发布版本格式不支持')
        assets={x['name'] for x in release.get('assets',[])}
        if not {ASSET,ASSET+'.sha256'}.issubset(assets): raise RuntimeError('发布版本缺少更新资产或校验文件')
        return {'version':version,'tag':tag,'notes':release.get('body',''),'url':release.get('html_url','')}
    def check():
        try:
            release=latest(); available=tuple(map(int,release['version'].split('.')))>tuple(map(int,current().split('.')))
            return {'current':current(),'repository':REPO,'latest':{**release,'source':'github'},'reachable':True,'update_available':available,'github':{'ok':True,'version':release['version']},'modelscope':{'ok':False,'error':'团队版不使用此更新源'}}
        except Exception as error:
            return {'current':current(),'repository':REPO,'reachable':False,'update_available':False,'latest':{},'error':str(error),'github':{'ok':False,'error':str(error)},'modelscope':{'ok':False}}
    app.get('/api/check-update')(check)
    app.get('/api/team-update/check')(check)
    app.get('/api/team-update/status')(status)
    @app.get('/api/team-update/health')
    def health(): return {'version':current(),'pid':os.getpid()}

    def apply(request: Request):
        from urllib.parse import urlsplit
        origin=request.headers.get('origin','')
        if request.client and request.client.host not in ('127.0.0.1','::1','testclient'):
            raise HTTPException(403,'请在运行画布的电脑本机执行更新')
        if origin and urlsplit(origin).hostname not in ('127.0.0.1','localhost','::1'):
            raise HTTPException(403,'更新请求来源不受支持')
        with guard:
            if maintaining(): raise HTTPException(409,'已有更新或恢复操作，请查看更新状态')
            state['maintenance']=True
        try:
            if state['writes'] or busy_callback(): raise HTTPException(409,'仍有写入、生成或待取回任务，请保存画布并完成任务后再更新')
            for f in (root/'data/canvases').rglob('*.json'):
                if busy_record(json.loads(f.read_text(encoding='utf-8'))): raise HTTPException(409,'画布存在未完成任务，请先查询并保存结果')
            installation_task(str(root))
            release=latest()
            if tuple(map(int,release['version'].split('.'))) <= tuple(map(int,current().split('.'))): raise HTTPException(409,'当前已是最新版本，不需要更新')
            operation=uuid.uuid4().hex;stage=root/'data/team-update'/operation;stage.mkdir(parents=True)
            gh(['release','download',release['tag'],'--repo',REPO,'--pattern',ASSET,'--pattern',ASSET+'.sha256','--dir',str(stage)],timeout=180)
            archive=stage/ASSET
            expected=(stage/(ASSET+'.sha256')).read_text().split()[0].lower()
            if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected: raise RuntimeError('更新包校验失败，未替换任何程序')
            manifest,files=inspect_archive(archive)
            if manifest['version']!=release['version']: raise RuntimeError('发布版本与更新包不一致')
            if files['requirements.txt'].strip()!=(root/'requirements.txt').read_bytes().strip(): raise RuntimeError('此版本改变运行依赖，需要使用完整安装版；自动更新已停止，原程序不变')
            # Import staged application using installed dependencies before stopping service.
            preview=stage/'preview'
            for name,content in files.items(): atomic_write(preview/name,content)
            code='import sys; sys.path.insert(0,'+repr(str(preview))+'); import main'
            result=subprocess.run([sys.executable,'-B','-c',code],cwd=preview,capture_output=True,timeout=45,
                creationflags=0x08000000 if os.name=='nt' else 0)
            if result.returncode: raise RuntimeError('新版本导入检查失败，未停止原服务')
            # Detect writes/background work that raced with the initial gate.
            if state['writes'] or busy_callback(): raise HTTPException(409,'任务尚未结束，更新已取消')
            # Find listener belonging to this exact process, not a configured default port.
            from team_update_worker import ps
            port=int(ps('(Get-NetTCPConnection -State Listen | Where-Object OwningProcess -eq '+str(os.getpid())+' | Select-Object -First 1).LocalPort'))
            record={'operation':operation,'phase':'prepared','version':release['version'],'message':'更新包已校验，正在准备重启','updated_at':time.time()}
            atomic_write(statusfile,json.dumps(record,ensure_ascii=False).encode())
            subprocess.Popen([sys.executable,'-B',str(root/'team_update_worker.py'),'--root',str(root),'--archive',str(archive),'--port',str(port)],
                cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x00000008|0x00000200)
            return {'ok':True,'phase':'prepared','restart_scheduled':True,'source':'github','version':release['version']}
        except HTTPException: raise
        except Exception as error:
            if status().get('phase')=='prepared': atomic_write(statusfile,json.dumps({'phase':'failed','message':'更新进程未能启动'}).encode())
            raise HTTPException(400,str(error)) from None
        finally:
            state['maintenance']=False
    app.post('/api/team-update/apply')(apply)
    app.post('/api/update-from-github')(apply)
