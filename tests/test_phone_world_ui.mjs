/**
 * NyAiro v0.4 · 世界 / 身体界面（components/phone/web）定向检查。
 *
 * 只检查本轮 U 写集：owner 快照 → 视图的字段搬运、命令 payload 的精确 id 与
 * 精确 key、可用性只认 owner、未知结果保留原 key、出发不等于到达、地图结构。
 * 不启动服务、不跑浏览器（真实浏览器串链由父级做），也不碰其它组件。
 *
 * 运行：node --test tests/test_phone_world_ui.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import vm from 'node:vm';

const WEB = new URL('../components/phone/web/', import.meta.url);
const read = (name) => readFileSync(new URL(name, WEB), 'utf8');
const appPath = new URL('app.js', WEB);

const html = read('index.html');
const css = read('style.css');
const src = read('app.js');

const sandbox = vm.createContext({});
vm.runInContext(src, sandbox, {filename: 'components/phone/web/app.js'});
const P = sandbox.NyairoPhone;

class MemoryStorage {
  constructor() {
    this.data = new Map();
  }
  getItem(key) {
    return this.data.has(key) ? this.data.get(key) : null;
  }
  setItem(key, value) {
    this.data.set(key, String(value));
  }
  removeItem(key) {
    this.data.delete(key);
  }
}

const memoryStorage = () => new MemoryStorage();
const HEX64 = 'a3f5'.repeat(16);
const OBJECT_ID = 'item-' + HEX64;
const DEST_ID = 'nearby_street';
const KEY = 'nyairo-world-key-1';
/* 购买那条回归用的目录项与买回来的物件 id（都按服务原字节处理，不 trim 不截断）。 */
const CATALOG_ID = 'catalog-wafer-pack';
const PURCHASED_ID = 'item-' + 'b7c3'.repeat(16);

/** 一份完全按 docs/engineering/v04-world-body-contract.md 写的快照。 */
function snapshot(overrides = {}) {
  const base = {
    kind: 'world_phone_world',
    schema_version: 'world.phone.world.v1',
    ok: true,
    status: 'applied',
    data: {
      world: {
        status: 'available', source: 'world_state', world_id: 'shiomi_city', revision: 41,
        schema_version: 'world.foundation.v0', timezone: 'Asia/Tokyo', scene_id: 'home.bedroom',
        local_time: '2026-10-21T09:12:00+09:00', local_date: '2026-10-21', weekday: 3
      },
      location: {
        status: 'available', source: 'world_state+world_topology', node: 'home/bedroom',
        place_id: 'home', area_id: 'bedroom', name: '家', kind: 'home', home: true
      },
      local_environment: {
        status: 'available', source: 'world_package_scene',
        building: {place_id: 'home', name: '家'},
        area: {area_id: 'bedroom', name: '卧室', kind: 'room'},
        furniture: [{object_id: 'basic_bed', name: '床', kind: 'surface', note: ''}],
        furniture_total: 1,
        adjacent_areas: [{node: 'home', name: '家', kind: 'home'}],
        note: 'furniture 只含当前区域'
      },
      destinations: {
        status: 'available', source: 'world_daily_opportunities', scope: 'NOW_ONLY',
        current_node: 'home/bedroom', walk_seconds_max: 1800,
        places: [
          {
            location_id: 'convenience_store', name: '便利店', kind: 'shop', here: false,
            walk_seconds: 150, reach: 'reachable', route: ['nearby_street', 'convenience_store'],
            availability: {status: 'OPEN', reason_code: null, source: 'world_package_schedule'},
            affordances: [{affordance_id: 'BUY_SNACK', description: '买点吃的'}],
            indoor_areas: [{area_id: 'entrance', name: '入口区', kind: 'entrance'}],
            observed: {status: 'not_observed', reason_code: 'OUT_OF_VIEW'}
          },
          {
            location_id: 'school', name: '学校', kind: 'school', here: false,
            walk_seconds: 900, reach: 'reachable', route: ['nearby_street', 'school'],
            availability: {status: 'CLOSED', reason_code: null, source: 'world_package_schedule'},
            affordances: [], indoor_areas: [], observed: {status: 'not_observed', reason_code: 'OUT_OF_VIEW'}
          }
        ],
        nodes: [
          {
            node: DEST_ID, place_id: DEST_ID, area_id: DEST_ID, label: '附近街道', kind: 'place',
            walk_seconds: 90, reach: 'reachable', direct_move_available: true, route: [DEST_ID]
          },
          {
            node: 'convenience_store', place_id: 'convenience_store', area_id: 'convenience_store',
            label: '便利店', kind: 'shop', walk_seconds: 150, reach: 'reachable',
            direct_move_available: false, route: ['nearby_street', 'convenience_store']
          },
          {
            node: 'home/bedroom', place_id: 'home', area_id: 'bedroom', label: '卧室', kind: 'room',
            walk_seconds: 0, reach: 'reachable', direct_move_available: true, route: []
          }
        ]
      },
      travel: {
        status: 'none', source: 'world_travel_delivery.observe_travel',
        travel_id: null, execution_id: null, origin_location_id: null, destination_location_id: null,
        route_id: null, route_revision: null, route_duration_seconds: null,
        started_at: null, expected_arrival_at: null, arrived_at: null,
        elapsed_seconds: null, remaining_seconds: null, progress_band: null,
        location_unchanged: null, body_load: null, body_load_source: null
      },
      objects: {
        status: 'available', source: 'world_body_substrate', substrate_revision: 7,
        here: [{
          object_id: OBJECT_ID, label: 'virtual_snack_wafer', placement_kind: 'room',
          placement: {kind: 'room', location_id: 'home', area_id: 'bedroom'},
          portable: true, portable_source: 'world_body_binding.object_capability'
        }],
        hands: {left_hand: null, right_hand: null},
        held: [], counts: {here: 1, hands: 0}
      },
      body: {
        status: 'available', body_id: 'chiyo_body', pose: 'standing', orientation: 'south',
        pose_source: 'world_body_substrate',
        recovery: {state: 'inactive', mode: null, trend: null, progress_band: null},
        signals: [{signal_type: 'load_rising', qualitative_band: 'mild'}],
        signals_status: 'available', signals_source: 'world_body_gateway_api.get_body_signals',
        bands_only: true, raw_values_exposed: false, rest_evidence: null, note: '这里没有裸浮点'
      },
      actions: {
        status: 'available', source: 'world_body_gateway_api.get_available_actions',
        local_owner_verbs: ['MOVE', 'PICK_UP', 'PLACE', 'POSE', 'REST'],
        verbs: [
          {verb: 'MOVE', available: true, blocked_reason: null, detail: null, params: {destination_location_id: 'string'}, candidates: []},
          {verb: 'PICK_UP', available: true, blocked_reason: null, detail: null, params: {object_id: 'string', slot_id: 'string'}, candidates: []},
          {verb: 'PLACE', available: true, blocked_reason: null, detail: null, params: {object_id: 'string', target: 'object'}, candidates: []},
          {verb: 'POSE', available: true, blocked_reason: null, detail: null, params: {pose: 'string'}, candidates: []},
          {
            verb: 'REST', available: false, blocked_reason: 'RECOVERY_NOT_NEEDED',
            detail: 'fatigue is below the recovery threshold',
            params: {state: ['start', 'stop'], mode: ['rest', 'sleep_like']},
            start_available: false, stop_available: false, sleep_like_available: false,
            current: {pose: 'standing', recovery_state: 'inactive', travelling: false, rest_surface: false}
          }
        ]
      },
      recent_executions: {
        status: 'available', source: 'world_execution_ledger', limit: 5,
        items: [{
          execution_id: 'phone-exec:abc', action_type: 'MOVE', state: 'committed', result: 'SUCCESS',
          reason_code: 'move_departed', occurred_at: '2026-10-21T09:10:00+09:00',
          world_mutation_refs: ['world.travel=travel-1'], local_owner: true
        }]
      }
    }
  };
  if (!overrides) return base;
  return {
    ...base,
    ...overrides,
    data: overrides.data === null ? null : {...base.data, ...(overrides.data || {})}
  };
}

/* ---------------- 1. 语法与静态约束 ---------------- */

test('app.js 通过 node --check（真实进程，不是猜）', () => {
  execFileSync(process.execPath, ['--check', appPath.pathname.replace(/^\//, '')], {stdio: 'pipe'});
});

test('世界是第八个入口；原七个入口与地址都没动', () => {
  assert.deepEqual([...P.APP_ORDER], ['notes', 'references', 'reading', 'wallet', 'shop', 'inventory', 'body']);
  assert.deepEqual([...P.HOME_ORDER], ['notes', 'references', 'reading', 'wallet', 'shop', 'inventory', 'body', 'world']);
  assert.equal(P.WORLD_APP, 'world');
  assert.equal(P.API_BASE, '/api/phone');
  assert.match(src, /inventory: '\/inventory',\n\s+world: '\/world',/);
  assert.match(html, /<script src="app\.js" defer><\/script>/);
  assert.doesNotMatch(html, /(?:src|href)="https?:\/\//i);
});

test('SVG 地图自己画：只写 textContent 与安全属性，不注入 owner 文本', () => {
  assert.match(src, /document\.createElementNS\(SVG_NS/);
  assert.match(src, /label\.textContent = row\.shortLabel \|\| row\.label;/);
  for (const banned of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function']) {
    assert.equal(src.includes(banned), false, `app.js 不能使用 ${banned}`);
  }
});

/* ---------------- 2. 快照 → 视图 ---------------- */

test('九块都按 owner 字段解析，缺块标 missing 而不是空成功', () => {
  const view = P.worldView(snapshot());
  assert.equal(view.ok, true);
  assert.equal(view.header.worldId, 'shiomi_city');
  assert.equal(view.header.revision, 41);
  assert.equal(view.header.weekdayLabel, '周三');
  assert.equal(view.location.placeId, 'home');
  assert.equal(view.location.areaId, 'bedroom');
  assert.equal(view.location.node, 'home/bedroom');
  assert.equal(view.indoor.available, true);
  assert.equal(view.indoor.areaName, '卧室');
  assert.equal(view.indoor.furniture[0].name, '床');
  assert.equal(view.destinations.nodes.length, 3);
  assert.equal(view.destinations.nodes[2].here, true, '当前节点要按 current_node 标出来');
  assert.equal(view.objects.here[0].portable, true);
  assert.equal(view.actions.verbs.MOVE.available, true);
  assert.equal(view.recent.items[0].actionLabel, '出发');
  assert.equal(view.recent.items[0].localOwner, true);

  const missing = P.worldView({ok: true, status: 'applied', data: {world: {status: 'available', world_id: 'x'}}});
  assert.equal(missing.blocks.location, 'missing');
  assert.equal(missing.blocks.travel, 'missing');
  assert.equal(missing.blocks.body, 'missing');
  assert.equal(missing.location.status, 'missing');
  assert.equal(missing.travel.status, 'missing');
  assert.equal(missing.body.known, false);

  const noData = P.worldView({ok: true, status: 'applied'});
  assert.equal(noData.hasData, false);
  assert.equal(noData.header.status, 'missing');
});

test('body 只搬姿势/档位/恢复/来源：浮点与未列字段进不来', () => {
  const doc = snapshot();
  doc.data.body = {
    ...doc.data.body,
    fatigue: 0.42, activation: 0.66, comfort: 0.5, sensory_load: 0.3, tension: 0.9, warmth: 0.4,
    recovery: {state: 'active', mode: 'rest', trend: 'improving', progress_band: 'mild', fatigue_rate: -0.0021}
  };
  const view = P.worldView(doc);
  const dump = JSON.stringify(view.body);
  for (const key of ['fatigue', 'activation', 'comfort', 'sensory_load', 'tension', 'warmth', '0.42', '0.0021']) {
    assert.equal(dump.includes(key), false, `body 视图不能带 ${key}`);
  }
  assert.equal(view.body.recovery.stateLabel, '正在恢复');
  assert.equal(view.body.recovery.modeLabel, '休息');
  assert.equal(view.body.recovery.trend, 'improving');
  assert.equal(view.body.bandsOnly, true);
  assert.equal(view.body.rawExposed, false);
  assert.equal(view.body.poseLabel, '站着');
  assert.equal(view.body.poseSource, 'world_body_substrate');

  const partial = P.worldView(snapshot({data: {body: {status: 'partial', reason_code: 'WORLD_BODY_SERVICE_UNAVAILABLE', pose: 'lying'}}}));
  assert.equal(partial.body.known, true);
  assert.equal(partial.body.signalsMissing, true);
  assert.match(partial.body.reason, /WORLD_BODY_SERVICE_UNAVAILABLE/);
});

test('身体信号缺失/未知如实说，不补一条“感觉正常”', () => {
  const noSignals = P.worldView(snapshot({data: {body: {status: 'available', pose: 'seated', signals: null, signals_status: 'unknown'}}}));
  assert.equal(noSignals.body.signalsMissing, true);
  assert.deepEqual([...P.worldSignalLines(noSignals.body)], ['服务这次没有提供身体信号。']);

  const empty = P.worldView(snapshot({data: {body: {status: 'available', pose: 'seated', signals: []}}}));
  assert.deepEqual([...P.worldSignalLines(empty.body)], ['此刻身体没有报告信号。']);

  const unreadable = P.worldView(snapshot({data: {body: {status: 'unavailable', reason_code: 'SUBSTRATE_STATE_ERROR'}}}));
  assert.equal(unreadable.body.known, false);
  assert.match(P.worldSignalLines(unreadable.body)[0], /读不到/);

  const unknownSignal = P.worldView(snapshot({data: {body: {status: 'available', pose: 'seated', signals: [{signal_type: 'future_signal', qualitative_band: 'strong'}]}}}));
  assert.match(P.worldSignalLines(unknownSignal.body)[0], /future_signal/);
  assert.match(P.worldSignalLines(unknownSignal.body)[0], /强/);
});

/* ---------------- 3. 命令 payload：精确 key / 精确 id ---------------- */

test('MOVE payload 只有 destination_location_id；uid/authority/elapsed 一个字都进不去', () => {
  const built = P.buildWorldActionPayload({
    action: 'MOVE',
    params: {destination_location_id: DEST_ID, uid: 0, authority: 'main_self', elapsed_seconds: 12, home: '/tmp/x'},
    operation_key: KEY
  });
  assert.equal(built.ok, true);
  assert.deepEqual(Object.keys(built.payload).sort(), ['action', 'operation_key', 'params']);
  assert.deepEqual(Object.keys(built.payload.params), ['destination_location_id']);
  assert.equal(built.payload.params.destination_location_id, DEST_ID);
  assert.equal(built.payload.action, 'MOVE');
  assert.equal(built.payload.operation_key, KEY);
  assert.equal(JSON.stringify(built.payload).includes('elapsed_seconds'), false);
  assert.equal(JSON.stringify(built.payload).includes('authority'), false);
});

test('协议 id 原字节进 payload：69 字的 object_id 不截断、不脱敏', () => {
  assert.equal(OBJECT_ID.length, 69);
  const built = P.buildWorldActionPayload({
    action: 'PICK_UP',
    params: {object_id: OBJECT_ID, slot_id: 'left_hand'},
    operation_key: KEY
  });
  assert.equal(built.ok, true);
  assert.deepEqual(Object.keys(built.payload.params).sort(), ['object_id', 'slot_id']);
  assert.equal(built.payload.params.object_id, OBJECT_ID);
  assert.equal(built.payload.params.slot_id, 'left_hand');

  const tooLong = P.buildWorldActionPayload({
    action: 'PICK_UP',
    params: {object_id: 'item-' + 'b'.repeat(300), slot_id: 'left_hand'},
    operation_key: KEY
  });
  assert.equal(tooLong.ok, false, '超长 id 不能截断后提交');
  assert.match(tooLong.message, /没有发出请求/);
});

test('PICK_UP/PLACE/POSE/REST 的合法性与目标形状都由契约决定', () => {
  const badSlot = P.buildWorldActionPayload({action: 'PICK_UP', params: {object_id: OBJECT_ID, slot_id: 'third_hand'}, operation_key: KEY});
  assert.equal(badSlot.ok, false);
  assert.equal(badSlot.field, 'slot_id');

  const place = P.buildWorldActionPayload({
    action: 'PLACE',
    params: {object_id: OBJECT_ID, target: {kind: 'room', location_id: 'home', area_id: 'bedroom', extra: 'x'}},
    operation_key: KEY
  });
  assert.equal(place.ok, true);
  assert.deepEqual(Object.keys(place.payload.params.target).sort(), ['area_id', 'kind', 'location_id']);
  assert.deepEqual(JSON.parse(JSON.stringify(place.payload.params.target)), {kind: 'room', location_id: 'home', area_id: 'bedroom'});

  const handTarget = P.buildWorldActionPayload({
    action: 'PLACE',
    params: {object_id: OBJECT_ID, target: {kind: 'hand', slot_id: 'left_hand'}},
    operation_key: KEY
  });
  assert.equal(handTarget.ok, false, '界面只提交“放进当前房间”');

  const noArea = P.buildWorldActionPayload({
    action: 'PLACE',
    params: {object_id: OBJECT_ID, target: {kind: 'room', location_id: 'home', area_id: ''}},
    operation_key: KEY
  });
  assert.equal(noArea.ok, false);

  const badPose = P.buildWorldActionPayload({action: 'POSE', params: {pose: 'floating'}, operation_key: KEY});
  assert.equal(badPose.ok, false);
  const badOrientation = P.buildWorldActionPayload({action: 'POSE', params: {pose: 'seated', orientation: 'sideways'}, operation_key: KEY});
  assert.equal(badOrientation.ok, false);
  const pose = P.buildWorldActionPayload({action: 'POSE', params: {pose: 'seated', orientation: 'east'}, operation_key: KEY});
  assert.deepEqual(Object.keys(pose.payload.params).sort(), ['orientation', 'pose']);

  const restStop = P.buildWorldActionPayload({action: 'REST', params: {state: 'stop', mode: 'sleep_like'}, operation_key: KEY});
  assert.deepEqual(Object.keys(restStop.payload.params), ['state'], 'stop 不带 mode');
  const restStart = P.buildWorldActionPayload({action: 'REST', params: {state: 'start'}, operation_key: KEY});
  assert.deepEqual(JSON.parse(JSON.stringify(restStart.payload.params)), {state: 'start', mode: 'rest'});
  const restSleep = P.buildWorldActionPayload({action: 'REST', params: {state: 'start', mode: 'sleep_like'}, operation_key: KEY});
  assert.equal(restSleep.payload.params.mode, 'sleep_like');
  const badState = P.buildWorldActionPayload({action: 'REST', params: {state: 'pause'}, operation_key: KEY});
  assert.equal(badState.ok, false);

  for (const bad of [{action: 'TELEPORT', params: {}}, {action: 'move', params: {destination_location_id: DEST_ID}}, {action: 'MOVE', params: {destination_location_id: DEST_ID}, operation_key: '  '}]) {
    const built = P.buildWorldActionPayload(bad);
    assert.equal(built.ok, false, `非法输入 ${JSON.stringify(bad)} 不能构造出请求`);
  }
});

/* ---------------- 4. operation_key 绑定 action + 每个确切参数 ---------------- */

test('operation_key 的 scope 绑 action 与每个确切参数（含嵌套 target）', () => {
  const a = P.worldActionScope('MOVE', {destination_location_id: 'nearby_street'});
  const b = P.worldActionScope('MOVE', {destination_location_id: 'convenience_store'});
  const c = P.worldActionScope('PICK_UP', {destination_location_id: 'nearby_street'});
  assert.notEqual(a, b, '目标不同不能共用一个键');
  assert.notEqual(a, c, '动作不同不能共用一个键');
  assert.equal(a, P.worldActionScope('MOVE', {destination_location_id: 'nearby_street'}));
  assert.match(a, /^world:MOVE:\{"destination_location_id":"nearby_street"\}$/);

  const t1 = P.worldActionScope('PLACE', {object_id: OBJECT_ID, target: {kind: 'room', location_id: 'home', area_id: 'bedroom'}});
  const t2 = P.worldActionScope('PLACE', {object_id: OBJECT_ID, target: {kind: 'room', location_id: 'home', area_id: 'kitchen'}});
  assert.notEqual(t1, t2, '嵌套 target 也必须进 scope');
  assert.match(t1, /"area_id":"bedroom"/);
});

test('未知结果保留原 key：不会换 key 重发', () => {
  const storage = memoryStorage();
  let counter = 0;
  const ops = P.createOpStore(storage, () => `key-${++counter}`);
  const scope = P.worldActionScope('MOVE', {destination_location_id: DEST_ID});
  const first = ops.ensure(scope, {path: '/world/actions'});
  ops.markUnknown(scope, {path: '/world/actions'});
  assert.equal(ops.listUnresolved().length, 1);
  assert.equal(ops.listUnresolved()[0].key, first.key);
  assert.equal(ops.listUnresolved()[0].path, '/world/actions');
  assert.equal(ops.ensure(scope).key, first.key, '同一次意图必须复用原 key');

  const restored = P.createOpStore(storage, () => 'never-used');
  assert.equal(restored.listUnresolved()[0].key, first.key);
  assert.equal(restored.ensure(scope).key, first.key);

  /* 接线层：提交前只有 ops.ensure 造键；未知走后 markUnknown；查原 key 走 /world/operations。 */
  assert.match(src, /const entry = ops\.ensure\(scope, \{path: '\/world\/actions'\}\);/);
  assert.match(src, /ops\.markUnknown\(scope, \{path: entry && entry\.path\}\)/);
  assert.match(src, /const worldScope = String\(scope \|\| ''\)\.indexOf\('world:'\) === 0;/);
  assert.match(src, /'\/world\/operations\/' \+ encodeURIComponent\(key\)/);
  assert.doesNotMatch(src, /world\/actions[\s\S]{0,200}makeOperationKey\(\)/);
});

/* ---------------- 5. 只认 owner 的可用性 ---------------- */

test('禁用来自 owner：available=false 时按钮禁用并给人话原因', () => {
  const view = P.worldView(snapshot());
  assert.equal(P.worldMoveDisabledReason(view, view.destinations.nodes[0]), '', '可达且一步能到就该能出发');
  assert.equal(P.worldMoveDisabledReason(view, view.destinations.nodes[1]), '要分几步才能到，一次命令走不完');
  assert.equal(P.worldMoveDisabledReason(view, view.destinations.nodes[2]), '已经在这里了');

  const unreachable = P.worldView(snapshot({data: {destinations: {...snapshot().data.destinations, nodes: [{node: 'park', place_id: 'park', area_id: 'park', label: '公园', kind: 'place', walk_seconds: 600, reach: 'unreachable', direct_move_available: true, route: []}]}}}));
  assert.equal(P.worldMoveDisabledReason(unreachable, unreachable.destinations.nodes[0]), '不可达');

  const notAllowed = snapshot();
  notAllowed.data.actions = {
    ...notAllowed.data.actions,
    verbs: notAllowed.data.actions.verbs.map((verb) => (verb.verb === 'MOVE' ? {...verb, available: false, blocked_reason: 'TRAVELLING'} : verb))
  };
  const guarded = P.worldView(notAllowed);
  assert.equal(P.worldMoveDisabledReason(guarded, guarded.destinations.nodes[0]), '正在路上，先等到达');

  const rest = P.worldRestOption(view);
  assert.equal(rest.reported, true);
  assert.equal(rest.startEnabled, false);
  assert.equal(rest.stopEnabled, false);
  assert.equal(rest.sleepEnabled, false);
  assert.equal(rest.blockedText, '现在不需要恢复', '低负荷时服务会直接说不需要恢复，界面照实显示');

  const restOpen = snapshot();
  restOpen.data.actions = {
    ...restOpen.data.actions,
    verbs: restOpen.data.actions.verbs.map((verb) => (verb.verb === 'REST'
      ? {...verb, available: true, blocked_reason: null, start_available: true, stop_available: false, sleep_like_available: false}
      : verb))
  };
  const open = P.worldRestOption(P.worldView(restOpen));
  assert.equal(open.startEnabled, true);
  assert.equal(open.stopEnabled, false, '没有开着的恢复就不能停');
  assert.equal(open.sleepEnabled, false);

  const unReported = snapshot();
  unReported.data.actions = {status: 'unavailable', reason_code: 'WORLD_BODY_SERVICE_UNAVAILABLE', verbs: [], local_owner_verbs: ['MOVE', 'REST']};
  const blind = P.worldView(unReported);
  const blindRest = P.worldRestOption(blind);
  assert.equal(blindRest.reported, false);
  assert.equal(blindRest.startEnabled, false, '服务没报告就不能当成能用');
  assert.match(blindRest.blockedText, /身体服务|世界动作|没有报告/);
  assert.match(P.worldMoveDisabledReason(blind, blind.destinations.nodes[0]), /身体服务|世界动作|没有报告/);
});

test('拿起/放下按真实摆放与手容量：portable 未声明不算能拿', () => {
  const view = P.worldView(snapshot());
  const object = view.objects.here[0];
  const pickup = P.worldPickupOptions(view, object);
  assert.equal(pickup.ok, true);
  assert.deepEqual([...pickup.slots.map((slot) => slot.slot)], ['left_hand', 'right_hand']);

  const oneHand = snapshot();
  oneHand.data.objects = {...oneHand.data.objects, hands: {left_hand: OBJECT_ID, right_hand: null}};
  const one = P.worldPickupOptions(P.worldView(oneHand), P.worldView(oneHand).objects.here[0]);
  assert.deepEqual([...one.slots.map((slot) => slot.slot)], ['right_hand']);

  const unknownPortable = snapshot();
  unknownPortable.data.objects = {...unknownPortable.data.objects, here: [{...unknownPortable.data.objects.here[0], portable: null}]};
  const unknownView = P.worldView(unknownPortable);
  assert.equal(P.worldPickupOptions(unknownView, unknownView.objects.here[0]).ok, false);
  assert.match(P.worldPickupOptions(unknownView, unknownView.objects.here[0]).message, /没有声明/);

  const place = P.worldPlaceOption(view, {id: OBJECT_ID, idOk: true});
  assert.equal(place.ok, true);
  assert.deepEqual(JSON.parse(JSON.stringify(place.target)), {kind: 'room', location_id: 'home', area_id: 'bedroom'});

  const noRoom = P.worldView(snapshot({data: {location: {status: 'available', node: 'nearby_street', place_id: 'nearby_street', area_id: null, name: '附近街道', kind: 'place', home: false}}}));
  assert.equal(P.worldPlaceOption(noRoom, {id: OBJECT_ID, idOk: true}).ok, false, '在街区上就不知道放回哪个房间');
});

/* ---------------- 6. 出发 ≠ 到达 ---------------- */

test('在途只显示在路上与剩余时间；结果卡不宣称已到达', () => {
  const doc = snapshot();
  doc.data.travel = {
    status: 'in_progress', source: 'world_travel_delivery.observe_travel',
    travel_id: 'travel-1', execution_id: 'phone-exec:abc',
    origin_location_id: 'home', destination_location_id: DEST_ID,
    route_id: 'home->nearby_street', route_revision: 'shiomi-walk-v1', route_duration_seconds: 90,
    started_at: '2026-10-21T09:12:00+09:00', expected_arrival_at: '2026-10-21T09:13:30+09:00', arrived_at: null,
    elapsed_seconds: 31, remaining_seconds: 59, progress_band: 'underway',
    location_unchanged: true, body_load: 'pending', body_load_source: 'body_consequence_ledger'
  };
  const view = P.worldView(doc);
  assert.equal(view.travel.active, true);
  assert.equal(view.travel.bandLabel, '在路上');
  assert.equal(view.travel.remainingSeconds, 59);
  assert.equal(P.formatWalkText(view.travel.remainingSeconds), '59 秒');
  assert.equal(view.travel.bodyLoadText, '负荷待结算');
  assert.equal(view.travel.locationUnchanged, true);
  assert.equal(P.worldMoveDisabledReason(view, view.destinations.nodes[0]), '正在路上，先等到达');

  const lines = P.worldOutcomeLines('MOVE', {
    ok: true,
    status: 'applied',
    data: {
      execution_id: 'phone-exec:abc',
      travel: doc.data.travel,
      body: {applied_to_body: false, movement_settlement: 'pending'},
      world: {revision: 41, location: {node: 'home/bedroom', place_id: 'home', area_id: 'bedroom'}, location_unchanged: true}
    }
  });
  const text = lines.join(' / ');
  assert.match(text, /已出发去/);
  assert.match(text, /这是出发，不是到达/);
  assert.match(text, /负荷还没结算/);
  assert.equal(text.includes('已到达'), false);

  /* 位置块仍是起点：界面不许把出发写成本地位置变化。 */
  assert.equal(view.location.node, 'home/bedroom');

  const arrived = P.worldOutcomeLines('MOVE', {
    ok: true, status: 'applied',
    data: {travel: {...doc.data.travel, status: 'arrived', arrived_at: '2026-10-21T09:13:30+09:00', remaining_seconds: 0}}
  });
  assert.match(arrived.join(' / '), /世界报告已经到达/);
});

test('姿势成功不等于恢复成功；REST 成功后仍以 owner 的恢复状态为准', () => {
  const pose = P.worldOutcomeLines('POSE', {
    ok: true, status: 'applied',
    data: {world: {revision: 42, location: {node: 'home/bedroom'}, location_unchanged: true}, body: {applied_to_body: true, recovery: {state: 'inactive'}}}
  });
  assert.equal(pose.join(' / ').includes('恢复'), false, '换姿势不能顺手宣布恢复');

  const restNoChange = P.worldOutcomeLines('REST', {
    ok: true, status: 'applied',
    data: {body: {applied_to_body: false, recovery: {state: 'inactive', mode: null}}}
  });
  assert.match(restNoChange.join(' / '), /没有变化|advance_produced_no_change/);

  const restOpen = P.worldOutcomeLines('REST', {
    ok: true, status: 'applied',
    data: {body: {applied_to_body: true, recovery: {state: 'active', mode: 'rest'}}, world_mutation_refs: ['recovery:start:rest']}
  });
  assert.match(restOpen.join(' / '), /恢复已经开着/);
  assert.match(restOpen.join(' / '), /recovery:start:rest/);
});

/* ---------------- 7. 地图与显示 ---------------- */

test('地图计划只用可达节点：跳过当前节点、标注分几步、短名不溢出', () => {
  const view = P.worldView(snapshot());
  const plan = P.worldMapPlan(view, 8);
  assert.equal(plan.rows.length, 2, '家 是当前节点，不进目的地行');
  assert.equal(plan.rows[0].node, DEST_ID);
  assert.equal(plan.rows[0].direct, true);
  assert.equal(plan.rows[1].direct, false);
  assert.equal(plan.rows[1].walkText, '3 分钟');
  for (const row of plan.rows) {
    assert.ok((row.shortLabel || row.label).length <= 12, 'SVG 一行放不下长名');
  }
  assert.equal(plan.origin.label, '你在这里');
  assert.ok(plan.height > plan.origin.y);
  const capped = P.worldMapPlan(view, 1);
  assert.equal(capped.rows.length, 1);
});

test('距离/营业/可达性只显示 owner 给的事实', () => {
  const view = P.worldView(snapshot());
  const [store, school] = view.destinations.places;
  assert.equal(store.availabilityLabel, '营业中');
  assert.equal(school.availabilityLabel, '现在关着');
  assert.equal(store.walkText, '3 分钟');
  assert.equal(school.walkText, '15 分钟');
  assert.equal(store.affordances[0].description, '买点吃的');
  assert.equal(view.destinations.nodes[0].reachLabel, '可达');

  const noIndoor = P.worldView(snapshot({data: {local_environment: {status: 'unavailable', reason_code: 'WORLD_HAS_NO_INDOOR_DECLARATION'}}}));
  assert.equal(noIndoor.indoor.available, false);
  assert.equal(P.worldReasonText(noIndoor.indoor.reason), '这个世界包没有声明室内地图');

  const travelDisabled = P.worldView(snapshot({data: {travel: {status: 'disabled'}}}));
  assert.equal(travelDisabled.travel.active, false);
  assert.equal(travelDisabled.travel.statusLabel, '计时旅程关着（移动会瞬时到达）');
});

/* ---------------- 8. 未知与对账 ---------------- */

test('世界操作对账只解释 owner 字段，未知就是未知', () => {
  const lines = P.worldOperationLines({
    ok: true,
    status: 'unknown',
    data: {
      operation: 'world', execution_id: 'phone-exec:abc', action_type: 'MOVE',
      ledger_state: 'prepared', ledger_result: null, reason_code: null,
      world_mutation_refs: ['world.travel=travel-1'], reconciliation_required: true,
      travel: {status: 'in_progress', remaining_seconds: 59}
    }
  }).join(' / ');
  assert.match(lines, /世界动作/);
  assert.match(lines, /出发/);
  assert.match(lines, /prepared/);
  assert.match(lines, /人工对账/);
  assert.match(lines, /出发，不是到达/);
  assert.equal(lines.includes('已生效'), false);

  assert.deepEqual([...P.worldOperationLines({ok: true, status: 'not_found'})], []);
  assert.deepEqual([...P.worldOperationLines(null)], []);
});

test('reason_code 有中文人话，未知码原样保留', () => {
  assert.equal(P.worldReasonText('RECOVERY_NOT_NEEDED'), '现在不需要恢复');
  assert.equal(P.worldReasonText('REST_REQUIRES_RESTING_POSE'), '先坐下或躺下，再休息');
  assert.equal(P.worldReasonText('TRAVELLING'), '正在路上，先等到达');
  assert.equal(P.worldReasonText('nothing_held'), '手里没有东西');
  assert.match(P.worldReasonText('BRAND_NEW_CODE'), /BRAND_NEW_CODE/);
});

/* ---------------- 9. 导出面 ---------------- */

test('无 DOM 也能加载，世界 API 都在导出面上', () => {
  assert.equal(typeof sandbox.document, 'undefined');
  const expected = [
    'worldView', 'worldHeaderView', 'worldLocationView', 'worldIndoorView', 'worldTravelView',
    'worldDestinationsView', 'worldObjectsView', 'worldActionsView', 'worldRecentView', 'bodyView',
    'worldSignalLines', 'worldReasonText', 'worldVerbAvailability', 'worldMoveDisabledReason',
    'worldPickupOptions', 'worldPlaceOption', 'worldRestOption', 'buildPlacementTarget',
    'buildWorldActionPayload', 'worldActionScope', 'worldMapPlan', 'worldTechEntries', 'formatWalkText'
  ];
  for (const name of expected) {
    assert.equal(typeof P[name], 'function', `缺少导出 ${name}`);
  }
  assert.deepEqual([...P.WORLD_VERBS], ['MOVE', 'PICK_UP', 'PLACE', 'POSE', 'REST']);
  assert.deepEqual([...P.POSE_VALUES], ['standing', 'seated', 'lying', 'transitioning']);
  assert.deepEqual([...P.HAND_SLOT_VALUES], ['left_hand', 'right_hand']);
});

test('界面没有把身体内部浮点当字段读，也没有假状态文案', () => {
  for (const banned of ['fatigue', 'activation', 'sensory_load', 'warmth']) {
    assert.equal(new RegExp(`\\b${banned}\\b`).test(src), false, `app.js 不能读 ${banned}`);
  }
  assert.equal(/\bMOCK\b|mockData|fakeData/.test(src), false);
  /* 技术详情是折叠的，不是主视图。 */
  assert.match(src, /h\('details', \{class: 'tech-details'\}/);
  assert.match(css, /\.tech-details \{/);
  assert.match(css, /\.world-map-svg \{ width: 100%; height: auto; display: block; \}/);
  assert.match(css, /overflow-wrap: anywhere/);
});

/* ---------------- 10. 接线冒烟：stub DOM，不是浏览器验收 ---------------- */
/*
 * 这一段用一个很小的 DOM 桩把真实的 app.js 跑起来：主屏 → 世界 → 出发 →
 * 身体，检查按钮真的接上了 renderWorld/confirmMove/submitWorldAction。
 * 它证明的是“接线与字段搬运”，**不是**浏览器渲染/布局/键盘验收；
 * 真实浏览器的串链由父级在同一实例里做。
 */

function makeStubDom(seed) {
  class StubNode {
    constructor(tag) {
      this.tagName = String(tag).toUpperCase();
      this.children = [];
      this.attributes = {};
      this.listeners = {};
      this.textContent = '';
      this.className = '';
      this.dataset = {};
      this.disabled = false;
      this.value = '';
    }
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    }
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
    }
    append() {
      for (const node of arguments) if (node) this.children.push(node);
    }
    appendChild(node) {
      this.children.push(node);
      return node;
    }
    prepend(node) {
      if (node) this.children.unshift(node);
    }
    replaceChildren() {
      this.children = Array.prototype.slice.call(arguments).filter(Boolean);
    }
    addEventListener(type, fn) {
      if (!this.listeners[type]) this.listeners[type] = [];
      this.listeners[type].push(fn);
    }
    removeEventListener() {}
    focus() {}
    querySelector(selector) {
      const wanted = String(selector).replace('.', '');
      return this.find((node) => String(node.className || '').includes(wanted));
    }
    find(predicate) {
      for (const child of this.children) {
        if (!child || typeof child !== 'object') continue;
        if (predicate(child)) return child;
        const hit = child.find ? child.find(predicate) : null;
        if (hit) return hit;
      }
      return null;
    }
    findAll(predicate, out = []) {
      for (const child of this.children) {
        if (!child || typeof child !== 'object') continue;
        if (predicate(child)) out.push(child);
        if (child.findAll) child.findAll(predicate, out);
      }
      return out;
    }
    get allText() {
      const mine = String(this.textContent || '');
      const kids = this.children.map((child) => (child && child.allText !== undefined ? child.allText : String(child || '')));
      return [mine, ...kids].join(' ');
    }
  }

  const byId = new Map();
  const document = {
    readyState: 'complete',
    hidden: false,
    getElementById(id) {
      if (!byId.has(id)) byId.set(id, new StubNode('div'));
      return byId.get(id);
    },
    createElement(tag) {
      return new StubNode(tag);
    },
    createElementNS(_ns, tag) {
      return new StubNode(tag);
    },
    createTextNode(text) {
      const node = new StubNode('#text');
      node.textContent = String(text);
      return node;
    },
    addEventListener() {}
  };

  const requests = [];
  const responses = {
    '/state': {
      ok: true,
      read_only: false,
      csrf_token: 'tok-world',
      instance: {name: '妹妹', subject: 'chiyo', mode: 'demo'},
      apps: {
        notes: {status: 'available', data: {}},
        references: {status: 'available', data: {}},
        reading: {status: 'available', data: {}},
        wallet: {status: 'available', data: {}},
        shop: {status: 'available', data: {}},
        inventory: {status: 'available', data: {}},
        body: {status: 'available', data: {pose: 'standing', recovery: {state: 'inactive'}, signals: []}},
        world: {status: 'available', data: {}}
      }
    },
    '/world': seed.snapshot,
    '/world/actions': {
      ok: true,
      status: 'applied',
      data: {
        execution_id: 'phone-exec:1',
        action: 'MOVE',
        operation_key: 'stub-key',
        travel: {
          status: 'in_progress', origin_location_id: 'home', destination_location_id: DEST_ID,
          route_duration_seconds: 90, elapsed_seconds: 1, remaining_seconds: 89,
          progress_band: 'just_started', location_unchanged: true, body_load: 'pending'
        },
        body: {applied_to_body: false, movement_settlement: 'pending'},
        world: {revision: 41, location_unchanged: true}
      }
    },
    /* 商店 / 钱包 / 购买回执：只给购买那条跨 app 新鲜性回归用，其它用例不读它们。 */
    '/shop': {
      ok: true,
      status: 'applied',
      data: {
        currency: 'JPY',
        purchase_available: true,
        catalog: [{
          catalog_item_id: CATALOG_ID, name: '薄饼', description: '一包薄饼',
          price: 240, usable_kind: 'snack'
        }]
      }
    },
    '/wallet': {ok: true, status: 'applied', data: {currency: 'JPY', balance: 9760}},
    '/purchases': {
      ok: true,
      status: 'applied',
      data: {
        execution_id: 'shop-exec:1', operation_key: 'stub-key',
        catalog_item_id: CATALOG_ID, item_instance_id: PURCHASED_ID, price: 240
      }
    }
  };

  let seq = 0;
  const sandbox = {
    document: document,
    console: console,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    setInterval: () => 0,
    clearInterval: () => {},
    AbortController: AbortController,
    sessionStorage: new MemoryStorage(),
    crypto: {randomUUID: () => `stub-uuid-${++seq}`},
    fetch: async (url, init) => {
      const path = String(url).replace('/api/phone', '');
      requests.push({path: path, method: (init && init.method) || 'GET', init: init});
      const raw = responses[path];
      const doc = typeof raw === 'function' ? raw() : raw;
      if (!doc) return {ok: false, status: 404, text: async () => ''};
      return {ok: true, status: 200, text: async () => JSON.stringify(doc)};
    }
  };
  return {sandbox: sandbox, document: document, requests: requests, StubNode: StubNode, responses: responses};
}

const settle = async () => {
  for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

const click = (node) => {
  const handlers = (node && node.listeners && node.listeners.click) || [];
  assert.ok(handlers.length > 0, '这个按钮没有接上点击');
  for (const handler of handlers) handler({target: node});
};

const findButton = (rootNode, text) => rootNode.findAll((node) => node.tagName === 'BUTTON' && node.allText.includes(text))[0];

test('（stub DOM 冒烟）主屏点世界能渲染街区，出发走 POST 且回执不宣称到达', async () => {
  const dom = makeStubDom({snapshot: snapshot()});
  const context = vm.createContext(dom.sandbox);
  vm.runInContext(src, context, {filename: 'components/phone/web/app.js'});
  await settle();

  const body = dom.document.getElementById('screen-body');
  const tiles = body.findAll((node) => node.tagName === 'BUTTON' && String(node.className).includes('tile'));
  assert.equal(tiles.length, 8, '主屏要有八个入口（原七个 + 世界）');
  const worldTile = tiles.find((tile) => tile.allText.includes('世界'));
  assert.ok(worldTile, '主屏缺少世界入口');

  click(worldTile);
  await settle();

  const worldText = dom.document.getElementById('screen-body').allText;
  assert.equal(worldText.includes('界面错误'), false, `世界屏渲染报错：${worldText.slice(0, 200)}`);
  assert.match(worldText, /家/);
  assert.match(worldText, /街区/);
  assert.match(worldText, /附近街道/);
  assert.match(worldText, /卧室/);
  assert.match(worldText, /手边/);
  assert.match(worldText, /技术详情/);

  const svg = dom.document.getElementById('screen-body').find((node) => node.tagName === 'SVG');
  assert.ok(svg, '地图要真的画成 SVG');
  assert.ok(svg.findAll((node) => node.tagName === 'CIRCLE').length >= 3, 'SVG 里要有当前位置与目的地圆点');
  assert.equal(svg.getAttribute('aria-label').includes('附近街道'), true);

  const goButton = findButton(dom.document.getElementById('screen-body'), '出发');
  assert.ok(goButton, '可达目的地要有出发按钮');
  assert.equal(goButton.disabled, false, '一步能到的地方出发按钮不该禁用');
  click(goButton);
  await settle();

  const sheetHost = dom.document.getElementById('sheet-host');
  const sheetText = sheetHost.allText;
  assert.match(sheetText, /确认出发/);
  assert.match(sheetText, /不会提前显示已到达/);

  const confirm = sheetHost.find((node) => node.tagName === 'BUTTON' && String(node.className).includes('btn-primary'));
  assert.ok(confirm, '确认弹层要有主按钮');
  click(confirm);
  await settle();

  const posts = dom.requests.filter((entry) => entry.method === 'POST');
  assert.equal(posts.length, 1, '一次意图只发一条 POST');
  assert.equal(posts[0].path, '/world/actions');
  assert.equal(posts[0].init.headers['X-CSRF-Token'], 'tok-world');
  const sent = JSON.parse(posts[0].init.body);
  assert.deepEqual(Object.keys(sent).sort(), ['action', 'operation_key', 'params']);
  assert.equal(sent.action, 'MOVE');
  assert.deepEqual(Object.keys(sent.params), ['destination_location_id']);
  assert.equal(sent.params.destination_location_id, DEST_ID);
  assert.equal(sent.operation_key, 'stub-uuid-1');

  const after = dom.document.getElementById('screen-body').allText;
  assert.match(after, /这是出发，不是到达/);
  assert.equal(after.includes('已到达'), false);
  assert.ok(dom.requests.some((entry) => entry.method === 'GET' && entry.path === '/world'), '提交后要重读快照');
});

test('（stub DOM 冒烟）身体屏用 world 的 body 块，不显示任何数值', async () => {
  const dom = makeStubDom({snapshot: snapshot()});
  const context = vm.createContext(dom.sandbox);
  vm.runInContext(src, context, {filename: 'components/phone/web/app.js'});
  await settle();

  const bodyTile = dom.document.getElementById('screen-body')
    .findAll((node) => node.tagName === 'BUTTON' && String(node.className).includes('tile'))
    .find((tile) => tile.allText.includes('身体'));
  assert.ok(bodyTile, '主屏缺少身体入口');
  click(bodyTile);
  await settle();

  const text = dom.document.getElementById('screen-body').allText;
  assert.equal(text.includes('界面错误'), false, `身体屏渲染报错：${text.slice(0, 200)}`);
  assert.match(text, /姿势/);
  assert.match(text, /站着/);
  assert.match(text, /恢复/);
  assert.match(text, /负荷在升高/);
  assert.match(text, /只有档位/);
  assert.equal(/0\.\d+/.test(text), false, '身体屏不能出现裸浮点');
});

/* 父级真实浏览器那条失败的复刻：世界先读到「只有床、没有可拿的东西」的旧快照，
   再去商店买一件点心，回到世界时不能再显示购买前的事实。
   这不是浏览器验收：stub DOM 只证明缓存失效与重读接线，不证明布局与真实点击。 */
test('（stub DOM 冒烟）世界有旧缓存时购买，回世界必须重读 /world 并出现新物件与拿起按钮', async () => {
  const emptyObjects = {
    status: 'available', source: 'world_body_substrate', substrate_revision: 7,
    here: [], hands: {left_hand: null, right_hand: null}, held: [], counts: {here: 0, hands: 0}
  };
  const purchasedObjects = {
    status: 'available', source: 'world_body_substrate', substrate_revision: 8,
    here: [{
      object_id: PURCHASED_ID, label: 'virtual_shortcake', placement_kind: 'room',
      placement: {kind: 'room', location_id: 'home', area_id: 'bedroom'},
      portable: true, portable_source: 'world_body_binding.object_capability'
    }],
    hands: {left_hand: null, right_hand: null}, held: [], counts: {here: 1, hands: 0}
  };

  const dom = makeStubDom({snapshot: snapshot({data: {objects: emptyObjects}})});
  const context = vm.createContext(dom.sandbox);
  vm.runInContext(src, context, {filename: 'components/phone/web/app.js'});
  await settle();

  const screen = () => dom.document.getElementById('screen-body');
  const tiles = () => screen().findAll((node) => node.tagName === 'BUTTON' && String(node.className).includes('tile'));
  const tile = (label) => tiles().find((node) => node.allText.includes(label));
  const worldGets = () => dom.requests.filter((entry) => entry.method === 'GET' && entry.path === '/world').length;

  /* 1. 先进世界，把「购买前」的快照读进缓存。 */
  click(tile('世界'));
  await settle();
  const before = screen().allText;
  assert.equal(before.includes('界面错误'), false, `世界屏渲染报错：${before.slice(0, 200)}`);
  assert.match(before, /这个位置没有摆着的东西/);
  assert.equal(before.includes('拿起到'), false, '购买前这个世界里没有可拿的物件');
  assert.equal(worldGets(), 1);

  /* 2. 主屏 → 商店 → 商品详情 → 点确认购买（真发一条 POST /purchases）。 */
  click(dom.document.getElementById('btn-home'));
  await settle();
  click(tile('商店'));
  await settle();
  const card = screen().findAll((node) => node.tagName === 'BUTTON' && String(node.className).includes('shop-card'))[0];
  assert.ok(card, '商店里要有可点的目录项');
  click(card);
  await settle();
  const buyButton = findButton(screen(), '确认购买');
  assert.ok(buyButton, '商品详情要有确认购买按钮');
  assert.equal(buyButton.disabled, false, 'owner 说可购买时按钮不该禁用');
  click(buyButton);
  await settle();

  const sheetHost = dom.document.getElementById('sheet-host');
  assert.match(sheetHost.allText, /确认购买/);
  click(sheetHost.find((node) => node.tagName === 'BUTTON' && String(node.className).includes('btn-primary')));
  await settle();

  const posts = dom.requests.filter((entry) => entry.method === 'POST' && entry.path === '/purchases');
  assert.equal(posts.length, 1, '一次购买只发一条 POST');
  const sent = JSON.parse(posts[0].init.body);
  assert.equal(sent.catalog_item_id, CATALOG_ID);
  assert.equal(sent.operation_key, 'stub-uuid-1');
  assert.equal(worldGets(), 1, '人在商店里买东西，不该顺手重读世界');

  /* 3. 服务那边物件已经变了：只有再读一次 /world 才看得到。 */
  dom.responses['/world'] = snapshot({data: {objects: purchasedObjects}});

  /* 4. 回世界：旧快照必须作废，新物件与拿起按钮都要出现。 */
  click(dom.document.getElementById('btn-home'));
  await settle();
  click(tile('世界'));
  await settle();

  const after = screen().allText;
  assert.equal(after.includes('界面错误'), false, `世界屏渲染报错：${after.slice(0, 200)}`);
  assert.equal(worldGets(), 2, '回到世界要重读一次 /world，且只重读一次（不排第二条请求）');
  assert.equal(after.includes('这个位置没有摆着的东西'), false, '不能复用购买前的旧快照');
  assert.match(after, /virtual_shortcake/);
  assert.ok(findButton(screen(), '拿起到'), '新物件要有拿起到左/右手的按钮');
});

/* 父级续链那条真实失败：手上那件东西的 id 只有 69 字（item- + 64 hex），
   屏幕上的展示标签会被遮罩成 item-[已隐藏的长标识]，但 PLACE 的 object_id 必须是**原字节**。
   这里用同一个 live 实例 left_hand 的真实 id 走一遍：世界屏点「放下」→ 确认 → POST 原 id。 */
test('（stub DOM）手持长69字真实 id：世界屏点放下，PLACE 必须发原字节而不是遮罩后的展示串', async () => {
  /* live /tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094 的 left_hand 原值（只读证据，本测试不碰实例）。 */
  const HELD_ID = 'item-7bd7c347b2cb6d134816f4020ad912440db3c42ef408462151c9d06ad48aab2c';
  assert.equal(HELD_ID.length, 69, '这条回归锁的是真实形状：69 字');

  const heldObjects = {
    status: 'available', source: 'world_body_substrate', substrate_revision: 9,
    here: [], hands: {left_hand: HELD_ID, right_hand: null}, held: [HELD_ID],
    counts: {here: 0, hands: 1}
  };
  const doc = snapshot({data: {objects: heldObjects}});

  /* 1. 字段先分开：原值归 id，展示归 idLabel（遮罩不影响它被找到）。 */
  const worldView = P.worldView(doc);
  const objects = worldView.objects;
  const slot = objects.handSlots.left_hand;
  assert.equal(slot.present, true);
  assert.equal(slot.idOk, true);
  assert.equal(slot.id, HELD_ID, '协议原值必须是 69 字原字节');
  assert.notEqual(slot.idLabel, HELD_ID, '展示标签必须与协议原值分开');
  assert.match(slot.idLabel, /已隐藏/, '展示标签仍按安全规则遮罩长标识');
  assert.equal(objects.handSlots.right_hand.present, false);
  assert.deepEqual([...objects.heldObjects.map((item) => item.id)], [HELD_ID], 'held 也保留原值');
  assert.equal(objects.held[0], slot.idLabel, 'held 的展示那一份与 id 分开');
  const freeHand = P.worldPickupOptions(worldView, {id: HELD_ID, idOk: true, portable: true});
  assert.deepEqual([...freeHand.slots.map((item) => item.slot)], ['right_hand'], '左手占着时只能拿起到右手');

  /* 2. 真实点击链：进世界 → 左手那一行点「放下」→ 弹层确认 → 一条 PLACE。 */
  const dom = makeStubDom({snapshot: doc});
  const context = vm.createContext(dom.sandbox);
  vm.runInContext(src, context, {filename: 'components/phone/web/app.js'});
  await settle();

  const screen = () => dom.document.getElementById('screen-body');
  click(screen().findAll((node) => node.tagName === 'BUTTON' && String(node.className).includes('tile'))
    .find((node) => node.allText.includes('世界')));
  await settle();

  const worldText = screen().allText;
  assert.equal(worldText.includes('界面错误'), false, `世界屏渲染报错：${worldText.slice(0, 200)}`);
  assert.match(worldText, /左手/);
  assert.match(worldText, /已隐藏/, '左手这件东西仍然要显示出来（遮罩后的标签）');
  assert.equal(worldText.includes(HELD_ID), false, '协议原值不上屏');

  const dropButton = screen().findAll((node) => node.tagName === 'BUTTON' && node.allText.includes('放下'))[0];
  assert.ok(dropButton, '左手有东西时要能点「放下」');
  assert.equal(dropButton.disabled, false, '标识可用、房间可读时「放下」不该禁用');
  click(dropButton);
  await settle();

  const sheetHost = dom.document.getElementById('sheet-host');
  const sheetText = sheetHost.allText;
  assert.match(sheetText, /放下手里的东西/);
  assert.match(sheetText, /已隐藏/, '弹层里的物件名用展示标签');
  assert.equal(sheetText.includes(HELD_ID), false, '弹层也不回显协议原值');

  click(sheetHost.find((node) => node.tagName === 'BUTTON' && String(node.className).includes('btn-primary')));
  await settle();

  const posts = dom.requests.filter((entry) => entry.method === 'POST' && entry.path === '/world/actions');
  assert.equal(posts.length, 1, '一次放下只发一条 POST');
  const sent = JSON.parse(posts[0].init.body);
  assert.equal(sent.action, 'PLACE');
  assert.deepEqual(Object.keys(sent.params).sort(), ['object_id', 'target']);
  assert.equal(sent.params.object_id, HELD_ID, 'PLACE 的 object_id 必须是 69 字原字节，不是展示串');
  assert.deepEqual(sent.params.target, {kind: 'room', location_id: 'home', area_id: 'bedroom'});
  assert.equal(JSON.stringify(sent).includes('已隐藏'), false, '请求体里不能出现遮罩后的展示串');
});
