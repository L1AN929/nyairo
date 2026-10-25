/**
 * 手机主屏「此刻」的纯函数与静态约束检查。
 *
 * 不启动服务、不做 DOM 渲染：只证明页面消费的是服务返回的真实字段、缺字段不补造、
 * 以及新增的样式/请求路径没有破坏原有的离线约束。
 *
 * 运行：node --test tests/test_phone_living_ui.mjs
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

const sandbox = vm.createContext({});
vm.runInContext(src, sandbox, {filename: 'components/phone/web/app.js'});
const P = sandbox.NyairoPhone;

const full = {
  schema: 'phone.living.v1',
  read_only: true,
  model_called: false,
  services_started: 0,
  observed_at: '2026-10-23T10:00:00+00:00',
  phase: {key: 'paused', basis: 'ACTIVITY_PAUSED_NOT_RUNNING'},
  sentence: '她把这件活动停在这里：去她观察到的地方（PAUSED，2026-10-22T23:49:34+08:00）；当前没有在运行。',
  foreground: {status: 'READ', ref: 'actv_91acbd742551228b', title: '去她观察到的地方',
    activity_status: 'PAUSED', activity_kind: 'GO_TO_OBSERVED_PLACE',
    paused_at: '2026-10-22T23:49:34+08:00', started_at: '2026-10-22T22:11:02+08:00'},
  where: {status: 'READY', place_name: '书房', place_id: 'place:study', unknowns: []},
  work: {status: 'READY', title: '一篇旧作', artifact_id: 'artifact:old',
    head_version_id: 'av:1', resumable: true, stopped_at: '2026-10-22T21:39:46+08:00'},
  reading: {status: 'READY', title: '桃花源记', page: 1, pages: 2},
  next: {check_window_at: '2026-10-23T11:00:00+00:00'},
  memory: {status: 'READY', binding_status: 'READY', recall_ready: true,
    recall_state: 'empty', experience_count: 0, source: 'life_preflight'},
  run: {
    resident: {running_recorded: true, pid: 4321, pid_alive: true, lease: {status: 'HELD'}},
    pause: {state: 'absent', paused: false},
    window: {running: true, pid: 100, port: 3831,
      child: {pid: 200, alive: false, exit_code: 0}},
    capacity: {used: 1, remaining: 3, function_cap: 4}
  }
};

test('living 路径常量只指向同源 /api/phone 前缀下的本机入口', () => {
  assert.equal(P.LIVING_PATH, '/living');
  assert.equal(P.LIVING_WINDOW_PATH, '/living/window');
  assert.equal(P.LIVING_PAUSE_PATH, '/living/pause');
  assert.equal(P.LIVING_STOP_PATH, '/living/stop');
  assert.equal(P.LIVING_DEFAULT_SECONDS, 120);
  assert.equal(P.LIVING_DEFAULT_REQUESTS, 1);
});

test('livingView 读服务字段，不补造缺的位置、作品或阅读', () => {
  const view = P.livingView(full);
  assert.equal(view.key, 'paused');
  assert.equal(view.place, '书房');
  assert.equal(view.placeKnown, true);
  assert.equal(view.workTitle, '一篇旧作');
  assert.equal(view.readingTitle, '桃花源记');
  assert.equal(view.readingPage, 1);
  assert.equal(view.nextAt, '2026-10-23T11:00:00+00:00');
  assert.equal(view.activityKnown, true);
  assert.equal(view.activityTitle, '去她观察到的地方');
  assert.equal(view.activityStatus, 'PAUSED');
  assert.equal(view.activityAt, '2026-10-22T23:49:34+08:00');
  assert.equal(view.activityRefFg, 'actv_91acbd742551228b');
  assert.equal(view.workStoppedAt, '2026-10-22T21:39:46+08:00');
  assert.equal(view.memoryStatus, 'READY');
  assert.equal(view.memoryRecall, 'empty');
  assert.equal(view.memoryCount, 0);
  assert.equal(view.memorySource, 'life_preflight');
  assert.equal(view.residentRunning, true);
  assert.equal(view.paused, false);
  assert.equal(view.used, 1);
  assert.equal(view.remaining, 3);
  assert.equal(view.cap, 4);
  assert.equal(view.supervisorAttached, true);
  assert.equal(view.childAlive, false);
});

test('缺字段时 livingView 返回 null/unknown，不让界面编出「正在读某本书」', () => {
  const sparse = P.livingView({
    phase: {key: 'stopped'},
    sentence: '现在没有在运行，也没有读到上次的停点。',
    where: {status: 'UNKNOWN', unknowns: ['NO_LIVE_OWNER_READ']},
    work: {},
    reading: {},
    run: {resident: {}, pause: {}, window: {}, capacity: {}}
  });
  assert.equal(sparse.key, 'stopped');
  assert.equal(sparse.place, '');
  assert.equal(sparse.placeKnown, false);
  assert.deepEqual(sparse.whereUnknown, ['NO_LIVE_OWNER_READ']);
  assert.equal(sparse.workTitle, '');
  assert.equal(sparse.readingTitle, '');
  assert.equal(sparse.memoryStatus, '');
  assert.equal(sparse.remaining, null);
  assert.equal(sparse.supervisorAttached, false);
});

test('活动权威读不到时明确「未核实」，不拿原始 id 冒充当前活动', () => {
  const view = P.livingView({
    phase: {key: 'stopped'},
    foreground: {status: 'UNVERIFIED', reason_code: 'CANONICAL_STATE_CORRUPT'},
    run: {}
  });
  assert.equal(view.activityKnown, false);
  assert.equal(view.activityTitle, '');
  assert.equal(view.activityReason, 'CANONICAL_STATE_CORRUPT');
});

test('Memory 文案把接通、空、未知与不可召回分开', () => {
  assert.match(P.livingMemoryText({memoryStatus: 'READY', memoryRecall: 'empty', memoryCount: 0}),
    /记忆已接通 · 暂无/);
  assert.match(P.livingMemoryText({memoryStatus: 'READY', memoryRecall: 'ready', memoryCount: 3}),
    /可召回 3 条/);
  assert.match(P.livingMemoryText({memoryStatus: 'READY', memoryRecall: 'ready', memoryCount: null}),
    /条数未读到/);
  assert.match(P.livingMemoryText({memoryStatus: 'READY', memoryRecall: 'not_ready'}),
    /当前不可召回/);
  assert.match(P.livingMemoryText({memoryStatus: 'UNBOUND'}), /未绑定/);
  assert.match(P.livingMemoryText({memoryStatus: 'BLOCKED'}), /不可用/);
  assert.equal(P.livingActivityStatusText('PAUSED'), '已暂缓');
  assert.equal(P.livingActivityStatusText('ACTIVE'), '进行中');
});

test('主行不显示原始活动 id，id 只进技术细节', () => {
  assert.doesNotMatch(src, /上次活动/);
  assert.match(src, /livingLine\('当前活动'/);
  assert.match(src, /livingLine\('活动引用', view\.activityRefFg, true\)/);
});

test('异步「此刻」刷新不在指针按下时重建按钮，也不重建别的屏', () => {
  assert.match(src, /if \(store\.pointerDown\) \{/);
  assert.match(src, /function releaseDeferredRender\(/);
  assert.match(src, /addEventListener\('pointerdown'/);
  assert.match(src, /function renderHomeIfCurrent\(/);
  assert.match(src, /renderHomeIfCurrent\(\);/);
});

test('未知 phase 名称一律降级为 unknown', () => {
  assert.equal(P.livingView({phase: {key: 'made-up'}}).key, 'unknown');
  assert.equal(P.livingView({}).key, 'unknown');
  assert.equal(P.livingView(null), null);
});

test('历史记录不会被当成正在做：pid 不在时 residentRunning 为 false', () => {
  const view = P.livingView({
    phase: {key: 'unknown', basis: 'STALE_RECORD'},
    run: {resident: {running_recorded: true, pid_alive: false}, pause: {}, window: {}, capacity: {}}
  });
  assert.equal(view.residentRunning, false);
  assert.equal(view.residentRecorded, true);
});

test('主屏渲染接到 living 数据，不使用统计仪表盘式大数字', () => {
  assert.match(src, /nodes\.push\(renderLiving\(\)\);/);
  assert.match(src, /function renderLiving\(/);
  assert.match(src, /aria-label': '此刻'/);
  /* 没有虚构的第一人称日记/示例文案。 */
  assert.doesNotMatch(src, /今天(我|她)?(读|写)了/);
  assert.doesNotMatch(src, /示例日常|演示日常/);
});

test('样式只用本项目字体与颜色 token，无外网资源、无 emoji', () => {
  assert.doesNotMatch(html, /(?:src|href)="https?:\/\//i);
  assert.doesNotMatch(css, /@import\s+url\(/i);
  assert.doesNotMatch(css, /url\(\s*['"]?https?:/i);
  assert.doesNotMatch(css, /font-family:[^;]*['"](?:Inter|Roboto|Noto)/);
  for (const text of [src, css]) {
    assert.doesNotMatch(text, /[\u{1F300}-\u{1FAFF}]/u, '界面不使用 emoji');
  }
});

test('375px 与 reduced-motion：新增卡片有窄屏与无动画约束', () => {
  assert.match(css, /\.living-line-v[^{]*\{[^}]*overflow-wrap: anywhere;/s);
  assert.match(css, /@media \(max-width: 430px\)[\s\S]*?\.living-actions \.btn \{ flex: 1 1 auto; \}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});

test('控制按钮走既有的 CSRF 写入闸门，不绕过只读状态', () => {
  const blocked = P.canWrite({phase: 'readonly', csrf: null, readOnly: true});
  assert.equal(blocked.ok, false);
  assert.match(src, /request\('POST', path, \{csrf: store\.conn\.csrf/);
  assert.match(src, /function runLivingControl\(/);
  /* 只读刷新只发 GET /living，不触发模型。 */
  assert.match(src, /request\('GET', LIVING_PATH, \{\}\)/);
});
