// Creator history, draft recovery and map geometry; no runtime state is stored here.
export const fingerprint = value => JSON.stringify(value, (_key, val) => val && typeof val==='object' && !Array.isArray(val)
  ? Object.fromEntries(Object.keys(val).sort().map(k=>[k,val[k]])) : val);
const copy = value => JSON.parse(value);
const clamp = (v, low, high) => Math.min(high, Math.max(low, v));

export class DraftHistory {
  constructor(packageValue, {limit=40, maxBytes=32_000_000}={}) {
    this.current=fingerprint(packageValue);this.past=[];this.future=[];
    this.limit=limit;this.maxBytes=maxBytes;this.group=null;
  }
  get canUndo(){return this.past.length>0;}
  get canRedo(){return this.future.length>0;}
  endGroup(){this.group=null;}
  record(value, group=null) {
    const next=fingerprint(value);
    if(next===this.current)return false;
    if(!group||this.group!==group)this.past.push(this.current);
    this.current=next;this.group=group;this.future=[];this.trim();return true;
  }
  trim(){
    while(this.past.length>this.limit)this.past.shift();
    while(this.past.length && [this.current,...this.past,...this.future].reduce((sum,s)=>sum+s.length*2,0)>this.maxBytes)this.past.shift();
  }
  undo(){
    if(!this.canUndo)return null;
    this.future.push(this.current);this.current=this.past.pop();this.endGroup();return copy(this.current);
  }
  redo(){
    if(!this.canRedo)return null;
    this.past.push(this.current);this.current=this.future.pop();this.endGroup();return copy(this.current);
  }
}

// This permits unfinished fields, but not broken references or malformed render data.
export function editableShape(p){
  try {
    if(!p||!((p.format==='nyairo.world-package.v1'&&p.requires==='nyairo.editor-runtime.v1')||(p.format==='nyairo.world-package.v2'&&p.requires==='nyairo.editor-runtime.v2')||(p.format==='nyairo.world-package.v3'&&p.requires==='nyairo.editor-runtime.v3'))||!p.meta||!p.initial)return false;
    const v3=p.format==='nyairo.world-package.v3',v2=v3||p.format==='nyairo.world-package.v2',color=x=>typeof x==='string'&&/^#[0-9a-f]{6}$/i.test(x);
    if(v2&&(!p.scene||!color(p.scene.background)||typeof p.scene.grid!=='boolean'||typeof p.scene.decorations!=='boolean'))return false;
    if(!['id','name','author','version','description','license','cover'].every(k=>typeof p.meta[k]==='string'))return false;
    if(!['room_id','time','resident_name'].every(k=>typeof p.initial[k]==='string'))return false;
    if(!Array.isArray(p.rooms)||p.rooms.length<1||p.rooms.length>60||!Array.isArray(p.objects)||p.objects.length>300||!Array.isArray(p.connections)||p.connections.length>300)return false;
    const ids=new Set();
    for(const r of p.rooms){
      if(!r||typeof r.id!=='string'||ids.has(r.id)||typeof r.name!=='string'||typeof r.description!=='string'||!['room','shop','outdoor'].includes(r.kind))return false;
      if(!['x','y','w','h'].every(k=>typeof r[k]==='number'&&Number.isFinite(r[k])))return false;
      if(r.hours!==null&&(!r.hours||!['open','close'].every(k=>Number.isFinite(r.hours[k]))))return false;
      if(v2&&(!r.visual||!['floor','border','background'].every(k=>color(r.visual[k]))||!['bedroom','bookstore','cafe','street'].includes(r.visual.theme)||!['none','planks','tiles'].includes(r.visual.texture)||!['none','solid','dashed'].includes(r.visual.border_style)))return false;
      ids.add(r.id);
    }
    if(!ids.has(p.initial.room_id)||!p.connections.every(e=>e&&ids.has(e.from)&&ids.has(e.to)))return false;
    const itemIds=new Set(),actions=['sit','rest','read','pick_up','place','store','retrieve','drink','fill','toggle_device','toggle'];
    for(const o of p.objects){
      if(!o||typeof o.id!=='string'||itemIds.has(o.id)||typeof o.name!=='string'||typeof o.description!=='string'||!ids.has(o.room_id)||!['chair','bed','book','cup','cabinet','table','plant','dispenser','lamp'].includes(o.kind))return false;
      if(!['x','y','capacity','servings'].every(k=>Number.isFinite(o[k]))||typeof o.portable!=='boolean'||!Array.isArray(o.actions)||!o.actions.every(a=>actions.includes(a)))return false;
      if(v2&&(!o.visual||!['scale','rotation','layer'].every(k=>Number.isFinite(o.visual[k]))||typeof o.visual.show_name!=='boolean'||!o.behavior||typeof o.behavior!=='object'))return false;
      const params={book:{read:['minutes','energy_cost']},bed:{rest:['minutes','energy_gain']},cup:{drink:['hydration_gain']},dispenser:{fill:['minutes','servings'],toggle_device:['minutes']},lamp:{toggle:['minutes']}}[o.kind]||{};
      if(v2&&!Object.entries(params).every(([action,keys])=>o.behavior[action]&&keys.every(k=>Number.isFinite(o.behavior[action][k]))))return false;
      itemIds.add(o.id);
    }
    if(v3){
      if(!p.assets||typeof p.assets!=='object'||Object.values(p.assets).some(a=>!a||!['image/png','image/webp','image/svg+xml'].includes(a.mime)||typeof a.data!=='string'))return false;
      for(const r of p.rooms){if(!r.space||typeof r.space.enabled!=='boolean'||!r.space.entry||!['x','y'].every(k=>Number.isFinite(r.space.entry[k])))return false;
        for(const [kind,keys] of Object.entries({walls:['x1','y1','x2','y2','width'],doors:['offset','length'],windows:['offset','length'],floors:['x','y','w','h']})){if(!Array.isArray(r.space[kind])||r.space[kind].some(i=>!i||typeof i.id!=='string'||!color(i.color)||!keys.every(k=>Number.isFinite(i[k]))))return false;}
        if(r.space.doors.concat(r.space.windows).some(d=>!r.space.walls.some(w=>w.id===d.wall_id)))return false;
      }
      if(p.objects.some(o=>!o.interaction||typeof o.interaction.near!=='boolean'||!Number.isFinite(o.interaction.radius)||typeof o.initial_status!=='string'||(o.asset_id!==null&&!p.assets[o.asset_id])))return false;
    }
    return true;
  }catch{return false;}
}

export class RecoveryStore {
  constructor(storage, workspaceId, tabId){
    this.storage=storage;this.prefix='nyairo.editor.backup.v1.'+workspaceId+'.';this.key=this.prefix+tabId;
  }
  write(packageValue, baseRevision, baseFingerprint, now=Date.now()){
    const data={schema:1,package:packageValue,baseRevision,baseFingerprint,updated:now};
    const serialized=fingerprint(data);
    if(serialized.length>16_000_000)throw new Error('恢复副本过大，请手动保存或导出。');
    this.storage.setItem(this.key,serialized);
    return data;
  }
  clearOwn(){this.storage.removeItem(this.key);}
  preserve(packageValue,baseRevision,baseFingerprint){
    const data={schema:1,package:packageValue,baseRevision,baseFingerprint,updated:Date.now()};
    const serialized=fingerprint(data);
    if(serialized.length>16_000_000)throw new Error('恢复副本过大，请先导出当前修改。');
    this.storage.setItem(this.prefix+'kept-'+crypto.randomUUID(),serialized);
  }
  candidates(savedPackage){
    const saved=fingerprint(savedPackage),result=[];
    for(let i=0;i<this.storage.length;i++){
      const key=this.storage.key(i);if(!key?.startsWith(this.prefix))continue;
      try {
        const raw=this.storage.getItem(key);if(!raw||raw.length>16_000_000)continue;
        const b=JSON.parse(raw);
        if(b.schema!==1||!Number.isInteger(b.baseRevision)||b.baseRevision<0||typeof b.baseFingerprint!=='string'||!Number.isFinite(b.updated)||!editableShape(b.package)||fingerprint(b.package)===saved)continue;
        result.push({...b,key});
      }catch{/* A corrupt entry cannot prevent opening the saved server draft. */}
    }
    return result.sort((a,b)=>b.updated-a.updated);
  }
}

export function moveRoom(room, dx, dy, snap=true){
  if(![room.x,room.y,room.w,room.h,dx,dy].every(Number.isFinite)||room.w<=0||room.h<=0||room.w>1000||room.h>620)return null;
  const round=v=>snap?Math.round(v/5)*5:Math.round(v);
  return {x:clamp(round(room.x+dx),0,1000-room.w),y:clamp(round(room.y+dy),0,620-room.h)};
}

export function moveObject(p, item, point){
  if(!Number.isFinite(point.x)||!Number.isFinite(point.y))return null;
  // Prefer the topmost drawn room where overlapping rectangles exist.
  const room=[...p.rooms].reverse().find(r=>r.w>0&&r.h>0&&point.x>=r.x&&point.x<=r.x+r.w&&point.y>=r.y&&point.y<=r.y+r.h);
  if(!room)return null;
  return {room_id:room.id,x:clamp(Math.round((point.x-room.x)/room.w*100),5,95),y:clamp(Math.round((point.y-room.y)/room.h*100),15,90)};
}
