/**
 * NyAiro v0.4 · 虚拟手机前端（components/phone/web）定向检查。
 *
 * 只检查本次交付的前端：静态资源约束、XSS/存储边界、CSRF 闸门、
 * mutation 结果判定、可读状态与不造数据。不启动服务、不碰其他组件。
 *
 * 运行：node --test tests/test_phone_ui.mjs
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

/** 在没有 DOM 的 vm 里加载 app.js，取出它暴露的纯函数。 */
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

test('静态资源是三个本地文件，没有外部依赖', () => {
  assert.match(html, /<script src="app\.js" defer><\/script>/);
  assert.match(html, /<link rel="stylesheet" href="style\.css">/);
  assert.doesNotMatch(html, /(?:src|href)="https?:\/\//i);
  assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)/i, '不允许内联脚本');
  assert.match(html, /http-equiv="Content-Security-Policy"/);
  assert.match(html, /connect-src 'self'/);
});

test('index.html 没有内联事件处理器', () => {
  assert.doesNotMatch(html, /\son[a-z]+\s*=\s*["']/i);
});

test('app.js 不用字符串拼 HTML，也不执行动态代码', () => {
  for (const banned of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function']) {
    assert.equal(src.includes(banned), false, `app.js 不能使用 ${banned}`);
  }
});

test('不写 localStorage，sessionStorage 只有显式允许的三个键', () => {
  assert.doesNotMatch(src, /\blocalStorage\s*(?:\.|\[)/);
  assert.doesNotMatch(html, /\blocalStorage\s*(?:\.|\[)/);
  assert.doesNotMatch(src, /sessionStorage\s*(?:\.|\[)/, '所有会话存储都要走注入的 storage 适配器');
  const keys = [...src.matchAll(/'(nyairo\.phone\.[a-z0-9.]+)'/g)].map((match) => match[1]);
  const allowed = Object.values(P.SESSION_KEYS).sort();
  assert.deepEqual([...new Set(keys)].sort(), allowed);
});

test('一次意图只用一个操作键，键里只有 key/at/path 三个字段', () => {
  const storage = memoryStorage();
  let counter = 0;
  const ops = P.createOpStore(storage, () => `key-${++counter}`);
  const first = ops.ensure('note:create', {path: '/notes'});
  const again = ops.ensure('note:create', {path: '/notes'});
  assert.equal(again.key, first.key, '同一 scope 必须复用同一个键');
  assert.equal(again.reused, true);
  const other = ops.ensure('purchase:catalog-1', {path: '/purchases'});
  assert.notEqual(other.key, first.key);

  const persisted = JSON.parse(storage.getItem(P.SESSION_KEYS.pending));
  assert.deepEqual(Object.keys(persisted['note:create']).sort(), ['at', 'key', 'path']);
  assert.equal(persisted['note:create'].path, '/notes');
});

test('结果未知时保留同一个键，并进入未确认列表；恢复后仍在', () => {
  const storage = memoryStorage();
  let counter = 0;
  const ops = P.createOpStore(storage, () => `key-${++counter}`);
  const entry = ops.ensure('purchase:a', {path: '/purchases'});
  ops.markUnknown('purchase:a', {path: '/purchases'});
  const unresolved = ops.listUnresolved();
  assert.equal(unresolved.length, 1);
  assert.equal(unresolved[0].key, entry.key);
  assert.equal(unresolved[0].path, '/purchases');
  assert.equal(ops.ensure('purchase:a').key, entry.key, '未知结果不能换键');

  const restored = P.createOpStore(storage, () => 'never-used');
  assert.equal(restored.listUnresolved().length, 1);
  assert.equal(restored.ensure('purchase:a').key, entry.key);
});

test('最终结果释放操作键；损坏的会话数据不拖垮界面', () => {
  const storage = memoryStorage();
  let counter = 0;
  const ops = P.createOpStore(storage, () => `key-${++counter}`);
  const first = ops.ensure('note:create');
  ops.markUnknown('note:create');
  ops.resolve('note:create');
  assert.equal(ops.listUnresolved().length, 0);
  const next = ops.ensure('note:create');
  assert.notEqual(next.key, first.key, '结案后重新发起是新意图');

  const broken = memoryStorage();
  broken.setItem(P.SESSION_KEYS.pending, '{');
  broken.setItem(P.SESSION_KEYS.unresolved, '[]');
  const recovered = P.createOpStore(broken, () => 'fresh-key');
  assert.equal(recovered.listUnresolved().length, 0);
  assert.equal(recovered.ensure('use:x').key, 'fresh-key');
});

test('导航位置只记屏幕名，非法值回落主屏', () => {
  const storage = memoryStorage();
  const nav = P.createNavStore(storage);
  nav.save({screen: 'wallet'});
  assert.equal(JSON.stringify(nav.read()), JSON.stringify({screen: 'wallet', detail: ''}));
  nav.save({screen: '../../etc/passwd'});
  assert.equal(nav.read().screen, 'home');
  storage.setItem(P.SESSION_KEYS.nav, 'not json');
  assert.equal(nav.read(), null);
});

test('写请求必须带 CSRF 头，读请求不带', () => {
  const get = P.buildRequestInit({method: 'GET', csrf: 'token-abc'});
  assert.equal(get.headers['X-CSRF-Token'], undefined);
  assert.equal(get.body, undefined);

  assert.throws(() => P.buildRequestInit({method: 'POST', body: {a: 1}}), /CSRF_MISSING/);
  assert.throws(() => P.buildRequestInit({method: 'POST', csrf: '   '}), /CSRF_MISSING/);

  const post = P.buildRequestInit({method: 'POST', csrf: 'token-abc', body: {catalog_item_id: 'a', operation_key: 'k'}});
  assert.equal(post.headers['X-CSRF-Token'], 'token-abc');
  assert.equal(post.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(post.body), {catalog_item_id: 'a', operation_key: 'k'});
});

test('state 没有 csrf_token 时整机只读，且不自行绕过', () => {
  const readOnly = P.stateView({ok: true, read_only: false, instance: {name: '妹妹', subject: 'life-1', mode: 'demo'}, apps: {}});
  assert.equal(readOnly.phase, 'readonly');
  assert.equal(readOnly.csrf, null);
  const guard = P.canWrite(readOnly);
  assert.equal(guard.ok, false);
  assert.equal(guard.reason, 'CSRF_MISSING');
  assert.match(guard.message, /不会绕过/);

  const writable = P.stateView({ok: true, read_only: false, csrf_token: 'tok-1234', instance: {name: '妹妹'}, apps: {}});
  assert.equal(writable.phase, 'online');
  assert.equal(P.canWrite(writable).ok, true);

  const declared = P.stateView({ok: true, read_only: true, csrf_token: 'tok-1234', apps: {}});
  assert.equal(declared.phase, 'readonly');
  assert.equal(P.canWrite(declared).reason, 'READ_ONLY');
});

test('state 里缺失的应用被标为未报告，不冒充可用', () => {
  const view = P.stateView({ok: true, read_only: false, csrf_token: 't', apps: {notes: {status: 'available', data: {}}}});
  assert.equal(view.apps.notes.status, 'available');
  for (const key of ['references', 'reading', 'wallet', 'shop', 'inventory', 'body']) {
    assert.equal(view.apps[key].status, 'missing', `${key} 不能默认可用`);
    assert.equal(view.apps[key].reasonCode, 'APP_NOT_REPORTED');
  }
  assert.equal(P.stateView({ok: false}).phase, 'error');
});

test('mutation 结果绝不默认成功', () => {
  const apply = (res) => P.deriveMutationOutcome(res);
  assert.equal(apply({kind: 'http', status: 200, doc: {ok: true}}).status, 'unknown', '2xx 但没有 status 不能算成功');
  assert.equal(apply({kind: 'http', status: 200, doc: {ok: true, status: 'applied'}}).status, 'applied');
  assert.equal(apply({kind: 'http', status: 200, doc: {ok: true, status: 'already_applied'}}).status, 'already_applied');
  assert.equal(apply({kind: 'http', status: 200, doc: {ok: true, status: 'unknown'}}).status, 'unknown');
  assert.equal(apply({kind: 'http', status: 409, doc: {ok: false, reason_code: 'CONFLICT'}}).status, 'conflict');
  assert.equal(apply({kind: 'http', status: 400, doc: {ok: false, reason_code: 'INSUFFICIENT_FUNDS'}}).status, 'rejected');
  assert.equal(apply({kind: 'http', status: 402, doc: {ok: false, reason_code: 'INSUFFICIENT_FUNDS'}}).status, 'rejected');
  assert.equal(apply({kind: 'http', status: 500, doc: null}).status, 'unknown');
  assert.equal(apply({kind: 'network', cause: 'timeout'}).status, 'unknown');
  assert.equal(apply({kind: 'unparsable', status: 200}).status, 'unknown');
  assert.equal(apply({kind: 'blocked', reason: 'CSRF_MISSING'}).status, 'blocked');

  assert.equal(P.deriveReadOutcome({kind: 'network'}).status, 'offline');
  assert.equal(P.deriveReadOutcome({kind: 'http', status: 404, doc: null}).status, 'error');
  assert.equal(P.deriveReadOutcome({kind: 'http', status: 200, doc: {ok: false, reason_code: 'X'}}).status, 'rejected');
  const ok = P.deriveReadOutcome({kind: 'http', status: 200, doc: {ok: true, items: []}});
  assert.equal(ok.status, 'ok');
  assert.ok(ok.doc);
});

test('原因码有中文可读文本，未知码原样保留', () => {
  assert.equal(P.describeReason('INSUFFICIENT_FUNDS'), '余额不足，未扣款');
  assert.equal(P.describeReason('ITEM_NOT_DELIVERED'), '物品还没送达，不能提前使用');
  assert.match(P.describeReason('SOMETHING_NEW'), /原因码/);
  assert.match(P.describeReason('SOMETHING_NEW'), /SOMETHING_NEW/);
  assert.equal(P.reasonText('商店今天关了'), '商店今天关了');
  assert.equal(P.reasonText('SHOP_CLOSED'), '商店当前未营业');
  assert.equal(P.reasonText(''), '');
});

test('金额只格式化服务给的数，缺字段返回 null', () => {
  assert.equal(P.formatMoney('CNY', 12.5), '12.50 CNY');
  assert.equal(P.formatMoney('CNY', -3), '-3.00 CNY');
  assert.equal(P.formatMoney(null, 3), null);
  assert.equal(P.formatMoney('', 3), null);
  assert.equal(P.formatMoney('CNY', null), null);
  assert.equal(P.formatMoney('CNY', 'abc'), null);
  assert.equal(P.formatMoney('CNY', Infinity), null);
});

test('显示文本清掉控制字符并遮住本机路径与凭据', () => {
  assert.equal(P.sanitizeLine('a\u0007b', 40), 'ab');
  const path = P.sanitizeLine('C:\\Users\\Ann\\secret\\notes.md');
  assert.match(path, /已隐藏的本机路径/);
  assert.equal(path.includes('Ann'), false);
  const posix = P.sanitizeLine('/home/ann/data/phone.db');
  assert.match(posix, /已隐藏的本机路径/);
  assert.equal(posix.includes('ann'), false);
  assert.match(P.sanitizeLine('Bearer abcdefghijklmnop'), /已隐藏的凭据/);
  assert.match(P.sanitizeLine('a'.repeat(0) + 'f'.repeat(64)), /已隐藏的长标识/);
  assert.equal(P.sanitizeLine('  今天写了半页草稿  ', 40), '今天写了半页草稿');
  assert.ok(P.sanitizeLine('x'.repeat(300), 20).length <= 20);
});

test('只读字段渲染会剔除密钥、路径与嵌套对象', () => {
  const entries = P.displayFieldEntries({
    csrf_token: 'secret-value',
    home_path: 'C:\\Users\\Ann',
    note: '一切正常',
    amount: 3,
    ok: true,
    nested: {a: 1}
  }, 8);
  assert.equal(JSON.stringify(Array.from(entries, (entry) => entry.key)), JSON.stringify(['note', 'amount', 'ok']));
  for (const entry of entries) {
    assert.equal(/token|path|home|csrf/i.test(entry.key), false);
    assert.equal(/secret-value|Ann/.test(entry.text), false);
  }
  assert.equal(entries.find((entry) => entry.key === 'ok').text, '是');
});

test('笔记输入有长度上限，空标题被挡下', () => {
  const empty = P.validateNoteDraft({title: '   ', body: '正文'});
  assert.equal(empty.ok, false);
  assert.ok(empty.errors.includes('标题不能为空'));

  const longTitle = P.validateNoteDraft({title: '标'.repeat(81), body: ''});
  assert.ok(longTitle.errors.includes('标题最多 80 字'));

  const longBody = P.validateNoteDraft({title: '标题', body: '字'.repeat(4001)});
  assert.ok(longBody.errors.includes('正文最多 4000 字'));

  const good = P.validateNoteDraft({title: '  半页草稿  ', body: '第一行\r\n第二行'});
  assert.equal(good.ok, true);
  assert.equal(good.value.title, '半页草稿');
  assert.equal(good.value.body, '第一行\n第二行');
});

test('修订笔记带 note_id，新建不带；同标题同 head 的两则备忘录按 id 区分', () => {
  const memoA = {note_id: 'n-1', title: '同名标题', body: 'A 备忘', operation_key: 'key-a', expected_head: 'h-7'};
  const memoB = {note_id: 'n-2', title: '同名标题', body: 'B 备忘', operation_key: 'key-b', expected_head: 'h-7'};
  const editA = P.buildNotePayload(memoA);
  const editB = P.buildNotePayload(memoB);

  assert.equal(editA.note_id, 'n-1');
  assert.equal(editB.note_id, 'n-2');
  assert.equal(editA.title, editB.title, '两则标题相同，界面不能靠标题认人');
  assert.equal(editA.expected_head, 'h-7');
  assert.equal(editB.expected_head, 'h-7');
  assert.equal(editA.operation_key, 'key-a');
  assert.equal(editB.operation_key, 'key-b');
  assert.notEqual(editA.body, editB.body);
  assert.equal(
    JSON.stringify(Object.keys(editA).sort()),
    JSON.stringify(['body', 'expected_head', 'note_id', 'operation_key', 'title'])
  );

  const created = P.buildNotePayload({title: '新的一页', body: '', operation_key: 'key-c'});
  assert.equal(Object.prototype.hasOwnProperty.call(created, 'note_id'), false, '新建不能带 note_id');
  assert.equal(Object.prototype.hasOwnProperty.call(created, 'expected_head'), false, '没有 head 就不传');
  assert.equal(created.operation_key, 'key-c');

  const blankId = P.buildNotePayload({note_id: '   ', title: 'x', body: '', operation_key: 'k'});
  assert.equal(Object.prototype.hasOwnProperty.call(blankId, 'note_id'), false);
  // 服务给的 id 原样回传：不 trim，也不悄悄剔掉控制字符（含控制字符直接判无效、不放进 payload）。
  const spacedId = P.buildNotePayload({note_id: '  n-9  ', title: 'x', body: '', operation_key: 'k'});
  assert.equal(spacedId.note_id, '  n-9  ');
  const idWithControl = P.buildNotePayload({note_id: 'n-3\u0000', title: 'x', body: '', operation_key: 'k'});
  assert.equal(Object.prototype.hasOwnProperty.call(idWithControl, 'note_id'), false);
  assert.equal(P.buildNotePayload(null).note_id, undefined);
  assert.match(P.noteEditBlocked({mode: 'edit', noteId: ''}), /条目 id/);
  assert.match(P.noteEditBlocked({mode: 'edit', noteId: '   '}), /条目 id/);
  assert.equal(P.noteEditBlocked({mode: 'edit', noteId: 'n-1'}), '');
  assert.equal(P.noteEditBlocked({mode: 'create', noteId: null}), '');
  assert.equal(P.noteEditBlocked(null), '');

  // 接线层：payload 的 note_id 只能来自 editor.noteId，不能由标题/正文反查。
  assert.match(src, /note_id: editor\.mode === 'edit' \? editor\.noteId : ''/);
  assert.doesNotMatch(src, /note_id:\s*[^,;\n]*(?:title|body)/);
});

test('收藏只收指针字段，三个输入都有上限', () => {
  const empty = P.validateReferenceDraft({});
  assert.equal(empty.ok, false);
  assert.equal(empty.errors.length, 3);

  const long = P.validateReferenceDraft({
    source_id: 's'.repeat(201),
    source_revision: 'r'.repeat(121),
    label: 'l'.repeat(61)
  });
  assert.equal(JSON.stringify(Array.from(long.errors)), JSON.stringify(['出处标识最多 200 字', '来源版本最多 120 字', '名字最多 60 字']));

  const good = P.validateReferenceDraft({source_id: 'supply:artifact/42', source_revision: '3', label: '想读的那篇'});
  assert.equal(good.ok, true);
  assert.deepEqual(Object.keys(good.value).sort(), ['label', 'source_id', 'source_revision']);
  assert.equal(good.value.source_id, 'supply:artifact/42');
});

test('商店目录照实展示，不可购买时保持阻塞与原因', () => {
  const blocked = P.shopView({
    ok: true,
    status: 'ok',
    data: {currency: 'CNY', catalog: [{catalog_item_id: 'lamp', name: '小台灯', description: '晚上画画用', price: 18, usable_kind: 'light'}], purchase_available: false, blocked_reason: 'SHOP_CLOSED'}
  });
  assert.equal(blocked.items.length, 1);
  assert.equal(blocked.items[0].name, '小台灯');
  assert.equal(blocked.items[0].price, 18);
  assert.equal(blocked.purchaseAvailable, false);
  assert.equal(blocked.blockedText, '商店当前未营业');

  const open = P.shopView({ok: true, data: {currency: 'CNY', catalog: [], purchase_available: true}});
  assert.equal(open.purchaseAvailable, true);
  assert.equal(open.items.length, 0);
  assert.equal(open.itemsMissing, false);

  const missing = P.shopView({ok: true, data: {currency: 'CNY'}});
  assert.equal(missing.itemsMissing, true);
  assert.equal(Array.from(missing.items).length, 0);

  const noPrice = P.shopView({ok: true, data: {currency: 'CNY', catalog: [{catalog_item_id: 'a', name: 'a'}], purchase_available: true}});
  assert.equal(noPrice.items[0].price, null, '价格缺失不能补一个 0');
});

test('物品只能用服务声明 usable 的那件', () => {
  const view = P.inventoryView({
    ok: true,
    data: {items: [
      {item_instance_id: 'i1', name: '落地灯', ownership: 'self', location: 'room', delivery_status: 'delivered', usable: true},
      {item_instance_id: 'i2', name: '书', delivery_status: 'in_transit', usable: false, blocked_reason: 'ITEM_NOT_DELIVERED'},
      {item_instance_id: 'i3', name: '杯子', usable: 'yes'}
    ]}
  });
  assert.equal(view.items.length, 3);
  assert.equal(view.items[0].usable, true);
  assert.equal(view.items[0].blocked, '');
  assert.equal(view.items[1].usable, false);
  assert.match(view.items[1].blocked, /还没送达/);
  assert.equal(view.items[1].deliveryLabel, '运送中');
  assert.equal(view.items[2].usable, false, 'usable 必须是布尔 true');
  assert.ok(view.items[2].blocked.length > 0);

  const missing = P.inventoryView({ok: true, data: {}});
  assert.equal(missing.itemsMissing, true);
});

test('笔记列表保留 head；缺失时明确说明冲突保护不可用', () => {
  const withHead = P.noteListView({ok: true, head: 'h-7', items: [{id: 'n1', title: '标题', body: '正文', revision: 2, updated_at: '2026-10-17T09:00:00'}]});
  assert.equal(withHead.head, 'h-7');
  assert.equal(withHead.items[0].revision, 2);
  assert.equal(withHead.itemsMissing, false);

  const noHead = P.noteListView({ok: true, items: []});
  assert.equal(noHead.head, null);
  assert.equal(noHead.items.length, 0);
  assert.equal(P.noteListView({ok: true}).itemsMissing, true);
});

test('时钟与日期用真实时间格式，不写死', () => {
  const fixed = new Date(2026, 9, 17, 9, 5, 0);
  assert.equal(P.clockText(fixed), '09:05');
  assert.match(P.dateText(fixed), /^10月17日 周/);
  assert.equal(P.formatWhen('2026-10-17T09:05:00'), '2026-10-17 09:05');
  assert.equal(P.formatWhen(''), '');
  assert.equal(P.formatWhen(0), '');
  assert.equal(P.formatWhen('不是时间'), '不是时间');
});

test('界面引用的元素 id 都在 index.html 里', () => {
  const ids = [...src.matchAll(/\$\('([a-z0-9-]+)'\)/g)].map((match) => match[1]);
  assert.ok(ids.length >= 12, `只找到 ${ids.length} 个 id 引用`);
  for (const id of new Set(ids)) {
    assert.match(html, new RegExp(`id="${id}"`), `index.html 缺少 id="${id}"`);
  }
});

test('窄屏规则让手机全屏，并尊重减少动效', () => {
  assert.match(css, /--cream-screen:\s*#fffaf3/);
  assert.match(css, /--berry:\s*#b33554/);
  assert.match(css, /--mint:\s*#3d8f78/);
  const narrow = css.match(/@media \(max-width: 900px\) \{[\s\S]*?\n\}/);
  assert.ok(narrow, '缺少窄屏断点');
  assert.match(narrow[0], /\.sidebar \{ display: none;/);
  assert.match(narrow[0], /height: 100dvh/);
  assert.match(css, /prefers-reduced-motion: reduce/);
});

test('前端不藏伪数据，也不暴露本机绝对路径', () => {
  assert.doesNotMatch(src, /\bMOCK\b|mockData|fakeData|lorem ipsum/);
  assert.equal(P.API_BASE, '/api/phone');
  for (const [name, text] of [['index.html', html], ['style.css', css], ['app.js', src]]) {
    assert.equal(/[A-Za-z]:\\{1,2}(?:Users|nyairo|NYAIRO)/.test(text), false, `${name} 不能带本机绝对路径`);
    assert.equal(/\/home\/[a-z0-9_]+/i.test(text), false, `${name} 不能带 home 路径`);
  }
});

test('导出的是纯函数集合，加载时不要求 DOM', () => {
  assert.equal(typeof sandbox.document, 'undefined', 'vm 沙箱里没有 document');
  const expected = [
    'sanitizeLine', 'sanitizeText', 'reasonText', 'describeReason', 'formatMoney', 'formatWhen',
    'clockText', 'csrfFromState', 'stateView', 'canWrite', 'deriveReadOutcome',
    'deriveMutationOutcome', 'buildRequestInit', 'noteListView', 'shopView', 'inventoryView',
    'validateNoteDraft', 'buildNotePayload', 'noteEditBlocked', 'validateReferenceDraft', 'displayFieldEntries', 'createOpStore',
    'createNavStore', 'makeOperationKey',
    'protocolId', 'safeLabel', 'scopeKey', 'apiPath', 'buildUsePayload', 'buildPurchasePayload',
    'isReadSuccessStatus', 'readStatusWarning'
  ];
  for (const name of expected) {
    assert.equal(typeof P[name], 'function', `缺少导出 ${name}`);
  }
  assert.deepEqual([...P.APP_ORDER].sort(), ['body', 'inventory', 'notes', 'reading', 'references', 'shop', 'wallet']);
  assert.equal(JSON.stringify(Array.from(P.MUTATION_STATUSES)), JSON.stringify(['applied', 'already_applied', 'rejected', 'unknown', 'conflict']));
});

/* ---------------- 真实 schema ID 负控：网络 payload 必须是服务原字节 ---------------- */

const HEX64 = () => 'a3f5'.repeat(16);
const ITEM_ID = 'item-' + HEX64();
const CATALOG_ID = 'catalog:' + '9c1d'.repeat(28);
const NOTE_ID = 'note-' + '5b2f'.repeat(16);
const HEAD = 'artifact-version:' + '7e11'.repeat(8);

test('库存 item-<64hex>：view id 与 POST /items/use payload 原字节不变，展示另用安全标签', () => {
  assert.equal(ITEM_ID.length, 69);
  const view = P.inventoryView({
    ok: true,
    data: {items: [
      {item_instance_id: ITEM_ID, name: '点心', catalog_item_id: CATALOG_ID, ownership: 'self', location: 'home', delivery_status: 'delivered', usable: true}
    ]}
  });
  const item = view.items[0];
  assert.equal(item.id, ITEM_ID, 'view 的 id 必须是服务原字节');
  assert.equal(item.idOk, true);
  assert.equal(item.catalogId, CATALOG_ID);
  assert.match(item.idLabel, /已隐藏的长标识/);
  assert.equal(item.idLabel.includes(ITEM_ID), false, '展示标签不能带原 id');
  assert.notEqual(item.idLabel, item.id, '展示标签不能复用业务 id');
  assert.match(item.catalogLabel, /已隐藏的长标识/);

  const built = P.buildUsePayload({item_instance_id: item.id, operation_key: 'key-use-1'});
  assert.equal(built.ok, true);
  assert.equal(built.payload.item_instance_id, ITEM_ID);
  assert.equal(built.payload.item_instance_id.length, 69);
  assert.deepEqual(Object.keys(built.payload).sort(), ['item_instance_id', 'operation_key']);
  assert.equal(built.payload.operation_key, 'key-use-1');
});

test('长 catalog_id：购买 payload 与导航 key 都不截断、不脱敏', () => {
  assert.ok(CATALOG_ID.length > 80, '这个 id 比旧的 80 字截断上限长');
  const view = P.shopView({
    ok: true,
    data: {currency: 'CNY', catalog: [{catalog_item_id: CATALOG_ID, name: '点心', price: 240, usable_kind: 'food'}], purchase_available: true}
  });
  assert.equal(view.items[0].id, CATALOG_ID);
  assert.equal(view.items[0].idOk, true);
  assert.match(view.items[0].idLabel, /已隐藏的长标识/);
  assert.equal(view.items[0].idLabel.includes(CATALOG_ID), false);

  const built = P.buildPurchasePayload({catalog_item_id: view.items[0].id, operation_key: 'key-buy-1'});
  assert.equal(built.ok, true);
  assert.equal(built.payload.catalog_item_id, CATALOG_ID);
  assert.equal(built.payload.catalog_item_id.length, CATALOG_ID.length);

  const storage = memoryStorage();
  P.createNavStore(storage).save({screen: 'shop-item', detail: CATALOG_ID});
  assert.equal(P.createNavStore(storage).read().detail, CATALOG_ID, '导航 key 要保留服务原字符串');
  assert.equal(JSON.parse(storage.getItem(P.SESSION_KEYS.nav)).detail, CATALOG_ID);
});

test('notes：note_id 与 artifact-version head 原样进 payload，展示才脱敏', () => {
  const view = P.noteListView({ok: true, head: HEAD, items: [{id: NOTE_ID, title: '标题', body: '正文', revision: 4}]});
  assert.equal(view.head, HEAD);
  assert.equal(view.items[0].id, NOTE_ID);
  assert.equal(view.items[0].idOk, true);
  assert.match(view.items[0].idLabel, /已隐藏的长标识/);

  const payload = P.buildNotePayload({
    note_id: view.items[0].id, title: '标题', body: '正文', operation_key: 'key-note-1', expected_head: view.head
  });
  assert.equal(payload.note_id, NOTE_ID);
  assert.equal(payload.expected_head, HEAD);
  assert.equal(payload.operation_key, 'key-note-1');
  assert.equal(P.noteEditBlocked({mode: 'edit', noteId: NOTE_ID}), '');
  assert.equal(P.noteEditBlocked({mode: 'edit', noteId: 'item-\u0000x'}).includes('条目 id'), true);
});

test('非法协议标识被拒，不 trim、不截断、不脱敏后提交', () => {
  assert.equal(P.protocolId(ITEM_ID).value, ITEM_ID);
  assert.equal(P.protocolId('  ' + ITEM_ID + '  ').value, '  ' + ITEM_ID + '  ', '不 trim');
  assert.equal(P.protocolId('item-\u0007bad').reason, 'CONTROL_CHARS');
  assert.equal(P.protocolId('item-\nbad').reason, 'CONTROL_CHARS');
  assert.equal(P.protocolId('x'.repeat(201)).reason, 'TOO_LONG');
  assert.equal(P.protocolId(1234).reason, 'NOT_TEXT');
  assert.equal(P.protocolId('   ').reason, 'EMPTY');

  const ctrl = P.buildUsePayload({item_instance_id: 'item-\u0000bad', operation_key: 'k'});
  assert.equal(ctrl.ok, false);
  assert.match(ctrl.message, /没有发出请求/);
  const tooLong = 'item-' + 'b'.repeat(300);
  assert.equal(P.buildUsePayload({item_instance_id: tooLong, operation_key: 'k'}).ok, false, '超长 id 不能截断后提交');
  assert.equal(P.buildPurchasePayload({catalog_item_id: tooLong, operation_key: 'k'}).ok, false);
  assert.equal(P.buildUsePayload({item_instance_id: ITEM_ID, operation_key: '  '}).ok, false);
  assert.equal(P.buildPurchasePayload({catalog_item_id: ''}).ok, false);
});

test('两个 64hex 物件的 scope 与 operation_key 不会因为脱敏而撞在一起', () => {
  const scopeA = 'use:item-' + 'a3f5'.repeat(16);
  const scopeB = 'use:item-' + 'b7c9'.repeat(16);
  assert.notEqual(P.scopeKey(scopeA), P.scopeKey(scopeB));
  assert.equal(P.scopeKey(scopeA), scopeA);
  const storage = memoryStorage();
  let counter = 0;
  const ops = P.createOpStore(storage, () => `key-${++counter}`);
  const first = ops.ensure(scopeA, {path: '/items/use'});
  const second = ops.ensure(scopeB, {path: '/items/use'});
  assert.notEqual(first.key, second.key, '不同的物件不能共用同一个操作键');
  assert.equal(ops.ensure(scopeA).key, first.key);
  const saved = JSON.parse(storage.getItem(P.SESSION_KEYS.pending));
  assert.equal(Object.prototype.hasOwnProperty.call(saved, scopeA), true, 'scope 必须保留原字节');
});

test('读取信封的 applied / already_applied 就是成功读取，POST 未知语义不变', () => {
  assert.equal(P.isReadSuccessStatus('applied'), true);
  assert.equal(P.isReadSuccessStatus('ALREADY_APPLIED'), true);
  assert.equal(P.isReadSuccessStatus('unavailable'), false);
  assert.equal(P.readStatusWarning('applied'), '');
  assert.equal(P.readStatusWarning('already_applied'), '');
  assert.equal(P.readStatusWarning(''), '');
  assert.match(P.readStatusWarning('unavailable'), /不可用/);

  const wallet = P.deriveReadOutcome({kind: 'http', status: 200, doc: {ok: true, status: 'applied', data: {currency: 'CNY', balance: 9760}}});
  assert.equal(wallet.status, 'ok');
  assert.equal(P.deriveReadOutcome({kind: 'http', status: 200, doc: {status: 'already_applied', data: {}}}).status, 'ok');
  assert.equal(P.deriveReadOutcome({kind: 'http', status: 200, doc: {ok: false, status: 'applied'}}).status, 'rejected');
  assert.equal(P.deriveReadOutcome({kind: 'http', status: 200, doc: {status: 'unavailable', reason_code: 'X'}}).status, 'rejected');

  // POST 侧一个字都没动：2xx 无 status → unknown，服务说 unknown 就是 unknown。
  assert.equal(P.deriveMutationOutcome({kind: 'http', status: 200, doc: {ok: true}}).status, 'unknown');
  assert.equal(P.deriveMutationOutcome({kind: 'http', status: 200, doc: {ok: true, status: 'unknown'}}).status, 'unknown');
  assert.equal(P.deriveMutationOutcome({kind: 'http', status: 200, doc: {ok: true, status: 'applied'}}).status, 'applied');
  assert.equal(P.deriveMutationOutcome({kind: 'http', status: 200, doc: {ok: true, status: 'already_applied'}}).status, 'already_applied');
});

test('接线层：协议标识不经过 sanitizeLine，payload 只从视图原值来', () => {
  assert.doesNotMatch(src, /id:\s*sanitizeLine\(/, '视图里的 id 不能是脱敏值');
  assert.doesNotMatch(src, /item_instance_id:\s*sanitizeLine/);
  assert.doesNotMatch(src, /catalog_item_id:\s*item\.idLabel/);
  assert.match(src, /id: idCheck\.ok \? idCheck\.value : ''/);
  assert.match(src, /buildUsePayload\(\{item_instance_id: item\.id, operation_key: entry\.key\}\)/);
  assert.match(src, /buildPurchasePayload\(\{catalog_item_id: item\.id, operation_key: entry\.key\}\)/);
  assert.match(src, /readStatusWarning\(view\.status\)/);
});
