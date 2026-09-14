const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const modulePath = path.join(__dirname, '../static/js/smart-canvas-agent.js');
const agent = fs.existsSync(modulePath) ? require(modulePath) : {};
const copy = value => JSON.parse(JSON.stringify(value));
function conversation(id) {
    return {id, canvas_id:'canvas-one', title:id, created_at:1, updated_at:1,
        messages:[], plans:[], runs:[], draft:'', reference_node_ids:[],
        chat_provider:'', chat_model:'', image_provider:'', image_model:'', video_provider:'', video_model:''};
}
function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return {promise, resolve};
}
function setup(ids = ['a', 'b', 'c']) {
    assert.equal(typeof agent.createController, 'function', 'controller must be exported');
    const records = new Map(ids.map(id => [id, conversation(id)]));
    const calls = [];
    const gates = new Map();
    const responseGates = new Map();
    const patchFailures = new Set();
    const storage = new Map();
    const pending = new Map();
    const hostNodes = [{id:'n1', title:'Cat', text:'a cat', images:[{url:'/assets/cat.png', kind:'image', name:'cat.png'}]},
        {id:'n2', title:'Brief', text:'blue sky', images:[]}];
    const host = {canvasId:'canvas-one', selectedReferenceIds:() => ['n1', 'n2'],
        getAgentCanvasContext:ids => agent.contextFromNodes('canvas-one', hostNodes, ids),
        ensureSaved:async ids => host.getAgentCanvasContext(ids),
        getProviders:() => [{id:'tugo', has_key:true, protocol:'openai', chat_models:['chat1'], image_models:['image1'], video_models:['video1']}]};
    const options = {canvasId:'canvas-one', host,
        storage:{getItem:key => storage.get(key), setItem:(key, value) => storage.set(key, value)},
        pendingStorage:{getItem:key => pending.get(key), setItem:(key, value) => pending.set(key, value), removeItem:key => pending.delete(key)},
        fetch:async (url, options = {}) => {
            const method = options.method || 'GET';
            const body = options.body ? JSON.parse(options.body) : undefined;
            calls.push({url, method, body});
            const gate = gates.get(`${method} ${url}`);
            if(gate) await gate.promise;
            assert.ok(url.startsWith('/api/canvases/canvas-one/agent/conversations'));
            const parsed = new URL(url, 'http://localhost');
            const id = parsed.pathname.split('/')[6];
            if(method === 'POST' && parsed.pathname.endsWith('/messages')) {
                if(!records.has(id)) return {ok:false, status:404, json:async () => ({detail:'原对话已删除'})};
                records.get(id).messages.push({id:'u', role:'user', content:body.message}, {id:'a', role:'assistant', content:'reply'});
                return response({kind:'chat', conversation:copy(records.get(id))});
            }
            if(method === 'POST') { const record = conversation('new'); records.set('new', record); return response({conversation:copy(record)}); }
            if(!id) return response({conversations:[...records.values()].filter(c => c.title.includes(parsed.searchParams.get('q') || '')).map(copy)});
            if(!records.has(id)) return {ok:false, status:404, json:async () => ({detail:'对话不存在'})};
            if(method === 'DELETE') { records.delete(id); return response({deleted:true}); }
            if(method === 'PATCH') {
                if(patchFailures.has(id)) return {ok:false, status:503, json:async () => ({detail:'偏好保存失败'})};
                const permitted = ['draft','reference_node_ids','chat_provider','chat_model','image_provider','image_model','video_provider','video_model',
                    'case_input_mode','recipe_id','recipe_overrides'];
                assert.ok(Object.keys(body).every(key => permitted.includes(key)), 'PATCH must not write messages/plans/runs');
                Object.assign(records.get(id), body);
            }
            const value = copy(records.get(id));
            if(responseGates.has(`${method} ${url}`)) await responseGates.get(`${method} ${url}`).promise;
            return response({conversation:value});
        }};
    const controller = agent.createController(options);
    return {controller, host, hostNodes, records, calls, gates, responseGates, storage, pending, patchFailures,
        reload:overrides => agent.createController({...options, ...overrides})};
}
function response(body) { return {ok:true, json:async () => body}; }
const route = id => `/api/canvases/canvas-one/agent/conversations/${id}`;

test('creative input context uses explicit attachments and durable prior media, never historical prompts or parents', () => {
    const refs=agent.contextFromNodes('canvas',[
        {id:'image',type:'smart-image',text:'OLD PROMPT',inputNodeIds:['hidden'],images:[{url:'/one.png',kind:'image'}]},
        {id:'brief',type:'smart-prompt',text:'Explicit brief',images:[]}],['image','brief']);
    assert.equal(refs.references[0].text,''); assert.equal(refs.references[1].text,'Explicit brief');
    const run={id:'run',mode:'creation',reference_snapshot:[{id:'image',text:'OLD PROMPT',prompt_fallback:'OLDER',
        input_node_ids:['missing'],images:[{url:'/one.png',kind:'image',name:'one'}]}],
        operations:[{id:'first',op:'generate',kind:'image',prompt:'Frozen initial',reference_node_ids:['image']},
            {id:'next',op:'generate',kind:'video',prompt:'Frozen motion',reference_node_ids:['first','image']}],
        steps:[{operation_id:'first',result:{media:[{url:'/saved.png',kind:'image',name:'saved'}]}}]};
    const context=agent.generationInputs(run,'next');
    assert.equal(context.prompt,'Frozen motion'); assert.equal(context.kind,'video');
    assert.deepEqual(context.media.map(m=>m.url),['/saved.png','/one.png']);
    run.image_workflow={identity_media:[{url:'/one.png',kind:'image',name:'one'}],cases:[{media:{url:'/case.png',kind:'image'}}],reference_instruction:'Do not append again'};
    assert.deepEqual(agent.generationInputs(run,'first').media.map(m=>m.url),['/one.png','/case.png']);
    assert.equal(agent.generationInputs(run,'first').prompt,'Frozen initial');
});

test('inline references preserve text order and distinct node identities', () => {
    const text = `用${agent.referenceToken('图片1', 'n1')}的人物和${agent.referenceToken('图片2', 'n2')}的背景`;
    assert.deepEqual(agent.parseReferenceText(text), [
        {text:'用'}, {id:'n1', label:'图片1'}, {text:'的人物和'}, {id:'n2', label:'图片2'}, {text:'的背景'}]);
    const tree = {childNodes:[{nodeType:3, textContent:'用'},
        {nodeType:1, dataset:{referenceToken:agent.referenceToken('图片1','n1')}, childNodes:[]},
        {nodeType:1, tagName:'BR'}, {nodeType:3, textContent:'背景'}]};
    assert.equal(agent.readInlineDraft(tree), `用@[图片1](node:n1)\n背景`);
});

test('direct canvas reference uses clicked node rather than selection and deduplicates', async () => {
    const {controller:c, host, records} = setup();
    await c.open();
    host.selectedReferenceIds = () => ['n2'];
    await c.addReferences(['n1']);
    await c.addReferences(['n1']);
    assert.deepEqual(c.getSnapshot().conversation.reference_node_ids, ['n1']);
    assert.deepEqual(records.get('a').reference_node_ids, ['n1']);
    await assert.rejects(c.addReferences(['missing']), /不存在/);
    assert.deepEqual(c.getSnapshot().conversation.reference_node_ids, ['n1']);
});

test('successful send clears sent references in draft and storage but preserves next-turn additions', async () => {
    const {controller:c, records, gates, calls} = setup();
    await c.open();
    c.updatePreferences({draft:'first', reference_node_ids:['n1']});
    const gate = deferred(); gates.set(`POST ${route('a')}/messages`, gate);
    const sending = c.sendMessage();
    await new Promise(resolve => setImmediate(resolve));
    c.updatePreferences({reference_node_ids:['n1','n2']});
    gate.resolve(); await sending;
    assert.deepEqual(c.getSnapshot().conversation.reference_node_ids, ['n2']);
    assert.deepEqual(records.get('a').reference_node_ids, ['n2']);
    assert.deepEqual(calls.find(x => x.url.endsWith('/messages')).body.reference_node_ids, ['n1']);
    gates.clear(); c.updatePreferences({draft:'second'}); await c.sendMessage();
    assert.deepEqual(c.getSnapshot().conversation.reference_node_ids, []);
    assert.deepEqual(records.get('a').reference_node_ids, []);
});

test('deleting current history clears selection without creating a conversation', async () => {
    const {controller:c, calls} = setup();
    await c.open();
    await c.deleteConversation('a');
    assert.equal(c.getSnapshot().activeId, null);
    assert.deepEqual(c.getSnapshot().conversations.map(x => x.id), ['b', 'c']);
    assert.equal(calls.some(x => x.method === 'POST'), false);
});

test('deletion does not reset a different conversation selected during the request', async () => {
    const {controller:c, gates} = setup();
    await c.open();
    const gate = deferred(); gates.set(`DELETE ${route('a')}`, gate);
    const deleting = c.deleteConversation('a');
    await c.selectConversation('b');
    gate.resolve(); await deleting;
    assert.equal(c.getSnapshot().activeId, 'b');
    assert.equal(c.getSnapshot().conversations.some(x => x.id === 'a'), false);
});

test('late history load cannot restore a deleted conversation', async () => {
    const {controller:c, responseGates} = setup();
    await c.searchConversations();
    const gate = deferred(); responseGates.set(`GET ${route('a')}`, gate);
    const selecting = c.selectConversation('a');
    await new Promise(resolve => setImmediate(resolve));
    await c.deleteConversation('a');
    gate.resolve(); await selecting;
    assert.equal(c.getSnapshot().activeId, null);
    await c.selectConversation('a');
    assert.equal(c.getSnapshot().activeId, null);
});

test('failed deletion preserves current history and draft', async () => {
    const {controller:c, records} = setup();
    await c.open(); c.updatePreferences({draft:'keep this'});
    records.delete('a');
    await assert.rejects(c.deleteConversation('a'), /不存在/);
    assert.equal(c.getSnapshot().activeId, 'a');
    assert.equal(c.getSnapshot().conversation.draft, 'keep this');
    assert.equal(c.getSnapshot().conversations.some(x => x.id === 'a'), true);
});

test('first send freezes displayed default models before delayed conversation creation', async () => {
    const {controller:c, host, gates, calls} = setup([]);
    const shown = {id:'shown', has_key:true, protocol:'openai', primary:true, chat_models:['shown-chat'], image_models:['shown-image'], video_models:['shown-video']};
    let providers = [shown];
    host.getProviders = () => providers;
    await c.getProviders();
    c.updatePreferences({draft:'hello'});
    const gate = deferred();
    gates.set('POST /api/canvases/canvas-one/agent/conversations', gate);
    const sending = c.sendMessage();
    providers = [{...shown, id:'new-default', chat_models:['new-chat']}, shown];
    gate.resolve(); await sending;
    const sent = calls.find(call => call.url.endsWith('/messages')).body;
    assert.equal(sent.chat_provider, 'shown');
    assert.equal(sent.chat_model, 'shown-chat');
});

test('opening an empty canvas lists without creating a conversation', async () => {
    const {controller, calls} = setup([]);
    await controller.open();
    assert.equal(controller.getSnapshot().activeId, null);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].method, 'GET');
});
test('first-open edited preferences survive reload before New or Send without creating empty conversations', async () => {
    const {controller:c, reload, calls, records} = setup([]);
    await c.open();
    c.updatePreferences({draft:'first unsent idea', chat_provider:'tugo', chat_model:'chat1',
        image_provider:'tugo', image_model:'image1', video_provider:'tugo', video_model:'video1'});
    await c.addSelectedReferences();
    await Promise.all([c.savePreferences(), c.savePreferences()]); // blur and autosave
    const restored = reload();
    await restored.open();
    assert.equal(restored.getSnapshot().conversation.draft, 'first unsent idea');
    assert.deepEqual(restored.getSnapshot().conversation.reference_node_ids, ['n1','n2']);
    for(const kind of ['chat','image','video']) {
        assert.equal(restored.getSnapshot().conversation[`${kind}_provider`], 'tugo');
        assert.equal(restored.getSnapshot().conversation[`${kind}_model`], `${kind}1`);
    }
    assert.equal(records.size, 0);
    assert.ok(calls.every(call => call.method === 'GET'));
    assert.equal(reload({canvasId:'canvas-two'}).getSnapshot().conversation.draft, '');
    assert.equal(reload({pendingStorage:undefined}).getSnapshot().conversation.draft, '', 'another tab session has no pending draft');
});
test('first-open legacy pending image options are retired without losing draft or attachments', async () => {
    const {pending,reload,calls} = setup([]);
    pending.set('canvas-agent:canvas-one:pending', JSON.stringify({draft:'保留这段草稿',reference_node_ids:['n1'],
        case_input_mode:'single_case',recipe_id:'recipe_hidden',recipe_overrides:['background']}));
    const restored=reload();
    assert.equal(restored.getSnapshot().conversation.draft,'保留这段草稿');
    assert.deepEqual(restored.getSnapshot().conversation.reference_node_ids,['n1']);
    assert.equal(restored.getSnapshot().conversation.case_input_mode,'design_only');
    assert.equal(restored.getSnapshot().conversation.recipe_id,'');
    await restored.sendMessage();
    const sent=calls.find(call=>call.url.endsWith('/messages')).body;
    assert.equal(sent.case_input_mode,'design_only');
    assert.equal(sent.recipe_id,'');
    assert.deepEqual(sent.reference_node_ids,['n1']);
});

test('first-open latest typing persists immediately and promotion removes pending state with one POST', async () => {
    const {controller:c, reload, gates, calls, records} = setup([]);
    c.updatePreferences({draft:'send this'});
    assert.equal(reload().getSnapshot().conversation.draft, 'send this', 'reload before debounce retains input');
    const gate = deferred();
    gates.set('POST /api/canvases/canvas-one/agent/conversations', gate);
    const sending = c.sendMessage();
    c.updatePreferences({draft:'next turn while creating'});
    await Promise.all([c.savePreferences(), c.savePreferences()]);
    assert.equal(reload().getSnapshot().conversation.draft, 'next turn while creating');
    gate.resolve(); await sending;
    assert.equal(records.get('new').draft, 'next turn while creating');
    assert.equal(calls.filter(call => call.method === 'POST' && !call.url.endsWith('/messages')).length, 1);
    const restored = reload();
    assert.equal(restored.getSnapshot().conversation.draft, '', 'promoted pending record is removed');
    await restored.open();
    assert.equal(restored.getSnapshot().activeId, 'new');
    assert.equal(restored.getSnapshot().conversation.draft, 'next turn while creating');
    assert.equal(restored.getSnapshot().conversation.messages[0].content, 'send this');
});
for(const action of ['New', 'Send']) for(const typing of [false, true]) {
    test(`first promotion recovery after failed PATCH and reload: ${action}, typing=${typing}`, async () => {
        const {controller:c, reload, gates, records, calls, pending, patchFailures} = setup([]);
        await c.open();
        assert.equal(calls.filter(call => call.method === 'POST').length, 0);
        const expected = {draft:'recover this draft', reference_node_ids:['n1'],
            chat_provider:'tugo', chat_model:'chat1', image_provider:'tugo', image_model:'image1',
            video_provider:'tugo', video_model:'video1'};
        c.updatePreferences(expected);
        const gate = deferred();
        gates.set('PATCH ' + route('new'), gate);
        patchFailures.add('new');
        const creating = action === 'New' ? c.newConversation() : c.sendMessage();
        const rejected = assert.rejects(creating, /偏好保存失败/);
        await new Promise(resolve => setImmediate(resolve));
        if(typing) {
            Object.assign(expected, {draft:'new typing during failed save', reference_node_ids:['n2']});
            c.updatePreferences(expected);
        }
        gate.resolve(); await rejected;
        assert.equal(c.getSnapshot().conversation.draft, expected.draft);
        assert.equal(records.get('new').draft, '');
        assert.deepEqual(records.get('new').messages, []);
        const restored = reload();
        const beforeOpen = calls.length;
        await restored.open();
        assert.ok(calls.slice(beforeOpen).every(call => call.method === 'GET'), 'reload is read-only');
        assert.equal(restored.getSnapshot().activeId, 'new', 'recovery must keep the already-created ID');
        for(const [key, value] of Object.entries(expected)) assert.deepEqual(restored.getSnapshot().conversation[key], value, key);
        assert.ok(pending.has('canvas-agent:canvas-one:pending'), 'failed initial save retains recovery');
        patchFailures.clear(); gates.clear();
        if(action === 'New') {
            assert.equal((await restored.newConversation()).id, 'new', 'New retries pending promotion');
            for(const [key, value] of Object.entries(expected)) assert.deepEqual(records.get('new')[key], value, key);
        } else {
            await restored.sendMessage();
            assert.equal(records.get('new').messages[0].content, expected.draft);
            const sent = calls.find(call => call.url.endsWith('/messages'));
            assert.deepEqual(sent.body.reference_node_ids, expected.reference_node_ids);
            assert.equal(sent.body.chat_model, expected.chat_model);
        }
        assert.equal(pending.has('canvas-agent:canvas-one:pending'), false);
        assert.equal(calls.filter(call => call.method === 'POST' && !call.url.endsWith('/messages')).length, 1);
        const saved = reload(); await saved.open();
        assert.equal(saved.getSnapshot().activeId, 'new');
        assert.deepEqual(saved.getSnapshot().conversation.reference_node_ids, action === 'Send' ? [] : expected.reference_node_ids);
    });
}
test('first promotion recovery keeps new typing until the latest PATCH succeeds', async () => {
    const {controller:c, reload, gates, calls, records, pending} = setup([]);
    c.updatePreferences({draft:'initial draft'});
    const gate = deferred(); gates.set('PATCH ' + route('new'), gate);
    const creating = c.newConversation();
    await new Promise(resolve => setImmediate(resolve));
    c.updatePreferences({draft:'typed after PATCH began', reference_node_ids:['n2'], image_model:'new-model'});
    gate.resolve(); await creating;
    assert.equal(records.get('new').draft, 'initial draft');
    const restored = reload();
    const loading = deferred(); gates.set('GET ' + route('new'), loading);
    const opening = restored.open();
    restored.updatePreferences({draft:'typed during reload detail'});
    loading.resolve(); await opening;
    assert.equal(restored.getSnapshot().activeId, 'new');
    assert.equal(restored.getSnapshot().conversation.draft, 'typed during reload detail');
    assert.deepEqual(restored.getSnapshot().conversation.reference_node_ids, ['n2']);
    assert.equal(restored.getSnapshot().conversation.image_model, 'new-model');
    assert.ok(pending.has('canvas-agent:canvas-one:pending'));
    await restored.savePreferences();
    assert.equal(records.get('new').draft, 'typed during reload detail');
    assert.equal(pending.has('canvas-agent:canvas-one:pending'), false);
    assert.equal(calls.filter(call => call.method === 'POST').length, 1);
});
test('late initial history restore keeps an edited blank draft visible and promotes it on New', async () => {
    const {controller:c, gates, records} = setup();
    const gate = deferred();
    gates.set('GET /api/canvases/canvas-one/agent/conversations?q=', gate);
    const opening = c.open();
    c.updatePreferences({draft:'written while history loads', image_provider:'first', image_model:'image-a'});
    gate.resolve(); await opening;
    assert.equal(c.getSnapshot().activeId, null);
    assert.equal(c.getSnapshot().conversation.draft, 'written while history loads');
    await c.newConversation();
    assert.equal(records.get('new').draft, 'written while history loads');
    assert.equal(records.get('new').image_model, 'image-a');
});
test('untouched blank start still restores existing history', async () => {
    const {controller:c} = setup();
    await c.open();
    assert.equal(c.getSnapshot().activeId, 'a');
});
for(const switchTo of [null, 'b']) test(`pending blank references follow promotion${switchTo ? ' despite another switch' : ''}`, async () => {
    const {controller:c, host, records, calls} = setup();
    const gate = deferred();
    host.ensureSaved = async () => gate.promise;
    const adding = c.addSelectedReferences();
    await c.newConversation();
    if(switchTo) await c.selectConversation(switchTo);
    gate.resolve(); await adding;
    assert.deepEqual(records.get('new').reference_node_ids, ['n1','n2']);
    assert.ok(calls.some(call => call.method === 'PATCH' && call.url === route('new') && call.body.reference_node_ids.length === 2));
    assert.equal(c.getSnapshot().activeId, switchTo || 'new');
    if(switchTo) assert.deepEqual(c.getSnapshot().conversation.reference_node_ids, []);
});
test('pending blank reference selection prevents initial restore from stranding its session', async () => {
    const {controller:c, host, gates} = setup();
    const history = deferred(), save = deferred();
    gates.set('GET /api/canvases/canvas-one/agent/conversations?q=', history);
    host.ensureSaved = async () => save.promise;
    const opening = c.open();
    const adding = c.addSelectedReferences();
    history.resolve(); await opening;
    assert.equal(c.getSnapshot().activeId, null);
    save.resolve(); await adding;
    assert.deepEqual(c.getSnapshot().conversation.reference_node_ids, ['n1','n2']);
});
test('panel key release reaches host modifier cleanup while keydown remains contained', () => {
    const agentSource = fs.readFileSync(modulePath, 'utf8');
    const mainSource = fs.readFileSync(path.join(__dirname, '../static/js/smart-canvas.js'), 'utf8');
    const panelHandlers = new Map(), windowHandlers = new Map();
    const register = map => (type, listener) => map.set(type, [...(map.get(type) || []), listener]);
    const context = {panel:{addEventListener:register(panelHandlers)}, window:{addEventListener:register(windowHandlers)},
        isRKeyDown:true};
    vm.createContext(context);
    // Execute the actual containment registrations and host release listener, not source-string assertions.
    vm.runInContext(agentSource.slice(agentSource.indexOf("        panel.addEventListener('keydown'"),
        agentSource.indexOf("        document.addEventListener('pointerdown'")), context);
    const releaseStart = mainSource.indexOf("window.addEventListener('keyup', e => {");
    vm.runInContext(mainSource.slice(releaseStart, mainSource.indexOf("window.addEventListener('blur'", releaseStart)), context);
    const dispatch = type => {
        const event = {key:'r', stopped:false, stopPropagation() { this.stopped = true; }};
        for(const listener of panelHandlers.get(type) || []) listener(event);
        if(!event.stopped) for(const listener of windowHandlers.get(type) || []) listener(event);
        return event;
    };
    assert.equal(dispatch('keydown').stopped, true, 'panel typing must not activate canvas shortcuts');
    dispatch('keyup');
    assert.equal(context.isRKeyDown, false, 'releasing R after focusing the panel must clear the modifier');
});
test('Agent stays left of Workflow with aligned top and height under desktop and narrow UI scaling', () => {
    const css = fs.readFileSync(path.join(__dirname, '../static/css/smart-canvas.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const mediaBlock = /@media[^{}]+\{(?:[^{}]|\{[^{}]*\})*\}/g;
    const desktop = css.replace(mediaBlock, '');
    const narrow = [...css.matchAll(mediaBlock)].filter(match => /max-width:\s*900px/.test(match[0]))
        .map(match => match[0].slice(match[0].indexOf('{') + 1, -1)).join('\n');
    // Evaluate the shipped toolbar's simple px declarations and shared zoom rule.
    // Browser layout/flex/text measurements remain the parent's integration check.
    function styles(className, source) {
        const selectors = [`.${className}`, `html[data-studio-scale="off"].studio-scale-managed .${className}`];
        const result = {};
        for(const [, selectorList, body] of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
            if(!selectorList.split(',').some(selector => selectors.includes(selector.trim()))) continue;
            for(const declaration of body.split(';')) {
                const colon = declaration.indexOf(':');
                if(colon >= 0) result[declaration.slice(0, colon).trim()] = declaration.slice(colon + 1).trim();
            }
        }
        return result;
    }
    for(const viewport of [2560, 390]) for(const scale of [1.1, 1, 0.8]) {
        const source = desktop + (viewport <= 900 ? narrow : '');
        function geometry(className) {
            const style = styles(className, source);
            const zoom = style.zoom === 'var(--studio-ui-scale, 1)' ? scale : 1;
            return {right:parseFloat(style.right) * zoom, width:parseFloat(style.width) * zoom,
                top:parseFloat(style.top) * zoom, height:parseFloat(style.height) * zoom};
        }
        const agentButton = geometry('agent-toggle'), workflow = geometry('smart-workflow-toggle');
        const gap = agentButton.right - workflow.right - workflow.width;
        assert.ok(gap > 0, `${viewport}px at scale ${scale}: Agent overlaps Workflow by ${-gap}px`);
        assert.equal(agentButton.top, workflow.top, 'toolbar buttons must align vertically');
        assert.equal(agentButton.height, workflow.height, 'toolbar button heights must match');
        assert.equal(styles('agent-panel', source).zoom, undefined, 'conversation panel must remain outside toolbar scaling');
    }
});
test('open Agent minimap offset preserves the screen gap at low and wide densities', () => {
    const css = fs.readFileSync(path.join(__dirname, '../static/css/smart-canvas.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
    const scaledSelectors = rules.find(([, , body]) => /zoom:\s*var\(--studio-ui-scale,\s*1\)/.test(body))[1].split(',').map(s => s.trim());
    for(const [viewport, scale] of [[1280, 0.766], [2560, 1], [2560, 1.1]]) {
        for(const className of ['smart-minimap', 'minimap-arrange-btn']) {
            const openSelector = `.shell.agent-open .${className}`;
            const managedSelector = `html[data-studio-scale="off"].studio-scale-managed ${openSelector}`;
            let right;
            for(const [, selectors, body] of rules) {
                if(selectors.split(',').some(s => [openSelector, managedSelector].includes(s.trim()))) {
                    right = body.match(/(?:^|;)\s*right:\s*([^;]+)/)?.[1] || right;
                }
            }
            assert.ok(right, 'open-panel positioning must be defined');
            const expression = right.replaceAll('var(--agent-panel-width)', '360')
                .replace(/var\(--studio-ui-scale,\s*1\)/g, String(scale)).replace(/calc|px/g, '');
            assert.match(expression, /^[\d.\s()+*/-]+$/, 'only the toolbar offset arithmetic is evaluated');
            const layoutRight = vm.runInNewContext(expression);
            const zoom = scaledSelectors.includes(`html[data-studio-scale="off"].studio-scale-managed .${className}`) ? scale : 1;
            const elementRight = viewport - layoutRight * zoom;
            const panelLeft = viewport - 14 - 360;
            const gap = panelLeft - elementRight;
            assert.ok(Math.abs(gap - 14) < 0.01, `${className} at ${viewport}px / ${scale}: expected 14px gap, got ${gap}px`);
        }
    }
});
test('drafts, reference IDs and model preferences stay with their conversation', async () => {
    const {controller:c, records, calls} = setup();
    await c.selectConversation('a');
    c.updatePreferences({draft:'draft A', reference_node_ids:['n1'], chat_provider:'deepseek', chat_model:'deepseek-chat'});
    await c.selectConversation('b');
    c.updatePreferences({draft:'draft B', reference_node_ids:['n2'], video_provider:'autodl', video_model:'minimax-h3'});
    await c.selectConversation('a');
    assert.equal(c.getSnapshot().conversation.draft, 'draft A');
    assert.deepEqual(c.getSnapshot().conversation.reference_node_ids, ['n1']);
    assert.equal(records.get('b').draft, 'draft B');
    assert.equal(records.get('b').video_model, 'minimax-h3');
    assert.equal(records.get('a').chat_model, 'deepseek-chat');
    assert.equal(records.get('a').video_provider, '');
    assert.equal(calls.find(call => call.method === 'PATCH' && call.url === route('a')).body.draft, 'draft A');
});
test('a delayed outgoing PATCH cannot select its old destination after a newer switch', async () => {
    const {controller:c, gates} = setup();
    await c.selectConversation('a');
    c.updatePreferences({draft:'pending'});
    const gate = deferred(); gates.set(`PATCH ${route('a')}`, gate);
    const first = c.selectConversation('b');
    await c.selectConversation('c');
    gate.resolve(); await first;
    assert.equal(c.getSnapshot().activeId, 'c');
    assert.equal(c.getSnapshot().conversation.id, 'c');
});
test('late detail responses populate only their own cache and never replace the active view', async () => {
    const {controller:c, gates} = setup();
    const gate = deferred(); gates.set(`GET ${route('a')}`, gate);
    const first = c.selectConversation('a');
    await c.selectConversation('b');
    gate.resolve(); await first;
    assert.equal(c.getSnapshot().conversation.id, 'b');
    await c.selectConversation('a');
    assert.equal(c.getSnapshot().conversation.id, 'a');
});
test('edits made during save are retained and the next save sends the latest draft', async () => {
    const {controller:c, gates, records} = setup();
    await c.selectConversation('a'); c.updatePreferences({draft:'first'});
    const gate = deferred(); gates.set(`PATCH ${route('a')}`, gate);
    const saving = c.savePreferences();
    c.updatePreferences({draft:'latest'});
    gate.resolve(); await saving;
    assert.equal(c.getSnapshot().conversation.draft, 'latest');
    await c.savePreferences();
    assert.equal(records.get('a').draft, 'latest');
});
test('history search encodes server title query and retains the selected conversation', async () => {
    const {controller:c, calls} = setup();
    await c.selectConversation('a');
    await c.searchConversations('猫 & sky?');
    assert.equal(calls.at(-1).url, '/api/canvases/canvas-one/agent/conversations?q=%E7%8C%AB%20%26%20sky%3F');
    assert.equal(c.getSnapshot().activeId, 'a');
});
test('creating conversations leaves host nodes intact and remembers selection per canvas', async () => {
    const {controller:c, hostNodes, storage} = setup();
    const before = copy(hostNodes);
    await c.newConversation();
    assert.deepEqual(hostNodes, before);
    assert.equal(c.getSnapshot().conversation.id, 'new');
    assert.equal(storage.get('canvas-agent:canvas-one:active'), 'new');
});
test('blank-start draft and preferences survive explicit first conversation creation', async () => {
    const {controller:c, records} = setup([]);
    await c.open();
    c.updatePreferences({draft:'first idea', chat_provider:'deepseek', chat_model:'deepseek-chat'});
    await c.addSelectedReferences();
    await c.newConversation();
    assert.equal(c.getSnapshot().conversation.draft, 'first idea');
    assert.deepEqual(records.get('new').reference_node_ids, ['n1','n2']);
    assert.equal(records.get('new').chat_provider, 'deepseek');
});
test('a failed outgoing save keeps the unsaved conversation available for retry', async () => {
    const {controller:c, records} = setup();
    await c.selectConversation('a');
    c.updatePreferences({draft:'keep me'});
    records.delete('a'); // PATCH now returns a failed persistence response.
    await assert.rejects(c.selectConversation('b'));
    assert.equal(c.getSnapshot().activeId, 'a');
    assert.equal(c.getSnapshot().conversation.draft, 'keep me');
});
test('typing during first conversation creation survives the delayed POST response', async () => {
    const {controller:c, gates, records} = setup([]);
    c.updatePreferences({draft:'initial'});
    const gate = deferred();
    gates.set('POST /api/canvases/canvas-one/agent/conversations', gate);
    const creating = c.newConversation();
    c.updatePreferences({draft:'still typing'});
    gate.resolve(); await creating;
    assert.equal(records.get('new').draft, 'still typing');
});
test('removed references are excluded from next context; missing IDs are errors', async () => {
    const {controller:c, hostNodes} = setup();
    await c.selectConversation('a');
    await c.addSelectedReferences();
    await c.removeReference('n1');
    assert.deepEqual((await c.getContext()).references.map(ref => ref.nodeId), ['n2']);
    hostNodes.pop();
    await assert.rejects(c.getContext(), /n2/);
});
test('selection is captured before save and remains attached to its original conversation', async () => {
    const {controller:c, host, records} = setup();
    await c.selectConversation('a');
    const gate = deferred(); host.ensureSaved = async () => gate.promise;
    const adding = c.addSelectedReferences();
    await c.selectConversation('b');
    host.selectedReferenceIds = () => [];
    gate.resolve(); await adding;
    assert.equal(c.getSnapshot().conversation.id, 'b');
    assert.deepEqual(c.getSnapshot().conversation.reference_node_ids, []);
    assert.deepEqual(records.get('a').reference_node_ids, ['n1','n2']);
});
test('empty selection and unsuccessful canvas save cannot attach references', async () => {
    const {controller:c, host} = setup();
    await c.selectConversation('a');
    host.selectedReferenceIds = () => [];
    await assert.rejects(c.addSelectedReferences(), /选择/);
    host.selectedReferenceIds = () => ['n1'];
    host.ensureSaved = async () => { throw new Error('画布未保存'); };
    await assert.rejects(c.addSelectedReferences(), /未保存/);
    assert.deepEqual(c.getSnapshot().conversation.reference_node_ids, []);
});
test('model menus require configured keys and supported enabled providers, and default to primary usable models', () => {
    assert.equal(typeof agent.modelOptions, 'function');
    const providers = [
        {id:'first', has_key:true, protocol:'openai', image_models:['image-a'], chat_models:['chat-a']},
        {id:'deepseek', has_key:true, primary:true, protocol:'openai', chat_models:['deepseek-chat'], image_models:['image-b']},
        {id:'off', has_key:true, enabled:false, protocol:'openai', chat_models:['hidden'], image_models:['hidden']},
        {id:'runninghub', has_key:true, protocol:'runninghub', image_models:['hidden']},
        {id:'cli', has_key:true, protocol:'codex', chat_models:['hidden']},
        {id:'autodl', has_key:true, protocol:'autodl', video_models:['minimax-h3'], image_models:['hidden']},
        {id:'modelscope', enabled:true, has_key:false, protocol:'openai', chat_models:['unconfigured-chat']},
        {id:'no-key', has_key:false, protocol:'openai', image_models:['unconfigured-image'], video_models:['unconfigured-video']},
        {id:'unknown-key', protocol:'openai', chat_models:['unknown-chat']}];
    assert.deepEqual(agent.modelOptions(providers, 'chat').map(x => x.model), ['chat-a','deepseek-chat']);
    assert.deepEqual(agent.modelOptions(providers, 'image').map(x => x.model), ['image-a','image-b']);
    assert.deepEqual(agent.modelOptions(providers, 'video').map(x => x.model), ['minimax-h3']);
    assert.deepEqual(agent.resolveModel(providers, {}, 'chat'), {provider:'deepseek', model:'deepseek-chat'});
    assert.deepEqual(agent.resolveModel(providers, {chat_provider:'modelscope', chat_model:'unconfigured-chat'}, 'chat'),
        {provider:'modelscope', model:'unconfigured-chat'});
    providers.forEach(provider => { provider.has_key = false; });
    assert.deepEqual(agent.resolveModel(providers, {}, 'chat'), {provider:'', model:''});
});
test('media URLs reject executable forms and node context includes only requested IDs', () => {
    assert.equal(typeof agent.safeMediaUrl, 'function');
    assert.equal(agent.safeMediaUrl('javascript:alert(1)'), '');
    assert.equal(agent.safeMediaUrl('data:text/html,<script>'), '');
    assert.equal(agent.safeMediaUrl('/assets/cat.png'), '/assets/cat.png');
    const {host} = setup();
    assert.deepEqual(host.getAgentCanvasContext(['n1']), {canvasId:'canvas-one', references:[
        {nodeId:'n1', name:'Cat', text:'', images:[{url:'/assets/cat.png', kind:'image', name:'cat.png'}]}]});
});

test('send captures all fields before canvas save, preserves next edits and routes late response to origin', async () => {
    const {controller:c, calls, host} = setup();
    await c.selectConversation('a');
    c.updatePreferences({draft:'first', reference_node_ids:['n1'], chat_provider:'tugo', chat_model:'chat1',
        image_provider:'tugo', image_model:'image1', video_provider:'tugo', video_model:'video1'});
    const save = deferred(); host.ensureSaved = async ids => { assert.deepEqual(ids, ['n1']); await save.promise; };
    const pending = c.sendMessage();
    assert.equal(c.getSnapshot().busy, true);
    assert.equal(c.getSnapshot().conversation.draft, '');
    await assert.rejects(c.sendMessage(), /回复|发送/);
    c.updatePreferences({draft:'next', reference_node_ids:['n2'], image_model:'new-image'});
    await c.selectConversation('b');
    save.resolve(); await pending;
    assert.equal(c.getSnapshot().activeId, 'b');
    assert.deepEqual(c.getSnapshot().conversation.messages, []);
    const sent = calls.find(call => call.url === route('a') + '/messages');
    assert.deepEqual(sent.body, {message:'first', reference_node_ids:['n1'], chat_provider:'tugo', chat_model:'chat1',
        generation_defaults:{image:{provider:'tugo', model:'image1'}, video:{provider:'tugo', model:'video1'}},
        case_input_mode:'design_only',recipe_id:'',recipe_overrides:[]});
    await c.selectConversation('a');
    assert.equal(c.getSnapshot().conversation.draft, 'next');
    assert.equal(c.getSnapshot().conversation.image_model, 'new-image');
    assert.equal(c.getSnapshot().conversation.messages.length, 2);
});

test('first send blocks double click during POST and sends to captured new ID after switch', async () => {
    const {controller:c, gates, calls} = setup();
    c.updatePreferences({draft:'first', chat_provider:'tugo', chat_model:'chat1', image_provider:'tugo', image_model:'image1',
        video_provider:'tugo', video_model:'video1'});
    const gate = deferred(); gates.set('POST /api/canvases/canvas-one/agent/conversations', gate);
    const pending = c.sendMessage();
    await assert.rejects(c.sendMessage(), /回复|发送/);
    c.updatePreferences({draft:'new typing'});
    await c.selectConversation('b');
    gate.resolve(); await pending;
    assert.equal(c.getSnapshot().activeId, 'b');
    assert.equal(calls.filter(call => call.url.endsWith('/messages')).length, 1);
    assert.equal(calls.find(call => call.url.endsWith('/messages')).url, route('new') + '/messages');
    await c.selectConversation('new');
    assert.equal(c.getSnapshot().conversation.draft, 'new typing');
});

test('conversations send in parallel and an old request error is not shown in the switched view', async () => {
    const {controller:c, gates, records} = setup();
    await c.selectConversation('a'); c.updatePreferences({draft:'a'});
    const gate = deferred(); gates.set('POST ' + route('a') + '/messages', gate);
    const pending = c.sendMessage();
    // Wait until the request reaches the deferred transport.
    await new Promise(resolve => setImmediate(resolve));
    await c.selectConversation('b'); c.updatePreferences({draft:'b'});
    await c.sendMessage();
    records.delete('a'); gate.resolve(); await assert.rejects(pending, /删除/);
    assert.equal(c.getSnapshot().error, '');
    assert.equal(c.getSnapshot().conversation.messages[0].content, 'b');
    await c.selectConversation('a');
    assert.match(c.getSnapshot().error, /删除/);
});

test('invalid explicitly selected model is retained for settings guidance, ModelScope generation is excluded', () => {
    const providers = [{id:'modelscope', has_key:true, protocol:'openai', image_models:['ms-image'], chat_models:['ms-chat']},
        {id:'tugo', has_key:true, protocol:'openai', image_models:['image1']}];
    assert.deepEqual(agent.modelOptions(providers, 'image').map(x => x.model), ['image1']);
    assert.deepEqual(agent.resolveModel(providers, {image_provider:'missing', image_model:'old'}, 'image'),
        {provider:'missing', model:'old'});
});

function testDocument() {
    return {createElement(tag) { return {tag, children:[], attributes:{}, textContent:'',
        setAttribute(key, value) { this.attributes[key] = value; }, append(...nodes) { this.children.push(...nodes); }}; }};
}
function flatten(element) { return [element, ...element.children.flatMap(flatten)]; }
test('sent inline references render saved thumbnails in text order without exposing node markup', () => {
    const element = agent.renderMessage({role:'user', content:'用@[图片1](node:n1)的背景',
        references:[{id:'n1', images:[{kind:'image', url:'/assets/cat.png'}]}]}, testDocument());
    const nodes = flatten(element);
    assert.equal(nodes.filter(n => n.tag === 'img')[0].src, '/assets/cat.png');
    assert.equal(nodes.map(n => n.textContent).join(''), '用图片1的背景');
});
test('plan cards bind historical version and show actual settings, refs, disclosure, safe text and disabled confirmation', () => {
    const plan = {id:'plan1', version:1, status:'superseded', summary:'<script>unsafe</script>',
        reference_snapshot:[{id:'n1', title:'Cat', text:'reference prompt', images:[{url:'/assets/cat.png', kind:'image', name:'cat.png'}]}],
        operations:[{id:'p', op:'create_prompt', text:'A cat'},
            {id:'m', op:'create_media', kind:'video', reference_node_ids:['n1']},
            {id:'c', op:'connect', from:'p', to:'m'},
            {id:'g', op:'generate', node:'m', settings:{provider:'autodl', model:'minimax-h3', count:1,
                duration:8, resolution:'768p', aspect_ratio:'16:9', size:'16:9'}}]};
    const record = {plans:[plan, {...plan, version:2, summary:'new', status:'proposed'}]};
    const element = agent.renderMessage({role:'assistant', content:'review', plan_id:'plan1', plan_version:1}, testDocument(), {conversation:record, controller:{}});
    const nodes = flatten(element), text = nodes.map(n => n.textContent).join(' ');
    for(const expected of ['A cat','Cat','minimax-h3','768p','8','72','Litterbox','费用','<script>unsafe</script>']) assert.ok(text.includes(expected), expected);
    assert.ok(nodes.find(n => n.tag === 'button').disabled);
    assert.ok(nodes.every(n => n.innerHTML === undefined));
});

test('current plan confirmation dynamically calls future controller hook with frozen ID and version', async () => {
    const calls = [], controller = {confirmPlan:async (...args) => calls.push(args)};
    const plan = {id:'p', version:2, status:'proposed', summary:'ok', reference_snapshot:[], operations:[]};
    const element = agent.renderMessage({role:'assistant', content:'ok', plan_id:'p', plan_version:2}, testDocument(), {conversation:{id:'original',plans:[plan]}, controller});
    const button = flatten(element).find(n => n.tag === 'button');
    assert.equal(button.disabled, false);
    await button.onclick();
    assert.deepEqual(calls, [['p',2,'original']]);
});

test('confirmed plan renders run progress, partial output and result-only recovery', async () => {
    const plan = {id:'p',version:1,status:'confirmed',summary:'render',reference_snapshot:[{id:'ref',title:'ref',text:'prompt',images:[]}],operations:[
        {id:'m',op:'create_media',kind:'image',reference_node_ids:['ref']},
        {id:'g',op:'generate',node:'m',settings:{provider:'test',model:'image',count:2,aspect_ratio:'16:9',resolution:'1k',size:'1024x576'}}]};
    const run = {id:'r',plan_id:'p',version:1,status:'unknown',steps:[{operation_id:'g',status:'unknown',
        result:{media:[{url:'/output/partial.png',kind:'image',width:777,height:333}]},error:'请到平台核实'}]};
    const calls=[];
    const nodes = flatten(agent.renderMessage({role:'assistant',content:'ok',plan_id:'p',plan_version:1},testDocument(),
        {conversation:{id:'original',plans:[plan],runs:[run]},controller:{restoreResults:async (...args)=>calls.push(args)}}));
    const text = nodes.map(n=>n.textContent).join(' ');
    assert.ok(text.includes('核实'));
    assert.ok(text.includes('777'));
    assert.ok(!text.includes('失效'));
    assert.ok(nodes.some(n=>n.tag==='img' && n.src==='/output/partial.png'));
    const button=nodes.find(n=>n.tag==='button' && !n.disabled);
    assert.equal(button.textContent,'仅恢复 / 保存已返回素材');
    await button.onclick(); assert.deepEqual(calls,[['r','original']]);
});

test('every generate card previews effective frozen prompt including fallback-only and future output', () => {
    for(const explicit of [false,true]) {
        const plan={id:'p',version:1,status:'proposed',summary:'review',reference_snapshot:[
            {id:'out',title:'Output',text:'',prompt_fallback:'Inherited frozen prompt',images:[{url:'/out.png',kind:'image'}],input_node_ids:[]},
            {id:'a',title:'A',text:'Canonical first',images:[],input_node_ids:[]},
            {id:'z',title:'Z',text:'Canonical second',images:[],input_node_ids:[]},
            {id:'texts',title:'Texts',text:'',images:[],input_node_ids:['a','z']}],
            operations:[{id:'m',op:'create_media',kind:'image',reference_node_ids:explicit?['out','texts']:['out']},
                {id:'g',op:'generate',node:'m',settings:{provider:'test',model:'image',count:1}},
                {id:'v',op:'create_media',kind:'video',reference_node_ids:['g']},
                {id:'vg',op:'generate',node:'v',settings:{provider:'test',model:'video',count:1}}]};
        const elements=flatten(agent.renderMessage({role:'assistant',plan_id:'p',plan_version:1},testDocument(),
            {conversation:{plans:[plan]},controller:{confirmPlan(){}}}));
        const prompt=explicit?'Canonical first\nCanonical second':'Inherited frozen prompt';
        for(const id of ['g','vg']) assert.ok(elements.some(n=>n.textContent===`生效提示词 ${id}：${prompt}`));
        assert.equal(elements.find(n=>n.tag==='button').disabled,false);
        assert.ok(!elements.some(n=>n.src?.includes('undefined')),'no invented future media');
    }
});

test('shared frozen resolver agrees with backend on text, URL dedup and generated fallback boundary', () => {
    const path=require('node:path'), {execFileSync}=require('node:child_process');
    const run={id:'run',reference_snapshot:[
        {id:'a',text:'First',images:[{url:'/same.png',kind:'image',name:'first'}],input_node_ids:[]},
        {id:'z',text:'Second',images:[{url:'/same.png',kind:'image',name:'last'}],input_node_ids:['a']},
        {id:'ref',type:'smart-image',text:'Non-prompt node text',images:[],input_node_ids:['a','z']}],
        operations:[{id:'m',op:'create_media',kind:'image',reference_node_ids:['ref','ref']},
            {id:'g',op:'generate',node:'m',settings:{count:1}},
            {id:'v',op:'create_media',kind:'video',reference_node_ids:['g']},
            {id:'vg',op:'generate',node:'v',settings:{count:1}}],
        steps:[{operation_id:'g',result:{media:[{url:'/generated.png',kind:'image',name:'output'}]}}]};
    for(const explicit of [false,true]) {
        const candidate=JSON.parse(JSON.stringify(run));
        if(explicit) candidate.operations.splice(3,0,{id:'p',op:'create_prompt',text:'Explicit override'},
            {id:'c',op:'connect',from:'p',to:'v'});
        const expected=JSON.parse(execFileSync(path.join(__dirname,'../python/python.exe'),['-c',
            'import sys,json,os; sys.path.insert(0,os.getcwd()); from canvas_agent import generation_inputs; r=json.load(sys.stdin); print(json.dumps([generation_inputs(r,t) for t in ["g","vg"]]))'],
            {cwd:path.join(__dirname,'..'),input:JSON.stringify(candidate),encoding:'utf8'}));
        ['g','vg'].forEach((target,i)=>{
            const actual=agent.generationInputs(candidate,target);
            assert.deepEqual([actual.kind,actual.prompt,actual.media.map(({url,kind,name})=>({url,kind,name}))],expected[i]);
        });
        assert.equal(agent.generationInputs(candidate,'g').media[0].name,'last');
        assert.deepEqual(agent.generationInputs(candidate,'vg').media.map(m=>m.url),['/generated.png']);
        assert.equal(agent.generationInputs(candidate,'vg').prompt,explicit?'Explicit override':'Non-prompt node text\nFirst\nSecond');
    }
});

test('paused video result exposes explicit continue bound to its original conversation', async () => {
    const calls=[];
    const plan={id:'p',version:1,status:'confirmed',summary:'video',reference_snapshot:[],operations:[]};
    const run={id:'r',plan_id:'p',version:1,status:'paused',client_id:'client',steps:[{operation_id:'g',status:'generated',
        result:{media:[{url:'/output/video.mp4',kind:'video'}]},error:''}]};
    const nodes=flatten(agent.renderMessage({role:'assistant',plan_id:'p',plan_version:1},testDocument(),{
        conversation:{id:'original',plans:[plan],runs:[run]},controller:{resumeRun:async (...args)=>calls.push(args)}}));
    assert.ok(nodes.some(n=>n.tag==='video' && n.src==='/output/video.mp4' && n.controls));
    const button=nodes.find(n=>n.tag==='button' && n.textContent.includes('继续'));
    assert.ok(button);
    await button.onclick();
    assert.deepEqual(calls,[['r','original']]);
});

test('a late preference PATCH response cannot erase a completed discussion reply', async () => {
    const {controller:c, gates, responseGates} = setup();
    await c.selectConversation('a'); c.updatePreferences({draft:'hello'});
    const model = deferred(); gates.set('POST ' + route('a') + '/messages', model);
    const pending = c.sendMessage();
    await new Promise(resolve => setImmediate(resolve));
    const echo = deferred(); responseGates.set('PATCH ' + route('a'), echo);
    c.updatePreferences({draft:'next'});
    const saving = c.savePreferences();
    await new Promise(resolve => setImmediate(resolve));
    model.resolve(); await pending;
    assert.equal(c.getSnapshot().conversation.messages.length, 2);
    echo.resolve(); await saving;
    assert.equal(c.getSnapshot().conversation.messages.length, 2);
    assert.equal(c.getSnapshot().conversation.draft, 'next');
});

test('selecting the same generation model leaves a current plan valid, changing it marks stale', async () => {
    const {controller:c, records} = setup();
    Object.assign(records.get('a'), {image_provider:'tugo', image_model:'image1', plans:[{id:'p', version:1, status:'proposed',
        generation_defaults:{image:{provider:'tugo', model:'image1'}, video:{provider:'tugo', model:'video1'}}}]});
    await c.selectConversation('a');
    c.updatePreferences({image_model:'image1'});
    assert.equal(c.getSnapshot().conversation.plans[0].status, 'proposed');
    c.updatePreferences({image_model:'different'});
    assert.equal(c.getSnapshot().conversation.plans[0].status, 'superseded');
});

test('models with unsupported per-model protocols are excluded from both menus', () => {
    const providers = [{id:'mixed', has_key:true, protocol:'openai', chat_models:['plain','special'], image_models:['plain','special'],
        model_protocols:{special:'gemini'}}];
    for(const kind of ['chat','image']) assert.deepEqual(agent.modelOptions(providers, kind).map(x => x.model), ['plain']);
});

test('failed first preference save reports the error on the newly created conversation and restores its draft', async () => {
    const {controller:c, gates, records} = setup([]);
    c.updatePreferences({draft:'keep first message'});
    const gate = deferred(); gates.set('PATCH ' + route('new'), gate);
    const pending = c.sendMessage();
    await new Promise(resolve => setImmediate(resolve));
    records.delete('new'); gate.resolve();
    await assert.rejects(pending);
    assert.equal(c.getSnapshot().activeId, 'new');
    assert.match(c.getSnapshot().error, /不存在/);
    assert.equal(c.getSnapshot().conversation.draft, 'keep first message');
    assert.equal(c.getSnapshot().busy, false);
});

test('special adapters that ignore generic size or quality are excluded from generation menus', () => {
    const providers = [{id:'proxy', has_key:true, protocol:'openai', image_request_mode:'openai-video-proxy', image_models:['image']},
        {id:'json', has_key:true, protocol:'openai', image_request_mode:'openai-json', image_models:['image']},
        {id:'responses', has_key:true, protocol:'openai', image_request_mode:'openai-responses', image_models:['image']},
        {id:'yuli', has_key:true, protocol:'openai', base_url:'https://yuli.host', video_models:['video']}];
    assert.deepEqual(agent.modelOptions(providers, 'image'), []);
    assert.deepEqual(agent.modelOptions(providers, 'video'), []);
});
