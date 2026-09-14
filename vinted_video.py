"""Vinted video REST adapter with durable operations and original-only downloads."""
import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException


DURATIONS = {'seedance2.0': [5, 10, 15], 'seedance2.0fast': [5, 10, 15],
             'seedance2.0mini': [5, 10], 'seedance2.5': [30]}
RATIOS = ['1:1', '3:4', '4:3', '9:16', '16:9', '21:9']


class VintedVideoService:
    def __init__(self, directory, provider, key, reference, output_path, output_url, client_factory=None):
        self.directory = Path(directory)
        self.provider, self.key, self.reference = provider, key, reference
        self.output_path, self.output_url = output_path, output_url
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=90))
        self.locks = {}

    def path(self, operation):
        if not re.fullmatch(r'[a-f0-9]{32}', operation or ''):
            raise HTTPException(400, 'Vinted 操作编号无效')
        return self.directory / (operation + '.json')

    def save(self, state):
        target = self.path(state['operation'])
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, target)

    def load(self, operation):
        try:
            return json.loads(self.path(operation).read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise HTTPException(404, '未找到 Vinted 操作记录，未重新提交生成')

    def validate(self, payload):
        model = payload.get('model')
        if (model not in DURATIONS or payload.get('duration') not in DURATIONS[model]
                or payload.get('aspect_ratio') not in RATIOS or payload.get('resolution') not in ('', '720p', None)):
            raise HTTPException(400, 'Vinted 模型、时长、比例或分辨率不支持；当前仅支持 720p')
        if not 1 <= len(str(payload.get('prompt') or '').strip()) <= 6000:
            raise HTTPException(400, 'Vinted 提示词需要 1–6000 个字符')
        images = payload.get('images') or []
        if len(images) > (30 if model == 'seedance2.5' else 9):
            raise HTTPException(400, 'Vinted 参考图片数量超过此模型上限')
        if payload.get('videos') or payload.get('audios') or any(ref.get('role') in ('first_frame', 'last_frame') for ref in images):
            raise HTTPException(400, 'Vinted 不支持参考视频、音频或专用首尾帧约束，请使用普通参考图片')
        if any(payload.get(flag) for flag in ('enhance_prompt', 'enable_upsample', 'watermark',
                                             'generate_audio', 'return_last_frame', 'multimodal', 'trusted_asset')) or payload.get('seed') is not None:
            raise HTTPException(400, 'Vinted 不支持所选附加选项，请使用 Vinted 视频参数面板')

    def credentials(self, provider_id):
        provider = self.provider(provider_id)
        base = str(provider.get('base_url') or '').rstrip('/')
        if base.endswith('/v1'):
            base = base[:-3]
        if base != 'https://vinted.cam' or provider.get('enabled') is False:
            raise HTTPException(400, 'Vinted 平台未启用或地址不是 https://vinted.cam')
        key = self.key(provider_id)
        if not key:
            raise HTTPException(400, '请在 Vinted 平台设置 API Key')
        return provider, base, key

    async def generate(self, payload):
        self.validate(payload)
        operation = payload.get('vinted_operation_id', '')
        target = self.path(operation)
        async with self.locks.setdefault(operation, asyncio.Lock()):
            provider, base, key = self.credentials(payload['provider_id'])
            if payload['model'] not in provider.get('video_models', []):
                raise HTTPException(400, 'Vinted 模型未配置')
            if target.exists():
                state = self.load(operation)
                if state['request'] != payload:
                    raise HTTPException(409, '原操作参数已冻结；请先恢复原任务，勿更换内容重试')
            else:
                state = {'operation': operation, 'provider_id': payload['provider_id'], 'base': base,
                    'key_hash': hashlib.sha256(key.encode()).hexdigest(), 'request': payload,
                    'body': {'model': payload['model'], 'prompt': payload['prompt'], 'duration': payload['duration'],
                             'ratio': payload['aspect_ratio'], 'resolution': '720p',
                             'camera_movement': 'fixed' if payload.get('camerafixed') else 'auto'},
                    'uploaded': 0, 'status': 'prepared'}
                self.save(state)
            return await self.run(state)

    async def resume(self, operation):
        async with self.locks.setdefault(operation, asyncio.Lock()):
            return await self.run(self.load(operation))

    async def run(self, state):
        operation = state['operation']
        _, base, key = self.credentials(state['provider_id'])
        if state['base'] != base or state['key_hash'] != hashlib.sha256(key.encode()).hexdigest():
            raise HTTPException(409, '请使用原 Vinted 平台地址和 API Key 恢复此任务')
        if state.get('result'):
            filename = 'vinted_' + operation + '.mp4'
            if Path(self.output_path(filename)).is_file():
                return state['result']
        if state['status'] == 'failed':
            raise HTTPException(400, {'message': state.get('error', 'Vinted 生成失败'), 'terminal': True})
        headers = {'Authorization': 'Bearer ' + key, 'User-Agent': 'vinted-video-client/1.0'}
        try:
            async with self.client_factory() as client:
                async def call(method, route, body=None, idem=None):
                    request_headers = dict(headers)
                    if idem:
                        request_headers['Idempotency-Key'] = idem
                    response = await client.request(method, base + route, json=body, headers=request_headers)
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ValueError('Empty API response')
                    return data

                if not state.get('job_id'):
                    images = state['request'].get('images') or []
                    for ref in images[state['uploaded']:]:
                        value = self.reference(ref)
                        if value.startswith('data:image/'):
                            image = await call('POST', '/v1/files', {'image_b64': value})
                            if not isinstance(image.get('image_id'), str) or not image['image_id']:
                                raise ValueError('Missing image_id')
                            state['body'].setdefault('image_ids', []).append(image['image_id'])
                        elif urlsplit(value).scheme in ('http', 'https'):
                            state['body'].setdefault('image_urls', []).append(value)
                        else:
                            raise HTTPException(400, '参考图无法读取，请检查文件后恢复原操作')
                        state['uploaded'] += 1
                        self.save(state)
                    state['status'] = 'submitting'
                    self.save(state)
                    created = await call('POST', '/v1/videos', state['body'], operation)
                    if not isinstance(created.get('id'), str) or not created['id'] or not re.fullmatch(r'[A-Za-z0-9_-]+', created['id']):
                        raise ValueError('Missing job id')
                    state.update(job_id=created['id'], status='queued')
                    self.save(state)
                route = '/v1/videos/' + state['job_id']
                deadline = time.monotonic() + 3600
                while time.monotonic() < deadline:
                    job = await call('GET', route)
                    status = job.get('status')
                    if status not in ('queued', 'running', 'succeeded', 'failed'):
                        raise ValueError('Unknown task status')
                    state['status'] = status
                    if status == 'failed':
                        state['error'] = str(job.get('error') or 'Vinted 视频生成失败').replace(key, '[redacted]')[:600]
                    self.save(state)
                    if status == 'failed':
                        raise HTTPException(400, {'message': state['error'], 'terminal': True})
                    if status == 'succeeded':
                        break
                    await asyncio.sleep(5)
                else:
                    raise TimeoutError('Task still pending')
                signed = await call('GET', route + '/signed_url?download=1')
                if signed.get('quality') != 'original' or not signed.get('url'):
                    raise HTTPException(409, '原画尚未就绪，已保留原任务；稍后点击查询结果，不会改用预览视频')
                url = urljoin(base + '/', signed['url'])
                if urlsplit(url).scheme != 'https' or urlsplit(url).netloc != urlsplit(base).netloc:
                    raise ValueError('Unexpected download origin')
                filename = 'vinted_' + operation + '.mp4'
                output = Path(self.output_path(filename))
                output.parent.mkdir(parents=True, exist_ok=True)
                partial = output.with_suffix('.mp4.part')
                try:
                    async with client.stream('GET', url, headers={'User-Agent': 'vinted-video-client/1.0'}, timeout=600) as response:
                        response.raise_for_status()
                        if response.headers.get('X-Video-Quality') != 'original':
                            raise ValueError('Download is not original quality')
                        size = 0
                        prefix = bytearray()
                        with partial.open('wb') as target:
                            async for block in response.aiter_bytes():
                                if len(prefix) < 16:
                                    prefix.extend(block[:16-len(prefix)])
                                size += len(block)
                                target.write(block)
                        length = response.headers.get('Content-Length')
                        if size == 0 or (length and size != int(length)) or bytes(prefix[4:8]) != b'ftyp':
                            raise ValueError('Incomplete or invalid MP4')
                    os.replace(partial, output)
                finally:
                    partial.unlink(missing_ok=True)
                state['result'] = {'videos': [self.output_url(filename)], 'task_id': state['job_id'],
                                   'vinted_operation_id': operation}
                state['status'] = 'completed'
                self.save(state)
                return state['result']
        except HTTPException:
            raise
        except (httpx.HTTPError, OSError, ValueError, KeyError) as error:
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else 502
            raise HTTPException(502, f'Vinted 请求未完成（HTTP {status}），操作 {operation} 已保存，请点击查询结果恢复；不会重新创建操作') from None
