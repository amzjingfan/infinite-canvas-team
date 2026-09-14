"""One final image design, compiled into a frozen v3 execution contract.

Research is evidence for design choices, never an implicit generation input.
This module does not persist conversations or submit image requests.
"""
import json
import re
from copy import deepcopy
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import Field, StrictInt, StrictStr, ValidationError

from canvas_creative import (Structured, Requirement, CopyPolicy, CreativeSchemaError,
    CreativeSourceError, invalid, schema_validation_detail, reference_catalog,
    resolve_reference, reference_image_order, copy_instructions, structured_once)
from canvas_creative import CreativeTask, CreativePlanResponse, CREATIVE_INSTRUCTIONS, plan_creative


COMPILER_VERSION = 'image-design/3.0'
CaseInputMode = Literal['design_only', 'single_case']
RecipeOverride = Literal['layout', 'background', 'palette', 'lighting', 'typography', 'aspect_ratio']


class SubjectReference(Structured):
    source_id: StrictStr = Field(min_length=1, max_length=2000)
    role: Literal['identity', 'style', 'composition', 'content', 'edit_target']


class Subject(Structured):
    name: StrictStr = Field(min_length=1, max_length=1000)
    references: list[SubjectReference] = Field(default_factory=list, max_length=40)
    observations: list[Requirement] = Field(default_factory=list, max_length=40)


class Layout(Structured):
    subject_count: StrictInt = Field(ge=1, le=20)
    viewpoint: StrictStr = Field(min_length=1, max_length=1000)
    placement: StrictStr = Field(min_length=1, max_length=1000)
    occupancy: StrictStr = Field(min_length=1, max_length=1000)
    pose: Literal['grounded', 'floating', 'not_applicable']
    reading_order: list[StrictStr] = Field(min_length=1, max_length=20)


class Appearance(Structured):
    background: StrictStr = Field(min_length=1, max_length=2000)
    palette: list[StrictStr] = Field(min_length=1, max_length=20)
    lighting: StrictStr = Field(min_length=1, max_length=2000)
    material_rendering: StrictStr = Field(min_length=1, max_length=2000)
    typography: StrictStr = Field(min_length=1, max_length=2000)


class CaseUse(Structured):
    case_id: StrictInt = Field(ge=1)
    use: Literal['research', 'generation']
    aspects: list[Literal['composition', 'palette', 'lighting', 'typography', 'texture']] = Field(min_length=1, max_length=5)
    adopted_features: list[StrictStr] = Field(min_length=1, max_length=20)


class RenderOptions(Structured):
    aspect_ratio: Literal['1:1', '16:9', '9:16', '4:3', '3:4', '3:2', '2:3', '21:9'] = '1:1'
    resolution: Literal['1k', '2k', '4k'] = '2k'
    quality: Literal['low', 'medium', 'high'] = 'high'


class DesignCard(Structured):
    intent: StrictStr = Field(min_length=1, max_length=2000)
    subject: Subject
    layout: Layout
    appearance: Appearance
    copy_policy: CopyPolicy = Field(default_factory=CopyPolicy, alias='copy')
    case_uses: list[CaseUse] = Field(default_factory=list, max_length=5)
    render_options: RenderOptions = Field(default_factory=RenderOptions)
    constraints: list[Requirement] = Field(default_factory=list, max_length=40)


ROLE_LABELS = {'identity': '用户主体身份参考', 'style': '用户风格参考', 'composition': '用户构图参考',
               'content': '用户内容附件', 'edit_target': '用户指定待修改素材'}
ASPECT_LABELS = {'composition': '构图', 'palette': '色板', 'lighting': '布光', 'typography': '文字层级', 'texture': '质感表现'}
POSE_LABELS = {'grounded': '静置，主体与承载面之间形成可信的接触阴影',
               'floating': '悬浮，按与承载面的距离形成投射阴影', 'not_applicable': '不适用物体摆放与承载面阴影'}
SOURCE_LABELS = {'user': '用户明确要求', 'reference': '参考观察（模型判断；与原图矛盾时以原图为准）',
                 'proposal': '设计决定', 'uncertain': '尚未确认（不得据此增改产品或编造事实）'}


def design_summary(card):
    layout, appearance = card['layout'], card['appearance']
    return (f"{card['intent']}。主体：{card['subject']['name']}；{layout['subject_count']}个主体，"
            f"{layout['viewpoint']}，{layout['placement']}，{layout['occupancy']}；{POSE_LABELS[layout['pose']]}。"
            f"背景：{appearance['background']}；色板：{'、'.join(appearance['palette'])}；"
            f"布光：{appearance['lighting']}；文字层级：{appearance['typography']}。")


def render_design(card, inputs):
    """Render once from the effective card. An empty input list omits numbering."""
    layout, appearance = card['layout'], card['appearance']
    sections = ['【定稿设计】\n' + design_summary(card) +
                '\n阅读顺序：' + ' → '.join(layout['reading_order']) +
                '\n材质表现：' + appearance['material_rendering']]
    requirements = [*card['subject']['observations'], *card['constraints']]
    if requirements:
        sections.append('【依据与约束】\n' + '\n'.join(
            f"{SOURCE_LABELS[item['source']]}：{item['text']}" for item in requirements))
    if card['case_uses']:
        sections.append('【已采纳设计要点】\n' + '\n'.join(
            f"案例 {use['case_id']}，仅借鉴{'、'.join(ASPECT_LABELS[a] for a in use['aspects'])}："
            + '；'.join(use['adopted_features']) for use in card['case_uses']) +
            '\n只应用以上已采纳属性，不复制案例商品、品牌、准确文案或未采纳的视觉元素。')
    sections.append(copy_instructions(card['copy']))
    if inputs:
        sections.append('【实际图片输入顺序与用途】\n' + '\n'.join(
            f"图{i+1}：{media['purpose']}。" for i, media in enumerate(inputs)) +
            '\n主体身份以用户原图为准；风格与构图参考不能替换主体。参考观察与原图矛盾时以原图为准。')
    return '\n\n'.join(sections)


def resolve_design_evidence(value, sources):
    """Accept exact aliases or explicitly labelled IDs, never infer from prose."""
    exact = resolve_reference(value, sources)
    if exact is not None:
        return exact
    markers = list(re.finditer(r'''(?<![\w])["']?source_id["']?\s*[:：=]\s*''', value))
    resolved = set()
    for marker in markers:
        token = re.match(r'''(?:"([^"]+)"|'([^']+)'|`([^`]+)`|([^\s,，;；。()（）\[\]{}"'`]+))''', value[marker.end():])
        if token is None:
            return None
        key = next(part for part in token.groups() if part is not None)
        if key not in sources:
            return None
        resolved.add(key)
    return next(iter(resolved)) if len(resolved) == 1 else None


def compile_design(card, snapshot, request, defaults, normalize_settings, *, research=None,
                   case_input_mode='design_only', recipe=None, recipe_overrides=(), user_context=()):
    try:
        card = DesignCard.model_validate(card).model_dump(by_alias=True)
    except ValidationError as exc:
        raise CreativeSchemaError(schema_validation_detail(exc), card) from None
    if recipe:
        from canvas_image_recipes import recipe_design
        for path, value in recipe_design(recipe, recipe_overrides).items():
            parent = card
            keys = path.split('.')
            for key in keys[:-1]:
                parent = parent[key]
            parent[keys[-1]] = deepcopy(value)
        case_input_mode = recipe['case_input_mode']
    if case_input_mode not in ('design_only', 'single_case'):
        invalid('案例输入策略无效。')
    research = deepcopy(research or {})
    cases = {case['id']: case for case in research.get('cases', [])}
    uses = card['case_uses']
    if len({use['case_id'] for use in uses}) != len(uses) or any(use['case_id'] not in cases for use in uses):
        invalid('design_card.case_uses 包含重复或未实际研究的案例。')
    enhanced = [use for use in uses if use['use'] == 'generation']
    if len(enhanced) > 1 or (enhanced and case_input_mode != 'single_case'):
        invalid('design_card.case_uses：案例默认只用于设计；只有已选择单图增强时才可将一张案例作为实际输入。')
    if case_input_mode == 'single_case' and len(enhanced) != 1:
        invalid('design_card.case_uses：已选择单图增强，须指定一张已研究案例作为实际输入。')
    if any(len(set(use['aspects'])) != len(use['aspects']) or
           any(not feature.strip() or len(feature) > 2000 for feature in use['adopted_features']) for use in uses):
        invalid('design_card.case_uses：采纳属性须唯一，具体设计要点不能为空或过长。')
    # Replace research's legacy primary/secondary label before creating any
    # executable source table. The original remains in the research record only.
    adopted_cases = []
    for use in uses:
        case = deepcopy(cases[use['case_id']])
        case['purpose'] = '；'.join(use['adopted_features'])
        adopted_cases.append(case)
    sources = {s['id']: s for s in reference_catalog(snapshot, adopted_cases)}
    supplied_text = '\n'.join([request, *user_context, *(ref['text'] for ref in snapshot if ref.get('text'))])
    roles = {ref['id']: 'identity' for ref in snapshot}
    seen = set()
    source_issues = []
    for ref in card['subject']['references']:
        key = resolve_reference(ref['source_id'], sources)
        if key is None or sources[key]['kind'] != 'attachment':
            source_issues.append('subject.references 只能绑定当前明确附件，不能将案例作为产品来源。')
            continue
        if key in seen:
            source_issues.append('subject.references 同一附件重复定义用途。')
        ref['source_id'] = key
        roles[key] = ref['role']
        seen.add(key)
    requirements = [*card['subject']['observations'], *card['constraints']]
    if len({r['id'] for r in requirements}) != len(requirements):
        invalid('design_card 的观察与约束 ID 重复。')
    policy = card['copy']
    for field, items in (('subject.observations', card['subject']['observations']),
                         ('constraints', card['constraints']), ('copy.exact_text', policy['exact_text'])):
        for i, item in enumerate(items):
            evidence = item['evidence'].strip()
            location = f'design_card.{field}[{i}].evidence'
            if item['source'] == 'user' and (not evidence or evidence not in supplied_text):
                source_issues.append(location + '：用户依据必须逐字存在于当前请求或明确文本附件中。')
            if item['source'] == 'reference':
                key = resolve_design_evidence(evidence, sources)
                if key is None:
                    source_issues.append(location + '：无法唯一对应本次真实附件或已采用研究来源。'
                        'evidence 请只填一个完整来源 ID，观察说明留在 text；可用来源为：' + '、'.join(sources) + '。')
                elif sources[key]['kind'] == 'case' and (field == 'copy.exact_text' or item.get('category') == 'identity'):
                    source_issues.append(location + '：案例不是用户产品身份、品牌或准确文案依据。')
                else:
                    item['evidence'] = key
                    if item.get('category') == 'identity' and roles.get(key) not in ('identity', 'edit_target'):
                        source_issues.append(location + '：产品观察引用了非身份附件。')
    if source_issues:
        raise CreativeSourceError(422, '\n'.join(source_issues) + '\n真实用户要求必须保留；模型设计标 proposal，不能编造来源。')
    if not policy['allow_additional_text'] and (not policy['exclusivity_evidence'].strip() or
            policy['exclusivity_evidence'] not in supplied_text):
        invalid('copy.allow_additional_text=false 须有用户禁止额外文案的原文依据。')
    if any(bad and bad in text['text'] for bad in policy['forbidden_text'] for text in policy['exact_text']):
        invalid('copy 指定文案与禁止文案冲突。')
    inputs = []
    for ref in snapshot:
        for media in ref['images']:
            if media['kind'] != 'image' or not media['url']:
                invalid('新版图片任务只支持实际可读取的图片附件。')
            role = roles[ref['id']]
            inputs.append({**deepcopy(media), 'source_id': ref['id'], 'role': role, 'purpose': ROLE_LABELS[role]})
    for use in enhanced:
        case = cases[use['case_id']]
        if case['media'].get('kind') != 'image' or not case['media'].get('url'):
            invalid('声明为实际输入的案例没有可读取图片。')
        role = 'composition' if use['aspects'] == ['composition'] else 'style'
        inputs.append({**deepcopy(case['media']), 'source_id': f"case:{case['id']}", 'role': role,
                       'purpose': f"案例 {case['id']}，仅借鉴" + '、'.join(ASPECT_LABELS[a] for a in use['aspects']) +
                       '：' + '；'.join(use['adopted_features']) + '，不复制商品、品牌或文案'})
    unique = {}
    for media in inputs:
        if media['url'] in unique and unique[media['url']]['role'] != media['role']:
            invalid('同一图片的实际输入用途矛盾。')
        unique.setdefault(media['url'], media)
    inputs = list(unique.values())
    if len(inputs) > 20:
        invalid('实际图片输入超过所选生成适配器的上限。')
    settings = normalize_settings({**defaults['image'], 'count': 1, **card['render_options']}, 'image')
    prompt = render_design(card, inputs)
    if len(prompt) > 20000:
        invalid('设计编译后的完整提示词过长，请精简设计字段中的重复内容。')
    contract = {'version': 3, 'compiler_version': COMPILER_VERSION, 'request': request,
                'design_card': deepcopy(card), 'requirements': deepcopy(requirements), 'copy': deepcopy(policy),
                'reference_roles': {media['source_id']: media['role'] for media in inputs},
                'reference_sources': deepcopy(list(sources.values())), 'generation_inputs': deepcopy(inputs)}
    operation = {'id': 'image1', 'op': 'generate', 'kind': 'image', 'prompt': prompt,
                 'visual_prompt': render_design(card, []), 'settings': settings,
                 'reference_node_ids': [ref['id'] for ref in snapshot],
                 'generation_inputs': inputs, 'contract': contract}
    plan = {'mode': 'creation', 'contract_version': 3, 'compiler_version': COMPILER_VERSION,
            'request': request, 'summary': design_summary(card), 'design_card': card,
            'case_input_mode': case_input_mode, 'operations': [operation], 'reference_snapshot': deepcopy(snapshot)}
    research.update({'request': request, 'output': 'image', 'case_input_mode': case_input_mode,
        'max_revisions': min(2, max(0, research.get('max_revisions', 2))),
        'cases': research.get('cases', []), 'generation_inputs': deepcopy(inputs),
        'identity_media': [deepcopy(m) for m in inputs if m['role'] in ('identity', 'edit_target')],
        'user_media': [deepcopy(m) for m in inputs if not m['source_id'].startswith('case:')],
        'input_roles': [{'url': m['url'], 'purpose': m['purpose']} for m in inputs],
        'exact_text': [item['text'] for item in policy['exact_text']],
        'direction': render_design(card, []),
        'identity_rules': '主体身份以当前用户原图为准；不确定产品细节不能猜测增改。',
        'generation_contract': {**deepcopy(contract), 'effective_prompt': prompt}})
    plan['image_workflow'] = research
    if recipe:
        plan.update(recipe_id=recipe['id'], recipe_overrides=list(recipe_overrides),
                    recipe_source={key: deepcopy(recipe[key]) for key in ('id', 'name', 'source', 'created_at', 'version')})
    return plan


class DesignResponse(Structured):
    kind: Literal['chat', 'plan']
    reply: StrictStr | None = Field(default=None, min_length=1, max_length=20000)
    design_card: DesignCard | None = None


class ReferencedDesignResponse(DesignResponse):
    summary: StrictStr | None = None
    tasks: list[CreativeTask] | None = None


class DesignIssue(Structured):
    id: StrictStr = Field(min_length=1, max_length=80)
    kind: Literal['conflict', 'suggestion', 'uncertain']
    paths: list[StrictStr] = Field(min_length=1, max_length=20)
    evidence: StrictStr = Field(min_length=8, max_length=3000)
    correction: StrictStr = Field(min_length=8, max_length=3000)


class DesignPreflight(Structured):
    issues: list[DesignIssue] = Field(max_length=20)


class FieldChange(Structured):
    path: StrictStr = Field(min_length=1, max_length=300)
    value: Any


class DesignPatch(Structured):
    changes: list[FieldChange] = Field(min_length=1, max_length=40)


DESIGN_INSTRUCTIONS = '''IMAGE_DESIGN:plan
根据当前用户要求、明确附件与提供的设计研究资料（如有），作出一份可执行的单图定稿设计。
生成型回复只返回 kind=plan 和 design_card，不返回 reply、summary、tasks、操作 ID、provider/model/size。
纯讨论或只要提示词时只返回 kind=chat 和 reply，不返回设计卡或执行计划；research.output=prompt 时不得生成执行计划。
用户当前要求优先于历史。历史已确认设计是待续用资料，不是用户原文；未确认助手提议不是用户要求。
references 只能引用当前明确附件，不继承连线、上游或媒体旧提示词；所有用户图片会保留，未分配用途的图片默认是身份参考。
subject.references 用 reference_sources 中附件的 source_id 及明确 role；产品身份以真实用户原图为准，观察如有矛盾不能覆盖原图。
subject.observations 与 constraints 每项记录 source：user 必须给当前请求、相关用户消息或明确文本附件中的连续原文 evidence；
reference 的 evidence 只填 reference_sources 中一个完整 id，具体看图说明放 text；不要把“参考图第几格可见什么”当来源 ID。
如在 evidence 附加说明，必须用 source_id:完整ID 显式标注且只对应一个真实来源；不能模糊猜测或同时引用互相冲突的来源。
proposal 为本次设计；uncertain 为无法核验的事实。来源不够时不能猜品牌、材质、功效、参数或标识。
layout 是一个定稿：subject_count、单一 viewpoint、placement、occupancy、reading_order；pose 只能 grounded/floating/not_applicable 三选一，阴影由应用据此生成。
appearance 选择一套生效的 background、palette、lighting、material_rendering、typography，避免并列备选。
copy.exact_text 逐条给文字、placement、source/evidence，既可用户指定也可 proposal 拟定；allow_additional_text 默认 true，只有用户明确禁止补充且提供原文 exclusivity_evidence 才能 false。
case_uses 只保留真正采纳的 case_id、use、aspects、adopted_features。案例只能提供构图、色板、布光、文字层级、质感，不能证明产品身份、品牌或准确文案。
新的图片任务中所有案例 use=research；案例图片只供研究，不自动附加到生成、检查或修图。只有当前明确附件才是实际图片输入。
adopted_features 须具体说明本次借用什么，未采用的案例背景、颜色与元素不是本次约束。案例图与全文用于研究，不默认传给生图。
render_options 只含允许枚举的 aspect_ratio、resolution、quality；缺省 1:1、2k、high；应用保持用户所选模型、计算尺寸并固定生成一张。
用户附件是拼贴或多视角本身不是冲突，不据此拒绝任务。正常规划后由应用复核和等待用户确认，不声称已生成或实际图片已合格。
'''

DESIGN_REVIEW_INSTRUCTIONS = '''IMAGE_DESIGN:preflight
复核定稿设计和应用实际编译的输入，不生成图片，不另选风格，不增加用户需求。
仅返回 issues，每项含 id、kind、paths、evidence、correction；paths 使用真实 design_card 内路径，例如 appearance.background 或 copy.exact_text[0].text，不加 design_card 前缀。
conflict 只用于设计字段中真实互斥决定、明确违反用户原话或产品依据，须引用对应存在的字段和具体证据。
suggestion 是视觉建议或尚未发生的生成风险；uncertain 是当前资料无法判定且不影响按已知要求生成的情况。二者不阻断生成。
参考图是多视角/拼贴、未采用案例有别的颜色、缺少某条负面禁令，都不单独构成 conflict。检查依据是最终设计，不是案例全部内容。
默认案例仅用于研究，没传给生图不是缺失；只有 generation_inputs 声明的输入缺失才是绑定问题，不能建议改正文掩盖绑定错误。
真实用户文案、外观与来源须保留，不把模型观察当作产品事实。previous_conflicts 存在时逐项检查是否真正消除，不能仅改分类或删除问题。
未发现具体问题返回 issues=[]。此为生成前定稿复核，不代表实际成图通过。
'''

PATCH_INSTRUCTIONS = '''IMAGE_DESIGN:patch
只纠正本次列出的真实内容冲突，返回 changes:[{path,value}]，不要重写整份设计或提供总结。
path 必须在 allowed_paths 中，或在其中某个字段内部；每个 value 是该字段修正后的完整值。
保留其余设计和全部真实用户要求；不能删掉用户要求、准确文案或变更其来源来通过复核。
同一字段只提交一次，不提交互相覆盖的父子路径。应用会局部合并、重新编译和复核。
'''


def design_history(history, *, snapshot=None, reference_based=False):
    """Only user directions and confirmed design data; no failed/proposed prose."""
    current_media = {media['url'] for ref in snapshot or [] for media in ref.get('images', [])}
    def relevant(message):
        if reference_based:
            prior_media = {media['url'] for ref in message.get('references', []) for media in ref.get('images', [])}
            return not prior_media or prior_media == current_media
        return True
    messages = [{'role': 'user', 'content': m['content']} for m in history.get('messages', [])
                if m.get('role') == 'user' and isinstance(m.get('content'), str) and relevant(m)]
    confirmed = [p for p in history.get('plans', []) if p.get('contract_version') == 3 and
                 (p.get('status') == 'confirmed' or any(r.get('plan_id') == p['id'] and
                    r.get('version') == p.get('version') for r in history.get('runs', [])))]
    if confirmed and not reference_based:
        messages.append({'role': 'assistant', 'content': json.dumps(
            {'confirmed_design': confirmed[-1]['design_card'], 'source': 'confirmed_design_not_user_quote'}, ensure_ascii=False)})
    return messages


def design_target(history, request):
    if not re.search(r'(?:继续|修改|调整|沿用).{0,30}(?:方案|设计)|(?:方案|设计).{0,20}(?:继续|修改|调整)|\b(?:revise|adjust|continue)\b.{0,40}\b(?:design|plan)\b', request, re.I):
        return None
    previous = next((p for p in reversed(history.get('plans', [])) if p.get('contract_version') == 3 and
                     p.get('status') in ('proposed', 'confirmed')), None)
    if previous is None:
        return None
    return {'design_card': deepcopy(previous['design_card']), 'status': previous['status'],
            'source': 'previous_proposal_not_user_quote' if previous['status'] == 'proposed' else 'confirmed_design_not_user_quote'}




def _path_parts(path):
    if not re.fullmatch(r'[a-z_]+(?:\.[a-z_]+|\[\d+\])*', path):
        invalid('复核 paths 必须指向设计卡中的有效字段。')
    return [int(index) if index else name for name, index in re.findall(r'([a-z_]+)|\[(\d+)\]', path)]


def _at_path(card, path):
    value = card
    try:
        for key in _path_parts(path):
            value = value[key]
    except (KeyError, IndexError, TypeError):
        invalid('复核 paths 引用了不存在的设计字段：' + path)
    return value


def _within(path, parent):
    return path == parent or path.startswith(parent + '.') or path.startswith(parent + '[')


def _user_requirements(card):
    return [r for r in [*card['subject']['observations'], *card['constraints'], *card['copy']['exact_text']]
            if r['source'] == 'user']


def apply_design_patch(card, patch, conflicts):
    allowed = list(dict.fromkeys(path for issue in conflicts for path in issue['paths']))
    updated, seen = deepcopy(card), []
    for change in patch['changes']:
        path = change['path']
        _at_path(card, path)
        if not any(_within(path, parent) for parent in allowed) or any(
                _within(path, other) or _within(other, path) for other in seen):
            invalid('changes.path 超出明确冲突字段或互相覆盖；其余定稿不得重写。')
        keys = _path_parts(path)
        parent = updated
        for key in keys[:-1]:
            parent = parent[key]
        parent[keys[-1]] = deepcopy(change['value'])
        seen.append(path)
    try:
        updated = DesignCard.model_validate(updated).model_dump(by_alias=True)
    except ValidationError as exc:
        raise CreativeSchemaError(schema_validation_detail(exc), patch) from None
    if any(item not in _user_requirements(updated) for item in _user_requirements(card)) or (
            not card['copy']['allow_additional_text'] and updated['copy'] != card['copy']):
        invalid('纠正不能删除、改写或降级真实用户要求与排他文案规则。')
    if any(all(_at_path(card, path) == _at_path(updated, path) for path in issue['paths']) for issue in conflicts):
        invalid('原冲突字段未发生修改，不能只改分类或删除问题来通过复核。')
    return updated


async def plan_image_design(run_llm, payload, snapshot, request, normalize_settings, *, research,
                            diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else []
    defaults = payload['generation_defaults']
    user_context = [m['content'] for m in payload.get('messages', []) if m['role'] == 'user']
    sources = reference_catalog(snapshot, [dict(c, purpose='研究资料，实际用途由设计卡决定') for c in research.get('cases', [])])
    images = list(dict.fromkeys([*payload['images'], *(c['media']['url'] for c in research.get('cases', []))]))
    # The retired single-case control (including its text shortcut) cannot add
    # implicit inputs to new image requests. Existing frozen runs are untouched.
    case_input_mode = 'design_only'
    # Full selected cases and template are design-stage evidence only. Neither
    # legacy primary labels nor the whole canonical skill are system instructions.
    study = {key: deepcopy(research[key]) for key in ('output', 'template', 'template_text', 'observations') if key in research}
    study['cases'] = [{k: v for k, v in c.items() if k != 'purpose'} for c in research.get('cases', [])]
    data = {'request': request, 'attachments': snapshot, 'reference_sources': sources,
            'image_order': reference_image_order(sources, images), 'research': study,
            'case_input_mode': case_input_mode,
            'generation_defaults': defaults}
    attached_designs = payload.get('reference_designs', [])
    reference_only = research.get('origin') == 'image_reference'
    if attached_designs:
        data['reference_designs'] = deepcopy(attached_designs)
    if payload.get('design_target'):
        data['design_target'] = deepcopy(payload['design_target'])
    system = DESIGN_INSTRUCTIONS
    if attached_designs:
        from canvas_image_context import REFERENCE_DESIGN_INSTRUCTIONS
        system += REFERENCE_DESIGN_INSTRUCTIONS
    if reference_only:
        system += ('\n本次没有调用 Skill 或检索案例，只依据当前附件和可选的原设计辅助资料。'
                   '\n单张图片的生成/修改用 kind=plan 和 design_card（case_uses=[]）；纯分析/讨论用 kind=chat 和 reply。'
                   '\n若本次要求是视频、多个素材或图片转视频任务，改用 kind=plan、reply、summary、tasks，不返回 design_card；'
                   '每个 task 遵守 Schema 的 kind、prompt、reference_node_ids、reference_roles、settings、requirements、copy。'
                   '不要因为引用了图片而把用户的视频要求改成单张图片。')
    counts = {'planning': 0, 'preflight': 0}
    repairs = {'planning': 0, 'preflight': 0}
    total_calls = 0

    def record(stage, status, detail='', candidate=None):
        diagnostics.append({'stage': stage, 'attempt': counts[stage], 'status': status,
                            'detail': detail, 'candidate': deepcopy(candidate)})

    def build(design):
        return compile_design(design, snapshot, request, defaults, normalize_settings, research=research,
            case_input_mode=case_input_mode, user_context=user_context)

    async def checked_call(schema, stage, system, call_data, call_images, validate, messages=()):
        nonlocal total_calls
        call_data = deepcopy(call_data)
        while total_calls < 6:
            candidate = None
            total_calls += 1
            counts[stage] += 1
            try:
                candidate = await structured_once(run_llm, schema, payload, system, call_data, call_images, messages)
                result = validate(candidate)
                return candidate, result
            except HTTPException as exc:
                if exc.status_code != 422 or (candidate is None and not isinstance(exc, CreativeSchemaError)):
                    raise
                draft = exc.candidate if isinstance(exc, CreativeSchemaError) else candidate
                record(stage, 'rejected', str(exc.detail), draft)
                if repairs[stage]:
                    label = '来源核对' if isinstance(exc, CreativeSourceError) else '设计结构' if stage == 'planning' else '复核格式'
                    invalid(f'未生成图片：{label}纠正一次后仍未通过，草稿与诊断已保存。无需补写内部字段或改写无关创作要求。')
                repairs[stage] += 1
                call_data.update(previous_answer=draft, correction=str(exc.detail))
                system += '\n只纠正 correction 指出的内部格式、字段或来源问题，保留真实要求及其余决定。previous_answer 是失败草稿，不是新的用户要求。'
        invalid('未生成图片：设计与复核已达到六次调用上限，草稿和诊断已保存。')

    def initial(answer):
        if answer['kind'] == 'chat':
            if set(answer) != {'kind', 'reply'} or not answer['reply'].strip():
                invalid('讨论回复只应包含 kind=chat 和非空 reply。')
            return None
        if reference_only and 'tasks' in answer and 'design_card' not in answer:
            try:
                CreativePlanResponse.model_validate(answer)
            except ValidationError as exc:
                raise CreativeSchemaError(schema_validation_detail(exc), answer) from None
            if len(answer.get('tasks') or []) == 1 and answer['tasks'][0]['kind'] == 'image' and answer['tasks'][0]['settings']['count'] == 1:
                invalid('单张图片任务请返回 design_card，只有视频或多素材任务使用 tasks。')
            return None
        if set(answer) != {'kind', 'design_card'} or research.get('output') == 'prompt':
            invalid('生成型回复只返回 kind=plan 与 design_card；只要提示词时不能形成执行计划。')
        return build(answer['design_card'])

    schema = ReferencedDesignResponse if reference_only else DesignResponse
    answer, plan = await checked_call(schema, 'planning', system, data, images, initial,
                                      payload.get('messages', []))
    if reference_only and 'tasks' in answer:
        async def remaining_llm(call):
            nonlocal total_calls
            if total_calls >= 6:
                invalid('未生成素材：设计与复核已达到六次调用上限，诊断已保存。')
            total_calls += 1
            return await run_llm(call)
        from canvas_image_context import REFERENCE_DESIGN_INSTRUCTIONS
        return await plan_creative(remaining_llm,
            {**payload, 'system_prompt': CREATIVE_INSTRUCTIONS + REFERENCE_DESIGN_INSTRUCTIONS},
            snapshot, request, normalize_settings, diagnostics=diagnostics, initial_answer=answer)
    record('planning', 'passed', candidate=answer)
    if plan is None:
        return answer, None
    audit, previous_conflicts = [], []
    for content_round in range(2):
        def validate_review(checked):
            ids = [issue['id'] for issue in checked['issues']]
            if len(ids) != len(set(ids)):
                invalid('复核 issues.id 重复。')
            for issue in checked['issues']:
                for path in issue['paths']:
                    _at_path(plan['design_card'], path)
            return checked

        contract = plan['image_workflow']['generation_contract']
        check_data = {'request': request, 'user_directions': user_context, 'design_card': plan['design_card'],
                      'summary': plan['summary'], 'generation_contract': contract, 'previous_conflicts': previous_conflicts}
        checked, _ = await checked_call(DesignPreflight, 'preflight', DESIGN_REVIEW_INSTRUCTIONS, check_data,
            [m['url'] for m in plan['operations'][0]['generation_inputs']], validate_review)
        audit.append(deepcopy(checked))
        conflicts = [issue for issue in checked['issues'] if issue['kind'] == 'conflict']
        if not conflicts:
            status = 'ready_with_notes' if checked['issues'] else 'passed'
            record('preflight', status, candidate=checked)
            plan['preflight'] = {'status': status, 'issues': checked['issues'], 'attempts': audit,
                                 'provider': payload['provider'], 'model': payload['model']}
            plan['image_workflow']['generation_contract']['preflight_notes'] = deepcopy(checked['issues'])
            return {'kind': 'plan', 'reply': plan['summary'], 'summary': plan['summary'], 'design_card': plan['design_card']}, plan
        record('preflight', 'conflict', '；'.join(i['evidence'] for i in conflicts), checked)
        if content_round:
            invalid('未生成图片：定向纠正一次后仍有明确冲突：' + '；'.join(i['evidence'] for i in conflicts))
        allowed = list(dict.fromkeys(path for issue in conflicts for path in issue['paths']))
        patch_data = {'request': request, 'design_card': plan['design_card'], 'conflicts': conflicts,
                      'allowed_paths': allowed, 'reference_sources': sources, 'user_directions': user_context}
        patch, revised = await checked_call(DesignPatch, 'planning', PATCH_INSTRUCTIONS, patch_data, images,
            lambda patch: build(apply_design_patch(plan['design_card'], patch, conflicts)))
        record('planning', 'passed', candidate=patch)
        previous_conflicts, plan = conflicts, revised
    invalid('未生成图片：内部定稿处理未完成，诊断已保存。')
