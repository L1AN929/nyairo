/**
 * NyAiro v0.4 · 阶段 B 窄修回归：**主屏直达身体**（全新页面 → 身体磁贴 → POSE/REST 真的出现）。
 *
 * 父级 2026-11-15 在真实浏览器里遇到的缺陷：全新打开的手机页面，主屏直接点「身体」时只渲染
 * `/api/phone/state` 的 body 投影，`/api/phone/world` 从来没被请求过，于是身体屏没有
 * POSE（站起/坐下/躺下）与 REST（开始/结束休息）按钮；而且一旦 `/world` 读过一次失败，
 * 缓存里留着 error/offline 条目，之后从主屏再点身体也永远不会重发请求（永久堵死）。
 *
 * 本文件走的是**真实 DOM 路径**，不是纯函数：
 *   1. 用最小 DOM 桩（createElement/createElementNS/append/prepend/replaceChildren/
 *      addEventListener/textContent/dataset/setAttribute）把 `components/phone/web/app.js`
 *      整份跑起来（它会自己 boot()、自己发 GET /api/phone/state）；
 *   2. 从**渲染出来的 DOM 树**里找到主屏的「身体」磁贴并真的点它的 click 监听；
 *   3. 只看真实发出的请求（fetch 被替换成记录器）与真实子节点文本。
 *
 * 不启动手机服务、不联网、不跑浏览器、不消费任何 owner 数据。
 *
 * 运行：node --test tests/test_v04_phone_sharing_body_entry.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const WEB = new URL('../components/phone/web/', import.meta.url);
const src = readFileSync(new URL('app.js', WEB), 'utf8');

/* ------------------------------------------------------------------ *
 * 最小 DOM 桩：够 app.js 的渲染面用，不多给一点能力
 * ------------------------------------------------------------------ */

function walk(node, out = []) {
  if (!node) return out;
  out.push(node);
  (node.children || []).forEach((child) => walk(child, out));
  return out;
}

function textOf(node) {
  if (!node) return '';
  const own = typeof node._text === 'string' ? node._text : '';
  const kids = (node.children || []).map((child) => textOf(child)).join('');
  return own + kids;
}

function makeNode(tag) {
  const node = {
    tagName: String(tag).toUpperCase(),
    nodeType: 1,
    className: '',
    dataset: {},
    attrs: {},
    listeners: {},
    children: [],
    _text: '',
    disabled: false,
    value: '',
    scrollTop: 0
  };
  Object.defineProperty(node, 'textContent', {
    get() { return textOf(node); },
    set(value) { node._text = String(value); node.children = []; }
  });
  node.setAttribute = function setAttribute(key, value) { node.attrs[key] = String(value); };
  node.getAttribute = function getAttribute(key) {
    return Object.prototype.hasOwnProperty.call(node.attrs, key) ? node.attrs[key] : null;
  };
  node.addEventListener = function addEventListener(name, fn) {
    (node.listeners[name] = node.listeners[name] || []).push(fn);
  };
  node.append = function append(child) { if (child) node.children.push(child); };
  node.appendChild = function appendChild(child) { node.append(child); return child; };
  node.prepend = function prepend(child) { if (child) node.children.unshift(child); };
  node.replaceChildren = function replaceChildren() {
    node.children = Array.prototype.slice.call(arguments).filter(Boolean);
  };
  node.querySelector = function querySelector() { return null; };
  node.focus = function focus() { /* 测试里不需要焦点 */ };
  /* 真实点击：把 click 监听按顺序跑一遍（btn() 自己挡 disabled，这里不代劳）。 */
  node.click = function click() {
    (node.listeners.click || []).forEach((fn) => fn({type: 'click', target: node}));
  };
  return node;
}

function makeTextNode(text) {
  return {nodeType: 3, tagName: '#TEXT', _text: String(text), children: []};
}

function makeDocument() {
  const byId = new Map();
  const doc = {
    hidden: false,
    nodeType: 9,
    addEventListener: function addEventListener() { /* keydown / visibilitychange 不参与本回归 */ },
    createElement: function createElement(tag) { return makeNode(tag); },
    createElementNS: function createElementNS(ns, tag) { return makeNode(tag); },
    createTextNode: function createTextNode(text) { return makeTextNode(text); },
    getElementById: function getElementById(id) {
      if (!byId.has(id)) byId.set(id, makeNode('div'));
      return byId.get(id);
    }
  };
  return {doc, byId};
}

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => { data.set(key, String(value)); },
    removeItem: (key) => { data.delete(key); }
  };
}

/* ------------------------------------------------------------------ *
 * 服务文档：只写这条回归真正读到的字段
 * ------------------------------------------------------------------ */

const API = '/api/phone';

/** /api/phone/state：真的报 body 这个 app（父级实例就是这样），world 可选。 */
function stateDoc(options = {}) {
  const apps = {
    notes: {status: 'available', data: {}},
    references: {status: 'available', data: {}},
    reading: {status: 'available', data: {}},
    wallet: {status: 'available', data: {}},
    shop: {status: 'available', data: {}},
    inventory: {status: 'available', data: {}},
    body: {
      status: 'available',
      data: {
        body_status: 'available',
        body: {
          status: 'available',
          pose: 'standing',
          orientation: 'north',
          pose_source: 'world_state',
          bands_only: true,
          raw_values_exposed: false,
          signals_status: 'available',
          signals: [{signal_type: 'fatigue', qualitative_band: 'mild'}],
          recovery: {state: 'inactive', mode: 'rest', trend: 'stable', progress_band: 'none'}
        }
      }
    }
  };
  if (options.worldApp !== false) apps.world = {status: 'available', data: {}};
  return {
    ok: true,
    status: 'ok',
    read_only: false,
    csrf_token: 'csrf-body-entry-token',
    instance: {name: 'body-entry', subject: 'owner:test', mode: 'local'},
    apps: apps
  };
}

/** /api/phone/world：owner 说 POSE 与 REST 都能用（足够让按钮真的出现）。 */
function worldDoc() {
  return {
    ok: true,
    status: 'applied',
    kind: 'world_phone_world',
    schema_version: 'world.phone.world.v1',
    data: {
      world: {
        status: 'available', source: 'world_state', world_id: 'shiomi_city', revision: 7,
        timezone: 'Asia/Tokyo', scene_id: 'home.bedroom',
        local_time: '2026-10-21T09:12:00+09:00', local_date: '2026-10-21', weekday: 3
      },
      location: {
        status: 'available', source: 'world_state', node: 'home/bedroom',
        place_id: 'home', area_id: 'bedroom', name: '家', kind: 'home', home: true
      },
      local_environment: {
        status: 'available', source: 'world_package_scene',
        building: {place_id: 'home', name: '家'},
        area: {area_id: 'bedroom', name: '卧室', kind: 'room'},
        furniture: [], furniture_total: 0, adjacent_areas: [], note: ''
      },
      destinations: {
        status: 'available', source: 'world_daily_opportunities', scope: 'NOW_ONLY',
        current_node: 'home/bedroom', walk_seconds_max: 1800, places: [], nodes: []
      },
      travel: {status: 'missing'},
      body: {
        status: 'available', source: 'world_state', body_id: 'body:local',
        pose: 'standing', orientation: 'north', pose_source: 'world_state',
        bands_only: true, raw_values_exposed: false,
        signals_status: 'available', signals_source: 'world_body_substrate',
        signals: [{signal_type: 'fatigue', qualitative_band: 'mild'}],
        recovery: {state: 'inactive', mode: 'rest', trend: 'stable', progress_band: 'none'},
        rest_evidence: '', note: ''
      },
      actions: {
        status: 'available', source: 'world_body_gateway_api',
        local_owner_verbs: ['POSE', 'REST'],
        verbs: [
          {verb: 'POSE', available: true, blocked_reason: '', params: {}, current: {pose: 'standing'}},
          {
            verb: 'REST', available: true, blocked_reason: '', params: {},
            start_available: true, stop_available: false, sleep_like_available: true,
            current: {pose: 'standing', recovery_state: 'inactive', travelling: false}
          }
        ]
      },
      recent_executions: {status: 'missing'}
    }
  };
}

/* ------------------------------------------------------------------ *
 * 把 app.js 整份跑起来（它会自己 boot）
 * ------------------------------------------------------------------ */

function bootPhone(options = {}) {
  const {doc, byId} = makeDocument();
  const calls = [];
  const attempts = new Map();
  const routes = options.routes || {};
  const storage = memoryStorage();
  Object.keys(options.seedStorage || {}).forEach(function (key) {
    storage.setItem(key, options.seedStorage[key]);
  });

  const context = {
    document: doc,
    sessionStorage: storage,
    crypto: {randomUUID: () => 'uuid-body-entry-' + (calls.length + 1)},
    setInterval: () => 0,
    clearInterval: () => {},
    setTimeout: () => 0,
    clearTimeout: () => {},
    console: console,
    fetch: async (url, init) => {
      const path = String(url);
      const method = (init && init.method) || 'GET';
      calls.push({path: path, method: method});
      const attempt = (attempts.get(path) || 0) + 1;
      attempts.set(path, attempt);
      const handler = routes[path];
      if (typeof handler !== 'function') {
        throw new TypeError('unexpected request ' + method + ' ' + path);
      }
      const answer = handler(attempt);
      if (!answer || answer.network === true) throw new TypeError('network down');
      return {
        ok: answer.status >= 200 && answer.status < 300,
        status: answer.status,
        text: async () => JSON.stringify(answer.body)
      };
    }
  };
  vm.createContext(context);
  vm.runInContext(src, context, {filename: 'components/phone/web/app.js'});

  const screenBody = byId.get('screen-body');
  const screenHead = byId.get('screen-head');

  const phone = {
    ctx: context,
    pure: context.NyairoPhone,
    calls: calls,
    byId: byId,
    screenBody: screenBody,
    screenHead: screenHead,
    paths: () => calls.map((entry) => entry.path),
    count: (path) => calls.filter((entry) => entry.path === path).length,
    nodes: () => walk(screenBody),
    texts: () => textOf(screenBody),
    buttons: () => walk(screenBody).filter((node) => node.tagName === 'BUTTON'),
    buttonByText: (needle) => walk(screenBody).find((node) => node.tagName === 'BUTTON' && textOf(node).includes(needle)) || null,
    /* 主屏磁贴：appTile 用 aria-label = 应用名 + '：' + 状态。 */
    tile: (label) => walk(screenBody).find((node) => node.tagName === 'BUTTON' &&
      String(node.attrs['aria-label'] || '').indexOf(label + '：') === 0) || null,
    chrome: {
      back: byId.get('btn-back'),
      home: byId.get('btn-home'),
      refresh: byId.get('btn-refresh')
    }
  };
  return phone;
}

/** 等所有 await 链落定（app.js 里读取是 async，没有真实定时器）。 */
async function settle(times = 24) {
  for (let i = 0; i < times; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

/* ------------------------------------------------------------------ *
 * 1. 全新页面 → 主屏点身体 → 真的去读 /world，POSE/REST 按钮出现
 * ------------------------------------------------------------------ */

test('全新页面主屏点「身体」会真的读 /world，并出现 POSE/REST 按钮', async () => {
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/world']: () => ({status: 200, body: worldDoc()})
    }
  });
  await settle();

  /* boot 只读 /state：主屏不预取世界（这条是背景，不是缺陷）。 */
  assert.deepEqual(phone.paths(), [API + '/state'],
    '全新页面应该只发 GET /state，实际：' + JSON.stringify(phone.paths()));

  const tile = phone.tile('身体');
  assert.ok(tile, '主屏应该渲染出「身体」磁贴；现有按钮：' +
    JSON.stringify(phone.buttons().map((b) => String(b.attrs['aria-label'] || textOf(b)))));

  tile.click();
  await settle();

  /* 关键断言 1：点身体之后 /world 真的被请求（不依赖先去世界屏绕一圈）。 */
  assert.equal(phone.count(API + '/world'), 1,
    '点「身体」必须发一次 GET /world，实际请求：' + JSON.stringify(phone.paths()));

  /* 关键断言 2：屏幕上是来自世界快照的投影，不是只有 /state 投影。 */
  assert.ok(phone.texts().includes('来自世界快照'),
    '身体屏应该标「来自世界快照」，实际：' + phone.texts());

  /* 关键断言 3：POSE 面板真的在 DOM 里，且坐下/躺下可点（owner 说 POSE 可用）。 */
  const posePanel = phone.nodes().find((node) => node.tagName === 'H2' && textOf(node) === '姿势');
  assert.ok(posePanel, '身体屏应该有「姿势」面板；实际：' + JSON.stringify(phone.texts()));
  const seated = phone.buttonByText('坐着');
  assert.ok(seated, '身体屏应该有「坐着」按钮（坐下命令），实际按钮：' +
    JSON.stringify(phone.buttons().map((node) => textOf(node))));
  assert.equal(seated.disabled, false, 'owner 说 POSE 可用时「坐着」不该被禁用');

  /* 关键断言 4：REST 的开始/睡着式按钮在 DOM 里，且开始休息可点。 */
  const startRest = phone.buttonByText('开始休息');
  assert.ok(startRest, '身体屏应该有「开始休息」按钮（REST 命令）');
  assert.equal(startRest.disabled, false, 'owner 说 start_available 时「开始休息」不该被禁用');
  assert.ok(phone.buttonByText('躺下睡一会'), '身体屏应该有 REST 的睡着式按钮');
  assert.ok(phone.buttonByText('结束休息'), '身体屏应该有「结束休息」按钮');

  /* 关键断言 5：已经读到的快照不重复请求（重试只针对失败，不是每次都重发）。 */
  phone.chrome.back.click();
  await settle();
  phone.tile('身体').click();
  await settle();
  assert.equal(phone.count(API + '/world'), 1,
    'world 已经读到之后再进身体屏不该重复请求（实际：' + JSON.stringify(phone.paths()) + '）');
});

/* ------------------------------------------------------------------ *
 * 2. /world 读失败（HTTP 错误）之后不是永久堵死：退回主屏再点身体会重发
 * ------------------------------------------------------------------ */

test('world 读失败后从主屏再点身体会重发 GET /world（error 不留在缓存里堵死）', async () => {
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/world']: (attempt) => (attempt === 1
        ? {status: 503, body: {ok: false, status: 'unavailable', reason_code: 'WORLD_UNAVAILABLE'}}
        : {status: 200, body: worldDoc()})
    }
  });
  await settle();

  phone.tile('身体').click();
  await settle();

  assert.equal(phone.count(API + '/world'), 1, '第一次点身体应该发一次 /world');
  /* 失败时不许假装有世界：没有 POSE/REST 面板，而要给重试入口。 */
  assert.ok(!phone.buttonByText('坐着'), '世界没读到时不该出现 POSE 按钮');
  assert.ok(!phone.buttonByText('开始休息'), '世界没读到时不该出现 REST 按钮');
  assert.ok(phone.texts().includes('世界快照这次没读到'),
    '世界读失败要照实说；实际：' + phone.texts());

  /* 退回主屏，再从主屏点身体：缓存里的 error 条目必须触发新的读取。 */
  phone.chrome.back.click();
  await settle();
  const tileAgain = phone.tile('身体');
  assert.ok(tileAgain, '退回主屏后应该还有「身体」磁贴');
  tileAgain.click();
  await settle();

  assert.equal(phone.count(API + '/world'), 2,
    '失败之后再点身体必须重发 GET /world（实际：' + JSON.stringify(phone.paths()) + '）');
  assert.ok(phone.buttonByText('坐着'), '重试读到世界后应该出现 POSE 按钮');
  assert.ok(phone.buttonByText('开始休息'), '重试读到世界后应该出现 REST 按钮');
  assert.ok(phone.texts().includes('来自世界快照'), '重试成功后投影应该来自世界快照');
});

/* ------------------------------------------------------------------ *
 * 3. /world 读失败（连不上）之后，屏上的「重新读取世界快照」按钮真的重发
 * ------------------------------------------------------------------ */

test('world 连不上时身体屏的「重新读取世界快照」会真的重发 GET /world', async () => {
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/world']: (attempt) => (attempt === 1
        ? {network: true}
        : {status: 200, body: worldDoc()})
    }
  });
  await settle();

  phone.tile('身体').click();
  await settle();

  assert.equal(phone.count(API + '/world'), 1, '第一次点身体应该发一次 /world');
  const retry = phone.buttonByText('重新读取世界快照');
  assert.ok(retry, '世界连不上时身体屏要给「重新读取世界快照」按钮；实际按钮：' +
    JSON.stringify(phone.buttons().map((node) => textOf(node))));

  retry.click();
  await settle();

  assert.equal(phone.count(API + '/world'), 2,
    '点「重新读取世界快照」必须重发 GET /world（实际：' + JSON.stringify(phone.paths()) + '）');
  assert.ok(phone.buttonByText('坐着'), '重试读到世界后应该出现 POSE 按钮');
  assert.ok(phone.buttonByText('开始休息'), '重试读到世界后应该出现 REST 按钮');
});

/* ------------------------------------------------------------------ *
 * 4. /state 没报告 world 这个 app（老实例）时也要去读 /world
 * ------------------------------------------------------------------ */

test('/state 没报告 world 时，主屏点身体仍然会去读 /world', async () => {
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc({worldApp: false})}),
      [API + '/world']: () => ({status: 200, body: worldDoc()})
    }
  });
  await settle();

  phone.tile('身体').click();
  await settle();

  assert.equal(phone.count(API + '/world'), 1,
    '/state 没报 world 也要读一次 /world，由服务自己回答（实际：' + JSON.stringify(phone.paths()) + '）');
  assert.ok(phone.buttonByText('坐着'), '读到世界后应该出现 POSE 按钮');
});

/* ------------------------------------------------------------------ *
 * 5. 浏览器直达 / 刷新之后恢复在身体屏（sessionStorage 里记着 nav）：也要读 /world
 * ------------------------------------------------------------------ */

test('浏览器直达身体屏（恢复的 nav 就是 body）也要读 /world 并出 POSE/REST 按钮', async () => {
  const phone = bootPhone({
    seedStorage: {'nyairo.phone.nav.v1': JSON.stringify({screen: 'body', detail: ''})},
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/world']: () => ({status: 200, body: worldDoc()})
    }
  });
  await settle();

  assert.ok(phone.paths().indexOf(API + '/world') >= 0,
    '恢复在身体屏时 boot 之后必须读一次 /world（实际：' + JSON.stringify(phone.paths()) + '）');
  assert.equal(phone.count(API + '/world'), 1,
    '身体屏恢复时 /world 只读一次，不要叠加请求（实际：' + JSON.stringify(phone.paths()) + '）');
  assert.ok(phone.buttonByText('坐着'), '直达身体屏时应该直接有 POSE 按钮；实际按钮：' +
    JSON.stringify(phone.buttons().map((node) => textOf(node))));
  assert.ok(phone.buttonByText('开始休息'), '直达身体屏时应该直接有 REST 按钮');
});

/* ------------------------------------------------------------------ *
 * 6. 纯函数面：worldSnapshotNeedsLoad 的判据（只有 loading/ready 才不重发）
 * ------------------------------------------------------------------ */

test('worldSnapshotNeedsLoad：只有「正在读」和「读到了」不重发，失败态一律重试', () => {
  const phone = bootPhone({
    routes: {[API + '/state']: () => ({status: 200, body: stateDoc()})}
  });
  const needs = phone.pure.worldSnapshotNeedsLoad;
  assert.equal(typeof needs, 'function', 'worldSnapshotNeedsLoad 应该被导出');

  assert.equal(needs(undefined), true, '没有条目要读');
  assert.equal(needs(null), true, '条目为 null 要读');
  assert.equal(needs({phase: 'error', message: 'x'}), true, 'error 条目必须重试（这是本轮缺陷）');
  assert.equal(needs({phase: 'offline'}), true, 'offline 条目必须重试');
  assert.equal(needs({}), true, '没有 phase 的条目必须重试');
  assert.equal(needs({phase: 'loading'}), false, '正在读时不要叠加请求');
  assert.equal(needs({phase: 'ready', doc: {}}), false, '已经读到就不要重复读');
});
