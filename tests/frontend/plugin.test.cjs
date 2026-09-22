const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function app(){
 const nodes=new Map(),handlers={},sent=[];
 function node(){return {textContent:'',children:[],focus(){},querySelector(){return null},append(...xs){this.children.push(...xs)},replaceChildren(){this.children=[];this.textContent=''}};}
 const document={getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},createElement:node};
 const parent={postMessage:m=>sent.push(m)};
 const window={parent,addEventListener:(k,f)=>handlers[k]=f};
 const ctx=vm.createContext({document,window,URL,setTimeout,clearTimeout});
 const html=fs.readFileSync('src/dynamix_manager/plugin_app.html','utf8');
 vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],ctx);
 return {ctx,nodes,handlers,parent,sent};
}
test('ticket UI uses literal text and only trusted ticket links',()=>{
 const a=app();
 a.ctx.data={tickets:[{ID:1,Title:'<img onerror=alert(1)>',url:'javascript:alert(1)'},{ID:2,Title:'OK',url:'https://cedarville.teamdynamix.com/TDNext/Apps/634/Tickets/TicketDet.aspx?TicketID=2'}],warning:'Bounded'};
 vm.runInContext('render(data)',a.ctx);
 const cards=a.nodes.get('tickets').children;
 assert.equal(cards[0].children[0].textContent,'#1 · <img onerror=alert(1)>');
 assert.equal(cards[0].children.length,2);
 assert.equal(cards[1].children[2].rel,'noopener noreferrer');
 assert.equal(a.nodes.get('warning').textContent,'Bounded');
});
test('app handshake and results only accept parent messages',()=>{
 const a=app();assert.equal(a.sent[0].method,'ui/initialize');
 const data={jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{structuredContent:{tickets:[{ID:42,Title:'Ticket'}]}}};
 const count=a.nodes.size;a.handlers.message({source:{},data});assert.equal(a.nodes.size,count);
 a.handlers.message({source:a.parent,data});assert.equal(a.nodes.get('tickets').children.length,1);
 a.handlers.message({source:a.parent,data:{jsonrpc:'2.0',id:'tdx-init',result:{}}});
 assert.equal(a.sent[1].method,'ui/notifications/initialized');
});

test('click loads detail and activity through the MCP bridge, and back retains results',async()=>{
 const a=app();
 vm.runInContext("render({tickets:[{ID:42,Title:'Wifi'}]})",a.ctx);
 a.handlers.message({source:a.parent,data:{jsonrpc:'2.0',id:'tdx-init',result:{}}});
 const opening=a.nodes.get('tickets').children[0].children[0].onclick();
 let msg=a.sent.at(-1);assert.equal(msg.method,'tools/call');assert.equal(msg.params.name,'get_ticket');assert.equal(msg.params.arguments.ticket_id,42);
 a.handlers.message({source:a.parent,data:{jsonrpc:'2.0',id:msg.id,result:{structuredContent:{detail:{ID:42,Title:'Wifi',RequestorName:'Requester'},description_text:'Needs assistance'}}}});
 await new Promise(setImmediate);
 assert.equal(a.nodes.get('description').textContent,'Needs assistance');
 assert.equal(a.nodes.get('detail').hidden,false);
 msg=a.sent.at(-1);assert.equal(msg.params.name,'ticket_feed');
 a.handlers.message({source:a.parent,data:{jsonrpc:'2.0',id:msg.id,result:{structuredContent:{ticket_id:42,items:[{CreatedFullName:'Analyst',body_text:'Investigating',IsPrivate:true}],warning:'Partial'}}}});
 await opening;
 assert.equal(a.nodes.get('activity').children[0].children[1].textContent,'Investigating');
 assert.match(a.nodes.get('activity').children[0].children[0].textContent,/Private/);
 a.nodes.get('back').onclick();assert.equal(a.nodes.get('detail').hidden,true);assert.equal(a.nodes.get('tickets').children.length,1);
});

test('stale ticket responses cannot replace a newer selection',async()=>{
 const a=app();let resolveFirst;
 a.ctx.window.openai={callTool:(name,args)=>{
  if(name==='ticket_feed')return Promise.resolve({structuredContent:{ticket_id:args.ticket_id,items:[]}});
  if(args.ticket_id===1)return new Promise(r=>{resolveFirst=r});
  return Promise.resolve({structuredContent:{detail:{ID:2,Title:'Second'},description_text:'Second description'}});
 }};
 const first=vm.runInContext('openTicket(1)',a.ctx);await vm.runInContext('openTicket(2)',a.ctx);
 resolveFirst({structuredContent:{detail:{ID:1,Title:'First'}}});await first;
 assert.equal(a.nodes.get('detail-title').textContent,'#2 · Second');
});

test('tool errors preserve results and make retry possible',async()=>{
 const a=app();a.ctx.window.openai={callTool:async()=>({isError:true})};
 vm.runInContext("render({tickets:[{ID:1,Title:'One'}]})",a.ctx);
 await vm.runInContext('openTicket(1)',a.ctx);
 assert.match(a.nodes.get('error').textContent,/could not load/);
 assert.equal(a.nodes.get('results').hidden,false);assert.equal(a.nodes.get('tickets').children.length,1);
});

test('back invalidates in-flight activity and html remains literal',async()=>{
 const a=app();let complete;
 a.ctx.window.openai={callTool:async name=>name==='get_ticket'?{structuredContent:{detail:{ID:1,Title:'One'},description_text:'<script>literal</script>'}}:new Promise(r=>{complete=r})};
 const opening=vm.runInContext('openTicket(1)',a.ctx);await new Promise(setImmediate);
 assert.equal(a.nodes.get('description').textContent,'<script>literal</script>');
 a.nodes.get('back').onclick();complete({structuredContent:{ticket_id:1,items:[{body_text:'Late'}]}});await opening;
 assert.equal(a.nodes.get('detail').hidden,true);assert.equal(a.nodes.get('activity').children.length,0);
});
test('widget header describes a ticket viewer, not a read-only connector',()=>{
 const html=fs.readFileSync('src/dynamix_manager/plugin_app.html','utf8');
 const header=html.match(/<header>([\s\S]*?)<\/header>/)[1];
 assert.match(header,/InfoTech Tickets · Ticket viewer/);
 assert.doesNotMatch(html,/Read only/i);
});
