/**
 * NyAiro v0.4 阶段 B · 手机「个人实践工作台 + Memory」界面数据层定向检查。
 *
 * 只检查本轮新增的纯函数面（没有 DOM 时 app.js 走的就是这条路）：回顾/做法列表与详情、
 * 证据核对、试用与修订草稿校验、请求体、路径拼装、Memory 状态视图，以及样式里正文按原样换行。
 * 屏幕表与主屏入口用源码扫描确认（SCREENS 是 IIFE 内的常量，不对外导出）。
 * 不启动服务、不造数据、不读存储；真实 HTTP / owner / Memory 交互由
 * tests/test_phone_practice_api.py 与 tests/test_v04_personal_practice_stage.py 覆盖。
 *
 * 运行：node --test tests/test_phone_practice_ui.mjs
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

const REVIEW_ID = 'artifact:' + 'a'.repeat(32);
const METHOD_ID = 'artifact:' + 'b'.repeat(32);
const EXPERIENCE_ID = 'experience:' + 'c'.repeat(16);

const workbenchDoc = {
  ok: true,
  status: 'ok',
  data: {
    reviews: [
      {artifact_id: REVIEW_ID, kind: 'review', title: '写完一页的回顾', question: '怎样把一页写完？',
       experience_count: 1, experience_basis: 'experience', revision: 2, version_number: 2,
       head_version_id: 'version-2', lifecycle: 'ACTIVE', status: 'active',
       updated_at: 1760000200, not_memory: true},
      {artifact_id: 'artifact:' + 'd'.repeat(32), kind: 'review', title: '一份没有经历的计划',
       question: '还没证据的问题', experience_count: 0, experience_basis: 'none', revision: 1,
       lifecycle: 'ACTIVE', status: 'active', updated_at: 1760000100},
    ],
    methods: [
      {artifact_id: METHOD_ID, kind: 'method', title: '先写三行', instruction: '先写三行，再展开。',
       scope: '一页以内', uncertainty: '长文未知', method_state: 'CONTESTED',
       experience_count: 1, review_count: 1, trial_count: 2, counterexample_count: 1,
       revisions: 1, revision: 4, version_number: 4, head_version_id: 'version-4',
       lifecycle: 'ACTIVE', status: 'active', updated_at: 1760000300, verified_skill: false},
    ],
    counts: {reviews: 2, methods: 1},
    review_status: 'ok', method_status: 'ok',
    review_truncated: false, method_truncated: false,
    memory: {binding_status: 'READY', recall_ready: true, owner: 'chiyo', installed: true,
      enable_available: true, participation: 'BOUND_READY_READONLY_VERIFIED'},
    memory_status: 'ok',
    not_verified_skill: true
  }
};

test('工作台列表：回顾与做法分开、任何一行都不带正文', () => {
  const view = P.practiceListView(workbenchDoc);
  assert.equal(view.itemsMissing, false);
  assert.equal(view.reviews.length, 2);
  assert.equal(view.methods.length, 1);
  assert.equal(view.reviews[0].kindText, '回顾');
  assert.equal(view.reviews[1].experienceCount, 0);
  assert.equal(view.methods[0].kindText, '做法');
  assert.equal(view.methods[0].stateText, '有反例');
  assert.equal(view.methods[0].counterexampleCount, 1);
  assert.equal(view.methods[0].verifiedSkill, false);
  assert.equal(view.memory.bindingStatus, 'READY');
  assert.equal(view.memory.recallReady, true);
  assert.equal(view.notVerifiedSkill, true);
  for (const row of view.reviews.concat(view.methods)) {
    assert.equal(Object.prototype.hasOwnProperty.call(row, 'body'), false, '列表不带正文');
    assert.equal(Object.prototype.hasOwnProperty.call(row, 'content'), false, '列表不带正文');
  }
  // 缺数组就是缺，不假装空列表。
  assert.equal(P.practiceListView({ok: true, data: {}}).itemsMissing, true);
});

test('做法详情：状态、证据核对、反例与「不是已验证 Skill」', () => {
  const doc = {ok: true, status: 'ok', data: {
    project: {artifact_id: METHOD_ID, kind: 'method', title: '先写三行', method_state: 'CONTESTED',
      revision: 4, version_number: 4, head_version_id: 'version-4', lifecycle: 'ACTIVE',
      status: 'active'},
    kind: 'method', body: '## 做法\n先写三行。', content: '<!-- marker -->\n\n## 做法',
    content_available: true, head_version_id: 'version-4', revision: 4,
    governance: {blocked: false, reason_code: ''},
    instruction: '先写三行，再展开。', scope: '一页以内', uncertainty: '长文未知',
    method_state: 'CONTESTED', experience_ids: [EXPERIENCE_ID], review_refs: [REVIEW_ID],
    evidence_checks: [
      {kind: 'experience', experience_id: EXPERIENCE_ID, status: 'CURRENT', usable: true,
       excerpt: '先写三行再展开，那一页就写完了。', occurred_at: '2026-11-01'},
      {kind: 'review', artifact_id: REVIEW_ID, status: 'UNAVAILABLE', usable: false,
       reason_code: 'PRACTICE_EVIDENCE_UNAVAILABLE', title: '写完一页的回顾'}
    ],
    trials: [
      {outcome: 'HELPED', detail: '有用', version_id: 'version-2', evidence_usable: true},
      {outcome: 'NOT_HELPED', detail: '长文没用', version_id: 'version-3', evidence_usable: true}
    ],
    counterexamples: [{outcome: 'NOT_HELPED', detail: '长文没用'}],
    revisions: 1, verified_skill: false, stable_belief: false,
    note: '做法草案不是已验证 Skill，也不是人格或稳定信念。'
  }};
  const view = P.practiceDetailView(doc);
  assert.equal(view.kind, 'method');
  assert.equal(view.methodState, 'CONTESTED');
  assert.equal(view.stateText, '有反例');
  assert.equal(view.checks.length, 2);
  assert.equal(view.checks[0].statusText, '现在仍可读');
  assert.equal(view.checks[1].usable, false);
  assert.equal(view.checks[1].kindText, '被引用的回顾');
  assert.equal(view.unusableCount, 1);
  assert.equal(view.trials.length, 2);
  assert.equal(view.counterexamples.length, 1);
  assert.equal(view.counterexamples[0].outcomeText, '这次没用');
  assert.equal(view.verifiedSkill, false);
  assert.equal(view.blocked, false);
  assert.ok(view.body.indexOf('先写三行') >= 0);
  assert.equal(P.practiceDetailView({ok: true, data: {}}), null);
});

test('治理挡住时详情不给正文（界面也不替它显示）', () => {
  const doc = {ok: true, status: 'ok', reason_code: 'PRACTICE_EVIDENCE_UNAVAILABLE', data: {
    project: {artifact_id: REVIEW_ID, kind: 'review', title: '回顾一', lifecycle: 'ACTIVE',
      status: 'active', revision: 2, head_version_id: 'version-2'},
    kind: 'review', body: '', content: '', content_available: false,
    governance: {blocked: true, reason_code: 'PRACTICE_EVIDENCE_UNAVAILABLE',
      detail: '依赖它的正文不再透出。'},
    question: '怎样把一页写完？', experience_ids: [EXPERIENCE_ID],
    experience_checks: [{kind: 'experience', experience_id: EXPERIENCE_ID,
      status: 'UNAVAILABLE', usable: false, reason_code: 'MEMORY_EXPERIENCE_NOT_RECALLABLE'}]
  }};
  const view = P.practiceDetailView(doc);
  assert.equal(view.blocked, true);
  assert.equal(view.contentAvailable, false);
  assert.equal(view.body, '');
  assert.equal(view.blockedReason, 'PRACTICE_EVIDENCE_UNAVAILABLE');
  assert.equal(view.unusableCount, 1);
});

test('回顾详情：问题、无经历基础的说明与 head', () => {
  const doc = {ok: true, status: 'ok', data: {
    project: {artifact_id: REVIEW_ID, kind: 'review', title: '计划', lifecycle: 'ACTIVE',
      status: 'active', revision: 1, version_number: 1, head_version_id: 'version-1'},
    kind: 'review', body: '一份计划', content: 'x', content_available: true,
    governance: {blocked: false}, question: '还没证据的问题', experience_ids: [],
    experience_basis: 'none', experience_checks: [],
    note: '这份回顾没有引用任何经历：它是一份计划/问题，不是已证经验的记录。'
  }};
  const view = P.practiceDetailView(doc);
  assert.equal(view.experienceIds.length, 0);
  assert.equal(view.checks.length, 0);
  assert.equal(view.project.head, 'version-1');
  assert.ok(view.note.indexOf('没有引用任何经历') >= 0);
});

test('Memory 状态视图：READY / UNBOUND / BLOCKED 都说人话', () => {
  const ready = P.memoryStatusView({ok: true, data: {binding_status: 'READY', recall_ready: true,
    owner: 'chiyo', installed: true}});
  assert.equal(ready.statusText, '已绑定（只读可召回）');
  assert.equal(ready.ok, true);
  const unbound = P.memoryStatusView({ok: false, data: {binding_status: 'UNBOUND',
    recall_ready: false, owner: 'chiyo', installed: true, reason: 'MEMORY_OPTIONAL_UNBOUND'}});
  assert.equal(unbound.statusText, '未绑定 Memory');
  assert.equal(unbound.ok, false);
  assert.equal(unbound.reason, 'MEMORY_OPTIONAL_UNBOUND');
  const blocked = P.memoryStatusView({ok: false, data: {binding_status: 'BLOCKED',
    recall_ready: false, installed: true}});
  assert.equal(blocked.statusText, '绑定但不可用');
  assert.match(ready.note, /不会自动重启/);
});

test('经历列表与详情：只搬运 id/时间/来源，不编造', () => {
  const list = P.experienceListView({ok: true, data: {
    experiences: [{experience_id: EXPERIENCE_ID, occurred_at: '2026-11-01T08:00:00+00:00',
      text: '先写三行再展开，那一页就写完了。', outcome_kind: 'VERIFIED_RESULT', owner: 'chiyo'}],
    count: 1, query: '写', binding_status: 'READY', owner: 'chiyo'
  }});
  assert.equal(list.itemsMissing, false);
  assert.equal(list.experiences.length, 1);
  assert.equal(list.experiences[0].owner, 'chiyo');
  assert.equal(list.count, 1);
  assert.equal(P.experienceListView({ok: true, data: {}}).itemsMissing, true);
  const detail = P.experienceDetailView({ok: true, data: {
    experience: {experience_id: EXPERIENCE_ID, text: '正文', occurred_at: '2026-11-01',
      source_ref: 'life:test'},
    binding_status: 'READY', owner: 'chiyo', experience_id: EXPERIENCE_ID
  }});
  assert.equal(detail.experience.text, '正文');
  assert.equal(detail.experience.sourceRef, 'life:test');
  assert.equal(P.experienceDetailView({ok: true, data: {}}), null);
});

test('回顾草稿：问题必填，经历引用可选且被规范化', () => {
  const empty = P.validatePracticeReviewDraft({});
  assert.equal(empty.ok, false);
  assert.ok(empty.errors.some((line) => line.indexOf('问题') >= 0));
  const ok = P.validatePracticeReviewDraft({question: ' 怎样写完一页 ', experience_ids: [EXPERIENCE_ID]});
  assert.equal(ok.ok, true);
  assert.equal(ok.value.question, '怎样写完一页');
  assert.deepEqual([...ok.value.experience_ids], [EXPERIENCE_ID]);
  const noExperience = P.validatePracticeReviewDraft({question: '问题'});
  assert.equal(noExperience.ok, true);
  assert.deepEqual([...noExperience.value.experience_ids], []);
});

test('做法草稿：做法必填，未知默认「暂定，尚未验证」', () => {
  const empty = P.validatePracticeMethodDraft({});
  assert.equal(empty.ok, false);
  assert.ok(empty.errors.some((line) => line.indexOf('标题') >= 0));
  assert.ok(empty.errors.some((line) => line.indexOf('具体做法') >= 0));
  const ok = P.validatePracticeMethodDraft({title: ' 先写三行 ', instruction: '先写三行\r\n再展开。',
    scope: '一页以内'});
  assert.equal(ok.ok, true);
  assert.equal(ok.value.title, '先写三行');
  assert.equal(ok.value.instruction, '先写三行\n再展开。');
  assert.equal(ok.value.uncertainty, P.PRACTICE_DEFAULT_UNCERTAINTY);
  assert.equal(ok.value.experience_ids.length, 0);
});

test('试用草稿：有用/没用必须有证据，没有 head 不发', () => {
  const noEvidence = P.validatePracticeTrialDraft({outcome: 'HELPED', expected_head: 'v1'});
  assert.equal(noEvidence.ok, false);
  assert.ok(noEvidence.errors.some((line) => line.indexOf('说不好') >= 0));
  const unknown = P.validatePracticeTrialDraft({outcome: 'UNKNOWN', expected_head: 'v1'});
  assert.equal(unknown.ok, true);
  const withEvidence = P.validatePracticeTrialDraft({outcome: 'HELPED', detail: '这一次有用',
    experience_ids: [EXPERIENCE_ID], expected_head: 'v1'});
  assert.equal(withEvidence.ok, true);
  assert.equal(withEvidence.value.outcome, 'HELPED');
  const noHead = P.validatePracticeTrialDraft({outcome: 'UNKNOWN', expected_head: ''});
  assert.equal(noHead.ok, false);
  assert.ok(noHead.errors.some((line) => line.indexOf('head') >= 0));
  const badOutcome = P.validatePracticeTrialDraft({outcome: 'SUPPORTED', expected_head: 'v1'});
  assert.equal(badOutcome.ok, false);
});

test('修订草稿：做法必填、head 必填、未知回落默认值', () => {
  const empty = P.validatePracticeRevisionDraft({});
  assert.equal(empty.ok, false);
  const ok = P.validatePracticeRevisionDraft({instruction: '先写五行', expected_head: 'v2'});
  assert.equal(ok.ok, true);
  assert.equal(ok.value.uncertainty, P.PRACTICE_DEFAULT_UNCERTAINTY);
  assert.equal(ok.value.expected_head, 'v2');
});

test('请求体：缺 head / 缺 key / 缺证据都不发', () => {
  const asJson = (value) => JSON.stringify(value);
  assert.equal(asJson(P.buildPracticeReviewCreatePayload({question: 'q',
    experience_ids: [EXPERIENCE_ID], operation_key: 'k1'})),
  asJson({question: 'q', experience_ids: [EXPERIENCE_ID], operation_key: 'k1'}));
  assert.equal(P.buildPracticeReviewCreatePayload({question: 'q', experience_ids: [],
    operation_key: 'k1'}).experience_ids.length, 0);
  assert.equal(P.buildPracticeReviewCreatePayload({question: '', operation_key: 'k1'}), null);
  assert.equal(P.buildPracticeReviewCreatePayload({question: 'q'}), null, '没有操作键就不发');

  const method = P.buildPracticeMethodCreatePayload({title: 't', instruction: 'i',
    operation_key: 'k2'});
  assert.equal(method.title, 't');
  assert.equal(method.uncertainty, P.PRACTICE_DEFAULT_UNCERTAINTY);
  assert.equal(P.buildPracticeMethodCreatePayload({title: '', instruction: 'i',
    operation_key: 'k2'}), null);

  assert.equal(asJson(P.buildPracticeDocumentPayload({content: '新的一版', expected_head: 'v1',
    operation_key: 'k3'})),
  asJson({content: '新的一版', expected_head: 'v1', operation_key: 'k3'}));
  assert.equal(P.buildPracticeDocumentPayload({content: '新的一版', expected_head: '',
    operation_key: 'k3'}), null, '没有 head 就是关掉 CAS，宁可不写');

  assert.equal(asJson(P.buildPracticeTrialPayload({outcome: 'HELPED', detail: 'd',
    experience_ids: [EXPERIENCE_ID], expected_head: 'v2', operation_key: 'k4'})),
  asJson({outcome: 'HELPED', detail: 'd', experience_ids: [EXPERIENCE_ID],
    expected_head: 'v2', operation_key: 'k4'}));
  assert.equal(P.buildPracticeTrialPayload({outcome: 'HELPED', experience_ids: [],
    expected_head: 'v2', operation_key: 'k4'}), null);

  assert.equal(asJson(P.buildPracticeRetirePayload({expected_head: 'v3', operation_key: 'k5'})),
  asJson({expected_head: 'v3', operation_key: 'k5'}));
  assert.equal(P.buildPracticeRetirePayload({expected_head: '', operation_key: 'k5'}), null);

  assert.equal(asJson(P.buildMemoryEnablePayload({operation_key: 'memory:1'})),
  asJson({operation_key: 'memory:1'}));
  assert.equal(P.buildMemoryEnablePayload({}), null);
});

test('路径：owner 的 id 原样进路径，查询被编码，超长就不拼', () => {
  assert.equal(P.practiceReviewPath(REVIEW_ID), '/practice/reviews/' + REVIEW_ID);
  assert.equal(P.practiceReviewDocumentsPath(REVIEW_ID),
    '/practice/reviews/' + REVIEW_ID + '/documents');
  assert.equal(P.practiceMethodPath(METHOD_ID), '/practice/methods/' + METHOD_ID);
  assert.equal(P.practiceMethodTrialsPath(METHOD_ID), '/practice/methods/' + METHOD_ID + '/trials');
  assert.equal(P.practiceMethodRevisionsPath(METHOD_ID),
    '/practice/methods/' + METHOD_ID + '/revisions');
  assert.equal(P.practiceMethodRetirePath(METHOD_ID), '/practice/methods/' + METHOD_ID + '/retire');
  assert.equal(P.practiceReviewDocumentsPath('bad/id'), '');
  assert.equal(P.practiceMethodPath('bad id'), '');
  assert.equal(P.practiceMethodsPath({}), '/practice/methods');
  assert.equal(P.practiceMethodsPath({query: '三行 x', limit: 5}),
    '/practice/methods?query=' + encodeURIComponent('三行 x') + '&limit=5');
  assert.equal(P.practiceMethodsPath({query: 'x'.repeat(P.PRACTICE_QUERY_MAX + 1)}), '');
  assert.equal(P.memoryExperiencesPath({}), '/memory/experiences');
  assert.equal(P.memoryExperiencesPath({query: '写', limit: 3}),
    '/memory/experiences?query=' + encodeURIComponent('写') + '&limit=3');
  assert.equal(P.memoryExperiencePath(EXPERIENCE_ID), '/memory/experiences/' + EXPERIENCE_ID);
  assert.equal(P.memoryExperiencePath(''), '');
});

test('样式与作用域：正文按原样换行，作用域都有前缀，旧 app 顺序没动', () => {
  assert.match(css, /\.practice-text\s*\{[^}]*white-space:\s*pre-wrap/);
  assert.match(css, /\.practice-input\s*\{/);
  assert.match(css, /\.material-text,[\s\S]{0,80}\.practice-text/);
  assert.deepEqual([...P.PRACTICE_METHOD_STATES], ['TENTATIVE', 'TRIAL', 'CONTESTED', 'RETIRED']);
  assert.deepEqual([...P.PRACTICE_TRIAL_OUTCOMES], ['HELPED', 'NOT_HELPED', 'UNKNOWN']);
  assert.equal(P.PRACTICE_SCOPES.reviewCreate, 'practice:review');
  assert.equal(P.PRACTICE_SCOPES.document, 'practice:document');
  assert.equal(P.PRACTICE_SCOPES.methodCreate, 'practice:method');
  assert.equal(P.PRACTICE_SCOPES.trial, 'practice:trial');
  assert.equal(P.PRACTICE_SCOPES.revise, 'practice:revise');
  assert.equal(P.PRACTICE_SCOPES.retire, 'practice:retire');
  assert.equal(P.PRACTICE_SCOPES.memoryEnable, 'memory:enable');
  /* 旧七个 app 的顺序一个字都没动；实践工作台不是第十一个 tile。 */
  assert.deepEqual([...P.APP_ORDER].sort(),
    ['body', 'inventory', 'notes', 'reading', 'references', 'shop', 'wallet']);
  assert.equal(Object.prototype.hasOwnProperty.call(P.APP_REGISTRY, 'practice'), false);
});

test('治理挡住时元数据自然为空（问句 / 指令 / 试用说明都不渲染旧正文）', () => {
  const sentinel = '北极星旧正文-9d1c';
  const doc = {ok: true, status: 'ok', reason_code: 'PRACTICE_EVIDENCE_UNAVAILABLE', data: {
    project: {artifact_id: METHOD_ID, kind: 'method', title: '做法-治理', lifecycle: 'ACTIVE',
      status: 'active', revision: 3, head_version_id: 'version-3'},
    kind: 'method', body: '', content: '', content_available: false,
    governance: {blocked: true, reason_code: 'PRACTICE_EVIDENCE_UNAVAILABLE'},
    instruction: '', scope: '', uncertainty: '', method_state: 'TENTATIVE',
    experience_ids: [EXPERIENCE_ID], review_refs: [],
    evidence_checks: [{kind: 'experience', experience_id: EXPERIENCE_ID,
      status: 'UNAVAILABLE', usable: false}],
    trials: [{outcome: 'HELPED', detail: '', version_id: 'version-2', evidence_usable: true}],
    counterexamples: [], verified_skill: false, stable_belief: false
  }};
  const view = P.practiceDetailView(doc);
  assert.equal(view.blocked, true);
  assert.equal(view.instruction, '');
  assert.equal(view.scope, '');
  assert.equal(view.uncertainty, '');
  assert.equal(view.body, '');
  assert.equal(view.trials.length, 1, '状态可以留，但说明是空的');
  assert.equal(view.trials[0].detail, '');
  assert.equal(JSON.stringify(view).indexOf(sentinel), -1);
  const row = P.practiceRowView({artifact_id: METHOD_ID, kind: 'method', title: '做法-治理',
    instruction: '', scope: '', uncertainty: '', method_state: 'TENTATIVE',
    governance_blocked: true});
  assert.equal(row.instruction, '');
  assert.equal(row.stateText, '草案（未验证）');
});

test('屏幕表与主屏入口：八屏都注册了，主屏有一个直接入口', () => {
  const screens = ['practice', 'practice-review', 'practice-review-new', 'practice-review-edit',
    'practice-method', 'practice-method-new', 'practice-method-revise', 'practice-experience'];
  for (const screen of screens) {
    const escaped = screen.replace(/-/g, '\\-');
    assert.match(src, new RegExp("'?" + escaped + "'?: \\{render:"), screen + ' 必须在屏幕表里');
    assert.match(src, new RegExp("'?" + escaped + "'?: \\{label:"), screen + ' 必须有导航标题');
  }
  assert.match(src, /go\('practice'\)/);
  /* 没有删除入口：实践只到 retire，界面没有 delete 这个词的路径。 */
  assert.equal(/\/practice\/[^'"\s]*delete/.test(src), false);
});
