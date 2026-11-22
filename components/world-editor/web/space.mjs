// Room-local geometry. Runtime pathfinding stays authoritative on the server.
export const emptySpace=(enabled=false)=>({enabled,entry:{x:5,y:10},walls:[],doors:[],windows:[],floors:[]});
export const snap5=v=>Math.max(0,Math.min(100,Math.round(v/5)*5));
export function localPoint(room,point){return {x:snap5((point.x-room.x)/room.w*100),y:snap5((point.y-room.y)/room.h*100)};}
export function spaceCopy(room,type,id){
  const item=room.space[type].find(e=>e.id===id);if(!item)return null;
  return {type,item:structuredClone(item),doors:type==='walls'?structuredClone(room.space.doors.filter(d=>d.wall_id===id)):[],windows:type==='walls'?structuredClone(room.space.windows.filter(d=>d.wall_id===id)):[]};
}
export function spacePaste(room,copy,idFactory){
  if(!copy)return null;const item=structuredClone(copy.item),old=item.id;item.id=idFactory();
  if(copy.type==='walls'){const dx=item.x2<100?5:item.x1>0?-5:0,dy=item.y2<100?5:item.y1>0?-5:0;item.x1+=dx;item.x2+=dx;item.y1+=dy;item.y2+=dy;
    for(const kind of ['doors','windows'])for(const d of copy[kind])room.space[kind].push({...d,id:idFactory(),wall_id:item.id});
  }else if(copy.type==='floors'){item.x=Math.min(100-item.w,item.x+5);item.y=Math.min(100-item.h,item.y+5);}
  else{if(!room.space.walls.some(w=>w.id===item.wall_id))throw new Error('请先粘贴依附的墙壁；门窗只能粘贴到原区域。');item.offset+=5;}
  room.space[copy.type].push(item);return item.id;
}
export function removeSpace(room,type,id){room.space[type]=room.space[type].filter(x=>x.id!==id);if(type==='walls')for(const k of ['doors','windows'])room.space[k]=room.space[k].filter(x=>x.wall_id!==id);}
export function translateSpace(room,type,item,dx,dy){
  const next=structuredClone(item);dx=snap5(Math.abs(dx))*Math.sign(dx);dy=snap5(Math.abs(dy))*Math.sign(dy);
  if(type==='walls'){dx=Math.max(-next.x1,Math.min(100-next.x2,dx));dy=Math.max(-next.y1,Math.min(100-next.y2,dy));next.x1+=dx;next.x2+=dx;next.y1+=dy;next.y2+=dy;}
  if(type==='floors'){next.x=Math.max(0,Math.min(100-next.w,next.x+dx));next.y=Math.max(0,Math.min(100-next.h,next.y+dy));}
  if(type==='doors'||type==='windows'){const w=room.space.walls.find(w=>w.id===next.wall_id);const length=w.x2-w.x1+w.y2-w.y1;next.offset=Math.max(0,Math.min(length-next.length,next.offset+(w.x1===w.x2?dy:dx)));}
  return next;
}
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const assetUri=a=>`data:${a.mime};base64,${a.data}`;

function parseHexRgb(hex){
  const s=String(hex||'').trim();
  if(/^#[0-9a-f]{6}$/i.test(s))return [parseInt(s.slice(1,3),16),parseInt(s.slice(3,5),16),parseInt(s.slice(5,7),16)];
  if(/^#[0-9a-f]{3}$/i.test(s))return [parseInt(s[1]+s[1],16),parseInt(s[2]+s[2],16),parseInt(s[3]+s[3],16)];
  return null;
}
const toHex=(r,g,b)=>'#'+[r,g,b].map(v=>Math.max(0,Math.min(255,Math.round(v))).toString(16).padStart(2,'0')).join('');

export function mapTone(hex,role='floor'){
  const dark=typeof document!=='undefined'&&document.documentElement.dataset.theme!=='light';
  if(!dark){
    if(role==='canvas'&&String(hex||'').toLowerCase()==='#f8f9fc')return '#edf2f7';
    if(role==='grid')return '#bccbd9';
    return hex;
  }
  if(role==='grid')return '#2b3240';
  const rgb=parseHexRgb(hex);
  if(!rgb)return hex;
  const [r,g,b]=rgb,lum=0.299*r+0.587*g+0.114*b,avg=(r+g+b)/3;
  if(role==='canvas')return lum>180?'#13151b':hex;
  if(role==='header')return lum>170?'#20242e':hex;
  if(role==='border')return lum>135?toHex(62+(r-avg)*0.25,68+(g-avg)*0.25,84+(b-avg)*0.25):hex;
  if(role==='wall')return String(hex||'').toLowerCase()==='#8c8a7d'?'#646976':hex;
  if(role==='floor'&&lum>160)return toHex(37+(r-avg)*0.42,39+(g-avg)*0.42,45+(b-avg)*0.42);
  return hex;
}

export function objectArt(p,o,builtin,scale=1,rotation=0){
  const a=p.assets?.[o.asset_id];let art=a?`<image x="-40" y="-30" width="80" height="60" href="${esc(assetUri(a))}" preserveAspectRatio="xMidYMid meet"/>`:(builtin[o.kind]||'');
  return `<g transform="rotate(${rotation}) scale(${scale})">${art}</g>`;
}
export function spaceSvg(room,p,state,selected,layers,part,edit){
  const s=room.space;if(!s?.enabled)return '';const xy=(x,y)=>[room.x+room.w*x/100,room.y+room.h*y/100];let html='';
  const dark=typeof document!=='undefined'&&document.documentElement.dataset.theme!=='light';
  const attrs=(type,item)=>`data-space="${type}" data-space-id="${esc(item.id)}" data-space-room="${esc(room.id)}" class="space-item ${selected?.room===room.id&&selected.type===type&&selected.id===item.id&&edit?'space-selected':''}" role="button" tabindex="${edit?0:-1}" aria-label="${{walls:'墙壁',doors:'门',windows:'窗',floors:'地面'}[type]} ${esc(item.id)}"`;
  if(part==='floor'&&layers.floors){
    for(const f of s.floors){const [x,y]=xy(f.x,f.y),w=room.w*f.w/100,h=room.h*f.h/100,id='patch-'+room.id+'-'+f.id,fc=mapTone(f.color,'floor');
      if(f.texture!=='none')html+=`<defs><pattern id="${esc(id)}" width="25" height="25" patternUnits="userSpaceOnUse"><rect width="25" height="25" fill="${esc(fc)}"/><path d="${f.texture==='tiles'?'M0 0H25V25H0Z':'M0 0H25M0 12H25M12 0V12'}" stroke="${dark?'#9aa4b8':'#847968'}" opacity="${dark?'.11':'.16'}" fill="none"/></pattern></defs>`;
      html+=`<g ${attrs('floors',f)}><rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${f.texture==='none'?esc(fc):'url(#'+esc(id)+')'}"/>`;
      if(f.asset_id&&p.assets[f.asset_id])html+=`<image x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="none" href="${esc(assetUri(p.assets[f.asset_id]))}"/>`;
      if(selected?.id===f.id&&edit)html+=`<rect x="${x+1}" y="${y+1}" width="${Math.max(0,w-2)}" height="${Math.max(0,h-2)}" fill="none" stroke="${dark?'#7da6db':'#3b76a8'}" stroke-dasharray="5 4" stroke-width="2"/>`;html+='</g>';
    }
  }
  if(part==='walls'&&edit&&p.scene.grid){const id='indoor-grid-'+room.id;html+=`<defs><pattern id="${esc(id)}" x="${room.x}" y="${room.y}" width="${room.w/20}" height="${room.h/20}" patternUnits="userSpaceOnUse"><path d="M${room.w/20} 0H0V${room.h/20}" stroke="${dark?'#8aa4c8':'#7b8fab'}" stroke-width=".6" opacity="${dark?'.13':'.20'}" fill="none"/></pattern></defs><rect x="${room.x}" y="${room.y}" width="${room.w}" height="${room.h}" fill="url(#${esc(id)})" pointer-events="none"/>`;}
  if(part==='walls'&&layers.walls){
    for(const w of s.walls){const [x,y]=xy(w.x1,w.y1),[xx,yy]=xy(w.x2,w.y2),wc=mapTone(w.color,'wall');html+=`<g ${attrs('walls',w)}><path d="M${x} ${y}L${xx} ${yy}" stroke="transparent" stroke-width="18" fill="none"/><path d="M${x} ${y}L${xx} ${yy}" stroke="${esc(wc)}" stroke-width="${w.width}" fill="none"/>${edit?`<text x="${(x+xx)/2+8}" y="${(y+yy)/2-10}" fill="${dark?'#939aa8':'#6c716c'}" font-size="10" pointer-events="none">${Math.round((w.x2-w.x1+w.y2-w.y1)/5)} 格</text>`:''}</g>`;}
    const gapBg=mapTone(p.scene.background,'canvas');
    for(const type of ['doors','windows'])for(const d of s[type]){const w=s.walls.find(w=>w.id===d.wall_id);if(!w)continue;const vertical=w.x1===w.x2,[x,y]=xy(w.x1+(vertical?0:d.offset),w.y1+(vertical?d.offset:0)),[xx,yy]=xy(w.x1+(vertical?0:d.offset+d.length),w.y1+(vertical?d.offset+d.length:0));const status=state?.doors?.[room.id]?.[d.id]||d.state,color=type==='windows'?(dark&&d.color==='#90b6cb'?'#7da6db':d.color):status==='open'?'#68bf94':status==='locked'?'#c97b84':(dark&&d.color==='#bd976e'?'#cfab82':d.color);
      html+=`<g ${attrs(type,d)} data-door-state="${esc(status||'window')}"><path d="M${x} ${y}L${xx} ${yy}" stroke="${esc(gapBg)}" stroke-width="13"/><path d="M${x} ${y}L${xx} ${yy}" stroke="${esc(color)}" stroke-width="7" ${status==='open'?'stroke-dasharray="3 5"':''}/><text x="${(x+xx)/2+12}" y="${(y+yy)/2}" fill="${esc(color)}" font-size="11">${type==='windows'?'窗':({open:'开',closed:'关',locked:'锁'})[status]}</text></g>`;
    }
  }
  return html;
}
