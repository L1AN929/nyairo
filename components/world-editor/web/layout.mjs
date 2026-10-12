// Pure creator geometry. Each result is a new package for a single history step.
const clone=p=>structuredClone(p);
const clamp=(v,l,h)=>Math.min(h,Math.max(l,v));
export const ref=(type,id)=>`${type}:${id}`;
export function entries(p,keys){return [...keys].map(key=>{const i=key.indexOf(':'),type=key.slice(0,i),id=key.slice(i+1);return {type,item:(type==='room'?p.rooms:p.objects).find(x=>x.id===id)};}).filter(e=>e.item);}
export function center(p,e){const r=e.type==='room'?e.item:p.rooms.find(r=>r.id===e.item.room_id);return e.type==='room'?{x:r.x+r.w/2,y:r.y+r.h/2}:{x:r.x+r.w*e.item.x/100,y:r.y+r.h*e.item.y/100};}
export function drop(p,point){const r=[...p.rooms].reverse().find(r=>r.w>0&&r.h>0&&point.x>=r.x&&point.x<=r.x+r.w&&point.y>=r.y&&point.y<=r.y+r.h);return r?{room_id:r.id,x:clamp(Math.round((point.x-r.x)/r.w*100),5,95),y:clamp(Math.round((point.y-r.y)/r.h*100),15,90)}:null;}
export function translate(p,keys,dx,dy,step=0){
  if(![dx,dy].every(Number.isFinite))return null;
  const es=entries(p,keys);if(!es.length)return null;
  if(step){const a=es[0].type==='room'?es[0].item:center(p,es[0]);dx=Math.round((a.x+dx)/step)*step-a.x;dy=Math.round((a.y+dy)/step)*step-a.y;}
  const rs=es.filter(e=>e.type==='room').map(e=>e.item),moving=new Set(rs.map(r=>r.id));
  if(rs.length){dx=clamp(dx,-Math.min(...rs.map(r=>r.x)),1000-Math.max(...rs.map(r=>r.x+r.w)));dy=clamp(dy,-Math.min(...rs.map(r=>r.y)),620-Math.max(...rs.map(r=>r.y+r.h)));}
  const next=clone(p);
  for(const e of es){
    if(e.type==='room'){const r=next.rooms.find(r=>r.id===e.item.id);r.x=Math.round(e.item.x+dx);r.y=Math.round(e.item.y+dy);}
    else if(!moving.has(e.item.room_id)){const c=center(p,e),change=drop(next,{x:c.x+dx,y:c.y+dy});if(!change)return null;Object.assign(next.objects.find(o=>o.id===e.item.id),change);}
  }
  return next;
}
export function resize(room,dx,dy,step=0){const round=v=>step?Math.round(v/step)*step:Math.round(v);return {w:clamp(round(room.w+dx),100,1000-room.x),h:clamp(round(room.h+dy),80,620-room.y)};}
export function boxSelect(p,rect,type='object'){return (type==='room'?p.rooms:p.objects).filter(item=>{const c=center(p,{type,item});return c.x>=rect.x&&c.x<=rect.x+rect.w&&c.y>=rect.y&&c.y<=rect.y+rect.h;}).map(item=>ref(type,item.id));}
export function copySelection(p,keys){
  const es=entries(p,keys),rooms=es.filter(e=>e.type==='room').map(e=>clone(e.item)),ids=new Set(rooms.map(r=>r.id)),objects=p.objects.filter(o=>ids.has(o.room_id)||keys.has(ref('object',o.id))).map(clone);
  return {rooms,objects,connections:p.connections.filter(e=>ids.has(e.from)&&ids.has(e.to)).map(clone)};
}
export function paste(p,clip,targetRoom=null){
  if(!clip||(!clip.rooms.length&&!clip.objects.length))throw new Error('请先复制区域或物件。');
  if(p.rooms.length+clip.rooms.length>60||p.objects.length+clip.objects.length>300)throw new Error('副本超过区域或物件数量上限。');
  const n=clone(p),mapping=new Map(),keys=new Set();
  const dx=clip.rooms.length?Math.min(25,1000-Math.max(...clip.rooms.map(r=>r.x+r.w))):0;
  const dy=clip.rooms.length?Math.min(25,620-Math.max(...clip.rooms.map(r=>r.y+r.h))):0;
  for(const r of clip.rooms){const id='room-'+crypto.randomUUID().slice(0,12);mapping.set(r.id,id);n.rooms.push({...clone(r),id,name:r.name+' 副本',x:r.x+dx,y:r.y+dy});keys.add(ref('room',id));}
  for(const o of clip.objects){const id=o.kind+'-'+crypto.randomUUID().slice(0,12),copiedRoom=mapping.get(o.room_id),room_id=copiedRoom||(n.rooms.some(r=>r.id===targetRoom)?targetRoom:n.rooms.some(r=>r.id===o.room_id)?o.room_id:n.initial.room_id);n.objects.push({...clone(o),id,name:o.name+' 副本',room_id,x:copiedRoom?o.x:clamp(o.x+5,5,95),y:copiedRoom?o.y:clamp(o.y+5,15,90)});if(!copiedRoom)keys.add(ref('object',id));}
  n.connections.push(...clip.connections.map(e=>({from:mapping.get(e.from),to:mapping.get(e.to)})));
  return {package:n,keys};
}
export function arrange(p,keys,operation){
  const es=entries(p,keys);if(es.length<2)throw new Error('请先选择至少两个同类对象。');
  if(new Set(es.map(e=>e.type)).size!==1)throw new Error('区域和物件请分别对齐。');
  const type=es[0].type;
  if(type==='object'&&new Set(es.map(e=>e.item.room_id)).size!==1)throw new Error('物件对齐和等距排列需要在同一区域内。');
  const axis=['left','center','right','horizontal'].includes(operation)?'x':'y',size=axis==='x'?'w':'h';
  const point=e=>type==='room'?e.item[axis]+(['center','middle','horizontal','vertical'].includes(operation)?e.item[size]/2:['right','bottom'].includes(operation)?e.item[size]:0):center(p,e)[axis];
  const ordered=[...es].sort((a,b)=>point(a)-point(b)),lo=point(ordered[0]),hi=point(ordered.at(-1)),n=clone(p);
  if(['horizontal','vertical'].includes(operation)&&es.length<3)throw new Error('等间距排列至少需要三个对象。');
  ordered.forEach((e,i)=>{const desired=['horizontal','vertical'].includes(operation)?lo+(hi-lo)*i/(ordered.length-1):['right','bottom'].includes(operation)?hi:['center','middle'].includes(operation)?(lo+hi)/2:lo;
    if(type==='room'){const r=n.rooms.find(r=>r.id===e.item.id),offset=point(e)-e.item[axis];r[axis]=clamp(Math.round(desired-offset),0,(axis==='x'?1000:620)-r[size]);}
    else{const r=p.rooms.find(r=>r.id===e.item.room_id),o=n.objects.find(o=>o.id===e.item.id);o[axis]=clamp(Math.round((desired-r[axis])/r[size]*100),axis==='x'?5:15,axis==='x'?95:90);}
  });return n;
}
