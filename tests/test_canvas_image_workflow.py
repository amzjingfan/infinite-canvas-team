import json
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from image_case_library import ImageCaseLibrary
from test_image_case_library import write_library
from canvas_agent import MessageRequest
from canvas_image_workflow import ImageIntent, QualityReview
try:
    from canvas_image_workflow import CanvasImageWorkflow
except ImportError:
    CanvasImageWorkflow = None


def intent(**updates):
    return {'query': 'perfume', 'output': 'image', 'max_revisions': 2,
            'identity_rules': '保留玻璃瓶、金色瓶盖、比例和商标。', 'exact_text': ['夏日香气'], **updates}


def selection(**updates):
    return {'primary_id': 358, 'secondary_ids': [], 'template': '产品',
            'observations': [{'id': 358, 'detail': '琥珀色背景，瓶身居中，柔和侧光，标题留白。'}],
            'direction': '保留用户产品，借用琥珀色布光与留白，不复制案例商品。', **updates}


def message(**updates):
    return MessageRequest(**{'message': '/gpt-image-2-style-library 结合参考图生成海报，检查后定向修图',
        'skill_ids': ['gpt-image-2-style-library'], 'reference_node_ids': ['ref'],
        'chat_provider': 'deepseek', 'chat_model': 'vision-chat',
        'generation_defaults': {'image': {'provider': 'tugo', 'model': 'gpt-image-2'},
                                'video': {'provider': '', 'model': ''}}, **updates})


def snapshot():
    return [{'id': 'ref', 'title': '参考图.jpg', 'text': '', 'type': 'image', 'input_node_ids': [],
             'images': [{'url': '/assets/product.png', 'kind': 'image', 'name': '参考图.jpg'}]}]


def invocation():
    return {'instructions': 'Use enhanced skill instructions.',
            'skills': [{'id': 'gpt-image-2-style-library', 'name': 'Image Skill', 'version': 'skill-version'}]}


def review_answer(passed=True, score=95):
    return {'checks': [{'category': category, 'status': 'pass' if passed or category != 'text' else 'fail',
                       'detail': '已逐项查看，主体和画面符合要求。' if passed or category != 'text' else '标题第二个字错误，应为“夏日香气”。',
                       'fix': '' if passed or category != 'text' else '只修改顶部标题为“夏日香气”，保持字重、字号、位置和其他内容。'}
                      for category in ('identity', 'text', 'composition', 'artifacts')],
            'score': score, 'summary': '主体和版式符合要求。' if passed else '标题文字需要定向修正，其他部分保持。',
            'edit_prompt': '' if passed else '保留产品、背景、布光和布局，只将顶部标题修正为“夏日香气”，保持其他内容不变。'}


class ResearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        write_library(self.root / 'skill')
        self.requests = []
        self.answers = [intent(), selection()]

    async def llm(self, payload):
        self.requests.append(payload)
        result = self.answers.pop(0)
        if isinstance(result, Exception):
            raise result
        return {'text': json.dumps(result, ensure_ascii=False)}

    def workflow(self):
        self.assertTrue(callable(CanvasImageWorkflow), 'Image skill execution adapter is not implemented')
        return CanvasImageWorkflow(lambda: ImageCaseLibrary(self.root / 'skill', self.root / 'assets'), self.llm)

    async def test_research_sends_actual_user_and_case_images_and_complete_case_text(self):
        context = await self.workflow().research(message(), snapshot(), invocation())
        self.assertEqual(self.requests[0]['images'], ['/assets/product.png'])
        self.assertIn('Product and poster templates.', self.requests[0]['system_prompt'])
        self.assertIn('full final sentence with amber.', self.requests[1]['message'])
        self.assertTrue(self.requests[1]['images'][0].startswith('data:image/'))
        self.assertEqual(context['cases'][0]['id'], 358)
        self.assertEqual(context['candidates'][0]['id'], 358)
        self.assertEqual(context['corpus_total'], 4)
        self.assertEqual(context['max_revisions'], 2)
        self.assertEqual(context['chat_model'], 'vision-chat')
        self.assertEqual(context['identity_media'][0]['url'], '/assets/product.png')
        self.assertIn('Complete product template', context['template_text'])
        self.assertIn('references/image-workflow.md', context['resource_versions'])
        self.assertNotIn('image_data', context['cases'][0])
        self.assertNotIn('data:image/', json.dumps(context))

    async def test_creative_binding_uses_final_full_copy_and_prompt_not_unapproved_research_restrictions(self):
        from canvas_creative import normalize_creative_tasks
        from canvas_agent import generation_inputs
        from test_canvas_creative import task
        workflow = self.workflow()
        self.answers = [intent(exact_text=['Invented 99:00']), selection(direction='旧研究提议：只允许两个文字。')]
        context = await workflow.research(message(), snapshot(), invocation())
        candidate = task(reference_node_ids=['ref'], reference_roles={'ref': 'identity'}, requirements=[])
        operations = normalize_creative_tasks([candidate], snapshot(), message().message,
            lambda raw, kind: {**raw, 'count': 1, 'size': '1152x2048'})
        plan = {'mode': 'creation', 'operations': operations, 'reference_snapshot': snapshot()}
        workflow.bind_plan(plan, context)
        bound = plan['image_workflow']
        prompt = generation_inputs(plan, 'poster')[1]
        self.assertNotIn('Invented 99:00', prompt)
        self.assertNotIn('只允许两个文字', prompt)
        self.assertIn('Textured Surface', prompt)
        self.assertEqual(prompt, operations[0]['prompt'])
        self.assertEqual(bound['exact_text'], ['Textured Surface'])
        self.assertTrue(bound['generation_contract']['copy']['allow_additional_text'])
        self.assertEqual([m['url'] for m in generation_inputs(plan, 'poster')[2]],
                         ['/assets/product.png', bound['cases'][0]['media']['url']])
        self.assertEqual(context['exact_text'], ['Invented 99:00'], 'research evidence stays unchanged')

    async def test_cannot_claim_unseen_case_or_observation(self):
        for answer in (selection(primary_id=21), selection(observations=[]),
                       selection(observations=[{'id': 21, 'detail': 'imagined'}])):
            self.answers = [intent(), answer, answer]
            with self.subTest(answer=answer), self.assertRaises(HTTPException):
                await self.workflow().research(message(), snapshot(), invocation())
        self.assertFalse((self.root / 'assets').exists())

    async def test_rewrites_empty_query_once_then_reads_matching_real_case(self):
        self.answers = [intent(query='unmatched'), {'query': 'perfume'}, selection()]
        result = await self.workflow().research(message(), snapshot(), invocation())
        self.assertEqual(result['queries'], ['unmatched', 'perfume'])
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(result['cases'][0]['id'], 358)

    async def test_empty_corpus_match_or_vision_failure_never_fabricates_research(self):
        self.answers = [intent(query='nope'), {'query': 'still_nope'}]
        with self.assertRaises(HTTPException):
            await self.workflow().research(message(), snapshot(), invocation())
        self.answers = [intent(), HTTPException(502, 'vision unavailable')]
        with self.assertRaises(HTTPException):
            await self.workflow().research(message(), snapshot(), invocation())
        self.assertFalse((self.root / 'assets').exists())

    async def test_prompt_only_and_lower_revision_budget_are_preserved(self):
        self.answers = [intent(output='prompt', max_revisions=0), selection()]
        result = await self.workflow().research(message(message='/gpt-image-2-style-library 只写提示词，不生图'), snapshot(), invocation())
        self.assertEqual(result['output'], 'prompt')
        self.assertEqual(result['max_revisions'], 0)
        self.answers = [intent(max_revisions=1), selection()]
        result = await self.workflow().research(message(message='/gpt-image-2-style-library 最多修图一轮'), snapshot(), invocation())
        self.assertEqual(result['max_revisions'], 1)

    async def test_invalid_budget_and_excessive_or_non_image_inputs_reject_before_expensive_research(self):
        self.answers = [intent(max_revisions=3), intent(max_revisions=3)]
        with self.assertRaises(HTTPException):
            await self.workflow().research(message(), snapshot(), invocation())
        for images in ([{'url': f'/assets/{i}.png', 'kind': 'image'} for i in range(7)],
                       [{'url': '/output/video.mp4', 'kind': 'video'}]):
            self.requests.clear()
            refs = snapshot(); refs[0]['images'] = images
            with self.subTest(images=images), self.assertRaises(HTTPException):
                await self.workflow().research(message(), refs, invocation())
            self.assertEqual(self.requests, [])

    async def test_review_receives_generated_pixels_identity_and_style_and_computes_pass(self):
        workflow = self.workflow()
        self.assertTrue(callable(getattr(workflow, 'review', None)), 'Actual generated-image review is not implemented')
        context = await workflow.research(message(), snapshot(), invocation())
        self.answers = [review_answer()]
        result = await workflow.review(context, {'url': '/output/generated.png', 'kind': 'image'})
        self.assertTrue(result['passed'])
        self.assertEqual(self.requests[-1]['images'][0:2], ['/output/generated.png', '/assets/product.png'])
        self.assertTrue(self.requests[-1]['images'][2].startswith('/assets/skill-references/'))
        self.assertEqual(self.requests[-1]['provider'], 'deepseek')
        self.assertEqual(self.requests[-1]['model'], 'vision-chat')

    async def test_review_cannot_pass_without_all_checks_or_edit_uncertain_guess(self):
        workflow = self.workflow()
        self.assertTrue(callable(getattr(workflow, 'review', None)), 'Actual generated-image review is not implemented')
        context = await workflow.research(message(), snapshot(), invocation())
        malformed = review_answer(); malformed['checks'].pop()
        self.answers = [malformed, malformed]
        with self.assertRaises(HTTPException):
            await workflow.review(context, {'url': '/output/generated.png', 'kind': 'image'})
        uncertain = review_answer(); uncertain['checks'][1].update(status='uncertain', detail='当前标题文字过小，无法逐字辨认。')
        self.answers = [uncertain]
        result = await workflow.review(context, {'url': '/output/generated.png', 'kind': 'image'})
        self.assertFalse(result['passed'])
        self.assertFalse(result['can_edit'])

    async def test_long_complete_case_is_not_truncated_or_rejected_by_public_message_limit(self):
        path = self.root / 'skill/upstream/data/cases.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['cases'][0]['prompt'] = 'perfume ' * 3500 + 'END_OF_COMPLETE_CASE'
        path.write_text(json.dumps(data), encoding='utf-8')
        await self.workflow().research(message(), snapshot(), invocation())
        request = self.requests[1]
        self.assertLessEqual(len(request['message']), 20000, 'internal research must obey the existing public message bound')
        self.assertTrue(request['messages'])
        self.assertIn('END_OF_COMPLETE_CASE', request['messages'][0]['content'])
        self.assertIn('perfume ' * 3500, request['messages'][0]['content'])

    async def test_prose_instead_of_internal_research_is_corrected_once_with_same_images(self):
        # The live model followed the whole skill instead of the internal stage.
        replies = ['## 案例库检索与模版选择\n当前环境没有可用的图像生成工具，请复制提示词。',
                   json.dumps(intent(), ensure_ascii=False)]
        calls = []
        async def upstream(payload):
            calls.append(payload)
            return {'text': replies.pop(0)}
        workflow = CanvasImageWorkflow(None, upstream)
        result = await workflow.structured(ImageIntent, message().model_dump(),
            'IMAGE_WORKFLOW:research\n完整技能资料：先输出可复制提示词。',
            {'request': '生成产品海报', 'references': snapshot()}, ['/assets/product.png'])
        self.assertEqual(result['query'], 'perfume')
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call['images'], ['/assets/product.png'])
            self.assertEqual(call['provider'], 'deepseek')
            self.assertEqual(call['model'], 'vision-chat')
            self.assertEqual(json.loads(call['message'])['request'], '生成产品海报')
            # Required schema is conveyed to the model, not only validated locally.
            schema = json.loads(call['system_prompt'].split('JSON Schema:\n')[-1])
            self.assertEqual(schema['properties']['output']['enum'], ['image', 'prompt'])
            self.assertEqual(schema['properties']['max_revisions']['maximum'], 2)

    async def test_structure_failures_name_stage_and_category_without_leaking_response(self):
        for schema, response, expected in (
                (ImageIntent, 'private-upstream-text', 'JSON'),
                (ImageIntent, json.dumps(intent(max_revisions=3)), 'max_revisions'),
                (QualityReview, '{}', 'checks')):
            calls = []
            async def upstream(payload):
                calls.append(payload)
                return {'text': response}
            with self.subTest(schema=schema.__name__, response=response):
                with self.assertRaises(HTTPException) as error:
                    await CanvasImageWorkflow(None, upstream).structured(schema, message().model_dump(),
                        '阶段说明', {'request': '保持原需求'}, ['/assets/product.png'])
                self.assertEqual(len(calls), 2, 'correction must stop after one additional chat call')
                self.assertIn(expected, error.exception.detail)
                self.assertIn('需求分析' if schema is ImageIntent else '成图检查', error.exception.detail)
                self.assertNotIn('private-upstream-text', error.exception.detail)

    async def test_json_markdown_wrapper_is_accepted_without_weakening_validation(self):
        replies = ['```json\n' + json.dumps(intent()) + '\n```']
        async def upstream(payload):
            return {'text': replies.pop(0)}
        result = await CanvasImageWorkflow(None, upstream).structured(ImageIntent, message().model_dump(),
            'IMAGE_WORKFLOW:research', {}, [])
        self.assertEqual(result['max_revisions'], 2)

    async def test_model_added_template_alias_does_not_discard_valid_visual_evidence(self):
        self.answers = [intent(), selection(template_category='产品', budget_override=100)]
        result = await self.workflow().research(message(), snapshot(), invocation())
        self.assertEqual(result['cases'][0]['id'], 358)
        self.assertEqual(result['template'], '产品')
        self.assertEqual(result['max_revisions'], 2)
        self.assertNotIn('template_category', result)
        self.assertNotIn('budget_override', result)
        self.assertEqual(len(self.requests), 2, 'irrelevant metadata must not trigger another model call')


if __name__ == '__main__':
    unittest.main()
