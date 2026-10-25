/**
 * NyAiro v0.4 · 手机相册前端定向检查（EP-16）。
 *
 * 只查相册这一块：主屏入口、双列缩略图、详情/导入屏、真实同源图片路径、
 * base64 上传与 CSRF、预览失败不保存、无 innerHTML / localStorage / 外部依赖。
 * 不启动服务；真实点击与文件选择由父级浏览器验收。
 *
 * 运行：node --test tests/test_phone_album_ui.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const WEB = new URL('../components/phone/web/', import.meta.url);
const read = (name) => readFileSync(new URL(name, WEB), 'utf8');

const src = read('app.js');
const css = read('style.css');

const sandbox = vm.createContext({});
vm.runInContext(src, sandbox, {filename: 'components/phone/web/app.js'});
const P = sandbox.NyairoPhone;

test('主屏有相册入口，旧七项 APP_ORDER 不变', () => {
  assert.deepEqual([...P.APP_ORDER], ['notes', 'references', 'reading', 'wallet', 'shop', 'inventory', 'body']);
  assert.match(src, /btn\('相册'/);
  assert.match(src, /album: \{label: '相册'/);
  assert.match(src, /album: \{label: '相册'\}/);
});

test('相册路由与资源名接在现有屏表上', () => {
  for (const key of ["album: {render: renderAlbum}", "'album-image': {render: renderAlbumImage}",
    "'album-import': {render: renderAlbumImport}"]) {
    assert.equal(src.includes(key), true, `缺少 ${key}`);
  }
  assert.match(src, /album: '\/album'/);
  assert.match(src, /album: 'album'/);
});

test('图片路径只接受 owner 形状的 id，且是同源 /api/phone', () => {
  assert.equal(P.albumIdFor('artifact:0123abcd'), 'artifact:0123abcd');
  assert.equal(P.albumIdFor('../etc/passwd'), '');
  assert.equal(P.albumIdFor('artifact:ok name'), '');
  assert.equal(P.albumIdFor(''), '');
  assert.equal(P.albumDetailPath('artifact:abc'), '/album/artifact:abc');
  assert.equal(P.albumImagePath('artifact:abc'), '/album/artifact:abc/image');
  assert.equal(P.albumImagePath('../x'), '');
  assert.doesNotMatch(src, /https?:\/\/[^\s'"]*album/i);
});

test('img src 是完整同源 API 地址，且缩略图与详情都实际接在它上面', () => {
  /* 局限：本 harness 没有 DOM，运行时渲染函数（albumPicture）依赖 store，
   * 无法在纯函数沙箱里调用。这里同时断言地址构造与源码接线，证明 <img> 用的
   * 不是 request() 的相对路径；真实浏览器请求与像素渲染仍由父级复验。 */
  assert.equal(P.albumImageSrc('artifact:abc'), '/api/phone/album/artifact:abc/image',
    'img 直接写 src，必须自己带上 API_BASE');
  assert.equal(P.albumImageSrc('../x'), '');
  assert.equal(P.albumImageSrc(''), '');
  assert.equal(P.albumImagePath('artifact:abc'), '/album/artifact:abc/image',
    'metadata request 仍走相对路径，由 request() 统一补前缀，不能双加');
  assert.match(src, /src: albumImageSrc\(artifactId\)/);
  assert.doesNotMatch(src, /src: albumImagePath\(artifactId\)/,
    '旧接线只给了相对路径，浏览器会 404');
  assert.doesNotMatch(src, /src: API_BASE \+ '\/album/);
  assert.match(src, /albumPicture\(row\.artifactId, 'album-thumb'\)/);
  assert.match(src, /albumPicture\(row\.artifactId, 'album-detail-img'\)/);
});

test('列表行只认服务字段，不补造说明或图片来源', () => {
  const row = P.albumRowView({
    artifact_id: 'artifact:abc', caption: '海边的下午', content_type: 'image/png',
    media: {name: 'a'.repeat(64) + '.png', sha256: 'a'.repeat(64), bytes: 1234},
    source: {origin: 'user_import', declared: 'self_declared'},
    lifecycle: 'ACTIVE', created_at_iso: '2026-10-22T08:00:00.000Z'
  });
  assert.equal(row.artifactId, 'artifact:abc');
  assert.equal(row.caption, '海边的下午');
  assert.equal(row.contentType, 'image/png');
  assert.equal(row.bytes, 1234);
  assert.equal(row.createdAt, '2026-10-22T08:00:00.000Z');
  assert.equal(row.source, undefined, '来源由页面固定写“本机导入（自述）”，不取自响应');
  assert.equal(P.albumRowView({artifact_id: 'artifact:abc', content_type: 'image/webp'}), null);
  assert.equal(P.albumRowView({caption: 'no id'}), null);
});

test('列表缺 images 数组时明确缺失，不当作空相册', () => {
  const missing = P.albumListView({status: 'applied'});
  assert.equal(missing.missing, true);
  const empty = P.albumListView({images: []});
  assert.equal(empty.missing, false);
  assert.deepEqual(empty.images, []);
});

test('导入只发白名单字段，字节是纯 base64，说明上限 200', () => {
  assert.equal(P.ALBUM_MAX_CAPTION, 200);
  assert.equal(P.ALBUM_MAX_BYTES, 5 * 1024 * 1024);
  assert.match(src, /content_type: draft\.contentType/);
  assert.match(src, /data: draft\.data/);
  assert.match(src, /operation_key: entry\.key/);
  assert.match(src, /request\('POST', '\/album\/images', \{csrf: store\.conn\.csrf, body: payload\}\)/);
  assert.match(src, /reader\.readAsDataURL\(file\)/);
  assert.match(src, /url\.slice\(comma \+ 1\)/, '保存时去掉 data URL 前缀');
  assert.doesNotMatch(src, /dataUrl:\s*draft\.dataUrl[^}]*operation/i);
});

test('预览失败或没预览成功时保存按钮禁用', () => {
  assert.match(src, /const disabled = busy \|\| !draft\.previewOk/);
  assert.match(src, /预览失败不会保存/);
  assert.match(src, /浏览器解码不了这张图片，不能保存/);
  assert.match(src, /draft\.previewOk = true; renderAll\(\);/);
});

test('详情显示完整图、说明与自述来源，不声称理解图片', () => {
  assert.match(src, /album-detail-img/);
  assert.match(src, /kv\('来源', '你从本机导入（自述）'\)/);
  assert.match(src, /不代表她看过或理解图片内容/);
  assert.match(src, /不会自动写进记忆/);
});

test('双列缩略图，窄屏也不溢出', () => {
  assert.match(css, /\.album-grid\s*\{[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/s);
  assert.match(css, /\.album-thumb[^}]*object-fit: cover/s);
  assert.match(css, /\.album-detail-img[^}]*object-fit: contain/s);
  assert.match(css, /\.album-caption[^}]*text-overflow: ellipsis/s);
});

test('相册没有引入 HTML 拼接、本地存储或动态代码', () => {
  for (const banned of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function']) {
    assert.equal(src.includes(banned), false, `app.js 不能使用 ${banned}`);
  }
  assert.doesNotMatch(src, /\blocalStorage\s*(?:\.|\[)/);
  assert.doesNotMatch(src, /blob:/);
});

/* ---------------- stub DOM：真实渲染出的 <img src> ----------------
 * 只证明接线与写进属性的地址；不加载图片、不做布局，浏览器里的
 * network 请求与像素仍由父级真实浏览器复验。
 */
const ALBUM_ID = 'artifact:abc';

function makeStubDom() {
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
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
    }
    append() { for (const node of arguments) if (node) this.children.push(node); }
    prepend(node) { if (node) this.children.unshift(node); }
    replaceChildren() { this.children = Array.prototype.slice.call(arguments).filter(Boolean); }
    addEventListener(type, fn) {
      if (!this.listeners[type]) this.listeners[type] = [];
      this.listeners[type].push(fn);
    }
    removeEventListener() {}
    focus() {}
    findAll(predicate, out = []) {
      for (const child of this.children) {
        if (!child || typeof child !== 'object') continue;
        if (predicate(child)) out.push(child);
        if (child.findAll) child.findAll(predicate, out);
      }
      return out;
    }
    get allText() {
      const kids = this.children.map((child) => (child && child.allText !== undefined
        ? child.allText : String(child || '')));
      return [String(this.textContent || ''), ...kids].join(' ');
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
    createElement(tag) { return new StubNode(tag); },
    createElementNS(_ns, tag) { return new StubNode(tag); },
    createTextNode(text) {
      const node = new StubNode('#text');
      node.textContent = String(text);
      return node;
    },
    addEventListener() {}
  };

  const row = {
    artifact_id: ALBUM_ID,
    caption: '海边的下午',
    content_type: 'image/png',
    media: {bytes: 1234},
    lifecycle: 'ACTIVE',
    created_at_iso: '2026-10-22T08:00:00.000Z'
  };
  const requests = [];
  const sandbox = {
    document,
    setTimeout,
    clearTimeout,
    setInterval: () => 0,
    clearInterval: () => {},
    AbortController,
    sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    fetch: async (url, init) => {
      const method = (init && init.method) || 'GET';
      const path = String(url).replace('/api/phone', '');
      requests.push({method, path});
      let doc = null;
      if (method === 'GET' && path === '/state') {
        doc = {ok: true, read_only: true, instance: {name: '妹妹', subject: 'chiyo', mode: 'demo'},
          apps: {album: {status: 'available', data: {}}}};
      } else if (method === 'GET' && path === '/album') {
        doc = {ok: true, status: 'applied', images: [row]};
      } else if (method === 'GET' && path === '/album/' + ALBUM_ID) {
        doc = {ok: true, status: 'applied', image: row};
      }
      if (!doc) return {ok: false, status: 404, text: async () => ''};
      return {ok: true, status: 200, text: async () => JSON.stringify(doc)};
    }
  };
  return {sandbox, document, requests};
}

const settle = async () => {
  for (let i = 0; i < 12; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

const findAll = (root, predicate) => (root && root.findAll ? root.findAll(predicate) : []);
const findButton = (root, text) =>
  findAll(root, (node) => node.tagName === 'BUTTON' && node.allText.includes(text))[0];
const hasClass = (node, name) => String(node.className || '').split(/\s+/).indexOf(name) >= 0;

test('（stub DOM）相册列表与详情渲染出的 img src 都是完整同源 API 地址', async () => {
  const harness = makeStubDom();
  const context = vm.createContext(harness.sandbox);
  vm.runInContext(src, context, {filename: 'components/phone/web/app.js'});
  await settle();

  const body = harness.document.getElementById('screen-body');
  const tile = findButton(body, '相册');
  assert.ok(tile, '主屏要有相册入口');
  tile.listeners.click[0]({target: tile});
  await settle();

  assert.ok(harness.requests.some((entry) => entry.method === 'GET' && entry.path === '/album'),
    '打开相册要读 owner 列表');
  const thumbs = findAll(body, (node) => node.tagName === 'IMG' && hasClass(node, 'album-thumb'));
  assert.equal(thumbs.length, 1, '列表要渲染出缩略图 img');
  assert.equal(thumbs[0].getAttribute('src'), '/api/phone/album/' + ALBUM_ID + '/image',
    '缩略图 src 缺 /api/phone 会 404');
  assert.match(thumbs[0].getAttribute('src'), /^\/api\/phone\/album\//);

  const card = findAll(body, (node) => hasClass(node, 'album-card'))[0];
  assert.ok(card, '缩略图外面要有可点卡片');
  card.listeners.click[0]({target: card});
  await settle();

  assert.ok(harness.requests.some((entry) => entry.path === '/album/' + ALBUM_ID),
    '详情要读 owner 的元数据（相对路径，request 补前缀）');
  const detail = findAll(body, (node) => node.tagName === 'IMG' && hasClass(node, 'album-detail-img'));
  assert.equal(detail.length, 1, '详情要渲染出大图 img');
  assert.equal(detail[0].getAttribute('src'), '/api/phone/album/' + ALBUM_ID + '/image');
  assert.equal(body.allText.includes('这张图片现在读不到'), false, '成功渲染时不该出现读不到');
});
