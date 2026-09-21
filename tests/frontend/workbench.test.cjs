const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function context() {
 const nodes = new Map();
 const node = id => {if(!nodes.has(id)) nodes.set(id,{value:'',textContent:'',hidden:false,disabled:false,addEventListener(){},replaceChildren(){},append(){},querySelectorAll(){return []}});return nodes.get(id);};
 const ctx=vm.createContext({document:{getElementById:node,querySelectorAll:()=>[node("draft"),node("visibility"),node("save")],createElement:()=>({}),createTextNode:x=>x},window:{addEventListener(){}},fetch:async()=>({ok:true,json:async()=>({authenticated:false,csrf_token:'test'})}),Option:function(){}});
 vm.runInContext(fs.readFileSync('src/dynamix_manager/workbench/static/app.js','utf8'),ctx);
 return {ctx,node};
}
test('failed draft load cannot carry internal text into a public or different ticket composer',async()=>{
 const {ctx,node}=context(); await new Promise(setImmediate);
 node('draft').value='CONFIDENTIAL internal text'; node('instructions').value='private instructions';
 vm.runInContext(`ticket={id:2}; visibility='public'; fetch=async()=>({ok:false,status:502,json:async()=>({detail:'Draft unavailable'})});`,ctx);
 await assert.rejects(vm.runInContext('loadDraft()',ctx),/Draft unavailable/);
 assert.equal(node('draft').value,''); assert.equal(node('instructions').value,'');
 assert.throws(()=>vm.runInContext('payload()',ctx),/not loaded/);
});

test('restored draft retains its earlier source revision',async()=>{
 const {ctx,node}=context(); await new Promise(setImmediate);
 vm.runInContext(`ticket={id:2,source_revision:'new',recipients:[]}; visibility='public'; fetch=async()=>({ok:true,json:async()=>({text:'older draft',instructions:'',source_revision:'old',recipients:[]})});`,ctx);
 await vm.runInContext('loadDraft()',ctx);
 assert.equal(vm.runInContext('payload().source_revision',ctx),'old');
 assert.equal(node('stale-draft').hidden,false);
});
test('inputs and visibility are disabled for the duration of asynchronous actions',async()=>{
 const {ctx,node}=context(); await new Promise(setImmediate);
 vm.runInContext('composerReady=true',ctx);
 let resolve; ctx.operation=()=>new Promise(r=>{resolve=r;});
 const promise=vm.runInContext('run(operation)',ctx);
 assert.equal(node('draft').disabled,true);assert.equal(node('visibility').disabled,true);
 resolve(); await promise;
 assert.equal(node('draft').disabled,false);assert.equal(node('visibility').disabled,false);
});

test('failed draft load keeps composition disabled after request finishes',async()=>{
 const {ctx,node}=context();await new Promise(setImmediate);
 vm.runInContext(`ticket={id:2,recipients:[]};fetch=async()=>({ok:false,status:502,json:async()=>({detail:'Draft unavailable'})});`,ctx);
 await vm.runInContext('run(loadDraft)',ctx);
 assert.equal(node('draft').disabled,true);assert.equal(node('save').disabled,true);
});

test('generation displays progress immediately and failures beside its button',async()=>{
 const {ctx,node}=context(); await new Promise(setImmediate);
 let reject; ctx.pending=()=>new Promise((resolve,r)=>{reject=r;});
 vm.runInContext(`ticket={id:1};composerReady=true;fetch=async(url)=>{if(url.endsWith('/suggest'))return pending();return {ok:true,json:async()=>({})};};`,ctx);
 const call=vm.runInContext('generate()',ctx);
 await new Promise(setImmediate);
 assert.match(node('generation-status').textContent,/Generating/);
 reject(new Error('OpenAI request timed out'));
 await assert.rejects(call,/timed out/);
 assert.match(node('generation-status').textContent,/timed out/);
 assert.equal(node('generate').textContent,'Generate suggestions');
});

test('close action uses completed metadata, prefers Closed, and rejects ambiguity',async()=>{
 const {ctx}=context(); await new Promise(setImmediate);
 assert.equal(vm.runInContext("closureStatus([{id:9,name:'Cancelled',status_class:'cancelled'},{id:42,name:'Resolved',status_class:'completed'},{id:88,name:'Closed',status_class:'completed'}]).id",ctx),88);
 assert.equal(vm.runInContext("closureStatus([{id:42,name:'Resolved',status_class:'completed'}]).id",ctx),42);
 assert.equal(vm.runInContext("closureStatus([{id:9,name:'Closed',status_class:'cancelled'}])",ctx),null);
 assert.equal(vm.runInContext("closureStatus([{id:42,name:'Done',status_class:'completed'},{id:43,name:'Resolved',status_class:'completed'}])",ctx),null);
});
test('update and close preserves message and recipients and opens preview without submitting',async()=>{
 const {ctx,node}=context(); await new Promise(setImmediate);
 const calls=[]; ctx.record=(url,options)=>{calls.push({url,body:JSON.parse(options.body)});};
 node('draft').value='Issue fixed';node('recipients').querySelectorAll=()=>[{value:'requester-1'}];
 node('preview').showModal=()=>{node('preview').open=true;};
 vm.runInContext("ticket={id:101,statuses:[{id:88,name:'Closed',status_class:'completed'}]};composerReady=true;session={mode:'demo'};fetch=async(url,options)=>{record(url,options);return {ok:true,json:async()=>({ticket_id:101,title:'Example',text:'Issue fixed',new_status:'Closed',recipients:[]})};};",ctx);
 await vm.runInContext('reviewClose()',ctx);
 assert.equal(calls.length,2);assert.ok(calls[1].url.endsWith('/preview'));
 assert.equal(calls[1].body.status_id,88);assert.equal(calls[1].body.text,'Issue fixed');
 assert.deepEqual(calls[1].body.recipients,['requester-1']);assert.equal(node('preview').open,true);
});

test('PDF links are labeled, authenticated local routes and cleared between tickets',async()=>{
 const {ctx,node}=context(); await new Promise(setImmediate);
 let links=[];
 node('pdf-links').replaceChildren=()=>{links=[];};
 node('pdf-links').append=link=>links.push(link);
 vm.runInContext("ticket={id:101,attachments:[{id:'abc',name:'Current.pdf'},{id:'def',name:'Previous.pdf'}]};renderPDFs()",ctx);
 assert.equal(links.length,2);
 assert.equal(links[0].textContent,'View PDF · Current.pdf');
 assert.equal(links[0].href,'/api/tickets/101/attachments/abc/pdf');
 assert.equal(links[0].target,'_blank');
 assert.equal(links[1].textContent,'View PDF · Previous.pdf');
 vm.runInContext("ticket={id:102};renderPDFs()",ctx);
 assert.equal(links.length,0);
});
