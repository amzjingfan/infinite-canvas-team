const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/js/smart-canvas.js'),'utf8');
function setup(){
    const calls=[];
    const node={id:'output',images:[]};
    const ctx={settings:{videoProvider:'vinted',videoModel:'seedance2.5',videoDuration:30},node,calls,
        apiProviders:[],escapeHtml:String,tr:String,crypto:{randomUUID:()=> 'a'.repeat(32)},
        liveSmartNode:n=>n,render:()=>{},queueCanvasSave:async()=>calls.push('save'),
        mediaKindForItem:r=>r.kind||'image',resultMediaUrls:r=>r.videos,
        fetch:async(url,options)=>{calls.push({url,body:JSON.parse(options.body||'{}')});return {ok:true,json:async()=>({videos:['/assets/test.mp4']})};}};
    vm.createContext(ctx);
    for(const name of ['isVintedProviderId','vintedDurations','renderVideoDurationControl','runVintedVideoGeneration']){
        const match=source.match(new RegExp(`^(?:async )?function ${name}\\([^]*?^}`,'m'));
        assert.ok(match,`Application helper ${name}`);vm.runInContext(match[0],ctx);
    }
    return ctx;
}
test('Vinted duration menu follows selected model without unsupported custom values',()=>{
    const ctx=setup(); const menu=ctx.renderVideoDurationControl();
    assert.match(menu,/data-smart-value="30"/);assert.doesNotMatch(menu,/<input/);
    ctx.settings.videoModel='seedance2.0mini';const mini=ctx.renderVideoDurationControl();
    assert.match(mini,/data-smart-value="10"/);assert.doesNotMatch(mini,/data-smart-value="15"/);
});
test('Vinted saves the operation before submit and resumes the original after interruption',async()=>{
    const ctx=setup();let initial=true;
    ctx.fetch=async(url,options)=>{ctx.calls.push({url,body:JSON.parse(options.body||'{}')});
        if(initial){initial=false;return {ok:false,status:502,json:async()=>({detail:'network interrupted'})};}
        return {ok:true,json:async()=>({videos:['/assets/test.mp4']})};};
    await assert.rejects(ctx.runVintedVideoGeneration('test',[{url:'/assets/input.png',kind:'image'}],
        {videoProvider:'vinted',videoModel:'seedance2.0fast',videoDuration:5,videoAspect:'9:16'},ctx.node));
    assert.equal(ctx.calls[0],'save');assert.equal(ctx.calls[1].body.vinted_operation_id,'a'.repeat(32));
    assert.equal(ctx.node.vintedOperationId,'a'.repeat(32));
    await ctx.runVintedVideoGeneration('',[],{},ctx.node);
    assert.equal(ctx.calls.filter(c=>c.url==='/api/canvas-video').length,1);
    assert.equal(ctx.calls.filter(c=>c.url===`/api/vinted/operations/${'a'.repeat(32)}/resume`).length,1);
    assert.equal(ctx.node.vintedOperationId,'a'.repeat(32), 'Keep recovery until caller attaches the media');
});
test('Vinted does not submit when the canvas cannot persist its operation',async()=>{
    const ctx=setup();ctx.queueCanvasSave=async()=>{throw new Error('save failed');};
    await assert.rejects(ctx.runVintedVideoGeneration('test',[],{videoProvider:'vinted'},ctx.node),/save failed/);
    assert.equal(ctx.calls.length,0);assert.equal(ctx.node.vintedOperationId,undefined);
});
test('The real result finalizer releases a Vinted operation only after attaching video',()=>{
    const ctx=setup();ctx.node.vintedOperationId='a'.repeat(32);
    Object.assign(ctx,{nodes:[ctx.node],cleanHistoryImages:x=>x,copyMediaSizeFields:(a,b)=>b,
        markSmartNodeComplete:()=>{},mediaNodeDefaultScale:()=>2,attachRunMeta:()=>{},
        stripImageGenerationMeta:x=>x,clearSourceBusyStateIfDownstreamDone:()=>{},
        selectedId:'',activeComposerSubject:null});
    vm.runInContext(source.match(/^function finalizePendingNode\([^]*?^}/m)[0],ctx);
    ctx.finalizePendingNode(ctx.node,['/assets/test.mp4'],null,'video');
    assert.equal(ctx.node.images[0].url,'/assets/test.mp4');assert.equal(ctx.node.images[0].kind,'video');
    assert.equal(ctx.node.vintedOperationId,undefined);
});
