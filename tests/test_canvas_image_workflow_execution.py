import asyncio
import json
import sys
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from canvas_agent import create_router
from test_canvas_image_workflow import review_answer
import test_canvas_image_workflow_integration as fixtures


class ImageCycleTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.WorkflowIntegrationTests.setUp
    provider = staticmethod(fixtures.WorkflowIntegrationTests.provider)
    write_canvas = fixtures.WorkflowIntegrationTests.write_canvas
    send = fixtures.WorkflowIntegrationTests.send

    async def llm(self, payload):
        if 'IMAGE_WORKFLOW:review' not in payload['system_prompt']:
            return await fixtures.WorkflowIntegrationTests.llm(self, payload)
        self.calls.append(payload)
        self.review_started.set()
        if self.review_gate:
            await self.review_gate.wait()
        answer = self.reviews.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {'text': json.dumps(answer, ensure_ascii=False)}

    async def no_image(self, request):
        index = len(self.image_calls)
        self.image_calls.append(request)
        self.image_started.set()
        if self.image_gate:
            await self.image_gate.wait()
        if self.fail_image_round == index:
            raise RuntimeError('provider connection lost after submission')
        return {'images': [f'/output/round{index}.png'], 'task_id': f'image-task-{index}'}

    async def prepare_run(self, reviews=None, budget=2):
        self.reviews = reviews if reviews is not None else [review_answer()]
        self.review_gate = self.image_gate = None
        self.review_started, self.image_started = asyncio.Event(), asyncio.Event()
        self.fail_image_round = None
        self.intent['max_revisions'] = budget
        sent = await self.send()
        self.assertEqual(sent.status_code, 200, sent.text)
        plan = sent.json()['plan']
        self.confirm_url = self.base + f"/plans/{plan['id']}/confirm"
        response = await self.client.post(self.confirm_url, json={'version': 1, 'client_id': 'one'})
        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()['run']
        self.assertIn('image_workflow', run, 'confirmed run must freeze actual workflow and revision budget')
        self.run_id = run['id']
        self.run_url = self.base + f"/runs/{run['id']}"
        for operation in ('p', 'm', 'c'):
            if operation == 'c':
                self.canvas['connections'].append({'from': f"agent_{run['id']}_p", 'to': f"agent_{run['id']}_m", 'kind': 'input'})
                ids = []
            else:
                ids = [f"agent_{run['id']}_{operation}"]
                self.canvas['nodes'].append({'id': ids[0], 'images': [], 'text': '',
                    'agent': {'canvasId': 'canvas', 'conversationId': run['conversation_id'], 'runId': run['id'],
                              'operationId': operation, 'role': 'prompt' if operation == 'p' else 'source'}})
            self.write_canvas()
            response = await self.client.post(self.run_url + '/events', json={
                'client_id': 'one', 'type': 'operation_completed', 'operation_id': operation, 'created_node_ids': ids})
            self.assertEqual(response.status_code, 200, response.text)
        return run

    async def execute(self):
        response = await self.client.post(self.run_url + '/steps/g/execute', json={'client_id': 'one'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def settled(self):
        for _ in range(200):
            data = (await self.client.get(self.run_url + '/events')).json()
            step = data['run']['steps'][-1]
            if step['status'] in ('generated', 'unknown', 'failed'):
                return step
            await asyncio.sleep(.005)
        self.fail('image cycle did not settle')

    async def test_passed_first_image_stops_and_keeps_frozen_models_and_evidence(self):
        run = await self.prepare_run()
        # Changes to later conversation preferences/skill selection cannot rewrite accepted work.
        await self.client.patch(self.base, json={'chat_provider': 'other', 'chat_model': 'other'})
        main.SKILL_CATALOG.save([], 1)
        await self.execute()
        step = await self.settled()
        self.assertEqual(len(self.image_calls), 1)
        self.assertEqual(step['status'], 'generated')
        self.assertEqual(step['result']['quality']['status'], 'passed')
        self.assertEqual(step['result']['best_url'], '/output/round0.png')
        self.assertEqual(self.image_calls[0].provider_id, 'tugo')
        self.assertEqual(self.image_calls[0].model, 'gpt-image-2')
        self.assertEqual(self.calls[-1]['model'], 'vision-chat')
        self.assertEqual(self.calls[-1]['provider'], 'deepseek')
        self.assertEqual(run['image_workflow']['max_revisions'], 2)

    async def test_two_revisions_are_bounded_preserve_all_versions_and_select_best(self):
        await self.prepare_run([review_answer(False, 70), review_answer(False, 90), review_answer(False, 80)])
        await self.execute()
        step = await self.settled()
        self.assertEqual(len(self.image_calls), 3)
        result = step['result']
        self.assertEqual([a['round'] for a in result['attempts']], [0, 1, 2])
        self.assertEqual([m['url'] for m in result['media']], ['/output/round0.png', '/output/round1.png', '/output/round2.png'])
        self.assertEqual(result['best_url'], '/output/round1.png')
        self.assertEqual(result['quality']['status'], 'limit_reached')
        for index, request in enumerate(self.image_calls[1:], 1):
            refs = [m.url for m in request.reference_images]
            self.assertEqual(refs[:2], [f'/output/round{index-1}.png', '/assets/product.png'])
            self.assertTrue(refs[2].startswith('/assets/skill-references/'))
            self.assertIn('只将顶部标题', request.prompt)
            self.assertEqual((request.provider_id, request.model, request.size, request.quality),
                             ('tugo', 'gpt-image-2', '768x1024', 'high'))

    async def test_zero_revision_budget_and_uncertain_review_never_trigger_edit(self):
        await self.prepare_run([review_answer(False)], budget=0)
        await self.execute(); step = await self.settled()
        self.assertEqual(len(self.image_calls), 1)
        self.assertEqual(step['result']['quality']['status'], 'limit_reached')

    async def test_review_failure_keeps_successful_picture_without_claiming_pass(self):
        await self.prepare_run([HTTPException(502, 'secret upstream details')])
        await self.execute(); step = await self.settled()
        self.assertEqual(step['status'], 'generated')
        self.assertEqual(step['result']['quality']['status'], 'review_failed')
        self.assertEqual(step['result']['media'][0]['url'], '/output/round0.png')
        self.assertNotIn('secret', json.dumps(step))
        self.assertEqual(len(self.image_calls), 1)

    async def test_stop_during_review_prevents_next_paid_submission(self):
        await self.prepare_run([review_answer(False)])
        self.review_gate = asyncio.Event()
        await self.execute()
        await asyncio.wait_for(self.review_started.wait(), 1)
        try:
            response = await self.client.post(self.run_url + '/stop', json={'client_id': 'one'})
            self.assertEqual(response.status_code, 200)
        finally:
            self.review_gate.set()
        step = await self.settled()
        self.assertEqual(len(self.image_calls), 1)
        self.assertEqual(step['result']['quality']['status'], 'stopped')
        self.assertEqual(step['result']['media'][0]['url'], '/output/round0.png')

    async def test_reference_mutation_before_revision_preserves_result_and_blocks_edit(self):
        await self.prepare_run([review_answer(False)])
        self.review_gate = asyncio.Event()
        await self.execute()
        await asyncio.wait_for(self.review_started.wait(), 1)
        self.canvas['nodes'][0]['images'][0]['url'] = '/assets/replaced.png'; self.write_canvas()
        self.review_gate.set()
        step = await self.settled()
        self.assertEqual(len(self.image_calls), 1)
        self.assertEqual(step['result']['quality']['status'], 'blocked')
        self.assertEqual(step['result']['media'][0]['url'], '/output/round0.png')

    async def test_uncertain_revision_submission_preserves_prior_success_and_cannot_replay(self):
        await self.prepare_run([review_answer(False)])
        self.fail_image_round = 1
        await self.execute(); step = await self.settled()
        self.assertEqual(step['status'], 'unknown')
        self.assertEqual(len(self.image_calls), 2)
        self.assertEqual(step['result']['media'][0]['url'], '/output/round0.png')
        await self.execute()
        await self.client.post(self.confirm_url, json={'version': 1, 'client_id': 'one'})
        self.assertEqual(len(self.image_calls), 2)

    async def test_duplicate_confirm_execute_and_poll_cannot_duplicate_initial_image(self):
        await self.prepare_run()
        self.image_gate = asyncio.Event()
        await self.execute()
        await asyncio.wait_for(self.image_started.wait(), 1)
        try:
            await self.execute()
            repeated = await self.client.post(self.confirm_url, json={'version': 1, 'client_id': 'one'})
            self.assertEqual(repeated.json()['run']['id'], self.run_id)
            await self.client.get(self.run_url + '/events')
            self.assertEqual(len(self.image_calls), 1)
        finally:
            self.image_gate.set()
        await self.settled()

    async def test_restart_distinguishes_review_interruption_from_unknown_image_submit(self):
        run = await self.prepare_run()
        await self.execute(); await self.settled()
        app = FastAPI()
        app.include_router(create_router(main.canvas_agent_store, get_provider=self.provider))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        self.addAsyncCleanup(client.aclose)
        for phase, expected in [('reviewing', 'generated'), ('submitting', 'unknown')]:
            def interrupted(record):
                stored = record['runs'][0]; stored['status'] = 'running'
                step = stored['steps'][-1]; step['status'] = 'running'
                step['result']['quality']['phase'] = phase
            main.canvas_agent_store.mutate('canvas', run['conversation_id'], interrupted)
            response = await client.get(self.base)
            restored = response.json()['conversation']['runs'][0]['steps'][-1]
            self.assertEqual(restored['status'], expected)
            self.assertEqual(restored['result']['media'][0]['url'], '/output/round0.png')
        self.assertEqual(len(self.image_calls), 1)


if __name__ == '__main__':
    unittest.main()
