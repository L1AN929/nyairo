"""Small orthogonal indoor grid; room graph travel remains separate."""
from collections import deque
from legacy import fail, fields, integer, color, ident

DOOR_ACTIONS = ['open_door', 'close_door', 'lock_door', 'unlock_door']

def empty_space(enabled=False):
    return {'enabled': enabled, 'entry': {'x': 5, 'y': 10}, 'walls': [], 'doors': [], 'windows': [], 'floors': []}

def validate_space(s, assets):
    fields(s, ['enabled','entry','walls','doors','windows','floors'], '室内空间')
    if type(s['enabled']) is not bool: fail('室内空间开关无效。')
    cell(s['entry'])
    seen = set()
    def common(item):
        ident(item['id'], '空间对象 ID')
        if item['id'] in seen: fail('同一区域的空间对象 ID 重复。')
        seen.add(item['id'])
        color(item['color'], '空间颜色')
    walls = {}
    for kind in ('walls','doors','windows','floors'):
        if not isinstance(s[kind], list) or len(s[kind]) > 100: fail('每类空间对象最多 100 个。')
    for w in s['walls']:
        fields(w, ['id','x1','y1','x2','y2','color','width'], '墙壁'); common(w)
        for k in ('x1','y1','x2','y2'): grid(w[k], k)
        if (w['x1'] == w['x2']) == (w['y1'] == w['y2']): fail('墙壁需是有长度的水平或竖直线段。')
        if w['x2'] < w['x1'] or w['y2'] < w['y1']: fail('墙壁端点按从左到右、从上到下排列。')
        integer(w['width'], '墙壁视觉宽度', 2, 12); walls[w['id']] = w
    openings = {}
    for kind in ('doors','windows'):
        for d in s[kind]:
            fields(d, ['id','wall_id','offset','length','color'] + (['state','actions'] if kind == 'doors' else []), '门窗'); common(d)
            if d['wall_id'] not in walls: fail('门窗必须依附现有墙壁。')
            grid(d['offset'], '门窗起点'); grid(d['length'], '门窗长度', 5)
            w = walls[d['wall_id']]; length = w['x2']-w['x1'] + w['y2']-w['y1']
            if d['offset'] + d['length'] > length: fail('门窗超出墙壁长度。')
            spans = openings.setdefault(d['wall_id'], [])
            if any(d['offset'] < b and d['offset']+d['length'] > a for a,b in spans): fail('同一墙壁上的门窗不能重叠。')
            spans.append((d['offset'], d['offset']+d['length']))
            if kind == 'doors':
                if d['state'] not in ('open','closed','locked'): fail('门状态需为开启、关闭或锁定。')
                if not isinstance(d['actions'], list) or len(set(d['actions'])) != len(d['actions']) or any(a not in DOOR_ACTIONS for a in d['actions']): fail('门行为不受支持。')
    for f in s['floors']:
        fields(f, ['id','x','y','w','h','color','texture','asset_id'], '地面分区'); common(f)
        for k in ('x','y'): grid(f[k], k)
        for k in ('w','h'): grid(f[k], k, 5)
        if f['x']+f['w'] > 100 or f['y']+f['h'] > 100: fail('地面分区超出区域。')
        if f['texture'] not in ('none','planks','tiles'): fail('地面纹理不支持。')
        if f['asset_id'] is not None and f['asset_id'] not in assets: fail('地面素材不存在。')

def grid(v, label, minimum=0):
    integer(v,label,minimum,100)
    if v % 5: fail(f'{label}需要对齐 5% 室内网格。')

def cell(p):
    fields(p, ['x','y'], '网格位置')
    for k in ('x','y'): integer(p[k], '网格'+k, 0, 19)
    return p['x'],p['y']

def object_cell(o): return min(19,int(o['x']//5)),min(19,int(o['y']//5))

def blocked(space, door_states, a, b):
    if not space['enabled']: return False
    ax,ay=(a[0]*5+2.5,a[1]*5+2.5); bx,by=(b[0]*5+2.5,b[1]*5+2.5)
    for w in space['walls']:
        vertical=w['x1']==w['x2']
        hits=(min(ax,bx)<w['x1']<max(ax,bx) and w['y1']<=ay<w['y2']) if vertical else (min(ay,by)<w['y1']<max(ay,by) and w['x1']<=ax<w['x2'])
        if not hits: continue
        offset=(ay-w['y1']) if vertical else (ax-w['x1'])
        if not any(d['wall_id']==w['id'] and d['offset']<=offset<d['offset']+d['length'] and door_states[d['id']]=='open' for d in space['doors']): return True
    return False

def path(space, door_states, start, end):
    start=tuple(start);end=tuple(end); q=deque([start]); previous={start:None}
    while q:
        a=q.popleft()
        if a==end:
            out=[]
            while a is not None: out.append({'x':a[0],'y':a[1]});a=previous[a]
            return out[::-1]
        for b in ((a[0]+1,a[1]),(a[0]-1,a[1]),(a[0],a[1]+1),(a[0],a[1]-1)):
            if 0<=b[0]<20 and 0<=b[1]<20 and b not in previous and not blocked(space,door_states,a,b): previous[b]=a;q.append(b)
    return None

def door_cells(space, d):
    w=next(w for w in space['walls'] if w['id']==d['wall_id']); mid=d['offset']+d['length']/2
    px=w['x1'] if w['x1']==w['x2'] else w['x1']+mid
    py=w['y1']+mid if w['x1']==w['x2'] else w['y1']
    if w['x1']==w['x2']: candidates=((int(px/5)-1,int(py/5)),(int(px/5),int(py/5)))
    else: candidates=((int(px/5),int(py/5)-1),(int(px/5),int(py/5)))
    return [c for c in candidates if 0<=c[0]<20 and 0<=c[1]<20]
