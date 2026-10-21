/**
 * NyAiro rc1 · 实体日用店 / 房间布置 / 房间灯：界面（components/phone/web）定向检查。
 *
 * 只覆盖本轮 U 写集：
 *   · 实体店货架 → 商品 → 确认购买（POST /store/purchases）与原字节目录 id；
 *   · 到货状态：pending 不能布置、付款成功不宣称到货；
 *   · 自有家具布置（动词名只认 owner 报的 ARRANGE / RELOCATE，参数键按 C 的 PARAM_SPEC）；
 *   · instance 级台面（`desk_surface:<实例 id>`，两张桌子不串）与「把手里的小物件放到这块台面上」；
 *   · 房间灯 unknown / on / off（绝对状态，不发翻转）与「未知不是黑的」；
 *   · 结果未知时保留原操作键，不换键重发；
 *   · 390 宽度相关的静态样式检查（**不是**浏览器像素验证）。
 *
 * 仍然不启动服务、不开真实浏览器：这是 stub DOM 的接线与字段级检查，
 * 真实页面串链由父级在同一实例上做。运行：
 *   node --test tests/test_room_living_ui.mjs
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

const pureSandbox = vm.createContext({});
vm.runInContext(src, pureSandbox, {filename: 'components/phone/web/app.js'});
const P = pureSandbox.NyairoPhone;

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

/* ---------------- 真实形状的标识（一律按服务原字节，不 trim、不截断） ---------------- */

/* live 验收里出现过的 69 字实例 id 形状：item- + 64 hex。 */
const SNACK_ID = 'item-7bd7c347b2cb6d134816f4020ad912440db3c42ef408462151c9d06ad48aab2c';
const DESK_ID = 'item-' + 'd0c5'.repeat(16);
const LAMP_ID = 'item-' + 'ab12'.repeat(16);
const SHELF_ID = 'item-' + '1a2b'.repeat(16);
const LAMP2_ID = 'item-' + 'cd34'.repeat(16);
/* 台面是 instance 级标识：`<kind>:<owner item_instance_id>`；bed_surface 还保留兼容的裸写法。 */
const DESK_SURFACE = 'desk_surface:' + DESK_ID;
const BED_SURFACE = 'bed_surface';
/* 目录项：catalog: + 112 hex，比界面上的展示标签长得多。 */
const DESK_CATALOG = 'catalog:' + '9c1d'.repeat(28);
const LAMP_CATALOG = 'catalog:' + '7a4e'.repeat(28);

assert.equal(SNACK_ID.length, 69, '这条检查锁的是真实形状：69 字');
assert.equal(DESK_ID.length, 69);
assert.equal(DESK_SURFACE.length, 82, 'desk_surface:<69 字实例 id>');
assert.equal(DESK_SURFACE.startsWith('desk_surface:item-'), true);

const KEY = 'nyairo-room-key-1';

function roomBlocks(overrides = {}) {
  return {
    light: {
      status: 'available', band: 'dim', band_source: 'world_room_environment.light_band',
      power: 'off', since: '2026-10-21T20:58:00+09:00', on_seconds: 0,
      fixture_ids: [LAMP_ID], declared_source: 'catalog:bedside_lamp',
      place_id: 'home', area_id: 'bedroom', reason_code: null
    },
    furniture: {
      status: 'available', source: 'world_property_economy',
      count: 3, arrange_available: true, unplaced_delivered: [],
      items: [
        {
          item_instance_id: DESK_ID, catalog_item_id: DESK_CATALOG, name: '书桌',
          label: '书桌', usable_kind: 'FURNISH', relocatable: true,
          surfaces: ['desk_surface:' + DESK_ID],
          placement: {kind: 'room', location_id: 'home', area_id: 'bedroom'},
          current_location: 'home/bedroom',
          delivered: true, delivery_status: 'DELIVERED', arrange_available: true
        },
        {
          item_instance_id: LAMP_ID, catalog_item_id: LAMP_CATALOG, name: '床头灯',
          label: '床头灯', usable_kind: 'FURNISH', relocatable: true, surfaces: [],
          placement: {kind: 'room', location_id: 'home', area_id: 'bedroom'},
          current_location: 'home/bedroom',
          delivered: true, delivery_status: 'DELIVERED', arrange_available: true
        },
        {
          item_instance_id: SHELF_ID, catalog_item_id: 'small_bookshelf', name: '小书架',
          label: '小书架', usable_kind: 'FURNISH', relocatable: true, surfaces: [],
          placement: null,
          current_location: 'in_transit/home/bedroom',
          delivered: false, delivery_status: 'IN_TRANSIT', arrange_available: false
        }
      ]
    },
    deliveries: {
      status: 'available', mode: 'temporal_home_delivery', duration_seconds: 7200,
      destination_location: 'home/bedroom', pending_count: 1, delivered_count: 0,
      realized_by: 'world_body_service.tick', read_delivers_items: false,
      pending: ['delivery-' + SHELF_ID], delivered: [],
      items: [{
        delivery_id: 'delivery-' + SHELF_ID, item_instance_id: SHELF_ID,
        destination_location: 'home/bedroom',
        purchased_at: '2026-10-21T20:30:00+09:00',
        expected_delivery_at: '2026-10-21T22:30:00+09:00',
        delivered_at: null, status: 'PENDING',
        delivery_provenance: 'store_counter_delivery'
      }]
    },
    surfaces: {
      status: 'available', source: 'world_body_substrate',
      count: 2, rest_here: [], declared_kinds: ['bed_surface', 'chair_surface', 'desk_surface'],
      instance_scoped: true, read_is_pure: true,
      here: [
        {
          surface_id: DESK_SURFACE, surface_kind: 'desk_surface', label: 'desk',
          owner_object_id: DESK_ID, owner_catalog_item_id: 'simple_desk',
          location_id: 'home', area_id: 'bedroom',
          pose_condition: null, rest_modes: [], items: []
        },
        {
          surface_id: BED_SURFACE, surface_kind: 'bed_surface', label: 'bed',
          owner_object_id: null, owner_catalog_item_id: 'basic_bed',
          location_id: 'home', area_id: 'bedroom',
          pose_condition: 'lying', rest_modes: ['rest', 'sleep_like'], items: [SNACK_ID]
        }
      ]
    },
    ...overrides
  };
}

/* C 的 PARAM_SPEC 与 P 的白名单：ARRANGE 收 object_id（或别名 item_instance_id）+ target，
   TOGGLE_FIXTURE 收 object_id（或别名 fixture_id）+ state。 */
const ROOM_VERBS = [
  {verb: 'ARRANGE', available: true, blocked_reason: null, detail: null,
    params: {item_instance_id: 'string', object_id: 'string', target: 'object'}, candidates: []},
  {verb: 'TOGGLE_FIXTURE', available: true, blocked_reason: null, detail: null,
    params: {object_id: 'string', fixture_id: 'string', state: ['on', 'off']}, candidates: []}
];

/** 一份按本轮字段写的世界快照（房间四块都在；旧五块保持原形）。 */
function snapshot({room = {}, verbs = ROOM_VERBS, objects = null} = {}) {
  const blocks = roomBlocks(room);
  return {
    kind: 'world_phone_world',
    schema_version: 'world.phone.world.v1',
    ok: true,
    status: 'applied',
    data: {
      world: {
        status: 'available', source: 'world_state', world_id: 'shiomi_city', revision: 41,
        schema_version: 'world.foundation.v0', timezone: 'Asia/Tokyo', scene_id: 'home.bedroom',
        local_time: '2026-10-21T21:04:00+09:00', local_date: '2026-10-21', weekday: 3
      },
      location: {
        status: 'available', source: 'world_state+world_topology', node: 'home/bedroom',
        place_id: 'home', area_id: 'bedroom', name: '家', kind: 'home', home: true
      },
      local_environment: {
        status: 'available', source: 'world_package_scene',
        building: {place_id: 'home', name: '家'},
        area: {area_id: 'bedroom', name: '卧室', kind: 'room'},
        /* 世界包自带的摆设：不能拿、也不是她买的。 */
        furniture: [{object_id: 'home_table06', name: '桌子', kind: 'surface', note: '世界自带'}],
        furniture_total: 1,
        adjacent_areas: [{node: 'home', name: '家', kind: 'home'}],
        note: 'furniture 只含当前区域',
        light: blocks.light
      },
      destinations: {
        status: 'available', source: 'world_daily_opportunities', scope: 'NOW_ONLY',
        current_node: 'home/bedroom', walk_seconds_max: 1800,
        places: [{
          location_id: 'home_goods_store', name: '日用杂货店', kind: 'store', here: false,
          walk_seconds: 240, reach: 'reachable', route: ['nearby_street', 'home_goods_store'],
          availability: {status: 'OPEN', reason_code: null, source: 'world_package_schedule'},
          affordances: [], indoor_areas: []
        }],
        nodes: [{
          node: 'nearby_street', place_id: 'nearby_street', area_id: 'nearby_street', label: '附近街道',
          kind: 'place', walk_seconds: 90, reach: 'reachable', direct_move_available: true, route: ['nearby_street']
        }]
      },
      travel: {
        status: 'none', source: 'world_travel_delivery.observe_travel',
        travel_id: null, execution_id: null, origin_location_id: null, destination_location_id: null,
        route_id: null, route_revision: null, route_duration_seconds: null,
        started_at: null, expected_arrival_at: null, arrived_at: null,
        elapsed_seconds: null, remaining_seconds: null, progress_band: null,
        location_unchanged: null, body_load: null, body_load_source: null
      },
      objects: Object.assign(
        {},
        objects || {
          status: 'available', source: 'world_body_substrate', substrate_revision: 12,
          here: [],
          hands: {left_hand: SNACK_ID, right_hand: null},
          held: [SNACK_ID],
          counts: {here: 0, hands: 1}
        },
        /* 契约 §3.2–3.4：房间三块是 objects 的块内附加字段（不是顶层）。 */
        {
          surfaces: blocks.surfaces,
          owned_furniture: blocks.furniture,
          deliveries: blocks.deliveries
        }
      ),
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
        local_owner_verbs: ['MOVE', 'PICK_UP', 'PLACE', 'POSE', 'REST', 'ARRANGE', 'TOGGLE_FIXTURE'],
        verbs: [
          {verb: 'MOVE', available: true, blocked_reason: null, detail: null,
            params: {destination_location_id: 'string'}, candidates: []},
          {verb: 'PLACE', available: true, blocked_reason: null, detail: null,
            params: {object_id: 'string', target: 'object'}, candidates: []},
          {verb: 'POSE', available: true, blocked_reason: null, detail: null,
            params: {pose: 'string'}, candidates: []},
          {
            verb: 'REST', available: false, blocked_reason: 'RECOVERY_NOT_NEEDED',
            detail: 'fatigue is below the recovery threshold',
            params: {state: ['start', 'stop'], mode: ['rest', 'sleep_like']},
            start_available: false, stop_available: false, sleep_like_available: false,
            current: {pose: 'standing', recovery_state: 'inactive', travelling: false, rest_surface: false}
          }
        ].concat(verbs)
      },
      recent_executions: {status: 'available', source: 'world_execution_ledger', limit: 5, items: []}
    }
  };
}

/** GET /api/phone/store 的货架（C 的 store_shop_projection 形状）。 */
function storeDoc(overrides = {}) {
  return {
    ok: true,
    status: 'applied',
    data: {
      kind: 'world.phone.shopping.store',
      status: 'available',
      store_id: 'home_goods_store',
      channel: 'home_goods_store',
      store_kind: 'physical_counter',
      currency: 'JPY',
      balance: 9760,
      at_store: true,
      location: {location_id: 'home_goods_store', area_id: null},
      purchase_available: true,
      reason_code: null,
      availability: {
        status: 'OPEN', reason_code: null, source: 'world_package_schedule',
        opens_at: '10:00', closes_at: '20:00', timezone: 'Asia/Tokyo'
      },
      payment: {currency: 'JPY', source: 'world_wallet', cash: false, top_up: false, outbound: false},
      delivery: {
        destination_location: 'home/bedroom', mode: 'temporal_home_delivery',
        duration_seconds: 7200, arrival_written_by: 'world_body_service.tick',
        placement_requires: 'ARRANGE'
      },
      pending_delivery_count: 1,
      owned_furniture_count: 3,
      capacity_available: true,
      read_is_pure: true,
      paid_real_money: false,
      catalog: [
        {
          catalog_item_id: DESK_CATALOG, name: '书桌', description: '能放东西的一张桌子',
          price: 4200, currency: 'JPY', usable_kind: 'FURNISH', affordable: true
        },
        {
          catalog_item_id: 'virtual_snack_wafer', name: '薄饼', description: '一小包',
          price: 240, currency: 'JPY', usable_kind: 'CARRY', affordable: true
        }
      ],
      ...overrides
    }
  };
}

/* ---------------- 1. 语法与词表 ---------------- */

test('app.js 通过 node --check（真实进程，不是猜）', () => {
  execFileSync(process.execPath, ['--check', appPath.pathname.replace(/^\//, '')], {stdio: 'pipe'});
});

test('旧五动词一字不改；新增动词只在 ALL_WORLD_VERBS 里', () => {
  assert.deepEqual([...P.WORLD_VERBS], ['MOVE', 'PICK_UP', 'PLACE', 'POSE', 'REST']);
  assert.deepEqual([...P.ALL_WORLD_VERBS],
    ['MOVE', 'PICK_UP', 'PLACE', 'POSE', 'REST', 'ARRANGE', 'RELOCATE', 'TOGGLE_FIXTURE']);
  /* 参数键就是 C 的 PARAM_SPEC 那两个：object_id（别名 item_instance_id）+ target / state。 */
  assert.deepEqual([...P.ARRANGE_PARAM_KEYS], ['object_id', 'target']);
  assert.deepEqual([...P.FIXTURE_PARAM_KEYS], ['object_id', 'state']);
  assert.deepEqual([...P.ROOM_VERB_NAMES.arrange], ['ARRANGE', 'RELOCATE']);
  assert.deepEqual([...P.ROOM_VERB_NAMES.fixture], ['TOGGLE_FIXTURE']);
});

/* ---------------- 2. 房间四块的字段搬运 ---------------- */

test('房间光：available 才给档位/开关/来源，unknown 保留原因码且绝不折算成 0', () => {
  const available = P.roomLightView({light: roomBlocks().light});
  assert.equal(available.status, 'available');
  assert.equal(available.available, true);
  assert.equal(available.band, 'dim');
  assert.equal(available.bandLabel, '暗');
  assert.equal(available.power, 'off');
  assert.equal(available.powerLabel, '关着');
  assert.equal(available.declaredSource, 'catalog:bedside_lamp');
  assert.deepEqual([...available.fixtureIds.map((item) => item.id)], [LAMP_ID], '灯具 id 保留原字节');
  assert.notEqual(available.fixtureIds[0].idLabel, LAMP_ID, '展示标签与协议原值分开');

  const unknown = P.roomLightView({light: {status: 'unknown', reason_code: 'NO_DECLARED_LIGHT_IN_ROOM'}});
  assert.equal(unknown.available, false);
  assert.equal(unknown.band, '');
  assert.equal(unknown.power, '');
  assert.equal(unknown.reason, 'NO_DECLARED_LIGHT_IN_ROOM');
  assert.deepEqual([...unknown.fixtureIds], []);

  const missing = P.roomLightView({});
  assert.equal(missing.status, 'missing');
  assert.equal(missing.available, false);
});

test('自有家具与台面：instance id 原字节留作 payload，展示另用标签；名字不决定能力', () => {
  const furniture = P.ownedFurnitureView(roomBlocks().furniture);
  assert.equal(furniture.items.length, 3);
  const desk = furniture.items[0];
  assert.equal(desk.id, DESK_ID, '家具 id 必须是 69 字原字节');
  assert.equal(desk.idOk, true);
  assert.notEqual(desk.idLabel, DESK_ID);
  assert.equal(desk.catalogId, DESK_CATALOG, '目录项原字节也留着');
  assert.equal(desk.delivered, true);
  assert.equal(furniture.items[2].delivered, false, 'pending 的那件 delivered=false');
  assert.equal(furniture.items[2].delivery, 'in_transit', 'C 的大写枚举统一成小写');

  const surfaces = P.surfacesView(roomBlocks().surfaces);
  assert.equal(surfaces.items.length, 2);
  assert.equal(surfaces.items[0].id, DESK_SURFACE);
  assert.equal(surfaces.items[0].id.length, 82, 'desk_surface:<69 字实例 id>');
  assert.equal(surfaces.items[1].id, BED_SURFACE);
  assert.notEqual(surfaces.items[0].id, surfaces.items[1].id, '两张桌子是两块不同的台面');
  /* 能力只照 owner 的声明：桌面没有 pose_condition/rest_modes，床有 lying + rest/sleep_like。 */
  assert.equal(surfaces.items[0].poseLabel, '');
  assert.deepEqual([...surfaces.items[0].restModes], []);
  assert.equal(surfaces.items[0].label, '书桌面');
  assert.equal(surfaces.items[0].ownerItemId, DESK_ID, '台面的主人是真实 instance id');
  assert.equal(surfaces.items[1].poseLabel, '能躺');
  assert.deepEqual([...surfaces.items[1].restModes.map((item) => item.code)], ['rest', 'sleep_like']);
  assert.deepEqual([...surfaces.items[1].children.map((item) => item.id)], [SNACK_ID], '上面放着的东西是真实 id');

  /* 没有任何姿势/休息声明 = 没声明；界面不会因为名字叫“书桌面”就假设它能坐能放。 */
  const undeclared = P.surfacesView({status: 'available', items: [{surface_id: DESK_SURFACE, label: 'desk'}]});
  assert.equal(undeclared.items[0].poseLabel, '');
  assert.deepEqual([...undeclared.items[0].restModes], []);
  assert.deepEqual([...undeclared.items[0].children], []);
});

test('布置动词只认 owner 报上来的：报 RELOCATE 就用 RELOCATE，要发的键没被声明就挡下', () => {
  const view = P.worldView(snapshot());
  const desk = P.ownedFurnitureView(roomBlocks().furniture).items[0];
  assert.equal(P.roomVerbFor(view, 'arrange').verb, 'ARRANGE');
  const option = P.worldArrangeOption(view, desk);
  assert.equal(option.ok, true, option.message);
  assert.equal(option.verb, 'ARRANGE');
  assert.deepEqual([...option.targets.map((item) => item.key)], ['room']);
  assert.deepEqual(JSON.parse(JSON.stringify(option.targets[0].target)),
    {kind: 'room', location_id: 'home', area_id: 'bedroom'});

  const relocateOnly = P.worldView(snapshot({
    verbs: [{verb: 'RELOCATE', available: true, blocked_reason: null, detail: null,
      params: {object_id: 'string', target: 'object'}, candidates: []}]
  }));
  assert.equal(P.roomVerbFor(relocateOnly, 'arrange').verb, 'RELOCATE');
  assert.equal(P.worldArrangeOption(relocateOnly, desk).verb, 'RELOCATE');
  const relocateBuilt = P.buildWorldActionPayload({
    action: 'RELOCATE', params: {object_id: DESK_ID, target: {kind: 'room', location_id: 'home', area_id: 'bedroom'}},
    operation_key: KEY
  });
  assert.equal(relocateBuilt.ok, true, relocateBuilt.message);
  assert.equal(relocateBuilt.payload.action, 'RELOCATE');

  /* 只有 ARRANGE 可用性 false：不猜、不给按钮 */
  const blocked = P.worldView(snapshot({
    verbs: [{verb: 'ARRANGE', available: false, blocked_reason: 'NOT_OWNED', detail: null,
      params: {object_id: 'string', target: 'object'}, candidates: []}]
  }));
  const blockedOption = P.worldArrangeOption(blocked, desk);
  assert.equal(blockedOption.ok, false);
  assert.equal(blockedOption.message, '这件东西不属于这个实例');

  /* owner 没声明 target → 挡下并说明，绝不改名硬发 */
  const mismatched = P.worldView(snapshot({
    verbs: [{verb: 'ARRANGE', available: true, blocked_reason: null, detail: null,
      params: {object_id: 'string'}, candidates: []}]
  }));
  const mismatchOption = P.worldArrangeOption(mismatched, desk);
  assert.equal(mismatchOption.ok, false);
  assert.match(mismatchOption.message, /参数/);
  assert.equal(P.paramKeyMismatch(['item_instance_id', 'object_id', 'target'], P.ARRANGE_PARAM_KEYS), '');
  assert.notEqual(P.paramKeyMismatch(['object_id'], P.ARRANGE_PARAM_KEYS), '', '缺 target 就要挡下');

  /* pending 的家具不能布置 */
  const pending = P.worldArrangeOption(view, P.ownedFurnitureView(roomBlocks().furniture).items[2]);
  assert.equal(pending.ok, false);
  assert.match(pending.message, /还没到货/);
});

test('灯：绝对状态参数、同状态禁用、参数键不一致就挡下', () => {
  const view = P.worldView(snapshot());
  const light = view.room.light;
  const fixture = light.fixtureIds[0];
  assert.equal(P.worldFixtureOption(view, fixture, 'off').ok, true);

  const built = P.buildWorldActionPayload({
    action: 'TOGGLE_FIXTURE', params: {object_id: LAMP_ID, state: 'on'}, operation_key: KEY
  });
  assert.equal(built.ok, true, built.message);
  assert.deepEqual(Object.keys(built.payload.params).sort(), ['object_id', 'state']);
  assert.equal(built.payload.params.object_id, LAMP_ID);
  assert.equal(built.payload.params.state, 'on');
  /* fixture_id 只是别名入口，payload 里只有 object_id */
  const alias = P.buildWorldActionPayload({
    action: 'TOGGLE_FIXTURE', params: {fixture_id: LAMP_ID, state: 'off'}, operation_key: KEY
  });
  assert.equal(alias.payload.params.object_id, LAMP_ID);
  assert.equal('fixture_id' in alias.payload.params, false);
  /* 没有“翻转”这种值 */
  const flip = P.buildWorldActionPayload({
    action: 'TOGGLE_FIXTURE', params: {object_id: LAMP_ID, state: 'toggle'}, operation_key: KEY
  });
  assert.equal(flip.ok, false);
  assert.equal(flip.field, 'state');

  const mismatch = P.worldView(snapshot({
    verbs: [{verb: 'TOGGLE_FIXTURE', available: true, blocked_reason: null, detail: null,
      params: {fixture_id: 'string', power: 'string'}, candidates: []}]
  }));
  assert.match(P.worldFixtureOption(mismatch, fixture, 'off').message, /参数/);

  const noVerb = P.worldView(snapshot({verbs: []}));
  assert.equal(P.worldFixtureOption(noVerb, fixture, 'off').ok, false);
  assert.equal(P.roomVerbFor(noVerb, 'fixture').verb, '');
});

test('摆放/布置目标：形状就是已冻结的 {kind, location_id, area_id} / {kind, location_id, surface_id}', () => {
  const surface = P.buildSurfacePlacementTarget({kind: 'surface', location_id: 'home', surface_id: DESK_SURFACE});
  assert.equal(surface.ok, true, surface.message);
  assert.deepEqual(JSON.parse(JSON.stringify(surface.target)),
    {kind: 'surface', location_id: 'home', surface_id: DESK_SURFACE});
  assert.equal(surface.target.surface_id.length, 82, '台面 id 原字节');

  const badKind = P.buildSurfacePlacementTarget({kind: 'container', location_id: 'home', container_id: 'drawer'});
  assert.equal(badKind.ok, false);
  const badId = P.buildSurfacePlacementTarget({kind: 'surface', location_id: 'home', surface_id: ''});
  assert.equal(badId.ok, false);

  const arrangeRoom = P.buildArrangeTarget({kind: 'room', location_id: 'home', area_id: 'bedroom'});
  assert.deepEqual(JSON.parse(JSON.stringify(arrangeRoom.target)),
    {kind: 'room', location_id: 'home', area_id: 'bedroom'});
  const arrangeSurface = P.buildArrangeTarget({kind: 'surface', location_id: 'home', surface_id: DESK_SURFACE});
  assert.equal(arrangeSurface.ok, true);
  const arrangeHand = P.buildArrangeTarget({kind: 'hand', slot_id: 'left_hand'});
  assert.equal(arrangeHand.ok, false);

  const built = P.buildWorldActionPayload({
    action: 'ARRANGE',
    params: {object_id: DESK_ID, target: {kind: 'surface', location_id: 'home', surface_id: DESK_SURFACE}},
    operation_key: KEY
  });
  assert.equal(built.ok, true, built.message);
  assert.equal(built.payload.params.object_id, DESK_ID, '69 字原字节');
  assert.equal(built.payload.params.target.surface_id, DESK_SURFACE);
  const aliasBuilt = P.buildWorldActionPayload({
    action: 'ARRANGE', params: {item_instance_id: DESK_ID, target: {kind: 'room', location_id: 'home', area_id: 'bedroom'}},
    operation_key: KEY
  });
  assert.equal(aliasBuilt.payload.params.object_id, DESK_ID, '别名入口也发成 object_id');
  assert.equal('item_instance_id' in aliasBuilt.payload.params, false);

  /* 操作键把 action + kind + target + state 全串进去：同键换目标就是另一个 scope */
  const a = P.worldActionScope('TOGGLE_FIXTURE', {object_id: LAMP_ID, state: 'on'});
  const b = P.worldActionScope('TOGGLE_FIXTURE', {object_id: LAMP_ID, state: 'off'});
  const c = P.worldActionScope('ARRANGE', {object_id: DESK_ID, target: {kind: 'room', location_id: 'home', area_id: 'bedroom'}});
  assert.notEqual(a, b, 'on/off 不能共用同一个键');
  assert.notEqual(a, c);
  assert.match(c, /"area_id":"bedroom"/);
});

/* ---------------- 3. 实体店货架视图 ---------------- */

test('实体店：位置/营业/余额/渠道/交付都照 owner 说；长目录 id 原字节保留', () => {
  const view = P.storeView(storeDoc());
  assert.equal(view.storeId, 'home_goods_store');
  assert.equal(view.storeName, 'home_goods_store', 'C 不给 store_name 就退回 store_id 的标签，不编名字');
  assert.equal(view.storeKind, 'physical_counter');
  assert.equal(view.channel, 'home_goods_store');
  assert.equal(view.availability, 'OPEN');
  assert.equal(view.availabilityLabel, '营业中');
  assert.equal(view.opensAt, '10:00');
  assert.equal(view.closesAt, '20:00');
  assert.equal(view.balance, 9760);
  assert.equal(view.balanceText, '9760.00 JPY');
  assert.equal(view.here, true, 'at_store');
  assert.equal(view.locationId, 'home_goods_store');
  assert.equal(view.pendingDeliveryCount, 1);
  assert.equal(view.ownedFurnitureCount, 3);
  assert.equal(view.capacityAvailable, true);
  assert.equal(view.deliveryMode, 'temporal_home_delivery');
  assert.match(view.deliveryText, /送到家/);
  assert.equal(view.placementRequires, 'ARRANGE');
  assert.equal(view.items.length, 2);
  assert.equal(view.items[0].id, DESK_CATALOG, '目录项 id 原字节');
  assert.equal(view.items[0].usableKindLabel, '家具（要配送）');
  assert.equal(view.items[0].deliveryText, '付款后由店里送到家（要等一段时间）');
  assert.equal(view.items[0].affordable, true);
  assert.equal(view.items[1].usableKindLabel, '随身带着');

  /* 不在店里 / 店关门：原因码原样翻成人话 */
  const away = P.storeView(storeDoc({at_store: false, purchase_available: false, reason_code: 'STORE_LOCATION_REQUIRED'}));
  assert.equal(away.here, false);
  assert.equal(away.purchaseAvailable, false);
  assert.match(away.blockedText, /不在店里/);
  const closed = P.storeView(storeDoc({purchase_available: false, reason_code: 'STORE_CLOSED',
    availability: {status: 'CLOSED', opens_at: '10:00', closes_at: '20:00'}}));
  assert.equal(closed.availabilityLabel, '现在关着');
  assert.match(closed.blockedText, /没开门/);

  /* 客户端拿不到这一件能不能买时保持 null，不替服务说“能买” */
  const unknownItem = P.storeView(storeDoc({catalog: [{catalog_item_id: 'x', name: 'x', price: 1, usable_kind: 'FURNISH'}]}));
  assert.equal(unknownItem.items[0].affordable, null);
  assert.equal(unknownItem.items[0].purchasable, null);
  /* 余额不够时 owner 说 affordable=false，界面就照它挡下 */
  const poor = P.storeView(storeDoc({catalog: [{catalog_item_id: 'x', name: 'x', price: 99999, usable_kind: 'FURNISH', affordable: false}]}));
  assert.equal(poor.items[0].purchasable, false);
});

/* ---------------- 4. stub DOM：真实点击链 ---------------- */

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
      this.title = '';
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
      csrf_token: 'tok-room',
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
    '/world/actions': seed.worldAction || {
      ok: true,
      status: 'applied',
      data: {
        execution_id: 'phone-exec:room-1',
        action: 'TOGGLE_FIXTURE',
        operation_key: 'stub-key',
        fixture: {status: 'committed', state: 'on', object_id: LAMP_ID},
        body: {applied_to_body: false, consequence_reason: 'environment_input_pending'},
        world_mutation_refs: ['world.fixture=' + LAMP_ID + ':on']
      }
    },
    '/store': seed.store || storeDoc(),
    '/store/purchases': seed.purchase || {
      ok: true,
      status: 'applied',
      data: {
        execution_id: 'store-exec:1', operation_key: 'stub-key',
        catalog_item_id: DESK_CATALOG, item_instance_id: DESK_ID, price: 4200,
        delivery_status: 'PENDING', materialization: 'pending_arrange',
        expected_delivery_at: '2026-10-21T22:30:00+09:00',
        delivery_location: 'home/bedroom', current_balance: 5560
      }
    },
    '/shop': {
      ok: true, status: 'applied',
      data: {currency: 'JPY', purchase_available: true,
        catalog: [{catalog_item_id: 'virtual_snack_wafer', name: '薄饼', price: 240, usable_kind: 'snack'}]}
    },
    '/wallet': {ok: true, status: 'applied', data: {currency: 'JPY', balance: 9760}}
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
  return {sandbox: sandbox, document: document, requests: requests, responses: responses};
}

const settle = async () => {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

const click = (node) => {
  const handlers = (node && node.listeners && node.listeners.click) || [];
  assert.ok(handlers.length > 0, '这个按钮没有接上点击');
  for (const handler of handlers) handler({target: node});
};

const findButton = (rootNode, text) => rootNode.findAll((node) => node.tagName === 'BUTTON' && node.allText.includes(text))[0];
const rowsWith = (rootNode, text) => rootNode.findAll((node) => node.tagName === 'LI' && node.allText.includes(text));

async function bootApp(seed) {
  const dom = makeStubDom(seed);
  const context = vm.createContext(dom.sandbox);
  vm.runInContext(src, context, {filename: 'components/phone/web/app.js'});
  await settle();
  return dom;
}

const screen = (dom) => dom.document.getElementById('screen-body');
const posts = (dom, path) => dom.requests.filter((entry) => entry.method === 'POST' && entry.path === path);
const gets = (dom, path) => dom.requests.filter((entry) => entry.method === 'GET' && entry.path === path);

test('（stub DOM）主屏 → 实体日用店：货架按服务显示营业/余额/交付，且只发 GET', async () => {
  const dom = await bootApp({snapshot: snapshot()});
  const stores = () => gets(dom, '/store').length;

  assert.equal(stores(), 0, '进主屏不该去读店里的货架');
  click(findButton(screen(dom), '实体日用店'));
  await settle();

  const text = screen(dom).allText;
  assert.equal(text.includes('界面错误'), false, `实体店屏渲染报错：${text.slice(0, 200)}`);
  assert.match(text, /渠道：实体日用店/);
  assert.match(text, /home_goods_store/);
  assert.match(text, /营业中/);
  assert.match(text, /10:00 – 20:00/);
  assert.match(text, /9760\.00 JPY/);
  assert.match(text, /家具（要配送）/);
  assert.match(text, /付款后由店里送到家/);
  assert.match(text, /ARRANGE/);
  assert.equal(stores(), 1, '进店只读一次货架');
  assert.equal(posts(dom, '/store/purchases').length, 0, '读货架不发任何写请求');
  assert.equal(gets(dom, '/world').length, 0, '读货架不该顺手重读世界');
});

test('（stub DOM）店里买书桌：确认弹层 → POST /store/purchases 带原字节目录 id → 回世界出现家具', async () => {
  const dom = await bootApp({snapshot: snapshot()});
  click(findButton(screen(dom), '实体日用店'));
  await settle();

  const deskCard = screen(dom).findAll((node) => node.tagName === 'BUTTON' && String(node.className).includes('shop-card'))
    .find((node) => node.allText.includes('书桌'));
  assert.ok(deskCard, '货架上要有能点开的书桌');
  click(deskCard);
  await settle();

  const detail = screen(dom).allText;
  assert.match(detail, /确认购买/);
  assert.match(detail, /交付方式/);
  assert.match(detail, /付完款不等于已经到货/);

  const buy = findButton(screen(dom), '确认购买');
  assert.equal(buy.disabled, false, 'owner 说能买时不该禁用');
  click(buy);
  await settle();

  const sheetHost = dom.document.getElementById('sheet-host');
  assert.match(sheetHost.allText, /确认在实体店购买/);
  assert.match(sheetHost.allText, /到货之前不能布置/);
  const confirm = sheetHost.find((node) => node.tagName === 'BUTTON' && String(node.className).includes('btn-primary'));
  assert.ok(confirm, '确认弹层要有主按钮');
  click(confirm);
  await settle();

  const sentList = posts(dom, '/store/purchases');
  assert.equal(sentList.length, 1, '一次意图只发一条 POST');
  assert.equal(sentList[0].init.headers['X-CSRF-Token'], 'tok-room');
  const sent = JSON.parse(sentList[0].init.body);
  assert.deepEqual(Object.keys(sent).sort(), ['catalog_item_id', 'operation_key']);
  assert.equal(sent.catalog_item_id, DESK_CATALOG, '目录项必须是原字节');
  assert.equal(sent.operation_key, 'stub-uuid-1');
  assert.equal(posts(dom, '/purchases').length, 0, '实体店的购买不能打到手机线上店的接口');
  /* 回执说 PENDING，界面不能宣称到货 */
  const afterBuy = screen(dom).allText;
  assert.match(afterBuy, /还没到货/);
  assert.match(afterBuy, /pending_arrange|ARRANGE/);

  /* 服务那边家具已经进了房间：回世界必须重读，并且家具卡上出现书桌 */
  dom.responses['/world'] = snapshot();
  click(dom.document.getElementById('btn-home'));
  await settle();
  click(findButton(screen(dom), '房间与布置'));
  await settle();

  const world = screen(dom).allText;
  assert.equal(world.includes('界面错误'), false, `世界屏渲染报错：${world.slice(0, 200)}`);
  assert.match(world, /自己买的家具/);
  assert.match(world, /书桌/);
  assert.ok(gets(dom, '/world').length >= 1, '回世界要重读快照');
});

test('（stub DOM）没到货的家具不能布置：按钮不出现，只有一句原因', async () => {
  const dom = await bootApp({snapshot: snapshot()});
  click(findButton(screen(dom), '房间与布置'));
  await settle();

  /* 家具卡那一条（不是“还在路上”那一条）：它带着“自有”标签 */
  const shelfRow = rowsWith(screen(dom), '小书架').find((node) => node.allText.includes('自有'));
  assert.ok(shelfRow, '家具卡要列出还在配送的小书架');
  assert.match(shelfRow.allText, /还没到货/);
  assert.equal(findButton(shelfRow, '布置：'), undefined, '没到货就不给布置按钮');
  assert.match(shelfRow.allText, /不能提前布置/);

  /* 到货的那两件才给按钮 */
  const deskRow = rowsWith(screen(dom), '书桌').find((node) => node.allText.includes('自有'));
  assert.ok(findButton(deskRow, '布置：放进现在的房间'), '到货且有标识的家具要给布置按钮');
  assert.equal(posts(dom, '/world/actions').length, 0, '只是看一眼不发命令');
});

test('（stub DOM）两张桌子不串：点哪块台面就发哪块 surface_id（原字节）', async () => {
  const dom = await bootApp({snapshot: snapshot()});
  click(findButton(screen(dom), '房间与布置'));
  await settle();

  const deskSurfaceRow = rowsWith(screen(dom), '书桌面').find((node) => node.allText.includes('属于：simple_desk'));
  const bedSurfaceRow = rowsWith(screen(dom), '床面').find((node) => node.allText.includes('属于：basic_bed'));
  assert.ok(deskSurfaceRow && bedSurfaceRow, '两块台面都要出现');
  assert.match(deskSurfaceRow.allText, /服务没有声明这张台面能做什么姿势/);
  assert.match(bedSurfaceRow.allText, /服务声明：能躺/);
  assert.match(bedSurfaceRow.allText, /服务声明：能休息/);
  /* 床面上已经放着那包点心：件数来自 owner 的 children */
  assert.match(bedSurfaceRow.allText, /上面放着 1 件东西/);

  const placeOnDesk = findButton(deskSurfaceRow, '把左手的东西放上去');
  assert.ok(placeOnDesk, '手里有东西时要能放到这块台面上');
  assert.equal(placeOnDesk.disabled, false);
  click(placeOnDesk);
  await settle();

  const sheetHost = dom.document.getElementById('sheet-host');
  assert.match(sheetHost.allText, /书桌面/);
  assert.equal(sheetHost.allText.includes(BED_SURFACE), false, '弹层不泄露其它台面的标识');
  click(sheetHost.find((node) => node.tagName === 'BUTTON' && String(node.className).includes('btn-primary')));
  await settle();

  const sentList = posts(dom, '/world/actions');
  assert.equal(sentList.length, 1, '一次摆放只发一条 POST');
  const sent = JSON.parse(sentList[0].init.body);
  assert.equal(sent.action, 'PLACE');
  assert.equal(sent.params.object_id, SNACK_ID, '手里那件东西的 69 字原 id');
  assert.deepEqual(JSON.parse(JSON.stringify(sent.params.target)),
    {kind: 'surface', location_id: 'home', surface_id: DESK_SURFACE});
  assert.equal(sent.params.target.surface_id.length, 82, '台面 id 是 instance 级的原字节');
  const raw = JSON.stringify(sent);
  assert.equal(raw.includes(BED_SURFACE), false, '请求体里不能出现另一块台面的 id');
  assert.equal(raw.includes(DESK_SURFACE), true);
  /* 屏幕上不显示协议原值 */
  assert.equal(screen(dom).allText.includes(DESK_SURFACE), false);
});

test('（stub DOM）开灯：只发绝对状态 on，回执不宣称身体已变', async () => {
  const dom = await bootApp({snapshot: snapshot()});
  click(findButton(screen(dom), '房间与布置'));
  await settle();

  const lightText = screen(dom).allText;
  assert.match(lightText, /房间的光/);
  assert.match(lightText, /房间光：暗/);
  assert.match(lightText, /灯：关着/);
  assert.match(lightText, /光的来源：catalog:bedside_lamp/);
  assert.match(lightText, /不替身体说话/);

  const onButton = findButton(screen(dom), '开灯');
  assert.ok(onButton, '有灯具标识就要有开灯按钮');
  assert.equal(onButton.disabled, false, '当前 off，开灯可用');
  const offButton = findButton(screen(dom), '关灯');
  assert.equal(offButton.disabled, true, '已经是 off，关灯不发重复写入');

  click(onButton);
  await settle();
  const sheetHost = dom.document.getElementById('sheet-host');
  assert.match(sheetHost.allText, /开着（on）/);
  click(sheetHost.find((node) => node.tagName === 'BUTTON' && String(node.className).includes('btn-primary')));
  await settle();

  const sentList = posts(dom, '/world/actions');
  assert.equal(sentList.length, 1);
  const sent = JSON.parse(sentList[0].init.body);
  assert.equal(sent.action, 'TOGGLE_FIXTURE');
  assert.deepEqual(Object.keys(sent.params).sort(), ['object_id', 'state']);
  assert.equal(sent.params.object_id, LAMP_ID, '灯具 id 原字节');
  assert.equal(sent.params.state, 'on', '绝对状态：on');
  assert.equal(JSON.stringify(sent).includes('toggle'), false);

  const after = screen(dom).allText;
  assert.match(after, /灯现在是开着的/);
  assert.match(after, /灯变了不等于身体变了/);
  assert.equal(/身体已经/.test(after), false, '不能宣称身体已经变了');
});

test('（stub DOM）房间光未知：说明未知，不画成黑的，也没有开关按钮', async () => {
  const dom = await bootApp({
    snapshot: snapshot({room: {light: {status: 'unknown', reason_code: 'NO_DECLARED_LIGHT_IN_ROOM'}}})
  });
  click(findButton(screen(dom), '房间与布置'));
  await settle();

  const text = screen(dom).allText;
  assert.equal(text.includes('界面错误'), false, `世界屏渲染报错：${text.slice(0, 200)}`);
  assert.match(text, /房间光未知/);
  assert.match(text, /没有声明过的灯/);
  assert.match(text, /未知不等于“黑的”/);
  assert.match(text, /没有可切换的灯/);
  assert.equal(text.includes('开灯'), false, '没有灯具标识就不给开灯按钮');
  assert.equal(posts(dom, '/world/actions').length, 0);
  /* 也不能凭空给出一个亮度值 */
  assert.equal(/\b0\s*\/\s*1\b|亮度\s*\d/.test(text), false);
});

test('（stub DOM）动词没被报告：休息按钮保持禁用，台面只引用 owner 声明的那些', async () => {
  const dom = await bootApp({snapshot: snapshot({verbs: []})});
  click(findButton(screen(dom), '房间与布置'));
  await settle();

  const restButton = findButton(screen(dom), '开始休息');
  assert.ok(restButton, '休息面板还在（它属于已有功能）');
  assert.equal(restButton.disabled, true, 'owner 说现在不能休息，按钮必须禁用');

  /* 台面只列 owner 给的两块，界面不编第三块、也不编“全局书桌桌面” */
  const text = screen(dom).allText;
  assert.match(text, /书桌面/);
  assert.equal((text.match(/书桌面/g) || []).length, 1);
  /* 没有 ARRANGE 动词时，家具卡不能给布置按钮 */
  const deskRow = rowsWith(screen(dom), '书桌').find((node) => node.allText.includes('自有'));
  assert.equal(findButton(deskRow, '布置：'), undefined);
  assert.match(deskRow.allText, /服务没有报告/);
  assert.equal(posts(dom, '/world/actions').length, 0);
});

test('（stub DOM）结果未知：保留原操作键，用同一个键查询，不换键重发', async () => {
  const dom = await bootApp({
    snapshot: snapshot(),
    purchase: {ok: true, status: 'unknown', reason_code: 'OWNER_RESULT_UNKNOWN', data: {operation_key: 'stub-key'}}
  });
  dom.responses['/operations/stub-uuid-1'] = {
    ok: true, status: 'applied',
    data: {operation_key: 'stub-uuid-1', catalog_item_id: DESK_CATALOG, item_instance_id: DESK_ID}
  };

  click(findButton(screen(dom), '实体日用店'));
  await settle();
  const deskCard = screen(dom).findAll((node) => node.tagName === 'BUTTON' && String(node.className).includes('shop-card'))
    .find((node) => node.allText.includes('书桌'));
  click(deskCard);
  await settle();
  click(findButton(screen(dom), '确认购买'));
  await settle();
  const sheetHost = dom.document.getElementById('sheet-host');
  click(sheetHost.find((node) => node.tagName === 'BUTTON' && String(node.className).includes('btn-primary')));
  await settle();

  assert.equal(posts(dom, '/store/purchases').length, 1, '未知也只发一次');
  assert.match(screen(dom).allText, /结果未知|没有被确认/);

  const opsQuery = findButton(dom.document.getElementById('unresolved-list'), '查询状态');
  assert.ok(opsQuery, '未知的操作要留在未确认列表里');
  click(opsQuery);
  await settle();

  assert.equal(gets(dom, '/operations/stub-uuid-1').length, 1, '查询要用原来那个键');
  assert.equal(posts(dom, '/store/purchases').length, 1, '查询不能再发一次购买');
  assert.equal(gets(dom, '/world/operations/stub-uuid-1').length, 0, '购物键不走世界查询通道');
});

/* ---------------- 4b. 契约嵌套位置 / 多盏灯 / 布置回执 ---------------- */

test('房间三块从 data.objects 里读（契约 §3.2–3.4），不是要求 C 另开顶层字段', () => {
  const nested = snapshot();
  assert.equal(nested.data.objects.surfaces.status, 'available');
  assert.equal('surfaces' in nested.data, false, '真实快照不该有顶层 surfaces');
  assert.equal('owned_furniture' in nested.data, false);
  assert.equal('deliveries' in nested.data, false);

  const view = P.worldView(nested);
  assert.equal(view.room.surfaces.status, 'available');
  assert.equal(view.room.surfaces.items.length, 2);
  assert.equal(view.room.surfaces.items[0].id, DESK_SURFACE);
  assert.equal(view.room.furniture.items.length, 3);
  assert.equal(view.room.furniture.items[0].id, DESK_ID);
  assert.equal(view.room.deliveries.items.length, 1);
  assert.equal(view.room.deliveries.items[0].state, 'pending');
  assert.equal(view.room.light.status, 'available', 'light 在 local_environment.light');
  assert.equal(view.room.light.power, 'off');

  /* 旧快照兜底：只有顶层同名键时也读得到（不是契约要求的方向）。 */
  const legacy = snapshot();
  legacy.data.surfaces = legacy.data.objects.surfaces;
  legacy.data.owned_furniture = legacy.data.objects.owned_furniture;
  delete legacy.data.objects.surfaces;
  delete legacy.data.objects.owned_furniture;
  const legacyView = P.worldView(legacy);
  assert.equal(legacyView.room.surfaces.items.length, 2);
  assert.equal(legacyView.room.furniture.items.length, 3);
});

test('（stub DOM）两盏灯各自状态：只开关被点的那一盏（owner 逐盏报 power）', async () => {
  const dom = await bootApp({
    snapshot: snapshot({
      room: {
        light: {
          status: 'available', band: 'dim', band_source: 'environment.light.v1',
          power: 'on', fixture_ids: [LAMP2_ID], since: '2026-10-21T20:58:00+09:00',
          on_seconds: 600, declared_source: 'catalog:bedside_lamp', reason_code: null,
          place_id: 'home', area_id: 'bedroom'
        }
      },
      verbs: [
        {verb: 'ARRANGE', available: true, blocked_reason: null, detail: null,
          params: {object_id: 'string', target: 'placement_target(room|surface)'}, candidates: []},
        {verb: 'TOGGLE_FIXTURE', available: true, blocked_reason: null, detail: null,
          params: {object_id: 'string', state: ['on', 'off']},
          candidates: [
            {object_id: LAMP_ID, label: '床头灯', power: 'off', since: null, band: 'dim',
              declared_source: 'catalog:bedside_lamp', place_id: 'home', area_id: 'bedroom'},
            {object_id: LAMP2_ID, label: '小夜灯', power: 'on', since: '2026-10-21T20:58:00+09:00', band: 'dim',
              declared_source: 'catalog:bedside_lamp', place_id: 'home', area_id: 'bedroom'}
          ]}
      ]
    })
  });
  click(findButton(screen(dom), '房间与布置'));
  await settle();

  const rows = screen(dom).findAll((node) => String(node.className || '') === 'room-fixture');
  assert.equal(rows.length, 2, '两盏灯要有两行');
  const offRow = rows.find((row) => row.allText.includes('这盏：关着'));
  const onRow = rows.find((row) => row.allText.includes('这盏：开着'));
  assert.ok(offRow && onRow, '每盏灯各自报开关状态');
  assert.equal(findButton(offRow, '开灯').disabled, false, '关着的那盏可以开');
  assert.equal(findButton(offRow, '关灯').disabled, true, '关着的那盏不需要再关');
  assert.equal(findButton(onRow, '开灯').disabled, true, '开着的那盏不需要再开');
  assert.equal(findButton(onRow, '关灯').disabled, false, '开着的那盏可以关');

  click(findButton(offRow, '开灯'));
  await settle();
  const sheetHost = dom.document.getElementById('sheet-host');
  assert.match(sheetHost.allText, /床头灯/);
  assert.equal(sheetHost.allText.includes(LAMP2_ID), false, '弹层不泄露另一盏的标识');
  click(sheetHost.find((node) => node.tagName === 'BUTTON' && String(node.className).includes('btn-primary')));
  await settle();

  const sentList = posts(dom, '/world/actions');
  assert.equal(sentList.length, 1);
  const sent = JSON.parse(sentList[0].init.body);
  assert.equal(sent.action, 'TOGGLE_FIXTURE');
  assert.deepEqual(Object.keys(sent.params).sort(), ['object_id', 'state']);
  assert.equal(sent.params.object_id, LAMP_ID, '只开被点的那一盏');
  assert.equal(sent.params.state, 'on');
  assert.equal(JSON.stringify(sent).includes(LAMP2_ID), false);
});

test('（stub DOM）布置回执读 data.arranged：目标与跟着一起搬的子物件', async () => {
  const dom = await bootApp({
    snapshot: snapshot(),
    worldAction: {
      ok: true,
      status: 'applied',
      data: {
        execution_id: 'phone-exec:arrange-1',
        action: 'ARRANGE',
        status: 'applied',
        reason_code: 'arrange_committed',
        arranged: {
          object_id: DESK_ID,
          target: {kind: 'room', location_id: 'home', area_id: 'bedroom'},
          carried: [SNACK_ID],
          surface_ids: [DESK_SURFACE]
        },
        body: {applied_to_body: false},
        world_mutation_refs: ['world.placement=' + DESK_ID]
      }
    }
  });
  click(findButton(screen(dom), '房间与布置'));
  await settle();
  const deskRow = rowsWith(screen(dom), '书桌').find((node) => node.allText.includes('自有'));
  click(findButton(deskRow, '布置：放进现在的房间'));
  await settle();

  const sheetHost = dom.document.getElementById('sheet-host');
  click(sheetHost.find((node) => node.tagName === 'BUTTON' && String(node.className).includes('btn-primary')));
  await settle();

  const sentList = posts(dom, '/world/actions');
  assert.equal(sentList.length, 1);
  const sent = JSON.parse(sentList[0].init.body);
  assert.equal(sent.action, 'ARRANGE');
  assert.deepEqual(Object.keys(sent.params).sort(), ['object_id', 'target']);
  assert.equal(sent.params.object_id, DESK_ID);

  const after = screen(dom).allText;
  assert.match(after, /home \/ bedroom/, '回执里的目标位置要照实显示');
  assert.match(after, /跟着一起搬的还有 1 件/);
  assert.match(after, /摆好了不等于现在能用/);
});

/* ---------------- 5. 静态与结构约束（不是浏览器验收） ---------------- */

test('界面结构：两屏两入口、无 innerHTML、技术信息在 details 里', () => {
  assert.match(html, /<script src="app\.js" defer><\/script>/);
  assert.doesNotMatch(html, /(?:src|href)="https?:\/\//i);
  for (const banned of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function']) {
    assert.equal(src.includes(banned), false, `app.js 不能使用 ${banned}`);
  }
  assert.match(src, /store: \{render: renderStore\}/);
  assert.match(src, /'store-item': \{render: renderStoreItem\}/);
  assert.match(src, /store: '\/store'/);
  assert.match(src, /h\('details', \{class: 'tech-details'\}/);
  /* 技术详情里给房间排错事实，但不放坐标 */
  assert.match(src, /push\('light_band'/);
  assert.match(src, /push\('surfaces_count'/);
  assert.equal(/fakeCoord|gridX|tileX|rowIndex/.test(src), false);
});

test('390 宽度：新类都有窄屏规则与换行保护（静态检查，不是像素验证）', () => {
  /* 新类必须真的在样式表里有定义，别让结构裸奔 */
  for (const cls of ['channel-card', 'channel-title', 'channel-note', 'home-shortcuts', 'room-fixture', 'room-fixture-name']) {
    assert.equal(css.includes('.' + cls), true, `样式表缺少 .${cls}`);
    assert.equal(src.includes(cls), true, `app.js 没用到 .${cls}`);
  }
  /* 窄屏（≤430px，覆盖 390）里这几类必须有专门规则 */
  const narrow = css.slice(css.indexOf('@media (max-width: 430px)'));
  assert.match(narrow, /\.channel-card \{/);
  assert.match(narrow, /\.room-fixture \{ flex-direction: column/);
  assert.match(narrow, /\.home-shortcuts \.btn \{/);
  assert.match(narrow, /\.screen-body \{ padding: 0 14px 18px; \}/);
  /* 溢出保护：新块都是 min-width:0 + 可换行，没有写死的大于 390 的宽度 */
  const block = css.slice(css.indexOf('.channel-card {'), css.indexOf('/* ---------- 窄屏：手机全屏'));
  assert.match(block, /min-width: 0;/);
  assert.match(block, /overflow-wrap: anywhere;/);
  const widths = [...block.matchAll(/(?:^|[^-])width:\s*(\d+)px/g)].map((hit) => Number(hit[1]));
  assert.deepEqual(widths, [], `新增样式块不该写死 px 宽度：${widths.join(',')}`);
  assert.match(css, /overflow-wrap: anywhere/);
});
