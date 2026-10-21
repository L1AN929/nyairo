/**
 * NyAiro v0.4 阶段 B · 手机「分享与本地收件箱」界面数据层定向检查。
 *
 * 只检查纯函数面（没有 DOM 时 app.js 走的就是这条路）：列表/详情/来源视图、草稿校验、
 * 两个写入请求体、路径、操作键 scope、原 key 对账的人话，以及主屏入口与样式。
 * 不启动服务、不造数据、不读存储；真实 HTTP 与 owner 交互由
 * tests/test_v04_phone_sharing_service.py 与 tests/test_v04_phone_sharing_http.py 覆盖。
 *
 * 运行：node --test tests/phone_sharing_ui.mjs
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

const SHARE_ID = 'artifact:' + 'a'.repeat(32);
const SOURCE_ID = 'artifact:' + 'b'.repeat(32);
const HEAD = 'artifact-version:' + 'c'.repeat(32);
const SHARE_TEXT = '留白重排完了，第二段还是挤。';

const listDoc = {
  ok: true,
  status: 'ok',
  data: {
    shares: [
      {share_id: SHARE_ID, state: 'DRAFT', state_derived: false, title: '留白重排',
       share_revision: 1, head_version_id: HEAD, updated_at: '2026-11-14T09:00:00+00:00',
       lifecycle: 'ACTIVE', audience: 'local:owner', channel: 'local_inbox',
       has_receipt: false, delivered: false, text_chars: 14, text_withheld: false,
       text_available: true, source_state: 'READABLE', source_reason_code: '',
       source: {artifact_id: SOURCE_ID, version_id: HEAD}, sendable: true,
       actions: ['send_local', 'defer', 'drop']},
      {share_id: 'artifact:' + 'd'.repeat(32), state: 'LOCAL_DELIVERED', title: '昨天那句',
       head_version_id: HEAD, updated_at: '2026-11-13T09:00:00+00:00', has_receipt: true,
       delivered: true, text_chars: 8, text_withheld: true, text_available: false,
       source_state: 'GOVERNED', source_reason_code: 'SOURCE_GOVERNED',
       source: {artifact_id: SOURCE_ID}, sendable: false, actions: []},
    ],
    counts: {total: 2, draft: 1, deferred: 0, dropped: 0, expired: 0, local_delivered: 1},
    truncated: false,
    ignored: [],
    inbox: {channel: 'local_inbox', audience: 'local:owner', delivered_count: 1, local_only: true},
    withheld_count: 1,
    private_text_included: false,
    local_only: true,
    external_send: false,
    note: '本实例本地存档，未向外部发送、未证明用户已读',
  },
};

test('列表视图：只有元数据，永远没有正文', () => {
  const view = P.shareListView(listDoc);
  assert.equal(view.ok, true);
  assert.equal(view.items.length, 2);
  assert.equal(view.items[0].stateLabel, '草稿');
  assert.equal(view.items[1].stateLabel, '本地收件箱');
  assert.equal(view.items[1].textWithheld, true);
  assert.equal(view.items[1].sourceState, 'GOVERNED');
  assert.equal(view.counts.draft, 1);
  assert.equal(view.counts.localDelivered, 1);
  assert.equal(view.inbox.localOnly, true);
  assert.equal(view.inbox.deliveredCount, 1);
  assert.equal(view.withheldCount, 1);
  assert.equal(view.privateTextIncluded, false);
  assert.match(view.note, /本地存档/);
  for (const row of view.items) {
    assert.equal(Object.prototype.hasOwnProperty.call(row, 'text'), false, '列表不带正文');
  }
  assert.equal(P.shareListView(null), null);
});

test('详情视图：正文、来源当前性、收据与终态都照实搬', () => {
  const doc = {
    ok: true,
    status: 'ok',
    data: {
      share: {share_id: SHARE_ID, state: 'LOCAL_DELIVERED', state_derived: false, title: '留白重排',
        audience: 'local:owner', channel: 'local_inbox', head_version_id: HEAD, share_revision: 2,
        artifact_revision: 3, created_at: '2026-11-14T08:00:00+00:00',
        updated_at: '2026-11-14T09:00:00+00:00', expires_at: '', text: SHARE_TEXT,
        text_chars: 14, text_withheld: false, text_available: true,
        source: {artifact_id: SOURCE_ID, version_id: HEAD, revision: 2},
        sendable: false, terminal: false, actions: []},
      source_current: {state: 'READABLE', readable: true, governed: false, matches_recorded: true,
        stale: false, reason_code: '', lifecycle: 'ACTIVE'},
      source_readable: true,
      receipt: {delivery_id: 'localdelivery:abc123', delivered_at: '2026-11-14T09:00:00+00:00',
        channel: 'local_inbox', audience: 'local:owner', recipient: 'local:owner',
        local_only: true, external_channel_sent: false, external_delivery_confirmed: false,
        read_by_audience: false, acknowledged: false},
      delivered: true, text_withheld: false, withheld_reason_code: '',
      private_text_included: true, local_only: true,
      note: '本实例本地存档，未向外部发送、未证明用户已读',
    },
  };
  const view = P.shareDetailView(doc);
  assert.equal(view.stateLabel, '本地收件箱');
  assert.equal(view.text, SHARE_TEXT);
  assert.equal(view.receipt.localOnly, true);
  assert.equal(view.receipt.externalSent, false);
  assert.equal(view.receipt.readByAudience, false);
  assert.equal(view.delivered, true);
  assert.equal(view.actions.length, 0, '已投递没有下一步动作');
  assert.equal(view.sourceCurrent.readable, true);
  assert.match(view.note, /本地存档/);

  const withheld = P.shareDetailView({ok: true, status: 'ok', data: {
    share: {share_id: SHARE_ID, state: 'DRAFT', text: '', text_withheld: true,
      text_chars: 14, head_version_id: HEAD, sendable: true, actions: ['send_local']},
    source_current: {state: 'GOVERNED', readable: false, governed: true, reason_code: 'SOURCE_GOVERNED'},
    text_withheld: true, withheld_reason_code: 'SOURCE_GOVERNED', private_text_included: false,
  }});
  assert.equal(withheld.text, '');
  assert.equal(withheld.textWithheld, true);
  assert.equal(withheld.withheldReason, 'SOURCE_GOVERNED');
  assert.equal(P.shareDetailView({ok: true, status: 'ok', data: {}}), null);
});

test('来源视图：只列服务给的那些，读不到的照实标', () => {
  const view = P.shareSourcesView({ok: true, status: 'ok', data: {sources: [
    {artifact_id: SOURCE_ID, title: '她的留白练习', kind: 'project', readable: true,
     lifecycle: 'ACTIVE', reason_code: '', revision: 2},
    {artifact_id: 'artifact-reading', title: '她的阅读材料', kind: 'reading', readable: false,
     lifecycle: 'ACTIVE', reason_code: 'SOURCE_UNREADABLE'},
  ], truncated: false, private_text_included: false}});
  assert.equal(view.items.length, 2);
  assert.equal(view.items[0].kindLabel, '作品');
  assert.equal(view.items[1].kindLabel, '阅读材料');
  assert.equal(view.items[1].readable, false);
  assert.equal(view.items[1].reason, 'SOURCE_UNREADABLE');
});

test('草稿校验：必须有明确的短文或明确的前缀长度', () => {
  const empty = P.validateShareDraft({source_artifact_id: SOURCE_ID});
  assert.equal(empty.ok, false);
  assert.ok(empty.errors.some((line) => line.includes('明确的分享文本')));

  const both = P.validateShareDraft({source_artifact_id: SOURCE_ID, share_text: SHARE_TEXT,
    excerpt_chars: 5});
  assert.equal(both.ok, false);

  const tooLong = P.validateShareDraft({source_artifact_id: SOURCE_ID,
    share_text: 'x'.repeat(P.SHARE_TEXT_MAX + 1)});
  assert.equal(tooLong.ok, false);

  const badExcerpt = P.validateShareDraft({source_artifact_id: SOURCE_ID, excerpt_chars: 0});
  assert.equal(badExcerpt.ok, false);

  const noSource = P.validateShareDraft({share_text: SHARE_TEXT});
  assert.equal(noSource.ok, false);

  const ok = P.validateShareDraft({source_artifact_id: SOURCE_ID, share_text: ' 一句话 ',
    title: '标题'});
  assert.equal(ok.ok, true);
  assert.equal(ok.value.share_text, ' 一句话 ');
});

test('prepare 请求体：只有显式字段，永不出现受众/渠道', () => {
  const payload = P.buildSharePreparePayload({source_artifact_id: SOURCE_ID,
    share_text: SHARE_TEXT, title: '留白重排', operation_key: 'k1'});
  assert.equal(JSON.stringify(payload), JSON.stringify({
    source_artifact_id: SOURCE_ID, operation_key: 'k1', share_text: SHARE_TEXT,
    title: '留白重排'}));
  assert.equal(Object.prototype.hasOwnProperty.call(payload, 'audience'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(payload, 'channel'), false);

  const excerpt = P.buildSharePreparePayload({source_artifact_id: SOURCE_ID, excerpt_chars: 12,
    expires_after_s: 3600, operation_key: 'k2'});
  assert.equal(excerpt.excerpt_chars, 12);
  assert.equal(Object.prototype.hasOwnProperty.call(excerpt, 'share_text'), false);
  assert.equal(excerpt.expires_after_s, 3600);

  assert.equal(P.buildSharePreparePayload({source_artifact_id: SOURCE_ID,
    share_text: SHARE_TEXT}), null, '没有操作键就不发');
  assert.equal(P.buildSharePreparePayload({source_artifact_id: SOURCE_ID,
    operation_key: 'k3'}), null, '没有明确短文就不发');
});

test('动作请求体：发送 / 延后 / 放弃各自的字段，缺 head 就不发', () => {
  const send = P.buildShareActionPayload({action: 'send_local', expected_head: HEAD,
    operation_key: 'k4'});
  assert.equal(JSON.stringify(send), JSON.stringify({action: 'send_local',
    expected_head: HEAD, operation_key: 'k4'}));

  const noHead = P.buildShareActionPayload({action: 'send_local', expected_head: '',
    operation_key: 'k5'});
  assert.equal(noHead, null, '没有 head 就是关掉 CAS，宁可不写');

  const deferEmpty = P.buildShareActionPayload({action: 'defer', expected_head: HEAD,
    operation_key: 'k6'});
  assert.equal(deferEmpty, null, '延后必须写清等到什么机会');

  const defer = P.buildShareActionPayload({action: 'defer', expected_head: HEAD,
    operation_key: 'k7', opportunity_ref: '夜里安静下来'});
  assert.equal(defer.opportunity_ref, '夜里安静下来');
  assert.equal(Object.prototype.hasOwnProperty.call(defer, 'reason'), false);

  const drop = P.buildShareActionPayload({action: 'drop', expected_head: HEAD,
    operation_key: 'k8', reason: '先不给她看'});
  assert.equal(drop.reason, '先不给她看');
  assert.equal(P.buildShareActionPayload({action: 'expire', expected_head: HEAD,
    operation_key: 'k9'}), null, '手机不做 expire');
});

test('scope 与路径：一个动作一个键，owner 的 id 原样进路径', () => {
  const sendScope = P.shareActionScope(SHARE_ID, 'send_local');
  const dropScope = P.shareActionScope(SHARE_ID, 'drop');
  assert.equal(sendScope, 'share:send_local:' + SHARE_ID);
  assert.notEqual(sendScope, dropScope, '不同动作不能撞同一个键');
  assert.equal(P.shareActionScope('bad/id', 'send_local'), '');
  assert.equal(P.shareActionScope(SHARE_ID, 'expire'), '');

  assert.equal(P.sharePath(SHARE_ID), '/shares/' + SHARE_ID);
  assert.equal(P.shareActionsPath(SHARE_ID), '/shares/' + SHARE_ID + '/actions');
  assert.equal(P.shareOperationPath('share:send_local:abc'), '/shares/operations/share:send_local:abc');
  assert.equal(P.shareSourcesPath(), '/shares/sources');
  assert.equal(P.sharePath('bad/id'), '');
  assert.equal(P.shareOperationPath(''), '');
});

test('对账的人话：原 key 不换，也不冒充已生效', () => {
  const lines = P.shareOperationLines({ok: true, status: 'ok', data: {
    operation_key: 'share:send_local:abc',
    records: [{action: 'share:send_local', status: 'unknown', reason_code: 'RESOLUTION_INCONCLUSIVE',
      at: '2026-11-14T09:00:00+00:00', actor: 'user', ok: false}],
    recorded: true,
    shares: [{share_id: SHARE_ID, state: 'DRAFT', delivered: false, sendable: true}],
    shares_ok: true, shares_reason_code: '',
  }});
  const text = lines.join('\n');
  assert.match(text, /share:send_local:abc/);
  assert.match(text, /unknown/);
  assert.match(text, /草稿/);
  assert.match(text, /没有用新键重发/);
  const empty = P.shareOperationLines({ok: true, status: 'ok', data: {
    operation_key: 'k', records: [], shares: [], shares_ok: true}});
  assert.match(empty.join('\n'), /没有这条键的记录/);
});

test('主屏入口与样式：分享入口在、正文块可读、旧七项顺序没动', () => {
  assert.match(src, /分享与收件箱/);
  assert.match(src, /function renderShares/);
  assert.match(src, /function renderShare\b/);
  assert.match(src, /function renderShareNew/);
  assert.match(src, /\/shares\/operations\//);
  assert.match(src, /SHARE_LOCAL_NOTE/);
  /* 铁律仍在：不调用 localStorage、不拼 HTML。 */
  assert.equal(/localStorage\s*\./.test(src), false);
  assert.equal(/innerHTML|outerHTML|insertAdjacentHTML|document\.write/.test(src), false);
  assert.match(css, /\.share-text\s*\{[^}]*white-space:\s*pre-wrap/);
  assert.match(css, /\.share-note\s*\{/);
  assert.deepEqual([...P.APP_ORDER], ['notes', 'references', 'reading', 'wallet', 'shop',
    'inventory', 'body']);
  assert.deepEqual([...P.HOME_ORDER], ['notes', 'references', 'reading', 'wallet', 'shop',
    'inventory', 'body', 'world']);
});
