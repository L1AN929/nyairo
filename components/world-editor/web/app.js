import {emptySpace,localPoint,snap5,spaceCopy,spacePaste,removeSpace,translateSpace,spaceSvg,assetUri,objectArt} from '/space.mjs';
import {DraftHistory, RecoveryStore, fingerprint, editableShape} from '/editor-state.mjs';
import {ref,entries,center,drop,translate,resize,boxSelect,copySelection,paste,arrange} from '/layout.mjs';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone = x => structuredClone(x);
const kinds = {chair:'椅子',bed:'床',book:'书',cup:'杯子',cabinet:'储物柜',table:'桌子',plant:'盆栽',dispenser:'饮水设备',lamp:'灯'};
const icons = {chair:'▤',bed:'▰',book:'▥',cup:'◡',cabinet:'▦',table:'⊞',plant:'♧'};
const verbs = {sit:'坐下',rest:'休息',read:'阅读',pick_up:'拿起',place:'放下',store:'收纳持有物',retrieve:'取出',drink:'喝一份饮品',fill:'给持有杯装水',toggle_device:'切换设备可用状态',toggle:'开关灯'};
const defaults = {chair:['sit'],bed:['rest'],book:['pick_up','place','read'],cup:['pick_up','place','drink'],cabinet:['store','retrieve'],table:[],plant:[],dispenser:['fill','toggle_device'],lamp:['toggle']};
let draft, draftRevision=0, dirty=false, edits=0, mode='edit', selected={type:'room',id:null}, trial=null, trialList=[], actionBusy=false, saveBusy=false, ready=false;
let selection=new Set(),view={x:0,y:0,zoom:1},tool='select',snap=true,clipboard=null,placing=null,ghostPoint=null,connectFrom=null,ignoreClick=false,spaceHeld=false,rulePreview=null;
let themes={},actionSpecs={},drawing={};
let recoveryClient=crypto.randomUUID(),recoverySequence=0,recoveryPending=Promise.resolve();
let importKind='world', toastTimer;
let history, savedPackage, savedFingerprint, recovery, recoveryBase, pendingBackups=[], recoveryConflict=false;
let backupTimer, backupStatus='无需恢复副本', backupUnavailable=false, dragMode=false, dragging=null;

function toast(message, error=false) {
  clearTimeout(toastTimer); $('toast').hidden=false; $('toast').textContent=message;
  $('toast').className=error?'error':'';
  toastTimer=setTimeout(()=>$('toast').hidden=true,error?12000:6500);
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Nyairo-Editor':'1'},body:JSON.stringify(data)});
  const result = await response.json();
  if(!response.ok) { const error=new Error(result.error || '操作失败，请重试。'); error.code=result.code; throw error; }
  return result;
}
function bind(id, fn) { $(id).addEventListener('click',()=>run(fn)); }
async function run(fn) { try { await fn(); } catch(e) { toast(e.message || '操作失败，请重试。', true); } }
async function confirm(title, message) {
  if($('confirm-dialog').open) return false;
  $('confirm-title').textContent=title; $('confirm-text').textContent=message;
  $('confirm-dialog').showModal();
  return new Promise(resolve=>$('confirm-dialog').addEventListener('close',()=>resolve($('confirm-dialog').returnValue==='ok'),{once:true}));
}
function source() { return mode==='trial' && trial ? trial.package : draft; }
function changed() {
  selectionClean();rulePreview=null;$('rule-results').textContent='内容已修改，请重新预览规则。';
  dirty=fingerprint(draft)!==savedFingerprint;edits++;
  if(!dirty&&!recoveryConflict)recoveryBase={revision:draftRevision,fingerprint:savedFingerprint};
  scheduleBackup();renderHeader();renderList();renderMap();
}
function touch({group=null}={}) { history?.record(draft,group);changed(); }
function scheduleBackup(){
  clearTimeout(backupTimer);
  backupStatus=dirty?'正在保留恢复副本':'无需恢复副本';
  backupTimer=setTimeout(flushBackup,250);
}
function flushBackup({unloading=false}={}){
  clearTimeout(backupTimer);if(!ready)return;
  let localOK=false;
  try {
    if(!recovery)throw new Error('本浏览器存储不可用');
    if(dirty)recovery.write(draft,recoveryBase.revision,recoveryBase.fingerprint);else recovery.clearOwn();
    localOK=true;
  }catch{/* A server-side recovery copy can still survive browser storage loss. */}
  const isDirty=dirty,sequence=++recoverySequence;
  const body=JSON.stringify({client_id:recoveryClient,sequence,package:isDirty?clone(draft):null,base_revision:recoveryBase.revision,base_fingerprint:recoveryBase.fingerprint});
  backupStatus=isDirty?'正在保留本机恢复副本':'无需恢复副本';
  $('backup-status').textContent=backupStatus;$('backup-status').classList.toggle('warning',backupUnavailable);
  const send=async()=>{
    const response=await fetch('/api/recovery',{method:'POST',headers:{'Content-Type':'application/json','X-Nyairo-Editor':'1'},body,keepalive:unloading&&new TextEncoder().encode(body).length<60000});
    if(!response.ok)throw new Error('恢复副本写入失败');
    if(sequence===recoverySequence){backupUnavailable=false;backupStatus=isDirty?'本机恢复副本已更新'+(localOK?'':'（浏览器存储不可用）'):'无需恢复副本';$('backup-status').textContent=backupStatus;$('backup-status').classList.remove('warning');}
  };
  const pending=unloading?send():recoveryPending.catch(()=>{}).then(send);
  recoveryPending=pending.catch(()=>{if(sequence===recoverySequence){backupUnavailable=true;backupStatus=localOK?'浏览器副本已保留 · 本机副本写入失败':'恢复副本不可用 · 请手动保存或导出';$('backup-status').textContent=backupStatus;$('backup-status').classList.add('warning');}});
}
async function preserveDraft(){
  const id='kept-'+crypto.randomUUID();await api('/api/recovery',{client_id:id,sequence:1,package:draft,base_revision:recoveryBase.revision,base_fingerprint:recoveryBase.fingerprint});
  try{recovery?.preserve(draft,recoveryBase.revision,recoveryBase.fingerprint);}catch{/* The durable copy above already protects this draft. */}
}
function renderRecovery(){
  $('recovery-banner').hidden=!pendingBackups.length;
  $('conflict-banner').hidden=!recoveryConflict;
  if(!pendingBackups.length)return;
  $('recovery-title').textContent=`本机发现 ${pendingBackups.length} 个未保存的草稿副本`;
  const old=$('backup-select').value;
  $('backup-select').innerHTML=pendingBackups.map(b=>`<option value="${esc(b.key)}">${esc(b.package.meta.name||'未命名世界')} · ${esc(new Date(b.updated).toLocaleString('zh-CN'))}</option>`).join('');
  if(pendingBackups.some(b=>b.key===old))$('backup-select').value=old;
  const selectedBackup=pendingBackups.find(b=>b.key===$('backup-select').value)||pendingBackups[0];
  $('recovery-description').textContent=selectedBackup.baseRevision!==draftRevision||selectedBackup.baseFingerprint!==savedFingerprint
    ?'这个副本基于旧版本。恢复后可编辑和导出，不能直接覆盖已更新的服务器草稿。'
    :'保存到服务器之前的修改可以找回来。恢复后请检查，再点击保存草稿。';
}
async function restoreDraft(){
  const b=pendingBackups.find(b=>b.key===$('backup-select').value);if(!b)return;
  if(dirty&&!await confirm('恢复未保存副本？','将打开所选副本。当前编辑内容已留有浏览器恢复副本；需要时也可先取消并导出。'))return;
  if(dirty)await preserveDraft();
  flushBackup();draft=upgradeEditable(b.package);history=new DraftHistory(draft);mode='edit';selection=new Set();
  recoveryBase={revision:b.baseRevision,fingerprint:b.baseFingerprint};
  recoveryConflict=b.baseRevision!==draftRevision||b.baseFingerprint!==savedFingerprint;
  pendingBackups=[];selected={type:'room',id:draft.initial.room_id};changed();renderAll();flushBackup();
  toast(recoveryConflict?'旧版本副本已打开，请导出以保留修改。':'草稿副本已恢复，请检查并保存。');
}
async function returnSaved(){
  if(!await confirm('回到已保存的草稿？','请先导出需要保留的修改。将重新打开服务器上已保存的草稿。'))return;
  const result=await api('/api/bootstrap');
  draft=clone(result.draft.package);draftRevision=result.draft.revision;savedPackage=clone(draft);savedFingerprint=fingerprint(draft);
  history=new DraftHistory(draft);mode='edit';recoveryConflict=false;pendingBackups=[];selected={type:'room',id:draft.initial.room_id};changed();renderAll();flushBackup();
}
function historyStep(direction){
  if(mode!=='edit'||!ready)return;
  if(dragging)finishDrag(false);
  const next=direction==='undo'?history.undo():history.redo();if(!next)return;
  draft=next;
  if(selected.type!=='world'&&!(selected.type==='room'?draft.rooms:draft.objects).some(x=>x.id===selected.id))selected={type:'room',id:draft.initial.room_id};
  changed();renderAll();
}
function renderHeader(){
  const p=source();if(!p)return;const edit=mode==='edit';
  document.body.classList.toggle('preview-mode',mode==='preview');
  $('world-title').textContent=p.meta.name||'未命名世界';$('world-description').textContent=p.meta.description||'添加区域、摆放物件，再亲自试运行。';
  $('save-indicator').textContent=mode==='trial'?`试运行版本 ${trial?.state.revision??0}`:dirty?'有未保存的修改':'草稿已保存在本机';
  $('stage-label').textContent=mode==='trial'?'居民的世界状态':mode==='preview'?'场景预览':'世界画布';
  for(const m of ['edit','preview','trial']){$(m+'-mode').classList.toggle('active',mode===m);$(m+'-mode').setAttribute('aria-pressed',mode===m);}
  $('trial-panel').hidden=mode!=='trial';$('edit-footer').hidden=!edit;$('arrange-toolbar').hidden=!edit||editDomain==='space';$('batch-toolbar').hidden=!edit||editDomain==='space';
  $('save-btn').disabled=mode==='trial'||saveBusy||recoveryConflict;$('export-btn').disabled=!ready;
  for(const id of ['add-room','world-settings','load-example','import-btn','new-world'])$(id).disabled=!edit;
  $('undo-btn').disabled=!edit||!history?.canUndo;$('redo-btn').disabled=!edit||!history?.canRedo;
  $('map').classList.toggle('draggable',dragMode&&edit);$('grid-toggle').checked=p.scene.grid;
  $('map-help').textContent=edit?(placing?`点击区域内放置${kinds[placing]}；Esc 取消。`:tool==='connect'?'依次点击两片区域创建双向连接。':'Shift 点击多选 · 区域右下角调整尺寸 · 方向键微调'):mode==='preview'?'最终场景外观 · 辅助线与选框已隐藏，尚未保存的内容保留。':'位置、路径、姿态、持有物与占用均以运行时状态为准。';
  if(edit&&editDomain==='space')$('map-help').textContent=spaceTool==='wall'?'拖出水平或竖直墙；吸附室内 5% 网格。':spaceTool==='floor'?'在当前区域拖出地面分区。':spaceTool==='door'||spaceTool==='window'?'点击墙壁添加门窗；属性中调整起点和长度。':'选择空间对象可拖动；墙端点、长度、门窗尺寸可在右侧修改。';
  $('backup-status').textContent=backupStatus;$('backup-status').classList.toggle('warning',backupUnavailable);
  const count=selectionKeys().size;$('selection-status').textContent=count?`已选 ${count} 个对象`:'选择一个区域或物件';
  for(const id of ['copy-btn','duplicate-btn','delete-selection'])$(id).disabled=!edit||!count;
  $('paste-btn').disabled=!edit||!clipboard;$('arrange-btn').disabled=!edit||count<2;
  document.querySelectorAll('[data-tool]').forEach(b=>b.classList.toggle('active',tool===b.dataset.tool));
  renderRecovery();
}
function renderList(){
  const p=source();if(!p)return;const query=$('search-input').value.trim().toLowerCase();$('room-count').textContent=p.rooms.length;
  $('room-list').innerHTML=p.rooms.map(r=>`<button class="room-row ${mode==='edit'?selectionKeys().has(ref('room',r.id))?'active':'':selected.type==='room'&&selected.id===r.id?'active':''}" data-room="${esc(r.id)}"><span class="room-symbol" aria-hidden="true">${r.kind==='outdoor'?'♧':'▧'}</span><span>${esc(r.name)}<small>${p.objects.filter(o=>(mode==='trial'?trial.state.objects[o.id].room_id:o.room_id)===r.id).length} 个物件${mode==='trial'&&trial.state.resident.room_id===r.id?' · 居民在这里':''}</small></span></button>`).join('');
  document.querySelectorAll('[data-room]').forEach(b=>b.onclick=e=>{if(mode==='edit'&&tool==='connect')connectClick(b.dataset.room);else select('room',b.dataset.room,e.shiftKey);});
  const objects=p.objects.filter(o=>(o.name+' '+kinds[o.kind]).toLowerCase().includes(query));
  $('object-list').innerHTML=objects.length?objects.map(o=>{const s=mode==='trial'?trial.state.objects[o.id]:null,room=p.rooms.find(r=>r.id===(s?.room_id||o.room_id));return `<button class="object-row ${mode==='edit'?selectionKeys().has(ref('object',o.id))?'active':'':selected.id===o.id?'active':''}" data-list-object="${esc(o.id)}"><svg class="object-thumb" viewBox="-40 -30 80 60" aria-hidden="true">${objectArt(p,o,drawing)}</svg><span>${esc(o.name)}<small>${esc(room?.name||'')} ${s?.placement==='held'?'· 持有':s?.placement==='container'?'· 收纳':''}</small></span></button>`;}).join(''):'<p class="empty-hint">没有匹配的物件。可从下方素材创建。</p>';
  document.querySelectorAll('[data-list-object]').forEach(b=>b.onclick=e=>select('object',b.dataset.listObject,e.shiftKey));
  const ks=Object.keys(kinds).filter(k=>kinds[k].includes(query)&&(!$('asset-category').value||(['book','cup','dispenser'].includes(k)?'器具':['plant','lamp'].includes(k)?'装饰':'家具')===$('asset-category').value));
  $('palette').innerHTML=ks.length?ks.map(k=>`<button data-kind="${k}" class="${placing===k?'armed':''}" ${mode!=='edit'?'disabled':''}><svg viewBox="-40 -30 80 60" aria-hidden="true">${sprite(k)}</svg>${kinds[k]}</button>`).join(''):'<p class="empty-hint">没有匹配素材。</p>';
  document.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{if(dragging)finishDrag(false);placingAsset=null;placing=b.dataset.kind;tool='select';ghostPoint=null;renderAll();toast(`已选择${kinds[placing]}，在区域内点击放置；Esc 取消。`);});
}
function select(type,id,add=false){
  spatialSelection=null;assetFocus=null;if(editDomain==='space'&&type==='object')editDomain='objects';
  history?.endGroup();
  if(mode==='edit'&&['room','object'].includes(type)){const key=ref(type,id);if(add){const next=new Set(selectionKeys());next.has(key)?next.delete(key):next.add(key);selection=next;}else selection=new Set([key]);
    const first=entries(draft,selection)[0];selected=selection.has(key)?{type,id}:first?{type:first.type,id:first.item.id}:{type:'world',id:null};
  }else{selection=new Set();selected={type,id};}
  renderHeader();renderList();renderMap();renderInspector();if(mode==='edit')renderBatch();renderV04();
}
function themeVisual(kind='room'){const key=kind==='outdoor'?'street':kind==='shop'?'bookstore':'bedroom';return {theme:key,...Object.fromEntries(Object.entries(themes[key]).filter(([k])=>k!=='name'))};}
function behaviorDefaults(kind){return Object.fromEntries(defaults[kind].filter(v=>actionSpecs[v]?.parameters.length).map(v=>[v,Object.fromEntries(actionSpecs[v].parameters.map(param=>[param.key,param.default]))]));}
function upgradeEditable(p){p=clone(p);if(p.format.endsWith('.v1')){p.format='nyairo.world-package.v2';p.requires='nyairo.editor-runtime.v2';p.scene={background:'#f8f9fc',grid:true,decorations:true};p.rooms.forEach(r=>r.visual=themeVisual(r.kind));p.objects.forEach(o=>{o.visual={scale:100,rotation:0,layer:0,show_name:true};o.behavior=behaviorDefaults(o.kind);});}if(!p.format.endsWith('.v3')){p.format='nyairo.world-package.v3';p.requires='nyairo.editor-runtime.v3';p.assets={};p.rooms.forEach(r=>r.space=emptySpace());p.objects.forEach(o=>{o.asset_id=null;o.initial_status=o.kind==='cup'?(o.servings?'water':'empty'):'neutral';o.interaction={near:false,radius:2};});}return p;}
function applyPackage(next,keys=null){draft=next;if(keys){selection=keys;const e=entries(draft,keys)[0];selected=e?{type:e.type,id:e.item.id}:{type:'room',id:draft.initial.room_id};}touch();renderAll();}
function copyObjects(){if(spatialSelection){const {room}=spatialItem();spaceClipboard=spaceCopy(room,spatialSelection.type,spatialSelection.id);toast('空间对象已复制，墙壁包括依附门窗。');return;}clipboard=copySelection(draft,selectionKeys());renderHeader();toast(`已复制 ${clipboard.rooms.length} 个区域、${clipboard.objects.length} 个物件。`);}
function pasteObjects(duplicate=false){if(spatialSelection||editDomain==='space')return pasteSpatial(duplicate);const clip=duplicate?copySelection(draft,selectionKeys()):clipboard;const target=duplicate?null:selected.type==='room'?selected.id:draft.objects.find(o=>o.id===selected.id)?.room_id;const result=paste(draft,clip,target);applyPackage(result.package,result.keys);toast('副本已创建；一次撤销可移除整批副本。');}
function layerObjects(front){const es=entries(draft,selectionKeys()).filter(e=>e.type==='object');if(!es.length)return;const edge=(front?Math.max:Math.min)(0,...draft.objects.map(o=>o.visual.layer));for(const e of es)e.item.visual.layer=Math.max(-100,Math.min(100,edge+(front?1:-1)));touch();renderAll();}
async function newWorld(){
  if(!await confirm('创建空白世界？','将替换编辑草稿，已有试运行保留；本次替换可撤销。未保存内容会另存恢复副本。'))return;
  if(dirty)await preserveDraft();
  const id='room-'+crypto.randomUUID().slice(0,12);const p={format:'nyairo.world-package.v3',requires:'nyairo.editor-runtime.v3',assets:{},meta:{id:'world-'+crypto.randomUUID().slice(0,12),name:'新的世界',author:'创作者',version:'0.1.0',description:'',license:'CC0-1.0',cover:'generated-floorplan'},scene:{background:'#f8f9fc',grid:true,decorations:true},rooms:[{id,name:'第一个区域',description:'',kind:'room',x:150,y:140,w:520,h:340,hours:null,visual:themeVisual(),space:emptySpace(true)}],connections:[],objects:[],initial:{room_id:id,time:'2026-10-11T09:00',resident_name:'测试居民'}};
  view={x:0,y:0,zoom:1};placing=null;connectFrom=null;tool='select';applyPackage(p,new Set([ref('room',id)]));$('guide-panel').open=true;
}
function renderBatch(){
  const es=entries(draft,selectionKeys());if(es.length<2)return;
  const same=new Set(es.map(e=>e.type)).size===1,type=es[0].type;
  const container=document.createElement('div');container.className='batch-properties';container.innerHTML=`<strong>已选择 ${es.length} 个对象</strong><p>拖动选中对象整体移动；一次批量修改作为一步撤销。</p>${same?(type==='room'?`<label>批量区域主题<select id="batch-value">${Object.entries(themes).map(([k,v])=>`<option value="${k}">${v.name}</option>`).join('')}</select></label>`:'<label>批量视觉尺寸 %<input id="batch-value" type="number" value="100" min="40" max="200"></label>')+'<button id="batch-apply">应用到全部选中对象</button>':''}`;
  $('inspector').prepend(container);if($('batch-apply'))$('batch-apply').onclick=()=>run(()=>{const val=$('batch-value').value;if(type==='room'){for(const e of es)e.item.visual={theme:val,...Object.fromEntries(Object.entries(themes[val]).filter(([k])=>k!=='name'))};}else{const number=Number(val);if(!Number.isInteger(number)||number<40||number>200)throw new Error('视觉尺寸需要是 40–200 的整数。');for(const e of es)e.item.visual.scale=number;}touch();renderAll();});
}
async function previewRules(){
  const value=$('rule-time').value.trim();if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(value))throw new Error('测试时间请填写 00:00–23:59，例如 23:00。');const [h,m]=value.split(':').map(Number);const result=await api('/api/rules/preview',{package:draft,minute:h*60+m});
  rulePreview=new Map(result.rooms.map(r=>[r.id,r.open]));$('feedback-panel').open=true;
  $('rule-results').innerHTML=result.rooms.map(r=>`<div class="rule-row"><span>${esc(draft.rooms.find(x=>x.id===r.id).name)}</span><strong class="${r.open?'open':'closed'}">${r.open?'时间允许进入':'当前关闭'}</strong></div>`).join('')+'<p class="field-hint">仅预览开放时间。真实移动还需直接连接并释放当前家具；不会改变试运行时间。</p>';renderMap();
}
function verbLabel(v,o){return ['read','rest'].includes(v)?`${verbs[v]} ${o.behavior[v].minutes} 分钟`:verbs[v];}

function selectionKeys(){return selection.size?selection:new Set(selected.id&&['room','object'].includes(selected.type)?[ref(selected.type,selected.id)]:[]);}
function selectionClean(){selection=new Set([...selection].filter(k=>entries(draft,new Set([k])).length));if(selection.size&&!selection.has(ref(selected.type,selected.id))){const e=entries(draft,selection)[0];selected={type:e.type,id:e.item.id};}}
function sprite(kind,scale=1,rotation=0){return `<g transform="rotate(${rotation}) scale(${scale})">${drawing[kind]||''}</g>`;}
function roomOpen(room,minutes){if(!room.hours)return true;const h=Math.floor(minutes/60)%24,{open,close}=room.hours;return open<close?h>=open&&h<close:h>=open||h<close;}
function trialMinute(){const t=trial.package.initial.time.slice(11).split(':').map(Number);return t[0]*60+t[1]+trial.state.minutes;}
function renderMap(){
  const p=source();if(!p)return;const map=$('map'),edit=mode==='edit',preview=mode==='preview',rooms=Object.fromEntries(p.rooms.map(r=>[r.id,r]));
  map.setAttribute('viewBox',`${view.x} ${view.y} ${1000/view.zoom} ${620/view.zoom}`);$('zoom-label').textContent=Math.round(view.zoom*100)+'%';
  map.classList.toggle('tool-pan',tool==='pan');map.classList.toggle('tool-box',edit&&tool==='box');map.classList.toggle('placing',!!placing&&edit);
  let html='<defs><pattern id="grid" width="25" height="25" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#cbd6e9"/></pattern></defs>';
  html+=`<rect x="-10000" y="-10000" width="20000" height="20000" fill="${esc(p.scene.background)}"/>`;
  if(edit&&p.scene.grid)html+='<rect width="1000" height="620" fill="url(#grid)"/>';
  for(const [i,e] of p.connections.entries()){
    const a=rooms[e.from],b=rooms[e.to];if(!a||!b)continue;
    if(!preview)html+=`<path class="map-edge" data-edge="${i}" d="M ${a.x+a.w/2} ${a.y+a.h/2} L ${b.x+b.w/2} ${b.y+b.h/2}" stroke="${selected.type==='connection'&&selected.id===String(i)?'#396be4':'#91a7bb'}" fill="none" stroke-width="3" stroke-dasharray="8 6"/>`;
  }
  for(const r of p.rooms){
    const v=r.visual,active=edit&&selectionKeys().has(ref('room',r.id)),candidate=edit&&tool==='connect'&&connectFrom&&connectFrom!==r.id;
    const closed=mode==='trial'?!roomOpen(r,trialMinute()):rulePreview?.get(r.id)===false;
    const pid='floor-'+r.id,size=v.texture==='planks'?60:30,lines=v.texture==='planks'?'<path d="M0 0H60M0 30H60M30 0V30M0 30V60"/>':'<path d="M0 0H30V30H0Z"/>';
    if(v.texture!=='none')html+=`<defs><pattern id="${esc(pid)}" width="${size}" height="${size}" patternUnits="userSpaceOnUse"><rect width="${size}" height="${size}" fill="${esc(v.floor)}"/><g fill="none" stroke="${esc(v.border)}" opacity=".18" stroke-width="1">${lines}</g></pattern></defs>`;
    const fill=v.texture==='none'?v.floor:`url(#${pid})`,nameLimit=Math.max(3,Math.min(24,Math.floor((r.w-40)/17)));
    html+=`<g class="map-room ${active?'selected':''} ${closed&&!preview?'room-closed':''} ${candidate?'connect-candidate':''}" role="button" tabindex="${preview?-1:0}" aria-label="选择区域 ${esc(r.name)}" data-map-room="${esc(r.id)}"><rect class="room-bg" x="${r.x}" y="${r.y}" width="${Math.max(0,r.w)}" height="${Math.max(0,r.h)}" rx="10" fill="${esc(fill)}" stroke="${v.border_style==='none'?'none':esc(v.border)}" stroke-width="2" ${v.border_style==='dashed'?'stroke-dasharray="8 5"':''}/>`;
    html+=spaceSvg(r,p,mode==='trial'?trial.state:null,spatialSelection,edit?visibleLayers:{floors:true,walls:true},'floor',edit&&editDomain==='space');
    if(p.scene.decorations)html+=`<rect x="${r.x+10}" y="${r.y+10}" width="${Math.max(0,r.w-20)}" height="38" rx="5" fill="${esc(v.background)}" pointer-events="none"/>`;
    html+=`<text class="room-name" x="${r.x+20}" y="${r.y+32}">${esc(r.name.length>nameLimit?r.name.slice(0,nameLimit-1)+'…':r.name)}</text>`;
    if(!preview)html+=`<text class="room-kind" x="${r.x+20}" y="${r.y+63}">${r.hours?`${r.hours.open}:00–${r.hours.close}:00${r.hours.open>r.hours.close?' · 跨夜':''}`:'全天开放'}${closed?' · 当前关闭':''}</text>`;
    html+='</g>';
  }
  for(const r of p.rooms)html+=spaceSvg(r,p,mode==='trial'?trial.state:null,spatialSelection,edit?visibleLayers:{walls:true,floors:true},'walls',edit&&editDomain==='space');
  if(mode==='trial'&&trial.state.path.points.length){const r=rooms[trial.state.path.room_id];html+=`<polyline class="walk-path" points="${trial.state.path.points.map(pos=>`${r.x+r.w*(pos.x+.5)/20},${r.y+r.h*(pos.y+.5)/20}`).join(' ')}" fill="none" stroke="#789dbc" stroke-width="2" stroke-dasharray="4 4" pointer-events="none"/>`;}
  for(const o of [...p.objects].sort((a,b)=>a.visual.layer-b.visual.layer)){
    if(edit&&!visibleLayers.objects)continue;const state=mode==='trial'?trial.state.objects[o.id]:null;if(state&&state.placement!=='room')continue;
    const room=rooms[state?.room_id||o.room_id];if(!room)continue;
    const x=room.x+room.w*(state&&room.space.enabled?(state.position.x+.5)*5:o.x)/100,y=room.y+room.h*(state&&room.space.enabled?(state.position.y+.5)*5:o.y)/100,v=o.visual,scale=v.scale/100,active=edit&&selectionKeys().has(ref('object',o.id)),occupied=mode==='trial'&&trial.state.resident.using===o.id;
    html+=`<g class="map-item ${active?'selected':''} ${occupied?'occupied':''}" transform="translate(${x},${y})" role="button" tabindex="${preview?-1:0}" aria-label="选择物件 ${esc(o.name)}" data-map-object="${esc(o.id)}"><rect class="item-bg" x="${-42*scale}" y="${-33*scale}" width="${84*scale}" height="${66*scale}" rx="7"/>${objectArt(p,o,drawing,scale,v.rotation)}`;
    if(v.show_name)html+=`<text class="item-name" text-anchor="middle" y="${33*scale+17}">${esc(o.name.slice(0,20))}</text>`;
    if(occupied)html+=`<text class="occupied-label" text-anchor="middle" y="${-33*scale-6}">使用中</text>`;
    if(state&&o.kind==='cup')html+=`<text class="state-badge" x="${24*scale}" y="${-25*scale}">${state.servings?'装水 '+state.servings+' 份':'空杯'}</text>`;
    if(state&&['dispenser','lamp'].includes(o.kind))html+=`<text class="state-badge" text-anchor="middle" y="${-33*scale-6}">${statusNames[state.status]}</text>`;
    if(state&&o.kind==='cabinet'){const count=Object.values(trial.state.objects).filter(s=>s.container_id===o.id).length;html+=`<text class="state-badge" text-anchor="middle" y="${-33*scale-6}">收纳 ${count}/${o.capacity}</text>`;}
    html+='</g>';
  }
  if(mode==='trial'&&trial){
    const r=trial.state.resident,room=rooms[r.room_id],using=p.objects.find(o=>o.id===r.using);let x=room.x+room.w/2,y=room.y+room.h-40;
    if(room.space.enabled){x=room.x+room.w*(r.position.x+.5)/20;y=room.y+room.h*(r.position.y+.5)/20;}
    if(using&&!room.space.enabled){x=room.x+room.w*using.x/100+35;y=room.y+room.h*using.y/100;}
    const posture=r.pose==='lying'?'<g transform="rotate(-90)"><circle cy="-14" r="7"/><path d="M0 -5V15M-9 3H9M-6 25L0 15L6 25" stroke-width="6"/></g>':r.pose==='sitting'?'<circle cy="-18" r="7"/><path d="M0 -8V7H13V24M-10 -2L0 5" stroke-width="6"/>':'<circle cy="-19" r="7"/><path d="M0 -9V9M-11 -3L0 2L11 -3M-8 25L0 9L8 25" stroke-width="6"/>';
    html+=`<g id="resident-marker" transform="translate(${x},${y})" data-pose="${r.pose}" data-room="${esc(r.room_id)}" aria-label="居民${esc(({standing:'站立',sitting:'坐下',lying:'躺下'})[r.pose])}"><circle r="30" fill="#fff" opacity=".95"/><g fill="#4f79da" stroke="#4f79da" stroke-linecap="round" stroke-linejoin="round">${posture}</g>`;
    if(r.held){const item=p.objects.find(o=>o.id===r.held);html+=`<g data-held="${esc(item.id)}" transform="translate(32,-6)">${objectArt(p,item,drawing,.55)}</g>`;}
    html+=`<text y="47" text-anchor="middle" fill="#315dc5" font-size="11">${esc(r.name)} · ${esc(({standing:'站立',sitting:'坐着',lying:'躺下'})[r.pose])}</text></g>`;
  }
  if(edit&&selectionKeys().size===1&&selected.type==='room'){
    const r=rooms[selected.id];if(r)html+=`<rect class="resize-handle" data-resize="${esc(r.id)}" x="${r.x+r.w-7}" y="${r.y+r.h-7}" width="14" height="14" rx="3" role="button" tabindex="0" aria-label="调整区域尺寸（拖动或方向键）"/>`;
  }
  if(edit&&placing&&ghostPoint){const change=drop(p,ghostPoint);if(change)html+=`<g opacity=".55" transform="translate(${ghostPoint.x},${ghostPoint.y})" pointer-events="none">${placingAsset?objectArt(p,{asset_id:placingAsset,kind:placing},drawing):sprite(placing)}<text y="43" text-anchor="middle" fill="#396be4" font-size="12">点击放置${kinds[placing]}</text></g>`;}
  if(dragging?.kind==='box'&&dragging.rect){const b=dragging.rect;html+=`<rect class="selection-box" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}"/>`;}
  map.innerHTML=html;
}
function mapPoint(e,matrix=null){const m=matrix||$('map').getScreenCTM()?.inverse();return m?new DOMPoint(e.clientX,e.clientY).matrixTransform(m):null;}
function focusMapItem(){if(!selected.id)return;const attr=selected.type==='room'?'data-map-room':'data-map-object';$('map').querySelector(`[${attr}="${CSS.escape(selected.id)}"]`)?.focus({preventScroll:true});}
function setZoom(next,anchor=null){next=Math.min(4,Math.max(.5,next));anchor||={x:view.x+500/view.zoom,y:view.y+310/view.zoom};view.x=anchor.x-(anchor.x-view.x)*view.zoom/next;view.y=anchor.y-(anchor.y-view.y)*view.zoom/next;view.zoom=next;renderMap();}
function setTool(next){if(dragging)finishDrag(false);tool=next;placing=null;connectFrom=null;ghostPoint=null;renderAll();toast(next==='connect'?'先点起点区域，再点目标区域。':next==='box'?'在画布拖出方框，以中心点选中指定类型的对象。':next==='pan'?'拖动画布平移视图；全图按钮可以复位。':'Shift 点击多选；打开拖拽后可以移动。');}
function finishDrag(commit){
  const d=dragging;if(!d)return;dragging=null;if($('map').hasPointerCapture(d.pointerId))$('map').releasePointerCapture(d.pointerId);
  $('map').classList.remove('dragging','invalid-drop');
  if(d.kind==='pan'){if(!commit)view=d.viewBefore;}
  else if(d.kind==='box'){
    if(commit&&d.rect){const found=boxSelect(d.before,d.rect,$('box-target').value);selection=d.add?new Set([...selection,...found]):new Set(found);const first=entries(draft,selection)[0];selected=first?{type:first.type,id:first.item.id}:{type:'world',id:null};}
  }else if(d.kind==='place'){if(commit&&placing)run(()=>addObject(placing,d.point));}
  else if(d.moved&&commit&&d.valid)touch();else draft=d.before;
  ignoreClick=true;setTimeout(()=>ignoreClick=false,0);renderAll();focusMapItem();
  if(d.moved&&commit&&d.valid===false)toast('整批物件需要落在区域内，已恢复整批原位置。');
}
function connectClick(id){
  if(!connectFrom){connectFrom=id;select('room',id);renderMap();toast('起点已选中，再点另一片区域。');return;}
  if(id===connectFrom){connectFrom=null;renderMap();return;}
  if(draft.connections.some(e=>new Set([e.from,e.to]).has(id)&&new Set([e.from,e.to]).has(connectFrom))){toast('这两个区域已经连接。');return;}
  draft.connections.push({from:connectFrom,to:id});connectFrom=null;touch();select('room',id);toast('双向连接已创建，可撤销。');
}
$('drag-toggle').onchange=e=>{if(dragging)finishDrag(false);dragMode=e.target.checked;renderHeader();};
$('grid-toggle').onchange=e=>{draft.scene.grid=e.target.checked;touch();};
$('snap-toggle').onchange=e=>{snap=e.target.checked;};
document.querySelectorAll('[data-tool]').forEach(b=>b.onclick=()=>setTool(b.dataset.tool));
$('map').addEventListener('click',e=>{
  if(ignoreClick||mode==='preview'||dragging||placing)return;
  const obj=e.target.closest('[data-map-object]'),room=e.target.closest('[data-map-room]'),edge=e.target.closest('[data-edge]');
  if(mode==='edit'&&tool==='connect'){if(room)connectClick(room.dataset.mapRoom);return;}
  if(tool==='box'||tool==='pan')return;
  if(obj)select('object',obj.dataset.mapObject,e.shiftKey||e.ctrlKey||e.metaKey);
  else if(room)select('room',room.dataset.mapRoom,e.shiftKey||e.ctrlKey||e.metaKey);
  else if(edge&&mode==='edit')select('connection',edge.dataset.edge);
  else if(mode==='edit'&&!e.target.hasAttribute('data-resize'))select('world',null);
});
$('map').addEventListener('pointerdown',e=>{
  $('context-menu').hidden=true;if(!ready||dragging||$('confirm-dialog').open||e.button===2)return;
  const matrix=$('map').getScreenCTM()?.inverse(),point=mapPoint(e,matrix);if(!point)return;
  const base={start:point,startClient:{x:e.clientX,y:e.clientY},pointerId:e.pointerId,matrix,moved:false,valid:true};
  if(e.button===1||spaceHeld||tool==='pan'){dragging={...base,kind:'pan',viewBefore:{...view}};}
  else if(mode!=='edit'||e.button!==0)return;
  else if(placing){dragging={...base,kind:'place',point};}
  else if(tool==='box'){dragging={...base,kind:'box',before:clone(draft),add:e.shiftKey};}
  else if(e.target.hasAttribute('data-resize')){const id=e.target.dataset.resize;dragging={...base,kind:'resize',id,before:clone(draft),origin:clone(draft.rooms.find(r=>r.id===id))};}
  else if(tool==='select'&&dragMode){const el=e.target.closest('[data-map-object],[data-map-room]');if(!el)return;
    const type=el.hasAttribute('data-map-object')?'object':'room',id=type==='object'?el.dataset.mapObject:el.dataset.mapRoom,key=ref(type,id);
    if(!selectionKeys().has(key))select(type,id,e.shiftKey);if(!selectionKeys().has(key))return;
    dragging={...base,kind:'move',before:clone(draft),keys:new Set(selectionKeys())};history.endGroup();
  }else return;
  e.preventDefault();$('map').setPointerCapture(e.pointerId);
});
$('map').addEventListener('pointermove',e=>{
  const d=dragging;if(!d){if(mode==='edit'&&placing){ghostPoint=mapPoint(e);renderMap();}return;}if(e.pointerId!==d.pointerId)return;
  const point=mapPoint(e,d.matrix);if(!point)return;d.point=point;
  if(Math.hypot(e.clientX-d.startClient.x,e.clientY-d.startClient.y)<4&&!d.moved)return;
  e.preventDefault();d.moved=true;const dx=point.x-d.start.x,dy=point.y-d.start.y;
  if(d.kind==='pan'){view.x=d.viewBefore.x-dx;view.y=d.viewBefore.y-dy;}
  else if(d.kind==='box')d.rect={x:Math.min(point.x,d.start.x),y:Math.min(point.y,d.start.y),w:Math.abs(dx),h:Math.abs(dy)};
  else if(d.kind==='resize'){draft=clone(d.before);Object.assign(draft.rooms.find(r=>r.id===d.id),resize(d.origin,dx,dy,snap&&!e.altKey?25:0));}
  else if(d.kind==='move'){const next=translate(d.before,d.keys,dx,dy,snap&&!e.altKey?25:0);d.valid=!!next;draft=next||clone(d.before);}
  else if(d.kind==='place')ghostPoint=point;
  renderMap();$('map').classList.add('dragging');$('map').classList.toggle('invalid-drop',!d.valid);
});
$('map').addEventListener('pointerleave',()=>{if(!dragging&&ghostPoint){ghostPoint=null;renderMap();}});
$('map').addEventListener('pointerup',e=>{if(dragging?.pointerId===e.pointerId)finishDrag(true);});
$('map').addEventListener('pointercancel',()=>finishDrag(false));
$('map').addEventListener('lostpointercapture',()=>{if(dragging)finishDrag(false);});
$('map').addEventListener('wheel',e=>{if(e.ctrlKey||e.metaKey){e.preventDefault();setZoom(view.zoom*(e.deltaY<0?1.15:1/1.15),mapPoint(e));}},{passive:false});
$('map').addEventListener('keydown',e=>{
  if(['Enter',' '].includes(e.key)&&e.target!==$('map')){e.preventDefault();const el=e.target.closest('[data-map-object],[data-map-room]');if(el)select(el.hasAttribute('data-map-object')?'object':'room',el.dataset.mapObject||el.dataset.mapRoom,e.shiftKey);return;}
  if(mode!=='edit'||e.ctrlKey||e.metaKey||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;
  const el=e.target.closest('[data-map-object],[data-map-room],[data-resize]');if(!el)return;e.preventDefault();
  const x=e.key==='ArrowLeft'?-1:e.key==='ArrowRight'?1:0,y=e.key==='ArrowUp'?-1:e.key==='ArrowDown'?1:0,step=e.shiftKey?20:e.altKey?1:5;
  if(el.hasAttribute('data-resize')){const r=draft.rooms.find(r=>r.id===el.dataset.resize);Object.assign(r,resize(r,x*step,y*step));touch({group:'resize:'+r.id});renderInspector();$('map').querySelector('[data-resize]')?.focus({preventScroll:true});}
  else{const type=el.hasAttribute('data-map-object')?'object':'room',id=el.dataset.mapObject||el.dataset.mapRoom;if(!selectionKeys().has(ref(type,id)))select(type,id);const next=translate(draft,selectionKeys(),x*step,y*step,0);if(next){draft=next;touch({group:'keyboard-move'});renderInspector();focusMapItem();}}
});
$('map').addEventListener('keyup',()=>history?.endGroup());
$('map').addEventListener('contextmenu',e=>{
  if(mode!=='edit')return;e.preventDefault();const el=e.target.closest('[data-map-object],[data-map-room]');if(!el)return;
  const type=el.hasAttribute('data-map-object')?'object':'room',id=el.dataset.mapObject||el.dataset.mapRoom;if(!selectionKeys().has(ref(type,id)))select(type,id);
  const menu=$('context-menu');menu.style.left=Math.min(e.clientX,innerWidth-225)+'px';menu.style.top=Math.max(5,Math.min(e.clientY,innerHeight-260))+'px';menu.hidden=false;
  $('ctx-connect').disabled=type!=='room';$('ctx-front').disabled=type!=='object';$('ctx-back').disabled=type!=='object';
});

function field(label,key,value,type='text',extra='') {
  if(type==='color')return `<label class="field">${label}<span class="color-field"><input type="color" data-color-for="${key}" value="${/^#[0-9a-f]{6}$/i.test(value)?value:'#ffffff'}" aria-label="${label}拾色器"><input type="text" data-field="${key}" value="${esc(value)}" maxlength="7" pattern="#[0-9a-fA-F]{6}" aria-label="${label}颜色值"></span></label>`;
  if(type==='textarea')return `<label class="field">${label}<textarea data-field="${key}" ${extra}>${esc(value)}</textarea></label>`;
  return `<label class="field">${label}<input type="${type}" data-field="${key}" value="${esc(value)}" ${extra}></label>`;
}
function choices(label,key,value,options) { return `<label class="field">${label}<select data-field="${key}">${options.map(([k,n])=>`<option value="${esc(k)}" ${k===value?'selected':''}>${esc(n)}</option>`).join('')}</select></label>`; }
function bindFields(target, callback=()=>{}) {
  $('inspector').querySelectorAll('[data-field]').forEach(el=> {
    const update=()=> { const key=el.dataset.field; const val=el.type==='number'||el.type==='range'||key.startsWith('hours.')?Number(el.value):el.value;
      const bits=key.split('.'); let obj=target;
      for(const b of bits.slice(0,-1))obj=obj[b]; obj[bits.at(-1)]=val;
      touch({group:`${selected.type}:${selected.id}:${key}`}); callback(key); };
    el.addEventListener('focus',()=>history?.endGroup());
    el.addEventListener('blur',()=>history?.endGroup());
    el.addEventListener(el.tagName==='SELECT'?'change':'input',update);
  });
  $('inspector').querySelectorAll('[data-color-for]').forEach(el=>el.oninput=()=>{const text=$('inspector').querySelector(`[data-field="${el.dataset.colorFor}"]`);text.value=el.value;text.dispatchEvent(new Event('input',{bubbles:true}));});
}
function roomVisualFields(item){return '<details class="visual-section" open><summary>区域视觉</summary>'+choices('主题预设','visual.theme',item.visual.theme,Object.entries(themes).map(([k,v])=>[k,v.name]))+'<div class="field-pair">'+field('地板颜色','visual.floor',item.visual.floor,'color')+field('标题背景','visual.background',item.visual.background,'color')+'</div>'+choices('基础纹理','visual.texture',item.visual.texture,[['none','纯色'],['planks','木地板'],['tiles','方砖']])+'<div class="field-pair">'+field('边框颜色','visual.border',item.visual.border,'color')+choices('区域边框','visual.border_style',item.visual.border_style,[['solid','实线'],['dashed','虚线'],['none','隐藏']])+'</div><p class="field-hint">预设仅是一组视觉属性，颜色与纹理可继续修改。</p></details>';}
function objectVisualFields(item){return '<details class="visual-section" open><summary>视觉尺寸与排列</summary><div class="field-pair">'+field('尺寸 %','visual.scale',item.visual.scale,'number','min="40" max="200"')+field('朝向 °','visual.rotation',item.visual.rotation,'number','min="0" max="359"')+'</div>'+field('排列层级','visual.layer',item.visual.layer,'number','min="-100" max="100"')+`<label class="check-row"><input id="show-name" type="checkbox" ${item.visual.show_name?'checked':''}>显示物件名称</label><div class="field-pair"><button id="raise-layer">置于顶层</button><button id="lower-layer">置于底层</button></div><p class="field-hint">尺寸、朝向和层级仅影响外观，不改变移动、碰撞或家具占用规则。</p></details>`;}
function behaviorFields(item){
  return '<details class="behavior-section" open><summary>行为与规则 · 真实运行时模板</summary>'+defaults[item.kind].map(v=>{const spec=actionSpecs[v],enabled=item.actions.includes(v);return `<section class="behavior-card ${enabled?'':'disabled'}"><label class="check-row"><input type="checkbox" data-verb="${v}" ${enabled?'checked':''}>${esc(spec.label)}</label><p>条件：${esc(spec.condition)}</p><p class="behavior-flow">${esc(spec.result)}</p>${spec.parameters.length?'<div class="field-pair">'+spec.parameters.map(param=>field(param.label,'behavior.'+v+'.'+param.key,item.behavior[v][param.key],'number',`min="${param.min}" max="${param.max}" ${enabled?'':'disabled'}`)).join('')+'</div>':''}</section>`;}).join('')+(defaults[item.kind].length?'':'<p class="field-hint">此素材是陈设，没有运行时行为。</p>')+'</details>';
}
function bindVisualControls(item){
  if($('show-name'))$('show-name').onchange=e=>{item.visual.show_name=e.target.checked;touch();};
  if($('raise-layer'))$('raise-layer').onclick=()=>layerObjects(true);
  if($('lower-layer'))$('lower-layer').onclick=()=>layerObjects(false);
}
function renderConnectionInspector(){
  const index=Number(selected.id),edge=draft.connections[index];if(!edge){selected={type:'room',id:draft.initial.room_id};return renderInspector();}
  $('inspector-title').textContent='双向区域连接';const opts=draft.rooms.map(r=>[r.id,r.name]);
  $('inspector').innerHTML=choices('起点','from',edge.from,opts)+choices('终点','to',edge.to,opts)+'<button id="update-connection" class="primary">调整连接</button><p class="field-hint">连接决定区域间是否可直接移动，画布位置不替代连通关系。</p><button id="delete-item" class="danger">删除这条连接</button>';
  $('update-connection').onclick=()=>run(()=>{const from=$('inspector').querySelector('[data-field=from]').value,to=$('inspector').querySelector('[data-field=to]').value;
    if(from===to)throw new Error('连接需要两个不同区域。');if(draft.connections.some((e,i)=>i!==index&&new Set([e.from,e.to]).has(from)&&new Set([e.from,e.to]).has(to)))throw new Error('这条连接已经存在。');draft.connections[index]={from,to};touch();renderInspector();});
  $('delete-item').onclick=()=>run(deleteSelected);
}

function renderInspector() {
  const p=source(); if(!p)return;
  if(spatialSelection)return renderSpatialInspector();
  if(mode==='trial')return renderTrialInspector();
  if(selected.type==='connection')return renderConnectionInspector();
  if(selected.type==='world')return renderSettings();
  const item=selected.type==='room'?p.rooms.find(r=>r.id===selected.id):p.objects.find(o=>o.id===selected.id);
  if(!item){ selected={type:'room',id:p.rooms[0].id};return renderInspector(); }
  $('inspector-title').textContent=selected.type==='room'?'区域属性':'物件属性';
  let html=`<div class="property-id">${esc(item.id)}</div>`+field('名称','name',item.name,'text','maxlength="80"')+field('说明','description',item.description,'textarea','maxlength="1000"');
  if(selected.type==='room') {
    html+=choices('区域类型','kind',item.kind,[['room','室内'],['shop','商店'],['outdoor','户外']]);
    html+='<div class="inspector-section">地图布局</div><div class="field-pair">'+field('横向位置','x',item.x,'number','min="0" max="900"')+field('纵向位置','y',item.y,'number','min="0" max="540"')+'</div><div class="field-pair">'+field('宽度','w',item.w,'number','min="100" max="1000"')+field('高度','h',item.h,'number','min="80" max="620"')+'</div>';
    html+=roomVisualFields(item)+`<label class="check-row"><input id="space-enabled" type="checkbox" ${item.space.enabled?'checked':''}>启用室内空间与路径</label>`+'<div class="field-pair">'+field('区域入口格 X','space.entry.x',item.space.entry.x,'number','min="0" max="19"')+field('区域入口格 Y','space.entry.y',item.space.entry.y,'number','min="0" max="19"')+'</div><p class="field-hint">入口也是出生点和区域旅行的出入口。空间工具绘制该区域内部，区域连接另行保留。</p>';
    const aiBuildings=[['auto','自动推荐'],['home','住宅'],['bookstore','书店'],['cafe','咖啡馆'],['convenience_store','便利店'],['classroom','教室'],['library','图书馆'],['market','市场'],['shop_generic','商铺'],['hall','公共建筑']];
    html+='<div class="inspector-section">AI 生成室内布局</div><div class="field-pair"><label class="field">建筑用途<select id="ai-building">'+aiBuildings.map(b=>`<option value="${b[0]}">${b[1]}</option>`).join('')+'</select></label><label class="field">随机种子<input id="ai-seed" type="number" min="0" max="2147483647" value="'+(Date.now()%1000000)+'"></label></div><button id="ai-interior-btn" class="dashed">生成室内布局（替换当前空间与家具，可撤销）</button><p class="field-hint">程序负责几何与通行性；配置 GLM Key 后可叠加语义设计。结果进入撤销栈，保存后随世界包回写。</p>';
    html+='<div class="inspector-section">AI 视角预览</div>'+'<button id="ai-perception-btn" class="dashed">查看此位置的 AI 感知（需要 WorldGen 世界包）</button>'+'<pre id="ai-perception-out" class="sub" style="white-space:pre-wrap;display:none;max-height:260px;overflow:auto;background:#f6f8f6;border-radius:8px;padding:8px;"></pre>';
    html+=`<div class="inspector-section">进入与使用规则</div><label class="check-row"><input id="hours-toggle" type="checkbox" ${item.hours?'checked':''}>限定开放时间</label>`;
    if(item.hours)html+='<div class="field-pair">'+field('开放小时','hours.open',item.hours.open,'number','min="0" max="23"')+field('关闭小时','hours.close',item.hours.close,'number','min="0" max="23"')+'</div><p class="field-hint">支持跨夜开放；关闭时允许离开，不允许进入和使用物件。</p>';
    html+='<div class="inspector-section">连接到其他区域</div>';
    const linked=p.connections.filter(e=>e.from===item.id||e.to===item.id);
    html+=linked.map(e=>{const dest=e.from===item.id?e.to:e.from;return `<div class="connection-item"><span>${esc(p.rooms.find(r=>r.id===dest)?.name)}</span><button aria-label="移除连接" data-remove-edge="${esc(dest)}">×</button></div>`;}).join('');
    const options=p.rooms.filter(r=>r.id!==item.id&&!linked.some(e=>e.from===r.id||e.to===r.id));
    if(options.length)html+=`<label class="field">选择区域<select id="connect-select">${options.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('')}</select></label><button id="connect-btn">＋ 创建双向连接</button>`;
    else html+='<p class="field-hint">添加新区域后，可以在这里建立连接。</p>';
    html+=`<button id="spawn-btn" class="dashed">${p.initial.room_id===item.id?'✓ 当前出生区域':'设为出生区域'}</button><button id="delete-item" class="danger">删除区域与其中的物件</button>`;
    $('inspector').innerHTML=html; bindFields(item,key=>{if(key==='visual.theme'){const theme=item.visual.theme;item.visual={theme,...Object.fromEntries(Object.entries(themes[theme]).filter(([k])=>k!=='name'))};touch({group:`room:${item.id}:visual.theme`});renderInspector();}});
    $('space-enabled').onchange=e=>{item.space.enabled=e.target.checked;touch();};
    $('ai-interior-btn').onclick=async()=>{
      const existing=p.objects.filter(o=>o.room_id===item.id).length;
      if(!await confirm('生成室内布局？',`将替换「${item.name}」的室内空间与其中 ${existing} 件家具。生成结果可撤销（Ctrl+Z），保存后随世界包回写。`))return;
      const payload={room:{id:item.id,kind:item.kind,name:item.name},building:$('ai-building').value,seed:$('ai-seed').value,glm:!!localStorage.getItem('nyairo-glm')};
      const result=await api('/api/nyairo/interior',payload);
      if(!await confirm('应用生成结果？',`建筑用途：${result.building_label} · 种子 ${result.seed} · 家具 ${result.objects.length} 件 · 通行性 ${(result.passability.ratio*100).toFixed(0)}%\n${result.warnings&&result.warnings.length?('注意：'+result.warnings.join('；')):'设计来源：'+(result.design_source==='glm'?'GLM 语义设计 + 程序几何':'程序化布局')}`))return;
      const next=clone(draft);
      const target=next.rooms.find(r=>r.id===item.id);
      target.space=result.space;
      next.objects=next.objects.filter(o=>o.room_id!==item.id).concat(result.objects);
      applyPackage(next,new Set([ref('room',item.id)]));
      toast(`室内布局已生成：${result.building_label} · 家具 ${result.objects.length} 件 · 通行性 ${(result.passability.ratio*100).toFixed(0)}%。可撤销；保存草稿后随世界包回写。`);
    $('ai-perception-btn').onclick=async()=>{const out=$('ai-perception-out');const remembered=localStorage.getItem('nyairo-original-dir')||'';const dir=prompt('查看 AI 感知需要 WorldGen 世界包目录（绝对路径）',remembered);if(dir===null||!dir.trim())return;localStorage.setItem('nyairo-original-dir',dir.trim());const instance=prompt('隔离实例目录（可选，提供则读取真实运行状态）','');const payload={package_dir:dir.trim(),place_id:item.id};if(instance&&instance.trim())payload.instance_home=instance.trim();const result=await api('/api/nyairo/perception',payload);const a=result.allowed_observation;const lines=['【允许观察】',`建筑：${a.building.name}`,`区域：${a.area.name}（${a.area.kind}）`,`家具：${a.furniture.map(f=>f.name).join('、')||'（无）'}`,`可通行：${a.adjacent_areas.map(x=>x.name).join('、')||'（无）'}`,`营业：${JSON.stringify(a.opening_rule)}`,a.ambient_facts.length?`环境事实：${a.ambient_facts.map(f=>f.summary).join('；')}`:null,'','【静态世界包】',`区域连接：${result.static_world_data.area_connections.join('，')||'（单区域）'}`,`本楼家具总数：${result.static_world_data.furniture_total_in_building}`,`常驻 NPC：${result.static_world_data.npcs_based_here.join('、')||'（无）'}`,'','【需运行时状态】',`当前位置：${JSON.stringify(result.runtime_dependent.current_position)}`,`数据来源：${result.source}`,result.note].filter(x=>x!==null);out.textContent=lines.join('
');out.style.display='block';};
    };
    $('hours-toggle').onchange=e=>{item.hours=e.target.checked?{open:8,close:22}:null;touch();renderInspector();};
    $('spawn-btn').onclick=()=>{p.initial.room_id=item.id;touch();renderInspector();};
    if($('connect-btn'))$('connect-btn').onclick=()=>{p.connections.push({from:item.id,to:$('connect-select').value});touch();renderInspector();};
    $('inspector').querySelectorAll('[data-remove-edge]').forEach(el=>el.onclick=()=>{p.connections=p.connections.filter(e=>!((e.from===item.id&&e.to===el.dataset.removeEdge)||(e.to===item.id&&e.from===el.dataset.removeEdge)));touch();renderInspector();});
  } else {
    html+=`<div class="notice-box">${kinds[item.kind]} · 只有已启用的用途才能在试运行中执行。</div>`;
    html+=choices('所在区域','room_id',item.room_id,p.rooms.map(r=>[r.id,r.name]));
    html+='<div class="field-pair">'+field('横向位置 %','x',item.x,'number','min="5" max="95"')+field('纵向位置 %','y',item.y,'number','min="15" max="90"')+'</div><div class="inspector-section">物件用途</div>';
    html+=objectVisualFields(item)+choices('图片素材','asset_id',item.asset_id||'',[['','内置图形'],...Object.values(p.assets).map(a=>[a.id,a.name])])+`<div class="notice-box">执行者：独立测试居民。行为结果由本机运行时提交。</div><label class="check-row"><input id="near-toggle" type="checkbox" ${item.interaction.near?'checked':''}>要求沿可达路径接近目标</label>`+field('接近距离（网格）','interaction.radius',item.interaction.radius,'number','min="1" max="8"')+'<p class="field-hint">接近条件在区域启用室内空间时生效；墙后的直线近距离仍不可交互。</p>'+(['cup','dispenser','lamp'].includes(item.kind)?choices('初始物件状态','initial_status',item.initial_status,(item.kind==='cup'?['empty','water']:item.kind==='dispenser'?['available','unavailable']:['off','on']).map(k=>[k,statusNames[k]])):'')+behaviorFields(item);
    if(['book','cup'].includes(item.kind))html+=`<label class="check-row"><input id="portable-toggle" type="checkbox" ${item.portable?'checked':''}>可被拿起并带走</label>`;
    if(item.kind==='cabinet')html+=field('储物容量','capacity',item.capacity,'number','min="1" max="20"');
  if(item.kind==='cup')html+=field('初始饮品份数','servings',item.servings,'number','min="0" max="20"');
    html+='<button id="delete-item" class="danger">删除物件</button>';
    $('inspector').innerHTML=html;bindFields(item,key=>{if(key==='asset_id'&&!item.asset_id)item.asset_id=null;if(item.kind==='cup'&&['servings','initial_status'].includes(key)){if(key==='servings')item.initial_status=item.servings>0?'water':'empty';else item.servings=item.initial_status==='empty'?0:Math.max(1,item.servings);touch({group:`object:${item.id}:${key}`});if(key==='initial_status')renderInspector();} });bindVisualControls(item);$('near-toggle').onchange=e=>{item.interaction.near=e.target.checked;touch();};
    $('inspector').querySelectorAll('[data-verb]').forEach(el=>el.onchange=()=>{item.actions=Array.from($('inspector').querySelectorAll('[data-verb]:checked')).map(c=>c.dataset.verb);if(item.actions.includes('pick_up')||item.actions.includes('place'))item.portable=true;touch();renderInspector();});
    if($('portable-toggle'))$('portable-toggle').onchange=e=>{item.portable=e.target.checked;if(!item.portable)item.actions=item.actions.filter(v=>!['pick_up','place'].includes(v));touch();renderInspector();};
  }
  $('delete-item').onclick=()=>run(deleteSelected);
}
function renderSettings() {
  $('inspector-title').textContent='作品与起始设置';
  $('inspector').innerHTML=field('作品名称','meta.name',draft.meta.name,'text','maxlength="120"')+field('作品说明','meta.description',draft.meta.description,'textarea','maxlength="2000"')+field('作者','meta.author',draft.meta.author,'text','maxlength="120"')+field('作品 ID','meta.id',draft.meta.id,'text','maxlength="64"')+field('内容版本','meta.version',draft.meta.version)+field('内容授权','meta.license',draft.meta.license)+'<div class="notice-box">封面：根据区域和物件自动生成平面图。导出时提供 SVG 封面，另一实例可从世界内容重建。</div>'+choices('出生区域','initial.room_id',draft.initial.room_id,draft.rooms.map(r=>[r.id,r.name]))+field('世界起始时间','initial.time',draft.initial.time,'datetime-local')+field('测试居民名称','initial.resident_name',draft.initial.resident_name,'text','maxlength="80"')+'<div class="notice-box">此处只定义测试居民的名字和出生地点。不会复制私人角色、关系或记忆。</div><p class="field-hint">时间由操作推进；关闭编辑器后不自动执行。内容包使用编辑器运行时 v3，v1 / v2 可升级导入。</p>';
  $('inspector').innerHTML+='<details class="visual-section" open><summary>场景视觉</summary>'+field('画布背景','scene.background',draft.scene.background,'color')+`<label class="check-row"><input id="scene-grid" type="checkbox" ${draft.scene.grid?'checked':''}>编辑网格</label><label class="check-row"><input id="scene-decorations" type="checkbox" ${draft.scene.decorations?'checked':''}>区域标题背景装饰</label></details>`;
  bindFields(draft);for(const [id,key] of [['scene-grid','grid'],['scene-decorations','decorations']])$(id).onchange=e=>{draft.scene[key]=e.target.checked;touch();};
}
async function deleteSelected(){
  if(mode!=='edit')return;
  if(spatialSelection)return deleteSpatial();
  if(selected.type==='connection'){if(!await confirm('删除连接？','两个区域将无法沿这条连接直接移动。本次修改可撤销，已有试运行保留原连接。'))return;draft.connections.splice(Number(selected.id),1);selected={type:'room',id:draft.initial.room_id};selection=new Set([ref('room',selected.id)]);touch();renderAll();return;}
  const es=entries(draft,selectionKeys());if(!es.length)throw new Error('请先选择要删除的对象。');
  const rooms=new Set(es.filter(e=>e.type==='room').map(e=>e.item.id)),objects=new Set(es.filter(e=>e.type==='object').map(e=>e.item.id));
  for(const o of draft.objects)if(rooms.has(o.room_id))objects.add(o.id);
  if(draft.rooms.length-rooms.size<1)throw new Error('世界至少保留一个区域。');
  const edges=draft.connections.filter(e=>rooms.has(e.from)||rooms.has(e.to)).length;
  if(!await confirm('删除这些对象？',`实际移除 ${rooms.size} 个区域、${objects.size} 个物件、${edges} 条连接。${rooms.has(draft.initial.room_id)?'出生区域会改为第一个保留区域。':''}整批操作可撤销，已有居民经历不受影响。`))return;
  draft.rooms=draft.rooms.filter(r=>!rooms.has(r.id));draft.objects=draft.objects.filter(o=>!objects.has(o.id));draft.connections=draft.connections.filter(e=>!rooms.has(e.from)&&!rooms.has(e.to));if(rooms.has(draft.initial.room_id))draft.initial.room_id=draft.rooms[0].id;
  selected={type:'room',id:draft.initial.room_id};selection=new Set([ref('room',selected.id)]);touch();renderAll();
}
function addRoom() {
  if(draft.rooms.length>=60)throw new Error('区域数量上限为 60。');
  const id='room-'+crypto.randomUUID().slice(0,8);
  const n=draft.rooms.length;
  let spot=null;
  for(const [w,h] of [[260,180],[240,100],[160,80]]) {
    for(let y=10;y+h<=610&&!spot;y+=10)for(let x=10;x+w<=990&&!spot;x+=10) {
      if(draft.rooms.every(r=>x+w+12<=r.x||r.x+r.w+12<=x||y+h+12<=r.y||r.y+r.h+12<=y))spot={x,y,w,h};
    }
    if(spot)break;
  }
  if(!spot)throw new Error('地图上没有足够的空位，请先缩小或调整已有区域，再添加区域。');
  draft.rooms.push({id,name:'新区域 '+(n+1),description:'',kind:'room',...spot,hours:null,visual:themeVisual(),space:emptySpace(true)});
  touch();select('room',id);toast('区域已加入。记得在右侧连接到已有区域。');
}
function addObject(kind,point=null) {
  if(mode!=='edit')return;const placement=point?drop(draft,point):null;if(point&&!placement)throw new Error('请在区域内部点击放置物件。');
  if(draft.objects.length>=300)throw new Error('物件数量上限为 300。');
  let roomId=selected.type==='room'?selected.id:selected.type==='object'?draft.objects.find(o=>o.id===selected.id)?.room_id:draft.initial.room_id;
  if(!draft.rooms.some(r=>r.id===roomId))roomId=draft.initial.room_id;
  const n=draft.objects.filter(o=>o.room_id===roomId).length,id=kind+'-'+crypto.randomUUID().slice(0,8);
  draft.objects.push({id,name:'新'+kinds[kind],description:'',kind,room_id:roomId,x:25+(n%3)*25,y:30+(Math.floor(n/3)%3)*22,...placement,actions:clone(defaults[kind]),portable:['book','cup'].includes(kind),capacity:kind==='cabinet'?4:0,servings:kind==='cup'?2:0,visual:{scale:100,rotation:0,layer:0,show_name:true},behavior:behaviorDefaults(kind),asset_id:placingAsset,interaction:{near:true,radius:2},initial_status:kind==='cup'?'water':kind==='dispenser'?'available':kind==='lamp'?'off':'neutral'});
  if(placingAsset){const a=draft.assets[placingAsset],o=draft.objects.at(-1);o.visual=clone(a.visual);o.name=a.name;if(!a.interactive)o.actions=[];}placingAsset=null;
  placing=null;ghostPoint=null;
  touch();select('object',id);
}
function downloadFile(info) {
  const a=document.createElement('a');a.href=info.url;a.download=info.filename;document.body.append(a);a.click();a.remove();
}
async function saveDraft() {
  if(recoveryConflict)throw new Error('这个副本基于旧版本，请先导出，再回到已保存的草稿。');
  if(saveBusy)return;saveBusy=true;renderHeader();
  const snapshot=clone(draft);flushBackup();
  try { const saved=await api('/api/draft',{package:snapshot,revision:draftRevision});draftRevision=saved.revision;savedPackage=clone(saved.package);savedFingerprint=fingerprint(saved.package);recoveryBase={revision:draftRevision,fingerprint:savedFingerprint};dirty=fingerprint(draft)!==savedFingerprint;flushBackup();toast('草稿已保存到本机。'+(saved.warnings.length?'\n'+saved.warnings.join('\n'):'')); }
  catch(e){if(e.code==='STALE')recoveryConflict=true;throw e;}
  finally {saveBusy=false;renderHeader();}
}
async function switchMode(next) {
  if(spatialGesture)endSpatial(false);spatialSelection=null;assetFocus=null;
  if(dragging)finishDrag(false);history?.endGroup();flushBackup();placing=null;ghostPoint=null;connectFrom=null;tool='select';
  if(next==='trial'&&!trial) { await newTrial(false); return; }
  mode=next; const p=source();
  if(selected.type==='world'||!(selected.type==='room'?p.rooms:p.objects).some(x=>x.id===selected.id)) selected={type:'room',id:p.initial.room_id};
  if(next==='edit')selectionClean();renderAll();
}
async function newTrial(ask=true) {
  if(actionBusy)return;
  if(ask&&!await confirm('新建试运行？','从当前编辑草稿的初始设置创建新居民。旧试运行会保留，可在存档列表里继续。'))return;
  actionBusy=true;
  try { spatialSelection=null;trial=await api('/api/trials',{package:draft});mode='trial';selected={type:'room',id:trial.state.resident.room_id};await refreshList();renderAll();toast('隔离试运行已开始。点击区域空地行走；接近门和设备，再执行动作。'); }
  finally {actionBusy=false;renderAll();}
}
async function refreshList() { const result=await api('/api/bootstrap');trialList=result.trials;renderTrialList(); }
function renderTrialList() {
  $('trial-select').innerHTML='<option value="">选择试运行</option>'+trialList.map(t=>`<option value="${esc(t.id)}" ${t.id===trial?.id?'selected':''}>${esc(t.name)} · ${t.id.slice(0,6)} · 第 ${t.revision} 次操作</option>`).join('');
}
function timeLabel(p, minutes) {
  const date=new Date(p.initial.time+'Z');date.setUTCMinutes(date.getUTCMinutes()+minutes);
  return date.toISOString().slice(11,16);
}
function renderTrial() {
  if(!trial)return;
  const s=trial.state,r=s.resident,p=trial.package;
  $('trial-content-note').textContent=fingerprint(draft)!==fingerprint(p)?'创作草稿已变更。本次试运行继续使用冻结内容；点击「从草稿新建试运行」验证修改。':'当前试运行使用启动时冻结的内容；创作撤销不会倒退居民经历。';
  $('resident-status').textContent=`${r.name} · ${p.rooms.find(x=>x.id===r.room_id).name} · 网格 (${r.position.x},${r.position.y}) · ${({standing:'站立',sitting:'坐着',lying:'躺着'})[r.pose]}`;
  $('resident-metrics').innerHTML=`<div class="metric"><span>世界时间 · 已过 ${Math.floor(s.minutes/1440)} 天</span><strong>${timeLabel(p,s.minutes)}</strong></div><div class="metric"><span>身体状态</span><strong>精力 ${r.energy} · 水分 ${r.hydration}</strong></div><div class="metric"><span>手中物件</span><strong>${esc(p.objects.find(o=>o.id===r.held)?.name||'空手')}</strong></div>`;
  $('events').innerHTML=s.events.length?[...s.events].reverse().map(e=>`<li><time>第${Math.floor(e.minutes/1440)}天 ${timeLabel(p,e.minutes)}</time><span>${esc(e.message)}</span></li>`).join(''):'<li class="empty-hint">还没有发生事件。执行移动或物件用途后，这里会留下结果。</li>';
  $('stand-btn').disabled=actionBusy||!r.using;
  for(const id of ['wait-btn','new-trial-btn','import-save-btn','refresh-trial-btn'])$(id).disabled=actionBusy;
  $('trial-select').disabled=actionBusy;
  renderTrialList();
}
function renderTrialInspector() {
  const p=trial.package,s=trial.state,r=s.resident;
  $('inspector-title').textContent=selected.type==='room'?'区域与移动':'物件与交互';
  if(selected.type==='room') {
    const room=p.rooms.find(x=>x.id===selected.id);
    const connected=p.connections.some(e=>new Set([e.from,e.to]).has(r.room_id)&&new Set([e.from,e.to]).has(room.id));
    const here=room.id===r.room_id,open=roomOpen(room,trialMinute());
    $('inspector').innerHTML=`<div class="property-id">${esc(room.id)}</div><h3>${esc(room.name)}</h3><p class="empty-hint">${esc(room.description||'没有区域说明。')}</p><div class="notice-box">${room.hours?`每天 ${room.hours.open}:00–${room.hours.close}:00 开放`:'全天开放'}<br>${here?'居民在这里。':!open?'当前关闭，等待开放后才能进入。':connected?'这个区域和当前位置相连。':'这个区域和当前位置没有直接连接。'}</div><button id="move-btn" class="primary" ${here||!connected||!open||!!r.using||actionBusy?'disabled':''}>${here?'当前所在区域':'移动到这里'}</button><p class="field-hint">移动需要直接连接且目标已开放；正在使用家具时需先站起。</p>`;
    $('move-btn').onclick=()=>run(()=>execute({action:'move',target:room.id}));
    return;
  }
  const item=p.objects.find(x=>x.id===selected.id);
  if(!item){selected={type:'room',id:r.room_id};return renderTrialInspector();}
  const os=s.objects[item.id],here=os.room_id===r.room_id;
  const placement=os.placement==='held'?'由居民持有':os.placement==='container'?`收纳在${p.objects.find(x=>x.id===os.container_id)?.name}`:'放在区域里';
  let html=`<div class="property-id">${esc(item.id)}</div><h3>${esc(item.name)}</h3><p class="empty-hint">${esc(item.description||kinds[item.kind])}</p><div class="state-details"><div><small>当前区域</small>${esc(p.rooms.find(x=>x.id===os.room_id).name)}</div><div><small>位置状态</small>${esc(placement)}</div><div><small>阅读次数</small>${os.read_count}</div><div><small>使用次数</small>${os.use_count}</div></div>`;
  html+=`<div class="notice-box">状态：${statusNames[os.status]} · 网格 (${os.position.x},${os.position.y})${item.interaction.near?' · 需接近 '+item.interaction.radius+' 格':''}</div>`;
  if(item.kind==='cup')html+=`<div class="notice-box">杯中剩余 ${os.servings} 份饮品</div>`;
  if(item.kind==='cabinet') {
    const stored=p.objects.filter(o=>s.objects[o.id].container_id===item.id);
    html+=`<div class="notice-box">已使用 ${stored.length} / ${item.capacity} 个储物位置</div>`;
    if(stored.length)html+=`<label class="field">取出物件<select id="retrieve-item">${stored.map(o=>`<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('')}</select></label>`;
  }
  html+='<div class="inspector-section">执行物件用途</div>';
  for(const v of item.actions) {
    let disabled=actionBusy||!here||os.placement==='container'||!roomOpen(p.rooms.find(x=>x.id===r.room_id),trialMinute());
    if(v==='pick_up')disabled ||= !!r.held;
    if(['read','place','drink'].includes(v))disabled ||= r.held!==item.id;
    if(['sit','rest'].includes(v))disabled ||= !!r.using;
    if(v==='store')disabled ||= !r.held;
    if(v==='retrieve')disabled ||= !!r.held||!p.objects.some(o=>s.objects[o.id].container_id===item.id);
    html+=`<button class="primary" data-execute="${v}" ${disabled?'disabled':''}>${verbLabel(v,item)}</button>`;
  }
  if(!item.actions.length)html+='<p class="field-hint">这个物件是环境陈设，没有可执行用途。</p>';
  html+=`<p class="field-hint">${!here?'物件不在这里，请先移动到对应区域。':item.kind==='book'?'先拿起书，再阅读。阅读会推进时间并更新阅读次数。':'动作会在后端检查时间、位置和占用条件。'}</p>`;
  $('inspector').innerHTML=html;
  $('inspector').querySelectorAll('[data-execute]').forEach(el=>el.onclick=()=>run(()=>execute({action:el.dataset.execute,target:item.id,...(el.dataset.execute==='retrieve'?{item:$('retrieve-item')?.value}: {})})));
}
async function execute(command) {
  if(actionBusy)return;
  actionBusy=true;renderTrial();renderInspector();
  try {
    const tid=trial.id;
    trial=await api(`/api/trials/${tid}/actions`,{execution_id:crypto.randomUUID(),revision:trial.state.revision,command});
    const t=trialList.find(t=>t.id===tid);if(t)t.revision=trial.state.revision;
    renderAll();toast(trial.event.message);
  } catch(e) {
    if(e.code==='STALE'){trial=await api(`/api/trials/${trial.id}`);renderAll();}
    throw e;
  } finally {actionBusy=false;renderAll();}
}
function renderAll(){renderHeader();renderList();renderMap();renderInspector();if(mode==='edit')renderBatch();if(mode==='trial')renderTrial();renderV04();}

async function importFile(file) {
  if(file.size>8_000_000)throw new Error('文件超过 8 MB，请使用较小的世界包或存档。');
  let value;try{value=JSON.parse(await file.text());}catch{throw new Error('文件不是有效的 JSON。');}
  if(importKind==='world') {
    const result=await api('/api/validate',{package:value});
    if(!await confirm('导入这个世界？',`将编辑草稿替换为“${result.package.meta.name}”。现有试运行和其中的经历会保留。${dirty?'\n当前有未保存修改，请先取消并导出或保存。':''}`))return;
    draft=result.package;selected={type:'room',id:draft.initial.room_id};touch();renderAll();
    toast('世界已导入为编辑草稿，请点击保存。'+(result.warnings.length?'\n'+result.warnings.join('\n'):''));
  } else {
    if(!await confirm('恢复试运行存档？','会创建一个独立试运行，不覆盖草稿或已有试运行。恢复时回到存档记录的世界时间。'))return;
    trial=await api('/api/saves/import',{save:value});mode='trial';selected={type:'room',id:trial.state.resident.room_id};await refreshList();renderAll();toast('存档已恢复到新的试运行。');
  }
}
bind('save-btn',saveDraft);
bind('undo-btn',()=>historyStep('undo'));bind('redo-btn',()=>historyStep('redo'));
bind('restore-draft',restoreDraft);bind('return-saved',returnSaved);
bind('dismiss-recovery',()=>{pendingBackups=[];renderRecovery();});
$('backup-select').onchange=renderRecovery;
bind('export-btn',async()=>{const info=await api('/api/exports',{kind:'world',package:source()});downloadFile(info);toast('世界包与平面图封面已保存到本机数据目录：\n'+info.local_file+'\n同时已请求浏览器下载。包内只含内容与初始规则。');});
bind('export-nyairo-btn',async()=>{const remembered=localStorage.getItem('nyairo-original-dir')||'';const dir=prompt('原始 WorldGen 世界包目录（绝对路径）。\n编辑器 v3 不包含 NPC、环境事件等完整世界信息；回写将以该原始包为基础做差量合并，原始包不会被覆盖。',remembered);if(!dir||!dir.trim())return;localStorage.setItem('nyairo-original-dir',dir.trim());const info=await api('/api/nyairo/export',{package:draft,original_dir:dir.trim()});const s=info.summary||{};const changes=[(info.added_locations||[]).length+' 新地点',(info.removed_locations||[]).length+' 删除地点',(info.added_connections||[]).length+' 新道路',(info.removed_connections||[]).length+' 删道路'].join('，');toast('NyAiro 世界包已回写：\n'+info.output+'\n地点 '+s.locations+' · 道路 '+s.edges+' · 家具 '+(s.objects!=null?s.objects:'-')+' · 结构校验 '+info.validation+' · 可交 rc9 加载：'+(info.rc9_loadable?'是':'否')+'\n本次修改：'+changes+'；室内场景 '+info.scene_locations.length+' 处、物件摆位 '+info.scene_placements.length+' 件。\n（室内几何与家具交互是编辑器本地能力，rc9 不消费。）');});
bind('import-btn',()=>{importKind='world';$('file-input').value='';$('file-input').click();});
bind('import-save-btn',()=>{importKind='save';$('file-input').value='';$('file-input').click();});
$('file-input').onchange=e=>{if(e.target.files[0])run(()=>importFile(e.target.files[0]));};
bind('edit-mode',()=>switchMode('edit'));bind('preview-mode',()=>switchMode('preview'));bind('trial-mode',()=>switchMode('trial'));
bind('add-room',addRoom);bind('world-settings',()=>select('world',null));
bind('load-example',async()=>{if(!await confirm('载入书店示例？','会替换当前编辑草稿。若需要保留，请先导出世界包；已有试运行会保留。'))return;draft=await api('/api/example');selected={type:'room',id:draft.initial.room_id};touch();renderAll();});
bind('validate-btn',async()=>{$('feedback-panel').open=true;try{const result=await api('/api/validate',{package:source()});$('check-results').className='';$('check-results').textContent=result.warnings.length?'可以加载；请检查：'+result.warnings.join(' · '):'检查通过：区域、连接、视觉、行为参数与起始设置有效。';}catch(e){$('check-results').className='error';$('check-results').textContent=e.message;throw e;}});
bind('new-trial-btn',newTrial);bind('stand-btn',()=>execute({action:'stand'}));bind('wait-btn',()=>execute({action:'wait',minutes:Number($('wait-minutes').value)}));
bind('export-save-btn',async()=>{if(!trial)return;const info=await api('/api/exports',{kind:'save',trial_id:trial.id});downloadFile(info);toast('试运行存档已保存到本机数据目录：\n'+info.local_file+'\n同时已请求浏览器下载。包含居民状态和最近 200 条事件。');});
bind('refresh-trial-btn',async()=>{if(!trial)return;trial=await api(`/api/trials/${trial.id}`);await refreshList();renderAll();});
$('trial-select').onchange=e=>run(async()=>{if(!e.target.value)return;trial=await api(`/api/trials/${e.target.value}`);selected={type:'room',id:trial.state.resident.room_id};renderAll();});

bind('new-world',newWorld);bind('copy-btn',copyObjects);bind('paste-btn',()=>pasteObjects());bind('duplicate-btn',()=>pasteObjects(true));bind('delete-selection',deleteSelected);
bind('arrange-btn',()=>applyPackage(arrange(draft,selectionKeys(),$('arrange-select').value)));
bind('zoom-in',()=>setZoom(view.zoom*1.25));bind('zoom-out',()=>setZoom(view.zoom/1.25));bind('fit-view',()=>{view={x:0,y:0,zoom:1};renderMap();});
bind('toggle-library',()=>{const hidden=document.body.classList.toggle('left-closed');$('toggle-library').setAttribute('aria-expanded',!hidden);});
bind('toggle-inspector',()=>{const hidden=document.body.classList.toggle('right-closed');$('toggle-inspector').setAttribute('aria-expanded',!hidden);});
bind('rule-preview-btn',previewRules);$('search-input').oninput=()=>{renderList();renderAssetLibrary();};
for(const [id,fn] of [['ctx-copy',copyObjects],['ctx-duplicate',()=>pasteObjects(true)],['ctx-delete',deleteSelected],['ctx-connect',()=>setTool('connect')],['ctx-front',()=>layerObjects(true)],['ctx-back',()=>layerObjects(false)]])bind(id,()=>{ $('context-menu').hidden=true;return fn();});
document.addEventListener('pointerdown',e=>{if(!$('context-menu').contains(e.target))$('context-menu').hidden=true;});

window.addEventListener('beforeunload',e=>{flushBackup({unloading:true});if(dirty){e.preventDefault();e.returnValue='';}});
window.addEventListener('pagehide',()=>flushBackup({unloading:true}));
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')flushBackup();});
window.addEventListener('keydown',e=>{
  const textInput=e.target.closest('input,textarea,select,[contenteditable="true"]');
  if(e.code==='Space'&&!textInput&&$('map').contains(e.target)){spaceHeld=true;e.preventDefault();}
  if(e.key==='Escape'){if(placing||connectFrom){placing=null;ghostPoint=null;connectFrom=null;renderAll();}$('context-menu').hidden=true;}
  if(ready&&mode==='edit'&&!textInput&&!$('confirm-dialog').open&&!dragging){
    if((e.ctrlKey||e.metaKey)&&['c','v','d'].includes(e.key.toLowerCase())){e.preventDefault();run(()=>e.key.toLowerCase()==='c'?copyObjects():pasteObjects(e.key.toLowerCase()==='d'));return;}
    if(['Delete','Backspace'].includes(e.key)){e.preventDefault();run(deleteSelected);return;}
  }
  if(e.key==='Escape'&&dragging){e.preventDefault();finishDrag(false);return;}
  if(!ready||mode!=='edit'||$('confirm-dialog').open||e.target.closest('input,textarea,select,[contenteditable="true"]'))return;
  if((e.ctrlKey||e.metaKey)&&!e.altKey){
    const key=e.key.toLowerCase();if(key==='z'||key==='y'){e.preventDefault();historyStep(key==='y'||e.shiftKey?'redo':'undo');}
  }
});
window.addEventListener('keyup',e=>{if(e.code==='Space')spaceHeld=false;});
window.addEventListener('blur',()=>{spaceHeld=false;if(dragging)finishDrag(false);});
// v0.4 additions share the existing history, recovery, save and action pipeline.
let editDomain='objects',spaceTool='select',spatialSelection=null,spatialGesture=null,spaceClipboard=null,placingAsset=null;
let visibleLayers={walls:true,floors:true,objects:true},assetFocus=null;
const statusNames={empty:'空杯',water:'装有饮品',available:'可用',unavailable:'不可用',on:'开启',off:'关闭',neutral:'普通',open:'开启',closed:'关闭',locked:'锁定'};
const spatialKinds={walls:'墙壁',doors:'门',windows:'窗',floors:'地面'};
function currentRoom(p=source()){return p.rooms.find(r=>r.id===(mode==='trial'?trial.state.resident.room_id:spatialSelection?.room||(selected.type==='room'?selected.id:p.objects.find(o=>o.id===selected.id)?.room_id)))||p.rooms.find(r=>r.id===p.initial.room_id)||p.rooms[0];}
function setDomain(next){editDomain=next;spaceTool='select';spatialSelection=null;placing=null;placingAsset=null;tool='select';if(dragging)finishDrag(false);if(next==='space'&&selected.type!=='room')selected={type:'room',id:currentRoom().id};renderAll();}
function renderV04(){
  $('domain-tabs').hidden=mode!=='edit';$('space-toolbar').hidden=mode!=='edit'||editDomain!=='space';$('arrange-toolbar').hidden=mode!=='edit'||editDomain==='space';$('batch-toolbar').hidden=mode!=='edit'||editDomain==='space';
  $('asset-panel').hidden=mode!=='edit'||editDomain!=='assets';$('spatial-list').hidden=mode!=='edit'||editDomain!=='space';
  document.querySelectorAll('[data-domain]').forEach(e=>(e.classList.toggle('active',e.dataset.domain===editDomain),e.setAttribute('aria-pressed',e.dataset.domain===editDomain)));document.querySelectorAll('[data-space-tool]').forEach(e=>(e.classList.toggle('active',e.dataset.spaceTool===spaceTool),e.setAttribute('aria-pressed',e.dataset.spaceTool===spaceTool)));
  if(mode==='edit'&&editDomain==='space'){
    const room=currentRoom();$('spatial-list').innerHTML=`<p class="field-hint">${esc(room.name)} · 20×20 网格</p>`+Object.keys(spatialKinds).map(type=>room.space[type].map(item=>`<button class="spatial-row" data-select-space="${type}" data-id="${esc(item.id)}">${spatialKinds[type]} · ${esc(item.id)}</button>`).join('')).join('');
    $('spatial-list').querySelectorAll('[data-select-space]').forEach(e=>e.onclick=()=>chooseSpace(room.id,e.dataset.selectSpace,e.dataset.id));
    $('map-help').textContent=spaceTool==='wall'?'拖出水平或竖直墙；吸附室内 5% 网格。':spaceTool==='floor'?'在当前区域拖出地面分区。':spaceTool==='door'||spaceTool==='window'?'点击墙壁添加门窗；属性中调整起点和长度。':'选择空间对象可拖动；墙端点、长度、门窗尺寸可在右侧修改。';
  }
  if(mode==='trial'&&currentRoom().space.enabled)$('map-help').textContent='点击当前区域空地沿路径行走；选择门开关，再接近设备执行交互。';
  $('trial-walk').hidden=mode!=='trial'||!trial?.package.rooms.find(r=>r.id===trial.state.resident.room_id).space.enabled;
  renderAssetLibrary();
}
function chooseSpace(room,type,id){history?.endGroup();spatialSelection={room,type,id};selected={type:'room',id:room};selection=new Set();spaceTool='select';renderAll();}
function spatialItem(){const room=currentRoom();return {room,item:room?.space[spatialSelection?.type]?.find(x=>x.id===spatialSelection.id)};}
function renderSpatialInspector(){
  const {room,item}=spatialItem();if(!item){spatialSelection=null;return renderInspector();}const type=spatialSelection.type;$('inspector-title').textContent=spatialKinds[type]+'与规则';
  let html=`<div class="property-id">${esc(item.id)}</div>`;
  if(mode==='trial'){
    if(type!=='doors')return $('inspector').innerHTML=html+'<p class="notice-box">墙壁和窗阻挡网格通行；地面只改变外观。</p>';
    html+=`<h3>门 · ${statusNames[trial.state.doors[room.id][item.id]]}</h3><p class="notice-box">执行者：测试居民。需在门的本侧两格内，动作推进 1 分钟。</p>`+item.actions.map(a=>`<button data-door-action="${a}" ${actionBusy?'disabled':''}>${esc(actionSpecs[a].label)}</button>`).join('');
    $('inspector').innerHTML=html;$('inspector').querySelectorAll('[data-door-action]').forEach(e=>e.onclick=()=>run(()=>execute({action:e.dataset.doorAction,target:room.id+':'+item.id})));return;
  }
  html+='<p class="field-hint">坐标/长度采用区域百分比，每格 5%；拖动与修改可撤销。</p>';
  if(type==='walls')html+='<div class="field-pair">'+field('起点 X','x1',item.x1,'number','step="5"')+field('起点 Y','y1',item.y1,'number','step="5"')+'</div><div class="field-pair">'+field('终点 X','x2',item.x2,'number','step="5"')+field('终点 Y','y2',item.y2,'number','step="5"')+'</div>'+field('墙视觉宽度','width',item.width,'number','min="2" max="12"');
  if(type==='doors'||type==='windows')html+=choices('依附墙壁','wall_id',item.wall_id,room.space.walls.map(w=>[w.id,w.id]))+field('沿墙起点 %','offset',item.offset,'number','step="5" min="0"')+field('门窗长度 %','length',item.length,'number','step="5" min="5"');
  if(type==='floors')html+='<div class="field-pair">'+field('X %','x',item.x,'number','step="5"')+field('Y %','y',item.y,'number','step="5"')+'</div><div class="field-pair">'+field('宽 %','w',item.w,'number','step="5" min="5"')+field('高 %','h',item.h,'number','step="5" min="5"')+'</div>'+choices('地面纹理','texture',item.texture,[['none','纯色'],['planks','木地板'],['tiles','方砖']])+choices('地面图片','asset_id',item.asset_id||'',[['','无图片'],...Object.values(draft.assets).map(a=>[a.id,a.name])]);
  html+=field('视觉颜色','color',item.color,'color');
  if(type==='doors')html+=choices('初始门状态','state',item.state,[['open','开启'],['closed','关闭'],['locked','锁定']])+'<p class="notice-box">执行者：独立测试居民；本侧两格内；一次成功操作 1 分钟。锁门需先关门，解锁后仍需开门。</p>'+['open_door','close_door','lock_door','unlock_door'].map(a=>`<label class="check-row"><input data-door-enabled="${a}" type="checkbox" ${item.actions.includes(a)?'checked':''}>${esc(actionSpecs[a].label)}</label>`).join('');
  html+='<div class="field-pair"><button id="space-copy">复制</button><button id="space-paste">粘贴副本</button></div><button id="space-delete" class="danger">删除'+spatialKinds[type]+'</button>';
  $('inspector').innerHTML=html;bindFields(item,k=>{if(k==='asset_id'&&!item.asset_id)item.asset_id=null;});
  $('inspector').querySelectorAll('[data-door-enabled]').forEach(e=>e.onchange=()=>{item.actions=Array.from($('inspector').querySelectorAll('[data-door-enabled]:checked')).map(e=>e.dataset.doorEnabled);touch();});
  $('space-copy').onclick=()=>copyObjects();$('space-paste').onclick=()=>run(()=>pasteObjects());$('space-delete').onclick=()=>run(deleteSpatial);
}
async function deleteSpatial(){const {room,item}=spatialItem();if(!item)return;const count=spatialSelection.type==='walls'?room.space.doors.concat(room.space.windows).filter(d=>d.wall_id===item.id).length:0;if(!await confirm('删除空间对象？',`删除${spatialKinds[spatialSelection.type]}${count?'及依附的 '+count+' 个门窗':''}。本次操作可撤销；已有试运行保留结构。`))return;removeSpace(room,spatialSelection.type,item.id);spatialSelection=null;touch();renderAll();}
async function pasteSpatial(duplicate=false){const {room,item}=spatialItem();const clip=duplicate&&item?spaceCopy(room,spatialSelection.type,item.id):spaceClipboard;if(!clip)throw new Error('先复制一个空间对象。');const next=clone(draft),r=next.rooms.find(r=>r.id===room.id),id=spacePaste(r,clip,()=> 'space-'+crypto.randomUUID().slice(0,10));await api('/api/validate',{package:next});draft=next;spatialSelection={room:r.id,type:clip.type,id};touch();renderAll();}
function spaceRoomPoint(point){const room=[...draft.rooms].reverse().find(r=>point.x>=r.x&&point.x<=r.x+r.w&&point.y>=r.y&&point.y<=r.y+r.h);return room?{room,point:localPoint(room,point)}:null;}
function addOpening(room,type,point,wallId=null){
  const walls=room.space.walls;const distance=w=>w.x1===w.x2?Math.abs(point.x-w.x1)+Math.max(w.y1-point.y,0,point.y-w.y2):Math.abs(point.y-w.y1)+Math.max(w.x1-point.x,0,point.x-w.x2);
  const wall=wallId?walls.find(w=>w.id===wallId):[...walls].sort((a,b)=>distance(a)-distance(b))[0];if(!wall||distance(wall)>8)throw new Error('请点击现有墙壁放置门窗。');
  const length=wall.x2-wall.x1+wall.y2-wall.y1,offset=Math.max(0,Math.min(length-10,snap5((wall.x1===wall.x2?point.y-wall.y1:point.x-wall.x1)-5)));if(length<10)throw new Error('这段墙至少需要两格长度。');
  const item={id:'space-'+crypto.randomUUID().slice(0,10),wall_id:wall.id,offset,length:10,color:type==='doors'?'#bd976e':'#90b6cb',...(type==='doors'?{state:'closed',actions:['open_door','close_door','lock_door','unlock_door']}: {})};
  if(room.space.doors.concat(room.space.windows).some(d=>d.wall_id===wall.id&&offset<d.offset+d.length&&offset+10>d.offset))throw new Error('门窗与已有门窗重叠，请换一个位置。');
  room.space.enabled=true;room.space[type].push(item);touch();chooseSpace(room.id,type,item.id);
}
function endSpatial(commit){
  const d=spatialGesture;if(!d)return;spatialGesture=null;if($('map').hasPointerCapture(d.pointer))$('map').releasePointerCapture(d.pointer);
  if(commit&&d.changed)touch();else draft=d.before;ignoreClick=true;setTimeout(()=>ignoreClick=false,0);renderAll();
}
$('map').addEventListener('pointerdown',e=>{
  if(mode!=='edit'||editDomain!=='space'||tool==='pan'||spaceHeld||e.button!==0)return;e.stopImmediatePropagation();e.preventDefault();const point=mapPoint(e),hit=spaceRoomPoint(point);if(!hit)return;
  const {room,point:local}=hit,el=e.target.closest('[data-space]');
  if(spaceTool==='door'||spaceTool==='window'){run(()=>addOpening(room,spaceTool==='door'?'doors':'windows',local,el?.dataset.space==='walls'?el.dataset.spaceId:null));return;}
  if(spaceTool==='select'&&!el){select('room',room.id);renderV04();return;}
  const before=clone(draft);let item,type;
  if(spaceTool==='select'){type=el.dataset.space;item=room.space[type].find(x=>x.id===el.dataset.spaceId);chooseSpace(room.id,type,item.id);}
  else{type=spaceTool==='wall'?'walls':'floors';item={id:'space-'+crypto.randomUUID().slice(0,10),color:type==='walls'?'#8c8a7d':'#eee7d8',...(type==='walls'?{x1:local.x,y1:local.y,x2:local.x,y2:local.y,width:7}:{x:local.x,y:local.y,w:5,h:5,texture:'planks',asset_id:null})};room.space.enabled=true;room.space[type].push(item);spatialSelection={room:room.id,type,id:item.id};}
  spatialGesture={before,room:room.id,type,id:item.id,origin:clone(item),start:local,pointer:e.pointerId,draw:spaceTool!=='select',changed:false};$('map').setPointerCapture(e.pointerId);renderMap();
},true);
$('map').addEventListener('pointermove',e=>{
  const d=spatialGesture;if(!d)return;e.stopImmediatePropagation();const room=draft.rooms.find(r=>r.id===d.room),local=localPoint(room,mapPoint(e)),item=room.space[d.type].find(x=>x.id===d.id);
  if(d.draw&&d.type==='walls'){const dx=local.x-d.start.x,dy=local.y-d.start.y;if(Math.abs(dx)>=Math.abs(dy))Object.assign(item,{x1:Math.min(d.start.x,local.x),x2:Math.max(d.start.x,local.x),y1:d.start.y,y2:d.start.y});else Object.assign(item,{x1:d.start.x,x2:d.start.x,y1:Math.min(d.start.y,local.y),y2:Math.max(d.start.y,local.y)});d.changed=item.x1!==item.x2||item.y1!==item.y2;}
  else if(d.draw){Object.assign(item,{x:Math.min(local.x,d.start.x),y:Math.min(local.y,d.start.y),w:Math.abs(local.x-d.start.x),h:Math.abs(local.y-d.start.y)});d.changed=item.w>=5&&item.h>=5;}
  else{Object.assign(item,translateSpace(room,d.type,d.origin,local.x-d.start.x,local.y-d.start.y));d.changed=fingerprint(item)!==fingerprint(d.origin);}
  renderMap();
},true);
for(const name of ['pointerup','pointercancel'])$('map').addEventListener(name,e=>{if(spatialGesture){e.stopImmediatePropagation();endSpatial(name==='pointerup');}},true);
$('map').addEventListener('click',e=>{
  if(ignoreClick)return;const el=e.target.closest('[data-space]');
  if(mode==='trial'&&el?.dataset.space==='doors'){e.stopImmediatePropagation();chooseSpace(el.dataset.spaceRoom,'doors',el.dataset.spaceId);return;}
  if(mode==='trial'&&!e.target.closest('[data-map-object],#resident-marker')){const r=trial.package.rooms.find(r=>r.id===trial.state.resident.room_id),p=mapPoint(e);if(r.space.enabled&&p.x>=r.x&&p.x<r.x+r.w&&p.y>=r.y&&p.y<r.y+r.h){e.stopImmediatePropagation();run(()=>execute({action:'walk',x:Math.min(19,Math.floor((p.x-r.x)/r.w*20)),y:Math.min(19,Math.floor((p.y-r.y)/r.h*20))}));return;}}
  if(mode==='edit'&&editDomain==='space'){e.stopImmediatePropagation();if(el)chooseSpace(el.dataset.spaceRoom,el.dataset.space,el.dataset.spaceId);}
},true);
$('map').addEventListener('contextmenu',e=>{if(mode==='edit'&&editDomain==='space'){e.preventDefault();e.stopImmediatePropagation();const el=e.target.closest('[data-space]');if(!el)return;chooseSpace(el.dataset.spaceRoom,el.dataset.space,el.dataset.spaceId);const menu=$('context-menu');menu.hidden=false;menu.style.left=Math.min(e.clientX,innerWidth-220)+'px';menu.style.top=Math.min(e.clientY,innerHeight-260)+'px';for(const id of ['ctx-connect','ctx-front','ctx-back'])$(id).disabled=true;}},true);
$('map').addEventListener('keydown',e=>{const el=e.target.closest('[data-space]');if(el&&['Enter',' '].includes(e.key)&&(mode==='edit'||mode==='trial'&&el.dataset.space==='doors')){e.preventDefault();e.stopImmediatePropagation();chooseSpace(el.dataset.spaceRoom,el.dataset.space,el.dataset.spaceId);}},true);
window.addEventListener('keydown',e=>{if(e.key==='Escape'&&spatialGesture){e.preventDefault();e.stopImmediatePropagation();endSpatial(false);}else if(mode==='edit'&&editDomain==='space'&&spatialSelection&&!e.target.closest('input,textarea,select')&&['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();e.stopImmediatePropagation();const {room,item}=spatialItem(),dx=e.key==='ArrowLeft'?-5:e.key==='ArrowRight'?5:0,dy=e.key==='ArrowUp'?-5:e.key==='ArrowDown'?5:0;Object.assign(item,translateSpace(room,spatialSelection.type,item,dx,dy));touch({group:'space-keyboard'});renderMap();renderInspector();}},true);
function renderAssetLibrary(){
  const p=source(),query=$('search-input').value.trim().toLowerCase(),category=$('asset-category').value,collection=$('asset-collection').value,items=Object.values(p.assets||{});
  const old=collection;$('asset-collection').innerHTML='<option value="">所有素材集合</option>'+[...new Set(items.map(a=>a.collection).filter(Boolean))].map(s=>`<option>${esc(s)}</option>`).join('');$('asset-collection').value=old;
  $('custom-assets').innerHTML=items.filter(a=>(a.name+' '+a.collection).toLowerCase().includes(query)&&(!category||a.category===category)&&(!collection||a.collection===collection)).map(a=>`<div class="asset-row"><button draggable="${mode==='edit'}" ${mode!=='edit'?'disabled':''} data-custom-asset="${esc(a.id)}"><img src="${esc(assetUri(a))}" alt="${esc(a.name)}"><span>${esc(a.name)}<small>${esc(a.collection||a.category)}</small></span></button><button ${mode!=='edit'?'disabled':''} data-edit-asset="${esc(a.id)}" aria-label="修改${esc(a.name)}素材">⋯</button></div>`).join('')||'<p class="empty-hint">还没有匹配图片。导入后可在多个物件间复用。</p>';
  $('custom-assets').querySelectorAll('[data-custom-asset]').forEach(e=>{e.onclick=()=>{const a=draft.assets[e.dataset.customAsset];setDomain('objects');placing=a.kind;placingAsset=a.id;renderAll();toast('已选择图片素材，在区域内点击或拖入放置。');};e.ondragstart=event=>event.dataTransfer.setData('application/x-nyairo-resource',e.dataset.customAsset);});
  $('custom-assets').querySelectorAll('[data-edit-asset]').forEach(e=>e.onclick=()=>{assetFocus=e.dataset.editAsset;renderAssetInspector();});
  document.querySelectorAll('[data-kind]').forEach(e=>{e.draggable=mode==='edit';e.ondragstart=event=>event.dataTransfer.setData('application/x-nyairo-resource','builtin:'+e.dataset.kind);});
}
function renderAssetInspector(){
  const a=draft.assets[assetFocus];if(!a)return;$('inspector-title').textContent='可复用图片素材';$('inspector').innerHTML=`<img class="asset-preview" src="${esc(assetUri(a))}" alt="${esc(a.name)}">`+field('素材名称','name',a.name)+choices('分类','category',a.category,[['家具','家具'],['器具','器具'],['装饰','装饰'],['地板','地板']])+field('素材集合','collection',a.collection)+choices('对应物件类型','kind',a.kind,Object.entries(kinds))+field('默认尺寸 %','visual.scale',a.visual.scale,'number')+field('默认朝向 °','visual.rotation',a.visual.rotation,'number')+field('默认层级','visual.layer',a.visual.layer,'number')+`<label class="check-row"><input id="asset-interactive" type="checkbox" ${a.interactive?'checked':''}>新实例启用该类型的交互</label><p class="field-hint">修改素材默认属性只影响以后放置的实例。图像与居民状态独立，多个实例复用同一图片。</p>`;bindFields(a);$('asset-interactive').onchange=e=>{a.interactive=e.target.checked;touch();};
}
$('map').addEventListener('dragover',e=>{if(mode==='edit'&&e.dataTransfer.types.includes('application/x-nyairo-resource')){e.preventDefault();e.dataTransfer.dropEffect='copy';}});
$('map').addEventListener('drop',e=>{if(mode!=='edit')return;const value=e.dataTransfer.getData('application/x-nyairo-resource');if(!value)return;e.preventDefault();run(()=>{placingAsset=value.startsWith('builtin:')?null:value;const kind=placingAsset?draft.assets[placingAsset].kind:value.slice(8);addObject(kind,mapPoint(e));});});
$('asset-file').onchange=()=>run(async()=>{const file=$('asset-file').files[0];if(!file)return;if(file.size>524288)throw new Error('单张图片最多 512 KiB，请先缩小图片。');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('图片无法读取。'));reader.readAsDataURL(file);});
  const mime=file.type||({png:'image/png',webp:'image/webp',svg:'image/svg+xml'})[file.name.split('.').at(-1).toLowerCase()];const a=await api('/api/assets/import',{name:file.name.replace(/\.[^.]+$/,''),mime,data,kind:$('import-asset-kind').value,category:$('import-asset-category').value,collection:$('import-asset-collection').value,interactive:$('import-asset-interactive').checked});
  await new Promise((resolve,reject)=>{const pic=new Image();pic.onload=resolve;pic.onerror=()=>reject(new Error('图片无法显示，请使用有效的静态图片。'));pic.src=assetUri(a);});if(Object.keys(draft.assets).length>=32)throw new Error('最多 32 个自定义素材。');draft.assets[a.id]=a;touch();renderAll();assetFocus=a.id;renderAssetInspector();toast('图片已导入，可复用放置。世界导出会嵌入实际使用的素材。');
});
for(const e of document.querySelectorAll('[data-domain]'))e.onclick=()=>setDomain(e.dataset.domain);
for(const e of document.querySelectorAll('[data-space-tool]'))e.onclick=()=>{spaceTool=e.dataset.spaceTool;placing=null;tool='select';renderV04();};
for(const key of ['walls','floors','objects'])$('layer-'+key).onchange=e=>{visibleLayers[key]=e.target.checked;renderMap();};
bind('space-copy-btn',()=>{if(spatialSelection)copyObjects();else toast('先选择墙、门窗或地面。');});bind('space-paste-btn',()=>pasteSpatial());bind('space-delete-btn',deleteSpatial);
bind('import-asset-btn',()=>{$('asset-file').value='';$('asset-file').click();});$('asset-category').onchange=()=>{renderList();renderAssetLibrary();};$('asset-collection').onchange=renderAssetLibrary;
bind('walk-btn',()=>execute({action:'walk',x:Number($('walk-x').value),y:Number($('walk-y').value)}));

for(const el of document.querySelectorAll('button'))el.disabled=true;
run(async()=>{
  const result=await api('/api/bootstrap');themes=result.themes;actionSpecs=result.actions;drawing=result.assets;draft=result.draft.package;draftRevision=result.draft.revision;trialList=result.trials;selected.id=draft.initial.room_id;selection=new Set([ref('room',selected.id)]);
  savedPackage=clone(draft);savedFingerprint=fingerprint(draft);recoveryBase={revision:draftRevision,fingerprint:savedFingerprint};history=new DraftHistory(draft);
  try {
    // A new document gets its own key: a refresh/duplicated tab must never
    // clear an earlier, still-unreviewed recovery copy while its draft is clean.
    const tabId=recoveryClient;
    recovery=new RecoveryStore(localStorage,result.workspace_id,tabId);pendingBackups=recovery.candidates(draft);
  }catch{backupUnavailable=true;backupStatus='恢复副本不可用 · 请手动保存';}
  const allBackups=new Map(pendingBackups.map(b=>[b.key,b]));
  for(const b of result.recoveries){if(editableShape(b.package)&&fingerprint(b.package)!==savedFingerprint){const key='nyairo.editor.backup.v1.'+result.workspace_id+'.'+b.client_id;allBackups.set(key,{...b,key});}}
  pendingBackups=[...allBackups.values()].sort((a,b)=>b.updated-a.updated);
  ready=true;for(const el of document.querySelectorAll('button'))el.disabled=false;renderAll();
});
