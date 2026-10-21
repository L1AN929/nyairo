/**
 * NyAiro v0.4 阶段 B · 手机「私人项目」界面数据层定向检查。
 *
 * 只检查本轮新增的纯函数面（没有 DOM 时 app.js 走的就是这条路）：
 * 列表/详情/历史视图、草稿校验、三个写入请求体、路径拼装，以及项目入口在 app 注册表里。
 * 不启动服务、不造数据、不读存储；真实 HTTP 与 owner 交互由 tests/test_phone_projects_api.py 覆盖。
 *
 * 运行：node --test tests/test_phone_projects_ui.mjs
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

const listDoc = {
  ok: true,
  status: 'applied',
  projects: [
    {artifact_id: 'artifact:' + 'a'.repeat(32), title: '夜航手记', lifecycle: 'ACTIVE',
     revision: 2, head_version_id: 'version-1', updated_at: 1760000100},
    {artifact_id: 'artifact:' + 'b'.repeat(32), title: '旧的记录', lifecycle: 'ARCHIVED',
     revision: 5, head_version_id: 'version-2', updated_at: 1760000000},
  ],
  counts: {total: 2, active: 1, archived: 1, excluded: 2},
  excluded_artifact_ids: ['artifact-reading', 'artifact-items'],
  private_text_included: false,
};

test('项目列表视图：状态、计数与「不含正文」都在', () => {
  const view = P.projectListView(listDoc);
  assert.equal(view.itemsMissing, false);
  assert.equal(view.projects.length, 2);
  assert.equal(view.projects[0].title, '夜航手记');
  assert.equal(view.projects[0].status, 'active');
  assert.equal(view.projects[1].status, 'archived');
  assert.equal(view.activeCount, 1);
  assert.equal(view.archivedCount, 1);
  assert.equal(view.excludedCount, 2);
  assert.equal(view.truncated, false);
  for (const row of view.projects) {
    assert.equal(Object.prototype.hasOwnProperty.call(row, 'content'), false, '列表不带正文');
  }
});

test('项目详情视图：正文、head 与「历史不全」照实说', () => {
  const doc = {
    ok: true,
    status: 'applied',
    project: {artifact_id: 'artifact:' + 'a'.repeat(32), title: '夜航手记', lifecycle: 'ACTIVE',
      revision: 2, head_version_id: 'version-1', version_number: 2, content: '第一段。\n第二段。',
      content_available: true, content_chars: 9},
    history: {complete: false, reason_code: 'PORT_LACKS_LIST_VERSIONS', count: 1,
      entries: [{version_id: 'version-1', version_number: 2, is_head: true, content_chars: 9}]},
  };
  const view = P.projectDetailView(doc);
  assert.equal(view.project.title, '夜航手记');
  assert.equal(view.content, '第一段。\n第二段。');
  assert.equal(view.history.complete, false);
  assert.equal(view.history.reason, 'PORT_LACKS_LIST_VERSIONS');
  assert.equal(view.history.entries.length, 1);
  assert.equal(view.history.entries[0].isHead, true);
  // 缺 project 对象就是 null，界面据此显示「服务没有返回这一份」，不编一份出来。
  assert.equal(P.projectDetailView({ok: true}), null);
});

test('草稿校验：空标题/空正文/超长都挡住', () => {
  // 跨 vm realm 的对象原型不同，这里按序列化比较（值本身才是要测的东西）。
  assert.equal(JSON.stringify(P.validateProjectDraft({title: '', content: '正文'}).errors),
    JSON.stringify(['标题不能为空']));
  assert.equal(JSON.stringify(P.validateProjectDraft({title: '标题', content: '   '}).errors),
    JSON.stringify(['正文不能为空']));
  const long = 'x'.repeat(P.PROJECT_TITLE_MAX + 1);
  assert.equal(P.validateProjectDraft({title: long, content: '正文'}).ok, false);
  const ok = P.validateProjectDraft({title: ' 夜航 ', content: '一\r\n二'});
  assert.equal(ok.ok, true);
  assert.equal(ok.value.title, '夜航');
  assert.equal(ok.value.content, '一\n二');
});

test('写入请求体：三个动作各自的字段与「缺 head 就不发」', () => {
  const asJson = (value) => JSON.stringify(value);
  assert.equal(
    asJson(P.buildProjectCreatePayload({title: '夜航', content: '正文', operation_key: 'k1'})),
    asJson({title: '夜航', content: '正文', operation_key: 'k1'}));
  assert.equal(P.buildProjectCreatePayload({title: '', content: '正文', operation_key: 'k1'}), null);
  assert.equal(P.buildProjectCreatePayload({title: '夜航', content: '正文'}), null);

  assert.equal(
    asJson(P.buildProjectCommitPayload({content: '新的一版', expected_head: 'version-1',
      operation_key: 'k2'})),
    asJson({content: '新的一版', expected_head: 'version-1', operation_key: 'k2'}));
  assert.equal(
    P.buildProjectCommitPayload({content: '新的一版', expected_head: '', operation_key: 'k2'}), null,
    '没有 head 就是关掉 CAS，宁可不写');

  assert.equal(
    asJson(P.buildProjectClosePayload({expected_revision: 3, operation_key: 'k3'})),
    asJson({expected_revision: 3, operation_key: 'k3'}));
  assert.equal(P.buildProjectClosePayload({expected_revision: 0, operation_key: 'k3'}), null);
  assert.equal(P.buildProjectClosePayload({expected_revision: '3', operation_key: 'k3'}), null);
});

test('路径：owner 的 artifact id 原样进路径，怪 id 直接拼不出来', () => {
  const id = 'artifact:' + 'a'.repeat(32);
  assert.equal(P.projectPath(id), '/projects/' + id);
  assert.equal(P.projectPath('bad id/x'), '');
  assert.equal(P.projectPath(''), '');
});

test('版本页视图：has_more / 游标 / 总数只照服务返回', () => {
  const doc = {
    ok: true,
    status: 'applied',
    history: {
      complete: false, reason_code: 'HISTORY_INCOMPLETE', count: 2, total_versions: 5,
      has_more: true, next_before_version_number: 3,
      entries: [
        {version_id: 'version-5', version_number: 5, is_head: true, content_chars: 100},
        {version_id: 'version-4', version_number: 4, is_head: false, content_chars: 90},
      ],
    },
  };
  const page = P.projectVersionPageView(doc);
  assert.equal(page.entries.length, 2);
  assert.equal(page.entries[0].versionNumber, 5);
  assert.equal(page.entries[0].isHead, true);
  assert.equal(page.complete, false);
  assert.equal(page.reason, 'HISTORY_INCOMPLETE');
  assert.equal(page.totalVersions, 5);
  assert.equal(page.hasMore, true);
  assert.equal(page.nextBefore, 3);
  // 没有 history 就是 null：界面据此说“服务没有返回历史”，不编空列表。
  assert.equal(P.projectVersionPageView({ok: true}), null);
});

test('历史合并：同一个 version_id 只留一次，按版本号倒序', () => {
  const merged = P.projectHistoryMerge(
    [{versionId: 'version-5', versionNumber: 5}],
    [{versionId: 'version-5', versionNumber: 5}, {versionId: 'version-4', versionNumber: 4},
      {versionId: 'version-3', versionNumber: 3}]);
  // 跨 vm realm 的数组原型不同，按值比较（join 之后只比内容）。
  assert.equal(merged.map((row) => row.versionId).join(','), 'version-5,version-4,version-3');
});

test('单版本视图：正文、字数、时间、截断与收回都照实说', () => {
  const view = P.projectVersionView({
    ok: true,
    version: {version_id: 'version-2', version_number: 2, parent_version_id: 'version-1',
      created_at: 1760000000, content_chars: 120, returned_chars: 80,
      content_truncated: true, content: '这一版的正文', lifecycle: 'ACTIVE', continuable: false},
    content_withheld: false,
  });
  assert.equal(view.versionId, 'version-2');
  assert.equal(view.versionNumber, 2);
  assert.equal(view.chars, 120);
  assert.equal(view.returnedChars, 80);
  assert.equal(view.content, '这一版的正文');
  assert.equal(view.truncated, true);
  assert.equal(view.withheld, false);

  const withheld = P.projectVersionView({
    ok: true,
    version: {version_id: 'version-2', version_number: 2, content: '', content_available: false},
    content_withheld: true,
  });
  assert.equal(withheld.withheld, true);
  assert.equal(P.projectVersionView({ok: true}), null);
});

test('对比视图：只搬服务给出的差异字段，没给就留 null', () => {
  const view = P.projectCompareView({
    ok: true,
    compare: {
      from: {version_id: 'version-1', version_number: 1, content_chars: 10, is_head: false},
      to: {version_id: 'version-3', version_number: 3, content_chars: 14, is_head: true},
      body_changed: true, hash_changed: true, content_changed: true,
      headers_differ: false, machine_head_only: false, identical_version: false,
      char_delta: 4,
      lines: {added_lines: 2, removed_lines: 1, bounded_lines: false},
      first_difference: {index: 5, from_context: '旧句', to_context: '新句'},
      comparison: {basis: 'stripped_body', from_truncated: false, to_truncated: false},
      reason_code: '',
    },
  });
  assert.equal(view.bodyChanged, true);
  assert.equal(view.addedLines, 2);
  assert.equal(view.removedLines, 1);
  assert.equal(view.charDelta, 4);
  assert.equal(view.firstDifference.index, 5);
  assert.equal(view.firstDifference.fromContext, '旧句');
  assert.equal(view.to.versionNumber, 3);
  // 服务没说“正文变没变”时就是 null，界面显示“无法确定”，不替它判。
  const bare = P.projectCompareView({ok: true, compare: {from: {}, to: {}}});
  assert.equal(bare.bodyChanged, null);
  assert.equal(bare.addedLines, null);
  assert.equal(P.projectCompareView({ok: true}), null);
});

test('派生请求体：来源、purpose 与 120 字说明都校验', () => {
  const id = 'artifact:' + 'a'.repeat(32);
  const payload = P.buildProjectDerivePayload({
    title: '夜航（派生）', content: '新正文', source_artifact_id: id,
    source_version_id: 'version-3', purpose: 'adapt', note: '派生自 v3', operation_key: 'k4',
  });
  assert.deepEqual({
    title: payload.title, content: payload.content, source_artifact_id: payload.source_artifact_id,
    source_version_id: payload.source_version_id, purpose: payload.purpose,
    note: payload.note, operation_key: payload.operation_key,
  }, {
    title: '夜航（派生）', content: '新正文', source_artifact_id: id,
    source_version_id: 'version-3', purpose: 'adapt', note: '派生自 v3', operation_key: 'k4',
  });
  assert.equal(P.buildProjectDerivePayload({title: '标题', content: '正文', source_artifact_id: id,
    source_version_id: 'version-3', purpose: 'steal', operation_key: 'k4'}), null);
  assert.equal(P.buildProjectDerivePayload({title: '标题', content: '正文', source_artifact_id: id,
    source_version_id: '', purpose: 'adapt', operation_key: 'k4'}), null);
  assert.equal(P.buildProjectDerivePayload({title: '标题', content: '正文', source_artifact_id: id,
    source_version_id: 'version-3', purpose: 'adapt', note: 'x'.repeat(121), operation_key: 'k4'}), null);
  assert.equal(P.buildProjectDerivePayload({title: '标题', content: '正文', source_artifact_id: 'bad id/x',
    source_version_id: 'version-3', purpose: 'adapt', operation_key: 'k4'}), null);
  // derive 有自己的键，不能和 commit 共用。
  assert.equal(P.PROJECT_SCOPES.derive, 'project:derive');
  assert.notEqual(P.PROJECT_SCOPES.derive, P.PROJECT_SCOPES.commit);
});

test('派生草稿与标题：预填原标题（派生）与那一版正文', () => {
  const id = 'artifact:' + 'a'.repeat(32);
  const draft = P.projectDeriveDraft(
    {id: id, title: '夜航手记'},
    {versionId: 'version-3', versionNumber: 3, content: '第三版正文'});
  assert.equal(draft.mode, 'derive');
  assert.equal(draft.artifactId, id);
  assert.equal(draft.sourceVersionId, 'version-3');
  assert.equal(draft.sourceVersionNumber, 3);
  assert.equal(draft.title, '夜航手记（派生）');
  assert.equal(draft.content, '第三版正文');
  assert.equal(draft.note, '派生自 v3');
  assert.equal(draft.purpose, 'adapt');
  assert.ok(P.projectDeriveTitle('x'.repeat(P.PROJECT_TITLE_MAX)).length <= P.PROJECT_TITLE_MAX);
});

test('版本路径：项目/版本 id 原样进路径，怪 id 拼不出来', () => {
  const id = 'artifact:' + 'a'.repeat(32);
  assert.equal(P.projectVersionsPath(id, {limit: 10}), '/projects/' + id + '/versions?limit=10');
  assert.equal(P.projectVersionsPath(id, {limit: 10, before: 3}),
    '/projects/' + id + '/versions?limit=10&before=3');
  assert.equal(P.projectVersionPath(id, 'version-3'), '/projects/' + id + '/versions/version-3');
  assert.equal(P.projectComparePath(id, 'version-3'),
    '/projects/' + id + '/versions/version-3/compare');
  assert.equal(P.projectVersionPath(id, 'bad id/x'), '');
  assert.equal(P.projectVersionsPath('bad id/x', {limit: 10}), '');
});

test('入口与样式：项目 app 已注册，正文块有可读样式', () => {
  assert.ok(P.APP_REGISTRY.projects, '主屏要有项目入口');
  assert.equal(P.APP_REGISTRY.projects.label, '项目');
  assert.match(css, /\.project-text\s*\{[^}]*white-space:\s*pre-wrap/);
  // 旧七个 app 的顺序一个字都没动（旧检查按这七个字面量对齐）。
  assert.deepEqual([...P.APP_ORDER].sort(),
    ['body', 'inventory', 'notes', 'reading', 'references', 'shop', 'wallet']);
});
