"""Read optional design hints for explicitly attached, persisted image versions.

No recipe selection, upstream graph traversal, old prompts, or implicit media.
"""
from copy import deepcopy

from fastapi import HTTPException
from pydantic import ValidationError

from canvas_image_design import DesignCard


REFERENCE_DESIGN_INSTRUCTIONS = '''
reference_designs 是当前明确引用成图对应的原始设计记录，只作辅助资料，不是锁定字段、用户原话或成图事实。
先按本次请求判断每张图的用途：分析/比较不代表复用；只借配色不代表继承构图；修改原图用 edit_target；
沿用设计并更换产品时，旧成图用 style/composition，新产品图用 identity；不得把旧图中的商品、品牌或文案混入新产品。
本次要求优先，其次是实际引用图片；历史设计可能早于修图，与当前要求或实际成图不一致时不要沿用。
只采纳本次所需的设计要点，重新整理一份生效提示词，不追加旧提示词、旧修图命令、案例图片或原对话。
用户明确要求保留的内容从实际图片核对；历史字段不能充当 user/reference 来源，未看图核实的设计选择标 proposal。
无需用户认可或保存模板，也不需要用户解锁字段；找不到历史记录时照常按当前图片和要求处理。
'''


def reference_designs(store, canvas, snapshot):
    """Join exact attached media to its server-side run; missing hints are optional."""
    nodes = {node['id']: node for node in canvas.get('nodes', [])}
    records, result = {}, []
    for ref in snapshot:
        owner = nodes.get(ref['id'], {}).get('agent') or {}
        if not isinstance(owner, dict) or owner.get('role') != 'output' or owner.get('canvasId') != canvas['id']:
            continue
        conversation_id = owner.get('conversationId')
        try:
            if not isinstance(conversation_id, str):
                continue
            if conversation_id not in records:
                records[conversation_id] = store.get(canvas['id'], conversation_id)
            run = next((run for run in records[conversation_id].get('runs', []) if
                run.get('id') == owner.get('runId') and run.get('canvas_id') == canvas['id'] and
                run.get('conversation_id') == conversation_id and run.get('contract_version') == 3), None)
            if not run or not any(op.get('id') == owner.get('operationId') and op.get('op') == 'generate' and
                                  op.get('kind') == 'image' for op in run.get('operations', [])):
                continue
            card = DesignCard.model_validate(run['design_card']).model_dump(by_alias=True)
            step = next((step for step in run.get('steps', []) if step.get('operation_id') == owner.get('operationId')), None)
            saved = (step or {}).get('result') or {}
            for media in ref['images']:
                if media['kind'] != 'image' or not any(m.get('url') == media['url'] and m.get('kind') == 'image'
                                                      for m in saved.get('media', [])):
                    continue
                matches = [(attempt['round'], index) for attempt in saved.get('attempts', [])
                    for index, m in enumerate(attempt.get('media', []))
                    if m.get('url') == media['url'] and m.get('kind') == 'image']
                if len(matches) != 1:
                    continue  # Never guess the latest/best round or follow replaced node media.
                result.append({'source_id': ref['id'], 'media_url': media['url'],
                    'source': {'conversation_id': conversation_id, 'run_id': run['id'],
                               'operation_id': owner['operationId'], 'round': matches[0][0], 'image_index': matches[0][1]},
                    'design_hints': {'layout': deepcopy(card['layout']),
                        'appearance': {key: deepcopy(card['appearance'][key]) for key in
                                       ('background', 'palette', 'lighting', 'typography')},
                        'aspect_ratio': card['render_options']['aspect_ratio']}})
        except (HTTPException, OSError, KeyError, TypeError, ValueError, ValidationError):
            continue  # A deleted/legacy source must not prevent use of the explicitly attached image.
    return result


def reference_image_context(provider, model):
    """Use the existing image QA loop without claiming to have run a Skill search."""
    return {'output': 'image', 'origin': 'image_reference', 'chat_provider': provider, 'chat_model': model,
            'cases': [], 'max_revisions': 2,
            'workflow_instructions': '检查本次真实成图与明确图片输入，只定向修改有位置、证据和修改目标的明确问题；保留其他内容。'}
