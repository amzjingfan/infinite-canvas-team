import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_canvas_image_workflow_execution as legacy
from test_canvas_image_design_execution import cycle_card
from test_canvas_image_workflow import message
from test_canvas_image_workflow_quality import issue, issue_review


def creative_answer():
    return {'kind': 'plan', 'design_card': cycle_card()}


class CreativeImageCycleTests(unittest.IsolatedAsyncioTestCase):
    setUp = legacy.ImageCycleTests.setUp
    provider = staticmethod(legacy.ImageCycleTests.provider)
    write_canvas = legacy.ImageCycleTests.write_canvas
    send = legacy.ImageCycleTests.send
    no_image = legacy.ImageCycleTests.no_image
    settled = legacy.ImageCycleTests.settled

    async def execute(self):
        response = await self.client.post(self.run_url + '/steps/image1/execute', json={'client_id': 'one'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def llm(self, payload):
        if 'IMAGE_DESIGN:preflight' in payload['system_prompt']:
            self.calls.append(payload)
            return {'text': '{"issues":[]}'}
        return await legacy.ImageCycleTests.llm(self, payload)

    async def prepare_run(self, reviews):
        self.reviews = reviews
        self.review_gate = self.image_gate = None
        self.review_started, self.image_started = asyncio.Event(), asyncio.Event()
        self.fail_image_round = None
        self.plan = creative_answer()
        sent = await self.send(message(message='/gpt-image-2-style-library 保留产品外观，生成信息海报'))
        self.assertEqual(sent.status_code, 200, sent.text)
        plan = sent.json()['plan']
        self.assertEqual(plan['mode'], 'creation')
        self.assertEqual([o['op'] for o in plan['operations']], ['generate'])
        self.confirm_url = self.base + f"/plans/{plan['id']}/confirm"
        response = await self.client.post(self.confirm_url, json={'version': 1, 'client_id': 'one'})
        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()['run']
        self.run_id = run['id']
        self.run_url = self.base + f"/runs/{run['id']}"
        return run

    async def test_edit_uses_only_explicit_targets_and_full_copy_then_stops_for_unresolved_identity(self):
        uncertain = issue(id='control', category='identity', status='uncertain', fix='GUESS CONTROL MARKINGS')
        run = await self.prepare_run([issue_review([issue(), uncertain]),
            issue_review([issue(status='pass', fix='', can_edit=False), uncertain])])
        self.review_gate = asyncio.Event()
        await self.execute()
        await asyncio.wait_for(self.review_started.wait(), 1)
        # The original canvas is no longer an execution dependency after confirmation.
        self.canvas['nodes'] = []; self.write_canvas()
        self.review_gate.set()
        step = await self.settled()
        result = step['result']
        self.assertEqual(len(self.image_calls), 2)
        self.assertEqual(result['quality']['status'], 'needs_review')
        prompt = self.image_calls[1].prompt
        for text in ('Textured Surface', 'Design for everyday use', '准确文案清单不是其他文字的禁令'):
            self.assertIn(text, prompt)
        self.assertNotIn('GUESS', prompt)
        self.assertNotIn('BAD GLOBAL', prompt)
        self.assertEqual(self.image_calls[0].prompt, run['operations'][0]['prompt'])
        self.assertEqual([i['id'] for i in result['attempts'][1]['edit_targets']], ['title'])
        review_calls = [p for p in self.calls if 'IMAGE_WORKFLOW:review' in p['system_prompt']]
        self.assertEqual([i['id'] for i in json.loads(review_calls[-1]['message'])['previous_issues']], ['title', 'control'])
        self.assertIn('/output/round0.png', review_calls[-1]['images'])
        self.assertEqual(result['quality']['generation_count'], 2)
        self.assertEqual(result['quality']['edit_count'], 1)
        self.assertEqual(self.canvas['connections'], [])

    async def test_retained_best_version_prioritizes_hard_failures_over_score_and_nonhard_issue_count(self):
        title = issue()
        dust = issue(id='dust',category='artifacts',severity='minor',evidence='右侧背景可见一个灰色斑点。',fix='清除右侧背景灰点。')
        glare = issue(id='glare',category='composition',severity='minor',evidence='左侧高光盖住一小段背景纹理。',fix='轻微压低左侧背景高光。')
        await self.prepare_run([issue_review([title],99),
            issue_review([issue(status='pass',fix='',can_edit=False),dust,glare],65),
            issue_review([title,{**dust,'status':'pass','can_edit':False},{**glare,'status':'pass','can_edit':False}],98)])
        await self.execute(); step = await self.settled()
        self.assertEqual(len(self.image_calls), 3)
        self.assertEqual(step['result']['best_url'], '/output/round1.png')
        self.assertEqual(step['result']['quality']['status'], 'limit_reached')
        self.assertEqual(step['result']['quality']['edit_count'], 2)

    async def test_real_researched_case_and_filename_sources_survive_planning_confirmation_and_review(self):
        self.reviews = [issue_review([])]
        self.review_gate = self.image_gate = None
        self.review_started, self.image_started = asyncio.Event(), asyncio.Event()
        self.fail_image_round = None
        self.plan = creative_answer()
        item = self.plan['design_card']
        item['subject']['observations'].append({'id': 'appearance', 'category': 'identity', 'source': 'reference',
                                    'text': '保留原图中的产品外观。', 'evidence': '参考图.jpg'})
        item['constraints'].append({'id': 'lighting', 'category': 'composition', 'source': 'reference',
                                    'text': '借用案例的琥珀色侧光，保留用户产品。', 'evidence': '案例358'})
        item['case_uses'][0]['aspects'].append('lighting')
        item['case_uses'][0]['adopted_features'].append('琥珀色侧光')
        sent = await self.send(message(message='/gpt-image-2-style-library 保留产品外观，生成信息海报'))
        self.assertEqual(sent.status_code, 200, sent.text)
        plan = sent.json()['plan']
        op = plan['operations'][0]
        case_url = plan['image_workflow']['cases'][0]['media']['url']
        self.assertEqual(op['reference_node_ids'], ['ref'])
        self.assertEqual(op['contract']['requirements'][-1]['evidence'], 'case:358')
        planner = [call for call in self.calls if 'IMAGE_DESIGN:plan' in call['system_prompt']]
        self.assertEqual(len(planner), 1, 'known references must be bound locally without a model correction')
        self.assertEqual(planner[0]['images'], ['/assets/product.png', case_url])
        planning_data = json.loads(planner[0]['message'])
        self.assertEqual([source['id'] for source in planning_data['reference_sources']], ['ref', 'case:358'])
        self.assertEqual(planning_data['image_order'][1]['source_ids'], ['case:358'])
        preflight = next(call for call in self.calls if 'IMAGE_DESIGN:preflight' in call['system_prompt'])
        self.assertEqual(preflight['images'], ['/assets/product.png'])
        self.assertEqual(json.loads(preflight['message'])['generation_contract']['reference_sources'][-1]['id'], 'case:358')
        self.assertEqual(self.image_calls, [])
        confirmed = await self.client.post(self.base + f"/plans/{plan['id']}/confirm",
                                           json={'version': 1, 'client_id': 'one'})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.run_id = confirmed.json()['run']['id']
        self.run_url = self.base + f"/runs/{self.run_id}"
        await self.execute()
        step = await self.settled()
        self.assertEqual(step['result']['quality']['status'], 'passed')
        self.assertEqual(len(self.image_calls), 1)
        self.assertEqual([ref.url for ref in self.image_calls[0].reference_images], ['/assets/product.png'])
        review_call = next(call for call in self.calls if 'IMAGE_WORKFLOW:review' in call['system_prompt'])
        contract = json.loads(review_call['message'])['generation_contract']
        self.assertEqual(contract['requirements'][-1]['evidence'], 'case:358')
        self.assertEqual(contract['reference_roles']['ref'], 'identity')
        self.assertNotIn('case:358', contract['reference_roles'])
        self.assertEqual(contract['design_card']['case_uses'][0]['use'], 'research')


if __name__ == '__main__':
    unittest.main()
