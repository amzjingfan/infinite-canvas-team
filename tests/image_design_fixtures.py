"""Hand-authored product/design fixtures; no production builders for expectations."""
from copy import deepcopy


def card():
    return {
        'intent': '制作产品电商海报',
        'subject': {'name': '粉白产品', 'references': [{'source_id': 'product', 'role': 'identity'}],
                    'observations': []},
        'layout': {'subject_count': 1, 'viewpoint': '三分之四视角', 'placement': '上半部居中',
                   'occupancy': '约占一半画幅', 'pose': 'grounded',
                   'reading_order': ['产品', '标签', '底部信息']},
        'appearance': {'background': '浅米白背景', 'palette': ['浅米白', '粉色', '深灰'],
                       'lighting': '左上方柔和主光', 'material_rendering': '保留原产品表面质感',
                       'typography': '深灰现代无衬线，标题和标签层级清晰'},
        'copy': {'exact_text': [{'text': '日常之选', 'placement': '顶部标题',
                                'source': 'proposal', 'evidence': ''}],
                 'allow_additional_text': True, 'forbidden_text': [], 'exclusivity_evidence': ''},
        'case_uses': [{'case_id': 157, 'use': 'research', 'aspects': ['composition'],
                       'adopted_features': ['上中下分区与细引线位置']}],
        'render_options': {'aspect_ratio': '3:4', 'resolution': '2k', 'quality': 'high'},
        'constraints': [{'id': 'keep', 'category': 'identity', 'source': 'user',
                         'text': '保留产品外观', 'evidence': '保留产品外观'}],
    }


def snapshot():
    return [{'id': 'product', 'title': '参考图.jpg', 'type': 'smart-image', 'text': '',
             'images': [{'url': '/assets/product.png', 'kind': 'image', 'name': '参考图.jpg'}],
             'input_node_ids': []}]


def research():
    return {'skill_id': 'gpt-image-2-style-library',
            'skills': [{'id': 'gpt-image-2-style-library', 'name': '图片设计', 'version': 'fixture'}],
            'request': '保留产品外观，生成海报', 'chat_provider': 'fixture', 'chat_model': 'chat',
            'query': '商品 海报', 'output': 'image', 'max_revisions': 2,
            'identity_rules': '旧研究观察，不是执行依据', 'exact_text': [],
            'identity_media': deepcopy(snapshot()[0]['images']),
            'cases': [{'id': 157, 'title': '工业电商', 'prompt': 'UNRELATED DARK INDUSTRIAL COPY',
                       'source_url': 'https://example.test/case157', 'image_sha256': 'fixture157',
                       'media': {'url': '/assets/case157.png', 'kind': 'image', 'name': '案例157'},
                       'purpose': '主风格/构图参考'}],
            'queries': ['商品 海报'], 'corpus_total': 400, 'corpus_commit': 'fixture',
            'candidates': [{'id': 157, 'title': '工业电商', 'observation': '黑色背景橙色强调的多分区海报'}],
            'unavailable': [], 'direction': '旧主风格指令，不可追加',
            'template': '商品海报', 'template_text': '突出产品与文字层级',
            'workflow_instructions': '查看真实成图，只修明确问题。', 'resource_versions': {}}


def defaults():
    return {'image': {'provider': 'fixture', 'model': 'image'},
            'video': {'provider': 'fixture', 'model': 'video'}}


def provider(pid):
    return {'id': pid, 'protocol': 'openai', 'enabled': True,
            'chat_models': ['chat'], 'image_models': ['image'], 'video_models': ['video']}
