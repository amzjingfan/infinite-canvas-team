const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../static/gpt-chat.html'), 'utf8');
function context(apiProviders) {
    const scope = {apiProviders, config:{ms_chat_models:['legacy-ms'],chat_model:'legacy-chat',image_model:'legacy-image'},
        provider:'modelscope',activeChatModel:'legacy-ms',msModel:'legacy-ms',activeImageProvider:'missing',activeImageModel:'legacy-image',
        mode:'agent',modelPickerScope:'chat',chatProviderModels:{},renderModelPicker(){}};
    vm.createContext(scope);
    vm.runInContext(html.slice(html.indexOf('        function uniqueModels('), html.indexOf('        async function loadConfig(')), scope);
    return scope;
}
test('GPT chat picker hides every unconfigured provider including legacy ModelScope fallback', () => {
    const row = {chat_models:['chat'],image_models:['image']};
    const c = context([{...row,id:'ok',has_key:true}, {...row,id:'modelscope',has_key:false},
        {...row,id:'missing'}, {...row,id:'disabled',enabled:false,has_key:true}]);
    assert.deepEqual(Array.from(c.chatProviders(),p=>p.id), ['ok']);
    assert.deepEqual(Array.from(c.imageProviders(),p=>p.id), ['ok']);
    c.validateSavedProviderState();
    assert.equal(c.provider, 'ok');
    assert.equal(c.activeChatModel, 'chat');
});
test('empty credentials leave no phantom provider or model and render safely', () => {
    const c = context([]);
    assert.equal(c.chatProviders().length, 0);
    assert.equal(c.imageProviders().length, 0);
    c.validateSavedProviderState();
    c.renderProviderControls();
    assert.equal(c.modelListForCurrent().length, 0);
    assert.equal(c.activeChatModel, '');
    assert.equal(c.activeImageModel, '');
});
