"""v3 spatial content, retaining the proven v1/v2 core in legacy.py."""
from legacy import *
import legacy
from space import empty_space, validate_space, cell, object_cell, path, door_cells, DOOR_ACTIONS
from resources import validate_asset, KINDS

FORMAT='nyairo.world-package.v3'
ENGINE='nyairo.editor-runtime.v3'
SAVE_FORMAT='nyairo.editor-save.v3'
OLD_FORMATS=(legacy.LEGACY_FORMAT,legacy.FORMAT)
DEFAULT_ACTIONS={**legacy.DEFAULT_ACTIONS,'dispenser':['fill','toggle_device'],'lamp':['toggle']}
PARAMETERS={**legacy.PARAMETERS,'fill':{'minutes':(2,1,60,'装水分钟'),'servings':(1,1,20,'装入份数')},'toggle_device':{'minutes':(1,1,60,'设备切换分钟')},'toggle':{'minutes':(1,1,60,'开关分钟')}}
ACTIONS=set().union(*DEFAULT_ACTIONS.values())
ALL_ACTIONS=ACTIONS|{'walk','move','stand','wait'}|set(DOOR_ACTIONS)
STATUS={'cup':('empty','water'),'dispenser':('available','unavailable'),'lamp':('off','on')}

def default_behavior(kind):
    return {a:{k:v[0] for k,v in PARAMETERS[a].items()} for a in DEFAULT_ACTIONS[kind] if a in PARAMETERS}

def default_status(kind, servings=0):
    return 'water' if kind=='cup' and servings else 'empty' if kind=='cup' else 'available' if kind=='dispenser' else 'off' if kind=='lamp' else 'neutral'

def templates():
    out=legacy.templates()
    for a,label,condition,result in [('fill','给持有杯装水','设备可用、持有空杯、同区域开放且满足接近条件','空杯 → 装入配置份数；推进配置分钟'),('toggle_device','切换设备可用状态','同区域开放且满足接近条件','可用 ↔ 不可用；推进配置分钟'),('toggle','开关灯','同区域开放且满足接近条件','关闭 ↔ 开启；推进配置分钟')]:
        out[a]={'label':label,'condition':condition,'result':result,'parameters':[{'key':k,'default':v[0],'min':v[1],'max':v[2],'label':v[3]} for k,v in PARAMETERS[a].items()]}
    for a,label in zip(DOOR_ACTIONS,['开门','关门','锁门','解锁']): out[a]={'label':label,'condition':'居民在门附近；状态允许且行为启用','result':'门状态改变 → 更新通行；时间 +1 分钟','parameters':[]}
    return out

def upgrade(p):
    p=copy.deepcopy(p);p.update(format=FORMAT,requires=ENGINE,assets={})
    for r in p['rooms']: r['space']=empty_space()
    for o in p['objects']: o.update(asset_id=None,initial_status=default_status(o['kind'],o['servings']),interaction={'near':False,'radius':2})
    return p

def projection(p, for_save=False):
    b=copy.deepcopy(p);b.update(format=legacy.FORMAT,requires=legacy.ENGINE);del b['assets']
    for r in b['rooms']: del r['space']
    for o in b['objects']:
        for k in ('asset_id','interaction','initial_status'): del o[k]
        if o['kind'] in ('dispenser','lamp'): o.update(kind='table',actions=[],behavior={})
        if for_save and o['kind']=='cup': o['servings']=20
    return b

def validate_package(raw):
    if isinstance(raw,dict) and raw.get('format') in OLD_FORMATS:
        old,warnings=legacy.validate_package(raw);return upgrade(old),warnings
    if not isinstance(raw,dict) or raw.get('format')!=FORMAT or raw.get('requires')!=ENGINE: fail('世界包版本不兼容：支持编辑器 v1、v2 升级，以及 v3。','INCOMPATIBLE')
    fields(raw,['format','requires','meta','scene','rooms','connections','objects','initial','assets'],'世界包')
    p=copy.deepcopy(raw)
    if not isinstance(p['assets'],dict) or len(p['assets'])>32: fail('最多 32 个自定义素材。')
    total=0
    for aid,a in p['assets'].items():
        total+=validate_asset(a)
        if aid!=a['id']: fail('素材清单键与 ID 不一致。')
    if total>3*1024*1024: fail('素材总大小不能超过 3 MiB。')
    if not isinstance(p['rooms'],list) or not isinstance(p['objects'],list): fail('区域和物件需要是列表。')
    for r in p['rooms']:
        fields(r,['id','name','description','kind','x','y','w','h','hours','visual','space'],'区域');validate_space(r['space'],p['assets'])
    for o in p['objects']:
        fields(o,['id','name','description','kind','room_id','x','y','actions','portable','capacity','servings','visual','behavior','asset_id','interaction','initial_status'],'物件')
        if not isinstance(o['kind'],str) or o['kind'] not in KINDS: fail('物件类型不支持。')
        if not isinstance(o['actions'],list) or any(a not in DEFAULT_ACTIONS[o['kind']] for a in o['actions']) or len(set(o['actions']))!=len(o['actions']): fail('物件用途需要来自受支持的模板。')
        if o['asset_id'] is not None and o['asset_id'] not in p['assets']: fail('物件引用的素材不存在。')
        fields(o['interaction'],['near','radius'],'交互条件')
        if type(o['interaction']['near']) is not bool: fail('接近条件需要是布尔值。')
        integer(o['interaction']['radius'],'接近网格距离',1,8)
        if o['initial_status'] not in STATUS.get(o['kind'],('neutral',)): fail('物件初始状态无效。')
        if o['kind']=='cup' and (o['initial_status']=='water')!=(o['servings']>0): fail('杯子空满状态与初始份数不一致。')
        expected=default_behavior(o['kind']);fields(o['behavior'],expected.keys(),'行为参数')
        for a in expected:
            fields(o['behavior'][a],PARAMETERS[a].keys(),'行为'+a)
            for k,(_,lo,hi,label) in PARAMETERS[a].items(): integer(o['behavior'][a][k],label,lo,hi)
    _,warnings=legacy.validate_package(projection(p))
    if len(canonical(p).encode())>8_000_000: fail('世界包超过 8 MB。')
    return p,warnings

def export_package(raw):
    p,_=validate_package(raw);used={o['asset_id'] for o in p['objects']}
    used.update(f['asset_id'] for r in p['rooms'] for f in r['space']['floors'])
    p['assets']={k:v for k,v in p['assets'].items() if k in used};return p

def upgrade_state(p,s):
    s=copy.deepcopy(s);room=next(r for r in p['rooms'] if r['id']==s['resident']['room_id'])
    s['resident']['position']=copy.deepcopy(room['space']['entry']);s['doors']={r['id']:{d['id']:d['state'] for d in r['space']['doors']} for r in p['rooms']};s['path']={'room_id':room['id'],'points':[]}
    for o in p['objects']:
        os=s['objects'][o['id']];os['status']=default_status(o['kind'],os['servings']);x,y=object_cell(o);os['position']={'x':x,'y':y}
        if os['placement']=='held': os['position']=copy.deepcopy(s['resident']['position'])
    for os in s['objects'].values():
        if os['container_id']: os['position']=copy.deepcopy(s['objects'][os['container_id']]['position'])
    return s

def initial_state(p):
    s=upgrade_state(p,legacy.initial_state(p))
    for o in p['objects']: s['objects'][o['id']]['status']=o['initial_status']
    return s

def validate_save(raw):
    fields(raw,['format','package','package_hash','state'],'试运行存档')
    if raw['format'] in (legacy.SAVE_FORMAT,legacy.LEGACY_SAVE):
        p,s=legacy.validate_save(raw);p=upgrade(p);return p,upgrade_state(p,s)
    if raw['format']!=SAVE_FORMAT or not isinstance(raw['package'],dict) or raw['package'].get('format')!=FORMAT: fail('存档与世界包格式版本不兼容。','INCOMPATIBLE')
    if digest(raw['package'])!=raw['package_hash']: fail('存档的内容包摘要不匹配。','CORRUPT')
    p,_=validate_package(raw['package']);s=copy.deepcopy(raw['state'])
    fields(s,['revision','minutes','resident','objects','events','doors','path'],'居民状态')
    fields(s['resident'],['name','room_id','pose','energy','hydration','held','using','position'],'居民');cell(s['resident']['position'])
    expected={r['id']:{d['id'] for d in r['space']['doors']} for r in p['rooms']}
    if not isinstance(s['doors'],dict) or set(s['doors'])!=set(expected): fail('存档门区域不一致。')
    for rid,ids in expected.items():
        if not isinstance(s['doors'][rid],dict) or set(s['doors'][rid])!=ids or any(v not in ('open','closed','locked') for v in s['doors'][rid].values()): fail('存档门状态不一致。')
    fields(s['path'],['room_id','points'],'上次路径')
    if s['path']['room_id']!=s['resident']['room_id'] or not isinstance(s['path']['points'],list) or len(s['path']['points'])>400: fail('存档路径无效。')
    previous=None
    for pos in s['path']['points']:
        q=cell(pos)
        if previous is not None and sum(abs(a-b) for a,b in zip(previous,q))!=1: fail('路径不是相邻网格。')
        previous=q
    if previous is not None and previous!=cell(s['resident']['position']): fail('路径终点与居民不一致。')
    base=projection(p,True);bs=copy.deepcopy(s);del bs['doors'];del bs['path'];del bs['resident']['position']
    if not isinstance(s['objects'],dict) or set(s['objects'])!={o['id'] for o in p['objects']}: fail('存档物件不一致。')
    for o in p['objects']:
        os=s['objects'][o['id']];fields(os,['room_id','placement','container_id','read_count','use_count','servings','status','position'],'物件状态');cell(os['position'])
        if os['status'] not in STATUS.get(o['kind'],('neutral',)): fail('存档物件状态无效。')
        if o['kind']=='cup' and (os['status']=='water')!=(os['servings']>0): fail('杯子状态和份数矛盾。')
        if os['placement']=='held' and os['position']!=s['resident']['position']: fail('持有物位置与居民不一致。')
        if os['placement']=='container' and os['container_id'] in s['objects'] and os['position']!=s['objects'][os['container_id']]['position']: fail('收纳物与柜子位置不一致。')
        del bs['objects'][o['id']]['status'];del bs['objects'][o['id']]['position']
    doors={r['id']+':'+d['id'] for r in p['rooms'] for d in r['space']['doors']}
    if not isinstance(s['events'],list): fail('存档事件需要是列表。')
    for e,be in zip(s['events'],bs['events']):
        if not isinstance(e,dict) or e.get('action') not in ALL_ACTIONS: fail('存档包含不支持的动作。')
        if e['action'] in DOOR_ACTIONS and e.get('target') not in doors: fail('门事件引用无效。')
        if e['action']=='walk' and e.get('target') not in {r['id'] for r in p['rooms']}: fail('路径事件区域无效。')
        if e['action'] in ('fill','toggle_device','toggle') and not any(o['id']==e.get('target') and e['action'] in DEFAULT_ACTIONS[o['kind']] for o in p['objects']): fail('交互事件目标无效。')
        if e['action'] not in legacy.ACTIONS|{'move','stand','wait'}: be.update(action='wait',target=None)
    legacy.validate_save({'format':legacy.SAVE_FORMAT,'package':base,'package_hash':digest(base),'state':bs});return p,s

def _reachable(room,s,target,radius):
    route=path(room['space'],s['doors'][room['id']],cell(s['resident']['position']),target)
    return route is not None and len(route)-1<=radius

def apply_action(p,current,command):
    if not isinstance(command,dict) or set(command)-{'action','target','item','minutes','x','y'}: fail('不支持的操作字段。')
    action=command.get('action')
    if not isinstance(action,str) or action not in ALL_ACTIONS: fail('不支持的动作。','UNKNOWN_ACTION')
    s=copy.deepcopy(current);r=s['resident'];room=next(x for x in p['rooms'] if x['id']==r['room_id']);target=command.get('target');duration=1
    if target is not None and not isinstance(target,str): fail('目标 ID 必须是文字。')
    if action=='walk':
        if not room['space']['enabled']: fail('此旧区域未启用室内空间，请使用区域移动。','SPACE_OFF')
        if r['using']: fail('请先站起，再行走。','IN_USE')
        end=cell({'x':command.get('x'),'y':command.get('y')});route=path(room['space'],s['doors'][room['id']],cell(r['position']),end)
        if route is None: fail('无法到达：墙壁或关闭/锁定的门阻断路径。请从本侧接近门并打开。','BLOCKED')
        if len(route)==1: fail('居民已经在这个网格。','SAME_POSITION')
        r['position']=route[-1];s['path']={'room_id':room['id'],'points':route};duration=len(route)-1;r['energy']=max(0,r['energy']-(duration+9)//10)
        message=f'沿可达路径行走 {duration} 格，到达 ({end[0]},{end[1]})；耗时 {duration} 分钟。'
    elif action in DOOR_ACTIONS:
        if not room['space']['enabled']: fail('当前区域未启用室内空间。','SPACE_OFF')
        door=next((d for d in room['space']['doors'] if room['id']+':'+d['id']==target),None)
        if not door: fail('门不在当前区域。','UNKNOWN_TARGET')
        if action not in door['actions']: fail('此门未启用该行为。','NOT_ALLOWED')
        if not is_open(room,start_minute(p)+s['minutes']): fail('区域当前不允许使用物件。','CLOSED')
        if not any(_reachable(room,s,c,2) for c in door_cells(room['space'],door)): fail('无法操作门：请先走到门旁边（两格内）。','TOO_FAR')
        state=s['doors'][room['id']][door['id']];needed={'open_door':'closed','close_door':'open','lock_door':'closed','unlock_door':'locked'}[action]
        if state!=needed: fail('无法操作门：门当前'+{'open':'开启','closed':'关闭','locked':'锁定'}[state]+'，不满足此动作条件。','LOCKED' if state=='locked' else 'STATE')
        new={'open_door':'open','close_door':'closed','lock_door':'locked','unlock_door':'closed'}[action];s['doors'][room['id']][door['id']]=new;message='门已'+{'open':'开启，通道恢复通行','closed':'关闭，通道阻止通行','locked':'锁定，无法打开'}[new]+'。'
    else:
        o=next((o for o in p['objects'] if o['id']==target),None)
        if o:
            os=s['objects'][o['id']]
            if os['room_id']!=r['room_id']: fail('物件不在当前区域。','NOT_REACHABLE')
            if os['placement']=='container': fail('物件已收纳，请先取出。','STORED')
            if o['interaction']['near'] and room['space']['enabled'] and os['placement']!='held' and not _reachable(room,s,cell(os['position']),o['interaction']['radius']): fail('无法交互：请先沿可达路径接近目标（'+str(o['interaction']['radius'])+'格内）。','TOO_FAR')
        if action in ('fill','toggle_device','toggle'):
            if not o or action not in o['actions']: fail('此物件未启用该用途。','NOT_ALLOWED')
            if not is_open(room,start_minute(p)+s['minutes']): fail('当前区域的使用时间已结束。','CLOSED')
            duration=o['behavior'][action]['minutes']
            if action=='fill':
                cup=next((x for x in p['objects'] if x['id']==r['held'] and x['kind']=='cup'),None)
                if not cup: fail('无法装水：居民没有持有空杯。','NO_CUP')
                cs=s['objects'][cup['id']]
                if cs['status']!='empty' or cs['servings']!=0: fail('无法装水：请先喝完杯中饮品，不能重复装水。','NOT_EMPTY')
                if os['status']!='available': fail('无法装水：饮水设备当前不可用。','DISABLED')
                cs.update(status='water',servings=o['behavior']['fill']['servings']);os['use_count']+=1;message=f'装水成功：{cup["name"]}由空杯变为装水，含 {cs["servings"]} 份；耗时 {duration} 分钟。'
            else:
                os['status']=('unavailable' if os['status']=='available' else 'available') if action=='toggle_device' else ('off' if os['status']=='on' else 'on');os['use_count']+=1;message=f'{o["name"]}状态已变为 '+{'available':'可用','unavailable':'不可用','on':'开启','off':'关闭'}[os['status']]+'。'
        else:
            if action=='move' and room['space']['enabled'] and path(room['space'],s['doors'][room['id']],cell(r['position']),cell(room['space']['entry'])) is None: fail('无法离开区域：到区域入口的室内路径被阻断。','BLOCKED')
            s,event=legacy.apply_action(p,s,{k:v for k,v in command.items() if k not in ('x','y')});r=s['resident']
            if action=='move': r['position']=copy.deepcopy(next(x for x in p['rooms'] if x['id']==r['room_id'])['space']['entry']);s['path']={'room_id':r['room_id'],'points':[]}
            if action=='place': s['objects'][target]['position']=copy.deepcopy(r['position'])
            if action=='store':
                for v in s['objects'].values():
                    if v['container_id']==target: v['position']=copy.deepcopy(s['objects'][target]['position'])
            if action=='drink':
                cs=s['objects'][target];cs['status']='water' if cs['servings'] else 'empty';event['message']=f'喝水成功：杯子现在'+('为空' if not cs['servings'] else f'剩余 {cs["servings"]} 份')+f'，居民水分 {r["hydration"]}。'
            for v in s['objects'].values():
                if v['placement']=='held': v['position']=copy.deepcopy(r['position'])
            return s,event
    for v in s['objects'].values():
        if v['placement']=='held': v['position']=copy.deepcopy(r['position'])
    s['minutes']+=duration;integer(s['minutes'],'经过分钟',0,5256000);s['revision']+=1
    event={'revision':s['revision'],'minutes':s['minutes'],'action':action,'target':target if action!='walk' else room['id'],'message':message};s['events']=(s['events']+[event])[-200:];return s,event

def cover_svg(package):
    p,_=validate_package(package);base=projection(p);base['objects']=[];out=legacy.cover_svg(base).decode().removesuffix('</g></svg>');rooms={r['id']:r for r in p['rooms']}
    for r in p['rooms']:
        if not r['space']['enabled']: continue
        def xy(x,y): return r['x']+r['w']*x/100,r['y']+r['h']*y/100
        for f in r['space']['floors']:
            x,y=xy(f['x'],f['y']);fill=f['color']
            if f['texture']!='none':
                pattern='patch-'+r['id']+'-'+f['id'];lines='M0 0H25V25H0Z' if f['texture']=='tiles' else 'M0 0H25M0 12H25M12 0V12'
                out+=f'<defs><pattern id="{pattern}" width="25" height="25" patternUnits="userSpaceOnUse"><rect width="25" height="25" fill="{fill}"/><path d="{lines}" stroke="#847968" opacity=".16" fill="none"/></pattern></defs>';fill='url(#'+pattern+')'
            out+=f'<rect x="{x}" y="{y}" width="{r["w"]*f["w"]/100}" height="{r["h"]*f["h"]/100}" fill="{fill}"/>'
            if f['asset_id']:
                a=p['assets'][f['asset_id']];out+=f'<image x="{x}" y="{y}" width="{r["w"]*f["w"]/100}" height="{r["h"]*f["h"]/100}" href="data:{a["mime"]};base64,{a["data"]}"/>'
        for w in r['space']['walls']:
            x,y=xy(w['x1'],w['y1']);xx,yy=xy(w['x2'],w['y2']);out+=f'<path d="M{x} {y}L{xx} {yy}" stroke="{w["color"]}" stroke-width="{w["width"]}"/>'
        for d in r['space']['doors']+r['space']['windows']:
            w=next(w for w in r['space']['walls'] if w['id']==d['wall_id']);vertical=w['x1']==w['x2'];x,y=xy(w['x1']+(0 if vertical else d['offset']),w['y1']+(d['offset'] if vertical else 0));xx,yy=xy(w['x1']+(0 if vertical else d['offset']+d['length']),w['y1']+(d['offset']+d['length'] if vertical else 0));color='#78a489' if d.get('state')=='open' else '#846d71' if d.get('state')=='locked' else d['color'];dash=' stroke-dasharray="3 5"' if d.get('state')=='open' else ''
            out+=f'<path d="M{x} {y}L{xx} {yy}" stroke="{p["scene"]["background"]}" stroke-width="13"/><path d="M{x} {y}L{xx} {yy}" stroke="{color}" stroke-width="7"{dash}/>'
        if p['scene']['decorations']:out+=f'<rect x="{r["x"]+10}" y="{r["y"]+10}" width="{r["w"]-20}" height="38" rx="5" fill="{r["visual"]["background"]}"/>'
        out+=f'<text x="{r["x"]+18}" y="{r["y"]+30}" fill="#526078" font-size="16" font-family="sans-serif">{html.escape(r["name"][:24])}</text>'
    for o in sorted(p['objects'],key=lambda o:o['visual']['layer']):
        r=rooms[o['room_id']];v=o['visual'];x,y=r['x']+r['w']*o['x']/100,r['y']+r['h']*o['y']/100
        if o['asset_id']:
            a=p['assets'][o['asset_id']];art=f'<image x="-40" y="-30" width="80" height="60" href="data:{a["mime"]};base64,{a["data"]}"/>'
        else: art=ASSETS[o['kind']]
        out+=f'<g transform="translate({x},{y}) rotate({v["rotation"]}) scale({v["scale"]/100})">{art}</g>'
        if v['show_name']: out+=f'<text x="{x}" y="{y+45}" text-anchor="middle" font-family="sans-serif" font-size="11">{html.escape(o["name"])}</text>'
    return (out+'</g></svg>').encode()
