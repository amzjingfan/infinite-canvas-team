import asyncio
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from canvas_agent import generation_inputs
from canvas_creative import creative_inputs
from canvas_image_design import compile_design
from canvas_image_workflow import revision_inputs
from image_design_fixtures import card, snapshot, research, defaults
from test_canvas_image_workflow import message
from test_canvas_image_workflow_quality import issue, issue_review
import test_canvas_image_workflow_execution as legacy


def cycle_card():
    value = card()
    value['subject']['references'][0]['source_id'] = 'ref'
    value['case_uses'][0]['case_id'] = 358
    value['copy']['exact_text'] = [{'text': 'Textured Surface', 'placement': '顶部标题', 'source': 'proposal', 'evidence': ''},
                                  {'text': 'Design for everyday use', 'placement': '副标题', 'source': 'proposal', 'evidence': ''}]
    return value


class DesignExecutionTests(unittest.IsolatedAsyncioTestCase):
    setUp = legacy.ImageCycleTests.setUp
    provider = staticmethod(legacy.ImageCycleTests.provider)
    write_canvas = legacy.ImageCycleTests.write_canvas
    send = legacy.ImageCycleTests.send
    no_image = legacy.ImageCycleTests.no_image
    settled = legacy.ImageCycleTests.settled

    async def llm(self, payload):
        if 'IMAGE_DESIGN:' in payload['system_prompt']:
            self.calls.append(deepcopy(payload))
            return {'text': json.dumps({'issues': []} if 'IMAGE_DESIGN:preflight' in payload['system_prompt'] else
                                      {'kind': 'plan', 'design_card': self.design}, ensure_ascii=False)}
        return await legacy.ImageCycleTests.llm(self, payload)

    async def execute(self):
        response = await self.client.post(self.run_url + '/steps/image1/execute', json={'client_id': 'one'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def prepare_run(self, reviews, mode='design_only'):
        self.design = cycle_card()
        self.reviews = reviews
        self.review_gate = self.image_gate = None
        self.review_started, self.image_started = asyncio.Event(), asyncio.Event()
        self.fail_image_round = None
        created = (await self.client.post('/api/canvases/canvas/agent/conversations')).json()['conversation']
        self.base = f"/api/canvases/canvas/agent/conversations/{created['id']}"
        await self.client.patch(self.base, json={'case_input_mode': mode})
        sent = await self.client.post(self.base + '/messages', json=message(
            message='/gpt-image-2-style-library 保留产品外观，生成信息海报', case_input_mode=mode).model_dump())
        self.assertEqual(sent.status_code, 200, sent.text)
        self.proposal = sent.json()['plan']
        self.confirm_url = self.base + f"/plans/{self.proposal['id']}/confirm"
        confirmed = await self.client.post(self.confirm_url, json={'version': 1, 'client_id': 'one'})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        run = confirmed.json()['run']
        self.run_id = run['id']
        self.run_url = self.base + f"/runs/{run['id']}"
        return run

    async def test_research_default_never_enters_initial_image_review_or_targeted_edit_and_confirmation_freezes_design(self):
        run = await self.prepare_run([issue_review([issue()]), issue_review([issue(status='pass', can_edit=False, fix='')])])
        self.assertEqual(run.get('design_card'), self.design)
        self.assertEqual(run.get('compiler_version'), 'image-design/3.0')
        self.assertEqual([m['url'] for m in generation_inputs(run, 'image1')[2]], ['/assets/product.png'])
        await self.client.patch(self.base, json={'case_input_mode': 'single_case', 'chat_model': 'other'})
        def stale_research(record):
            context = record['runs'][0]['image_workflow']
            context['cases'][0]['purpose'] = 'WRONG PRIMARY STYLE'
            context['cases'][0]['media']['url'] = '/assets/stale-case.png'
            context['identity_media'][0]['url'] = '/assets/stale-product.png'
        main.canvas_agent_store.mutate('canvas', run['conversation_id'], stale_research)
        self.canvas['nodes'] = []
        self.write_canvas()
        await self.execute()
        step = await self.settled()
        self.assertEqual(step['result']['quality']['status'], 'passed')
        self.assertEqual(len(self.image_calls), 2)
        self.assertEqual([m.url for m in self.image_calls[0].reference_images], ['/assets/product.png'])
        self.assertEqual([m.url for m in self.image_calls[1].reference_images], ['/output/round0.png', '/assets/product.png'])
        self.assertEqual(self.image_calls[0].prompt, run['operations'][0]['prompt'])
        self.assertIn('图2：用户主体身份参考', self.image_calls[1].prompt)
        self.assertNotIn('图1：用户主体身份参考', self.image_calls[1].prompt)
        self.assertNotIn('WRONG PRIMARY', self.image_calls[1].prompt)
        reviews = [p for p in self.calls if 'IMAGE_WORKFLOW:review' in p['system_prompt']]
        self.assertEqual(reviews[0]['images'], ['/output/round0.png', '/assets/product.png'])
        self.assertEqual(reviews[1]['images'], ['/output/round1.png', '/assets/product.png', '/output/round0.png'])
        self.assertTrue(all(p['model'] == 'vision-chat' for p in reviews))
        record = (await self.client.get(self.base)).json()['conversation']
        self.assertEqual(record['runs'][0]['operations'], run['operations'])

    async def test_historical_confirmed_single_case_run_keeps_its_frozen_inputs_in_every_generation_stage(self):
        run = await self.prepare_run([issue_review([issue()]), issue_review([issue(status='pass', can_edit=False, fix='')])],
                                     mode='single_case')
        self.assertEqual(run['case_input_mode'], 'design_only')
        # Seed the already-confirmed legacy contract, not a new single-case request.
        historical = deepcopy(run['design_card'])
        historical['case_uses'][0]['use'] = 'generation'
        frozen = compile_design(historical, run['reference_snapshot'], run['request'],
            self.proposal['generation_defaults'], lambda raw, kind: {**run['operations'][0]['settings'], **raw},
            research=run['image_workflow'], case_input_mode='single_case')
        run.update({key: frozen[key] for key in ('design_card', 'case_input_mode', 'operations', 'image_workflow')})
        main.canvas_agent_store.mutate('canvas', run['conversation_id'], lambda r: r['runs'][0].update(deepcopy(run)))
        inputs = run['operations'][0]['generation_inputs']
        case_url = inputs[1]['url']
        self.assertEqual(inputs[1]['role'], 'composition')
        self.assertNotIn('主风格', run['operations'][0]['prompt'])
        await self.execute()
        result = (await self.settled())['result']
        self.assertEqual(result['quality']['status'], 'passed')
        self.assertEqual([m.url for m in self.image_calls[0].reference_images], ['/assets/product.png', case_url])
        self.assertEqual([m.url for m in self.image_calls[1].reference_images], ['/output/round0.png', '/assets/product.png', case_url])
        for attempt in result['attempts']:
            self.assertIn('上中下分区与细引线位置', attempt['input_roles'][-1]['purpose'])
            self.assertNotIn('主风格', attempt['input_roles'][-1]['purpose'])

    async def test_v3_input_reader_uses_frozen_operation_and_does_not_rebuild_from_current_research(self):
        plan = compile_design(card(), snapshot(), '保留产品外观，生成海报', defaults(), lambda raw, kind: raw,
                              research=research())
        plan['image_workflow']['cases'][0]['media']['url'] = '/poison-case.png'
        self.assertEqual([m['url'] for m in creative_inputs(plan, 'image1')[2]], ['/assets/product.png'])
        prompt, inputs, _ = revision_inputs(plan['image_workflow'], {'url': '/result.png', 'kind': 'image'},
                                            {'edit_prompt': '只将标题改为日常之选'})
        self.assertEqual([m['url'] for m in inputs], ['/result.png', '/assets/product.png'])
        self.assertNotIn('图1：用户主体身份参考', prompt)

    async def test_generated_image_reference_reuses_actual_image_then_checks_new_result_without_skill_search(self):
        first = await self.prepare_run([issue_review([])])
        await self.execute()
        source = (await self.settled())['result']['media'][0]
        self.canvas['nodes'].append({'id': 'prior_image', 'type': 'smart-image', 'images': [source],
            'agent': {'canvasId': 'canvas', 'conversationId': first['conversation_id'], 'runId': first['id'],
                      'operationId': 'image1', 'role': 'output'}})
        self.write_canvas()
        self.design = cycle_card()
        self.design['subject']['references'].append({'source_id': 'prior_image', 'role': 'composition'})
        self.design['appearance']['background'] = '新要求的深蓝背景'
        self.design['case_uses'] = []
        before = len(self.calls)
        created = (await self.client.post('/api/canvases/canvas/agent/conversations')).json()['conversation']
        self.base = f"/api/canvases/canvas/agent/conversations/{created['id']}"
        sent = await self.client.post(self.base + '/messages', json=message(
            message='沿用成图的构图，改为深蓝背景，保留产品外观', skill_ids=[],
            reference_node_ids=['prior_image', 'ref']).model_dump())
        self.assertEqual(sent.status_code, 200, sent.text)
        proposal = sent.json()['plan']
        self.assertEqual(len(self.calls) - before, 2, 'Reference reuse must not run a new case search')
        self.assertEqual(proposal['image_workflow']['origin'], 'image_reference')
        self.assertEqual(proposal['image_workflow']['cases'], [])
        confirmed = await self.client.post(self.base + f"/plans/{proposal['id']}/confirm", json={'version': 1, 'client_id': 'one'})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        run = confirmed.json()['run']
        self.run_url = self.base + f"/runs/{run['id']}"
        self.reviews = [issue_review([])]
        await self.execute()
        result = (await self.settled())['result']
        self.assertEqual(result['quality']['status'], 'passed')
        self.assertEqual([m.url for m in self.image_calls[-1].reference_images], [source['url'], '/assets/product.png'])
        self.assertEqual(self.calls[-1]['images'], ['/output/round1.png', source['url'], '/assets/product.png'])
        self.assertIn('新要求的深蓝背景', self.image_calls[-1].prompt)


if __name__ == '__main__':
    unittest.main()
