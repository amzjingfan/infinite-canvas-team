const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/smart-canvas.js'), 'utf8');
function fn(name) {
    const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
    if(start < 0) return '';
    const rest = source.slice(start + 1);
    const end = rest.search(/\n(?:async )?function /);
    return source.slice(start, end < 0 ? source.length : start + 1 + end);
}
function context({provider='autodl', fail=false, resume=false, refs}={}) {
    const calls = [];
    const node = resume ? {autodlTaskId:'saved-task'} : {};
    const settings = {videoProvider:provider, videoModel:provider === 'autodl' ? 'minimax-h3' : 'veo3', videoDuration:15, videoAspect:'9:16', videoResolution:'768p'};
    const ctx = {
        settings, Date, setTimeout:fn => fn(),
        apiProviders:[{id:'autodl', video_models:['minimax-h3']}, {id:'custom-api', name:'TUGO', video_models:['veo3']}],
        render:()=>{}, scheduleSave:()=>{}, tr:x=>x, toast:()=>{},
        liveSmartNode:n=>n,
        escapeHtml:String, escapeAttr:String, videoAspectIconClass:()=>'',
        filterJimengVideoModels:x=>x, dynamicParams:{innerHTML:''},
        isJimengProviderId:()=>false, renderVideoTrustedAssetControl:()=>'<trusted>',
        imageRefsOnly:items=>items.filter(x=>x.kind==='image'),
        audioRefsOnly:items=>items.filter(x=>x.kind==='audio'),
        videoRefsOnly:items=>items.filter(x=>x.kind==='video'),
        applyUploadedUrlsToSmartRefs:x=>x, videoProviderPlatform:()=>'',
        resultMediaUrls:r=>r.urls,
        fetch:async (url, options) => {
            calls.push({url, body:options?.body && JSON.parse(options.body)});
            if(url === '/api/autodl/tasks') return {ok:true,json:async()=>({task_id:'new-task'})};
            if(url === '/api/autodl/download' || url === '/api/canvas-video') return {ok:true,json:async()=>({urls:['/assets/output/result.mp4']})};
            if(fail) throw new Error('offline');
            return {ok:true,json:async()=>({status:'COMPLETED'})};
        }
    };
    vm.createContext(ctx);
    vm.runInContext(fn('isVintedProviderId'),ctx);
    vm.runInContext(fn('vintedDurations'),ctx);
    for(const name of ['videoApiProviders','videoProviderById','providerVideoModels','renderVideoProviderControl','renderVideoModelControl','renderVideoDurationControl','renderVideoAspectControl','renderVideoResolutionControl','renderVideoToggleControl','renderApiVideoParams','autodlFetch','runAutoDLVideoGeneration','runApiVideoGeneration','autodlPendingBodyHtml','resumeAutoDLVideoNode','clearSmartNodeTransientRunState']) vm.runInContext(fn(name),ctx);
    return {ctx,calls,node,settings,refs:refs || [{kind:'image',url:'/assets/input/product.jpg'},{kind:'audio',url:'/assets/input/voice.wav'}]};
}
(async()=>{
    const a = context();
    const urls = await a.ctx.runApiVideoGeneration('connected prompt', a.refs, a.settings, a.node);
    assert.equal(a.calls[0].url, '/api/autodl/tasks', 'AutoDL must not call the OpenAI video route');
    assert.deepEqual(a.calls[0].body, {prompt:'connected prompt', duration:15, resolution:'768p竖', images:['/assets/input/product.jpg'], audios:['/assets/input/voice.wav']});
    assert.equal(urls[0], '/assets/output/result.mp4');
    assert.equal(a.node.autodlTaskId, undefined);
    const b = context({resume:true,fail:true});
    await assert.rejects(b.ctx.runApiVideoGeneration('prompt',b.refs,b.settings,b.node), /offline/);
    assert.equal(b.node.autodlTaskId, 'saved-task');
    assert.equal(b.calls.some(c=>c.url==='/api/autodl/tasks'),false);
    const c = context({refs:[{kind:'video',url:'/assets/input/v.mp4'}]});
    await assert.rejects(c.ctx.runApiVideoGeneration('prompt',c.refs,c.settings,c.node), /不支持参考视频/);
    assert.equal(c.calls.length,0);
    for(const invalid of [{videoDuration:16}, {videoDuration:1.5}, {videoResolution:'1080p'}, {videoAspect:'1:1'}]){
        const test = context();
        Object.assign(test.settings,invalid);
        await assert.rejects(test.ctx.runApiVideoGeneration('prompt',test.refs,test.settings,test.node));
        assert.equal(test.calls.length,0);
    }
    const d = context({provider:'custom-api'});
    await d.ctx.runApiVideoGeneration('prompt',d.refs,d.settings,d.node);
    assert.equal(d.calls[0].url, '/api/canvas-video');
    assert.equal(d.calls[0].body.provider_id, 'custom-api');
    const ui = context();
    ui.settings.videoResolution='1080p'; ui.settings.videoAspect='1:1'; ui.settings.videoDuration=60;
    ui.ctx.renderApiVideoParams();
    assert.equal(ui.settings.videoResolution,'768p');
    assert.equal(ui.settings.videoAspect,'9:16');
    assert.equal(ui.settings.videoDuration,15);
    assert.match(ui.ctx.dynamicParams.innerHTML, /768p/);
    assert.doesNotMatch(ui.ctx.dynamicParams.innerHTML, /videoWatermark|videoTrustedAsset|videoGenerateAudio|1080p/);
    d.ctx.renderApiVideoParams();
    assert.match(d.ctx.dynamicParams.innerHTML, /videoWatermark/);
    const pending = context({resume:true});
    pending.node.id = 'output';
    pending.node.autodlStatus = '排队中';
    assert.match(pending.ctx.autodlPendingBodyHtml(pending.node,{width:300,height:200}), /排队中|查询结果/);
    pending.node.running = true;
    assert.match(pending.ctx.autodlPendingBodyHtml(pending.node,{width:300,height:200}), /disabled/);
    pending.node.running = false;
    pending.ctx.nodes = [pending.node];
    pending.ctx.finalizePendingNode = (node,urls,meta,kind) => { node.images = urls.map(url=>({url,kind})); };
    pending.ctx.syncRunButtonState = ()=>{};
    await pending.ctx.resumeAutoDLVideoNode('output');
    assert.equal(pending.node.images[0].kind,'video');
    assert.equal(pending.node.images[0].url,'/assets/output/result.mp4');
    assert.equal(pending.calls.some(c=>c.url==='/api/autodl/tasks'),false);
    const synced = context();
    let live = synced.node;
    synced.ctx.liveSmartNode = () => live;
    const fetch = synced.ctx.fetch;
    synced.ctx.fetch = async (...args) => { const result = await fetch(...args); live = {...live}; return result; };
    await synced.ctx.runApiVideoGeneration('prompt',synced.refs,synced.settings,synced.node);
    assert.equal(live.autodlStatus,'已完成','canvas sync must not leave a stale task on the live output');
    assert.equal(live.autodlTaskId,undefined);
    const copied = {autodlTaskId:'original-paid-task',autodlStatus:'排队中',running:true};
    synced.ctx.clearSmartNodeTransientRunState(copied);
    assert.equal(copied.autodlTaskId,undefined,'copied/exported nodes must not inherit a paid task');
    console.log('AutoDL platform: routing, inputs, resume, validation and platform UI passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
