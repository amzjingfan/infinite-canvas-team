(function(root) {
    'use strict';
    const imagePreferenceFields = ['case_input_mode','recipe_id','recipe_overrides'];
    const retiredImagePreferences = () => ({case_input_mode:'design_only',recipe_id:'',recipe_overrides:[]});
    const preferenceFields = ['draft', 'reference_node_ids', 'chat_provider', 'chat_model', 'image_provider', 'image_model', 'video_provider', 'video_model', ...imagePreferenceFields];
    const clone = value => JSON.parse(JSON.stringify(value));
    const blank = () => ({id:null, title:'新对话', messages:[], plans:[], runs:[], draft:'', reference_node_ids:[],
        chat_provider:'', chat_model:'', image_provider:'', image_model:'', video_provider:'', video_model:'',
        case_input_mode:'design_only',recipe_id:'',recipe_overrides:[]});
    const preferences = record => ({...Object.fromEntries(preferenceFields.map(key => [key, clone(record[key] ?? blank()[key])])),
        ...retiredImagePreferences()});
    const recipeOverrideLabels = {layout:'布局',background:'背景',palette:'色板',lighting:'布光',typography:'文字层级',aspect_ratio:'比例'};
    const runStatus = {idle:'未执行',ready:'待执行',submitting:'正在提交',running:'执行中',generated:'结果已返回，等待保存',
        completed:'执行完成',paused:'已暂停',failed:'失败，请制定新计划',unknown:'状态未知，请到平台核实'};

    function supportsGenerationAdapter(provider, kind, model) {
        if(kind === 'chat') return true;
        const base = String(provider.base_url || '').toLowerCase();
        if(provider.id === 'tudou' || ['ai-tudou.net','apimart.ai','apihub.agnes-ai.com'].some(host => base.includes(host))) return false;
        if(kind === 'image') return (provider.image_request_mode || 'openai') === 'openai' && !model.startsWith('agnes-image-');
        return !['lingjing','vinted'].includes(provider.id) && !['yuli.host','apistudio.vip','vinted.cam'].some(host => base.includes(host)) && !model.startsWith('agnes-video-');
    }
    function modelOptions(providers, kind) {
        return providers.filter(p => p.enabled !== false && p.has_key === true &&
            (kind === 'chat' || p.id !== 'modelscope') &&
            ((p.protocol || 'openai') === 'openai' || (kind === 'video' && p.protocol === 'autodl' && p.id === 'autodl')))
            .flatMap(p => [...new Set(p[`${kind}_models`] || [])]
                .filter(model => (p.id === 'modelscope' || !p.model_protocols?.[model] || p.model_protocols[model] === 'openai') && supportsGenerationAdapter(p, kind, model))
                .map(model =>
                ({provider:p.id, providerName:p.name || p.id, model, primary:!!p.primary})));
    }
    function resolveModel(providers, record, kind) {
        const options = modelOptions(providers, kind);
        const provider = record[`${kind}_provider`];
        const model = record[`${kind}_model`];
        if(provider && model) return {provider, model};
        const choice = options.find(item => item.provider === provider && item.model === model)
            || options.find(item => item.provider === provider)
            || options.find(item => item.primary) || options[0];
        return {provider:choice?.provider || '', model:choice?.model || ''};
    }
    function safeMediaUrl(value) {
        const url = String(value || '').trim();
        return /^(https?:\/\/|\/[^/\\]|blob:https?:\/\/|data:image\/(png|jpeg|jpg|gif|webp);base64,)/i.test(url) ? url : '';
    }
    function referenceToken(label, id) {
        return `@[${String(label).replace(/[\[\]\r\n]/g, '')}](node:${id})`;
    }
    function referenceName(ref, fallback) {
        const mediaName = ref?.images?.length === 1 ? String(ref.images[0].name || '').trim() : '';
        if(mediaName) return mediaName;
        const name = String(ref?.name || ref?.title || '').trim();
        return name && name !== (ref?.nodeId || ref?.id) && !['Image','Group','Prompt','Video'].includes(name) ? name : fallback;
    }
    function parseReferenceText(text) {
        const parts = [], pattern = /@\[([^\]\r\n]+)\]\(node:([a-zA-Z0-9_-]+)\)/g;
        let start = 0;
        for(const match of String(text).matchAll(pattern)) {
            if(match.index > start) parts.push({text:text.slice(start, match.index)});
            parts.push({id:match[2], label:match[1]});
            start = match.index + match[0].length;
        }
        if(start < text.length) parts.push({text:text.slice(start)});
        return parts;
    }
    function readInlineDraft(element) {
        let text = '';
        for(const child of element.childNodes) {
            if(child.nodeType === 3) text += child.textContent;
            else if(child.dataset?.referenceToken) text += child.dataset.referenceToken;
            else if(child.tagName === 'BR') text += '\n';
            else {
                if(['DIV','P'].includes(child.tagName) && text && !text.endsWith('\n')) text += '\n';
                text += readInlineDraft(child);
            }
        }
        return text;
    }
    function contextFromNodes(canvasId, nodes, ids, mediaKind = image => image.kind || 'image') {
        return {canvasId, references:[...new Set(ids)].map(id => {
            const node = nodes.find(item => item.id === id);
            if(!node) throw new Error(`引用节点已删除或不存在：${id}。请移除该引用后重试。`);
            const textNode = ['smart-prompt','prompt','text','smart-text'].includes(node.type) || (!node.type && !node.images?.length && !node.runSettings);
            return {nodeId:id, name:node.title || node.name || id, text:textNode ? String(node.text || '') : '',
                images:(node.images || []).map(img => ({url:img.url || '', kind:mediaKind(img), name:img.name || ''}))};
        })};
    }

    function generationInputs(run, target) {
        if(run.contract_version === 3) {
            const op=run.operations.find(item=>item.id===target);
            if(!op || !Array.isArray(op.generation_inputs)) throw new Error('新版任务缺少冻结的实际图片输入，请重新规划。');
            return {kind:op.kind,prompt:op.prompt,media:clone(op.generation_inputs)};
        }
        if(run.mode === 'creation') {
            const sources = new Map(run.reference_snapshot.map(ref => [ref.id,ref.images || []]));
            for(const op of run.operations) {
                const media = op.reference_node_ids.flatMap(id => {
                    if(!sources.has(id)) throw new Error('Agent 冻结附件或前序结果不存在。');
                    return sources.get(id);
                });
                if(op.id === target) {
                    const workflow = run.image_workflow;
                    const inputs = workflow ? [...workflow.identity_media,...workflow.cases.map(c=>c.media),...media] : media;
                    return {kind:op.kind,prompt:op.prompt,media:clone([...new Map(inputs.map(m=>[m.url,m])).values()])};
                }
                sources.set(op.id,run.steps?.find(s=>s.operation_id===op.id)?.result?.media || []);
            }
            throw new Error('Agent 冻结创作任务不存在。');
        }
        // Match server generation_inputs: snapshot parent order, pre-order text,
        // URL dedup (last material, first position), and generated-media boundary.
        const sources = new Map(run.reference_snapshot.map(ref => [ref.id, {
            texts:ref.text?.trim() ? [ref.text] : [], fallback:ref.prompt_fallback ? [ref.prompt_fallback] : [],
            media:(ref.images || []).map((m,index) => ({...m,nodeId:ref.id,imageIndex:index})), parents:ref.input_node_ids || []}]));
        function collect(id, seen=new Set()) {
            if(seen.has(id)) return {texts:[],fallback:[],media:[]};
            seen.add(id);
            const source = sources.get(id);
            if(!source) throw new Error('Agent 冻结依赖不存在。');
            const result = {texts:[...source.texts],fallback:[...source.fallback],media:[...source.media]};
            for(const parent of source.parents) {
                const next = collect(parent, seen);
                for(const key of ['texts','fallback','media']) result[key].push(...next[key]);
            }
            result.media = [...new Map(result.media.map(m => [m.url,m])).values()];
            return result;
        }
        for(const op of run.operations) {
            if(op.op === 'create_prompt') sources.set(op.id,{texts:[op.text],fallback:[],media:[],parents:[]});
            else if(op.op === 'create_media') sources.set(op.id,{texts:[],fallback:[],media:[],parents:[...op.reference_node_ids]});
            else if(op.op === 'connect') sources.get(op.to).parents.push(op.from);
            else if(op.op === 'generate') {
                const {texts,fallback,media} = collect(op.node);
                const prompt = (texts.length ? texts : fallback).join('\n');
                const kind = run.operations.find(item => item.id === op.node).kind;
                if(op.id === target) {
                    const workflow = run.image_workflow;
                    if(!workflow) return {kind,prompt,media};
                    const inputs = [...workflow.identity_media,...workflow.cases.map(c=>c.media),...media];
                    return {kind,prompt:prompt+'\n\n'+workflow.reference_instruction,
                        media:[...new Map(inputs.map(m=>[m.url,m])).values()]};
                }
                const outputs = run.steps?.find(s => s.operation_id === op.id)?.result?.media || [];
                sources.set(op.id,{texts:[],fallback:[prompt],parents:[],
                    media:outputs.map((m,index) => ({...m,nodeId:`agent_${run.id}_${op.id}`,imageIndex:index}))});
            }
        }
        throw new Error('Agent 冻结生成步骤不存在。');
    }

    function generationResultContext(run, target) {
        const context = generationInputs(run,target);
        const result = run.steps?.find(s=>s.operation_id===target)?.result;
        const best = result?.attempts?.find(a=>a.media.some(m=>m.url===result.best_url));
        return best ? {...context,prompt:best.prompt,media:best.input_media} : context;
    }

    function renderImageResearch(context, document) {
        const box=document.createElement('details'); box.className='agent-image-research';
        const summary=document.createElement('summary');
        const design=context.generation_contract?.version===3 ? context.generation_contract.design_card : null;
        summary.textContent=context.origin==='image_reference' ? '引用图片的原设计记录（仅辅助）' :
            `${context.recipe_id ? '冻结研究依据' : '研究依据'} · 完整案例库 ${context.corpus_total || 0} 条 · 已看图 ${context.candidates?.length || 0} 个 · ${context.template || '图片设计'}`;
        box.append(summary);
        const line=text=>{const p=document.createElement('p');p.textContent=text;box.append(p);};
        if(context.origin!=='image_reference') line(`检索词：${(context.queries || []).join(' → ')}`);
        if(context.reference_designs?.length) {
            line('已关联明确引用图片的原设计；本次要求和实际图片优先，不锁定原设计。');
            for(const ref of context.reference_designs) line(`引用成图 · 版本 ${ref.source.round+1} · 原背景：${ref.design_hints.appearance.background}`);
        }
        if(!design) line(`设计方向：${context.direction}`);
        for(const candidate of context.candidates || []) line(`研究观察 · 案例 ${candidate.id} · ${candidate.title}：${candidate.observation || ''}`);
        if(context.unavailable?.length) line(`未用于看图的案例：${context.unavailable.map(c=>`${c.id}（${c.reason}）`).join('、')}`);
        for(const selected of context.cases) {
            const use=design?.case_uses.find(item=>item.case_id===selected.id);
            line(design ? `${use?.use==='generation' ? '实际图片输入' : '研究参考'} · 案例 ${selected.id} · ${selected.title} · ${use ? use.adopted_features.join('；') : '未采纳为定稿要求'}` :
                `采用案例 ${selected.id} · ${selected.purpose} · ${selected.title}`);
            const url=safeMediaUrl(selected.media.url);
            if(url){const img=document.createElement('img');img.src=url;img.alt=`案例 ${selected.id}`;
                img.className='agent-result-preview';img.loading='lazy';box.append(img);}
            if(/^https?:\/\//i.test(selected.source_url || '')) {
                const link=document.createElement('a');link.href=selected.source_url;link.target='_blank';
                link.rel='noopener noreferrer';link.textContent=`查看案例 ${selected.id} 来源`;box.append(link);
            }
            line(`完整案例提示词：${selected.prompt}`);
        }
        return box;
    }

    function renderDesignCard(plan, document) {
        const card=plan.design_card, layout=card.layout, appearance=card.appearance;
        const box=document.createElement('details');box.className='agent-design-decisions';box.open=true;
        const title=document.createElement('summary');title.textContent='设计决定';box.append(title);
        const line=text=>{const p=document.createElement('p');p.textContent=text;box.append(p);};
        line(`${card.subject.name} · ${layout.subject_count} 个主体 · ${layout.viewpoint} · ${layout.placement} · ${layout.occupancy}`);
        line(`摆放：${{grounded:'静置 · 接触阴影',floating:'悬浮 · 投射阴影',not_applicable:'不适用'}[layout.pose]}；阅读顺序：${layout.reading_order.join(' → ')}`);
        line(`背景：${appearance.background}；色板：${appearance.palette.join('、')}；布光：${appearance.lighting}`);
        line(`材质表现：${appearance.material_rendering}；文字层级：${appearance.typography}`);
        for(const use of card.case_uses) line(`采纳案例 ${use.case_id}：${use.adopted_features.join('；')}（${use.use==='generation'?'附带原图':'仅用于设计'}）`);
        if(plan.recipe_source) line(`复用方案：${plan.recipe_source.name} · 本次允许变更：${plan.recipe_overrides?.map(k=>recipeOverrideLabels[k]).join('、') || '仅产品与文案'}`);
        return box;
    }

    function renderGenerationInputs(plan, document) {
        const box=document.createElement('details');box.className='agent-generation-inputs';box.open=true;
        const title=document.createElement('summary');
        const inputs=plan.operations.flatMap(op=>op.generation_inputs || []);
        title.textContent=`实际图片输入 · ${inputs.length} 张`;box.append(title);
        if(!inputs.length){const empty=document.createElement('p');empty.textContent='本次为文字生图，无图片输入。';box.append(empty);}
        for(const [index,input] of inputs.entries()) {
            const row=document.createElement('div');row.className='agent-input-row';
            const url=safeMediaUrl(input.url);
            if(url){const image=document.createElement('img');image.src=url;image.alt=input.name || `实际输入 ${index+1}`;image.loading='lazy';row.append(image);}
            const description=document.createElement('p');description.textContent=`图 ${index+1} · ${input.purpose} · ${input.name || input.source_id}`;
            row.append(description);box.append(row);
        }
        return box;
    }

    function renderImageQuality(result, document, stepStatus) {
        const box=document.createElement('div');box.className='agent-image-quality';
        const line=text=>{const p=document.createElement('p');p.textContent=text;box.append(p);};
        const labels={reviewing:'正在检查成图',editing:'正在定向修图',passed:'模型检查通过',
            limit_reached:'达到修图上限',needs_review:'存在无法确认的项目',review_failed:'检查未完成',
            review_interrupted:'检查已中断',stopped:'已停止后续修图',blocked:'修图条件已变化'};
        const quality=result.quality || {};
        line(`${stepStatus === 'unknown' && quality.pending_revision ? '修图返回状态待核实' : labels[quality.status] || '等待检查'}${quality.round > 0 ? ` · 第 ${quality.round} 轮修图` : ''}`);
        if(Number.isInteger(quality.generation_count)) line(`图片调用 ${quality.generation_count} 次 · 修图 ${quality.edit_count} 次 · 已保留 ${result.attempts?.length || 0} 个版本`);
        if(quality.detail) line(quality.detail);
        if(Number.isInteger(quality.best_round) && result.attempts?.length > 1) line(`推荐版本 ${quality.best_round+1} · 基于模型检查；其他版本仍保留`);
        const category={identity:'产品/主体一致性',text:'文字',composition:'构图',artifacts:'画面缺陷'};
        const status={pass:'通过',fail:'有问题',uncertain:'无法确认'};
        const severity={hard:'硬性要求',major:'主要问题',minor:'局部问题'};
        const records=[...(result.attempts || []),...(quality.pending_revision ? [{...quality.pending_revision,pending:true}] : [])];
        for(const attempt of records) {
            const details=document.createElement('details');const title=document.createElement('summary');
            title.textContent=attempt.pending ? `修图 ${attempt.round} · 已提交，尚未返回新版本` :
                `版本 ${attempt.round+1} · ${attempt.round ? `修图 ${attempt.round}` : '首次生成'} · ${attempt.review ? attempt.review.summary : '未完成检查'}`;
            details.append(title);
            const add=text=>{const p=document.createElement('p');p.textContent=text;details.append(p);};
            for(const check of attempt.review?.checks || [])
                add(`${category[check.category] || check.category} · ${status[check.status] || check.status}：${check.detail}${check.status === 'fail' && check.fix && !attempt.review.issues ? `\n修正目标：${check.fix}` : ''}`);
            for(const issue of attempt.review?.issues || []) {
                add(`问题 ${issue.id} · ${category[issue.category]} · ${status[issue.status]} · ${severity[issue.severity]}\n位置：${issue.location || '未提供'}\n证据：${issue.evidence || '未提供'}`);
                if(issue.can_edit && issue.status === 'fail') add(`可定向修改：${issue.fix}\n保留：${(issue.preserve || []).join('；')}`);
                else if(issue.status !== 'pass') add('待核验，不自动修改此项。');
            }
            for(const target of attempt.edit_targets || []) add(`本轮编辑目标 ${target.id} · ${target.location}：${target.fix}`);
            for(const [index,input] of (attempt.input_media || []).entries()) {
                const purpose=attempt.input_roles?.find(r=>r.url===input.url)?.purpose || '本版本实际参考素材';
                add(`输入 ${index+1} · ${purpose} · ${input.name || input.url}`);
                const url=safeMediaUrl(input.url);
                if(url) {const link=document.createElement('a');link.href=url;link.target='_blank';link.rel='noopener noreferrer';
                    link.textContent=`查看输入 ${index+1}`;details.append(link);}
            }
            add(`本版本实际提示词：${attempt.prompt}`);
            box.append(details);
        }
        return box;
    }

    function createController({canvasId, fetch:requestFetch, host, storage, pendingStorage}) {
        const base = `/api/canvases/${encodeURIComponent(canvasId)}/agent/conversations`;
        const storageKey = `canvas-agent:${canvasId}:active`;
        const pendingKey = `canvas-agent:${canvasId}:pending`;
        const cache = new Map();
        const dirty = new Map();
        const saves = new Map();
        const loads = new Map();
        const deleted = new Set(), deleting = new Set();
        const sending = new Map(), errors = new Map();
        const executions = new Map();
        const listeners = new Set();
        let lastProviders = [];
        let start = blank(), startEdited = false;
        let activeId = null, conversations = [], selectionVersion = 0, searchVersion = 0, editVersion = 0;
        let recoveryId = null;
        // Keep first-conversation preferences recoverable until the latest PATCH
        // succeeds, including the created ID so recovery never needs another POST.
        try {
            const pending = JSON.parse(pendingStorage?.getItem(pendingKey) || 'null');
            if(pending) {
                for(const key of preferenceFields) if(key in pending) start[key] = pending[key];
                Object.assign(start, retiredImagePreferences());
                startEdited = true;
                if(pending.id) {
                    activeId = recoveryId = pending.id;
                    cache.set(activeId, {...start, id:activeId});
                    dirty.set(activeId, ++editVersion);
                    start = blank(); startEdited = false;
                }
            }
        } catch(e) { /* Session storage may be unavailable. */ }
        const recordFor = id => id ? cache.get(id) : start;
        const getSnapshot = () => {
            const conversation = clone(recordFor(activeId) || null);
            const pending = sending.get(activeId);
            const sentMessageSaved = pending && conversation?.messages.some(message =>
                message.role === 'user' && !pending.messageIds.includes(message.id));
            for(const run of conversation?.runs || []) {
                if(executions.get(run.id)?.stop && run.status === 'running') run.status = 'paused';
            }
            return {canvasId, activeId, conversation, conversations:clone(conversations),
                runActivity:Object.fromEntries([...executions].filter(([, execution]) => execution.conversationId === activeId)
                    .map(([id, execution]) => [id, execution.resuming ? 'resuming' : execution.promise ? 'running' : 'idle'])),
                pendingMessage:pending && !sentMessageSaved ? clone(pending.message) : null,
                busy:sending.has(activeId), error:errors.get(activeId) || '', clientId:host.clientId};
        };
        const emit = () => listeners.forEach(listener => listener(getSnapshot()));
        function remember(id) { try { storage?.setItem(storageKey, id); } catch(e) { /* Storage may be unavailable in embedded browsers. */ } }
        async function request(url, method = 'GET', body) {
            if(!canvasId) throw new Error('请先打开已保存的画布。');
            const response = await requestFetch(url, {method, ...(body === undefined ? {} :
                {headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})});
            const data = await response.json();
            if(!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `对话请求失败 (${response.status})`);
            return data;
        }
        function updatePreferences(patch, id = activeId) {
            if(Object.keys(patch).some(key => !preferenceFields.includes(key))) throw new Error('只允许修改对话草稿、引用与模型偏好。');
            patch = {...patch, ...retiredImagePreferences()};
            const record = recordFor(id);
            if(!record) throw new Error('对话加载中，请稍后重试。');
            if(Object.keys(patch).some(key => /^(image|video)_(provider|model)$/.test(key) &&
                JSON.stringify(record[key]) !== JSON.stringify(patch[key]))) {
                (record.plans || []).forEach(plan => {
                    if(plan.status === 'proposed' && !record.runs.some(run => run.plan_id === plan.id)) plan.status = 'superseded';
                });
            }
            Object.assign(record, clone(patch));
            if(id) dirty.set(id, ++editVersion);
            else startEdited = true;
            if(!id || id === recoveryId) {
                try { pendingStorage?.setItem(pendingKey, JSON.stringify({...preferences(record), ...(id ? {id} : {})})); }
                catch(e) { /* Keep the in-memory draft when storage is unavailable. */ }
            }
            emit();
        }
        function acceptConversation(id, conversation) {
            if(deleted.has(id)) return;
            const local = recordFor(id);
            if(local && Number(conversation.updated_at) < Number(local.updated_at)) return;
            // Messages and plan versions are append-only. A PATCH snapshot can predate a completed reply.
            const plans = new Map(conversation.plans.map(plan => [`${plan.id}:${plan.version}`, plan]));
            for(const plan of local.plans) {
                const key = `${plan.id}:${plan.version}`;
                if(!plans.has(key)) plans.set(key, plan);
                else if(plan.status === 'superseded') plans.get(key).status = 'superseded';
            }
            const runs = new Map(local.runs.map(run => [run.id, run]));
            for(const run of conversation.runs) runs.set(run.id, run);
            cache.set(id, {...blank(), ...conversation, ...preferences(local), plans:[...plans.values()], runs:[...runs.values()],
                messages:local.messages.length > conversation.messages.length ? local.messages : conversation.messages});
            const latest = cache.get(id);
            conversations = conversations.map(item => item.id === id ? {...item, title:latest.title,
                updated_at:latest.updated_at, status:latest.runs.at(-1)?.status || 'idle'} : item);
        }
        function savePreferences(id = activeId) {
            if(!id || !dirty.has(id)) return saves.get(id) || Promise.resolve(recordFor(id));
            const version = dirty.get(id);
            const body = preferences(recordFor(id));
            const previous = saves.get(id) || Promise.resolve();
            const saving = previous.catch(() => {}).then(async () => {
                const {conversation} = await request(`${base}/${encodeURIComponent(id)}`, 'PATCH', body);
                // The server echoes the saved snapshot; local typing may already be newer.
                acceptConversation(id, conversation);
                if(dirty.get(id) === version) {
                    dirty.delete(id);
                    if(id === recoveryId) {
                        recoveryId = null;
                        try { pendingStorage?.removeItem(pendingKey); } catch(e) { /* Storage may be unavailable. */ }
                    }
                }
                emit();
                return clone(cache.get(id));
            });
            saves.set(id, saving);
            saving.then(() => { if(saves.get(id) === saving) saves.delete(id); }, () => { if(saves.get(id) === saving) saves.delete(id); });
            return saving;
        }
        async function loadConversation(id, refresh = false) {
            if(cache.has(id) && !refresh) return cache.get(id);
            if(!loads.has(id)) {
                const loading = request(`${base}/${encodeURIComponent(id)}`).then(({conversation}) => {
                    if(deleted.has(id)) return null;
                    if(cache.has(id)) acceptConversation(id, conversation);
                    else cache.set(id, {...blank(), ...conversation, ...retiredImagePreferences()});
                    emit();
                    return conversation;
                });
                loads.set(id, loading);
                loading.then(() => loads.delete(id), () => loads.delete(id));
            }
            return loads.get(id);
        }
        async function selectConversation(id) {
            if(deleted.has(id) || deleting.has(id)) return;
            const outgoingId = activeId;
            const version = ++selectionVersion;
            const saving = savePreferences(outgoingId);
            activeId = id;
            emit();
            try { await saving; }
            catch(error) {
                if(version === selectionVersion) { activeId = outgoingId; emit(); }
                throw error;
            }
            if(version !== selectionVersion) return;
            await loadConversation(id);
            if(version === selectionVersion) { remember(id); emit(); }
        }
        async function searchConversations(q = '') {
            const version = ++searchVersion;
            const data = await request(`${base}?q=${encodeURIComponent(q)}`);
            const list = data.conversations.filter(item => !deleted.has(item.id));
            if(version === searchVersion) { conversations = list; emit(); }
            return list;
        }
        async function deleteConversation(id) {
            if(deleted.has(id) || deleting.has(id)) return;
            if(sending.has(id)) throw new Error('对话正在回复，请等回复结束后再删除。');
            deleting.add(id);
            try {
                await request(`${base}/${encodeURIComponent(id)}`, 'DELETE');
                deleted.add(id);
                cache.delete(id); dirty.delete(id); errors.delete(id);
                conversations = conversations.filter(item => item.id !== id);
                if(recoveryId === id) {
                    recoveryId = null;
                    try { pendingStorage?.removeItem(pendingKey); } catch(e) { /* Storage may be unavailable. */ }
                }
                if(activeId === id) {
                    ++selectionVersion;
                    activeId = null; start = blank(); startEdited = false;
                    remember('');
                }
                emit();
            } finally { deleting.delete(id); }
        }
        async function open() {
            const version = selectionVersion;
            if(recoveryId) await loadConversation(recoveryId, true);
            const list = await searchConversations();
            if(version !== selectionVersion || activeId || startEdited || !list.length) return;
            let saved;
            try { saved = storage?.getItem(storageKey); } catch(e) { /* Use the newest conversation. */ }
            await selectConversation(list.some(item => item.id === saved) ? saved : list[0].id);
        }
        async function newConversation() {
            const outgoingId = activeId;
            if(outgoingId && outgoingId === recoveryId) return savePreferences(outgoingId);
            const startingRecord = outgoingId ? null : start;
            const version = ++selectionVersion;
            await savePreferences(outgoingId);
            const {conversation} = await request(base, 'POST', {});
            if(startingRecord && sending.has(null)) {
                sending.set(conversation.id, sending.get(null));
                sending.delete(null);
            }
            const initial = startingRecord ? preferences(startingRecord) : null;
            cache.set(conversation.id, {...blank(), ...conversation});
            conversations = [conversation, ...conversations];
            if(version === selectionVersion) { activeId = conversation.id; remember(activeId); }
            if(initial) {
                // Pending reference operations retain this object and follow its promoted ID.
                startingRecord.id = conversation.id;
                recoveryId = conversation.id;
                if(start === startingRecord) {
                    start = blank(); startEdited = false;
                }
                updatePreferences(initial, conversation.id);
                await savePreferences(conversation.id);
            }
            emit();
            return clone(cache.get(conversation.id));
        }
        async function addReferences(nodeIds) {
            const origin = recordFor(activeId);
            const ids = [...new Set(nodeIds)];
            if(!ids.length) throw new Error('请先在画布中选择要引用的节点，再点击添加引用。');
            if(!origin) throw new Error('对话加载中，请稍后重试。');
            host.getAgentCanvasContext(ids);
            if(!origin.id) startEdited = true;
            await host.ensureSaved(ids);
            host.getAgentCanvasContext(ids);
            const id = origin.id;
            updatePreferences({reference_node_ids:[...new Set([...recordFor(id).reference_node_ids, ...ids])]}, id);
            await savePreferences(id);
        }
        async function addSelectedReferences() {
            return addReferences(host.selectedReferenceIds());
        }
        async function removeReference(nodeId) {
            const id = activeId;
            updatePreferences({reference_node_ids:recordFor(id).reference_node_ids.filter(value => value !== nodeId)}, id);
            await savePreferences(id);
        }
        async function getContext(id = activeId) {
            const record = recordFor(id);
            if(!record) throw new Error('对话加载中，请稍后重试。');
            const ids = record.reference_node_ids.slice();
            host.getAgentCanvasContext(ids);
            await host.ensureSaved(ids);
            return host.getAgentCanvasContext(ids);
        }
        async function getProviders() {
            const providers = host.getProviders?.();
            lastProviders = clone(providers?.length ? providers : (await request('/api/providers')).providers);
            return lastProviders;
        }
        async function sendMessage() {
            let id = activeId;
            if(sending.has(id)) throw new Error('此对话正在发送或回复，请稍后再试。');
            const origin = recordFor(id);
            if(!origin?.draft.trim()) throw new Error('请输入消息。');
            const captured = preferences(origin);
            // Freeze what the menu showed at the click, before creating/saving a
            // first conversation can yield to global API setting changes.
            const visibleProviders = clone(lastProviders.length ? lastProviders : (host.getProviders?.() || []));
            const visibleModels = Object.fromEntries(['chat','image','video'].map(kind => [kind, resolveModel(visibleProviders, captured, kind)]));
            const token = {message:{role:'user',content:captured.draft}, messageIds:origin.messages.map(message => message.id)};
            sending.set(id, token);
            errors.delete(id);
            // The sent draft clears immediately. Subsequent typing belongs to the next turn.
            updatePreferences({draft:''}, id);
            try {
                if(!id) id = (await newConversation()).id;
                const providers = await getProviders();
                const selected = visibleModels;
                if(!modelOptions(providers, 'chat').some(choice => choice.provider === selected.chat.provider && choice.model === selected.chat.model)) {
                    throw new Error('所选对话模型不可用，请在 API 设置中配置并重新选择模型。');
                }
                const initial = {};
                for(const kind of ['chat','image','video']) for(const part of ['provider','model']) {
                    const field = `${kind}_${part}`;
                    if(!captured[field] && recordFor(id)[field] === captured[field]) initial[field] = selected[kind][part];
                }
                if(Object.keys(initial).length) updatePreferences(initial, id);
                const ids = captured.reference_node_ids;
                host.getAgentCanvasContext(ids);
                await host.ensureSaved(ids);
                host.getAgentCanvasContext(ids);
                await savePreferences(id);
                const result = await request(`${base}/${encodeURIComponent(id)}/messages`, 'POST', {
                    message:captured.draft, reference_node_ids:ids, chat_provider:selected.chat.provider, chat_model:selected.chat.model,
                    ...(root?.StudioSkills ? {skill_ids:root.StudioSkills.invokedIds(captured.draft)} : {}),
                    generation_defaults:{image:selected.image, video:selected.video},
                    ...Object.fromEntries(imagePreferenceFields.map(field=>[field,captured[field]]))});
                acceptConversation(id, result.conversation);
                // A preference edit made while planning must invalidate the frozen result locally too.
                for(const plan of cache.get(id).plans) {
                    if(plan.status === 'proposed' && ['image','video'].some(kind => {
                        const current = cache.get(id), frozen = plan.generation_defaults[kind];
                        return current[`${kind}_provider`] !== frozen.provider || current[`${kind}_model`] !== frozen.model;
                    })) plan.status = 'superseded';
                }
                conversations = conversations.map(item => item.id === id ? {...item, title:result.conversation.title} : item);
                if(ids.length) {
                    const sentReferences = new Set(ids);
                    updatePreferences({reference_node_ids:recordFor(id).reference_node_ids.filter(ref => !sentReferences.has(ref))}, id);
                    // Cleanup failure must not make an accepted message retryable.
                    try { await savePreferences(id); }
                    catch(error) { errors.set(id, '消息已发送，引用清理尚未保存，请重试保存或保持页面打开。'); }
                }
                return clone(cache.get(id));
            } catch(error) {
                // Reload the server-persisted safe error reply, keeping next-turn local preferences.
                id = id || origin.id;
                if(id) {
                    try {
                        const {conversation} = await request(`${base}/${encodeURIComponent(id)}`);
                        acceptConversation(id, conversation);
                    } catch(ignore) { /* Retain local state when the original conversation is unavailable. */ }
                }
                errors.set(id, error.message || '发送失败，请重试。');
                if(recordFor(id) && !recordFor(id).draft) updatePreferences({draft:captured.draft}, id);
                throw error;
            } finally {
                for(const [key, value] of sending) if(value === token) sending.delete(key);
                emit();
            }
        }
        const runUrl = (id, runId) => `${base}/${encodeURIComponent(id)}/runs/${encodeURIComponent(runId)}`;
        const getRun = (id, runId) => cache.get(id)?.runs.find(run => run.id === runId);
        function executionFor(id, runId) {
            if(!executions.has(runId)) executions.set(runId, {conversationId:id, stop:false, advance:false, promise:null, savedResults:new Map()});
            return executions.get(runId);
        }
        function acceptRun(id, data) {
            acceptConversation(id, data.conversation);
            const run = getRun(id, data.run.id);
            const execution = executionFor(id, run.id);
            if(run.client_id === host.clientId) host.setAgentRunState({conversationId:id, runId:run.id,
                status:execution.stop && run.status === 'running' ? 'paused' : run.status});
            emit();
            return run;
        }
        async function pollRun(runId, conversationId = activeId) {
            const data = await request(`${runUrl(conversationId, runId)}/events`);
            return clone(acceptRun(conversationId, data));
        }
        async function postEvent(id, runId, body) {
            return acceptRun(id, await request(`${runUrl(id,runId)}/events`, 'POST', {...body, client_id:host.clientId}));
        }
        function driveRun(id, runId) {
            const execution = executionFor(id, runId);
            if(execution.promise) return execution.promise;
            const driving = (async () => {
                try {
                    while(true) {
                        let run = getRun(id, runId);
                        if(!run || run.client_id !== host.clientId) return;
                        const step = run.steps.find(step => step.status !== 'completed');
                        if(!step) return;
                        const operation = run.operations.find(op => op.id === step.operation_id);
                        const args = {conversationId:id, runId, operation, clientId:host.clientId};
                        if(step.result?.media.length) {
                            // The host finalizes a surviving output directly, or
                            // restores only that output from the frozen run.
                            const fingerprint=JSON.stringify(step.result);
                            let saved=execution.savedResults.get(operation.id);
                            if(saved?.fingerprint !== fingerprint) {
                                const mapping=host.applyAgentGenerationResult({...args,result:step.result,run});
                                await host.ensureSaved();
                                saved={fingerprint,mapping};
                                execution.savedResults.set(operation.id,saved);
                            }
                            if(step.status === 'generated') {
                                await postEvent(id, runId, {type:'operation_completed', operation_id:operation.id,
                                    created_node_ids:saved.mapping.created_node_ids});
                                continue;
                            }
                            if(['unknown','failed'].includes(step.status)) return;
                        }
                        if(['submitting','running'].includes(step.status)) {
                            await new Promise(resolve => setTimeout(resolve, 1000));
                            run = await pollRun(runId, id);
                            const current = run.steps.find(s => s.operation_id === operation.id);
                            if(current.status === 'running' && current.provider_task_id && !current.error) {
                                acceptRun(id, await host.resumeCanvasAgentGeneration(args));
                            }
                            if(current.error && !(current.result?.media.length && ['unknown','failed'].includes(current.status))) return;
                            continue;
                        }
                        if(step.status !== 'ready' || execution.stop || !execution.advance || run.status !== 'running') return;
                        let mapping;
                        try { mapping = host.applyAgentCanvasOperations({...args, run, operations:[operation]}); }
                        catch(error) {
                            if(error.code === 'AGENT_DEPENDENCY_INVALID' && error.operationId === operation.id) {
                                await host.ensureSaved();
                                await postEvent(id, runId, {type:'dependency_failed', operation_id:operation.id});
                            }
                            throw error;
                        }
                        await host.ensureSaved();
                        // A stop click takes effect locally before its HTTP reply,
                        // including while a strict canvas save is pending.
                        if(execution.stop || !execution.advance || getRun(id, runId).status !== 'running') return;
                        if(operation.op === 'generate') {
                            acceptRun(id, await request(`${runUrl(id,runId)}/steps/${encodeURIComponent(operation.id)}/execute`, 'POST',
                                {client_id:host.clientId, resume_only:false}));
                        } else {
                            await postEvent(id, runId, {type:'operation_completed', operation_id:operation.id,
                                created_node_ids:mapping.created_node_ids});
                        }
                    }
                } catch(error) {
                    execution.advance = false;
                    try { await pollRun(runId, id); }
                    catch(ignore) { /* Keep cached state if recovery itself is offline. */ }
                    errors.set(id, error.message || '执行中断，请检查后显式继续。');
                    throw error;
                } finally {
                    execution.promise = null;
                    emit();
                }
            })();
            execution.promise = driving;
            emit();
            return driving;
        }
        async function confirmPlan(planId, version, conversationId = activeId) {
            const id = conversationId;
            const plan = cache.get(id)?.plans.find(plan => plan.id === planId && plan.version === version);
            if(!plan) throw new Error('计划不存在，请刷新对话。');
            const prior = cache.get(id).runs.find(run => run.plan_id === planId && run.version === version);
            await savePreferences(id);
            await host.ensureSaved(plan.mode === 'creation' ? [] : plan.reference_snapshot.map(ref => ref.id));
            const data = await request(`${base}/${encodeURIComponent(id)}/plans/${encodeURIComponent(planId)}/confirm`, 'POST',
                {version, client_id:host.clientId});
            const run = acceptRun(id, data);
            // Reconfirm is retrieval, never permission to advance a recovered run.
            if(prior || run.client_id !== host.clientId || run.status !== 'running') return clone(run);
            const execution = executionFor(id, run.id);
            execution.advance = true;
            errors.delete(id);
            return driveRun(id, run.id);
        }
        async function stopRun(runId, conversationId = activeId) {
            const id = conversationId, execution = executionFor(id, runId);
            execution.stop = true;
            execution.advance = false;
            host.setAgentRunState({conversationId:id,runId,status:'paused'});
            emit();
            acceptRun(id, await request(`${runUrl(id,runId)}/stop`, 'POST', {client_id:host.clientId}));
        }
        async function resumeRun(runId, conversationId = activeId) {
            const id = conversationId;
            const execution = executionFor(id, runId);
            if(execution.resuming) return;
            execution.resuming = true;
            emit();
            try {
                const run = await postEvent(id, runId, {type:'resume'});
                execution.stop = false;
                execution.advance = true;
                execution.savedResults.clear();
                errors.delete(id);
                const step = run.steps.find(s => ['submitting','running'].includes(s.status));
                if(step?.provider_task_id) acceptRun(id, await host.resumeCanvasAgentGeneration({conversationId:id,runId,
                    operation:run.operations.find(op => op.id === step.operation_id), clientId:host.clientId}));
            } catch(error) {
                errors.set(id, error.message || '恢复失败，请重试。');
                throw error;
            } finally {
                execution.resuming = false;
                emit();
            }
            return driveRun(id, runId);
        }
        async function restoreResults(runId, conversationId = activeId) {
            const id = conversationId, execution = executionFor(id, runId);
            if(execution.promise) return execution.promise;
            execution.advance = false;
            execution.stop = true;
            const restoring = (async () => {
                try {
                    const run = await postEvent(id, runId, {type:'restore_results'});
                    if(run.client_id !== host.clientId) return;
                    for(const step of run.steps.filter(s => s.status !== 'completed' && s.result?.media.length)) {
                        host.applyAgentGenerationResult({conversationId:id, runId, run, result:step.result,
                            operation:run.operations.find(op => op.id === step.operation_id)});
                        await host.ensureSaved();
                    }
                    errors.delete(id);
                } catch(error) {
                    errors.set(id, error.message);
                    throw error;
                } finally { execution.promise = null; emit(); }
            })();
            execution.promise = restoring;
            return restoring;
        }
        return {getSnapshot, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
            open, selectConversation, newConversation, deleteConversation, searchConversations, updatePreferences, savePreferences,
            addSelectedReferences, addReferences, removeReference, getContext, getProviders, sendMessage,
            confirmPlan, stopRun, resumeRun, restoreResults, pollRun};
    }

    function renderMessage(message, document, {conversation, controller} = {}) {
        const element = document.createElement('article');
        element.className = `agent-message${message.role === 'user' ? ' agent-message-user' : ''}`;
        element.setAttribute('aria-label', message.role === 'user' ? '你' : 'Agent');
        const appendText = text => {
            const paragraph = document.createElement('p');
            const parts = parseReferenceText(text);
            if(!parts.some(part => part.id)) paragraph.textContent = text;
            else for(const part of parts) {
                const item = document.createElement('span');
                if(!part.id) item.textContent = part.text;
                else {
                    item.className = 'agent-inline-reference';
                    const ref = message.references?.find(ref => ref.id === part.id || ref.nodeId === part.id);
                    const media = ref?.images?.find(image => image.kind === 'image' && safeMediaUrl(image.url));
                    if(media) {
                        const image = document.createElement('img'); image.src = safeMediaUrl(media.url); image.alt = ''; item.append(image);
                    }
                    const label = document.createElement('span'); label.textContent = referenceName(ref, part.label); item.append(label);
                }
                paragraph.append(item);
            }
            element.append(paragraph);
        };
        const appendImage = (url, name) => {
            const safe = safeMediaUrl(url);
            if(!safe) return;
            const image = document.createElement('img');
            image.src = safe;
            image.alt = name || '对话图片';
            image.loading = 'lazy';
            element.append(image);
        };
        if(typeof message.content === 'string') appendText(message.content);
        else if(Array.isArray(message.content)) message.content.forEach(part => {
            if(part.type === 'text') appendText(String(part.text || ''));
            if(part.type === 'image_url') appendImage(part.image_url?.url || part.image_url, part.name);
        });
        (message.images || []).forEach(image => appendImage(typeof image === 'string' ? image : image.url, image.name));
        if(message.image_research) element.append(renderImageResearch(message.image_research,document));
        const plan = conversation?.plans?.find(item => item.id === message.plan_id && item.version === message.plan_version);
        if(plan) {
            const card = document.createElement('section');
            card.className = 'agent-plan-card';
            const line = text => { const p = document.createElement('p'); p.textContent = text; card.append(p); };
            line(`计划 v${plan.version} · ${plan.summary}`);
            if(plan.mode === 'creation') {
                line('独立创作 · 提示词与结果分别展示，不创建工作流连线。展示提示词的编辑不改变已确认任务。');
                if(plan.preflight?.status === 'passed') line(`已完成生成前一致性复核 · ${plan.preflight.model}（模型检查）`);
            }
            if(plan.contract_version===3) {
                if(plan.preflight?.status==='ready_with_notes') line('已定稿，含检查关注项 · 此项是生成前复核，不代表实际成图通过。');
                card.append(renderDesignCard(plan,document),renderGenerationInputs(plan,document));
                if(plan.preflight?.issues?.length) {
                    const notes=document.createElement('details');notes.className='agent-design-notes';
                    const title=document.createElement('summary');title.textContent=`检查关注项 · ${plan.preflight.issues.length} 项`;notes.append(title);
                    for(const issue of plan.preflight.issues) {
                        const item=document.createElement('p');item.textContent=`${{suggestion:'建议',uncertain:'待核验',conflict:'冲突'}[issue.kind]} · ${issue.evidence}\n${issue.correction}`;notes.append(item);
                    }
                    card.append(notes);
                }
            }
            if(plan.image_workflow) {
                const workflow=plan.image_workflow;
                line(`增强图片流程 · 首次生成 1 张 · 最多修图 ${workflow.max_revisions} 次 · 最多图片调用 ${1+workflow.max_revisions} 次`);
                line('确认后执行成图检查，有明确问题才定向修图。关闭页面不取消已接受的闭环；停止会阻止下一次图片提交。');
                card.append(renderImageResearch(workflow,document));
            }
            for(const operation of plan.operations) {
                if(operation.op === 'create_prompt') line(`提示词 ${operation.id}：${operation.text}`);
                if(operation.op === 'create_media') line(`${operation.kind === 'image' ? '图片' : '视频'} ${operation.id} · 引用：${operation.reference_node_ids.join('、') || '无'}`);
                if(operation.op === 'connect') line(`连接：${operation.from} → ${operation.to}`);
                if(operation.op === 'generate') {
                    const s = operation.settings;
                    const label=plan.mode === 'creation' ? `${operation.kind === 'image' ? '图片' : '视频'}任务 ${operation.id}` : `生成 ${operation.node} → ${operation.id}`;
                    line(`${label}：${s.provider} · ${s.model} · ${s.count} 个 · ${s.aspect_ratio} · ${s.resolution} · 尺寸 ${s.size}${s.duration ? ` · ${s.duration} 秒` : ''}${s.quality ? ` · ${s.quality}` : ''}`);
                    if(plan.mode === 'creation') {
                        const details=document.createElement('details');details.className='agent-task-details';
                        const title=document.createElement('summary');title.textContent=`完整生效提示词与要求 · ${operation.id}`;details.append(title);
                        const add=text=>{const p=document.createElement('p');p.textContent=text;details.append(p);};
                        add(`生效提示词 ${operation.id}：${operation.prompt}`);
                        const source={user:'用户明确要求',reference:'参考观察（以原图为准）',proposal:'方案设计提议',uncertain:'尚未确认'};
                        for(const requirement of operation.contract?.requirements || [])
                            add(`${source[requirement.source]}：${requirement.text}${requirement.evidence ? `\n依据：${requirement.evidence}` : ''}`);
                        const copy=operation.contract?.copy;
                        if(copy) {
                            for(const item of copy.exact_text) add(`准确文案 · ${item.placement} · ${source[item.source]}：${item.text}`);
                            add(copy.allow_additional_text ? '允许补充文案；准确文案清单不是其他文字的禁令，不可编造事实。' : '仅允许指定文案，不添加其他文字。');
                            if(copy.forbidden_text.length) add(`禁止文案：${copy.forbidden_text.join('、')}`);
                        }
                        add(`附件 / 前序结果：${operation.reference_node_ids.join('、') || '无'}`);
                        card.append(details);
                    } else line(`生效提示词 ${operation.id}：${generationInputs(plan, operation.id).prompt}`);
                }
            }
            for(const ref of plan.reference_snapshot) {
                if(plan.contract_version===3 && ref.images.length) continue;
                line(`引用 ${ref.id} · ${ref.title}：${ref.text}${ref.images.map(m => ` · ${m.kind} ${m.name || m.url}`).join('')}`);
                if(plan.mode === 'creation') for(const media of ref.images) {
                    const url=safeMediaUrl(media.url);
                    if(media.kind === 'image' && url) {const preview=document.createElement('img');preview.src=url;preview.alt=media.name || ref.title;
                        preview.loading='lazy';preview.className='agent-result-preview';card.append(preview);}
                }
            }
            line('费用未知，确认后调用生成服务可能产生费用。');
            if(plan.operations.some(op => op.op === 'generate' && op.settings.provider === 'autodl')) line('AutoDL 参考素材会上传至 Litterbox，公开临时链接保留 72 小时。');
            const action = (label, callback, disabled=false) => {
                const button = document.createElement('button');
                button.type = 'button'; button.textContent = label; button.disabled = disabled;
                button.onclick = async () => {
                    if(button.disabled) return;
                    button.disabled = true;
                    try { await callback(); }
                    catch(error) { line(error.message || '操作失败，请重试。'); button.disabled = false; }
                };
                card.append(button);
            };
            const run = conversation.runs?.find(run => run.plan_id === plan.id && run.version === plan.version);
            if(run) {
                const progress = document.createElement('div');
                progress.className = 'agent-run-progress';
                progress.textContent = `${runStatus[run.status] || run.status} · ${run.steps.filter(s => s.status === 'completed').length}/${run.steps.length} 步`;
                card.append(progress);
                for(const step of run.steps) {
                    line(`${step.operation_id} · ${runStatus[step.status] || step.status}${step.error ? ` · ${step.error}` : ''}`);
                    if(step.result?.quality) card.append(renderImageQuality(step.result,document,step.status));
                    for(const media of step.result?.media || []) {
                        const url = safeMediaUrl(media.url);
                        if(!url) continue;
                        const attempt=step.result?.attempts?.find(a=>a.media.some(m=>m.url===media.url));
                        if(attempt) line(`版本 ${attempt.round+1}${step.result.attempts.length > 1 && media.url===step.result.best_url && Number.isInteger(step.result.quality?.best_round) ? ' · 推荐' : ''}`);
                        const preview = document.createElement(media.kind === 'video' ? 'video' : 'img');
                        preview.className = 'agent-result-preview'; preview.src = url;
                        if(media.kind === 'video') { preview.controls = true; preview.preload = 'metadata'; }
                        else { preview.alt = media.name || '生成结果'; preview.loading = 'lazy'; }
                        card.append(preview);
                        if(media.width && media.height) {
                            const size = `${media.width}x${media.height}`;
                            const requested = plan.operations.find(op => op.id === step.operation_id)?.settings?.size;
                            line(`实际尺寸 ${size}${requested && requested !== size ? `（与请求 ${requested} 不同，以返回素材为准）` : ''}`);
                        }
                    }
                }
                if(run.status === 'unknown') line('已返回素材会保留。请在生成平台核实其余任务状态；此运行不会自动重试或继续。');
                else if(!['completed','failed'].includes(run.status)) {
                    line('停止只阻止后续步骤；已提交任务仍可能继续运行和计费，结果仍会保存。');
                    const snapshot = controller?.getSnapshot?.();
                    const owner = snapshot?.clientId;
                    const activity = snapshot?.runActivity?.[run.id];
                    if(activity === 'resuming') action('正在恢复…', () => {}, true);
                    else if(run.status === 'running' && activity === 'running' && (!owner || owner === run.client_id))
                        action('停止后续步骤', () => controller.stopRun(run.id, conversation.id));
                    else action(owner && owner !== run.client_id ? '接管并继续' : '继续执行', () => controller.resumeRun(run.id, conversation.id));
                }
                if(['unknown','failed'].includes(run.status) && run.steps.some(s => s.status !== 'completed' && s.result?.media.length))
                    action('仅恢复 / 保存已返回素材', () => controller.restoreResults(run.id, conversation.id));
            } else {
                action(plan.status !== 'proposed' ? '计划已失效，请发送消息更新计划' : '确认执行',
                    () => controller.confirmPlan(plan.id, plan.version, conversation.id),
                    plan.status !== 'proposed' || typeof controller?.confirmPlan !== 'function');
            }
            element.append(card);
        }
        return element;
    }

    function mount({document, host, fetch, storage, pendingStorage}) {
        const panel = document.getElementById('agentPanel');
        if(!panel) return null;
        const byId = id => document.getElementById(id);
        const controller = createController({canvasId:host.canvasId, host, fetch, storage, pendingStorage});
        let providers = [], modelKind = 'chat', saveTimer, searchTimer, messageKey = '', lastActiveId, wasSending = false;
        const editor = byId('agentDraftEditor');
        let editorId, knownReferences = [], caretOffset = null, composing = false;
        function rememberCaret() {
            const selection = document.getSelection();
            if(!selection?.rangeCount || !editor.contains(selection.anchorNode)) return;
            const range = selection.getRangeAt(0).cloneRange();
            range.selectNodeContents(editor);
            range.setEnd(selection.anchorNode, selection.anchorOffset);
            caretOffset = readInlineDraft(range.cloneContents()).length;
        }
        function restoreCaret(offset) {
            const selection = document.getSelection(), range = document.createRange();
            let remaining = offset, found = false;
            function visit(parent) {
                for(const child of parent.childNodes) {
                    const token = child.dataset?.referenceToken;
                    const length = token ? token.length : child.nodeType === 3 ? child.textContent.length : 0;
                    if(token || child.nodeType === 3) {
                        if(remaining <= length) {
                            if(token) remaining === 0 ? range.setStartBefore(child) : range.setStartAfter(child);
                            else range.setStart(child, remaining);
                            found = true; return;
                        }
                        remaining -= length;
                    } else { visit(child); if(found) return; }
                }
            }
            visit(editor);
            if(!found) { range.selectNodeContents(editor); range.collapse(false); }
            range.collapse(true); selection.removeAllRanges(); selection.addRange(range);
        }
        function renderInlineDraft(value) {
            if(composing || readInlineDraft(editor) === value) return;
            const focused = document.activeElement === editor;
            editor.replaceChildren();
            for(const part of parseReferenceText(value)) {
                if(!part.id) { editor.append(document.createTextNode(part.text)); continue; }
                const chip = document.createElement('span');
                chip.className = 'agent-inline-reference'; chip.contentEditable = 'false';
                chip.dataset.referenceToken = referenceToken(part.label, part.id);
                let ref;
                try { ref = host.getAgentCanvasContext([part.id]).references[0]; }
                catch(error) { chip.classList.add('invalid'); }
                const media = ref?.images.find(item => item.kind === 'image' && safeMediaUrl(item.url));
                if(media) {
                    const image = document.createElement('img'); image.src = safeMediaUrl(media.url); image.alt = ''; chip.append(image);
                }
                const label = document.createElement('span'); label.textContent = part.label; chip.append(label);
                chip.title = ref ? `${part.label} · ${ref.images[0]?.name || ref.name}` : '引用节点已失效';
                editor.append(chip);
            }
            if(focused && caretOffset !== null) restoreCaret(caretOffset);
        }
        const status = (text, error = false) => {
            byId('agentStatus').textContent = text;
            byId('agentStatus').classList.toggle('error', error);
        };
        const run = async operation => {
            try { await operation(); }
            catch(error) { status(error.message || '操作失败，请重试。', true); }
        };
        function closeModels() {
            byId('agentModelPopup').hidden = true;
            byId('agentChatModel').setAttribute('aria-expanded', 'false');
            byId('agentGenerationModel').setAttribute('aria-expanded', 'false');
        }
        function renderModels() {
            const record = controller.getSnapshot().conversation || blank();
            const selected = resolveModel(providers, record, modelKind);
            byId('agentModelSelection').textContent = `当前${{chat:'对话',image:'图片',video:'视频'}[modelKind]}模型：${selected.model || '未配置'}`;
            const list = byId('agentModelList');
            list.replaceChildren();
            byId('agentModelTabs').hidden = modelKind === 'chat';
            panel.querySelectorAll('[data-agent-kind]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.agentKind === modelKind)));
            const choices = modelOptions(providers, modelKind);
            if(!choices.length) list.textContent = '暂无可用模型，请在 API 设置中配置。';
            choices.forEach(choice => {
                const button = document.createElement('button');
                button.type = 'button';
                const name = document.createElement('span');
                name.className = 'agent-model-name'; name.textContent = choice.model;
                const provider = document.createElement('span');
                provider.className = 'agent-model-provider'; provider.textContent = choice.providerName;
                button.append(name, provider);
                button.title = `${choice.providerName} · ${choice.model}`;
                button.setAttribute('aria-pressed', String(choice.provider === selected.provider && choice.model === selected.model));
                button.onclick = () => run(async () => {
                    const id = controller.getSnapshot().activeId;
                    controller.updatePreferences({[`${modelKind}_provider`]:choice.provider, [`${modelKind}_model`]:choice.model}, id);
                    await controller.savePreferences(id);
                    status(`已选择 ${choice.model}`);
                });
                list.append(button);
            });
        }
        function refresh() {
            const snapshot = controller.getSnapshot();
            const sentNow = snapshot.busy && !wasSending;
            wasSending = snapshot.busy;
            if(sentNow) byId('agentMessages').scrollTop = byId('agentMessages').scrollHeight;
            const record = snapshot.conversation;
            const available = !!record;
            editor.contentEditable = String(available);
            byId('agentTitle').textContent = record?.title || '加载对话…';
            ['agentDraft','agentAddReference','agentChatModel','agentGenerationModel'].forEach(id => { byId(id).disabled = !available; });
            const draft = byId('agentDraft');
            const ids = record?.reference_node_ids || [];
            const changedConversation = editorId !== snapshot.activeId;
            if(changedConversation) { editorId = snapshot.activeId; knownReferences = []; caretOffset = null; }
            const parts = parseReferenceText(record?.draft || '');
            const present = parts.filter(part => part.id && ids.includes(part.id));
            let textOffset = 0, caretShift = 0;
            let nextDraft = parts.map(part => {
                if(!part.id) { textOffset += part.text.length; return part.text; }
                const previous = referenceToken(part.label, part.id);
                let ref; try { ref = host.getAgentCanvasContext([part.id]).references[0]; } catch(ignore) {}
                const next = ids.includes(part.id) ? referenceToken(referenceName(ref, part.label), part.id) : '';
                textOffset += previous.length;
                if(caretOffset !== null && textOffset <= caretOffset) caretShift += next.length - previous.length;
                return next;
            }).join('');
            if(caretOffset !== null) caretOffset += caretShift;
            const added = ids.filter(id => !knownReferences.includes(id) && !present.some(part => part.id === id));
            knownReferences = ids.slice();
            if(added.length) {
                const labels = present.map(part => part.label);
                const tokens = added.map(id => {
                    let ref; try { ref = host.getAgentCanvasContext([id]).references[0]; } catch(ignore) {}
                    const kind = ref?.images[0]?.kind === 'video' ? '视频' : ref?.images.length ? '图片' : '提示词';
                    let number = 1; while(labels.includes(`${kind}${number}`)) number++;
                    const label = referenceName(ref, `${kind}${number}`); labels.push(label);
                    return referenceToken(label, id);
                }).join(' ');
                const offset = Math.min(caretOffset ?? nextDraft.length, nextDraft.length);
                nextDraft = nextDraft.slice(0, offset) + tokens + nextDraft.slice(offset);
                caretOffset = offset + tokens.length;
            }
            if(record && nextDraft !== record.draft) { controller.updatePreferences({draft:nextDraft}); return; }
            if(draft.value !== (record?.draft || '')) draft.value = record?.draft || '';
            renderInlineDraft(draft.value);
            const sendReady = typeof controller.sendMessage === 'function';
            byId('agentSend').disabled = !sendReady || !available || snapshot.busy || !record.draft.trim();
            byId('agentSend').title = snapshot.busy ? '此对话正在回复' : '发送消息';
            if(snapshot.activeId !== lastActiveId) {
                lastActiveId = snapshot.activeId;
                closeModels();
                status('描述想法，讨论计划；确认后执行。');
            }
            status(snapshot.error || '描述想法，讨论计划；确认后执行。', !!snapshot.error);
            const nextMessageKey = JSON.stringify([snapshot.activeId, record?.messages, record?.plans, record?.runs, snapshot.busy, snapshot.pendingMessage, snapshot.runActivity]);
            if(nextMessageKey !== messageKey) {
                messageKey = nextMessageKey;
                const messages = byId('agentMessages');
                const previousScroll = messages.scrollTop;
                const follow = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 60;
                messages.replaceChildren();
                if((!record || !record.messages.length) && !snapshot.busy) {
                    const empty = document.createElement('div');
                    empty.className = 'agent-empty';
                    const title = document.createElement('strong');
                    title.textContent = record ? '一起把想法变成作品' : '正在加载对话…';
                    const hint = document.createElement('span');
                    hint.textContent = record ? '引用画布中的素材，描述你的创意。每段对话独立保留草稿、引用与模型选择。' : '';
                    empty.append(title, hint);
                    messages.append(empty);
                } else record?.messages.forEach(message => messages.append(api.renderMessage(message, document, {conversation:record, controller})));
                if(snapshot.pendingMessage) messages.append(api.renderMessage(snapshot.pendingMessage, document));
                if(snapshot.busy) {
                    const pending = document.createElement('article');
                    pending.className = 'agent-message agent-message-pending';
                    pending.setAttribute('role', 'status');
                    pending.setAttribute('aria-label', 'Agent 正在回复');
                    const indicator = document.createElement('span');
                    indicator.className = 'agent-pending-indicator';
                    indicator.setAttribute('aria-hidden', 'true');
                    const label = document.createElement('span');
                    label.textContent = '正在讨论与校验计划…';
                    pending.append(indicator, label);
                    messages.append(pending);
                }
                messages.scrollTop = follow ? messages.scrollHeight : previousScroll;
            }
            byId('agentChatLabel').textContent = resolveModel(providers, record || blank(), 'chat').model;
            const imageModel = resolveModel(providers, record || blank(), 'image').model || '未配置';
            const videoModel = resolveModel(providers, record || blank(), 'video').model || '未配置';
            byId('agentGenerationModel').title = `图片：${imageModel}；视频：${videoModel}`;
            const historyList = byId('agentHistoryList'); historyList.replaceChildren();
            if(!snapshot.conversations.length) historyList.textContent = '没有匹配的对话';
            snapshot.conversations.forEach(conversation => {
                const row = document.createElement('div'); row.className = 'agent-history-row';
                const button = document.createElement('button');
                button.type = 'button'; button.textContent = `${conversation.title} · ${runStatus[conversation.status] || '未执行'}`;
                button.setAttribute('aria-current', String(conversation.id === snapshot.activeId));
                button.onclick = () => run(async () => {
                    await controller.selectConversation(conversation.id);
                    byId('agentHistory').hidden = true;
                    byId('agentHistoryToggle').setAttribute('aria-expanded', 'false');
                    editor.focus();
                });
                const remove = document.createElement('button');
                remove.type = 'button'; remove.className = 'agent-history-delete';
                remove.textContent = '删除';
                remove.title = '删除对话，不影响画布素材';
                remove.setAttribute('aria-label', `删除对话 ${conversation.title}`);
                remove.onclick = () => run(async () => {
                    if(!window.confirm(`删除对话「${conversation.title}」？删除后无法恢复，画布上的节点和素材会保留。`)) return;
                    remove.disabled = true;
                    try { await controller.deleteConversation(conversation.id); }
                    finally { remove.disabled = false; }
                });
                row.append(button, remove); historyList.append(row);
            });
            if(!byId('agentModelPopup').hidden) renderModels();
        }
        function setOpen(open) {
            panel.hidden = !open;
            byId('shell').classList.toggle('agent-open', open);
            byId('agentToggle').classList.toggle('active', open);
            byId('agentToggle').setAttribute('aria-expanded', String(open));
            if(open) {
                host.closeAssets?.();
                panel.focus();
                run(() => controller.open());
                run(async () => { providers = await controller.getProviders(); refresh(); });
                refresh();
            } else {
                closeModels();
                clearTimeout(saveTimer);
                const id = controller.getSnapshot().activeId;
                run(() => controller.savePreferences(id));
            }
        }
        byId('agentToggle').onclick = () => setOpen(panel.hidden);
        byId('agentCollapse').onclick = () => { setOpen(false); byId('agentToggle').focus(); };
        byId('agentNew').onclick = () => run(async () => {
            byId('agentNew').disabled = true;
            try { await controller.newConversation(); editor.focus(); }
            finally { byId('agentNew').disabled = false; }
        });
        byId('agentHistoryToggle').onclick = () => {
            const history = byId('agentHistory'); history.hidden = !history.hidden;
            byId('agentHistoryToggle').setAttribute('aria-expanded', String(!history.hidden));
            if(!history.hidden) { run(() => controller.searchConversations(byId('agentSearch').value)); byId('agentSearch').focus(); }
        };
        byId('agentSearch').oninput = event => {
            clearTimeout(searchTimer);
            const query = event.target.value;
            searchTimer = setTimeout(() => run(() => controller.searchConversations(query)), 200);
        };
        byId('agentDraft').oninput = event => {
            const id = controller.getSnapshot().activeId;
            controller.updatePreferences({draft:event.target.value}, id);
            clearTimeout(saveTimer);
            saveTimer = setTimeout(() => run(() => controller.savePreferences(id)), 450);
        };
        document.addEventListener('selectionchange', rememberCaret);
        editor.oncompositionstart = () => { composing = true; };
        editor.oncompositionend = () => { composing = false; editor.oninput(); };
        editor.oninput = () => {
            if(composing) return;
            rememberCaret();
            const value = readInlineDraft(editor);
            const ids = parseReferenceText(value).filter(part => part.id).map(part => part.id);
            const record = controller.getSnapshot().conversation;
            controller.updatePreferences({draft:value, reference_node_ids:record.reference_node_ids.filter(id => ids.includes(id))});
            clearTimeout(saveTimer);
            const id = controller.getSnapshot().activeId;
            saveTimer = setTimeout(() => run(() => controller.savePreferences(id)), 450);
        };
        editor.onpaste = event => {
            event.preventDefault();
            const selection = document.getSelection();
            if(!selection.rangeCount) return;
            const range = selection.getRangeAt(0);
            range.deleteContents();
            const text = document.createTextNode(event.clipboardData.getData('text/plain'));
            range.insertNode(text); range.setStartAfter(text); range.collapse(true);
            selection.removeAllRanges(); selection.addRange(range); editor.oninput();
        };
        byId('agentDraft').onblur = () => {
            const id = controller.getSnapshot().activeId;
            clearTimeout(saveTimer);
            run(() => controller.savePreferences(id));
        };
        editor.onblur = byId('agentDraft').onblur;
        root?.StudioSkills?.attach(editor, {
            read:() => readInlineDraft(editor),
            caret:() => { rememberCaret(); return caretOffset ?? readInlineDraft(editor).length; },
            replace:(text, offset) => {
                caretOffset = offset;
                renderInlineDraft(text);
                editor.focus(); restoreCaret(offset); editor.oninput();
            }
        });
        byId('agentAddReference').onclick = () => run(async () => {
            await controller.addSelectedReferences();
            editor.focus(); restoreCaret(caretOffset ?? readInlineDraft(editor).length);
        });
        const openModels = kind => run(async () => {
            modelKind = kind;
            byId('agentModelPopup').hidden = false;
            byId('agentChatModel').setAttribute('aria-expanded', String(kind === 'chat'));
            byId('agentGenerationModel').setAttribute('aria-expanded', String(kind !== 'chat'));
            renderModels();
            providers = await controller.getProviders();
            renderModels();
        });
        const toggleModels = kind => {
            if(!byId('agentModelPopup').hidden) closeModels();
            else openModels(kind);
        };
        byId('agentChatModel').onclick = () => toggleModels('chat');
        byId('agentGenerationModel').onclick = () => toggleModels('image');
        panel.querySelectorAll('[data-agent-kind]').forEach(button => { button.onclick = () => { modelKind = button.dataset.agentKind; renderModels(); }; });
        byId('agentSend').onclick = () => controller.sendMessage().catch(() => {});
        panel.addEventListener('keydown', event => {
            if(event.key === 'Escape') { closeModels(); event.stopPropagation(); }
        });
        // Stop bubbling without cancelling native selection, input, scroll or clipboard behavior.
        // Let keyup reach host modifier cleanup when focus changes during a held key.
        ['pointerdown','mousedown','click','dblclick','contextmenu','wheel','keydown','paste','copy','cut'].forEach(type => {
            panel.addEventListener(type, event => event.stopPropagation());
        });
        ['dragover','drop'].forEach(type => panel.addEventListener(type, event => { event.preventDefault(); event.stopPropagation(); }));
        // Capture before the Agent panel or canvas stops propagation. The two
        // triggers handle their own toggle so the same click cannot reopen it.
        document.addEventListener('pointerdown', event => {
            if(byId('agentModelPopup').contains(event.target)) return;
            if(byId('agentChatModel').contains(event.target) || byId('agentGenerationModel').contains(event.target)) return;
            closeModels();
        }, true);
        controller.subscribe(refresh);
        refresh();
        return {controller, host, setOpen, refresh, setStatus:status,
            referenceNodes(ids) {
                // Capture the current draft before opening the panel starts history restoration.
                const adding = controller.addReferences(ids);
                setOpen(true);
                return run(async () => {
                    await adding; status('已引用到当前对话');
                    editor.focus(); restoreCaret(caretOffset ?? readInlineDraft(editor).length);
                });
            }};
    }

    const api = {createController, modelOptions, resolveModel, safeMediaUrl, referenceToken, parseReferenceText, readInlineDraft, contextFromNodes, generationInputs, generationResultContext, renderMessage, mount};
    if(typeof module !== 'undefined' && module.exports) module.exports = api;
    if(root) {
        root.CanvasAgent = api;
        root.addEventListener('DOMContentLoaded', () => {
            let storage, pendingStorage;
            try { storage = root.localStorage; } catch(e) { /* Selection still works without persistence. */ }
            try { pendingStorage = root.sessionStorage; } catch(e) { /* Pending drafts remain in memory. */ }
            api.ui = mount({document:root.document, host:root.createCanvasAgentHost(), fetch:root.fetch.bind(root), storage, pendingStorage});
        });
    }
})(typeof window === 'undefined' ? null : window);
