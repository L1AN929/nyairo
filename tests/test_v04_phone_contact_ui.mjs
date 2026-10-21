/**
 * NyAiro v0.4 · 阶段 C：主屏「来访与共同时间」的真实 DOM 路径。
 *
 * 这个文件走的是**真实 DOM 路径**，不是纯函数：
 *   1. 用最小 DOM 桩（createElement / createElementNS / append / prepend /
 *      replaceChildren / addEventListener / textContent / dataset / setAttribute）
 *      把 components/phone/web/app.js 整份跑起来（它自己 boot()、自己发 GET /state）；
 *   2. 从**渲染出来的 DOM 树**里找主屏的「来访与共同时间」入口，真的点它的 click 监听；
 *   3. 只看真实发出的请求（fetch 被替换成记录器）与真实子节点文本。
 *
 * 要守住的四条：
 *   * 普通来话 / 明确邀请 / 显式结束 三条路都从主屏进得去，而且各自真的发一次 POST；
 *   * **queued 不等于 accepted**：屏上不许出现「她接受了」，必须写「已记下」；
 *   * 没有邀请时不给假的「结束」按钮；有邀请时才给；
 *   * 点刷新 / 重进这一屏才重读，绝不秒级轮询；正文不写进 sessionStorage。
 *
 * 运行：node --test tests/test_v04_phone_contact_ui.mjs
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
  node.click = function click() {
    (node.listeners.click || []).forEach((fn) => fn({type: 'click', target: node}));
  };
  /* 输入框：测试里用 input 事件模拟用户打字。 */
  node.type = function type(value) {
    node.value = String(value);
    (node.listeners.input || []).forEach((fn) => fn({type: 'input', target: node}));
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
    data: data,
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => { data.set(key, String(value)); },
    removeItem: (key) => { data.delete(key); }
  };
}

/* ------------------------------------------------------------------ *
 * 服务文档：只写这条回归真正读到的字段
 * ------------------------------------------------------------------ */

const API = '/api/phone';

function stateDoc() {
  return {
    ok: true,
    status: 'ok',
    read_only: false,
    csrf_token: 'csrf-contact-token',
    instance: {name: 'contact-entry', subject: 'chiyo', mode: 'local'},
    apps: {
      notes: {status: 'available', data: {}},
      references: {status: 'available', data: {}},
      reading: {status: 'available', data: {}},
      wallet: {status: 'available', data: {}},
      shop: {status: 'available', data: {}},
      inventory: {status: 'available', data: {}},
      body: {status: 'available', data: {}},
      world: {status: 'available', data: {}},
      contact: {status: 'available', data: {}}
    }
  };
}

const INVITATION_ID = 'localinvite:0123456789abcdef';
const EVENT_REF = 'contact_evt:0123456789abcdef';

/** /contact：计数 + 有界行 + B 的只读会话。 */
function contactDoc(options = {}) {
  const rows = options.rows || [
    {
      event_ref: EVENT_REF, seq: 1, event_kind: 'CO_PRESENCE_INVITATION',
      effect: 'INVITATION', processing_state: 'PENDING', outcome: '',
      occurred_at: '2026-11-20T10:00:00+00:00', received_at: '2026-11-20T10:00:01+00:00',
      body_chars: 3, body_stored: true, body_included: false, scope: '一起坐十分钟',
      expired: false, invitation_id: INVITATION_ID, invitation_bound: false,
      attempts: 0, claimed_by: '', pause_requested: false, activity_started: false
    }
  ];
  return {
    ok: true,
    status: 'ok',
    data: {
      counts: {PENDING: rows.length, CLAIMED: 0, DEFERRED: 0, UNKNOWN: 0, PROCESSED: 0},
      by_kind: {LOCAL_MESSAGE: 0, CO_PRESENCE_INVITATION: rows.length, CO_PRESENCE_END: 0},
      event_count: rows.length,
      capacity: 200,
      ledger_state: 'OK',
      events: rows,
      events_ok: true,
      events_reason_code: '',
      truncated: false,
      session: options.session || {
        state: 'NONE', readable: true, open: false, invitation_id: '',
        scope: '', opened_at: '', ended_at: '', end_reason: '', joint_activity_id: ''
      },
      subject: 'chiyo', home_bound: true, home_source: 'environment:CHIYO_WORLD_HOME',
      channel: 'local_inbox', source: 'local_operator',
      private_text_included: false, external_send: false, delivered_to_user: false,
      note: '只进这台实例自己的本地收件箱，没有发到任何外部渠道，也不等于用户已收到',
      queued_note: 'queued（已记下、还没处理）不等于她答应了；接纳与否由她自己的选择决定'
    }
  };
}

/** POST /contact/events 的成功信封：queued，且 accepted 恒为 false。 */
function deliveredDoc(body) {
  const kind = body.event_kind;
  return {
    ok: true,
    status: 'applied',
    data: {
      event_ref: EVENT_REF,
      operation_key: body.operation_key,
      event: {
        event_ref: EVENT_REF, seq: 1, event_kind: kind,
        effect: kind === 'LOCAL_MESSAGE' ? 'OBSERVATION_ONLY'
          : (kind === 'CO_PRESENCE_INVITATION' ? 'INVITATION' : 'END'),
        processing_state: 'PENDING', outcome: '', body_chars: 3, body_stored: true,
        body_included: false, scope: body.scope || '', expired: false,
        invitation_id: body.invitation_id || INVITATION_ID, invitation_bound: false,
        attempts: 0, claimed_by: '', pause_requested: false, activity_started: false
      },
      event_kind: kind,
      effect: kind === 'LOCAL_MESSAGE' ? 'OBSERVATION_ONLY' : 'INVITATION',
      processing_state: 'PENDING',
      queued: true, accepted: false, pause_requested: false, activity_started: false,
      owners_written: 0, replayed: false, created: true, local_only: true,
      note: '只进这台实例自己的本地收件箱，没有发到任何外部渠道，也不等于用户已收到',
      queued_note: '已记下，还没处理：这不是她答应了，也不是共同时间已经开始'
    }
  };
}

/* ------------------------------------------------------------------ *
 * 把 app.js 整份跑起来（它会自己 boot）
 * ------------------------------------------------------------------ */

function bootPhone(options = {}) {
  const {doc, byId} = makeDocument();
  const calls = [];
  const routes = options.routes || {};
  const storage = memoryStorage();
  Object.keys(options.seedStorage || {}).forEach(function (key) {
    storage.setItem(key, options.seedStorage[key]);
  });

  const context = {
    document: doc,
    sessionStorage: storage,
    crypto: {randomUUID: () => '12345678-1234-4234-9234-' + String(calls.length + 1).padStart(12, '0')},
    setInterval: () => 0,
    clearInterval: () => {},
    setTimeout: () => 0,
    clearTimeout: () => {},
    console: console,
    fetch: async (url, init) => {
      const path = String(url);
      const method = (init && init.method) || 'GET';
      let parsed = null;
      if (init && typeof init.body === 'string' && init.body) {
        try { parsed = JSON.parse(init.body); } catch (error) { parsed = null; }
      }
      calls.push({path: path, method: method, body: parsed,
        csrf: init && init.headers ? init.headers['X-CSRF-Token'] : null});
      const handler = routes[path];
      if (typeof handler !== 'function') {
        throw new TypeError('unexpected request ' + method + ' ' + path);
      }
      const answer = handler(parsed);
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
    storage: storage,
    calls: calls,
    byId: byId,
    screenBody: screenBody,
    screenHead: screenHead,
    paths: () => calls.map((entry) => entry.path),
    count: (path) => calls.filter((entry) => entry.path === path).length,
    posts: () => calls.filter((entry) => entry.method === 'POST'),
    nodes: () => walk(screenBody),
    texts: () => textOf(screenBody),
    buttons: () => walk(screenBody).filter((node) => node.tagName === 'BUTTON'),
    buttonByText: (needle) => walk(screenBody)
      .find((node) => node.tagName === 'BUTTON' && textOf(node).includes(needle)) || null,
    textareas: () => walk(screenBody).filter((node) => node.tagName === 'TEXTAREA'),
    inputs: () => walk(screenBody).filter((node) => node.tagName === 'INPUT'),
    selects: () => walk(screenBody).filter((node) => node.tagName === 'SELECT'),
    /* 主屏的直达入口是行内按钮（不是磁贴）。 */
    shortcut: (label) => walk(screenBody)
      .find((node) => node.tagName === 'BUTTON' && textOf(node).trim() === label) || null,
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
 * 1. 主屏直达「来访与共同时间」→ 真的发一次 GET /contact
 * ------------------------------------------------------------------ */

test('主屏「来访与共同时间」入口会真的读 /contact，并渲染三条投递入口', async () => {
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/contact']: () => ({status: 200, body: contactDoc()})
    }
  });
  await settle();

  assert.deepEqual(phone.paths(), [API + '/state'],
    '全新页面应该只发 GET /state，实际：' + JSON.stringify(phone.paths()));

  const entry = phone.shortcut('来访与共同时间');
  assert.ok(entry, '主屏应该渲染出「来访与共同时间」入口；现有按钮：' +
    JSON.stringify(phone.buttons().map((node) => textOf(node))));

  entry.click();
  await settle();

  assert.equal(phone.count(API + '/contact'), 1,
    '点进来访屏必须发一次 GET /contact，实际：' + JSON.stringify(phone.paths()));
  assert.ok(phone.texts().includes('一条普通来话'), '屏上应该有「一条普通来话」入口');
  assert.ok(phone.texts().includes('一次明确邀请'), '屏上应该有「一次明确邀请」入口');
  assert.ok(phone.texts().includes('共同时间'), '屏上应该有共同时间面板');
});

/* ------------------------------------------------------------------ *
 * 2. queued ≠ accepted：投一条普通来话，屏上不许出现「她接受了」
 * ------------------------------------------------------------------ */

test('投一条普通来话：POST 真的发出，屏上只写「已记下」，绝不写「她接受了」', async () => {
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/contact']: () => ({status: 200, body: contactDoc()}),
      [API + '/contact/events']: (body) => ({status: 200, body: deliveredDoc(body)})
    }
  });
  await settle();
  phone.shortcut('来访与共同时间').click();
  await settle();

  const area = phone.textareas()[0];
  assert.ok(area, '普通来话要有输入框；现有节点：' + phone.texts());
  area.type('我来啦');

  const send = phone.buttonByText('投一条普通来话');
  assert.ok(send, '应该有「投一条普通来话」按钮；现有按钮：' +
    JSON.stringify(phone.buttons().map((node) => textOf(node))));
  assert.equal(send.disabled, false, '已连上且能写时按钮不该被禁用');
  send.click();
  await settle();

  const posts = phone.posts();
  assert.equal(posts.length, 1, '必须真的发一次 POST，实际：' + JSON.stringify(posts));
  assert.equal(posts[0].path, API + '/contact/events');
  assert.equal(posts[0].body.event_kind, 'LOCAL_MESSAGE');
  assert.equal(posts[0].body.text, '我来啦');
  assert.ok(posts[0].body.operation_key, '每次投递都要带自己的操作键');
  assert.match(posts[0].body.operation_key, /^contact_op:[A-Za-z0-9_.:-]+$/, '浏览器UUID必须在生成时变成端口接受的原键');
  assert.equal(posts[0].csrf, 'csrf-contact-token', '写必须带 CSRF 令牌');

  /* 关键：屏幕上不许出现「她接受了」这种替她回答的话。 */
  const surface = phone.texts();
  assert.ok(!surface.includes('她接受了'), 'queued 不许写成她接受了；实际：' + surface);
  assert.ok(surface.includes('已记下') || surface.includes('界面没有替她接纳'),
    '屏上应该照实写「已记下 / 没有替她接纳」；实际：' + surface);
  assert.ok(surface.includes('投递') || surface.includes('已生效'),
    '结果卡要写这次是投递；实际：' + surface);
});

/* ------------------------------------------------------------------ *
 * 3. 明确邀请：scope 是必填，不填就不许发
 * ------------------------------------------------------------------ */

test('明确邀请没写范围时：不发请求，给出人读的错', async () => {
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/contact']: () => ({status: 200, body: contactDoc({rows: []})}),
      [API + '/contact/events']: (body) => ({status: 200, body: deliveredDoc(body)})
    }
  });
  await settle();
  phone.shortcut('来访与共同时间').click();
  await settle();

  const invite = phone.buttonByText('投一次明确邀请');
  assert.ok(invite, '应该有「投一次明确邀请」按钮');
  invite.click();
  await settle();

  assert.equal(phone.posts().length, 0, '范围没填就不许发请求');
  assert.ok(phone.texts().includes('必须写清范围'),
    '要给出人读的原因；实际：' + phone.texts());
});

test('写清范围后明确邀请真的发出，并带上 scope', async () => {
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/contact']: () => ({status: 200, body: contactDoc({rows: []})}),
      [API + '/contact/events']: (body) => ({status: 200, body: deliveredDoc(body)})
    }
  });
  await settle();
  phone.shortcut('来访与共同时间').click();
  await settle();

  /* 邀请那块的范围输入：第一次出现的 text 输入框。 */
  const scopeInput = phone.inputs().find((node) => node.attrs.type === 'text');
  assert.ok(scopeInput, '邀请要有范围输入框；现有输入：' +
    JSON.stringify(phone.inputs().map((node) => node.attrs)));
  scopeInput.type('一起坐十分钟');

  phone.buttonByText('投一次明确邀请').click();
  await settle();

  const posts = phone.posts();
  assert.equal(posts.length, 1, '必须真的发一次 POST，实际：' + JSON.stringify(posts));
  assert.equal(posts[0].body.event_kind, 'CO_PRESENCE_INVITATION');
  assert.equal(posts[0].body.scope, '一起坐十分钟');
  assert.ok(!phone.texts().includes('她接受了'), '邀请投递也不许写成她接受了');
});

/* ------------------------------------------------------------------ *
 * 4. 显式结束：没有邀请就不给假的结束按钮；有邀请才给
 * ------------------------------------------------------------------ */

test('没有邀请时不渲染「结束这次邀请」按钮', async () => {
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/contact']: () => ({status: 200,
        body: contactDoc({rows: [], session: {state: 'ABSENT', readable: false, open: false}})})
    }
  });
  await settle();
  phone.shortcut('来访与共同时间').click();
  await settle();

  assert.equal(phone.buttonByText('结束这次邀请'), null,
    '没有邀请时不许给假的结束按钮；现有按钮：' +
    JSON.stringify(phone.buttons().map((node) => textOf(node))));
  assert.ok(phone.texts().includes('现在没有可以结束的邀请'),
    '要照实说现在没有可以结束的邀请；实际：' + phone.texts());
});

test('有邀请时显式结束真的发出：带 invitation_id + explicit_end', async () => {
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/contact']: () => ({status: 200, body: contactDoc()}),
      [API + '/contact/events']: (body) => ({status: 200, body: deliveredDoc(body)})
    }
  });
  await settle();
  phone.shortcut('来访与共同时间').click();
  await settle();

  const select = phone.selects()[0];
  assert.ok(select, '有邀请时要有「结束哪一次」的选择；现有节点：' + phone.texts());
  assert.equal(select.value, INVITATION_ID, '默认选现在那条邀请');

  const end = phone.buttonByText('结束这次邀请');
  assert.ok(end, '有邀请时应该给「结束这次邀请」按钮；现有按钮：' +
    JSON.stringify(phone.buttons().map((node) => textOf(node))));
  end.click();
  await settle();

  const posts = phone.posts();
  assert.equal(posts.length, 1, '必须真的发一次 POST，实际：' + JSON.stringify(posts));
  assert.equal(posts[0].body.event_kind, 'CO_PRESENCE_END');
  assert.equal(posts[0].body.invitation_id, INVITATION_ID);
  assert.equal(posts[0].body.explicit_end, true);
});

/* ------------------------------------------------------------------ *
 * 5. 细节：列表不带正文；详情才带；不写 localStorage；不轮询
 * ------------------------------------------------------------------ */

test('列表不泄正文；打开详情才读一次正文，而且不写进 sessionStorage', async () => {
  const SECRET = '这段正文只在详情里出现';
  const rows = [{
    event_ref: EVENT_REF, seq: 1, event_kind: 'LOCAL_MESSAGE',
    effect: 'OBSERVATION_ONLY', processing_state: 'PENDING', outcome: '',
    occurred_at: '2026-11-20T10:00:00+00:00', received_at: '2026-11-20T10:00:01+00:00',
    body_chars: SECRET.length, body_stored: true, body_included: false,
    scope: '', expired: false, invitation_id: '', invitation_bound: false,
    attempts: 0, claimed_by: '', pause_requested: false, activity_started: false
  }];
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/contact']: () => ({status: 200, body: contactDoc({rows: rows})}),
      [API + '/contact/events/' + EVENT_REF]: () => ({
        status: 200,
        body: {
          ok: true, status: 'ok',
          data: {
            event: Object.assign({}, rows[0], {body_included: true}),
            operation_key: 'contact_op:aaaaaaaaaaaaaaaaaaaaaaaa',
            source: 'local_operator', channel: 'local_inbox', source_ref: '',
            client_identity_ignored: false, invitation: null, end: null, binding: null,
            claim: null, result: null, results: [],
            body: SECRET, body_included: true, private_text_included: true,
            local_only: true,
            note: '只进这台实例自己的本地收件箱，没有发到任何外部渠道，也不等于用户已收到'
          }
        }
      })
    }
  });
  await settle();
  phone.shortcut('来访与共同时间').click();
  await settle();

  assert.ok(!phone.texts().includes(SECRET),
    '列表屏不许出现正文；实际：' + phone.texts());

  const row = phone.nodes().find((node) => node.tagName === 'BUTTON' &&
    textOf(node).includes('普通来话'));
  assert.ok(row, '列表里应该有那条来话；现有按钮：' +
    JSON.stringify(phone.buttons().map((node) => textOf(node))));
  row.click();
  await settle();

  assert.equal(phone.count(API + '/contact/events/' + EVENT_REF), 1,
    '打开详情必须读一次事件详情，实际：' + JSON.stringify(phone.paths()));
  assert.ok(phone.texts().includes(SECRET), '详情屏应该出现正文；实际：' + phone.texts());

  /* 正文与导航都只在 sessionStorage；localStorage 里一个字都不许有。 */
  const stored = Array.from(phone.storage.data.values()).join('\n');
  assert.ok(!stored.includes(SECRET), '正文绝不许进任何 storage');

  /* 没有秒级轮询：除了这一次详情读取，没有额外的 /contact 读取。 */
  assert.equal(phone.count(API + '/contact'), 1, '详情读取不该把列表再轮询一遍');
});

test('读不到 /contact 时照实说读不到，并给重试入口（不给空 inbox）', async () => {
  const phone = bootPhone({
    routes: {
      [API + '/state']: () => ({status: 200, body: stateDoc()}),
      [API + '/contact']: () => ({status: 503,
        body: {ok: false, status: 'unreadable', reason_code: 'CONTACT_STATE_CORRUPT',
          data: {ledger_state: 'UNKNOWN'}}})
    }
  });
  await settle();
  phone.shortcut('来访与共同时间').click();
  await settle();

  assert.ok(phone.texts().includes('来信箱读不出来') ||
    phone.texts().includes('来信箱这次读不到'),
    '坏账本要照实说；实际：' + phone.texts());
  assert.ok(!phone.texts().includes('来信箱是空的'),
    '读不到绝不许说成「空收件箱」；实际：' + phone.texts());
  assert.ok(phone.buttonByText('重试'), '要给重试入口');
});

test('纯函数面：正文不升级成邀请；键与路径都不改写', async () => {
  const phone = bootPhone({
    routes: {[API + '/state']: () => ({status: 200, body: stateDoc()})}
  });
  const pure = phone.pure;

  /* 普通来话即使正文写着「一起坐坐」，kind 也还是 LOCAL_MESSAGE。 */
  const message = pure.buildContactPayload({event_kind: 'LOCAL_MESSAGE',
    text: '一起坐坐好不好', operation_key: 'contact:send:1'});
  assert.equal(message.event_kind, 'LOCAL_MESSAGE');
  assert.equal(message.scope, undefined, '普通来话不许带 scope');

  /* 邀请没有 scope 就不成形。 */
  assert.equal(pure.buildContactPayload({event_kind: 'CO_PRESENCE_INVITATION',
    text: '坐坐', operation_key: 'contact:send:2'}), null);

  /* 结束没有 invitation_id 就不成形。 */
  assert.equal(pure.buildContactPayload({event_kind: 'CO_PRESENCE_END',
    operation_key: 'contact:send:3'}), null);

  /* 路径：协议串原样进路径，不做 URL 编码。 */
  assert.equal(pure.contactEventPath(EVENT_REF), '/contact/events/' + EVENT_REF);
  assert.equal(pure.contactOperationPath('contact_op:' + 'a'.repeat(24)),
    '/contact/operations/contact_op:' + 'a'.repeat(24));
  assert.equal(pure.contactEventPath('bad ref'), '', '带空格的 ref 不许进路径');

  /* 词表：PENDING 的界面说法里必须出现「还没处理」。 */
  assert.ok(pure.CONTACT_STATE_TEXT.PENDING.includes('还没处理'));
  assert.equal(pure.CONTACT_OUTCOME_TEXT.ACCEPTED, '她接受了');
});
