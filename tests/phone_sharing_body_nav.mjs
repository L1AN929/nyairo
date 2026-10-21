/**
 * NyAiro v0.4 阶段 B · 「从主屏直达身体」导航回归（父级 2026-11-15 实际遇到的缺陷）。
 *
 * 缺陷因果：`ensureLoaded('body')` 只用 `!store.data.world` 判断要不要读世界快照。
 * 只要世界快照读过一次并落在 `error`/`offline`（世界子进程刚起、请求超时、5xx……），
 * 缓存里就留下了一个非空条目 —— `!entry` 从此为假，身体屏**再也不会重试**，
 * 于是屏上永远只有 /state 投影、没有坐下/开始-结束休息按钮，只能「先去世界屏点重试」绕过。
 *
 * 这条回归钉住三件事：
 *   1. `worldSnapshotNeedsLoad()` 的判断（没有 / error / offline 要读；loading / ready 不重发）；
 *   2. `ensureLoaded('body')` 与通用 world 分支都走这条判断（不再用 `!store.data.world`）；
 *   3. 身体屏在世界快照没到时给的是**可操作的**重试，而不是一句「只能显示 /state 投影」；
 *      再点一次同一个磁贴也会重新尝试（`go()` 的同屏分支会 `ensureLoaded`）。
 *
 * 真实 DOM 路径由父级的无头 Edge 核对；本地也用一次性 harness（scratch，不入库）跑过
 * 「首次 /world 失败 → 点『重新读取世界快照』→ 出现休息/姿势按钮」。
 *
 * 运行：node --test tests/phone_sharing_body_nav.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const WEB = new URL('../components/phone/web/', import.meta.url);
const src = readFileSync(new URL('app.js', WEB), 'utf8');
const sandbox = vm.createContext({});
vm.runInContext(src, sandbox, {filename: 'components/phone/web/app.js'});
const P = sandbox.NyairoPhone;

test('worldSnapshotNeedsLoad：没有 / error / offline 要读，loading / ready 不重发', () => {
  assert.equal(typeof P.worldSnapshotNeedsLoad, 'function');
  assert.equal(P.worldSnapshotNeedsLoad(null), true, '没有条目就读');
  assert.equal(P.worldSnapshotNeedsLoad(undefined), true);
  assert.equal(P.worldSnapshotNeedsLoad({phase: 'error', message: 'HTTP 503'}), true,
    '上一次失败必须重试，否则身体屏永远停在 /state 投影');
  assert.equal(P.worldSnapshotNeedsLoad({phase: 'offline'}), true);
  assert.equal(P.worldSnapshotNeedsLoad({phase: 'loading'}), false, '同一屏不发两条打架的请求');
  assert.equal(P.worldSnapshotNeedsLoad({phase: 'ready', doc: {}}), false);
  assert.equal(P.worldSnapshotNeedsLoad({}), true, '没有 phase 的条目当作要读');
});

function bodyBranch() {
  const start = src.indexOf("if (screen === 'body') {");
  assert.ok(start > 0, 'ensureLoaded 里必须有 body 分支');
  const end = src.indexOf('if (screen ===', start + 10);
  return src.slice(start, end > 0 ? end : start + 800);
}

test("ensureLoaded('body')：用 worldSnapshotNeedsLoad 重试，不再用 !store.data.world", () => {
  const branch = bodyBranch();
  assert.match(branch, /worldSnapshotNeedsLoad\(store\.data\.world\)/);
  assert.match(branch, /loadResource\('world'\)/);
  assert.equal(/!store\.data\.world/.test(branch), false,
    '`!store.data.world` 会把「上次失败」当成「已经读过」，身体屏就再也回不来');
});

test('通用 world 分支也用同一条判断', () => {
  const start = src.indexOf("if (resource === 'world') {");
  assert.ok(start > 0);
  const region = src.slice(start, start + 400);
  assert.match(region, /worldSnapshotNeedsLoad\(store\.data\.world\)/);
  assert.equal(/!store\.data\.world/.test(region), false);
});

test('go()：再点同一个磁贴也重新走一遍读取', () => {
  const start = src.indexOf('function go(screen, options) {');
  assert.ok(start > 0);
  const region = src.slice(start, start + 900);
  const same = region.indexOf('if (screen === store.screen) {');
  assert.ok(same >= 0);
  const head = region.slice(same, same + 320);
  assert.match(head, /ensureLoaded\(screen\);/, '同屏点击不能什么都不做（身体/世界要能自己重试）');
});

test('身体屏：快照没到时给可操作的重试，不说「只能显示 /state 投影」', () => {
  assert.match(src, /重新读取世界快照/);
  assert.match(src, /正在读取世界快照…/);
  assert.match(src, /世界快照这次没读到/);
  assert.equal(/这里只能显示 \/state 的投影/.test(src), false,
    '这句话就是父级看到的卡死状态，必须换成带重试按钮的说明');
  assert.match(src, /function renderBody/);
  assert.match(src, /worldPosePanel\(projection\.world\)/, '坐下/站起要复用世界既有的 POSE 入口');
  assert.match(src, /bodyRecoveryPanel\(body, projection\.world\)/);
});

test('身体屏仍然只读 owner 事实：会话不等于恢复的说法还在', () => {
  assert.match(src, /不等于恢复了/);
  assert.match(src, /未知不等于休息好了/);
  assert.match(src, /命令被接受只说明 owner 收下了这次命令/);
  /* 旧七项与世界入口的导航契约没被这次修复动过。 */
  assert.deepEqual([...P.APP_ORDER], ['notes', 'references', 'reading', 'wallet', 'shop',
    'inventory', 'body']);
  assert.deepEqual([...P.HOME_ORDER], ['notes', 'references', 'reading', 'wallet', 'shop',
    'inventory', 'body', 'world']);
});
