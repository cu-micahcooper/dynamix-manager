"use strict";
const $ = id => document.getElementById(id);
let session, queue = [], index = 0, ticket, visibility = "public", suggestion, preview, busy = false, composerReady = false, draftRevision = null;
const show = (id, on = true) => { $(id).hidden = !on; };
const text = (id, value) => { $(id).textContent = value ?? ""; };
function notice(message = "") { text("notice", message); show("notice", !!message); }
function date(value) { if (!value) return "Not set"; const d = new Date(value); return isNaN(d) ? value : d.toLocaleString(); }
async function api(path, method = "GET", data) {
 const response = await fetch("/api" + path, {method, headers: {"Content-Type":"application/json", "X-CSRF-Token":session?.csrf_token || ""}, ...(data !== undefined ? {body:JSON.stringify(data)} : {})});
 const result = await response.json();
 if (!response.ok) { const e = new Error(typeof result.detail === "string" ? result.detail : "Request could not be completed."); e.status = response.status; throw e; }
 return result;
}
async function run(action) {
 if (busy) return;
 busy = true; notice();
 const buttons = [...document.querySelectorAll("button, input, textarea, select")]; buttons.forEach(b => b.disabled = true);
 try { await action(); } catch(e) { notice(e.message); if(e.status === 409 && /uncertain|pending/i.test(e.message)) show("uncertain"); if($("preview").open) $("preview").close(); }
 finally { busy = false; buttons.forEach(b => b.disabled = false); if(!composerReady) for(const id of ["draft","instructions","status","save","review","review-close","generate","ack-stale"]) $(id).disabled=true; }
}
function renderSession() {
 show("saved-login", !!session.capabilities?.td_login_saved);
 show("ai-settings",session.authenticated); show("login", !session.authenticated); show("workspace", session.authenticated); show("logout", session.authenticated);
 show("capability-notice",session.authenticated && session.mode === "live"); text("capability-notice", "Live ticket reads connected. " + (session.capabilities?.submission_contract_verified ? "Live submissions require your explicit review." : "Live submission is disabled; drafts remain available.") + (session.capabilities?.ai_configured ? "" : " Configure your OpenAI key and model in AI settings for suggestions."));
 text("account", session.authenticated ? `${session.user?.name || "Signed in"} · ${session.mode === "demo" ? "DEMO" : "LIVE"}` : "Runs on your computer");
}
async function login(mode, useSaved = false) {
 session = await api("/login", "POST", {mode,username:$("username").value,password:$("password").value,token:$("token").value,remember:$("remember-login").checked,use_saved:useSaved});
 $("password").value = ""; $("token").value = ""; renderSession(); await loadQueue();
}
function element(tag, value, cls) { const el = document.createElement(tag); el.textContent = value ?? ""; if(cls) el.className = cls; return el; }
function richText(node, plain, sanitized) {
 // Only the backend sanitizer's dedicated HTML fields reach this sink.
 node.classList.add("rich-text");
 if(typeof sanitized === "string") node.innerHTML=sanitized;
 else node.textContent=plain || "";
 for(const link of node.querySelectorAll("a[href]")){link.target="_blank";link.rel="noopener noreferrer";}
}
async function loadQueue() {
 const result = await api("/queue"); queue = result.tickets; index = Math.min(index, Math.max(0,queue.length - 1));
 text("queue-warning", result.warning || (!result.complete ? "Queue completeness has not been verified." : "")); show("queue-warning", !!$("queue-warning").textContent);
 text("return-skipped", `Return to skipped (${Array.isArray(result.skipped) ? result.skipped.length : result.skipped || 0})`);
 await loadTicket();
}
async function loadTicket() {
 show("empty", !queue.length); show("ticket-view", !!queue.length); text("progress", queue.length ? `${index+1} of ${queue.length}` : "Pass complete");
 if (!queue.length) {ticket = null; return;}
 composerReady=false; $("draft").value=""; $("instructions").value="";
 ticket = await api(`/tickets/${queue[index].id}`); visibility = "public"; $("visibility").value = visibility;
 renderPDFs();
 text("ticket-id", `TICKET #${ticket.id}`); text("ticket-title",ticket.title); richText($("description"),ticket.description,ticket.description_html); text("history-label", ticket.history_complete ? "Full history" : "Incomplete history");
 $("remote").removeAttribute("href"); if (/^https:\/\//i.test(ticket.url || "")) $("remote").href = ticket.url;
 $("ticket-meta").replaceChildren(...[ticket.status, `Priority ${ticket.priority}`, `Due ${date(ticket.due)}`, ticket.requester?.name].filter(Boolean).map(v=>element("span",v)));
 $("history").replaceChildren();
 for (const entry of ticket.history || []) { const row = element("article","", "entry" + (entry.private ? " private" : "")); const head = element("div","","entry-head"); head.append(element("strong",entry.author),element("span",date(entry.created),"muted"),element("span",entry.private ? "Internal" : "Public","badge")); const body=element("div","","prose");richText(body,entry.text,entry.text_html);row.append(head,body); $("history").append(row); }
 $("status").replaceChildren(new Option("Keep current status", "")); for(const s of ticket.statuses || []) $("status").append(new Option(s.name,String(s.id)));
 $("suggestions").replaceChildren(element("p", "Generate suggestions when you’re ready.")); show("replacement",false); show("uncertain",!!ticket.uncertain); suggestion = null; preview = null; show("generation-status",false);
 await loadDraft();
}
function renderPDFs() {
 const box = $("pdf-links"); box.replaceChildren();
 for(const attachment of ticket.attachments || []) {
  const link = element("a", "View PDF · " + attachment.name, "pdf-link");
  link.href = `/api/tickets/${ticket.id}/attachments/${encodeURIComponent(attachment.id)}/pdf`;
  link.target = "_blank"; link.rel = "noopener noreferrer";
  box.append(link);
 }
}
function renderRecipients(selected) {
 $("recipients").replaceChildren(element("legend","Notify"));
 for (const person of visibility === "internal" ? [] : ticket.recipients || []) { const label = element("label","","check"); const cb = document.createElement("input"); cb.type="checkbox"; cb.value=String(person.id); cb.checked=(selected || []).map(String).includes(String(person.id)); cb.addEventListener("change", dirty); label.append(cb,document.createTextNode(person.name || person.email || person.id)); $("recipients").append(label); }
 if (visibility === "internal" || !(ticket.recipients || []).length) $("recipients").append(element("p","No notifications will be sent.","muted"));
}
async function loadDraft() {
 composerReady=false; draftRevision=null; $("draft").value=""; $("instructions").value=""; $("status").value=""; renderRecipients([]); text("save-state","Loading draft…");
 const draft = await api(`/tickets/${ticket.id}/draft?visibility=${visibility}`);
 $("draft").value=draft.text || ""; $("instructions").value=draft.instructions || ""; $("status").value=draft.status_id == null ? "" : String(draft.status_id); renderRecipients(draft.recipients);
 draftRevision=draft.source_revision || ticket.source_revision; composerReady=true; show("stale-draft",!!draftRevision && draftRevision !== ticket.source_revision);
 text("save-state","Saved locally"); text("instruction-hint",visibility === "public" ? "Include only instructions intended for the public reply. Internal history is excluded from this AI request." : "Internal history may be included. This draft stays separate from your public reply.");
}
function payload() { if(!composerReady) throw new Error("Draft is not loaded. Refresh before editing or submitting."); return {source_revision:draftRevision,text:$("draft").value,instructions:$("instructions").value,visibility,recipients:[...$("recipients").querySelectorAll("input:checked")].map(x=>x.value),status_id:$("status").value ? Number($("status").value) : null}; }
function dirty() { text("save-state","Unsaved changes"); }
async function save() { if(!ticket || !composerReady) return; await api(`/tickets/${ticket.id}/draft`,"PUT",payload()); text("save-state","Saved locally"); }
async function move(step) {await save(); index = Math.max(0,Math.min(queue.length-1,index+step)); await loadTicket();}
async function generate() {
 text("generation-status","Generating suggestions… This can take up to 90 seconds."); show("generation-status"); text("generate","Generating…");
 try {
 await save(); const result = await api(`/tickets/${ticket.id}/suggest`,"POST",{visibility,instructions:$("instructions").value}); suggestion = result;
 const box=$("suggestions"); box.replaceChildren(element("p",result.summary));
 if(result.simulated) box.prepend(element("p","SIMULATED DEMO SUGGESTION","eyebrow"));
 for(const [key,title] of [["unresolved","Still unresolved"],["next_steps","Next steps"],["questions","Questions to ask"]]) { if(result[key]?.length) {box.append(element("h4",title)); const list=element("ul",""); for(const item of result[key]) list.append(element("li",item)); box.append(list);} }
 if(result.references?.length) box.append(element("p",`Source entries: ${result.references.join(", ")}`,"muted"));
 text("replacement-text",result.draft); show("replacement"); text("generation-status","Suggestions ready. Review the suggested draft below.");
 } catch(e) {text("generation-status",e.message); throw e;} finally {text("generate","Generate suggestions");}
}
function closureStatus(statuses = []) {
 const completed = statuses.filter(s => s.status_class === "completed");
 const closed = completed.filter(s => s.name.trim().toLowerCase() === "closed");
 return closed.length === 1 ? closed[0] : completed.length === 1 ? completed[0] : null;
}
async function reviewClose() {
 if(!composerReady) throw new Error("Draft is not loaded. Refresh before editing or submitting.");
 const selected = $("status").value;
 const explicit = (ticket.statuses || []).find(s => String(s.id) === selected && s.status_class === "completed");
 const closing = explicit || closureStatus(ticket.statuses);
 if(!closing) throw new Error("Choose a completed status in Status after submission before closing. If none is available, open the ticket in TeamDynamix.");
 $("status").value = String(closing.id);
 await review();
}
async function review() {
 await save(); if(draftRevision && draftRevision !== ticket.source_revision) throw new Error("This draft predates the current ticket. Review the updated history and acknowledge it before submission."); preview=await api(`/tickets/${ticket.id}/preview`,"POST",payload()); const box=$("preview-content");
 box.replaceChildren(element("h3",`#${preview.ticket_id} · ${preview.title}`),element("p",`${preview.visibility === "internal" ? "Internal note" : "Public reply"} · Notify: ${(preview.recipients || []).map(p=>p.name || p.email || p.id).join(", ") || "Nobody"}`),element("div",preview.text,"prose"),element("p",`Status: ${preview.current_status} → ${preview.new_status || preview.current_status}`));
 text("preview-warning", (session.mode === "demo" ? "Demo submission changes fictional data only. " : "This update will be sent to TeamDynamix. ") + (preview.warning || "")); $("preview").showModal();
}
async function submit() {
 const result=await api(`/tickets/${ticket.id}/submit`,"POST",{preview_id:preview.preview_id}); $("preview").close();
 if(result.status === "confirmed") {await loadQueue(); notice(session.mode === "demo" ? "Demo update saved. Ready for the next ticket." : "Update confirmed. Ready for the next ticket.");}
 else {notice(result.message || `Submission ${result.status}.`); show("uncertain",result.status === "uncertain");}
}
$("demo").onclick=()=>run(()=>login("demo")); $("login-form").onsubmit=e=>{e.preventDefault();run(()=>login("live"));};
$("logout").onclick=()=>run(async()=>{await save(); await api("/logout","POST",{}); session=await api("/session"); ticket=null;renderSession();});
$("refresh").onclick=()=>run(async()=>{await save();await loadQueue();});
$("previous").onclick=()=>run(()=>move(-1)); $("next").onclick=()=>run(()=>move(1)); $("save").onclick=()=>run(save);
$("visibility").onchange=()=>run(async()=>{const selected=$("visibility").value; try{await save();}catch(e){$("visibility").value=visibility;throw e;}visibility=selected;await loadDraft();show("replacement",false);suggestion=null;$("suggestions").replaceChildren(element("p","Generate suggestions for this visibility."));});
$("generate").onclick=()=>run(generate); $("use-draft").onclick=()=>run(async()=>{if(suggestion){$("draft").value=suggestion.draft;await save();show("replacement",false);}});
$("review").onclick=()=>run(review); $("review-close").onclick=()=>run(reviewClose); $("submit").onclick=()=>run(submit);
$("skip").onclick=()=>run(async()=>{await save();await api(`/tickets/${ticket.id}/skip`,"POST",{});await loadQueue();});
for(const id of ["reset-pass","return-skipped"]) $(id).onclick=()=>run(async()=>{await save();await api("/reset-pass","POST",{skipped_only:id === "return-skipped"});index=0;await loadQueue();});
$("reconcile").onclick=()=>run(async()=>{const r=await api(`/tickets/${ticket.id}/reconcile`,"POST",{});notice(r.message || `Outcome: ${r.status}`);if(r.status === "confirmed") await loadQueue();});
$("acknowledge").onclick=()=>run(async()=>{if(!$("ack-check").checked) throw new Error("Inspect the remote ticket and check the acknowledgement first.");await api(`/tickets/${ticket.id}/acknowledge`,"POST",{acknowledged:true});show("uncertain",false);$("ack-check").checked=false;});
for(const id of ["draft","instructions","status"]) $(id).addEventListener("input",dirty);
window.addEventListener("beforeunload",event=>{if($("save-state").textContent === "Unsaved changes"){event.preventDefault();event.returnValue="";}});
run(async()=>{session=await api("/session");renderSession();if(session.authenticated)await loadQueue();});

$("ai-settings").onclick=()=>{$("ai-model").value=session.capabilities?.model || ""; $("ai-dialog").showModal();};
$("close-ai").onclick=()=>$("ai-dialog").close();
$("ai-form").onsubmit=e=>{e.preventDefault();run(async()=>{await api("/ai-settings","POST",{api_key:$("ai-key").value,model:$("ai-model").value});$("ai-key").value="";$("ai-dialog").close();session=await api("/session");renderSession();notice("OpenAI settings saved to the local .env file.");});};

$("ack-stale").onclick=()=>run(async()=>{draftRevision=ticket.source_revision;await save();show("stale-draft",false);notice("Draft retained against the current ticket. Review your message before submitting.");});

$("use-saved-login").onclick=()=>run(()=>login("live",true));
$("forget-login").onclick=()=>run(async()=>{session=await api("/forget-login","POST",{});renderSession();notice("Saved TeamDynamix login removed from .env.");});
