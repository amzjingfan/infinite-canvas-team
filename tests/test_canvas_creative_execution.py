import asyncio
import copy
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from canvas_agent import CanvasAgentExecution, ConfirmRequest, ExecuteRequest, RunEvent
from test_canvas_creative import task
import test_canvas_creative_integration as fixtures


class CreativeExecutionTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.CreativeDiscussionTests.setUp
    provider = staticmethod(fixtures.CreativeDiscussionTests.provider)
    llm = fixtures.CreativeDiscussionTests.llm
    payload = fixtures.CreativeDiscussionTests.payload
    send = fixtures.CreativeDiscussionTests.send

    async def prepare_run(self, tasks=None):
        if tasks:
            self.answer['tasks'] = tasks
        self.image_calls = []
        async def image(request):
            self.image_calls.append(copy.deepcopy(request))
            return {'images': [f'/output/result{len(self.image_calls)}.png']}
        self.execution = CanvasAgentExecution(self.store, self.provider,
            lambda kind, settings, prompt, media: {'kind': kind, 'prompt': prompt, 'media': media, 'settings': settings},
            image, image, None, None, None)
        plan = (await self.send())['plan']
        response = self.execution.confirm('canvas', self.record['id'], plan['id'], ConfirmRequest(version=1, client_id='client'))
        self.run = response['run']
        return plan

    async def generate(self, operation_id='poster'):
        await self.execution.execute('canvas', self.record['id'], self.run['id'], operation_id,
                                     ExecuteRequest(client_id='client'))
        await asyncio.gather(*list(self.execution.tasks.values()))
        return self.store.get('canvas', self.record['id'])['runs'][0]

    def acknowledge(self, operation_id, media):
        node_id = f"agent_{self.run['id']}_{operation_id}"
        self.canvas['nodes'].append({'id': node_id, 'images': copy.deepcopy(media),
            'agent': {'canvasId': 'canvas', 'conversationId': self.record['id'], 'runId': self.run['id'],
                      'operationId': operation_id, 'role': 'output'}})
        return self.execution.event('canvas', self.record['id'], self.run['id'],
            RunEvent(type='operation_completed', client_id='client', operation_id=operation_id, created_node_ids=[node_id]))['run']

    async def test_creative_run_uses_frozen_attachments_without_canvas_graph_or_display_prompt_dependency(self):
        plan = await self.prepare_run()
        self.assertEqual(self.run.get('mode'), 'creation')
        self.assertEqual(len(self.run['steps']), 1)
        self.canvas['nodes'] = [{'id': f"agent_{self.run['id']}_poster_prompt", 'type': 'smart-prompt', 'text': 'USER EDITED DISPLAY'}]
        self.canvas['connections'] = []
        run = await self.generate()
        self.assertEqual(run['steps'][0]['status'], 'generated')
        self.assertEqual(self.image_calls[0]['prompt'], plan['operations'][0]['prompt'])
        self.assertEqual([m['url'] for m in self.image_calls[0]['media']], ['/assets/product.png'])
        self.canvas['nodes'] = []
        run = self.acknowledge('poster', run['steps'][0]['result']['media'])
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(self.canvas['connections'], [])

    async def test_next_task_reads_durable_result_even_if_user_removed_its_canvas_output(self):
        await self.prepare_run([task(), task(id='detail', prompt='Make a close-up of the previous image.',
            reference_node_ids=['poster'], reference_roles={'poster': 'edit_target'})])
        self.assertEqual(self.run.get('mode'), 'creation')
        run = await self.generate()
        self.acknowledge('poster', run['steps'][0]['result']['media'])
        self.canvas['nodes'] = []; self.canvas['connections'] = []
        run = await self.generate('detail')
        self.assertEqual([m['url'] for m in self.image_calls[1]['media']], ['/output/result1.png'])
        run = self.acknowledge('detail', run['steps'][1]['result']['media'])
        self.assertEqual(run['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
