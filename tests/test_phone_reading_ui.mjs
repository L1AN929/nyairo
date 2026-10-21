/**
 * NyAiro rc1 · 阅读工作台（components/phone/web）定向检查。
 *
 * 只检查这一轮的前端消费面：
 *   · GET /reading/material?page 的 data 字段搬运（不猜 flat）；
 *   · 翻页只发 GET，只有显式「保存阅读停点」才 POST positions；
 *   · 导入正文 / 原页笔记 / 疑问三种写入的真实 payload 形状（白名单键集）；
 *   · 旧版本停点与旧版本锚点不悄悄跳到当前页；
 *   · 未决操作的原键查询走 /reading/operations/<key>，不落到购物 operation；
 *   · 正文/笔记不进 sessionStorage，正文注入不执行；
 *   · loading / empty / unavailable / disabled / unknown / 旧版需重看 的诚实状态。
 *
 * 运行：node --test tests/test_phone_reading_ui.mjs
 * 说明：后半段的 stub DOM 只证明接线与字段搬运，**不是**浏览器渲染/布局/键盘验收。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const WEB = new URL('../components/phone/web/', import.meta.url);
const read = (name) => readFileSync(new URL(name, WEB), 'utf8');
const html = read('index.html');
const css = read('style.css');
const src = read('app.js');

/* 无 DOM 沙箱：只取纯函数面。 */
const pure = vm.createContext({});
vm.runInContext(src, pure, {filename: 'components/phone/web/app.js'});
const P = pure.NyairoPhone;

/* ---------------- 真实形状的协议值 ---------------- */
const HEX32 = (chunk) => chunk.repeat(16);
const ARTIFACT_ID = 'artifact:' + HEX32('ab12');
const SOURCE_ID = 'artifact:' + HEX32('cd34');
const REV_NEW = 'artifact-version:' + HEX32('7e11');
const REV_OLD = 'artifact-version:' + HEX32('0a1b');
const HEAD_1 = 'artifact-version:' + HEX32('11aa');
const HEAD_2 = 'artifact-version:' + HEX32('22bb');
const ANNOTATION_ID = 'memo:' + HEX32('5b2f');
const NOTE_ID = 'memo:' + HEX32('9c4d');
const SECRET_BODY = '这一段只属于她：妹妹在窗边坐了很久。';
const NOTE_BODY = '她把书签夹在第二页，没有读完就先合上了。';

/* ---------------- 1. 静态约束 ---------------- */

test('app.js 解析通过，阅读接口与不拼 HTML 的规矩都在', () => {
  new vm.Script(src, {filename: 'app.js'});
  for (const banned of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function']) {
    assert.equal(src.includes(banned), false, `app.js 不能使用 ${banned}`);
  }
  assert.match(src, /'\/reading\/material'/);
  assert.match(src, /'\/reading\/positions'/);
  assert.match(src, /'\/reading\/annotations'/);
  assert.match(src, /'\/reading\/operations\/' \+ encodeURIComponent\(check\.value\)/);
  assert.equal(src.includes("'/api/phone/reading/material'"), false, '不许绕开 API_BASE 拼绝对地址');
  assert.doesNotMatch(src, /\blocalStorage\s*(?:\.|\[)/);
});

/* ---------------- 2. 纯函数：契约字段搬运 ---------------- */

const materialDoc = (options) => {
  const opts = options || {};
  const page = opts.page === undefined ? 1 : opts.page;
  return {
    ok: true,
    status: 'applied',
    data: {
      available: opts.available === undefined ? true : opts.available,
      artifact_id: ARTIFACT_ID,
      source_id: SOURCE_ID,
      source_revision: opts.revision || REV_NEW,
      version_number: 3,
      title: '秋日散步',
      page: page,
      pages: 2,
      text: page === 2 ? '第二页：风把纸吹动了一下。' : SECRET_BODY,
      content_available: true,
      private_head: opts.head || HEAD_1,
      bookmark: opts.bookmark === undefined
        ? {page: 2, source_revision: opts.revision || REV_NEW, needs_reread: false}
        : opts.bookmark,
      understanding: 'UNKNOWN',
      annotations: opts.annotations || []
    }
  };
};

test('新阅读字段只从 data 读；顶层同名字段不当契约（不猜 flat）', () => {
  const view = P.readingMaterialView(materialDoc({}));
  assert.equal(view.phase, 'ready');
  assert.equal(view.available, true);
  assert.equal(view.title, '秋日散步');
  assert.equal(view.page, 1);
  assert.equal(view.pages, 2);
  assert.equal(view.versionNumber, 3);
  assert.equal(view.sourceRevision, REV_NEW, '协议值原字节');
  assert.equal(view.privateHead, HEAD_1);
  assert.equal(view.understanding, 'UNKNOWN');
  assert.equal(view.bookmark.page, 2);
  assert.equal(view.needsReread, false);

  /* flat：把同样的字段放在 doc 顶层，界面必须诚实说“没有 data”，而不是把顶层当契约。 */
  const flat = materialDoc({});
  const flatDoc = Object.assign({}, flat.data, {ok: true, status: 'applied'});
  const missing = P.readingMaterialView(flatDoc);
  assert.equal(missing.phase, 'malformed');
  assert.equal(missing.available, false);
  assert.equal(missing.title, undefined, '顶层 title 不能流进视图');
  assert.equal(missing.text, undefined, '顶层 text 不能流进视图');
  assert.equal(missing.annotationsMissing, true);
});

test('页数/页码只认严格整数：bool、字符串、小数都不算', () => {
  assert.equal(P.readingPageValue(1), 1);
  assert.equal(P.readingPageValue(true), null);
  assert.equal(P.readingPageValue('2'), null);
  assert.equal(P.readingPageValue(2.5), null);
  assert.equal(P.readingPageValue(0), null);
  assert.equal(P.readingPageValue(-1), null);
  assert.equal(P.readingPageValue(NaN), null);

  /* 页码/页数是字符串时，视图标成未知，界面不会自己转成数字。 */
  const doc = materialDoc({});
  doc.data.page = '1';
  doc.data.pages = '2';
  const view = P.readingMaterialView(doc);
  assert.equal(view.page, null);
  assert.equal(view.pageKnown, false);
});

test('GET 路径：没有 page 参数就让服务按停点决定，翻页才带 ?page=', () => {
  assert.equal(P.readingMaterialPath(null), '/reading/material');
  assert.equal(P.readingMaterialPath(undefined), '/reading/material');
  assert.equal(P.readingMaterialPath(true), '/reading/material', 'bool 不许变成 ?page=1');
  assert.equal(P.readingMaterialPath(2), '/reading/material?page=2');
});

test('UTF-8 字节与 %%PAGE%% 分页上限：空正文、空白页、超页数、超单页、超总量都拒', () => {
  assert.equal(P.utf8ByteLength('a'), 1);
  assert.equal(P.utf8ByteLength('她'), 3);
  assert.equal(P.utf8ByteLength('😀'), 4);
  assert.equal(P.utf8ByteLength(''), 0);

  assert.equal(P.validateReadingContent('').ok, false);
  assert.equal(P.validateReadingContent('   \n  ').ok, false);
  const ok = P.validateReadingContent('第一页正文%%PAGE%%第二页正文');
  assert.equal(ok.ok, true, ok.errors.join('；'));
  assert.equal(ok.stats.pageCount, 2);
  assert.equal(P.validateReadingContent('第一页%%PAGE%%%%PAGE%%第三页').ok, false, '空白页要拒');

  const many = new Array(65).fill('一').join('%%PAGE%%');
  assert.equal(P.validateReadingContent(many).ok, false, '超过 64 页要拒');

  const fatPage = '一'.repeat(4001); // 4001 * 3 = 12003 bytes
  const fat = P.validateReadingContent(fatPage);
  assert.equal(fat.ok, false);
  assert.equal(fat.stats.pageBytes[0] > P.READING_PAGE_MAX_BYTES, true);

  const fatTotal = '一'.repeat(8001) + '%%PAGE%%' + '一'.repeat(8000); // 48003 bytes, 两页各 8001 字
  const total = P.validateReadingContent(fatTotal);
  assert.equal(total.ok, false);
  assert.match(total.errors.join('；'), /24000/);

  /* 单页 12000 字节以内、总 24000 字节以内是允许的（3900*3*2 + 分隔符 8 字节 = 23408） */
  const allowed = P.validateReadingContent('一'.repeat(3900) + '%%PAGE%%' + '一'.repeat(3900));
  assert.equal(allowed.ok, true, allowed.errors.join('；'));
});

test('导入 payload 只有白名单三项；客户端不许声明 title / 来源回执 / 理解', () => {
  const built = P.buildImportPayload({
    content: '第一页%%PAGE%%第二页',
    expected_revision: REV_NEW,
    operation_key: 'op-import-1',
    title: '她自己的标题',
    understanding: 'UNDERSTOOD',
    source_receipt: 'whatever'
  });
  assert.equal(built.ok, true);
  assert.deepEqual(Object.keys(built.payload).sort(), ['content', 'expected_revision', 'operation_key']);
  assert.equal(built.payload.content, '第一页%%PAGE%%第二页');
  assert.equal(built.payload.expected_revision, REV_NEW);
  assert.equal(built.pages, 2);

  const noRev = P.buildImportPayload({content: '正文', operation_key: 'k'});
  assert.equal(noRev.ok, false);
  assert.equal(noRev.field, 'expected_revision');
  const empty = P.buildImportPayload({content: '  ', expected_revision: REV_NEW, operation_key: 'k'});
  assert.equal(empty.ok, false);
  assert.equal(empty.errors[0], '正文不能为空');
});

test('停点 payload：page 严格整数，三件协议值缺一不发', () => {
  const built = P.buildPositionPayload({
    page: 2,
    source_revision: REV_NEW,
    expected_head: HEAD_1,
    operation_key: 'op-pos-1',
    extra: 'x'
  });
  assert.equal(built.ok, true);
  assert.deepEqual(Object.keys(built.payload).sort(),
    ['expected_head', 'operation_key', 'page', 'source_revision']);
  assert.equal(built.payload.page, 2);
  assert.equal(built.payload.source_revision, REV_NEW);
  assert.equal(built.payload.expected_head, HEAD_1);

  for (const badPage of [true, '2', 2.5, 0, null]) {
    const bad = P.buildPositionPayload({page: badPage, source_revision: REV_NEW,
      expected_head: HEAD_1, operation_key: 'k'});
    assert.equal(bad.ok, false, `page=${String(badPage)} 不该通过`);
    assert.equal(bad.field, 'page');
  }
  const noHead = P.buildPositionPayload({page: 1, source_revision: REV_NEW, operation_key: 'k'});
  assert.equal(noHead.ok, false);
  assert.equal(noHead.field, 'expected_head');
  assert.match(noHead.message, /没有可用的事项 head/);
});

test('批注 payload 按 kind 白名单：note 要 title+body，unclear 只要 note', () => {
  const note = P.buildAnnotationPayload({
    kind: 'note', page: 1, source_revision: REV_NEW, expected_head: HEAD_1,
    operation_key: 'op-note-1', title: '第一页', body: NOTE_BODY, note: '不该出现的疑问'
  });
  assert.equal(note.ok, true);
  assert.deepEqual(Object.keys(note.payload).sort(),
    ['body', 'expected_head', 'kind', 'operation_key', 'page', 'source_revision', 'title']);
  assert.equal(note.payload.title, '第一页');
  assert.equal(note.payload.body, NOTE_BODY);

  const unclear = P.buildAnnotationPayload({
    kind: 'unclear', page: 1, source_revision: REV_NEW, expected_head: HEAD_1,
    operation_key: 'op-unclear-1', title: '不该出现的标题', body: '不该出现的正文', note: '这一段没读懂'
  });
  assert.equal(unclear.ok, true);
  assert.deepEqual(Object.keys(unclear.payload).sort(),
    ['expected_head', 'kind', 'note', 'operation_key', 'page', 'source_revision']);
  assert.equal(unclear.payload.note, '这一段没读懂');

  const badKind = P.buildAnnotationPayload({kind: 'highlight', page: 1, source_revision: REV_NEW,
    expected_head: HEAD_1, operation_key: 'k'});
  assert.equal(badKind.ok, false);
  const emptyNote = P.buildAnnotationPayload({kind: 'unclear', page: 1, source_revision: REV_NEW,
    expected_head: HEAD_1, operation_key: 'k', note: '   '});
  assert.equal(emptyNote.ok, false);
  assert.equal(P.validateAnnotationDraft({kind: 'note', title: '标题', body: ''}).ok, false);
});

test('opaque ID：注解 id/版本/键原字节进视图，展示标签另走 safeLabel', () => {
  const view = P.readingMaterialView(materialDoc({
    annotations: [{
      id: ANNOTATION_ID, kind: 'note', title: '第二页', body: NOTE_BODY,
      source_id: SOURCE_ID, source_revision: REV_NEW, page: 2, created_at: '2026-10-22T10:00:00'
    }]
  }));
  assert.equal(view.annotations.length, 1);
  const row = view.annotations[0];
  assert.equal(row.id, ANNOTATION_ID, '协议 id 必须原字节');
  assert.equal(row.sourceRevision, REV_NEW);
  assert.notEqual(row.idLabel, row.id, '展示标签不能等于协议值本身');
  assert.equal(row.canNavigate, true);
  assert.equal(row.stale, false);

  assert.equal(P.readingScopeFor('reading-position', REV_NEW, 2),
    'reading-position:' + REV_NEW + ':2');
  assert.equal(P.readingScopeFor('reading-note', REV_NEW, 2),
    'reading-note:' + REV_NEW + ':2');
  assert.equal(P.readingScopeFor('reading-import', REV_NEW, 0), 'reading-import:' + REV_NEW + ':0');
  assert.equal(P.readingScopeFor('purchase', REV_NEW, 1), '', '阅读 scope 不能落到购物上');
  assert.equal(P.readingOperationPath('op-key-777'), '/reading/operations/op-key-777');
  assert.equal(P.readingOperationPath(''), '');
  assert.equal(P.readingScopePage('reading-position:' + REV_NEW + ':2'), 2);
});

test('旧版本停点与旧版本批注：标 needs_reread，且不许导航到当前页同号', () => {
  const view = P.readingMaterialView(materialDoc({
    bookmark: {page: 2, source_revision: REV_OLD, needs_reread: true},
    annotations: [{
      id: ANNOTATION_ID, kind: 'unclear', note: '这里没懂', source_id: SOURCE_ID,
      source_revision: REV_OLD, page: 2, created_at: '2026-10-20T09:00:00'
    }]
  }));
  assert.equal(view.bookmarkStale, true);
  assert.equal(view.needsReread, true);
  const row = view.annotations[0];
  assert.equal(row.stale, true);
  assert.equal(row.sameRevision, false);
  assert.equal(row.canNavigate, false, '旧版本的批注不许把用户带到当前版本的第 2 页');

  /* 旧版停点但服务没标 needs_reread：界面自己按版本差异判定，不替服务说没事 */
  const quiet = P.readingMaterialView(materialDoc({
    bookmark: {page: 1, source_revision: REV_OLD, needs_reread: false}
  }));
  assert.equal(quiet.bookmarkStale, true);
  assert.equal(quiet.needsReread, true);
});

/* ---------------- 3. stub DOM 接线（不是浏览器验收） ---------------- */

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
  dump() {
    return Array.from(this.data.values()).join('\n');
  }
}

function makeStubDom(options) {
  const opts = options || {};
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

  const storage = new MemoryStorage();
  if (opts.seedUnresolved) storage.setItem(P.SESSION_KEYS.unresolved, JSON.stringify(opts.seedUnresolved));

  const stateDoc = {
    ok: true,
    read_only: false,
    csrf_token: 'tok-read',
    instance: {name: '妹妹', subject: 'chiyo', mode: 'demo'},
    apps: {
      notes: {status: 'available', data: {}},
      references: {status: 'available', data: {}},
      reading: {status: opts.readingStatus || 'available', data: {}},
      wallet: {status: 'available', data: {}},
      shop: {status: 'available', data: {}},
      inventory: {status: 'available', data: {}},
      body: {status: 'available', data: {}},
      world: {status: 'available', data: {}}
    }
  };

  const handlers = {
    material: opts.material || (() => materialDoc({})),
    reading: opts.reading || {ok: true, status: 'applied', items: []},
    notes: opts.notes || {ok: true, status: 'applied', head: HEAD_1, items: []},
    positions: opts.positions || (() => ({ok: true, status: 'applied',
      data: {page: 1, source_revision: REV_NEW, private_head: HEAD_2, understanding: 'UNKNOWN', operation_key: 'k'}})),
    annotations: opts.annotations || (() => ({ok: true, status: 'applied', data: {id: ANNOTATION_ID}})),
    importMaterial: opts.importMaterial || (() => ({ok: true, status: 'applied',
      data: {source_revision: REV_NEW, version_number: 4, page: 1}})),
    operation: opts.operation || (() => ({ok: true, status: 'applied',
      data: {operation_key: 'k', action: 'save_position', page: 1, effect_state: 'applied'}}))
  };

  const requests = [];
  let seq = 0;
  const sandbox = {
    document: document,
    console: console,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    setInterval: () => 0,
    clearInterval: () => {},
    AbortController: AbortController,
    sessionStorage: storage,
    crypto: {randomUUID: () => `uuid-${++seq}`},
    fetch: async (url, init) => {
      const method = (init && init.method) || 'GET';
      const path = String(url).replace('/api/phone', '');
      const body = init && init.body ? JSON.parse(init.body) : null;
      requests.push({path: path, method: method, init: init, body: body});
      let doc = null;
      if (method === 'GET') {
        if (path === '/state') doc = stateDoc;
        else if (path.indexOf('/reading/material') === 0) doc = handlers.material(path);
        else if (path.indexOf('/reading/operations/') === 0) doc = handlers.operation(path);
        else if (path === '/reading') doc = handlers.reading;
        else if (path === '/notes') doc = handlers.notes;
      } else if (path === '/reading/positions') {
        doc = handlers.positions();
      } else if (path === '/reading/annotations') {
        doc = handlers.annotations();
      } else if (path === '/reading/material') {
        doc = handlers.importMaterial();
      }
      if (!doc) return {ok: false, status: 404, text: async () => ''};
      const status = typeof doc.__status === 'number' ? doc.__status : 200;
      return {ok: status < 400, status: status, text: async () => JSON.stringify(doc)};
    }
  };
  return {sandbox: sandbox, document: document, requests: requests, storage: storage};
}

const settle = async () => {
  for (let i = 0; i < 12; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

const bodyOf = (harness) => harness.document.getElementById('screen-body');

const hasClass = (node, name) => String(node.className || '').split(/\s+/).indexOf(name) >= 0;

const findButton = (rootNode, text) =>
  rootNode.findAll((node) => node.tagName === 'BUTTON' && node.allText.includes(text))[0];

const click = (node, label) => {
  const handlers = (node && node.listeners && node.listeners.click) || [];
  assert.ok(handlers.length > 0, `${label || '这个按钮'} 没有接上点击`);
  for (const handler of handlers) handler({target: node});
};

const type = (node, value, label) => {
  assert.ok(node, `没有找到要输入的${label || '控件'}`);
  node.value = value;
  for (const handler of (node.listeners.input || [])) handler({target: node});
};

const textareaFor = (harness, placeholderPart) =>
  bodyOf(harness).findAll((node) => node.tagName === 'TEXTAREA' && String(node.getAttribute('placeholder') || '').includes(placeholderPart))[0];

const inputOfType = (harness, type_) =>
  bodyOf(harness).findAll((node) => node.tagName === 'INPUT' && node.getAttribute('type') === type_)[0];

const pageOf = (path) => {
  const match = /[?&]page=(\d+)/.exec(String(path));
  return match ? Number(match[1]) : 1;
};

async function boot(harness) {
  const context = vm.createContext(harness.sandbox);
  vm.runInContext(src, context, {filename: 'components/phone/web/app.js'});
  await settle();
  return context;
}

async function openReading(harness) {
  const tile = findButton(bodyOf(harness), '阅读');
  click(tile, '主屏的阅读入口');
  await settle();
}

const posts = (harness) => harness.requests.filter((entry) => entry.method === 'POST');

test('（stub DOM）打开阅读屏：旧记录 + 真实材料两块都读，正文只做 textContent', async () => {
  const harness = makeStubDom({});
  await boot(harness);
  await openReading(harness);

  const reads = harness.requests.filter((entry) => entry.method === 'GET').map((entry) => entry.path);
  assert.ok(reads.includes('/reading'), '要读旧取得/位置记录');
  assert.ok(reads.includes('/reading/material'), '要读真实材料（没有 page 就让服务按停点决定）');

  const body = bodyOf(harness);
  const text = body.allText;
  assert.equal(text.includes('界面错误'), false, `阅读屏渲染报错：${text.slice(0, 200)}`);
  assert.match(text, /秋日散步/);
  assert.match(text, /第 1 \/ 2 页/);
  assert.match(text, /理解：未知/);
  assert.match(text, /翻页只是 GET，不写入/);

  const textNode = body.find((node) => hasClass(node, 'reading-text'));
  assert.ok(textNode, '正文要单独成块');
  assert.equal(textNode.children.length, 0, '正文只能用 textContent，不许塞 HTML 子节点');
  assert.equal(textNode.textContent.includes(SECRET_BODY), true);
});

test('（stub DOM）翻页只发 GET：两侧都不产生写请求', async () => {
  const harness = makeStubDom({material: (path) => materialDoc({page: pageOf(path)})});
  await boot(harness);
  await openReading(harness);
  const before = harness.requests.length;

  click(findButton(bodyOf(harness), '下一页'), '下一页');
  await settle();
  assert.ok(harness.requests.slice(before).some((entry) => entry.path === '/reading/material?page=2'));

  click(findButton(bodyOf(harness), '上一页'), '上一页');
  await settle();
  assert.ok(harness.requests.slice(before).some((entry) => entry.path === '/reading/material?page=1'));

  const after = harness.requests.slice(before);
  assert.deepEqual(after.filter((entry) => entry.method !== 'GET').map((entry) => entry.method + entry.path), [],
    '翻页不许有任何写请求');
});

test('（stub DOM）保存停点：POST 形状精确，成功后用刷新回来的 private_head', async () => {
  let head = HEAD_1;
  const harness = makeStubDom({
    material: (path) => materialDoc({page: pageOf(path), head: head}),
    positions: () => {
      head = HEAD_2;
      return {ok: true, status: 'applied',
        data: {page: 1, source_revision: REV_NEW, private_head: HEAD_2, understanding: 'UNKNOWN', operation_key: 'k'}};
    }
  });
  await boot(harness);
  await openReading(harness);

  click(findButton(bodyOf(harness), '保存阅读停点'), '保存阅读停点');
  await settle();

  const first = posts(harness);
  assert.equal(first.length, 1, '一次意图只发一条 POST');
  assert.equal(first[0].path, '/reading/positions');
  assert.equal(first[0].init.headers['X-CSRF-Token'], 'tok-read');
  assert.deepEqual(Object.keys(first[0].body).sort(),
    ['expected_head', 'operation_key', 'page', 'source_revision']);
  assert.equal(first[0].body.page, 1);
  assert.equal(first[0].body.source_revision, REV_NEW, '材料版本原字节');
  assert.equal(first[0].body.expected_head, HEAD_1);
  assert.match(first[0].body.operation_key, /^uuid-\d+$/);

  /* 写成功后必须重读材料：新的 private_head 才允许下一次写入 */
  assert.ok(harness.requests.some((entry) => entry.method === 'GET' && entry.path === '/reading/material?page=1'),
    '保存停点后要重读材料，拿到新的 private_head');

  click(findButton(bodyOf(harness), '保存阅读停点'), '第二次保存停点');
  await settle();
  const all = posts(harness);
  assert.equal(all.length, 2, all.map((entry) => entry.path + JSON.stringify(entry.body)).join(' | '));
  assert.equal(all[1].body.expected_head, HEAD_2, '不能拿旧 head 连写');
  assert.notEqual(all[1].body.operation_key, all[0].body.operation_key, '上一次已结案 → 这次是新意图、新键');
});

test('（stub DOM）刷新没回来就不写：不拿旧 head 重发停点', async () => {
  let refuseMaterial = false;
  const harness = makeStubDom({
    material: (path) => (refuseMaterial
      ? {__status: 503, ok: false, status: 'unavailable', reason_code: 'SUPPLY_UNAVAILABLE'}
      : materialDoc({page: pageOf(path)})),
    positions: () => {
      refuseMaterial = true;
      return {ok: true, status: 'applied',
        data: {page: 1, source_revision: REV_NEW, private_head: HEAD_2, understanding: 'UNKNOWN'}};
    }
  });
  await boot(harness);
  await openReading(harness);
  click(findButton(bodyOf(harness), '保存阅读停点'), '保存阅读停点');
  await settle();
  assert.equal(posts(harness).length, 1);

  /* 刷新失败 → 屏幕退回“没有读到阅读材料”，停点按钮不在，也就不可能用旧 head 再写 */
  assert.equal(findButton(bodyOf(harness), '保存阅读停点'), undefined);
  assert.match(bodyOf(harness).allText, /没有读到阅读材料/);
  await settle();
  assert.equal(posts(harness).length, 1, '刷新失败后不许再发写请求');
});

test('（stub DOM）原页笔记与疑问：payload 按 kind 白名单发出去', async () => {
  const harness = makeStubDom({});
  await boot(harness);
  await openReading(harness);

  type(inputOfType(harness, 'text'), '第二页的批注', '笔记标题');
  type(textareaFor(harness, '这一页里真正想记下的一段'), NOTE_BODY, '笔记正文');
  click(findButton(bodyOf(harness), '保存这一页的笔记'), '保存笔记');
  await settle();

  const first = posts(harness);
  assert.equal(first.length, 1);
  assert.equal(first[0].path, '/reading/annotations');
  assert.deepEqual(Object.keys(first[0].body).sort(),
    ['body', 'expected_head', 'kind', 'operation_key', 'page', 'source_revision', 'title']);
  assert.equal(first[0].body.kind, 'note');
  assert.equal(first[0].body.page, 1);
  assert.equal(first[0].body.source_revision, REV_NEW);
  assert.equal(first[0].body.expected_head, HEAD_1);
  assert.equal(first[0].body.body, NOTE_BODY);
  assert.equal(first[0].body.operation_key, first[0].body.operation_key.trim());

  type(textareaFor(harness, '哪里没读懂'), '这一段我没读懂', '疑问');
  click(findButton(bodyOf(harness), '记为疑问'), '记为疑问');
  await settle();
  const second = posts(harness)[1];
  assert.ok(second, '疑问也要真的发出去');
  assert.deepEqual(Object.keys(second.body).sort(),
    ['expected_head', 'kind', 'note', 'operation_key', 'page', 'source_revision']);
  assert.equal(second.body.kind, 'unclear');
  assert.equal(second.body.note, '这一段我没读懂');
});

test('（stub DOM）PAGE_NOT_ACQUIRED：提示先保存停点，不自动重发', async () => {
  const harness = makeStubDom({
    annotations: () => ({__status: 400, ok: false, status: 'rejected', reason_code: 'PAGE_NOT_ACQUIRED'})
  });
  await boot(harness);
  await openReading(harness);
  type(inputOfType(harness, 'text'), '标题', '笔记标题');
  type(textareaFor(harness, '这一页里真正想记下的一段'), NOTE_BODY, '笔记正文');
  click(findButton(bodyOf(harness), '保存这一页的笔记'), '保存笔记');
  await settle();

  const text = bodyOf(harness).allText;
  assert.match(text, /这一版这一页还没取得/);
  assert.match(text, /先保存阅读停点/);
  assert.ok(findButton(bodyOf(harness), '保存第 1 页停点'), '要能一键去保存这一页的停点');
  assert.equal(posts(harness).length, 1, '被拒后不许自动重发');
});

test('（stub DOM）冲突：保留草稿，不自动覆盖，也不自动重发', async () => {
  let attempt = 0;
  const harness = makeStubDom({
    annotations: () => {
      attempt += 1;
      return {__status: 409, ok: false, status: 'conflict', reason_code: 'READING_HEAD_CONFLICT'};
    }
  });
  await boot(harness);
  await openReading(harness);
  type(inputOfType(harness, 'text'), '会冲突的标题', '笔记标题');
  type(textareaFor(harness, '这一页里真正想记下的一段'), NOTE_BODY, '笔记正文');
  click(findButton(bodyOf(harness), '保存这一页的笔记'), '保存笔记');
  await settle();

  assert.equal(attempt, 1);
  assert.equal(posts(harness).length, 1, '冲突后不许自动重发');
  const text = bodyOf(harness).allText;
  assert.match(text, /这次没有写入/);
  assert.match(text, /草稿还在下面/);
  assert.ok(findButton(bodyOf(harness), '重新读取材料'), '要给出显式重读入口');

  /* 草稿还在内存里：重渲染后输入框仍挂着原文（输入用 value 属性重建） */
  const bodyArea = textareaFor(harness, '这一页里真正想记下的一段');
  assert.equal(bodyArea.value, NOTE_BODY);
  assert.equal(inputOfType(harness, 'text').getAttribute('value'), '会冲突的标题');
});

test('（stub DOM）结果未知：原键不换，查询走 /reading/operations/<key>', async () => {
  const harness = makeStubDom({
    annotations: () => ({ok: true, status: 'unknown'}),
    operation: () => ({ok: true, status: 'applied',
      data: {operation_key: 'uuid-1', action: 'annotate', page: 1, source_revision: REV_NEW, effect_state: 'applied'}})
  });
  await boot(harness);
  await openReading(harness);
  type(inputOfType(harness, 'text'), '标题', '笔记标题');
  type(textareaFor(harness, '这一页里真正想记下的一段'), NOTE_BODY, '笔记正文');
  click(findButton(bodyOf(harness), '保存这一页的笔记'), '保存笔记');
  await settle();

  const firstKey = posts(harness)[0].body.operation_key;
  assert.equal(posts(harness).length, 1);
  assert.match(bodyOf(harness).allText, /结果未知|没有被确认/);

  /* 再点一次：同一个未决意图复用原键 */
  click(findButton(bodyOf(harness), '保存这一页的笔记'), '再次保存笔记');
  await settle();
  assert.equal(posts(harness)[1].body.operation_key, firstKey, '结果未知时不许换键');

  click(findButton(bodyOf(harness), '查询这次操作'), '查询这次操作');
  await settle();
  const queries = harness.requests.filter((entry) => entry.method === 'GET' &&
    entry.path.indexOf('/reading/operations/') === 0);
  assert.equal(queries.length, 1);
  assert.equal(queries[0].path, '/reading/operations/' + firstKey);
  assert.equal(harness.requests.some((entry) => entry.path === '/operations/' + firstKey), false,
    '阅读的键不能落到购物的 operation 接口');
  assert.match(bodyOf(harness).allText, /这条键属于：annotate/);
});

test('（stub DOM）对账的 typed 回答：not_found 是答案，不说成“查询失败”', async () => {
  const harness = makeStubDom({
    annotations: () => ({ok: true, status: 'unknown'}),
    operation: () => ({__status: 404, ok: false, status: 'not_found',
      reason_code: 'READING_OPERATION_NOT_FOUND', data: {operation_key: 'uuid-1', action: 'unknown'}})
  });
  await boot(harness);
  await openReading(harness);
  type(inputOfType(harness, 'text'), '标题', '笔记标题');
  type(textareaFor(harness, '这一页里真正想记下的一段'), NOTE_BODY, '笔记正文');
  click(findButton(bodyOf(harness), '保存这一页的笔记'), '保存笔记');
  await settle();
  click(findButton(bodyOf(harness), '查询这次操作'), '查询这次操作');
  await settle();

  const text = bodyOf(harness).allText;
  assert.match(text, /服务没有这个操作键的记录（NOT_FOUND）/);
  assert.equal(text.includes('查询没有成功'), false, 'typed not_found 不能说成查询失败');
  assert.deepEqual(JSON.parse(harness.storage.getItem(P.SESSION_KEYS.unresolved) || '[]'), [],
    '服务明确说没有这条记录才算结案');
});

test('（stub DOM）正文与笔记不进 sessionStorage：只留 key/at/path', async () => {
  const harness = makeStubDom({annotations: () => ({ok: true, status: 'unknown'})});
  await boot(harness);
  await openReading(harness);
  type(inputOfType(harness, 'text'), '私人笔记标题', '笔记标题');
  type(textareaFor(harness, '这一页里真正想记下的一段'), NOTE_BODY, '笔记正文');
  click(findButton(bodyOf(harness), '保存这一页的笔记'), '保存笔记');
  await settle();
  type(textareaFor(harness, '把本地正文粘进来'), SECRET_BODY + '%%PAGE%%第二页', '导入正文');
  click(findButton(bodyOf(harness), '确认替换当前材料'), '确认替换');
  await settle();
  click(findButton(harness.document.getElementById('sheet-host'), '替换'), '替换确认');
  await settle();

  const dump = harness.storage.dump();
  assert.equal(dump.includes(SECRET_BODY), false, '正文不能进 sessionStorage');
  assert.equal(dump.includes(NOTE_BODY), false, '笔记不能进 sessionStorage');
  assert.equal(dump.includes('私人笔记标题'), false);
  assert.equal(dump.includes('"content"'), false, '完整 payload 不能进 sessionStorage');

  const pending = JSON.parse(harness.storage.getItem(P.SESSION_KEYS.pending) || '{}');
  for (const scope of Object.keys(pending)) {
    assert.deepEqual(Object.keys(pending[scope]).sort(), ['at', 'key', 'path'], `scope ${scope} 只许 key/at/path`);
  }
  const unresolved = JSON.parse(harness.storage.getItem(P.SESSION_KEYS.unresolved) || '[]');
  for (const entry of unresolved) {
    assert.deepEqual(Object.keys(entry).sort(), ['at', 'key', 'path', 'scope']);
  }
});

test('（stub DOM）导入正文：确认弹层后 POST 只带三项，%%PAGE%% 分页照发', async () => {
  const harness = makeStubDom({});
  await boot(harness);
  await openReading(harness);

  const content = '第一页\n%%PAGE%%\n第二页';
  type(textareaFor(harness, '把本地正文粘进来'), content, '导入正文');
  click(findButton(bodyOf(harness), '确认替换当前材料'), '确认替换');

  const sheet = harness.document.getElementById('sheet-host');
  assert.match(sheet.allText, /替换当前阅读材料/);
  assert.match(sheet.allText, /needs_reread/);
  click(findButton(sheet, '替换'), '弹层里的替换');
  await settle();

  const sent = posts(harness);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].path, '/reading/material');
  assert.deepEqual(Object.keys(sent[0].body).sort(), ['content', 'expected_revision', 'operation_key']);
  assert.equal(sent[0].body.content, content);
  assert.equal(sent[0].body.expected_revision, REV_NEW);
  /* 新版本落定后回到第 1 页重读 */
  assert.ok(harness.requests.some((entry) => entry.method === 'GET' && entry.path === '/reading/material?page=1'));
});

test('（stub DOM）正文注入不执行：<img>/<script> 只是文本', async () => {
  const payload = '<img src=x onerror="globalThis.__pwned=1"><script>globalThis.__pwned=2</script>尾巴';
  const doc = materialDoc({});
  doc.data.text = payload;
  const harness = makeStubDom({material: () => doc});
  await boot(harness);
  await openReading(harness);

  const body = bodyOf(harness);
  const textNode = body.find((node) => hasClass(node, 'reading-text'));
  assert.ok(textNode, '正文块要在');
  assert.equal(textNode.textContent, payload, '正文原样进 textContent');
  assert.equal(textNode.children.length, 0, '不许生成子节点');
  assert.equal(body.findAll((node) => node.tagName === 'IMG' || node.tagName === 'SCRIPT').length, 0,
    '注入的标签不能变成真实元素');
  assert.equal(harness.sandbox.__pwned, undefined);
  assert.equal(globalThis.__pwned, undefined);
});

test('（stub DOM）诚实状态：禁用 / 不可用材料都不补演示正文', async () => {
  const disabled = makeStubDom({readingStatus: 'disabled'});
  await boot(disabled);
  await openReading(disabled);
  assert.match(bodyOf(disabled).allText, /这个应用在当前实例里被禁用/);
  assert.equal(disabled.requests.some((entry) => entry.path.indexOf('/reading/material') === 0), false,
    '应用被禁用时不去读材料');

  /* C 的真实形状：503 + ok:false + reason_code + data.available=false */
  const unavailable = makeStubDom({
    material: () => ({__status: 503, ok: false, status: 'unavailable',
      reason_code: 'READING_MATERIAL_UNAVAILABLE', data: {available: false}})
  });
  await boot(unavailable);
  await openReading(unavailable);
  const text = bodyOf(unavailable).allText;
  assert.match(text, /这份材料当前不可用/);
  assert.match(text, /阅读材料当前不可用/);
  assert.equal(bodyOf(unavailable).find((node) => hasClass(node, 'reading-text')), null,
    '不可用时没有正文块');
  assert.equal(findButton(bodyOf(unavailable), '保存阅读停点'), undefined);
  const importBtn = findButton(bodyOf(unavailable), '确认替换当前材料');
  assert.ok(importBtn);
  assert.equal(importBtn.disabled, true, '没有可用版本时导入按钮禁用');

  /* 页码越界：409 + PAGE_NOT_PRESENT，屏上要写出原因而不是只说“读取失败” */
  const outOfRange = makeStubDom({
    material: () => ({__status: 409, ok: false, status: 'rejected',
      reason_code: 'PAGE_NOT_PRESENT', data: {page: 9, pages: 2}})
  });
  await boot(outOfRange);
  await openReading(outOfRange);
  assert.match(bodyOf(outOfRange).allText, /这份材料没有这一页/);
  assert.match(bodyOf(outOfRange).allText, /没有读到阅读材料/);
});

test('（stub DOM）旧版本锚点不悄悄跳页：先问一句，再按当前版本看', async () => {
  const harness = makeStubDom({
    material: (path) => materialDoc({page: pageOf(path)}),
    notes: {
      ok: true, status: 'applied', head: HEAD_1,
      items: [{
        id: NOTE_ID, title: '散步', body: '那天风很大。', revision: 1,
        updated_at: '2026-10-22T10:00:00',
        reading_anchor: {source_id: SOURCE_ID, source_revision: REV_OLD, page: 2}
      }]
    }
  });
  await boot(harness);
  click(findButton(bodyOf(harness), '笔记'), '主屏的笔记入口');
  await settle();
  assert.match(bodyOf(harness).allText, /锚在阅读第 2 页/);

  const anchorButton = findButton(bodyOf(harness), '回到阅读第 2 页');
  assert.ok(anchorButton, '普通笔记要有可选的阅读锚点入口');
  click(anchorButton, '回到阅读第 2 页');
  await settle();

  const materialReads = harness.requests.filter((entry) => entry.path.indexOf('/reading/material') === 0)
    .map((entry) => entry.path);
  assert.ok(materialReads.includes('/reading/material'), '先读当前版本');
  assert.equal(materialReads.includes('/reading/material?page=2'), false,
    '版本不一致时不许直接跳到当前版本的第 2 页');
  assert.match(bodyOf(harness).allText, /不会自己跳到当前版本的第 2 页/);

  click(findButton(bodyOf(harness), '按当前版本看第 2 页'), '按当前版本看第 2 页');
  await settle();
  assert.ok(harness.requests.some((entry) => entry.path === '/reading/material?page=2'),
    '用户显式点了才去读第 2 页');
});

/* ---------------- 4. 窄屏静态规则（不是像素验证） ---------------- */

test('390：正文块可换行、翻页与导入按钮不写死宽度', () => {
  const block = css.slice(css.indexOf('.reading-text {'), css.indexOf('.reading-pager {'));
  assert.match(block, /white-space: pre-wrap;/);
  assert.match(block, /overflow-wrap: anywhere;/);

  const narrow = css.slice(css.indexOf('@media (max-width: 430px)'));
  assert.match(narrow, /\.reading-pager \.btn \{ flex: 1 1 auto; \}/);
  assert.match(narrow, /\.reading-pager-input \{ width: 100%; \}/);
  assert.match(css, /\.reading-annotation\.is-stale/);

  const widths = [...block.matchAll(/(?:^|[^-])width:\s*(\d+)px/g)].map((hit) => Number(hit[1]));
  assert.deepEqual(widths, [], `阅读正文块不该写死 px 宽度：${widths.join(',')}`);
});
