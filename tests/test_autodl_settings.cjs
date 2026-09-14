const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/api-settings.js'), 'utf8');
let selected = {id:'autodl',video_models:['minimax-h3']};
const ctx = {provider:()=>selected, videoModelList:{innerHTML:''}, imageModelList:{}, chatModelList:{},
    tr:x=>x, modelDisplayName:()=>'', escapeHtml:String, escapeAttr:String, refreshIcons:()=>{},
    providerSupportsModelProtocol:()=>false, modelProtocolSelectHtml:()=>''};
vm.createContext(ctx);
for(const name of ['isFixedProvider','renderModels']){
    const start = source.indexOf(`function ${name}(`);
    const end = source.indexOf('\nfunction ',start+1);
    vm.runInContext(source.slice(start,end),ctx);
}
assert.equal(ctx.isFixedProvider(selected),true,'AutoDL is a built-in platform with a fixed workflow');
ctx.renderModels('video');
assert.match(ctx.videoModelList.innerHTML,/minimax-h3/);
assert.match(ctx.videoModelList.innerHTML,/readonly/);
assert.doesNotMatch(ctx.videoModelList.innerHTML,/removeModel|updateModel/);
selected = {id:'custom-api',video_models:['veo3']};
assert.equal(ctx.isFixedProvider(selected),false);
ctx.renderModels('video');
assert.match(ctx.videoModelList.innerHTML,/removeModel/);
assert.match(ctx.videoModelList.innerHTML,/updateModel/);
console.log('AutoDL settings: fixed workflow and editable TUGO models passed');
