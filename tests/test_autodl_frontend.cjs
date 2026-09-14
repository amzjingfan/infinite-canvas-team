const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/smart-canvas.js'), 'utf8');
const code = source.slice(source.indexOf('async function autodlFetch('), source.indexOf('async function runMinimaxNode('));

async function runCase({resume=false, failQuery=false, video=false}={}) {
    const segment = {prompt:'a cat', duration:5, autodlResolution:'480p横', autodlSeed:0};
    if(resume) segment.autodlTaskId = 'existing-task';
    const calls = [];
    const ctx = {
        smartMinimaxSelectedSegment:() => segment,
        inputPromptTextFor:() => '',
        smartMinimaxRefsForKind:(_, kind) => kind === 'image' ? [{url:'/assets/input/cat.png'}] : kind === 'video' && video ? [{url:'v.mp4'}] : [],
        render:() => {}, scheduleSave:() => {}, setTimeout:fn => fn(),
        fetch:async (url, options) => {
            calls.push({url, body:options?.body && JSON.parse(options.body)});
            if(url === '/api/autodl/tasks') return {ok:true, json:async () => ({task_id:'new-task'})};
            if(url === '/api/autodl/download') return {ok:true, json:async () => ({urls:['/assets/output/v.mp4']})};
            if(failQuery) throw new Error('offline');
            return {ok:true, json:async () => ({status:'COMPLETED'})};
        }
    };
    vm.createContext(ctx);
    vm.runInContext(code, ctx);
    const node = {};
    if(failQuery || video) {
        await assert.rejects(ctx.runMinimaxAutoDL(node));
        if(failQuery) assert.equal(segment.autodlTaskId, resume ? 'existing-task' : 'new-task');
        if(video) assert.equal(calls.length, 0);
    } else {
        const urls = await ctx.runMinimaxAutoDL(node);
        assert.equal(urls[0], '/assets/output/v.mp4');
        assert.equal(segment.autodlTaskId, undefined);
        if(resume) assert.equal(calls.some(c => c.url === '/api/autodl/tasks'), false);
        else {
            assert.equal(calls[0].body.resolution, '480p横');
            assert.equal(calls[0].body.seed, 0);
            assert.equal(calls[0].body.images[0], '/assets/input/cat.png');
        }
    }
}
(async () => {
    await runCase();
    await runCase({resume:true});
    await runCase({resume:true, failQuery:true});
    await runCase({video:true});
    console.log('AutoDL frontend: 4 cases passed');
})().catch(error => { console.error(error); process.exitCode=1; });
