/*
 * NyAiro v0.4 · 虚拟手机前端（components/phone/web/app.js）
 *
 * 合同：v0.4 手机施工单「第一版 HTTP 契约」，路径前缀 /api/phone。
 * 铁律：
 *   1. 只消费同源 /api/phone。没有服务就显示“未连接”，不 mock、不拿示例数据顶上。
 *   2. 成功 / 价格 / 余额只看服务响应；缺字段就写“未提供”，界面不补造。
 *   3. 所有后端文本先 sanitize 再用 textContent 写入，不拼 HTML。
 *   4. 不写 localStorage；sessionStorage 只存未决 operation id 与导航位置。
 *   5. 每次 mutation 复用同一个 UUID 操作键；结果未知时保留键，可用
 *      GET /api/phone/operations/<key> 对账，不自动换键盲重发。
 *   6. 写入必须带 X-CSRF-Token（值来自 GET /state 的 csrf_token）；拿不到就只读，不绕过。
 */
(function () {
  'use strict';

  const root = typeof globalThis !== 'undefined' ? globalThis : (function () { return this; })();

  const API_BASE = '/api/phone';
  const READ_TIMEOUT_MS = 12000;
  const WRITE_TIMEOUT_MS = 20000;
  const MAX_REQUEST_LOG = 12;
  const SVG_NS = 'http://www.w3.org/2000/svg';
  /* 相册（EP-16）：与 owner 同一条上限；图片 id 只接受协议形状。 */
  const ALBUM_MAX_BYTES = 5 * 1024 * 1024;
  const ALBUM_MAX_CAPTION = 200;
  const ALBUM_ID_SAFE = /^[A-Za-z0-9._:\-]+$/;

  const SESSION_KEYS = {
    pending: 'nyairo.phone.pending.v1',
    unresolved: 'nyairo.phone.unresolved.v1',
    nav: 'nyairo.phone.nav.v1'
  };

  /* 七个旧入口的顺序一个字都不动（旧检查按这七个字面量对齐）。 */
  const APP_ORDER = ['notes', 'references', 'reading', 'wallet', 'shop', 'inventory', 'body'];
  /* 第八个入口：世界/身体体验面。APP_ORDER 保持七项，主屏网格用 HOME_ORDER。 */
  const WORLD_APP = 'world';
  const HOME_ORDER = APP_ORDER.concat([WORLD_APP]);

  const APP_REGISTRY = {
    notes: {label: '笔记', icon: 'note', desc: '写下还能翻开的一页'},
    references: {label: '收藏', icon: 'bookmark', desc: '只记出处，不抓内容'},
    reading: {label: '阅读', icon: 'book', desc: '真实取得的材料'},
    wallet: {label: '钱包', icon: 'wallet', desc: '虚拟预算与流水'},
    shop: {label: '商店', icon: 'bag', desc: '目录、价格与购买'},
    inventory: {label: '物品', icon: 'box', desc: '归属、位置与使用'},
    body: {label: '身体', icon: 'heart', desc: '只读状态视图'},
    world: {label: '世界', icon: 'map', desc: '街区、房间与路程'},
    /* 第十个入口：她自己的私人项目（B 的 private_project_service，作品仍是 Supply 工件）。 */
    projects: {label: '项目', icon: 'note', desc: '自己的多份作品'},
    /* 第十一个入口：自己从本机导入的相册图片（Supply 同一 owner 的媒体工件）。 */
    album: {label: '相册', icon: 'bookmark', desc: '自己导入的图片'},
  };
  const SCREEN_META = {
    home: {label: '主屏'},
    notes: {label: '笔记'},
    'note-edit': {label: '写笔记'},
    references: {label: '收藏'},
    reading: {label: '阅读'},
    wallet: {label: '钱包'},
    shop: {label: '商店'},
    'shop-item': {label: '商品详情'},
    inventory: {label: '物品'},
    body: {label: '身体'},
    world: {label: '世界'},
    store: {label: '实体日用店'},
    'store-item': {label: '店里的商品'},
    projects: {label: '项目'},
    album: {label: '相册'},
    'album-image': {label: '这张图片'},
    'album-import': {label: '导入图片'},
    project: {label: '项目详情'},
    'project-edit': {label: '写项目'},
    materials: {label: '资料'},
    material: {label: '资料详情'},
    'material-import': {label: '导入资料'},
    research: {label: '研究'},
    'research-item': {label: '研究详情'},
    'research-create': {label: '起一份研究'},
    'research-edit': {label: '写研究'},
    practice: {label: '实践工作台'},
    'practice-review': {label: '回顾详情'},
    'practice-review-new': {label: '起一份回顾'},
    'practice-review-edit': {label: '写回顾'},
    'practice-method': {label: '做法详情'},
    'practice-method-new': {label: '写做法草案'},
    'practice-method-revise': {label: '修订做法'},
    'practice-experience': {label: '经历详情'},
    shares: {label: '分享与收件箱'},
    share: {label: '这条分享'},
    'share-new': {label: '写一条分享'},
    contact: {label: '来访与共同时间'},
    'contact-event': {label: '这次来话'},
    'shared-practice': {label: '共同实践'},
    'shared-practice-work': {label: '这次共同实践'},
  };

  const RESOURCE_PATH = {
    notes: '/notes',
    references: '/references',
    reading: '/reading',
    wallet: '/wallet',
    shop: '/shop',
    inventory: '/inventory',
    world: '/world',
    store: '/store',
    projects: '/projects',
    album: '/album',
  };

  /* 主屏「此刻」：只读投影，与服务/监督入口同一源；不是资源读取，走自己的装载函数。 */
  const LIVING_PATH = '/living';
  const LIVING_WINDOW_PATH = '/living/window';
  const LIVING_PAUSE_PATH = '/living/pause';
  const LIVING_STOP_PATH = '/living/stop';
  /* 一次明确的有界窗口：默认只花 1 次请求、120 秒；上限由服务端强制，界面不放大。 */
  const LIVING_DEFAULT_SECONDS = 120;
  const LIVING_DEFAULT_REQUESTS = 1;

  const SCREEN_RESOURCE = {
    notes: 'notes',
    references: 'references',
    reading: 'reading',
    wallet: 'wallet',
    shop: 'shop',
    inventory: 'inventory',
    world: 'world',
    store: 'store',
    projects: 'projects',
    album: 'album',
  };

  /* 详情屏各自记住选中哪一件：商品详情用 shop-item，实体店用 store-item（两条渠道不串）；
     项目详情/写项目共用 projectDetailId，因为写的就是那一件。 */
  const DETAIL_FIELDS = {'shop-item': 'shopDetailId', 'store-item': 'storeDetailId',
    'project': 'projectDetailId', 'project-edit': 'projectDetailId',
    'album-image': 'albumDetailId',
    'material': 'materialDetailId', 'material-import': 'materialDetailId',
    'research-item': 'researchDetailId', 'research-create': 'researchDetailId',
    'research-edit': 'researchDetailId',
    'practice-review': 'practiceDetailId', 'practice-review-edit': 'practiceDetailId',
    'practice-method': 'practiceDetailId', 'practice-method-revise': 'practiceDetailId',
    'share': 'shareDetailId', 'contact-event': 'contactDetailRef',
    'shared-practice-work': 'sharedPracticeDetailId'};

  /* 实践详情/编辑屏共用 practiceDetailId：写的就是那一件（与项目/研究同一套约定）。 */
  const PRACTICE_DETAIL_SCREENS = ['practice-review', 'practice-review-edit',
    'practice-method', 'practice-method-revise'];

  const MUTATION_STATUSES = ['applied', 'already_applied', 'rejected', 'unknown', 'conflict'];
  /* ``stale_source`` 是 B 自己的一个结论：来源换版/被拒，这次**没有写**。它既不是 conflict
     （草稿没被人改），也不是 unknown（结果是确定的），所以单独处理，不进上面那张表
     （那张表是旧检查按字面量对齐的）。 */
  const SHARE_STALE_STATUS = 'stale_source';

  /* 服务原因码 → 可读中文。未列出的原因码原样显示，不猜含义。 */
  const REASON_TEXT = {
    INSUFFICIENT_FUNDS: '余额不足，未扣款',
    BALANCE_TOO_LOW: '余额不足，未扣款',
    SHOP_CLOSED: '商店当前未营业',
    PURCHASE_UNAVAILABLE: '当前不可购买',
    PURCHASE_DISABLED: '当前不可购买',
    NOT_AT_LOCATION: '当前位置不符，不能进行这一步',
    LOCATION_MISMATCH: '当前位置不符，不能进行这一步',
    UNAUTHORIZED: '未获授权',
    FORBIDDEN: '未获授权',
    READ_ONLY: '实例处于只读状态',
    ITEM_NOT_DELIVERED: '物品还没送达，不能提前使用',
    NOT_DELIVERED: '物品还没送达，不能提前使用',
    ALREADY_CONSUMED: '这件东西已经用过了，不能再次消耗',
    ALREADY_USED: '这件东西已经用过了，不能再次消耗',
    NOT_OWNER: '所有者不符，这件东西不属于该实例',
    OWNERSHIP_MISMATCH: '所有者不符，这件东西不属于该实例',
    CONFLICT: '内容已被更新，这次没有写入',
    REVISION_CONFLICT: '内容已被更新，这次没有写入',
    NOT_FOUND: '服务里没有找到这一项',
    CSRF_MISSING: '请求缺少写入令牌',
    INVALID_CSRF: '写入令牌无效或已过期',
    APP_UNAVAILABLE: '应用当前不可用',
    APP_DISABLED: '应用已被禁用',
    /* ---- 分享（B 的 LifeSharingService，经 P 的信封；原因码原样保留） ---- */
    SOURCE_GOVERNED: '来源现在读不到（owner 拒绝），这段正文不再投影',
    SOURCE_REVISION_CHANGED: '来源换版了：这条草稿要按现在的正文重新准备',
    SOURCE_ARCHIVED: '来源已归档，不能再拿它发',
    SOURCE_NOT_FOUND: '来源不存在了',
    SOURCE_UNREADABLE: '来源现在读不到',
    SHARE_ALREADY_DELIVERED: '这条已经在本地收件箱里了，不会第二次投递',
    SHARE_TERMINAL_STATE: '这条分享已经结束（放弃 / 过期），不能重发',
    SHARE_EXPIRED: '草稿过期了，没有发出',
    SHARE_TEXT_REQUIRED: '要写一句明确的分享文本，或指定来源前缀长度',
    SHARE_TEXT_INVALID: '分享文本不可用（空、过长或有控制字符）',
    SHARE_SOURCE_NOT_OFFERED: '来源不在这台实例真的读得到的作品里',
    SHARE_STATE_NOT_ALLOWED: '这个状态不允许这个动作',
    SHARE_ACTION_REFUSED: '这个动作手机不做',
    SHARE_ID_INVALID: '分享标识不可用',
    SHARE_TITLE_INVALID: '标题不可用',
    EXPIRES_AFTER_INVALID: '过期窗口不可用（60–2592000 秒）',
    EXCERPT_CHARS_INVALID: '前缀长度不可用（1–400）',
    DEFER_CONDITION_REQUIRED: '延后要写清等到什么机会',
    CAS_CONFLICT: '草稿版本已经变了，这次没有写入',
    SHARE_PORT_ERROR: '分享 owner 这次没有回答',
    SHARES_UNAVAILABLE: '分享 owner 现在不可用',
    SHARES_UNBOUND: '这台实例没有绑定工作区 / 授权，分享不可用',
    SHARES_PROJECTS_UNAVAILABLE: '作品列表现在读不到',
    /* ---- 共同实践（A 的 shared_practice_service，经 P 的信封；原因码原样保留） ---- */
    SHARED_PRACTICE_UNBOUND: '这台实例还没接上共同实践的 owner，界面不替你编一份',
    SHARED_PRACTICE_INTERFACE_MISMATCH: '共同实践的 owner 接口还没对齐，这次没有调用',
    SHARED_PRACTICE_OWNER_ERROR: '共同实践的 owner 这次没有回答',
    SHARED_PRACTICE_ANSWER_INVALID: '共同实践的 owner 回了看不懂的答案',
    SHARED_PRACTICE_CALL_REFUSED: '共同实践的 owner 不接受这个调用形状',
    SHARED_PRACTICE_OP_UNSUPPORTED: '这一面 owner 还没提供',
    SHARED_PRACTICE_ACTION_REFUSED: '这个动作手机不做',
    SHARED_PRACTICE_ACTION_UNSUPPORTED: '手机只做提交 / 修改 / 暂停 / 重新取用 / 结束',
    SHARED_PRACTICE_TEXT_REQUIRED: '要写一句你自己的话',
    SHARED_PRACTICE_TEXT_INVALID: '这段话不可用（空、过长或有控制字符）',
    SHARED_PRACTICE_SOURCE_REQUIRED: '先选一份明确共享的材料',
    SHARED_PRACTICE_SOURCE_INVALID: '这份材料不能这样引用（只接受 owner 列出的材料）',
    SHARED_PRACTICE_SOURCE_VERSION_INVALID: '材料的版本不可用',
    SHARED_PRACTICE_HEAD_REQUIRED: '缺这一次读到的版本；先刷新再提交',
    SHARED_PRACTICE_ID_INVALID: '这次共同实践的标识不可用',
    SHARED_PRACTICE_ID_REQUIRED: '先选一次共同实践',
    SHARED_PRACTICE_OPERATION_KEY_REQUIRED: '缺操作键',
    SHARED_PRACTICE_OPEN_REFUSED: '这次没能开成共同实践',
    SHARED_PRACTICE_SUBMIT_REFUSED: '这句话没有被收下',
    SHARED_PRACTICE_MODIFY_REFUSED: '这次修改没有被收下',
    SHARED_PRACTICE_PAUSE_REFUSED: '这次暂停没有被接受',
    SHARED_PRACTICE_RESUME_REFUSED: '这次重新取用没有被接受',
    SHARED_PRACTICE_END_REFUSED: '这次结束没有被接受',
    SHARED_PRACTICE_REASON_INVALID: '结束理由不可用（最多 64 字）',
    SHARED_PRACTICE_UNAVAILABLE: '共同实践面现在不可用',
    /* ---- 世界 / 身体（owner 原因码 → 人话；没列出的码原样显示） ---- */
    NOT_REACHABLE: '这一步走不过去',
    NOT_TAKEABLE: '这件东西拿不起来',
    OCCUPIED: '手里已经占着了',
    UNKNOWN_TARGET: '服务不认识这个目的地',
    STALE_STATE: '世界刚刚变过，需要重来一次',
    POSTCONDITION_FAILED: '服务没有达成这次动作',
    TRAVELLING: '正在路上，先等到达',
    REST_WHILE_TRAVELLING: '在路上不能休息',
    REST_REQUIRES_RESTING_POSE: '先坐下或躺下，再休息',
    SLEEP_LIKE_REQUIRES_LYING_POSE: '想睡着，得先躺下',
    SLEEP_LIKE_REQUIRES_REST_SURFACE: '这里没有能睡的床',
    RECOVERY_NOT_NEEDED: '现在不需要恢复',
    RECOVERY_ALREADY_ACTIVE: '已经在休息了',
    NO_OPEN_RECOVERY: '现在没有开着的休息',
    HAND_OCCUPIED: '两只手都占着',
    NO_TAKEABLE_OBJECT: '这里没有能拿的东西',
    nothing_held: '手里没有东西',
    NOT_EXPOSED: '这个动作没有对本地 owner 开放',
    INVALID_PARAMS: '参数不对，请求没有执行',
    UNKNOWN_WORLD_ACTION: '服务不认识这个动作',
    OPERATION_KEY_CONFLICT: '这个操作键属于别的操作',
    OPERATION_TARGET_CONFLICT: '同一个操作键指向了不同的目标',
    OPERATION_NOT_FOUND: '服务没有这个操作键的记录',
    INVALID_OPERATION_KEY: '操作键不合法',
    WORLD_NOT_INITIALIZED: '这个世界还没有初始化',
    WORLD_STATE_UNREADABLE: '世界状态读不出来',
    WORLD_STATE_INVALID: '世界状态不完整',
    WORLD_LOCATION_INVALID: '当前位置读不出来',
    WORLD_HAS_NO_INDOOR_DECLARATION: '这个世界包没有声明室内地图',
    WORLD_PACKAGE_UNREADABLE: '世界包读不出来',
    WORLD_DATA_UNAVAILABLE: '世界数据暂时不可用',
    WORLD_BODY_SERVICE_UNAVAILABLE: '身体服务暂时读不到',
    SUBSTRATE_NOT_INITIALIZED: '物件与摆放还没有初始化',
    SUBSTRATE_STATE_ERROR: '物件摆放的存档有问题',
    FEATURE_DISABLED: '这个功能在当前实例里关着',
    WORLD_UNAVAILABLE: '世界暂时不可用',
    NOT_ALLOWED: '这一步不被允许',
    NOT_AT_LOCATION: '当前位置不符，不能进行这一步',
    /* ---- rc1 实体日用店 / 布置 / 房间灯（owner 原因码 → 人话；没列出的码原样显示） ---- */
    STORE_LOCATION_REQUIRED: '你现在不在店里，买不了实体店的东西',
    STORE_CLOSED: '店现在没开门',
    ITEM_NOT_SOLD_AT_STORE: '这件东西不在实体店卖',
    STORE_UNAVAILABLE: '店里的货架读不到',
    NOT_OWNED: '这件东西不属于这个实例',
    NOT_AN_ECONOMY_INSTANCE: '服务里没有这件东西的产权记录',
    DESTINATION_NOT_ALLOWED: '这个世界不接受这种摆放目标',
    SURFACE_NOT_DECLARED: '这张桌面没有在世界里声明',
    NOT_A_FIXTURE: '这不是能开关的灯',
    FIXTURE_NOT_HERE: '这盏灯不在这里',
    NO_DECLARED_LIGHT_IN_ROOM: '这个房间没有声明过的灯',
    /* ---- rc1 阅读工作台：C 的 facade（components/life/scripts/phone_reading.py）真实原因码。
       没列出的码原样显示（服务返回原因码：X），界面不猜含义，也不替服务换一个说法。 ---- */
    READING_REVISION_CONFLICT: '材料版本已经变了，这次没有写入',
    READING_HEAD_CONFLICT: '事项版本已被更新，这次没有写入',
    READING_DOCUMENT_VERSION_MISMATCH: '私人事项文档版本已变，这次没有写入',
    READING_WRITE_REFUSED: '服务拒绝了这次写入',
    READING_GRANT_REQUIRED: '这个实例没有阅读写入的授权',
    READING_OPERATION_KEY_REUSED: '这个操作键已经用过别的动作，不会再执行一次',
    READING_OPERATION_UNKNOWN: '服务仍然不知道这个阅读操作的结果',
    READING_OPERATION_NOT_FOUND: '服务没有这个阅读操作键的记录',
    READING_MATERIAL_UNAVAILABLE: '阅读材料当前不可用',
    PAGE_NOT_ACQUIRED: '这一版这一页还没取得：先保存阅读停点，再写笔记',
    PAGE_NOT_PRESENT: '这份材料没有这一页',
    PAGE_INVALID: '页码不合法',
    CONTENT_INVALID: '正文不是可用的文本',
    CONTENT_EMPTY: '正文不能为空',
    CONTENT_TOO_LARGE: '正文超过 24000 字节上限',
    BLANK_PAGE: '有空白页：分页符之间必须有正文',
    TOO_MANY_PAGES: '分页超过 64 页上限',
    PAGE_TOO_LARGE: '有一页超过单页 12000 字节上限',
    ANNOTATION_KIND_INVALID: '批注类型只能是笔记或疑问',
    ANNOTATION_FIELD_NOT_ALLOWED: '这类批注不接受这个字段',
    OPERATION_KEY_INVALID: '操作键不合法',
    SUPPLY_UNAVAILABLE: '材料库暂时读不到',
    SUPPLY_REFUSED: '材料库拒绝了这次请求',
    PRIVATE_ITEMS_UNAVAILABLE: '私人事项暂时读不到',
    /* ---- 来访与共同时间（A 的 LocalContactInbox 经 P 投递；原因码原样保留） ---- */
    CONTACT_SUBJECT_UNBOUND: '这台实例没有绑定主体，来信箱不可用',
    CONTACT_HOME_UNBOUND: '这台实例没有绑定 home，来信箱不可用',
    CONTACT_STATE_ROOT_UNBOUND: '这台实例没有 Life state root，来信箱不可用',
    CONTACT_UNAVAILABLE: '来信箱这次不可用',
    CONTACT_STATE_UNREADABLE: '来信箱账本读不出来：结果未知，不是“没有”',
    CONTACT_STATE_CORRUPT: '来信箱账本坏了（不是 JSON / 缺字段）',
    CONTACT_STATE_EMPTY: '来信箱账本只写了一半',
    CONTACT_STATE_SCHEMA_MISMATCH: '来信箱账本版本不对',
    CONTACT_STATE_SUBJECT_MISMATCH: '这份来信箱属于另一个主体',
    CONTACT_INBOX_CAPACITY_REACHED: '来信箱满了（200 条上限），这条没有记下',
    CONTACT_OPERATION_KEY_CONFLICT: '这个操作键已经属于另一条来话',
    CONTACT_OPERATION_KEY_REQUIRED: '缺少操作键',
    CONTACT_OPERATION_KEY_INVALID: '操作键不合法',
    CONTACT_OPERATION_ABSENT: '这个键还没有任何记录',
    CONTACT_EVENT_KIND_UNSUPPORTED: '不是这台手机能投的三类之一',
    CONTACT_TEXT_INVALID: '正文不可用（空、过长或有控制字符）',
    CONTACT_SCOPE_REQUIRED: '明确邀请必须写清范围',
    CONTACT_SCOPE_INVALID: '邀请范围不可用',
    CONTACT_FIELD_NOT_ALLOWED_FOR_KIND: '这类来话不接受这个字段',
    CONTACT_INVITATION_ID_REQUIRED: '结束要写明结束哪一次邀请',
    CONTACT_INVITATION_ID_INVALID: '邀请标识不可用',
    CONTACT_END_NEEDS_EXPLICIT_END: '结束必须是显式结束',
    CONTACT_REASON_INVALID: '理由不可用',
    CONTACT_EXPIRES_AFTER_INVALID: '有效期不可用（1–86400 秒）',
    CONTACT_DURATION_INVALID: '时长不可用（0–86400 秒）',
    CONTACT_EVENT_REF_INVALID: '这条来话的标识不可用',
    CONTACT_EVENT_NOT_FOUND: '来信箱里没有这一条'
  };

  const OUTCOME_TITLE = {
    pending: '提交中',
    applied: '已生效',
    already_applied: '早已生效',
    rejected: '已拒绝',
    conflict: '版本冲突',
    stale_source: '来源换版，这次没有写',
    unknown: '结果未知',
    blocked: '未发出请求',
    offline: '未连接'
  };

  const RESULT_FIELD_LABELS = {
    transaction_id: '交易号',
    item_instance_id: '物件实例',
    current_balance: '当前余额',
    balance: '余额',
    revision: '版本',
    head: '服务 head',
    updated_at: '更新时间',
    delivery_status: '配送',
    ownership: '归属',
    location: '位置',
    currency: '币种'
  };

  /* ---- 世界/身体的词表：只翻译 owner 给的字面量，绝不补状态 ---- */
  const ACTION_TEXT = {
    MOVE: '出发',
    PICK_UP: '拿起',
    PLACE: '放下',
    POSE: '换姿势',
    REST: '休息',
    /* rc1 房间：布置（ARRANGE 与 RELOCATE 是同一个语义，C 的契约定名后只留实际报上来的那个） */
    ARRANGE: '布置',
    RELOCATE: '布置',
    TOGGLE_FIXTURE: '开关灯'
  };

  const POSE_TEXT = {standing: '站着', seated: '坐着', lying: '躺着', transitioning: '正在动'};
  const ORIENTATION_TEXT = {north: '朝北', south: '朝南', east: '朝东', west: '朝西', up: '朝上', down: '朝下'};
  const RECOVERY_STATE_TEXT = {active: '正在恢复', completed: '恢复结束', interrupted: '被打断了', inactive: '没有在恢复'};
  const RECOVERY_MODE_TEXT = {rest: '休息', sleep_like: '睡着了'};
  const RECOVERY_TREND_TEXT = {improving: '在好转', worsening: '被打断了', stable: '平稳', flat: '平稳'};
  const RECOVERY_BAND_TEXT = {mild: '轻微', noticeable: '明显', strong: '强'};
  const TRAVEL_BAND_TEXT = {just_started: '刚出门', underway: '在路上', almost_there: '快到了'};
  const TRAVEL_STATUS_TEXT = {
    none: '现在没有在途',
    in_progress: '在路上',
    arrived: '刚落脚',
    disabled: '计时旅程关着（移动会瞬时到达）',
    unavailable: '在途状态读不到'
  };
  const BODY_LOAD_TEXT = {pending: '负荷待结算', settled: '负荷已结算', unknown: '负荷未知'};
  const AVAILABILITY_TEXT = {OPEN: '营业中', CLOSED: '现在关着', UNKNOWN: '营业状态未知'};
  const REACH_TEXT = {reachable: '可达', unreachable: '不可达', unknown: '可达性未知'};
  const SIGNAL_TEXT = {
    recovery_underway: '正在恢复',
    recovery_interrupted: '恢复被打断',
    recovery_completed: '恢复结束',
    load_rising: '负荷在升高',
    load_high: '负荷偏高',
    load_settled: '负荷已经落定',
    strain: '有点撑',
    tension_high: '绷着',
    comfort_low: '不太舒服',
    comfort_high: '舒服',
    sensory_high: '感官被填满',
    sensory_low: '有点钝',
    warmth_low: '发凉',
    warmth_high: '有点热'
  };
  const BAND_TEXT = {mild: '轻', noticeable: '明显', strong: '强'};
  const EXECUTION_STATE_TEXT = {committed: '已提交', rejected: '被拒绝', prepared: '未确认', uncertain: '未确认'};

  /* ------------------------------------------------------------------ *
   * 纯函数层（可被 node 直接检查，不依赖 DOM）
   * ------------------------------------------------------------------ */

  function sanitizeLine(value, maxLen) {
    if (value === null || value === undefined) return '';
    if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') return '';
    let text = String(value).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '');
    text = text.replace(/\s+/g, ' ').trim();
    text = maskSensitive(text);
    const limit = typeof maxLen === 'number' && maxLen > 0 ? maxLen : 240;
    if (text.length > limit) text = text.slice(0, limit - 1) + '…';
    return text;
  }

  function sanitizeText(value, maxLen) {
    if (value === null || value === undefined) return '';
    if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') return '';
    let text = String(value).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '');
    text = text.replace(/\r\n?/g, '\n');
    text = maskSensitive(text);
    const limit = typeof maxLen === 'number' && maxLen > 0 ? maxLen : 4000;
    if (text.length > limit) text = text.slice(0, limit - 1) + '…';
    return text;
  }

  function maskSensitive(text) {
    let out = text.replace(/[A-Za-z]:\\{1,2}Users\\{1,2}[^\\\s]+(?:\\{1,2}[^\s]*)?/g, '[已隐藏的本机路径]');
    out = out.replace(/\/(?:home|Users)\/[^\s/]+(?:\/[^\s]*)?/g, '[已隐藏的本机路径]');
    out = out.replace(/\b(?:sk|pk|ghp|gho|glpat|xoxb|rk)-[A-Za-z0-9_-]{8,}/g, '[已隐藏的凭据]');
    out = out.replace(/\bBearer\s+[A-Za-z0-9._-]{8,}/gi, '[已隐藏的凭据]');
    out = out.replace(/\b[A-Fa-f0-9]{40,}\b/g, '[已隐藏的长标识]');
    return out;
  }
  /* 协议标识（item_instance_id / catalog_item_id / note_id / expected_head /
     operation_key）是服务里的真实键：回传时必须是原字节，绝不 trim、截断或脱敏。
     屏幕上的说明另走 safeLabel()，两者不共用同一个值。 */
  const PROTOCOL_ID_MAX = 200;
  const PROTOCOL_HEAD_MAX = 256;
  const PROTOCOL_KEY_MAX = 128;
  const CONTROL_CHARS = /[\u0000-\u001F\u007F]/;

  /* 校验类型、长度与控制字符；通过就把服务原字符串原样交出，失败绝不做替换。 */
  function protocolId(value, maxLen) {
    const limit = typeof maxLen === 'number' && maxLen > 0 ? maxLen : PROTOCOL_ID_MAX;
    if (typeof value !== 'string') {
      return {ok: false, value: '', reason: 'NOT_TEXT', message: '服务没有给出字符串标识'};
    }
    if (!value.trim()) return {ok: false, value: '', reason: 'EMPTY', message: '标识是空的'};
    if (value.length > limit) {
      return {ok: false, value: '', reason: 'TOO_LONG', message: '标识超过 ' + limit + ' 个字符'};
    }
    if (CONTROL_CHARS.test(value)) {
      return {ok: false, value: '', reason: 'CONTROL_CHARS', message: '标识里含控制字符'};
    }
    return {ok: true, value: value, reason: '', message: ''};
  }

  /* 只给屏幕用的安全标签：可以遮罩与截断，永远不进请求体。 */
  function safeLabel(value, maxLen) {
    const limit = typeof maxLen === 'number' && maxLen > 0 ? maxLen : 80;
    return sanitizeLine(value, limit);
  }

  /* 界面自己拼的接口路径：只接受以 / 开头的服务路径。 */
  function apiPath(value) {
    const check = protocolId(value, 120);
    if (!check.ok || check.value.charAt(0) !== '/') return '';
    return check.value;
  }

  function reasonText(raw) {
    if (!raw || typeof raw !== 'string') return '';
    const value = raw.trim();
    if (!value) return '';
    if (/^[A-Z0-9_]{3,48}$/.test(value)) return describeReason(value);
    return sanitizeLine(value, 200);
  }

  function describeReason(code) {
    if (!code) return '';
    const key = String(code).trim().toUpperCase();
    if (!key) return '';
    if (REASON_TEXT[key]) return REASON_TEXT[key];
    return '服务返回原因码：' + sanitizeLine(key, 60);
  }

  function humanizeCode(code) {
    if (code === null || code === undefined) return '';
    const key = String(code).trim();
    if (!key) return '';
    const map = {
      available: '可用',
      unavailable: '不可用',
      disabled: '已禁用',
      ok: '正常',
      applied: '已生效',
      already_applied: '早已生效',
      rejected: '已拒绝',
      unknown: '未知',
      conflict: '冲突',
      owned: '已拥有',
      delivered: '已送达',
      in_transit: '运送中',
      pending: '待确认',
      not_delivered: '未送达',
      read: '已读',
      unread: '未读'
    };
    return map[key.toLowerCase()] || key;
  }

  function formatMoney(currency, amount) {
    if (typeof currency !== 'string') return null;
    const cur = sanitizeLine(currency, 12);
    if (!cur) return null;
    let num = NaN;
    if (typeof amount === 'number') num = amount;
    else if (typeof amount === 'string' && amount.trim() !== '') num = Number(amount);
    if (!isFinite(num)) return null;
    const fixed = Math.abs(num) >= 100000000 ? String(num) : num.toFixed(2);
    return fixed + ' ' + cur;
  }

  function formatWhen(value) {
    if (value === null || value === undefined || value === '') return '';
    if (typeof value === 'number' && isFinite(value) && value <= 0) return '';
    if (typeof value === 'number' && isFinite(value)) {
      const fromNumber = new Date(value < 1e12 ? value * 1000 : value);
      if (!isNaN(fromNumber.getTime())) return formatDateParts(fromNumber);
    }
    if (typeof value === 'string') {
      const parsed = new Date(value);
      if (!isNaN(parsed.getTime())) return formatDateParts(parsed);
      return sanitizeLine(value, 32);
    }
    return '';
  }

  function formatDateParts(date) {
    return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate()) +
      ' ' + pad2(date.getHours()) + ':' + pad2(date.getMinutes());
  }

  function pad2(value) {
    return String(value).padStart(2, '0');
  }

  function clockText(date) {
    const now = isDateLike(date) ? date : new Date();
    if (isNaN(now.getTime())) return '--:--';
    return pad2(now.getHours()) + ':' + pad2(now.getMinutes());
  }

  function dateText(date) {
    const now = isDateLike(date) ? date : new Date();
    if (isNaN(now.getTime())) return '';
    const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
    return (now.getMonth() + 1) + '月' + now.getDate() + '日 ' + weekdays[now.getDay()];
  }

  function isDateLike(value) {
    return !!(value && typeof value === 'object' && typeof value.getTime === 'function' && typeof value.getHours === 'function');
  }

  function csrfFromState(doc) {
    if (!doc || typeof doc !== 'object') return null;
    const candidates = [
      doc.csrf_token,
      doc.csrfToken,
      doc.data && doc.data.csrf_token,
      doc.instance && doc.instance.csrf_token
    ];
    for (let i = 0; i < candidates.length; i += 1) {
      const candidate = candidates[i];
      if (typeof candidate !== 'string') continue;
      const token = candidate.trim();
      if (!token || token.length > 256) continue;
      if (/[\s\u0000-\u001F\u007F]/.test(token)) continue;
      return token;
    }
    return null;
  }

  function stateView(doc) {
    if (!doc || typeof doc !== 'object' || doc.ok !== true) {
      return {phase: 'error', detail: '服务没有返回 ok:true 的状态', instance: null, apps: {}, csrf: null, readOnly: true};
    }
    const csrf = csrfFromState(doc);
    const readOnly = doc.read_only === true;
    const rawApps = doc.apps && typeof doc.apps === 'object' ? doc.apps : {};
    const apps = {};
    /* 世界是第八个 app：状态报告里出现就跟着取，没报告就如实标“未报告”。 */
    HOME_ORDER.forEach(function (key) {
      const entry = rawApps[key];
      if (entry && typeof entry === 'object') {
        apps[key] = {
          status: typeof entry.status === 'string' ? entry.status : 'unavailable',
          reasonCode: entry.reason_code ? String(entry.reason_code) : '',
          data: entry.data && typeof entry.data === 'object' ? entry.data : null
        };
      } else {
        apps[key] = {status: 'missing', reasonCode: 'APP_NOT_REPORTED', data: null};
      }
    });
    const rawInstance = doc.instance && typeof doc.instance === 'object' ? doc.instance : null;
    const instance = rawInstance ? {
      name: sanitizeLine(rawInstance.name, 60) || '未命名实例',
      subject: sanitizeLine(rawInstance.subject, 60),
      mode: sanitizeLine(rawInstance.mode, 40)
    } : null;
    return {
      phase: readOnly || !csrf ? 'readonly' : 'online',
      detail: !csrf ? '服务没有提供 csrf_token：写入被界面挡下，不会自行绕过。' : (readOnly ? '服务声明 read_only。' : '已连接，写入令牌已取得。'),
      instance: instance,
      apps: apps,
      csrf: csrf,
      readOnly: readOnly
    };
  }

  function canWrite(conn) {
    if (!conn) return {ok: false, reason: 'NO_STATE', message: '还没有读到服务状态，不能写入。'};
    if (conn.phase === 'loading') return {ok: false, reason: 'LOADING', message: '正在读取服务状态，请稍后再写。'};
    if (conn.phase === 'offline') return {ok: false, reason: 'OFFLINE', message: '未连接服务，不能写入。'};
    if (conn.phase === 'error') return {ok: false, reason: 'STATE_ERROR', message: '服务状态不可用，不能写入。'};
    if (!conn.csrf) return {ok: false, reason: 'CSRF_MISSING', message: '只读：服务没有提供 CSRF 令牌，界面不会绕过。'};
    if (conn.readOnly) return {ok: false, reason: 'READ_ONLY', message: '只读：服务声明 read_only，不能写入。'};
    return {ok: true, reason: '', message: ''};
  }

  /* 读取成功的 status：钱包 / 商店 / 物品端点用 applied 表示“这次读取成立了”。
     这是读取语义，POST 的 unknown/rejected 判定完全不受影响。 */
  const READ_SUCCESS_STATUSES = ['ok', 'available', 'applied', 'already_applied'];

  function isReadSuccessStatus(status) {
    const key = typeof status === 'string' ? status.trim().toLowerCase() : '';
    if (!key) return false;
    return READ_SUCCESS_STATUSES.indexOf(key) >= 0;
  }

  /* 只有既不是成功读取、也不是空的 status 才值得在屏上提醒。 */
  function readStatusWarning(status) {
    const key = typeof status === 'string' ? status.trim().toLowerCase() : '';
    if (!key) return '';
    if (isReadSuccessStatus(key)) return '';
    return humanizeCode(key);
  }

  function deriveReadOutcome(res) {
    if (!res) return {status: 'offline', message: '没有收到响应'};
    if (res.kind === 'network') return {status: 'offline', message: res.cause === 'timeout' ? '请求超时' : '请求没有到达服务'};
    if (res.kind === 'blocked') return {status: 'blocked', reason: res.reason, message: '请求被本地安全闸拦下'};
    if (res.kind === 'unparsable') return {status: 'error', message: '响应不是 JSON（HTTP ' + res.status + '），这里可能没有手机接口'};
    const doc = res.doc;
    if (!doc || typeof doc !== 'object') return {status: 'error', message: '服务没有返回 JSON 对象（HTTP ' + res.status + '）'};
    if (doc.ok === true) return {status: 'ok', doc: doc};
    /* 精确放宽这一处：没有 ok 字段、但服务写明 applied/already_applied 的读取信封算读取成功。 */
    if (doc.ok === undefined && isReadSuccessStatus(doc.status)) return {status: 'ok', doc: doc};
    return {status: 'rejected', doc: doc, reason: doc.reason_code || '', message: '服务返回 ok=false（HTTP ' + res.status + '）'};
  }

  function deriveMutationOutcome(res) {
    if (!res) return {status: 'unknown', cause: 'no-response', message: '没有收到响应'};
    if (res.kind === 'blocked') return {status: 'blocked', reason: res.reason, message: '请求没有发出'};
    if (res.kind === 'network') return {status: 'unknown', cause: res.cause || 'network', message: '请求没有确认送达'};
    if (res.kind === 'unparsable') return {status: 'unknown', cause: 'unparsable', message: '响应无法解析'};
    const doc = res.doc && typeof res.doc === 'object' ? res.doc : null;
    const raw = doc && typeof doc.status === 'string' ? doc.status.toLowerCase() : '';
    if (MUTATION_STATUSES.indexOf(raw) >= 0) {
      return {status: raw, doc: doc, reason: (doc && doc.reason_code) || ''};
    }
    if (raw === SHARE_STALE_STATUS) {
      /* 来源换版/被拒：服务确定没有写，别让它冒充 409 conflict。 */
      return {status: SHARE_STALE_STATUS, doc: doc, reason: (doc && doc.reason_code) || ''};
    }
    if (res.status === 409) return {status: 'conflict', doc: doc, reason: (doc && doc.reason_code) || 'CONFLICT'};
    if (res.status === 400 || res.status === 422) {
      return {status: 'rejected', doc: doc, reason: (doc && doc.reason_code) || ''};
    }
    if (doc && doc.ok === false && res.status < 500) {
      return {status: 'rejected', doc: doc, reason: doc.reason_code || ''};
    }
    if (res.status >= 500) return {status: 'unknown', cause: 'server-error', doc: doc};
    /* 2xx 但没有 status：绝不能当成成功。 */
    return {status: 'unknown', cause: 'missing-status', doc: doc};
  }

  function buildRequestInit(opts) {
    opts = opts || {};
    const method = String(opts.method || 'GET').toUpperCase();
    const headers = {'Accept': 'application/json'};
    const init = {method: method, headers: headers, credentials: 'same-origin', cache: 'no-store'};
    if (method === 'GET' || method === 'HEAD') return init;
    const csrf = typeof opts.csrf === 'string' ? opts.csrf.trim() : '';
    if (!csrf) {
      const error = new Error('CSRF_MISSING');
      error.code = 'CSRF_MISSING';
      throw error;
    }
    headers['Content-Type'] = 'application/json';
    headers['X-CSRF-Token'] = csrf;
    init.body = JSON.stringify(opts.body && typeof opts.body === 'object' ? opts.body : {});
    return init;
  }

  function noteListView(doc) {
    const source = doc && typeof doc === 'object' ? doc : null;
    const rawItems = source && Array.isArray(source.items) ? source.items : null;
    const items = (rawItems || []).filter(function (item) {
      return item && typeof item === 'object';
    }).map(function (item) {
      const rawId = typeof item.id === 'string' ? item.id : (typeof item.note_id === 'string' ? item.note_id : '');
      const idCheck = protocolId(rawId, PROTOCOL_ID_MAX);
      /* 可选阅读锚点：只有服务给 page 时才成立；source_id / source_revision 是协议值，
         屏幕另走 safeLabel，绝不拿展示文本去发请求。 */
      const rawAnchor = item.reading_anchor && typeof item.reading_anchor === 'object' ? item.reading_anchor : null;
      const anchorPage = rawAnchor ? readingPageValue(rawAnchor.page) : null;
      const anchorSource = rawAnchor ? protocolId(rawAnchor.source_id, PROTOCOL_ID_MAX) : {ok: false, value: ''};
      const anchorRevision = rawAnchor ? protocolId(rawAnchor.source_revision, PROTOCOL_HEAD_MAX) : {ok: false, value: ''};
      return {
        id: idCheck.ok ? idCheck.value : '',
        idOk: idCheck.ok,
        idLabel: safeLabel(idCheck.value, 80),
        idMessage: idCheck.ok ? '' : idCheck.message,
        title: sanitizeLine(item.title, 120),
        body: sanitizeText(item.body, 4000),
        revision: item.revision === null || item.revision === undefined ? null : item.revision,
        updatedAt: item.updated_at || item.updatedAt || '',
        anchor: anchorPage === null ? null : {
          page: anchorPage,
          sourceId: anchorSource.ok ? anchorSource.value : '',
          sourceRevision: anchorRevision.ok ? anchorRevision.value : '',
          revisionLabel: safeLabel(anchorRevision.ok ? anchorRevision.value : rawAnchor.source_revision, 40)
        }
      };
    });
    return {
      items: items,
      itemsMissing: rawItems === null,
      head: source && typeof source.head === 'string' && source.head ? source.head : null
    };
  }

  /* ------------------------------------------------------------------ *
   * rc1 阅读工作台（U 写集：只消费，不造事实）
   *
   * 消费面（字段一律在 data 里，顶层同名键不算契约）：
   *   · GET  /reading/material?page=N → available / artifact_id / source_id / source_revision /
   *     version_number / title / page / pages / text / content_available / private_head /
   *     bookmark{page,source_revision,needs_reread} / understanding / annotations[]
   *   · POST /reading/material    ← {content,expected_revision,operation_key}
   *   · POST /reading/positions   ← {page,source_revision,expected_head,operation_key}
   *   · POST /reading/annotations ← {kind,page,source_revision,expected_head,operation_key,title?,body?,note?}
   *   · GET  /reading/operations/<key> → 原键对账（不是购物 /operations/<key>）
   *
   * 铁律：
   *   1. 新字段只从 data 读；doc 顶层出现同名字段也不当契约（不猜 flat）。
   *   2. GET 翻页是只读；只有点「保存阅读停点」才写 positions。
   *   3. 停点/笔记都用 data 里真实的 source_revision + private_head 做 CAS；
   *      刷新没回来之前不再写（绝不复用旧 head）。
   *   4. 旧版本停点/锚点标 needs_reread 并说明，绝不悄悄落到当前版本同页。
   *   5. 正文与批注只进内存与 textContent；不进 sessionStorage，不拼 HTML。
   * ------------------------------------------------------------------ */

  const READING_MATERIAL_PATH = '/reading/material';
  const READING_POSITIONS_PATH = '/reading/positions';
  const READING_ANNOTATIONS_PATH = '/reading/annotations';
  const READING_PAGE_SEPARATOR = '%%PAGE%%';
  const READING_CONTENT_MAX_BYTES = 24000;
  const READING_PAGE_MAX_BYTES = 12000;
  const READING_PAGES_MAX = 64;
  const READING_TEXT_MAX = 12000;
  const ANNOTATION_TITLE_MAX = 80;
  const ANNOTATION_BODY_MAX = 4000;
  const ANNOTATION_NOTE_MAX = 2000;
  const READING_ANNOTATION_KINDS = ['note', 'unclear'];
  const READING_SCOPES = ['reading-import', 'reading-position', 'reading-note', 'reading-unclear'];
  const READING_SCOPE_TEXT = {
    'reading-import': '导入阅读正文',
    'reading-position': '保存阅读停点',
    'reading-note': '写阅读笔记',
    'reading-unclear': '记阅读疑问'
  };

  /* UTF-8 字节数自己算：不依赖 TextEncoder，也能在 node 检查里跑。 */
  function utf8ByteLength(value) {
    if (typeof value !== 'string' || !value) return 0;
    let bytes = 0;
    for (let i = 0; i < value.length; i += 1) {
      const code = value.charCodeAt(i);
      if (code < 0x80) {
        bytes += 1;
      } else if (code < 0x800) {
        bytes += 2;
      } else if (code >= 0xd800 && code <= 0xdbff && i + 1 < value.length) {
        const next = value.charCodeAt(i + 1);
        if (next >= 0xdc00 && next <= 0xdfff) {
          bytes += 4;
          i += 1;
        } else {
          bytes += 3;
        }
      } else {
        bytes += 3;
      }
    }
    return bytes;
  }

  /* 页码只认严格整数：布尔、字符串、小数一律 null，界面的页码不接受“差不多”。 */
  function readingPageValue(value) {
    if (typeof value !== 'number') return null;
    if (!isFinite(value) || Math.floor(value) !== value) return null;
    if (value < 1) return null;
    return value;
  }

  /* %%PAGE%% 明确分页；没有分隔符就是一页。 */
  function readingContentStats(content) {
    const raw = typeof content === 'string' ? content : '';
    const text = raw.replace(/\r\n?/g, '\n');
    const pages = text.split(READING_PAGE_SEPARATOR);
    return {
      text: text,
      bytes: utf8ByteLength(text),
      pageCount: pages.length,
      pageBytes: pages.map(utf8ByteLength),
      blankPages: pages.map(function (page) { return !page.trim(); })
    };
  }

  function validateReadingContent(content) {
    const stats = readingContentStats(content);
    const errors = [];
    if (!stats.text.trim()) {
      errors.push('正文不能为空');
    } else {
      if (stats.bytes > READING_CONTENT_MAX_BYTES) {
        errors.push('正文 ' + stats.bytes + ' 字节，超过 ' + READING_CONTENT_MAX_BYTES + ' 字节上限');
      }
      if (stats.pageCount > READING_PAGES_MAX) {
        errors.push('分了 ' + stats.pageCount + ' 页，超过 ' + READING_PAGES_MAX + ' 页上限');
      }
      const blank = stats.blankPages.indexOf(true);
      if (blank >= 0) {
        errors.push('第 ' + (blank + 1) + ' 页是空白：' + READING_PAGE_SEPARATOR + ' 之间必须有正文');
      }
      const over = stats.pageBytes.findIndex(function (bytes) { return bytes > READING_PAGE_MAX_BYTES; });
      if (over >= 0) {
        errors.push('第 ' + (over + 1) + ' 页 ' + stats.pageBytes[over] + ' 字节，超过单页 ' + READING_PAGE_MAX_BYTES + ' 字节上限');
      }
    }
    return {ok: errors.length === 0, errors: errors, stats: stats};
  }

  /* 三个写接口共用的协议值：缺一个就不发请求，绝不拿展示标签顶上。
     needHead=false 只给导入用——POST /reading/material 的字段里本来就没有 expected_head。 */
  function readingTargetIds(source, needHead) {
    const revision = protocolId(source.source_revision, PROTOCOL_HEAD_MAX);
    if (!revision.ok) {
      return {ok: false, field: 'source_revision', reason: revision.reason,
        message: '没有可用的材料版本（' + revision.message + '），这次没有发出请求。'};
    }
    let headValue = '';
    if (needHead !== false) {
      const head = protocolId(source.expected_head, PROTOCOL_HEAD_MAX);
      if (!head.ok) {
        return {ok: false, field: 'expected_head', reason: head.reason,
          message: '没有可用的事项 head（' + head.message + '），这次没有发出请求。'};
      }
      headValue = head.value;
    }
    const key = protocolId(source.operation_key, PROTOCOL_KEY_MAX);
    if (!key.ok) {
      return {ok: false, field: 'operation_key', reason: key.reason,
        message: '操作键不可用（' + key.message + '），没有发出请求。'};
    }
    return {ok: true, revision: revision.value, head: headValue, key: key.value};
  }

  /* POST /reading/material：白名单只有三项。title / source receipt / 理解都不许客户端声明。 */
  function buildImportPayload(input) {
    const source = input && typeof input === 'object' ? input : {};
    const content = validateReadingContent(source.content);
    if (!content.ok) {
      return {ok: false, field: 'content', reason: 'INVALID_CONTENT',
        message: content.errors.join('；'), errors: content.errors};
    }
    /* 导入用的是 expected_revision（不是 source_revision）：字段名照契约，不做别名猜测。 */
    const ids = readingTargetIds({
      source_revision: source.expected_revision,
      operation_key: source.operation_key
    }, false);
    if (!ids.ok) {
      /* 报错字段名按契约叫 expected_revision，不把界面内部的 source_revision 抛给用户。 */
      return ids.field === 'source_revision'
        ? {ok: false, field: 'expected_revision', reason: ids.reason, message: ids.message}
        : ids;
    }
    return {ok: true, pages: content.stats.pageCount, bytes: content.stats.bytes,
      payload: {content: content.stats.text, expected_revision: ids.revision, operation_key: ids.key}};
  }

  /* POST /reading/positions：page 必须是严格整数（布尔/小数/字符串都不发）。 */
  function buildPositionPayload(input) {
    const source = input && typeof input === 'object' ? input : {};
    const page = readingPageValue(source.page);
    if (page === null) {
      return {ok: false, field: 'page', reason: 'INVALID_PAGE',
        message: '页码必须是 1 以上的整数，这次没有发出请求。'};
    }
    const ids = readingTargetIds(source);
    if (!ids.ok) return ids;
    return {ok: true, payload: {page: page, source_revision: ids.revision,
      expected_head: ids.head, operation_key: ids.key}};
  }

  function validateAnnotationDraft(input) {
    const source = input && typeof input === 'object' ? input : {};
    const kind = typeof source.kind === 'string' ? source.kind : '';
    if (READING_ANNOTATION_KINDS.indexOf(kind) < 0) {
      return {ok: false, errors: ['先选是按「笔记」记，还是按「疑问」记'], value: {kind: ''}};
    }
    const errors = [];
    const title = typeof source.title === 'string' ? source.title.replace(/\s+/g, ' ').trim() : '';
    const body = typeof source.body === 'string' ? source.body.replace(/\r\n?/g, '\n') : '';
    const note = typeof source.note === 'string' ? source.note.replace(/\r\n?/g, '\n') : '';
    if (kind === 'note') {
      if (!title) errors.push('笔记标题不能为空');
      if (title.length > ANNOTATION_TITLE_MAX) errors.push('标题最多 ' + ANNOTATION_TITLE_MAX + ' 字');
      if (!body.trim()) errors.push('笔记正文不能为空');
      if (body.length > ANNOTATION_BODY_MAX) errors.push('正文最多 ' + ANNOTATION_BODY_MAX + ' 字');
    } else {
      if (!note.trim()) errors.push('疑问要写一句到底哪里没懂');
      if (note.length > ANNOTATION_NOTE_MAX) errors.push('疑问最多 ' + ANNOTATION_NOTE_MAX + ' 字');
    }
    return {ok: errors.length === 0, errors: errors, value: {kind: kind, title: title, body: body, note: note}};
  }

  /* POST /reading/annotations：字段按 kind 白名单，别的一律不进 payload。 */
  function buildAnnotationPayload(input) {
    const source = input && typeof input === 'object' ? input : {};
    const draft = validateAnnotationDraft(source);
    if (!draft.ok) {
      return {ok: false, field: 'draft', reason: 'INVALID_DRAFT',
        message: draft.errors.join('；'), errors: draft.errors};
    }
    const page = readingPageValue(source.page);
    if (page === null) {
      return {ok: false, field: 'page', reason: 'INVALID_PAGE',
        message: '页码必须是 1 以上的整数，这次没有发出请求。'};
    }
    const ids = readingTargetIds(source);
    if (!ids.ok) return ids;
    const payload = {kind: draft.value.kind, page: page, source_revision: ids.revision,
      expected_head: ids.head, operation_key: ids.key};
    if (draft.value.kind === 'note') {
      payload.title = draft.value.title;
      payload.body = draft.value.body;
    } else {
      payload.note = draft.value.note;
    }
    return {ok: true, payload: payload};
  }

  function readingScopeFor(kind, sourceRevision, page) {
    if (READING_SCOPES.indexOf(kind) < 0) return '';
    const revision = protocolId(sourceRevision, PROTOCOL_HEAD_MAX);
    const pageValue = readingPageValue(page);
    return kind + ':' + (revision.ok ? revision.value : 'no-revision') + ':' + (pageValue === null ? '0' : String(pageValue));
  }

  function readingScopePage(scope) {
    const parts = String(scope || '').split(':');
    if (parts.length < 3) return null;
    return readingPageValue(Number(parts[parts.length - 1]));
  }

  function readingOperationPath(key) {
    const check = protocolId(key, PROTOCOL_KEY_MAX);
    if (!check.ok) return '';
    return '/reading/operations/' + encodeURIComponent(check.value);
  }

  function readingMaterialPath(page) {
    const value = readingPageValue(page);
    return value === null ? READING_MATERIAL_PATH : READING_MATERIAL_PATH + '?page=' + value;
  }

  function readingBookmarkView(raw) {
    const bookmark = asRecord(raw);
    if (!bookmark) return null;
    const page = readingPageValue(bookmark.page);
    const revision = protocolId(bookmark.source_revision, PROTOCOL_HEAD_MAX);
    return {
      page: page,
      sourceRevision: revision.ok ? revision.value : '',
      sourceRevisionLabel: safeLabel(revision.ok ? revision.value : bookmark.source_revision, 40),
      needsReread: bookmark.needs_reread === true
    };
  }

  function readingAnnotationView(raw, currentRevision) {
    const entry = asRecord(raw);
    if (!entry) return null;
    const idCheck = protocolId(entry.id, PROTOCOL_ID_MAX);
    const kindRaw = asString(entry.kind);
    const kind = READING_ANNOTATION_KINDS.indexOf(kindRaw) >= 0 ? kindRaw : '';
    const page = readingPageValue(entry.page);
    const revision = protocolId(entry.source_revision, PROTOCOL_HEAD_MAX);
    const revisionValue = revision.ok ? revision.value : '';
    const sameRevision = !!(revisionValue && currentRevision && revisionValue === currentRevision);
    const stale = entry.needs_reread === true || entry.stale === true ||
      (!!revisionValue && !!currentRevision && !sameRevision);
    return {
      id: idCheck.ok ? idCheck.value : '',
      idOk: idCheck.ok,
      idLabel: safeLabel(idCheck.ok ? idCheck.value : entry.id, 40),
      kind: kind,
      kindLabel: kind === 'unclear' ? '疑问' : (kind === 'note' ? '笔记' : (kindRaw ? safeLabel(kindRaw, 20) : '批注')),
      title: sanitizeLine(entry.title, ANNOTATION_TITLE_MAX),
      body: sanitizeText(entry.body, ANNOTATION_BODY_MAX),
      note: sanitizeText(entry.note, ANNOTATION_NOTE_MAX),
      sourceIdLabel: safeLabel(entry.source_id, 60),
      page: page,
      sourceRevision: revisionValue,
      sourceRevisionLabel: safeLabel(revisionValue, 40),
      sameRevision: sameRevision,
      stale: stale,
      createdAt: entry.created_at || '',
      /* 旧版本的批注可以看，但不能借它悄悄跳到当前页。 */
      canNavigate: page !== null && sameRevision
    };
  }

  function readingMaterialView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    const status = envelope ? asString(envelope.status) : '';
    const envelopeReason = envelope ? asString(envelope.reason_code) : '';
    if (!data) {
      return {
        phase: 'malformed', status: status, reasonCode: envelopeReason, statusLabel: readStatusWarning(status),
        available: false, annotations: [], annotationsMissing: true, bookmark: null, bookmarkStale: false,
        needsReread: false
      };
    }
    const page = readingPageValue(data.page);
    const pages = readingPageValue(data.pages);
    const versionNumber = typeof data.version_number === 'number' && isFinite(data.version_number)
      ? data.version_number : null;
    const revision = protocolId(data.source_revision, PROTOCOL_HEAD_MAX);
    const head = protocolId(data.private_head, PROTOCOL_HEAD_MAX);
    const artifactId = protocolId(data.artifact_id, PROTOCOL_ID_MAX);
    const sourceId = protocolId(data.source_id, PROTOCOL_ID_MAX);
    const currentRevision = revision.ok ? revision.value : '';
    const bookmark = readingBookmarkView(data.bookmark);
    const bookmarkStale = !!(bookmark && bookmark.sourceRevision && currentRevision &&
      bookmark.sourceRevision !== currentRevision);
    const rawAnnotations = Array.isArray(data.annotations) ? data.annotations : null;
    const annotations = (rawAnnotations || []).map(function (entry) {
      return readingAnnotationView(entry, currentRevision);
    }).filter(function (entry) { return !!entry; });
    return {
      phase: 'ready',
      status: status,
      reasonCode: envelopeReason || asString(data.reason_code),
      statusLabel: readStatusWarning(status),
      available: data.available === true,
      artifactId: artifactId.ok ? artifactId.value : '',
      artifactIdLabel: safeLabel(artifactId.ok ? artifactId.value : data.artifact_id, 40),
      sourceId: sourceId.ok ? sourceId.value : '',
      sourceIdLabel: safeLabel(sourceId.ok ? sourceId.value : data.source_id, 60),
      sourceRevision: currentRevision,
      sourceRevisionLabel: safeLabel(currentRevision, 40),
      versionNumber: versionNumber,
      title: sanitizeLine(data.title, 120),
      page: page,
      pages: pages,
      pageKnown: page !== null && pages !== null && page <= pages,
      text: sanitizeText(data.text, READING_TEXT_MAX),
      contentAvailable: data.content_available === true,
      privateHead: head.ok ? head.value : '',
      privateHeadLabel: safeLabel(head.ok ? head.value : data.private_head, 40),
      bookmark: bookmark,
      bookmarkStale: bookmarkStale,
      needsReread: !!(bookmark && (bookmark.needsReread || bookmarkStale)),
      understanding: asString(data.understanding),
      annotations: annotations,
      annotationsMissing: rawAnnotations === null
    };
  }

  function shopView(doc) {
    const source = doc && typeof doc === 'object' ? doc : null;
    const data = source && source.data && typeof source.data === 'object' ? source.data : null;
    const rawCatalog = data && Array.isArray(data.catalog) ? data.catalog : null;
    const currency = data && typeof data.currency === 'string' ? sanitizeLine(data.currency, 12) : '';
    const items = (rawCatalog || []).filter(function (entry) {
      return entry && typeof entry === 'object';
    }).map(function (entry) {
      const price = typeof entry.price === 'number' && isFinite(entry.price) ? entry.price : null;
      const idCheck = protocolId(entry.catalog_item_id, PROTOCOL_ID_MAX);
      return {
        id: idCheck.ok ? idCheck.value : '',
        idOk: idCheck.ok,
        idLabel: safeLabel(idCheck.value, 80),
        idMessage: idCheck.ok ? '' : idCheck.message,
        name: sanitizeLine(entry.name, 80) || '未命名物件',
        description: sanitizeText(entry.description, 400),
        price: price,
        usableKind: sanitizeLine(entry.usable_kind, 40)
      };
    });
    const purchaseAvailable = !!(data && data.purchase_available === true);
    let blockedSource = '';
    if (data && typeof data.blocked_reason === 'string' && data.blocked_reason) blockedSource = data.blocked_reason;
    else if (source && typeof source.reason_code === 'string' && source.reason_code) blockedSource = source.reason_code;
    return {
      status: source && typeof source.status === 'string' ? source.status : '',
      currency: currency,
      items: items,
      itemsMissing: rawCatalog === null,
      purchaseAvailable: purchaseAvailable,
      blockedText: blockedSource ? reasonText(blockedSource) : ''
    };
  }

  function inventoryView(doc) {
    const source = doc && typeof doc === 'object' ? doc : null;
    const data = source && source.data && typeof source.data === 'object' ? source.data : null;
    const rawItems = data && Array.isArray(data.items) ? data.items : null;
    const items = (rawItems || []).filter(function (entry) {
      return entry && typeof entry === 'object';
    }).map(function (entry) {
      const usable = entry.usable === true;
      let blocked = '';
      if (!usable) {
        blocked = reasonText(entry.blocked_reason || entry.reason_code) || '服务没有说明这件东西为什么不能用。';
      }
      /* 库存里的 item_instance_id 是 POST /items/use 的真实键：原字节保留，展示另用 idLabel。 */
      const idCheck = protocolId(entry.item_instance_id, PROTOCOL_ID_MAX);
      const catalogCheck = protocolId(entry.catalog_item_id, PROTOCOL_ID_MAX);
      return {
        id: idCheck.ok ? idCheck.value : '',
        idOk: idCheck.ok,
        idLabel: safeLabel(idCheck.value, 80),
        idMessage: idCheck.ok ? '' : idCheck.message,
        name: sanitizeLine(entry.name, 80) || '未命名物件',
        catalogId: catalogCheck.ok ? catalogCheck.value : '',
        catalogIdOk: catalogCheck.ok,
        catalogLabel: safeLabel(catalogCheck.value, 80),
        ownership: sanitizeLine(entry.ownership, 40),
        location: sanitizeLine(entry.location, 80),
        delivery: sanitizeLine(entry.delivery_status, 40),
        deliveryLabel: humanizeCode(entry.delivery_status),
        usable: usable,
        blocked: blocked,
      };
    });
    return {
      status: source && typeof source.status === 'string' ? source.status : '',
      items: items,
      itemsMissing: rawItems === null,
      reasonCode: source && source.reason_code ? source.reason_code : ''
    };
  }

  function validateNoteDraft(input) {
    const source = input && typeof input === 'object' ? input : {};
    const titleRaw = typeof source.title === 'string' ? source.title : '';
    const bodyRaw = typeof source.body === 'string' ? source.body : '';
    const title = titleRaw.replace(/\s+/g, ' ').trim();
    const body = bodyRaw.replace(/\r\n?/g, '\n');
    const errors = [];
    if (!title) errors.push('标题不能为空');
    if (title.length > 80) errors.push('标题最多 80 字');
    if (body.length > 4000) errors.push('正文最多 4000 字');
    return {ok: errors.length === 0, value: {title: title, body: body}, errors: errors};
  }

  /* POST /notes 的实际 payload：修订必须带 note_id（来自列表项 id），新建绝不带。
     标题与正文仍然靠 expected_head 做真实 CAS，不按标题猜是哪一条。
     note_id / expected_head / operation_key 都按服务原字节回传，不 trim、不截断、不脱敏。 */
  function buildNotePayload(input) {
    const source = input && typeof input === 'object' ? input : {};
    const payload = {
      title: typeof source.title === 'string' ? source.title : '',
      body: typeof source.body === 'string' ? source.body : ''
    };
    const noteId = protocolId(source.note_id, PROTOCOL_ID_MAX);
    if (noteId.ok) payload.note_id = noteId.value;
    const operationKey = protocolId(source.operation_key, PROTOCOL_KEY_MAX);
    if (operationKey.ok) payload.operation_key = operationKey.value;
    const head = protocolId(source.expected_head, PROTOCOL_HEAD_MAX);
    if (head.ok) payload.expected_head = head.value;
    return payload;
  }

  /* POST /purchases 的实际 payload：catalog_item_id 原字节回传，operation_key 复用同一次意图的键。 */
  function buildPurchasePayload(input) {
    const source = input && typeof input === 'object' ? input : {};
    const catalogRef = protocolId(source.catalog_item_id, PROTOCOL_ID_MAX);
    if (!catalogRef.ok) {
      return {ok: false, field: 'catalog_item_id', reason: catalogRef.reason,
        message: '目录项标识不可用（' + catalogRef.message + '），没有发出请求。'};
    }
    const key = protocolId(source.operation_key, PROTOCOL_KEY_MAX);
    if (!key.ok) {
      return {ok: false, field: 'operation_key', reason: key.reason,
        message: '操作键不可用（' + key.message + '），没有发出请求。'};
    }
    return {ok: true, payload: {catalog_item_id: catalogRef.value, operation_key: key.value}};
  }

  /* POST /items/use 的实际 payload：item_instance_id 必须与服务库存里的原字节完全一致。 */
  function buildUsePayload(input) {
    const source = input && typeof input === 'object' ? input : {};
    const itemRef = protocolId(source.item_instance_id, PROTOCOL_ID_MAX);
    if (!itemRef.ok) {
      return {ok: false, field: 'item_instance_id', reason: itemRef.reason,
        message: '物件标识不可用（' + itemRef.message + '），没有发出请求。'};
    }
    const key = protocolId(source.operation_key, PROTOCOL_KEY_MAX);
    if (!key.ok) {
      return {ok: false, field: 'operation_key', reason: key.reason,
        message: '操作键不可用（' + key.message + '），没有发出请求。'};
    }
    return {ok: true, payload: {item_instance_id: itemRef.value, operation_key: key.value}};
  }

  /* 修订必须有可用条目 id，否则宁可挡住，也不让服务把修订当成新建。 */
  function noteEditBlocked(editor) {
    const mode = editor && editor.mode === 'edit' ? 'edit' : 'create';
    if (mode !== 'edit') return '';
    const check = protocolId(editor && editor.noteId, PROTOCOL_ID_MAX);
    if (!check.ok) return '这一条没有可用的条目 id（' + check.message + '），不能修订；请回到列表重新打开它。';
    return '';
  }

  function validateReferenceDraft(input) {
    const source = input && typeof input === 'object' ? input : {};
    const sourceId = typeof source.source_id === 'string' ? source.source_id.replace(/\s+/g, ' ').trim() : '';
    const revision = typeof source.source_revision === 'string' ? source.source_revision.replace(/\s+/g, ' ').trim() : '';
    const label = typeof source.label === 'string' ? source.label.replace(/\s+/g, ' ').trim() : '';
    const errors = [];
    if (!sourceId) errors.push('出处标识不能为空');
    if (sourceId.length > 200) errors.push('出处标识最多 200 字');
    if (!revision) errors.push('来源版本不能为空');
    if (revision.length > 120) errors.push('来源版本最多 120 字');
    if (!label) errors.push('给它起一个能认出来的名字');
    if (label.length > 60) errors.push('名字最多 60 字');
    return {ok: errors.length === 0, value: {source_id: sourceId, source_revision: revision, label: label}, errors: errors};
  }

  const HIDDEN_FIELD_KEY = /(token|secret|password|passwd|csrf|cookie|session|api[_-]?key|private|credential|home|profile|root[_-]?path|file[_-]?path)/i;

  function displayFieldEntries(record, limit) {
    if (!record || typeof record !== 'object' || Array.isArray(record)) return [];
    const cap = typeof limit === 'number' && limit > 0 ? limit : 8;
    const entries = [];
    Object.keys(record).forEach(function (key) {
      if (entries.length >= cap) return;
      if (HIDDEN_FIELD_KEY.test(key)) return;
      const value = record[key];
      if (value === null || value === undefined) return;
      if (typeof value === 'object') return;
      let text = '';
      if (typeof value === 'boolean') text = value ? '是' : '否';
      else text = sanitizeLine(value, 200);
      if (!text) return;
      entries.push({key: sanitizeLine(key, 40), text: text});
    });
    return entries;
  }

  function makeOperationKey() {
    const cryptoApi = root.crypto;
    if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
      try {
        return cryptoApi.randomUUID();
      } catch (error) { /* 退回到可用的随机键 */ }
    }
    const hex = '0123456789abcdef';
    let random = '';
    for (let i = 0; i < 24; i += 1) random += hex[Math.floor(Math.random() * 16)];
    return 'op-' + Date.now().toString(16) + '-' + random;
  }

  /* op key 表里的 scope 必须保留服务原字符串：scope 里带 item_instance_id /
     catalog_item_id，一旦脱敏或截断，两个不同的物件就会撞成同一个键并复用
     同一个 operation_key。这里只校验长度与控制字符；不合法时退到可复现的哈希桶。 */
  const SCOPE_MAX = 400;

  function hashText(text) {
    let hash = 0x811c9dc5;
    for (let i = 0; i < text.length; i += 1) {
      hash ^= text.charCodeAt(i);
      hash = (hash * 0x01000193) >>> 0;
    }
    return hash.toString(16);
  }

  function scopeKey(scope) {
    const check = protocolId(scope, SCOPE_MAX);
    if (check.ok) return check.value;
    const text = scope === null || scope === undefined ? '' : String(scope);
    return 'scope:invalid:' + hashText(text);
  }

  function createOpStore(storage, makeKey) {
    const generate = typeof makeKey === 'function' ? makeKey : makeOperationKey;
    const entries = Object.create(null);
    const unresolved = [];

    function readJSON(key, fallback) {
      try {
        if (!storage || typeof storage.getItem !== 'function') return fallback;
        const raw = storage.getItem(key);
        if (!raw) return fallback;
        return JSON.parse(raw);
      } catch (error) {
        return fallback;
      }
    }

    function persist() {
      const pending = {};
      Object.keys(entries).forEach(function (scope) {
        const entry = entries[scope];
        pending[scope] = {key: entry.key, at: entry.at, path: entry.path};
      });
      try {
        if (storage && typeof storage.setItem === 'function') {
          storage.setItem(SESSION_KEYS.pending, JSON.stringify(pending));
          storage.setItem(SESSION_KEYS.unresolved, JSON.stringify(unresolved));
        }
      } catch (error) { /* 私密模式不可写：只保留内存态 */ }
    }

    (function restore() {
      const saved = readJSON(SESSION_KEYS.pending, null);
      if (saved && typeof saved === 'object' && !Array.isArray(saved)) {
        Object.keys(saved).forEach(function (scope) {
          const entry = saved[scope];
          if (!entry || typeof entry !== 'object') return;
          if (typeof entry.key !== 'string' || !entry.key) return;
          entries[scope] = {
            key: entry.key,
            at: typeof entry.at === 'number' ? entry.at : 0,
            path: apiPath(entry.path)
          };
        });
      }
      const savedUnknown = readJSON(SESSION_KEYS.unresolved, null);
      if (Array.isArray(savedUnknown)) {
        savedUnknown.forEach(function (entry) {
          if (!entry || typeof entry !== 'object') return;
          if (typeof entry.key !== 'string' || !entry.key) return;
          const scope = scopeKey(entry.scope);
          if (!scope) return;
          if (unresolved.some(function (existing) { return existing.key === entry.key; })) return;
          unresolved.push({
            scope: scope,
            key: entry.key,
            path: apiPath(entry.path),
            at: typeof entry.at === 'number' ? entry.at : 0
          });
        });
      }
    })();

    return {
      ensure: function (scope, meta) {
        const clean = scopeKey(scope);
        const existing = entries[clean];
        if (existing) return {key: existing.key, at: existing.at, path: existing.path, reused: true};
        const created = {
          key: (meta && meta.keyPrefix ? meta.keyPrefix : '') + generate(),
          at: Date.now(),
          path: apiPath(meta && meta.path)
        };
        entries[clean] = created;
        persist();
        return {key: created.key, at: created.at, path: created.path, reused: false};
      },
      peek: function (scope) {
        const found = entries[scopeKey(scope)];
        return found ? {key: found.key, at: found.at, path: found.path} : null;
      },
      resolve: function (scope) {
        const clean = scopeKey(scope);
        delete entries[clean];
        for (let i = unresolved.length - 1; i >= 0; i -= 1) {
          if (unresolved[i].scope === clean) unresolved.splice(i, 1);
        }
        persist();
      },
      markUnknown: function (scope, meta) {
        const clean = scopeKey(scope);
        const entry = entries[clean];
        if (!entry) return null;
        if (meta && meta.path) entry.path = apiPath(meta.path);
        const already = unresolved.some(function (item) { return item.key === entry.key; });
        if (!already) {
          unresolved.push({scope: clean, key: entry.key, path: entry.path, at: Date.now()});
        }
        persist();
        return {key: entry.key, at: entry.at, path: entry.path};
      },
      listUnresolved: function () {
        return unresolved.slice().sort(function (a, b) { return a.at - b.at; });
      }
    };
  }

  function createNavStore(storage) {
    return {
      save: function (state) {
        const screen = state && typeof state.screen === 'string' && SCREEN_META[state.screen] ? state.screen : 'home';
        /* detail 是打开商品详情用的 catalog_item_id：必须原样保留，否则刷新后找不回这一件。 */
        const detailCheck = protocolId(state && state.detail, PROTOCOL_ID_MAX);
        const detail = detailCheck.ok ? detailCheck.value : '';
        try {
          if (storage && typeof storage.setItem === 'function') {
            storage.setItem(SESSION_KEYS.nav, JSON.stringify({screen: screen, detail: detail}));
          }
        } catch (error) { /* 导航位置丢了不影响业务真相 */ }
      },
      read: function () {
        try {
          if (!storage || typeof storage.getItem !== 'function') return null;
          const raw = storage.getItem(SESSION_KEYS.nav);
          if (!raw) return null;
          const parsed = JSON.parse(raw);
          if (!parsed || typeof parsed.screen !== 'string') return null;
          if (!SCREEN_META[parsed.screen]) return null;
          const detailCheck = protocolId(parsed.detail, PROTOCOL_ID_MAX);
          return {
            screen: parsed.screen,
            detail: detailCheck.ok ? detailCheck.value : ''
          };
        } catch (error) {
          return null;
        }
      },
      clear: function () {
        try {
          if (storage && typeof storage.removeItem === 'function') storage.removeItem(SESSION_KEYS.nav);
        } catch (error) { /* ignore */ }
      }
    };
  }

  /* ------------------------------------------------------------------ *
   * 世界 / 身体：owner 快照 → 安全视图
   * 契约：docs/engineering/v04-world-body-contract.md（C 冻结的 data 九块）
   * 铁律：只搬运 owner 给的事实；缺字段就是 unknown/unavailable；
   *       不补数值、不拿 display 名当授权、不显示 body 内部浮点。
   * ------------------------------------------------------------------ */

  const WORLD_VERBS = ['MOVE', 'PICK_UP', 'PLACE', 'POSE', 'REST'];
  const POSE_VALUES = ['standing', 'seated', 'lying', 'transitioning'];
  const ORIENTATION_VALUES = ['north', 'south', 'east', 'west', 'up', 'down'];
  const HAND_SLOT_VALUES = ['left_hand', 'right_hand'];
  const HAND_SLOT_TEXT = {left_hand: '左手', right_hand: '右手'};
  const REST_STATE_VALUES = ['start', 'stop'];
  const REST_MODE_VALUES = ['rest', 'sleep_like'];
  const WORLD_DEST_MAX = 96;
  const WORLD_OBJECT_MAX = 128;
  const WORLD_KEY_MAX = 128;
  const WORLD_BLOCK_KEYS = ['world', 'location', 'local_environment', 'destinations', 'travel', 'objects', 'body', 'actions', 'recent_executions'];

  /* ------------------------------------------------------------------ *
   * rc1 房间 / 实体日用店（components/phone/web 的消费端）
   *
   * 契约：docs/engineering/rc1-room-living-contract.md（C 冻结；本文件在它落地前
   * 先把界面结构与命令路径准备好，字段名与动词名**全部收在下面这张表里**，
   * 契约定稿后只改这一处，不散落在各 render）。
   *
   * 已冻结、可以直接用的形状（不是猜的）：
   *   · 摆放目标逐 kind 的精确键集：components/phone/server.py:96-102
   *     room = {kind, location_id, area_id}；surface = {kind, location_id, surface_id}
   *   · 实体店 HTTP：GET /api/phone/store、POST /api/phone/store/purchases{catalog_item_id, operation_key}
   *   · 快照新增块：owned_furniture / deliveries / surfaces / local_environment.light
   *
   * 铁律：
   *   1. 动词名只认 owner 在 data.actions.verbs 里**真的报上来**的那个：ARRANGE 与
   *      RELOCATE 谁出现用谁，两个都没有就是不支持（不是自己挑一个发出去）。
   *   2. 参数键也要对得上 owner 报的 params；对不上就挡下并说明，不改名、不硬发。
   *   3. 未知光不是“黑的”；pending 到货不能使用/布置；布置好不等于能直接用；
   *      开灯回执不宣称身体已变。
   * ------------------------------------------------------------------ */

  const ROOM_VERB_NAMES = {
    arrange: ['ARRANGE', 'RELOCATE'],
    fixture: ['TOGGLE_FIXTURE']
  };
  const ROOM_EXTRA_VERBS = ['ARRANGE', 'RELOCATE', 'TOGGLE_FIXTURE'];
  const ALL_WORLD_VERBS = WORLD_VERBS.concat(ROOM_EXTRA_VERBS);
  /* ARRANGE/RELOCATE 与 TOGGLE_FIXTURE 要发的键（C 的 PARAM_SPEC 与 P 的白名单都要求 object_id）。
     item_instance_id 只是别名：owner 报的名字不是它，界面就发 object_id。 */
  const ARRANGE_PARAM_KEYS = ['object_id', 'target'];
  const FIXTURE_PARAM_KEYS = ['object_id', 'state'];
  const ROOM_ID_MAX = 128;
  const LIGHT_BAND_TEXT = {dim: '暗', moderate: '中等', bright: '明亮'};
  const LIGHT_POWER_TEXT = {on: '开着', off: '关着'};
  const LIGHT_STATUS_TEXT = {available: '房间光有声明', unknown: '房间光未知', disabled: '房间光功能关着'};
  const FIXTURE_STATE_VALUES = ['on', 'off'];
  const USABLE_KIND_TEXT = {CARRY: '随身带着', FURNISH: '家具（要配送）', CONSUME: '可以吃掉', LIGHT: '灯'};
  /* 到货状态：owner 用大写枚举（PENDING/DELIVERED），这里统一小写再翻，不猜新状态。 */
  /* 到货状态：C 用大写枚举（DELIVERED / IN_TRANSIT / AT_HOME_UNPLACED / PENDING），统一小写再翻。 */
  const DELIVERY_STATE_TEXT = {
    pending: '还没到货', delivered: '已到货', in_transit: '配送中', cancelled: '已取消',
    at_home_unplaced: '到家了，还没摆出来'
  };
  const SURFACE_SUPPORT_TEXT = {seated: '能坐', lying: '能躺', rest: '能休息', sleep_like: '能睡', place: '能放东西'};
  /* 台面的 label 是 owner 的英文字面量（desk/bed/chair），这里只做翻译，不补能力。 */
  const SURFACE_LABEL_TEXT = {desk: '书桌面', bed: '床面', chair: '椅面', desk_surface: '书桌面', bed_surface: '床面', chair_surface: '椅面'};
  /* 交付方式的 owner 字面量 → 人话；没列出的原样显示，不替服务下结论。 */
  const DELIVERY_MODE_TEXT = {
    instant: '当场就能拿走',
    delivery: '下单后配送',
    instant_ownership_then_materialize: '即时归属后直接送到家',
    temporal_home_delivery: '付款后由店里送到家（要等一段时间）',
    instant_home_delivery: '即时虚拟配送，直接到家'
  };

  function asRecord(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  }

  function asString(value) {
    return typeof value === 'string' ? value : '';
  }

  function asBool(value) {
    return typeof value === 'boolean' ? value : null;
  }

  function asSeconds(value) {
    return typeof value === 'number' && isFinite(value) && value >= 0 && Math.floor(value) === value ? value : null;
  }

  function asCount(value) {
    return typeof value === 'number' && isFinite(value) ? value : null;
  }

  /* owner 原因码 → 人话；大小写两种拼写都查，查不到就原样给服务的串。 */
  function worldReasonText(code) {
    const key = asString(code).trim();
    if (!key) return '';
    if (REASON_TEXT[key.toUpperCase()]) return REASON_TEXT[key.toUpperCase()];
    if (REASON_TEXT[key]) return REASON_TEXT[key];
    return reasonText(key);
  }

  function worldBlock(doc, key) {
    const data = doc && typeof doc === 'object' ? asRecord(doc.data) : null;
    return data ? asRecord(data[key]) : null;
  }

  /* 秒 → 「3 分钟」「40 秒」「1 小时 5 分钟」；没有数就是空串，不编 0。 */
  function formatWalkText(seconds) {
    const value = asSeconds(seconds);
    if (value === null) return '';
    if (value < 60) return value + ' 秒';
    const minutes = Math.round(value / 60);
    if (minutes < 60) return minutes + ' 分钟';
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? hours + ' 小时 ' + rest + ' 分钟' : hours + ' 小时';
  }

  function worldHeaderView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    const weekday = source ? asCount(source.weekday) : null;
    const weekdayLabel = weekday !== null && weekday >= 0 && weekday <= 6
      ? ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][weekday]
      : '';
    return {
      status: status,
      reason: source ? asString(source.reason_code) : '',
      source: source ? asString(source.source) : '',
      worldId: source ? safeLabel(source.world_id, 60) : '',
      revision: source ? asCount(source.revision) : null,
      sceneId: source ? safeLabel(source.scene_id, 60) : '',
      timezone: source ? safeLabel(source.timezone, 40) : '',
      localTime: source ? asString(source.local_time) : '',
      localDate: source ? asString(source.local_date) : '',
      weekdayLabel: weekdayLabel
    };
  }

  function worldLocationView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    const rawName = source ? asString(source.name) : '';
    /* place/area 是 MOVE 与 PLACE 的真实键：原字节留着，屏幕另用 Label。 */
    const placeId = source ? rawProtocol(source.place_id, WORLD_DEST_MAX) : '';
    const areaId = source ? rawProtocol(source.area_id, WORLD_DEST_MAX) : '';
    const placeLabel = source ? safeLabel(source.place_id, 60) : '';
    return {
      status: status,
      reason: source ? asString(source.reason_code) : '',
      node: source ? rawProtocol(source.node, WORLD_DEST_MAX) : '',
      nodeLabel: source ? safeLabel(source.node, 80) : '',
      placeId: placeId,
      placeIdLabel: placeLabel,
      areaId: areaId,
      areaIdLabel: source ? safeLabel(source.area_id, 60) : '',
      name: rawName ? safeLabel(rawName, 60) : placeLabel,
      kind: source ? safeLabel(source.kind, 30) : '',
      home: source ? asBool(source.home) : null
    };
  }

  function worldIndoorView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    const building = source ? asRecord(source.building) : null;
    const area = source ? asRecord(source.area) : null;
    const rawFurniture = source && Array.isArray(source.furniture) ? source.furniture : [];
    const rawAdjacent = source && Array.isArray(source.adjacent_areas) ? source.adjacent_areas : [];
    return {
      status: status,
      reason: source ? asString(source.reason_code) : '',
      available: status === 'available',
      buildingId: building ? safeLabel(building.place_id, 60) : '',
      buildingName: building ? safeLabel(building.name, 60) : '',
      areaId: area ? safeLabel(area.area_id, 60) : '',
      areaName: area ? safeLabel(area.name, 60) : '',
      areaKind: area ? safeLabel(area.kind, 30) : '',
      furniture: rawFurniture.filter(asRecord).map(function (entry) {
        return {
          id: safeLabel(entry.object_id, 60),
          name: safeLabel(entry.name, 60) || safeLabel(entry.object_id, 60),
          kind: safeLabel(entry.kind, 30),
          note: sanitizeLine(entry.note, 120)
        };
      }),
      furnitureTotal: source ? asCount(source.furniture_total) : null,
      adjacent: rawAdjacent.filter(asRecord).map(function (entry) {
        return {
          node: safeLabel(entry.node, 80),
          name: safeLabel(entry.name, 60) || safeLabel(entry.node, 80),
          kind: safeLabel(entry.kind, 30)
        };
      }),
      note: source ? sanitizeLine(source.note, 200) : ''
    };
  }

  function worldTravelView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    const band = source ? asString(source.progress_band) : '';
    const bodyLoad = source ? asString(source.body_load) : '';
    return {
      status: status,
      reason: source ? asString(source.reason_code) : '',
      active: status === 'in_progress',
      travelId: source ? safeLabel(source.travel_id, 80) : '',
      executionId: source ? safeLabel(source.execution_id, 100) : '',
      origin: source ? safeLabel(source.origin_location_id, 80) : '',
      destination: source ? safeLabel(source.destination_location_id, 80) : '',
      routeId: source ? safeLabel(source.route_id, 80) : '',
      durationSeconds: source ? asSeconds(source.route_duration_seconds) : null,
      elapsedSeconds: source ? asSeconds(source.elapsed_seconds) : null,
      remainingSeconds: source ? asSeconds(source.remaining_seconds) : null,
      band: band,
      bandLabel: TRAVEL_BAND_TEXT[band] || (band ? safeLabel(band, 30) : ''),
      statusLabel: TRAVEL_STATUS_TEXT[status] || (status ? safeLabel(status, 30) : ''),
      locationUnchanged: source ? asBool(source.location_unchanged) : null,
      bodyLoad: bodyLoad,
      bodyLoadText: BODY_LOAD_TEXT[bodyLoad] || (bodyLoad ? safeLabel(bodyLoad, 30) : ''),
      startedAt: source ? asString(source.started_at) : '',
      expectedArrivalAt: source ? asString(source.expected_arrival_at) : '',
      arrivedAt: source ? asString(source.arrived_at) : ''
    };
  }

  function worldDestinationsView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    const current = source ? rawProtocol(source.current_node, WORLD_DEST_MAX) : '';
    const rawPlaces = source && Array.isArray(source.places) ? source.places : [];
    const rawNodes = source && Array.isArray(source.nodes) ? source.nodes : [];
    return {
      status: status,
      reason: source ? asString(source.reason_code) : '',
      scope: source ? safeLabel(source.scope, 40) : '',
      current: current,
      currentLabel: source ? safeLabel(source.current_node, 80) : '',
      walkMaxSeconds: source ? asSeconds(source.walk_seconds_max) : null,
      nodes: rawNodes.filter(asRecord).map(function (entry) {
        const node = rawProtocol(entry.node, WORLD_DEST_MAX);
        const reach = asString(entry.reach);
        return {
          node: node,
          nodeLabel: safeLabel(entry.node, 80),
          placeId: rawProtocol(entry.place_id, WORLD_DEST_MAX),
          areaId: rawProtocol(entry.area_id, WORLD_DEST_MAX),
          label: safeLabel(entry.label, 60) || safeLabel(entry.place_id, 60) || safeLabel(entry.node, 80),
          kind: safeLabel(entry.kind, 30),
          walkSeconds: asSeconds(entry.walk_seconds),
          walkText: formatWalkText(entry.walk_seconds),
          reach: reach,
          reachLabel: REACH_TEXT[reach] || (reach ? safeLabel(reach, 30) : ''),
          direct: asBool(entry.direct_move_available),
          route: (Array.isArray(entry.route) ? entry.route : []).map(function (step) { return safeLabel(step, 60); }),
          here: node === current
        };
      }),
      places: rawPlaces.filter(asRecord).map(function (entry) {
        const reach = asString(entry.reach);
        const availability = asRecord(entry.availability);
        const availabilityStatus = availability ? asString(availability.status) : '';
        return {
          locationId: rawProtocol(entry.location_id, WORLD_DEST_MAX),
          locationLabel: safeLabel(entry.location_id, 80),
          name: safeLabel(entry.name, 60) || safeLabel(entry.location_id, 60),
          kind: safeLabel(entry.kind, 30),
          here: asBool(entry.here) === true,
          walkSeconds: asSeconds(entry.walk_seconds),
          walkText: formatWalkText(entry.walk_seconds),
          reach: reach,
          reachLabel: REACH_TEXT[reach] || (reach ? safeLabel(reach, 30) : ''),
          route: (Array.isArray(entry.route) ? entry.route : []).map(function (step) { return safeLabel(step, 60); }),
          availability: availabilityStatus,
          availabilityLabel: AVAILABILITY_TEXT[availabilityStatus] || (availabilityStatus ? safeLabel(availabilityStatus, 30) : ''),
          affordances: (Array.isArray(entry.affordances) ? entry.affordances : []).filter(asRecord).map(function (item) {
            return {
              id: safeLabel(item.affordance_id, 60),
              description: sanitizeLine(item.description, 120)
            };
          }),
          indoorAreas: (Array.isArray(entry.indoor_areas) ? entry.indoor_areas : []).filter(asRecord).map(function (item) {
            return {id: safeLabel(item.area_id, 60), name: safeLabel(item.name, 60), kind: safeLabel(item.kind, 30)};
          })
        };
      })
    };
  }
  /* 手上/手持的每一件东西：协议原值（id，只进 payload）与展示标签（idLabel，只上屏）分开放。
     非字符串 / 空 / 超长 / 含控制字符一律 idOk=false，界面严格拒绝，绝不拿展示串冒名提交。 */
  function worldHandView(raw) {
    const check = protocolId(raw, WORLD_OBJECT_MAX);
    return {
      present: raw !== null && raw !== undefined && raw !== '',
      id: check.ok ? check.value : '',
      idOk: check.ok,
      idMessage: check.ok ? '' : check.message,
      idLabel: safeLabel(check.ok ? check.value : raw, 40)
    };
  }
  function worldObjectsView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    const hands = source ? asRecord(source.hands) : null;
    const rawHere = source && Array.isArray(source.here) ? source.here : [];
    const counts = source ? asRecord(source.counts) : null;
    const handSlots = {
      left_hand: worldHandView(hands ? hands.left_hand : null),
      right_hand: worldHandView(hands ? hands.right_hand : null)
    };
    const heldObjects = (source && Array.isArray(source.held) ? source.held : []).map(worldHandView);
    return {
      status: status,
      reason: source ? asString(source.reason_code) : '',
      revision: source ? asCount(source.substrate_revision) : null,
      here: rawHere.filter(asRecord).map(function (entry) {
        const idCheck = protocolId(entry.object_id, WORLD_OBJECT_MAX);
        const placement = asRecord(entry.placement);
        return {
          id: idCheck.ok ? idCheck.value : '',
          idOk: idCheck.ok,
          idLabel: safeLabel(idCheck.value, 40),
          label: safeLabel(entry.label, 60) || safeLabel(entry.object_id, 60),
          placementKind: safeLabel(entry.placement_kind, 30),
          placementText: placement
            ? [safeLabel(placement.location_id, 60), safeLabel(placement.area_id, 60)].filter(Boolean).join(' / ')
            : '',
          portable: asBool(entry.portable),
          portableSource: safeLabel(entry.portable_source, 60)
        };
      }),
      /* 展示用那一份：空着 = 假值（沿用旧消费点按真值判断手是否空着）。原值只在 handSlots/heldObjects。 */
      hands: {
        left_hand: handSlots.left_hand.present ? handSlots.left_hand.idLabel : '',
        right_hand: handSlots.right_hand.present ? handSlots.right_hand.idLabel : ''
      },
      handSlots: handSlots,
      handsReported: !!hands,
      heldObjects: heldObjects,
      held: heldObjects.map(function (item) { return item.idLabel; }),
      counts: {
        here: counts ? asCount(counts.here) : null,
        hands: counts ? asCount(counts.hands) : null
      }
    };
  }

  /* body 只出姿势 / 档位 / 恢复 / 来源：白名单拷贝，没列出的字段（尤其浮点）绝不进来。 */
  function bodyView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    const recoveryRaw = source ? asRecord(source.recovery) : null;
    const state = recoveryRaw ? asString(recoveryRaw.state) : '';
    const trend = recoveryRaw ? asString(recoveryRaw.trend) : '';
    const band = recoveryRaw ? asString(recoveryRaw.progress_band) : '';
    const mode = recoveryRaw ? asString(recoveryRaw.mode) : '';
    const rawSignals = source && Array.isArray(source.signals) ? source.signals : null;
    const signals = (rawSignals || []).filter(asRecord).slice(0, 8).map(function (entry) {
      const type = asString(entry.signal_type);
      const rawBand = asString(entry.qualitative_band);
      return {
        type: safeLabel(type, 40),
        label: SIGNAL_TEXT[type] || ('服务给了没翻译的信号：' + safeLabel(type, 40)),
        band: rawBand,
        bandLabel: BAND_TEXT[rawBand] || (rawBand ? safeLabel(rawBand, 20) : '')
      };
    });
    const pose = asString(source ? source.pose : '');
    const orientation = asString(source ? source.orientation : '');
    const signalsStatus = rawSignals ? 'available' : (source ? (asString(source.signals_status) || 'unknown') : 'unknown');
    return {
      status: status,
      reason: source ? asString(source.reason_code) : '',
      known: status === 'available' || status === 'partial',
      bodyId: source ? safeLabel(source.body_id, 60) : '',
      pose: pose,
      poseLabel: POSE_TEXT[pose] || (pose ? safeLabel(pose, 30) : ''),
      orientation: orientation,
      orientationLabel: ORIENTATION_TEXT[orientation] || (orientation ? safeLabel(orientation, 30) : ''),
      poseSource: source ? safeLabel(source.pose_source, 60) : '',
      recovery: {
        state: state,
        mode: mode,
        stateLabel: RECOVERY_STATE_TEXT[state] || (state ? safeLabel(state, 30) : ''),
        modeLabel: RECOVERY_MODE_TEXT[mode] || (mode ? safeLabel(mode, 30) : ''),
        trend: trend,
        trendLabel: RECOVERY_TREND_TEXT[trend] || (trend ? safeLabel(trend, 30) : ''),
        band: band,
        bandLabel: RECOVERY_BAND_TEXT[band] || (band ? safeLabel(band, 20) : ''),
        active: state === 'active'
      },
      signals: signals,
      signalsMissing: rawSignals === null,
      signalsStatus: asString(signalsStatus),
      signalsSource: source ? safeLabel(source.signals_source, 60) : '',
      bandsOnly: source ? asBool(source.bands_only) : null,
      rawExposed: source ? asBool(source.raw_values_exposed) : null,
      restEvidence: source ? safeLabel(source.rest_evidence, 80) : '',
      note: source ? sanitizeLine(source.note, 200) : ''
    };
  }

  /* 身体信号 → 一句能读的话；owner 没给信号就明确说没给。 */
  function worldSignalLines(body) {
    const view = body && typeof body === 'object' && body.signals ? body : bodyView(null);
    if (!view.known) return ['身体投影暂时读不到，界面不猜状态。'];
    if (view.signalsMissing) return ['服务这次没有提供身体信号。'];
    if (!view.signals.length) return ['此刻身体没有报告信号。'];
    return view.signals.map(function (signal) {
      return signal.bandLabel ? signal.label + '（' + signal.bandLabel + '）' : signal.label;
    });
  }

  function worldVerbView(entry) {
    const source = asRecord(entry);
    if (!source) return null;
    const verb = asString(source.verb);
    if (ALL_WORLD_VERBS.indexOf(verb) < 0) return null;
    const blocked = asString(source.blocked_reason);
    const params = asRecord(source.params);
    const current = asRecord(source.current);
    return {
      verb: verb,
      available: asBool(source.available),
      blockedReason: blocked,
      blockedText: blocked ? worldReasonText(blocked) : '',
      detail: sanitizeLine(source.detail, 200),
      params: params ? Object.keys(params).map(function (key) { return safeLabel(key, 40); }) : [],
      /* candidates 的字段按动词不同：MOVE 给 location_id，ARRANGE 给 object_id+placement+surfaces，
         TOGGLE_FIXTURE 给 object_id+power/since/band/declared_source。这里原样搬运，不按动词编造。 */
      candidates: (Array.isArray(source.candidates) ? source.candidates : []).filter(asRecord).map(function (item) {
        return {
          id: safeLabel(item.object_id || item.location_id, 80),
          objectId: rawProtocol(item.object_id, WORLD_OBJECT_MAX),
          locationId: rawProtocol(item.location_id, WORLD_DEST_MAX),
          areaId: rawProtocol(item.area_id, WORLD_DEST_MAX),
          label: safeLabel(item.label, 60),
          kind: safeLabel(item.kind, 30),
          power: asString(item.power).toLowerCase(),
          since: asString(item.since),
          band: asString(item.band),
          declaredSource: safeLabel(item.declared_source, 80),
          placement: asRecord(item.placement)
        };
      }),
      startAvailable: asBool(source.start_available),
      stopAvailable: asBool(source.stop_available),
      sleepLikeAvailable: asBool(source.sleep_like_available),
      currentPose: current ? safeLabel(current.pose, 30) : '',
      currentRecovery: current ? safeLabel(current.recovery_state, 30) : '',
      travelling: current ? asBool(current.travelling) : null,
      restSurface: current ? asBool(current.rest_surface) : null
    };
  }

  function worldActionsView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    const rawVerbs = source && Array.isArray(source.verbs) ? source.verbs : [];
    const verbs = {};
    rawVerbs.forEach(function (entry) {
      const view = worldVerbView(entry);
      if (view && !verbs[view.verb]) verbs[view.verb] = view;
    });
    return {
      status: status,
      reason: source ? asString(source.reason_code) : '',
      localVerbs: (source && Array.isArray(source.local_owner_verbs) ? source.local_owner_verbs : []).map(function (verb) {
        return safeLabel(verb, 20);
      }),
      verbs: verbs
    };
  }

  function worldRecentView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    const rawItems = source && Array.isArray(source.items) ? source.items : [];
    return {
      status: status,
      limit: source ? asCount(source.limit) : null,
      items: rawItems.filter(asRecord).map(function (entry) {
        const actionType = asString(entry.action_type);
        const stateKey = asString(entry.state);
        return {
          executionId: safeLabel(entry.execution_id, 100),
          actionType: safeLabel(actionType, 20),
          actionLabel: ACTION_TEXT[actionType] || safeLabel(actionType, 20),
          state: safeLabel(stateKey, 20),
          stateLabel: EXECUTION_STATE_TEXT[stateKey] || safeLabel(stateKey, 20),
          result: safeLabel(entry.result, 30),
          reason: asString(entry.reason_code),
          reasonText: worldReasonText(entry.reason_code),
          occurredAt: asString(entry.occurred_at),
          localOwner: asBool(entry.local_owner)
        };
      })
    };
  }

  /* 整个 GET /api/phone/world 的视图：九块都在；缺哪块就标 missing，不冒充空成功。 */
  /* rc1 房间三块挂在 objects 里（契约 §3.2–3.4）；顶层同名键只作旧快照兜底，
     不给 C 增加平行的顶层字段。 */
  function pickRoomBlock(data, objects, key) {
    const nested = objects ? asRecord(objects[key]) : null;
    if (nested) return nested;
    return data ? asRecord(data[key]) : null;
  }

  function worldView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    const objects = data ? asRecord(data.objects) : null;
    const view = {
      ok: !!(envelope && envelope.ok === true),
      status: envelope ? asString(envelope.status) : '',
      reason: envelope ? asString(envelope.reason_code) : '',
      hasData: !!data,
      blocks: {},
      header: worldHeaderView(data ? data.world : null),
      location: worldLocationView(data ? data.location : null),
      indoor: worldIndoorView(data ? data.local_environment : null),
      destinations: worldDestinationsView(data ? data.destinations : null),
      travel: worldTravelView(data ? data.travel : null),
      objects: worldObjectsView(data ? data.objects : null),
      body: bodyView(data ? data.body : null),
      actions: worldActionsView(data ? data.actions : null),
      recent: worldRecentView(data ? data.recent_executions : null),
      /* rc1 房间四块（C 的 contract §3.1–3.4）：room light 在 local_environment.light，
         台面 / 自有家具 / 配送是 **objects 块的块内附加字段**（契约保留九大顶层块形状）。
         顶层同名键只在旧快照兼容时兜底，不是契约要求。 */
      room: {
        light: roomLightView(data ? data.local_environment : null),
        furniture: ownedFurnitureView(pickRoomBlock(data, objects, 'owned_furniture')),
        deliveries: deliveriesView(pickRoomBlock(data, objects, 'deliveries')),
        surfaces: surfacesView(pickRoomBlock(data, objects, 'surfaces'))
      }
    };
    WORLD_BLOCK_KEYS.forEach(function (key) {
      const block = data ? asRecord(data[key]) : null;
      view.blocks[key] = block ? (asString(block.status) || 'available') : 'missing';
    });
    return view;
  }
  /* 协议值（目的地节点 / 物件 id / 摆放字段）必须是服务原字节，展示另用 label。 */
  function rawProtocol(value, maxLen) {
    const check = protocolId(value, maxLen);
    return check.ok ? check.value : '';
  }

  function worldVerbAvailability(view, verb) {
    const actions = view && view.actions ? view.actions : null;
    if (!actions || actions.status === 'unavailable' || actions.status === 'missing') {
      return {
        reported: false,
        available: null,
        blockedText: actions && actions.reason ? worldReasonText(actions.reason) : '服务没有报告世界动作'
      };
    }
    const entry = actions.verbs ? actions.verbs[verb] : null;
    if (!entry) {
      return {reported: false, available: null, blockedText: '服务没有报告“' + (ACTION_TEXT[verb] || verb) + '”这个动作'};
    }
    return {
      reported: true,
      available: entry.available,
      blockedText: entry.blockedText,
      entry: entry
    };
  }

  /* 一个可达节点能不能“点一下就出发”；返回空串才允许提交。 */
  function worldMoveDisabledReason(view, node) {
    const travel = view && view.travel ? view.travel : null;
    if (!node || !node.node) return '这个目的地没有可用的标识';
    if (travel && travel.active) return '正在路上，先等到达';
    if (node.here) return '已经在这里了';
    if (node.reach && node.reach !== 'reachable') return node.reachLabel || '这一步走不过去';
    if (node.direct === false) return '要分几步才能到，一次命令走不完';
    if (node.direct !== true) return '服务没有说明能不能一次走到';
    const gate = worldVerbAvailability(view, 'MOVE');
    if (!gate.reported) return gate.blockedText;
    if (gate.available !== true) return gate.blockedText || '服务说现在不能出门';
    return '';
  }

  /* 拿起：对象自己要有可用 id、portable 必须真的是 true，还要有真正空着的手。 */
  function worldPickupOptions(view, object) {
    if (!object || !object.idOk || !object.id) {
      return {ok: false, slots: [], message: '这件东西没有可用的标识，不能拿起。'};
    }
    if (object.portable === false) return {ok: false, slots: [], message: '服务说这件东西拿不起来。'};
    if (object.portable === null) return {ok: false, slots: [], message: '服务没有声明这件东西能不能拿。'};
    const gate = worldVerbAvailability(view, 'PICK_UP');
    if (!gate.reported || gate.available !== true) {
      return {ok: false, slots: [], message: gate.blockedText || '服务没有说明现在能不能拿起东西。'};
    }
    const objects = view && view.objects ? view.objects : null;
    if (!objects || !objects.handsReported) {
      return {ok: false, slots: [], message: '服务没有报告哪只手空着。'};
    }
    /* 手是否空着只认 owner 报的 handSlots.present（展示串不再参与判断）。 */
    const handSlots = objects.handSlots || null;
    const handsDisplay = objects.hands || {};
    const slots = HAND_SLOT_VALUES.filter(function (slot) {
      if (handSlots && handSlots[slot]) return !handSlots[slot].present;
      return !handsDisplay[slot];
    }).map(function (slot) {
      return {slot: slot, label: HAND_SLOT_TEXT[slot] || slot};
    });
    if (!slots.length) return {ok: false, slots: [], message: '两只手都占着，先放下手里的东西。'};
    return {ok: true, slots: slots, message: ''};
  }

  /* 放下：契约只认 substrate 的摆放目标；这里只能给“放进当前房间”。 */
  function buildPlacementTarget(target) {
    const source = asRecord(target);
    if (!source) return {ok: false, target: null, message: '没有给出摆放目标。'};
    if (asString(source.kind) !== 'room') {
      return {ok: false, target: null, message: '界面只提交“放进当前房间”的摆放。'};
    }
    const placeId = protocolId(source.location_id, WORLD_DEST_MAX);
    const areaId = protocolId(source.area_id, WORLD_DEST_MAX);
    if (!placeId.ok || !areaId.ok) {
      return {ok: false, target: null, message: '当前位置读不出房间，界面不知道放回哪儿。'};
    }
    return {ok: true, target: {kind: 'room', location_id: placeId.value, area_id: areaId.value}, message: ''};
  }

  function worldPlaceOption(view, object) {
    if (!object || !object.idOk || !object.id) {
      return {ok: false, target: null, message: '这件东西没有可用的标识，不能放下。'};
    }
    const gate = worldVerbAvailability(view, 'PLACE');
    if (!gate.reported || gate.available !== true) {
      return {ok: false, target: null, message: gate.blockedText || '服务没有说明现在能不能放东西。'};
    }
    const location = view && view.location ? view.location : null;
    if (!location || !location.placeId) {
      return {ok: false, target: null, message: '当前位置读不出来，界面不知道放回哪儿。'};
    }
    if (!location.areaId) {
      return {ok: false, target: null, message: '这里不是房间，界面不知道放回哪儿。'};
    }
    const placement = buildPlacementTarget({kind: 'room', location_id: location.placeId, area_id: location.areaId});
    return {ok: placement.ok, target: placement.target, message: placement.ok ? '' : placement.message};
  }

  /* 休息按钮只认 owner 的 start/stop/sleep_like 三个可用位；没报告就禁用。 */
  function worldRestOption(view) {
    const gate = worldVerbAvailability(view, 'REST');
    if (!gate.reported || !gate.entry) {
      return {
        reported: false,
        startEnabled: false,
        sleepEnabled: false,
        stopEnabled: false,
        blockedText: gate.blockedText || '服务没有报告休息能力。'
      };
    }
    const entry = gate.entry;
    const state = view && view.body && view.body.recovery ? view.body.recovery.state : '';
    return {
      reported: true,
      startEnabled: entry.startAvailable === true,
      sleepEnabled: entry.sleepLikeAvailable === true,
      stopEnabled: entry.stopAvailable === true,
      blockedText: gate.available === false ? (gate.blockedText || '服务说现在不能休息。') : '',
      state: state,
      pose: view && view.body ? view.body.pose : '',
      detail: entry.detail
    };
  }

  /* 参数按契约的精确 key 重建：多余的 key 不进 payload，缺 key 直接挡下。 */
  function buildWorldActionPayload(input) {
    const source = asRecord(input);
    const action = source ? asString(source.action) : '';
    if (ALL_WORLD_VERBS.indexOf(action) < 0) {
      return {ok: false, field: 'action', message: '服务不认识这个动作，没有发出请求。'};
    }
    const key = protocolId(source.operation_key, WORLD_KEY_MAX);
    if (!key.ok) {
      return {ok: false, field: 'operation_key', message: '操作键不可用（' + key.message + '），没有发出请求。'};
    }
    const params = source.params && typeof source.params === 'object' ? asRecord(source.params) : null;
    if (!params) return {ok: false, field: 'params', message: '动作参数不是对象，没有发出请求。'};
    const built = {};
    if (action === 'MOVE') {
      const dest = protocolId(params.destination_location_id, WORLD_DEST_MAX);
      if (!dest.ok) return {ok: false, field: 'destination_location_id', message: '目的地标识不可用（' + dest.message + '），没有发出请求。'};
      built.destination_location_id = dest.value;
    } else if (action === 'PICK_UP') {
      const objectId = protocolId(params.object_id, WORLD_OBJECT_MAX);
      if (!objectId.ok) return {ok: false, field: 'object_id', message: '物件标识不可用（' + objectId.message + '），没有发出请求。'};
      const slot = asString(params.slot_id);
      if (HAND_SLOT_VALUES.indexOf(slot) < 0) return {ok: false, field: 'slot_id', message: '手的位置只能是左手或右手。'};
      built.object_id = objectId.value;
      built.slot_id = slot;
    } else if (action === 'PLACE') {
      const objectId = protocolId(params.object_id, WORLD_OBJECT_MAX);
      if (!objectId.ok) return {ok: false, field: 'object_id', message: '物件标识不可用（' + objectId.message + '），没有发出请求。'};
      /* 台面是已冻结的目标种类（kind=surface）；房间优先，其次才是台面。 */
      const roomTarget = buildPlacementTarget(params.target);
      const placement = roomTarget.ok ? roomTarget : buildSurfacePlacementTarget(params.target);
      if (!placement.ok) return {ok: false, field: 'target', message: placement.message};
      built.object_id = objectId.value;
      built.target = placement.target;
    } else if (action === 'POSE') {
      const pose = asString(params.pose);
      if (POSE_VALUES.indexOf(pose) < 0) return {ok: false, field: 'pose', message: '姿势只能是站/坐/躺/正在动这几种。'};
      built.pose = pose;
      if (params.orientation !== undefined && params.orientation !== null) {
        const orientation = asString(params.orientation);
        if (ORIENTATION_VALUES.indexOf(orientation) < 0) {
          return {ok: false, field: 'orientation', message: '朝向不是服务认识的那几个方向。'};
        }
        built.orientation = orientation;
      }
    } else if (action === 'ARRANGE' || action === 'RELOCATE') {
      /* 布置/搬动自己买的家具：C 的 PARAM_SPEC 要的是 object_id + target（房间或世界声明过的台面）。
         item_instance_id 只是别名入口：界面统一把它发成 object_id，形状只有一种。 */
      const arrangeId = protocolId(params.object_id !== undefined ? params.object_id : params.item_instance_id, WORLD_OBJECT_MAX);
      if (!arrangeId.ok) return {ok: false, field: 'object_id', message: '家具标识不可用（' + arrangeId.message + '），没有发出请求。'};
      const target = buildArrangeTarget(params.target);
      if (!target.ok) return {ok: false, field: 'target', message: target.message};
      built.object_id = arrangeId.value;
      built.target = target.target;
    } else if (action === 'TOGGLE_FIXTURE') {
      /* 绝对状态：只发 on / off，没有“翻转”。fixture_id 是 object_id 的别名入口。 */
      const objectId = protocolId(params.object_id !== undefined ? params.object_id : params.fixture_id, ROOM_ID_MAX);
      if (!objectId.ok) return {ok: false, field: 'object_id', message: '灯的标识不可用（' + objectId.message + '），没有发出请求。'};
      const state = asString(params.state);
      if (FIXTURE_STATE_VALUES.indexOf(state) < 0) return {ok: false, field: 'state', message: '灯只能是要开（on）或要关（off），界面不发翻转。'};
      built.object_id = objectId.value;
      built.state = state;
    } else if (action === 'REST') {
      const state = asString(params.state);
      if (REST_STATE_VALUES.indexOf(state) < 0) return {ok: false, field: 'state', message: '休息只能是开始或停止。'};
      built.state = state;
      if (state === 'start') {
        const mode = params.mode === undefined || params.mode === null ? 'rest' : asString(params.mode);
        if (REST_MODE_VALUES.indexOf(mode) < 0) return {ok: false, field: 'mode', message: '休息方式只能是歇一会或睡着。'};
        built.mode = mode;
      }
    }
    return {ok: true, payload: {action: action, params: built, operation_key: key.value}, message: ''};
  }

  /* 操作键的 scope 要把 action + 每个确切参数都串进去，否则两件事会共用同一个键。 */
  function canonicalWorldParams(params) {
    const source = asRecord(params) || {};
    const ordered = {};
    Object.keys(source).sort().forEach(function (key) {
      const value = source[key];
      const nested = asRecord(value);
      ordered[key] = nested ? JSON.parse(canonicalWorldParams(nested)) : value;
    });
    return JSON.stringify(ordered);
  }

  function worldActionScope(action, params) {
    const verb = ALL_WORLD_VERBS.indexOf(asString(action)) >= 0 ? action : 'UNKNOWN';
    return 'world:' + verb + ':' + canonicalWorldParams(params);
  }

  /* 街区图画法（纯数据）：当前位置在上，可走的节点按 owner 给的秒数排成一条路。 */
  function worldMapPlan(view, limit) {
    const cap = typeof limit === 'number' && limit > 0 ? limit : 8;
    const destinations = view && view.destinations ? view.destinations : null;
    const nodes = destinations && Array.isArray(destinations.nodes) ? destinations.nodes : [];
    const reachable = nodes.filter(function (node) {
      return node && node.node && !node.here && node.reach === 'reachable';
    }).slice(0, cap);
    const rowHeight = 36;
    const top = 52;
    const width = 320;
    return {
      width: width,
      height: top + Math.max(reachable.length, 1) * rowHeight,
      origin: {x: 26, y: 24, label: '你在这里'},
      rows: reachable.map(function (node, index) {
        return {
          node: node.node,
          label: node.label,
          /* SVG 里一行不折行：短名给图，全长留给列表与 title。 */
          shortLabel: node.label.length > 12 ? node.label.slice(0, 11) + '…' : node.label,
          x: 26,
          y: top + index * rowHeight - 6,
          direct: node.direct === true,
          walkText: node.walkText,
          title: node.label + (node.walkText ? ' · ' + node.walkText : '') + (node.direct === true ? '' : ' · 要分几步')
        };
      })
    };
  }

  /* 折叠起来的技术详情：只列 id / 版本 / 原因码这类排错用得上的事实。 */
  function worldTechEntries(view) {
    const source = asRecord(view) ? view : worldView(null);
    const entries = [];
    function push(key, value) {
      if (value === null || value === undefined || value === '') return;
      entries.push({key: key, text: String(value)});
    }
    push('world_id', source.header.worldId);
    push('world_revision', source.header.revision);
    push('scene_id', source.header.sceneId);
    push('location_node', source.location.node);
    push('place_id', source.location.placeId);
    push('area_id', source.location.areaId);
    push('travel_status', source.travel.status);
    push('travel_execution', source.travel.executionId);
    push('progress_band', source.travel.band);
    push('body_load', source.travel.bodyLoad);
    push('substrate_revision', source.objects.revision);
    push('actions_status', source.actions.status);
    push('actions_reason', source.actions.reason);
    push('body_status', source.body.status);
    push('body_reason', source.body.reason);
    push('signals_status', source.body.signalsStatus);
    push('recent_status', source.recent.status);
    push('location_status', source.location.status);
    push('indoor_status', source.indoor.status);
    push('destinations_status', source.destinations.status);
    /* rc1 房间：技术详情只放排错用得上的事实（状态/原因码/秒数），不放假坐标。 */
    const room = asRecord(source.room) ? source.room : null;
    const light = room && room.light ? room.light : roomLightView(null);
    push('light_status', light.status);
    push('light_reason', light.reason);
    push('light_band', light.band);
    push('light_band_source', light.bandSource);
    push('light_power', light.power);
    push('light_since', light.since);
    push('light_on_seconds', light.onSeconds);
    push('light_declared_source', light.declaredSource);
    push('light_fixtures', light.fixtureIds.map(function (item) { return item.idLabel; }).join('、'));
    if (room && room.furniture) {
      push('owned_furniture_status', room.furniture.status);
      push('owned_furniture_reason', room.furniture.reason);
      push('owned_furniture_count', room.furniture.items.length);
    }
    if (room && room.deliveries) {
      push('deliveries_status', room.deliveries.status);
      push('deliveries_reason', room.deliveries.reason);
      push('deliveries_count', room.deliveries.items.length);
    }
    if (room && room.surfaces) {
      push('surfaces_status', room.surfaces.status);
      push('surfaces_reason', room.surfaces.reason);
      push('surfaces_count', room.surfaces.items.length);
    }
    return entries;
  }
  /* ---------------- rc1：房间光 / 自有家具 / 配送 / 桌面 / 实体店 ---------------- */

  /* local_environment.light：owner 说 available 才算“房间光已知”。
     unknown 一律保留成 unknown —— 界面上不写“黑的”，也不折算成 0。 */
  function roomLightView(envBlock) {
    const env = asRecord(envBlock);
    const source = env ? asRecord(env.light) : null;
    if (!source) {
      return {
        status: 'missing', statusLabel: '', reason: '', available: false,
        band: '', bandLabel: '', bandSource: '', power: '', powerLabel: '',
        since: '', onSeconds: null, onText: '', fixtureIds: [], declaredSource: '',
        placeId: '', areaId: '', note: ''
      };
    }
    const status = asString(source.status) || 'unknown';
    const band = asString(source.band);
    const power = asString(source.power);
    const onSeconds = asSeconds(source.on_seconds);
    const fixtures = (Array.isArray(source.fixture_ids) ? source.fixture_ids : []).map(function (entry) {
      const check = protocolId(entry, ROOM_ID_MAX);
      return {
        id: check.ok ? check.value : '',
        idOk: check.ok,
        idLabel: safeLabel(check.ok ? check.value : entry, 40)
      };
    }).filter(function (entry) { return entry.idOk || entry.idLabel; });
    return {
      status: status,
      statusLabel: LIGHT_STATUS_TEXT[status] || (status ? safeLabel(status, 20) : ''),
      reason: asString(source.reason_code),
      available: status === 'available',
      band: band,
      bandLabel: LIGHT_BAND_TEXT[band] || (band ? safeLabel(band, 20) : ''),
      bandSource: safeLabel(source.band_source, 60),
      power: power,
      powerLabel: LIGHT_POWER_TEXT[power] || (power ? safeLabel(power, 20) : ''),
      since: asString(source.since),
      onSeconds: onSeconds,
      onText: onSeconds === null ? '' : formatWalkText(onSeconds),
      fixtureIds: fixtures,
      declaredSource: safeLabel(source.declared_source, 80),
      placeId: rawProtocol(source.place_id, ROOM_ID_MAX),
      areaId: rawProtocol(source.area_id, ROOM_ID_MAX),
      note: sanitizeLine(source.note, 160)
    };
  }
  /* owned_furniture：她自己买的家具。instance id 原字节进 payload，placement 只读展示。 */
  function ownedFurnitureView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    const rawItems = source && Array.isArray(source.items) ? source.items : null;
    return {
      status: status,
      reason: source ? asString(source.reason_code) : '',
      itemsMissing: rawItems === null,
      arrangeAvailable: source ? asBool(source.arrange_available) : null,
      unplacedDelivered: (source && Array.isArray(source.unplaced_delivered) ? source.unplaced_delivered : []).map(function (entry) {
        return safeLabel(entry, 40);
      }),
      items: (rawItems || []).filter(asRecord).map(function (entry) {
        /* C 的块用 item_instance_id；substrate 那边有时叫 object_id。两个都收，发出去的永远是原字节。 */
        const rawId = entry.item_instance_id !== undefined ? entry.item_instance_id : entry.object_id;
        const idCheck = protocolId(rawId, WORLD_OBJECT_MAX);
        const catalogCheck = protocolId(entry.catalog_item_id, PROTOCOL_ID_MAX);
        const placement = asRecord(entry.placement) || asRecord(entry.target);
        const delivery = asString(entry.delivery_status || entry.delivery || entry.state).toLowerCase();
        const rawTargets = Array.isArray(entry.arrange_targets) ? entry.arrange_targets : [];
        return {
          id: idCheck.ok ? idCheck.value : '',
          idOk: idCheck.ok,
          idMessage: idCheck.ok ? '' : idCheck.message,
          idLabel: safeLabel(idCheck.ok ? idCheck.value : rawId, 40),
          name: sanitizeLine(entry.name, 80) || safeLabel(entry.label, 60) || safeLabel(entry.catalog_item_id, 60) || '没写名字的家具',
          catalogId: catalogCheck.ok ? catalogCheck.value : '',
          catalogLabel: safeLabel(entry.catalog_item_id, 60),
          usableKind: asString(entry.usable_kind),
          /* 能不能搬动是 owner 声明（relocatable），不是按名字猜。 */
          relocatable: asBool(entry.relocatable),
          ownsSurfaces: (Array.isArray(entry.surfaces) ? entry.surfaces : []).map(function (surface) {
            const check = protocolId(surface, ROOM_ID_MAX);
            return {id: check.ok ? check.value : '', idOk: check.ok, idLabel: safeLabel(surface, 40)};
          }),
          placementKind: placement ? safeLabel(placement.kind, 30) : '',
          placementText: placement
            ? [safeLabel(placement.location_id, 60), safeLabel(placement.area_id, 60)].filter(Boolean).join(' / ')
            : '',
          currentLocation: safeLabel(entry.current_location, 60),
          placed: placement ? true : asBool(entry.placed),
          delivered: asBool(entry.delivered),
          owned: asBool(entry.owned),
          arrangeAvailable: asBool(entry.arrange_available),
          delivery: delivery,
          deliveryLabel: DELIVERY_STATE_TEXT[delivery] || (delivery ? safeLabel(delivery, 30) : ''),
          arrangeTargets: rawTargets.filter(asRecord).map(function (targetEntry) {
            const target = asRecord(targetEntry.target);
            return {
              label: safeLabel(targetEntry.label || targetEntry.display_label, 40) || '服务给出的位置',
              target: target
            };
          }).filter(function (targetEntry) { return !!targetEntry.target; })
        };
      })
    };
  }

  /* deliveries：C 的投影里有 mode/duration_seconds/pending/delivered/items（大写状态枚举）。
     pending 就是还没到，不能使用也不能布置。 */
  function deliveriesView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    const rawItems = source && Array.isArray(source.items) ? source.items : null;
    const mode = source ? asString(source.mode) : '';
    return {
      status: status,
      reason: source ? asString(source.reason_code) : '',
      mode: mode,
      modeText: DELIVERY_MODE_TEXT[mode] || (mode ? safeLabel(mode, 40) : ''),
      durationSeconds: source ? asSeconds(source.duration_seconds) : null,
      pendingCount: source ? asCount(source.pending_count) : null,
      itemsMissing: rawItems === null,
      items: (rawItems || []).filter(asRecord).map(function (entry) {
        const idCheck = protocolId(entry.item_instance_id, WORLD_OBJECT_MAX);
        const rawState = asString(entry.state || entry.delivery_status || entry.status).toLowerCase();
        const remaining = asSeconds(entry.remaining_seconds);
        return {
          id: idCheck.ok ? idCheck.value : '',
          idOk: idCheck.ok,
          idLabel: safeLabel(idCheck.ok ? idCheck.value : entry.item_instance_id, 40),
          name: sanitizeLine(entry.name, 80) || safeLabel(entry.catalog_item_id, 60) || '没写名字的物件',
          state: rawState,
          stateLabel: DELIVERY_STATE_TEXT[rawState] || (rawState ? safeLabel(rawState, 30) : ''),
          pending: rawState === 'pending' || rawState === 'in_transit',
          expectedAt: asString(entry.expected_at || entry.expected_delivery_at || entry.expected_arrival_at),
          deliveredAt: asString(entry.delivered_at),
          remainingSeconds: remaining,
          remainingText: remaining === null ? '' : formatWalkText(remaining)
        };
      })
    };
  }

  /* surfaces：C 的 substrate 投影。surface_id 是 instance 级的 `<kind>:<owner item id>`
     （bed_surface 还保留兼容的裸写法），两张桌子绝不共用一块“全局桌面”。
     能力只照声明读：pose_condition / rest_modes / items（已经放在上面的东西）。 */
  function surfacesView(block) {
    const source = asRecord(block);
    const status = source ? (asString(source.status) || 'unavailable') : 'missing';
    /* C 的块把“真的在这里的台面”放在 here（items 只是历史叫法）；两个都认。 */
    const rawHere = source && Array.isArray(source.here) ? source.here
      : (source && Array.isArray(source.items) ? source.items : null);
    return {
      status: status,
      reason: source ? asString(source.reason_code) : '',
      itemsMissing: rawHere === null,
      count: source ? asCount(source.count) : null,
      restHere: (source && Array.isArray(source.rest_here) ? source.rest_here : []).length,
      items: (rawHere || []).filter(asRecord).map(function (entry) {
        const idCheck = protocolId(entry.surface_id, ROOM_ID_MAX);
        const placeCheck = protocolId(entry.location_id, WORLD_DEST_MAX);
        const areaCheck = protocolId(entry.area_id, WORLD_DEST_MAX);
        const poseCondition = asString(entry.pose_condition);
        const restModes = (Array.isArray(entry.rest_modes) ? entry.rest_modes : []).map(function (item) {
          const code = asString(item);
          return {code: code, label: SURFACE_SUPPORT_TEXT[code] || safeLabel(code, 30)};
        }).filter(function (item) { return !!item.code; });
        const rawLabel = sanitizeLine(entry.label || entry.name, 40);
        const kind = asString(entry.surface_kind);
        return {
          id: idCheck.ok ? idCheck.value : '',
          idOk: idCheck.ok,
          idMessage: idCheck.ok ? '' : idCheck.message,
          idLabel: safeLabel(idCheck.ok ? idCheck.value : entry.surface_id, 40),
          kind: kind,
          label: SURFACE_LABEL_TEXT[rawLabel] || rawLabel || SURFACE_LABEL_TEXT[kind] || '没写名字的台面',
          ownerItemId: rawProtocol(entry.owner_object_id || entry.owner_item_instance_id, ROOM_ID_MAX),
          ownerCatalog: safeLabel(entry.owner_catalog_item_id, 60),
          ownerName: sanitizeLine(entry.owner_name, 60),
          locationId: placeCheck.ok ? placeCheck.value : '',
          areaId: areaCheck.ok ? areaCheck.value : '',
          here: asBool(entry.here),
          poseCondition: poseCondition,
          poseLabel: poseCondition ? (SURFACE_SUPPORT_TEXT[poseCondition] || safeLabel(poseCondition, 20)) : '',
          restModes: restModes,
          children: (Array.isArray(entry.items) ? entry.items : []).map(function (child) {
            const check = protocolId(child, WORLD_OBJECT_MAX);
            return {id: check.ok ? check.value : '', idOk: check.ok, idLabel: safeLabel(child, 40)};
          }),
          note: sanitizeLine(entry.note, 120)
        };
      })
    };
  }
  /* 动词名只认 owner 报上来的那一个：ARRANGE 出现就用 ARRANGE，RELOCATE 出现就用 RELOCATE。 */
  function roomVerbFor(view, kind) {
    const names = ROOM_VERB_NAMES[kind] || [];
    const actions = view && view.actions ? view.actions : null;
    const label = kind === 'fixture' ? '开关灯' : '布置';
    if (!actions || actions.status === 'unavailable' || actions.status === 'missing') {
      return {
        verb: '', entry: null, available: null,
        blockedText: actions && actions.reason ? worldReasonText(actions.reason) : '服务没有报告世界动作'
      };
    }
    for (let i = 0; i < names.length; i += 1) {
      const entry = actions.verbs ? actions.verbs[names[i]] : null;
      if (entry) return {verb: names[i], entry: entry, available: entry.available, blockedText: entry.blockedText};
    }
    return {verb: '', entry: null, available: null, blockedText: '服务没有报告“' + label + '”这个动作'};
  }

  /* 界面要发的键必须都在 owner 声明的参数里（owner 可能同时接受别名，例如 object_id 与
     item_instance_id）；只要有一个要发的键没被声明，就挡下并说明，绝不改名硬发。 */
  function paramKeyMismatch(reported, planned) {
    if (!Array.isArray(reported) || !reported.length) return '';
    const declared = reported.map(function (key) { return asString(key); }).filter(Boolean).sort();
    const want = (Array.isArray(planned) ? planned : []).map(function (key) { return asString(key); });
    const missing = want.filter(function (key) { return declared.indexOf(key) < 0; });
    if (!missing.length) return '';
    return '服务声明的参数是 ' + declared.join('、') + '，界面这次要发 ' + missing.join('、') +
      '，服务没有声明它；两边不一致，先不提交。';
  }

  /* 台面目标：形状就是已冻结的 {kind, location_id, surface_id}（components/phone/server.py:96-102）。 */
  function buildSurfacePlacementTarget(target) {
    const source = asRecord(target);
    if (!source) return {ok: false, target: null, message: '没有给出台面目标，没有发出请求。'};
    if (asString(source.kind) !== 'surface') return {ok: false, target: null, message: '这不是台面目标。'};
    const surface = protocolId(source.surface_id, ROOM_ID_MAX);
    if (!surface.ok) {
      return {ok: false, target: null, message: '这块台面的标识不可用（' + surface.message + '），没有发出请求。'};
    }
    const place = protocolId(source.location_id, WORLD_DEST_MAX);
    if (!place.ok) {
      return {ok: false, target: null, message: '台面所在的地方读不出来，没有发出请求。'};
    }
    return {ok: true, target: {kind: 'surface', location_id: place.value, surface_id: surface.value}, message: ''};
  }

  /* 布置目标：房间（放进当前房间）或已声明的台面；container/hand 一律拒绝。 */
  function buildArrangeTarget(target) {
    const source = asRecord(target);
    if (!source) return {ok: false, target: null, message: '没有给出布置目标，没有发出请求。'};
    const kind = asString(source.kind);
    if (kind === 'room') {
      return buildPlacementTarget({kind: 'room', location_id: source.location_id, area_id: source.area_id});
    }
    if (kind === 'surface') return buildSurfacePlacementTarget(source);
    return {ok: false, target: null, message: '布置目标只能是房间或声明过的台面。'};
  }
  /* 可布置的位置：先是“放进现在这个房间”（只用 owner 给的 place/area），
     再是 owner 在条目上给出的 arrange_targets（没有就不编）。 */
  function arrangeTargetsFor(view, item) {
    const targets = [];
    const location = view && view.location ? view.location : null;
    if (location && location.placeId && location.areaId) {
      const built = buildPlacementTarget({kind: 'room', location_id: location.placeId, area_id: location.areaId});
      if (built.ok) targets.push({key: 'room', label: '放进现在的房间', target: built.target});
    }
    const declared = item && item.arrangeTargets ? item.arrangeTargets : [];
    declared.forEach(function (entry, index) {
      const built = buildArrangeTarget(entry.target);
      if (built.ok) targets.push({key: 'owner-' + index, label: entry.label, target: built.target});
    });
    return targets;
  }

  /* 一件自己买的家具能不能布置：owner 报的动词 + 可用性 + 到货 + 归属，全过才给按钮。 */
  function worldArrangeOption(view, item) {
    const found = roomVerbFor(view, 'arrange');
    if (!found.verb) return {ok: false, verb: '', targets: [], message: found.blockedText};
    const mismatch = paramKeyMismatch(found.entry ? found.entry.params : null, ARRANGE_PARAM_KEYS);
    if (mismatch) return {ok: false, verb: found.verb, targets: [], message: mismatch};
    if (found.available !== true) {
      return {ok: false, verb: found.verb, targets: [], message: found.blockedText || '服务说现在不能布置家具'};
    }
    if (!item || !item.idOk || !item.id) {
      return {ok: false, verb: found.verb, targets: [], message: '这件家具没有可用的标识，不能布置。'};
    }
    if (item.owned === false) {
      return {ok: false, verb: found.verb, targets: [], message: '这件东西不属于这个实例，不能布置。'};
    }
    /* 先说到货（这一件最容易误解），再看 owner 的两条声明。 */
    if (item.delivered === false || item.delivery === 'pending' || item.delivery === 'in_transit') {
      return {ok: false, verb: found.verb, targets: [], message: '这件家具还没到货，不能提前布置。'};
    }
    /* owner 的两条声明：relocatable（这类家具能不能搬）与 arrange_available（这一件现在能不能摆）。 */
    if (item.relocatable === false) {
      return {ok: false, verb: found.verb, targets: [], message: '服务说这一类家具不能搬动。'};
    }
    if (item.arrangeAvailable === false) {
      return {ok: false, verb: found.verb, targets: [], message: '服务说这一件现在不能布置。'};
    }
    const targets = arrangeTargetsFor(view, item);
    if (!targets.length) {
      return {ok: false, verb: found.verb, targets: [], message: '服务没有给出可以摆放的位置。'};
    }
    return {ok: true, verb: found.verb, targets: targets, message: ''};
  }

  /* 灯的开关是绝对状态 on/off，绝不发“toggle”；同状态由 owner 判 noop。 */
  function worldFixtureOption(view, fixture, currentPower) {
    const found = roomVerbFor(view, 'fixture');
    const checkId = protocolId(fixture && fixture.id, ROOM_ID_MAX);
    if (!found.verb) return {ok: false, verb: '', message: found.blockedText};
    const mismatch = paramKeyMismatch(found.entry ? found.entry.params : null, FIXTURE_PARAM_KEYS);
    if (mismatch) return {ok: false, verb: found.verb, message: mismatch};
    if (found.available !== true) {
      return {ok: false, verb: found.verb, message: found.blockedText || '服务说现在不能开关这盏灯'};
    }
    if (!checkId.ok) {
      return {ok: false, verb: found.verb, message: '这盏灯的标识不可用（' + checkId.message + '），没有发出请求。'};
    }
    const power = asString(currentPower);
    if (power && FIXTURE_STATE_VALUES.indexOf(power) < 0) {
      return {ok: false, verb: found.verb, message: '服务给的灯状态不是 on/off，界面不猜它现在是开还是关。'};
    }
    return {ok: true, verb: found.verb, message: ''};
  }

  /* 房间里可以开关的灯：优先用 owner 在 TOGGLE_FIXTURE.candidates 里逐盏报的
     object_id/power/label（多盏灯时每盏的状态各自独立）；没有 candidates 才退回
     light.fixture_ids + 房间聚合 power。两条路都只用 owner 的字段，不猜。 */
  function roomFixtureRows(view) {
    const light = view.room.light;
    const found = roomVerbFor(view, 'fixture');
    const candidates = found.entry && Array.isArray(found.entry.candidates) ? found.entry.candidates : [];
    const declared = candidates.filter(asRecord).map(function (entry) {
      const check = protocolId(entry.objectId, ROOM_ID_MAX);
      const power = asString(entry.power);
      return {
        id: check.ok ? check.value : '',
        idOk: check.ok,
        idMessage: check.ok ? '' : check.message,
        idLabel: safeLabel(entry.label, 40) || safeLabel(entry.objectId, 40),
        power: power,
        powerKnown: power === 'on' || power === 'off',
        band: asString(entry.band),
        since: asString(entry.since),
        declaredSource: safeLabel(entry.declaredSource, 80),
        source: 'gateway_candidate'
      };
    }).filter(function (row) { return row.idOk || row.idLabel; });
    if (declared.length) return declared;
    return light.fixtureIds.map(function (item) {
      return {
        id: item.id,
        idOk: item.idOk,
        idMessage: '',
        idLabel: item.idLabel,
        power: light.power,
        powerKnown: light.power === 'on' || light.power === 'off',
        band: light.band,
        since: light.since,
        declaredSource: light.declaredSource,
        source: 'light_block'
      };
    });
  }

  function asNumberOrNull(value) {
    if (typeof value === 'number' && isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() !== '' && isFinite(Number(value))) return Number(value);
    return null;
  }

  /* 实体店的货架：GET /api/phone/store 的 data（C 的 store_shop_projection）。
     位置/营业/余额/渠道/上限都按 owner 报的读；读不到就如实说读不到。 */
  function storeView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    const rawCatalog = data && Array.isArray(data.catalog) ? data.catalog : null;
    const availability = data ? asRecord(data.availability) : null;
    const availabilityStatus = availability ? asString(availability.status) : '';
    const currency = data ? sanitizeLine(data.currency, 12) : '';
    const balance = data ? asNumberOrNull(data.balance) : null;
    const location = data ? asRecord(data.location) : null;
    const delivery = data ? asRecord(data.delivery) : null;
    const deliveryMode = delivery ? asString(delivery.mode) : '';
    const items = (rawCatalog || []).filter(asRecord).map(function (entry) {
      const idCheck = protocolId(entry.catalog_item_id, PROTOCOL_ID_MAX);
      const itemDelivery = asRecord(entry.delivery);
      const mode = itemDelivery ? asString(itemDelivery.mode) : deliveryMode;
      const usableKind = asString(entry.usable_kind);
      /* C 给每件一个 affordable（余额够不够）；没有这件能不能买的说法时保持 null，不替服务说“能买”。 */
      const affordable = asBool(entry.affordable);
      const purchasable = asBool(entry.purchasable);
      return {
        id: idCheck.ok ? idCheck.value : '',
        idOk: idCheck.ok,
        idMessage: idCheck.ok ? '' : idCheck.message,
        idLabel: safeLabel(idCheck.ok ? idCheck.value : entry.catalog_item_id, 60),
        name: sanitizeLine(entry.name, 80) || '未命名物件',
        description: sanitizeText(entry.description, 300),
        price: asNumberOrNull(entry.price),
        itemCurrency: sanitizeLine(entry.currency, 12) || currency,
        usableKind: usableKind,
        usableKindLabel: USABLE_KIND_TEXT[usableKind] || (usableKind ? safeLabel(usableKind, 30) : ''),
        deliveryMode: mode,
        deliveryText: DELIVERY_MODE_TEXT[mode] || (mode ? safeLabel(mode, 40) : ''),
        affordable: affordable,
        purchasable: purchasable === null ? affordable : purchasable,
        blockedReason: asString(entry.blocked_reason),
        blockedText: entry.blocked_reason ? worldReasonText(entry.blocked_reason) : ''
      };
    });
    let blockedSource = '';
    if (data && typeof data.reason_code === 'string' && data.reason_code) blockedSource = data.reason_code;
    else if (data && typeof data.blocked_reason === 'string' && data.blocked_reason) blockedSource = data.blocked_reason;
    else if (envelope && typeof envelope.reason_code === 'string' && envelope.reason_code) blockedSource = envelope.reason_code;
    const atStore = data ? (asBool(data.at_store) !== null ? asBool(data.at_store) : asBool(data.here)) : null;
    return {
      ok: !!(envelope && envelope.ok === true),
      status: data ? (asString(data.status) || (envelope ? asString(envelope.status) : '')) : (envelope ? asString(envelope.status) : ''),
      reason: envelope ? asString(envelope.reason_code) : '',
      reasonCode: data ? asString(data.reason_code) : '',
      hasData: !!data,
      storeId: data ? safeLabel(data.store_id, 60) : '',
      storeName: data ? (sanitizeLine(data.store_name, 60) || safeLabel(data.store_id, 60)) : '',
      storeKind: data ? safeLabel(data.store_kind, 40) : '',
      channel: data ? safeLabel(data.channel, 60) : '',
      locationId: location ? rawProtocol(location.location_id, WORLD_DEST_MAX) : (data ? rawProtocol(data.location_id, WORLD_DEST_MAX) : ''),
      areaId: location ? rawProtocol(location.area_id, WORLD_DEST_MAX) : '',
      here: atStore,
      currency: currency,
      balance: balance,
      balanceText: balance === null ? '' : formatMoney(currency, balance),
      pendingDeliveryCount: data ? asCount(data.pending_delivery_count) : null,
      ownedFurnitureCount: data ? asCount(data.owned_furniture_count) : null,
      capacityAvailable: data ? asBool(data.capacity_available) : null,
      deliveryMode: deliveryMode,
      deliveryText: DELIVERY_MODE_TEXT[deliveryMode] || (deliveryMode ? safeLabel(deliveryMode, 40) : ''),
      deliverySeconds: delivery ? asSeconds(delivery.duration_seconds) : null,
      placementRequires: delivery ? safeLabel(delivery.placement_requires, 30) : '',
      availability: availabilityStatus,
      availabilityLabel: AVAILABILITY_TEXT[availabilityStatus] || (availabilityStatus ? safeLabel(availabilityStatus, 30) : ''),
      opensAt: availability ? safeLabel(availability.opens_at || availability.open, 20) : '',
      closesAt: availability ? safeLabel(availability.closes_at || availability.close, 20) : '',
      timezone: availability ? safeLabel(availability.timezone, 40) : '',
      purchaseAvailable: !!(data && data.purchase_available === true),
      blockedText: blockedSource ? worldReasonText(blockedSource) : '',
      items: items,
      itemsMissing: rawCatalog === null
    };
  }
  /* 私人项目（B 的 private_project_service 消费端）的界限与作用域前缀。
     它们必须住在 node 早退之前：纯函数检查要在没有 DOM 的情况下也能读到。 */
  const PROJECT_TITLE_MAX = 120;
  const PROJECT_CONTENT_MAX = 20000;
  /* 写作用的 scope 前缀：同一个 key 在同一次意图上复用，绝不换键重发。 */
  /* derive 是从旧版本另起一件新作品：它有自己的键，绝不和 commit 共用（同一键只对应一个意图）。 */
  const PROJECT_SCOPES = {create: 'project:create', commit: 'project:commit',
    close: 'project:close', derive: 'project:derive'};
  const PROJECT_HISTORY_LIMIT = 10;
  const PROJECT_DERIVE_NOTE_MAX = 120;
  const PROJECT_SOURCE_PURPOSES = ['quote', 'adapt', 'reuse'];

  /* 资料库与研究工作台（B 的 research_workspace_service 消费端）
   *
   * 契约：docs/engineering/v04-research-workspace-result.md
   *   GET  /api/phone/materials?query=&limit=     资料列表（不含正文）
   *   GET  /api/phone/materials/<artifact_id>     一件资料的正文与来源版本
   *   POST /api/phone/materials                   {title, text, source_kind, origin, operation_key}
   *   GET  /api/phone/research                    研究列表（不含正文）
   *   GET  /api/phone/research/<artifact_id>      正文 + 引用 + 每个来源的 CURRENT/STALE/UNAVAILABLE
   *   POST /api/phone/research                    {question, source_refs, scope, uncertainty, operation_key}
   *   POST /api/phone/research/<id>/updates       {content, expected_head, operation_key, scope?, uncertainty?}
   *   POST /api/phone/research/<id>/close         {expected_revision, operation_key}
   *
   * 铁律：资料/研究也是 Supply 的 ManagedArtifact；界面不猜来源版本、不代签成功、
   *       不把研究说成 Memory 或"已经读完了"。GET 只读；写要 CSRF + CAS。
   */
  const MATERIAL_TITLE_MAX = 120;
  const MATERIAL_TEXT_MAX = 19000;
  const MATERIAL_ORIGIN_MAX = 300;
  const MATERIAL_QUERY_MAX = 120;
  const RESEARCH_QUESTION_MAX = 500;
  const RESEARCH_SCOPE_MAX = 400;
  const RESEARCH_UNCERTAINTY_MAX = 400;
  const RESEARCH_CONTENT_MAX = 19000;
  const RESEARCH_SOURCES_MAX = 12;
  const RESEARCH_DEFAULT_UNCERTAINTY = '暂定，尚未验证';
  const MATERIAL_SOURCE_KINDS = ['user_supplied', 'fictional'];
  const SOURCE_KIND_TEXT = {user_supplied: '他给的文本', fictional: '明确是虚构'};
  const CHECK_STATUS_TEXT = {CURRENT: '当前版本', STALE: '来源已更新', UNAVAILABLE: '读不到/已撤回'};
  const MATERIAL_SCOPES = {import: 'material:import', create: 'research:create',
    update: 'research:update', close: 'research:close'};

  function materialRowView(raw) {
    const record = asRecord(raw);
    if (!record) return null;
    const id = protocolId(record.artifact_id, 200);
    if (!id.ok) return null;
    const revision = typeof record.revision === 'number' ? record.revision : null;
    return {
      id: id.value,
      title: safeLabel(record.title, MATERIAL_TITLE_MAX) || '未命名资料',
      sourceRevision: typeof record.source_revision === 'string' ? record.source_revision : '',
      sourceKind: typeof record.source_kind === 'string' ? record.source_kind : '',
      sourceKindText: SOURCE_KIND_TEXT[record.source_kind] || safeLabel(record.source_kind, 40),
      origin: sanitizeLine(record.origin, MATERIAL_ORIGIN_MAX) || '',
      excerpt: sanitizeText(record.excerpt, MATERIAL_ORIGIN_MAX + 200) || '',
      availability: typeof record.availability === 'string' ? record.availability : '',
      readOnly: record.read_only === true,
      revision: revision,
      versionNumber: typeof record.version_number === 'number' ? record.version_number : null,
      updatedAt: record.updated_at
    };
  }

  function materialListView(doc) {
    const record = asRecord(doc) || {};
    const data = asRecord(record.data) || record;
    const raw = Array.isArray(data.materials) ? data.materials : null;
    const rows = [];
    (raw || []).forEach(function (item) {
      const row = materialRowView(item);
      if (row) rows.push(row);
    });
    const skipped = asRecord(data.skipped) || {};
    return {
      itemsMissing: raw === null,
      materials: rows,
      count: typeof data.count === 'number' ? data.count : rows.length,
      matched: typeof data.matched === 'number' ? data.matched : rows.length,
      query: typeof data.query === 'string' ? data.query : '',
      truncated: data.truncated === true,
      skippedResearch: typeof skipped.research === 'number' ? skipped.research : 0,
      skippedOther: typeof skipped.other === 'number' ? skipped.other : 0
    };
  }

  function materialDetailView(doc) {
    const record = asRecord(doc) || {};
    const data = asRecord(record.data) || record;
    const raw = asRecord(data.material);
    const material = materialRowView(raw);
    if (!material) return null;
    return {
      material: material,
      text: typeof raw.text === 'string' ? raw.text : '',
      contentAvailable: raw.content_available !== false,
      availability: material.availability || 'AVAILABLE',
      note: typeof data.note === 'string' ? sanitizeLine(data.note, 200) : ''
    };
  }

  function validateMaterialDraft(draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    const title = typeof source.title === 'string' ? source.title.trim() : '';
    const text = typeof source.text === 'string' ? source.text : '';
    const origin = typeof source.origin === 'string' ? source.origin.trim() : '';
    const kind = typeof source.source_kind === 'string' ? source.source_kind : '';
    if (!title) errors.push('标题不能为空');
    else if (title.length > MATERIAL_TITLE_MAX) errors.push('标题超过 ' + MATERIAL_TITLE_MAX + ' 字');
    const body = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    if (!body.trim()) errors.push('正文不能为空');
    if (body.indexOf('\u0000') >= 0) errors.push('正文里不能有控制字符');
    if (body.length > MATERIAL_TEXT_MAX) errors.push('正文超过 ' + MATERIAL_TEXT_MAX + ' 字');
    if (!origin) errors.push('来源声明不能为空（这是声明，不是认证）');
    else if (origin.length > MATERIAL_ORIGIN_MAX) errors.push('来源声明超过 ' + MATERIAL_ORIGIN_MAX + ' 字');
    if (MATERIAL_SOURCE_KINDS.indexOf(kind) < 0) errors.push('要选文本类型：他给的文本 / 明确是虚构');
    return {
      ok: errors.length === 0,
      errors: errors,
      value: {title: title.slice(0, MATERIAL_TITLE_MAX), text: body,
        origin: origin.slice(0, MATERIAL_ORIGIN_MAX),
        source_kind: MATERIAL_SOURCE_KINDS.indexOf(kind) >= 0 ? kind : ''}
    };
  }

  function buildMaterialImportPayload(input) {
    const source = asRecord(input) || {};
    const payload = {
      title: typeof source.title === 'string' ? source.title.trim() : '',
      text: typeof source.text === 'string' ? source.text : '',
      source_kind: typeof source.source_kind === 'string' ? source.source_kind : '',
      origin: typeof source.origin === 'string' ? source.origin.trim() : '',
      operation_key: typeof source.operation_key === 'string' ? source.operation_key.trim() : ''
    };
    if (!payload.title || !payload.text.trim() || !payload.origin) return null;
    if (!payload.operation_key) return null;
    if (MATERIAL_SOURCE_KINDS.indexOf(payload.source_kind) < 0) return null;
    return payload;
  }

  function materialPath(artifactId) {
    const id = protocolId(artifactId, 200);
    if (!id.ok || !/^[A-Za-z0-9._:\-]+$/.test(id.value)) return '';
    return '/materials/' + id.value;
  }

  function materialsListPath(input) {
    const source = asRecord(input) || {};
    const query = typeof source.query === 'string' ? source.query.trim() : '';
    const limit = source.limit;
    const parts = [];
    if (query) {
      if (query.length > MATERIAL_QUERY_MAX) return '';
      parts.push('query=' + encodeURIComponent(query));
    }
    if (typeof limit === 'number' && isFinite(limit) && limit >= 1) {
      parts.push('limit=' + Math.floor(limit));
    }
    return '/materials' + (parts.length ? '?' + parts.join('&') : '');
  }

  function sourceCheckView(raw) {
    const record = asRecord(raw);
    if (!record) return null;
    const id = protocolId(record.artifact_id, 200);
    if (!id.ok) return null;
    const status = typeof record.status === 'string' ? record.status : 'UNAVAILABLE';
    return {
      artifactId: id.value,
      sourceRevision: typeof record.source_revision === 'string' ? record.source_revision : '',
      currentRevision: typeof record.current_source_revision === 'string'
        ? record.current_source_revision : '',
      status: status,
      statusText: CHECK_STATUS_TEXT[status] || status,
      reason: typeof record.reason_code === 'string' ? record.reason_code : '',
      title: safeLabel(record.title, MATERIAL_TITLE_MAX) || ''
    };
  }

  function researchRowView(raw) {
    const record = asRecord(raw);
    if (!record) return null;
    const id = protocolId(record.artifact_id, 200);
    if (!id.ok) return null;
    return {
      id: id.value,
      title: safeLabel(record.title, MATERIAL_TITLE_MAX) || '未命名研究',
      question: sanitizeLine(record.question, RESEARCH_QUESTION_MAX) || '',
      scope: sanitizeLine(record.scope, RESEARCH_SCOPE_MAX) || '',
      uncertainty: sanitizeLine(record.uncertainty, RESEARCH_UNCERTAINTY_MAX) || '',
      sourceCount: typeof record.source_count === 'number' ? record.source_count : null,
      lifecycle: typeof record.lifecycle === 'string' ? record.lifecycle : '',
      status: record.status === 'archived' ? 'archived' : 'active',
      revision: typeof record.revision === 'number' ? record.revision : null,
      head: typeof record.head_version_id === 'string' ? record.head_version_id : '',
      updatedAt: record.updated_at
    };
  }

  function researchListView(doc) {
    const record = asRecord(doc) || {};
    const data = asRecord(record.data) || record;
    const raw = Array.isArray(data.projects) ? data.projects : null;
    const rows = [];
    (raw || []).forEach(function (item) {
      const row = researchRowView(item);
      if (row) rows.push(row);
    });
    return {
      itemsMissing: raw === null,
      projects: rows,
      count: typeof data.count === 'number' ? data.count : rows.length,
      truncated: data.truncated === true,
      note: typeof data.note === 'string' ? sanitizeLine(data.note, 200) : ''
    };
  }

  function researchDetailView(doc) {
    const record = asRecord(doc) || {};
    const data = asRecord(record.data) || record;
    const raw = asRecord(data.project);
    const project = researchRowView(raw);
    if (!project) return null;
    const checks = [];
    const sourceChecks = Array.isArray(raw.source_checks) ? raw.source_checks
      : (Array.isArray(data.source_checks) ? data.source_checks : []);
    sourceChecks.forEach(function (item) {
      const row = sourceCheckView(item);
      if (row) checks.push(row);
    });
    const refs = [];
    (Array.isArray(raw.source_refs) ? raw.source_refs : []).forEach(function (item) {
      const ref = asRecord(item);
      if (!ref) return;
      const id = protocolId(ref.artifact_id, 200);
      if (!id.ok) return;
      refs.push({artifactId: id.value,
        sourceRevision: typeof ref.source_revision === 'string' ? ref.source_revision : ''});
    });
    return {
      project: project,
      content: typeof raw.content === 'string' ? raw.content : '',
      contentAvailable: raw.content_available !== false,
      body: typeof raw.body === 'string' ? raw.body : '',
      question: sanitizeLine(raw.question, RESEARCH_QUESTION_MAX) || project.question,
      scope: sanitizeLine(raw.scope, RESEARCH_SCOPE_MAX) || '',
      uncertainty: sanitizeLine(raw.uncertainty, RESEARCH_UNCERTAINTY_MAX) || '',
      refs: refs,
      checks: checks,
      sourcesCurrent: raw.sources_current === true,
      notMemory: raw.not_memory !== false,
      stableBelief: raw.stable_belief === true,
      note: typeof raw.note === 'string' ? sanitizeLine(raw.note, 200) : ''
    };
  }

  function validateResearchDraft(draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    const question = typeof source.question === 'string' ? source.question.trim() : '';
    const scope = typeof source.scope === 'string' ? source.scope.trim() : '';
    const uncertainty = typeof source.uncertainty === 'string' ? source.uncertainty.trim() : '';
    const refs = Array.isArray(source.source_refs) ? source.source_refs : [];
    if (!question) errors.push('问题不能为空');
    else if (question.length > RESEARCH_QUESTION_MAX) errors.push('问题超过 ' + RESEARCH_QUESTION_MAX + ' 字');
    if (scope.length > RESEARCH_SCOPE_MAX) errors.push('范围超过 ' + RESEARCH_SCOPE_MAX + ' 字');
    if (uncertainty.length > RESEARCH_UNCERTAINTY_MAX) errors.push('未知超过 ' + RESEARCH_UNCERTAINTY_MAX + ' 字');
    if (!refs.length) errors.push('至少要选一件具体资料作为来源');
    else if (refs.length > RESEARCH_SOURCES_MAX) errors.push('来源最多 ' + RESEARCH_SOURCES_MAX + ' 件');
    return {
      ok: errors.length === 0,
      errors: errors,
      value: {question: question.slice(0, RESEARCH_QUESTION_MAX),
        scope: scope.slice(0, RESEARCH_SCOPE_MAX),
        uncertainty: (uncertainty || RESEARCH_DEFAULT_UNCERTAINTY).slice(0, RESEARCH_UNCERTAINTY_MAX),
        source_refs: refs}
    };
  }

  function validateResearchUpdateDraft(draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    const content = typeof source.content === 'string' ? source.content : '';
    const text = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    if (!text.trim()) errors.push('正文不能为空');
    if (text.indexOf('\u0000') >= 0) errors.push('正文里不能有控制字符');
    if (text.length > RESEARCH_CONTENT_MAX) errors.push('正文超过 ' + RESEARCH_CONTENT_MAX + ' 字');
    const scope = typeof source.scope === 'string' ? source.scope.trim() : '';
    const uncertainty = typeof source.uncertainty === 'string' ? source.uncertainty.trim() : '';
    if (scope.length > RESEARCH_SCOPE_MAX) errors.push('范围超过 ' + RESEARCH_SCOPE_MAX + ' 字');
    if (uncertainty.length > RESEARCH_UNCERTAINTY_MAX) errors.push('未知超过 ' + RESEARCH_UNCERTAINTY_MAX + ' 字');
    return {ok: errors.length === 0, errors: errors,
      value: {content: text, scope: scope, uncertainty: uncertainty}};
  }

  function buildResearchCreatePayload(input) {
    const source = asRecord(input) || {};
    const question = typeof source.question === 'string' ? source.question.trim() : '';
    const key = typeof source.operation_key === 'string' ? source.operation_key.trim() : '';
    const refs = Array.isArray(source.source_refs) ? source.source_refs : [];
    if (!question || !key || !refs.length) return null;
    const payload = {question: question, source_refs: refs, operation_key: key};
    const title = typeof source.title === 'string' ? source.title.trim() : '';
    if (title) payload.title = title;
    const scope = typeof source.scope === 'string' ? source.scope.trim() : '';
    if (scope) payload.scope = scope;
    const uncertainty = typeof source.uncertainty === 'string' ? source.uncertainty.trim() : '';
    if (uncertainty) payload.uncertainty = uncertainty;
    return payload;
  }

  function buildResearchUpdatePayload(input) {
    const source = asRecord(input) || {};
    const payload = {
      content: typeof source.content === 'string' ? source.content : '',
      expected_head: typeof source.expected_head === 'string' ? source.expected_head : '',
      operation_key: typeof source.operation_key === 'string' ? source.operation_key.trim() : ''
    };
    /* 没有 head 就不发：那等于关掉 CAS，宁可不写。 */
    if (!payload.content.trim() || !payload.expected_head || !payload.operation_key) return null;
    if (typeof source.scope === 'string' && source.scope.trim()) payload.scope = source.scope.trim();
    if (typeof source.uncertainty === 'string' && source.uncertainty.trim()) {
      payload.uncertainty = source.uncertainty.trim();
    }
    return payload;
  }

  function buildResearchClosePayload(input) {
    const source = asRecord(input) || {};
    const revision = source.expected_revision;
    const key = typeof source.operation_key === 'string' ? source.operation_key.trim() : '';
    if (typeof revision !== 'number' || !isFinite(revision) || revision < 1 || !key) return null;
    return {expected_revision: Math.floor(revision), operation_key: key};
  }

  function researchPath(artifactId) {
    const id = protocolId(artifactId, 200);
    if (!id.ok || !/^[A-Za-z0-9._:\-]+$/.test(id.value)) return '';
    return '/research/' + id.value;
  }

  function researchUpdatesPath(artifactId) {
    const base = researchPath(artifactId);
    return base ? base + '/updates' : '';
  }

  function researchClosePath(artifactId) {
    const base = researchPath(artifactId);
    return base ? base + '/close' : '';
  }

  /* 个人实践工作台 + Memory（B 的 personal_practice_service / A 的 LifeMemoryService 消费端）
   *
   * 契约：docs/engineering/v04-practice-workspace-result.md
   *   GET  /api/phone/memory                            绑定状态（纯读，绝不建库）
   *   GET  /api/phone/memory/experiences?query=&limit=  她能召回的经历
   *   GET  /api/phone/memory/experiences/<id>           一条经历（A 的治理决定它是否还可读）
   *   POST /api/phone/memory/enable                     显式启用（owner 由服务配置决定）
   *   GET  /api/phone/practice                          回顾 + 做法 + Memory 状态
   *   GET  /api/phone/practice/reviews/<id>             正文 + 每条经历现在的可读性
   *   POST /api/phone/practice/reviews                  {question, title, experience_ids, operation_key}
   *   GET  /api/phone/practice/methods?query=&limit=    做法列表（不含正文）
   *   GET  /api/phone/practice/methods/<id>             做法 + 状态 + 全部证据核对
   *   POST /api/phone/practice/methods                  {title, instruction, scope, uncertainty, …}
   *   POST .../practice/reviews|methods/<id>/…          documents / trials / revisions / retire
   *
   * 铁律：回顾与做法仍是 Supply 的 ManagedArtifact；做法草案永远是 TENTATIVE，界面不替它
   *       宣布「已验证」；证据读不回来时不显示正文，只显示元数据与核对结果。
   */
  const PRACTICE_TITLE_MAX = 120;
  const PRACTICE_QUESTION_MAX = 500;
  const PRACTICE_INSTRUCTION_MAX = 2000;
  const PRACTICE_SCOPE_MAX = 400;
  const PRACTICE_UNCERTAINTY_MAX = 400;
  const PRACTICE_CONTENT_MAX = 18000;
  const PRACTICE_TRIAL_DETAIL_MAX = 600;
  const PRACTICE_REFS_MAX = 12;
  const PRACTICE_LIST_LIMIT = 12;
  const PRACTICE_QUERY_MAX = 120;
  const PRACTICE_DEFAULT_UNCERTAINTY = '暂定，尚未验证';
  const PRACTICE_METHOD_STATES = ['TENTATIVE', 'TRIAL', 'CONTESTED', 'RETIRED'];
  const PRACTICE_TRIAL_OUTCOMES = ['HELPED', 'NOT_HELPED', 'UNKNOWN'];
  const PRACTICE_STATE_TEXT = {TENTATIVE: '草案（未验证）', TRIAL: '试过（仍未验证）',
    CONTESTED: '有反例', RETIRED: '已退休'};
  const PRACTICE_OUTCOME_TEXT = {HELPED: '这次有用', NOT_HELPED: '这次没用', UNKNOWN: '说不好'};
  const PRACTICE_CHECK_TEXT = {CURRENT: '现在仍可读', UNAVAILABLE: '读不到/已撤回'};
  const PRACTICE_SCOPES = {reviewCreate: 'practice:review', document: 'practice:document',
    methodCreate: 'practice:method', trial: 'practice:trial', revise: 'practice:revise',
    retire: 'practice:retire', memoryEnable: 'memory:enable'};

  function practiceIdText(value) {
    const id = protocolId(value, 200);
    return id.ok ? id.value : '';
  }

  function practiceKindText(kind) {
    if (kind === 'review') return '回顾';
    if (kind === 'method') return '做法';
    return '未知';
  }

  function practiceStateText(state) {
    return PRACTICE_STATE_TEXT[state] || '状态未提供';
  }

  function practiceRowView(raw) {
    const record = asRecord(raw);
    if (!record) return null;
    const id = practiceIdText(record.artifact_id);
    if (!id) return null;
    const kind = typeof record.kind === 'string' ? record.kind : '';
    const state = typeof record.method_state === 'string' ? record.method_state : '';
    return {
      id: id,
      kind: kind,
      kindText: practiceKindText(kind),
      title: safeLabel(record.title, PRACTICE_TITLE_MAX) || '未命名',
      question: sanitizeText(record.question, PRACTICE_QUESTION_MAX) || '',
      instruction: sanitizeText(record.instruction, PRACTICE_INSTRUCTION_MAX) || '',
      scope: sanitizeLine(record.scope, PRACTICE_SCOPE_MAX) || '',
      uncertainty: sanitizeLine(record.uncertainty, PRACTICE_UNCERTAINTY_MAX) || '',
      methodState: state,
      stateText: state ? practiceStateText(state) : '',
      experienceCount: firstNumber(record, ['experience_count']) || 0,
      reviewCount: firstNumber(record, ['review_count']) || 0,
      trialCount: firstNumber(record, ['trial_count']) || 0,
      counterexampleCount: firstNumber(record, ['counterexample_count']) || 0,
      revisions: firstNumber(record, ['revisions']) || 0,
      revision: typeof record.revision === 'number' ? record.revision : null,
      head: typeof record.head_version_id === 'string' ? record.head_version_id : '',
      versionNumber: typeof record.version_number === 'number' ? record.version_number : null,
      lifecycle: typeof record.lifecycle === 'string' ? record.lifecycle : '',
      status: record.status === 'archived' ? 'archived' : 'active',
      updatedAt: record.updated_at,
      verifiedSkill: record.verified_skill === true
    };
  }

  function practiceListView(doc) {
    const record = asRecord(doc) || {};
    const data = asRecord(record.data) || record;
    const rawReviews = Array.isArray(data.reviews) ? data.reviews : null;
    const rawMethods = Array.isArray(data.methods) ? data.methods : null;
    const reviews = [];
    const methods = [];
    (rawReviews || []).forEach(function (item) {
      const row = practiceRowView(item);
      if (row) reviews.push(row);
    });
    (rawMethods || []).forEach(function (item) {
      const row = practiceRowView(item);
      if (row) methods.push(row);
    });
    const memory = memoryStatusView({ok: true, data: asRecord(data.memory) || {}});
    return {
      itemsMissing: rawReviews === null || rawMethods === null,
      reviews: reviews,
      methods: methods,
      reviewTruncated: data.review_truncated === true,
      methodTruncated: data.method_truncated === true,
      reviewStatus: typeof data.review_status === 'string' ? data.review_status : '',
      methodStatus: typeof data.method_status === 'string' ? data.method_status : '',
      memory: memory,
      memoryStatus: typeof data.memory_status === 'string' ? data.memory_status : '',
      notVerifiedSkill: data.not_verified_skill !== false
    };
  }

  function practiceCheckView(raw) {
    const record = asRecord(raw);
    if (!record) return null;
    const kind = record.kind === 'review' ? 'review' : 'experience';
    const label = kind === 'review'
      ? practiceIdText(record.artifact_id)
      : practiceIdText(record.experience_id);
    if (!label) return null;
    const status = typeof record.status === 'string' ? record.status : 'UNAVAILABLE';
    return {
      kind: kind,
      kindText: kind === 'review' ? '被引用的回顾' : '经历',
      id: label,
      status: status,
      statusText: PRACTICE_CHECK_TEXT[status] || status,
      usable: record.usable === true,
      reason: typeof record.reason_code === 'string' ? record.reason_code : '',
      detail: sanitizeLine(record.detail, 200) || '',
      title: safeLabel(record.title, PRACTICE_TITLE_MAX) || '',
      excerpt: sanitizeText(record.excerpt, 240) || '',
      occurredAt: typeof record.occurred_at === 'string' ? record.occurred_at : ''
    };
  }

  function practiceTrialView(raw) {
    const record = asRecord(raw);
    if (!record) return null;
    const outcome = typeof record.outcome === 'string' ? record.outcome : '';
    if (PRACTICE_TRIAL_OUTCOMES.indexOf(outcome) < 0) return null;
    return {
      outcome: outcome,
      outcomeText: PRACTICE_OUTCOME_TEXT[outcome] || outcome,
      detail: sanitizeText(record.detail, PRACTICE_TRIAL_DETAIL_MAX) || '',
      versionId: typeof record.version_id === 'string' ? record.version_id : '',
      evidenceUsable: record.evidence_usable === true,
      counterexample: outcome === 'NOT_HELPED'
    };
  }

  function practiceDetailView(doc) {
    const record = asRecord(doc) || {};
    const data = asRecord(record.data) || record;
    const project = practiceRowView(asRecord(data.project));
    if (!project) return null;
    const governance = asRecord(data.governance) || {};
    const rawChecks = Array.isArray(data.experience_checks) ? data.experience_checks
      : (Array.isArray(data.evidence_checks) ? data.evidence_checks : []);
    const checks = [];
    rawChecks.forEach(function (item) {
      const view = practiceCheckView(item);
      if (view) checks.push(view);
    });
    const trials = [];
    (Array.isArray(data.trials) ? data.trials : []).forEach(function (item) {
      const view = practiceTrialView(item);
      if (view) trials.push(view);
    });
    const kind = typeof data.kind === 'string' ? data.kind : project.kind;
    const experienceIds = [];
    (Array.isArray(data.experience_ids) ? data.experience_ids : []).forEach(function (item) {
      const id = practiceIdText(item);
      if (id && experienceIds.indexOf(id) < 0) experienceIds.push(id);
    });
    const reviewRefs = [];
    (Array.isArray(data.review_refs) ? data.review_refs : []).forEach(function (item) {
      const id = practiceIdText(item);
      if (id && reviewRefs.indexOf(id) < 0) reviewRefs.push(id);
    });
    const state = typeof data.method_state === 'string' ? data.method_state : project.methodState;
    return {
      project: project,
      kind: kind,
      kindText: practiceKindText(kind),
      body: typeof data.body === 'string' ? data.body : '',
      content: typeof data.content === 'string' ? data.content : '',
      contentAvailable: data.content_available !== false,
      blocked: governance.blocked === true,
      blockedReason: typeof governance.reason_code === 'string' ? governance.reason_code : '',
      blockedDetail: sanitizeLine(governance.detail, 240) || '',
      question: sanitizeText(data.question, PRACTICE_QUESTION_MAX) || '',
      instruction: sanitizeText(data.instruction, PRACTICE_INSTRUCTION_MAX) || '',
      scope: sanitizeLine(data.scope, PRACTICE_SCOPE_MAX) || '',
      scopeMissing: data.scope_missing === true,
      uncertainty: sanitizeLine(data.uncertainty, PRACTICE_UNCERTAINTY_MAX) || '',
      methodState: state,
      stateText: state ? practiceStateText(state) : '',
      checks: checks,
      unusableCount: checks.filter(function (check) { return !check.usable; }).length,
      experienceIds: experienceIds,
      reviewRefs: reviewRefs,
      trials: trials,
      counterexamples: trials.filter(function (trial) { return trial.counterexample; }),
      revisions: firstNumber(data, ['revisions']) || 0,
      verifiedSkill: data.verified_skill === true,
      stableBelief: data.stable_belief === true,
      note: sanitizeLine(data.note, 240) || ''
    };
  }

  function experienceRowView(raw) {
    const record = asRecord(raw);
    if (!record) return null;
    const id = practiceIdText(record.experience_id);
    if (!id) return null;
    return {
      id: id,
      occurredAt: typeof record.occurred_at === 'string' ? record.occurred_at : '',
      text: sanitizeText(record.text, 1200) || '',
      excerpt: sanitizeText(record.text, 160) || '',
      outcomeKind: typeof record.outcome_kind === 'string' ? record.outcome_kind : '',
      owner: safeLabel(record.owner, 40) || '',
      sourceRef: safeLabel(record.source_ref, 200) || '',
      activityRef: safeLabel(record.activity_ref, 200) || '',
      artifactRef: safeLabel(record.artifact_ref, 200) || '',
      versionRef: safeLabel(record.version_ref, 200) || ''
    };
  }

  function experienceListView(doc) {
    const record = asRecord(doc) || {};
    const data = asRecord(record.data) || record;
    const raw = Array.isArray(data.experiences) ? data.experiences : null;
    const rows = [];
    (raw || []).forEach(function (item) {
      const row = experienceRowView(item);
      if (row) rows.push(row);
    });
    const count = firstNumber(data, ['count']);
    return {
      itemsMissing: raw === null,
      experiences: rows,
      count: count === null ? rows.length : count,
      query: typeof data.query === 'string' ? data.query : '',
      bindingStatus: typeof data.binding_status === 'string' ? data.binding_status : '',
      owner: safeLabel(data.owner, 40) || ''
    };
  }

  function experienceDetailView(doc) {
    const record = asRecord(doc) || {};
    const data = asRecord(record.data) || record;
    const experience = experienceRowView(asRecord(data.experience));
    if (!experience) return null;
    return {
      experience: experience,
      bindingStatus: typeof data.binding_status === 'string' ? data.binding_status : '',
      owner: safeLabel(data.owner, 40) || '',
      experienceId: typeof data.experience_id === 'string' ? data.experience_id : ''
    };
  }

  function memoryStatusView(doc) {
    const record = asRecord(doc) || {};
    const data = asRecord(record.data) || record;
    const binding = typeof data.binding_status === 'string' && data.binding_status
      ? data.binding_status
      : (data.installed === false ? 'UNBOUND' : '');
    const text = {READY: '已绑定（只读可召回）', UNBOUND: '未绑定 Memory',
      BLOCKED: '绑定但不可用', UNAVAILABLE: '读不到绑定状态'}[binding] || '状态未提供';
    return {
      ok: record.ok === true || binding === 'READY',
      bindingStatus: binding,
      statusText: text,
      recallReady: data.recall_ready === true,
      installed: data.installed === true || binding !== '',
      owner: safeLabel(data.owner, 40) || '',
      reason: typeof data.reason === 'string' ? data.reason
        : (typeof record.reason_code === 'string' ? record.reason_code : ''),
      participation: typeof data.participation === 'string' ? data.participation : '',
      database: typeof data.database === 'string' ? data.database : '',
      controlPresent: typeof data.controls_database === 'string' && data.controls_database !== '',
      enableAvailable: data.enable_available !== false,
      restartRequired: data.restart_required === true,
      note: '启用只在这一个实例自己的 memory 目录里发生；不会自动重启任何服务。'
    };
  }

  function normalizePracticeText(value, limit) {
    const text = typeof value === 'string' ? value : '';
    const body = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    return {body: body, tooLong: body.length > limit, nul: body.indexOf('\u0000') >= 0};
  }

  function practiceRefList(value, limit) {
    const source = Array.isArray(value) ? value : [];
    const rows = [];
    let invalid = 0;
    source.forEach(function (item) {
      const id = practiceIdText(item);
      if (!id) {
        invalid += 1;
        return;
      }
      if (rows.indexOf(id) < 0) rows.push(id);
    });
    return {rows: rows, invalid: invalid, tooMany: rows.length > limit};
  }

  function validatePracticeReviewDraft(draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    const question = typeof source.question === 'string' ? source.question.trim() : '';
    const title = typeof source.title === 'string' ? source.title.trim() : '';
    if (!question) errors.push('问题不能为空');
    else if (question.length > PRACTICE_QUESTION_MAX) {
      errors.push('问题超过 ' + PRACTICE_QUESTION_MAX + ' 字');
    }
    if (title && title.length > PRACTICE_TITLE_MAX) {
      errors.push('标题超过 ' + PRACTICE_TITLE_MAX + ' 字');
    }
    const refs = practiceRefList(source.experience_ids, PRACTICE_REFS_MAX);
    if (refs.tooMany) errors.push('引用的经历最多 ' + PRACTICE_REFS_MAX + ' 条');
    return {
      ok: errors.length === 0,
      errors: errors,
      value: {question: question.slice(0, PRACTICE_QUESTION_MAX),
        title: title.slice(0, PRACTICE_TITLE_MAX), experience_ids: refs.rows}
    };
  }

  function validatePracticeMethodDraft(draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    const title = typeof source.title === 'string' ? source.title.trim() : '';
    const instruction = normalizePracticeText(source.instruction, PRACTICE_INSTRUCTION_MAX);
    const scope = typeof source.scope === 'string' ? source.scope.trim() : '';
    const uncertainty = typeof source.uncertainty === 'string' ? source.uncertainty.trim() : '';
    if (!title) errors.push('标题不能为空');
    else if (title.length > PRACTICE_TITLE_MAX) errors.push('标题超过 ' + PRACTICE_TITLE_MAX + ' 字');
    if (!instruction.body.trim()) errors.push('具体做法不能为空');
    if (instruction.tooLong) errors.push('具体做法超过 ' + PRACTICE_INSTRUCTION_MAX + ' 字');
    if (instruction.nul) errors.push('正文里不能有控制字符');
    if (scope.length > PRACTICE_SCOPE_MAX) errors.push('范围超过 ' + PRACTICE_SCOPE_MAX + ' 字');
    if (uncertainty.length > PRACTICE_UNCERTAINTY_MAX) {
      errors.push('未知超过 ' + PRACTICE_UNCERTAINTY_MAX + ' 字');
    }
    const experiences = practiceRefList(source.experience_ids, PRACTICE_REFS_MAX);
    const reviews = practiceRefList(source.review_refs, PRACTICE_REFS_MAX);
    if (experiences.tooMany || reviews.tooMany) {
      errors.push('引用最多 ' + PRACTICE_REFS_MAX + ' 条');
    }
    return {
      ok: errors.length === 0,
      errors: errors,
      value: {title: title.slice(0, PRACTICE_TITLE_MAX),
        instruction: instruction.body.trim(), scope: scope.slice(0, PRACTICE_SCOPE_MAX),
        uncertainty: (uncertainty || PRACTICE_DEFAULT_UNCERTAINTY).slice(0, PRACTICE_UNCERTAINTY_MAX),
        experience_ids: experiences.rows, review_refs: reviews.rows}
    };
  }

  function validatePracticeDocumentDraft(draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    const body = normalizePracticeText(source.content, PRACTICE_CONTENT_MAX);
    if (!body.body.trim()) errors.push('正文不能为空');
    if (body.tooLong) errors.push('正文超过 ' + PRACTICE_CONTENT_MAX + ' 字');
    if (body.nul) errors.push('正文里不能有控制字符');
    /* 没有 head 就不发：那等于关掉 CAS，宁可不写。 */
    const head = typeof source.expected_head === 'string' ? source.expected_head.trim() : '';
    if (!head) errors.push('没有读到 head，这次修改不能保存（会绕过冲突保护）');
    return {ok: errors.length === 0, errors: errors,
      value: {content: body.body, expected_head: head}};
  }

  function validatePracticeTrialDraft(draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    const outcome = typeof source.outcome === 'string' ? source.outcome.toUpperCase() : '';
    const detail = normalizePracticeText(source.detail, PRACTICE_TRIAL_DETAIL_MAX);
    if (PRACTICE_TRIAL_OUTCOMES.indexOf(outcome) < 0) {
      errors.push('要选一个结果：有用 / 没用 / 说不好');
    }
    if (detail.tooLong) errors.push('说明超过 ' + PRACTICE_TRIAL_DETAIL_MAX + ' 字');
    if (detail.nul) errors.push('说明里不能有控制字符');
    const refs = practiceRefList(source.experience_ids, PRACTICE_REFS_MAX);
    const head = typeof source.expected_head === 'string' ? source.expected_head.trim() : '';
    if (outcome && outcome !== 'UNKNOWN' && !refs.rows.length) {
      errors.push('没有可读证据时只能记「说不好」——先选一条真实经历');
    }
    if (!head) errors.push('没有读到 head，这次评价不能保存');
    return {ok: errors.length === 0, errors: errors,
      value: {outcome: outcome, detail: detail.body.trim(),
        experience_ids: refs.rows, expected_head: head}};
  }

  function validatePracticeRevisionDraft(draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    const instruction = normalizePracticeText(source.instruction, PRACTICE_INSTRUCTION_MAX);
    const scope = typeof source.scope === 'string' ? source.scope.trim() : '';
    const uncertainty = typeof source.uncertainty === 'string' ? source.uncertainty.trim() : '';
    const head = typeof source.expected_head === 'string' ? source.expected_head.trim() : '';
    if (!instruction.body.trim()) errors.push('具体做法不能为空');
    if (instruction.tooLong) errors.push('具体做法超过 ' + PRACTICE_INSTRUCTION_MAX + ' 字');
    if (scope.length > PRACTICE_SCOPE_MAX) errors.push('范围超过 ' + PRACTICE_SCOPE_MAX + ' 字');
    if (uncertainty.length > PRACTICE_UNCERTAINTY_MAX) {
      errors.push('未知超过 ' + PRACTICE_UNCERTAINTY_MAX + ' 字');
    }
    if (!head) errors.push('没有读到 head，这次修订不能保存');
    return {ok: errors.length === 0, errors: errors,
      value: {instruction: instruction.body.trim(), scope: scope.slice(0, PRACTICE_SCOPE_MAX),
        uncertainty: (uncertainty || PRACTICE_DEFAULT_UNCERTAINTY)
          .slice(0, PRACTICE_UNCERTAINTY_MAX), expected_head: head}};
  }

  function buildPracticeReviewCreatePayload(input) {
    const checked = validatePracticeReviewDraft(input);
    const source = asRecord(input) || {};
    const key = typeof source.operation_key === 'string' ? source.operation_key.trim() : '';
    if (!checked.ok || !key) return null;
    const payload = {question: checked.value.question,
      experience_ids: checked.value.experience_ids, operation_key: key};
    if (checked.value.title) payload.title = checked.value.title;
    return payload;
  }

  function buildPracticeMethodCreatePayload(input) {
    const checked = validatePracticeMethodDraft(input);
    const source = asRecord(input) || {};
    const key = typeof source.operation_key === 'string' ? source.operation_key.trim() : '';
    if (!checked.ok || !key) return null;
    return {title: checked.value.title, instruction: checked.value.instruction,
      scope: checked.value.scope, uncertainty: checked.value.uncertainty,
      experience_ids: checked.value.experience_ids,
      review_refs: checked.value.review_refs, operation_key: key};
  }

  function buildPracticeDocumentPayload(input) {
    const checked = validatePracticeDocumentDraft(input);
    const source = asRecord(input) || {};
    const key = typeof source.operation_key === 'string' ? source.operation_key.trim() : '';
    if (!checked.ok || !key) return null;
    return {content: checked.value.content, expected_head: checked.value.expected_head,
      operation_key: key};
  }

  function buildPracticeTrialPayload(input) {
    const checked = validatePracticeTrialDraft(input);
    const source = asRecord(input) || {};
    const key = typeof source.operation_key === 'string' ? source.operation_key.trim() : '';
    if (!checked.ok || !key) return null;
    return {outcome: checked.value.outcome, detail: checked.value.detail,
      experience_ids: checked.value.experience_ids,
      expected_head: checked.value.expected_head, operation_key: key};
  }

  function buildPracticeRevisionPayload(input) {
    const checked = validatePracticeRevisionDraft(input);
    const source = asRecord(input) || {};
    const key = typeof source.operation_key === 'string' ? source.operation_key.trim() : '';
    if (!checked.ok || !key) return null;
    return {instruction: checked.value.instruction, scope: checked.value.scope,
      uncertainty: checked.value.uncertainty, expected_head: checked.value.expected_head,
      operation_key: key};
  }

  function buildPracticeRetirePayload(input) {
    const source = asRecord(input) || {};
    const head = typeof source.expected_head === 'string' ? source.expected_head.trim() : '';
    const key = typeof source.operation_key === 'string' ? source.operation_key.trim() : '';
    if (!head || !key) return null;
    return {expected_head: head, operation_key: key};
  }

  function buildMemoryEnablePayload(input) {
    const source = asRecord(input) || {};
    const key = typeof source.operation_key === 'string' ? source.operation_key.trim() : '';
    if (!key) return null;
    return {operation_key: key};
  }

  function practiceReviewPath(artifactId) {
    const id = practiceIdText(artifactId);
    if (!id || !/^[A-Za-z0-9._:\-]+$/.test(id)) return '';
    return '/practice/reviews/' + id;
  }

  function practiceReviewDocumentsPath(artifactId) {
    const base = practiceReviewPath(artifactId);
    return base ? base + '/documents' : '';
  }

  function practiceMethodPath(artifactId) {
    const id = practiceIdText(artifactId);
    if (!id || !/^[A-Za-z0-9._:\-]+$/.test(id)) return '';
    return '/practice/methods/' + id;
  }

  function practiceMethodTrialsPath(artifactId) {
    const base = practiceMethodPath(artifactId);
    return base ? base + '/trials' : '';
  }

  function practiceMethodRevisionsPath(artifactId) {
    const base = practiceMethodPath(artifactId);
    return base ? base + '/revisions' : '';
  }

  function practiceMethodRetirePath(artifactId) {
    const base = practiceMethodPath(artifactId);
    return base ? base + '/retire' : '';
  }

  function practiceMethodsPath(input) {
    const source = asRecord(input) || {};
    const query = typeof source.query === 'string' ? source.query.trim() : '';
    const parts = [];
    if (query) {
      if (query.length > PRACTICE_QUERY_MAX) return '';
      parts.push('query=' + encodeURIComponent(query));
    }
    if (typeof source.limit === 'number' && isFinite(source.limit) && source.limit >= 1) {
      parts.push('limit=' + Math.floor(source.limit));
    }
    return '/practice/methods' + (parts.length ? '?' + parts.join('&') : '');
  }

  function memoryExperiencesPath(input) {
    const source = asRecord(input) || {};
    const query = typeof source.query === 'string' ? source.query.trim() : '';
    const parts = [];
    if (query) {
      if (query.length > PRACTICE_QUERY_MAX) return '';
      parts.push('query=' + encodeURIComponent(query));
    }
    if (typeof source.limit === 'number' && isFinite(source.limit) && source.limit >= 1) {
      parts.push('limit=' + Math.floor(source.limit));
    }
    return '/memory/experiences' + (parts.length ? '?' + parts.join('&') : '');
  }

  function memoryExperiencePath(experienceId) {
    const id = practiceIdText(experienceId);
    if (!id || !/^[A-Za-z0-9._:\-]+$/.test(id)) return '';
    return '/memory/experiences/' + id;
  }

  /* ---------------- 分享的词表与边界（B 的 LifeSharingService） ---------------- */

  const SHARE_STATES = ['DRAFT', 'DEFERRED', 'DROPPED', 'EXPIRED', 'LOCAL_DELIVERED'];
  const SHARE_STATE_TEXT = {
    DRAFT: '草稿', DEFERRED: '已延后', DROPPED: '已放弃', EXPIRED: '已过期',
    LOCAL_DELIVERED: '本地收件箱'
  };
  const SHARE_ACTION_VALUES = ['send_local', 'defer', 'drop'];
  const SHARE_ACTION_TEXT = {send_local: '放进本地收件箱', defer: '延后', drop: '放弃'};
  /* 手机只做这三件事；expire 是 owner 侧的事实（读面推导），界面不替她过期。 */
  const SHARE_SCOPES = ['share'];
  const SHARE_SOURCE_KIND_TEXT = {project: '作品', reading: '阅读材料'};
  const SHARE_TEXT_MAX = 2000;
  const SHARE_EXCERPT_MAX = 400;
  const SHARE_TITLE_MAX = 80;
  const SHARE_CONDITION_MAX = 200;
  const SHARE_REASON_MAX = 200;
  const SHARE_EXPIRY_MIN = 60;
  const SHARE_EXPIRY_MAX = 2592000;
  const SHARE_LOCAL_NOTE = '本实例本地存档，未向外部发送、未证明用户已读';

  /* ---------------- 来访与共同时间（A 的 LocalContactInbox；P 只投递） ----------------

     铁律：
       * 三条路径：普通来话（只观察）、明确范围的邀请、显式结束当前邀请。
       * 「已记下」不等于「她答应了」：queued = PENDING，接纳与否由她自己的协议决定。
       * 界面只翻译服务给的字面量；不发明第四个状态。
       * 正文只进内存与 textContent：不拼 HTML、不进任何 storage。 */

  const CONTACT_PATH = '/contact';
  const CONTACT_EVENTS_PATH = '/contact/events';
  const CONTACT_KINDS_PHONE = ['LOCAL_MESSAGE', 'CO_PRESENCE_INVITATION', 'CO_PRESENCE_END'];
  const CONTACT_KIND_TEXT = {
    LOCAL_MESSAGE: '普通来话',
    CO_PRESENCE_INVITATION: '明确邀请',
    CO_PRESENCE_END: '显式结束'
  };
  /* A 的 effect 词表：普通来话永远是 OBSERVATION_ONLY，绝不当邀请。 */
  const CONTACT_EFFECT_TEXT = {
    OBSERVATION_ONLY: '只观察',
    INVITATION: '邀请',
    END: '结束'
  };
  const CONTACT_STATE_TEXT = {
    PENDING: '已记下（还没处理）',
    CLAIMED: '她正在处理',
    DEFERRED: '延后了（有明确条件）',
    UNKNOWN: '她的答复未知',
    PROCESSED: '已处理完'
  };
  const CONTACT_OUTCOME_TEXT = {
    OBSERVED: '只是看到了',
    ACCEPTED: '她接受了',
    DECLINED: '她拒绝了',
    DEFERRED: '她延后了',
    ENDED: '这次共同时间结束了',
    UNKNOWN: '结果未知'
  };
  /* B 的会话记录（co_presence.json，只读投影）的状态词表。 */
  const CONTACT_SESSION_TEXT = {
    NONE: '没有开着的共同时间',
    OPEN: '共同时间开着',
    CLOSED: '共同时间已经结束',
    PARKED_NO_JOINT: '她推迟了这次共处',
    ENDED: '共同时间已经结束',
    ABSENT: '还没有共同时间记录',
    UNREADABLE: '共同时间记录读不出来',
    INVALID: '共同时间记录不完整'
  };
  const CONTACT_TEXT_MAX = 2000;
  const CONTACT_SCOPE_MAX = 200;
  const CONTACT_EXPIRY_MIN = 1;
  const CONTACT_EXPIRY_MAX = 86400;
  const CONTACT_DURATION_MAX = 86400;
  const CONTACT_LOCAL_NOTE = '只进这台实例自己的本地收件箱，没有外发，也不等于用户已收到';
  const CONTACT_QUEUED_NOTE = '已记下，还没处理：这不是她答应了，也不是共同时间已经开始';
  const CONTACT_SCOPES = ['contact'];

  /* 列表视图：状态 / 类型 / 范围 / 延后条件 / 结束——没有正文。 */
  function contactListView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!envelope) return null;
    const rows = data && Array.isArray(data.events) ? data.events.filter(asRecord) : [];
    const counts = data ? asRecord(data.counts) : null;
    const items = rows.map(function (row) {
      const state = asString(row.processing_state);
      const kind = asString(row.event_kind);
      const ref = protocolId(row.event_ref, 200);
      return {
        eventRef: ref.ok ? ref.value : '',
        refLabel: safeLabel(row.event_ref, 40),
        idOk: ref.ok,
        seq: asCount(row.seq),
        kind: kind,
        kindLabel: CONTACT_KIND_TEXT[kind] || (kind ? safeLabel(kind, 24) : '未说明'),
        effect: asString(row.effect),
        effectLabel: CONTACT_EFFECT_TEXT[asString(row.effect)] || safeLabel(row.effect, 24),
        state: state,
        stateLabel: CONTACT_STATE_TEXT[state] || (state ? safeLabel(state, 24) : '服务没有说状态'),
        outcome: asString(row.outcome),
        outcomeLabel: CONTACT_OUTCOME_TEXT[asString(row.outcome)] || '',
        outcomeReason: asString(row.outcome_reason_code),
        occurredAt: asString(row.occurred_at),
        receivedAt: asString(row.received_at),
        bodyChars: asCount(row.body_chars) || 0,
        bodyStored: row.body_stored === true,
        scope: sanitizeLine(row.scope, CONTACT_SCOPE_MAX),
        expiresAt: asString(row.expires_at),
        expired: row.expired === true,
        invitationId: asString(row.invitation_id),
        invitationBound: row.invitation_bound === true,
        deferCondition: sanitizeLine(row.defer_condition, CONTACT_SCOPE_MAX),
        opportunityRef: safeLabel(row.opportunity_ref, 60),
        sessionRef: safeLabel(row.session_ref, 60),
        attempts: asCount(row.attempts) || 0,
        claimedBy: safeLabel(row.claimed_by, 40)
      };
    });
    const session = data ? asRecord(data.session) : null;
    return {
      ok: envelope.ok === true,
      status: asString(envelope.status),
      reason: asString(envelope.reason_code),
      ledgerState: data ? asString(data.ledger_state) : '',
      items: items,
      eventCount: data ? (asCount(data.event_count) || 0) : 0,
      capacity: data ? (asCount(data.capacity) || 0) : 0,
      counts: counts ? {
        pending: asCount(counts.PENDING) || 0,
        claimed: asCount(counts.CLAIMED) || 0,
        deferred: asCount(counts.DEFERRED) || 0,
        unknown: asCount(counts.UNKNOWN) || 0,
        processed: asCount(counts.PROCESSED) || 0
      } : null,
      truncated: data ? data.truncated === true : false,
      session: session ? {
        state: asString(session.state) || 'ABSENT',
        stateLabel: CONTACT_SESSION_TEXT[asString(session.state)] || safeLabel(session.state, 24),
        readable: session.readable === true,
        open: session.open === true,
        invitationId: asString(session.invitation_id),
        scope: sanitizeLine(session.scope, CONTACT_SCOPE_MAX),
        openedAt: asString(session.opened_at),
        endedAt: asString(session.ended_at),
        endReason: asString(session.end_reason),
        jointActivityId: asString(session.joint_activity_id),
        detail: sanitizeLine(session.detail, 120)
      } : null,
      subject: data ? safeLabel(data.subject, 60) : '',
      homeBound: data ? data.home_bound === true : false,
      homeSource: data ? safeLabel(data.home_source, 40) : '',
      privateTextIncluded: data ? data.private_text_included === true : false,
      localOnly: data ? data.local_only !== false : true,
      externalSend: data ? data.external_send === true : false,
      deliveredToUser: data ? data.delivered_to_user === true : false,
      note: data ? sanitizeLine(data.note, 200) : '',
      queuedNote: data ? sanitizeLine(data.queued_note, 200) : ''
    };
  }

  /* 一次处理结果的几句话：结果 / 原因 / 延后条件 / 结束绑定（只翻译服务给的字段）。 */
  function contactResultLines(result) {
    const row = asRecord(result);
    if (!row) return null;
    const outcome = asString(row.outcome);
    const lines = [];
    if (outcome) lines.push('结果：' + (CONTACT_OUTCOME_TEXT[outcome] || safeLabel(outcome, 24)));
    if (asString(row.reason_code)) lines.push('原因码：' + safeLabel(row.reason_code, 40));
    if (asString(row.detail)) lines.push('说明：' + sanitizeText(row.detail, 400));
    if (asString(row.defer_condition)) {
      lines.push('等的是：' + sanitizeLine(row.defer_condition, CONTACT_SCOPE_MAX));
    }
    if (asString(row.opportunity_ref)) lines.push('机会：' + safeLabel(row.opportunity_ref, 60));
    if (asString(row.invitation_ref)) lines.push('对应的邀请：' + safeLabel(row.invitation_ref, 60));
    if (asString(row.session_ref)) lines.push('共同时间：' + safeLabel(row.session_ref, 60));
    return {
      outcome: outcome,
      outcomeLabel: CONTACT_OUTCOME_TEXT[outcome] || '',
      reason: asString(row.reason_code),
      recordedAt: asString(row.recorded_at),
      consumer: safeLabel(row.consumer, 40),
      terminal: row.terminal === true,
      retryable: row.retryable === true,
      lines: lines
    };
  }

  /* 详情视图：只有这里可能出现正文，而且只来自 get_event(include_body)。 */
  function contactEventView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    const event = data ? asRecord(data.event) : null;
    if (!envelope || !event) return null;
    const state = asString(event.processing_state);
    const kind = asString(event.event_kind);
    return {
      ok: envelope.ok === true,
      status: asString(envelope.status),
      reason: asString(envelope.reason_code),
      eventRef: asString(event.event_ref),
      refLabel: safeLabel(event.event_ref, 60),
      seq: asCount(event.seq),
      kind: kind,
      kindLabel: CONTACT_KIND_TEXT[kind] || (kind ? safeLabel(kind, 24) : '未说明'),
      effect: asString(event.effect),
      effectLabel: CONTACT_EFFECT_TEXT[asString(event.effect)] || safeLabel(event.effect, 24),
      state: state,
      stateLabel: CONTACT_STATE_TEXT[state] || (state ? safeLabel(state, 24) : '服务没有说状态'),
      outcome: asString(event.outcome),
      outcomeLabel: CONTACT_OUTCOME_TEXT[asString(event.outcome)] || '',
      outcomeReason: asString(event.outcome_reason_code),
      occurredAt: asString(event.occurred_at),
      receivedAt: asString(event.received_at),
      scope: sanitizeLine(event.scope, CONTACT_SCOPE_MAX),
      expiresAt: asString(event.expires_at),
      expired: event.expired === true,
      invitationId: asString(event.invitation_id),
      invitationBound: event.invitation_bound === true,
      deferCondition: sanitizeLine(event.defer_condition, CONTACT_SCOPE_MAX),
      opportunityRef: safeLabel(event.opportunity_ref, 60),
      sessionRef: safeLabel(event.session_ref, 60),
      attempts: asCount(event.attempts) || 0,
      claimedBy: safeLabel(event.claimed_by, 40),
      bodyChars: asCount(event.body_chars) || 0,
      bodyStored: event.body_stored === true,
      /* 正文在信封的 data.body（P 的详情投影），不在 event 里；两处都认，优先 data。 */
      body: sanitizeText(data.body !== undefined && data.body !== null
        ? data.body : event.body, CONTACT_TEXT_MAX),
      bodyIncluded: data.body_included === true,
      privateTextIncluded: data.private_text_included === true,
      operationKey: safeLabel(data.operation_key, 60),
      source: asString(data.source),
      channel: asString(data.channel),
      sourceRef: safeLabel(data.source_ref, 60),
      clientIdentityIgnored: data.client_identity_ignored === true,
      binding: asRecord(data.binding),
      resultLines: contactResultLines(asRecord(data.result)),
      results: (Array.isArray(data.results) ? data.results : []).filter(asRecord).map(contactResultLines),
      localOnly: data.local_only !== false,
      note: sanitizeLine(data.note, 200)
    };
  }

  /* 原 key 的只读对账：四种答案原样映射，绝不换键重投。 */
  function contactOperationView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!envelope || !data) return null;
    return {
      ok: envelope.ok === true,
      status: asString(envelope.status),
      reason: asString(envelope.reason_code),
      operationKey: safeLabel(data.operation_key, 60),
      kind: asString(data.kind),
      eventRef: asString(data.event_ref),
      eventState: asString(data.event_state),
      outcome: asString(data.outcome),
      recorded: data.recorded === true,
      retryable: data.retryable === true,
      ledgerState: asString(data.ledger_state),
      note: sanitizeLine(data.note, 200)
    };
  }

  /* 三条请求体：只装客户端自己的意图（kind / 正文 / 范围 / 结束哪一次 / 自己的键）。 */
  function buildContactPayload(input) {
    const source = asRecord(input) || {};
    const kind = asString(source.event_kind);
    if (CONTACT_KINDS_PHONE.indexOf(kind) < 0) return null;
    const key = asString(source.operation_key).trim();
    if (!key) return null;
    const payload = {event_kind: kind, operation_key: key};
    if (kind === 'CO_PRESENCE_END') {
      const invitation = asString(source.invitation_id).trim();
      if (!invitation) return null;
      payload.invitation_id = invitation;
      payload.explicit_end = true;
      const reason = asString(source.reason).trim();
      if (reason) {
        if (reason.length > 64) return null;
        payload.reason = reason;
      }
      return payload;
    }
    const text = typeof source.text === 'string' ? source.text : '';
    if (text.trim()) {
      if (text.length > CONTACT_TEXT_MAX) return null;
      payload.text = text;
    }
    if (kind === 'CO_PRESENCE_INVITATION') {
      const scope = asString(source.scope).trim();
      if (!scope) return null;
      const clean = scope.split(/\s+/).join(' ');
      if (clean.length > CONTACT_SCOPE_MAX) return null;
      payload.scope = clean;
      if (typeof source.expires_after_s === 'number' && isFinite(source.expires_after_s)) {
        const expires = Math.floor(source.expires_after_s);
        if (expires < CONTACT_EXPIRY_MIN || expires > CONTACT_EXPIRY_MAX) return null;
        payload.expires_after_s = expires;
      }
      if (typeof source.duration_seconds === 'number' && isFinite(source.duration_seconds)) {
        const duration = Math.floor(source.duration_seconds);
        if (duration < 0 || duration > CONTACT_DURATION_MAX) return null;
        payload.duration_seconds = duration;
      }
    }
    return payload;
  }

  /* 发出前的就地校验：给人读的理由，而不是只回一个 null。 */
  function validateContactDraft(kind, draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    const text = typeof source.text === 'string' ? source.text : '';
    if (kind === 'CO_PRESENCE_END') {
      if (!asString(source.invitation_id).trim()) errors.push('要结束一次邀请，先选清是哪一次。');
      if (asString(source.reason).trim().length > 64) errors.push('结束理由最多 64 个字符。');
      return {ok: errors.length === 0, errors: errors};
    }
    if (kind === 'LOCAL_MESSAGE') {
      if (!text.trim()) errors.push('普通来话要写一句正文。');
      if (text.length > CONTACT_TEXT_MAX) errors.push('正文最多 ' + CONTACT_TEXT_MAX + ' 个字符。');
      return {ok: errors.length === 0, errors: errors};
    }
    if (kind === 'CO_PRESENCE_INVITATION') {
      const scope = asString(source.scope).trim();
      if (!scope) errors.push('明确邀请必须写清范围（一起做什么）。');
      if (scope.split(/\s+/).join(' ').length > CONTACT_SCOPE_MAX) {
        errors.push('范围最多 ' + CONTACT_SCOPE_MAX + ' 个字符。');
      }
      if (text.length > CONTACT_TEXT_MAX) errors.push('正文最多 ' + CONTACT_TEXT_MAX + ' 个字符。');
      const expires = source.expires_after_s;
      if (expires !== '' && expires !== null && expires !== undefined) {
        const value = Math.floor(Number(expires));
        if (!isFinite(value) || value < CONTACT_EXPIRY_MIN || value > CONTACT_EXPIRY_MAX) {
          errors.push('有效期要在 ' + CONTACT_EXPIRY_MIN + '–' + CONTACT_EXPIRY_MAX + ' 秒之间。');
        }
      }
      return {ok: errors.length === 0, errors: errors};
    }
    return {ok: false, errors: ['未知的来话类型。']};
  }

  /* 路径：标识原样保留，不改写、不编码（协议串里的冒号必须原样进路径）。 */
  function contactEventPath(eventRef) {
    const ref = protocolId(eventRef, 200);
    if (!ref.ok || !/^[A-Za-z0-9._:\-]+$/.test(ref.value)) return '';
    return '/contact/events/' + ref.value;
  }

  function contactOperationPath(key) {
    const check = protocolId(key, 128);
    if (!check.ok || !/^[A-Za-z0-9._:\-]+$/.test(check.value)) return '';
    return '/contact/operations/' + check.value;
  }

  /* ---------------- 共同实践（A 的 shared_practice_service；P 只投用户自己的意图） ----------------

     铁律：
       * 五条用户意图：提交我这一句 / 改我这一段 / 暂停 / 重新取用 / 显式结束。
         **没有**一条替她说话：界面里不存在「替她提交」或「替她接受」的按钮。
       * 请求 ≠ 排队 ≠ 决定 ≠ 回应 ≠ 已提交作品。界面照实分开这四个词。
       * GET 纯读；写要 CSRF、字段白名单、原 key 不变（生成时确定前缀，重试同键）。
       * 选材只从 owner 列出的共享来源里选；不列私人材料全集，不显示私人正文。
       * 正文只进内存与 textContent：不拼 HTML、不进任何 storage。 */

  const SHARED_PRACTICE_PATH = '/shared-practice';
  /* 手机只投用户自己的四种意图。resume / reopen 是她的那一半（A 的 ACTOR_WRITES 只给 Life
     侧），界面里没有替她重新取用的按钮，服务也会按名拒绝。 */
  const SHARED_PRACTICE_ACTIONS = ['submit', 'modify', 'pause', 'end'];
  const SHARED_PRACTICE_SUBJECT_ONLY = ['resume', 'reopen'];
  const SHARED_PRACTICE_ACTION_TEXT = {
    submit: '提交我这一句',
    modify: '改我这一段（新的一轮）',
    pause: '暂停这次共同实践',
    end: '显式结束'
  };
  const SHARED_PRACTICE_STATE_TEXT = {
    OPEN: '开着的',
    PAUSED: '已暂停',
    ENDED: '已结束',
    CLOSED: '已结束',
    CANDIDATE: '还只是候选',
    UNKNOWN: '状态未知'
  };
  const SHARED_PRACTICE_CONTRIBUTOR_TEXT = {
    user: '你', operator: '你', local_operator: '你',
    subject: '她', life: '她', assistant: '她'
  };
  const SHARED_PRACTICE_TEXT_MAX = 4000;
  const SHARED_PRACTICE_TITLE_MAX = 120;
  const SHARED_PRACTICE_REASON_MAX = 64;
  const SHARED_PRACTICE_ID_MAX = 200;
  const SHARED_PRACTICE_NOTE = '这是这台实例本地的共同实践：没有外发，"已收到"不等于她读过或同意';
  const SHARED_PRACTICE_QUEUED_NOTE = '你的输入进本实例来信箱；排队不等于她已处理、同意或写入作品。没有新输入不会自行反复请求模型';
  const SHARED_PRACTICE_MODIFY_NOTE = '「改我这一段」是你在同一段里的新的一轮：上一段留在历史里，'
    + 'A 不改写已经记录的贡献';
  const SHARED_PRACTICE_SCOPES = ['shared-practice'];

  /* 列表视图：状态 / 材料 / 双方计数。列表**没有**私人正文，也不列私人材料全集。 */
  function sharedPracticeListView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!envelope) return null;
    const rows = data && Array.isArray(data.practices) ? data.practices.filter(asRecord) : [];
    const items = rows.map(function (row) {
      const id = protocolId(row.practice_id, SHARED_PRACTICE_ID_MAX);
      const state = asString(row.state).toUpperCase();
      return {
        practiceId: id.ok ? id.value : '',
        idOk: id.ok,
        idLabel: safeLabel(row.practice_id, 40),
        title: sanitizeLine(row.title, 120) || '未命名共同实践',
        state: state,
        stateText: SHARED_PRACTICE_STATE_TEXT[state]
          || (state ? safeLabel(state, 24) : '服务没有说状态'),
        open: row.open === true,
        head: safeLabel(row.head_version_id, 60),
        revision: asCount(row.revision),
        sourceTitle: sanitizeLine(row.source_title, 120),
        sourceReadable: row.source_readable === true,
        sourceReason: asString(row.source_reason_code),
        /* A 的列表只给计数、不给按人分组：拿不到就照实会是 null，界面写「—」而不是 0。 */
        userRounds: asCount(row.user_rounds),
        subjectRounds: asCount(row.subject_rounds),
        contributionsTotal: asCount(row.contributions_total),
        updatedAt: asString(row.updated_at)
      };
    });
    const service = data ? asRecord(data.service) : null;
    return {
      ok: envelope.ok === true,
      status: asString(envelope.status),
      reason: asString(envelope.reason_code),
      listOk: data ? data.list_ok !== false : false,
      count: data ? (asCount(data.count) || 0) : 0,
      truncated: data ? data.truncated === true : false,
      privateTextIncluded: data ? data.private_text_included === true : false,
      localOnly: data ? data.local_only !== false : true,
      note: data ? sanitizeLine(data.note, 200) : '',
      service: service ? {
        bound: service.bound === true,
        owner: safeLabel(service.owner, 60),
        actions: Array.isArray(service.actions) ? service.actions.filter(function (value) {
          return typeof value === 'string';
        }) : [],
        gaps: Array.isArray(service.interface_gaps) ? service.interface_gaps.length : 0,
        writesSubject: service.writes_subject_contribution === true,
        acceptsForSubject: service.accepts_for_subject === true,
        startsDaemon: service.starts_life_daemon === true
      } : null,
      items: items
    };
  }

  /* 详情视图：材料引用/版本、双方贡献（谁写的必须可见）、作品版本、暂停/结束、权限边界。 */
  function sharedPracticeWorkView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!envelope || !data) return null;
    const practice = asRecord(data.practice);
    const material = asRecord(data.material);
    const work = asRecord(data.work);
    const permissions = asRecord(data.permissions);
    const pause = asRecord(data.pause);
    const end = asRecord(data.end);
    const rawContributions = Array.isArray(data.contributions) ? data.contributions : [];
    const contributions = rawContributions.filter(asRecord).map(function (row) {
      const by = asString(row.by).toLowerCase();
      const isUser = row.is_user === true || by === 'user' || by === 'operator'
        || by === 'local_operator';
      const isSubject = row.is_subject === true || by === 'subject' || by === 'life'
        || by === 'assistant';
      return {
        by: by,
        byText: SHARED_PRACTICE_CONTRIBUTOR_TEXT[by] || (by ? safeLabel(by, 24) : '来源未说明'),
        isUser: isUser,
        isSubject: isSubject,
        round: asCount(row.round),
        text: sanitizeText(row.text, SHARED_PRACTICE_TEXT_MAX),
        textChars: asCount(row.text_chars),
        at: asString(row.at),
        origin: asString(row.origin),
        decisionRef: safeLabel(row.decision_ref, 60),
        draft: row.draft === true
      };
    });
    const versions = (Array.isArray(data.versions) ? data.versions : []).filter(asRecord)
      .map(function (row) {
        return {
          versionId: safeLabel(row.version_id, 60),
          versionNumber: asCount(row.version_number),
          at: asString(row.at),
          by: asString(row.by),
          textChars: asCount(row.text_chars),
          isHead: row.is_head === true,
          text: sanitizeText(row.text, SHARED_PRACTICE_TEXT_MAX)
        };
      });
    const state = practice ? asString(practice.state).toUpperCase() : '';
    return {
      ok: envelope.ok === true,
      status: asString(envelope.status),
      practiceId: practice ? asString(practice.practice_id) : '',
      title: practice ? (sanitizeLine(practice.title, 120) || '未命名共同实践') : '未命名共同实践',
      state: state,
      stateText: SHARED_PRACTICE_STATE_TEXT[state] || (state ? safeLabel(state, 24) : '状态未说明'),
      open: practice ? practice.open === true : false,
      jointState: practice ? asString(practice.joint_state).toUpperCase() : '',
      head: practice ? safeLabel(practice.head_version_id, 60) : '',
      revision: practice ? asCount(practice.revision) : null,
      material: material ? {
        artifactId: safeLabel(material.artifact_id, 60),
        versionId: safeLabel(material.version_id, 60),
        title: sanitizeLine(material.title, 120),
        readable: material.readable === true,
        reason: asString(material.reason_code)
      } : null,
      work: work ? {
        artifactId: safeLabel(work.artifact_id, 60),
        versionId: safeLabel(work.version_id, 60),
        revision: asCount(work.revision),
        text: sanitizeText(work.text, SHARED_PRACTICE_TEXT_MAX),
        versionCount: asCount(work.version_count) || 0
      } : null,
      contributions: contributions,
      userContributions: asCount(data.user_contributions) || 0,
      subjectContributions: asCount(data.subject_contributions) || 0,
      versions: versions,
      pause: pause ? {
        paused: pause.paused === true,
        requestedBy: asString(pause.requested_by),
        reason: asString(pause.reason_code)
      } : null,
      end: end ? {
        ended: end.ended === true,
        endedAt: asString(end.ended_at),
        reason: asString(end.reason)
      } : null,
      permissions: permissions ? {
        privateSourceExposed: permissions.private_source_exposed === true,
        boundedExcerpt: permissions.bounded_excerpt === true,
        subjectVisible: permissions.subject_contribution_visible === true,
        actions: Array.isArray(permissions.actions) ? permissions.actions.filter(function (v) {
          return typeof v === 'string';
        }) : [],
        mayWriteSubject: permissions.may_write_subject_contribution === true
      } : null,
      privateTextIncluded: data.private_text_included === true,
      localOnly: data.local_only !== false,
      deliveredToUser: data.delivered_to_user === true,
      note: data ? sanitizeLine(data.note, 200) : ''
    };
  }

  /* 一次动作的几句话：只翻译服务给的字段，绝不把 queued 写成她答应了。 */
  function sharedPracticeOutcomeLines(action, doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!data) return [];
    const lines = [];
    const label = SHARED_PRACTICE_ACTION_TEXT[action] || action;
    lines.push('这次做的是：' + label + '。');
    const state = asString(data.state).toUpperCase();
    if (state) lines.push('作品现在是：' +
      (SHARED_PRACTICE_STATE_TEXT[state] || safeLabel(state, 24)) + '。');
    const eventKind = asString(data.event_kind);
    const eventState = asString(data.event_state).toUpperCase();
    const queued = data.queued === true;
    if (data.submitted === true) {
      lines.push(queued
        ? '这一句已经进了她的来信箱（' + (eventKind || '事件') + '），还没被处理。'
        : '服务记下了这次请求。');
    }
    if (queued) {
      lines.push('排队不等于她答应了：处理它的是正常 Life，作品和回应都要等她那边走完。');
    }
    if (eventState) lines.push('这次来话的状态：' + safeLabel(eventState, 24) + '。');
    if (data.work_changed === true) lines.push('共同作品确实改了版本。');
    if (data.subject_responded === true) lines.push('她那边也回应了一次。');
    if (action === 'pause' && data.state === 'PAUSED') {
      lines.push('这一段作品暂停了：它只是不再追加，不是她那边收工。');
    }
    if (data.accepted === true) {
      lines.push('服务报告 accepted=true：这不是界面替她做的判断。');
    } else {
      lines.push('界面没有替她接纳：接不接受由她自己的选择决定。');
    }
    if (data.starts_life_daemon === true) {
      lines.push('服务报告这次动过 Life：照实显示，界面没有起它。');
    }
    if (action === 'end' && queued) {
      lines.push('结束是给她的一个明确 END 事件：她那边收口时会同时放掉 joint 与这件作品。');
    }
    lines.push(SHARED_PRACTICE_NOTE + '。');
    return lines;
  }

  /* 原 key 的只读对账。 */
  function sharedPracticeOperationView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!envelope || !data) return null;
    return {
      ok: envelope.ok === true,
      status: asString(envelope.status),
      reason: asString(envelope.reason_code),
      operationKey: safeLabel(data.operation_key, 60),
      practiceId: safeLabel(data.practice_id, 60),
      recorded: data.recorded === true,
      state: asString(data.state),
      committed: data.committed === true,
      retryable: data.retryable === true,
      note: sanitizeLine(data.note, 200)
    };
  }

  /* 两条请求体：只装用户自己的意图。subject / contributor / accept / state 永远不在里面。
     open 带 title 与 text：A 的 open_practice 要一个标题与第一轮正文，界面不替它编。 */
  function buildSharedPracticeOpenPayload(input) {
    const source = asRecord(input) || {};
    const key = asString(source.operation_key).trim();
    const artifactId = asString(source.source_artifact_id).trim();
    const versionId = asString(source.source_version_id).trim();
    const title = sanitizeLine(source.title, SHARED_PRACTICE_TITLE_MAX).trim();
    const text = typeof source.text === 'string' ? source.text : '';
    if (!key || !artifactId || !versionId || !title || !text.trim()) return null;
    if (artifactId.length > SHARED_PRACTICE_ID_MAX || versionId.length > SHARED_PRACTICE_ID_MAX) {
      return null;
    }
    if (text.length > SHARED_PRACTICE_TEXT_MAX) return null;
    return {source_artifact_id: artifactId, source_version_id: versionId, title: title,
      text: text, operation_key: key};
  }

  function buildSharedPracticeActionPayload(input) {
    const source = asRecord(input) || {};
    const action = asString(source.action).trim().toLowerCase();
    if (SHARED_PRACTICE_ACTIONS.indexOf(action) < 0) return null;
    const key = asString(source.operation_key).trim();
    const head = asString(source.expected_head).trim();
    if (!key || !head) return null;
    const payload = {action: action, expected_head: head, operation_key: key};
    if (action === 'submit' || action === 'modify') {
      const text = typeof source.text === 'string' ? source.text : '';
      if (!text.trim()) return null;
      if (text.length > SHARED_PRACTICE_TEXT_MAX) return null;
      payload.text = text;
    }
    if (action === 'end') {
      const reason = asString(source.reason).trim();
      if (reason) {
        if (reason.length > SHARED_PRACTICE_REASON_MAX) return null;
        payload.reason = reason;
      }
    }
    return payload;
  }

  /* 发出前的就地校验：给人读的理由，而不是只回一个 null。 */
  function validateSharedPracticeDraft(kind, draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    if (kind === 'open') {
      if (!asString(source.source_artifact_id).trim()) errors.push('先选一份明确共享的材料。');
      if (!asString(source.source_version_id).trim()) errors.push('这份材料的版本还没读到，先刷新。');
      if (!sanitizeLine(source.title, SHARED_PRACTICE_TITLE_MAX).trim()) {
        errors.push('给这一次共同实践起一个标题（最多 ' + SHARED_PRACTICE_TITLE_MAX + ' 字）。');
      }
      const opening = typeof source.text === 'string' ? source.text : '';
      if (!opening.trim()) errors.push('写下你的第一轮：A 不接受空的开口。');
      if (opening.length > SHARED_PRACTICE_TEXT_MAX) {
        errors.push('这段话最多 ' + SHARED_PRACTICE_TEXT_MAX + ' 个字符。');
      }
      return {ok: errors.length === 0, errors: errors};
    }
    const action = asString(source.action).trim().toLowerCase();
    if (SHARED_PRACTICE_ACTIONS.indexOf(action) < 0) {
      return {ok: false, errors: ['未知的共同实践动作。']};
    }
    if (!asString(source.expected_head).trim()) {
      errors.push('缺这一次读到的版本：先刷新，再提交。');
    }
    if (action === 'submit' || action === 'modify') {
      const text = typeof source.text === 'string' ? source.text : '';
      if (!text.trim()) errors.push('要写一句你自己的话。');
      if (text.length > SHARED_PRACTICE_TEXT_MAX) {
        errors.push('这段话最多 ' + SHARED_PRACTICE_TEXT_MAX + ' 个字符。');
      }
    }
    if (action === 'end' && asString(source.reason).trim().length > SHARED_PRACTICE_REASON_MAX) {
      errors.push('结束理由最多 ' + SHARED_PRACTICE_REASON_MAX + ' 个字符。');
    }
    return {ok: errors.length === 0, errors: errors};
  }

  /* 路径：标识原样保留，不改写、不编码（协议串里的冒号必须原样进路径）。 */
  function sharedPracticeWorkPath(practiceId) {
    const id = protocolId(practiceId, SHARED_PRACTICE_ID_MAX);
    if (!id.ok || !/^[A-Za-z0-9._:\-]+$/.test(id.value)) return '';
    return '/shared-practice/works/' + id.value;
  }

  function sharedPracticeActionsPath(practiceId) {
    const base = sharedPracticeWorkPath(practiceId);
    return base ? base + '/actions' : '';
  }

  function sharedPracticeOperationPath(key) {
    const check = protocolId(key, 128);
    if (!check.ok || !/^[A-Za-z0-9._:\-]+$/.test(check.value)) return '';
    return '/shared-practice/operations/' + check.value;
  }
  const pureApi = {
    API_BASE: API_BASE,
    APP_ORDER: APP_ORDER,
    APP_REGISTRY: APP_REGISTRY,
    SESSION_KEYS: SESSION_KEYS,
    REASON_TEXT: REASON_TEXT,
    MUTATION_STATUSES: MUTATION_STATUSES,
    sanitizeLine: sanitizeLine,
    sanitizeText: sanitizeText,
    safeLabel: safeLabel,
    protocolId: protocolId,
    apiPath: apiPath,
    scopeKey: scopeKey,
    reasonText: reasonText,
    describeReason: describeReason,
    humanizeCode: humanizeCode,
    formatMoney: formatMoney,
    formatWhen: formatWhen,
    clockText: clockText,
    dateText: dateText,
    csrfFromState: csrfFromState,
    stateView: stateView,
    canWrite: canWrite,
    deriveReadOutcome: deriveReadOutcome,
    deriveMutationOutcome: deriveMutationOutcome,
    LIVING_PATH: LIVING_PATH,
    LIVING_WINDOW_PATH: LIVING_WINDOW_PATH,
    LIVING_PAUSE_PATH: LIVING_PAUSE_PATH,
    LIVING_STOP_PATH: LIVING_STOP_PATH,
    LIVING_DEFAULT_SECONDS: LIVING_DEFAULT_SECONDS,
    LIVING_DEFAULT_REQUESTS: LIVING_DEFAULT_REQUESTS,
    livingView: livingView,
    livingMemoryText: memoryLineText,
    livingActivityStatusText: activityStatusText,
    isReadSuccessStatus: isReadSuccessStatus,
    readStatusWarning: readStatusWarning,
    buildRequestInit: buildRequestInit,
    noteListView: noteListView,
    shopView: shopView,
    inventoryView: inventoryView,
    validateNoteDraft: validateNoteDraft,
    buildNotePayload: buildNotePayload,
    buildPurchasePayload: buildPurchasePayload,
    buildUsePayload: buildUsePayload,
    noteEditBlocked: noteEditBlocked,
    validateReferenceDraft: validateReferenceDraft,
    displayFieldEntries: displayFieldEntries,
    /* rc1 阅读工作台：纯函数面（服务契约的全部搬运都在这几个函数里） */
    READING_MATERIAL_PATH: READING_MATERIAL_PATH,
    READING_POSITIONS_PATH: READING_POSITIONS_PATH,
    READING_ANNOTATIONS_PATH: READING_ANNOTATIONS_PATH,
    READING_PAGE_SEPARATOR: READING_PAGE_SEPARATOR,
    READING_CONTENT_MAX_BYTES: READING_CONTENT_MAX_BYTES,
    READING_PAGE_MAX_BYTES: READING_PAGE_MAX_BYTES,
    READING_PAGES_MAX: READING_PAGES_MAX,
    READING_ANNOTATION_KINDS: READING_ANNOTATION_KINDS,
    READING_SCOPES: READING_SCOPES,
    READING_SCOPE_TEXT: READING_SCOPE_TEXT,
    utf8ByteLength: utf8ByteLength,
    readingPageValue: readingPageValue,
    readingContentStats: readingContentStats,
    validateReadingContent: validateReadingContent,
    buildImportPayload: buildImportPayload,
    buildPositionPayload: buildPositionPayload,
    validateAnnotationDraft: validateAnnotationDraft,
    buildAnnotationPayload: buildAnnotationPayload,
    readingScopeFor: readingScopeFor,
    readingScopePage: readingScopePage,
    readingOperationPath: readingOperationPath,
    readingMaterialPath: readingMaterialPath,
    readingBookmarkView: readingBookmarkView,
    readingAnnotationView: readingAnnotationView,
    readingMaterialView: readingMaterialView,
    makeOperationKey: makeOperationKey,
    createOpStore: createOpStore,
    createNavStore: createNavStore,
    /* 私人相册 */
    ALBUM_MAX_BYTES: ALBUM_MAX_BYTES,
    ALBUM_MAX_CAPTION: ALBUM_MAX_CAPTION,
    albumIdFor: albumIdFor,
    albumRowView: albumRowView,
    albumListView: albumListView,
    albumDetailPath: albumDetailPath,
    albumImagePath: albumImagePath,
    albumImageSrc: albumImageSrc,
    /* 世界 / 身体 */
    HOME_ORDER: HOME_ORDER,
    WORLD_APP: WORLD_APP,
    WORLD_VERBS: WORLD_VERBS,
    POSE_VALUES: POSE_VALUES,
    ORIENTATION_VALUES: ORIENTATION_VALUES,
    HAND_SLOT_VALUES: HAND_SLOT_VALUES,
    REST_STATE_VALUES: REST_STATE_VALUES,
    REST_MODE_VALUES: REST_MODE_VALUES,
    ACTION_TEXT: ACTION_TEXT,
    POSE_TEXT: POSE_TEXT,
    worldView: worldView,
    worldSnapshotNeedsLoad: worldSnapshotNeedsLoad,
    worldHeaderView: worldHeaderView,
    worldLocationView: worldLocationView,
    worldIndoorView: worldIndoorView,
    worldTravelView: worldTravelView,
    worldDestinationsView: worldDestinationsView,
    worldObjectsView: worldObjectsView,
    worldActionsView: worldActionsView,
    worldRecentView: worldRecentView,
    bodyView: bodyView,
    worldSignalLines: worldSignalLines,
    worldReasonText: worldReasonText,
    worldVerbAvailability: worldVerbAvailability,
    worldMoveDisabledReason: worldMoveDisabledReason,
    worldPickupOptions: worldPickupOptions,
    worldPlaceOption: worldPlaceOption,
    worldRestOption: worldRestOption,
    buildPlacementTarget: buildPlacementTarget,
    buildWorldActionPayload: buildWorldActionPayload,
    worldActionScope: worldActionScope,
    worldMapPlan: worldMapPlan,
    worldTechEntries: worldTechEntries,
    formatWalkText: formatWalkText,
    worldOutcomeLines: worldOutcomeLines,
    worldOperationLines: worldOperationLines,
    /* rc1 房间 / 实体日用店 */
    ROOM_VERB_NAMES: ROOM_VERB_NAMES,
    ROOM_EXTRA_VERBS: ROOM_EXTRA_VERBS,
    ALL_WORLD_VERBS: ALL_WORLD_VERBS,
    ARRANGE_PARAM_KEYS: ARRANGE_PARAM_KEYS,
    FIXTURE_PARAM_KEYS: FIXTURE_PARAM_KEYS,
    LIGHT_BAND_TEXT: LIGHT_BAND_TEXT,
    roomLightView: roomLightView,
    ownedFurnitureView: ownedFurnitureView,
    deliveriesView: deliveriesView,
    surfacesView: surfacesView,
    roomVerbFor: roomVerbFor,
    paramKeyMismatch: paramKeyMismatch,
    buildSurfacePlacementTarget: buildSurfacePlacementTarget,
    buildArrangeTarget: buildArrangeTarget,
    arrangeTargetsFor: arrangeTargetsFor,
    worldArrangeOption: worldArrangeOption,
    worldFixtureOption: worldFixtureOption,
    storeView: storeView,
    /* 私人项目：纯结构搬运（列表视图 / 详情视图 / 历史视图 / 草稿校验 / 请求体） */
    PROJECT_TITLE_MAX: PROJECT_TITLE_MAX,
    PROJECT_CONTENT_MAX: PROJECT_CONTENT_MAX,
    PROJECT_SCOPES: PROJECT_SCOPES,
    PROJECT_HISTORY_LIMIT: PROJECT_HISTORY_LIMIT,
    PROJECT_DERIVE_NOTE_MAX: PROJECT_DERIVE_NOTE_MAX,
    PROJECT_SOURCE_PURPOSES: PROJECT_SOURCE_PURPOSES,
    projectRowView: projectRowView,
    projectListView: projectListView,
    projectDetailView: projectDetailView,
    projectHistoryView: projectHistoryView,
    projectVersionPageView: projectVersionPageView,
    projectVersionView: projectVersionView,
    projectCompareView: projectCompareView,
    projectHistoryMerge: projectHistoryMerge,
    validateProjectDraft: validateProjectDraft,
    buildProjectCreatePayload: buildProjectCreatePayload,
    buildProjectCommitPayload: buildProjectCommitPayload,
    buildProjectClosePayload: buildProjectClosePayload,
    buildProjectDerivePayload: buildProjectDerivePayload,
    projectDeriveDraft: projectDeriveDraft,
    projectDeriveTitle: projectDeriveTitle,
    projectPath: projectPath,
    projectVersionsPath: projectVersionsPath,
    projectVersionPath: projectVersionPath,
    projectComparePath: projectComparePath,
    /* 分享（B 的 LifeSharingService）：纯结构搬运（列表 / 详情 / 来源 / 草稿校验 / 请求体 / 路径） */
    SHARE_STATES: SHARE_STATES,
    SHARE_STATE_TEXT: SHARE_STATE_TEXT,
    SHARE_ACTION_VALUES: SHARE_ACTION_VALUES,
    SHARE_ACTION_TEXT: SHARE_ACTION_TEXT,
    SHARE_SCOPES: SHARE_SCOPES,
    SHARE_SOURCE_KIND_TEXT: SHARE_SOURCE_KIND_TEXT,
    SHARE_TEXT_MAX: SHARE_TEXT_MAX,
    SHARE_EXCERPT_MAX: SHARE_EXCERPT_MAX,
    SHARE_EXPIRY_MIN: SHARE_EXPIRY_MIN,
    SHARE_EXPIRY_MAX: SHARE_EXPIRY_MAX,
    SHARE_LOCAL_NOTE: SHARE_LOCAL_NOTE,
    shareStateText: shareStateText,
    shareListView: shareListView,
    shareDetailView: shareDetailView,
    shareSourcesView: shareSourcesView,
    sharePath: sharePath,
    shareActionsPath: shareActionsPath,
    shareOperationPath: shareOperationPath,
    shareSourcesPath: shareSourcesPath,
    shareActionScope: shareActionScope,
    buildShareActionPayload: buildShareActionPayload,
    validateShareDraft: validateShareDraft,
    buildSharePreparePayload: buildSharePreparePayload,
    shareOperationLines: shareOperationLines,
    /* 资料库 / 研究工作台：纯结构搬运（列表 / 详情 / 草稿校验 / 请求体 / 路径） */
    MATERIAL_TITLE_MAX: MATERIAL_TITLE_MAX,
    MATERIAL_TEXT_MAX: MATERIAL_TEXT_MAX,
    MATERIAL_ORIGIN_MAX: MATERIAL_ORIGIN_MAX,
    MATERIAL_QUERY_MAX: MATERIAL_QUERY_MAX,
    MATERIAL_SOURCE_KINDS: MATERIAL_SOURCE_KINDS,
    SOURCE_KIND_TEXT: SOURCE_KIND_TEXT,
    CHECK_STATUS_TEXT: CHECK_STATUS_TEXT,
    MATERIAL_SCOPES: MATERIAL_SCOPES,
    RESEARCH_QUESTION_MAX: RESEARCH_QUESTION_MAX,
    RESEARCH_SCOPE_MAX: RESEARCH_SCOPE_MAX,
    RESEARCH_UNCERTAINTY_MAX: RESEARCH_UNCERTAINTY_MAX,
    RESEARCH_CONTENT_MAX: RESEARCH_CONTENT_MAX,
    RESEARCH_SOURCES_MAX: RESEARCH_SOURCES_MAX,
    RESEARCH_DEFAULT_UNCERTAINTY: RESEARCH_DEFAULT_UNCERTAINTY,
    materialRowView: materialRowView,
    materialListView: materialListView,
    materialDetailView: materialDetailView,
    validateMaterialDraft: validateMaterialDraft,
    buildMaterialImportPayload: buildMaterialImportPayload,
    materialPath: materialPath,
    materialsListPath: materialsListPath,
    sourceCheckView: sourceCheckView,
    researchRowView: researchRowView,
    researchListView: researchListView,
    researchDetailView: researchDetailView,
    validateResearchDraft: validateResearchDraft,
    validateResearchUpdateDraft: validateResearchUpdateDraft,
    buildResearchCreatePayload: buildResearchCreatePayload,
    buildResearchUpdatePayload: buildResearchUpdatePayload,
    buildResearchClosePayload: buildResearchClosePayload,
    researchPath: researchPath,
    researchUpdatesPath: researchUpdatesPath,
    researchClosePath: researchClosePath,
    /* 个人实践工作台 / Memory：纯结构搬运（列表 / 详情 / 草稿校验 / 请求体 / 路径） */
    PRACTICE_TITLE_MAX: PRACTICE_TITLE_MAX,
    PRACTICE_QUESTION_MAX: PRACTICE_QUESTION_MAX,
    PRACTICE_INSTRUCTION_MAX: PRACTICE_INSTRUCTION_MAX,
    PRACTICE_SCOPE_MAX: PRACTICE_SCOPE_MAX,
    PRACTICE_UNCERTAINTY_MAX: PRACTICE_UNCERTAINTY_MAX,
    PRACTICE_CONTENT_MAX: PRACTICE_CONTENT_MAX,
    PRACTICE_TRIAL_DETAIL_MAX: PRACTICE_TRIAL_DETAIL_MAX,
    PRACTICE_REFS_MAX: PRACTICE_REFS_MAX,
    PRACTICE_LIST_LIMIT: PRACTICE_LIST_LIMIT,
    PRACTICE_QUERY_MAX: PRACTICE_QUERY_MAX,
    PRACTICE_DEFAULT_UNCERTAINTY: PRACTICE_DEFAULT_UNCERTAINTY,
    PRACTICE_METHOD_STATES: PRACTICE_METHOD_STATES,
    PRACTICE_TRIAL_OUTCOMES: PRACTICE_TRIAL_OUTCOMES,
    PRACTICE_STATE_TEXT: PRACTICE_STATE_TEXT,
    PRACTICE_OUTCOME_TEXT: PRACTICE_OUTCOME_TEXT,
    PRACTICE_CHECK_TEXT: PRACTICE_CHECK_TEXT,
    PRACTICE_SCOPES: PRACTICE_SCOPES,
    practiceKindText: practiceKindText,
    practiceStateText: practiceStateText,
    practiceRowView: practiceRowView,
    practiceListView: practiceListView,
    practiceCheckView: practiceCheckView,
    practiceTrialView: practiceTrialView,
    practiceDetailView: practiceDetailView,
    experienceRowView: experienceRowView,
    experienceListView: experienceListView,
    experienceDetailView: experienceDetailView,
    memoryStatusView: memoryStatusView,
    validatePracticeReviewDraft: validatePracticeReviewDraft,
    validatePracticeMethodDraft: validatePracticeMethodDraft,
    validatePracticeDocumentDraft: validatePracticeDocumentDraft,
    validatePracticeTrialDraft: validatePracticeTrialDraft,
    validatePracticeRevisionDraft: validatePracticeRevisionDraft,
    buildPracticeReviewCreatePayload: buildPracticeReviewCreatePayload,
    buildPracticeMethodCreatePayload: buildPracticeMethodCreatePayload,
    buildPracticeDocumentPayload: buildPracticeDocumentPayload,
    buildPracticeTrialPayload: buildPracticeTrialPayload,
    buildPracticeRevisionPayload: buildPracticeRevisionPayload,
    buildPracticeRetirePayload: buildPracticeRetirePayload,
    buildMemoryEnablePayload: buildMemoryEnablePayload,
    practiceReviewPath: practiceReviewPath,
    practiceReviewDocumentsPath: practiceReviewDocumentsPath,
    practiceMethodPath: practiceMethodPath,
    practiceMethodTrialsPath: practiceMethodTrialsPath,
    practiceMethodRevisionsPath: practiceMethodRevisionsPath,
    practiceMethodRetirePath: practiceMethodRetirePath,
    practiceMethodsPath: practiceMethodsPath,
    memoryExperiencesPath: memoryExperiencesPath,
    memoryExperiencePath: memoryExperiencePath,
    /* 来访与共同时间：纯结构搬运（列表 / 详情 / 对账 / 请求体 / 校验 / 路径） */
    CONTACT_PATH: CONTACT_PATH,
    CONTACT_EVENTS_PATH: CONTACT_EVENTS_PATH,
    CONTACT_KINDS_PHONE: CONTACT_KINDS_PHONE,
    CONTACT_KIND_TEXT: CONTACT_KIND_TEXT,
    CONTACT_EFFECT_TEXT: CONTACT_EFFECT_TEXT,
    CONTACT_STATE_TEXT: CONTACT_STATE_TEXT,
    CONTACT_OUTCOME_TEXT: CONTACT_OUTCOME_TEXT,
    CONTACT_SESSION_TEXT: CONTACT_SESSION_TEXT,
    CONTACT_TEXT_MAX: CONTACT_TEXT_MAX,
    CONTACT_SCOPE_MAX: CONTACT_SCOPE_MAX,
    CONTACT_EXPIRY_MIN: CONTACT_EXPIRY_MIN,
    CONTACT_EXPIRY_MAX: CONTACT_EXPIRY_MAX,
    CONTACT_DURATION_MAX: CONTACT_DURATION_MAX,
    CONTACT_LOCAL_NOTE: CONTACT_LOCAL_NOTE,
    CONTACT_QUEUED_NOTE: CONTACT_QUEUED_NOTE,
    CONTACT_SCOPES: CONTACT_SCOPES,
    contactListView: contactListView,
    contactEventView: contactEventView,
    contactOperationView: contactOperationView,
    contactResultLines: contactResultLines,
    buildContactPayload: buildContactPayload,
    validateContactDraft: validateContactDraft,
    contactEventPath: contactEventPath,
    contactOperationPath: contactOperationPath,
    /* 共同实践（A 的 shared_practice_service）：纯结构搬运 */
    SHARED_PRACTICE_PATH: SHARED_PRACTICE_PATH,
    SHARED_PRACTICE_ACTIONS: SHARED_PRACTICE_ACTIONS,
    SHARED_PRACTICE_SUBJECT_ONLY: SHARED_PRACTICE_SUBJECT_ONLY,
    SHARED_PRACTICE_TITLE_MAX: SHARED_PRACTICE_TITLE_MAX,
    SHARED_PRACTICE_MODIFY_NOTE: SHARED_PRACTICE_MODIFY_NOTE,
    SHARED_PRACTICE_ACTION_TEXT: SHARED_PRACTICE_ACTION_TEXT,
    SHARED_PRACTICE_STATE_TEXT: SHARED_PRACTICE_STATE_TEXT,
    SHARED_PRACTICE_CONTRIBUTOR_TEXT: SHARED_PRACTICE_CONTRIBUTOR_TEXT,
    SHARED_PRACTICE_TEXT_MAX: SHARED_PRACTICE_TEXT_MAX,
    SHARED_PRACTICE_REASON_MAX: SHARED_PRACTICE_REASON_MAX,
    SHARED_PRACTICE_ID_MAX: SHARED_PRACTICE_ID_MAX,
    SHARED_PRACTICE_NOTE: SHARED_PRACTICE_NOTE,
    SHARED_PRACTICE_QUEUED_NOTE: SHARED_PRACTICE_QUEUED_NOTE,
    SHARED_PRACTICE_SCOPES: SHARED_PRACTICE_SCOPES,
    sharedPracticeListView: sharedPracticeListView,
    sharedPracticeWorkView: sharedPracticeWorkView,
    sharedPracticeOutcomeLines: sharedPracticeOutcomeLines,
    sharedPracticeOperationView: sharedPracticeOperationView,
    buildSharedPracticeOpenPayload: buildSharedPracticeOpenPayload,
    buildSharedPracticeActionPayload: buildSharedPracticeActionPayload,
    validateSharedPracticeDraft: validateSharedPracticeDraft,
    sharedPracticeWorkPath: sharedPracticeWorkPath,
    sharedPracticeActionsPath: sharedPracticeActionsPath,
    sharedPracticeOperationPath: sharedPracticeOperationPath
  };

  root.NyairoPhone = Object.assign(root.NyairoPhone || {}, pureApi);

  /* 没有 DOM（node 检查纯函数）时到此为止。 */
  if (typeof document === 'undefined' || !document || typeof document.createElement !== 'function') return;

  /* ------------------------------------------------------------------ *
   * 以下为浏览器运行时
   * ------------------------------------------------------------------ */

  const ICONS = {
    note: ['M6 3.5h8.2L19 8.3V20a.8.8 0 0 1-.8.8H6a.8.8 0 0 1-.8-.8V4.3a.8.8 0 0 1 .8-.8z', 'M14 3.6V8.5h5', 'M8.6 12.4h6.4', 'M8.6 16h4.4'],
    bookmark: ['M7 3.8h10a.8.8 0 0 1 .8.8v15.6L12 16.4l-5.8 3.8V4.6a.8.8 0 0 1 .8-.8z'],
    book: ['M12 6.6C10.2 4.9 7.5 4.5 4.5 5v13c3-.5 5.7 0 7.5 1.7 1.8-1.7 4.5-2.2 7.5-1.7V5c-3-.5-5.7-.1-7.5 1.6z', 'M12 6.6v13'],
    wallet: ['M3.6 8.4a2 2 0 0 1 2-2h11.2a2 2 0 0 1 2 2v8.2a2 2 0 0 1-2 2H5.6a2 2 0 0 1-2-2z', 'M3.6 10.6h12.8a1.6 1.6 0 0 1 1.6 1.6v2.2a1.6 1.6 0 0 1-1.6 1.6H3.6z', 'M15.6 13.4h.9'],
    bag: ['M6 8h12l-1 11.4a1.6 1.6 0 0 1-1.6 1.4H8.6A1.6 1.6 0 0 1 7 19.4z', 'M9.4 8V6.4a2.6 2.6 0 0 1 5.2 0V8'],
    box: ['M12 3.4 20 7.4v9.2L12 20.6 4 16.6V7.4z', 'M4.2 7.4 12 11.4l7.8-4', 'M12 11.4v9.2'],
    box: ['M12 3.4 20 7.4v9.2L12 20.6 4 16.6V7.4z', 'M4.2 7.4 12 11.4l7.8-4', 'M12 11.4v9.2'],
    home: ['M4 10.6 12 4l8 6.6V20a.8.8 0 0 1-.8.8H4.8A.8.8 0 0 1 4 20z', 'M9.4 20.4v-6h5.2v6'],
    back: ['M14.5 5.5 8 12l6.5 6.5'],
    plus: ['M12 5.2v13.6', 'M5.2 12h13.6'],
    refresh: ['M19.4 12a7.4 7.4 0 1 1-2.4-5.5', 'M19.6 4.4v4.2h-4.2'],
    check: ['M5 12.5 9.6 17 19 6.8'],
    dot: ['M12 12h.01'],
    map: ['M3.6 6.4 9.4 4l5.2 2.4L20.4 4v13.6L14.6 20l-5.2-2.4-5.8 2.4z', 'M9.4 4v13.6', 'M14.6 6.4V20']
  };

  /* ------------------------------------------------------------------ *
   * 私人项目（B 的 private_project_service 消费端）
   *
   * 契约：docs/engineering/v04-private-projects-stage-result.md §3（本轮 P 自己的面）
   *   GET  /api/phone/projects                 列表（不含正文）
   *   GET  /api/phone/projects/<artifact_id>   详情 + 版本历史（含正文）
   *   POST /api/phone/projects                 {title, content, operation_key}
   *   POST /api/phone/projects/<id>/commits    {content, expected_head, operation_key}
   *   POST /api/phone/projects/<id>/close      {expected_revision, operation_key}
   *
   * 铁律：作品就是 Supply 的 ManagedArtifact；界面不猜项目、不刷新 version、不代签成功。
   *       CAS 用的是上一次读到的 head / revision；历史不全就照实说不全。
   * ------------------------------------------------------------------ */

  function projectRowView(raw) {
    const record = asRecord(raw);
    if (!record) return null;
    const id = protocolId(record.artifact_id, 200);
    if (!id.ok) return null;
    const lifecycle = typeof record.lifecycle === 'string' ? record.lifecycle : '';
    return {
      id: id.value,
      title: safeLabel(record.title, PROJECT_TITLE_MAX) || '未命名项目',
      lifecycle: lifecycle,
      status: lifecycle === 'ARCHIVED' ? 'archived' : 'active',
      revision: typeof record.revision === 'number' ? record.revision : null,
      head: typeof record.head_version_id === 'string' ? record.head_version_id : '',
      versionNumber: typeof record.version_number === 'number' ? record.version_number : null,
      createdAt: record.created_at,
      updatedAt: record.updated_at
    };
  }

  function projectListView(doc) {
    const record = asRecord(doc) || {};
    const raw = Array.isArray(record.projects) ? record.projects : null;
    const rows = [];
    (raw || []).forEach(function (item) {
      const row = projectRowView(item);
      if (row) rows.push(row);
    });
    const counts = asRecord(record.counts) || {};
    return {
      itemsMissing: raw === null,
      projects: rows,
      activeCount: typeof counts.active === 'number' ? counts.active : null,
      archivedCount: typeof counts.archived === 'number' ? counts.archived : null,
      excludedCount: Array.isArray(record.excluded_artifact_ids)
        ? record.excluded_artifact_ids.length : 0,
      truncated: record.truncated === true,
      source: typeof record.source === 'string' ? safeLabel(record.source, 40) : ''
    };
  }

  function projectHistoryView(history) {
    const record = asRecord(history) || {};
    const raw = Array.isArray(record.entries) ? record.entries : [];
    const entries = [];
    raw.forEach(function (item) {
      const row = asRecord(item);
      if (!row) return;
      const id = protocolId(row.version_id, 200);
      if (!id.ok) return;
      entries.push({
        versionId: id.value,
        versionNumber: typeof row.version_number === 'number' ? row.version_number : null,
        parent: typeof row.parent_version_id === 'string' ? row.parent_version_id : '',
        createdAt: row.created_at,
        chars: typeof row.content_chars === 'number' ? row.content_chars : null,
        isHead: row.is_head === true
      });
    });
    return {
      complete: record.complete === true,
      reason: typeof record.reason_code === 'string' ? record.reason_code : '',
      count: typeof record.count === 'number' ? record.count : entries.length,
      entries: entries
    };
  }

  function projectDetailView(doc) {
    const record = asRecord(doc) || {};
    const raw = asRecord(record.project);
    const project = projectRowView(raw);
    if (!project) return null;
    return {
      project: project,
      content: typeof raw.content === 'string' ? raw.content : '',
      contentAvailable: raw.content_available !== false,
      contentChars: typeof raw.content_chars === 'number' ? raw.content_chars : null,
      parentVersionId: typeof raw.parent_version_id === 'string' ? raw.parent_version_id : '',
      history: projectHistoryView(record.history)
    };
  }

  /* 一页版本元数据（GET /projects/<id>/versions）。正文永远不在这一层：
     拿不到 history 就返回 null，由界面照实说“服务没有返回历史”，不编空列表。 */
  function projectVersionPageView(doc) {
    const record = asRecord(doc) || {};
    const history = asRecord(record.history);
    if (!history) return null;
    const parsed = projectHistoryView(history);
    return {
      entries: parsed.entries,
      entriesMissing: !Array.isArray(history.entries),
      complete: parsed.complete,
      reason: parsed.reason,
      count: parsed.count,
      totalVersions: typeof history.total_versions === 'number' ? history.total_versions : null,
      hasMore: history.has_more === true,
      nextBefore: typeof history.next_before_version_number === 'number'
        && isFinite(history.next_before_version_number) ? history.next_before_version_number : null
    };
  }

  /* 单个历史版本（GET .../versions/<version_id>）：正文、字数、时间与截断都照服务说。 */
  function projectVersionView(doc) {
    const record = asRecord(doc) || {};
    const raw = asRecord(record.version);
    if (!raw) return null;
    const id = protocolId(raw.version_id, 200);
    return {
      versionId: id.ok ? id.value : '',
      versionNumber: typeof raw.version_number === 'number' ? raw.version_number : null,
      parentVersionId: typeof raw.parent_version_id === 'string' ? raw.parent_version_id : '',
      createdAt: raw.created_at,
      chars: typeof raw.content_chars === 'number' ? raw.content_chars : null,
      returnedChars: typeof raw.returned_chars === 'number' ? raw.returned_chars : null,
      truncated: raw.content_truncated === true,
      withheld: record.content_withheld === true || raw.derived_text_withheld === true
        || raw.content_available === false,
      content: typeof raw.content === 'string' ? raw.content : '',
      contentAvailable: raw.content_available !== false,
      lifecycle: typeof raw.lifecycle === 'string' ? raw.lifecycle : '',
      continuable: raw.continuable === true
    };
  }

  /* 旧版 vs 当前 head（GET .../versions/<version_id>/compare）。
     只搬服务真的给出的字段：服务没给就留 null，界面写“未提供”，不替它算差异。 */
  function projectCompareView(doc) {
    const record = asRecord(doc) || {};
    const raw = asRecord(record.compare);
    if (!raw) return null;
    const from = asRecord(raw.from) || {};
    const to = asRecord(raw.to) || {};
    const lines = asRecord(raw.lines) || {};
    const comparison = asRecord(raw.comparison) || {};
    const first = asRecord(raw.first_difference);
    return {
      from: {
        versionId: typeof from.version_id === 'string' ? from.version_id : '',
        versionNumber: typeof from.version_number === 'number' ? from.version_number : null,
        chars: typeof from.content_chars === 'number' ? from.content_chars : null,
        isHead: from.is_head === true,
        withheld: from.content_withheld === true
      },
      to: {
        versionId: typeof to.version_id === 'string' ? to.version_id : '',
        versionNumber: typeof to.version_number === 'number' ? to.version_number : null,
        chars: typeof to.content_chars === 'number' ? to.content_chars : null,
        isHead: to.is_head === true,
        withheld: to.content_withheld === true
      },
      bodyChanged: asBool(raw.body_changed),
      hashChanged: asBool(raw.hash_changed),
      contentChanged: asBool(raw.content_changed),
      headersDiffer: asBool(raw.headers_differ),
      machineHeadOnly: asBool(raw.machine_head_only),
      identicalVersion: raw.identical_version === true,
      charDelta: typeof raw.char_delta === 'number' ? raw.char_delta : null,
      addedLines: typeof lines.added_lines === 'number' ? lines.added_lines : null,
      removedLines: typeof lines.removed_lines === 'number' ? lines.removed_lines : null,
      boundedLines: lines.bounded_lines === true,
      firstDifference: first ? {
        index: typeof first.index === 'number' ? first.index : null,
        fromContext: typeof first.from_context === 'string' ? first.from_context : '',
        toContext: typeof first.to_context === 'string' ? first.to_context : ''
      } : null,
      basis: typeof comparison.basis === 'string' ? comparison.basis : '',
      fromTruncated: comparison.from_truncated === true,
      toTruncated: comparison.to_truncated === true,
      reason: typeof raw.reason_code === 'string' ? raw.reason_code : ''
    };
  }

  /* 把详情自带的 head 与分页拉回来的版本并成一张表：同一个 version_id 只留一次。 */
  function projectHistoryMerge(base, incoming) {
    const seen = Object.create(null);
    const merged = [];
    (Array.isArray(base) ? base : []).concat(Array.isArray(incoming) ? incoming : []).forEach(function (row) {
      const entry = asRecord(row);
      if (!entry || typeof entry.versionId !== 'string' || !entry.versionId) return;
      if (seen[entry.versionId]) return;
      seen[entry.versionId] = true;
      merged.push(entry);
    });
    merged.sort(function (a, b) {
      const left = typeof a.versionNumber === 'number' ? a.versionNumber : -1;
      const right = typeof b.versionNumber === 'number' ? b.versionNumber : -1;
      return right - left;
    });
    return merged;
  }

  function validateProjectDraft(draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    const title = typeof source.title === 'string' ? source.title.trim() : '';
    const content = typeof source.content === 'string' ? source.content : '';
    if (!title) errors.push('标题不能为空');
    else if (title.length > PROJECT_TITLE_MAX) errors.push('标题超过 ' + PROJECT_TITLE_MAX + ' 字');
    const text = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    if (!text.trim()) errors.push('正文不能为空');
    if (text.indexOf('\u0000') >= 0) errors.push('正文里不能有控制字符');
    if (text.length > PROJECT_CONTENT_MAX) errors.push('正文超过 ' + PROJECT_CONTENT_MAX + ' 字');
    return {
      ok: errors.length === 0,
      errors: errors,
      value: {title: title.slice(0, PROJECT_TITLE_MAX), content: text}
    };
  }

  function buildProjectCreatePayload(input) {
    const source = asRecord(input) || {};
    const payload = {
      title: typeof source.title === 'string' ? source.title.trim() : '',
      content: typeof source.content === 'string' ? source.content : '',
      operation_key: typeof source.operation_key === 'string' ? source.operation_key.trim() : ''
    };
    if (!payload.title || !payload.content.trim() || !payload.operation_key) return null;
    return payload;
  }

  function buildProjectCommitPayload(input) {
    const source = asRecord(input) || {};
    const payload = {
      content: typeof source.content === 'string' ? source.content : '',
      expected_head: typeof source.expected_head === 'string' ? source.expected_head : '',
      operation_key: typeof source.operation_key === 'string' ? source.operation_key.trim() : ''
    };
    /* 没有 head 就不发：那等于关掉 CAS，宁可不写。 */
    if (!payload.content.trim() || !payload.expected_head || !payload.operation_key) return null;
    return payload;
  }

  function buildProjectClosePayload(input) {
    const source = asRecord(input) || {};
    const revision = source.expected_revision;
    const key = typeof source.operation_key === 'string' ? source.operation_key.trim() : '';
    if (typeof revision !== 'number' || !isFinite(revision) || revision < 1 || !key) return null;
    return {expected_revision: Math.floor(revision), operation_key: key};
  }

  /* 派生是写：新作品的标题/正文 + 它取自哪个工件的哪一版。
     note 是给人看的一句话（≤120），不是认证；缺来源、缺键就不发。 */
  function buildProjectDerivePayload(input) {
    const source = asRecord(input) || {};
    const title = typeof source.title === 'string' ? source.title.trim() : '';
    const content = typeof source.content === 'string' ? source.content : '';
    const artifactId = projectPathId(source.source_artifact_id);
    const versionId = projectPathId(source.source_version_id);
    const purpose = typeof source.purpose === 'string' ? source.purpose.trim().toLowerCase() : '';
    const note = typeof source.note === 'string' ? source.note.trim() : '';
    const key = typeof source.operation_key === 'string' ? source.operation_key.trim() : '';
    if (!title || !content.trim() || !artifactId || !versionId) return null;
    if (PROJECT_SOURCE_PURPOSES.indexOf(purpose) < 0) return null;
    if (note.length > PROJECT_DERIVE_NOTE_MAX || !key) return null;
    return {
      title: title,
      content: content,
      source_artifact_id: artifactId,
      source_version_id: versionId,
      purpose: purpose,
      note: note,
      operation_key: key
    };
  }

  /* 派生草稿：标题“原标题（派生）”，正文是那一版读回来的原文。
     正文被截断/收回时不在这里假装完整版——调用方会禁用派生按钮。 */
  function projectDeriveDraft(project, version) {
    const source = asRecord(project);
    const old = asRecord(version);
    if (!source || !old) return null;
    const artifactId = projectPathId(source.id);
    const versionId = projectPathId(old.versionId);
    if (!artifactId || !versionId) return null;
    const number = typeof old.versionNumber === 'number' ? old.versionNumber : null;
    return {
      mode: 'derive',
      artifactId: artifactId,
      sourceVersionId: versionId,
      sourceVersionNumber: number,
      sourceTitle: sanitizeLine(source.title, PROJECT_TITLE_MAX) || '未命名项目',
      title: projectDeriveTitle(source.title),
      content: typeof old.content === 'string' ? old.content : '',
      note: number === null ? '派生自这一版' : '派生自 v' + number,
      purpose: 'adapt',
      head: '',
      revision: null,
      errors: []
    };
  }

  function projectDeriveTitle(title) {
    const suffix = '（派生）';
    const base = typeof title === 'string' ? title.trim() : '';
    const room = Math.max(1, PROJECT_TITLE_MAX - suffix.length);
    return (base ? base.slice(0, room) : '未命名项目') + suffix;
  }

  /* 服务端路由原样接受的那套字符；不做 URL 编码、不猜别的 id 形状。 */
  function projectPathId(value) {
    const id = protocolId(value, 200);
    if (!id.ok || !/^[A-Za-z0-9._:\-]+$/.test(id.value)) return '';
    return id.value;
  }

  function projectPath(artifactId) {
    const id = projectPathId(artifactId);
    /* artifact:<hex> 里的冒号必须原样进路径，否则服务端认不出这一件。 */
    if (!id) return '';
    return '/projects/' + id;
  }

  function projectVersionsPath(artifactId, options) {
    const base = projectPath(artifactId);
    if (!base) return '';
    const query = [];
    const limit = options && options.limit;
    const before = options && options.before;
    if (typeof limit === 'number' && isFinite(limit) && limit > 0) {
      query.push('limit=' + Math.floor(limit));
    }
    if (typeof before === 'number' && isFinite(before) && before > 0) {
      query.push('before=' + Math.floor(before));
    }
    return base + '/versions' + (query.length ? '?' + query.join('&') : '');
  }

  function projectVersionPath(artifactId, versionId) {
    const base = projectPath(artifactId);
    const version = projectPathId(versionId);
    if (!base || !version) return '';
    return base + '/versions/' + version;
  }

  function projectComparePath(artifactId, versionId) {
    const path = projectVersionPath(artifactId, versionId);
    return path ? path + '/compare' : '';
  }

  /* ---------------- 分享（B 的 LifeSharingService；这里只做搬运与校验） ----------------
     铁律：
       * 列表永远不带正文；正文只在详情里、且只在来源现在仍然可读时出现。
       * 受众与渠道是服务端的固定值（local:owner / local_inbox），界面从不提交它们。
       * 同一个操作键对应一个意图：CAS 冲突 / 未知结果都留着原键，不偷偷换键。
       * 「本地收件箱」= 本实例本地存档，不是外部已发送，更不是用户已读。 */

  function shareStateText(state) {
    const key = asString(state);
    return SHARE_STATE_TEXT[key] || (key ? safeLabel(key, 20) : '服务没有说状态');
  }

  /* 列表视图：只有元数据 + 来源当前性。没有 text 字段，也不生造一条正文。 */
  function shareListView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!envelope) return null;
    const rows = data && Array.isArray(data.shares) ? data.shares.filter(asRecord) : [];
    const counts = data ? asRecord(data.counts) : null;
    const inbox = data ? asRecord(data.inbox) : null;
    const items = rows.map(function (row) {
      const state = asString(row.state);
      const shareId = protocolId(row.share_id, 200);
      return {
        shareId: shareId.ok ? shareId.value : '',
        shareLabel: safeLabel(row.share_id, 40),
        idOk: shareId.ok,
        state: state,
        stateLabel: shareStateText(state),
        stateDerived: row.state_derived === true,
        title: safeLabel(row.title, 60) || '（没有标题）',
        head: protocolId(row.head_version_id, 256),
        headOk: protocolId(row.head_version_id, 256).ok,
        updatedAt: asString(row.updated_at),
        lifecycle: asString(row.lifecycle),
        delivered: row.delivered === true,
        hasReceipt: row.has_receipt === true,
        textChars: asCount(row.text_chars),
        textWithheld: row.text_withheld === true,
        textAvailable: row.text_available !== false,
        sourceState: asString(row.source_state),
        sourceReason: asString(row.source_reason_code),
        source: asRecord(row.source),
        sendable: row.sendable === true,
        actions: (Array.isArray(row.actions) ? row.actions : []).filter(function (action) {
          return SHARE_ACTION_VALUES.indexOf(asString(action)) >= 0;
        })
      };
    });
    return {
      ok: envelope.ok === true,
      status: asString(envelope.status),
      reason: asString(envelope.reason_code),
      items: items,
      counts: counts ? {
        total: asCount(counts.total) || 0,
        draft: asCount(counts.draft) || 0,
        deferred: asCount(counts.deferred) || 0,
        dropped: asCount(counts.dropped) || 0,
        expired: asCount(counts.expired) || 0,
        localDelivered: asCount(counts.local_delivered) || 0
      } : null,
      inbox: inbox ? {
        channel: asString(inbox.channel) || 'local_inbox',
        audience: asString(inbox.audience) || 'local:owner',
        deliveredCount: asCount(inbox.delivered_count) || 0,
        localOnly: inbox.local_only !== false
      } : null,
      truncated: data ? data.truncated === true : false,
      withheldCount: data ? (asCount(data.withheld_count) || 0) : 0,
      privateTextIncluded: data ? data.private_text_included === true : false,
      localOnly: data ? data.local_only !== false : true,
      externalSend: data ? data.external_send === true : false,
      note: data ? sanitizeLine(data.note, 200) : '',
      owner: data ? asRecord(data.owner) : null
    };
  }

  /* 详情视图：状态 / 来源当前性 / 等待理由 / 收据 / （来源仍可读时的）有界正文。 */
  function shareDetailView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    const share = data ? asRecord(data.share) : null;
    if (!envelope || !share) return null;
    const state = asString(share.state);
    const sourceCurrent = data ? asRecord(data.source_current) : null;
    const receipt = data ? asRecord(data.receipt) : null;
    const deferred = asRecord(share.deferred);
    const dropped = asRecord(share.dropped);
    const expired = asRecord(share.expired);
    return {
      ok: envelope.ok === true,
      status: asString(envelope.status),
      reason: asString(envelope.reason_code),
      shareId: protocolId(share.share_id, 200),
      shareLabel: safeLabel(share.share_id, 40),
      state: state,
      stateLabel: shareStateText(state),
      stateDerived: share.state_derived === true,
      title: safeLabel(share.title, 60) || '（没有标题）',
      text: typeof share.text === 'string' ? share.text : '',
      textChars: asCount(share.text_chars),
      textWithheld: share.text_withheld === true,
      textAvailable: share.text_available !== false,
      head: asString(share.head_version_id),
      shareRevision: asCount(share.share_revision),
      audience: asString(share.audience),
      channel: asString(share.channel),
      createdAt: asString(share.created_at),
      updatedAt: asString(share.updated_at),
      expiresAt: asString(share.expires_at),
      source: asRecord(share.source),
      sourceCurrent: sourceCurrent ? {
        state: asString(sourceCurrent.state),
        readable: sourceCurrent.readable === true,
        governed: sourceCurrent.governed === true,
        stale: sourceCurrent.stale === true,
        matchesRecorded: sourceCurrent.matches_recorded !== false,
        reason: asString(sourceCurrent.reason_code),
        lifecycle: asString(sourceCurrent.lifecycle)
      } : null,
      deferred: deferred ? {
        condition: sanitizeLine(deferred.condition, SHARE_CONDITION_MAX),
        opportunityRef: sanitizeLine(deferred.opportunity_ref, SHARE_CONDITION_MAX),
        at: asString(deferred.deferred_at)
      } : null,
      dropped: dropped ? {reason: sanitizeLine(dropped.reason, SHARE_REASON_MAX),
        at: asString(dropped.at)} : null,
      expired: expired ? {reason: sanitizeLine(expired.reason, SHARE_REASON_MAX),
        at: asString(expired.at)} : null,
      receipt: receipt ? {
        deliveredAt: asString(receipt.delivered_at),
        channel: asString(receipt.channel),
        audience: asString(receipt.audience),
        recipient: asString(receipt.recipient),
        localOnly: receipt.local_only !== false,
        externalSent: receipt.external_channel_sent === true,
        externalConfirmed: receipt.external_delivery_confirmed === true,
        readByAudience: receipt.read_by_audience === true,
        acknowledged: receipt.acknowledged === true,
        note: sanitizeLine(receipt.note, 200)
      } : null,
      delivered: data.delivered === true,
      terminal: share.terminal === true || state === 'DROPPED' || state === 'EXPIRED',
      sendable: share.sendable === true,
      actions: (Array.isArray(share.actions) ? share.actions : []).filter(function (action) {
        return SHARE_ACTION_VALUES.indexOf(asString(action)) >= 0;
      }),
      withheldReason: asString(data.withheld_reason_code),
      privateTextIncluded: data.private_text_included === true,
      localOnly: data.local_only !== false,
      note: data ? sanitizeLine(data.note, 200) : '',
      owner: data ? asRecord(data.owner) : null
    };
  }

  /* 可选来源：只列服务真的读得到的作品/阅读绑定；读不到的照实带原因，不当成可选。 */
  function shareSourcesView(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!envelope) return null;
    const raw = data && Array.isArray(data.sources) ? data.sources.filter(asRecord) : [];
    return {
      ok: envelope.ok === true,
      status: asString(envelope.status),
      reason: asString(envelope.reason_code),
      items: raw.map(function (row) {
        const id = protocolId(row.artifact_id, 200);
        const kind = asString(row.kind);
        return {
          artifactId: id.ok ? id.value : '',
          idOk: id.ok,
          label: safeLabel(row.artifact_id, 40),
          title: safeLabel(row.title, 60) || '（没有标题）',
          /* 来源当前 head：共同实践选材时它就是「你看到的那个版本」的凭据。 */
          versionId: safeLabel(row.head_version_id, 60),
          kind: kind,
          kindLabel: SHARE_SOURCE_KIND_TEXT[kind] || safeLabel(kind, 20),
          readable: row.readable === true,
          lifecycle: asString(row.lifecycle),
          reason: asString(row.reason_code),
          revision: asCount(row.revision)
        };
      }),
      truncated: data ? data.truncated === true : false,
      privateTextIncluded: data ? data.private_text_included === true : false
    };
  }

  /* 路径里的 owner 标识：除了 protocolId 的类型/长度/控制字符，还不许出现路径或空白字符
     （一个带 '/' 的 id 会被拼成另一条路，等于把本地猜测当成服务事实）。 */
  function shareIdOk(value, maxLen) {
    const check = protocolId(value, maxLen);
    if (!check.ok) return check;
    if (/[\s/?#]/.test(check.value)) {
      return {ok: false, value: '', reason: 'NOT_AN_ID', message: '标识里有路径或空白字符'};
    }
    return check;
  }

  function sharePath(shareId) {
    const id = shareIdOk(shareId, 200);
    return id.ok ? '/shares/' + id.value : '';
  }

  function shareActionsPath(shareId) {
    const path = sharePath(shareId);
    return path ? path + '/actions' : '';
  }

  function shareOperationPath(key) {
    const check = shareIdOk(key, 128);
    return check.ok ? '/shares/operations/' + check.value : '';
  }

  function shareSourcesPath() {
    return '/shares/sources';
  }

  /* 一个意图一个键：同一条分享的同一个动作永远落在同一个 scope 上（换动作才换键）。 */
  function shareActionScope(shareId, action) {
    const id = shareIdOk(shareId, 200);
    const name = asString(action);
    if (!id.ok || SHARE_ACTION_VALUES.indexOf(name) < 0) return '';
    return 'share:' + name + ':' + id.value;
  }

  /* 延后 / 发送 / 放弃的请求体：字段按契约精确重建，多余的一律不发。 */
  function buildShareActionPayload(input) {
    const source = asRecord(input);
    if (!source) return null;
    const action = asString(source.action);
    if (SHARE_ACTION_VALUES.indexOf(action) < 0) return null;
    const head = protocolId(source.expected_head, 256);
    const key = protocolId(source.operation_key, 128);
    if (!head.ok || !key.ok) return null;
    const payload = {action: action, expected_head: head.value, operation_key: key.value};
    if (action === 'defer') {
      const condition = asString(source.defer_condition).trim().slice(0, SHARE_CONDITION_MAX);
      const opportunity = asString(source.opportunity_ref).trim().slice(0, SHARE_CONDITION_MAX);
      if (!condition && !opportunity) return null;
      if (condition) payload.defer_condition = condition;
      if (opportunity) payload.opportunity_ref = opportunity;
    } else {
      const reason = asString(source.reason).trim().slice(0, SHARE_REASON_MAX);
      if (reason) payload.reason = reason;
    }
    return payload;
  }

  /* 草稿校验：必须**明确**一段短文或明确的前缀长度；绝不默认拿整篇私人材料。 */
  function validateShareDraft(draft) {
    const source = asRecord(draft) || {};
    const errors = [];
    const sourceId = protocolId(source.source_artifact_id, 200);
    if (!sourceId.ok) errors.push('先选一条现在读得到的来源');
    const text = asString(source.share_text).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    const rawExcerpt = source.excerpt_chars;
    const hasText = text.trim().length > 0;
    const hasExcerpt = rawExcerpt !== null && rawExcerpt !== undefined && String(rawExcerpt).trim() !== '';
    if (hasText && hasExcerpt) errors.push('短文与前缀长度只能选一个');
    if (!hasText && !hasExcerpt) errors.push('要写一句明确的分享文本，或指定来源前缀长度');
    if (hasText && text.length > SHARE_TEXT_MAX) errors.push('分享文本最多 ' + SHARE_TEXT_MAX + ' 字');
    if (hasText && text.indexOf('\u0000') >= 0) errors.push('分享文本不能包含控制字符');
    let excerpt = null;
    if (hasExcerpt) {
      const value = Number(rawExcerpt);
      if (!Number.isInteger(value) || value < 1 || value > SHARE_EXCERPT_MAX) {
        errors.push('前缀长度要是 1–' + SHARE_EXCERPT_MAX + ' 的整数');
      } else {
        excerpt = value;
      }
    }
    const title = asString(source.title).trim();
    if (title.length > SHARE_TITLE_MAX) errors.push('标题最多 ' + SHARE_TITLE_MAX + ' 字');
    let expires = null;
    const rawExpires = source.expires_after_s;
    if (rawExpires !== null && rawExpires !== undefined && String(rawExpires).trim() !== '') {
      const value = Number(rawExpires);
      if (!Number.isInteger(value) || value < SHARE_EXPIRY_MIN || value > SHARE_EXPIRY_MAX) {
        errors.push('过期窗口要是 ' + SHARE_EXPIRY_MIN + '–' + SHARE_EXPIRY_MAX + ' 秒的整数');
      } else {
        expires = value;
      }
    }
    return {
      ok: errors.length === 0,
      errors: errors,
      value: {
        source_artifact_id: sourceId.ok ? sourceId.value : '',
        share_text: hasText ? text : '',
        excerpt_chars: excerpt,
        title: title,
        expires_after_s: expires
      }
    };
  }

  function buildSharePreparePayload(input) {
    const source = asRecord(input);
    if (!source) return null;
    const key = protocolId(source.operation_key, 128);
    if (!key.ok) return null;
    const checked = validateShareDraft(source);
    if (!checked.ok) return null;
    const payload = {source_artifact_id: checked.value.source_artifact_id,
      operation_key: key.value};
    if (checked.value.share_text) {
      payload.share_text = checked.value.share_text;
    } else if (checked.value.excerpt_chars !== null) {
      payload.excerpt_chars = checked.value.excerpt_chars;
    } else {
      return null;
    }
    if (checked.value.title) payload.title = checked.value.title;
    if (checked.value.expires_after_s !== null) payload.expires_after_s = checked.value.expires_after_s;
    return payload;
  }

  /* 原 key 的只读对账结果 → 人话。绝不在这里换键，也不冒充「已生效」。 */
  function shareOperationLines(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!data) return [];
    const lines = [];
    const key = asString(data.operation_key);
    if (key) lines.push('这条键（原样）：' + key);
    const records = Array.isArray(data.records) ? data.records.filter(asRecord) : [];
    if (!records.length) {
      lines.push('手机这边没有这条键的记录：这次请求可能根本没到服务。');
    } else {
      const last = records[records.length - 1];
      lines.push('手机记录：' + safeLabel(last.action, 60) + ' → ' + safeLabel(last.status, 30) +
        (asString(last.reason_code) ? '（' + asString(last.reason_code) + '）' : ''));
    }
    const shares = Array.isArray(data.shares) ? data.shares.filter(asRecord) : [];
    if (shares.length) {
      lines.push('现在读到的分享状态：' + shares.slice(0, 5).map(function (row) {
        return shareStateText(row.state) + (row.delivered === true ? '（本地收件箱）' : '');
      }).join('、'));
    }
    if (data.shares_ok === false) lines.push('分享列表这次没读到：' + safeLabel(data.shares_reason_code, 40));
    lines.push('对账只是读：没有用新键重发，也没有改写这条分享。');
    return lines;
  }

  function h(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        const value = attrs[key];
        if (value === null || value === undefined || value === false) return;
        if (key === 'text') { node.textContent = String(value); return; }
        if (key === 'class') { node.className = value; return; }
        if (key === 'on') {
          Object.keys(value).forEach(function (eventName) { node.addEventListener(eventName, value[eventName]); });
          return;
        }
        if (key === 'dataset') {
          Object.keys(value).forEach(function (dataKey) { node.dataset[dataKey] = String(value[dataKey]); });
          return;
        }
        if (key === 'disabled') { node.disabled = !!value; return; }
        node.setAttribute(key, String(value));
      });
    }
    if (children) appendAll(node, children);
    return node;
  }

  /* 子节点允许嵌套数组；非节点值一律转成文本节点，绝不当 HTML 解析。 */
  function appendAll(node, children) {
    const list = Array.isArray(children) ? children : [children];
    for (let i = 0; i < list.length; i += 1) {
      const child = list[i];
      if (child === null || child === undefined || child === false) continue;
      if (Array.isArray(child)) {
        appendAll(node, child);
      } else if (typeof child === 'object') {
        node.append(child);
      } else {
        node.append(document.createTextNode(String(child)));
      }
    }
  }

  function icon(name) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    (ICONS[name] || ICONS.dot).forEach(function (d) {
      const path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('d', d);
      svg.append(path);
    });
    return svg;
  }

  function $(id) {
    return document.getElementById(id);
  }

  const store = {
    conn: {phase: 'loading', detail: '正在读取服务状态…', instance: null, apps: {}, csrf: null, readOnly: true},
    screen: 'home',
    stack: [],
    shopDetailId: null,
    storeDetailId: null,
    projectDetailId: null,
    albumDetailId: null,
    /* 相册导入的预览与字节只活在内存里：不落 sessionStorage，也不写 localStorage。 */
    albumBroken: Object.create(null),
    /* 项目详情页上的“全部版本 / 这一版 / 对比”都只活在内存里，不落 sessionStorage。 */
    projectHistory: null,
    projectVersion: null,
    projectCompare: null,
    materialDetailId: null,
    researchDetailId: null,
    practiceDetailId: null,
    practiceExperienceId: null,
    shareDetailId: null,
    contactDetailRef: null,
    sharedPracticeDetailId: null,
    data: {living: null, notes: null, references: null, reading: null, readingMaterial: null, wallet: null, shop: null,
      inventory: null, world: null, store: null, projects: null, project: null,
      album: null, albumImage: null,
      materials: null, material: null, research: null, researchItem: null,
      practice: null, practiceReview: null, practiceMethod: null,
      experiences: null, experience: null, memory: null,
      shares: null, share: null, shareSources: null, contact: null, contactEvent: null,
      sharedPractices: null, sharedPractice: null, sharedPracticeSources: null},
    editor: null,
    /* rc1 阅读工作台的草稿只在内存里：正文/笔记绝不进 sessionStorage。 */
    readingDraft: {title: '', body: '', note: '', importText: '', errors: []},
    /* 项目草稿同样只在内存里；title/content 绝不进 sessionStorage。 */
    projectDraft: {mode: 'create', artifactId: '', title: '', content: '', head: '', revision: null, errors: []},
    /* 相册导入草稿：说明、预览 data URL 与 base64 字节只在内存里，刷新即丢弃。 */
    albumDraft: {caption: '', fileName: '', contentType: '', dataUrl: '', data: '',
      bytes: 0, previewOk: false, previewError: '', errors: []},
    /* 资料/研究草稿也只在内存里：正文、来源声明绝不进 sessionStorage。 */
    materialDraft: {title: '', text: '', source_kind: 'user_supplied', origin: '', query: '', errors: []},
    researchDraft: {mode: 'create', artifactId: '', title: '', question: '', scope: '',
      uncertainty: '', content: '', head: '', revision: null, sources: [], errors: []},
    /* 实践草稿：回顾/做法/试用/修订都只在内存里，正文与元数据绝不进 sessionStorage。 */
    practiceDraft: {mode: 'review-new', artifactId: '', kind: 'review', title: '',
      question: '', instruction: '', scope: '', uncertainty: '', content: '',
      outcome: '', detail: '', head: '', revision: null,
      experience_ids: [], review_refs: [], errors: []},
    memoryDraft: {query: '', errors: []},
    /* 分享草稿只在内存里：正文、来源、标题绝不进 sessionStorage。 */
    shareDraft: {source_artifact_id: '', share_text: '', excerpt_chars: '',
      title: '', expires_after_s: '', errors: []},
    readingAnchor: null,
    /* 分享动作那一行输入（延后的机会 / 放弃的理由）也只在内存里。 */
    shareActionNote: '',
    /* 来访草稿只在内存里：正文与范围绝不进任何 storage。
       三种意图各有自己的槽（同一屏上它们同时可见，共用一份会互相清掉）。 */
    contactDrafts: Object.create(null),
    /* 共同实践草稿：只在内存里；用户自己的正文绝不进任何 storage。 */
    sharedPracticeDraft: {mode: 'open', practiceId: '', source_artifact_id: '',
      source_version_id: '', source_title: '', action: 'submit', text: '', reason: '',
      head: '', errors: []},
    sharedPracticeDrafts: Object.create(null),
    result: null,
    busy: null,
    log: []
  };

  const dom = {ready: false};
  let ops = null;
  let navStore = null;

  function boot() {
    dom.clock = $('clock');
    dom.dot = $('conn-dot');
    dom.head = $('screen-head');
    dom.body = $('screen-body');
    dom.sheetHost = $('sheet-host');
    dom.homeBtn = $('btn-home');
    dom.backBtn = $('btn-back');
    dom.navCaption = $('nav-caption');
    dom.instanceFacts = $('instance-facts');
    dom.connLine = $('conn-line');
    dom.connDetail = $('conn-detail');
    dom.refreshBtn = $('btn-refresh');
    dom.unresolvedList = $('unresolved-list');
    dom.requestLog = $('request-log');
    dom.ready = true;

    ops = createOpStore(readSessionStorage());
    navStore = createNavStore(readSessionStorage());

    dom.homeBtn.prepend(icon('home'));
    dom.backBtn.prepend(icon('back'));
    dom.refreshBtn.addEventListener('click', function () { loadState(); });
    dom.homeBtn.addEventListener('click', goHome);
    dom.backBtn.addEventListener('click', goBack);
    /* 异步刷新（例如「此刻」读到结果）不能把用户正按着的按钮换掉：那会吞掉这一次 click。
       指针按着时先挂起整屏重建，抬起后再补一次。 */
    document.addEventListener('pointerdown', function () { store.pointerDown = true; }, true);
    document.addEventListener('pointerup', function () {
      store.pointerDown = false;
      releaseDeferredRender();
    }, true);
    document.addEventListener('pointercancel', function () {
      store.pointerDown = false;
      releaseDeferredRender();
    }, true);
    /* 指针在窗口外抬起时 document 收不到 pointerup：失焦也要把挂起的重建补上。 */
    root.addEventListener('blur', function () {
      store.pointerDown = false;
      releaseDeferredRender();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeSheet();
    });
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) tickClock();
    });

    startClock();
    restoreNav();
    renderAll();
    loadState();
  }

  function readSessionStorage() {
    try {
      return root.sessionStorage;
    } catch (error) {
      return null;
    }
  }

  function pushLog(method, path, status, outcome, ms) {
    store.log.push({method: method, path: path, status: status, outcome: outcome, ms: ms, at: Date.now()});
    if (store.log.length > MAX_REQUEST_LOG) store.log.shift();
    if (dom.ready) renderRequestLog();
  }

  async function request(method, path, options) {
    options = options || {};
    const started = Date.now();
    let init;
    try {
      init = buildRequestInit({method: method, csrf: options.csrf, body: options.body});
    } catch (error) {
      pushLog(method, path, '—', 'blocked:' + ((error && error.message) || 'error'), 0);
      return {kind: 'blocked', reason: (error && error.message) || 'BLOCKED'};
    }
    const fetchFn = typeof root.fetch === 'function' ? root.fetch.bind(root) : null;
    if (!fetchFn) {
      pushLog(method, path, '—', 'no-fetch', 0);
      return {kind: 'blocked', reason: 'NO_FETCH'};
    }
    let timer = null;
    if (typeof root.AbortController === 'function') {
      const controller = new root.AbortController();
      init.signal = controller.signal;
      timer = root.setTimeout(function () {
        try { controller.abort(); } catch (error) { /* ignore */ }
      }, method === 'GET' ? READ_TIMEOUT_MS : WRITE_TIMEOUT_MS);
    }
    try {
      const response = await fetchFn(API_BASE + path, init);
      const text = await response.text();
      let doc = null;
      let parseFailed = false;
      if (text) {
        try { doc = JSON.parse(text); } catch (error) { parseFailed = true; }
      }
      const outcome = parseFailed ? 'bad-json' : (doc && doc.status ? String(doc.status) : (response.ok ? 'ok' : 'http-' + response.status));
      pushLog(method, path, String(response.status), outcome, Date.now() - started);
      if (parseFailed) return {kind: 'unparsable', status: response.status, ok: response.ok};
      return {kind: 'http', status: response.status, ok: response.ok, doc: doc};
    } catch (error) {
      const timedOut = !!(error && error.name === 'AbortError');
      pushLog(method, path, '—', timedOut ? 'timeout' : 'network', Date.now() - started);
      return {kind: 'network', cause: timedOut ? 'timeout' : 'network'};
    } finally {
      if (timer !== null) root.clearTimeout(timer);
    }
  }

  function startClock() {
    tickClock();
    root.setInterval(tickClock, 10000);
  }

  function tickClock() {
    if (!dom.clock) return;
    const now = new Date();
    dom.clock.textContent = clockText(now);
    dom.clock.setAttribute('datetime', now.toISOString());
  }

  async function loadState() {
    store.conn = {phase: 'loading', detail: '正在读取服务状态…', instance: store.conn.instance, apps: store.conn.apps, csrf: null, readOnly: true};
    renderAll();
    const res = await request('GET', '/state', {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      const view = stateView(outcome.doc);
      store.conn = {
        phase: view.phase,
        detail: view.detail,
        instance: view.instance,
        apps: view.apps,
        csrf: view.csrf,
        readOnly: view.readOnly
      };
    } else if (outcome.status === 'offline') {
      store.conn = {phase: 'offline', detail: outcome.message, instance: null, apps: {}, csrf: null, readOnly: true};
    } else {
      const httpNote = res && typeof res.status === 'number' ? 'HTTP ' + res.status : '';
      store.conn = {
        phase: 'error',
        detail: [outcome.message, httpNote].filter(Boolean).join(' · '),
        instance: null,
        apps: {},
        csrf: null,
        readOnly: true
      };
    }
    renderAll();
    ensureLoaded(store.screen);
  }

  /* ---------------- 导航 ---------------- */

  function restoreNav() {
    if (!navStore) return;
    const saved = navStore.read();
    if (!saved) return;
    store.screen = saved.screen;
    store.shopDetailId = saved.screen === 'shop-item' ? (saved.detail || null) : null;
    store.storeDetailId = saved.screen === 'store-item' ? (saved.detail || null) : null;
    store.projectDetailId = (saved.screen === 'project' || saved.screen === 'project-edit')
      ? (saved.detail || null) : null;
    store.albumDetailId = saved.screen === 'album-image' ? (saved.detail || null) : null;
    store.materialDetailId = saved.screen === 'material' ? (saved.detail || null) : null;
    store.researchDetailId = (saved.screen === 'research-item' || saved.screen === 'research-edit')
      ? (saved.detail || null) : null;
    store.shareDetailId = saved.screen === 'share' ? (saved.detail || null) : null;
    store.contactDetailRef = saved.screen === 'contact-event' ? (saved.detail || null) : null;
    store.sharedPracticeDetailId = saved.screen === 'shared-practice-work'
      ? (saved.detail || null) : null;
  }

  function persistNav() {
    if (!navStore) return;
    navStore.save({
      screen: store.screen === 'note-edit' ? 'notes' : store.screen,
      detail: ((store.screen === 'store-item' ? store.storeDetailId
        : (store.screen === 'project' || store.screen === 'project-edit') ? store.projectDetailId
          : store.screen === 'album-image' ? store.albumDetailId
          : store.screen === 'material' ? store.materialDetailId
            : (store.screen === 'research-item' || store.screen === 'research-edit')
              ? store.researchDetailId
              : store.screen === 'share' ? store.shareDetailId
                : store.screen === 'contact-event' ? store.contactDetailRef
                  : store.screen === 'shared-practice-work' ? store.sharedPracticeDetailId
                    : store.shopDetailId) || '')
    });
  }

  function go(screen, options) {
    options = options || {};
    if (!SCREEN_META[screen]) return;
    const detailField = DETAIL_FIELDS[screen] || '';
    const targetDetail = Object.prototype.hasOwnProperty.call(options, 'detail')
      ? (options.detail || null)
      : (detailField ? store[detailField] : null);
    if (screen === store.screen) {
      if (detailField) store[detailField] = targetDetail;
      /* 再点一次同一屏也要走一遍读取：身体/世界这种「上次没读到」的屏，点它就该自己重试，
         而不是把用户逼回别的屏绕一圈。 */
      ensureLoaded(screen);
      renderAll();
      return;
    }
    store.stack.push({
      screen: store.screen,
      detail: store.screen === 'store-item' ? store.storeDetailId : (store.screen === 'shop-item' ? store.shopDetailId : null),
      projectDetail: (store.screen === 'project' || store.screen === 'project-edit') ? store.projectDetailId : null,
      materialDetail: store.screen === 'material' ? store.materialDetailId : null,
      researchDetail: (store.screen === 'research-item' || store.screen === 'research-edit')
        ? store.researchDetailId : null,
      practiceDetail: PRACTICE_DETAIL_SCREENS.indexOf(store.screen) >= 0
        ? store.practiceDetailId : null,
      practiceExperience: store.screen === 'practice-experience'
        ? store.practiceExperienceId : null,
      shareDetail: store.screen === 'share' ? store.shareDetailId : null,
      contactDetail: store.screen === 'contact-event' ? store.contactDetailRef : null,
      sharedPracticeDetail: store.screen === 'shared-practice-work'
        ? store.sharedPracticeDetailId : null,
      albumDetail: store.screen === 'album-image' ? store.albumDetailId : null
    });
    store.screen = screen;
    store.shopDetailId = screen === 'shop-item' ? targetDetail : null;
    store.storeDetailId = screen === 'store-item' ? targetDetail : null;
    store.projectDetailId = (screen === 'project' || screen === 'project-edit') ? targetDetail : null;
    store.materialDetailId = screen === 'material' ? targetDetail : null;
    store.researchDetailId = (screen === 'research-item' || screen === 'research-edit')
      ? targetDetail : null;
    store.practiceDetailId = PRACTICE_DETAIL_SCREENS.indexOf(screen) >= 0 ? targetDetail : null;
    store.shareDetailId = screen === 'share' ? targetDetail : null;
    store.contactDetailRef = screen === 'contact-event' ? targetDetail : null;
    store.sharedPracticeDetailId = screen === 'shared-practice-work' ? targetDetail : null;
    store.albumDetailId = screen === 'album-image' ? targetDetail : null;
    if (!options.keepResult) store.result = null;
    persistNav();
    ensureLoaded(screen);
    renderAll();
  }

  function goHome() {
    closeSheet();
    store.stack = [];
    store.screen = 'home';
    store.shopDetailId = null;
    store.storeDetailId = null;
    store.projectDetailId = null;
    store.materialDetailId = null;
    store.researchDetailId = null;
    store.practiceDetailId = null;
    store.practiceExperienceId = null;
    store.shareDetailId = null;
    store.contactDetailRef = null;
    store.albumDetailId = null;
    store.result = null;
    persistNav();
    renderAll();
  }

  function goBack() {
    closeSheet();
    store.result = null;
    const previous = store.stack.pop();
    if (!previous) {
      goHome();
      return;
    }
    store.screen = previous.screen;
    store.shopDetailId = previous.screen === 'shop-item' ? (previous.detail || null) : null;
    store.storeDetailId = previous.screen === 'store-item' ? (previous.detail || null) : null;
    store.projectDetailId = (previous.screen === 'project' || previous.screen === 'project-edit')
      ? (previous.projectDetail || null) : null;
    store.materialDetailId = previous.screen === 'material'
      ? (previous.materialDetail || null) : null;
    store.researchDetailId = (previous.screen === 'research-item' || previous.screen === 'research-edit')
      ? (previous.researchDetail || null) : null;
    store.practiceDetailId = PRACTICE_DETAIL_SCREENS.indexOf(previous.screen) >= 0
      ? (previous.practiceDetail || null) : null;
    store.practiceExperienceId = previous.screen === 'practice-experience'
      ? (previous.practiceExperience || null) : null;
    store.shareDetailId = previous.screen === 'share'
      ? (previous.shareDetail || null) : null;
    store.contactDetailRef = previous.screen === 'contact-event'
      ? (previous.contactDetail || null) : null;
    store.sharedPracticeDetailId = previous.screen === 'shared-practice-work'
      ? (previous.sharedPracticeDetail || null) : null;
    store.albumDetailId = previous.screen === 'album-image'
      ? (previous.albumDetail || null) : null;
    persistNav();
    ensureLoaded(previous.screen);
    renderAll();
  }

  function ensureLoaded(screen) {
    if (screen === 'home') {
      /* 主屏的「此刻」只读刷新；连不上服务也照样尝试一次，由它自己回答读不到。 */
      if (!store.data.living) loadLiving();
      return;
    }
    if (screen === 'shares') {
      /* 分享这一屏一次读齐：列表里有草稿/延后/收件箱的计数与来源当前性（没有正文）。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.shares) loadShares();
      return;
    }
    if (screen === 'share') {
      /* 详情按 id 读：id 变了就重读，绝不复用上一条的正文冒充这一条。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.shareDetailId) return;
      const shareEntry = store.data.share;
      if (!shareEntry || shareEntry.shareId !== store.shareDetailId) {
        loadShare(store.shareDetailId);
      }
      return;
    }
    if (screen === 'share-new') {
      /* 写之前先看这台实例现在真的读得到哪些来源：不给就是不给，不让人手填路径。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.shareSources) loadShareSources();
      return;
    }
    if (screen === 'contact') {
      /* 来访这一屏一次读齐：计数 + 有界行 + B 的只读会话记录（没有任何正文）。
         已经读到就不再重发；要刷新由屏上的按钮或重开这一屏触发，不做秒级轮询。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.contact) loadContact();
      return;
    }
    if (screen === 'contact-event') {
      /* 详情按 ref 读：ref 变了就重读，绝不复用上一条的正文冒充这一条。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.contactDetailRef) return;
      const contactEntry = store.data.contactEvent;
      if (!contactEntry || contactEntry.eventRef !== store.contactDetailRef) {
        loadContactEvent(store.contactDetailRef);
      }
      return;
    }
    if (screen === 'shared-practice') {
      /* 共同实践这一屏一次读齐：列表 + owner 的接口状态（没有任何私人正文）。
         读到就不再重发；刷新只由屏上的按钮或重开这一屏触发，不做秒级轮询。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.sharedPractices) loadSharedPractices();
      /* 选材要 owner 现在真的列得出的共享来源（B 的 /shares/sources）：这一屏也要读一次，
         读不到就照实说读不到，绝不替用户列私人材料全集。 */
      if (!store.data.shareSources) loadShareSources();
      return;
      return;
    }
    if (screen === 'shared-practice-work') {
      /* 详情按 id 读：id 变了就重读，绝不复用上一次的正文冒充这一次。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.sharedPracticeDetailId) return;
      const practiceEntry = store.data.sharedPractice;
      if (!practiceEntry || practiceEntry.practiceId !== store.sharedPracticeDetailId) {
        loadSharedPractice(store.sharedPracticeDetailId);
      }
      return;
    }
    if (screen === 'shop-item') {
      if (!store.data.shop) loadResource('shop');
      if (!store.data.wallet) loadResource('wallet');
      return;
    }
    if (screen === 'store' || screen === 'store-item') {
      /* 实体日用店走自己的货架接口；app 状态沿用 econ 的 shop 报告，不另造一个 app。 */
      if (appState('shop').status === 'disabled') return;
      if (!store.data.store) loadResource('store');
      if (screen === 'store-item' && !store.data.wallet) loadResource('wallet');
      return;
    }
    if (screen === 'body') {
      /* 身体面要 world 的 body 块（含 signals、POSE/REST 能力与来源）。
         这里**必须重试**上一次失败的读数，否则身体屏会永远停在 /state 投影：
         坐下、开始/结束休息这几条命令就永远出不来（父级 2026-11-15 实际遇到的缺陷）。 */
      if (worldSnapshotNeedsLoad(store.data.world)) loadResource('world');
      return;
    }
    if (screen === 'reading') {
      /* 阅读屏两块：旧取得/位置列表（GET /reading）与真实正文（GET /reading/material）。 */
      if (appState('reading').status !== 'available') return;
      if (!store.data.reading) loadResource('reading');
      if (!store.data.readingMaterial) loadReadingMaterial(null);
      return;
    }
    if (screen === 'projects') {
      /* 项目列表：/state 报不了这个 app（老实例）也照样去读，由它自己回答。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.projects) loadResource('projects');
      return;
    }
    if (screen === 'album') {
      /* 相册列表：/state 不报这个 app，读不读得到由相册接口自己回答。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.album) loadResource('album');
      return;
    }
    if (screen === 'album-image') {
      /* 详情按 id 读：id 变了就重读，绝不复用上一张的说明冒充这一张。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.albumDetailId) return;
      const albumEntry = store.data.albumImage;
      if (!albumEntry || albumEntry.artifactId !== store.albumDetailId) {
        loadAlbumImage(store.albumDetailId);
      }
      return;
    }
    if (screen === 'album-import') {
      /* 导入屏不需要预读；图片由用户实际选择后才进入内存。 */
      return;
    }
    if (screen === 'materials') {
      /* 资料列表：/state 不报这个 app，读不读得到由服务自己回答。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.materials) loadMaterials(store.materialDraft.query);
      return;
    }
    if (screen === 'material') {
      /* 详情按 id 读：id 变了就重读，绝不复用上一件的正文冒充这一件。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.materialDetailId) return;
      const materialEntry = store.data.material;
      if (!materialEntry || materialEntry.artifactId !== store.materialDetailId) {
        loadMaterial(store.materialDetailId);
      }
      return;
    }
    if (screen === 'material-import') {
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.materials) loadMaterials('');
      return;
    }
    if (screen === 'research') {
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.research) loadResearch();
      return;
    }
    if (screen === 'research-item' || screen === 'research-edit') {
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.researchDetailId) return;
      const researchEntry = store.data.researchItem;
      if (!researchEntry || researchEntry.artifactId !== store.researchDetailId) {
        loadResearchItem(store.researchDetailId);
      }
      return;
    }
    if (screen === 'research-create') {
      /* 起一份研究要选具体来源：先有资料列表可选，绝不凭空编来源。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.materials) loadMaterials('');
      return;
    }
    if (screen === 'practice') {
      /* 工作台一次读齐：回顾 + 做法 + Memory 状态（Memory 未绑定也照样进屏）。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.practice) loadPractice();
      return;
    }
    if (screen === 'practice-review' || screen === 'practice-review-edit') {
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.practiceDetailId) return;
      const entry = store.data.practiceReview;
      if (!entry || entry.artifactId !== store.practiceDetailId) {
        loadPracticeReview(store.practiceDetailId);
      }
      return;
    }
    if (screen === 'practice-method' || screen === 'practice-method-revise') {
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.practiceDetailId) return;
      const entry = store.data.practiceMethod;
      if (!entry || entry.artifactId !== store.practiceDetailId) {
        loadPracticeMethod(store.practiceDetailId);
      }
      /* 试用评价要选真实经历：先有经历列表可选，绝不用编造的 id 顶上。 */
      if (!store.data.experiences) loadExperiences(store.memoryDraft.query);
      return;
    }
    if (screen === 'practice-review-new' || screen === 'practice-method-new') {
      /* 新建时必须先有可选的经历；没有就说没有，绝不用编造的 id 顶上。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.data.experiences) loadExperiences(store.memoryDraft.query);
      if (screen === 'practice-method-new' && !store.data.practice) loadPractice();
      return;
    }
    if (screen === 'practice-experience') {
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.practiceExperienceId) return;
      const entry = store.data.experience;
      if (!entry || entry.experienceId !== store.practiceExperienceId) {
        loadExperience(store.practiceExperienceId);
      }
      return;
    }
    if (screen === 'project' || screen === 'project-edit') {
      /* 详情按 id 读：id 变了就重读，绝不复用上一件的正文冒充这一件。 */
      if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
      if (!store.projectDetailId) return;
      const entry = store.data.project;
      if (!entry || entry.artifactId !== store.projectDetailId) {
        loadProject(store.projectDetailId);
      }
      return;
    }
    const resource = SCREEN_RESOURCE[screen];
    if (!resource) return;
    if (store.conn.phase !== 'online' && store.conn.phase !== 'readonly') return;
    /* 世界是第八个 app：/state 没报告也照样去读 /world，由它自己回答。 */
    if (resource === 'world') {
      if (appState(WORLD_APP).status === 'disabled') return;
      if (worldSnapshotNeedsLoad(store.data.world)) loadResource('world');
      return;
    }
    if (appState(resource).status !== 'available') return;
    if (!store.data[resource]) loadResource(resource);
  }

  /* ---------------- 资源读取 ---------------- */

  async function loadResource(resource) {
    const path = RESOURCE_PATH[resource];
    if (!path) return;
    store.data[resource] = {phase: 'loading', doc: null, message: ''};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data[resource] = {phase: 'ready', doc: outcome.doc, message: ''};
    } else {
      store.data[resource] = {
        phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null,
        message: outcome.message,
        http: res && res.status
      };
    }
    renderAll();
  }

  /* ---------------- 阅读材料（GET 只读，翻页不写） ---------------- */

  function readingView() {
    const entry = store.data.readingMaterial;
    if (!entry || entry.phase !== 'ready') return null;
    return readingMaterialView(entry.doc);
  }

  function currentReadingPage() {
    const view = readingView();
    if (!view || view.phase !== 'ready' || !view.available) return null;
    return view.page;
  }

  async function loadReadingMaterial(page) {
    const requested = readingPageValue(page);
    store.data.readingMaterial = {phase: 'loading', doc: null, message: '', page: requested};
    renderAll();
    const res = await request('GET', readingMaterialPath(requested), {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.readingMaterial = {phase: 'ready', doc: outcome.doc, message: '', page: requested,
        http: res && res.status};
      resolveReadingAnchor();
    } else {
      /* 非 ok 的答案也可能带真实原因（PAGE_NOT_PRESENT、READING_MATERIAL_UNAVAILABLE）：
         有 data.available=false 就照“材料不可用”渲染，其余留成明确的读取失败并写出原因。 */
      const doc = res && res.kind === 'http' && res.doc && typeof res.doc === 'object' ? res.doc : null;
      const data = doc && doc.data && typeof doc.data === 'object' ? doc.data : null;
      const reason = (doc && typeof doc.reason_code === 'string' && doc.reason_code) ||
        (data && typeof data.reason_code === 'string' ? data.reason_code : '');
      const keepDoc = !!(doc && data && data.available === false);
      store.data.readingMaterial = {
        phase: outcome.status === 'offline' ? 'offline' : (keepDoc ? 'ready' : 'error'),
        doc: keepDoc ? doc : null,
        message: reason
          ? reasonText(reason) + (res && res.status ? '（HTTP ' + res.status + '）' : '')
          : outcome.message,
        reasonCode: reason,
        http: res && res.status,
        page: requested
      };
    }
    renderAll();
  }

  /* 从笔记锚点进来：先把当前版本读出来，版本一致才回到那一页；
     版本不一致就停在当前页，由界面显式问一句——不悄悄跳到当前版本的同一页。 */
  function resolveReadingAnchor() {
    const anchor = store.readingAnchor;
    if (!anchor || !anchor.pending) return;
    const view = readingView();
    if (!view || view.phase !== 'ready' || !view.available) return;
    if (anchor.sourceRevision && anchor.sourceRevision === view.sourceRevision) {
      anchor.pending = false;
      loadReadingMaterial(anchor.page);
    }
  }

  function openReadingAnchor(anchor) {
    if (!anchor) return;
    const page = readingPageValue(anchor.page);
    if (page === null) return;
    store.readingAnchor = {page: page, sourceRevision: anchor.sourceRevision || '',
      sourceId: anchor.sourceId || '', from: anchor.from || 'note', pending: true};
    store.result = null;
    /* 先读当前版本再判是否一致：不拿旧缓存当“当前版本”，也不直接跳到锚点页。 */
    store.data.readingMaterial = null;
    if (store.screen === 'reading') {
      loadReadingMaterial(null);
      return;
    }
    go('reading');
  }

  /* 翻页：只发 GET，不写任何东西。 */
  function goToReadingPage(page) {
    const value = readingPageValue(page);
    if (value === null) return;
    store.readingAnchor = null;
    loadReadingMaterial(value);
  }

  function appState(key) {
    const apps = store.conn.apps || {};
    if (Object.prototype.hasOwnProperty.call(apps, key)) return apps[key];
    return {status: 'missing', reasonCode: 'APP_NOT_REPORTED', data: null};
  }

  function appStatusLabel(status) {
    if (status === 'available') return '可用';
    if (status === 'disabled') return '已禁用';
    if (status === 'unavailable') return '不可用';
    if (status === 'missing') return '未报告';
    return status ? sanitizeLine(status, 24) : '未报告';
  }

  function stateTone(status) {
    if (status === 'available') return '';
    if (status === 'disabled') return 'is-warn';
    return 'is-bad';
  }

  function appGate(key) {
    const phase = store.conn.phase;
    if (phase === 'loading') return statePanel('loading', '正在读取 /api/phone/state…');
    if (phase === 'offline') {
      return statePanel('offline', '未连接：读不到这部手机的状态', store.conn.detail, [btn('重新读取状态', {onClick: loadState})]);
    }
    if (phase === 'error') {
      return statePanel('error', '服务状态不可用', store.conn.detail, [btn('重试', {onClick: loadState})]);
    }
    const app = appState(key);
    if (app.status === 'available') return null;
    const reason = app.reasonCode ? reasonText(app.reasonCode) : '';
    const text = app.status === 'disabled'
      ? '这个应用在当前实例里被禁用。'
      : app.status === 'missing'
        ? '服务没有报告这个应用。'
        : '这个应用当前不可用。';
    return statePanel(app.status === 'disabled' ? 'disabled' : 'unavailable', text, reason);
  }

  /* ---------------- 视图构件 ---------------- */

  function page(title, subtitle, body, actions) {
    return {title: title, subtitle: subtitle, body: body, actions: actions || []};
  }

  const STATE_CLASS = {
    loading: 'is-loading',
    ok: 'is-ok',
    warn: 'is-warn',
    error: 'is-error',
    offline: 'is-offline',
    readonly: 'is-readonly',
    empty: 'is-empty',
    disabled: 'is-warn',
    unavailable: 'is-warn',
    missing: 'is-warn'
  };

  const STATE_TITLE = {
    loading: '正在读取',
    ok: '已读取',
    warn: '需要注意',
    error: '读取失败',
    offline: '未连接',
    readonly: '只读',
    empty: '还没有内容',
    disabled: '应用已禁用',
    unavailable: '应用不可用',
    missing: '应用未报告'
  };

  function statePanel(kind, text, detail, actions) {
    return h('div', {class: 'state-panel ' + (STATE_CLASS[kind] || 'is-warn'), role: 'status'}, [
      h('p', {class: 'state-title', text: STATE_TITLE[kind] || '状态'}),
      text ? h('p', {class: 'state-text', text: text}) : null,
      detail ? h('p', {class: 'state-detail', text: detail}) : null,
      actions && actions.length ? h('div', {class: 'state-actions'}, actions) : null
    ]);
  }

  function btn(label, options) {
    options = options || {};
    const classes = ['btn', options.variant === 'primary' ? 'btn-primary' : (options.variant === 'mint' ? 'btn-mint' : 'btn-ghost')];
    if (options.small) classes.push('btn-sm');
    const node = h('button', {
      class: classes.join(' '),
      type: 'button',
      disabled: options.disabled === true,
      title: options.title || null,
      on: {click: function () { if (options.disabled) return; if (typeof options.onClick === 'function') options.onClick(); }}
    }, [options.icon ? icon(options.icon) : null, h('span', {text: label})]);
    return node;
  }

  function chip(text, tone) {
    return h('span', {class: 'chip ' + (tone || 'is-neutral'), text: text});
  }

  function kv(label, value, mono) {
    return h('div', {class: 'kv'}, [
      h('span', {class: 'kv-k', text: label}),
      h('span', {class: 'kv-v' + (mono ? ' is-mono' : ''), text: value})
    ]);
  }

  function field(labelText, control, hint) {
    return h('label', {class: 'field'}, [
      h('span', {class: 'field-label', text: labelText}),
      control,
      hint ? h('span', {class: 'field-hint', text: hint}) : null
    ]);
  }

  function firstText(record, keys) {
    if (!record || typeof record !== 'object') return '';
    for (let i = 0; i < keys.length; i += 1) {
      const value = record[keys[i]];
      if (typeof value === 'string' && value.trim()) return sanitizeLine(value, 200);
      if (typeof value === 'number' && isFinite(value)) return String(value);
    }
    return '';
  }

  function firstNumber(record, keys) {
    if (!record || typeof record !== 'object') return null;
    for (let i = 0; i < keys.length; i += 1) {
      const value = record[keys[i]];
      if (typeof value === 'number' && isFinite(value)) return value;
    }
    return null;
  }

  /* ---------------- 渲染 ---------------- */

  let pendingRender = false;

  function renderAll() {
    if (!dom.ready) return;
    /* A pointer is down: replacing the buttons now would detach the one being clicked. */
    if (store.pointerDown) {
      pendingRender = true;
      return;
    }
    renderScreen();
    renderChrome();
    renderSidebar();
  }

  function releaseDeferredRender() {
    if (!pendingRender) return;
    pendingRender = false;
    root.setTimeout(renderAll, 0);
  }

  function renderScreen() {
    const spec = SCREENS[store.screen] || SCREENS.home;
    let built;
    try {
      built = spec.render();
    } catch (error) {
      built = page('界面错误', '这一屏没有渲染成功', [
        statePanel('error', '这一屏没有渲染成功', (error && error.message) || String(error))
      ]);
    }
    dom.head.replaceChildren.apply(dom.head, headNodes(built));
    dom.body.replaceChildren.apply(dom.body, built.body.filter(function (node) { return !!node; }));
    if (store.lastScreen !== store.screen) {
      dom.body.scrollTop = 0;
      store.lastScreen = store.screen;
    }
  }

  function headNodes(built) {
    const nodes = [h('h1', {class: 'screen-title', text: built.title})];
    if (built.subtitle) nodes.push(h('p', {class: 'screen-subtitle', text: built.subtitle}));
    if (built.actions && built.actions.length) nodes.push(h('div', {class: 'screen-actions'}, built.actions));
    return nodes;
  }

  function renderChrome() {
    dom.navCaption.textContent = (SCREEN_META[store.screen] || SCREEN_META.home).label;
    dom.backBtn.disabled = store.stack.length === 0 && store.screen === 'home';
    const phase = store.conn.phase;
    const dotClass = {loading: 'is-loading', online: 'is-online', readonly: 'is-readonly', offline: 'is-offline', error: 'is-error'}[phase] || 'is-loading';
    dom.dot.className = 'conn-dot ' + dotClass;
    const text = {
      loading: '正在读取服务状态',
      online: '已连接',
      readonly: '只读（没有写入令牌）',
      offline: '未连接',
      error: '服务异常'
    }[phase] || '状态未知';
    dom.dot.setAttribute('title', text);
    dom.dot.setAttribute('aria-label', text);
  }

  function renderSidebar() {
    if (!dom.instanceFacts) return;
    const instance = store.conn.instance;
    dom.instanceFacts.replaceChildren(
      kv('名称', instance && instance.name ? instance.name : '未提供'),
      kv('主体', instance && instance.subject ? instance.subject : '未提供'),
      kv('模式', instance && instance.mode ? instance.mode : '未提供'),
      kv('接口', API_BASE, true),
      kv('写入', canWrite(store.conn).ok ? '可用（已取得 CSRF 令牌）' : '不可用')
    );

    const phase = store.conn.phase;
    const lineText = {
      loading: '正在读取服务状态…',
      online: '已连接',
      readonly: '只读',
      offline: '未连接',
      error: '服务状态异常'
    }[phase] || '状态未知';
    dom.connLine.textContent = lineText;
    dom.connLine.className = 'conn-line is-' + phase;
    dom.connDetail.textContent = store.conn.detail || '';

    renderOpsList();
    renderRequestLog();
  }

  function renderOpsList() {
    const entries = ops ? ops.listUnresolved() : [];
    if (!entries.length) {
      dom.unresolvedList.replaceChildren(h('li', {class: 'ops-empty', text: '没有未确认的操作。'}));
      return;
    }
    dom.unresolvedList.replaceChildren.apply(dom.unresolvedList, entries.map(function (entry) {
      return h('li', {class: 'ops-item'}, [
        h('span', {class: 'ops-scope', text: scopeLabel(entry.scope)}),
        h('span', {class: 'ops-key', text: entry.key}),
        btn('查询状态', {small: true, icon: 'query', onClick: function () { queryOperation(entry.scope, entry.key); }})
      ]);
    }));
  }

  function renderRequestLog() {
    if (!dom.requestLog) return;
    if (!store.log.length) {
      dom.requestLog.replaceChildren(h('li', {class: 'reqlog-empty', text: '还没有请求。'}));
      return;
    }
    const items = store.log.slice().reverse().map(function (entry) {
      return h('li', {class: 'reqlog-item'}, [
        h('span', {class: 'reqlog-method', text: entry.method}),
        h('span', {class: 'reqlog-path', text: entry.path}),
        h('span', {class: 'reqlog-status', text: entry.status}),
        h('span', {class: 'reqlog-outcome', text: entry.outcome + ' · ' + (entry.ms || 0) + 'ms'})
      ]);
    });
    dom.requestLog.replaceChildren.apply(dom.requestLog, items);
  }

  function scopeLabel(scope) {
    const head = String(scope || '').split(':')[0];
    const map = {
      note: '笔记写入',
      reference: '添加收藏',
      purchase: '购买',
      use: '使用物品',
      world: '世界动作',
      project: '项目写入',
      material: '资料导入',
      research: '研究写入'
    };
    if (head === 'practice') return '实践写入';
    if (head === 'share') return '分享写入';
    if (head === 'contact') return '投递来话';
    if (head === 'memory') return 'Memory 启用';
    if (READING_SCOPE_TEXT[head]) return READING_SCOPE_TEXT[head];
    return map[head] || sanitizeLine(scope, 40);
  }

  /* ---------------- 主屏 ---------------- */

  /* ---------------- 主屏「此刻」 ---------------- */

  /* 只读刷新只负责主屏：用户已经进了阅读/输入，就不要用「此刻」的结果重建那一屏。 */
  function renderHomeIfCurrent() {
    if (store.screen === 'home') renderAll();
  }

  async function loadLiving() {
    store.data.living = {phase: 'loading', doc: null, message: ''};
    renderHomeIfCurrent();
    const res = await request('GET', LIVING_PATH, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      const data = outcome.doc && outcome.doc.data && typeof outcome.doc.data === 'object'
        ? outcome.doc.data : null;
      store.data.living = data
        ? {phase: 'ready', doc: data, message: ''}
        : {phase: 'error', doc: null, message: '服务返回了 ok，但没有此刻数据'};
    } else {
      store.data.living = {
        phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null,
        message: outcome.message,
        http: res && res.status
      };
    }
    renderHomeIfCurrent();
  }

  /* 一个已经过去的「下一次窗口」不能继续叫下一次：它只是记录，不是预约。 */
  function nextPast(value) {
    if (typeof value !== 'string' || !value.trim()) return false;
    const when = Date.parse(value);
    return Number.isFinite(when) && when < Date.now();
  }

  /* 把 GET /living 的投影收成页面能渲染的字段；缺字段就留 null，界面不补造。 */
  function livingView(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const phase = raw.phase && typeof raw.phase === 'object' ? raw.phase : {};
    const key = typeof phase.key === 'string' ? phase.key : 'unknown';
    const where = raw.where && typeof raw.where === 'object' ? raw.where : {};
    const work = raw.work && typeof raw.work === 'object' ? raw.work : {};
    const reading = raw.reading && typeof raw.reading === 'object' ? raw.reading : {};
    const memory = raw.memory && typeof raw.memory === 'object' ? raw.memory : {};
    const foreground = raw.foreground && typeof raw.foreground === 'object' ? raw.foreground : {};
    const next = raw.next && typeof raw.next === 'object' ? raw.next : {};
    const run = raw.run && typeof raw.run === 'object' ? raw.run : {};
    const resident = run.resident && typeof run.resident === 'object' ? run.resident : {};
    const pause = run.pause && typeof run.pause === 'object' ? run.pause : {};
    const windowState = run.window && typeof run.window === 'object' ? run.window : {};
    const capacity = run.capacity && typeof run.capacity === 'object' ? run.capacity : {};
    const child = windowState.child && typeof windowState.child === 'object' ? windowState.child : null;
    const allowed = ['active', 'paused', 'blocked', 'quiet', 'idle', 'stopped', 'unknown'];
    return {
      key: allowed.indexOf(key) >= 0 ? key : 'unknown',
      sentence: sanitizeLine(raw.sentence, 240) || '读不到她的当前状态。',
      observedAt: sanitizeLine(raw.observed_at, 40),
      place: sanitizeLine(where.place_name, 80) || sanitizeLine(where.place_id, 80),
      placeKnown: where.status === 'READY',
      whereUnknown: Array.isArray(where.unknowns)
        ? where.unknowns.slice(0, 4).map(function (item) { return sanitizeLine(String(item), 40); })
        : [],
      workTitle: sanitizeLine(work.title, 80),
      workVersion: sanitizeLine(work.head_version_id, 60),
      workStoppedAt: sanitizeLine(work.stopped_at, 40),
      activityRef: sanitizeLine(work.activity_ref, 60),
      /* 前台活动来自权威文件：标题/状态/它自己的时间；读不到就写读不到，不拿读取时间冒充。 */
      activityKnown: foreground.status === 'READ',
      activityTitle: sanitizeLine(foreground.title, 80),
      activityStatus: sanitizeLine(foreground.activity_status, 16),
      activityKind: sanitizeLine(foreground.activity_kind, 40),
      activityRefFg: sanitizeLine(foreground.ref, 60),
      activityAt: sanitizeLine(foreground.paused_at || foreground.updated_at, 40),
      activityReason: sanitizeLine(foreground.reason_code, 40),
      readingTitle: sanitizeLine(reading.title, 80),
      readingPage: firstNumber(reading, ['page']),
      readingPages: firstNumber(reading, ['pages']),
      nextAt: sanitizeLine(next.check_window_at, 40),
      nextPast: nextPast(next.check_window_at),
      memoryStatus: sanitizeLine(memory.binding_status, 24),
      memoryRecall: typeof memory.recall_state === 'string' && memory.recall_state
        ? memory.recall_state
        : (memory.recall_ready === true ? 'ready'
          : (memory.recall_ready === false ? 'not_ready' : 'unknown')),
      memoryCount: firstNumber(memory, ['experience_count']),
      memorySource: sanitizeLine(memory.source, 32),
      residentRunning: resident.running_recorded === true && resident.pid_alive === true,
      residentRecorded: resident.running_recorded === true,
      residentPid: firstNumber(resident, ['pid']),
      paused: pause.paused === true,
      pauseKnown: pause.paused === true || pause.paused === false,
      used: firstNumber(capacity, ['used']),
      remaining: firstNumber(capacity, ['remaining']),
      cap: firstNumber(capacity, ['function_cap']),
      supervisorAttached: windowState.running === true,
      childAlive: !!(child && child.alive === true),
      childPid: child ? firstNumber(child, ['pid']) : null
    };
  }

  const LIVING_PHASE = {
    active: {label: '进行中', tone: 'is-ok'},
    paused: {label: '暂停', tone: 'is-warn'},
    blocked: {label: '受阻', tone: 'is-warn'},
    quiet: {label: '安静', tone: 'is-neutral'},
    idle: {label: '暂缓', tone: 'is-neutral'},
    stopped: {label: '未运行', tone: 'is-neutral'},
    unknown: {label: '未知', tone: 'is-warn'}
  };

  function livingLine(label, value, mono) {
    if (!value) return null;
    return h('div', {class: 'living-line'}, [
      h('span', {class: 'living-line-k', text: label}),
      h('span', {class: 'living-line-v' + (mono ? ' is-mono' : ''), text: value})
    ]);
  }

  function activityStatusText(value) {
    const labels = {ACTIVE: '进行中', PAUSED: '已暂缓', WAITING: '等待中'};
    return labels[value] || sanitizeLine(value, 16);
  }

  /* Memory 只说绑定与召回事实；「已接通」永远不等于「她因此记得/改变了选择」。 */
  function memoryLineText(view) {
    if (view.memoryStatus === 'READY') {
      if (view.memoryRecall === 'empty') return '记忆已接通 · 暂无已记录的亲身经历';
      if (view.memoryRecall === 'ready' && view.memoryCount !== null) {
        return '记忆已接通 · 可召回 ' + view.memoryCount + ' 条经历';
      }
      if (view.memoryRecall === 'ready') return '记忆已接通 · 可召回条数未读到';
      if (view.memoryRecall === 'not_ready') return '记忆已接通 · 当前不可召回';
      return '记忆已接通 · 召回未确认';
    }
    if (view.memoryStatus === 'UNBOUND') return '未绑定记忆（可选状态，不是故障）';
    if (view.memoryStatus === 'CONFIGURED') return '已配置 Memory · 健康未证实';
    if (view.memoryStatus === 'BLOCKED') return '记忆不可用';
    return '记忆状态未知';
  }

  function renderLiving() {
    const entry = store.data.living;
    const view = entry && entry.phase === 'ready' ? livingView(entry.doc) : null;
    if (!view) {
      const message = entry && entry.phase === 'loading' ? '正在读取她的此刻…'
        : (entry && entry.message) || '还没有读到她的此刻。';
      return h('section', {class: 'living living-unknown', 'aria-label': '此刻'}, [
        h('div', {class: 'living-head'}, [
          h('p', {class: 'living-kicker', text: '此刻'}),
          chip('未读到', 'is-warn')
        ]),
        h('p', {class: 'living-sentence', text: message}),
        h('p', {class: 'living-note', text: '这里只写服务真实报告的事实；读不到就写读不到。'}),
        h('div', {class: 'row-actions'}, [
          btn('重新读取此刻', {small: true, onClick: loadLiving})
        ])
      ]);
    }
    const meta = LIVING_PHASE[view.key] || LIVING_PHASE.unknown;
    const observed = view.observedAt ? (formatWhen(view.observedAt) || view.observedAt) : '';
    const nodes = [
      h('div', {class: 'living-head'}, [
        h('p', {class: 'living-kicker', text: '此刻'}),
        chip(meta.label, meta.tone),
        observed ? h('span', {class: 'living-observed', text: '读取于 ' + observed}) : null
      ]),
      h('p', {class: 'living-sentence', text: view.sentence}),
      h('p', {class: 'living-where', text: view.placeKnown && view.place
        ? '位置：' + view.place
        : '位置：未知' + (view.whereUnknown.length ? '（' + view.whereUnknown.join('、') + '）' : '')})
    ];

    const lines = [];
    /* 前台活动是权威文件里的一等事实：标题、状态、它自己的发生/暂停时间。
       内部 id 只进折叠的技术细节，不在主行冒充她做了什么。 */
    if (view.activityKnown && (view.activityTitle || view.activityStatus)) {
      const status = activityStatusText(view.activityStatus);
      const when = view.activityAt ? (formatWhen(view.activityAt) || view.activityAt) : '';
      const title = view.activityTitle ? '《' + view.activityTitle + '》' : '';
      const parts = [title, status, when].filter(function (item) { return !!item; });
      lines.push(livingLine('当前活动', parts.join(' · ')));
    } else if (view.activityReason) {
      lines.push(livingLine('当前活动', '未核实（' + view.activityReason + '）'));
    }
    if (view.workTitle) {
      const when = view.workStoppedAt ? ' · ' + (formatWhen(view.workStoppedAt) || view.workStoppedAt) : '';
      lines.push(livingLine('作品停点', view.workTitle + (view.workVersion ? ' · ' + view.workVersion : '') + when));
    }
    if (view.readingTitle) {
      const page = view.readingPage !== null ? '第 ' + view.readingPage + (view.readingPages !== null ? '/' + view.readingPages : '') + ' 页' : '';
      lines.push(livingLine('阅读停点', view.readingTitle + (page ? ' · ' + page : '')));
    }
    if (view.nextAt) {
      lines.push(livingLine(view.nextPast ? '上次记录的窗口（已过）' : '下一次自然窗口',
        formatWhen(view.nextAt) || view.nextAt));
    }
    if (view.memoryStatus) {
      lines.push(livingLine('Memory', memoryLineText(view)));
    }
    if (lines.length) nodes.push(h('div', {class: 'living-lines'}, lines));

    const tech = [
      livingLine('活动引用', view.activityRefFg, true),
      livingLine('记录引用', view.activityRefFg ? '' : view.activityRef, true),
      livingLine('活动类型', view.activityKind, true),
      livingLine('活动核实', view.activityReason, true),
      livingLine('Memory 来源', view.memorySource, true)
    ].filter(function (node) { return !!node; });
    if (tech.length) {
      nodes.push(h('details', {class: 'tech-details'}, [
        h('summary', {class: 'tech-summary', text: '技术细节'}),
        h('div', {class: 'tech-body'}, tech)
      ]));
    }
    nodes.push(h('p', {class: 'living-note', text: 'Memory 接通只表示服务可读，不代表它影响了她这次的选择。'}));

    /* 运行与额度：这里说的是「本机运行」，不是她生活的完成度。 */
    const used = view.used === null ? '—' : String(view.used);
    const cap = view.cap === null ? '—' : String(view.cap);
    const remaining = view.remaining === null ? '—' : String(view.remaining);
    const runLines = [
      livingLine('resident', view.residentRunning ? '运行中'
        : (view.residentRecorded ? '记录说运行，但进程不在' : '未运行')),
      livingLine('暂停位', view.pauseKnown ? (view.paused ? '已暂停' : '未暂停') : '未知'),
      livingLine('本功能请求额度', used + ' / ' + cap + '（剩余 ' + remaining + '）')
    ];
    if (view.childAlive) runLines.push(livingLine('窗口子进程', '运行中 · pid ' + view.childPid, true));
    nodes.push(h('div', {class: 'living-run'}, [
      h('p', {class: 'living-run-title', text: '本机运行'})
    ].concat(runLines)));

    const canControl = store.conn.phase === 'online' && !store.conn.readOnly;
    const windowBlocked = !view.supervisorAttached
      || view.childAlive
      || (view.remaining !== null && view.remaining <= 0)
      || !canControl
      || store.busy === 'living:window';
    nodes.push(h('div', {class: 'row-actions living-actions'}, [
      btn(view.childAlive ? '窗口已在运行' : '启动有界窗口（120 秒 / 1 次，会调用模型）', {
        small: true,
        variant: 'primary',
        disabled: windowBlocked,
        title: view.supervisorAttached ? '只启动一个明确、有界的窗口：会调用模型；上限由服务端强制'
          : '页面没有挂载本机监督入口（请用 usable_day.py serve 启动）',
        onClick: function () { startLivingWindow(); }
      }),
      btn(view.paused ? '解除暂停' : '暂停运行', {
        small: true,
        disabled: !view.supervisorAttached || !canControl || store.busy === 'living:pause',
        title: view.paused
          ? '只撤销本机暂停位：不启动模型，也不会自动恢复 Activity'
          : '只封住新的决定：不会停掉正在运行的进程，也不会改动 Activity',
        onClick: function () { setLivingPaused(!view.paused); }
      }),
      btn('停止窗口', {
        small: true,
        disabled: !view.childAlive || !canControl || store.busy === 'living:stop',
        title: '只停本监督自己启动的窗口子进程；没有窗口时这里不可点',
        onClick: stopLivingWindow
      }),
      btn('刷新此刻', {small: true, onClick: loadLiving})
    ]));
    if (view.paused) {
      nodes.push(h('p', {class: 'living-note', text: '本机暂停位已打开：解除暂停不会自动开始运行，也不会把活动状态改回去。'}));
    }
    if (!view.supervisorAttached) {
      nodes.push(h('p', {class: 'living-note', text: '当前页面没有挂载本机监督入口：可以看状态，不能用网页启动运行。用 scripts/usable_day.py serve 启动手机面后再看这里。'}));
    }
    if (view.remaining !== null && view.remaining <= 0) {
      nodes.push(h('p', {class: 'living-note', text: '这个功能的请求额度已经用完：页面继续只读，不会重开窗口，也不会刷新额度。'}));
    }
    return h('section', {class: 'living living-' + view.key, 'aria-label': '此刻'}, nodes);
  }

  async function runLivingControl(scope, path, body, message) {
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: scope, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: message};
    renderAll();
    const res = await request('POST', path, {csrf: store.conn.csrf, body: body || {}});
    const outcome = deriveMutationOutcome(res);
    store.busy = null;
    const labels = {
      applied: message + '：服务已接受。',
      rejected: message + '：服务拒绝了这次请求。',
      conflict: message + '：服务明确拒绝，没有执行。',
      blocked: message + '：请求没有发出。',
      unknown: message + '：结果未知，先刷新状态，不要重复点击。'
    };
    store.result = {
      scope: scope,
      status: outcome.status,
      message: labels[outcome.status] || message + '：服务返回了未识别的状态。',
      reason: outcome.reason || '',
      data: outcome.doc && outcome.doc.data && typeof outcome.doc.data === 'object' ? outcome.doc.data : null
    };
    loadLiving();
  }

  function startLivingWindow() {
    runLivingControl('living:window', LIVING_WINDOW_PATH, {
      duration_seconds: LIVING_DEFAULT_SECONDS,
      max_new_requests: LIVING_DEFAULT_REQUESTS
    }, '启动有界窗口');
  }

  function setLivingPaused(paused) {
    runLivingControl('living:pause', LIVING_PAUSE_PATH, {paused: paused === true},
      paused ? '暂停运行' : '继续运行');
  }

  function stopLivingWindow() {
    runLivingControl('living:stop', LIVING_STOP_PATH, {}, '停止窗口');
  }

  function renderHome() {
    const nodes = [];
    const phase = store.conn.phase;

    if (phase === 'offline') {
      nodes.push(statePanel('offline', '未连接：读不到 /api/phone/state', store.conn.detail, [
        btn('重新读取状态', {onClick: loadState})
      ]));
    } else if (phase === 'error') {
      nodes.push(statePanel('error', '服务状态不可用', store.conn.detail, [btn('重试', {onClick: loadState})]));
    } else if (phase === 'readonly') {
      nodes.push(statePanel('readonly', '只读模式', store.conn.detail));
    } else if (phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取服务状态…'));
    }

    const unresolved = ops ? ops.listUnresolved() : [];
    if (unresolved.length) {
      nodes.push(h('button', {
        class: 'ribbon',
        type: 'button',
        on: {click: function () { go('ops'); }}
      }, [
        h('span', {class: 'ribbon-title', text: '有 ' + unresolved.length + ' 个未确认的操作'}),
        h('span', {class: 'ribbon-hint', text: '用同一个操作键查询结果，不会重发 →'})
      ]));
    }

    const now = new Date();
    nodes.push(h('div', {class: 'home-hero'}, [
      h('p', {class: 'home-date', text: dateText(now)}),
      h('p', {class: 'home-greeting', text: greeting(now)}),
      h('p', {class: 'home-instance', text: instanceLine()})
    ]));

    /* 「此刻」放在磁贴之前：用户先看到她真实的当前状态，再决定进哪个应用。 */
    nodes.push(renderLiving());

    nodes.push(h('div', {class: 'grid'}, HOME_ORDER.map(appTile)));
    /* 生活链的两个直接入口：实体日用店（店里买）与房间（在世界的自家房间里布置）。
       它们是行内按钮，不是第九个 app 图标；点进去读到的事实仍然只来自服务。 */
    nodes.push(h('div', {class: 'row-actions home-shortcuts'}, [
      btn('实体日用店', {small: true, icon: 'bag', onClick: function () { go('store'); }}),
      btn('房间与布置', {small: true, icon: 'map', onClick: function () { go('world'); }}),
      /* 她自己的多份作品：不是第九个图标，而是生活链的直接入口。 */
      btn('私人项目', {small: true, icon: 'note', onClick: function () { go('projects'); }}),
      /* 资料库与研究工作台：同样是生活链的直接入口，不加第九个 tile。 */
      btn('资料库', {small: true, icon: 'book', onClick: function () { go('materials'); }}),
      btn('研究工作台', {small: true, icon: 'note', onClick: function () { go('research'); }}),
      /* 个人实践工作台（回顾 / 做法 / Memory）：同样是生活链的直接入口。 */
      btn('实践工作台', {small: true, icon: 'check', onClick: function () { go('practice'); }}),
      /* 分享与本地收件箱：草稿 / 延后 / 本地存档都在这一屏，主屏不再只有静态磁贴。 */
      btn('分享与收件箱', {small: true, icon: 'bookmark', onClick: function () { go('shares'); }}),
      /* 来访与共同时间：普通来话 / 明确邀请 / 显式结束。主屏直达，不是第九个磁贴。 */
      btn('来访与共同时间', {small: true, icon: 'heart', onClick: function () { go('contact'); }}),
      /* 共同实践：选一份明确共享的材料，和她一起做同一件作品。主屏直达，不是磁贴。 */
      btn('共同实践', {small: true, icon: 'check', onClick: function () { go('shared-practice'); }}),
      /* 相册：自己有真实图片字节的私人图片，导入/回看都走 Supply 同一 owner。 */
      btn('相册', {small: true, icon: 'bookmark', onClick: function () { go('album'); }})
    ]));
    nodes.push(h('p', {class: 'footnote', text: '图标都能进入真实内容；没有连接时按钮不会替你编一个结果。'}));

    return page('主屏', '应用状态来自 /api/phone/state 的实际报告', nodes);
  }

  function greeting(date) {
    const hour = date.getHours();
    if (hour < 5) return '深夜好';
    if (hour < 11) return '早上好';
    if (hour < 14) return '中午好';
    if (hour < 18) return '下午好';
    if (hour < 23) return '晚上好';
    return '夜深了';
  }

  function instanceLine() {
    const instance = store.conn.instance;
    if (!instance) return '实例信息还没读到。';
    const parts = [instance.name];
    if (instance.subject) parts.push('主体 ' + instance.subject);
    if (instance.mode) parts.push('模式 ' + instance.mode);
    return parts.join(' · ');
  }

  function appTile(appKey) {
    const meta = APP_REGISTRY[appKey];
    const state = appState(appKey);
    const label = appStatusLabel(state.status);
    const note = state.status === 'available' ? meta.desc : label;
    return h('button', {
      class: 'tile',
      type: 'button',
      'aria-label': meta.label + '：' + label,
      title: state.reasonCode ? meta.label + ' · ' + state.reasonCode : meta.label,
      on: {click: function () { go(appKey); }}
    }, [
      h('span', {class: 'tile-icon tile-' + appKey}, [icon(meta.icon)]),
      h('span', {class: 'tile-label', text: meta.label}),
      h('span', {class: 'tile-note ' + (state.status === 'available' ? '' : stateTone(state.status)), text: note})
    ]);
  }

  /* ---------------- 结果卡 ---------------- */

  function resultCard() {
    const result = store.result;
    if (!result) return null;
    const toneClass = {
      pending: 'is-loading',
      applied: 'is-ok',
      already_applied: 'is-ok',
      rejected: 'is-error',
      conflict: 'is-warn',
      unknown: 'is-warn',
      stale_source: 'is-error',
      blocked: 'is-readonly',
      offline: 'is-error'
    }[result.status] || 'is-warn';
    const children = [
      h('p', {class: 'result-title', text: OUTCOME_TITLE[result.status] || '操作状态'})
    ];
    if (result.message) children.push(h('p', {class: 'result-text', text: result.message}));
    if (result.reason) children.push(h('p', {class: 'result-reason', text: reasonText(result.reason)}));
    if (result.lines && result.lines.length) {
      result.lines.forEach(function (line) {
        children.push(h('p', {class: 'result-line', text: line}));
      });
    }

    if (result.data && typeof result.data === 'object') {
      const entries = displayFieldEntries(result.data, 8);
      entries.forEach(function (entry) {
        let value = entry.text;
        if (entry.key === 'current_balance' || entry.key === 'balance') {
          const money = formatMoney(result.data.currency, result.data[entry.key]);
          if (money) value = money;
        } else if (entry.key === 'updated_at' || entry.key === 'created_at') {
          const when = formatWhen(result.data[entry.key]);
          if (when) value = when;
        }
        children.push(kv(RESULT_FIELD_LABELS[entry.key] || entry.key, value, /_id$/.test(entry.key)));
      });
      const receipt = result.data.receipt;
      if (receipt && typeof receipt === 'object') {
        children.push(h('p', {class: 'card-note', text: '回执（服务返回的字段，原样显示）'}));
        displayFieldEntries(receipt, 6).forEach(function (entry) {
          children.push(kv(RESULT_FIELD_LABELS[entry.key] || entry.key, entry.text));
        });
      }
    }

    if (result.key) children.push(h('p', {class: 'result-key', text: '操作键 ' + result.key}));
    if (result.status === 'unknown' || result.status === 'pending') {
      children.push(h('p', {class: 'card-note', text: '同一次操作不会自动换键重发；用下面的按钮查询这次操作的结果。'}));
      if (result.key && result.scope) {
        children.push(btn('查询这次操作', {small: true, icon: 'query', onClick: function () { queryOperation(result.scope, result.key); }}));
      }
    }
    if (result.scope && result.scope.indexOf('purchase') === 0 && (result.status === 'applied' || result.status === 'already_applied')) {
      children.push(h('div', {class: 'row-actions'}, [
        btn('查看物品', {small: true, variant: 'mint', onClick: function () { go('inventory'); }})
      ]));
    }
    if (result.status === 'conflict' && result.scope === 'note:write') {
      children.push(h('div', {class: 'row-actions'}, [
        btn('重新读取最新列表', {small: true, onClick: function () { loadResource('notes'); }})
      ]));
    }
    return h('div', {class: 'result ' + toneClass, role: 'status'}, children);
  }

  /* ---------------- 笔记 ---------------- */

  function renderNotes() {
    const gate = appGate('notes');
    if (gate) return page('笔记', '写入与修订', [gate]);

    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);

    nodes.push(h('div', {class: 'row-actions'}, [
      btn('新建', {variant: 'primary', icon: 'plus', onClick: startNewNote}),
      btn('刷新', {icon: 'refresh', onClick: function () { loadResource('notes'); }})
    ]));

    if (store.conn.phase === 'readonly') {
      nodes.push(statePanel('readonly', '只读模式：能看不能写', '服务没有提供 CSRF 令牌，保存会被界面挡下。'));
    }

    const entry = store.data.notes;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取笔记…'));
      return page('笔记', '写入与修订', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到笔记', entry.message, [
        btn('重试', {onClick: function () { loadResource('notes'); }})
      ]));
      return page('笔记', '写入与修订', nodes);
    }

    const view = noteListView(entry.doc);
    if (view.itemsMissing) {
      nodes.push(statePanel('error', '服务没有返回笔记列表', '响应里没有 items 数组。'));
    } else if (!view.items.length) {
      nodes.push(statePanel('empty', '还没有笔记', '写下第一则；保存后刷新也还在。', [
        btn('新建', {variant: 'primary', onClick: startNewNote})
      ]));
    } else {
      nodes.push(h('ul', {class: 'list'}, view.items.map(function (note) {
        const inner = [
          h('span', {class: 'list-top'}, [
            h('span', {class: 'list-title', text: note.title || '无标题'}),
            h('span', {class: 'list-meta', text: note.revision !== null ? 'rev ' + note.revision : ''})
          ]),
          note.body ? h('span', {class: 'list-text', text: note.body}) : null,
          h('span', {class: 'list-chips'}, [
            chip(formatWhen(note.updatedAt) || '时间未提供', 'is-neutral'),
            note.anchor ? chip('锚在阅读第 ' + note.anchor.page + ' 页', 'is-ok') : null,
            note.idOk ? null : chip('条目 id 不可用', 'is-warn')
          ])
        ];
        /* 只有拿到可用条目 id 的这一条才能点开修订；id 是协议值，屏幕只显示文本。 */
        const row = [];
        if (!note.idOk) row.push(h('div', {class: 'list-item is-static'}, inner));
        else row.push(h('button', {class: 'list-item', type: 'button', on: {click: function () { openNote(note.id); }}}, inner));
        /* 阅读锚点：入口是单独的按钮（不能嵌在修订按钮里）。旧版本不直接跳当前页，
           交给阅读屏先比对版本再问一句。 */
        if (note.anchor) {
          row.push(h('div', {class: 'row-actions'}, [
            btn('回到阅读第 ' + note.anchor.page + ' 页', {small: true, icon: 'book', onClick: function () {
              openReadingAnchor({
                page: note.anchor.page,
                sourceRevision: note.anchor.sourceRevision,
                sourceId: note.anchor.sourceId,
                from: 'note'
              });
            }})
          ]));
        }
        return h('li', {}, row);
      })));
    }

    nodes.push(h('p', {class: 'footnote', text: view.head
      ? '服务版本 head：' + safeLabel(view.head, 80)
      : '服务没有提供 head：界面的冲突保护不可用。'}));
    return page('笔记', '写入与修订；界面只提交，不代签成功', nodes);
  }

  function startNewNote() {
    const view = noteListView(store.data.notes && store.data.notes.doc);
    store.editor = {mode: 'create', noteId: null, title: '', body: '', head: view.head, errors: []};
    store.result = null;
    go('note-edit');
  }

  function rawNoteById(id) {
    const doc = store.data.notes && store.data.notes.doc;
    const items = doc && Array.isArray(doc.items) ? doc.items : [];
    for (let i = 0; i < items.length; i += 1) {
      const item = items[i];
      if (!item || typeof item !== 'object') continue;
      const candidate = typeof item.id === 'string' ? item.id : (typeof item.note_id === 'string' ? item.note_id : '');
      if (candidate && candidate === id) return item;
    }
    return null;
  }

  function openNote(id) {
    const raw = rawNoteById(id);
    const view = noteListView(store.data.notes && store.data.notes.doc);
    store.editor = {
      mode: 'edit',
      noteId: id,
      title: raw && typeof raw.title === 'string' ? raw.title : '',
      body: raw && typeof raw.body === 'string' ? raw.body : '',
      head: view.head,
      errors: []
    };
    store.result = null;
    go('note-edit');
  }

  function renderNoteEditor() {
    const editor = store.editor;
    if (!editor) {
      return page('写笔记', '', [
        statePanel('empty', '没有打开的草稿'),
        btn('回到笔记', {onClick: function () { go('notes'); }})
      ]);
    }
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);

    const titleInput = h('input', {
      class: 'input',
      type: 'text',
      maxlength: 80,
      autocomplete: 'off',
      placeholder: '一句话标题',
      value: editor.title
    });
    const titleCount = h('span', {class: 'counter', text: editor.title.length + ' / 80'});
    titleInput.addEventListener('input', function () {
      editor.title = titleInput.value;
      titleCount.textContent = editor.title.length + ' / 80';
    });

    const bodyArea = h('textarea', {
      class: 'textarea',
      maxlength: 4000,
      placeholder: '写在这一页上；保存后与后台是同一份事实。'
    });
    bodyArea.value = editor.body;
    const bodyCount = h('span', {class: 'counter', text: editor.body.length + ' / 4000'});
    bodyArea.addEventListener('input', function () {
      editor.body = bodyArea.value;
      bodyCount.textContent = editor.body.length + ' / 4000';
    });

    const busy = store.busy === 'note:create' || store.busy === 'note:write';

    const errors = [];
    if (editor.errors && editor.errors.length) {
      errors.push(h('ul', {class: 'errors'}, editor.errors.map(function (message) {
        return h('li', {text: message});
      })));
    }

    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: editor.mode === 'edit' ? '修订这一页' : '新的一页'}),
      field('标题', titleInput, '最多 80 字'),
      titleCount,
      field('正文', bodyArea, '最多 4000 字'),
      bodyCount,
      errors.length ? errors[0] : null,
      h('div', {class: 'form-actions'}, [
        btn(busy ? '提交中…' : '保存', {
          variant: 'primary',
          icon: 'check',
          disabled: busy,
          onClick: submitNote
        }),
        btn('取消', {onClick: function () { goBack(); }})
      ])
    ]));

    nodes.push(h('p', {
      class: 'footnote',
      text: editor.head
        ? '这次写入以服务 head（' + safeLabel(editor.head, 80) + '）为基准；别处先改过就会被拒绝。'
        : '服务没有提供 head：这次写入没有冲突保护。'
    }));
    nodes.push(h('p', {class: 'footnote', text: '长度上限由界面限制，服务端仍会复核；界面不会替你签成功。'}));
    return page(editor.mode === 'edit' ? '修订笔记' : '新笔记', '提交后以服务返回的状态为准', nodes);
  }

  async function submitNote() {
    const editor = store.editor;
    if (!editor) return;
    const draft = validateNoteDraft({title: editor.title, body: editor.body});
    if (!draft.ok) {
      editor.errors = draft.errors;
      renderAll();
      return;
    }
    editor.errors = [];
    const editBlocked = noteEditBlocked(editor);
    if (editBlocked) {
      store.result = {scope: 'note:write', status: 'blocked', message: editBlocked};
      renderAll();
      return;
    }
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: 'note:write', status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = editor.mode === 'edit' ? 'note:write' : 'note:create';
    const entry = ops.ensure(scope, {path: '/notes'});
    const payload = buildNotePayload({
      note_id: editor.mode === 'edit' ? editor.noteId : '',
      title: draft.value.title,
      body: draft.value.body,
      operation_key: entry.key,
      expected_head: editor.head
    });

    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在提交笔记…', key: entry.key};
    renderAll();

    const res = await request('POST', '/notes', {csrf: store.conn.csrf, body: payload});
    finishMutation(scope, deriveMutationOutcome(res), '笔记写入');
  }

  /* ---------------- 私人项目 ---------------- */

  /* 项目 app 的闸门：连接阶段的问题挡住整屏；服务自己报 unavailable 时仍然进屏，
     由屏内的错误状态显示服务的原因（否则用户看到一片空白，不知道是谁的问题）。 */
  function projectGate() {
    const gate = appGate('projects');
    if (!gate) return null;
    if (store.conn.phase === 'loading' || store.conn.phase === 'offline' ||
        store.conn.phase === 'error') return gate;
    return null;
  }

  function projectStatusChip(project) {
    return project.status === 'archived'
      ? chip('已归档', 'is-warn')
      : chip('进行中', 'is-ok');
  }

  function projectListRow(project) {
    const inner = [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: project.title}),
        h('span', {class: 'list-meta', text: project.revision !== null ? 'rev ' + project.revision : ''})
      ]),
      h('span', {class: 'list-chips'}, [
        projectStatusChip(project),
        project.versionNumber !== null ? chip('v' + project.versionNumber, 'is-neutral') : null,
        chip(formatWhen(project.updatedAt) || '时间未提供', 'is-neutral')
      ])
    ];
    return h('li', {}, [
      h('button', {class: 'list-item', type: 'button',
        on: {click: function () { openProject(project.id); }}}, inner)
    ]);
  }

  function renderProjects() {
    const gate = projectGate();
    if (gate) return page('项目', '她自己的作品', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('新建项目', {variant: 'primary', icon: 'plus', onClick: startNewProject}),
      btn('刷新', {icon: 'refresh', onClick: function () { loadResource('projects'); }})
    ]));
    if (store.conn.phase === 'readonly') {
      nodes.push(statePanel('readonly', '只读模式：能看不能写', '服务没有提供 CSRF 令牌，保存会被界面挡下。'));
    }
    const entry = store.data.projects;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取项目…'));
      return page('项目', '她自己的作品', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到项目',
        entry.message, [btn('重试', {onClick: function () { loadResource('projects'); }})]));
      return page('项目', '她自己的作品', nodes);
    }
    const view = projectListView(entry.doc);
    if (view.itemsMissing) {
      nodes.push(statePanel('error', '服务没有返回项目列表', '响应里没有 projects 数组。'));
    } else if (!view.projects.length) {
      nodes.push(statePanel('empty', '还没有项目', '这里只列她自己的作品；阅读材料和私人事项不在这张单上。', [
        btn('新建项目', {variant: 'primary', onClick: startNewProject})
      ]));
    } else {
      nodes.push(h('ul', {class: 'list'}, view.projects.map(projectListRow)));
    }
    const lines = [];
    if (view.activeCount !== null) lines.push('进行中 ' + view.activeCount);
    if (view.archivedCount !== null) lines.push('已归档 ' + view.archivedCount);
    if (view.excludedCount) lines.push('非项目工件 ' + view.excludedCount + '（材料/私人事项）');
    if (view.truncated) lines.push('列表已截断');
    if (lines.length) nodes.push(h('p', {class: 'footnote', text: lines.join(' · ')}));
    nodes.push(h('p', {class: 'footnote',
      text: '作品就是 Supply 的 ManagedArtifact：这一屏读的是 owner 的列表，归档也只是不再活跃。'}));
    return page('项目', '多份作品各自推进；正文在详情里', nodes);
  }

  /* ---------------- 私人相册（真实图片字节；仍是 Supply 同一 owner） ---------------- */

  function albumIdFor(value) {
    const checked = protocolId(value, PROTOCOL_ID_MAX);
    if (!checked.ok || !ALBUM_ID_SAFE.test(checked.value)) return '';
    return checked.value;
  }

  function albumDetailPath(artifactId) {
    const id = albumIdFor(artifactId);
    return id ? '/album/' + id : '';
  }

  function albumImagePath(artifactId) {
    const id = albumIdFor(artifactId);
    return id ? '/album/' + id + '/image' : '';
  }

  /* `request()` 接受 /album/... 这样的相对路径并统一补 API_BASE；
   * 但 <img src> 是浏览器自己发请求，拿不到那层前缀，必须在这里补全。 */
  function albumImageSrc(artifactId) {
    const path = albumImagePath(artifactId);
    return path ? API_BASE + path : '';
  }

  function albumRowView(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const id = albumIdFor(raw.artifact_id);
    if (!id) return null;
    const media = raw.media && typeof raw.media === 'object' ? raw.media : {};
    const bytes = typeof media.bytes === 'number' && isFinite(media.bytes) && media.bytes > 0
      ? media.bytes : null;
    const contentType = raw.content_type === 'image/jpeg' ? 'image/jpeg'
      : (raw.content_type === 'image/png' ? 'image/png' : '');
    if (!contentType) return null;
    return {
      artifactId: id,
      caption: sanitizeText(raw.caption, ALBUM_MAX_CAPTION),
      contentType: contentType,
      bytes: bytes,
      createdAt: raw.created_at_iso || (typeof raw.created_at === 'number' ? raw.created_at : null),
      lifecycle: sanitizeLine(raw.lifecycle, 24)
    };
  }

  function albumListView(doc) {
    const raw = doc && Array.isArray(doc.images) ? doc.images : null;
    const images = (raw || []).map(albumRowView).filter(function (row) { return !!row; });
    return {images: images, missing: raw === null, bounded: !!(doc && doc.bounded)};
  }

  async function loadAlbumImage(artifactId) {
    const path = albumDetailPath(artifactId);
    if (!path) return;
    store.data.albumImage = {phase: 'loading', doc: null, message: '', artifactId: artifactId};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.albumImage = {phase: 'ready', doc: outcome.doc, message: '',
        artifactId: artifactId, http: res && res.status};
    } else {
      store.data.albumImage = {
        phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null,
        message: outcome.message,
        artifactId: artifactId,
        http: res && res.status
      };
    }
    renderAll();
  }

  function albumBroken(artifactId) {
    return !!store.albumBroken[artifactId];
  }

  function markAlbumBroken(artifactId) {
    if (!artifactId || store.albumBroken[artifactId]) return;
    store.albumBroken[artifactId] = true;
    renderAll();
  }

  function albumPicture(artifactId, className) {
    if (albumBroken(artifactId)) {
      return h('div', {class: className + ' album-broken'}, [
        h('span', {class: 'album-broken-text', text: '这张图片现在读不到'})
      ]);
    }
    return h('img', {
      class: className,
      src: albumImageSrc(artifactId),
      alt: '',
      loading: 'lazy',
      decoding: 'async',
      on: {error: function () { markAlbumBroken(artifactId); }}
    });
  }

  function renderAlbum() {
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('导入图片', {variant: 'primary', icon: 'plus', onClick: startAlbumImport}),
      btn('刷新', {icon: 'refresh', onClick: function () { loadResource('album'); }})
    ]));
    if (store.conn.phase === 'readonly') {
      nodes.push(statePanel('readonly', '只读模式：能看不能导入', '服务没有提供 CSRF 令牌，导入会被界面挡下。'));
    }
    const entry = store.data.album;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取相册…'));
      return page('相册', '自己从本机导入的图片', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到相册',
        entry.message, [btn('重试', {onClick: function () { loadResource('album'); }})]));
      return page('相册', '自己从本机导入的图片', nodes);
    }
    const view = albumListView(entry.doc);
    if (view.missing) {
      nodes.push(statePanel('error', '服务没有返回相册列表', '响应里没有 images 数组。'));
    } else if (!view.images.length) {
      nodes.push(statePanel('empty', '还没有图片', '这里只放你自己选择导入的图片；不会生成、抓取或外发。', [
        btn('导入第一张图片', {variant: 'primary', onClick: startAlbumImport})
      ]));
    } else {
      nodes.push(h('div', {class: 'album-grid'}, view.images.map(function (row) {
        return h('button', {
          class: 'album-card',
          type: 'button',
          'aria-label': (row.caption || '这张图片') + '，打开详情',
          on: {click: function () { go('album-image', {detail: row.artifactId}); }}
        }, [
          albumPicture(row.artifactId, 'album-thumb'),
          h('span', {class: 'album-caption', text: row.caption || '没有说明'})
        ]);
      })));
    }
    const lines = ['共 ' + view.images.length + ' 张'];
    if (view.bounded) lines.push('列表已截断');
    nodes.push(h('p', {class: 'footnote', text: lines.join(' · ')}));
    nodes.push(h('p', {class: 'footnote',
      text: '图片保存在这个实例里；说明是你自己填的，不是她对图片内容的判断。'}));
    return page('相册', '双列回看；点开是完整图片与说明', nodes);
  }

  function renderAlbumImage() {
    const entry = store.data.albumImage;
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取这张图片…'));
      return page('这张图片', '', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到这张图片',
        entry.message, [btn('重试', {onClick: function () { loadAlbumImage(entry.artifactId); }})]));
      return page('这张图片', '', nodes);
    }
    const row = albumRowView(entry.doc && entry.doc.image);
    if (!row) {
      nodes.push(statePanel('error', '服务没有返回这张图片的说明', '响应里没有 image 对象。'));
      return page('这张图片', '', nodes);
    }
    nodes.push(h('div', {class: 'album-detail-frame'}, [albumPicture(row.artifactId, 'album-detail-img')]));
    nodes.push(h('h2', {class: 'card-title', text: row.caption || '没有说明'}));
    nodes.push(kv('来源', '你从本机导入（自述）'));
    const when = formatWhen(row.createdAt);
    if (when) nodes.push(kv('保存于', when));
    if (row.bytes !== null) nodes.push(kv('图片大小', Math.round(row.bytes / 1024) + ' KB'));
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('导入另一张', {onClick: startAlbumImport}),
      btn('回到相册', {onClick: function () { go('album'); }})
    ]));
    nodes.push(h('p', {class: 'footnote',
      text: '这张图是相册里的一张私人图片，不会进入作品列表、不会自动写进记忆，也不代表她看过或理解图片内容。'}));
    return page('这张图片', '说明与来源都来自你自己的填写', nodes);
  }

  function startAlbumImport() {
    store.albumDraft = {caption: '', fileName: '', contentType: '', dataUrl: '', data: '',
      bytes: 0, previewOk: false, previewError: '', errors: []};
    store.result = null;
    go('album-import');
  }

  function chooseAlbumFile(file) {
    const draft = store.albumDraft;
    draft.errors = [];
    draft.previewOk = false;
    draft.previewError = '';
    draft.dataUrl = '';
    draft.data = '';
    draft.bytes = 0;
    draft.contentType = '';
    draft.fileName = '';
    if (!file) {
      draft.errors = ['没有选到文件。'];
      renderAll();
      return;
    }
    if (file.type !== 'image/png' && file.type !== 'image/jpeg') {
      draft.errors = ['只支持 PNG 或 JPEG 图片；其他格式不会保存。'];
      renderAll();
      return;
    }
    if (!(file.size > 0) || file.size > ALBUM_MAX_BYTES) {
      draft.errors = ['图片必须大于 0 且不超过 5 MB。'];
      renderAll();
      return;
    }
    draft.fileName = sanitizeLine(file.name, 120);
    draft.contentType = file.type;
    draft.bytes = file.size;
    const Reader = typeof root.FileReader === 'function' ? root.FileReader : null;
    if (!Reader) {
      draft.errors = ['这个浏览器读不了本地文件，不能导入。'];
      renderAll();
      return;
    }
    const reader = new Reader();
    reader.onerror = function () {
      draft.previewError = '这个文件读不出来，不能保存。';
      draft.previewOk = false;
      renderAll();
    };
    reader.onload = function () {
      const url = String(reader.result || '');
      const comma = url.indexOf(',');
      if (comma < 0) {
        draft.previewError = '这个文件不是可预览的图片，不能保存。';
        renderAll();
        return;
      }
      draft.dataUrl = url;
      draft.data = url.slice(comma + 1);
      draft.previewOk = false;
      draft.previewError = '';
      renderAll();
    };
    reader.readAsDataURL(file);
  }

  function renderAlbumImport() {
    const draft = store.albumDraft;
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    if (store.conn.phase === 'readonly') {
      nodes.push(statePanel('readonly', '只读模式：不能导入', '服务没有提供 CSRF 令牌，保存会被界面挡下。'));
    }
    const input = h('input', {
      class: 'album-file-input',
      type: 'file',
      accept: 'image/png,image/jpeg',
      on: {change: function (event) {
        const files = event.target && event.target.files;
        chooseAlbumFile(files && files[0] ? files[0] : null);
      }}
    });
    nodes.push(field('选择图片', input, '只支持 PNG / JPEG，最大 5 MB'));
    if (draft.dataUrl) {
      const preview = draft.previewError
        ? statePanel('error', '这张图片预览失败', draft.previewError + '（预览失败不会保存）')
        : h('div', {class: 'album-import-preview'}, [h('img', {
          src: draft.dataUrl,
          alt: '这次选择的图片预览',
          on: {
            load: function () {
              if (!draft.previewOk) { draft.previewOk = true; renderAll(); }
            },
            error: function () {
              draft.previewOk = false;
              draft.previewError = '浏览器解码不了这张图片，不能保存。';
              renderAll();
            }
          }
        })]);
      nodes.push(preview);
    }
    if (draft.fileName) {
      nodes.push(h('p', {class: 'footnote', text: draft.fileName +
        (draft.bytes ? ' · ' + Math.round(draft.bytes / 1024) + ' KB' : '')}));
    }
    const captionInput = h('input', {
      class: 'input',
      type: 'text',
      maxlength: String(ALBUM_MAX_CAPTION),
      value: draft.caption,
      placeholder: '这张图对你来说是什么？（可不填）',
      on: {input: function (event) {
        draft.caption = event.target.value;
        const counter = document.getElementById('album-caption-count');
        if (counter) counter.textContent = draft.caption.length + ' / ' + ALBUM_MAX_CAPTION;
      }}
    });
    nodes.push(field('说明', captionInput, '最多 200 字；这是你自己写的说明，不是图片内容的识别结果'));
    nodes.push(h('p', {class: 'footnote', id: 'album-caption-count',
      text: draft.caption.length + ' / ' + ALBUM_MAX_CAPTION}));
    if (draft.errors.length) nodes.push(statePanel('error', '这次还不能保存', draft.errors[0]));
    const busy = store.busy === 'album:import';
    const disabled = busy || !draft.previewOk || !draft.data || draft.caption.length > ALBUM_MAX_CAPTION;
    nodes.push(h('div', {class: 'form-actions'}, [
      btn(busy ? '保存中…' : '保存到相册', {variant: 'primary', icon: 'check',
        disabled: disabled,
        title: draft.previewOk ? '' : '先选择一张能预览的图片',
        onClick: submitAlbumImport}),
      btn('取消', {onClick: function () { goBack(); }})
    ]));
    nodes.push(h('p', {class: 'footnote',
      text: '保存后，图片和这条说明都留在这个实例里；不会抓取、生成或发到外面。'}));
    return page('导入图片', '先预览，确认能显示再保存', nodes);
  }

  async function submitAlbumImport() {
    const draft = store.albumDraft;
    if (!draft.previewOk || !draft.data) {
      draft.errors = ['先选择一张能预览的图片。'];
      renderAll();
      return;
    }
    if (draft.caption.length > ALBUM_MAX_CAPTION) {
      draft.errors = ['说明最多 200 字。'];
      renderAll();
      return;
    }
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: 'album:import', status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = 'album:import';
    const entry = ops.ensure(scope, {path: '/album/images'});
    const payload = {
      caption: draft.caption,
      content_type: draft.contentType,
      data: draft.data,
      operation_key: entry.key
    };
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在保存图片…', key: entry.key};
    renderAll();
    const res = await request('POST', '/album/images', {csrf: store.conn.csrf, body: payload});
    finishMutation(scope, deriveMutationOutcome(res), '图片导入');
    if (store.result && (store.result.status === 'applied' ||
        store.result.status === 'already_applied')) {
      store.albumDraft = {caption: '', fileName: '', contentType: '', dataUrl: '', data: '',
        bytes: 0, previewOk: false, previewError: '', errors: []};
      store.data.album = null;
      go('album', {keepResult: true});
    }
  }

  /* 历史一行：点开就是“这一版”，不再新开屏。 */
  function projectHistoryRow(row, selected) {
    const chips = [chip(formatWhen(row.createdAt) || '时间未提供', 'is-neutral')];
    if (selected) chips.push(chip('正在查看', 'is-warn'));
    return h('li', {}, [
      h('button', {class: 'list-item', type: 'button', on: {click: function () {
        loadProjectVersion(row.versionId);
      }}}, [
        h('span', {class: 'list-top'}, [
          h('span', {class: 'list-title',
            text: (row.versionNumber !== null ? 'v' + row.versionNumber : '版本') + (row.isHead ? '（当前）' : '')}),
          h('span', {class: 'list-meta', text: row.chars !== null ? row.chars + ' 字' : ''})
        ]),
        h('span', {class: 'list-chips'}, chips.concat([
          chip(safeLabel(row.versionId, 24), 'is-mono')
        ]))
      ])
    ]);
  }

  /* “这一版”卡片：正文、字数、时间、截断，以及对比 / 派生两个动作。 */
  function projectVersionSection(project, state) {
    const heading = state.versionNumber !== null ? 'v' + state.versionNumber + ' 这一版' : '这一版';
    const nodes = [h('h2', {class: 'card-title', text: heading})];
    if (state.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读这一版…'));
      return h('div', {class: 'card'}, nodes);
    }
    if (state.phase !== 'ready') {
      nodes.push(statePanel(state.phase === 'offline' ? 'offline' : 'error', '没有读到这一版',
        state.message, [btn('重试', {onClick: function () { loadProjectVersion(state.versionId); }})]));
      return h('div', {class: 'card'}, nodes);
    }
    const version = projectVersionView(state.doc);
    if (!version) {
      nodes.push(statePanel('error', '服务没有返回这一版的正文', '响应里没有 version 对象。'));
      return h('div', {class: 'card'}, nodes);
    }
    const chips = [];
    if (version.versionNumber !== null) chips.push(chip('v' + version.versionNumber, 'is-neutral'));
    chips.push(chip(formatWhen(version.createdAt) || '时间未提供', 'is-neutral'));
    if (version.chars !== null) chips.push(chip(version.chars + ' 字', 'is-neutral'));
    if (version.truncated) chips.push(chip('正文被截断', 'is-warn'));
    nodes.push(h('div', {class: 'row-actions'}, chips));
    nodes.push(kv('版本 id', version.versionId, true));
    if (version.parentVersionId) nodes.push(kv('上一版', version.parentVersionId, true));
    const withheld = version.withheld || !version.contentAvailable;
    if (withheld) {
      nodes.push(statePanel('warn', '这一版正文没有返回',
        '服务收回了这一版的正文（来源不可读或已撤回）；版本、时间与字数仍然照实显示。'));
    } else {
      nodes.push(h('pre', {class: 'project-text', text: version.content || '（这一版是空的）'}));
    }
    if (version.truncated) {
      nodes.push(h('p', {class: 'footnote',
        text: '服务只返回了这一版的前一段（content_truncated）；对比只能比到这部分，也不能把截断的正文当作整篇派生。'}));
    }
    const canDerive = !withheld && !version.truncated && !!version.content.trim();
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('和当前版本对比', {disabled: withheld, onClick: function () { loadProjectCompare(version.versionId); }}),
      btn('从这一版派生新作品', {variant: 'primary', disabled: !canDerive,
        title: canDerive ? '' : '这一版正文被截断或没有返回，不能派生', onClick: function () {
          startDeriveProject(project, version);
        }})
    ]));
    return h('div', {class: 'card'}, nodes);
  }

  /* 对比卡片：只显示服务真的算出来的差异（新增/删除/未变）；没给的字段不补。 */
  function projectCompareSection(state) {
    const nodes = [h('h2', {class: 'card-title', text: '和当前版本对比'})];
    if (state.phase === 'loading') {
      nodes.push(statePanel('loading', '正在比较这一版与当前版本…'));
      return h('div', {class: 'card'}, nodes);
    }
    if (state.phase !== 'ready') {
      nodes.push(statePanel(state.phase === 'offline' ? 'offline' : 'error', '没有拿到对比结果',
        state.message, [btn('重试', {onClick: function () {
          loadProjectCompare(state.versionId);
        }})]));
      return h('div', {class: 'card'}, nodes);
    }
    const view = projectCompareView(state.doc);
    if (!view) {
      nodes.push(statePanel('error', '服务没有返回对比结果', '响应里没有 compare 对象。'));
      return h('div', {class: 'card'}, nodes);
    }
    const fromNumber = view.from.versionNumber;
    const toNumber = view.to.versionNumber;
    if (fromNumber !== null || toNumber !== null) {
      nodes.push(kv('比较', (fromNumber !== null ? 'v' + fromNumber : '这一版') + ' → ' +
        (toNumber !== null ? 'v' + toNumber : '当前 head')));
    }
    if (view.identicalVersion) {
      nodes.push(statePanel('ok', '这一版就是当前 head', '服务确认两边是同一个 version id。'));
    } else if (view.from.withheld || view.to.withheld) {
      nodes.push(statePanel('warn', '对比没有给出正文差异', '至少一侧的正文被服务收回。'));
    } else if (view.bodyChanged === true) {
      const parts = [];
      if (view.addedLines !== null) parts.push('新增 ' + view.addedLines + ' 行');
      if (view.removedLines !== null) parts.push('删除 ' + view.removedLines + ' 行');
      nodes.push(kv('正文', parts.length ? parts.join(' · ') : '有变化（服务没给行数）'));
    } else if (view.bodyChanged === false) {
      nodes.push(kv('正文', '没有变化'));
    } else {
      nodes.push(kv('正文', '无法确定' + (view.reason ? '（' + reasonText(view.reason) + '）' : '')));
    }
    if (view.charDelta !== null) {
      nodes.push(kv('字数差', (view.charDelta > 0 ? '+' : '') + view.charDelta + ' 字'));
    }
    if (view.hashChanged !== null) nodes.push(kv('存储哈希', view.hashChanged ? '变了' : '没变'));
    if (view.headersDiffer !== null) nodes.push(kv('作品头部', view.headersDiffer ? '有变化' : '没有变化'));
    if (view.machineHeadOnly === true) {
      nodes.push(statePanel('warn', '只有机器 head 移动了', '两边的存储哈希与正文都没有变。'));
    }
    if (view.firstDifference) {
      const first = view.firstDifference;
      const at = first.index !== null ? '第 ' + first.index + ' 字' : '第一处';
      nodes.push(kv('第一处不同', at + '：' + (first.fromContext || '（空）') + ' → ' + (first.toContext || '（空）')));
    }
    if (view.fromTruncated || view.toTruncated) {
      nodes.push(statePanel('warn', '对比只覆盖前一段',
        '至少一侧的正文被截断（bounded_chars）；服务没有声称整篇都比过。'));
    } else if (view.boundedLines) {
      nodes.push(h('p', {class: 'footnote', text: '行数只统计了有界的前若干行。'}));
    }
    if (view.reason && view.bodyChanged !== null) {
      nodes.push(h('p', {class: 'footnote', text: '服务原因码：' + reasonText(view.reason)}));
    }
    return h('div', {class: 'card'}, nodes);
  }

  function renderProject() {
    const gate = projectGate();
    if (gate) return page('项目详情', '', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const entry = store.data.project;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读这一份…'));
      return page('项目详情', '', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到这一份',
        entry.message, [btn('重试', {onClick: function () { loadProject(entry.artifactId); }})]));
      return page('项目详情', '', nodes);
    }
    const view = projectDetailView(entry.doc);
    if (!view) {
      nodes.push(statePanel('error', '服务没有返回这一份项目', '响应里没有 project 对象。'));
      return page('项目详情', '', nodes);
    }
    const project = view.project;
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: project.title}),
      h('div', {class: 'row-actions'}, [
        projectStatusChip(project),
        project.versionNumber !== null ? chip('v' + project.versionNumber, 'is-neutral') : null,
        project.revision !== null ? chip('rev ' + project.revision, 'is-neutral') : null
      ]),
      kv('工件', project.id, true),
      kv('最近更新', formatWhen(project.updatedAt) || '服务未提供'),
      kv('当前 head', project.head || '服务未提供', true)
    ]));

    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '正文'}),
      view.contentAvailable
        ? h('pre', {class: 'project-text', text: view.content || '（这一版是空的）'})
        : statePanel('warn', '这一版没有正文', '服务没有返回 content。'),
      view.contentChars !== null
        ? h('p', {class: 'footnote', text: view.contentChars + ' 字'})
        : null
    ]));

    const history = view.history;
    const historyState = store.projectHistory && store.projectHistory.artifactId === project.id
      ? store.projectHistory : null;
    const historyRows = projectHistoryMerge(history.entries,
      historyState && historyState.entries ? historyState.entries : []);
    const selectedVersion = store.projectVersion && store.projectVersion.artifactId === project.id
      ? store.projectVersion.versionId : '';
    const historyNodes = [h('h2', {class: 'card-title', text: '版本历史'})];
    if (!historyRows.length) {
      historyNodes.push(statePanel('warn', '服务没有报告任何版本',
        history.reason ? reasonText(history.reason) : ''));
    } else {
      historyNodes.push(h('ul', {class: 'list'}, historyRows.map(function (row) {
        return projectHistoryRow(row, row.versionId === selectedVersion);
      })));
      /* 详情自带的历史通常只有 head；分页结果没回来之前，这句“不全”仍然成立。 */
      const paged = historyState && historyState.phase === 'ready' && historyState.entries.length > 0;
      if (!history.complete && !paged) {
        historyNodes.push(statePanel('warn', '只有当前版本是确定的',
          (history.reason ? reasonText(history.reason) : '服务没有提供完整版本列表')
          + '；可以点“查看全部版本”向 owner 要最近 10 个。'));
      }
    }
    if (historyState && (historyState.phase === 'error' || historyState.phase === 'offline')) {
      /* 重试沿用失败那一次的游标：已经翻过的页不丢，也不从头再读一遍。 */
      const retryBefore = typeof historyState.retryBefore === 'number' ? historyState.retryBefore : null;
      historyNodes.push(statePanel(historyState.phase === 'offline' ? 'offline' : 'error',
        '没有读到更早的版本', historyState.message, [btn('重试', {onClick: function () {
          loadProjectHistory(retryBefore);
        }})]));
    } else if (!historyState) {
      historyNodes.push(h('div', {class: 'row-actions'}, [
        btn('查看全部版本（最近 ' + PROJECT_HISTORY_LIMIT + ' 个）', {onClick: function () {
          loadProjectHistory(null);
        }})
      ]));
    } else if (historyState.phase === 'loading') {
      historyNodes.push(statePanel('loading', '正在读取最近 ' + PROJECT_HISTORY_LIMIT + ' 个版本…'));
    } else if (historyState.phase === 'ready') {
      const lines = [];
      if (historyState.totalVersions !== null) lines.push('共 ' + historyState.totalVersions + ' 个版本');
      if (historyState.complete) lines.push('历史是完整的');
      else if (historyState.reason) lines.push('历史不完整：' + reasonText(historyState.reason));
      if (lines.length) historyNodes.push(h('p', {class: 'footnote', text: lines.join(' · ')}));
      if (historyState.hasMore) {
        if (historyState.nextBefore !== null) {
          historyNodes.push(h('div', {class: 'row-actions'}, [
            btn(historyState.loadingMore ? '正在读取更早的版本…' : '加载更早的版本', {
              disabled: historyState.loadingMore === true,
              onClick: function () { loadProjectHistory(historyState.nextBefore); }
            })
          ]));
        } else {
          historyNodes.push(statePanel('warn', '服务说还有更早的版本，但没有给游标',
            '没有 next_before_version_number，界面不会猜一个页码继续翻。'));
        }
      } else if (historyState.complete) {
        historyNodes.push(h('p', {class: 'footnote', text: '已经到最早的一版。'}));
      } else {
        historyNodes.push(h('p', {class: 'footnote',
          text: '服务没有给出更早的版本，也没有说历史是完整的。'}));
      }
    }
    nodes.push(h('div', {class: 'card'}, historyNodes));

    const versionState = store.projectVersion && store.projectVersion.artifactId === project.id
      ? store.projectVersion : null;
    if (versionState) nodes.push(projectVersionSection(project, versionState));
    const compareState = store.projectCompare && store.projectCompare.artifactId === project.id
      && versionState && store.projectCompare.versionId === versionState.versionId
      ? store.projectCompare : null;
    if (compareState) nodes.push(projectCompareSection(compareState));

    const actions = [];
    if (project.status === 'active') {
      actions.push(btn('修改正文', {variant: 'primary', icon: 'note',
        onClick: function () { editProject(view); }}));
      actions.push(btn('归档', {icon: 'check',
        onClick: function () { confirmCloseProject(view); }}));
    } else {
      actions.push(btn('已归档：不能再改', {disabled: true}));
    }
    actions.push(btn('刷新', {icon: 'refresh',
      onClick: function () { loadProject(project.id); }}));
    nodes.push(h('div', {class: 'row-actions'}, actions));
    nodes.push(h('p', {class: 'footnote',
      text: '修改以你上次读到的 head 为基准；别处先写过就会被服务拒绝（不会覆盖）。'}));
    return page('项目详情', '正文、版本与状态都来自 owner', nodes);
  }

  function renderProjectEditor() {
    const draft = store.projectDraft;
    if (!draft) {
      return page('写项目', '', [statePanel('empty', '没有打开的草稿'),
        btn('回到项目', {onClick: function () { go('projects'); }})]);
    }
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const editing = draft.mode === 'edit';
    const deriving = draft.mode === 'derive';

    const titleInput = h('input', {
      class: 'input', type: 'text', maxlength: PROJECT_TITLE_MAX, autocomplete: 'off',
      placeholder: '一句话标题', value: draft.title
    });
    const titleCount = h('span', {class: 'counter', text: draft.title.length + ' / ' + PROJECT_TITLE_MAX});
    titleInput.addEventListener('input', function () {
      draft.title = titleInput.value;
      titleCount.textContent = draft.title.length + ' / ' + PROJECT_TITLE_MAX;
    });

    const contentArea = h('textarea', {
      class: 'textarea', maxlength: PROJECT_CONTENT_MAX,
      placeholder: '第一版正文；保存后成为这一份的第一个版本。'
    });
    contentArea.value = draft.content;
    const contentCount = h('span', {class: 'counter', text: draft.content.length + ' / ' + PROJECT_CONTENT_MAX});
    contentArea.addEventListener('input', function () {
      draft.content = contentArea.value;
      contentCount.textContent = draft.content.length + ' / ' + PROJECT_CONTENT_MAX;
    });

    const busy = store.busy === PROJECT_SCOPES.create || store.busy === PROJECT_SCOPES.commit
      || store.busy === PROJECT_SCOPES.derive;
    const errors = draft.errors && draft.errors.length
      ? h('ul', {class: 'errors'}, draft.errors.map(function (message) { return h('li', {text: message}); }))
      : null;

    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title',
        text: editing ? '改这一份的正文' : (deriving ? '从旧版本派生新作品' : '新的一份')}),
      deriving ? kv('来源作品', draft.sourceTitle) : null,
      deriving && draft.sourceVersionNumber !== null
        ? kv('来源版本', 'v' + draft.sourceVersionNumber + '（' + safeLabel(draft.sourceVersionId, 60) + '）', true)
        : null,
      editing
        ? kv('标题（创建后不改名）', draft.title)
        : field('标题', titleInput, '最多 ' + PROJECT_TITLE_MAX + ' 字'),
      editing ? null : titleCount,
      field('正文', contentArea, '最多 ' + PROJECT_CONTENT_MAX + ' 字'),
      contentCount,
      errors,
      h('div', {class: 'form-actions'}, [
        btn(busy ? '提交中…' : (deriving ? '保存派生' : '保存'), {variant: 'primary', icon: 'check',
          disabled: busy, onClick: submitProjectDraft}),
        btn('取消', {onClick: function () { goBack(); }})
      ])
    ]));
    nodes.push(h('p', {class: 'footnote', text: editing && draft.head
      ? '这次写入以 head（' + safeLabel(draft.head, 60) + '）为基准；别处先改过就会被拒绝。'
      : (editing ? '服务没有给出 head：这次修改没有冲突保护。'
        : (deriving
          ? '保存后由服务新建一件作品并记下来源工件与版本（purpose=adapt）；原作品不会被改动。'
          : '保存后由服务创建工件并提交第一个版本。'))}));
    return page(editing ? '修改项目' : (deriving ? '派生新作品' : '新项目'),
      '提交后以服务返回的状态为准', nodes);
  }

  function blankProjectDraft() {
    return {mode: 'create', artifactId: '', title: '', content: '', head: '', revision: null, errors: []};
  }

  function startNewProject() {
    store.projectDraft = blankProjectDraft();
    store.result = null;
    go('project-edit', {detail: null});
  }

  function openProject(artifactId) {
    const id = protocolId(artifactId, 200);
    if (!id.ok) return;
    if (store.data.project && store.data.project.artifactId !== id.value) {
      store.data.project = null;
      store.projectHistory = null;
      store.projectVersion = null;
      store.projectCompare = null;
    }
    store.result = null;
    go('project', {detail: id.value});
  }

  function editProject(view) {
    store.projectDraft = {
      mode: 'edit', artifactId: view.project.id, title: view.project.title,
      content: view.content, head: view.project.head, revision: view.project.revision, errors: []
    };
    store.result = null;
    go('project-edit', {detail: view.project.id});
  }

  /* 从“这一版”开始派生：打开现有写作页，带上来源工件/版本与 adapt 说明。 */
  function startDeriveProject(project, version) {
    const draft = projectDeriveDraft(project, version);
    if (!draft) {
      store.result = {scope: PROJECT_SCOPES.derive, status: 'blocked',
        message: '来源工件或版本 id 不可用，没有打开派生草稿。'};
      renderAll();
      return;
    }
    store.projectDraft = draft;
    store.result = null;
    go('project-edit', {detail: project.id});
  }

  function knownProjectHistoryRows() {
    const entry = store.data.project;
    const view = entry && entry.phase === 'ready' ? projectDetailView(entry.doc) : null;
    const page = store.projectHistory && store.projectHistory.artifactId === store.projectDetailId
      ? store.projectHistory : null;
    return projectHistoryMerge(view ? view.history.entries : [],
      page ? page.entries : []);
  }

  function versionNumberFor(versionId) {
    const rows = knownProjectHistoryRows();
    for (let i = 0; i < rows.length; i += 1) {
      if (rows[i].versionId === versionId) return rows[i].versionNumber;
    }
    return null;
  }

  /* 分页读版本元数据：before 是上一页给的 next_before_version_number。
     第一页与追加共用一条路；追加时按 version_id 去重，不重复插入已有条目。 */
  async function loadProjectHistory(before) {
    const artifactId = store.projectDetailId;
    const cursor = typeof before === 'number' && isFinite(before) && before > 0
      ? Math.floor(before) : null;
    const path = projectVersionsPath(artifactId, {limit: PROJECT_HISTORY_LIMIT, before: cursor});
    if (!path) {
      store.projectHistory = {phase: 'error', artifactId: artifactId || '', entries: [],
        hasMore: false, nextBefore: null, totalVersions: null, complete: false, reason: '',
        retryBefore: cursor, loadingMore: false, message: '项目 id 不可用，没有读取版本列表'};
      renderAll();
      return;
    }
    const existing = store.projectHistory && store.projectHistory.artifactId === artifactId
      && Array.isArray(store.projectHistory.entries) && store.projectHistory.entries.length
      ? store.projectHistory : null;
    store.projectHistory = {
      phase: existing ? 'ready' : 'loading',
      artifactId: artifactId,
      entries: existing ? existing.entries : [],
      hasMore: existing ? existing.hasMore : false,
      nextBefore: existing ? existing.nextBefore : null,
      totalVersions: existing ? existing.totalVersions : null,
      complete: existing ? existing.complete : false,
      reason: existing ? existing.reason : '',
      retryBefore: cursor,
      loadingMore: true,
      message: ''
    };
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    const kept = existing || {entries: [], hasMore: false, nextBefore: null,
      totalVersions: null, complete: false, reason: ''};
    if (outcome.status === 'ok') {
      const page = projectVersionPageView(outcome.doc);
      if (!page) {
        store.projectHistory = {phase: 'error', artifactId: artifactId, entries: kept.entries,
          hasMore: kept.hasMore, nextBefore: kept.nextBefore, totalVersions: kept.totalVersions,
          complete: kept.complete, reason: kept.reason, retryBefore: cursor, loadingMore: false,
          message: '服务返回成功，但响应里没有 history 对象'};
      } else {
        store.projectHistory = {
          phase: 'ready', artifactId: artifactId,
          entries: projectHistoryMerge(kept.entries, page.entries),
          hasMore: page.hasMore,
          nextBefore: page.nextBefore,
          totalVersions: page.totalVersions !== null ? page.totalVersions : kept.totalVersions,
          complete: page.complete,
          reason: page.reason,
          loadingMore: false,
          message: ''
        };
      }
    } else {
      const reason = outcome.reason ? reasonText(outcome.reason) : '';
      store.projectHistory = {
        phase: outcome.status === 'offline' ? 'offline' : 'error',
        artifactId: artifactId,
        entries: kept.entries,
        hasMore: kept.hasMore,
        nextBefore: kept.nextBefore,
        totalVersions: kept.totalVersions,
        complete: kept.complete,
        reason: kept.reason,
        retryBefore: cursor,
        loadingMore: false,
        message: reason ? outcome.message + '：' + reason : outcome.message
      };
    }
    renderAll();
  }

  async function loadProjectVersion(versionId) {
    const artifactId = store.projectDetailId;
    const vid = projectPathId(versionId);
    const path = projectVersionPath(artifactId, vid);
    if (!path) {
      store.projectVersion = {phase: 'error', artifactId: artifactId || '', versionId: vid,
        versionNumber: null, doc: null, message: '项目或版本 id 不可用，没有读取这一版'};
      store.projectCompare = null;
      renderAll();
      return;
    }
    store.projectVersion = {phase: 'loading', artifactId: artifactId, versionId: vid,
      versionNumber: versionNumberFor(vid), doc: null, message: ''};
    store.projectCompare = null;
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.projectVersion = {phase: 'ready', artifactId: artifactId, versionId: vid,
        versionNumber: versionNumberFor(vid), doc: outcome.doc, message: ''};
    } else {
      const reason = outcome.reason ? reasonText(outcome.reason) : '';
      store.projectVersion = {
        phase: outcome.status === 'offline' ? 'offline' : 'error',
        artifactId: artifactId, versionId: vid, versionNumber: versionNumberFor(vid), doc: null,
        message: reason ? outcome.message + '：' + reason : outcome.message
      };
    }
    renderAll();
  }

  async function loadProjectCompare(versionId) {
    const artifactId = store.projectDetailId;
    const vid = projectPathId(versionId);
    const path = projectComparePath(artifactId, vid);
    if (!path) {
      store.projectCompare = {phase: 'error', artifactId: artifactId || '', versionId: vid,
        doc: null, message: '项目或版本 id 不可用，没有发起对比'};
      renderAll();
      return;
    }
    store.projectCompare = {phase: 'loading', artifactId: artifactId, versionId: vid,
      doc: null, message: ''};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.projectCompare = {phase: 'ready', artifactId: artifactId, versionId: vid,
        doc: outcome.doc, message: ''};
    } else {
      const reason = outcome.reason ? reasonText(outcome.reason) : '';
      store.projectCompare = {
        phase: outcome.status === 'offline' ? 'offline' : 'error',
        artifactId: artifactId, versionId: vid, doc: null,
        message: reason ? outcome.message + '：' + reason : outcome.message
      };
    }
    renderAll();
  }

  async function loadProject(artifactId) {
    /* 详情重读就是新事实：之前展开的版本页/这一版/对比都作废，不拿旧 head 冒充。 */
    store.projectHistory = null;
    store.projectVersion = null;
    store.projectCompare = null;
    const id = protocolId(artifactId, 200);
    if (!id.ok) {
      store.data.project = {phase: 'error', doc: null, message: '项目 id 不可用', artifactId: String(artifactId)};
      renderAll();
      return;
    }
    store.data.project = {phase: 'loading', doc: null, message: '', artifactId: id.value};
    renderAll();
    const res = await request('GET', projectPath(id.value), {});
    /* 期间可能已经打开另一份（例如派生成功后跳去新作品）：旧响应不许覆盖新的详情。 */
    if (!store.data.project || store.data.project.artifactId !== id.value) return;
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.project = {phase: 'ready', doc: outcome.doc, message: '', artifactId: id.value};
    } else {
      store.data.project = {
        phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status, artifactId: id.value
      };
    }
    renderAll();
  }

  async function submitProjectDraft() {
    const draft = store.projectDraft;
    if (!draft) return;
    const checked = validateProjectDraft({title: draft.title, content: draft.content});
    if (!checked.ok) {
      draft.errors = checked.errors;
      renderAll();
      return;
    }
    draft.errors = [];
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: draft.mode === 'derive' ? PROJECT_SCOPES.derive : PROJECT_SCOPES.create,
        status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    if (draft.mode === 'derive') {
      /* 派生写的是新作品，键与 commit 分开：同一个 key 只对应“派生这一件”这个意图。 */
      const scope = PROJECT_SCOPES.derive;
      const entry = ops.ensure(scope, {path: '/projects/derived'});
      const payload = buildProjectDerivePayload({
        title: checked.value.title, content: checked.value.content,
        source_artifact_id: draft.artifactId, source_version_id: draft.sourceVersionId,
        purpose: draft.purpose, note: draft.note, operation_key: entry.key
      });
      if (!payload) {
        store.result = {scope: scope, status: 'blocked',
          message: '这次派生缺少来源工件/版本或有效的作用（adapt），没有发出。'};
        renderAll();
        return;
      }
      store.busy = scope;
      store.result = {scope: scope, status: 'pending', message: '正在派生新作品…', key: entry.key};
      renderAll();
      const res = await request('POST', '/projects/derived', {csrf: store.conn.csrf, body: payload});
      const created = res && res.doc && res.doc.data && typeof res.doc.data.artifact_id === 'string'
        ? res.doc.data.artifact_id : '';
      finishMutation(scope, deriveMutationOutcome(res), '派生作品');
      const writeStatus = store.result && store.result.status;
      if (created && (writeStatus === 'applied' || writeStatus === 'already_applied')) {
        store.projectDraft = blankProjectDraft();
        store.data.projects = null;
        store.data.project = null;
        openProject(created);
      }
      return;
    }
    if (draft.mode === 'edit') {
      if (!draft.head) {
        store.result = {scope: PROJECT_SCOPES.commit, status: 'blocked',
          message: '服务没有给出 head，这次修改没有冲突保护，不能保存。'};
        renderAll();
        return;
      }
      const scope = PROJECT_SCOPES.commit;
      const path = projectPath(draft.artifactId) + '/commits';
      const entry = ops.ensure(scope, {path: '/projects/commits'});
      const payload = buildProjectCommitPayload({
        content: checked.value.content, expected_head: draft.head, operation_key: entry.key
      });
      if (!payload) {
        store.result = {scope: scope, status: 'blocked', message: '这次修改缺少正文或 head，没有发出。'};
        renderAll();
        return;
      }
      store.busy = scope;
      store.result = {scope: scope, status: 'pending', message: '正在提交新版本…', key: entry.key};
      renderAll();
      const res = await request('POST', path, {csrf: store.conn.csrf, body: payload});
      finishMutation(scope, deriveMutationOutcome(res), '项目修改');
      const writeStatus = store.result && store.result.status;
      if (writeStatus === 'applied' || writeStatus === 'already_applied') {
        /* 新版本落定：草稿作废，回到详情读回服务的新 head（下一次写入用它做 CAS）。 */
        store.projectDraft = blankProjectDraft();
        store.data.project = null;
        go('project', {detail: draft.artifactId});
      }
      return;
    }
    const scope = PROJECT_SCOPES.create;
    const entry = ops.ensure(scope, {path: '/projects'});
    const payload = buildProjectCreatePayload({
      title: checked.value.title, content: checked.value.content, operation_key: entry.key
    });
    if (!payload) {
      store.result = {scope: scope, status: 'blocked', message: '这次创建缺少标题或正文，没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在创建项目…', key: entry.key};
    renderAll();
    const res = await request('POST', '/projects', {csrf: store.conn.csrf, body: payload});
    const created = res && res.doc && res.doc.data && typeof res.doc.data.artifact_id === 'string'
      ? res.doc.data.artifact_id : '';
    finishMutation(scope, deriveMutationOutcome(res), '创建项目');
    if (created) {
      store.projectDraft = blankProjectDraft();
      store.data.projects = null;
      openProject(created);
    }
  }

  function confirmCloseProject(view) {
    openSheet({
      title: '归档这一份项目？',
      lines: [view.project.title],
      note: '归档只是不再活跃：正文和每个版本都还在，随时能读回来。',
      confirmLabel: '归档',
      onConfirm: function () { submitProjectClose(view); }
    });
  }

  async function submitProjectClose(view) {
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: PROJECT_SCOPES.close, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = PROJECT_SCOPES.close;
    const entry = ops.ensure(scope, {path: '/projects/close'});
    const payload = buildProjectClosePayload({
      expected_revision: view.project.revision, operation_key: entry.key
    });
    if (!payload) {
      store.result = {scope: scope, status: 'blocked',
        message: '服务没有给出这一份的 revision，这次归档没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在归档…', key: entry.key};
    renderAll();
    const res = await request('POST', projectPath(view.project.id) + '/close',
      {csrf: store.conn.csrf, body: payload});
    finishMutation(scope, deriveMutationOutcome(res), '归档项目');
  }

  /* ---------------- 收藏 ---------------- */

  function renderReferences() {
    const gate = appGate('references');
    if (gate) return page('收藏', '只记出处，不抓取内容', [gate]);

    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);

    const sourceInput = h('input', {class: 'input', type: 'text', maxlength: 200, autocomplete: 'off', placeholder: '例如 supply:artifact/42'});
    const revisionInput = h('input', {class: 'input', type: 'text', maxlength: 120, autocomplete: 'off', placeholder: '来源版本，例如 3 或 sha 前缀'});
    const labelInput = h('input', {class: 'input', type: 'text', maxlength: 60, autocomplete: 'off', placeholder: '她想怎么记住它'});
    const addErrors = h('ul', {class: 'errors'});

    const submit = function () {
      const draft = validateReferenceDraft({
        source_id: sourceInput.value,
        source_revision: revisionInput.value,
        label: labelInput.value
      });
      if (!draft.ok) {
        addErrors.replaceChildren.apply(addErrors, draft.errors.map(function (message) {
          return h('li', {text: message});
        }));
        return;
      }
      addErrors.replaceChildren();
      submitReference(draft.value);
    };

    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '添加一条收藏'}),
      h('p', {class: 'card-note', text: '只写指针，不会访问外网，也不会抓取页面内容。'}),
      field('出处标识', sourceInput, '服务侧的来源 id，最多 200 字'),
      field('来源版本', revisionInput, '记录当时的版本，最多 120 字'),
      field('名字', labelInput, '最多 60 字'),
      addErrors,
      h('div', {class: 'row-actions'}, [
        btn(store.busy === 'reference:add' ? '提交中…' : '添加收藏', {variant: 'primary', icon: 'plus', disabled: store.busy === 'reference:add', onClick: submit}),
        btn('刷新', {icon: 'refresh', onClick: function () { loadResource('references'); }})
      ])
    ]));

    const entry = store.data.references;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取收藏…'));
      return page('收藏', '只记出处，不抓取内容', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到收藏', entry.message, [
        btn('重试', {onClick: function () { loadResource('references'); }})
      ]));
      return page('收藏', '只记出处，不抓取内容', nodes);
    }

    const doc = entry.doc || {};
    const items = Array.isArray(doc.items) ? doc.items.filter(function (item) { return item && typeof item === 'object'; }) : null;
    if (!items) {
      nodes.push(statePanel('error', '服务没有返回收藏列表', '响应里没有 items 数组。'));
      return page('收藏', '只记出处，不抓取内容', nodes);
    }
    if (!items.length) {
      nodes.push(statePanel('empty', '还没有收藏', '记下想读的文章或想看的电影；存的是指针，不是内容。'));
      return page('收藏', '只记出处，不抓取内容', nodes);
    }

    nodes.push(h('ul', {class: 'list'}, items.map(function (item) {
      const label = sanitizeLine(item.label, 80) || '未命名收藏';
      const sourceId = sanitizeLine(item.source_id, 200);
      const revision = sanitizeLine(item.source_revision, 120);
      const read = item.read === true;
      const fetched = item.content_fetched === true;
      return h('li', {}, [h('div', {class: 'list-item is-static'}, [
        h('span', {class: 'list-top'}, [
          h('span', {class: 'list-title', text: label}),
          chip(read ? '已读' : '未读', read ? 'is-ok' : 'is-neutral')
        ]),
        h('span', {class: 'list-text is-full', text: (sourceId || '出处未提供') + (revision ? ' @ ' + revision : '')}),
        h('span', {class: 'list-chips'}, [
          chip(fetched ? '已取得内容' : '未抓取内容', fetched ? 'is-ok' : 'is-warn')
        ])
      ])]);
    })));

    nodes.push(h('p', {class: 'footnote', text: '收藏不等于读过或理解；内容取得状态以服务字段为准，界面不打开外链。'}));
    return page('收藏', '只记出处，不抓取内容', nodes);
  }

  async function submitReference(value) {
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: 'reference:add', status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = 'reference:add:' + value.source_id + '@' + value.source_revision;
    const entry = ops.ensure(scope, {path: '/references'});
    const payload = {
      source_id: value.source_id,
      source_revision: value.source_revision,
      label: value.label,
      operation_key: entry.key
    };
    store.busy = 'reference:add';
    store.result = {scope: scope, status: 'pending', message: '正在提交收藏…', key: entry.key};
    renderAll();
    const res = await request('POST', '/references', {csrf: store.conn.csrf, body: payload});
    finishMutation(scope, deriveMutationOutcome(res), '添加收藏');
  }

  /* ---------------- 阅读 ---------------- */

  /* ---------------- 阅读工作台 ---------------- */

  const READING_PAGE_SUBTITLE = '真实正文、显式停点、私人批注';

  function readingBusy(kind) {
    return typeof store.busy === 'string' && store.busy.indexOf(kind) === 0;
  }

  function readingResultHint() {
    const result = store.result;
    if (!result || !result.scope) return null;
    const kind = String(result.scope).split(':')[0];
    if (READING_SCOPES.indexOf(kind) < 0) return null;
    if (result.reason === 'PAGE_NOT_ACQUIRED' || result.reason === 'page_not_acquired') {
      const page = readingScopePage(result.scope);
      return statePanel('warn', '这一版这一页还没取得',
        '服务只接受已经读过的版本页。先保存阅读停点，再写笔记或疑问。',
        [btn(page === null ? '保存当前页停点' : '保存第 ' + page + ' 页停点',
          {small: true, variant: 'primary', onClick: saveReadingPosition})]);
    }
    if (result.status === 'conflict' ||
        /_CONFLICT$/.test(String(result.reason || '').toUpperCase()) ||
        String(result.reason || '') === 'CONFLICT') {
      const page = readingScopePage(result.scope);
      return statePanel('warn', '材料或事项已经变过，这次没有写入',
        '草稿还在下面。先重新读取，再决定要不要按新版本重写——界面不会自己覆盖。',
        [btn('重新读取材料', {small: true, onClick: function () { loadReadingMaterial(page); }})]);
    }
    return null;
  }

  function readingAnchorPanel(view) {
    const anchor = store.readingAnchor;
    if (!anchor) return null;
    if (anchor.pending) {
      const mismatch = !!(anchor.sourceRevision && view.sourceRevision &&
        anchor.sourceRevision !== view.sourceRevision);
      return statePanel('warn', '这条记录锚在第 ' + anchor.page + ' 页',
        mismatch
          ? '记下时的材料版本（' + safeLabel(anchor.sourceRevision, 40) + '）与当前版本（' +
            (view.sourceRevisionLabel || '未知') + '）不同，界面不会自己跳到当前版本的第 ' + anchor.page + ' 页。'
          : '还没确认当前版本是否与锚点一致，界面不直接跳页。',
        [
          btn('按当前版本看第 ' + anchor.page + ' 页', {small: true, onClick: function () {
            store.readingAnchor = null;
            goToReadingPage(anchor.page);
          }}),
          btn('留在当前页', {small: true, onClick: function () { store.readingAnchor = null; renderAll(); }})
        ]);
    }
    return statePanel('ok', '从锚点回到第 ' + anchor.page + ' 页',
      '版本一致（' + (view.sourceRevisionLabel || '未知') + '）。');
  }

  function readingMaterialCard(view) {
    const pageText = view.page === null ? '?' : String(view.page);
    const pagesText = view.pages === null ? '?' : String(view.pages);
    const meta = [
      chip('第 ' + pageText + ' / ' + pagesText + ' 页', 'is-neutral'),
      chip(view.understanding && view.understanding !== 'UNKNOWN'
        ? '理解：' + safeLabel(view.understanding, 20)
        : '理解：未知', 'is-warn')
    ];
    if (view.versionNumber !== null) meta.push(chip('版本号 ' + view.versionNumber, 'is-neutral'));
    if (view.contentAvailable) meta.push(chip('有正文', 'is-ok'));
    const facts = [];
    if (view.sourceRevisionLabel) facts.push(kv('材料版本', view.sourceRevisionLabel, true));
    if (view.artifactIdLabel) facts.push(kv('材料', view.artifactIdLabel, true));
    facts.push(kv('已保存停点', view.bookmark && view.bookmark.page !== null
      ? '第 ' + view.bookmark.page + ' 页' + (view.bookmarkStale ? '（记在另一个版本上）' : '')
      : '还没有保存过（翻页不算停点）', false));
    return h('section', {class: 'card reading-card'}, [
      h('h2', {class: 'reading-title', text: view.title || '（服务没有给标题）'}),
      h('div', {class: 'reading-meta'}, meta),
      h('div', {class: 'reading-facts'}, facts),
      view.needsReread
        ? h('p', {class: 'reading-warn', text: view.bookmarkStale
          ? '旧版本的停点：这一页记在另一个版本上，需要按当前版本重看。'
          : '服务标记这份材料需要重看（needs_reread）。'})
        : null,
      view.pageKnown ? null : h('p', {class: 'reading-warn',
        text: '服务给的页码对不上这份材料的页数，界面不会自己猜一个。'})
    ]);
  }

  function readingTextCard(view) {
    const body = view.text
      ? h('p', {class: 'reading-text', text: view.text})
      : statePanel('empty', '这一页没有正文', view.contentAvailable
        ? '服务说这一页是空的。'
        : '服务没有声明这一页有正文（content_available 不是 true），界面不补内容。');
    return h('section', {class: 'card reading-page'}, [
      h('h3', {class: 'card-title', text: '正文'}),
      body
    ]);
  }

  function readingPager(view) {
    const page = view.page;
    const pages = view.pages;
    const busy = readingBusy('reading-position');
    const canPrev = page !== null && page > 1;
    const canNext = page !== null && pages !== null && page < pages;
    const canSave = page !== null && !!view.sourceRevision && !!view.privateHead && !busy;
    const jumpInput = h('input', {
      class: 'input reading-pager-input',
      type: 'number',
      min: '1',
      max: pages === null ? null : String(pages),
      step: '1',
      inputmode: 'numeric',
      autocomplete: 'off',
      'aria-label': '跳到第几页',
      value: page === null ? '' : String(page)
    });
    const jump = function () {
      const value = readingPageValue(Number(jumpInput.value));
      if (value === null) {
        store.result = {scope: 'reading-position', status: 'blocked',
          message: '页码要是 1 以上的整数。'};
        renderAll();
        return;
      }
      goToReadingPage(value);
    };
    jumpInput.addEventListener('keydown', function (event) {
      if (event && event.key === 'Enter') {
        event.preventDefault();
        jump();
      }
    });
    return h('section', {class: 'card reading-card'}, [
      h('h3', {class: 'card-title', text: '翻页与停点'}),
      h('div', {class: 'reading-pager'}, [
        btn('上一页', {small: true, disabled: !canPrev, onClick: function () { goToReadingPage(page - 1); }}),
        h('span', {class: 'reading-page-of', text: '第 ' + (page === null ? '?' : page) + ' / ' +
          (pages === null ? '?' : pages) + ' 页'}),
        btn('下一页', {small: true, disabled: !canNext, onClick: function () { goToReadingPage(page + 1); }})
      ]),
      h('div', {class: 'reading-pager'}, [
        field('跳到', jumpInput, pages === null ? '服务没有给总页数' : '1 – ' + pages),
        btn('跳转', {small: true, onClick: jump})
      ]),
      h('div', {class: 'row-actions'}, [
        btn(busy ? '保存中…' : '保存阅读停点',
          {variant: 'primary', icon: 'check', disabled: !canSave, onClick: saveReadingPosition})
      ]),
      canSave ? null : h('p', {class: 'reading-warn', text: view.privateHead
        ? '没有可用的页码或材料版本，停点不能安全保存。'
        : '服务没有给 private_head：界面不会拿旧 head 去写停点。'}),
      h('p', {class: 'card-note', text: '翻页只是 GET，不写入；点「保存阅读停点」才把这一页记成停点。'})
    ]);
  }

  function readingAnnotationCard(view) {
    const draft = store.readingDraft;
    const busyNote = readingBusy('reading-note');
    const busyUnclear = readingBusy('reading-unclear');

    const titleInput = h('input', {
      class: 'input',
      type: 'text',
      maxlength: String(ANNOTATION_TITLE_MAX),
      autocomplete: 'off',
      placeholder: '一句话标题',
      value: draft.title
    });
    titleInput.addEventListener('input', function () { draft.title = titleInput.value; });

    const bodyArea = h('textarea', {
      class: 'textarea reading-textarea',
      maxlength: String(ANNOTATION_BODY_MAX),
      placeholder: '这一页里真正想记下的一段'
    });
    bodyArea.value = draft.body;
    bodyArea.addEventListener('input', function () { draft.body = bodyArea.value; });

    const noteArea = h('textarea', {
      class: 'textarea reading-textarea',
      maxlength: String(ANNOTATION_NOTE_MAX),
      placeholder: '哪里没读懂？写一句具体的疑问'
    });
    noteArea.value = draft.note;
    noteArea.addEventListener('input', function () { draft.note = noteArea.value; });

    const errorList = draft.errors && draft.errors.length
      ? h('ul', {class: 'errors'}, draft.errors.map(function (message) { return h('li', {text: message}); }))
      : null;

    const rows = view.annotations.map(function (entry) {
      const chips = [chip(entry.kindLabel, entry.kind === 'unclear' ? 'is-warn' : 'is-ok')];
      if (entry.page !== null) chips.push(chip('第 ' + entry.page + ' 页', 'is-neutral'));
      if (entry.stale) chips.push(chip('旧版本 · 需重看', 'is-warn'));
      const when = formatWhen(entry.createdAt);
      if (when) chips.push(chip(when, 'is-neutral'));
      const inner = [
        h('span', {class: 'list-top'}, [
          h('span', {class: 'list-title', text: entry.title || (entry.kind === 'unclear' ? '疑问' : '笔记')}),
          h('span', {class: 'list-meta', text: entry.page === null ? '' : '第 ' + entry.page + ' 页'})
        ]),
        entry.body ? h('span', {class: 'list-text', text: entry.body}) : null,
        entry.note ? h('span', {class: 'list-text', text: entry.note}) : null,
        h('span', {class: 'list-chips'}, chips)
      ];
      const li = [h('div', {class: 'list-item is-static reading-annotation' + (entry.stale ? ' is-stale' : '')}, inner)];
      if (entry.canNavigate) {
        li.push(h('div', {class: 'row-actions'}, [
          btn('回到这一页', {small: true, onClick: function () { goToReadingPage(entry.page); }})
        ]));
      } else if (entry.page !== null) {
        li.push(h('p', {class: 'reading-warn',
          text: '这条批注记在别的版本上：界面不会借它把你带到当前版本的第 ' + entry.page + ' 页。'}));
      }
      return h('li', {}, li);
    });

    return h('section', {class: 'card reading-card'}, [
      h('h3', {class: 'card-title', text: '这一页的私人批注'}),
      view.annotationsMissing
        ? statePanel('error', '服务没有返回批注列表',
          '响应里没有 annotations 数组；界面不会替你断定“没有批注”。')
        : (rows.length
          ? h('ul', {class: 'list'}, rows)
          : statePanel('empty', '这份材料还没有笔记或疑问', '写下的批注只出现在这个私人读取面。')),
      h('div', {class: 'reading-note-form'}, [
        field('笔记标题', titleInput, 'note 类批注需要标题'),
        field('笔记正文', bodyArea, '最多 ' + ANNOTATION_BODY_MAX + ' 字'),
        h('div', {class: 'form-actions'}, [
          btn(busyNote ? '保存中…' : '保存这一页的笔记', {variant: 'primary', icon: 'check',
            disabled: busyNote, onClick: function () { submitReadingAnnotation('note'); }})
        ])
      ]),
      h('div', {class: 'reading-note-form'}, [
        field('疑问', noteArea, 'unclear 类批注只写疑问本身，不冒充读懂'),
        h('div', {class: 'form-actions'}, [
          btn(busyUnclear ? '保存中…' : '记为疑问', {disabled: busyUnclear,
            onClick: function () { submitReadingAnnotation('unclear'); }})
        ])
      ]),
      errorList,
      h('p', {class: 'card-note',
        text: '批注锚在这一版这一页上；先保存过这一页的停点，否则服务会回 PAGE_NOT_ACQUIRED。'})
    ]);
  }

  function importCounterText(stats) {
    return stats.bytes + ' / ' + READING_CONTENT_MAX_BYTES + ' 字节 · ' + stats.pageCount + ' 页';
  }

  function readingImportCard(view) {
    const draft = store.readingDraft;
    const busy = readingBusy('reading-import');
    const area = h('textarea', {
      class: 'textarea reading-textarea reading-import-input',
      placeholder: '把本地正文粘进来；用 ' + READING_PAGE_SEPARATOR + ' 分页'
    });
    area.value = draft.importText;
    const counter = h('span', {class: 'counter', text: importCounterText(readingContentStats(draft.importText))});
    const errors = h('ul', {class: 'errors'});
    area.addEventListener('input', function () {
      draft.importText = area.value;
      const stats = readingContentStats(draft.importText);
      counter.textContent = importCounterText(stats);
      const check = validateReadingContent(draft.importText);
      errors.replaceChildren.apply(errors, check.errors.map(function (message) {
        return h('li', {text: message});
      }));
    });
    const hasRevision = !!(view && view.available && view.sourceRevision);
    return h('section', {class: 'card reading-card'}, [
      h('h3', {class: 'card-title', text: '导入本地正文'}),
      h('p', {class: 'card-note',
        text: '这是显式替换：当前材料的内容会被换掉，旧内容由 Supply 保留为旧版本，旧停点会标成需要重看。' +
          '标题、来源回执与理解状态都由服务回答，界面不声明。'}),
      field('正文', area, '正文 1–' + READING_CONTENT_MAX_BYTES + ' 字节，单页 ≤ ' +
        READING_PAGE_MAX_BYTES + ' 字节，最多 ' + READING_PAGES_MAX + ' 页'),
      counter,
      errors,
      h('div', {class: 'form-actions'}, [
        btn(busy ? '替换中…' : '确认替换当前材料', {variant: 'primary', icon: 'plus',
          disabled: busy || !hasRevision, onClick: confirmReadingImport}),
        hasRevision
          ? chip('当前版本 ' + view.sourceRevisionLabel, 'is-mono')
          : chip('没有可用版本：现在不能导入', 'is-warn')
      ])
    ]);
  }

  function legacyReadingRow(item) {
    const title = firstText(item, ['label', 'title', 'name', 'source_id']) || '未命名材料';
    const position = firstText(item, ['position', 'progress', 'page', 'offset', 'locator']);
    const when = firstText(item, ['updated_at', 'at', 'read_at', 'last_read_at']);
    const entries = displayFieldEntries(item, 6);
    return h('li', {}, [h('div', {class: 'list-item is-static'}, [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: title}),
        when ? h('span', {class: 'list-meta', text: formatWhen(when)}) : null
      ]),
      position ? h('span', {class: 'list-text is-full', text: '阅读位置：' + position}) : null,
      entries.length ? h('span', {class: 'list-chips'}, entries.map(function (entry) {
        return chip(entry.key + '：' + entry.text, 'is-neutral');
      })) : null
    ])]);
  }

  function readingLegacyCard() {
    const entry = store.data.reading;
    const nodes = [];
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取旧记录…'));
    } else if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到旧记录', entry.message, [
        btn('重试', {onClick: function () { loadResource('reading'); }})
      ]));
    } else {
      const doc = entry.doc || {};
      const items = Array.isArray(doc.items)
        ? doc.items.filter(function (item) { return item && typeof item === 'object'; })
        : null;
      if (!items) {
        nodes.push(statePanel('error', '服务没有返回阅读列表', '响应里没有 items 数组。'));
      } else if (!items.length) {
        nodes.push(statePanel('empty', '还没有真实取得的来源',
          '只有取得过的材料会出现在这里；界面不会假装读过。'));
      } else {
        nodes.push(h('ul', {class: 'list'}, items.map(legacyReadingRow)));
      }
    }
    return h('details', {class: 'tech-details'}, [
      h('summary', {class: 'tech-summary', text: '旧记录：取得与阅读位置（GET /reading）'}),
      h('div', {class: 'tech-body'}, nodes)
    ]);
  }

  function renderReading() {
    const gate = appGate('reading');
    if (gate) return page('阅读', READING_PAGE_SUBTITLE, [gate]);

    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const hint = readingResultHint();
    if (hint) nodes.push(hint);

    nodes.push(h('div', {class: 'row-actions'}, [
      btn('刷新', {icon: 'refresh', onClick: function () { loadReadingMaterial(currentReadingPage()); }})
    ]));

    if (store.conn.phase === 'readonly') {
      nodes.push(statePanel('readonly', '只读模式：能看不能写',
        '服务没有提供 CSRF 令牌，保存停点与批注会被界面挡下。'));
    }

    const entry = store.data.readingMaterial;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取阅读材料…'));
      return page('阅读', READING_PAGE_SUBTITLE, nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到阅读材料', entry.message, [
        btn('重试', {onClick: function () { loadReadingMaterial(currentReadingPage()); }})
      ]));
      return page('阅读', READING_PAGE_SUBTITLE, nodes);
    }

    const view = readingMaterialView(entry.doc);
    if (view.phase !== 'ready') {
      nodes.push(statePanel('error', '服务没有按契约返回 data',
        'GET ' + READING_MATERIAL_PATH + ' 的正文、页码与版本都在 data 里；界面不会去读顶层的同名字段。', [
          btn('重试', {onClick: function () { loadReadingMaterial(currentReadingPage()); }})
        ]));
      return page('阅读', READING_PAGE_SUBTITLE, nodes);
    }
    if (view.statusLabel) nodes.push(h('p', {class: 'reading-warn', text: '服务状态：' + view.statusLabel}));

    const anchorPanel = readingAnchorPanel(view);
    if (anchorPanel) nodes.push(anchorPanel);

    if (!view.available) {
      nodes.push(statePanel('unavailable', '这份材料当前不可用',
        reasonText(view.reasonCode) || '服务没有说明原因；界面不会补一份演示正文。', [
          btn('重试', {onClick: function () { loadReadingMaterial(currentReadingPage()); }})
        ]));
    } else {
      nodes.push(readingMaterialCard(view));
      nodes.push(readingTextCard(view));
      nodes.push(readingPager(view));
      nodes.push(readingAnnotationCard(view));
    }
    nodes.push(readingImportCard(view));
    nodes.push(readingLegacyCard());
    nodes.push(h('p', {class: 'footnote',
      text: '理解状态只按服务返回（这里固定 UNKNOWN）显示；界面不声称在桌边、灯亮或翻过一页就等于读懂。'}));
    return page('阅读', READING_PAGE_SUBTITLE, nodes);
  }

  async function saveReadingPosition() {
    const view = readingView();
    if (!view || view.phase !== 'ready' || !view.available) {
      store.result = {scope: 'reading-position', status: 'blocked',
        message: '材料还没读到，停点没有保存。'};
      renderAll();
      return;
    }
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: 'reading-position', status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = readingScopeFor('reading-position', view.sourceRevision, view.page);
    if (!scope) {
      store.result = {scope: 'reading-position', status: 'blocked', message: '这次没有可用的页码或版本，停点没有保存。'};
      renderAll();
      return;
    }
    const entry = ops.ensure(scope, {path: READING_POSITIONS_PATH});
    const built = buildPositionPayload({
      page: view.page,
      source_revision: view.sourceRevision,
      expected_head: view.privateHead,
      operation_key: entry.key
    });
    if (!built.ok) {
      store.result = {scope: scope, status: 'blocked', message: built.message};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在保存第 ' + view.page + ' 页的停点…', key: entry.key};
    renderAll();
    const res = await request('POST', READING_POSITIONS_PATH, {csrf: store.conn.csrf, body: built.payload});
    finishMutation(scope, deriveMutationOutcome(res), '保存阅读停点', [
      '这次记的是第 ' + built.payload.page + ' 页；翻页本身没有任何写入。',
      '理解状态仍然是 UNKNOWN：保存停点不等于读懂。'
    ]);
  }

  async function submitReadingAnnotation(kind) {
    const view = readingView();
    const scopeKind = kind === 'unclear' ? 'reading-unclear' : 'reading-note';
    if (!view || view.phase !== 'ready' || !view.available) {
      store.result = {scope: scopeKind, status: 'blocked', message: '材料还没读到，这条批注没有发出。'};
      renderAll();
      return;
    }
    const draft = store.readingDraft;
    const checked = validateAnnotationDraft({
      kind: kind,
      title: draft.title,
      body: draft.body,
      note: draft.note
    });
    if (!checked.ok) {
      draft.errors = checked.errors;
      store.result = {scope: scopeKind, status: 'blocked', message: '这条批注还不完整。', lines: checked.errors};
      renderAll();
      return;
    }
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: scopeKind, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = readingScopeFor(scopeKind, view.sourceRevision, view.page);
    const entry = ops.ensure(scope, {path: READING_ANNOTATIONS_PATH});
    const built = buildAnnotationPayload({
      kind: kind,
      page: view.page,
      source_revision: view.sourceRevision,
      expected_head: view.privateHead,
      operation_key: entry.key,
      title: checked.value.title,
      body: checked.value.body,
      note: checked.value.note
    });
    if (!built.ok) {
      store.result = {scope: scope, status: 'blocked', message: built.message, lines: built.errors || null};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在保存这条批注…', key: entry.key};
    renderAll();
    const res = await request('POST', READING_ANNOTATIONS_PATH, {csrf: store.conn.csrf, body: built.payload});
    finishMutation(scope, deriveMutationOutcome(res), '批注', [
      '批注只进私人读取面：它不等于读懂，也不改通用 metadata。'
    ]);
    const status = store.result && store.result.status;
    if (status === 'applied' || status === 'already_applied') {
      if (kind === 'unclear') {
        draft.note = '';
      } else {
        draft.title = '';
        draft.body = '';
      }
      draft.errors = [];
      renderAll();
    }
  }

  function confirmReadingImport() {
    const view = readingView();
    if (!view || view.phase !== 'ready' || !view.available || !view.sourceRevision) {
      store.result = {scope: 'reading-import', status: 'blocked',
        message: '没有可用的材料版本，这次没有导入。'};
      renderAll();
      return;
    }
    const checked = validateReadingContent(store.readingDraft.importText);
    if (!checked.ok) {
      store.readingDraft.errors = checked.errors;
      store.result = {scope: 'reading-import', status: 'blocked',
        message: '正文还不符合导入条件。', lines: checked.errors};
      renderAll();
      return;
    }
    openSheet({
      title: '替换当前阅读材料？',
      lines: [
        '会写入 ' + checked.stats.pageCount + ' 页、' + checked.stats.bytes + ' 字节的正文。',
        '当前材料版本（' + safeLabel(view.sourceRevision, 40) + '）会作为旧版本留在 Supply。',
        '旧停点仍然保留，但会标记 needs_reread（需要重看）。'
      ],
      note: '这是替换，不是追加；标题与来源回执由服务决定，界面不声明。',
      confirmLabel: '替换',
      onConfirm: submitReadingImport
    });
  }

  async function submitReadingImport() {
    const view = readingView();
    if (!view || view.phase !== 'ready' || !view.available || !view.sourceRevision) {
      store.result = {scope: 'reading-import', status: 'blocked',
        message: '材料已经变了，这次没有导入；先重新读取。'};
      renderAll();
      return;
    }
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: 'reading-import', status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = readingScopeFor('reading-import', view.sourceRevision, 0);
    const entry = ops.ensure(scope, {path: READING_MATERIAL_PATH});
    const built = buildImportPayload({
      content: store.readingDraft.importText,
      expected_revision: view.sourceRevision,
      operation_key: entry.key
    });
    if (!built.ok) {
      store.result = {scope: scope, status: 'blocked', message: built.message, lines: built.errors || null};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在把这段正文交给服务…', key: entry.key};
    renderAll();
    const res = await request('POST', READING_MATERIAL_PATH, {csrf: store.conn.csrf, body: built.payload});
    finishMutation(scope, deriveMutationOutcome(res), '导入正文', [
      '发出去的是正文本身；标题、版本与理解状态只能由服务回答。'
    ]);
    const status = store.result && store.result.status;
    if (status === 'applied' || status === 'already_applied') {
      store.readingDraft.importText = '';
      store.readingDraft.errors = [];
      renderAll();
    }
  }

  /* ---------------- 钱包 ---------------- */

  function renderWallet() {
    const gate = appGate('wallet');
    if (gate) return page('钱包', '虚拟预算与流水', [gate]);

    const nodes = [];
    const entry = store.data.wallet;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取钱包…'));
      return page('钱包', '虚拟预算与流水', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到钱包', entry.message, [
        btn('重试', {onClick: function () { loadResource('wallet'); }})
      ]));
      return page('钱包', '虚拟预算与流水', nodes);
    }

    const doc = entry.doc || {};
    const data = doc.data && typeof doc.data === 'object' ? doc.data : null;
    const status = doc.status ? String(doc.status) : '';
    const reason = doc.reason_code ? reasonText(doc.reason_code) : '';

    /* 钱包读取成功时 status 也是 applied：那不是异常，不要拉黄条。 */
    const statusWarning = readStatusWarning(status);
    if (statusWarning) {
      nodes.push(statePanel('warn', '服务报告钱包状态：' + statusWarning, reason));
    }
    if (!data) {
      nodes.push(statePanel('error', '服务没有返回钱包数据', reason));
      return page('钱包', '虚拟预算与流水', nodes);
    }

    const balance = formatMoney(data.currency, data.balance);
    nodes.push(h('div', {class: 'balance-card'}, [
      h('p', {class: 'balance-label', text: '当前余额'}),
      h('p', {class: 'balance-value', text: balance || '币种或余额未提供'}),
      h('p', {class: 'balance-note', text: '虚拟预算，不等于现实金钱；数字只来自服务。'})
    ]));
    if (!balance) {
      nodes.push(statePanel('warn', '余额字段不完整', '需要 currency 与数值型 balance；界面不猜单位、不补 0。'));
    }

    const transactions = Array.isArray(data.transactions) ? data.transactions.filter(function (tx) { return tx && typeof tx === 'object'; }) : null;
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('刷新', {icon: 'refresh', onClick: function () { loadResource('wallet'); }})
    ]));

    if (!transactions) {
      nodes.push(statePanel('empty', '服务没有返回流水', '响应里没有 transactions 数组。'));
    } else if (!transactions.length) {
      nodes.push(statePanel('empty', '还没有流水', '发生真实扣款或入账后才会出现。'));
    } else {
      nodes.push(h('h2', {class: 'card-title', text: '流水'}));
      nodes.push(h('ul', {class: 'list'}, transactions.map(function (tx) {
        return transactionRow(tx, data.currency);
      })));
    }

    nodes.push(h('p', {class: 'footnote', text: '流水金额、币种、时间都是服务字段；缺失的显示“未提供”，界面不推算。'}));
    return page('钱包', '虚拟预算与流水', nodes);
  }

  function transactionRow(tx, fallbackCurrency) {
    const amount = firstNumber(tx, ['amount', 'delta', 'value']);
    const currency = typeof tx.currency === 'string' && tx.currency ? tx.currency : fallbackCurrency;
    const money = formatMoney(currency, amount);
    const memo = firstText(tx, ['description', 'memo', 'note', 'reason_text', 'label', 'kind']);
    const when = firstText(tx, ['created_at', 'at', 'time', 'updated_at', 'timestamp']);
    const id = firstText(tx, ['transaction_id', 'id', 'entry_id']);
    const tone = amount === null ? '' : (amount < 0 ? 'is-out' : 'is-in');
    return h('li', {}, [h('div', {class: 'list-item is-static'}, [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: memo || '未写说明的变动'}),
        h('span', {class: 'amount ' + tone, text: money || '金额未提供'})
      ]),
      h('span', {class: 'list-chips'}, [
        chip(formatWhen(when) || '时间未提供', 'is-neutral'),
        id ? chip(id, 'is-mono') : chip('交易号未提供', 'is-neutral')
      ])
    ])]);
  }

  /* ---------------- 商店 ---------------- */

  function renderShop() {
    const gate = appGate('shop');
    if (gate) return page('商店', '目录、价格与购买', [shopChannelCard('shop'), gate]);

    const nodes = [shopChannelCard('shop')];
    const result = resultCard();
    if (result) nodes.push(result);

    const entry = store.data.shop;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取商店…'));
      return page('商店', '目录、价格与购买', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到商店', entry.message, [
        btn('重试', {onClick: function () { loadResource('shop'); }})
      ]));
      return page('商店', '目录、价格与购买', nodes);
    }

    const view = shopView(entry.doc);
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('刷新', {icon: 'refresh', onClick: function () { loadResource('shop'); }})
    ]));

    if (!view.purchaseAvailable) {
      nodes.push(statePanel('warn', '现在不能购买', view.blockedText || '服务没有把 purchase_available 设为 true。'));
    } else {
      nodes.push(statePanel('ok', '可以购买', '价格与余额由服务决定；确认后才会扣款。'));
    }

    /* 读取成功的 status 是 applied/already_applied，不是"需要注意"；只有真异常才提醒。 */
    const statusWarning = readStatusWarning(view.status);
    if (statusWarning) {
      nodes.push(statePanel('warn', '服务报告商店状态：' + statusWarning, view.blockedText));
    }

    if (view.itemsMissing) {
      nodes.push(statePanel('error', '服务没有返回目录', '响应里没有 catalog 数组。'));
      return page('商店', '目录、价格与购买', nodes);
    }
    if (!view.items.length) {
      nodes.push(statePanel('empty', '目录是空的', '没有可展示的虚拟商品。'));
      return page('商店', '目录、价格与购买', nodes);
    }

    nodes.push(h('ul', {class: 'list'}, view.items.map(function (item) {
      const inner = [
        h('span', {class: 'shop-head'}, [
          h('span', {class: 'list-title', text: item.name}),
          h('span', {class: 'shop-price', text: formatMoney(view.currency, item.price) || '价格未提供'})
        ]),
        item.description ? h('span', {class: 'shop-desc', text: item.description}) : null,
        h('span', {class: 'list-chips'}, [
          item.usableKind ? chip('用途：' + item.usableKind, 'is-neutral') : null,
          view.purchaseAvailable ? chip('可购买', 'is-ok') : chip('当前不可购买', 'is-warn'),
          item.idOk ? null : chip('目录项 id 不可用', 'is-warn')
        ])
      ];
      /* 详情与购买都靠 catalog_item_id 原值定位；没有可用 id 的目录项只能看，不能下单。 */
      if (!item.idOk) return h('li', {}, [h('div', {class: 'list-item shop-card is-static'}, inner)]);
      return h('li', {}, [h('button', {class: 'list-item shop-card', type: 'button', on: {click: function () { openShopItem(item.id); }}}, inner)]);
    })));

    nodes.push(h('p', {class: 'footnote', text: '价格和可购买状态都来自服务；界面不改价、不预扣、不假设成功。'}));
    return page('商店', '目录、价格与购买', nodes);
  }

  function openShopItem(id) {
    go('shop-item', {detail: id});
    if (!store.data.wallet) loadResource('wallet');
  }

  function findShopItem(id) {
    const view = shopView(store.data.shop && store.data.shop.doc);
    for (let i = 0; i < view.items.length; i += 1) {
      if (view.items[i].id === id) return view.items[i];
    }
    return null;
  }

  function walletBalance() {
    const entry = store.data.wallet;
    if (!entry || entry.phase !== 'ready') return null;
    const data = entry.doc && entry.doc.data && typeof entry.doc.data === 'object' ? entry.doc.data : null;
    if (!data) return null;
    return formatMoney(data.currency, data.balance);
  }

  function renderShopItem() {
    const gate = appGate('shop');
    if (gate) return page('商品详情', '', [gate]);

    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);

    const shopEntry = store.data.shop;
    if (!shopEntry || shopEntry.phase !== 'ready') {
      nodes.push(statePanel(shopEntry && shopEntry.phase === 'offline' ? 'offline' : 'loading', '正在读取目录…'));
      return page('商品详情', '', nodes);
    }
    const view = shopView(shopEntry.doc);
    const item = store.shopDetailId ? findShopItem(store.shopDetailId) : null;
    if (!item) {
      nodes.push(statePanel('error', '目录里找不到这件商品', '可能已下架，或链接里的 id 不对。', [
        btn('回到商店', {onClick: function () { goBack(); }})
      ]));
      return page('商品详情', '', nodes);
    }

    const priceText = formatMoney(view.currency, item.price);
    const balanceText = walletBalance();
    const guard = canWrite(store.conn);
    const blocked = [];
    if (!view.purchaseAvailable) blocked.push(view.blockedText || '服务没有把 purchase_available 设为 true。');
    if (!priceText) blocked.push('服务没有给出价格。');
    if (!item.idOk) blocked.push('目录项 id 不可用：' + item.idMessage);
    if (!guard.ok) blocked.push(guard.message);

    nodes.push(h('div', {class: 'detail-hero'}, [
      h('h2', {class: 'detail-name', text: item.name}),
      h('p', {class: 'detail-price', text: priceText || '价格未提供'}),
      item.description ? h('p', {class: 'shop-desc', text: item.description}) : null,
      h('div', {class: 'list-chips'}, [
        view.purchaseAvailable ? chip('可购买', 'is-ok') : chip('当前不可购买', 'is-warn'),
        item.usableKind ? chip('用途：' + item.usableKind, 'is-neutral') : null
      ])
    ]));

    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '这次要付多少'}),
      kv('价格', priceText || '未提供'),
      kv('当前余额', balanceText || '未提供'),
      item.idOk ? kv('目录项', item.idLabel, true) : null
    ]));

    if (blocked.length) {
      nodes.push(statePanel('warn', '现在不能下单', blocked.join('；')));
    }

    nodes.push(h('div', {class: 'row-actions'}, [
      btn('确认购买', {
        variant: 'primary',
        icon: 'bag',
        disabled: blocked.length > 0 || store.busy === 'purchase:' + item.id,
        onClick: function () { confirmPurchase(item); }
      }),
      btn('返回目录', {onClick: function () { goBack(); }})
    ]));

    nodes.push(h('p', {class: 'footnote', text: '扣款与新物件的归属由服务在同一次操作里提交；OWNED 不等于已经送达，更不能提前使用。'}));
    return page('商品详情', '确认后才会真正扣款', nodes);
  }

  function confirmPurchase(item) {
    const view = shopView(store.data.shop && store.data.shop.doc);
    const guard = canWrite(store.conn);
    const problems = [];
    if (!guard.ok) problems.push(guard.message);
    if (!view.purchaseAvailable) problems.push(view.blockedText || '服务没有把 purchase_available 设为 true。');
    if (formatMoney(view.currency, item.price) === null) problems.push('服务没有给出价格。');
    if (!item.idOk) problems.push('目录项 id 不可用：' + item.idMessage);
    if (problems.length) {
      store.result = {scope: 'purchase:' + item.id, status: 'blocked', message: '现在不能购买：' + problems.join('；')};
      renderAll();
      return;
    }
    const balanceText = walletBalance();
    openSheet({
      title: '确认购买',
      lines: [
        item.name,
        '目录项：' + item.idLabel,
        '价格：' + formatMoney(view.currency, item.price),
        '当前余额：' + (balanceText || '未提供')
      ],
      note: '确认后服务会扣虚拟预算，并生成一件属于她的物件。OWNED 不等于已送达。',
      confirmLabel: '确认购买',
      onConfirm: function () { submitPurchase(item); }
    });
  }

  async function submitPurchase(item) {
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: 'purchase:' + item.id, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = 'purchase:' + item.id;
    const entry = ops.ensure(scope, {path: '/purchases'});
    /* catalog_item_id 必须是服务目录里的原字节：先校验，不合法就不发请求，也不换键重发。 */
    const built = buildPurchasePayload({catalog_item_id: item.id, operation_key: entry.key});
    if (!built.ok) {
      ops.resolve(scope);
      store.result = {scope: scope, status: 'blocked', message: built.message};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在提交购买…', key: entry.key};
    renderAll();
    const res = await request('POST', '/purchases', {csrf: store.conn.csrf, body: built.payload});
    finishMutation(scope, deriveMutationOutcome(res), '购买');
  }

  /* ---------------- 实体日用店（店里买；和手机线上店是两条渠道） ---------------- */

  /* 渠道说明卡：线上店和实体店谁也不是谁的别名，价钱、到手方式、要不要到店都不一样。 */
  function shopChannelCard(current) {
    const isStore = current === 'store';
    const actions = [];
    if (isStore) {
      actions.push(btn('手机线上店', {small: true, onClick: function () { go('shop'); }}));
      actions.push(btn('刷新货架', {small: true, icon: 'refresh', onClick: function () { loadResource('store'); }}));
    } else {
      actions.push(btn('去实体日用店', {small: true, onClick: function () { go('store'); }}));
    }
    return h('div', {class: 'channel-card'}, [
      h('p', {class: 'channel-title', text: isStore ? '渠道：实体日用店' : '渠道：手机线上店'}),
      h('p', {class: 'channel-note', text: isStore
        ? '街上那家实体日用店：人要到店里、店在营业、余额够才买得成；家具付完款由店里配送，没到货之前不能摆也不能用。'
        : '手机里的线上店：下单即归属、直接送到家。街上的实体日用店是另一个渠道，价钱和交付方式各自算，界面不混着说。'}),
      h('div', {class: 'row-actions'}, actions)
    ]);
  }

  /* 货架：位置/营业/余额/渠道四条约束都来自服务；缺哪条就说缺哪条。 */
  function renderStore() {
    const nodes = [shopChannelCard('store')];
    const gate = appGate('shop');
    if (gate) {
      nodes.push(gate);
      return page('实体日用店', '到店、营业中、余额够才买', nodes);
    }
    const result = resultCard();
    if (result) nodes.push(result);

    const entry = store.data.store;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取店里的货架…'));
      return page('实体日用店', '到店、营业中、余额够才买', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到店里的货架', entry.message, [
        btn('重试', {onClick: function () { loadResource('store'); }})
      ]));
      return page('实体日用店', '到店、营业中、余额够才买', nodes);
    }

    const view = storeView(entry.doc);
    const chips = [];
    if (view.availabilityLabel) {
      chips.push(chip('营业：' + view.availabilityLabel, view.availability === 'OPEN' ? 'is-ok' : 'is-warn'));
    }
    if (view.opensAt || view.closesAt) {
      chips.push(chip([view.opensAt, view.closesAt].filter(Boolean).join(' – '), 'is-neutral'));
    }
    if (view.balanceText) chips.push(chip('余额：' + view.balanceText, 'is-neutral'));
    if (view.here === true) chips.push(chip('你就在店里', 'is-ok'));
    if (view.here === false) chips.push(chip('你不在店里', 'is-warn'));
    if (view.balanceText) chips.push(chip('余额：' + view.balanceText, 'is-neutral'));
    if (view.here === true) chips.push(chip('你就在店里', 'is-ok'));
    if (view.here === false) chips.push(chip('你不在店里', 'is-warn'));
    if (view.pendingDeliveryCount !== null) chips.push(chip('还没到货 ' + view.pendingDeliveryCount + ' 件', 'is-neutral'));
    if (view.capacityAvailable === false) chips.push(chip('账本已满，买不了', 'is-warn'));

    const notes = [];
    if (view.here === false) notes.push('要买店里的东西，得先真的走到这家店；界面不会替你把位置挪过去。');
    if (view.deliveryText) {
      notes.push('这家店的交付方式：' + view.deliveryText +
        (view.placementRequires ? '；到手后要 ' + view.placementRequires + ' 才算摆好。' : '。'));
    }
    if (view.blockedText) notes.push('服务说：' + view.blockedText);

    nodes.push(h('div', {class: 'channel-card'}, [
      h('p', {class: 'channel-title', text: view.storeName || '实体日用店'}),
      chips.length ? h('div', {class: 'world-chips'}, chips) : null
    ].concat(notes.map(function (line) {
      return h('p', {class: 'channel-note', text: line});
    }))));
    if (view.itemsMissing) {
      nodes.push(statePanel('error', '服务没有返回货架目录', '响应里没有 catalog 数组。'));
      return page('实体日用店', '到店、营业中、余额够才买', nodes);
    }
    if (!view.items.length) {
      nodes.push(statePanel('empty', '货架是空的', '这家店现在没有可买的东西。'));
      return page('实体日用店', '到店、营业中、余额够才买', nodes);
    }

    nodes.push(h('ul', {class: 'list'}, view.items.map(function (item) {
      const inner = [
        h('span', {class: 'shop-head'}, [
          h('span', {class: 'list-title', text: item.name}),
          h('span', {class: 'shop-price', text: formatMoney(view.currency, item.price) || '价格未提供'})
        ]),
        item.description ? h('span', {class: 'shop-desc', text: item.description}) : null,
        h('span', {class: 'list-chips'}, [
          item.usableKindLabel ? chip(item.usableKindLabel, 'is-neutral') : null,
          item.deliveryText ? chip(item.deliveryText, 'is-neutral') : null,
          item.purchasable === false ? chip('这件现在买不了', 'is-warn') : null,
          item.idOk ? null : chip('目录项 id 不可用', 'is-warn')
        ])
      ];
      /* 详情与下单都靠 catalog_item_id 原值定位；没有可用 id 的目录项只能看。 */
      if (!item.idOk) return h('li', {}, [h('div', {class: 'list-item shop-card is-static'}, inner)]);
      return h('li', {}, [h('button', {
        class: 'list-item shop-card', type: 'button',
        on: {click: function () { openStoreItem(item.id); }}
      }, inner)]);
    })));
    nodes.push(h('p', {class: 'footnote', text: '价格、营业状态和能不能买都来自服务；界面不改价、不预扣，也不把“付款成功”说成“已经到货”。'}));
    return page('实体日用店', '到店、营业中、余额够才买', nodes);
  }

  function openStoreItem(id) {
    go('store-item', {detail: id});
    if (!store.data.wallet) loadResource('wallet');
  }

  function findStoreItem(id) {
    const view = storeView(store.data.store && store.data.store.doc);
    for (let i = 0; i < view.items.length; i += 1) {
      if (view.items[i].id === id) return view.items[i];
    }
    return null;
  }

  /* 一件店里商品现在能不能下单：位置、营业、这件能不能买、余额、id、只读状态，全过才给按钮。 */
  function storeItemBlocked(view, item) {
    const blocked = [];
    if (!view.purchaseAvailable) blocked.push(view.blockedText || '服务没有把 purchase_available 设为 true。');
    if (view.here === false) blocked.push('你不在店里。');
    if (item.purchasable === false) blocked.push(item.blockedText || '服务说这件现在买不了。');
    if (item.affordable === false) blocked.push('余额不够这一件（服务的 affordable=false）。');
    if (formatMoney(view.currency, item.price) === null) blocked.push('服务没有给出价格。');
    if (!item.idOk) blocked.push('目录项 id 不可用：' + item.idMessage);
    const guard = canWrite(store.conn);
    if (!guard.ok) blocked.push(guard.message);
    return blocked;
  }

  function renderStoreItem() {
    const nodes = [shopChannelCard('store')];
    const gate = appGate('shop');
    if (gate) {
      nodes.push(gate);
      return page('店里的商品', '', nodes);
    }
    const result = resultCard();
    if (result) nodes.push(result);

    const entry = store.data.store;
    if (!entry || entry.phase !== 'ready') {
      nodes.push(statePanel(entry && entry.phase === 'offline' ? 'offline' : 'loading', '正在读取货架…'));
      return page('店里的商品', '', nodes);
    }
    const view = storeView(entry.doc);
    const item = store.storeDetailId ? findStoreItem(store.storeDetailId) : null;
    if (!item) {
      nodes.push(statePanel('error', '货架上找不到这件商品', '可能已下架，或者链接里的 id 不对。', [
        btn('回到货架', {onClick: function () { goBack(); }})
      ]));
      return page('店里的商品', '', nodes);
    }

    const priceText = formatMoney(view.currency, item.price);
    const balanceText = walletBalance() || view.balanceText;
    const blocked = storeItemBlocked(view, item);

    nodes.push(h('div', {class: 'detail-hero'}, [
      h('h2', {class: 'detail-name', text: item.name}),
      h('p', {class: 'detail-price', text: priceText || '价格未提供'}),
      item.description ? h('p', {class: 'shop-desc', text: item.description}) : null,
      h('div', {class: 'list-chips'}, [
        chip('实体日用店', 'is-neutral'),
        view.availabilityLabel ? chip('营业：' + view.availabilityLabel, view.availability === 'OPEN' ? 'is-ok' : 'is-warn') : null,
        item.usableKindLabel ? chip(item.usableKindLabel, 'is-neutral') : null,
        item.deliveryText ? chip(item.deliveryText, 'is-neutral') : null
      ])
    ]));

    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '这次要付多少'}),
      kv('价格', priceText || '未提供'),
      kv('当前余额', balanceText || '未提供'),
      item.deliveryText ? kv('交付方式', item.deliveryText) : null,
      item.idOk ? kv('目录项', item.idLabel, true) : null
    ]));

    if (blocked.length) nodes.push(statePanel('warn', '现在不能下单', blocked.join('；')));

    nodes.push(h('div', {class: 'row-actions'}, [
      btn('确认购买', {
        variant: 'primary',
        icon: 'bag',
        disabled: blocked.length > 0 || store.busy === 'store-purchase:' + item.id,
        onClick: function () { confirmStorePurchase(item); }
      }),
      btn('返回货架', {onClick: function () { goBack(); }})
    ]));
    nodes.push(h('p', {class: 'footnote', text: '扣款与产权由服务在同一次操作里提交；付完款不等于已经到货，没到货的家具不能摆、也不能用。'}));
    return page('店里的商品', '确认后才会真的扣款', nodes);
  }

  function confirmStorePurchase(item) {
    const view = storeView(store.data.store && store.data.store.doc);
    const blocked = storeItemBlocked(view, item);
    if (blocked.length) {
      store.result = {scope: 'store-purchase:' + item.id, status: 'blocked', message: '现在不能购买：' + blocked.join('；')};
      renderAll();
      return;
    }
    openSheet({
      title: '确认在实体店购买',
      lines: [
        item.name,
        '渠道：实体日用店' + (view.storeName ? ' · ' + view.storeName : ''),
        '目录项：' + item.idLabel,
        '价格：' + formatMoney(view.currency, item.price),
        '当前余额：' + (walletBalance() || view.balanceText || '未提供'),
        item.deliveryText ? '交付：' + item.deliveryText : '交付：服务没有说明'
      ],
      note: '确认后服务会从同一个钱包扣款并记产权。付款成功不等于到货；到货之前不能布置，也不能使用。',
      confirmLabel: '确认购买',
      onConfirm: function () { submitStorePurchase(item); }
    });
  }

  async function submitStorePurchase(item) {
    const guard = canWrite(store.conn);
    const scope = 'store-purchase:' + item.id;
    if (!guard.ok) {
      store.result = {scope: scope, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const entry = ops.ensure(scope, {path: '/store/purchases'});
    /* catalog_item_id 必须是服务货架上的原字节；不合法就不发，也不换键重发。 */
    const built = buildPurchasePayload({catalog_item_id: item.id, operation_key: entry.key});
    if (!built.ok) {
      ops.resolve(scope);
      store.result = {scope: scope, status: 'blocked', message: built.message};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在提交店里的购买…', key: entry.key};
    renderAll();
    const res = await request('POST', '/store/purchases', {csrf: store.conn.csrf, body: built.payload});
    finishMutation(scope, deriveMutationOutcome(res), '店里购买', deliveryOutcomeLines(deriveMutationOutcome(res).doc));
  }

  /* 购买回执 → 人话：只读 owner 真的报回来的交付字段，没报就说没报。 */
  function deliveryOutcomeLines(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!data) return [];
    const lines = [];
    /* C 的 store_purchase 回执：delivery_status / expected_delivery_at / materialization /
       current_balance。付完款只是 OWNED，不是到货；界面照 owner 的字面话说。 */
    const rawState = asString(data.delivery_status || (asRecord(data.delivery) || {}).status).toLowerCase();
    const stateLabel = DELIVERY_STATE_TEXT[rawState] || (rawState ? safeLabel(rawState, 30) : '');
    if (stateLabel) {
      lines.push('交付状态：' + stateLabel +
        (rawState === 'pending' || rawState === 'in_transit' ? '（还没到货，不能布置也不能用）' : ''));
    }
    const expectedAt = asString(data.expected_delivery_at);
    if (expectedAt) lines.push('世界给的到货时间：' + (formatWhen(expectedAt) || '时间读不懂'));
    const remaining = asSeconds((asRecord(data.delivery) || {}).remaining_seconds);
    if (remaining !== null) lines.push('服务说还要等 ' + formatWalkText(remaining) + '。');
    if (asString(data.materialization) === 'pending_arrange') {
      lines.push('materialization=pending_arrange：这件东西已经归属，但还没摆到房间里；要用 ARRANGE 才算摆好。');
    }
    return lines;
  }

  /* ---------------- 物品 ---------------- */

  function renderInventory() {
    const gate = appGate('inventory');
    if (gate) return page('物品', '归属、位置与使用', [gate]);

    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);

    const entry = store.data.inventory;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读取物品…'));
      return page('物品', '归属、位置与使用', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到物品', entry.message, [
        btn('重试', {onClick: function () { loadResource('inventory'); }})
      ]));
      return page('物品', '归属、位置与使用', nodes);
    }

    const view = inventoryView(entry.doc);
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('刷新', {icon: 'refresh', onClick: function () { loadResource('inventory'); }})
    ]));

    /* 物品读取成功时 status 同样是 applied：那不是异常，不要拉黄条。 */
    const statusWarning = readStatusWarning(view.status);
    if (statusWarning) {
      nodes.push(statePanel('warn', '服务报告物品状态：' + statusWarning, reasonText(view.reasonCode)));
    }
    if (view.itemsMissing) {
      nodes.push(statePanel('error', '服务没有返回物品列表', '响应里没有 items 数组。'));
      return page('物品', '归属、位置与使用', nodes);
    }
    if (!view.items.length) {
      nodes.push(statePanel('empty', '还没有物品', '买到的虚拟物件会出现在这里；拥有不等于已送达。'));
      return page('物品', '归属、位置与使用', nodes);
    }

    nodes.push(h('ul', {class: 'list'}, view.items.map(function (item) {
      return h('li', {}, [h('div', {class: 'list-item is-static'}, [
        h('span', {class: 'list-top'}, [
          h('span', {class: 'list-title', text: item.name}),
          chip(item.usable ? '可以用' : '现在不能用', item.usable ? 'is-ok' : 'is-warn')
        ]),
        h('span', {class: 'list-chips'}, [
          item.ownership ? chip('归属：' + item.ownership, 'is-neutral') : null,
          item.location ? chip('位置：' + item.location, 'is-neutral') : null,
          item.delivery ? chip('配送：' + (item.deliveryLabel || item.delivery), 'is-neutral') : null
        ]),
        item.catalogIdOk ? h('span', {class: 'list-meta', text: '目录项 ' + item.catalogLabel}) : null,
        item.idOk ? h('span', {class: 'list-meta', text: '物件 ' + item.idLabel}) : null,
        item.idOk ? null : h('span', {class: 'list-text is-full', text: '物件标识不可用：' + item.idMessage + '；这一件不能提交使用。'}),
        item.blocked ? h('span', {class: 'list-text is-full', text: '不能用的原因：' + item.blocked}) : null,
        h('span', {class: 'list-chips'}, [
          btn('使用', {
            small: true,
            variant: 'mint',
            disabled: !item.usable || !item.idOk || store.busy === 'use:' + item.id,
            onClick: function () { confirmUse(item); }
          })
        ])
      ])]);
    })));

    nodes.push(h('p', {class: 'footnote', text: '能不能用由服务按所有者与当前位置判断；界面没有直接改状态的入口。'}));
    return page('物品', '归属、位置与使用', nodes);
  }

  function confirmUse(item) {
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: 'use:' + item.id, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    if (!item.usable) {
      store.result = {scope: 'use:' + item.id, status: 'blocked', message: '这件东西现在不能用。', reason: item.blocked};
      renderAll();
      return;
    }
    if (!item.idOk) {
      store.result = {scope: 'use:' + item.id, status: 'blocked', message: '物件标识不可用：' + item.idMessage + '；这一件不能提交使用。'};
      renderAll();
      return;
    }
    openSheet({
      title: '使用这件物品',
      lines: [
        item.name,
        '物件：' + item.idLabel,
        '配送：' + (item.deliveryLabel || '服务未提供'),
        '位置：' + (item.location || '服务未提供')
      ],
      note: '确认后只提交一次；结果由后端按所有者当前条件判定，界面不替你写结果。',
      confirmLabel: '确认使用',
      onConfirm: function () { submitUse(item); }
    });
  }

  async function submitUse(item) {
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: 'use:' + item.id, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = 'use:' + item.id;
    const entry = ops.ensure(scope, {path: '/items/use'});
    /* item_instance_id 必须是服务库存里的原字节：先校验，不合法就不发请求。 */
    const built = buildUsePayload({item_instance_id: item.id, operation_key: entry.key});
    if (!built.ok) {
      ops.resolve(scope);
      store.result = {scope: scope, status: 'blocked', message: built.message};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在提交使用…', key: entry.key};
    renderAll();
    const res = await request('POST', '/items/use', {csrf: store.conn.csrf, body: built.payload});
    finishMutation(scope, deriveMutationOutcome(res), '使用物品');
  }

  /* ---------------- 世界：街区 / 房间 / 在路上 ---------------- */

  /* 世界入口只看 /state 有没有把它禁用；没报告也不拦，靠 /world 自己回答。 */
  function worldGate() {
    const phase = store.conn.phase;
    if (phase === 'loading') return statePanel('loading', '正在读取 /api/phone/state…');
    if (phase === 'offline') {
      return statePanel('offline', '未连接：读不到这部手机的状态', store.conn.detail, [
        btn('重新读取状态', {onClick: loadState})
      ]);
    }
    if (phase === 'error') return statePanel('error', '服务状态不可用', store.conn.detail, [btn('重试', {onClick: loadState})]);
    const app = appState(WORLD_APP);
    if (app.status === 'disabled') {
      return statePanel('disabled', '世界在这个实例里被禁用。', app.reasonCode ? reasonText(app.reasonCode) : '');
    }
    return null;
  }

  function worldClockText(localTime) {
    const text = typeof localTime === 'string' ? localTime : '';
    const spot = text.indexOf('T');
    if (spot < 0) return '';
    return text.slice(spot + 1, spot + 6);
  }

  function liveWorldView() {
    const entry = store.data.world;
    if (!entry || entry.phase !== 'ready' || !entry.doc) return null;
    return worldView(entry.doc);
  }

  /* 世界快照该不该（重新）读：除了「正在读」与「读到了」，一律要读（没有条目、error、offline、
     没有 phase 的条目都算）。这条判断是**身体屏能用**的关键：只判断 `!entry` 的话，一次失败之后
     缓存里留着 error 条目，从主屏点身体就再也不会去读世界，屏上永远只剩 /state 投影、
     没有坐下/休息按钮（父级 2026-11-15 实测缺陷）。 */
  function worldSnapshotNeedsLoad(entry) {
    if (!entry) return true;
    const phase = typeof entry.phase === 'string' ? entry.phase : '';
    /* 只有「正在读」与「读到了」不再重发；其余（含 error/offline/没有 phase）都算要读。 */
    return phase !== 'loading' && phase !== 'ready';
  }

  function worldNodeLabel(view, node) {
    if (!node) return '';
    const nodes = view && view.destinations ? view.destinations.nodes : [];
    for (let i = 0; i < nodes.length; i += 1) {
      if (nodes[i].node === node) return nodes[i].label;
    }
    const places = view && view.destinations ? view.destinations.places : [];
    for (let i = 0; i < places.length; i += 1) {
      if (places[i].locationId === node) return places[i].name;
    }
    return safeLabel(node, 40);
  }

  /* 一句生活感描述：只用 owner 给的位置 / 姿势 / 恢复 / 信号，不编天气和心情。 */
  function worldLifeLine(view) {
    const place = view.location.status === 'available' ? view.location.name : '位置暂时读不到';
    const room = view.indoor.available && view.indoor.areaName ? place + '·' + view.indoor.areaName : place;
    const bits = [];
    if (view.travel.active) {
      const destination = worldNodeLabel(view, view.travel.destination) || '目的地';
      bits.push('正走在去' + destination + '的路上' +
        (view.travel.remainingSeconds !== null ? '，还有 ' + formatWalkText(view.travel.remainingSeconds) : ''));
    } else if (view.body.poseLabel) {
      bits.push('在' + room + '，' + view.body.poseLabel);
    } else {
      bits.push('在' + room);
    }
    if (!view.travel.active) {
      if (view.body.recovery.active) bits.push('正在' + (view.body.recovery.modeLabel || '休息'));
      else if (view.body.recovery.state === 'completed') bits.push('刚结束一次休息');
      else if (view.body.recovery.state === 'interrupted') bits.push('刚才的休息被打断了');
    }
    const signalLine = worldSignalLines(view.body)[0];
    if (signalLine && view.body.known && view.body.signals.length) bits.push(signalLine);
    return bits.join('，') + '。';
  }

  function worldHero(view) {
    const header = view.header;
    const clock = worldClockText(header.localTime);
    const chips = [];
    if (view.travel.active) chips.push(chip('在路上', 'is-warn'));
    if (view.body.recovery.active) chips.push(chip('正在休息', 'is-ok'));
    if (view.location.home === true) chips.push(chip('在家', 'is-neutral'));
    if (view.location.kind) chips.push(chip(view.location.kind, 'is-neutral'));
    return h('div', {class: 'world-hero'}, [
      h('p', {class: 'world-hero-kicker', text: [header.localDate, header.weekdayLabel, clock].filter(Boolean).join(' ') || '世界时间未提供'}),
      h('h2', {class: 'world-hero-title', text: view.location.status === 'available' ? view.location.name : '位置暂时读不到'}),
      h('p', {class: 'world-hero-line', text: worldLifeLine(view)}),
      chips.length ? h('div', {class: 'world-chips'}, chips) : null,
      view.location.status !== 'available'
        ? h('p', {class: 'world-hero-warn', text: '位置读不到：' + (worldReasonText(view.location.reason) || '服务没有说明')})
        : null
    ]);
  }

  function worldTravelCard(view) {
    const travel = view.travel;
    if (travel.status === 'missing') return null;
    if (travel.active) {
      const origin = worldNodeLabel(view, travel.origin) || '起点';
      const destination = worldNodeLabel(view, travel.destination) || '目的地';
      return h('div', {class: 'world-travel', role: 'status'}, [
        h('p', {class: 'world-travel-title', text: '在路上：' + origin + ' → ' + destination}),
        h('p', {class: 'world-travel-text', text: (travel.bandLabel || '在路上') +
          (travel.remainingSeconds !== null ? ' · 还有 ' + formatWalkText(travel.remainingSeconds) : ' · 服务没有给剩余时间')}),
        h('div', {class: 'world-chips'}, [
          travel.bodyLoadText ? chip(travel.bodyLoadText, travel.bodyLoad === 'pending' ? 'is-warn' : 'is-neutral') : null,
          travel.locationUnchanged === true ? chip('还没到，位置仍是起点', 'is-warn') : null
        ]),
        h('p', {class: 'world-travel-note', text: '到达由世界自己的 tick 结算；这一屏不会提前把你挪过去。'})
      ]);
    }
    if (travel.status === 'disabled') {
      return h('p', {class: 'footnote', text: '计时旅程关着：出门会直接到，界面只认服务返回的位置。'});
    }
    if (travel.status === 'unavailable') {
      return h('p', {class: 'footnote', text: '在途状态读不到：' + (worldReasonText(travel.reason) || '服务没有说明')});
    }
    return null;
  }
  function svgNode(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, String(attrs[key])); });
    return node;
  }

  /* 地图是自己按安全结构画的：owner 只给节点与秒数，这里只写 textContent 和安全属性。 */
  function worldMapPanel(view) {
    const plan = worldMapPlan(view, 8);
    if (!plan.rows.length) {
      return h('div', {class: 'world-map'}, [
        h('p', {class: 'world-panel-note', text: '服务这次没有给可达的街区节点（可能不在节点上，或计时旅程关着）。'})
      ]);
    }
    const summary = plan.rows.map(function (row) {
      return row.label + (row.walkText ? ' ' + row.walkText : '') + (row.direct ? '' : '（要分几步）');
    }).join('；');
    const svg = svgNode('svg', {
      viewBox: '0 0 ' + plan.width + ' ' + plan.height,
      class: 'world-map-svg',
      role: 'img',
      'aria-label': '街区示意图：从这里可以走到 ' + summary
    });
    const last = plan.rows[plan.rows.length - 1];
    svg.append(svgNode('path', {
      d: 'M' + plan.origin.x + ' ' + plan.origin.y + ' L' + plan.origin.x + ' ' + (last.y + 6),
      class: 'map-spine'
    }));
    plan.rows.forEach(function (row) {
      svg.append(svgNode('line', {
        x1: plan.origin.x, y1: row.y + 4, x2: row.x, y2: row.y + 4, class: 'map-stub'
      }));
      svg.append(svgNode('circle', {
        cx: row.x, cy: row.y + 4, r: 7,
        class: 'map-dot' + (row.direct ? '' : ' is-far')
      }));
      const label = svgNode('text', {x: row.x + 16, y: row.y + 8, class: 'map-label'});
      label.textContent = row.shortLabel || row.label;
      svg.append(label);
      if (row.walkText) {
        const away = svgNode('text', {x: plan.width - 8, y: row.y + 8, class: 'map-walk', 'text-anchor': 'end'});
        away.textContent = row.walkText + (row.direct ? '' : ' · 分几步');
        svg.append(away);
      }
    });
    svg.append(svgNode('circle', {cx: plan.origin.x, cy: plan.origin.y, r: 9, class: 'map-here'}));
    const hereLabel = svgNode('text', {x: plan.origin.x + 18, y: plan.origin.y + 4, class: 'map-here-label'});
    hereLabel.textContent = plan.origin.label;
    svg.append(hereLabel);
    return h('div', {class: 'world-map'}, [
      h('h2', {class: 'world-panel-title', text: '街区'}),
      svg,
      h('p', {class: 'world-panel-note', text: '这张图只按服务给的可达节点和步行时长画；虚线圆点要分几步才走得到。'})
    ]);
  }

  function worldMoveRow(view, node) {
    const reason = worldMoveDisabledReason(view, node);
    return h('li', {}, [h('div', {class: 'list-item is-static'}, [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: node.label}),
        h('span', {class: 'list-meta', text: node.walkText || '时长未提供'})
      ]),
      h('span', {class: 'list-chips'}, [
        node.kind ? chip(node.kind, 'is-neutral') : null,
        node.direct === false ? chip('要分几步', 'is-warn') : null,
        node.reach && node.reach !== 'reachable' ? chip(node.reachLabel || '不可达', 'is-warn') : null
      ]),
      h('span', {class: 'list-chips'}, [
        btn('出发', {
          small: true,
          variant: 'mint',
          disabled: !!reason || store.busy !== null,
          title: reason || ('出发去' + node.label),
          onClick: function () { confirmMove(view, node); }
        })
      ]),
      reason ? h('span', {class: 'list-text is-full', text: reason}) : null
    ])]);
  }

  function worldMoveNodeForPlace(view, place) {
    const nodes = view.destinations.nodes;
    let fallback = null;
    for (let i = 0; i < nodes.length; i += 1) {
      if (nodes[i].placeId !== place.locationId) continue;
      if (nodes[i].areaId && nodes[i].areaId === nodes[i].placeId) return nodes[i];
      if (!fallback) fallback = nodes[i];
    }
    for (let i = 0; i < nodes.length; i += 1) {
      if (nodes[i].node === place.locationId) return nodes[i];
    }
    return fallback;
  }

  function worldPlaceRow(view, place) {
    const node = worldMoveNodeForPlace(view, place);
    const reason = node ? worldMoveDisabledReason(view, node) : '服务没有把这里列成能一步走到的地方';
    const chips = [];
    if (place.kind) chips.push(chip(place.kind, 'is-neutral'));
    if (place.availabilityLabel) chips.push(chip(place.availabilityLabel, place.availability === 'OPEN' ? 'is-ok' : 'is-neutral'));
    if (place.here) chips.push(chip('就在这里', 'is-ok'));
    if (place.walkText) chips.push(chip(place.walkText, 'is-neutral'));
    return h('li', {}, [h('div', {class: 'list-item is-static'}, [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: place.name}),
        h('span', {class: 'list-meta', text: place.reachLabel || ''})
      ]),
      h('span', {class: 'list-chips'}, chips),
      place.indoorAreas.length
        ? h('span', {class: 'list-text is-full', text: '室内：' + place.indoorAreas.map(function (area) {
          return area.name || area.id;
        }).join('、')})
        : null,
      place.affordances.length
        ? h('span', {class: 'list-text is-full', text: '能做的事：' + place.affordances.map(function (item) {
          return item.description || item.id;
        }).filter(Boolean).join('；')})
        : null,
      place.here
        ? null
        : h('span', {class: 'list-chips'}, [
          btn('出发', {
            small: true,
            variant: 'mint',
            disabled: !!reason || store.busy !== null,
            title: reason || ('出发去' + place.name),
            onClick: function () { if (node) confirmMove(view, node); }
          })
        ]),
      !place.here && reason ? h('span', {class: 'list-text is-full', text: reason}) : null
    ])]);
  }
  function worldIndoorPanel(view) {
    const indoor = view.indoor;
    if (indoor.status === 'missing') return null;
    if (!indoor.available) {
      return h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '室内'}),
        h('p', {class: 'world-panel-note', text: indoor.reason
          ? worldReasonText(indoor.reason)
          : '服务这次没有给室内地图。'})
      ]);
    }
    const exits = indoor.adjacent.map(function (exit) {
      const nodes = view.destinations.nodes;
      let matched = null;
      for (let i = 0; i < nodes.length; i += 1) {
        if (nodes[i].node === exit.node) {
          matched = nodes[i];
          break;
        }
      }
      if (!matched) return chip(exit.name || exit.node, 'is-neutral');
      const reason = worldMoveDisabledReason(view, matched);
      return btn(exit.name || exit.node, {
        small: true,
        disabled: !!reason || store.busy !== null,
        title: reason || ('去' + (exit.name || exit.node)),
        onClick: function () { confirmMove(view, matched); }
      });
    });
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: (indoor.buildingName || '室内') + (indoor.areaName ? ' · ' + indoor.areaName : '')}),
      indoor.areaKind ? h('p', {class: 'world-panel-note', text: '这里是' + indoor.areaName + '（' + indoor.areaKind + '）。'}) : null,
      indoor.furniture.length
        ? h('div', {class: 'world-chips'}, indoor.furniture.map(function (item) {
          return chip(item.name, 'is-neutral');
        }))
        : h('p', {class: 'world-panel-note', text: '这个世界包没有在这个区域摆任何家具。'}),
      exits.length ? h('div', {class: 'row-actions'}, [h('span', {class: 'world-panel-note', text: '相邻：'})].concat(exits)) : null,
      indoor.note ? h('p', {class: 'world-panel-note', text: indoor.note}) : null
    ]);
  }

  /* ---------------- rc1：房间（灯 / 配送 / 自有家具 / 台面） ---------------- */

  /* 房间光：只搬 owner 报的档位、开关、来源与时长。
     unknown 就说未知，不写成“黑的”，也不给假的 0。 */
  function worldRoomLightPanel(view) {
    const light = view.room.light;
    if (light.status === 'missing') return null;
    const rows = [];
    const chips = [];
    if (light.available) {
      chips.push(chip('房间光：' + (light.bandLabel || '服务没有给档位'), 'is-neutral'));
      if (light.power) chips.push(chip('灯：' + light.powerLabel, light.power === 'on' ? 'is-ok' : 'is-neutral'));
      if (light.since) chips.push(chip('从 ' + (formatWhen(light.since) || '时间读不懂') + ' 起', 'is-neutral'));
      if (light.onText) chips.push(chip('已经开了 ' + light.onText, 'is-neutral'));
    } else if (light.status === 'disabled') {
      rows.push(h('p', {class: 'world-panel-note', text: '房间光这个功能在当前实例里关着' +
        (light.reason ? '：' + worldReasonText(light.reason) : '。')}));
    } else {
      rows.push(h('p', {class: 'world-panel-note', text: '房间光未知' +
        (light.reason ? '：' + worldReasonText(light.reason) : '（服务没有说明）') +
        '。未知不等于“黑的”，界面不会替它画一个亮度。'}));
    }
    if (light.declaredSource) rows.push(h('p', {class: 'world-panel-note', text: '光的来源：' + light.declaredSource}));
    const fixtureRows = roomFixtureRows(view);
    const fixtureNodes = fixtureRows.map(function (fixture) {
      const option = worldFixtureOption(view, fixture, fixture.power);
      const onDisabled = !option.ok || fixture.power === 'on';
      const offDisabled = !option.ok || fixture.power === 'off';
      const stateChip = fixture.powerKnown
        ? chip('这盏：' + (fixture.power === 'on' ? '开着' : '关着'), fixture.power === 'on' ? 'is-ok' : 'is-neutral')
        : chip('这盏的开关状态未知', 'is-warn');
      return h('div', {class: 'room-fixture'}, [
        h('span', {class: 'room-fixture-name', text: fixture.idLabel}),
        stateChip,
        h('div', {class: 'row-actions'}, [
          btn('开灯', {
            small: true, variant: 'mint',
            disabled: onDisabled || store.busy !== null,
            title: option.ok
              ? (fixture.power === 'on' ? '服务说这盏灯已经开着' : '把这一盏开到 on（绝对状态）')
              : option.message,
            onClick: function () { confirmFixture(view, fixture, 'on'); }
          }),
          btn('关灯', {
            small: true,
            disabled: offDisabled || store.busy !== null,
            title: option.ok
              ? (fixture.power === 'off' ? '服务说这盏灯已经关着' : '把这一盏关到 off（绝对状态）')
              : option.message,
            onClick: function () { confirmFixture(view, fixture, 'off'); }
          })
        ])
      ]);
    });
    if (!fixtureRows.length && light.available) {
      rows.push(h('p', {class: 'world-panel-note', text: '这个房间里没有服务声明过的灯，所以没有开关按钮。'}));
    }
    if (!light.available && light.status !== 'disabled' && !fixtureRows.length) {
      rows.push(h('p', {class: 'world-panel-note', text: '没有可切换的灯：服务没有给出房间里的灯具标识。'}));
    }
    rows.push(h('p', {class: 'world-panel-note', text: '开关灯只改这个房间的灯；身体会不会跟着变，要看下一次读到的身体投影，界面不替身体说话。'}));
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '房间的光'})
    ].concat(chips.length ? [h('div', {class: 'world-chips'}, chips)] : [], fixtureNodes, rows));
  }

  /* 配送：付款成功 ≠ 到货。pending 的家具既不能布置也不能用。 */
  function worldDeliveriesPanel(view) {
    const deliveries = view.room.deliveries;
    if (deliveries.status === 'missing') return null;
    if (deliveries.status === 'unavailable') {
      return h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '还在路上的东西'}),
        h('p', {class: 'world-panel-note', text: worldReasonText(deliveries.reason) || '到货状态暂时读不到。'})
      ]);
    }
    if (!deliveries.items.length) {
      return h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '还在路上的东西'}),
        h('p', {class: 'world-panel-note', text: '服务这次没有列出在送的物件。'})
      ]);
    }
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '还在路上的东西'}),
      h('ul', {class: 'list'}, deliveries.items.map(function (item) {
        return h('li', {}, [h('div', {class: 'list-item is-static'}, [
          h('span', {class: 'list-top'}, [
            h('span', {class: 'list-title', text: item.name}),
            h('span', {class: 'list-meta', text: item.remainingText ? '还要 ' + item.remainingText : ''})
          ]),
          h('span', {class: 'list-chips'}, [
            item.stateLabel ? chip(item.stateLabel, item.pending ? 'is-warn' : 'is-ok') : chip('服务没有给到货状态', 'is-warn'),
            item.expectedAt ? chip('预计 ' + (formatWhen(item.expectedAt) || '时间读不懂'), 'is-neutral') : null,
            item.pending ? chip('到货前不能布置、不能用', 'is-warn') : null
          ]),
          item.idOk ? null : h('span', {class: 'list-text is-full', text: '这条配送的物件标识不可用，界面不会拿它去提交任何操作。'})
        ])]);
      })),
      h('p', {class: 'world-panel-note', text: '到货由世界自己的时间线推进；界面不会因为“付款成功”就说已经送到。'})
    ]);
  }

  /* 自己买的家具：和世界自带的摆设严格分开——这里每一件都有产权与 instance 标识。 */
  function worldOwnedFurniturePanel(view) {
    const furniture = view.room.furniture;
    if (furniture.status === 'missing') return null;
    if (furniture.status === 'unavailable') {
      return h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '自己买的家具'}),
        h('p', {class: 'world-panel-note', text: worldReasonText(furniture.reason) || '自有家具暂时读不到。'})
      ]);
    }
    if (!furniture.items.length) {
      return h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '自己买的家具'}),
        h('p', {class: 'world-panel-note', text: '她还没买过家具；世界自带的摆设不算她的。'})
      ]);
    }
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '自己买的家具'}),
      h('ul', {class: 'list'}, furniture.items.map(function (item) {
        const option = worldArrangeOption(view, item);
        const chips = [chip('自有', 'is-ok')];
        if (item.deliveryLabel) chips.push(chip(item.deliveryLabel, item.delivered === false ? 'is-warn' : 'is-neutral'));
        if (item.delivery === 'pending' || item.delivery === 'in_transit') chips.push(chip('还没到货', 'is-warn'));
        if (item.placed === true) chips.push(chip('已经摆出来', 'is-neutral'));
        else if (item.placed === false) chips.push(chip('还没摆出来', 'is-neutral'));
        if (item.ownsSurfaces.length) chips.push(chip('自带 ' + item.ownsSurfaces.length + ' 块台面', 'is-neutral'));
        const buttons = option.ok ? option.targets.map(function (target) {
          return btn('布置：' + target.label, {
            small: true, variant: 'mint',
            disabled: store.busy !== null,
            title: '用 ' + option.verb + ' 把 ' + item.name + ' 放到' + target.label,
            onClick: function () { confirmArrange(view, item, target, option.verb); }
          });
        }) : [];
        return h('li', {}, [h('div', {class: 'list-item is-static'}, [
          h('span', {class: 'list-top'}, [
            h('span', {class: 'list-title', text: item.name}),
            h('span', {class: 'list-meta', text: item.placementText || ''})
          ]),
          h('span', {class: 'list-chips'}, chips),
          buttons.length
            ? h('span', {class: 'list-chips'}, buttons)
            : h('span', {class: 'list-text is-full', text: option.message})
        ])]);
      })),
      h('p', {class: 'world-panel-note', text: '布置是一条真实的世界命令；摆好了也不等于现在就能用——能不能坐、能不能放东西要看台面自己的声明。'})
    ]);
  }

  /* 台面：instance 级 surface_id。两张桌子是两块不同的台面，不会串。 */
  function worldSurfacesPanel(view) {
    const surfaces = view.room.surfaces;
    if (surfaces.status === 'missing') return null;
    if (surfaces.status === 'unavailable') {
      return h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '台面与坐卧的地方'}),
        h('p', {class: 'world-panel-note', text: worldReasonText(surfaces.reason) || '台面暂时读不到。'})
      ]);
    }
    if (!surfaces.items.length) {
      return h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '台面与坐卧的地方'}),
        h('p', {class: 'world-panel-note', text: '服务没有给出任何台面；买来的桌子还没成为台面之前，界面不会假设它有一块桌面。'})
      ]);
    }
    const placeGate = worldVerbAvailability(view, 'PLACE');
    const hands = [
      {slot: 'left_hand', hand: view.objects.handSlots ? view.objects.handSlots.left_hand : null},
      {slot: 'right_hand', hand: view.objects.handSlots ? view.objects.handSlots.right_hand : null}
    ];
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '台面与坐卧的地方'}),
      h('ul', {class: 'list'}, surfaces.items.map(function (surface) {
        const chips = [];
        if (surface.ownerCatalog) chips.push(chip('属于：' + surface.ownerCatalog, 'is-neutral'));
        const placeText = [surface.locationId, surface.areaId].filter(Boolean).join(' / ');
        if (placeText) chips.push(chip(placeText, 'is-neutral'));
        if (surface.here === true) chips.push(chip('就在这儿', 'is-ok'));
        /* 能力只照 owner 的声明读：pose_condition 与 rest_modes，没有就明说没有。 */
        if (surface.poseLabel) chips.push(chip('服务声明：' + surface.poseLabel, 'is-neutral'));
        surface.restModes.forEach(function (item) { chips.push(chip('服务声明：' + item.label, 'is-neutral')); });
        if (!surface.poseLabel && !surface.restModes.length) {
          chips.push(chip('服务没有声明这张台面能做什么姿势', 'is-neutral'));
        }
        if (surface.children.length) chips.push(chip('上面放着 ' + surface.children.length + ' 件东西', 'is-ok'));

        const buttons = [];
        if (surface.idOk && placeGate.available === true) {
          hands.forEach(function (entry) {
            const hand = entry.hand;
            if (!hand || !hand.present || !hand.idOk || !hand.id) return;
            buttons.push(btn('把' + (HAND_SLOT_TEXT[entry.slot] || entry.slot) + '的东西放上去', {
              small: true, variant: 'mint',
              disabled: store.busy !== null,
              title: '放到这块台面（' + surface.label + '）',
              onClick: function () { confirmSurfacePlace(view, hand, surface); }
            }));
          });
        }
        return h('li', {}, [h('div', {class: 'list-item is-static'}, [
          h('span', {class: 'list-top'}, [
            h('span', {class: 'list-title', text: surface.label}),
            h('span', {class: 'list-meta', text: surface.idOk ? '' : '标识不可用'})
          ]),
          h('span', {class: 'list-chips'}, chips),
          buttons.length ? h('span', {class: 'list-chips'}, buttons) : null,
          surface.idOk ? null : h('span', {class: 'list-text is-full', text: '这块台面的标识不可用（' + surface.idMessage + '），界面不会拿它去提交摆放。'}),
          surface.note ? h('span', {class: 'list-text is-full', text: surface.note}) : null
        ])]);
      })),
      h('p', {class: 'world-panel-note', text: '每块台面有自己的标识：桌上放的东西只属于那一张桌子。手里没有东西时，摆放按钮不会出现。'})
    ]);
  }

  function worldRoomPanel(view) {
    const room = view && view.room ? view.room : null;
    if (!room) return null;
    const nodes = [];
    const light = worldRoomLightPanel(view);
    if (light) nodes.push(light);
    const deliveries = worldDeliveriesPanel(view);
    if (deliveries) nodes.push(deliveries);
    const furniture = worldOwnedFurniturePanel(view);
    if (furniture) nodes.push(furniture);
    const surfaces = worldSurfacesPanel(view);
    if (surfaces) nodes.push(surfaces);
    return nodes;
  }

  /* 手上那件东西：hand 是 worldObjectsView 的 handSlots[slot]（{id, idOk, idLabel, present}）。
     屏幕只用 idLabel，payload 只用 id；标识不可用就禁用「放下」，不拿展示串顶替。 */
  function worldHandRow(view, slot, hand) {
    const label = HAND_SLOT_TEXT[slot] || slot;
    if (!hand || !hand.present) {
      return h('div', {class: 'world-hand'}, [
        h('span', {class: 'world-hand-slot', text: label}),
        h('span', {class: 'world-hand-item is-empty', text: '空着'})
      ]);
    }
    if (!hand.idOk || !hand.id) {
      return h('div', {class: 'world-hand'}, [
        h('span', {class: 'world-hand-slot', text: label}),
        h('span', {class: 'world-hand-item', text: hand.idLabel || '手上这件东西'}),
        btn('放下', {
          small: true,
          disabled: true,
          title: '手上这件东西的标识不可用（' + (hand.idMessage || '服务没有给出可用标识') + '），界面不会拿展示文字冒名提交。'
        })
      ]);
    }
    const place = worldPlaceOption(view, {id: hand.id, idOk: true});
    return h('div', {class: 'world-hand'}, [
      h('span', {class: 'world-hand-slot', text: label}),
      h('span', {class: 'world-hand-item', text: hand.idLabel}),
      btn('放下', {
        small: true,
        disabled: !place.ok || store.busy !== null,
        title: place.ok ? ('把 ' + hand.idLabel + ' 放回' + (view.location.name || '这里')) : place.message,
        onClick: function () { confirmPlace(view, hand); }
      })
    ]);
  }

  function worldObjectsPanel(view) {
    const objects = view.objects;
    if (objects.status === 'missing') return null;
    if (objects.status === 'unavailable') {
      return h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '手边'}),
        h('p', {class: 'world-panel-note', text: worldReasonText(objects.reason) || '物件与摆放暂时读不到。'})
      ]);
    }
    const here = objects.here.map(function (object) {
      const pickup = worldPickupOptions(view, object);
      return h('li', {}, [h('div', {class: 'list-item is-static'}, [
        h('span', {class: 'list-top'}, [
          h('span', {class: 'list-title', text: object.label}),
          h('span', {class: 'list-meta', text: object.placementText || ''})
        ]),
        h('span', {class: 'list-chips'}, [
          object.portable === true ? chip('能拿', 'is-ok') : null,
          object.portable === false ? chip('拿不起来', 'is-neutral') : null,
          object.portable === null ? chip('能不能拿未声明', 'is-warn') : null
        ]),
        pickup.ok
          ? h('span', {class: 'list-chips'}, pickup.slots.map(function (slot) {
            return btn('拿起到' + slot.label, {
              small: true,
              variant: 'mint',
              disabled: store.busy !== null,
              onClick: function () { confirmPickup(view, object, slot.slot); }
            });
          }))
          : h('span', {class: 'list-text is-full', text: pickup.message})
      ])]);
    });
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '手边'}),
      h('div', {class: 'world-hands'}, [
        worldHandRow(view, 'left_hand', objects.handSlots ? objects.handSlots.left_hand : null),
        worldHandRow(view, 'right_hand', objects.handSlots ? objects.handSlots.right_hand : null)
      ]),
      here.length
        ? h('ul', {class: 'list'}, here)
        : h('p', {class: 'world-panel-note', text: '这个位置没有摆着的东西；拿得起来的只在这里列出的物件里。'})
    ]);
  }

  function worldPosePanel(view) {
    const gate = worldVerbAvailability(view, 'POSE');
    const body = view.body;
    const options = [h('option', {value: '', text: '朝向不指定'})].concat(ORIENTATION_VALUES.map(function (value) {
      return h('option', {value: value, text: ORIENTATION_TEXT[value] || value});
    }));
    const select = h('select', {class: 'world-select', 'aria-label': '换姿势时的朝向'}, options);
    const buttons = POSE_VALUES.map(function (pose) {
      const same = body.pose === pose;
      return btn(POSE_TEXT[pose] || pose, {
        small: true,
        disabled: gate.available !== true || same || store.busy !== null,
        title: gate.available !== true
          ? (gate.blockedText || '服务没有说明能不能换姿势')
          : (same ? '现在就是这个姿势' : null),
        onClick: function () {
          const orientation = select && typeof select.value === 'string' && select.value ? select.value : null;
          confirmPose(view, pose, orientation);
        }
      });
    });
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '姿势'}),
      h('p', {class: 'world-panel-note', text: '现在是' + (body.poseLabel || '未知姿势') +
        (body.orientationLabel ? '，' + body.orientationLabel : '') + '。' +
        (gate.available === true ? '换姿势是一条命令，成功只代表世界接受了它。' : '')}),
      h('div', {class: 'row-actions'}, buttons),
      h('div', {class: 'row-actions'}, [select]),
      gate.available !== true && gate.blockedText ? h('p', {class: 'world-panel-note', text: gate.blockedText}) : null
    ]);
  }

  function worldRestPanel(view) {
    const rest = worldRestOption(view);
    const recovery = view.body.recovery;
    const rows = [];
    rows.push(h('p', {class: 'world-rest-state', text: '恢复：' + (view.body.known ? (recovery.stateLabel || '服务没有说状态') : '身体投影暂时读不到')}));
    if (view.body.known) {
      const details = [];
      if (recovery.modeLabel) details.push(recovery.modeLabel);
      if (recovery.bandLabel) details.push('程度' + recovery.bandLabel);
      if (recovery.trendLabel) details.push(recovery.trendLabel);
      if (details.length) rows.push(h('p', {class: 'world-panel-note', text: details.join(' · ')}));
      if (view.body.restEvidence) rows.push(h('p', {class: 'world-panel-note', text: '休息条件：' + view.body.restEvidence}));
    }
    rows.push(h('div', {class: 'row-actions'}, [
      btn('开始休息', {
        small: true,
        variant: 'mint',
        disabled: !rest.startEnabled || store.busy !== null,
        title: rest.startEnabled ? '开始一次休息' : (rest.blockedText || '服务没有说明能不能开始休息'),
        onClick: function () { confirmRest(view, 'start', 'rest'); }
      }),
      btn('躺下睡一会', {
        small: true,
        variant: 'mint',
        disabled: !rest.sleepEnabled || store.busy !== null,
        title: rest.sleepEnabled ? '睡着式的恢复' : (rest.blockedText || '现在不能睡'),
        onClick: function () { confirmRest(view, 'start', 'sleep_like'); }
      }),
      btn('结束休息', {
        small: true,
        disabled: !rest.stopEnabled || store.busy !== null,
        title: rest.stopEnabled ? '结束当前这次恢复' : (rest.blockedText || '现在没有开着的休息'),
        onClick: function () { confirmRest(view, 'stop', null); }
      })
    ]));
    if (rest.blockedText) rows.push(h('p', {class: 'world-panel-note', text: '服务说：' + rest.blockedText}));
    rows.push(h('p', {class: 'world-panel-note', text: '命令被接受不等于已经恢复；下一次读取到的恢复状态才是事实。负荷低时服务会直接说“现在不需要恢复”，那不是界面出错。'}));
    return h('div', {class: 'world-panel'}, [h('h2', {class: 'world-panel-title', text: '休息与恢复'})].concat(rows));
  }

  function worldBodyPanel(view) {
    const body = view.body;
    const signals = worldSignalLines(body);
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '身体在说什么'}),
      body.known
        ? h('ul', {class: 'world-signals'}, signals.map(function (line) {
          return h('li', {class: 'world-signal', text: line});
        }))
        : h('p', {class: 'world-panel-note', text: signals[0]}),
      h('div', {class: 'row-actions'}, [
        btn('打开身体', {small: true, onClick: function () { go('body'); }})
      ]),
      h('p', {class: 'world-panel-note', text: '身体只给姿势、档位和恢复状态；这里没有可调的身体数值。'})
    ]);
  }

  function worldRecentPanel(view) {
    const recent = view.recent;
    if (recent.status === 'missing' || !recent.items.length) return null;
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '刚才发生的事'}),
      h('ul', {class: 'world-recent'}, recent.items.slice(0, 5).map(function (item) {
        return h('li', {class: 'world-recent-item'}, [
          h('span', {class: 'world-recent-what', text: item.actionLabel + (item.stateLabel ? ' · ' + item.stateLabel : '')}),
          h('span', {class: 'world-recent-when', text: formatWhen(item.occurredAt) || '时间未提供'}),
          item.reasonText ? h('span', {class: 'world-recent-note', text: item.reasonText}) : null,
          item.localOwner === true ? chip('本地 owner', 'is-neutral') : null
        ]);
      }))
    ]);
  }

  function worldTechPanel(view) {
    const entries = worldTechEntries(view);
    const blocks = WORLD_BLOCK_KEYS.map(function (key) {
      const status = view.blocks[key] || 'missing';
      return chip(key + ':' + status, status === 'available' || status === 'partial' ? 'is-neutral' : 'is-warn');
    });
    return h('details', {class: 'tech-details'}, [
      h('summary', {class: 'tech-summary', text: '技术详情（owner 字段与原因码）'}),
      h('div', {class: 'tech-chips'}, blocks),
      h('div', {class: 'tech-body'}, entries.map(function (entry) {
        return kv(entry.key, entry.text, true);
      }))
    ]);
  }
  /* ---------------- 世界的五条命令：MOVE / PICK_UP / PLACE / POSE / REST ---------------- */

  function confirmMove(view, node) {
    const reason = worldMoveDisabledReason(view, node);
    if (reason) {
      store.result = {scope: worldActionScope('MOVE', {destination_location_id: node.node}), status: 'blocked', message: reason};
      renderAll();
      return;
    }
    openSheet({
      title: '出发去' + node.label,
      lines: [
        '从' + (view.location.name || '当前位置') + '出发',
        '步行时长（服务给的）：' + (node.walkText || '未提供'),
        node.direct ? '一次就能走到' : '要分几步，这一步只走一段'
      ],
      note: '点确认只是提交出发；到没到要看世界自己的结算，界面不会提前显示已到达。',
      confirmLabel: '确认出发',
      onConfirm: function () {
        submitWorldAction('MOVE', {destination_location_id: node.node}, '出发');
      }
    });
  }

  function confirmPickup(view, object, slot) {
    const pickup = worldPickupOptions(view, object);
    if (!pickup.ok) {
      store.result = {scope: worldActionScope('PICK_UP', {object_id: object.id, slot_id: slot}), status: 'blocked', message: pickup.message};
      renderAll();
      return;
    }
    const label = HAND_SLOT_TEXT[slot] || slot;
    openSheet({
      title: '拿起到' + label,
      lines: [
        object.label,
        '物件：' + object.idLabel,
        object.placementText ? '现在放在：' + object.placementText : ''
      ],
      note: '拿起由世界按真实摆放、归属和手容量判定；界面只提交这一次。',
      confirmLabel: '确认拿起',
      onConfirm: function () {
        submitWorldAction('PICK_UP', {object_id: object.id, slot_id: slot}, '拿起');
      }
    });
  }

  /* hand 是 handSlots[slot]：PLACE 的 object_id 只发 id 原字节；屏幕上的名字用 idLabel。 */
  function confirmPlace(view, hand) {
    const objectId = hand && typeof hand.id === 'string' ? hand.id : '';
    const idLabel = hand && hand.idLabel ? hand.idLabel : '手上这件东西';
    const place = worldPlaceOption(view, {id: objectId, idOk: !!objectId});
    if (!place.ok) {
      store.result = {scope: worldActionScope('PLACE', {object_id: objectId}), status: 'blocked', message: place.message};
      renderAll();
      return;
    }
    openSheet({
      title: '放下手里的东西',
      lines: [
        '物件：' + idLabel,
        '放回：' + (view.location.name || '当前位置') + (view.indoor.available && view.indoor.areaName ? '·' + view.indoor.areaName : '')
      ],
      note: '摆放目标按当前房间提交；能不能放由世界判定。',
      confirmLabel: '确认放下',
      onConfirm: function () {
        submitWorldAction('PLACE', {object_id: objectId, target: place.target}, '放下');
      }
    });
  }

  /* 放到某一块台面上：target 用已冻结的 {kind, location_id, surface_id}，surface_id 是原字节。 */
  function confirmSurfacePlace(view, hand, surface) {
    const objectId = hand && typeof hand.id === 'string' ? hand.id : '';
    const placeId = surface && surface.locationId ? surface.locationId : (view.location ? view.location.placeId : '');
    const target = {kind: 'surface', location_id: placeId, surface_id: surface ? surface.id : ''};
    const built = buildSurfacePlacementTarget(target);
    const gate = worldVerbAvailability(view, 'PLACE');
    if (!built.ok || gate.available !== true) {
      store.result = {
        scope: worldActionScope('PLACE', {object_id: objectId, target: target}),
        status: 'blocked',
        message: built.ok ? (gate.blockedText || '服务没有说明现在能不能放东西。') : built.message
      };
      renderAll();
      return;
    }
    openSheet({
      title: '放到台面上',
      lines: [
        '物件：' + (hand && hand.idLabel ? hand.idLabel : '手里这件东西'),
        '台面：' + (surface ? surface.label : '服务给的台面'),
        '位置：' + (placeId || '服务没有给')
      ],
      note: '每一块台面有自己的标识；能不能放由世界判定，放上去之后还拿得起来。',
      confirmLabel: '确认放上去',
      onConfirm: function () {
        submitWorldAction('PLACE', {object_id: objectId, target: built.target}, '放上桌面');
      }
    });
  }

  /* 布置/搬动一件自己买的家具：动词名用 owner 报上来的那个（ARRANGE 或 RELOCATE）。 */
  function confirmArrange(view, item, target, verb) {
    const option = worldArrangeOption(view, item);
    if (!option.ok) {
      store.result = {
        scope: worldActionScope(verb || 'ARRANGE', {object_id: item && item.id, target: target && target.target}),
        status: 'blocked',
        message: option.message
      };
      renderAll();
      return;
    }
    openSheet({
      title: '布置：' + item.name,
      lines: [
        '家具：' + (item.idLabel || item.name),
        '位置：' + (target && target.label ? target.label : '服务给出的位置'),
        item.deliveryLabel ? '交付：' + item.deliveryLabel : ''
      ].filter(Boolean),
      note: '布置只改这件家具在世界里的位置；摆好不等于现在能用，能不能用要看台面自己的声明。',
      confirmLabel: '确认布置',
      onConfirm: function () {
        submitWorldAction(option.verb, {object_id: item.id, target: target.target}, '布置');
      }
    });
  }

  /* 灯的开关：payload 是绝对状态，绝不发“翻转”。 */
  function confirmFixture(view, fixture, state) {
    /* 每盏灯有自己记录下来的 power（gateway candidate）；没有时退回房间聚合 power。 */
    const currentPower = fixture && (fixture.power === 'on' || fixture.power === 'off')
      ? fixture.power
      : view.room.light.power;
    const option = worldFixtureOption(view, fixture, currentPower);
    const params = {object_id: fixture ? fixture.id : '', state: state};
    if (!option.ok) {
      store.result = {
        scope: worldActionScope(option.verb || 'TOGGLE_FIXTURE', params),
        status: 'blocked',
        message: option.message
      };
      renderAll();
      return;
    }
    openSheet({
      title: state === 'on' ? '开灯' : '关灯',
      lines: [
        '灯：' + (fixture.idLabel || '服务给的灯'),
        '要提交的状态：' + (state === 'on' ? '开着（on）' : '关着（off）')
      ],
      note: '提交的是绝对状态；灯已经是这个状态时由世界判 noop，不会重复写。开灯不代表身体已经变了。',
      confirmLabel: state === 'on' ? '确认开灯' : '确认关灯',
      onConfirm: function () {
        submitWorldAction(option.verb, params, state === 'on' ? '开灯' : '关灯');
      }
    });
  }

  function confirmPose(view, pose, orientation) {
    const gate = worldVerbAvailability(view, 'POSE');
    if (gate.available !== true) {
      store.result = {scope: worldActionScope('POSE', {pose: pose}), status: 'blocked', message: gate.blockedText || '服务没有说明能不能换姿势'};
      renderAll();
      return;
    }
    const params = {pose: pose};
    if (orientation) params.orientation = orientation;
    openSheet({
      title: '换姿势：' + (POSE_TEXT[pose] || pose),
      lines: [
        '现在：' + (view.body.poseLabel || '未知姿势') + (view.body.orientationLabel ? ' · ' + view.body.orientationLabel : ''),
        orientation ? '朝向：' + (ORIENTATION_TEXT[orientation] || orientation) : '朝向：不指定'
      ],
      note: '姿势是世界的测量；命令成功不代表身体恢复了。',
      confirmLabel: '确认换姿势',
      onConfirm: function () {
        submitWorldAction('POSE', params, '换姿势');
      }
    });
  }

  function confirmRest(view, state, mode) {
    const rest = worldRestOption(view);
    const enabled = state === 'start'
      ? (mode === 'sleep_like' ? rest.sleepEnabled : rest.startEnabled)
      : rest.stopEnabled;
    const params = state === 'start' ? {state: 'start', mode: mode || 'rest'} : {state: 'stop'};
    if (!enabled) {
      store.result = {
        scope: worldActionScope('REST', params),
        status: 'blocked',
        message: rest.blockedText || '服务说现在不能执行这一步。'
      };
      renderAll();
      return;
    }
    openSheet({
      title: state === 'start' ? (mode === 'sleep_like' ? '躺下睡一会' : '开始休息') : '结束休息',
      lines: [
        '现在的恢复状态：' + (view.body.recovery.stateLabel || '服务没有说'),
        state === 'start' ? '方式：' + (RECOVERY_MODE_TEXT[mode || 'rest'] || '休息') : '会结束当前这次恢复'
      ],
      note: '恢复由身体按真实经过的时间推进；命令成功不等于已经恢复。',
      confirmLabel: state === 'start' ? '确认开始' : '确认结束',
      onConfirm: function () {
        submitWorldAction('REST', params, state === 'start' ? '开始休息' : '结束休息');
      }
    });
  }

  async function submitWorldAction(action, params, label) {
    const guard = canWrite(store.conn);
    const scope = worldActionScope(action, params);
    if (!guard.ok) {
      store.result = {scope: scope, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    if (store.busy !== null) return;
    const entry = ops.ensure(scope, {path: '/world/actions'});
    const built = buildWorldActionPayload({action: action, params: params, operation_key: entry.key});
    if (!built.ok) {
      ops.resolve(scope);
      store.result = {scope: scope, status: 'blocked', message: built.message};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在提交' + (label || '世界动作') + '…', key: entry.key};
    renderAll();
    const res = await request('POST', '/world/actions', {csrf: store.conn.csrf, body: built.payload});
    const outcome = deriveMutationOutcome(res);
    finishMutation(scope, outcome, label || '世界动作', worldOutcomeLines(action, outcome.doc));
  }

  /* 世界命令回执 → 人话：出发不是到达、姿势不是恢复、负荷 pending 不写“已经累了”。 */
  function worldOutcomeLines(action, doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!data) return [];
    const lines = [];
    const travel = asRecord(data.travel);
    if (travel) {
      const status = asString(travel.status);
      const destination = safeLabel(travel.destination_location_id, 40);
      if (status === 'in_progress') {
        const remaining = asSeconds(travel.remaining_seconds);
        lines.push('已出发去' + (destination || '目的地') + (remaining !== null ? '，世界说还要 ' + formatWalkText(remaining) : '') + '。');
        lines.push('这是出发，不是到达；位置要等世界自己结算。');
      } else if (status === 'arrived') {
        lines.push('世界报告已经到达' + (destination ? '：' + destination : '') + '。');
      } else if (status) {
        lines.push('这次行程的状态：' + (TRAVEL_STATUS_TEXT[status] || safeLabel(status, 30)));
      }
    }
    const body = asRecord(data.body);
    if (body) {
      const settlement = asString(body.movement_settlement);
      if (settlement === 'pending') lines.push('这次路程的身体负荷还没结算（等到达之后再算）。');
      const recovery = asRecord(body.recovery);
      const state = recovery ? asString(recovery.state) : '';
      if (state === 'active' && action === 'REST') lines.push('恢复已经开着；是否真的在推进要看下一次读取。');
      if (state === 'inactive' && action === 'REST') lines.push('服务接受了命令，但这一次身体没有变化（advance_produced_no_change）。');
      const applied = asBool(body.applied_to_body);
      if (applied === false && !settlement && action !== 'REST') {
        lines.push('身体这次没有跟着变：' + (worldReasonText(asString(body.consequence_reason)) || '服务没有说明'));
      }
    }
    const world = asRecord(data.world);
    if (world && asBool(world.location_unchanged) === true) lines.push('位置没有变（location_unchanged）。');
    const refs = Array.isArray(data.world_mutation_refs) ? data.world_mutation_refs : [];
    if (refs.length) {
      lines.push('世界改动：' + refs.slice(0, 3).map(function (ref) { return safeLabel(ref, 60); }).join('、'));
    }
    /* rc1：灯与布置的回执只说 owner 真的报回来的东西（灯状态 / 摆放位置），不替身体下结论。 */
    if (action === 'TOGGLE_FIXTURE') {
      const fixture = asRecord(data.fixture);
      const fxStatus = fixture ? asString(fixture.status) : '';
      const fxState = fixture ? asString(fixture.state || fixture.power) : '';
      if (fxStatus === 'noop') {
        lines.push('世界说这盏灯已经是' + (fxState === 'on' ? '开着的' : '关着的') + '，这次没有重复写入。');
      } else if (fxState === 'on' || fxState === 'off') {
        lines.push('世界记账了：灯现在是' + (fxState === 'on' ? '开着的' : '关着的') + '。');
      } else {
        lines.push('世界接受了这次开关，但回执里没有灯的最终状态；以再次读到的房间光为准。');
      }
      lines.push('灯变了不等于身体变了：身体有没有跟上，要看下一次读到的身体投影。');
    }
    if (action === 'ARRANGE' || action === 'RELOCATE') {
      /* 契约 §2.1：成功回执是 data.arranged{object_id,target,carried,surface_ids}；
         `arrange`/`placement` 只是旧字段名兜底。noop 时 reason_code=already_arranged。 */
      const arrangement = asRecord(data.arranged) || asRecord(data.arrange) || asRecord(data.placement);
      const target = arrangement ? (asRecord(arrangement.target) || arrangement) : null;
      const where = target
        ? [safeLabel(target.location_id, 40), safeLabel(target.area_id, 40), safeLabel(target.surface_id, 40)]
          .filter(Boolean).join(' / ')
        : '';
      const carried = arrangement && Array.isArray(arrangement.carried) ? arrangement.carried.length : null;
      const arrangeReason = asString(data.reason_code) || asString(data.status);
      if (arrangeReason === 'already_arranged' || arrangeReason === 'noop') {
      } else {
        lines.push('这次布置已提交到世界的摆放账本' + (where ? '：' + where : '') + '。');
      }
      if (carried) lines.push('跟着一起搬的还有 ' + carried + ' 件放在它台面上的东西。');
      lines.push('摆好了不等于现在能用；能不能坐、能不能放东西要看台面自己的声明。');
    }
    return lines;
  }

  function worldScreen(view) {
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    nodes.push(worldHero(view));
    const travel = worldTravelCard(view);
    if (travel) nodes.push(travel);
    nodes.push(worldMapPanel(view));
    const destinations = view.destinations;
    if (destinations.status === 'unavailable') {
      nodes.push(statePanel('warn', '可达目的地读不到', worldReasonText(destinations.reason) || '服务没有说明'));
    } else if (!destinations.nodes.length) {
      nodes.push(statePanel('empty', '服务没有列出可达节点', '这时没有可以出发的地方；界面不会自己编一份街区。'));
    } else {
      nodes.push(h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '能走到的地方'}),
        h('ul', {class: 'list'}, destinations.nodes.map(function (node) { return worldMoveRow(view, node); }))
      ]));
    }
    if (destinations.places.length) {
      nodes.push(h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '附近'}),
        h('ul', {class: 'list'}, destinations.places.map(function (place) { return worldPlaceRow(view, place); }))
      ]));
    }
    const indoor = worldIndoorPanel(view);
    if (indoor) nodes.push(indoor);
    /* rc1 房间：灯、到货、自有家具、台面。都在世界快照里，进入这一屏不多发请求。 */
    const roomPanels = worldRoomPanel(view);
    if (roomPanels) roomPanels.forEach(function (panel) { nodes.push(panel); });
    const objects = worldObjectsPanel(view);
    if (objects) nodes.push(objects);
    nodes.push(worldPosePanel(view));
    nodes.push(worldRestPanel(view));
    nodes.push(worldBodyPanel(view));
    const recent = worldRecentPanel(view);
    if (recent) nodes.push(recent);
    nodes.push(worldTechPanel(view));
    return page('世界', '街区、房间、路上的位置和身体都只认服务的事实', nodes, [
      btn('刷新', {small: true, icon: 'refresh', onClick: function () { loadResource('world'); }})
    ]);
  }

  function renderWorld() {
    const gate = worldGate();
    if (gate) return page('世界', '街区、房间与路程', [gate]);
    const result = resultCard();
    const entry = store.data.world;
    if (!entry || entry.phase === 'loading') {
      return page('世界', '街区、房间与路程', [result, statePanel('loading', '正在读取世界…')]);
    }
    if (entry.phase !== 'ready') {
      const offline = entry.phase === 'offline';
      return page('世界', '街区、房间与路程', [
        result,
        statePanel(offline ? 'offline' : 'error', offline ? '没有连上世界' : '世界读不出来', entry.message, [
          btn('重试', {onClick: function () { loadResource('world'); }})
        ])
      ]);
    }
    return worldScreen(worldView(entry.doc));
  }
  /* ---------------- 身体：姿势 / 信号 / 恢复（只读 + owner 命令） ---------------- */

  /* 身体面优先用 /world 的 body 块（带 signals 与来源）；没有就读 /state 的 body 投影。 */
  function bodyProjection() {
    const live = liveWorldView();
    if (live && live.body.status !== 'missing') {
      return {view: live.body, fromWorld: true, world: live};
    }
    const app = appState('body');
    const data = app && app.data ? asRecord(app.data) : null;
    if (!data) return {view: bodyView(null), fromWorld: false, world: null};
    const inner = asRecord(data.body) || data;
    const patched = Object.assign({}, inner);
    /* P 的 /state 投影把状态放在 body_status：这里只是补上同一个事实，不新增判断。 */
    if (!patched.status && typeof data.body_status === 'string') patched.status = data.body_status;
    return {view: bodyView(patched), fromWorld: false, world: live};
  }

  function bodySignalPanel(body) {
    const lines = worldSignalLines(body);
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '身体在说什么'}),
      body.known
        ? h('ul', {class: 'world-signals'}, lines.map(function (line) {
          return h('li', {class: 'world-signal', text: line});
        }))
        : h('p', {class: 'world-panel-note', text: lines[0]}),
      h('p', {class: 'world-panel-note', text: body.signalsMissing
        ? '服务这次没有提供信号；界面不补一条“感觉正常”。'
        : '信号只有轻 / 明显 / 强这样的档位，没有数值。'})
    ]);
  }

  function bodyRecoveryPanel(body, world) {
    const recovery = body.recovery;
    const rows = [];
    rows.push(kv('状态', body.known ? (recovery.stateLabel || '服务没有说') : '读不到'));
    if (recovery.modeLabel) rows.push(kv('方式', recovery.modeLabel));
    if (recovery.bandLabel) rows.push(kv('程度', recovery.bandLabel));
    if (recovery.trendLabel) rows.push(kv('趋势', recovery.trendLabel));
    if (body.restEvidence) rows.push(kv('休息条件', body.restEvidence));
    if (body.poseSource) rows.push(kv('姿势来源', body.poseSource));
    if (body.signalsSource) rows.push(kv('信号来源', body.signalsSource));
    /* 会话结束 ≠ 恢复：owner 的词表里没有「恢复了」，这里也不替它造一个。 */
    if (body.known && recovery.state && !recovery.active) {
      rows.push(h('p', {class: 'world-panel-note', text:
        '会话状态是「' + (recovery.stateLabel || recovery.state) +
        '」：这只说明这次会话结束了，不等于恢复了。恢复要看趋势与档位，没有变化就不算恢复。'}));
    }
    if (body.known && !recovery.state) {
      rows.push(h('p', {class: 'world-panel-note', text:
        '服务这次没有给恢复状态：未知不等于休息好了，界面不补一条。'}));
    }
    const rest = world ? worldRestOption(world) : null;
    const actions = [];
    if (rest && world) {
      actions.push(btn('开始休息', {
        small: true,
        variant: 'mint',
        disabled: !rest.startEnabled || store.busy !== null,
        title: rest.startEnabled ? '开始一次休息' : (rest.blockedText || '服务没有说明能不能开始休息'),
        onClick: function () { confirmRest(world, 'start', 'rest'); }
      }));
      actions.push(btn('躺下睡一会', {
        small: true,
        variant: 'mint',
        disabled: !rest.sleepEnabled || store.busy !== null,
        title: rest.sleepEnabled ? '睡着式的恢复' : (rest.blockedText || '现在不能睡'),
        onClick: function () { confirmRest(world, 'start', 'sleep_like'); }
      }));
      actions.push(btn('结束休息', {
        small: true,
        disabled: !rest.stopEnabled || store.busy !== null,
        title: rest.stopEnabled ? '结束当前这次恢复' : (rest.blockedText || '现在没有开着的休息'),
        onClick: function () { confirmRest(world, 'stop', null); }
      }));
    }
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '恢复'})
    ].concat(rows).concat([
      actions.length ? h('div', {class: 'row-actions'}, actions) : null,
      rest && rest.blockedText ? h('p', {class: 'world-panel-note', text: '服务说：' + rest.blockedText}) : null,
      !world ? h('p', {class: 'world-panel-note', text: '世界的快照还没读到，所以这里没有坐下/休息按钮：点下面或上面的「重新读取世界快照」重试，不用先去世界屏。'}) : null,
      h('p', {class: 'world-panel-note', text: '低负荷时服务会直接拒绝“开始休息”，那是真实的判断，不是界面故障。'})
    ]));
  }

  function renderBody() {
    const projection = bodyProjection();
    const body = projection.view;
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);

    const app = appState('body');
    if (app.status === 'disabled') {
      return page('身体', '只读状态视图', [result, statePanel('disabled', '身体这个应用被禁用了。', app.reasonCode ? reasonText(app.reasonCode) : '')]);
    }
    if (app.status === 'missing' && !projection.fromWorld) {
      return page('身体', '只读状态视图', [result, statePanel('missing', '服务没有报告身体这个应用。', '界面不会自己编一份身体状态。')]);
    }

    nodes.push(h('div', {class: 'body-hero'}, [
      h('p', {class: 'body-hero-pose', text: body.known ? (body.poseLabel || '姿势未提供') : '读不到'}),
      h('p', {class: 'body-hero-line', text: body.known
        ? (body.orientationLabel ? body.orientationLabel + ' · ' : '') + (body.recovery.stateLabel || '恢复状态未提供')
        : '身体投影暂时读不到：' + (worldReasonText(body.reason) || '服务没有说明')}),
      h('div', {class: 'world-chips'}, [
        projection.fromWorld ? chip('来自世界快照', 'is-neutral') : chip('来自 /state 投影', 'is-neutral'),
        body.bandsOnly === true ? chip('只有档位', 'is-ok') : null,
        body.rawExposed === false ? chip('没有裸数值', 'is-ok') : null
      ])
    ]));

    if (body.known) {
      nodes.push(bodySignalPanel(body));
      nodes.push(bodyRecoveryPanel(body, projection.world));
    } else {
      nodes.push(statePanel('warn', '身体投影读不到', worldReasonText(body.reason) || '服务没有说明'));
      nodes.push(bodyRecoveryPanel(body, projection.world));
    }

    if (projection.world) {
      /* 显式坐下 / 站起：复用世界已有的 POSE 入口（判据在 owner），身体屏不再只是只读卡片。 */
      nodes.push(worldPosePanel(projection.world));
      nodes.push(h('p', {class: 'world-panel-note', text:
        '姿势与休息都是世界命令：命令被接受只说明 owner 收下了这次命令，身体有没有跟上要看下一次读到的投影。'}));
      const recent = worldRecentPanel(projection.world);
      if (recent) nodes.push(recent);
    } else {
      /* 快照没到时也要可操作：坐下/休息的入口依赖它，这里给出真正的重试按钮，
         不要求用户先去世界屏绕一圈（父级实测过的缺陷）。 */
      const worldEntry = store.data.world;
      const phase = worldEntry && typeof worldEntry.phase === 'string' ? worldEntry.phase : 'missing';
      if (phase === 'loading') {
        nodes.push(statePanel('loading', '正在读取世界快照…',
          '坐下、开始/结束休息这些命令要等世界快照读到之后才出现。'));
      } else if (phase === 'error' || phase === 'offline') {
        nodes.push(statePanel('warn', '世界快照这次没读到',
          (worldEntry && worldEntry.message) || '服务没有说明',
          [btn('重新读取世界快照', {onClick: function () { loadResource('world'); }})]));
      }
    }
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('回到世界', {small: true, onClick: function () { go('world'); }}),
      btn('刷新', {small: true, icon: 'refresh', onClick: function () { loadResource('world'); }})
    ]));
    nodes.push(h('p', {class: 'footnote', text: '身体只给姿势、档位、信号与恢复状态；没有调试开关，也没有直接改数值的接口。'}));
    return page('身体', '姿势、信号与恢复都来自 owner 事实', nodes);
  }

  /* ---------------- 分享与本地收件箱（B 的 LifeSharingService；P 只转发） ------------- */

  /* 分享这一面也走同一个门：没连上就不假装有空收件箱。 */
  function shareGate() {
    const phase = store.conn.phase;
    if (phase === 'loading' || phase === 'offline' || phase === 'error') {
      return statePanel(phase === 'loading' ? 'loading' : 'offline',
        phase === 'loading' ? '还没读到服务状态' : '没有连上手机服务', store.conn.detail || '');
    }
    return null;
  }

  function shareStateChip(row) {
    const state = asString(row.state);
    const tone = state === 'LOCAL_DELIVERED' ? 'is-ok'
      : (state === 'DROPPED' || state === 'EXPIRED') ? 'is-warn'
        : state === 'DEFERRED' ? 'is-neutral' : 'is-neutral';
    return chip(shareStateText(state) + (row.stateDerived ? '（按时间推导）' : ''), tone);
  }

  /* 来源当前性：可读 / 换版 / 归档 / 拒读，四种说法都照实写。 */
  function shareSourceCurrencyChip(row) {
    const state = asString(row.sourceState);
    if (!state) return null;
    const text = {
      READABLE: '来源仍可读', REVISION_CHANGED: '来源已换版', ARCHIVED: '来源已归档',
      GOVERNED: '来源拒绝读取', NOT_FOUND: '来源不存在', UNAVAILABLE: '来源暂时读不到'
    }[state] || safeLabel(state, 24);
    const tone = state === 'READABLE' ? 'is-ok' : 'is-warn';
    return chip(text, tone);
  }

  async function loadShares() {
    store.data.shares = {phase: 'loading', doc: null, message: ''};
    renderAll();
    const res = await request('GET', '/shares', {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.shares = {phase: 'ready', doc: outcome.doc, message: ''};
    } else {
      store.data.shares = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status};
    }
    renderAll();
  }

  async function loadShare(shareId) {
    const path = sharePath(shareId);
    if (!path) return;
    store.data.share = {phase: 'loading', doc: null, message: '', shareId: shareId};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.share = {phase: 'ready', doc: outcome.doc, message: '', shareId: shareId};
    } else {
      store.data.share = {phase: outcome.status === 'offline' ? 'offline' : 'error', doc: null,
        message: outcome.message, http: res && res.status, shareId: shareId};
    }
    renderAll();
  }

  async function loadShareSources() {
    store.data.shareSources = {phase: 'loading', doc: null, message: ''};
    renderAll();
    const res = await request('GET', shareSourcesPath(), {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.shareSources = {phase: 'ready', doc: outcome.doc, message: ''};
    } else {
      store.data.shareSources = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status};
    }
    renderAll();
  }

  /* ---------------- 来访与共同时间（A 的 LocalContactInbox；P 只投递） ----------------

     读取只发 GET：/contact（计数 + 有界行 + B 的只读会话）、/contact/events/<ref>（正文只在这里）、
     /contact/operations/<key>（原键对账）。写入只有一处：POST /contact/events。
     没有定时器、没有轮询、没有第二次写入：界面永不替她回答。 */

  async function loadContact() {
    store.data.contact = {phase: 'loading', doc: null, message: ''};
    renderAll();
    const res = await request('GET', CONTACT_PATH, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.contact = {phase: 'ready', doc: outcome.doc, message: ''};
    } else {
      store.data.contact = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status};
    }
    renderAll();
  }

  async function loadContactEvent(eventRef) {
    const path = contactEventPath(eventRef);
    if (!path) return;
    store.data.contactEvent = {phase: 'loading', doc: null, message: '', eventRef: eventRef};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.contactEvent = {phase: 'ready', doc: outcome.doc, message: '', eventRef: eventRef};
    } else {
      store.data.contactEvent = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status, eventRef: eventRef};
    }
    renderAll();
  }

  /* ---------------- 共同实践（A 的 shared_practice_service；P 只投用户自己的意图） ----------------

     读取只发 GET：/shared-practice（列表，无正文）、/shared-practice/works/<id>（详情与双方贡献）、
     /shared-practice/operations/<key>（原键对账）。写入只有两处：POST /shared-practice（选材开一次）
     与 POST .../works/<id>/actions（用户自己的 submit / modify / pause / resume / end）。
     没有定时器、没有轮询；界面里没有一条会替她说话、替她接纳或起 Life 的路径。 */

  async function loadSharedPractices() {
    store.data.sharedPractices = {phase: 'loading', doc: null, message: ''};
    renderAll();
    const res = await request('GET', SHARED_PRACTICE_PATH, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.sharedPractices = {phase: 'ready', doc: outcome.doc, message: ''};
    } else {
      store.data.sharedPractices = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: outcome.doc || null, message: outcome.message, http: res && res.status};
    }
    renderAll();
  }

  async function loadSharedPractice(practiceId) {
    const path = sharedPracticeWorkPath(practiceId);
    if (!path) return;
    store.data.sharedPractice = {phase: 'loading', doc: null, message: '',
      practiceId: practiceId};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.sharedPractice = {phase: 'ready', doc: outcome.doc, message: '',
        practiceId: practiceId};
    } else {
      store.data.sharedPractice = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: outcome.doc || null, message: outcome.message, http: res && res.status,
        practiceId: practiceId};
    }
    renderAll();
  }

  function sharedPracticeGate() {
    const phase = store.conn.phase;
    if (phase === 'loading' || phase === 'offline' || phase === 'error') {
      return statePanel(phase === 'loading' ? 'loading' : 'offline',
        phase === 'loading' ? '还没读到服务状态' : '没有连上手机服务', store.conn.detail || '');
    }
    return null;
  }

  function sharedPracticeDraftFor(mode) {
    if (!store.sharedPracticeDrafts) store.sharedPracticeDrafts = Object.create(null);
    if (!store.sharedPracticeDrafts[mode]) {
      store.sharedPracticeDrafts[mode] = Object.assign({}, store.sharedPracticeDraft,
        {mode: mode, errors: []});
    }
    return store.sharedPracticeDrafts[mode];
  }

  function sharedPracticeErrors() {
    const drafts = store.sharedPracticeDrafts || {};
    const out = [];
    Object.keys(drafts).forEach(function (mode) {
      (drafts[mode].errors || []).forEach(function (message) { out.push(message); });
    });
    return out;
  }

  function openSharedPractice(practiceId) {
    go('shared-practice-work', {detail: practiceId});
  }

  /* 一个 scope = 一个操作键：同一次意图重试复用原键，终态后释放（与来访同一个 ops 账本）。
     写完只做一次重读（refreshAfter），没有定时器、没有无限轮询。 */
  async function submitSharedPracticeOpen() {
    const draft = sharedPracticeDraftFor('open');
    const guard = canWrite(store.conn);
    const checked = validateSharedPracticeDraft('open', draft);
    draft.errors = checked.errors;
    if (!checked.ok) {
      renderAll();
      return;
    }
    if (!guard.ok) {
      store.result = {scope: 'shared-practice:open', status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    if (store.busy !== null) return;
    const scope = 'shared-practice:open';
    const entry = ops.ensure(scope, {path: SHARED_PRACTICE_PATH, keyPrefix: 'practice_op:'});
    const built = buildSharedPracticeOpenPayload(Object.assign({}, draft, {operation_key: entry.key}));
    if (!built) {
      ops.resolve(scope);
      store.result = {scope: scope, status: 'blocked',
        message: '请求没有发出：材料、标题或第一轮还缺一项。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在开这一次共同实践…', key: entry.key};
    renderAll();
    const res = await request('POST', SHARED_PRACTICE_PATH, {csrf: store.conn.csrf, body: built});
    const outcome = deriveMutationOutcome(res);
    finishMutation(scope, outcome, '开这一次', sharedPracticeOutcomeLines('open', outcome.doc));
    if (outcome.status === 'applied' || outcome.status === 'already_applied') {
      const payload = outcome.doc && outcome.doc.data && typeof outcome.doc.data === 'object'
        ? outcome.doc.data : {};
      if (store.sharedPracticeDrafts) {
        store.sharedPracticeDrafts.open = Object.assign({}, store.sharedPracticeDraft,
          {mode: 'open', errors: []});
      }
      store.data.sharedPractices = null;
      const created = asString(payload.practice_id);
      if (created) openSharedPractice(created);
      else renderAll();
    }
  }

  async function submitSharedPracticeAction(view, action) {
    const draft = sharedPracticeDraftFor('action');
    const guard = canWrite(store.conn);
    const name = asString(action).trim().toLowerCase();
    if (SHARED_PRACTICE_ACTIONS.indexOf(name) < 0) {
      /* resume / reopen 是她的那一半：界面按名拒绝，不发给服务去试。 */
      store.result = {scope: 'shared-practice:action', status: 'blocked',
        message: '界面里没有「替她' + (SHARED_PRACTICE_SUBJECT_ONLY.indexOf(name) >= 0
          ? '重新取用' : '做这件事') + '」这条路径。'};
      renderAll();
      return;
    }
    /* CAS 的 head 就是这一次读到的那个：草稿里的 head 只是缓存，屏上的 view.head 才算数。 */
    const head = asString(view && view.head).trim() || asString(draft.head).trim();
    const checked = validateSharedPracticeDraft('action',
      Object.assign({}, draft, {action: name, expected_head: head}));
    draft.errors = checked.errors;
    if (!checked.ok) {
      renderAll();
      return;
    }
    if (!guard.ok) {
      store.result = {scope: 'shared-practice:action', status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    if (store.busy !== null) return;
    const path = sharedPracticeActionsPath(view.practiceId);
    if (!path) {
      store.result = {scope: 'shared-practice:action', status: 'blocked',
        message: '这一次的标识不可用：没有发出请求。'};
      renderAll();
      return;
    }
    const scope = 'shared-practice:' + name;
    const entry = ops.ensure(scope, {path: path, keyPrefix: 'practice_op:'});
    const built = buildSharedPracticeActionPayload(Object.assign({}, draft, {action: name,
      expected_head: head, operation_key: entry.key}));
    if (!built) {
      ops.resolve(scope);
      store.result = {scope: scope, status: 'blocked',
        message: '请求没有发出：还缺这一次读到的版本或正文。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在提交…', key: entry.key};
    renderAll();
    const res = await request('POST', path, {csrf: store.conn.csrf, body: built});
    const outcome = deriveMutationOutcome(res);
    finishMutation(scope, outcome, SHARED_PRACTICE_ACTION_TEXT[name] || name,
      sharedPracticeOutcomeLines(name, outcome.doc));
    if (outcome.status === 'applied' || outcome.status === 'already_applied') {
      if (store.sharedPracticeDrafts && store.sharedPracticeDrafts.action) {
        store.sharedPracticeDrafts.action.text = '';
        store.sharedPracticeDrafts.action.reason = '';
        store.sharedPracticeDrafts.action.errors = [];
      }
      store.data.sharedPractice = null;
      renderAll();
      if (store.sharedPracticeDetailId) loadSharedPractice(store.sharedPracticeDetailId);
    }
  }

  function shareListRow(row) {
    const inner = [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: row.title}),
        h('span', {class: 'list-meta', text: formatWhen(row.updatedAt)})
      ]),
      h('span', {class: 'list-chips'}, [
        shareStateChip(row),
        shareSourceCurrencyChip(row),
        row.delivered ? chip('本地收件箱', 'is-ok') : null,
        row.textWithheld ? chip('正文已扣留', 'is-warn') : null,
        row.textChars !== null ? chip('正文 ' + row.textChars + ' 字', 'is-mono') : null
      ]),
      h('span', {class: 'list-note', text: row.textWithheld
        ? '来源被拒绝读取，这一段正文不再投影（整份 JSON 都没有它）。'
        : '打开这条可以读正文、来源当前性与收据。'})
    ];
    if (!row.idOk) return h('li', {}, [h('div', {class: 'list-item is-static'}, inner)]);
    return h('li', {}, [h('button', {class: 'list-item', type: 'button',
      on: {click: function () { openShare(row.shareId); }}}, inner)]);
  }

  function openShare(shareId) {
    go('share', {detail: shareId});
  }

  function shareInboxPanel(view) {
    const inbox = view.inbox;
    const counts = view.counts;
    const rows = [];
    if (counts) {
      rows.push(h('div', {class: 'list-chips'}, [
        chip('草稿 ' + counts.draft, 'is-neutral'),
        chip('已延后 ' + counts.deferred, 'is-neutral'),
        chip('本地收件箱 ' + counts.localDelivered, 'is-ok'),
        chip('已放弃 ' + counts.dropped, 'is-warn'),
        chip('已过期 ' + counts.expired, 'is-warn')
      ]));
    }
    if (inbox) {
      rows.push(h('p', {class: 'world-panel-note', text:
        '收件箱：' + (inbox.audience || 'local:owner') + ' · 渠道 ' + (inbox.channel || 'local_inbox') +
        ' · 里面 ' + inbox.deliveredCount + ' 条。'}));
    }
    rows.push(h('p', {class: 'share-note', text: view.note || SHARE_LOCAL_NOTE}));
    rows.push(h('p', {class: 'footnote', text:
      '“本地收件箱”是这台实例自己的存档：没有发到任何外部渠道，也不代表用户已经读过。'}));
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '她的分享'})
    ].concat(rows));
  }

  function renderShares() {
    const gate = shareGate();
    if (gate) return page('分享', '草稿、延后、本地收件箱', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const entry = store.data.shares;
    if (!entry || entry.phase === 'loading') {
      return page('分享', '草稿、延后、本地收件箱', [result, statePanel('loading', '正在读取分享…')]);
    }
    if (entry.phase !== 'ready') {
      const offline = entry.phase === 'offline';
      return page('分享', '草稿、延后、本地收件箱', [
        result,
        statePanel(offline ? 'offline' : 'error', offline ? '没有连上分享' : '分享读不出来',
          entry.message, [btn('重试', {onClick: function () { loadShares(); }})])
      ]);
    }
    const view = shareListView(entry.doc);
    if (!view) {
      return page('分享', '草稿、延后、本地收件箱', [result,
        statePanel('error', '服务没有返回可读的分享列表', '界面不编一份出来。')]);
    }
    if (!view.ok) {
      return page('分享', '草稿、延后、本地收件箱', [result,
        statePanel('warn', '分享这次读不到', reasonText(view.reason) || '服务没有说明',
          [btn('重试', {onClick: function () { loadShares(); }})])]);
    }
    nodes.push(shareInboxPanel(view));
    if (view.withheldCount > 0) {
      nodes.push(statePanel('warn', '有 ' + view.withheldCount + ' 条正文被扣留',
        '这些草稿的来源被拒绝读取：列表与详情都不会带出那段正文。'));
    }
    if (!view.items.length) {
      nodes.push(statePanel('empty', '现在没有分享', '草稿、延后与本地收件箱都是空的；不写就什么都不发生。'));
    } else {
      nodes.push(h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '最近更新的在前'}),
        h('ul', {class: 'list'}, view.items.map(shareListRow))
      ]));
    }
    if (view.truncated) {
      nodes.push(h('p', {class: 'footnote', text: '列表有截断：服务只给了最近的一批。'}));
    }
    return page('分享', '草稿、延后、本地收件箱都只来自服务', nodes, [
      btn('写一条分享', {small: true, variant: 'mint', onClick: function () { go('share-new'); }}),
      btn('刷新', {small: true, icon: 'refresh', onClick: function () { loadShares(); }})
    ]);
  }

  function shareTextCard(view) {
    if (view.textWithheld) {
      return h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '这段话'}),
        statePanel('warn', '正文已扣留', '来源被拒绝读取（' +
          (reasonText(view.withheldReason) || view.withheldReason || '没有说明') +
          '）：这段正文不再投影，界面也不偷偷读旧内容。')
      ]);
    }
    if (!view.text) {
      return h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '这段话'}),
        h('p', {class: 'world-panel-note', text: view.textChars
          ? '服务报告这条有 ' + view.textChars + ' 字，但这次没有投影正文。'
          : '服务这次没有给出正文。'})
      ]);
    }
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '这段话'}),
      h('p', {class: 'share-text', text: view.text}),
      h('p', {class: 'world-panel-note', text: '这是她明确写下的那段短文（' +
        (view.textChars === null ? '长度未提供' : view.textChars + ' 字') + '），不是整篇私人材料。'})
    ]);
  }

  function shareSourceCard(view) {
    const source = view.source || {};
    const current = view.sourceCurrent;
    const rows = [];
    rows.push(kv('来源作品', safeLabel(source.artifact_id, 40) || '未提供', true));
    rows.push(kv('记录的版本', safeLabel(source.version_id, 32) || '未提供', true));
    if (current) {
      rows.push(kv('现在', {
        READABLE: '仍可读', REVISION_CHANGED: '已换版（发送会被拦住）', ARCHIVED: '已归档',
        GOVERNED: '拒绝读取', NOT_FOUND: '不存在', UNAVAILABLE: '读不到'
      }[current.state] || safeLabel(current.state, 24)));
      if (current.reason) rows.push(kv('来源原因', reasonText(current.reason) || current.reason, true));
    }
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '来源当前性'})
    ].concat(rows).concat([
      current && current.governed
        ? h('p', {class: 'world-panel-note', text: '来源被拒绝读取：这条草稿的正文不会出现在任何返回里。'})
        : null,
      current && current.stale && !current.governed
        ? h('p', {class: 'world-panel-note', text: '来源换版不算删除：正文照实给你看，但发送会被服务以 SOURCE_REVISION_CHANGED 拦住。'})
        : null
    ]));
  }

  function shareReceiptCard(view) {
    const receipt = view.receipt;
    if (!receipt) {
      return view.delivered
        ? h('div', {class: 'world-panel'}, [
          h('h2', {class: 'world-panel-title', text: '收据'}),
          h('p', {class: 'world-panel-note', text: '服务说这条已经投递，但这次没有给收据。'})
        ])
        : null;
    }
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '本地收据'}),
      h('div', {class: 'share-receipt'}, [
        kv('投递标识', receipt.deliveryId || '未提供', true),
        kv('时间', formatWhen(receipt.deliveredAt) || '未提供'),
        kv('渠道', receipt.channel || '未提供'),
        kv('受众', receipt.recipient || receipt.audience || '未提供'),
        kv('外发', receipt.externalSent ? '服务说发过外部渠道' : '没有外发', true),
        kv('外部送达确认', receipt.externalConfirmed ? '有' : '没有', true),
        kv('用户已读', receipt.readByAudience ? '服务说已读' : '没有证明', true)
      ]),
      h('p', {class: 'share-note', text: SHARE_LOCAL_NOTE}),
      h('p', {class: 'footnote', text: '沉默不等于已读；一条投递只有一条收据，重复发送不会多出一条。'})
    ]);
  }

  function shareActionCard(view) {
    const rows = [];
    rows.push(h('h2', {class: 'world-panel-title', text: '她现在能对它做什么'}));
    if (view.terminal) {
      rows.push(h('p', {class: 'world-panel-note', text:
        '这条已经结束（' + view.stateLabel + '），终态不重发：想再说一件事，就写一条新的分享。'}));
      return h('div', {class: 'world-panel'}, rows);
    }
    if (!view.sendable) {
      rows.push(h('p', {class: 'world-panel-note', text:
        '这个状态（' + view.stateLabel + '）没有可做的动作；界面不会替你重发。'}));
      return h('div', {class: 'world-panel'}, rows);
    }
    const noteInput = h('input', {class: 'input', type: 'text', maxlength: SHARE_CONDITION_MAX,
      autocomplete: 'off', placeholder: '延后的机会 / 放弃的理由（发送时不用）',
      value: store.shareActionNote || ''});
    noteInput.addEventListener('input', function () { store.shareActionNote = noteInput.value; });
    rows.push(h('div', {class: 'row-actions'}, [noteInput]));
    rows.push(h('div', {class: 'row-actions'}, [
      btn(SHARE_ACTION_TEXT.send_local, {
        small: true, variant: 'mint', disabled: store.busy !== null,
        onClick: function () { confirmShareAction(view, 'send_local'); }
      }),
      btn(SHARE_ACTION_TEXT.defer, {
        small: true, disabled: store.busy !== null,
        onClick: function () { submitShareAction(view, 'defer'); }
      }),
      btn(SHARE_ACTION_TEXT.drop, {
        small: true, disabled: store.busy !== null,
        onClick: function () { confirmShareAction(view, 'drop'); }
      })
    ]));
    rows.push(h('p', {class: 'footnote', text:
      '同一个动作复用同一个操作键；结果未知时保留原键，用「未确认的操作」里的查询对账，不换键重发。'}));
    return h('div', {class: 'world-panel'}, rows);
  }

  function renderShare() {
    const gate = shareGate();
    if (gate) return page('这条分享', '状态、来源与收据', [gate]);
    if (!store.shareDetailId) {
      return page('这条分享', '状态、来源与收据', [
        statePanel('empty', '没有选中哪一条分享', '从分享列表里点开一条。',
          [btn('回到分享', {onClick: function () { go('shares'); }})])
      ]);
    }
    const entry = store.data.share;
    const result = resultCard();
    if (!entry || entry.phase === 'loading') {
      return page('这条分享', '状态、来源与收据', [result, statePanel('loading', '正在读取这一条…')]);
    }
    if (entry.phase !== 'ready') {
      const offline = entry.phase === 'offline';
      return page('这条分享', '状态、来源与收据', [
        result,
        statePanel(offline ? 'offline' : 'error', offline ? '没有连上分享' : '这一条读不出来',
          entry.message, [btn('重试', {onClick: function () { loadShare(store.shareDetailId); }})])
      ]);
    }
    const view = shareDetailView(entry.doc);
    if (!view) {
      return page('这条分享', '状态、来源与收据', [result,
        statePanel('error', '服务没有返回这一条分享', '界面不编一份出来。')]);
    }
    if (!view.ok) {
      return page('这条分享', '状态、来源与收据', [result,
        statePanel('warn', '这一条读不出来', reasonText(view.reason) || '服务没有说明',
          [btn('重试', {onClick: function () { loadShare(store.shareDetailId); }})])]);
    }
    const nodes = [];
    if (result) nodes.push(result);
    nodes.push(h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: view.title}),
      h('div', {class: 'list-chips'}, [
        shareStateChip({state: view.state, stateDerived: view.stateDerived}),
        view.delivered ? chip('本地收件箱', 'is-ok') : null,
        chip(view.audience || 'local:owner', 'is-mono'),
        chip(view.channel || 'local_inbox', 'is-mono')
      ]),
      h('p', {class: 'world-panel-note', text: '更新于 ' + (formatWhen(view.updatedAt) || '未提供') +
        (view.expiresAt ? ' · 过期窗口 ' + formatWhen(view.expiresAt) : '')})
    ]));
    nodes.push(shareTextCard(view));
    nodes.push(shareSourceCard(view));
    if (view.deferred) {
      nodes.push(h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '还在等什么'}),
        kv('条件', view.deferred.condition || '未提供'),
        kv('机会', view.deferred.opportunityRef || '未提供'),
        kv('延后于', formatWhen(view.deferred.at) || '未提供'),
        h('p', {class: 'world-panel-note', text: '延后不会自己重考：机会到了没有，是她自己的处境判断。'})
      ]));
    }
    if (view.dropped) {
      nodes.push(h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '已放弃'}),
        kv('理由', view.dropped.reason || '没有写理由'),
        kv('时间', formatWhen(view.dropped.at) || '未提供')
      ]));
    }
    if (view.expired) {
      nodes.push(h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '已过期'}),
        kv('理由', view.expired.reason || '没有写理由'),
        kv('时间', formatWhen(view.expired.at) || '未提供')
      ]));
    }
    const receipt = shareReceiptCard(view);
    if (receipt) nodes.push(receipt);
    nodes.push(shareActionCard(view));
    nodes.push(h('p', {class: 'share-note', text: view.note || SHARE_LOCAL_NOTE}));
    return page('这条分享', '状态、来源当前性与收据', nodes, [
      btn('刷新', {small: true, icon: 'refresh', onClick: function () { loadShare(store.shareDetailId); }}),
      btn('回分享列表', {small: true, onClick: function () { go('shares'); }})
    ]);
  }

  function shareSourceOption(row) {
    const id = protocolId(row.artifactId, 200);
    if (!id.ok || id.value !== row.artifactId) return null;
    const option = h('option', {value: id.value,
      text: row.title + '（' + row.kindLabel + (row.readable ? '' : ' · 现在读不到') + '）'});
    return option;
  }

  function renderShareNew() {
    const gate = shareGate();
    if (gate) return page('写一条分享', '只能选现在真的读得到的来源', [gate]);
    const draft = store.shareDraft;
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const sourcesEntry = store.data.shareSources;
    if (!sourcesEntry || sourcesEntry.phase === 'loading') {
      return page('写一条分享', '只能选现在真的读得到的来源',
        [result, statePanel('loading', '正在读取她现在的来源…')]);
    }
    if (sourcesEntry.phase !== 'ready') {
      const offline = sourcesEntry.phase === 'offline';
      return page('写一条分享', '只能选现在真的读得到的来源', [
        result,
        statePanel(offline ? 'offline' : 'error', offline ? '没有连上分享' : '来源读不出来',
          sourcesEntry.message, [btn('重试', {onClick: function () { loadShareSources(); }})])
      ]);
    }
    const sources = shareSourcesView(sourcesEntry.doc);
    const usable = sources ? sources.items.filter(function (row) { return row.idOk && row.readable; }) : [];
    const unreadable = sources ? sources.items.filter(function (row) { return row.idOk && !row.readable; }) : [];
    if (!usable.length) {
      return page('写一条分享', '只能选现在真的读得到的来源', [result,
        statePanel('empty', '现在没有可选的来源',
          '分享的来源必须是这台实例现在真的读得到的作品或阅读材料；这里不会让你手填路径或地址。',
          [btn('刷新来源', {onClick: function () { loadShareSources(); }})])]);
    }
    if (!draft.source_artifact_id && usable.length) draft.source_artifact_id = usable[0].artifactId;
    const select = h('select', {class: 'world-select', 'aria-label': '分享来源'},
      usable.map(function (row) {
        return h('option', {value: row.artifactId, text: row.title + '（' + row.kindLabel + '）'});
      }));
    select.value = draft.source_artifact_id;
    select.addEventListener('change', function () { draft.source_artifact_id = select.value; });
    const textArea = h('textarea', {class: 'textarea', rows: 4, maxlength: SHARE_TEXT_MAX,
      placeholder: '写下这一段要给她看的话（明确的一段短文，不是整篇材料）', text: draft.share_text});
    textArea.addEventListener('input', function () { draft.share_text = textArea.value; });
    const excerptInput = h('input', {class: 'input', type: 'number', min: 1, max: SHARE_EXCERPT_MAX,
      placeholder: '或：来源正文前 N 个字符（1–' + SHARE_EXCERPT_MAX + '）',
      value: draft.excerpt_chars});
    excerptInput.addEventListener('input', function () { draft.excerpt_chars = excerptInput.value; });
    const titleInput = h('input', {class: 'input', type: 'text', maxlength: SHARE_TITLE_MAX,
      placeholder: '标题（可选，缺省用来源标题）', value: draft.title});
    titleInput.addEventListener('input', function () { draft.title = titleInput.value; });
    const expiresInput = h('input', {class: 'input', type: 'number',
      placeholder: '过期窗口秒数（可选，' + SHARE_EXPIRY_MIN + '–' + SHARE_EXPIRY_MAX + '）',
      value: draft.expires_after_s});
    expiresInput.addEventListener('input', function () { draft.expires_after_s = expiresInput.value; });

    nodes.push(h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '来源'}),
      h('div', {class: 'row-actions'}, [select]),
      h('p', {class: 'world-panel-note', text: '只能选服务这次列出来的、现在真的读得到的作品或阅读材料；手填的路径、数据库或地址会被拒绝。'})
    ]));
    nodes.push(h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '这段话'}),
      textArea,
      h('div', {class: 'row-actions'}, [excerptInput]),
      h('p', {class: 'world-panel-note', text: '短文与前缀长度只能选一个：服务不会默认把整篇私人材料当成分享文本。'}),
      titleInput,
      expiresInput
    ]));
    if (draft.errors && draft.errors.length) {
      nodes.push(h('ul', {class: 'errors'}, draft.errors.map(function (message) {
        return h('li', {text: message});
      })));
    }
    if (unreadable.length) {
      nodes.push(h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '现在读不到的（不能选）'}),
        h('ul', {class: 'list'}, unreadable.map(function (row) {
          return h('li', {}, [h('div', {class: 'list-item is-static'}, [
            h('span', {class: 'list-top'}, [h('span', {class: 'list-title', text: row.title})]),
            h('span', {class: 'list-chips'}, [chip(row.reason ? (reasonText(row.reason) || row.reason) : '读不到', 'is-warn')])
          ])]);
        }))
      ]));
    }
    nodes.push(h('p', {class: 'share-note', text: SHARE_LOCAL_NOTE}));
    nodes.push(h('p', {class: 'footnote', text: '形成的是草稿，不是已发出；真正进本地收件箱要她再显式点一次。'}));
    return page('写一条分享', '只能选现在真的读得到的来源', nodes, [
      btn('形成草稿', {small: true, variant: 'mint', disabled: store.busy !== null,
        onClick: function () { submitSharePrepare(); }}),
      btn('取消', {small: true, onClick: function () { go('shares'); }})
    ]);
  }

  /* ---------------- 来访与共同时间：屏 ----------------

     这一屏只做三件事：投一条普通来话、投一次带明确范围的邀请、显式结束当前邀请。
     所有状态都来自服务真实回读（PENDING 就是还没被处理）；界面永不写「她接受了」。 */

  /* 三种意图各有自己的草稿槽：同一屏上普通来话与明确邀请同时可见，
     共用一份草稿会让先写的字被后渲染的清掉（真实缺陷，回归里已固定）。 */
  function emptyContactDraft(kind) {
    return {kind: kind, text: '', scope: '', expires_after_s: '', duration_seconds: '',
      invitation_id: '', reason: '', errors: []};
  }

  function contactDraftFor(kind) {
    if (!store.contactDrafts) store.contactDrafts = Object.create(null);
    if (!store.contactDrafts[kind]) store.contactDrafts[kind] = emptyContactDraft(kind);
    return store.contactDrafts[kind];
  }

  function contactErrors() {
    const drafts = store.contactDrafts || {};
    const out = [];
    Object.keys(drafts).forEach(function (kind) {
      (drafts[kind].errors || []).forEach(function (message) { out.push(message); });
    });
    return out;
  }

  function contactGate() {
    const phase = store.conn.phase;
    if (phase === 'loading' || phase === 'offline' || phase === 'error') {
      return statePanel(phase === 'loading' ? 'loading' : 'offline',
        phase === 'loading' ? '还没读到服务状态' : '没有连上手机服务', store.conn.detail || '');
    }
    return null;
  }

  function contactStateChip(row) {
    const tone = row.state === 'PROCESSED' ? 'is-ok'
      : row.state === 'UNKNOWN' ? 'is-warn'
        : row.state === 'CLAIMED' ? 'is-neutral' : 'is-neutral';
    return chip(row.stateLabel, tone);
  }

  function contactSessionPanel(view) {
    const session = view.session;
    const rows = [];
    if (!session) {
      rows.push(h('p', {class: 'world-panel-note', text: '服务没有给共同时间的会话记录。'}));
    } else {
      rows.push(h('div', {class: 'list-chips'}, [
        chip(session.stateLabel, session.open ? 'is-ok' : 'is-neutral'),
        session.invitationId ? chip('邀请 ' + safeLabel(session.invitationId, 40), 'is-mono') : null
      ]));
      if (session.scope) rows.push(kv('范围', session.scope));
      if (session.openedAt) rows.push(kv('开始', formatWhen(session.openedAt) || session.openedAt));
      if (session.endedAt) rows.push(kv('结束', formatWhen(session.endedAt) || session.endedAt));
      if (session.endReason) rows.push(kv('结束原因', reasonText(session.endReason) || session.endReason));
      if (session.jointActivityId) rows.push(kv('共同活动', safeLabel(session.jointActivityId, 60), true));
      if (session.detail) rows.push(kv('说明', session.detail));
      if (!session.open) {
        rows.push(h('p', {class: 'world-panel-note', text:
          '没有开着的共同时间：这不代表她拒绝过什么，只代表现在没有。'}));
      }
    }
    rows.push(h('p', {class: 'footnote', text:
      '会话记录是她的共同时间协议自己写的（B 的 co_presence.json），这里只读几个标签。'}));
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '共同时间'})
    ].concat(rows));
  }

  function contactRow(row) {
    const inner = [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: row.kindLabel}),
        h('span', {class: 'list-meta', text: formatWhen(row.occurredAt) || '时间未提供'})
      ]),
      h('span', {class: 'list-chips'}, [
        contactStateChip(row),
        chip(row.effectLabel, 'is-neutral'),
        row.scope ? chip('范围：' + row.scope, 'is-neutral') : null,
        row.bodyChars ? chip('正文 ' + row.bodyChars + ' 字', 'is-mono') : null,
        row.expired ? chip('已过期', 'is-warn') : null,
        row.outcomeLabel ? chip(row.outcomeLabel, row.outcome === 'ACCEPTED' ? 'is-ok' : 'is-neutral') : null,
        row.deferCondition ? chip('等：' + row.deferCondition, 'is-neutral') : null
      ]),
      h('span', {class: 'list-note', text: row.state === 'PENDING'
        ? '已记下、还没处理：还没人读过这条，更不是她答应了。'
        : (row.state === 'UNKNOWN'
          ? '她的答复未知：按原键对账，不盲目重投。'
          : '打开这一条可以看正文与她的处理结果。')})
    ];
    if (!row.idOk) return h('li', {}, [h('div', {class: 'list-item is-static'}, inner)]);
    return h('li', {}, [h('button', {class: 'list-item', type: 'button',
      on: {click: function () { openContactEvent(row.eventRef); }}}, inner)]);
  }

  function openContactEvent(eventRef) {
    go('contact-event', {detail: eventRef});
  }

  /* 两条投递入口：普通来话与明确邀请。两条都不写她的答案。 */
  function contactComposer(view) {
    const guard = canWrite(store.conn);
    const messageDraft = contactDraftFor('LOCAL_MESSAGE');
    const messageArea = h('textarea', {class: 'textarea', rows: 3, maxlength: CONTACT_TEXT_MAX,
      placeholder: '写一句普通来话（她只会「看到」，不会被当成邀请）', text: messageDraft.text});
    messageArea.addEventListener('input', function () { messageDraft.text = messageArea.value; });
    const inviteDraft = contactDraftFor('CO_PRESENCE_INVITATION');
    const inviteScope = h('input', {class: 'input', type: 'text', maxlength: CONTACT_SCOPE_MAX,
      placeholder: '一起做什么（必填，例如：一起坐十分钟）', value: inviteDraft.scope});
    inviteScope.addEventListener('input', function () { inviteDraft.scope = inviteScope.value; });
    const inviteText = h('textarea', {class: 'textarea', rows: 2, maxlength: CONTACT_TEXT_MAX,
      placeholder: '邀请时想说的话（可选）', text: inviteDraft.text});
    inviteText.addEventListener('input', function () { inviteDraft.text = inviteText.value; });
    const inviteExpiry = h('input', {class: 'input', type: 'number',
      min: CONTACT_EXPIRY_MIN, max: CONTACT_EXPIRY_MAX,
      placeholder: '有效秒数（可选，' + CONTACT_EXPIRY_MIN + '–' + CONTACT_EXPIRY_MAX + '）',
      value: inviteDraft.expires_after_s});
    inviteExpiry.addEventListener('input', function () { inviteDraft.expires_after_s = inviteExpiry.value; });
    const nodes = [
      h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '一条普通来话'}),
        messageArea,
        h('div', {class: 'row-actions'}, [
          btn('投一条普通来话', {small: true, variant: 'mint',
            disabled: !guard.ok || store.busy !== null,
            onClick: function () { submitContactKind('LOCAL_MESSAGE'); }})
        ]),
        h('p', {class: 'world-panel-note', text:
          '普通来话只被「看到」（OBSERVATION_ONLY）：它不会让共同时间开始，也不会替她做任何决定。'})
      ]),
      h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '一次明确邀请'}),
        field('范围（必填）', inviteScope, '范围是邀请的全部分量：写清一起做什么，她才能自己决定。'),
        inviteText,
        inviteExpiry,
        h('div', {class: 'row-actions'}, [
          btn('投一次明确邀请', {small: true,
            disabled: !guard.ok || store.busy !== null,
            onClick: function () { submitContactKind('CO_PRESENCE_INVITATION'); }})
        ]),
        h('p', {class: 'world-panel-note', text:
          '邀请只是投进她的收件箱：接不接受由她自己的协议决定，这一屏只显示「已记下」。'})
      ])
    ];
    const problems = contactErrors();
    if (problems.length) {
      nodes.push(h('ul', {class: 'errors'}, problems.map(function (message) {
        return h('li', {text: message});
      })));
    }
    if (!guard.ok) {
      nodes.push(statePanel('readonly', '只读：这些消息不会发出', guard.message));
    }
    return nodes;
  }

  /* 显式结束当前邀请：只有真的有一个开着的邀请/邀请事件时才给这个入口。 */
  function contactEndComposer(view) {
    const guard = canWrite(store.conn);
    const session = view.session;
    const candidates = [];
    if (session && session.invitationId) {
      candidates.push({invitationId: session.invitationId, label: '现在开着的共同时间'});
    }
    (view.items || []).forEach(function (row) {
      if (row.kind !== 'CO_PRESENCE_INVITATION' || !row.invitationId) return;
      if (candidates.some(function (item) { return item.invitationId === row.invitationId; })) return;
      candidates.push({invitationId: row.invitationId, label: row.kindLabel + ' · ' + row.refLabel});
    });
    if (!candidates.length) {
      return statePanel('empty', '现在没有可以结束的邀请',
        '结束只针对已经投出去的邀请；这里没有邀请时，就不给一个假的结束按钮。');
    }
    const draft = contactDraftFor('CO_PRESENCE_END');
    const known = candidates.some(function (item) { return item.invitationId === draft.invitation_id; });
    if (!draft.invitation_id || !known) draft.invitation_id = candidates[0].invitationId;
    const select = h('select', {class: 'world-select', 'aria-label': '要结束哪一次邀请'},
      candidates.map(function (item) {
        return h('option', {value: item.invitationId, text: item.label});
      }));
    select.value = draft.invitation_id;
    select.addEventListener('change', function () { draft.invitation_id = select.value; });
    const reasonInput = h('input', {class: 'input', type: 'text', maxlength: 64,
      placeholder: '结束理由（可选，最多 64 字）', value: draft.reason});
    reasonInput.addEventListener('input', function () { draft.reason = reasonInput.value; });
    return h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '显式结束这次邀请'}),
      h('div', {class: 'row-actions'}, [select]),
      reasonInput,
      h('div', {class: 'row-actions'}, [
        btn('结束这次邀请', {small: true, disabled: !guard.ok || store.busy !== null,
          onClick: function () { submitContactKind('CO_PRESENCE_END'); }})
      ]),
      h('p', {class: 'world-panel-note', text:
        '结束是一次显式的投递：它不会偷偷替她「关掉」什么，也不代表她已经同意过这次共处。'})
    ]);
  }

  function renderContact() {
    const gate = contactGate();
    if (gate) return page('来访与共同时间', '普通来话、明确邀请、显式结束', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const entry = store.data.contact;
    if (!entry || entry.phase === 'loading') {
      return page('来访与共同时间', '普通来话、明确邀请、显式结束',
        [result, statePanel('loading', '正在读取她的来信箱…')]);
    }
    if (entry.phase !== 'ready') {
      const offline = entry.phase === 'offline';
      return page('来访与共同时间', '普通来话、明确邀请、显式结束', [
        result,
        statePanel(offline ? 'offline' : 'error', offline ? '没有连上来信箱' : '来信箱读不出来',
          entry.message, [btn('重试', {onClick: function () { loadContact(); }})])
      ]);
    }
    const view = contactListView(entry.doc);
    if (!view) {
      return page('来访与共同时间', '普通来话、明确邀请、显式结束', [result,
        statePanel('error', '服务没有返回可读的来信箱', '界面不编一份出来。')]);
    }
    if (!view.ok) {
      /* 账本坏了 / 读不出来：照实说是「不知道」，不是「没有来信」。 */
      return page('来访与共同时间', '普通来话、明确邀请、显式结束', [result,
        statePanel('warn', '来信箱这次读不到', reasonText(view.reason) || '服务没有说明',
          [btn('重试', {onClick: function () { loadContact(); }})])]);
    }
    nodes.push(contactSessionPanel(view));
    if (view.counts) {
      nodes.push(h('div', {class: 'list-chips'}, [
        chip('已记下 ' + view.counts.pending, view.counts.pending ? 'is-neutral' : 'is-ok'),
        chip('她正在处理 ' + view.counts.claimed, 'is-neutral'),
        chip('延后 ' + view.counts.deferred, view.counts.deferred ? 'is-warn' : 'is-neutral'),
        chip('未知 ' + view.counts.unknown, view.counts.unknown ? 'is-warn' : 'is-neutral'),
        chip('已处理 ' + view.counts.processed, 'is-neutral'),
        chip('容量 ' + view.eventCount + '/' + view.capacity, 'is-mono')
      ]));
    }
    if (view.ledgerState && view.ledgerState !== 'OK') {
      nodes.push(statePanel('warn', '来信箱账本状态：' + safeLabel(view.ledgerState, 20),
        '账本不存在时读数是空的，不是「她从来没收到过」。'));
    }
    if (!view.items.length) {
      nodes.push(statePanel('empty', '来信箱是空的', '还没有投过任何来话；不投就什么都不发生。'));
    } else {
      nodes.push(h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '最近的在下面'}),
        h('ul', {class: 'list'}, view.items.map(contactRow))
      ]));
    }
    if (view.truncated) {
      nodes.push(h('p', {class: 'footnote', text: '列表有截断：服务只给了最近的一批。'}));
    }
    nodes.push(h('p', {class: 'world-panel-note', text: view.note || CONTACT_LOCAL_NOTE}));
    nodes.push(h('p', {class: 'footnote', text: view.queuedNote || CONTACT_QUEUED_NOTE}));
    nodes.push(contactEndComposer(view));
    contactComposer(view).forEach(function (node) { nodes.push(node); });
    return page('来访与共同时间', '只投递；接不接受由她自己决定', nodes, [
      btn('刷新', {small: true, icon: 'refresh', onClick: function () { loadContact(); }})
    ]);
  }

  function renderContactEvent() {
    const gate = contactGate();
    if (gate) return page('这次来话', '正文与她的处理结果', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const entry = store.data.contactEvent;
    const ref = store.contactDetailRef;
    if (!ref) {
      return page('这次来话', '正文与她的处理结果', [result,
        statePanel('empty', '没有选中任何一条来话', '从「来访与共同时间」打开一条。')]);
    }
    if (!entry || entry.phase === 'loading') {
      return page('这次来话', '正文与她的处理结果',
        [result, statePanel('loading', '正在读取这一条…')]);
    }
    if (entry.phase !== 'ready') {
      const offline = entry.phase === 'offline';
      return page('这次来话', '正文与她的处理结果', [
        result,
        statePanel(offline ? 'offline' : 'error', offline ? '没有连上来信箱' : '这一条读不出来',
          entry.message, [btn('重试', {onClick: function () { loadContactEvent(ref); }})])
      ]);
    }
    const view = contactEventView(entry.doc);
    if (!view) {
      return page('这次来话', '正文与她的处理结果', [result,
        statePanel('error', '服务没有返回可读的这一条', '界面不编一份出来。')]);
    }
    if (!view.ok) {
      return page('这次来话', '正文与她的处理结果', [result,
        statePanel('warn', '这一条读不到', reasonText(view.reason) || '服务没有说明',
          [btn('重试', {onClick: function () { loadContactEvent(ref); }})])]);
    }
    nodes.push(h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: view.kindLabel}),
      h('div', {class: 'list-chips'}, [
        chip(view.stateLabel, view.state === 'PROCESSED' ? 'is-ok' : 'is-neutral'),
        chip(view.effectLabel, 'is-neutral'),
        view.scope ? chip('范围：' + view.scope, 'is-neutral') : null,
        view.expired ? chip('已过期', 'is-warn') : null,
        view.invitationBound ? chip('已绑定邀请', 'is-ok') : null
      ]),
      kv('事件', view.refLabel, true),
      kv('记下于', formatWhen(view.receivedAt) || view.receivedAt || '未提供'),
      kv('发生时刻', formatWhen(view.occurredAt) || view.occurredAt || '未提供'),
      view.expiresAt ? kv('有效期到', formatWhen(view.expiresAt) || view.expiresAt) : null,
      view.invitationId ? kv('邀请', safeLabel(view.invitationId, 60), true) : null,
      kv('正文', view.bodyIncluded && view.body ? view.body : (view.bodyChars
        ? '服务这次没有投影正文（' + view.bodyChars + ' 字存在）' : '没有正文')),
      h('p', {class: 'footnote', text: view.bodyIncluded
        ? '正文只在这一次详情里投影；列表与摘要从不带正文。'
        : '这条这次没有带出正文：界面不拿旧内容顶上。'})
    ]));
    if (view.resultLines) {
      nodes.push(h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '她的处理结果'}),
        h('div', {class: 'list-chips'}, [
          chip(view.resultLines.outcomeLabel || '结果未记录',
            view.resultLines.outcome === 'ACCEPTED' ? 'is-ok'
              : view.resultLines.outcome === 'UNKNOWN' ? 'is-warn' : 'is-neutral'),
          view.resultLines.terminal ? chip('终态', 'is-neutral') : null,
          view.resultLines.retryable ? chip('可重试', 'is-warn') : null,
          view.resultLines.consumer ? chip('由 ' + view.resultLines.consumer + ' 处理', 'is-mono') : null
        ]),
        h('ul', {class: 'list'}, (view.resultLines.lines || []).map(function (line) {
          return h('li', {}, [h('div', {class: 'list-item is-static'}, [
            h('span', {class: 'list-text is-full', text: line})
          ])]);
        }))
      ]));
      if (view.resultLines.outcome === 'DEFERRED') {
        nodes.push(h('p', {class: 'footnote', text:
          '延后有明确条件：不会每个 tick 再问她一次，等那个机会真的发生才回来。'}));
      }
    } else if (view.state === 'PENDING') {
      nodes.push(statePanel('warn', '还没被处理（queued）',
        '这一条已经记下，但她的生活链还没读过它：这不是她答应了，也不是共同时间已经开始。'));
    } else {
      nodes.push(statePanel('empty', '还没有处理结果', '服务这次没有记录结果。'));
    }
    nodes.push(h('p', {class: 'world-panel-note', text: view.note || CONTACT_LOCAL_NOTE}));
    return page('这次来话', '正文与她的处理结果', nodes, [
      btn('回来访列表', {small: true, onClick: function () { go('contact'); }}),
      btn('刷新这一条', {small: true, icon: 'refresh',
        onClick: function () { loadContactEvent(ref); }})
    ]);
  }

  /* 投递结果的几句话：queued 就是 queued，绝不写成「她接受了」。 */
  function contactOutcomeLines(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!data) return [];
    const lines = [];
    const event = asRecord(data.event);
    const kind = asString(data.event_kind) || (event ? asString(event.event_kind) : '');
    if (kind) lines.push('这次投的是：' + (CONTACT_KIND_TEXT[kind] || safeLabel(kind, 24)) + '。');
    const state = asString(data.processing_state) || (event ? asString(event.processing_state) : '');
    if (state) lines.push('服务说它现在是：' + (CONTACT_STATE_TEXT[state] || safeLabel(state, 24)) + '。');
    if (data.accepted === true) {
      /* 这一层从不给 accepted=true；真出现了也只能照实说这不是界面的结论。 */
      lines.push('服务报告 accepted=true：这不是界面替她做的判断。');
    } else {
      lines.push('界面没有替她接纳：接不接受由她自己的选择决定。');
    }
    if (data.pause_requested === true || data.activity_started === true) {
      lines.push('服务报告这次动过活动：照实显示，界面没有发起它。');
    }
    lines.push(CONTACT_LOCAL_NOTE + '。');
    return lines;
  }

  /* 一个 intent = 一个操作键：同一个 scope 重试复用原键，终态后释放。 */
  async function submitContactKind(kind) {
    const draft = contactDraftFor(kind);
    const guard = canWrite(store.conn);
    const checked = validateContactDraft(kind, draft);
    draft.errors = checked.errors;
    if (!checked.ok) {
      renderAll();
      return;
    }
    if (!guard.ok) {
      store.result = {scope: 'contact:send', status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    if (store.busy !== null) return;
    const scope = 'contact:send';
    const entry = ops.ensure(scope, {path: CONTACT_EVENTS_PATH, keyPrefix: 'contact_op:'});
    const built = buildContactPayload(Object.assign({}, draft, {event_kind: kind,
      operation_key: entry.key}));
    if (!built) {
      ops.resolve(scope);
      store.result = {scope: scope, status: 'blocked', message: '请求没有发出：这条来话不合法。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在投递…', key: entry.key};
    renderAll();
    const res = await request('POST', CONTACT_EVENTS_PATH,
      {csrf: store.conn.csrf, body: built});
    const outcome = deriveMutationOutcome(res);
    finishMutation(scope, outcome, '投递', contactOutcomeLines(outcome.doc));
    if (outcome.status === 'applied' || outcome.status === 'already_applied') {
      // 成功后只清掉这一种意图的草稿，别顺手清掉另一种正在写的字。
      if (store.contactDrafts) store.contactDrafts[kind] = emptyContactDraft(kind);
      renderAll();
    }
  }

  /* 原键只读对账：/contact/operations/<key>，绝不换键重投。 */
  async function queryContactOperation(scope, key) {
    const path = contactOperationPath(key);
    if (!path) return;
    store.result = {scope: scope, status: 'pending', message: '正在用原键对账…', key: key};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status !== 'ok') {
      store.result = {scope: scope, status: 'unknown',
        message: '对账没有成功：' + outcome.message + '。结果仍然未知，原键保留。', key: key};
      renderAll();
      return;
    }
    const view = contactOperationView(outcome.doc);
    const status = view ? view.status : '';
    const lines = [];
    if (view && view.operationKey) lines.push('原键：' + view.operationKey);
    if (view && view.eventState) {
      lines.push('事件状态：' + (CONTACT_STATE_TEXT[view.eventState] || safeLabel(view.eventState, 24)));
    }
    if (view && view.outcome) {
      lines.push('结果：' + (CONTACT_OUTCOME_TEXT[view.outcome] || safeLabel(view.outcome, 24)));
    }
    if (status === 'absent') lines.push('这个键从没落地：可以用同一个键重试。');
    if (status === 'unknown') {
      lines.push('落地了但她给的结果是 UNKNOWN：按同一个键和 owner 对账，不盲目重做。');
    }
    if (status === 'unreadable') lines.push('账本读不出来：答案是「不知道」，不是「没落地」。');
    if (status === 'applied') lines.push('这个键已经落地；不改写、不重投。');
    const settled = status === 'applied' || status === 'absent';
    if (settled) ops.resolve(scope);
    store.result = {
      scope: scope,
      status: status === 'applied' ? 'applied' : (status === 'absent' ? 'rejected' : 'unknown'),
      message: '服务对这条键的答复：' + (status || '未说明') + '。',
      key: key,
      lines: lines
    };
    if (settled) refreshAfter(scope);
    renderAll();
  }

  /* ---------------- 共同实践：两屏 ----------------

     第一屏是「现在有哪些共同实践 + 选一份共享材料开一次」；第二屏是「这一次的材料、双方贡献、
     作品版本、暂停 / 重新取用 / 显式结束」。
     铁律：界面不列私人材料全集（只列 owner 说有共享资格的来源），不显示私人正文，
     不替她写贡献、不替她接纳、不起 Life。 */

  function sharedPracticeRow(row) {
    const inner = [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: row.title}),
        h('span', {class: 'list-meta', text: formatWhen(row.updatedAt) || '时间未提供'})
      ]),
      h('span', {class: 'list-chips'}, [
        chip(row.stateText, row.state === 'ENDED' || row.state === 'CLOSED'
          ? 'is-neutral' : (row.state === 'PAUSED' ? 'is-warn' : 'is-ok')),
        row.userRounds !== null ? chip('你 ' + row.userRounds + ' 次', 'is-neutral') : null,
        row.subjectRounds !== null ? chip('她 ' + row.subjectRounds + ' 次', 'is-neutral') : null,
        row.userRounds === null && row.contributionsTotal !== null
          ? chip('来回 ' + row.contributionsTotal + ' 条', 'is-neutral') : null,
        row.sourceTitle ? chip('材料：' + row.sourceTitle, 'is-neutral') : null,
        row.sourceReadable ? null : chip('材料现在读不到', 'is-warn')
      ]),
      h('span', {class: 'list-note', text: row.open
        ? '打开这一次可以看材料、双方贡献与作品版本。'
        : '这一次已经结束；打开仍然能读到当时的作品与贡献。'})
    ];
    if (!row.idOk) return h('li', {}, [h('div', {class: 'list-item is-static'}, inner)]);
    return h('li', {}, [h('button', {class: 'list-item', type: 'button',
      on: {click: function () { openSharedPractice(row.practiceId); }}}, inner)]);
  }

  /* 选材：只有 owner 这次真的列出来的来源才可选（B 的 /shares/sources），
     界面上不出现私人材料全集，也不手填路径 / 数据库 / 地址。 */
  function chooseSharedPracticeSource(row) {
    const draft = sharedPracticeDraftFor('open');
    draft.source_artifact_id = row.artifactId;
    draft.source_version_id = row.versionId;
    if (!draft.title) draft.title = row.title === '（没有标题）' ? '' : row.title;
    draft.errors = [];
    renderAll();
  }

  function sharedPracticeSourcePicker(draft) {
    const entry = store.data.shareSources;
    if (!entry || entry.phase === 'loading') {
      return [statePanel('loading', '正在读取可共享的来源…')];
    }
    const view = entry.phase === 'ready' ? shareSourcesView(entry.doc) : null;
    if (!view || !view.ok) {
      return [statePanel(entry.phase === 'offline' ? 'offline' : 'unavailable',
        '这次读不到可共享的来源', entry.message || '服务没有说明',
        [btn('重新读取来源', {onClick: function () { loadShareSources(); }})])];
    }
    const usable = view.items.filter(function (row) {
      return row.idOk && row.readable && row.versionId;
    });
    if (!usable.length) {
      return [statePanel('empty', '现在没有可共享的来源',
        '服务列出的来源这次都读不到（或有来源已经换版）；界面不替你列私人材料全集。')];
    }
    return [h('ul', {class: 'list'}, usable.map(function (row) {
      const chosen = draft.source_artifact_id === row.artifactId &&
        draft.source_version_id === row.versionId;
      return h('li', {}, [h('button', {class: 'list-item', type: 'button',
        on: {click: function () { chooseSharedPracticeSource(row); }}}, [
        h('span', {class: 'list-top'}, [
          h('span', {class: 'list-title', text: row.title}),
          h('span', {class: 'list-meta', text: chosen ? '已选这份' : row.kindLabel})
        ]),
        h('span', {class: 'list-note', text: chosen
          ? '材料与版本都取这一次读到的（版本 ' + safeLabel(row.versionId, 40) + '）'
          : '点一下选这份材料；你读到的是它对外的版本 ' + safeLabel(row.versionId, 24)})
      ])]);
    }))];
  }

  function sharedPracticeOpenComposer(view) {
    const guard = canWrite(store.conn);
    const draft = sharedPracticeDraftFor('open');
    const nodes = [];
    if (!view.service || !view.service.bound) {
      /* owner 没接上：照实说，不摆一个假的选材框。 */
      nodes.push(statePanel('unavailable', '共同实践还没接上 owner',
        view.reason ? (reasonText(view.reason) || view.reason) : '服务没有报告这个 owner。',
        [btn('重新读取', {onClick: function () { loadSharedPractices(); }})]));
      nodes.push(h('p', {class: 'world-panel-note', text:
        '这一屏只消费 /api/phone/shared-practice。owner 没发布接口时，界面不替你编一份共同实践，'
        + '也不新造聊天库或作品库。'}));
      return nodes;
    }
    const check = validateSharedPracticeDraft('open', draft);
    const titleInput = h('input', {class: 'input', type: 'text',
      maxlength: SHARED_PRACTICE_TITLE_MAX, placeholder: '这一次共同实践叫什么',
      value: draft.title,
      on: {input: function (event) { draft.title = event.target.value; }}});
    const openingArea = h('textarea', {class: 'textarea', rows: 3,
      maxlength: SHARED_PRACTICE_TEXT_MAX,
      placeholder: '你的第一轮：为什么选这一份、想一起做点什么',
      text: draft.text});
    openingArea.addEventListener('input', function () { draft.text = openingArea.value; });
    nodes.push(h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '开一次共同实践'}),
      h('p', {class: 'world-panel-note', text:
        '选一份明确共享的材料：只有 owner 说现在读得到的共享来源才列在这里，'
        + '界面上不出现你的私人材料全集。'})
    ].concat(sharedPracticeSourcePicker(draft)).concat([
      field('标题', titleInput, '标题是这次邀请的题目；她会看到它，但不会替你起名。'),
      field('你的第一轮', openingArea,
        '这一句会进她的来信箱；共同作品要等她那边真的答应并处理完才存在。'),
      draft.source_artifact_id
        ? kv('已选材料', safeLabel(draft.source_artifact_id, 40), true)
        : null,
      h('div', {class: 'row-actions'}, [
        btn('开这一次共同实践', {small: true, variant: 'mint',
          disabled: !guard.ok || store.busy !== null,
          onClick: function () { submitSharedPracticeOpen(); }}),
        btn('重新读取来源', {small: true,
          onClick: function () { loadShareSources(); }})
      ]),
      draft.errors && draft.errors.length
        ? h('ul', {class: 'errors'}, draft.errors.map(function (message) {
          return h('li', {text: message});
        }))
        : null,
      check.ok ? null : h('p', {class: 'world-panel-note', text:
        '选好材料、写好标题与第一轮之后才能开；这三项都来自真实读取与你自己的字。'}),
      h('p', {class: 'world-panel-note', text:
        '开这一次只是你的请求：它不会替她答应，也不会让共同时间自动开始。'}),
      h('p', {class: 'share-note', text: SHARED_PRACTICE_NOTE})
    ])));
    if (!guard.ok) nodes.push(statePanel('readonly', '只读：现在还写不了', guard.message));
    return nodes;
  }

  function renderSharedPractice() {
    const gate = sharedPracticeGate();
    if (gate) return page('共同实践', '围绕一份明确共享的材料', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const entry = store.data.sharedPractices;
    if (!entry || entry.phase === 'loading') {
      return page('共同实践', '围绕一份明确共享的材料',
        [result, statePanel('loading', '正在读取共同实践…')]);
    }
    if (entry.phase !== 'ready') {
      const offline = entry.phase === 'offline';
      const partial = entry.doc ? sharedPracticeListView(entry.doc) : null;
      if (partial && partial.service && !partial.service.bound) {
        nodes.push(statePanel(offline ? 'offline' : 'unavailable', '共同实践还没接上',
          entry.message, [btn('重试', {onClick: function () { loadSharedPractices(); }})]));
        sharedPracticeOpenComposer(partial).forEach(function (node) { nodes.push(node); });
        return page('共同实践', '围绕一份明确共享的材料', nodes);
      }
      nodes.push(statePanel(offline ? 'offline' : 'error',
        offline ? '没有连上共同实践' : '共同实践读不出来', entry.message,
        [btn('重试', {onClick: function () { loadSharedPractices(); }})]));
      return page('共同实践', '围绕一份明确共享的材料', nodes);
    }
    const view = sharedPracticeListView(entry.doc);
    if (!view) {
      return page('共同实践', '围绕一份明确共享的材料', [result,
        statePanel('error', '服务没有返回可读的共同实践列表', '界面不编一份出来。')]);
    }
    if (!view.ok || !view.listOk) {
      /* owner 没接上 / 读不出来：照实说，绝不写成「一次都没有」。 */
      nodes.push(statePanel('unavailable', '共同实践这次读不到',
        reasonText(view.reason) || view.reason || '服务没有说明',
        [btn('重试', {onClick: function () { loadSharedPractices(); }})]));
      sharedPracticeOpenComposer(view).forEach(function (node) { nodes.push(node); });
      return page('共同实践', '围绕一份明确共享的材料', nodes);
    }
    if (view.service && !view.service.bound) {
      nodes.push(statePanel('unavailable', '共同实践还没接上 owner',
        '服务报告 owner 没有绑定：这一屏只显示它自己说的话。'));
    }
    nodes.push(h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '现在有的'})
    ].concat(view.items.length ? [
      h('ul', {class: 'list'}, view.items.map(sharedPracticeRow))
    ] : [
      h('p', {class: 'world-panel-note', text: '现在一次共同实践都没有；不开就什么都不发生。'})
    ])));
    if (view.truncated) {
      nodes.push(h('p', {class: 'footnote', text: '列表有截断：服务只给了最近的一批。'}));
    }
    nodes.push(h('p', {class: 'world-panel-note', text: view.note || SHARED_PRACTICE_NOTE}));
    nodes.push(h('p', {class: 'footnote', text: SHARED_PRACTICE_QUEUED_NOTE}));
    sharedPracticeOpenComposer(view).forEach(function (node) { nodes.push(node); });
    const errors = sharedPracticeErrors();
    if (errors.length) {
      nodes.push(h('ul', {class: 'errors'}, errors.map(function (message) {
        return h('li', {text: message});
      })));
    }
    return page('共同实践', '只投你自己的输入；接不接受由她自己决定', nodes, [
      btn('刷新', {small: true, icon: 'refresh', onClick: function () { loadSharedPractices(); }})
    ]);
  }

  /* 一次共同实践的详情：材料、双方贡献（谁写的必须看见）、作品版本、暂停/结束、权限边界。 */
  function sharedPracticeContributionRow(row) {
    const tone = row.isSubject ? 'is-ok' : 'is-neutral';
    const chips = [
      chip(row.byText, tone),
      row.round !== null ? chip('第 ' + row.round + ' 轮', 'is-mono') : null,
      row.origin ? chip('来源：' + safeLabel(row.origin, 24), 'is-neutral') : null,
      row.draft ? chip('还只是草稿', 'is-warn') : null,
      row.decisionRef ? chip('决定 ' + row.decisionRef, 'is-mono') : null
    ];
    return h('li', {}, [h('div', {class: 'list-item is-static'}, [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: row.byText}),
        h('span', {class: 'list-meta', text: formatWhen(row.at) || '时间未提供'})
      ]),
      h('span', {class: 'list-chips'}, chips),
      h('span', {class: 'share-text', text: row.text || '（这一轮没有正文）'})
    ])]);
  }

  function sharedPracticeActionComposer(view) {
    const guard = canWrite(store.conn);
    const draft = sharedPracticeDraftFor('action');
    draft.head = view.head || '';
    draft.practiceId = view.practiceId || '';
    const nodes = [];
    const textArea = h('textarea', {class: 'textarea', rows: 3,
      maxlength: SHARED_PRACTICE_TEXT_MAX,
      placeholder: '写你自己这一句（她会不会回应由她自己决定）', text: draft.text});
    textArea.addEventListener('input', function () { draft.text = textArea.value; });
    const reasonInput = h('input', {class: 'input', type: 'text',
      maxlength: SHARED_PRACTICE_REASON_MAX, placeholder: '结束理由（可选，最多 64 字）',
      value: draft.reason});
    reasonInput.addEventListener('input', function () { draft.reason = reasonInput.value; });
    const submitDisabled = !guard.ok || store.busy !== null || !view.open;
    nodes.push(h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '你自己这一句'}),
      textArea,
      h('div', {class: 'row-actions'}, [
        btn(SHARED_PRACTICE_ACTION_TEXT.submit, {small: true, variant: 'mint',
          disabled: submitDisabled,
          onClick: function () { submitSharedPracticeAction(view, 'submit'); }}),
        btn(SHARED_PRACTICE_ACTION_TEXT.modify, {small: true,
          disabled: submitDisabled,
          onClick: function () { submitSharedPracticeAction(view, 'modify'); }})
      ]),
      h('p', {class: 'world-panel-note', text:
        '这两个按钮都只把你自己的话送进她的来信箱：界面没有「替她提交」或「替她接受」的入口。'}),
      h('p', {class: 'world-panel-note', text: SHARED_PRACTICE_MODIFY_NOTE})
    ]));
    nodes.push(h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '暂停 / 显式结束'}),
      reasonInput,
      h('div', {class: 'row-actions'}, [
        btn(SHARED_PRACTICE_ACTION_TEXT.pause, {small: true,
          disabled: !guard.ok || store.busy !== null || !view.open || view.state === 'PAUSED',
          onClick: function () { submitSharedPracticeAction(view, 'pause'); }}),
        btn(SHARED_PRACTICE_ACTION_TEXT.end, {small: true,
          disabled: !guard.ok || store.busy !== null || !view.open,
          onClick: function () { submitSharedPracticeAction(view, 'end'); }})
      ]),
      h('p', {class: 'world-panel-note', text:
        '暂停只停这件作品的追加（她的 Activity 不动）；结束是一条明确的 END 事件，'
        + '她那边收口时会同时放掉 joint 与这件作品——界面不自己把它标成结束。'}),
      /* 恢复 / 重开是她的那一半：界面照实说，不摆一个替她按的按钮。 */
      h('p', {class: 'footnote', text:
        '「重新取用」由她自己决定（A 只让 Life 侧恢复同一段）；这一屏不提供替她恢复的按钮。'})
    ]));
    const problems = sharedPracticeErrors();
    if (problems.length) {
      nodes.push(h('ul', {class: 'errors'}, problems.map(function (message) {
        return h('li', {text: message});
      })));
    }
    if (!guard.ok) {
      nodes.push(statePanel('readonly', '只读：现在还写不了', guard.message));
    }
    return nodes;
  }

  function renderSharedPracticeWork() {
    const gate = sharedPracticeGate();
    if (gate) return page('这次共同实践', '材料、双方贡献与作品版本', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const practiceId = store.sharedPracticeDetailId;
    if (!practiceId) {
      return page('这次共同实践', '材料、双方贡献与作品版本', [result,
        statePanel('empty', '没有选中任何一次共同实践', '从「共同实践」打开一次。')]);
    }
    const entry = store.data.sharedPractice;
    if (!entry || entry.phase === 'loading') {
      return page('这次共同实践', '材料、双方贡献与作品版本',
        [result, statePanel('loading', '正在读取这一次…')]);
    }
    if (entry.phase !== 'ready') {
      const offline = entry.phase === 'offline';
      return page('这次共同实践', '材料、双方贡献与作品版本', [
        result,
        statePanel(offline ? 'offline' : 'error', offline ? '没有连上共同实践' : '这一次读不出来',
          entry.message, [btn('重试', {onClick: function () { loadSharedPractice(practiceId); }})])
      ]);
    }
    const view = sharedPracticeWorkView(entry.doc);
    if (!view) {
      return page('这次共同实践', '材料、双方贡献与作品版本', [result,
        statePanel('error', '服务没有返回可读的这一次', '界面不编一份出来。')]);
    }
    if (!view.ok) {
      return page('这次共同实践', '材料、双方贡献与作品版本', [result,
        statePanel('warn', '这一次读不到', reasonText(view.reason) || '服务没有说明',
          [btn('重试', {onClick: function () { loadSharedPractice(practiceId); }})])]);
    }
    nodes.push(h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: view.title}),
      h('div', {class: 'list-chips'}, [
        chip(view.stateText, view.open ? 'is-ok' : 'is-neutral'),
        chip('你 ' + view.userContributions + ' 次', 'is-neutral'),
        chip('她 ' + view.subjectContributions + ' 次', 'is-neutral'),
        view.work && view.work.versionCount
          ? chip('作品 ' + view.work.versionCount + ' 版', 'is-mono') : null,
        view.pause && view.pause.paused ? chip('已暂停', 'is-warn') : null,
        view.end && view.end.ended ? chip('已结束', 'is-neutral') : null
      ]),
      view.material ? kv('材料', view.material.title || view.material.artifactId, true) : null,
      view.material && view.material.versionId
        ? kv('材料版本', view.material.versionId, true) : null,
      view.material && !view.material.readable
        ? kv('材料现在', reasonText(view.material.reason) || view.material.reason
          || '读不到', false) : null,
      view.head ? kv('这次读到的版本', view.head, true) : null,
      view.end && view.end.endedAt ? kv('结束于', formatWhen(view.end.endedAt)
        || view.end.endedAt) : null,
      view.end && view.end.reason ? kv('结束原因', reasonText(view.end.reason)
        || view.end.reason) : null,
      h('p', {class: 'footnote', text:
        '材料是明确共享的那一份引用与版本；界面不显示你的私人材料全集，也不显示它的私人正文。'}),
      /* 三件事必须分开说：这件作品自己的状态 / 它有多 joint / 她那边怎么处理了来话。 */
      h('p', {class: 'footnote', text:
        (view.jointState === 'JOINT'
          ? '这一段已经有她经核对来源的回应（JOINT）。'
          : '这一段还是候选（CANDIDATE）：打开 ≠ 她答应，你的话不等于她的选择。')
        + '「双方的来回」只投影服务现在给的内容；她的回应由正常 Life 写，这一屏只读。'})
    ]));
    nodes.push(h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '双方的来回'})
    ].concat(view.contributions.length ? [
      h('ul', {class: 'list'}, view.contributions.map(sharedPracticeContributionRow))
    ] : [
      h('p', {class: 'world-panel-note', text: '还没有任何一轮；你写一句就是第一轮。'})
    ])));
    nodes.push(h('div', {class: 'world-panel'}, [
      h('h2', {class: 'world-panel-title', text: '共同作品'})
    ].concat(view.work && view.work.text ? [
      h('pre', {class: 'project-text', text: view.work.text}),
      h('p', {class: 'world-panel-note', text: '这是服务现在投影出来的作品内容与版本（'
        + (view.work.versionCount || 0) + ' 版）。'})
    ] : [
      h('p', {class: 'world-panel-note', text: view.work && view.work.versionCount
        ? '服务报告有 ' + view.work.versionCount + ' 个版本，但这次没有投影正文。'
        : '还没有形成共同作品：这也是一个真实的结果。'})
    ])));
    if (view.versions && view.versions.length) {
      nodes.push(h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '作品版本（owner 自己的历史）'}),
        h('ul', {class: 'list'}, view.versions.map(function (row) {
          return h('li', {}, [h('div', {class: 'list-item is-static'}, [
            h('span', {class: 'list-top'}, [
              h('span', {class: 'list-title', text: row.versionNumber !== null
                ? '第 ' + row.versionNumber + ' 版' : '版本 ' + row.versionId}),
              h('span', {class: 'list-meta', text: formatWhen(row.at) || '时间未提供'})
            ]),
            h('span', {class: 'list-chips'}, [
              row.isHead ? chip('现在这一版', 'is-ok') : null,
              row.textChars !== null ? chip(row.textChars + ' 字', 'is-mono') : null
            ]),
            h('span', {class: 'list-note', text:
              '版本历史只有编号与字数：正文只在上面的「共同作品」里。'})
          ])]);
        }))
      ]));
    }
    if (view.permissions) {
      nodes.push(h('div', {class: 'world-panel'}, [
        h('h2', {class: 'world-panel-title', text: '权限边界'}),
        h('div', {class: 'list-chips'}, [
          chip(view.permissions.boundedExcerpt ? '只投影有界片段' : '没有投影片段', 'is-neutral'),
          chip('私人来源未公开', 'is-ok'),
          chip(view.permissions.mayWriteSubject ? '可以替她写' : '不能替她写',
            view.permissions.mayWriteSubject ? 'is-warn' : 'is-ok')
        ]),
        h('p', {class: 'world-panel-note', text:
          '这一层不公开私人来源全文；引用可以换版或撤权，服务换版之后旧的引用会被拒绝。'})
      ]));
    }
    nodes.push(h('p', {class: 'world-panel-note', text: view.note || SHARED_PRACTICE_NOTE}));
    nodes.push(h('p', {class: 'footnote', text: SHARED_PRACTICE_QUEUED_NOTE}));
    sharedPracticeActionComposer(view).forEach(function (node) { nodes.push(node); });
    return page('这次共同实践', '请求 ≠ 排队 ≠ 决定 ≠ 回应 ≠ 已提交作品', nodes, [
      btn('回共同实践', {small: true, onClick: function () { go('shared-practice'); }}),
      btn('刷新这一次', {small: true, icon: 'refresh',
        onClick: function () { loadSharedPractice(practiceId); }})
    ]);
  }

  function shareOutcomeLines(action, doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!data) return [];
    const lines = [];
    const state = asString(data.state);
    if (state) lines.push('服务说这条现在是：' + shareStateText(state) + '。');
    if (action === 'send_local') {
      const receipt = asRecord(data.receipt);
      if (businessSafe(receipt)) {
        lines.push('收据：' + (asString(receipt.delivery_id) || '没有投递标识') +
          ' · 投递于 ' + (formatWhen(receipt.delivered_at) || '未提供') + '。');
      }
      lines.push(SHARE_LOCAL_NOTE + '。');
    }
    if (action === 'defer') {
      const deferred = asRecord(data.deferred);
      if (businessSafe(deferred)) {
        lines.push('在等：' + (asString(deferred.condition) || asString(deferred.opportunity_ref) || '未提供'));
      }
      lines.push('延后不会按 tick 自己重考：机会到了没有，是她自己的处境判断。');
    }
    if (action === 'drop') {
      const dropped = asRecord(data.dropped);
      if (businessSafe(dropped) && asString(dropped.reason)) lines.push('理由：' + asString(dropped.reason));
      lines.push('放弃是终态：旧的定时器不会复活它，要再说一件事就写新的一条。');
    }
    if (action === 'prepare') {
      lines.push('草稿是 private 的：没有发出，也没有第二份分享库。');
    }
    const sourceCurrent = asRecord(data.source_current);
    if (businessSafe(sourceCurrent) && asString(sourceCurrent.state) && asString(sourceCurrent.state) !== 'READABLE') {
      lines.push('来源现在：' + safeLabel(sourceCurrent.state, 24) + '。');
    }
    return lines;
  }

  function businessSafe(value) {
    return !!value && typeof value === 'object';
  }

  function confirmShareAction(view, action) {
    const lines = [view.title];
    if (action === 'send_local') {
      lines.push('会写一条新的版本，并把这段话放进本实例的本地收件箱。');
      lines.push(SHARE_LOCAL_NOTE + '。');
    } else {
      lines.push('放弃是终态：之后不能再发送，也不能恢复。');
    }
    openSheet({
      title: '确认' + (SHARE_ACTION_TEXT[action] || '这一步'),
      lines: lines,
      note: '操作键只在结果确定后才释放；结果未知会留在「未确认的操作」里。',
      confirmLabel: SHARE_ACTION_TEXT[action] || '确认',
      onConfirm: function () { submitShareAction(view, action); }
    });
  }

  async function submitShareAction(view, action) {
    const guard = canWrite(store.conn);
    const shareId = view.shareId && view.shareId.ok ? view.shareId.value : '';
    const scope = shareActionScope(shareId, action);
    if (!guard.ok || !scope) {
      store.result = {scope: scope || ('share:' + action), status: 'blocked',
        message: guard.ok ? '这一条的标识不可用，没有发出请求。' : guard.message};
      renderAll();
      return;
    }
    if (store.busy !== null) return;
    const note = String(store.shareActionNote || '').trim();
    if (action === 'defer' && !note) {
      store.result = {scope: scope, status: 'blocked', message: '延后要写清等到什么机会（上面那一行）。'};
      renderAll();
      return;
    }
    const entry = ops.ensure(scope, {path: shareActionsPath(shareId)});
    const built = buildShareActionPayload({
      action: action,
      expected_head: view.head,
      operation_key: entry.key,
      defer_condition: action === 'defer' ? note : '',
      opportunity_ref: action === 'defer' ? note : '',
      reason: action === 'drop' ? note : ''
    });
    if (!built) {
      ops.resolve(scope);
      store.result = {scope: scope, status: 'blocked',
        message: '请求没有发出：缺 head 或操作键不可用。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending',
      message: '正在提交「' + (SHARE_ACTION_TEXT[action] || action) + '」…', key: entry.key};
    renderAll();
    const res = await request('POST', shareActionsPath(shareId),
      {csrf: store.conn.csrf, body: built});
    const outcome = deriveMutationOutcome(res);
    store.shareActionNote = '';
    finishMutation(scope, outcome, SHARE_ACTION_TEXT[action] || '分享动作',
      shareOutcomeLines(action, outcome.doc));
  }

  async function submitSharePrepare() {
    const draft = store.shareDraft;
    const guard = canWrite(store.conn);
    const checked = validateShareDraft(draft);
    draft.errors = checked.errors;
    if (!checked.ok) {
      renderAll();
      return;
    }
    if (!guard.ok) {
      store.result = {scope: 'share:prepare', status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    if (store.busy !== null) return;
    const scope = 'share:prepare';
    const entry = ops.ensure(scope, {path: '/shares'});
    const built = buildSharePreparePayload(Object.assign({}, draft, {operation_key: entry.key}));
    if (!built) {
      ops.resolve(scope);
      store.result = {scope: scope, status: 'blocked', message: '请求没有发出：草稿不合法。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在形成草稿…', key: entry.key};
    renderAll();
    const res = await request('POST', '/shares', {csrf: store.conn.csrf, body: built});
    const outcome = deriveMutationOutcome(res);
    const data = outcome.doc && outcome.doc.data && typeof outcome.doc.data === 'object'
      ? outcome.doc.data : null;
    const shareId = data && typeof data.share_id === 'string' ? data.share_id : '';
    finishMutation(scope, outcome, '分享草稿', shareOutcomeLines('prepare', outcome.doc));
    if ((outcome.status === 'applied' || outcome.status === 'already_applied') && shareId) {
      store.shareDraft = {source_artifact_id: draft.source_artifact_id, share_text: '',
        excerpt_chars: '', title: '', expires_after_s: '', errors: []};
      go('share', {detail: shareId, keepResult: true});
    }
  }

  /* 原 key 的只读对账：换键就是作弊，这里永远只用她原来那个键。 */
  async function queryShareOperation(scope, key) {
    const path = shareOperationPath(key);
    if (!path) return;
    store.result = {scope: scope, status: 'pending', message: '正在用原键对账…', key: key};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status !== 'ok') {
      store.result = {scope: scope, status: 'unknown',
        message: '对账没有成功：' + outcome.message + '。结果仍然未知，原键保留。', key: key};
      renderAll();
      return;
    }
    const doc = outcome.doc;
    const data = doc && doc.data && typeof doc.data === 'object' ? doc.data : null;
    const records = data && Array.isArray(data.records) ? data.records.filter(asRecord) : [];
    const last = records.length ? asString(records[records.length - 1].status).toLowerCase() : '';
    const definitive = ['applied', 'already_applied', 'rejected', 'conflict', 'stale_source']
      .indexOf(last) >= 0;
    if (definitive) ops.resolve(scope);
    store.result = {
      scope: scope,
      status: definitive ? last : 'unknown',
      message: definitive
        ? '服务对这条键有确定的答复（手机记录了它的原样回答）。'
        : '这条键还没有确定答复：保留原键，不换键重发。',
      reason: records.length ? asString(records[records.length - 1].reason_code) : '',
      data: data,
      key: key,
      lines: shareOperationLines(doc)
    };
    if (definitive) refreshAfter(scope);
    renderAll();
  }

  /* ---------------- 未确认操作 ---------------- */

  function renderOps() {
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);

    const entries = ops ? ops.listUnresolved() : [];
    nodes.push(h('p', {class: 'footnote', text: '结果未知的写入会留在这里。查询只发送 GET /operations/<key>（世界走 /world/operations/<key>，阅读走 /reading/operations/<key>），不会重发原来的购买、使用或阅读写入。'}));

    if (!entries.length) {
      nodes.push(statePanel('empty', '没有未确认的操作', '有写入结果未知时，这里会出现它的操作键。'));
    } else {
      nodes.push(h('ul', {class: 'list'}, entries.map(function (entry) {
        return h('li', {}, [h('div', {class: 'list-item is-static'}, [
          h('span', {class: 'list-top'}, [
            h('span', {class: 'list-title', text: scopeLabel(entry.scope)}),
            h('span', {class: 'list-meta', text: formatWhen(entry.at)})
          ]),
          h('span', {class: 'list-text is-full', text: entry.path || '接口路径未记录'}),
          h('span', {class: 'list-chips'}, [chip(entry.key, 'is-mono')]),
          h('span', {class: 'list-chips'}, [
            btn('查询状态', {small: true, icon: 'query', onClick: function () { queryOperation(entry.scope, entry.key); }})
          ])
        ])]);
      })));
    }
    return page('未确认的操作', '同一个操作键对账', nodes);
  }

  /* 世界操作的只读对账：只解释 owner 给的字段，不重发原命令。 */
  function worldOperationLines(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!data) return [];
    const lines = [];
    const operation = asString(data.operation);
    if (operation) {
      const names = {world: '世界动作', purchase: '购买', use: '使用物品'};
      lines.push('这条键属于：' + (names[operation] || safeLabel(operation, 30)));
    }
    const actionType = asString(data.action_type);
    if (actionType) lines.push('动作：' + (ACTION_TEXT[actionType] || safeLabel(actionType, 20)));
    const ledgerState = asString(data.ledger_state);
    const ledgerResult = asString(data.ledger_result);
    if (ledgerState || ledgerResult) lines.push('账本：' + [ledgerState, ledgerResult].filter(Boolean).join(' · '));
    const refs = Array.isArray(data.world_mutation_refs) ? data.world_mutation_refs : [];
    if (refs.length) lines.push('世界改动：' + refs.slice(0, 3).map(function (ref) { return safeLabel(ref, 60); }).join('、'));
    if (data.reconciliation_required === true) lines.push('服务说这次需要人工对账（reconciliation_required）。');
    const travel = asRecord(data.travel);
    if (travel) {
      const status = asString(travel.status);
      lines.push('行程：' + (TRAVEL_STATUS_TEXT[status] || safeLabel(status, 30)));
      const remaining = asSeconds(travel.remaining_seconds);
      if (status === 'in_progress' && remaining !== null) {
        lines.push('还有 ' + formatWalkText(remaining) + ' 到目的地；这次是出发，不是到达。');
      }
    }
    return lines;
  }

  /* 阅读操作的只读对账：原键查 /reading/operations/<key>，不落到购物 operation 上。 */
  function readingOperationLines(doc) {
    const envelope = asRecord(doc);
    const data = envelope ? asRecord(envelope.data) : null;
    if (!data) return [];
    const lines = [];
    const action = asString(data.action);
    if (action) lines.push('这条键属于：' + safeLabel(action, 40));
    if (data.page !== undefined) {
      const page = readingPageValue(data.page);
      if (page !== null) lines.push('页码：第 ' + page + ' 页');
    }
    const revision = asString(data.source_revision);
    if (revision) lines.push('材料版本：' + safeLabel(revision, 60));
    const effect = asString(data.effect_state || data.effect);
    if (effect) lines.push('效果：' + safeLabel(effect, 40));
    const recordStatus = asString(data.status || data.state);
    if (recordStatus) lines.push('服务记录：' + safeLabel(recordStatus, 40));
    return lines;
  }

  async function queryOperation(scope, key) {
    if (!key) return;
    /* 分享查 /shares/operations/<key>：只读对账，永远用原来那个键。 */
    if (SHARE_SCOPES.indexOf(String(scope || '').split(':')[0]) >= 0) {
      await queryShareOperation(scope, key);
      return;
    }
    /* 来访查 /contact/operations/<key>：只读对账，永远用原来那个键。 */
    if (CONTACT_SCOPES.indexOf(String(scope || '').split(':')[0]) >= 0) {
      await queryContactOperation(scope, key);
      return;
    }
    /* 世界动作查 /world/operations/<key>；阅读查 /reading/operations/<key>；
       手机购物/笔记仍走 /operations/<key>。三条线不互相顶替。 */
    const worldScope = String(scope || '').indexOf('world:') === 0;
    const readingScope = READING_SCOPES.indexOf(String(scope || '').split(':')[0]) >= 0;
    const path = worldScope
      ? '/world/operations/' + encodeURIComponent(key)
      : (readingScope
        ? readingOperationPath(key)
        : '/operations/' + encodeURIComponent(key));
    if (!path) return;
    store.result = {scope: scope, status: 'pending', message: '正在查询这次操作的状态…', key: key};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    /* 阅读对账是 C 的 typed 回答：ok:false + status not_found/unknown 也是答案，不是传输失败。
       世界/购物两条线保持原样（只有 ok 才算答案）。 */
    const rawDoc = res && res.kind === 'http' && res.doc && typeof res.doc === 'object' ? res.doc : null;
    const rawStatus = rawDoc && typeof rawDoc.status === 'string' ? rawDoc.status.toLowerCase() : '';
    const typedAnswer = readingScope &&
      (rawStatus === 'not_found' || rawStatus === 'unknown' || MUTATION_STATUSES.indexOf(rawStatus) >= 0);
    if (!typedAnswer && outcome.status !== 'ok') {
      store.result = {
        scope: scope,
        status: 'unknown',
        message: '查询没有成功：' + outcome.message + '。结果仍然未知。',
        key: key
      };
      renderAll();
      return;
    }
    const doc = typedAnswer ? rawDoc : outcome.doc;
    const raw = typeof doc.status === 'string' ? doc.status.toLowerCase() : '';
    const typedMissing = raw === 'not_found' || raw === 'unknown_operation';
    if (typedMissing) {
      ops.resolve(scope);
      store.result = {scope: scope, status: 'rejected',
        message: '服务没有这个操作键的记录（' + (raw === 'not_found' ? 'NOT_FOUND' : sanitizeLine(doc.status, 30)) + '）。',
        key: key};
    } else if (MUTATION_STATUSES.indexOf(raw) >= 0) {
      if (raw !== 'unknown') ops.resolve(scope);
      store.result = {
        scope: scope,
        status: raw,
        message: raw === 'unknown' ? '服务仍然不知道这次操作的结果。' : '查询到这次操作的最终状态。',
        reason: doc.reason_code || '',
        data: doc.data && typeof doc.data === 'object' ? doc.data : null,
        key: key,
        lines: worldScope ? worldOperationLines(doc) : (readingScope ? readingOperationLines(doc) : null)
      };
      if (raw === 'applied' || raw === 'already_applied') refreshAfter(scope);
    } else {
      store.result = {scope: scope, status: 'unknown', message: '服务返回了界面不认识的查询状态。', key: key};
    }
    renderAll();
  }

  /* ---------------- mutation 收尾 ---------------- */

  function finishMutation(scope, outcome, label, extraLines) {
    const entry = ops.peek(scope);
    const key = entry ? entry.key : '';
    const terminal = outcome.status === 'applied' || outcome.status === 'already_applied' ||
      outcome.status === 'rejected' || outcome.status === 'conflict' ||
      outcome.status === 'blocked' || outcome.status === 'stale_source';
    if (terminal) ops.resolve(scope);
    else ops.markUnknown(scope, {path: entry && entry.path});

    store.busy = null;
    const messages = {
      applied: (label || '操作') + '已生效：服务确认了这次写入。',
      already_applied: '这个操作键已经生效过，服务没有重复执行。',
      rejected: (label || '操作') + '被服务拒绝，没有写入。',
      conflict: '版本冲突：这次没有写入，草稿还在。',
      stale_source: '来源已经换版或被拒绝读取：这次没有发出，要按现在的内容重新准备一条。',
      unknown: '结果未知：这次' + (label || '操作') + '没有被确认。不要重复点击；可以用同一个操作键查询。',
      blocked: '请求没有发出。'
    };
    store.result = {
      scope: scope,
      status: outcome.status,
      message: messages[outcome.status] || '服务返回了未识别的状态。',
      reason: outcome.reason || '',
      data: outcome.doc && outcome.doc.data && typeof outcome.doc.data === 'object' ? outcome.doc.data : null,
      key: key,
      lines: Array.isArray(extraLines) && extraLines.length ? extraLines : null
    };
    if (outcome.status === 'applied' || outcome.status === 'already_applied') refreshAfter(scope);
    renderAll();
  }

  /* 同一个实例里物件会因购买/消耗/拿放而变：世界与身体视图共用这一份 /world 快照。
     事实可能变了就作废它，让下一次进入世界/身体屏重读；
     人正好停在那个屏上时只补一次读取（loadResource 先占位成 loading，不会被排第二条）。 */
  function invalidateWorldSnapshot() {
    store.data.world = null;
    if (store.screen === 'world' || store.screen === 'body') loadResource('world');
  }

  function refreshAfter(scope) {
    const kind = String(scope || '').split(':')[0];
    if (kind === 'note') {
      store.data.notes = null;
      if (store.screen === 'notes') loadResource('notes');
    } else if (kind === 'album') {
      store.data.album = null;
      store.albumBroken = Object.create(null);
      if (store.screen === 'album') loadResource('album');
    } else if (kind === 'reference') {
      store.data.references = null;
      if (store.screen === 'references') loadResource('references');
    } else if (kind === 'purchase') {
      store.data.shop = null;
      store.data.inventory = null;
      store.data.wallet = null;
      /* 余额变了，实体店货架上的“还能不能买”也跟着变：作废缓存，不在这里多发请求。 */
      store.data.store = null;
      /* 买到的是世界里的一件物件：不重读的话手边会停在购买前（没有新物件、没有拿起按钮）。 */
      invalidateWorldSnapshot();
      if (store.screen === 'shop' || store.screen === 'shop-item') loadResource('shop');
      if (store.screen === 'shop-item' && !store.data.wallet) loadResource('wallet');
      if (store.screen === 'inventory') loadResource('inventory');
    } else if (kind === 'store-purchase') {
      /* 实体店买的东西同样改钱包/库存/世界摆放，还改货架上“还能不能买”和自己的余额。 */
      store.data.store = null;
      store.data.shop = null;
      store.data.inventory = null;
      store.data.wallet = null;
      invalidateWorldSnapshot();
      if (store.screen === 'store' || store.screen === 'store-item') loadResource('store');
      if (store.screen === 'store-item' && !store.data.wallet) loadResource('wallet');
      if (store.screen === 'inventory') loadResource('inventory');
    } else if (kind === 'use') {
      store.data.inventory = null;
      /* 消耗同样改了这个世界里的物件与摆放。 */
      invalidateWorldSnapshot();
      if (store.screen === 'inventory') loadResource('inventory');
    } else if (kind === 'world') {
      /* 世界动作之后重读快照：在途/姿势/手边/布置/灯都以 owner 的新事实为准。
         位置也变了，实体店货架要不要重读交给下一次进店，这里只作废缓存。 */
      store.data.inventory = null;
      store.data.store = null;
      invalidateWorldSnapshot();
    } else if (kind === 'project') {
      /* 项目写入之后：列表与详情都作废；停在详情/写项目屏的人只补一次读取。
         草稿里的 head 属于旧版本，绝不带动到下一次写入。 */
      store.data.projects = null;
      store.data.project = null;
      if (store.screen === 'project' || store.screen === 'project-edit') {
        const projectId = store.projectDetailId;
        if (projectId) loadProject(projectId);
      } else if (store.screen === 'projects') {
        loadResource('projects');
      }
    } else if (kind === 'reading-import') {
      /* 新版本落定：旧缓存全部作废，回到新材料的第 1 页（不拿旧停点冒充新版本位置）。 */
      store.data.reading = null;
      store.data.readingMaterial = null;
      if (store.screen === 'reading') loadReadingMaterial(1);
    } else if (kind === 'reading-position' || kind === 'reading-note' || kind === 'reading-unclear') {
      /* 写成功后必须重读：private_head / annotations / 停点都以服务的新事实为准，
         下一次写入用的是刷新回来的 head，绝不复用旧值。 */
      const page = readingScopePage(scope);
      store.data.reading = null;
      store.data.readingMaterial = null;
      if (store.screen === 'reading') loadReadingMaterial(page);
    } else if (kind === 'share') {
      /* 分享写完/投递完：列表与来源的当前性都变了，作废缓存；正文与收据下次进详情再读。 */
      store.data.shares = null;
      store.data.shareSources = null;
      if (store.screen === 'shares') loadShares();
      if (store.screen === 'share') loadShare(store.shareDetailId);
    } else if (CONTACT_SCOPES.indexOf(kind) >= 0) {
      /* 投完必须重读：queued / 处理结果 / 会话状态都以服务的新事实为准。
         这里只补一次读取，不做轮询——她处理了没有，下次进屏或点刷新才看。 */
      store.data.contact = null;
      store.data.contactEvent = null;
      if (store.screen === 'contact') loadContact();
      if (store.screen === 'contact-event' && store.contactDetailRef) {
        loadContactEvent(store.contactDetailRef);
      }
    } else if (kind === 'shared-practice') {
      /* 共同实践写完：列表与这一次都作废；停在这一次上的人只补一次重读。
         新的 head 就是下一次写入的 CAS 凭据，绝不复用旧值；来源当前性也跟着变。 */
      store.data.sharedPractices = null;
      store.data.sharedPractice = null;
      store.data.shareSources = null;
      if (store.screen === 'shared-practice') {
        loadSharedPractices();
        loadShareSources();
      } else if (store.screen === 'shared-practice-work' && store.sharedPracticeDetailId) {
        loadSharedPractice(store.sharedPracticeDetailId);
      }
    }
  }

  /* ---------------- 弹层 ---------------- */

  function openSheet(spec) {
    closeSheet();
    if (!dom.sheetHost) return;
    const overlay = h('div', {
      class: 'sheet-overlay',
      on: {
        click: function (event) {
          if (event.target === overlay) closeSheet();
        }
      }
    }, [
      h('div', {class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': spec.title}, [
        h('div', {class: 'sheet-grip', 'aria-hidden': 'true'}),
        h('h2', {class: 'sheet-title', text: spec.title}),
        (spec.lines || []).map(function (line) {
          return h('p', {class: 'sheet-line', text: line});
        }),
        spec.note ? h('p', {class: 'sheet-note', text: spec.note}) : null,
        h('div', {class: 'sheet-actions'}, [
          btn('取消', {onClick: closeSheet}),
          btn(spec.confirmLabel || '确认', {
            variant: 'primary',
            onClick: function () {
              closeSheet();
              if (typeof spec.onConfirm === 'function') spec.onConfirm();
            }
          })
        ])
      ])
    ]);
    dom.sheetHost.replaceChildren(overlay);
    const focusTarget = overlay.querySelector('.btn-primary');
    if (focusTarget && typeof focusTarget.focus === 'function') focusTarget.focus();
  }

  function closeSheet() {
    if (dom.sheetHost) dom.sheetHost.replaceChildren();
  }

  /* ---------------- 资料库 / 研究工作台 ---------------- */

  /* 资料与研究都不是第九个 app 图标：它们是生活链的直接入口，数据只来自服务。 */

  function materialGate() {
    if (store.conn.phase === 'loading' || store.conn.phase === 'offline' ||
        store.conn.phase === 'error') {
      return statePanel(store.conn.phase === 'loading' ? 'loading' : 'offline',
        '还没连上手机服务', store.conn.detail || '');
    }
    return null;
  }

  function materialStatusChip(material) {
    if (material.readOnly) return chip('只读材料', 'is-neutral');
    if (material.availability === 'ARCHIVED') return chip('已归档', 'is-warn');
    if (material.availability === 'UNAVAILABLE') return chip('读不到', 'is-error');
    return chip(SOURCE_KIND_TEXT[material.sourceKind] || material.sourceKind || '资料', 'is-neutral');
  }

  function materialListRow(material) {
    const inner = [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: material.title}),
        h('span', {class: 'list-meta', text: material.revision !== null ? 'rev ' + material.revision : ''})
      ]),
      h('span', {class: 'list-chips'}, [
        materialStatusChip(material),
        chip(material.origin ? safeLabel(material.origin, 28) : '来源未声明', 'is-mono')
      ]),
      material.excerpt ? h('span', {class: 'list-note', text: material.excerpt}) : null
    ];
    return h('li', {}, [
      h('button', {class: 'list-item', type: 'button',
        on: {click: function () { openMaterial(material.id); }}}, inner)
    ]);
  }

  function renderMaterials() {
    const gate = materialGate();
    if (gate) return page('资料', '他给的文本，带来源声明', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const draft = store.materialDraft;
    const queryInput = h('input', {class: 'input', type: 'search', maxlength: MATERIAL_QUERY_MAX,
      autocomplete: 'off', placeholder: '在标题、正文、来源里找', value: draft.query});
    queryInput.addEventListener('input', function () { draft.query = queryInput.value; });
    queryInput.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { submitMaterialSearch(); }
    });
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '找一份资料'}),
      field('检索', queryInput, '只搜这一台手机里已有的资料；不联网抓取'),
      h('div', {class: 'form-actions'}, [
        btn('检索', {variant: 'primary', icon: 'book', onClick: submitMaterialSearch}),
        btn('清除', {onClick: function () { draft.query = ''; loadMaterials(''); }}),
        btn('导入文本', {icon: 'plus', onClick: startImportMaterial})
      ])
    ]));
    if (store.conn.phase === 'readonly') {
      nodes.push(statePanel('readonly', '只读模式：能看不能写', '服务没有提供 CSRF 令牌，导入会被界面挡下。'));
    }
    const entry = store.data.materials;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读资料库…'));
      return page('资料', '他给的文本，带来源声明', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到资料',
        entry.message, [btn('重试', {onClick: function () { loadMaterials(draft.query); }})]));
      return page('资料', '他给的文本，带来源声明', nodes);
    }
    const view = materialListView(entry.doc);
    if (view.itemsMissing) {
      nodes.push(statePanel('error', '服务没有返回资料列表', '响应里没有 data.materials 数组。'));
    } else if (!view.materials.length) {
      nodes.push(statePanel('empty', view.query ? '没有匹配的资料' : '还没有资料',
        view.query ? '换个词再找；服务真的没有匹配项。' : '导入一段他给的文本，它会带来源声明存下来。',
        [btn('导入文本', {variant: 'primary', onClick: startImportMaterial})]));
    } else {
      nodes.push(h('ul', {class: 'list'}, view.materials.map(materialListRow)));
    }
    const lines = [];
    if (view.matched) lines.push('匹配 ' + view.matched + ' 件');
    if (view.skippedResearch) lines.push('研究作品 ' + view.skippedResearch + ' 件（不在这里）');
    if (view.truncated) lines.push('列表已截断');
    if (lines.length) nodes.push(h('p', {class: 'footnote', text: lines.join(' · ')}));
    nodes.push(h('p', {class: 'footnote',
      text: '每件资料都带来源声明；声明不是认证，导入也不会覆盖既有阅读材料。'}));
    return page('资料', '检索、导入与来源版本', nodes);
  }

  function renderMaterial() {
    const gate = materialGate();
    if (gate) return page('资料详情', '', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const entry = store.data.material;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读这一件…'));
      return page('资料详情', '', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到这一件',
        entry.message, [btn('重试', {onClick: function () { loadMaterial(entry.artifactId); }})]));
      return page('资料详情', '', nodes);
    }
    const view = materialDetailView(entry.doc);
    if (!view) {
      nodes.push(statePanel('error', '服务没有返回这件资料', '响应里没有 data.material 对象。'));
      return page('资料详情', '', nodes);
    }
    const material = view.material;
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: material.title}),
      h('div', {class: 'row-actions'}, [
        materialStatusChip(material),
        material.revision !== null ? chip('rev ' + material.revision, 'is-neutral') : null,
        material.versionNumber !== null ? chip('v' + material.versionNumber, 'is-neutral') : null
      ]),
      kv('工件', material.id, true),
      kv('来源声明', material.origin || '未声明（不是认证）'),
      kv('来源版本', material.sourceRevision || '服务未提供', true),
      kv('类型', material.sourceKindText || '未声明'),
      kv('最近更新', formatWhen(material.updatedAt) || '服务未提供')
    ]));
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '正文'}),
      view.contentAvailable
        ? h('pre', {class: 'material-text', text: view.text || '（这一份没有正文）'})
        : statePanel('warn', '这一版没有正文', '服务没有返回 text。'),
      h('p', {class: 'footnote', text: '正文是他提供的文本原样保存；标记行不属于正文。'})
    ]));
    const actions = [btn('刷新', {icon: 'refresh', onClick: function () { loadMaterial(material.id); }})];
    actions.push(btn('回到资料库', {onClick: function () { go('materials'); }}));
    if (!material.readOnly) {
      actions.push(btn('用这件起一份研究', {variant: 'primary', icon: 'note',
        onClick: function () { startResearchWith([{artifact_id: material.id,
          source_revision: material.sourceRevision}]); }}));
    }
    nodes.push(h('div', {class: 'row-actions'}, actions));
    if (material.readOnly) {
      nodes.push(h('p', {class: 'footnote',
        text: '这是既有阅读材料：可以检索、可以引用，但导入永不覆盖它。'}));
    }
    return page('资料详情', '正文、来源声明与它所属的版本', nodes);
  }

  function renderMaterialImport() {
    const gate = materialGate();
    if (gate) return page('导入资料', '', [gate]);
    const draft = store.materialDraft;
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);

    const titleInput = h('input', {class: 'input', type: 'text', maxlength: MATERIAL_TITLE_MAX,
      autocomplete: 'off', placeholder: '一句话标题', value: draft.title});
    titleInput.addEventListener('input', function () { draft.title = titleInput.value; });
    const originInput = h('input', {class: 'input', type: 'text', maxlength: MATERIAL_ORIGIN_MAX,
      autocomplete: 'off', placeholder: '比如：他手抄给我的一张纸', value: draft.origin});
    originInput.addEventListener('input', function () { draft.origin = originInput.value; });
    const kindSelect = h('select', {class: 'input'}, MATERIAL_SOURCE_KINDS.map(function (kind) {
      return h('option', {value: kind, text: SOURCE_KIND_TEXT[kind]});
    }));
    kindSelect.value = draft.source_kind;
    kindSelect.addEventListener('change', function () { draft.source_kind = kindSelect.value; });
    const textArea = h('textarea', {class: 'textarea', maxlength: MATERIAL_TEXT_MAX,
      placeholder: '他提供的文本，原样保存'});
    textArea.value = draft.text;
    const counter = h('span', {class: 'counter', text: draft.text.length + ' / ' + MATERIAL_TEXT_MAX});
    textArea.addEventListener('input', function () {
      draft.text = textArea.value;
      counter.textContent = draft.text.length + ' / ' + MATERIAL_TEXT_MAX;
    });
    const busy = store.busy === MATERIAL_SCOPES.import;
    const errors = draft.errors && draft.errors.length
      ? h('ul', {class: 'errors'}, draft.errors.map(function (message) { return h('li', {text: message}); }))
      : null;

    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '导入一件资料'}),
      field('标题', titleInput, '最多 ' + MATERIAL_TITLE_MAX + ' 字'),
      field('文本类型', kindSelect, '他给的文本 / 明确是虚构：两种都要写清楚'),
      field('来源声明', originInput, '这是声明，不是真实性认证'),
      field('正文', textArea, '最多 ' + MATERIAL_TEXT_MAX + ' 字'),
      counter,
      errors,
      h('div', {class: 'form-actions'}, [
        btn(busy ? '提交中…' : '保存', {variant: 'primary', icon: 'check',
          disabled: busy, onClick: submitMaterialImport}),
        btn('取消', {onClick: function () { goBack(); }})
      ])
    ]));
    nodes.push(h('p', {class: 'footnote',
      text: '保存会由服务新建一件工件并提交第一版；同一个操作键不会变成两份。'}));
    return page('导入资料', '不联网抓取，只保存他给的文本', nodes);
  }

  function sourceCheckChip(check) {
    const tone = check.status === 'CURRENT' ? 'is-ok'
      : (check.status === 'STALE' ? 'is-warn' : 'is-error');
    return chip(CHECK_STATUS_TEXT[check.status] || check.status, tone);
  }

  function researchRowListView(row) {
    const inner = [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: row.title}),
        h('span', {class: 'list-meta', text: row.revision !== null ? 'rev ' + row.revision : ''})
      ]),
      h('span', {class: 'list-chips'}, [
        row.status === 'archived' ? chip('已关闭', 'is-warn') : chip('进行中', 'is-ok'),
        row.sourceCount !== null ? chip('来源 ' + row.sourceCount, 'is-neutral') : null,
        chip(formatWhen(row.updatedAt) || '时间未提供', 'is-neutral')
      ]),
      row.question ? h('span', {class: 'list-note', text: row.question}) : null
    ];
    return h('li', {}, [
      h('button', {class: 'list-item', type: 'button',
        on: {click: function () { openResearch(row.id); }}}, inner)
    ]);
  }

  function renderResearch() {
    const gate = materialGate();
    if (gate) return page('研究', '从一个具体问题开始', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('起一份研究', {variant: 'primary', icon: 'plus', onClick: startResearch}),
      btn('刷新', {icon: 'refresh', onClick: function () { loadResearch(); }})
    ]));
    if (store.conn.phase === 'readonly') {
      nodes.push(statePanel('readonly', '只读模式：能看不能写', '服务没有提供 CSRF 令牌。'));
    }
    const entry = store.data.research;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读研究…'));
      return page('研究', '从一个具体问题开始', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到研究',
        entry.message, [btn('重试', {onClick: loadResearch})]));
      return page('研究', '从一个具体问题开始', nodes);
    }
    const view = researchListView(entry.doc);
    if (view.itemsMissing) {
      nodes.push(statePanel('error', '服务没有返回研究列表', '响应里没有 data.projects 数组。'));
    } else if (!view.projects.length) {
      nodes.push(statePanel('empty', '还没有研究', '研究从一个具体问题开始，并且要指出具体来源。',
        [btn('起一份研究', {variant: 'primary', onClick: startResearch})]));
    } else {
      nodes.push(h('ul', {class: 'list'}, view.projects.map(researchRowListView)));
    }
    if (view.truncated) nodes.push(h('p', {class: 'footnote', text: '列表已截断'}));
    nodes.push(h('p', {class: 'footnote',
      text: view.note || '研究作品不是 Memory，也不是稳定信念：内容仍是暂定的。'}));
    return page('研究', '问题、范围、来源版本与未知', nodes);
  }

  function renderResearchItem() {
    const gate = materialGate();
    if (gate) return page('研究详情', '', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const entry = store.data.researchItem;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读这一份研究…'));
      return page('研究详情', '', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到这一份研究',
        entry.message, [btn('重试', {onClick: function () { loadResearchItem(entry.artifactId); }})]));
      return page('研究详情', '', nodes);
    }
    const view = researchDetailView(entry.doc);
    if (!view) {
      nodes.push(statePanel('error', '服务没有返回这一份研究', '响应里没有 data.project 对象。'));
      return page('研究详情', '', nodes);
    }
    const project = view.project;
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: project.title}),
      h('div', {class: 'row-actions'}, [
        project.status === 'archived' ? chip('已关闭', 'is-warn') : chip('进行中', 'is-ok'),
        project.revision !== null ? chip('rev ' + project.revision, 'is-neutral') : null,
        view.sourcesCurrent ? chip('来源都是当前版本', 'is-ok') : chip('来源有变化', 'is-warn')
      ]),
      kv('问题', project.question || '服务未提供'),
      kv('暂定范围', view.scope || '尚未划定'),
      kv('未知', view.uncertainty || RESEARCH_DEFAULT_UNCERTAINTY),
      kv('当前 head', project.head || '服务未提供', true),
      kv('最近更新', formatWhen(project.updatedAt) || '服务未提供')
    ]));

    const checkNodes = [h('h2', {class: 'card-title', text: '来源与实际版本'})];
    if (!view.checks.length) {
      checkNodes.push(statePanel('warn', '服务没有报告来源状态', '这一份的引用可能不完整。'));
    } else {
      checkNodes.push(h('ul', {class: 'list'}, view.checks.map(function (check) {
        return h('li', {}, [
          h('div', {class: 'list-item is-static'}, [
            h('span', {class: 'list-top'}, [
              h('span', {class: 'list-title', text: check.title || check.artifactId}),
              sourceCheckChip(check)
            ]),
            h('span', {class: 'list-chips'}, [
              chip('引用 ' + (check.sourceRevision || '未指定'), 'is-mono'),
              check.currentRevision && check.currentRevision !== check.sourceRevision
                ? chip('现在 ' + check.currentRevision, 'is-mono') : null
            ]),
            check.reason ? h('span', {class: 'list-note', text: check.reason}) : null
          ])
        ]);
      })));
      if (!view.sourcesCurrent) {
        checkNodes.push(statePanel('warn', '旧结果不等于当前已核实',
          '至少一个来源已经不是当时那个版本：这一份内容仍是暂定的，没有重新读过。'));
      }
    }
    nodes.push(h('div', {class: 'card'}, checkNodes));

    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '正文'}),
      view.contentAvailable
        ? h('pre', {class: 'research-text', text: view.body || '（这一份还没有正文）'})
        : statePanel('warn', '这一版没有正文', '服务没有返回 content。'),
      h('p', {class: 'footnote',
        text: '第一行是机器可读标记（问题与引用），界面只显示正文；续写时标记由服务重建。'})
    ]));

    const actions = [btn('刷新', {icon: 'refresh', onClick: function () { loadResearchItem(project.id); }})];
    if (project.status === 'active') {
      actions.push(btn('续写', {variant: 'primary', icon: 'note',
        onClick: function () { editResearch(view); }}));
      actions.push(btn('关闭', {icon: 'check', onClick: function () { confirmCloseResearch(view); }}));
    } else {
      actions.push(btn('已关闭：不能再改', {disabled: true}));
    }
    nodes.push(h('div', {class: 'row-actions'}, actions));
    nodes.push(h('p', {class: 'footnote',
      text: view.note || '研究作品不是 Memory，也不是稳定信念；关闭只是归档，正文与版本全在。'}));
    return page('研究详情', '问题、来源版本、暂定发现与未知', nodes);
  }

  function renderResearchCreate() {
    const gate = materialGate();
    if (gate) return page('起一份研究', '', [gate]);
    const draft = store.researchDraft;
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);

    const questionInput = h('input', {class: 'input', type: 'text',
      maxlength: RESEARCH_QUESTION_MAX, autocomplete: 'off',
      placeholder: '一个具体的问题', value: draft.question});
    questionInput.addEventListener('input', function () { draft.question = questionInput.value; });
    const titleInput = h('input', {class: 'input', type: 'text', maxlength: MATERIAL_TITLE_MAX,
      autocomplete: 'off', placeholder: '可留空，默认用问题当标题', value: draft.title});
    titleInput.addEventListener('input', function () { draft.title = titleInput.value; });
    const scopeInput = h('input', {class: 'input', type: 'text', maxlength: RESEARCH_SCOPE_MAX,
      autocomplete: 'off', placeholder: '这一份只讨论什么', value: draft.scope});
    scopeInput.addEventListener('input', function () { draft.scope = scopeInput.value; });
    const uncertaintyInput = h('input', {class: 'input', type: 'text',
      maxlength: RESEARCH_UNCERTAINTY_MAX, autocomplete: 'off',
      placeholder: RESEARCH_DEFAULT_UNCERTAINTY, value: draft.uncertainty});
    uncertaintyInput.addEventListener('input', function () {
      draft.uncertainty = uncertaintyInput.value;
    });

    const materialsEntry = store.data.materials;
    const sourceNodes = [h('h2', {class: 'card-title', text: '选择具体来源'})];
    if (!materialsEntry || materialsEntry.phase === 'loading') {
      sourceNodes.push(statePanel('loading', '正在读资料库…'));
    } else if (materialsEntry.phase !== 'ready') {
      sourceNodes.push(statePanel('error', '没有读到资料，不能凭空编来源',
        materialsEntry.message, [btn('重试', {onClick: function () { loadMaterials(''); }})]));
    } else {
      const view = materialListView(materialsEntry.doc);
      if (!view.materials.length) {
        sourceNodes.push(statePanel('empty', '资料库还是空的',
          '没有可引用的资料时，研究只能保留未知——先导入一份他给的文本。',
          [btn('导入文本', {onClick: startImportMaterial})]));
      } else {
        sourceNodes.push(h('ul', {class: 'list'}, view.materials.map(function (material) {
          const chosen = draft.sources.some(function (ref) { return ref.artifact_id === material.id; });
          return h('li', {}, [
            h('button', {class: 'list-item', type: 'button',
              on: {click: function () { toggleResearchSource(material, chosen); }}}, [
              h('span', {class: 'list-top'}, [
                h('span', {class: 'list-title', text: material.title}),
                chosen ? chip('已选', 'is-ok') : chip('未选', 'is-neutral')
              ]),
              h('span', {class: 'list-chips'}, [
                chip(material.sourceRevision ? '版本 ' + safeLabel(material.sourceRevision, 20) : '版本未提供', 'is-mono')
              ]),
              material.excerpt ? h('span', {class: 'list-note', text: material.excerpt}) : null
            ])
          ]);
        })));
      }
      sourceNodes.push(h('p', {class: 'footnote',
        text: '已选 ' + draft.sources.length + ' 件；引用的是你看到的那个版本，之后来源变了会标 STALE。'}));
    }
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '新的一份研究'}),
      field('问题', questionInput, '最多 ' + RESEARCH_QUESTION_MAX + ' 字'),
      field('标题（可留空）', titleInput),
      field('暂定范围', scopeInput, '最多 ' + RESEARCH_SCOPE_MAX + ' 字'),
      field('未知', uncertaintyInput, '默认「' + RESEARCH_DEFAULT_UNCERTAINTY + '」')
    ]));
    nodes.push(h('div', {class: 'card'}, sourceNodes));

    const busy = store.busy === MATERIAL_SCOPES.create;
    const errors = draft.errors && draft.errors.length
      ? h('ul', {class: 'errors'}, draft.errors.map(function (message) { return h('li', {text: message}); }))
      : null;
    nodes.push(h('div', {class: 'card'}, [
      errors,
      h('div', {class: 'form-actions'}, [
        btn(busy ? '提交中…' : '开始研究', {variant: 'primary', icon: 'check',
          disabled: busy, onClick: submitResearchDraft}),
        btn('取消', {onClick: function () { goBack(); }})
      ])
    ]));
    nodes.push(h('p', {class: 'footnote',
      text: '新建时会先核对每个来源：读不到或已撤回的来源会让这次创建被拒。'}));
    return page('起一份研究', '问题 + 具体来源，别的都还是未知', nodes);
  }

  function renderResearchEdit() {
    const draft = store.researchDraft;
    if (!draft || draft.mode !== 'edit') {
      return page('写研究', '', [statePanel('empty', '没有打开的草稿'),
        btn('回到研究', {onClick: function () { go('research'); }})]);
    }
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const contentArea = h('textarea', {class: 'textarea', maxlength: RESEARCH_CONTENT_MAX,
      placeholder: '写下这一版的暂定发现、方法、范围或未知'});
    contentArea.value = draft.content;
    const counter = h('span', {class: 'counter',
      text: draft.content.length + ' / ' + RESEARCH_CONTENT_MAX});
    contentArea.addEventListener('input', function () {
      draft.content = contentArea.value;
      counter.textContent = draft.content.length + ' / ' + RESEARCH_CONTENT_MAX;
    });
    const scopeInput = h('input', {class: 'input', type: 'text', maxlength: RESEARCH_SCOPE_MAX,
      autocomplete: 'off', value: draft.scope});
    scopeInput.addEventListener('input', function () { draft.scope = scopeInput.value; });
    const uncertaintyInput = h('input', {class: 'input', type: 'text',
      maxlength: RESEARCH_UNCERTAINTY_MAX, autocomplete: 'off', value: draft.uncertainty});
    uncertaintyInput.addEventListener('input', function () {
      draft.uncertainty = uncertaintyInput.value;
    });
    const busy = store.busy === MATERIAL_SCOPES.update;
    const errors = draft.errors && draft.errors.length
      ? h('ul', {class: 'errors'}, draft.errors.map(function (message) { return h('li', {text: message}); }))
      : null;
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '续写这一份研究'}),
      kv('标题（创建后不改名）', draft.title || '未命名研究'),
      kv('问题（不在这里改）', draft.question || '服务未提供'),
      field('正文', contentArea, '最多 ' + RESEARCH_CONTENT_MAX + ' 字'),
      counter,
      field('暂定范围', scopeInput),
      field('未知', uncertaintyInput),
      errors,
      h('div', {class: 'form-actions'}, [
        btn(busy ? '提交中…' : '保存', {variant: 'primary', icon: 'check',
          disabled: busy, onClick: submitResearchUpdate}),
        btn('取消', {onClick: function () { goBack(); }})
      ])
    ]));
    nodes.push(h('p', {class: 'footnote', text: draft.head
      ? '这次写入以 head（' + safeLabel(draft.head, 60) + '）为基准；别处先改过就会被拒绝。'
      : '服务没有给出 head：这次修改没有冲突保护，不能保存。'}));
    return page('写研究', '引用与标记由服务保留', nodes);
  }

  function openMaterial(artifactId) {
    const id = protocolId(artifactId, 200);
    if (!id.ok) return;
    if (store.data.material && store.data.material.artifactId !== id.value) store.data.material = null;
    store.result = null;
    go('material', {detail: id.value});
  }

  function startImportMaterial() {
    store.materialDraft = {title: '', text: '', source_kind: 'user_supplied', origin: '',
      query: store.materialDraft.query, errors: []};
    store.result = null;
    go('material-import', {detail: null});
  }

  function submitMaterialSearch() {
    store.result = null;
    loadMaterials(store.materialDraft.query);
  }

  function openResearch(artifactId) {
    const id = protocolId(artifactId, 200);
    if (!id.ok) return;
    if (store.data.researchItem && store.data.researchItem.artifactId !== id.value) {
      store.data.researchItem = null;
    }
    store.result = null;
    go('research-item', {detail: id.value});
  }

  function blankResearchDraft() {
    return {mode: 'create', artifactId: '', title: '', question: '', scope: '',
      uncertainty: '', content: '', head: '', revision: null, sources: [], errors: []};
  }

  function startResearch() {
    store.researchDraft = blankResearchDraft();
    store.result = null;
    go('research-create', {detail: null});
  }

  function startResearchWith(refs) {
    store.researchDraft = blankResearchDraft();
    (refs || []).forEach(function (ref) {
      if (ref && ref.artifact_id) store.researchDraft.sources.push(ref);
    });
    store.result = null;
    go('research-create', {detail: null});
  }

  function toggleResearchSource(material, chosen) {
    const draft = store.researchDraft;
    if (!draft) return;
    if (chosen) {
      draft.sources = draft.sources.filter(function (ref) { return ref.artifact_id !== material.id; });
    } else {
      if (draft.sources.length >= RESEARCH_SOURCES_MAX) {
        store.result = {scope: MATERIAL_SCOPES.create, status: 'blocked',
          message: '来源最多 ' + RESEARCH_SOURCES_MAX + ' 件。'};
        renderAll();
        return;
      }
      draft.sources.push({artifact_id: material.id, source_revision: material.sourceRevision});
    }
    renderAll();
  }

  function editResearch(view) {
    store.researchDraft = {
      mode: 'edit', artifactId: view.project.id, title: view.project.title,
      question: view.question, scope: view.scope, uncertainty: view.uncertainty,
      content: view.body, head: view.project.head, revision: view.project.revision,
      sources: view.refs.map(function (ref) {
        return {artifact_id: ref.artifactId, source_revision: ref.sourceRevision};
      }), errors: []
    };
    store.result = null;
    go('research-edit', {detail: view.project.id});
  }

  async function loadMaterials(query) {
    const path = materialsListPath({query: query || ''});
    if (!path) {
      store.data.materials = {phase: 'error', doc: null, message: '检索词太长', artifactId: ''};
      renderAll();
      return;
    }
    store.data.materials = {phase: 'loading', doc: null, message: ''};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.materials = {phase: 'ready', doc: outcome.doc, message: ''};
    } else {
      store.data.materials = {
        phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status
      };
    }
    renderAll();
  }

  async function loadMaterial(artifactId) {
    const id = protocolId(artifactId, 200);
    const path = id.ok ? materialPath(id.value) : '';
    if (!path) {
      store.data.material = {phase: 'error', doc: null, message: '资料 id 不可用',
        artifactId: String(artifactId)};
      renderAll();
      return;
    }
    store.data.material = {phase: 'loading', doc: null, message: '', artifactId: id.value};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.material = {phase: 'ready', doc: outcome.doc, message: '', artifactId: id.value};
    } else {
      store.data.material = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status, artifactId: id.value};
    }
    renderAll();
  }

  async function loadResearch() {
    store.data.research = {phase: 'loading', doc: null, message: ''};
    renderAll();
    const res = await request('GET', '/research', {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.research = {phase: 'ready', doc: outcome.doc, message: ''};
    } else {
      store.data.research = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status};
    }
    renderAll();
  }

  async function loadResearchItem(artifactId) {
    const id = protocolId(artifactId, 200);
    const path = id.ok ? researchPath(id.value) : '';
    if (!path) {
      store.data.researchItem = {phase: 'error', doc: null, message: '研究 id 不可用',
        artifactId: String(artifactId)};
      renderAll();
      return;
    }
    store.data.researchItem = {phase: 'loading', doc: null, message: '', artifactId: id.value};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.researchItem = {phase: 'ready', doc: outcome.doc, message: '',
        artifactId: id.value};
    } else {
      store.data.researchItem = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status, artifactId: id.value};
    }
    renderAll();
  }

  async function submitMaterialImport() {
    const draft = store.materialDraft;
    if (!draft) return;
    const checked = validateMaterialDraft(draft);
    if (!checked.ok) {
      draft.errors = checked.errors;
      renderAll();
      return;
    }
    draft.errors = [];
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: MATERIAL_SCOPES.import, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = MATERIAL_SCOPES.import;
    const entry = ops.ensure(scope, {path: '/materials'});
    const payload = buildMaterialImportPayload({
      title: checked.value.title, text: checked.value.text,
      source_kind: checked.value.source_kind, origin: checked.value.origin,
      operation_key: entry.key
    });
    if (!payload) {
      store.result = {scope: scope, status: 'blocked', message: '这次导入缺少标题、正文或来源声明，没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在保存资料…', key: entry.key};
    renderAll();
    const res = await request('POST', '/materials', {csrf: store.conn.csrf, body: payload});
    const created = res && res.doc && res.doc.data && res.doc.data.material
      && typeof res.doc.data.material.artifact_id === 'string'
      ? res.doc.data.material.artifact_id : '';
    finishMutation(scope, deriveMutationOutcome(res), '导入资料');
    if (created) {
      store.materialDraft = {title: '', text: '', source_kind: 'user_supplied', origin: '',
        query: store.materialDraft.query, errors: []};
      store.data.materials = null;
      openMaterial(created);
    }
  }

  async function submitResearchDraft() {
    const draft = store.researchDraft;
    if (!draft) return;
    const checked = validateResearchDraft({
      question: draft.question, scope: draft.scope, uncertainty: draft.uncertainty,
      source_refs: draft.sources
    });
    if (!checked.ok) {
      draft.errors = checked.errors;
      renderAll();
      return;
    }
    draft.errors = [];
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: MATERIAL_SCOPES.create, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = MATERIAL_SCOPES.create;
    const entry = ops.ensure(scope, {path: '/research'});
    const payload = buildResearchCreatePayload({
      question: checked.value.question, source_refs: checked.value.source_refs,
      title: draft.title, scope: checked.value.scope,
      uncertainty: checked.value.uncertainty, operation_key: entry.key
    });
    if (!payload) {
      store.result = {scope: scope, status: 'blocked', message: '这次创建缺少问题或来源，没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在建这一份研究…', key: entry.key};
    renderAll();
    const res = await request('POST', '/research', {csrf: store.conn.csrf, body: payload});
    const created = res && res.doc && res.doc.data && res.doc.data.project
      && typeof res.doc.data.project.artifact_id === 'string'
      ? res.doc.data.project.artifact_id : '';
    finishMutation(scope, deriveMutationOutcome(res), '创建研究');
    if (created) {
      store.researchDraft = blankResearchDraft();
      store.data.research = null;
      openResearch(created);
    }
  }

  async function submitResearchUpdate() {
    const draft = store.researchDraft;
    if (!draft || draft.mode !== 'edit') return;
    const checked = validateResearchUpdateDraft(draft);
    if (!checked.ok) {
      draft.errors = checked.errors;
      renderAll();
      return;
    }
    draft.errors = [];
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: MATERIAL_SCOPES.update, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    if (!draft.head) {
      store.result = {scope: MATERIAL_SCOPES.update, status: 'blocked',
        message: '服务没有给出 head，这次修改没有冲突保护，不能保存。'};
      renderAll();
      return;
    }
    const scope = MATERIAL_SCOPES.update;
    const path = researchUpdatesPath(draft.artifactId);
    if (!path) {
      store.result = {scope: scope, status: 'blocked', message: '研究 id 不可用，没有发出。'};
      renderAll();
      return;
    }
    const entry = ops.ensure(scope, {path: '/research/updates'});
    const payload = buildResearchUpdatePayload({
      content: checked.value.content, expected_head: draft.head,
      scope: checked.value.scope, uncertainty: checked.value.uncertainty,
      operation_key: entry.key
    });
    if (!payload) {
      store.result = {scope: scope, status: 'blocked', message: '这次修改缺少正文或 head，没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在提交新版本…', key: entry.key};
    renderAll();
    const res = await request('POST', path, {csrf: store.conn.csrf, body: payload});
    finishMutation(scope, deriveMutationOutcome(res), '研究续写');
    const writeStatus = store.result && store.result.status;
    if (writeStatus === 'applied' || writeStatus === 'already_applied') {
      store.researchDraft = blankResearchDraft();
      store.data.researchItem = null;
      go('research-item', {detail: draft.artifactId});
    }
  }

  function confirmCloseResearch(view) {
    openSheet({
      title: '关闭这一份研究？',
      lines: [view.project.title],
      note: '关闭只是归档：正文、每个版本和来源引用都保留，之后还能读回来；但不再续写。',
      confirmLabel: '关闭',
      onConfirm: function () { submitResearchClose(view); }
    });
  }

  async function submitResearchClose(view) {
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: MATERIAL_SCOPES.close, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = MATERIAL_SCOPES.close;
    const path = researchClosePath(view.project.id);
    const entry = ops.ensure(scope, {path: '/research/close'});
    const payload = buildResearchClosePayload({
      expected_revision: view.project.revision, operation_key: entry.key
    });
    if (!path || !payload) {
      store.result = {scope: scope, status: 'blocked',
        message: '服务没有给出这一份的 revision，这次关闭没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在关闭…', key: entry.key};
    renderAll();
    const res = await request('POST', path, {csrf: store.conn.csrf, body: payload});
    finishMutation(scope, deriveMutationOutcome(res), '关闭研究');
    const writeStatus = store.result && store.result.status;
    if (writeStatus === 'applied' || writeStatus === 'already_applied') {
      store.data.researchItem = null;
      store.data.research = null;
      loadResearchItem(view.project.id);
    }
  }

  /* ------------------------------------------------------------------ *
   * 个人实践工作台（B 的 personal_practice_service 消费端）+ Memory
   *
   * 契约：docs/engineering/v04-practice-workspace-result.md
   * 屏幕上必须能分清：草案（未验证）/ 试过但仍未验证 / 有反例 / 已退休；
   * 证据读不回来时只显示元数据与核对结果，不显示那一份的正文。
   * ------------------------------------------------------------------ */

  function practiceGate() {
    const gate = appGate('projects');
    if (!gate) return null;
    if (store.conn.phase === 'loading' || store.conn.phase === 'offline' ||
        store.conn.phase === 'error') return gate;
    return null;
  }

  function practiceStateChip(state) {
    const tone = state === 'CONTESTED' ? 'is-warn'
      : (state === 'TRIAL' ? 'is-neutral'
        : (state === 'RETIRED' ? 'is-warn' : 'is-neutral'));
    return chip(practiceStateText(state), tone);
  }

  function practiceCheckChip(check) {
    return chip(check.statusText, check.usable ? 'is-ok' : 'is-warn');
  }

  function practiceReviewRow(row) {
    const inner = [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: row.title}),
        h('span', {class: 'list-meta', text: row.revision !== null ? 'rev ' + row.revision : ''})
      ]),
      h('span', {class: 'list-chips'}, [
        chip('回顾', 'is-neutral'),
        chip('经历 ' + row.experienceCount, 'is-neutral'),
        row.experienceCount ? null : chip('无经历基础', 'is-warn'),
        chip(formatWhen(row.updatedAt) || '时间未提供', 'is-neutral')
      ]),
      row.question ? h('span', {class: 'list-note', text: row.question}) : null
    ];
    return h('li', {}, [
      h('button', {class: 'list-item', type: 'button',
        on: {click: function () { openPracticeReview(row.id); }}}, inner)
    ]);
  }

  function practiceMethodRow(row) {
    const inner = [
      h('span', {class: 'list-top'}, [
        h('span', {class: 'list-title', text: row.title}),
        h('span', {class: 'list-meta', text: 'v' + (row.versionNumber || 1)})
      ]),
      h('span', {class: 'list-chips'}, [
        practiceStateChip(row.methodState || 'TENTATIVE'),
        chip('经历 ' + row.experienceCount, 'is-neutral'),
        row.reviewCount ? chip('回顾 ' + row.reviewCount, 'is-neutral') : null,
        row.counterexampleCount ? chip('反例 ' + row.counterexampleCount, 'is-warn') : null,
        chip(formatWhen(row.updatedAt) || '时间未提供', 'is-neutral')
      ]),
      row.instruction ? h('span', {class: 'list-note', text: row.instruction}) : null
    ];
    return h('li', {}, [
      h('button', {class: 'list-item', type: 'button',
        on: {click: function () { openPracticeMethod(row.id); }}}, inner)
    ]);
  }

  function experienceRowNode(row) {
    return h('li', {}, [
      h('button', {class: 'list-item', type: 'button',
        on: {click: function () { openExperience(row.id); }}}, [
        h('span', {class: 'list-top'}, [
          h('span', {class: 'list-title', text: row.excerpt || row.id}),
          h('span', {class: 'list-meta', text: row.outcomeKind || '经历'})
        ]),
        h('span', {class: 'list-chips'}, [
          chip(row.occurredAt || '时间未提供', 'is-neutral'),
          row.owner ? chip('owner ' + row.owner, 'is-neutral') : null
        ])
      ])
    ]);
  }

  /* -- 选经历 / 选回顾（草稿里的多选，只在内存里） ------------------------------------ */

  function togglePracticeExperience(experienceId) {
    const draft = store.practiceDraft;
    if (!draft) return;
    const list = draft.experience_ids.slice();
    const at = list.indexOf(experienceId);
    if (at >= 0) list.splice(at, 1);
    else if (list.length >= PRACTICE_REFS_MAX) {
      store.result = {scope: PRACTICE_SCOPES[draft.mode === 'method-new' ? 'methodCreate'
        : 'reviewCreate'], status: 'blocked',
        message: '引用最多 ' + PRACTICE_REFS_MAX + ' 条。'};
      renderAll();
      return;
    } else list.push(experienceId);
    draft.experience_ids = list;
    renderAll();
  }

  function togglePracticeReviewRef(artifactId) {
    const draft = store.practiceDraft;
    if (!draft) return;
    const list = draft.review_refs.slice();
    const at = list.indexOf(artifactId);
    if (at >= 0) list.splice(at, 1);
    else if (list.length >= PRACTICE_REFS_MAX) {
      store.result = {scope: PRACTICE_SCOPES.methodCreate, status: 'blocked',
        message: '引用最多 ' + PRACTICE_REFS_MAX + ' 条。'};
      renderAll();
      return;
    } else list.push(artifactId);
    draft.review_refs = list;
    renderAll();
  }

  /* -- 工作台主屏 ------------------------------------------------------------------ */

  function renderPractice() {
    const gate = practiceGate();
    if (gate) return page('实践', '回顾、做法与 Memory', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('起一份回顾', {variant: 'primary', icon: 'plus', onClick: startPracticeReview}),
      btn('写一条做法', {icon: 'plus', onClick: startPracticeMethod}),
      btn('刷新', {icon: 'refresh', onClick: function () { loadPractice(); }})
    ]));
    if (store.conn.phase === 'readonly') {
      nodes.push(statePanel('readonly', '只读模式：能看不能写', '服务没有提供 CSRF 令牌。'));
    }

    const entry = store.data.practice;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读实践工作台…'));
      return page('实践', '回顾、做法与 Memory', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error',
        '没有读到实践工作台', entry.message,
        [btn('重试', {onClick: function () { loadPractice(); }})]));
      return page('实践', '回顾、做法与 Memory', nodes);
    }
    const view = practiceListView(entry.doc);
    const memory = view.memory;

    /* Memory 块 */
    const memoryNodes = [h('h2', {class: 'card-title', text: 'Memory（这个实例自己的）'})];
    memoryNodes.push(h('div', {class: 'row-actions'}, [
      chip(memory.statusText, memory.bindingStatus === 'READY' ? 'is-ok' : 'is-warn'),
      memory.recallReady ? chip('可召回', 'is-ok') : chip('不可召回', 'is-warn'),
      memory.owner ? chip('owner ' + memory.owner, 'is-neutral') : null
    ]));
    if (memory.reason) memoryNodes.push(kv('原因', memory.reason, true));
    if (memory.bindingStatus !== 'READY') {
      memoryNodes.push(statePanel('warn', 'Memory 还没绑定好',
        memory.bindingStatus === 'UNBOUND'
          ? '这个实例没有自己的 Memory：回顾可以写，但不能声称已证经验。'
          : '绑定存在但读不回来：经历、回顾与做法的证据都按“读不到”处理。'));
      if (memory.enableAvailable) {
        memoryNodes.push(h('div', {class: 'row-actions'}, [
          btn('显式启用 Memory', {variant: 'primary', icon: 'check',
            onClick: confirmEnableMemory})
        ]));
      }
    }
    memoryNodes.push(h('p', {class: 'footnote', text: memory.note}));
    nodes.push(h('div', {class: 'card'}, memoryNodes));

    /* 经历检索 */
    const experienceNodes = [h('h2', {class: 'card-title', text: '经历库'})];
    const queryInput = h('input', {class: 'input', type: 'search', maxlength: PRACTICE_QUERY_MAX,
      autocomplete: 'off', placeholder: '按内容检索她真正经历过的结果', value: store.memoryDraft.query});
    queryInput.addEventListener('input', function () { store.memoryDraft.query = queryInput.value; });
    const searchForm = h('form', {class: 'field'}, [
      h('span', {class: 'field-label', text: '检索'}),
      queryInput,
      h('button', {class: 'btn btn-ghost', type: 'submit'}, [h('span', {text: '检索'})])
    ]);
    searchForm.addEventListener('submit', function (event) {
      if (event && event.preventDefault) event.preventDefault();
      loadExperiences(store.memoryDraft.query);
    });
    experienceNodes.push(searchForm);
    const experienceEntry = store.data.experiences;
    if (!experienceEntry || experienceEntry.phase === 'loading') {
      experienceNodes.push(statePanel('loading', '正在读经历…'));
    } else if (experienceEntry.phase !== 'ready') {
      experienceNodes.push(statePanel(experienceEntry.phase === 'offline' ? 'offline' : 'error',
        '没有读到经历', experienceEntry.message,
        [btn('重试', {onClick: function () { loadExperiences(store.memoryDraft.query); }})]));
    } else {
      const experienceView = experienceListView(experienceEntry.doc);
      if (experienceView.itemsMissing) {
        experienceNodes.push(statePanel('error', '服务没有返回经历列表',
          '响应里没有 data.experiences 数组（未绑定 Memory 也会这样回答）。'));
      } else if (!experienceView.experiences.length) {
        experienceNodes.push(statePanel('empty', '没有可召回的记录',
          '安静也是合法的：这段时间没有已确认的结果，就没有经历。'));
      } else {
        experienceNodes.push(h('ul', {class: 'list'},
          experienceView.experiences.map(experienceRowNode)));
      }
    }
    experienceNodes.push(h('p', {class: 'footnote',
      text: '经历只来自已确认的结果；被撤回、删除或停止召回的记录读不回来，也不会出现在这里。'}));
    nodes.push(h('div', {class: 'card'}, experienceNodes));

    /* 回顾 */
    const reviewNodes = [h('h2', {class: 'card-title', text: '回顾'})];
    if (view.itemsMissing) {
      reviewNodes.push(statePanel('error', '服务没有返回回顾列表', '响应里没有 data.reviews 数组。'));
    } else if (!view.reviews.length) {
      reviewNodes.push(statePanel('empty', '还没有回顾',
        '回顾是一个具体问题 + 她真正能读回来的经历；不是每日任务。',
        [btn('起一份回顾', {variant: 'primary', onClick: startPracticeReview})]));
    } else {
      reviewNodes.push(h('ul', {class: 'list'}, view.reviews.map(practiceReviewRow)));
    }
    reviewNodes.push(h('p', {class: 'footnote',
      text: '回顾不是 Memory，也不是稳定信念；它引用哪条经历，读的时候就核对到哪条。'}));
    nodes.push(h('div', {class: 'card'}, reviewNodes));

    /* 做法 */
    const methodNodes = [h('h2', {class: 'card-title', text: '做法（草案）'})];
    if (view.itemsMissing) {
      methodNodes.push(statePanel('error', '服务没有返回做法列表', '响应里没有 data.methods 数组。'));
    } else if (!view.methods.length) {
      methodNodes.push(statePanel('empty', '还没有做法草案',
        '做法是一条具体可试的东西：有范围、有未知，默认就是“未验证”。',
        [btn('写一条做法', {variant: 'primary', onClick: startPracticeMethod})]));
    } else {
      methodNodes.push(h('ul', {class: 'list'}, view.methods.map(practiceMethodRow)));
    }
    methodNodes.push(h('p', {class: 'footnote',
      text: '草案不等于已验证 Skill，也不是人格或偏好：试过只到「试过」，有反例就是「有反例」。'}));
    nodes.push(h('div', {class: 'card'}, methodNodes));

    if (view.reviewTruncated || view.methodTruncated) {
      nodes.push(h('p', {class: 'footnote', text: '有一份列表已截断。'}));
    }
    return page('实践', '回顾、做法与 Memory（都可追溯）', nodes);
  }

  /* -- 回顾详情 / 写过 -------------------------------------------------------------- */

  function renderPracticeReview() {
    const gate = practiceGate();
    if (gate) return page('回顾', '', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const entry = store.data.practiceReview;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读这一份回顾…'));
      return page('回顾', '', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到这一份回顾',
        entry.message, [btn('重试', {onClick: function () { loadPracticeReview(entry.artifactId); }})]));
      return page('回顾', '', nodes);
    }
    const view = practiceDetailView(entry.doc);
    if (!view) {
      nodes.push(statePanel('error', '服务没有返回这一份回顾', '响应里没有 data.project 对象。'));
      return page('回顾', '', nodes);
    }
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: view.project.title}),
      h('div', {class: 'row-actions'}, [
        chip('回顾', 'is-neutral'),
        view.project.status === 'archived' ? chip('已归档', 'is-warn') : null,
        chip('v' + (view.project.versionNumber || 1), 'is-neutral'),
        view.project.revision !== null ? chip('rev ' + view.project.revision, 'is-neutral') : null,
        view.blocked ? chip('正文已停止透出', 'is-warn') : null
      ]),
      kv('问题', view.question || '服务未提供'),
      kv('当前 head', view.project.head || '服务未提供', true),
      kv('最近更新', formatWhen(view.project.updatedAt) || '服务未提供')
    ]));

    const checkNodes = [h('h2', {class: 'card-title', text: '引用的经历与现在的可读性'})];
    if (!view.checks.length) {
      checkNodes.push(statePanel('warn', '这一份没有引用任何经历',
        '它是一份计划/问题，不是已证经验的记录。'));
    } else {
      checkNodes.push(h('ul', {class: 'list'}, view.checks.map(function (check) {
        return h('li', {}, [
          h('div', {class: 'list-item is-static'}, [
            h('span', {class: 'list-top'}, [
              h('span', {class: 'list-title', text: check.excerpt || check.id}),
              practiceCheckChip(check)
            ]),
            h('span', {class: 'list-chips'}, [
              chip(check.occurredAt || '时间未提供', 'is-neutral'),
              check.reason ? chip(check.reason, 'is-warn') : null
            ])
          ])
        ]);
      })));
      if (view.unusableCount) {
        checkNodes.push(statePanel('warn', '有证据读不回来了',
          '依赖这些经历的正文不再透出：元数据还在，正文按治理停止。'));
      }
    }
    nodes.push(h('div', {class: 'card'}, checkNodes));

    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '正文'}),
      view.contentAvailable
        ? h('pre', {class: 'practice-text', text: view.body || '（这一份还没有正文）'})
        : statePanel('warn', '这一版的正文已停止透出',
          view.blockedDetail || '引用的经历读不回来了，正文按治理不再显示。'),
      h('p', {class: 'footnote',
        text: '第一行是机器可读标记（问题与引用），界面只显示正文；改写由服务重建标记。'})
    ]));

    const actions = [btn('刷新', {icon: 'refresh',
      onClick: function () { loadPracticeReview(view.project.id); }})];
    if (view.project.status === 'active' && !view.blocked) {
      actions.push(btn('写这一份', {variant: 'primary', icon: 'note',
        onClick: function () { editPracticeReview(view); }}));
    }
    nodes.push(h('div', {class: 'row-actions'}, actions));
    nodes.push(h('p', {class: 'footnote', text: view.note || '回顾不是 Memory，也不是稳定信念。'}));
    return page('回顾', view.kindText + ' · 问题、经历与未知', nodes);
  }

  function renderPracticeReviewNew() {
    const gate = practiceGate();
    if (gate) return page('起一份回顾', '', [gate]);
    const draft = store.practiceDraft;
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    if (draft.errors && draft.errors.length) {
      nodes.push(statePanel('warn', '这份回顾还没填好', draft.errors.join('；')));
    }
    const questionInput = h('input', {class: 'input', type: 'text',
      maxlength: PRACTICE_QUESTION_MAX, autocomplete: 'off',
      placeholder: '这一份要回答的具体问题', value: draft.question});
    questionInput.addEventListener('input', function () { draft.question = questionInput.value; });
    const titleInput = h('input', {class: 'input', type: 'text', maxlength: PRACTICE_TITLE_MAX,
      autocomplete: 'off', placeholder: '可留空，默认用问题当标题', value: draft.title});
    titleInput.addEventListener('input', function () { draft.title = titleInput.value; });
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '问题与标题'}),
      field('问题', questionInput, '一个问题；不填就不算一份回顾。'),
      field('标题', titleInput, '可留空。')
    ]));

    const experienceNodes = [h('h2', {class: 'card-title', text: '引用哪些经历'})];
    const entry = store.data.experiences;
    if (!entry || entry.phase === 'loading') {
      experienceNodes.push(statePanel('loading', '正在读经历…'));
    } else if (entry.phase !== 'ready') {
      experienceNodes.push(statePanel('error', '没有读到经历，不能凭空编证据',
        entry.message, [btn('重试', {onClick: function () { loadExperiences(''); }})]));
    } else {
      const listView = experienceListView(entry.doc);
      if (!listView.experiences.length) {
        experienceNodes.push(statePanel('empty', '没有可引用的经历',
          '没有已确认的结果时，这份回顾只能是一份计划（不引用任何经历）。'));
      } else {
        experienceNodes.push(h('ul', {class: 'list'}, listView.experiences.map(function (row) {
          const chosen = draft.experience_ids.indexOf(row.id) >= 0;
          return h('li', {}, [
            h('button', {class: 'list-item', type: 'button',
              on: {click: function () { togglePracticeExperience(row.id); }}}, [
              h('span', {class: 'list-top'}, [
                h('span', {class: 'list-title', text: row.excerpt || row.id}),
                chosen ? chip('已选', 'is-ok') : chip('未选', 'is-neutral')
              ]),
              h('span', {class: 'list-chips'}, [
                chip(row.occurredAt || '时间未提供', 'is-neutral'),
                row.outcomeKind ? chip(row.outcomeKind, 'is-neutral') : null
              ])
            ])
          ]);
        })));
      }
    }
    experienceNodes.push(h('p', {class: 'footnote',
      text: '创建前服务会真的逐条读这些经历；读不回来就会拒绝，不会写进一份“已证经验”。'}));
    nodes.push(h('div', {class: 'card'}, experienceNodes));
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('创建这份回顾', {variant: 'primary', icon: 'check', onClick: submitPracticeReviewCreate}),
      btn('回到实践', {onClick: function () { go('practice'); }})
    ]));
    return page('起一份回顾', '问题 + 她真正能读回来的经历', nodes);
  }

  function renderPracticeReviewEdit() {
    const gate = practiceGate();
    if (gate) return page('写回顾', '', [gate]);
    const draft = store.practiceDraft;
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    if (draft.errors && draft.errors.length) {
      nodes.push(statePanel('warn', '这次还没保存', draft.errors.join('；')));
    }
    const bodyInput = h('textarea', {class: 'input practice-input', rows: 16,
      maxlength: PRACTICE_CONTENT_MAX, placeholder: '这一份的正文'});
    bodyInput.value = draft.content;
    bodyInput.addEventListener('input', function () { draft.content = bodyInput.value; });
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '正文'}),
      field('正文', bodyInput, '第一行的机器标记由服务重建；这里只写正文。'),
      kv('这次写在哪一版之后', draft.head || '服务未提供 head（不能保存）', true)
    ]));
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('保存新版本', {variant: 'primary', icon: 'check', onClick: submitPracticeDocument}),
      btn('不写，回到这一份', {onClick: function () { go('practice-review', {detail: draft.artifactId}); }})
    ]));
    return page('写回顾', '保留标记，只改正文', nodes);
  }

  /* -- 做法详情 / 新建 / 修订 ------------------------------------------------------- */

  function renderPracticeMethod() {
    const gate = practiceGate();
    if (gate) return page('做法', '', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const entry = store.data.practiceMethod;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读这一条做法…'));
      return page('做法', '', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到这一条做法',
        entry.message, [btn('重试', {onClick: function () { loadPracticeMethod(entry.artifactId); }})]));
      return page('做法', '', nodes);
    }
    const view = practiceDetailView(entry.doc);
    if (!view) {
      nodes.push(statePanel('error', '服务没有返回这一条做法', '响应里没有 data.project 对象。'));
      return page('做法', '', nodes);
    }
    const state = view.methodState || 'TENTATIVE';
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: view.project.title}),
      h('div', {class: 'row-actions'}, [
        practiceStateChip(state),
        chip('v' + (view.project.versionNumber || 1), 'is-neutral'),
        view.revisions ? chip('修订 ' + view.revisions, 'is-neutral') : null,
        view.counterexamples.length ? chip('反例 ' + view.counterexamples.length, 'is-warn') : null,
        view.blocked ? chip('正文已停止透出', 'is-warn') : null
      ]),
      kv('具体做法', view.instruction || '服务未提供'),
      kv('适用条件', view.scope || (view.scopeMissing ? '还没有划定范围' : '服务未提供')),
      kv('我还不知道的', view.uncertainty || PRACTICE_DEFAULT_UNCERTAINTY),
      kv('当前 head', view.project.head || '服务未提供', true),
      kv('最近更新', formatWhen(view.project.updatedAt) || '服务未提供')
    ]));
    if (state === 'TENTATIVE') {
      nodes.push(statePanel('warn', '草案：还没有被试用验证',
        '这是一条可以试的做法，不是已验证的 Skill，也不是人格或稳定偏好。'));
    } else if (state === 'TRIAL') {
      nodes.push(statePanel('warn', '试过，但仍未验证',
        '有带证据的试用记录，但“试过”不等于“稳定成立”。'));
    } else if (state === 'CONTESTED') {
      nodes.push(statePanel('warn', '有反例：这一条至少有一次没起作用',
        '反例不会被抹掉；要不要改写这条做法，由后面的判断决定。'));
    } else if (state === 'RETIRED') {
      nodes.push(statePanel('warn', '已退休',
        '文档、每个版本与反例都保留；退休不是删除。'));
    }

    const checkNodes = [h('h2', {class: 'card-title', text: '证据与现在的可读性'})];
    if (!view.checks.length) {
      checkNodes.push(statePanel('warn', '这一条没有引用经历或回顾',
        '它只是一张草案：没有任何证据。'));
    } else {
      checkNodes.push(h('ul', {class: 'list'}, view.checks.map(function (check) {
        return h('li', {}, [
          h('div', {class: 'list-item is-static'}, [
            h('span', {class: 'list-top'}, [
              h('span', {class: 'list-title',
                text: check.kind === 'review' ? (check.title || check.id) : (check.excerpt || check.id)}),
              practiceCheckChip(check)
            ]),
            h('span', {class: 'list-chips'}, [
              chip(check.kindText, 'is-neutral'),
              check.reason ? chip(check.reason, 'is-warn') : null
            ])
          ])
        ]);
      })));
      if (view.unusableCount) {
        checkNodes.push(statePanel('warn', '有证据读不回来了',
          '这一条的正文与推断都不再透出：只剩下元数据与核对结果。'));
      }
    }
    nodes.push(h('div', {class: 'card'}, checkNodes));

    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '正文'}),
      view.contentAvailable
        ? h('pre', {class: 'practice-text', text: view.body || '（这一条还没有正文）'})
        : statePanel('warn', '这一版的正文已停止透出',
          view.blockedDetail || '引用的证据读不回来了，正文按治理不再显示。')
    ]));

    const trialNodes = [h('h2', {class: 'card-title', text: '试用记录'})];
    if (!view.trials.length) {
      trialNodes.push(statePanel('empty', '还没有试用记录', '记录是评价，不改原事实。'));
    } else {
      trialNodes.push(h('ul', {class: 'list'}, view.trials.map(function (trial) {
        return h('li', {}, [
          h('div', {class: 'list-item is-static'}, [
            h('span', {class: 'list-top'}, [
              h('span', {class: 'list-title', text: trial.outcomeText}),
              trial.counterexample ? chip('反例', 'is-warn') : null,
              trial.evidenceUsable ? chip('带回读证据', 'is-ok') : chip('无证据', 'is-warn')
            ]),
            trial.detail ? h('span', {class: 'list-note', text: trial.detail}) : null,
            trial.versionId ? h('span', {class: 'list-chips'},
              [chip(trial.versionId, 'is-mono')]) : null
          ])
        ]);
      })));
    }
    nodes.push(h('div', {class: 'card'}, trialNodes));

    /* 试用表单：只在这一屏里，只写一次（CAS head 来自刚读到的那一版）。 */
    if (state !== 'RETIRED' && view.project.status === 'active' && !view.blocked) {
      const draft = store.practiceDraft;
      const formNodes = [h('h2', {class: 'card-title', text: '记一次试用'})];
      formNodes.push(h('div', {class: 'row-actions'}, PRACTICE_TRIAL_OUTCOMES.map(function (outcome) {
        const chosen = draft.outcome === outcome;
        return btn(PRACTICE_OUTCOME_TEXT[outcome], {small: true,
          variant: chosen ? 'mint' : 'ghost',
          onClick: function () { draft.outcome = outcome; renderAll(); }});
      })));
      const detailInput = h('input', {class: 'input', type: 'text',
        maxlength: PRACTICE_TRIAL_DETAIL_MAX, autocomplete: 'off',
        placeholder: '这一次发生了什么（可留空）', value: draft.detail});
      detailInput.addEventListener('input', function () { draft.detail = detailInput.value; });
      formNodes.push(field('说明', detailInput, '写当时的实际情况，不写“大概有用”。'));
      const evidenceEntry = store.data.experiences;
      formNodes.push(h('p', {class: 'field-label', text: '这次依据哪条经历（有效/无效必须有）'}));
      if (!evidenceEntry || evidenceEntry.phase !== 'ready') {
        formNodes.push(statePanel('loading', '正在读经历…'));
      } else {
        const listView = experienceListView(evidenceEntry.doc);
        if (!listView.experiences.length) {
          formNodes.push(statePanel('warn', '没有可读经历',
            '没有证据时只能记「说不好」。'));
        } else {
          formNodes.push(h('ul', {class: 'list'}, listView.experiences.map(function (row) {
            const chosen = draft.experience_ids.indexOf(row.id) >= 0;
            return h('li', {}, [
              h('button', {class: 'list-item', type: 'button',
                on: {click: function () { togglePracticeExperience(row.id); }}}, [
                h('span', {class: 'list-top'}, [
                  h('span', {class: 'list-title', text: row.excerpt || row.id}),
                  chosen ? chip('已选', 'is-ok') : chip('未选', 'is-neutral')
                ]),
                h('span', {class: 'list-chips'}, [chip(row.occurredAt || '时间未提供', 'is-neutral')])
              ])
            ]);
          })));
        }
      }
      const head = view.project.head || '';
      formNodes.push(h('div', {class: 'row-actions'}, [
        btn('记录这次试用', {variant: 'primary', icon: 'check', onClick: function () {
          submitPracticeTrial(head);
        }}),
        btn('修订做法', {icon: 'note', onClick: function () { startPracticeRevise(view); }})
      ]));
      formNodes.push(h('p', {class: 'footnote',
        text: '「有用 / 没用」都要有可读证据；否则只能记「说不好」，也不会因此变成已验证。'}));
      nodes.push(h('div', {class: 'card'}, formNodes));
    }

    const actions = [btn('刷新', {icon: 'refresh',
      onClick: function () { loadPracticeMethod(view.project.id); }})];
    if (state !== 'RETIRED' && view.project.status === 'active') {
      actions.push(btn('退休这一条', {icon: 'check',
        onClick: function () { confirmRetireMethod(view); }}));
    }
    nodes.push(h('div', {class: 'row-actions'}, actions));
    nodes.push(h('p', {class: 'footnote',
      text: view.note || '做法草案不是已验证 Skill，也不是人格或稳定信念。'}));
    return page('做法', '做法、条件、未知与证据', nodes);
  }

  function renderPracticeMethodNew() {
    const gate = practiceGate();
    if (gate) return page('写做法草案', '', [gate]);
    const draft = store.practiceDraft;
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    if (draft.errors && draft.errors.length) {
      nodes.push(statePanel('warn', '这条做法还没填好', draft.errors.join('；')));
    }
    const titleInput = h('input', {class: 'input', type: 'text', maxlength: PRACTICE_TITLE_MAX,
      autocomplete: 'off', placeholder: '一句话说清这条做法', value: draft.title});
    titleInput.addEventListener('input', function () { draft.title = titleInput.value; });
    const instructionInput = h('textarea', {class: 'input practice-input', rows: 6,
      maxlength: PRACTICE_INSTRUCTION_MAX, placeholder: '具体做什么，按什么顺序'});
    instructionInput.value = draft.instruction;
    instructionInput.addEventListener('input', function () {
      draft.instruction = instructionInput.value;
    });
    const scopeInput = h('input', {class: 'input', type: 'text', maxlength: PRACTICE_SCOPE_MAX,
      autocomplete: 'off', placeholder: '在什么条件下成立（可留空，但会标出来）', value: draft.scope});
    scopeInput.addEventListener('input', function () { draft.scope = scopeInput.value; });
    const uncertaintyInput = h('input', {class: 'input', type: 'text',
      maxlength: PRACTICE_UNCERTAINTY_MAX, autocomplete: 'off',
      placeholder: PRACTICE_DEFAULT_UNCERTAINTY, value: draft.uncertainty});
    uncertaintyInput.addEventListener('input', function () {
      draft.uncertainty = uncertaintyInput.value;
    });
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '做法、条件、未知'}),
      field('标题', titleInput),
      field('具体做法', instructionInput),
      field('适用条件', scopeInput, '不写就明确标成“还没有划定范围”。'),
      field('我还不知道的', uncertaintyInput, '默认就是“暂定，尚未验证”。'),
      h('p', {class: 'footnote',
        text: '新建的做法一定是 TENTATIVE：草案不等于已验证 Skill，也不会因为试了几次自动升级。'})
    ]));

    const evidenceNodes = [h('h2', {class: 'card-title', text: '依据（可选）'})];
    const entry = store.data.experiences;
    if (!entry || entry.phase === 'loading') {
      evidenceNodes.push(statePanel('loading', '正在读经历…'));
    } else if (entry.phase !== 'ready') {
      evidenceNodes.push(statePanel('error', '没有读到经历', entry.message,
        [btn('重试', {onClick: function () { loadExperiences(''); }})]));
    } else {
      const listView = experienceListView(entry.doc);
      if (!listView.experiences.length) {
        evidenceNodes.push(statePanel('empty', '没有可引用的经历', '这条做法只能是一张草案。'));
      } else {
        evidenceNodes.push(h('ul', {class: 'list'}, listView.experiences.map(function (row) {
          const chosen = draft.experience_ids.indexOf(row.id) >= 0;
          return h('li', {}, [
            h('button', {class: 'list-item', type: 'button',
              on: {click: function () { togglePracticeExperience(row.id); }}}, [
              h('span', {class: 'list-top'}, [
                h('span', {class: 'list-title', text: row.excerpt || row.id}),
                chosen ? chip('已选', 'is-ok') : chip('未选', 'is-neutral')
              ]),
              h('span', {class: 'list-chips'}, [chip(row.occurredAt || '时间未提供', 'is-neutral')])
            ])
          ]);
        })));
      }
    }
    const practiceEntry = store.data.practice;
    if (practiceEntry && practiceEntry.phase === 'ready') {
      const listView = practiceListView(practiceEntry.doc);
      if (listView.reviews.length) {
        evidenceNodes.push(h('p', {class: 'field-label', text: '或引用一份回顾'}));
        evidenceNodes.push(h('ul', {class: 'list'}, listView.reviews.map(function (row) {
          const chosen = draft.review_refs.indexOf(row.id) >= 0;
          return h('li', {}, [
            h('button', {class: 'list-item', type: 'button',
              on: {click: function () { togglePracticeReviewRef(row.id); }}}, [
              h('span', {class: 'list-top'}, [
                h('span', {class: 'list-title', text: row.title}),
                chosen ? chip('已选', 'is-ok') : chip('未选', 'is-neutral')
              ]),
              row.question ? h('span', {class: 'list-note', text: row.question}) : null
            ])
          ]);
        })));
      }
    }
    nodes.push(h('div', {class: 'card'}, evidenceNodes));
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('存成草案', {variant: 'primary', icon: 'check', onClick: submitPracticeMethodCreate}),
      btn('回到实践', {onClick: function () { go('practice'); }})
    ]));
    return page('写做法草案', '具体做法 + 条件 + 未知', nodes);
  }

  function renderPracticeMethodRevise() {
    const gate = practiceGate();
    if (gate) return page('修订做法', '', [gate]);
    const draft = store.practiceDraft;
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    if (draft.errors && draft.errors.length) {
      nodes.push(statePanel('warn', '这次修订还没保存', draft.errors.join('；')));
    }
    const instructionInput = h('textarea', {class: 'input practice-input', rows: 6,
      maxlength: PRACTICE_INSTRUCTION_MAX, placeholder: '修订后的具体做法'});
    instructionInput.value = draft.instruction;
    instructionInput.addEventListener('input', function () {
      draft.instruction = instructionInput.value;
    });
    const scopeInput = h('input', {class: 'input', type: 'text', maxlength: PRACTICE_SCOPE_MAX,
      autocomplete: 'off', placeholder: '适用条件', value: draft.scope});
    scopeInput.addEventListener('input', function () { draft.scope = scopeInput.value; });
    const uncertaintyInput = h('input', {class: 'input', type: 'text',
      maxlength: PRACTICE_UNCERTAINTY_MAX, autocomplete: 'off',
      placeholder: PRACTICE_DEFAULT_UNCERTAINTY, value: draft.uncertainty});
    uncertaintyInput.addEventListener('input', function () {
      draft.uncertainty = uncertaintyInput.value;
    });
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '修订后的做法'}),
      field('具体做法', instructionInput),
      field('适用条件', scopeInput),
      field('我还不知道的', uncertaintyInput),
      kv('这次修订基于哪一版', draft.head || '服务未提供 head（不能保存）', true),
      h('p', {class: 'footnote',
        text: '旧版本留在 owner 的历史里；修订后状态回到 TENTATIVE，反例一条都不会少。'})
    ]));
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('保存修订', {variant: 'primary', icon: 'check', onClick: submitPracticeRevision}),
      btn('不改，回到这一条', {onClick: function () { go('practice-method', {detail: draft.artifactId}); }})
    ]));
    return page('修订做法', '保留旧版本，状态回到草案', nodes);
  }

  /* -- 经历详情 -------------------------------------------------------------------- */

  function renderPracticeExperience() {
    const gate = practiceGate();
    if (gate) return page('经历', '', [gate]);
    const nodes = [];
    const result = resultCard();
    if (result) nodes.push(result);
    const entry = store.data.experience;
    if (!entry || entry.phase === 'loading') {
      nodes.push(statePanel('loading', '正在读这条经历…'));
      return page('经历', '', nodes);
    }
    if (entry.phase !== 'ready') {
      nodes.push(statePanel(entry.phase === 'offline' ? 'offline' : 'error', '没有读到这条经历',
        entry.message, [btn('重试', {onClick: function () { loadExperience(entry.experienceId); }})]));
      return page('经历', '', nodes);
    }
    const view = experienceDetailView(entry.doc);
    if (!view) {
      nodes.push(statePanel('error', '服务没有返回这条经历',
        '它可能已经被撤回、删除或停止召回 —— 服务不会回一条旧正文。'));
      return page('经历', '', nodes);
    }
    const row = view.experience;
    nodes.push(h('div', {class: 'card'}, [
      h('h2', {class: 'card-title', text: '她真正经历过的一件事'}),
      h('div', {class: 'row-actions'}, [
        chip(row.occurredAt || '时间未提供', 'is-neutral'),
        row.outcomeKind ? chip(row.outcomeKind, 'is-neutral') : null,
        row.owner ? chip('owner ' + row.owner, 'is-neutral') : null
      ]),
      h('pre', {class: 'practice-text', text: row.text || '（服务没有返回正文）'}),
      kv('经历 id', row.id, true),
      kv('来源', row.sourceRef || '服务未提供', true),
      row.activityRef ? kv('活动', row.activityRef, true) : null,
      row.artifactRef ? kv('工件', row.artifactRef, true) : null,
      row.versionRef ? kv('版本', row.versionRef, true) : null
    ]));
    nodes.push(h('p', {class: 'footnote',
      text: '经历来自已确认的结果；它只在她自己的 Memory 里，未绑定 Memory 时读不到。'}));
    nodes.push(h('div', {class: 'row-actions'}, [
      btn('回到实践', {onClick: function () { go('practice'); }})
    ]));
    return page('经历', '来源、时间与结果', nodes);
  }

  /* -- 载入 / 打开 ------------------------------------------------------------------ */

  async function loadPractice() {
    store.data.practice = {phase: 'loading', doc: null, message: ''};
    renderAll();
    const res = await request('GET', '/practice', {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.practice = {phase: 'ready', doc: outcome.doc, message: ''};
    } else {
      store.data.practice = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status};
    }
    renderAll();
  }

  async function loadPracticeReview(artifactId) {
    const id = protocolId(artifactId, 200);
    const path = id.ok ? practiceReviewPath(id.value) : '';
    if (!path) {
      store.data.practiceReview = {phase: 'error', doc: null, message: '回顾 id 不可用',
        artifactId: String(artifactId)};
      renderAll();
      return;
    }
    store.data.practiceReview = {phase: 'loading', doc: null, message: '', artifactId: id.value};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.practiceReview = {phase: 'ready', doc: outcome.doc, message: '',
        artifactId: id.value};
    } else {
      store.data.practiceReview = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status, artifactId: id.value};
    }
    renderAll();
  }

  async function loadPracticeMethod(artifactId) {
    const id = protocolId(artifactId, 200);
    const path = id.ok ? practiceMethodPath(id.value) : '';
    if (!path) {
      store.data.practiceMethod = {phase: 'error', doc: null, message: '做法 id 不可用',
        artifactId: String(artifactId)};
      renderAll();
      return;
    }
    store.data.practiceMethod = {phase: 'loading', doc: null, message: '', artifactId: id.value};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.practiceMethod = {phase: 'ready', doc: outcome.doc, message: '',
        artifactId: id.value};
    } else {
      store.data.practiceMethod = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status, artifactId: id.value};
    }
    renderAll();
  }

  async function loadExperiences(query) {
    const path = memoryExperiencesPath({query: query || ''});
    if (!path) {
      store.data.experiences = {phase: 'error', doc: null, message: '检索词太长'};
      renderAll();
      return;
    }
    store.data.experiences = {phase: 'loading', doc: null, message: ''};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.experiences = {phase: 'ready', doc: outcome.doc, message: ''};
    } else {
      store.data.experiences = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status};
    }
    renderAll();
  }

  async function loadExperience(experienceId) {
    const path = memoryExperiencePath(experienceId);
    if (!path) {
      store.data.experience = {phase: 'error', doc: null, message: '经历 id 不可用',
        experienceId: String(experienceId)};
      renderAll();
      return;
    }
    store.data.experience = {phase: 'loading', doc: null, message: '',
      experienceId: String(experienceId)};
    renderAll();
    const res = await request('GET', path, {});
    const outcome = deriveReadOutcome(res);
    if (outcome.status === 'ok') {
      store.data.experience = {phase: 'ready', doc: outcome.doc, message: '',
        experienceId: String(experienceId)};
    } else {
      store.data.experience = {phase: outcome.status === 'offline' ? 'offline' : 'error',
        doc: null, message: outcome.message, http: res && res.status,
        experienceId: String(experienceId)};
    }
    renderAll();
  }

  function openPracticeReview(artifactId) {
    const id = protocolId(artifactId, 200);
    if (!id.ok) return;
    if (store.data.practiceReview && store.data.practiceReview.artifactId !== id.value) {
      store.data.practiceReview = null;
    }
    store.practiceDraft = blankPracticeDraft('review');
    store.result = null;
    go('practice-review', {detail: id.value});
  }

  function openPracticeMethod(artifactId) {
    const id = protocolId(artifactId, 200);
    if (!id.ok) return;
    if (store.data.practiceMethod && store.data.practiceMethod.artifactId !== id.value) {
      store.data.practiceMethod = null;
    }
    store.practiceDraft = blankPracticeDraft('method');
    store.result = null;
    go('practice-method', {detail: id.value});
  }

  function openExperience(experienceId) {
    const id = protocolId(experienceId, 200);
    if (!id.ok) return;
    store.result = null;
    go('practice-experience', {detail: id.value});
  }

  function blankPracticeDraft(mode) {
    return {mode: mode, artifactId: '', kind: mode === 'method' ? 'method' : 'review',
      title: '', question: '', instruction: '', scope: '', uncertainty: '', content: '',
      outcome: '', detail: '', head: '', revision: null,
      experience_ids: [], review_refs: [], errors: []};
  }

  function startPracticeReview(presetIds) {
    const draft = blankPracticeDraft('review-new');
    (presetIds || []).forEach(function (id) {
      const text = practiceIdText(id);
      if (text && draft.experience_ids.indexOf(text) < 0) draft.experience_ids.push(text);
    });
    store.practiceDraft = draft;
    store.result = null;
    go('practice-review-new', {detail: null});
  }

  function startPracticeMethod() {
    store.practiceDraft = blankPracticeDraft('method-new');
    store.result = null;
    go('practice-method-new', {detail: null});
  }

  function editPracticeReview(view) {
    store.practiceDraft = {
      mode: 'review-edit', artifactId: view.project.id, kind: 'review',
      title: view.project.title, question: view.question, instruction: '', scope: '',
      uncertainty: '', content: view.body, outcome: '', detail: '',
      head: view.project.head, revision: view.project.revision,
      experience_ids: view.experienceIds.slice(), review_refs: [], errors: []
    };
    store.result = null;
    go('practice-review-edit', {detail: view.project.id});
  }

  function startPracticeRevise(view) {
    store.practiceDraft = {
      mode: 'method-revise', artifactId: view.project.id, kind: 'method',
      title: view.project.title, question: '', instruction: view.instruction,
      scope: view.scope, uncertainty: view.uncertainty, content: view.body,
      outcome: '', detail: '', head: view.project.head, revision: view.project.revision,
      experience_ids: view.experienceIds.slice(), review_refs: view.reviewRefs.slice(),
      errors: []
    };
    store.result = null;
    go('practice-method-revise', {detail: view.project.id});
  }

  /* -- 写 -------------------------------------------------------------------------- */

  function confirmEnableMemory() {
    openSheet({
      title: '在这个实例里启用 Memory？',
      lines: ['会在这个实例自己的目录下新建 memory/evidence.db 与独立的 controls，并先备份配置。'],
      note: '不会重启任何服务：跑着的 resident 需要你自己重启才会用上新绑定。',
      confirmLabel: '启用',
      onConfirm: submitMemoryEnable
    });
  }

  async function submitMemoryEnable() {
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: PRACTICE_SCOPES.memoryEnable, status: 'blocked',
        message: guard.message};
      renderAll();
      return;
    }
    const scope = PRACTICE_SCOPES.memoryEnable;
    const entry = ops.ensure(scope, {path: '/memory/enable'});
    const payload = buildMemoryEnablePayload({operation_key: entry.key});
    if (!payload) {
      store.result = {scope: scope, status: 'blocked', message: '这次启用没有操作键，没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在启用 Memory…', key: entry.key};
    renderAll();
    const res = await request('POST', '/memory/enable', {csrf: store.conn.csrf, body: payload});
    finishMutation(scope, deriveMutationOutcome(res), '启用 Memory');
    store.data.memory = null;
    store.data.practice = null;
    loadPractice();
  }

  async function submitPracticeReviewCreate() {
    const draft = store.practiceDraft;
    if (!draft) return;
    const checked = validatePracticeReviewDraft(draft);
    if (!checked.ok) {
      draft.errors = checked.errors;
      renderAll();
      return;
    }
    draft.errors = [];
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: PRACTICE_SCOPES.reviewCreate, status: 'blocked',
        message: guard.message};
      renderAll();
      return;
    }
    const scope = PRACTICE_SCOPES.reviewCreate;
    const entry = ops.ensure(scope, {path: '/practice/reviews'});
    const payload = buildPracticeReviewCreatePayload({
      question: checked.value.question, title: checked.value.title,
      experience_ids: checked.value.experience_ids, operation_key: entry.key
    });
    if (!payload) {
      store.result = {scope: scope, status: 'blocked',
        message: '这次创建缺少问题或操作键，没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在建这份回顾…', key: entry.key};
    renderAll();
    const res = await request('POST', '/practice/reviews', {csrf: store.conn.csrf, body: payload});
    const created = res && res.doc && res.doc.data
      && typeof res.doc.data.artifact_id === 'string' ? res.doc.data.artifact_id : '';
    finishMutation(scope, deriveMutationOutcome(res), '创建回顾');
    if (created) {
      store.practiceDraft = blankPracticeDraft('review');
      store.data.practice = null;
      openPracticeReview(created);
    }
  }

  async function submitPracticeMethodCreate() {
    const draft = store.practiceDraft;
    if (!draft) return;
    const checked = validatePracticeMethodDraft(draft);
    if (!checked.ok) {
      draft.errors = checked.errors;
      renderAll();
      return;
    }
    draft.errors = [];
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: PRACTICE_SCOPES.methodCreate, status: 'blocked',
        message: guard.message};
      renderAll();
      return;
    }
    const scope = PRACTICE_SCOPES.methodCreate;
    const entry = ops.ensure(scope, {path: '/practice/methods'});
    const payload = buildPracticeMethodCreatePayload({
      title: checked.value.title, instruction: checked.value.instruction,
      scope: checked.value.scope, uncertainty: checked.value.uncertainty,
      experience_ids: checked.value.experience_ids,
      review_refs: checked.value.review_refs, operation_key: entry.key
    });
    if (!payload) {
      store.result = {scope: scope, status: 'blocked',
        message: '这次创建缺少做法或操作键，没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在存草案…', key: entry.key};
    renderAll();
    const res = await request('POST', '/practice/methods', {csrf: store.conn.csrf, body: payload});
    const created = res && res.doc && res.doc.data
      && typeof res.doc.data.artifact_id === 'string' ? res.doc.data.artifact_id : '';
    finishMutation(scope, deriveMutationOutcome(res), '创建做法草案');
    if (created) {
      store.practiceDraft = blankPracticeDraft('method');
      store.data.practice = null;
      openPracticeMethod(created);
    }
  }

  async function submitPracticeDocument() {
    const draft = store.practiceDraft;
    if (!draft) return;
    const checked = validatePracticeDocumentDraft({
      content: draft.content, expected_head: draft.head
    });
    if (!checked.ok) {
      draft.errors = checked.errors;
      renderAll();
      return;
    }
    draft.errors = [];
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: PRACTICE_SCOPES.document, status: 'blocked',
        message: guard.message};
      renderAll();
      return;
    }
    const scope = PRACTICE_SCOPES.document;
    const reviewPath = practiceReviewDocumentsPath(draft.artifactId);
    const methodPath = practiceMethodPath(draft.artifactId);
    const path = draft.kind === 'method' && methodPath
      ? methodPath + '/documents' : reviewPath;
    if (!path) {
      store.result = {scope: scope, status: 'blocked', message: '这份文档的 id 不可用，没有发出。'};
      renderAll();
      return;
    }
    const entry = ops.ensure(scope, {path: '/practice/documents'});
    const payload = buildPracticeDocumentPayload({
      content: checked.value.content, expected_head: checked.value.expected_head,
      operation_key: entry.key
    });
    if (!payload) {
      store.result = {scope: scope, status: 'blocked',
        message: '这次修改缺少正文或 head，没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在提交新版本…', key: entry.key};
    renderAll();
    const res = await request('POST', path, {csrf: store.conn.csrf, body: payload});
    finishMutation(scope, deriveMutationOutcome(res), '写文档');
    const status = store.result && store.result.status;
    if (status === 'applied' || status === 'already_applied') {
      store.practiceDraft = blankPracticeDraft(draft.kind === 'method' ? 'method' : 'review');
      store.data.practiceReview = null;
      store.data.practiceMethod = null;
      store.data.practice = null;
      if (draft.kind === 'method') go('practice-method', {detail: draft.artifactId});
      else go('practice-review', {detail: draft.artifactId});
    }
  }

  async function submitPracticeTrial(head) {
    const draft = store.practiceDraft;
    const entryData = store.data.practiceMethod;
    if (!draft || !entryData) return;
    const checked = validatePracticeTrialDraft({
      outcome: draft.outcome, detail: draft.detail,
      experience_ids: draft.experience_ids, expected_head: head
    });
    if (!checked.ok) {
      store.result = {scope: PRACTICE_SCOPES.trial, status: 'blocked',
        message: checked.errors.join('；')};
      renderAll();
      return;
    }
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: PRACTICE_SCOPES.trial, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = PRACTICE_SCOPES.trial;
    const path = practiceMethodTrialsPath(draft.artifactId || entryData.artifactId);
    const entry = ops.ensure(scope, {path: '/practice/trials'});
    const payload = buildPracticeTrialPayload({
      outcome: checked.value.outcome, detail: checked.value.detail,
      experience_ids: checked.value.experience_ids,
      expected_head: checked.value.expected_head, operation_key: entry.key
    });
    if (!path || !payload) {
      store.result = {scope: scope, status: 'blocked',
        message: '这次评价缺少结果、head 或操作键，没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在记录这次试用…', key: entry.key};
    renderAll();
    const res = await request('POST', path, {csrf: store.conn.csrf, body: payload});
    finishMutation(scope, deriveMutationOutcome(res), '记录试用');
    const status = store.result && store.result.status;
    if (status === 'applied' || status === 'already_applied') {
      const id = entryData.artifactId;
      store.practiceDraft = blankPracticeDraft('method');
      store.data.practiceMethod = null;
      store.data.practice = null;
      loadPracticeMethod(id);
    }
  }

  async function submitPracticeRevision() {
    const draft = store.practiceDraft;
    if (!draft) return;
    const checked = validatePracticeRevisionDraft({
      instruction: draft.instruction, scope: draft.scope,
      uncertainty: draft.uncertainty, expected_head: draft.head
    });
    if (!checked.ok) {
      draft.errors = checked.errors;
      renderAll();
      return;
    }
    draft.errors = [];
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: PRACTICE_SCOPES.revise, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = PRACTICE_SCOPES.revise;
    const path = practiceMethodRevisionsPath(draft.artifactId);
    const entry = ops.ensure(scope, {path: '/practice/revisions'});
    const payload = buildPracticeRevisionPayload({
      instruction: checked.value.instruction, scope: checked.value.scope,
      uncertainty: checked.value.uncertainty, expected_head: checked.value.expected_head,
      operation_key: entry.key
    });
    if (!path || !payload) {
      store.result = {scope: scope, status: 'blocked',
        message: '这次修订缺少做法或 head，没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在保存修订…', key: entry.key};
    renderAll();
    const res = await request('POST', path, {csrf: store.conn.csrf, body: payload});
    finishMutation(scope, deriveMutationOutcome(res), '修订做法');
    const status = store.result && store.result.status;
    if (status === 'applied' || status === 'already_applied') {
      store.practiceDraft = blankPracticeDraft('method');
      store.data.practiceMethod = null;
      store.data.practice = null;
      go('practice-method', {detail: draft.artifactId});
    }
  }

  function confirmRetireMethod(view) {
    openSheet({
      title: '退休这一条做法？',
      lines: [view.project.title],
      note: '退休只是状态：文档、每个版本与反例都保留，没有删除；之后不再接受试用或修订。',
      confirmLabel: '退休',
      onConfirm: function () { submitPracticeRetire(view); }
    });
  }

  async function submitPracticeRetire(view) {
    const guard = canWrite(store.conn);
    if (!guard.ok) {
      store.result = {scope: PRACTICE_SCOPES.retire, status: 'blocked', message: guard.message};
      renderAll();
      return;
    }
    const scope = PRACTICE_SCOPES.retire;
    const path = practiceMethodRetirePath(view.project.id);
    const entry = ops.ensure(scope, {path: '/practice/retire'});
    const payload = buildPracticeRetirePayload({
      expected_head: view.project.head, operation_key: entry.key
    });
    if (!path || !payload) {
      store.result = {scope: scope, status: 'blocked',
        message: '服务没有给出这一条的 head，这次退休没有发出。'};
      renderAll();
      return;
    }
    store.busy = scope;
    store.result = {scope: scope, status: 'pending', message: '正在退休…', key: entry.key};
    renderAll();
    const res = await request('POST', path, {csrf: store.conn.csrf, body: payload});
    finishMutation(scope, deriveMutationOutcome(res), '退休做法');
    const status = store.result && store.result.status;
    if (status === 'applied' || status === 'already_applied') {
      store.data.practiceMethod = null;
      store.data.practice = null;
      loadPracticeMethod(view.project.id);
    }
  }

  const SCREENS = {
    home: {render: renderHome},
    notes: {render: renderNotes},
    'note-edit': {render: renderNoteEditor},
    references: {render: renderReferences},
    reading: {render: renderReading},
    wallet: {render: renderWallet},
    shop: {render: renderShop},
    'shop-item': {render: renderShopItem},
    inventory: {render: renderInventory},
    body: {render: renderBody},
    body: {render: renderBody},
    world: {render: renderWorld},
    store: {render: renderStore},
    'store-item': {render: renderStoreItem},
    projects: {render: renderProjects},
    album: {render: renderAlbum},
    'album-image': {render: renderAlbumImage},
    'album-import': {render: renderAlbumImport},
    project: {render: renderProject},
    'project-edit': {render: renderProjectEditor},
    materials: {render: renderMaterials},
    material: {render: renderMaterial},
    'material-import': {render: renderMaterialImport},
    research: {render: renderResearch},
    'research-item': {render: renderResearchItem},
    'research-create': {render: renderResearchCreate},
    'research-edit': {render: renderResearchEdit},
    practice: {render: renderPractice},
    'practice-review': {render: renderPracticeReview},
    'practice-review-new': {render: renderPracticeReviewNew},
    'practice-review-edit': {render: renderPracticeReviewEdit},
    'practice-method': {render: renderPracticeMethod},
    'practice-method-new': {render: renderPracticeMethodNew},
    'practice-method-revise': {render: renderPracticeMethodRevise},
    'practice-experience': {render: renderPracticeExperience},
    shares: {render: renderShares},
    share: {render: renderShare},
    'share-new': {render: renderShareNew},
    contact: {render: renderContact},
    'contact-event': {render: renderContactEvent},
    'shared-practice': {render: renderSharedPractice},
    'shared-practice-work': {render: renderSharedPracticeWork},
    ops: {render: renderOps}
  };

  try {
    boot();
  } catch (error) {
    const message = '手机界面启动失败：' + ((error && error.message) || String(error));
    const body = document.getElementById('screen-body');
    if (body) body.textContent = message;
  }
})();
