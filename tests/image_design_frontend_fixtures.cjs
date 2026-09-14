const clone = value => JSON.parse(JSON.stringify(value));
const agent = require('../static/js/smart-canvas-agent.js');
const document = {createElement(tag) {return {tag, children:[], dataset:{}, textContent:'',
    append(...children) {this.children.push(...children);}, setAttribute() {}};}};
const flatten = node => [node, ...node.children.flatMap(flatten)];
function plan() {
    const inputs = [{url:'/product.png',kind:'image',name:'产品',source_id:'product',role:'identity',purpose:'用户主体身份参考'}];
    const design = {intent:'产品海报',subject:{name:'当前产品',observations:[]},
        layout:{subject_count:1,viewpoint:'正面',placement:'居中',occupancy:'半幅',pose:'grounded',reading_order:['产品','标题']},
        appearance:{background:'浅米白',palette:['米白','深灰'],lighting:'左上柔光',material_rendering:'依原图',typography:'深灰无衬线'},
        case_uses:[{case_id:157,use:'research',aspects:['composition'],adopted_features:['分区与引线']}],
        render_options:{aspect_ratio:'3:4',resolution:'2k',quality:'high'},
        constraints:[],copy:{exact_text:[{text:'本次文案',source:'proposal',placement:'标题'}],allow_additional_text:true,forbidden_text:[]}};
    return {id:'plan',version:1,summary:'产品海报',mode:'creation',status:'proposed',contract_version:3,design_card:design,
        case_input_mode:'design_only',reference_snapshot:[{id:'product',title:'产品',text:'',images:[inputs[0]]}],
        generation_defaults:{image:{provider:'fixture',model:'image'},video:{provider:'fixture',model:'video'}},
        preflight:{status:'ready_with_notes',model:'chat',issues:[{id:'collage',kind:'suggestion',paths:['layout.subject_count'],
            evidence:'多视角参考可能影响主体数量',correction:'成图检查关注主体数量'}]},
        operations:[{id:'image1',op:'generate',kind:'image',prompt:'唯一冻结的完整实际提示词',generation_inputs:inputs,
            reference_node_ids:['product'],settings:{provider:'fixture',model:'image',count:1,aspect_ratio:'3:4',resolution:'2k',quality:'high',size:'1536x2048'},
            contract:{version:3,design_card:design,requirements:[],copy:design.copy}}],
        image_workflow:{max_revisions:2,corpus_total:541,candidates:[{id:157,title:'案例',observation:'旧颜色'}],
            queries:['产品'],template:'海报',direction:'OLD DIRECTION',identity_media:[inputs[0]],
            generation_inputs:inputs,generation_contract:{version:3,design_card:design},
            cases:[{id:157,title:'案例157',purpose:'主风格/构图参考',media:{url:'/case157.png',kind:'image'},prompt:'OLD CASE FULL PROMPT'}]}};
}
function record() {
    return {id:'conv',canvas_id:'canvas',title:'对话',updated_at:1,created_at:1,draft:'',reference_node_ids:[],
        chat_provider:'fixture',chat_model:'chat',image_provider:'fixture',image_model:'image',video_provider:'fixture',video_model:'video',
        messages:[],plans:[],runs:[]};
}
function recipe() {return {id:'recipe_a',name:'认可浅色版',status:'user-approved',case_input_mode:'design_only',created_at:10,
    source:{conversation_id:'conv',run_id:'run',operation_id:'image1',round:0,image_index:0},
    result:{url:'/approved.png',kind:'image'},quality:{status:'needs_review'},review:{passed:false,summary:'小字待核验'}};}
function setup() {
    let stored = record();
    const calls = [], recipes = [recipe()];
    let gate = null;
    const host = {canvasId:'canvas',clientId:'client',ensureSaved:async()=>{},
        getAgentCanvasContext:ids=>({references:ids.map(id=>({nodeId:id,images:[]}))}),
        getProviders:()=>[{id:'fixture',has_key:true,protocol:'openai',chat_models:['chat'],image_models:['image'],video_models:['video']}]};
    const c = agent.createController({canvasId:'canvas',host,fetch:async(url, options={})=>{
        const method=options.method||'GET',body=options.body?JSON.parse(options.body):undefined;
        calls.push({url,method,body});
        const response = data => ({ok:true,json:async()=>clone(data)});
        if(url.endsWith('/recipes') && method==='GET') return response({recipes});
        if(url.endsWith('/recipes') && method==='POST') {
            if(gate) await gate;
            const saved={...recipe(),...body,id:'recipe_saved'};recipes.push(saved);return response({recipe:saved});
        }
        if(url.includes('/recipes/')) return response({recipe:recipes.find(r=>r.id===url.split('/').at(-1))});
        if(url.endsWith('/messages')) {
            stored.messages.push({role:'user',content:body.message});stored.updated_at++;
            return response({kind:'chat',conversation:stored});
        }
        if(method==='PATCH') {Object.assign(stored,body);stored.updated_at++;}
        if(url.includes('/conversations?')) return response({conversations:[stored]});
        return response({conversation:stored});
    }});
    return {c,calls,recipes,get stored(){return stored;},setGate(promise){gate=promise;}};
}
module.exports = {agent,clone,document,flatten,plan,record,recipe,setup};
