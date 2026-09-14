import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import canvas_image_workflow as quality
from canvas_creative import normalize_creative_tasks
from test_canvas_creative import task, references
from test_canvas_image_workflow import review_answer


def issue(**updates):
    return {'id': 'title', 'category': 'text', 'status': 'fail', 'severity': 'hard',
            'location': '顶部标题第一个单词', 'evidence': '图中可读为 Texture，确认文案为 Textured Surface。',
            'fix': '只把顶部标题改为 Textured Surface。', 'preserve': ['保留标题字体、字号、位置和副标题'],
            'can_edit': True, **updates}


def issue_review(issues=None, score=85):
    items = [issue()] if issues is None else issues
    answer = review_answer(True, score)
    answer['issues'] = copy.deepcopy(items)
    # A generic rewrite from a model must never override precise targets.
    answer['edit_prompt'] = 'BAD GLOBAL REWRITE: replace the product, remove all tiny control markings.'
    for check in answer['checks']:
        matches = [i for i in items if i['category'] == check['category']]
        check['status'] = 'fail' if any(i['status'] == 'fail' for i in matches) else (
            'uncertain' if any(i['status'] == 'uncertain' for i in matches) else 'pass')
        if matches:
            check['detail'] = matches[0]['evidence'] or '问题未提供足够证据，暂时无法确认。'
            check['fix'] = matches[0]['fix']
    return answer


def quality_context():
    operation = normalize_creative_tasks([task()], references(), '保留产品外观，生成信息海报',
        lambda raw, kind: raw)[0]
    return {'request': '保留产品外观，生成信息海报', 'identity_rules': '以用户参考图为准',
            'exact_text': ['STALE RESEARCH WORDS'], 'direction': '产品与说明排版', 'workflow_instructions': 'Fixture skill',
            'chat_provider': 'deepseek', 'chat_model': 'vision-chat', 'cases': [],
            'identity_media': references()[0]['images'], 'user_media': references()[0]['images'],
            'input_roles': [{'url': '/assets/product.png', 'purpose': '用户主体身份参考'}],
            'generation_contract': {**operation['contract'], 'effective_prompt': operation['prompt']}}


class IssueQualityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.answer = issue_review()
        self.calls = []
        async def llm(payload):
            self.calls.append(payload)
            return {'text': json.dumps(self.answer, ensure_ascii=False)}
        self.workflow = quality.CanvasImageWorkflow(None, llm)
        self.context = quality_context()

    async def test_editable_text_is_not_hidden_by_uncertain_identity_and_no_global_rewrite_executes(self):
        self.answer = issue_review([issue(), issue(id='control', category='identity', status='uncertain',
            location='产品底部控制标识', evidence='像素太少，无法辨认文字。', fix='GUESS A NEW CONTROL LABEL')])
        result = await self.workflow.review(self.context, {'url': '/out.png', 'kind': 'image'})
        self.assertTrue(result['can_edit'])
        self.assertFalse(result['passed'])
        self.assertEqual([i['id'] for i in result['editable_issues']], ['title'])
        self.assertFalse(result['issues'][1]['can_edit'])
        self.assertIn('Textured Surface', result['edit_prompt'])
        self.assertNotIn('GUESS', result['edit_prompt'])
        self.assertNotIn('BAD GLOBAL', result['edit_prompt'])

    async def test_no_evidence_location_or_fix_and_uncertain_findings_cannot_trigger_edit(self):
        for change in ({'evidence': ''}, {'location': ' '}, {'fix': ''}, {'status': 'uncertain'}, {'can_edit': False}):
            with self.subTest(change=change):
                self.answer = issue_review([issue(**change)])
                result = await self.workflow.review(self.context, {'url': '/out.png', 'kind': 'image'})
                self.assertFalse(result['can_edit'])
                self.assertEqual(result['edit_prompt'], '')
                self.assertFalse(result['passed'])

    async def test_recheck_receives_prior_targets_full_contract_and_every_user_reference(self):
        self.context['user_media'].append({'url': '/layout.png', 'kind': 'image'})
        previous = [issue()]
        self.answer = issue_review([issue(status='pass', fix='', can_edit=False)])
        result = await self.workflow.review(self.context, {'url': '/edited.png', 'kind': 'image'}, previous_issues=previous)
        data = json.loads(self.calls[-1]['message'])
        self.assertEqual(data['previous_issues'], previous)
        self.assertIn('Textured Surface', data['generation_contract']['effective_prompt'])
        self.assertEqual(self.calls[-1]['images'], ['/edited.png', '/assets/product.png', '/layout.png'])
        self.assertTrue(result['passed'])
        self.assertEqual(self.calls[-1]['provider'], 'deepseek')

    async def test_resolved_issue_does_not_hide_unreviewed_category_or_new_regression(self):
        self.answer = issue_review([])
        self.answer['checks'][0].update(status='uncertain', detail='身份细节无法核实。')
        result = await self.workflow.review(self.context, {'url': '/out.png', 'kind': 'image'})
        self.assertFalse(result['passed'])
        self.assertFalse(result['can_edit'])

    def test_version_rank_prefers_fewer_hard_failures_then_remaining_issues_before_score(self):
        rank = getattr(quality, 'review_rank', None)
        self.assertTrue(callable(rank), 'version ranking must use issue severity before numeric score')
        hard = {**issue_review([issue()], score=99), 'passed': False}
        minor = {**issue_review([issue(id='dust',category='artifacts',severity='minor'),
                                issue(id='glare',category='composition',severity='minor')], score=65), 'passed': False}
        self.assertGreater(rank(minor), rank(hard))
        uncertain = {**issue_review([issue(status='uncertain')], score=40), 'passed': False}
        self.assertGreater(rank(uncertain), rank(minor))


if __name__ == '__main__':
    unittest.main()
