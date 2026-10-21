/**
 * NyAiro v0.4 阶段 B · 手机「资料库 / 研究工作台」界面数据层定向检查。
 *
 * 只检查本轮新增的纯函数面（没有 DOM 时 app.js 走的就是这条路）：资料列表/检索路径/详情、
 * 导入草稿与请求体、研究列表/详情（含每个来源的 CURRENT/STALE/UNAVAILABLE）、续写与关闭的
 * 请求体、两类路径拼装，以及样式里正文按原样换行。
 * 不启动服务、不造数据、不读存储；真实 HTTP 与 owner 交互由 tests/test_phone_research_api.py 覆盖。
 *
 * 运行：node --test tests/test_phone_research_ui.mjs
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

const MATERIAL_ID = 'artifact:' + 'c'.repeat(32);
const RESEARCH_ID = 'artifact:' + 'd'.repeat(32);

const materialListDoc = {
  ok: true,
  status: 'ok',
  data: {
    materials: [
      {artifact_id: MATERIAL_ID, title: '潮汐表（他抄的）', source_revision: 'version-1',
       source_kind: 'user_supplied', origin: '他手抄给我的一张纸', excerpt: '潮汐每天两次。',
       revision: 1, version_number: 1, lifecycle: 'ACTIVE', availability: 'AVAILABLE',
       kind: 'material', read_only: false, updated_at: 1760000100, not_memory: true},
      {artifact_id: 'artifact-reading', title: '她的阅读材料', source_revision: 'version-r1',
       source_kind: 'reading_material', origin: '既有阅读材料', excerpt: '第一页',
       revision: 3, version_number: 3, lifecycle: 'ACTIVE', availability: 'AVAILABLE',
       kind: 'reading_material', read_only: true, updated_at: 1760000000},
    ],
    query: '潮汐',
    count: 2,
    matched: 2,
    truncated: false,
    skipped: {research: 1, private_items: 1, other: 2},
  },
};

test('资料列表：不含正文、只读材料标出来、跳过项照实说', () => {
  const view = P.materialListView(materialListDoc);
  assert.equal(view.itemsMissing, false);
  assert.equal(view.materials.length, 2);
  assert.equal(view.materials[0].title, '潮汐表（他抄的）');
  assert.equal(view.materials[0].sourceKindText, '他给的文本');
  assert.equal(view.materials[0].readOnly, false);
  assert.equal(view.materials[1].readOnly, true);
  assert.equal(view.materials[1].sourceKind, 'reading_material');
  assert.equal(view.matched, 2);
  assert.equal(view.skippedResearch, 1);
  for (const row of view.materials) {
    assert.equal(Object.prototype.hasOwnProperty.call(row, 'text'), false, '列表不带正文');
  }
  // 缺 materials 数组就是缺，不假装空列表。
  assert.equal(P.materialListView({ok: true, data: {}}).itemsMissing, true);
});

test('资料详情：正文、来源声明与可用性', () => {
  const doc = {ok: true, status: 'ok', data: {
    material: {artifact_id: MATERIAL_ID, title: '潮汐表（他抄的）', source_revision: 'version-1',
      source_kind: 'user_supplied', origin: '他手抄给我的一张纸', text: '潮汐每天两次。\n第二行。',
      revision: 1, version_number: 1, lifecycle: 'ACTIVE', availability: 'AVAILABLE',
      read_only: false, content_available: true},
    note: '资料只是被保存下来的文本；origin 是来源声明，不是真实性认证。',
  }};
  const view = P.materialDetailView(doc);
  assert.equal(view.text, '潮汐每天两次。\n第二行。');
  assert.equal(view.material.origin, '他手抄给我的一张纸');
  assert.equal(view.availability, 'AVAILABLE');
  assert.equal(P.materialDetailView({ok: true, data: {}}), null);
});

test('导入草稿：标题/正文/类型/来源声明都缺一不可', () => {
  const empty = P.validateMaterialDraft({});
  assert.equal(empty.ok, false);
  assert.ok(empty.errors.length >= 4);
  const noOrigin = P.validateMaterialDraft({title: '纸', text: '正文', source_kind: 'fictional'});
  assert.equal(noOrigin.ok, false);
  const badKind = P.validateMaterialDraft({title: '纸', text: '正文', source_kind: 'web',
    origin: '来源'});
  assert.equal(badKind.ok, false);
  const ok = P.validateMaterialDraft({title: ' 纸 ', text: '一\r\n二', source_kind: 'user_supplied',
    origin: ' 他给的 '});
  assert.equal(ok.ok, true);
  assert.equal(ok.value.title, '纸');
  assert.equal(ok.value.text, '一\n二');
  assert.equal(ok.value.origin, '他给的');

  const asJson = (value) => JSON.stringify(value);
  assert.equal(asJson(P.buildMaterialImportPayload({title: '纸', text: '正文',
    source_kind: 'fictional', origin: '来源', operation_key: 'k1'})),
  asJson({title: '纸', text: '正文', source_kind: 'fictional', origin: '来源', operation_key: 'k1'}));
  assert.equal(P.buildMaterialImportPayload({title: '纸', text: '正文', source_kind: 'web',
    origin: '来源', operation_key: 'k1'}), null);
  assert.equal(P.buildMaterialImportPayload({title: '纸', text: '正文', source_kind: 'fictional',
    origin: '', operation_key: 'k1'}), null);
  assert.equal(P.buildMaterialImportPayload({title: '纸', text: '正文', source_kind: 'fictional',
    origin: '来源'}), null, '没有操作键就不发');
});

test('路径与检索参数：owner 的 id 原样进路径，查询被编码', () => {
  assert.equal(P.materialPath(MATERIAL_ID), '/materials/' + MATERIAL_ID);
  assert.equal(P.materialPath('bad id/x'), '');
  assert.equal(P.materialsListPath({}), '/materials');
  assert.equal(P.materialsListPath({query: '潮汐 表'}), '/materials?query=' +
    encodeURIComponent('潮汐 表'));
  assert.equal(P.materialsListPath({query: 'x', limit: 5}), '/materials?query=x&limit=5');
  assert.equal(P.materialsListPath({query: 'x'.repeat(P.MATERIAL_QUERY_MAX + 1)}), '',
    '检索词太长就不拼路径，不截断成另一个词');
  assert.equal(P.researchPath(RESEARCH_ID), '/research/' + RESEARCH_ID);
  assert.equal(P.researchUpdatesPath(RESEARCH_ID), '/research/' + RESEARCH_ID + '/updates');
  assert.equal(P.researchClosePath(RESEARCH_ID), '/research/' + RESEARCH_ID + '/close');
  assert.equal(P.researchUpdatesPath('bad/id'), '');
});

const researchDetailDoc = {
  ok: true,
  status: 'ok',
  data: {
    project: {
      artifact_id: RESEARCH_ID, title: '研究：潮汐为什么每天两次？',
      question: '潮汐为什么每天两次？', scope: '只讨论这张手抄纸上的内容',
      uncertainty: '暂定，尚未验证', scope_note: null,
      lifecycle: 'ACTIVE', status: 'active', revision: 2, head_version_id: 'version-2',
      version_number: 2,
      content: '<!-- nyairo-research:v1 {"question":"潮汐为什么每天两次？"} -->\n\n## 暂定发现\n两次潮差大概一样。',
      content_available: true, body: '## 暂定发现\n两次潮差大概一样。',
      source_refs: [{artifact_id: MATERIAL_ID, source_revision: 'version-1'}],
      source_checks: [
        {artifact_id: MATERIAL_ID, source_revision: 'version-1', status: 'STALE',
         reason_code: 'SOURCE_REVISION_MOVED', current_source_revision: 'version-2',
         title: '潮汐表（他抄的）'},
        {artifact_id: 'artifact:gone', source_revision: 'version-9', status: 'UNAVAILABLE',
         reason_code: 'SOURCE_ARCHIVED', title: ''},
      ],
      sources_current: false, not_memory: true, stable_belief: false,
      note: '研究作品不是 Memory，也不是稳定信念：内容仍是暂定的，随来源版本变化。',
    },
    source_checks: [],
  },
};

test('研究详情：正文、引用与每个来源的真实版本状态', () => {
  const view = P.researchDetailView(researchDetailDoc);
  assert.equal(view.project.title, '研究：潮汐为什么每天两次？');
  assert.equal(view.body, '## 暂定发现\n两次潮差大概一样。');
  assert.equal(view.refs.length, 1);
  assert.equal(view.refs[0].sourceRevision, 'version-1');
  assert.equal(view.checks.length, 2);
  assert.equal(view.checks[0].status, 'STALE');
  assert.equal(view.checks[0].statusText, '来源已更新');
  assert.equal(view.checks[0].currentRevision, 'version-2');
  assert.equal(view.checks[1].status, 'UNAVAILABLE');
  assert.equal(view.checks[1].statusText, '读不到/已撤回');
  assert.equal(view.sourcesCurrent, false, '有 STALE 就不能说来源都是当前版本');
  assert.equal(view.notMemory, true);
  assert.equal(view.stableBelief, false);
  assert.equal(P.researchDetailView({ok: true, data: {}}), null);
});

test('研究列表：不含正文，来源数量与关闭状态都在', () => {
  const doc = {ok: true, status: 'ok', data: {projects: [
    {artifact_id: RESEARCH_ID, title: '研究：潮汐', question: '潮汐为什么每天两次？',
     scope: '', uncertainty: '暂定', source_refs: [{artifact_id: MATERIAL_ID,
       source_revision: 'version-1'}], source_count: 1, lifecycle: 'ACTIVE', status: 'active',
     revision: 2, head_version_id: 'version-2', updated_at: 1760000200, not_memory: true},
    {artifact_id: 'artifact:closed', title: '研究：旧的', question: 'q', source_count: 0,
     lifecycle: 'ARCHIVED', status: 'archived', revision: 4, head_version_id: 'version-4'},
  ], count: 2, truncated: false, note: '研究作品不是 Memory。'}};
  const view = P.researchListView(doc);
  assert.equal(view.projects.length, 2);
  assert.equal(view.projects[0].sourceCount, 1);
  assert.equal(view.projects[0].status, 'active');
  assert.equal(view.projects[1].status, 'archived');
  assert.equal(view.note, '研究作品不是 Memory。');
  for (const row of view.projects) {
    assert.equal(Object.prototype.hasOwnProperty.call(row, 'content'), false);
  }
  assert.equal(P.researchListView({ok: true, data: {}}).itemsMissing, true);
});

test('研究草稿：问题与至少一件来源是硬要求', () => {
  const empty = P.validateResearchDraft({});
  assert.equal(empty.ok, false);
  assert.ok(empty.errors.some((line) => line.indexOf('问题') >= 0));
  assert.ok(empty.errors.some((line) => line.indexOf('资料') >= 0));
  const refs = [{artifact_id: MATERIAL_ID, source_revision: 'version-1'}];
  const ok = P.validateResearchDraft({question: ' 潮汐为什么两次 ', source_refs: refs});
  assert.equal(ok.ok, true);
  assert.equal(ok.value.question, '潮汐为什么两次');
  assert.equal(ok.value.uncertainty, P.RESEARCH_DEFAULT_UNCERTAINTY, '默认就是「暂定，尚未验证」');
  assert.equal(ok.value.source_refs.length, 1);
  const tooMany = P.validateResearchDraft({question: 'q',
    source_refs: new Array(P.RESEARCH_SOURCES_MAX + 1).fill(refs[0])});
  assert.equal(tooMany.ok, false);
});

test('研究请求体：创建带上可选字段，续写没有 head 就不发，关闭要 revision', () => {
  const asJson = (value) => JSON.stringify(value);
  const refs = [{artifact_id: MATERIAL_ID, source_revision: 'version-1'}];
  assert.equal(asJson(P.buildResearchCreatePayload({question: 'q', source_refs: refs,
    operation_key: 'k1'})),
  asJson({question: 'q', source_refs: refs, operation_key: 'k1'}));
  const withExtras = P.buildResearchCreatePayload({question: 'q', source_refs: refs,
    title: '标题', scope: '范围', uncertainty: '未知', operation_key: 'k1'});
  assert.equal(withExtras.title, '标题');
  assert.equal(withExtras.scope, '范围');
  assert.equal(withExtras.uncertainty, '未知');
  assert.equal(withExtras.question, 'q');
  assert.equal(withExtras.operation_key, 'k1');
  assert.deepEqual([...withExtras.source_refs], refs);
  assert.equal(P.buildResearchCreatePayload({question: 'q', source_refs: [], operation_key: 'k1'}),
    null, '没有来源就不发');

  assert.equal(asJson(P.buildResearchUpdatePayload({content: '新的一版',
    expected_head: 'version-2', operation_key: 'k2'})),
  asJson({content: '新的一版', expected_head: 'version-2', operation_key: 'k2'}));
  assert.equal(P.buildResearchUpdatePayload({content: '新的一版', expected_head: '',
    operation_key: 'k2'}), null, '没有 head 就是关掉 CAS，宁可不写');
  assert.equal(asJson(P.buildResearchUpdatePayload({content: '正文', expected_head: 'v',
    operation_key: 'k2', scope: '新范围', uncertainty: '更初步'})),
  asJson({content: '正文', expected_head: 'v', operation_key: 'k2', scope: '新范围',
    uncertainty: '更初步'}));

  assert.equal(asJson(P.buildResearchClosePayload({expected_revision: 2, operation_key: 'k3'})),
    asJson({expected_revision: 2, operation_key: 'k3'}));
  assert.equal(P.buildResearchClosePayload({expected_revision: 0, operation_key: 'k3'}), null);
  assert.equal(P.buildResearchClosePayload({expected_revision: '2', operation_key: 'k3'}), null);

  const draft = P.validateResearchUpdateDraft({content: '  '});
  assert.equal(draft.ok, false);
  const good = P.validateResearchUpdateDraft({content: '一\r\n二'});
  assert.equal(good.ok, true);
  assert.equal(good.value.content, '一\n二');
});

test('样式与作用域：正文按原样换行，四个作用域都有前缀', () => {
  assert.match(css, /\.research-text\s*\{[^}]*white-space:\s*pre-wrap/);
  assert.match(css, /\.material-text,/);
  assert.deepEqual([...P.MATERIAL_SOURCE_KINDS], ['user_supplied', 'fictional']);
  assert.equal(P.MATERIAL_SCOPES.import, 'material:import');
  assert.equal(P.MATERIAL_SCOPES.create, 'research:create');
  assert.equal(P.MATERIAL_SCOPES.update, 'research:update');
  assert.equal(P.MATERIAL_SCOPES.close, 'research:close');
  // 旧七个 app 的顺序一个字都没动；资料/研究不是新 tile。
  assert.deepEqual([...P.APP_ORDER].sort(),
    ['body', 'inventory', 'notes', 'reading', 'references', 'shop', 'wallet']);
  assert.equal(Object.prototype.hasOwnProperty.call(P.APP_REGISTRY, 'materials'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(P.APP_REGISTRY, 'research'), false);
});
