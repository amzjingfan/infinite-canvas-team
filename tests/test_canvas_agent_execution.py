"""Execution contracts exercised through ASGI; every upstream is replaced."""
import asyncio
import json
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from canvas_agent import reference_snapshot, create_router


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target, value in [('CANVAS_DIR', temp.name), ('get_api_provider_exact', self.provider),
                              ('provider_env_key_value', lambda _: 'test-key')]:
            mock = patch.object(main, target, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.canvas = {'id': 'test', 'nodes': [{'id': 'ref', 'type': 'smart-prompt', 'text': 'old', 'images': []}], 'connections': []}
        self.write()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)
        self.calls = []

    def provider(self, pid):
        return {'id': pid, 'enabled': True, 'protocol': 'autodl' if pid == 'autodl' else 'openai',
                'image_models': ['image'], 'video_models': ['video'], 'chat_models': ['chat']}

    def write(self):
        (self.root / 'test.json').write_text(json.dumps(self.canvas), encoding='utf-8')

    async def plan(self, count=1, kind='image', provider='test', refs=None):
        c = (await self.client.post('/api/canvases/test/agent/conversations')).json()['conversation']
        self.base = f"/api/canvases/test/agent/conversations/{c['id']}"
        ops = [{'id': 'p', 'op': 'create_prompt', 'text': 'new'},
               {'id': 'm', 'op': 'create_media', 'kind': kind, 'reference_node_ids': refs or []},
               {'id': 'c', 'op': 'connect', 'from': 'p', 'to': 'm'},
               {'id': 'g', 'op': 'generate', 'node': 'm', 'settings': {
                   'provider': provider, 'model': kind, 'count': count, 'aspect_ratio': '16:9',
                   'resolution': '1k' if kind == 'image' else '480p',
                   'size': '1024x576' if kind == 'image' else '16:9',
                   **({'quality': 'high'} if kind == 'image' else {'duration': 5})}}]
        p = {'id': 'plan', 'version': 1, 'status': 'proposed', 'operations': ops,
             'reference_snapshot': reference_snapshot(self.canvas, refs or []),
             'generation_defaults': {'image': {'provider': 'test', 'model': 'image'}, 'video': {'provider': provider, 'model': 'video'}}}
        main.canvas_agent_store.mutate('test', c['id'], lambda r: r['plans'].append(p))
        return self.base

    async def confirm(self, base=None, client='one', expected=200):
        response = await self.client.post((base or self.base) + '/plans/plan/confirm', json={'version': 1, 'client_id': client})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json().get('run')

    async def event(self, run, op, base=None, client='one', expected=200):
        ids = [] if op == 'c' else [f"agent_{run['id']}_{op}"]
        # The real host strict-saves deterministic owned nodes before acknowledgement.
        if op != 'c':
            node = {'id': ids[0], 'agent': {'canvasId': 'test', 'conversationId': run['conversation_id'],
                    'runId': run['id'], 'operationId': op, 'role': 'output' if op == 'g' else ('prompt' if op == 'p' else 'source')}}
            if op == 'g':
                node['images'] = [{'url': '/output/ok.png', 'kind': 'image'}]
            self.canvas['nodes'] = [n for n in self.canvas['nodes'] if n['id'] != node['id']] + [node]
            self.write()
        else:
            self.canvas['connections'].append({'from':f"agent_{run['id']}_p",'to':f"agent_{run['id']}_m",'kind':'input'})
            self.write()
        response = await self.client.post((base or self.base) + f"/runs/{run['id']}/events", json={
            'type': 'operation_completed', 'operation_id': op, 'created_node_ids': ids, 'client_id': client})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    async def ready(self, run, base=None):
        for op in ('p', 'm', 'c'):
            await self.event(run, op, base)

    async def execute(self, run, base=None, client='one', resume=False, expected=200):
        response = await self.client.post((base or self.base) + f"/runs/{run['id']}/steps/g/execute", json={'client_id': client, 'resume_only': resume})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    async def settled(self, run, base=None):
        for _ in range(100):
            data = (await self.client.get((base or self.base) + f"/runs/{run['id']}/events")).json()
            if next(s for s in data['run']['steps'] if s['operation_id']=='g')['status'] not in ('submitting', 'running'):
                return data
            await asyncio.sleep(.005)
        self.fail('mock worker did not settle')

    async def test_confirm_idempotent_frozen_refs_and_ownership(self):
        await self.plan(refs=['ref'])
        self.canvas['nodes'][0]['x'] = 900
        self.write()
        run = await self.confirm()
        self.assertEqual((await self.confirm(client='two'))['id'], run['id'])
        await self.execute(run, expected=409)  # predecessors not yet acknowledged
        await self.ready(run)
        self.canvas['nodes'][0]['text'] = 'edited'
        self.write()
        await self.execute(run, expected=409)
        await self.execute(run, client='two', expected=409)

    async def test_concurrent_runs_duplicate_execute_stop_and_saved_ack(self):
        gate = asyncio.Event()
        async def image(req):
            self.calls.append(req)
            await gate.wait()
            return {'images': ['/output/ok.png'], 'image_items': [{'url': '/output/ok.png', 'width': 777, 'height': 333}], 'raw': 'SECRET'}
        with patch.object(main, 'build_online_image_result', image):
            a = await self.plan(); ar = await self.confirm(); await self.ready(ar)
            b = await self.plan(); br = await self.confirm(); await self.ready(br)
            await asyncio.gather(self.execute(ar, a), self.execute(br, b))
            await asyncio.sleep(.01)
            self.assertEqual(len(self.calls), 2)
            await self.execute(ar, a)
            await self.execute(ar, a, client='two', expected=409)
            stopped = await self.client.post(a + f"/runs/{ar['id']}/stop", json={'client_id': 'one'})
            self.assertEqual(stopped.status_code, 200)
            gate.set()
            result = await self.settled(ar, a)
            self.assertEqual(result['run']['status'], 'paused')
            self.assertEqual(result['run']['steps'][-1]['status'], 'generated')
            self.assertEqual(result['run']['steps'][-1]['result'], {'media': [{'url': '/output/ok.png', 'kind': 'image', 'name': '', 'width': 777, 'height': 333}]})
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertEqual((await self.settled(br, b))['run']['status'], 'running')
            ack = await self.event(br, 'g', b)
            self.assertEqual(ack['run']['status'], 'completed')
            self.assertEqual(len(self.calls), 2)

    async def test_partial_batch_success_is_durable_without_completing(self):
        async def image(req):
            self.calls.append(req)
            if len(self.calls) == 2:
                raise RuntimeError('SECRET transport error')
            return {'images': ['/output/ok.png']}
        with patch.object(main, 'build_online_image_result', image):
            await self.plan(count=2); run = await self.confirm(); await self.ready(run)
            await self.execute(run)
            data = await self.settled(run)
            step = data['run']['steps'][-1]
            self.assertEqual(step['status'], 'unknown')
            self.assertEqual(step['result']['media'][0]['url'], '/output/ok.png')
            self.assertNotIn('SECRET', json.dumps(data))
            await self.execute(run, resume=True)
            await self.event(run, 'g', expected=409)
            self.assertEqual([r.n for r in self.calls], [1, 1])

    async def test_result_only_takeover_keeps_unknown_terminal_and_rejects_old_owner(self):
        await self.plan(); run = await self.confirm(); await self.ready(run)
        def partial(record):
            r = record['runs'][0]
            r['status'] = 'unknown'
            r['steps'][-1].update(status='unknown', error='提交状态未知',
                result={'media': [{'url': '/partial.png', 'kind': 'image', 'name': ''}]})
        main.canvas_agent_store.mutate('test', run['conversation_id'], partial)
        url = self.base + f"/runs/{run['id']}/events"
        with patch.object(main, 'build_online_image_result', side_effect=AssertionError('no submit')):
            for _ in range(2):
                response = await self.client.post(url, json={'type': 'restore_results', 'client_id': 'two'})
                self.assertEqual(response.status_code, 200, response.text)
                restored = response.json()['run']
                self.assertEqual(restored['status'], 'unknown')
                self.assertEqual(restored['steps'][-1]['status'], 'unknown')
                self.assertEqual(restored['client_id'], 'two')
            await self.execute(run, expected=409)
            await self.execute(run, client='two')
            await self.event(run, 'g', client='two', expected=409)
            response = await self.client.post(url, json={'type': 'resume', 'client_id': 'two'})
            self.assertEqual(response.status_code, 409)
            response = await self.client.post(url, json={'type': 'restore_results', 'client_id': 'two', 'operation_id': 'g'})
            self.assertEqual(response.status_code, 422)

    async def test_autodl_known_task_only_queries_and_downloads(self):
        async def submit(req):
            self.calls.append(req)
            return {'task_id': 'known', 'status': 'RUNNING'}
        async def query(task):
            self.assertEqual(task, 'known')
            return {'status': 'SUCCESS', 'urls': ['https://remote/video.mp4']}
        async def download(req):
            self.assertEqual(req.task_id, 'known')
            return {'urls': ['/output/video.mp4']}
        with patch.object(main, 'autodl_submit', submit), patch.object(main, 'autodl_result', query), patch.object(main, 'autodl_download', download):
            await self.plan(kind='video', provider='autodl'); run = await self.confirm(); await self.ready(run)
            await self.execute(run); data = await self.settled(run)
            self.assertEqual(data['run']['steps'][-1]['provider_task_id'], 'known')
            self.assertEqual(data['run']['steps'][-1]['result']['media'][0]['url'], '/output/video.mp4')
            self.assertEqual(self.calls[0].resolution, '480p横')
            await self.execute(run, resume=True)
            self.assertEqual(len(self.calls), 1)

    async def test_trash_during_submission_retains_paid_result_but_purge_does_not_recreate(self):
        for purge in (False, True):
            gate = asyncio.Event()
            async def image(req):
                await gate.wait()
                return {'images': ['/output/ok.png']}
            with patch.object(main, 'build_online_image_result', image):
                self.canvas.pop('deleted_at', None); self.write()
                await self.plan(); run = await self.confirm(); await self.ready(run); await self.execute(run)
                self.canvas['deleted_at'] = 1; self.write()
                if purge:
                    main.canvas_agent_store.purge('test')
                gate.set(); await asyncio.sleep(.05)
                self.canvas.pop('deleted_at'); self.write()
                data = await self.client.get(self.base)
                if purge:
                    self.assertEqual(data.status_code, 404)
                else:
                    self.assertEqual(data.json()['conversation']['runs'][0]['steps'][-1]['status'], 'generated')

    async def test_same_clock_canvas_save_conflicts_and_merged_retry_preserves_nodes(self):
        with patch.object(main, 'now_ms', return_value=9000):
            main.save_canvas({'id': 'race', 'kind': 'smart', 'nodes': [], 'connections': []})
            a = await self.client.put('/api/canvases/race', json={'nodes': [{'id': 'a'}], 'base_updated_at': 9000})
            b = await self.client.put('/api/canvases/race', json={'nodes': [{'id': 'b'}], 'base_updated_at': 9000})
            self.assertEqual(a.status_code, 200)
            self.assertEqual(b.status_code, 409)
            latest = b.json()['detail']['canvas']
            retry = await self.client.put('/api/canvases/race', json={'nodes': latest['nodes'] + [{'id': 'b'}], 'base_updated_at': latest['updated_at']})
            self.assertEqual([n['id'] for n in retry.json()['canvas']['nodes']], ['a', 'b'])

    async def test_generated_reference_stops_at_output_and_preserves_prompt_fallback(self):
        self.canvas = {'id': 'test', 'nodes': [
            {'id': 'original', 'images': [{'url': f'/{i}.png'} for i in range(5)]},
            {'id': 'out', 'agent': {'role': 'output'}, 'runModelPrompt': 'frozen prompt', 'images': [{'url': '/result.png'}]}],
            'connections': [{'from': 'original', 'to': 'out', 'kind': 'flow'}]}
        snap = reference_snapshot(self.canvas, ['out'])
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]['prompt_fallback'], 'frozen prompt')
        self.assertEqual(snap[0]['input_node_ids'], [])

    async def restarted_client(self):
        app = FastAPI()
        app.include_router(create_router(main.canvas_agent_store, get_provider=lambda p: main.get_api_provider_exact(p),
            prepare_generation=lambda *args: main.prepare_canvas_agent_generation(*args),
            generate_image=lambda r: main.build_online_image_result(r), generate_video=lambda r: main.canvas_video(r),
            autodl_submit=lambda r: main.autodl_submit(r), autodl_result=lambda t: main.autodl_result(t),
            autodl_download=lambda t: main.autodl_download(main.AutoDLDownloadRequest(task_id=t))))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_restart_unknown_keeps_partial_media_and_never_resubmits(self):
        await self.plan(); run = await self.confirm(); await self.ready(run)
        def crashed(record):
            record['runs'][0]['steps'][-1].update(status='submitting', result={'media':[{'url':'/partial.png','kind':'image','name':''}]})
        main.canvas_agent_store.mutate('test',run['conversation_id'],crashed)
        client = await self.restarted_client()
        with patch.object(main,'build_online_image_result',side_effect=AssertionError('must not submit')):
            data = (await client.get(self.base)).json()['conversation']['runs'][0]
            self.assertEqual(data['status'],'unknown')
            self.assertEqual(data['steps'][-1]['result']['media'][0]['url'],'/partial.png')
            response = await client.post(self.base + f"/runs/{run['id']}/steps/g/execute",json={'client_id':'one','resume_only':True})
            self.assertEqual(response.json()['step']['status'],'unknown')
            response = await client.post(self.base + f"/runs/{run['id']}/events",json={'type':'resume','client_id':'two'})
            self.assertEqual(response.status_code,409)

    async def test_restart_ready_requires_explicit_resume_and_repeat_confirm_cannot_resume(self):
        await self.plan(); run = await self.confirm()
        client = await self.restarted_client()
        data = (await client.get(self.base)).json()['conversation']['runs'][0]
        self.assertEqual(data['status'],'paused')
        repeated = await client.post(self.base + '/plans/plan/confirm',json={'client_id':'two','version':1})
        self.assertEqual(repeated.json()['run']['status'],'paused')
        response = await client.post(self.base + f"/runs/{run['id']}/events",json={'type':'resume','client_id':'two'})
        self.assertEqual(response.json()['run']['client_id'],'two')
        await self.event(run,'p',expected=409)  # former owner can no longer advance

    async def test_restart_autodl_existing_task_id_only_queries_even_if_references_changed(self):
        await self.plan(kind='video',provider='autodl',refs=['ref']); run=await self.confirm(); await self.ready(run)
        main.canvas_agent_store.mutate('test',run['conversation_id'],lambda r:r['runs'][0]['steps'][-1].update(status='running',provider_task_id='known'))
        self.canvas['nodes'][0]['text']='changed'; self.write()
        client = await self.restarted_client()
        async def query(task):
            self.calls.append(task); return {'status':'SUCCESS','urls':['https://remote/video.mp4']}
        async def download(request): return {'urls':['/output/video.mp4']}
        with patch.object(main,'autodl_submit',side_effect=AssertionError('duplicate submission')), patch.object(main,'autodl_result',query), patch.object(main,'autodl_download',download):
            await client.get(self.base)
            await client.post(self.base + f"/runs/{run['id']}/events",json={'type':'resume','client_id':'two'})
            response=await client.post(self.base + f"/runs/{run['id']}/steps/g/execute",json={'client_id':'two','resume_only':True})
            self.assertEqual(response.status_code,200,response.text)
            await asyncio.sleep(.03)
            data=(await client.get(self.base)).json()['conversation']['runs'][0]
            self.assertEqual(data['steps'][-1]['status'],'generated')
            self.assertEqual(self.calls,['known'])

    async def test_confirm_rejects_stale_version_url_or_topology_but_not_layout(self):
        await self.plan(refs=['ref'])
        wrong = await self.client.post(self.base+'/plans/plan/confirm',json={'version':2,'client_id':'one'})
        self.assertEqual(wrong.status_code,409)
        self.canvas['nodes'][0]['images']=[{'url':'/changed.png'}]; self.write()
        await self.confirm(expected=409)
        self.canvas['nodes'][0]['images']=[]
        self.canvas['nodes'].append({'id':'newparent','text':'new ancestry'})
        self.canvas['connections']=[{'from':'newparent','to':'ref'}]; self.write()
        await self.confirm(expected=409)

    async def test_missing_key_disabled_provider_and_long_combined_prompt_reject_before_submit(self):
        await self.plan()
        with patch.object(main,'provider_env_key_value',return_value=''):
            await self.confirm(expected=422)
        with patch.object(main,'get_api_provider_exact',return_value={**self.provider('test'),'enabled':False}):
            await self.confirm(expected=422)
        await self.plan(refs=['ref'])
        cid=self.base.rsplit('/',1)[-1]
        main.canvas_agent_store.mutate('test',cid,lambda r:r['plans'][0]['operations'][0].update(text='x'*20000))
        await self.confirm(expected=422)
        self.assertEqual(main.canvas_agent_store.get('test',cid)['runs'],[])

    async def test_reference_edit_before_new_submission_marks_failed_so_new_plan_can_be_confirmed(self):
        await self.plan(refs=['ref']); run=await self.confirm(); await self.ready(run)
        self.canvas['nodes'][0]['text']='changed'; self.write()
        await self.execute(run,expected=409)
        stored=main.canvas_agent_store.get('test',run['conversation_id'])
        self.assertEqual(stored['runs'][0]['status'],'failed')
        self.assertEqual(stored['runs'][0]['steps'][-1]['status'],'failed')
        def new_plan(record):
            p=deepcopy(record['plans'][0]); p.update(id='plan2',status='proposed',reference_snapshot=reference_snapshot(self.canvas,['ref']))
            record['plans'].append(p)
        main.canvas_agent_store.mutate('test',run['conversation_id'],new_plan)
        response=await self.client.post(self.base+'/plans/plan2/confirm',json={'version':1,'client_id':'one'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotEqual(response.json()['run']['id'],run['id'])

    async def test_generated_reference_excludes_other_history_and_sends_only_result_to_discussion_and_video(self):
        self.canvas={'id':'test','nodes':[{'id':'old','images':[{'url':f'/original-{i}.png'} for i in range(5)]},
            {'id':'out','agent':{'role':'output'},'runModelPrompt':'frozen prompt','images':[{'url':'/result.png'}]}],
            'connections':[{'from':'old','to':'out','kind':'flow'}]}; self.write()
        await self.plan(kind='video',refs=['out'])
        async def llm(payload):
            self.assertEqual(payload['images'],['/result.png'])
            self.assertNotIn('/original-',payload['system_prompt'])
            self.assertEqual(payload['messages'],[])
            return {'text':json.dumps({'kind':'chat','reply':'ok'})}
        with patch.object(main,'run_canvas_agent_llm',llm):
            response=await self.client.post(self.base+'/messages',json={'message':'look','reference_node_ids':['out'],
                'chat_provider':'test','chat_model':'chat','generation_defaults':{'image':{'provider':'test','model':'image'},'video':{'provider':'test','model':'video'}}})
            self.assertEqual(response.status_code,200,response.text)
        async def video(request):
            self.assertEqual([m.url for m in request.images],['/result.png'])
            self.assertEqual(request.prompt,'new')  # explicit connected prompt overrides fallback
            return {'videos':['/output/video.mp4'],'task_id':'generic-known','raw':'SECRET'}
        with patch.object(main,'canvas_video',video):
            run=await self.confirm(); await self.ready(run); await self.execute(run)
            data=await self.settled(run)
            self.assertEqual(data['step']['provider_task_id'] if 'step' in data else data['run']['steps'][-1]['provider_task_id'],'generic-known')

    async def test_canvas_and_agent_reads_share_short_lock_during_actual_truncated_write(self):
        await self.plan()
        cid=self.base.rsplit('/',1)[-1]
        entered, release, reading=threading.Event(),threading.Event(),threading.Event()
        original_dump=json.dump
        def delayed_dump(value,target,*args,**kwargs):
            if str(getattr(target,'name','')).endswith('test.json'):
                entered.set(); release.wait(2)
            return original_dump(value,target,*args,**kwargs)
        def read():
            reading.set()
            return main.canvas_agent_store.get('test',cid)
        with patch.object(main.json,'dump',delayed_dump):
            writer=asyncio.create_task(asyncio.to_thread(main.save_canvas,self.canvas))
            await asyncio.to_thread(entered.wait,1)
            reader=asyncio.create_task(asyncio.to_thread(read))
            await asyncio.to_thread(reading.wait,1)
            try:
                await asyncio.sleep(.03)
                self.assertFalse(reader.done(),'Agent must wait for the in-place canvas write')
            finally:
                release.set()
            await writer
            self.assertEqual((await reader)['id'],cid)

    async def test_dynamic_output_count_is_revalidated_without_slicing_or_submitting_video(self):
        await self.plan()
        cid=self.base.rsplit('/',1)[-1]
        def extend(record):
            record['plans'][0]['operations'].extend([
                {'id':'v','op':'create_media','kind':'video','reference_node_ids':['g']},
                {'id':'vg','op':'generate','node':'v','settings':{'provider':'test','model':'video','count':1,
                    'aspect_ratio':'16:9','resolution':'480p','size':'16:9','duration':5}}])
        main.canvas_agent_store.mutate('test',cid,extend)
        async def image(request): return {'images':[f'/output/{i}.png' for i in range(5)]}
        with patch.object(main,'build_online_image_result',image), patch.object(main,'canvas_video',side_effect=AssertionError('must reject five images')):
            run=await self.confirm(); await self.ready(run); await self.execute(run)
            await self.settled(run)
            result=main.canvas_agent_store.get('test',cid)['runs'][0]['steps'][3]['result']
            self.assertEqual(len(result['media']),5)
            self.canvas['nodes'].append({'id':f"agent_{run['id']}_g",'images':result['media'],
                'agent':{'canvasId':'test','conversationId':cid,'runId':run['id'],'operationId':'g','role':'output'}})
            self.write()
            response=await self.client.post(self.base+f"/runs/{run['id']}/events",json={'type':'operation_completed','operation_id':'g',
                'created_node_ids':[f"agent_{run['id']}_g"],'client_id':'one'})
            self.assertEqual(response.status_code,200,response.text)
            await self.event(run,'v')
            response=await self.client.post(self.base+f"/runs/{run['id']}/steps/vg/execute",json={'client_id':'one'})
            self.assertEqual(response.status_code,422,response.text)
            self.assertEqual(main.canvas_agent_store.get('test',cid)['runs'][0]['status'],'failed')

    async def generated_dependency_run(self, provider='test'):
        await self.plan()
        cid = self.base.rsplit('/', 1)[-1]
        def extend(record):
            record['plans'][0]['operations'].extend([
                {'id': 'v', 'op': 'create_media', 'kind': 'video', 'reference_node_ids': ['g']},
                {'id': 'vg', 'op': 'generate', 'node': 'v', 'settings': {
                    'provider': provider, 'model': 'video', 'count': 1, 'aspect_ratio': '16:9',
                    'resolution': '480p', 'size': '16:9', 'duration': 5}}])
        main.canvas_agent_store.mutate('test', cid, extend)
        run = await self.confirm()
        async def image(request): return {'images': ['/output/ok.png']}
        with patch.object(main, 'build_online_image_result', image):
            await self.ready(run); await self.execute(run); await self.settled(run)
        await self.event(run, 'g'); await self.event(run, 'v')
        return run

    async def test_consumed_generated_dependency_changes_block_new_submit(self):
        for change in ('crop', 'replacement', 'removal', 'deletion', 'ownership'):
            with self.subTest(change=change):
                run = await self.generated_dependency_run()
                output = next(n for n in self.canvas['nodes'] if n['id'] == f"agent_{run['id']}_g")
                if change in ('crop', 'replacement'):
                    output['images'][0]['url'] = '/user-cropped.png' if change == 'crop' else '/replacement.png'
                elif change == 'removal': output['images'] = []
                elif change == 'deletion': self.canvas['nodes'].remove(output)
                else: output['agent']['conversationId'] = 'other'
                self.write()
                async def video(request):
                    self.calls.append(request)
                    return {'videos': ['/unexpected.mp4']}
                with patch.object(main, 'canvas_video', video):
                    response = await self.client.post(self.base + f"/runs/{run['id']}/steps/vg/execute", json={'client_id': 'one'})
                    await asyncio.sleep(.01)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.calls, [])
                stored = main.canvas_agent_store.get('test', run['conversation_id'])['runs'][0]
                self.assertEqual(stored['status'], 'failed')
                self.assertIn('重新制定', stored['steps'][-1]['error'])

    async def test_consumed_generated_dependency_layout_and_unrelated_output_are_allowed(self):
        run = await self.generated_dependency_run()
        output = next(n for n in self.canvas['nodes'] if n['id'] == f"agent_{run['id']}_g")
        output.update(x=999, y=222, selected=True)
        self.canvas['nodes'].append({'id': 'unrelated', 'images': [{'url': '/edited.png'}], 'agent': {'role': 'output'}})
        self.write()
        async def video(request):
            self.calls.append(request)
            return {'videos': ['/ok.mp4']}
        with patch.object(main, 'canvas_video', video):
            response = await self.client.post(self.base + f"/runs/{run['id']}/steps/vg/execute", json={'client_id': 'one'})
            self.assertEqual(response.status_code, 200, response.text)
            await asyncio.sleep(.02)
        self.assertEqual(len(self.calls), 1)
        self.assertIn('/output/ok.png', str(self.calls[0]))

    async def test_consumed_generated_dependency_edits_do_not_block_known_task_query(self):
        run = await self.generated_dependency_run('autodl')
        main.canvas_agent_store.mutate('test', run['conversation_id'], lambda r: r['runs'][0]['steps'][-1].update(
            status='running', provider_task_id='known'))
        self.canvas['nodes'] = [n for n in self.canvas['nodes'] if n['id'] != f"agent_{run['id']}_g"]
        self.write()
        client = await self.restarted_client()
        async def query(task): self.calls.append(task); return {'status': 'SUCCESS'}
        async def download(request): return {'urls': ['/ok.mp4']}
        with patch.object(main, 'autodl_submit', side_effect=AssertionError('no submit')), patch.object(main, 'autodl_result', query), patch.object(main, 'autodl_download', download):
            await client.post(self.base + f"/runs/{run['id']}/events", json={'type': 'resume', 'client_id': 'two'})
            response = await client.post(self.base + f"/runs/{run['id']}/steps/vg/execute", json={'client_id': 'two', 'resume_only': True})
            self.assertEqual(response.status_code, 200, response.text)
            await asyncio.sleep(.02)
        self.assertEqual(self.calls, ['known'])
        self.assertEqual(main.canvas_agent_store.get('test', run['conversation_id'])['runs'][0]['steps'][-1]['status'], 'generated')

    async def test_graph_dependency_failure_is_owned_validated_terminal_and_allows_new_plan(self):
        await self.plan(refs=['ref']); run = await self.confirm()
        await self.event(run, 'p')
        url = self.base + f"/runs/{run['id']}/events"
        body = {'type': 'dependency_failed', 'operation_id': 'm', 'client_id': 'one'}
        # A report alone cannot fail a valid graph or another owner's run.
        self.assertEqual((await self.client.post(url, json=body)).status_code, 409)
        self.canvas['nodes'] = [n for n in self.canvas['nodes'] if n['id'] != 'ref']; self.write()
        self.assertEqual((await self.client.post(url, json={**body, 'client_id': 'two'})).status_code, 409)
        response = await self.client.post(url, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        failed = response.json()['run']
        self.assertEqual(failed['status'], 'failed'); self.assertEqual(failed['steps'][1]['status'], 'failed')
        self.assertEqual(failed['steps'][0]['status'], 'completed')
        self.assertNotIn('ref', [n['id'] for n in self.canvas['nodes']])
        def replacement(record):
            p = deepcopy(record['plans'][0]); p.update(id='replacement', status='proposed', reference_snapshot=[])
            p['operations'][1]['reference_node_ids'] = []
            record['plans'].append(p)
        main.canvas_agent_store.mutate('test', run['conversation_id'], replacement)
        response = await self.client.post(self.base + '/plans/replacement/confirm', json={'version': 1, 'client_id': 'one'})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_missing_saved_connect_cannot_be_acknowledged(self):
        await self.plan(); run=await self.confirm()
        await self.event(run,'p'); await self.event(run,'m')
        response=await self.client.post(self.base+f"/runs/{run['id']}/events",json={'type':'operation_completed',
            'operation_id':'c','created_node_ids':[],'client_id':'one'})
        self.assertEqual(response.status_code,409,response.text)

    async def test_generated_event_rejects_missing_saved_media_and_resume_only_cannot_submit_ready(self):
        async def image(req):
            self.calls.append(req)
            return {'images':['/output/ok.png']}
        with patch.object(main,'build_online_image_result',image):
            await self.plan(); run=await self.confirm(); await self.ready(run)
            await self.execute(run,resume=True)
            self.assertEqual(self.calls,[])
            response=await self.client.post(self.base+f"/runs/{run['id']}/steps/missing/execute",json={'client_id':'one'})
            self.assertEqual(response.status_code,404)
            await self.execute(run); await self.settled(run)
            response=await self.client.post(self.base+f"/runs/{run['id']}/events",json={'type':'operation_completed',
                'operation_id':'g','created_node_ids':[f"agent_{run['id']}_g"],'client_id':'one'})
            self.assertEqual(response.status_code,409)
            await self.event(run,'g')
            await self.execute(run)
            self.assertEqual(len(self.calls),1)

    async def test_autodl_explicit_query_retry_clears_old_query_error_without_paid_retry(self):
        await self.plan(kind='video',provider='autodl'); run=await self.confirm(); await self.ready(run)
        main.canvas_agent_store.mutate('test',run['conversation_id'],lambda r:r['runs'][0]['steps'][-1].update(
            status='running',provider_task_id='known',error='previous query failed'))
        gate=asyncio.Event()
        async def query(task):
            await gate.wait(); return {'status':'RUNNING','urls':[]}
        with patch.object(main,'autodl_result',query),patch.object(main,'autodl_submit',side_effect=AssertionError('duplicate submit')):
            response=await self.execute(run,resume=True)
            self.assertEqual(response['step']['error'],'')
            gate.set(); await asyncio.sleep(.02)

    async def test_run_events_reject_extra_fields_and_step_data_on_stop_without_mutation(self):
        await self.plan(); run=await self.confirm()
        client=httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app,raise_app_exceptions=False),base_url='http://test')
        self.addAsyncCleanup(client.aclose)
        for body in ({'type':'stop','client_id':'one','operation_id':'missing'},
                     {'type':'resume','client_id':'one','created_node_ids':['fake']},
                     {'type':'operation_completed','client_id':'one','operation_id':'p','result':{'media':[]}}):
            response=await client.post(self.base+f"/runs/{run['id']}/events",json=body)
            self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(main.canvas_agent_store.get('test',run['conversation_id'])['runs'][0]['status'],'running')


if __name__ == '__main__':
    unittest.main()
