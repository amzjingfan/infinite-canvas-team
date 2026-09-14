"""Immutable, canvas-scoped image recipes created only by explicit user approval."""
import hashlib
import json
import os
import re
import tempfile
import time
from copy import deepcopy
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictInt, StrictStr, ValidationError

from canvas_creative import Structured, schema_validation_detail


class SaveRecipeRequest(Structured):
    name: StrictStr = Field(min_length=1, max_length=80)
    conversation_id: StrictStr = Field(pattern=r'^[a-zA-Z0-9_-]+$', max_length=100)
    run_id: StrictStr = Field(pattern=r'^[a-zA-Z0-9_-]+$', max_length=100)
    operation_id: StrictStr = Field(pattern=r'^[a-zA-Z0-9_-]+$', max_length=100)
    round: StrictInt = Field(ge=0, le=2)
    image_index: StrictInt = Field(default=0, ge=0, le=19)


REUSABLE_PATHS = {'layout': 'layout', 'background': 'appearance.background', 'palette': 'appearance.palette',
                  'lighting': 'appearance.lighting', 'typography': 'appearance.typography',
                  'aspect_ratio': 'render_options.aspect_ratio'}
OVERRIDE_LABELS = {'layout': '布局', 'background': '背景', 'palette': '色板', 'lighting': '布光',
                   'typography': '文字层级', 'aspect_ratio': '比例'}


def recipe_design(recipe, overrides=()):
    """Path/value locks only: never old product observations, copy or edit commands."""
    if len(set(overrides)) != len(overrides) or any(key not in REUSABLE_PATHS for key in overrides):
        raise HTTPException(422, '生产方案的允许变更字段无效。')
    card = recipe['design_card']
    locks = {}
    for option, path in REUSABLE_PATHS.items():
        if option in overrides:
            continue
        value = card
        for key in path.split('.'):
            value = value[key]
        locks[path] = deepcopy(value)
    locks['case_uses'] = deepcopy(card['case_uses'])
    return locks


def recipe_research(recipe, chat_provider, chat_model):
    """Frozen study provenance without prior identity, copy, constraints or prompts."""
    original = recipe.get('research', {})
    fields = ('skill_id', 'skills', 'cases', 'queries', 'corpus_total', 'corpus_commit', 'candidates',
              'unavailable', 'resource_versions', 'workflow_instructions', 'template', 'max_revisions')
    result = {key: deepcopy(original[key]) for key in fields if key in original}
    result.update(chat_provider=chat_provider, chat_model=chat_model, output='image', recipe_id=recipe['id'])
    return result


class ImageRecipeStore:
    def __init__(self, agent_store, *, resolve_media=None):
        self.agent_store, self.resolve_media = agent_store, resolve_media

    def _directory(self, canvas_id):
        return self.agent_store._directory(canvas_id) / 'recipes'

    def _read(self, canvas_id, recipe_id):
        if not re.fullmatch(r'recipe_[a-f0-9]{32}', recipe_id):
            raise HTTPException(404, '生产方案不存在')
        try:
            with (self._directory(canvas_id) / (recipe_id + '.json')).open(encoding='utf-8') as source:
                recipe = json.load(source)
        except FileNotFoundError:
            raise HTTPException(404, '生产方案不存在') from None
        if recipe.get('id') != recipe_id or recipe.get('canvas_id') != canvas_id or recipe.get('status') != 'user-approved':
            raise HTTPException(404, '生产方案不存在')
        return recipe

    def get(self, canvas_id, recipe_id):
        with self.agent_store.lock:
            canonical = self.agent_store._canvas(canvas_id)['id']
            return self._read(canonical, recipe_id)

    def list(self, canvas_id):
        with self.agent_store.lock:
            canonical = self.agent_store._canvas(canvas_id)['id']
            recipes = [self._read(canonical, path.stem) for path in self._directory(canonical).glob('recipe_*.json')]
            fields = ('id', 'name', 'created_at', 'status', 'source', 'result', 'quality', 'review', 'case_input_mode')
            return [{key: recipe[key] for key in fields} for recipe in sorted(recipes, key=lambda r: r['created_at'], reverse=True)]

    def _write(self, recipe):
        directory = self._directory(recipe['canvas_id'])
        temporary = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, suffix='.tmp', delete=False) as target:
                temporary = Path(target.name)
                json.dump(recipe, target, ensure_ascii=False, indent=2, allow_nan=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, directory / (recipe['id'] + '.json'))
        except (OSError, ValueError):
            raise HTTPException(503, '生产方案未能保存，请检查本地存储后重试；原图片和运行记录未改动。') from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, canvas_id, payload):
        try:
            payload = SaveRecipeRequest.model_validate(payload).model_dump()
        except ValidationError as exc:
            raise HTTPException(422, schema_validation_detail(exc)) from None
        if not payload['name'].strip():
            raise HTTPException(422, '请填写生产方案名称。')
        with self.agent_store.lock:
            canonical = self.agent_store._canvas(canvas_id)['id']
            source = {k: payload[k] for k in ('conversation_id', 'run_id', 'operation_id', 'round', 'image_index')}
            identity = json.dumps([canonical, source], sort_keys=True, separators=(',', ':')).encode('utf-8')
            recipe_id = 'recipe_' + hashlib.sha256(identity).hexdigest()[:32]
            try:
                return self._read(canonical, recipe_id)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            conversation = self.agent_store.get(canonical, payload['conversation_id'])
            run = next((r for r in conversation['runs'] if r['id'] == payload['run_id']), None)
            if not run or run.get('contract_version') != 3 or run.get('status') != 'completed':
                raise HTTPException(422, '只有已经完成并保存结果的新版图片任务可以保存生产方案。')
            op = next((o for o in run['operations'] if o['id'] == payload['operation_id'] and o.get('kind') == 'image'), None)
            step = next((s for s in run['steps'] if s['operation_id'] == payload['operation_id']), None)
            result = step.get('result') or {} if step else {}
            if not op or not step or step['status'] != 'completed' or result.get('quality', {}).get('phase') != 'finished':
                raise HTTPException(422, '此图片任务尚未完成，不能保存为生产方案。')
            attempt = next((a for a in result.get('attempts', []) if a['round'] == payload['round']), None)
            if not attempt or payload['image_index'] >= len(attempt.get('media', [])):
                raise HTTPException(422, '指定的图片版本不存在。')
            media = attempt['media'][payload['image_index']]
            if media.get('kind') != 'image' or media not in result.get('media', []):
                raise HTTPException(422, '指定图片不是该任务已保存的结果。')
            path = self.resolve_media(media['url']) if self.resolve_media else None
            if not path or not Path(path).is_file() or Path(path).stat().st_size == 0:
                raise HTTPException(422, '指定图片文件尚未落盘或已丢失，不能保存生产方案。')
            digest = hashlib.sha256()
            with Path(path).open('rb') as image:
                for chunk in iter(lambda: image.read(1024 * 1024), b''):
                    digest.update(chunk)
            context = run['image_workflow']
            recipe = {'id': recipe_id, 'canvas_id': canonical, 'name': payload['name'].strip(),
                'created_at': int(time.time() * 1000), 'status': 'user-approved', 'version': 1,
                'source': {**source, 'plan_id': run['plan_id'], 'plan_version': run['version']},
                'design_card': deepcopy(run['design_card']), 'case_input_mode': run['case_input_mode'],
                'compiler_version': run['compiler_version'], 'contract_version': 3,
                'final_prompt': attempt['prompt'], 'result': {**deepcopy(media), 'sha256': digest.hexdigest()},
                'review': deepcopy(attempt.get('review')), 'quality': deepcopy(result['quality']),
                'attempts': deepcopy(result['attempts']), 'research': deepcopy(context),
                'skills': deepcopy(context.get('skills', [])),
                'model_settings': {'image': deepcopy(op['settings']),
                    'chat': {'provider': context['chat_provider'], 'model': context['chat_model']}}}
            recipe['reusable_design'] = recipe_design(recipe)
            self._write(recipe)
            return recipe


def create_recipe_router(store, *, resolve_media=None):
    recipes = store if isinstance(store, ImageRecipeStore) else ImageRecipeStore(store, resolve_media=resolve_media)
    router = APIRouter(prefix='/api/canvases/{canvas_id}/agent/recipes')

    @router.get('')
    def list_recipes(canvas_id: str):
        return {'recipes': recipes.list(canvas_id)}

    @router.get('/{recipe_id}')
    def get_recipe(canvas_id: str, recipe_id: str):
        return {'recipe': recipes.get(canvas_id, recipe_id)}

    @router.post('')
    def save_recipe(canvas_id: str, payload: SaveRecipeRequest):
        return {'recipe': recipes.save(canvas_id, payload.model_dump())}

    return router
