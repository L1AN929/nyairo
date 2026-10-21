/**
 * NyAiro v0.4 · 阶段 C：主屏「共同实践」的真实 DOM 路径（390px 手机宽度下也是同一条路）。
 *
 * 这个文件走的是**真实 DOM 路径**，不是纯函数：
 *   1. 用最小 DOM 桩把 components/phone/web/app.js 整份跑起来（它自己 boot()、自己发 GET /state）；
 *   2. 从**渲染出来的 DOM 树**里找主屏入口、点真实的 click 监听、在真实的 input/textarea 上打字；
 *   3. 只看真实发出的请求（fetch 被替换成记录器）与真实子节点文本。
 *
 * 要守住的五条：
 *   * 选材只从 owner 这次列出的共享来源里选（/shares/sources），界面不列私人材料全集；
 *   * 开一次共同实践真的发出 `{source_artifact_id, source_version_id, title, text, operation_key}`；
 *   * 屏上「你 / 她」的贡献与作品版本都来自服务；queued / accepted 不许被界面写成她答应了；
 *   * 界面里**没有**替她「重新取用」的按钮（resume / reopen 是她那一半）；
 *   * 正文只进 textContent；业务数据不进 sessionStorage（sessionStorage 只留未决操作键）。
 *
 * 运行：node --test tests/test_v04_shared_practice_phone_ui.mjs
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
  Object.defineProperty(node, 'innerHTML', {
    get() { return ''; },
    set() { throw new Error('the phone UI must never write innerHTML'); }
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
  /* 输入框：用 input 事件模拟用户打字（正文只走 value，不进 storage）。 */
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
 * 服务答复的替身（形状按 A 的信封冻结）
 * ------------------------------------------------------------------ */

const PRACTICE_ID = 'artifact:' + 'a'.repeat(32);
const MATERIAL_ID = 'artifact:' + 'b'.repeat(32);
const MATERIAL_VERSION = 'version-' + 'b'.repeat(32) + '-3';
const USER_TEXT = '我这一段先这么写。';
const SUBJECT_TEXT = '那我把留白放到段尾。';

function stateDoc() {
  return {
    ok: true,
    status: 'ok',
    read_only: false,
    csrf_token: 'csrf-practice-token',
    instance: {name: 'practice-fixture', subject: 'chiyo', mode: 'local'},
    apps: {
      notes: {status: 'available', data: {}},
      references: {status: 'available', data: {}},
      reading: {status: 'available', data: {}},
      wallet: {status: 'available', data: {}},
      shop: {status: 'available', data: {}},
      inventory: {status: 'available', data: {}},
      body: {status: 'available', data: {}},
      world: {status: 'available', data: {}},
      contact: {status: 'available', data: {}},
      shared_practice: {status: 'available', data: {}}
    }
  };
}

/** B 的 /shares/sources：owner 真的列得出的共享来源（含 head 版本）。 */
function sourcesDoc() {
  return {
    ok: true,
    status: 'ok',
    data: {
      sources: [
        {artifact_id: MATERIAL_ID, title: '第三页草稿', kind: 'project', lifecycle: 'ACTIVE',
          revision: 3, head_version_id: MATERIAL_VERSION, readable: true, reason_code: ''},
        {artifact_id: 'artifact:' + 'c'.repeat(32), title: '读不到的那一份', kind: 'project',
          lifecycle: 'ACTIVE', revision: 1, head_version_id: 'version-c',
          readable: false, reason_code: 'SOURCE_UNREADABLE'}
      ],
      count: 2,
      truncated: false,
      private_text_included: false
    }
  };
}

function serviceDoc(bound = true) {
  return {
    kind: 'phone_shared_practice', owner: 'shared_practice_service', bound: bound,
    bound_owner: bound, interface_gaps: [],
    actions: ['submit', 'modify', 'pause', 'end'],
    subject_only_actions: ['resume', 'reopen'],
    writes_subject_contribution: false, accepts_for_subject: false,
    starts_life_daemon: false, stores_private_chat: false,
    note: '这是这台实例本地的共同实践；没有向任何外部渠道发送'
  };
}

function practiceRow(overrides = {}) {
  return Object.assign({
    practice_id: PRACTICE_ID, title: '一起把第三页排一遍', state: 'OPEN',
    state_text: '开着的', open: true, joint_state: 'JOINT',
    head_version_id: 'version-head-2', version_number: 2, revision: 2,
    source_artifact_id: MATERIAL_ID, source_version_id: MATERIAL_VERSION,
    source_title: '第三页草稿', source_readable: true, source_reason_code: '',
    material_count: 1, user_rounds: 1, subject_rounds: 1, contributions_total: 2,
    updated_at: '2026-11-20T10:00:00+00:00'
  }, overrides);
}

function listDoc(options = {}) {
  const rows = options.rows || [practiceRow()];
  return {
    ok: true, status: 'ok',
    data: {
      practices: rows, count: rows.length, truncated: false,
      private_text_included: false, local_only: true, delivered_to_user: false,
      note: '这是这台实例本地的共同实践；没有向任何外部渠道发送',
      service: serviceDoc(options.bound !== false)
    }
  };
}

function workDoc(options = {}) {
  const state = options.state || 'OPEN';
  return {
    ok: true, status: 'ok',
    data: {
      practice: practiceRow({state: state, open: state === 'OPEN'}),
      contributions: options.contributions || [
        {contribution_id: '1', by: 'user', by_text: '你', is_user: true, is_subject: false,
         round: 1, seq: 1, kind: 'message', text: USER_TEXT, text_chars: USER_TEXT.length,
         at: '2026-11-20T10:01:00+00:00', origin: 'message', decision_ref: '',
         basis_kind: '', basis_verified: false, draft: false},
        {contribution_id: '2', by: 'subject', by_text: '她', is_user: false, is_subject: true,
         round: 2, seq: 2, kind: 'response', text: SUBJECT_TEXT, text_chars: SUBJECT_TEXT.length,
         at: '2026-11-20T10:02:00+00:00', origin: 'response',
         decision_ref: 'dec:0123456789abcdef', basis_kind: 'decision', basis_verified: true,
         draft: false}
      ],
      versions: [
        {version_id: 'version-head-2', version_number: 2, at: '2026-11-20T10:02:00+00:00',
         by: 'life', text_chars: 24, is_head: true, text: ''},
        {version_id: 'version-head-1', version_number: 1, at: '2026-11-20T10:00:00+00:00',
         by: 'user', text_chars: 12, is_head: false, text: ''}
      ],
      user_contributions: 1, subject_contributions: 1, entries_truncated: false,
      material: {artifact_id: MATERIAL_ID, version_id: MATERIAL_VERSION,
        title: '第三页草稿', readable: true, reason_code: '', state: 'READABLE', count: 1},
      work: {artifact_id: PRACTICE_ID, version_id: 'version-head-2', version_number: 2,
        revision: 2, text: '第三页：把留白重新排一遍。', text_chars: 14, available: true,
        updated_at: '2026-11-20T10:02:00+00:00', updated_by: 'life', version_count: 2},
      pause: {paused: state === 'PAUSED', requested_by: state === 'PAUSED' ? 'user' : '',
        reason_code: '', note: ''},
      end: {ended: state === 'ENDED', ended_at: '', reason: ''},
      governance: {blocked: false, stale: false, reason_code: '', redacted: [],
        writes_allowed: state === 'OPEN'},
      permissions: {
        private_source_exposed: false, bounded_excerpt: true,
        subject_contribution_visible: true,
        actions: state === 'OPEN' ? ['submit', 'modify', 'pause', 'end']
          : (state === 'PAUSED' ? ['end'] : []),
        may_write_subject_contribution: false, may_accept_for_subject: false
      },
      private_text_included: true, local_only: true, delivered_to_user: false,
      note: '这是这台实例本地的共同实践；没有向任何外部渠道发送',
      service: serviceDoc(true)
    }
  };
}

function openDoc() {
  return {
    ok: true, status: 'applied',
    data: {
      action: 'open', action_text: '邀请她一起做这一次', practice_id: '',
      event_ref: 'contact_evt:0123456789abcdef', event_kind: 'CO_PRESENCE_INVITATION',
      event_state: 'PENDING', material_refs: [MATERIAL_ID],
      state: 'OPEN', joint_state: 'CANDIDATE', head_version_id: 'version-head-1',
      version_number: 1, operation_key: 'practice_op:1', source_version_id: MATERIAL_VERSION,
      queued: true, submitted: true, accepted: false, accepted_written_by_phone: false,
      subject_contribution_written: false, pause_requested: false, activity_started: false,
      starts_life_daemon: false, delivered_to_user: false, local_only: true,
      retryable: false, work_changed: false, subject_responded: false, stopped: false,
      note: '这是这台实例本地的共同实践；没有向任何外部渠道发送',
      owner: {status: 'applied'}
    }
  };
}

function actionDoc(action, options = {}) {
  const state = options.state || 'OPEN';
  return {
    ok: true, status: 'applied',
    data: {
      action: action, action_text: action, practice_id: PRACTICE_ID, state: state,
      joint_state: 'JOINT', head_version_id: 'version-head-3', version_number: 3,
      operation_key: 'practice_op:2', queued: false, submitted: true, accepted: false,
      accepted_written_by_phone: false, subject_contribution_written: false,
      pause_requested: action === 'pause', activity_started: false,
      starts_life_daemon: false, delivered_to_user: false, local_only: true,
      retryable: false, work_changed: false, subject_responded: true,
      stopped: state === 'PAUSED' || state === 'ENDED',
      note: '这是这台实例本地的共同实践；没有向任何外部渠道发送',
      owner: {status: 'applied'}
    }
  };
}

/* ------------------------------------------------------------------ *
 * 把 app.js 整份跑起来（它会自己 boot）
 * ------------------------------------------------------------------ */

function bootPhone(options = {}) {
  const {doc, byId} = makeDocument();
  const calls = [];
  const routes = Object.assign({
    '/api/phone/state': () => ({status: 200, body: stateDoc()}),
    '/api/phone/shares/sources': () => ({status: 200, body: sourcesDoc()}),
    '/api/phone/shared-practice': () => ({status: 200, body: listDoc()}),
    ['/api/phone/shared-practice/works/' + PRACTICE_ID]: () => ({status: 200, body: workDoc()})
  }, options.routes || {});
  const storage = memoryStorage();

  const context = {
    /* 390px 手机视口：CSS 断点之外的逻辑不该依赖宽度，这里把宽度给出去以便断言同一棵树。 */
    innerWidth: 390,
    innerHeight: 844,
    devicePixelRatio: 3,
    matchMedia: () => ({matches: false, addEventListener() {}, removeEventListener() {}}),
    document: doc,
    sessionStorage: storage,
    crypto: {randomUUID: () => '12345678-1234-4234-9234-' +
      String(calls.length + 1).padStart(12, '0')},
    setInterval: () => 0,
    clearInterval: () => {},
    setTimeout: (fn) => { if (typeof fn === 'function') fn(); return 0; },
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
      const answer = handler(parsed, {method, path});
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
  const phone = {
    ctx: context,
    pure: context.NyairoPhone,
    storage: storage,
    calls: calls,
    byId: byId,
    screenBody: screenBody,
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
    shortcut: (label) => walk(screenBody)
      .find((node) => node.tagName === 'BUTTON' && textOf(node).trim() === label) || null
  };
  return phone;
}

async function settle(times = 24) {
  for (let i = 0; i < times; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

/** 从主屏进「共同实践」屏（主屏入口是真实按钮）。 */
async function enterSharedPractice(phone) {
  const entry = phone.buttonByText('共同实践');
  assert.ok(entry, '主屏应该有「共同实践」入口：' + phone.texts().slice(0, 400));
  entry.click();
  await settle();
}

/* ------------------------------------------------------------------ *
 * 1. 主屏 -> 共同实践屏：列表与 owner 状态都来自服务
 * ------------------------------------------------------------------ */

/** 选材列表里那一行的按钮（不是共同实践列表里的行：那行的 chip 也会带材料标题）。 */
function pickSource(phone) {
  const source = walk(phone.screenBody).find((node) => node.tagName === 'BUTTON' &&
    (textOf(node).includes('点一下选这份材料') || textOf(node).includes('已选这份')));
  assert.ok(source, '应有一个可点的共享来源行：' + phone.texts().slice(0, 300));
  return source;
}

test('主屏「共同实践」入口真的读 GET /shared-practice，并照实显示 owner 状态', async () => {
  const phone = bootPhone();
  await settle();
  await enterSharedPractice(phone);
  assert.equal(phone.count('/api/phone/shared-practice'), 1);
  assert.match(phone.texts(), /一起把第三页排一遍/);
  assert.match(phone.texts(), /开着的/);
  assert.match(phone.texts(), /你 1 次/);
  assert.match(phone.texts(), /她 1 次/);
  assert.match(phone.texts(), /材料：第三页草稿/);
  /* 列表这一屏不投影私人正文。 */
  assert.ok(!phone.texts().includes(USER_TEXT), '列表不该出现正文');
});

test('owner 没接上时照实说没接上，不摆一个假的选材框', async () => {
  const phone = bootPhone({
    routes: {'/api/phone/shared-practice': () => ({status: 200, body: listDoc({rows: [], bound: false})})}
  });
  await settle();
  await enterSharedPractice(phone);
  assert.match(phone.texts(), /共同实践还没接上 owner/);
  assert.equal(phone.buttonByText('开这一次共同实践'), null);
});

/* ------------------------------------------------------------------ *
 * 2. 选材只从 owner 列出的来源里选；开一次真的发 POST
 * ------------------------------------------------------------------ */

test('选材只列 /shares/sources 里读得到的来源，点一下就带上那个版本', async () => {
  const phone = bootPhone();
  await settle();
  await enterSharedPractice(phone);
  assert.equal(phone.count('/api/phone/shares/sources'), 1);
  assert.match(phone.texts(), /第三页草稿/);
  assert.ok(!phone.texts().includes('读不到的那一份'), '读不到的来源不许当成可选');
  const pick = pickSource(phone);
  assert.ok(pick, '来源应该在可选列表里');
  pick.click();
  await settle(4);
  assert.match(phone.texts(), /已选这份/);
  assert.match(phone.texts(), new RegExp(MATERIAL_VERSION.slice(0, 12)));
});

test('开一次共同实践：POST 真的发出，带 title / text / 版本，成功后才进这一次', async () => {
  const phone = bootPhone({
    routes: {
      '/api/phone/shared-practice': (body, info) => {
        if (info && info.method === 'POST') return {status: 200, body: openDoc()};
        return {status: 200, body: listDoc()};
      },
      ['/api/phone/shared-practice/works/' + PRACTICE_ID]: () => ({status: 200, body: workDoc()})
    }
  });
  await settle();
  await enterSharedPractice(phone);
  pickSource(phone).click();
  await settle(4);
  const titleInput = phone.inputs().find((node) => node.attrs.placeholder &&
    node.attrs.placeholder.indexOf('叫什么') >= 0);
  assert.ok(titleInput, '应该有标题输入框');
  titleInput.type('一起把第三页排一遍');
  const opening = phone.textareas()[0];
  assert.ok(opening, '应该有「你的第一轮」输入框');
  opening.type(USER_TEXT);
  await settle(4);
  const button = phone.buttonByText('开这一次共同实践');
  assert.ok(button && button.disabled !== true, '写齐之后按钮应该可用');
  button.click();
  await settle(30);

  const posts = phone.posts();
  assert.equal(posts.length, 1, JSON.stringify(posts));
  const body = posts[0].body;
  assert.equal(body.source_artifact_id, MATERIAL_ID);
  assert.equal(body.source_version_id, MATERIAL_VERSION);
  assert.equal(body.title, '一起把第三页排一遍');
  assert.equal(body.text, USER_TEXT);
  assert.match(body.operation_key, /^practice_op:/);
  assert.equal(posts[0].csrf, 'csrf-practice-token');
  /* 绝不带她的那一半。 */
  for (const key of ['subject', 'contributor', 'accepted', 'state', 'by', 'outcome']) {
    assert.ok(!(key in body), '请求体不许出现 ' + key);
  }
  /* 邀请不是「已经开了作品」：界面留在这一屏，照实说已进她的来信箱、还没处理。 */
  assert.equal(phone.count('/api/phone/shared-practice/works/' + PRACTICE_ID), 0);
  assert.match(phone.texts(), /来信箱|还没被处理|排队不等于她答应了/);
  assert.ok(!phone.texts().includes('她接受了'), '屏上不许写她接受了');
  /* 业务数据不进 sessionStorage：只留未决操作键。 */
  const keys = [...phone.storage.data.keys()];
  keys.forEach((key) => assert.doesNotMatch(String(phone.storage.data.get(key)), /第三页|我这一段/));
});

test('没选材料就点「开」：不发请求，给出人读的错', async () => {
  const phone = bootPhone();
  await settle();
  await enterSharedPractice(phone);
  const button = phone.buttonByText('开这一次共同实践');
  if (button) button.click();
  await settle(20);
  assert.equal(phone.posts().length, 0, '缺材料时不许发请求');
  assert.match(phone.texts(), /先选一份明确共享的材料/);
});

/* ------------------------------------------------------------------ *
 * 3. 这一次：双方贡献、真实作品版本、没有替她恢复的按钮
 * ------------------------------------------------------------------ */

test('详情屏把「你 / 她」分开，并显示 owner 的作品版本（正文只在共同作品里）', async () => {
  const phone = bootPhone();
  await settle();
  await enterSharedPractice(phone);
  phone.buttonByText('一起把第三页排一遍').click();
  await settle(30);
  const text = phone.texts();
  assert.match(text, /双方的来回/);
  assert.match(text, new RegExp(USER_TEXT));
  assert.match(text, new RegExp(SUBJECT_TEXT));
  assert.match(text, /你/);
  assert.match(text, /她/);
  assert.match(text, /共同作品/);
  assert.match(text, /第三页：把留白重新排一遍。/);
  assert.match(text, /作品版本（owner 自己的历史）/);
  assert.match(text, /第 2 版/);
  assert.match(text, /现在这一版/);
  assert.match(text, /不能替她写/);
});

test('没有「重新取用」按钮：resume / reopen 是她那一半', async () => {
  const phone = bootPhone();
  await settle();
  await enterSharedPractice(phone);
  phone.buttonByText('一起把第三页排一遍').click();
  await settle(30);
  assert.equal(phone.buttonByText('重新取用'), null);
  const labels = phone.buttons().map((node) => textOf(node).trim());
  assert.ok(!labels.includes('重新取用'), labels.join(' / '));
  assert.match(phone.texts(), /由她自己决定/);
});

test('提交我这一句：POST 带 expected_head 与原键；屏上不写「她答应了」', async () => {
  const phone = bootPhone({
    routes: {
      ['/api/phone/shared-practice/works/' + PRACTICE_ID]: () => ({status: 200, body: workDoc()}),
      ['/api/phone/shared-practice/works/' + PRACTICE_ID + '/actions']:
        () => ({status: 200, body: actionDoc('submit')})
    }
  });
  await settle();
  await enterSharedPractice(phone);
  phone.buttonByText('一起把第三页排一遍').click();
  await settle(30);
  const area = phone.textareas()[0];
  assert.ok(area, '应该有写自己这一句的输入框');
  area.type('第二段我改了一遍。');
  await settle(4);
  phone.buttonByText(phone.pure.SHARED_PRACTICE_ACTION_TEXT.submit).click();
  await settle(40);

  const posts = phone.posts();
  assert.equal(posts.length, 1, JSON.stringify(posts) + ' buttons=' +
    JSON.stringify(phone.buttons().map((node) => ({text: textOf(node).trim().slice(0, 12),
      disabled: node.disabled}))) + ' tail=' + phone.texts().slice(-300));
  assert.equal(posts[0].body.action, 'submit');
  assert.equal(posts[0].body.text, '第二段我改了一遍。');
  assert.equal(posts[0].body.expected_head, 'version-head-2',
    'expected_head 必须是这一次读到的 head');
  assert.match(posts[0].body.operation_key, /^practice_op:/);
  assert.ok(!phone.texts().includes('她接受了'), '屏上不许写她接受了');
  assert.match(phone.texts(), /没有替她|不是界面替她做的判断/);
});

test('暂停与结束都是用户的请求；暂停后不再给「提交」', async () => {
  const phone = bootPhone({
    routes: {
      ['/api/phone/shared-practice/works/' + PRACTICE_ID]: () => ({status: 200, body: workDoc()}),
      ['/api/phone/shared-practice/works/' + PRACTICE_ID + '/actions']:
        () => ({status: 200, body: actionDoc('pause', {state: 'PAUSED'})})
    }
  });
  await settle();
  await enterSharedPractice(phone);
  phone.buttonByText('一起把第三页排一遍').click();
  await settle(30);
  phone.buttonByText(phone.pure.SHARED_PRACTICE_ACTION_TEXT.pause).click();
  await settle(40);
  const posts = phone.posts();
  assert.equal(posts.length, 1, JSON.stringify(posts));
  assert.equal(posts[0].body.action, 'pause');
  assert.equal(posts[0].body.expected_head, 'version-head-2');
  assert.ok(!('text' in posts[0].body), '暂停不带正文');
});

/* ------------------------------------------------------------------ *
 * 4. 读不到 / 断线：照实说，并给重试入口
 * ------------------------------------------------------------------ */

test('共同实践读不出来时照实说读不出来，并给重试入口', async () => {
  const phone = bootPhone({
    routes: {'/api/phone/shared-practice': () => ({status: 503,
      body: {ok: false, status: 'unavailable', reason_code: 'SHARED_PRACTICE_UNAVAILABLE',
        detail: 'owner 没回话', data: {practices: [], list_ok: false, service: serviceDoc(true)}}})}
  });
  await settle();
  await enterSharedPractice(phone);
  assert.match(phone.texts(), /共同实践这次读不到|共同实践读不出来/);
  assert.ok(phone.buttonByText('重试'), '应该给重试按钮');
});

/* ------------------------------------------------------------------ *
 * 5. 纯函数面：请求体 / 校验 / 路径 / 旧动作
 * ------------------------------------------------------------------ */

test('纯函数面：open 需要 title 与第一轮；resume 在纯函数层就不成立', () => {
  const pure = bootPhone().pure;
  const missing = pure.validateSharedPracticeDraft('open', {
    source_artifact_id: MATERIAL_ID, source_version_id: MATERIAL_VERSION});
  assert.equal(missing.ok, false);
  assert.ok(missing.errors.some((line) => line.includes('标题')));
  assert.ok(missing.errors.some((line) => line.includes('第一轮')));

  const body = pure.buildSharedPracticeOpenPayload({
    source_artifact_id: MATERIAL_ID, source_version_id: MATERIAL_VERSION,
    title: ' 一起排好 ', text: USER_TEXT, operation_key: 'practice_op:1'});
  /* vm 里的对象属于另一个 realm：按 JSON 比结构，不按引用比原型。 */
  assert.equal(JSON.stringify(body), JSON.stringify({source_artifact_id: MATERIAL_ID,
    source_version_id: MATERIAL_VERSION, title: '一起排好', text: USER_TEXT,
    operation_key: 'practice_op:1'}));
  assert.equal(pure.buildSharedPracticeActionPayload({action: 'resume',
    expected_head: 'version-head-2', operation_key: 'practice_op:1'}), null);
  assert.equal(pure.buildSharedPracticeActionPayload({action: 'reopen',
    expected_head: 'version-head-2', operation_key: 'practice_op:1'}), null);
  assert.equal(JSON.stringify(pure.SHARED_PRACTICE_ACTIONS),
    JSON.stringify(['submit', 'modify', 'pause', 'end']));
  assert.equal(JSON.stringify(pure.SHARED_PRACTICE_SUBJECT_ONLY),
    JSON.stringify(['resume', 'reopen']));
  assert.equal(pure.sharedPracticeWorkPath('artifact:bad id'), '');
});
