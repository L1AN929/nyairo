/* ==========================================================================
   nyairo Localized Documentation Center Engine (/en/docs/ & /ja/docs/)
   Mirrors app.js v070-55 docs reader, search, Style A palette & Option 6 theme
   ========================================================================== */

const DOCS_LOCALE = window.NYAIRO_DOCS_LOCALE === 'ja' ? 'ja' : 'en';

const I18N = {
  en: {
    siteTitle: 'nyairo Docs',
    docTitleSuffix: 'nyairo Docs',
    homeUrl: '../',
    zhBase: '../../',
    otherDocsBase: '../../ja/docs/',
    canonicalBase: 'https://nyairo.com/en/docs/',
    homeLabel: 'Home',
    docsLabel: 'Docs',
    defaultCategory: 'Docs',
    openNavLabel: 'Open navigation and docs outline',
    closeNavLabel: 'Close navigation and docs outline',
    minRead: (mins, secs) => secs > 0 ? `~${mins} min read · ${secs} sections` : `~${mins} min read`,
    mobileTocSummary: n => `On this page (${n})`,
    prevLabel: 'Previous',
    nextLabel: 'Next',
    backHomeLabel: 'Back to Site',
    backHomeTitle: '← nyairo Home',
    restartLabel: 'Start Over',
    firstDocTitle: 'Overview & Core Capabilities →',
    communityPrompt: 'Hit a setup snag or spot a doc issue?',
    qqLabel: 'QQ Group · 1126943805',
    qqTitle: 'Click to copy official QQ group number',
    copy: 'Copy',
    copiedBtn: 'Copied ✓',
    copiedShort: 'Copied ✓',
    copyManual: 'Press Ctrl+C',
    copySectionAria: 'Copy direct link to this section',
    copySectionTitle: 'Click to copy link to this section',
    anchorCopied: '✓ Copied',
    toggleToLight: 'Switch to Light mode',
    toggleToDark: 'Switch to Dark mode',
    searchEmpty: q => `No commands or docs found matching “<strong>${escapeHtml(q)}</strong>”`,
    badgeHeading: 'Section',
    badgeDoc: 'Chapter',
    badgeGo: 'Go',
    cliCopiedToast: 'Copied Linux / WSL install command',
    cliCopyFailToast: 'Clipboard restricted — please copy from the guide manually',
    palettes: {
      'deep-sea-sakura': { light: '#f5efea', dark: '#0c0b0d', dayInk: '#122e8a', nightInk: '#f4c2d1', short: 'Sea', title: 'Deep Sea · Sakura', index: '01 / 06' },
      'forest-mint': { light: '#f4f1ea', dark: '#0a0e0d', dayInk: '#164638', nightInk: '#a9dec6', short: 'Mint', title: 'Forest · Mint', index: '02 / 06' },
      'burgundy-rosewood': { light: '#f7f1ee', dark: '#0d0a0c', dayInk: '#73142c', nightInk: '#e6b3bd', short: 'Rose', title: 'Burgundy · Rose', index: '03 / 06' },
      'iris-lilac': { light: '#f5f2f8', dark: '#0b0a10', dayInk: '#3e277a', nightInk: '#d5c3f8', short: 'Iris', title: 'Iris · Lilac', index: '04 / 06' },
      'glacier-porcelain': { light: '#f2f5f9', dark: '#0a0c10', dayInk: '#164b8a', nightInk: '#b6d8f8', short: 'Ice', title: 'Glacier · Ice', index: '05 / 06' },
      'berry-peach': { light: '#faf4f5', dark: '#0e0b0c', dayInk: '#8f2d4b', nightInk: '#f8cad6', short: 'Peach', title: 'Berry · Peach', index: '06 / 06' }
    },
    mobileHomeChips: [
      { href: '../', label: '← Home', primary: true },
      { href: '../#en-preview', label: 'Preview' },
      { href: '../#en-tryout', label: '30s Tryout' },
      { href: '../#en-capabilities', label: 'How It Works' },
      { href: '../#en-gallery', label: 'Worlds' },
      { href: '../#en-downloads', label: 'Download' },
      { href: '../#en-faq', label: 'FAQ' },
      { href: '../../', label: '中文', lang: 'zh-CN', syncDoc: true },
      { href: '../../ja/docs/', label: '日本語', lang: 'ja', syncDoc: true }
    ],
    quickCommands: [
      {
        group: 'Quick Actions',
        title: 'Download Windows Desktop Installer (v0.7.0 · 5.8 MB)',
        summary: 'Direct download for nyairo-0.7.0-setup.exe (no admin rights required)',
        badge: 'Download',
        run: () => {
          const a = document.createElement('a');
          a.href = '../../downloads/nyairo-0.7.0-setup.exe';
          a.download = 'nyairo-0.7.0-setup.exe';
          document.body.appendChild(a);
          a.click();
          a.remove();
        }
      },
      {
        group: 'Quick Actions',
        title: 'Copy Linux / WSL One-Line Installer Command',
        summary: 'curl -fsSL https://nyairo.com/install.sh | bash',
        badge: 'Copy',
        run: async () => {
          const ok = await writeClipboardText('curl -fsSL https://nyairo.com/install.sh | bash');
          showToast(ok ? I18N.en.cliCopiedToast : I18N.en.cliCopyFailToast);
        }
      },
      {
        group: 'Quick Actions',
        title: 'Toggle Light / Dark Theme',
        summary: 'Switch instantly between warm paper Light mode and Deep Charcoal Dark mode',
        badge: 'Theme',
        run: () => {
          const cur = document.documentElement.getAttribute('data-theme') || 'light';
          switchTheme(cur === 'dark' ? 'light' : 'dark');
        }
      },
      {
        group: 'Quick Actions',
        title: 'Back to English Homepage (/en/)',
        summary: 'Return to the overview, 30s interactive tryout, and town showcase',
        badge: 'Home',
        run: () => { location.href = '../'; }
      },
      {
        group: 'Quick Actions',
        title: 'Read This Page in 中文 (Chinese)',
        summary: 'Open the matching Chinese documentation chapter',
        badge: '中文',
        run: () => { location.href = `../../#${encodeURIComponent(getCurrentDocId())}`; }
      },
      {
        group: 'Quick Actions',
        title: 'Read This Page in 日本語 (Japanese)',
        summary: 'Open the matching Japanese documentation chapter',
        badge: 'JA',
        run: () => { location.href = `../../ja/docs/#${encodeURIComponent(getCurrentDocId())}`; }
      },
      {
        group: 'Popular Docs',
        title: 'Install on Windows (SmartScreen & API Key Guide)',
        summary: 'Graphical installer steps, SmartScreen bypass, and DeepSeek / Ollama setup',
        badge: 'Docs',
        href: '#windows'
      },
      {
        group: 'Popular Docs',
        title: 'Desktop Client User Guide',
        summary: 'Five main tabs, custom pronouns, SOUL.md guardrails, and diagnostic bundles',
        badge: 'Desktop',
        href: '#desktop-guide'
      },
      {
        group: 'Popular Docs',
        title: 'Map Editor, 7 Built-In Towns & Procedural Worlds',
        summary: 'Template-free procedural world generation, offline editing, and pack export',
        badge: 'Worlds',
        href: '#world-editor'
      },
      {
        group: 'Popular Docs',
        title: 'v0.7.0 Changelog & Release Notes',
        summary: 'Craftsmanship Notebook, model diff lab, autonomy rails, and test scope',
        badge: 'NEW',
        href: '#changelog-v0-7-0'
      },
      {
        group: 'Popular Docs',
        title: 'Connect WeChat',
        summary: 'Link your digital companion to WeChat via QR code for daily chats and trips',
        badge: 'Channel',
        href: '#weixin'
      },
      {
        group: 'Popular Docs',
        title: 'Roadmap: Seasonal Evolution & Multi-Desktop',
        summary: 'Explore seasonal world shifts, macOS desktop plans, and runtime decoupling',
        badge: 'Planned',
        href: '#roadmap'
      }
    ]
  },
  ja: {
    siteTitle: 'nyairo ドキュメント',
    docTitleSuffix: 'nyairo ドキュメント',
    homeUrl: '../',
    zhBase: '../../',
    otherDocsBase: '../../en/docs/',
    canonicalBase: 'https://nyairo.com/ja/docs/',
    homeLabel: 'ホーム',
    docsLabel: 'ドキュメント',
    defaultCategory: 'ドキュメント',
    openNavLabel: 'ナビゲーションとドキュメント目次を開く',
    closeNavLabel: 'ナビゲーションとドキュメント目次を閉じる',
    minRead: (mins, secs) => secs > 0 ? `約 ${mins} 分で読めます · ${secs} 項目` : `約 ${mins} 分で読めます`,
    mobileTocSummary: n => `このページの目次（${n} 項目）`,
    prevLabel: '前のページ',
    nextLabel: '次のページ',
    backHomeLabel: 'サイトトップへ',
    backHomeTitle: '← nyairo ホーム',
    restartLabel: '最初から読む',
    firstDocTitle: 'プロジェクト概要とコア機能 →',
    communityPrompt: '設定でつまずいた点やドキュメントの誤りがありましたら：',
    qqLabel: '公式 QQ グループ · 1126943805',
    qqTitle: 'クリックして公式 QQ グループ番号をコピー',
    copy: 'コピー',
    copiedBtn: 'コピー済 ✓',
    copiedShort: 'コピー済 ✓',
    copyManual: 'Ctrl+C でコピー',
    copySectionAria: 'このセクションへの直リンクをコピー',
    copySectionTitle: 'クリックしてセクションリンクをコピー',
    anchorCopied: '✓ コピー済',
    toggleToLight: 'ライトモードに切り替え',
    toggleToDark: 'ダークモードに切り替え',
    searchEmpty: q => `「<strong>${escapeHtml(q)}</strong>」に一致するコマンドやドキュメントは見つかりませんでした`,
    badgeHeading: 'セクション',
    badgeDoc: 'チャプター',
    badgeGo: '移動',
    cliCopiedToast: 'Linux / WSL 導入コマンドをコピーしました',
    cliCopyFailToast: 'クリップボードにコピーできませんでした。導入手順から手動でコピーしてください',
    palettes: {
      'deep-sea-sakura': { light: '#f5efea', dark: '#0c0b0d', dayInk: '#122e8a', nightInk: '#f4c2d1', short: '深海', title: '深海 · 淡桜', index: '01 / 06' },
      'forest-mint': { light: '#f4f1ea', dark: '#0a0e0d', dayInk: '#164638', nightInk: '#a9dec6', short: '森林', title: '森林 · ミント', index: '02 / 06' },
      'burgundy-rosewood': { light: '#f7f1ee', dark: '#0d0a0c', dayInk: '#73142c', nightInk: '#e6b3bd', short: '深紅', title: '深紅 · ローズ', index: '03 / 06' },
      'iris-lilac': { light: '#f5f2f8', dark: '#0b0a10', dayInk: '#3e277a', nightInk: '#d5c3f8', short: '菖蒲', title: '菖蒲 · 紫', index: '04 / 06' },
      'glacier-porcelain': { light: '#f2f5f9', dark: '#0a0c10', dayInk: '#164b8a', nightInk: '#b6d8f8', short: '蒼碧', title: '蒼碧 · 氷青', index: '05 / 06' },
      'berry-peach': { light: '#faf4f5', dark: '#0e0b0c', dayInk: '#8f2d4b', nightInk: '#f8cad6', short: '苺緋', title: '苺緋 · 桃霧', index: '06 / 06' }
    },
    mobileHomeChips: [
      { href: '../', label: '← ホーム', primary: true },
      { href: '../#ja-preview', label: '実機画面' },
      { href: '../#ja-tryout', label: '30秒体験' },
      { href: '../#ja-how', label: '仕組み' },
      { href: '../#ja-capabilities', label: '機能一覧' },
      { href: '../#ja-gallery', label: '街と人物' },
      { href: '../#ja-downloads', label: 'ダウンロード' },
      { href: '../../', label: '中文', lang: 'zh-CN', syncDoc: true },
      { href: '../../en/docs/', label: 'English', lang: 'en', syncDoc: true }
    ],
    quickCommands: [
      {
        group: 'クイック操作',
        title: 'Windows デスクトップ版インストーラを取得 (v0.7.0 · 5.8 MB)',
        summary: 'nyairo-0.7.0-setup.exe を直接ダウンロード（管理者権限不要）',
        badge: '取得',
        run: () => {
          const a = document.createElement('a');
          a.href = '../../downloads/nyairo-0.7.0-setup.exe';
          a.download = 'nyairo-0.7.0-setup.exe';
          document.body.appendChild(a);
          a.click();
          a.remove();
        }
      },
      {
        group: 'クイック操作',
        title: 'Linux / WSL ワンライン導入コマンドをコピー',
        summary: 'curl -fsSL https://nyairo.com/install.sh | bash',
        badge: 'コピー',
        run: async () => {
          const ok = await writeClipboardText('curl -fsSL https://nyairo.com/install.sh | bash');
          showToast(ok ? I18N.ja.cliCopiedToast : I18N.ja.cliCopyFailToast);
        }
      },
      {
        group: 'クイック操作',
        title: 'ライト / ダーク表示を切り替え',
        summary: '全体の配色テーマを即座に切り替えます',
        badge: 'テーマ',
        run: () => {
          const cur = document.documentElement.getAttribute('data-theme') || 'light';
          switchTheme(cur === 'dark' ? 'light' : 'dark');
        }
      },
      {
        group: 'クイック操作',
        title: '日本語トップページへ戻る (/ja/)',
        summary: '実機プレビュー・30秒体験・ダウンロード案内ページを開きます',
        badge: 'ホーム',
        run: () => { location.href = '../'; }
      },
      {
        group: 'クイック操作',
        title: 'このページを中国語版 (中文) で読む',
        summary: '対応する中国語版ドキュメントの同じ章を開きます',
        badge: '中文',
        run: () => { location.href = `../../#${encodeURIComponent(getCurrentDocId())}`; }
      },
      {
        group: 'クイック操作',
        title: 'このページを英語版 (English) で読む',
        summary: '対応する英語版ドキュメントの同じ章を開きます',
        badge: 'EN',
        run: () => { location.href = `../../en/docs/#${encodeURIComponent(getCurrentDocId())}`; }
      },
      {
        group: 'よく読まれるガイド',
        title: 'Windows へのインストール（SmartScreen と API キー設定）',
        summary: 'インストーラ手順、SmartScreen 許可、DeepSeek / Ollama 設定ガイド',
        badge: '入門',
        href: '#windows'
      },
      {
        group: 'よく読まれるガイド',
        title: 'デスクトップクライアント利用ガイド',
        summary: '5 つの主要画面、自由代名詞と SOUL.md 保護、診断パッケージ出力',
        badge: 'デスクトップ',
        href: '#desktop-guide'
      },
      {
        group: 'よく読まれるガイド',
        title: 'マップエディタ・7 つの街・完全ランダム生成ワールド',
        summary: 'テンプレートなしの専用タウン自動生成、間取り編集とエクスポート',
        badge: 'ワールド',
        href: '#world-editor'
      },
      {
        group: 'よく読まれるガイド',
        title: 'v0.7.0 リリースノートと更新内容',
        summary: '個人手帳、モデル対照ラボ、自律判断ロック、テスト公開範囲の案内',
        badge: 'NEW',
        href: '#changelog-v0-7-0'
      },
      {
        group: 'よく読まれるガイド',
        title: 'WeChat 連携ガイド',
        summary: 'QR コードでスマホの WeChat と接続し、日常の会話や外出通知を受信',
        badge: '推奨',
        href: '#weixin'
      },
      {
        group: 'よく読まれるガイド',
        title: '今後のロードマップ（季節の移ろいと macOS 対応）',
        summary: '四季イベント、初対面の出会い演出、macOS デスクトップ版の計画',
        badge: '計画中',
        href: '#roadmap'
      }
    ]
  }
};

const T = I18N[DOCS_LOCALE];
const PALETTE_ORDER = [
  'deep-sea-sakura',
  'forest-mint',
  'burgundy-rosewood',
  'iris-lilac',
  'glacier-porcelain',
  'berry-peach'
];
const PALETTE_META = T.palettes;

let paletteVtSeq = 0;
let activePaletteAnim = null;
let themeVtSeq = 0;
let activeThemeVt = null;
let paletteFeedbackTimers = { shuffle: null, star: null, sheen: null };
let syncHeaderLampFn = null;
const layoutSyncFns = [];
let ANCHOR_TO_DOC_MAP = null;
let activeTocCleanup = null;
let lastLoadedDocId = null;
let SEARCH_INDEX = null;

document.addEventListener('DOMContentLoaded', bootstrap);

function bootstrap() {
  initTheme();
  initPaletteDropdown();
  initLangDropdown();
  initHeaderPhysics();
  renderSidebar();
  initRouting();
  initCommandPalette();
  initMobileMenu();
  initBackToTop();
}

function getActivePalette() {
  const cur = document.documentElement.getAttribute('data-palette');
  return cur && PALETTE_META[cur] ? cur : 'deep-sea-sakura';
}

function syncMetaThemeColor() {
  const metaTheme = document.getElementById('meta-theme-color');
  if (!metaTheme) return;
  const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  const pal = getActivePalette();
  metaTheme.setAttribute('content', PALETTE_META[pal][theme]);
}

function ensurePaletteWashBeam() {
  let beam = document.getElementById('palette-wash-beam');
  if (!beam && document.body) {
    beam = document.createElement('div');
    beam.id = 'palette-wash-beam';
    beam.className = 'palette-wash-beam';
    beam.setAttribute('aria-hidden', 'true');
    document.body.appendChild(beam);
  }
  return beam;
}

function renderPaletteDeckMeta(paletteId) {
  const meta = PALETTE_META[paletteId] || PALETTE_META['deep-sea-sakura'];
  const titleEl = document.getElementById('palette-stage-title');
  const hexEl = document.getElementById('palette-stage-hex');
  const counterEl = document.getElementById('palette-counter');
  if (titleEl) titleEl.textContent = meta.title;
  if (hexEl) hexEl.textContent = `${meta.dayInk} · ${meta.nightInk}`;
  if (counterEl) counterEl.textContent = meta.index;
}

function triggerPaletteFeedback(sourceCard) {
  const btn = document.getElementById('palette-btn');
  const star = document.getElementById('palette-stage-star');
  if (btn) {
    btn.classList.remove('is-shuffling');
    void btn.offsetWidth;
    btn.classList.add('is-shuffling');
    clearTimeout(paletteFeedbackTimers.shuffle);
    paletteFeedbackTimers.shuffle = setTimeout(() => btn.classList.remove('is-shuffling'), 480);
  }
  if (star) {
    star.classList.remove('is-popping');
    void star.offsetWidth;
    star.classList.add('is-popping');
    clearTimeout(paletteFeedbackTimers.star);
    paletteFeedbackTimers.star = setTimeout(() => star.classList.remove('is-popping'), 380);
  }
  const cardEl = sourceCard || document.querySelector('#palette-menu .palette-card.is-active');
  if (cardEl) {
    cardEl.classList.remove('is-sheen');
    void cardEl.offsetWidth;
    cardEl.classList.add('is-sheen');
    clearTimeout(paletteFeedbackTimers.sheen);
    paletteFeedbackTimers.sheen = setTimeout(() => cardEl.classList.remove('is-sheen'), 540);
  }
}

function applyPalette(paletteId) {
  const next = paletteId && PALETTE_META[paletteId] ? paletteId : 'deep-sea-sakura';
  if (next === 'deep-sea-sakura') {
    document.documentElement.removeAttribute('data-palette');
  } else {
    document.documentElement.setAttribute('data-palette', next);
  }
  try {
    localStorage.setItem('nyairo-palette', next);
  } catch {}
  syncMetaThemeColor();

  const capLabel = document.getElementById('palette-cap-label');
  if (capLabel) capLabel.textContent = PALETTE_META[next].short;

  const menu = document.getElementById('palette-menu');
  if (menu) {
    menu.querySelectorAll('.palette-card').forEach(card => {
      const active = card.getAttribute('data-palette-id') === next;
      card.classList.toggle('is-active', active);
      card.setAttribute('aria-checked', active ? 'true' : 'false');
    });
  }
  renderPaletteDeckMeta(next);
}

function switchPalette(paletteId, sourceCard = null) {
  const next = paletteId && PALETTE_META[paletteId] ? paletteId : 'deep-sea-sakura';
  if (next === getActivePalette()) return;

  const seq = ++paletteVtSeq;
  if (activePaletteAnim) {
    try { activePaletteAnim.cancel(); } catch {}
    activePaletteAnim = null;
  }
  if (activeThemeVt) {
    ++themeVtSeq;
    try { activeThemeVt.skipTransition(); } catch {}
    activeThemeVt = null;
    document.documentElement.classList.remove('vt-switching');
  }

  triggerPaletteFeedback(sourceCard);
  applyPalette(next);

  if (isReducedMotion()) return;

  const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  const beamPrimary = theme === 'light' ? PALETTE_META[next].dayInk : PALETTE_META[next].nightInk;
  const beamSecondary = theme === 'light' ? PALETTE_META[next].nightInk : PALETTE_META[next].dayInk;
  const beam = ensurePaletteWashBeam();
  if (beam && typeof beam.animate === 'function') {
    beam.style.setProperty('--beam-primary', beamPrimary);
    beam.style.setProperty('--beam-secondary', beamSecondary);
    beam.style.opacity = '1';
    const anim = beam.animate(
      [
        { transform: 'translate3d(64vw, 0, 0)', opacity: 0 },
        { transform: 'translate3d(18vw, 0, 0)', opacity: 0.94, offset: 0.3 },
        { transform: 'translate3d(-68vw, 0, 0)', opacity: 0 }
      ],
      { duration: 580, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' }
    );
    activePaletteAnim = anim;
    Promise.resolve(anim.finished).catch(() => {}).finally(() => {
      if (seq === paletteVtSeq) {
        beam.style.opacity = '0';
        activePaletteAnim = null;
      }
    });
  }
}

function initPaletteDropdown() {
  const wrap = document.getElementById('palette-dropdown');
  const capsule = document.getElementById('palette-capsule');
  const btn = document.getElementById('palette-btn');
  const menu = document.getElementById('palette-menu');
  let savedPal = 'deep-sea-sakura';
  try {
    savedPal = localStorage.getItem('nyairo-palette') || 'deep-sea-sakura';
  } catch {}
  applyPalette(savedPal);

  if (!wrap || !btn || !menu) return;

  const isOpen = () => menu.classList.contains('is-open');

  const clampPaletteMenu = () => {
    const wrapRect = wrap.getBoundingClientRect();
    const menuWidth = menu.offsetWidth || Math.min(372, window.innerWidth - 24);
    const idealLeft = wrapRect.right - menuWidth;
    const minLeft = 12;
    const shiftX = idealLeft < minLeft ? Math.round(minLeft - idealLeft) : 0;
    menu.style.setProperty('--menu-shift-x', `${shiftX}px`);
  };

  const openMenu = () => {
    if (isOpen()) return;
    clampPaletteMenu();
    menu.classList.add('is-open');
    if (capsule) capsule.classList.add('is-open');
    menu.setAttribute('aria-hidden', 'false');
    btn.setAttribute('aria-expanded', 'true');
  };

  const closeMenu = () => {
    if (!isOpen()) return;
    menu.classList.remove('is-open');
    if (capsule) capsule.classList.remove('is-open');
    menu.setAttribute('aria-hidden', 'true');
    btn.setAttribute('aria-expanded', 'false');
    renderPaletteDeckMeta(getActivePalette());
  };

  btn.addEventListener('click', e => {
    e.stopPropagation();
    const langMenu = document.getElementById('lang-menu');
    const langBtn = document.getElementById('lang-btn');
    if (langMenu && !langMenu.hidden) {
      langMenu.hidden = true;
      if (langBtn) langBtn.setAttribute('aria-expanded', 'false');
    }
    if (isOpen()) {
      closeMenu();
    } else {
      openMenu();
    }
  });

  menu.addEventListener('click', e => {
    const card = e.target.closest('[data-palette-id]');
    if (card) {
      e.stopPropagation();
      switchPalette(card.getAttribute('data-palette-id'), card);
      return;
    }
    const modeBtn = e.target.closest('[data-theme-pick]');
    if (modeBtn) {
      e.stopPropagation();
      const pick = modeBtn.getAttribute('data-theme-pick');
      const curTheme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      if (pick !== curTheme) {
        switchTheme(pick);
      }
      return;
    }
    const shuffleBtn = e.target.closest('#palette-shuffle');
    if (shuffleBtn) {
      e.stopPropagation();
      shuffleBtn.classList.remove('is-spinning');
      void shuffleBtn.offsetWidth;
      shuffleBtn.classList.add('is-spinning');
      setTimeout(() => shuffleBtn.classList.remove('is-spinning'), 440);
      const cur = getActivePalette();
      const candidates = PALETTE_ORDER.filter(id => id !== cur);
      const next = candidates[Math.floor(Math.random() * candidates.length)];
      const nextCard = menu.querySelector(`[data-palette-id="${next}"]`);
      switchPalette(next, nextCard);
    }
  });

  const grid = menu.querySelector('.palette-tray-grid');
  if (grid) {
    grid.querySelectorAll('.palette-card').forEach(card => {
      const palId = card.getAttribute('data-palette-id');
      card.addEventListener('mouseenter', () => renderPaletteDeckMeta(palId));
      card.addEventListener('focus', () => renderPaletteDeckMeta(palId));
      card.addEventListener('blur', () => renderPaletteDeckMeta(getActivePalette()));
    });
    grid.addEventListener('mouseleave', () => renderPaletteDeckMeta(getActivePalette()));
  }

  window.addEventListener('resize', () => {
    if (isOpen()) clampPaletteMenu();
  }, { passive: true });

  document.addEventListener('click', e => {
    if (!wrap.contains(e.target)) closeMenu();
  });

  window.addEventListener('keydown', e => {
    if (e.key === 'Escape' && isOpen()) {
      closeMenu();
      btn.focus();
    }
  });
}

function initLangDropdown() {
  const wrap = document.getElementById('lang-dropdown');
  const btn = document.getElementById('lang-btn');
  const menu = document.getElementById('lang-menu');
  if (!wrap || !btn || !menu) return;

  const closeMenu = () => {
    if (menu.hidden) return;
    menu.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
  };

  btn.addEventListener('click', e => {
    e.stopPropagation();
    const palMenu = document.getElementById('palette-menu');
    const palCap = document.getElementById('palette-capsule');
    const palBtn = document.getElementById('palette-btn');
    if (palMenu && palMenu.classList.contains('is-open')) {
      palMenu.classList.remove('is-open');
      if (palCap) palCap.classList.remove('is-open');
      palMenu.setAttribute('aria-hidden', 'true');
      if (palBtn) palBtn.setAttribute('aria-expanded', 'false');
    }
    const nextOpen = menu.hidden;
    menu.hidden = !nextOpen;
    btn.setAttribute('aria-expanded', String(nextOpen));
  });

  document.addEventListener('click', e => {
    if (!wrap.contains(e.target)) closeMenu();
  });

  window.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !menu.hidden) {
      closeMenu();
      btn.focus();
    }
  });
}

function initTheme() {
  const themeBtn = document.getElementById('theme-toggle');
  let saved = 'light';
  try {
    saved = localStorage.getItem('nyairo-theme') || localStorage.getItem('chiyo-theme') || 'light';
  } catch {}
  applyTheme(saved, false);
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      document.documentElement.classList.add('theme-ready');
    });
  });

  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme') || 'light';
      switchTheme(cur === 'dark' ? 'light' : 'dark');
    });
  }
}

function switchTheme(nextTheme) {
  const seq = ++themeVtSeq;
  if (activePaletteAnim) {
    ++paletteVtSeq;
    try { activePaletteAnim.cancel(); } catch {}
    activePaletteAnim = null;
    const beam = document.getElementById('palette-wash-beam');
    if (beam) beam.style.opacity = '0';
  }
  if (activeThemeVt) {
    try { activeThemeVt.skipTransition(); } catch {}
    activeThemeVt = null;
  }

  const reduced = isReducedMotion();
  if (!document.startViewTransition || reduced) {
    applyTheme(nextTheme);
    return;
  }

  document.documentElement.classList.add('vt-switching');
  const transition = document.startViewTransition(() => {
    applyTheme(nextTheme);
  });
  activeThemeVt = transition;

  transition.ready.then(() => {
    if (seq !== themeVtSeq) return;
    document.documentElement.animate(
      { opacity: [0, 1] },
      { duration: 190, easing: 'ease-out', fill: 'both', pseudoElement: '::view-transition-new(root)' }
    );
  }).catch(() => {});

  Promise.resolve(transition.finished).finally(() => {
    if (seq !== themeVtSeq) return;
    activeThemeVt = null;
    document.documentElement.classList.remove('vt-switching');
  });
}

function applyTheme(theme, persist = true) {
  const next = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  syncMetaThemeColor();
  if (persist) {
    try {
      localStorage.setItem('nyairo-theme', next);
      localStorage.setItem('chiyo-theme', next);
    } catch {}
  }

  const btnEl = document.getElementById('theme-toggle');
  if (btnEl) {
    btnEl.setAttribute('aria-pressed', next === 'light' ? 'true' : 'false');
    btnEl.setAttribute('title', next === 'dark' ? T.toggleToLight : T.toggleToDark);
  }

  document.querySelectorAll('#palette-menu [data-theme-pick]').forEach(modeBtn => {
    const active = modeBtn.getAttribute('data-theme-pick') === next;
    modeBtn.classList.toggle('is-active', active);
    modeBtn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function initHeaderPhysics() {
  const nav = document.getElementById('header-nav');
  const hoverPill = document.getElementById('nav-hover-pill');
  const activeLamp = document.getElementById('nav-active-lamp');
  const progressBar = document.getElementById('scroll-progress-bar');
  const headerEl = document.querySelector('.site-header');
  const backTopBtn = document.getElementById('back-to-top');

  if (nav) {
    const links = Array.from(nav.querySelectorAll('.nav-link'));
    const syncActiveLamp = () => {
      if (!activeLamp) return;
      const active = nav.querySelector('.nav-link.active');
      if (!active || active.offsetWidth === 0) {
        activeLamp.style.opacity = '0';
        return;
      }
      const pad = 11;
      const x = active.offsetLeft + pad;
      const w = Math.max(12, active.offsetWidth - pad * 2);
      activeLamp.style.opacity = '1';
      activeLamp.style.width = `${w}px`;
      activeLamp.style.transform = `translate3d(${x}px, 0, 0)`;
    };

    syncHeaderLampFn = syncActiveLamp;
    layoutSyncFns.push(syncActiveLamp);
    requestAnimationFrame(syncActiveLamp);

    links.forEach(link => {
      link.addEventListener('mouseenter', () => {
        if (!hoverPill || link.offsetWidth === 0) return;
        hoverPill.style.width = `${link.offsetWidth}px`;
        hoverPill.style.transform = `translate3d(${link.offsetLeft}px, 0, 0)`;
        hoverPill.style.opacity = '1';
      });
    });

    nav.addEventListener('mouseleave', () => {
      if (hoverPill) hoverPill.style.opacity = '0';
    });
  }

  let ticking = false;
  const onScrollFrame = () => {
    ticking = false;
    const docEl = document.documentElement;
    const maxScroll = docEl.scrollHeight - window.innerHeight;
    if (headerEl) {
      headerEl.classList.toggle('is-scrolled', window.scrollY > 12);
    }
    if (progressBar) {
      if (maxScroll > 120 && window.scrollY > 24) {
        const ratio = Math.min(1, Math.max(0, window.scrollY / maxScroll));
        progressBar.classList.add('is-visible');
        progressBar.style.transform = `scaleX(${ratio.toFixed(4)})`;
      } else {
        progressBar.classList.remove('is-visible');
      }
    }
    if (backTopBtn) {
      backTopBtn.classList.toggle('is-visible', window.scrollY > 680);
    }
  };

  window.addEventListener('scroll', () => {
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(onScrollFrame);
    }
  }, { passive: true });
}

function renderSidebar() {
  const sidebarEl = document.getElementById('docs-sidebar');
  if (!sidebarEl || typeof DOCS_TREE === 'undefined') return;

  const chipsHtml = T.mobileHomeChips.map(c => {
    const cls = `sidebar-mobile-chip${c.primary ? ' primary' : ''}`;
    const langAttr = c.lang ? ` lang="${c.lang}"` : '';
    const syncAttr = c.syncDoc ? ' data-sync-doc="1"' : '';
    return `<a href="${escapeHtml(c.href)}" class="${cls}"${langAttr}${syncAttr}>${escapeHtml(c.label)}</a>`;
  }).join('');

  let html = `
    <div class="sidebar-mobile-home">
      <div class="sidebar-mobile-grid">
        ${chipsHtml}
      </div>
    </div>
  `;
  DOCS_TREE.forEach(group => {
    html += `
      <div class="sidebar-group">
        <div class="sidebar-title">${escapeHtml(group.category)}</div>
        <ul class="sidebar-list">
    `;
    group.items.forEach(item => {
      html += `
        <li class="sidebar-item">
          <a href="#${escapeHtml(item.id)}" class="sidebar-link" data-id="${escapeHtml(item.id)}">
            <span>${escapeHtml(item.title)}</span>
            ${item.badge ? `<span class="sidebar-badge">${escapeHtml(item.badge)}</span>` : ''}
          </a>
        </li>
      `;
    });
    html += `</ul></div>`;
  });

  sidebarEl.innerHTML = html;
  sidebarEl.querySelectorAll('.sidebar-link, .sidebar-mobile-chip').forEach(link => {
    link.addEventListener('click', () => {
      sidebarEl.classList.remove('open');
      setMobileMenuState(false);
    });
  });
}

function getAnchorToDocMap() {
  if (ANCHOR_TO_DOC_MAP) return ANCHOR_TO_DOC_MAP;
  ANCHOR_TO_DOC_MAP = {};
  if (typeof DOCS_CONTENT === 'undefined') return ANCHOR_TO_DOC_MAP;

  for (const [docKey, docData] of Object.entries(DOCS_CONTENT)) {
    ANCHOR_TO_DOC_MAP[docKey] = docKey;
    if (docData.content) {
      const regex = /<h[1-6][^>]*\bid=(?:\\*["'])([^"'\\]+)(?:\\*["'])/gi;
      let match;
      while ((match = regex.exec(docData.content)) !== null) {
        ANCHOR_TO_DOC_MAP[match[1]] = docKey;
      }
    }
    if (Array.isArray(docData.toc)) {
      docData.toc.forEach(item => {
        if (item.id) ANCHOR_TO_DOC_MAP[item.id] = docKey;
      });
    }
  }
  return ANCHOR_TO_DOC_MAP;
}

function resolveDocAndAnchor(hash) {
  if (!hash) return { docId: 'intro', anchor: null };
  const map = getAnchorToDocMap();
  if (map[hash]) {
    return { docId: map[hash], anchor: hash === map[hash] ? null : hash };
  }
  const prefixes = [
    ['cli-', 'cli-reference'],
    ['modules-', 'modules'],
    ['tb-', 'troubleshooting'],
    ['rm-', 'roadmap'],
    ['changelog-', 'changelog'],
    ['intro-', 'intro'],
    ['quickstart-', 'quickstart'],
    ['installation-', 'installation'],
    ['windows-', 'windows'],
    ['linux-', 'linux'],
    ['agent-', 'agent-install'],
    ['configuration-', 'configuration'],
    ['telegram-', 'telegram'],
    ['qqbot-', 'telegram'],
    ['wx-', 'weixin'],
    ['dg-', 'desktop-guide'],
    ['dc-', 'daily-companions'],
    ['we-', 'world-editor'],
    ['update-', 'update'],
    ['data-', 'data'],
    ['testing-', 'testing'],
    ['privacy-', 'privacy'],
    ['release-', 'release'],
    ['contributing-', 'contributing'],
    ['status-matrix-', 'status-matrix'],
    ['thanks-', 'acknowledgments']
  ];
  for (const [pfx, id] of prefixes) {
    if (hash.startsWith(pfx)) return { docId: id, anchor: hash };
  }
  return { docId: 'intro', anchor: null };
}

function getCurrentDocId() {
  const rawHash = location.hash.replace('#', '').trim();
  const urlParams = new URLSearchParams(location.search);
  const queryDoc = (urlParams.get('doc') || urlParams.get('p') || '').trim();
  return resolveDocAndAnchor(rawHash || queryDoc).docId;
}

function updateLangLinks(docId) {
  const encoded = encodeURIComponent(docId || 'intro');
  const zhLink = document.querySelector('#lang-menu a[lang="zh-CN"]');
  if (zhLink) zhLink.setAttribute('href', `${T.zhBase}#${encoded}`);

  const otherLang = DOCS_LOCALE === 'en' ? 'ja' : 'en';
  const otherLink = document.querySelector(`#lang-menu a[lang="${otherLang}"]`);
  if (otherLink) otherLink.setAttribute('href', `${T.otherDocsBase}#${encoded}`);

  document.querySelectorAll('#docs-sidebar .sidebar-mobile-chip[data-sync-doc]').forEach(chip => {
    const l = chip.getAttribute('lang');
    if (l === 'zh-CN') chip.setAttribute('href', `${T.zhBase}#${encoded}`);
    else if (l === otherLang) chip.setAttribute('href', `${T.otherDocsBase}#${encoded}`);
  });
}

function initRouting() {
  window.addEventListener('hashchange', () => routeHash());
  window.addEventListener('popstate', () => routeHash());
  routeHash();
}

function routeHash(autoCloseSidebar = true) {
  const urlParams = new URLSearchParams(location.search);
  const queryDoc = (urlParams.get('doc') || urlParams.get('p') || '').trim();
  const rawHash = location.hash.replace('#', '').trim();
  const target = rawHash || queryDoc;

  if (rawHash && queryDoc) {
    history.replaceState(null, '', `${location.pathname}${location.hash}`);
  }

  const { docId, anchor } = resolveDocAndAnchor(target);
  const navDocs = document.getElementById('nav-docs');
  const navRoadmap = document.getElementById('nav-roadmap');
  document.querySelectorAll('#header-nav .nav-link').forEach(n => n.classList.remove('active'));
  if (docId === 'roadmap' && navRoadmap) {
    navRoadmap.classList.add('active');
  } else if (navDocs) {
    navDocs.classList.add('active');
  }
  if (syncHeaderLampFn) requestAnimationFrame(syncHeaderLampFn);

  loadDoc(docId, anchor, autoCloseSidebar);
}

function updateCanonicalUrl(docId) {
  const link = document.getElementById('canonical-link') || document.querySelector('link[rel="canonical"]');
  if (!link) return;
  link.setAttribute('href', docId && docId !== 'intro' ? `${T.canonicalBase}?doc=${encodeURIComponent(docId)}` : T.canonicalBase);
}

function loadDoc(docId, anchor = null, autoCloseSidebar = true) {
  if (typeof DOCS_CONTENT === 'undefined') return;
  const targetId = DOCS_CONTENT[docId] ? docId : 'intro';
  const doc = DOCS_CONTENT[targetId];
  const docChanged = lastLoadedDocId !== null && lastLoadedDocId !== targetId;
  lastLoadedDocId = targetId;

  document.title = `${doc.title} · ${T.docTitleSuffix}`;
  updateCanonicalUrl(targetId);
  updateLangLinks(targetId);

  const sidebarContainer = document.getElementById('docs-sidebar');
  let activeSidebarLink = null;
  document.querySelectorAll('.sidebar-link').forEach(link => {
    const isAct = link.getAttribute('data-id') === targetId;
    link.classList.toggle('active', isAct);
    if (isAct) activeSidebarLink = link;
  });
  if (activeSidebarLink && sidebarContainer) {
    requestAnimationFrame(() => {
      const linkTop = activeSidebarLink.offsetTop;
      const linkBottom = linkTop + activeSidebarLink.offsetHeight;
      const viewTop = sidebarContainer.scrollTop;
      const viewBottom = viewTop + sidebarContainer.clientHeight;
      if (linkTop < viewTop + 24 || linkBottom > viewBottom - 24) {
        sidebarContainer.scrollTop = Math.max(
          0,
          linkTop - Math.floor(sidebarContainer.clientHeight / 2) + Math.floor(activeSidebarLink.offsetHeight / 2)
        );
      }
    });
  }

  let categoryName = T.defaultCategory;
  if (typeof DOCS_TREE !== 'undefined') {
    for (const group of DOCS_TREE) {
      if (group.items.some(i => i.id === targetId)) {
        categoryName = group.category;
        break;
      }
    }
  }

  const tocItems = doc.toc || [];
  const plainLen = stripHtmlToPlain(doc.content || '').length;
  const charsPerMin = DOCS_LOCALE === 'ja' ? 420 : 1050;
  const readMins = Math.max(1, Math.round(plainLen / charsPerMin));
  const metaText = T.minRead(readMins, tocItems.length);

  const mobileTocHtml = tocItems.length > 1
    ? `<details class="mobile-toc-bar">
         <summary><span>${escapeHtml(T.mobileTocSummary(tocItems.length))}</span><span>▾</span></summary>
         <ul class="mobile-toc-list">
           ${tocItems.map(item => `<li><a href="#${escapeHtml(item.id)}" data-mobile-toc="${escapeHtml(item.id)}">${escapeHtml(item.text)}</a></li>`).join('')}
         </ul>
       </details>`
    : '';

  const container = document.getElementById('doc-container');
  if (container) {
    container.innerHTML = `
      <header class="doc-page-header">
        <div class="doc-header-top">
          <div class="breadcrumbs">
            <a href="${T.homeUrl}">${escapeHtml(T.homeLabel)}</a>
            <span>/</span>
            <a href="#intro">${escapeHtml(T.docsLabel)}</a>
            <span>/</span>
            <span class="crumb-cat">${escapeHtml(categoryName)}</span>
            <span>/</span>
            <span class="crumb-cur">${escapeHtml(doc.title)}</span>
          </div>
          <span class="doc-meta-pill">${escapeHtml(metaText)}</span>
        </div>

        <h1 class="page-title">${escapeHtml(doc.title)}</h1>
        ${doc.summary ? `<p class="page-summary">${escapeHtml(doc.summary)}</p>` : ''}
      </header>

      ${mobileTocHtml}

      <div class="markdown-body">
        ${doc.content}
      </div>

      <div class="page-pager" id="page-pager">
        ${renderPager(targetId)}
      </div>

      <div class="doc-community-bar">
        <span>${escapeHtml(T.communityPrompt)}</span>
        <div class="doc-community-links">
          <button type="button" class="doc-copy-qq" data-copy-qq="1126943805" title="${escapeHtml(T.qqTitle)}">
            <span>${escapeHtml(T.qqLabel)}</span>
            <span class="doc-qq-status">${escapeHtml(T.copy)}</span>
          </button>
          <a href="https://t.me/nyairoai" target="_blank" rel="noopener noreferrer">Telegram @nyairoai ↗</a>
          <a href="https://github.com/L1AN929/nyairo/issues" target="_blank" rel="noopener noreferrer">GitHub Issues ↗</a>
        </div>
      </div>
    `;
    if (docChanged && !isReducedMotion()) {
      container.classList.remove('view-enter');
      void container.offsetWidth;
      container.classList.add('view-enter');
    }
  }

  renderTOC(tocItems, anchor);
  bindDocCopyButtons();
  bindDocHeadingAnchors();

  const sidebarEl = document.getElementById('docs-sidebar');
  if (autoCloseSidebar) {
    if (sidebarEl) sidebarEl.classList.remove('open');
    setMobileMenuState(false);
  }

  if (anchor) {
    requestAnimationFrame(() => {
      const targetEl = document.getElementById(anchor);
      if (targetEl) {
        targetEl.scrollIntoView({ block: 'start', behavior: preferredScrollBehavior() });
      } else {
        window.scrollTo({ top: 0, behavior: 'instant' });
      }
    });
  } else {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
}

function renderPager(currentId) {
  const flat = [];
  DOCS_TREE.forEach(g => g.items.forEach(i => flat.push(i)));
  const idx = flat.findIndex(i => i.id === currentId);
  const prev = idx > 0 ? flat[idx - 1] : null;
  const next = idx < flat.length - 1 ? flat[idx + 1] : null;

  const prevHtml = prev
    ? `<a href="#${escapeHtml(prev.id)}" class="pager-btn pager-prev"><span class="pager-dir">${escapeHtml(T.prevLabel)}</span><span class="pager-title">← ${escapeHtml(prev.title)}</span></a>`
    : `<a href="${T.homeUrl}" class="pager-btn pager-prev pager-home"><span class="pager-dir">${escapeHtml(T.backHomeLabel)}</span><span class="pager-title">${escapeHtml(T.backHomeTitle)}</span></a>`;
  const nextHtml = next
    ? `<a href="#${escapeHtml(next.id)}" class="pager-btn pager-next"><span class="pager-dir">${escapeHtml(T.nextLabel)}</span><span class="pager-title">${escapeHtml(next.title)} →</span></a>`
    : `<a href="#intro" class="pager-btn pager-next pager-home"><span class="pager-dir">${escapeHtml(T.restartLabel)}</span><span class="pager-title">${escapeHtml(T.firstDocTitle)}</span></a>`;
  return prevHtml + nextHtml;
}

function renderTOC(tocList, initialAnchor = null) {
  if (activeTocCleanup) {
    activeTocCleanup();
    activeTocCleanup = null;
  }

  const tocListEl = document.getElementById('toc-list');
  const asideEl = tocListEl && tocListEl.closest('.docs-aside');
  if (!tocListEl) return;

  if (!tocList || tocList.length === 0) {
    tocListEl.classList.remove('has-rail');
    tocListEl.innerHTML = '';
    if (asideEl) asideEl.classList.add('is-empty');
    return;
  }
  if (asideEl) asideEl.classList.remove('is-empty');

  const initActiveId = (initialAnchor && tocList.some(it => it.id === initialAnchor))
    ? initialAnchor
    : tocList[0].id;

  tocListEl.innerHTML = `<li class="toc-active-rail" id="toc-active-rail" aria-hidden="true"></li>` + tocList.map(item => `
    <li class="toc-item">
      <a href="#${escapeHtml(item.id)}" class="toc-link${item.id === initActiveId ? ' active' : ''}" data-anchor="${escapeHtml(item.id)}">${escapeHtml(item.text)}</a>
    </li>
  `).join('');

  const railEl = document.getElementById('toc-active-rail');
  const links = Array.from(tocListEl.querySelectorAll('.toc-link'));
  const syncTocRail = () => {
    if (!railEl) return;
    const act = tocListEl.querySelector('.toc-link.active');
    if (!act || act.offsetHeight === 0) return;
    tocListEl.classList.add('has-rail');
    const padY = 4;
    railEl.style.height = `${Math.max(12, act.offsetHeight - padY * 2)}px`;
    railEl.style.transform = `translate3d(0, ${act.offsetTop + padY}px, 0)`;
  };
  requestAnimationFrame(syncTocRail);

  let clickLockId = initialAnchor && tocList.some(it => it.id === initialAnchor) ? initialAnchor : null;
  let clickLockUntil = clickLockId ? Date.now() + 750 : 0;

  links.forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      const id = link.getAttribute('data-anchor');
      const el = document.getElementById(id);
      if (el) {
        clickLockId = id;
        clickLockUntil = Date.now() + 750;
        el.scrollIntoView({ behavior: preferredScrollBehavior(), block: 'start' });
        history.replaceState(null, '', `#${id}`);
        links.forEach(l => l.classList.toggle('active', l === link));
        syncTocRail();
      }
    });
  });

  const onScroll = () => {
    if (clickLockId && Date.now() < clickLockUntil) return;
    clickLockId = null;

    let activeId = tocList[0].id;
    const atBottom = (window.innerHeight + Math.ceil(window.scrollY)) >= (document.documentElement.scrollHeight - 20);
    if (atBottom) {
      activeId = tocList[tocList.length - 1].id;
    } else {
      for (const item of tocList) {
        const h = document.getElementById(item.id);
        if (h && h.getBoundingClientRect().top <= 118) {
          activeId = item.id;
        }
      }
    }
    links.forEach(l => l.classList.toggle('active', l.getAttribute('data-anchor') === activeId));
    syncTocRail();
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', syncTocRail, { passive: true });
  activeTocCleanup = () => {
    window.removeEventListener('scroll', onScroll);
    window.removeEventListener('resize', syncTocRail);
  };
}

function bindDocCopyButtons() {
  document.querySelectorAll('.code-block').forEach(block => {
    const langEl = block.querySelector('.code-header > span:first-child');
    const lang = (langEl?.textContent || '').trim().toLowerCase();
    if (lang === 'text' || lang === 'markdown' || lang === 'prompt') {
      block.classList.add('is-prose-code');
    }
  });

  document.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const block = btn.closest('.code-block');
      const code = block && block.querySelector('code');
      if (!code) return;
      const orig = btn.textContent;
      const ok = await copyElementText(code);
      btn.textContent = ok ? T.copiedBtn : T.copyManual;
      if (ok) btn.classList.add('is-copied');
      setTimeout(() => {
        btn.textContent = orig;
        btn.classList.remove('is-copied');
      }, 2200);
    });
  });

  document.querySelectorAll('.doc-copy-qq').forEach(btn => {
    btn.addEventListener('click', async () => {
      const num = btn.getAttribute('data-copy-qq') || '1126943805';
      const st = btn.querySelector('.doc-qq-status');
      await writeClipboardText(num);
      btn.classList.add('is-copied');
      if (st) {
        st.textContent = T.copiedShort;
        setTimeout(() => {
          st.textContent = T.copy;
          btn.classList.remove('is-copied');
        }, 1800);
      } else {
        setTimeout(() => btn.classList.remove('is-copied'), 1800);
      }
    });
  });

  document.querySelectorAll('[data-mobile-toc]').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      const id = link.getAttribute('data-mobile-toc');
      const details = link.closest('details');
      if (details) details.open = false;
      const el = document.getElementById(id);
      if (el) {
        el.scrollIntoView({ behavior: preferredScrollBehavior(), block: 'start' });
        history.replaceState(null, '', `#${id}`);
      }
    });
  });
}

function bindDocHeadingAnchors() {
  const headings = document.querySelectorAll('#doc-container .markdown-body h2[id], #doc-container .markdown-body h3[id]');
  headings.forEach(h => {
    const id = h.getAttribute('id');
    if (!id || h.querySelector('.heading-anchor')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'heading-anchor';
    btn.setAttribute('aria-label', T.copySectionAria);
    btn.setAttribute('title', T.copySectionTitle);
    btn.textContent = '#';
    btn.addEventListener('click', async () => {
      history.replaceState(null, '', `#${id}`);
      const shareUrl = `${location.origin}${location.pathname}#${id}`;
      await writeClipboardText(shareUrl);
      btn.textContent = T.anchorCopied;
      btn.classList.add('is-copied');
      setTimeout(() => {
        btn.textContent = '#';
        btn.classList.remove('is-copied');
      }, 1600);
    });
    h.appendChild(btn);
  });
}

function stripHtmlToPlain(html) {
  if (!html) return '';
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim();
}

function buildSearchIndex() {
  if (SEARCH_INDEX) return SEARCH_INDEX;
  SEARCH_INDEX = [];
  if (typeof DOCS_CONTENT === 'undefined') return SEARCH_INDEX;

  Object.entries(DOCS_CONTENT).forEach(([docKey, doc]) => {
    const docTitle = doc.title || docKey;
    const summary = doc.summary || '';
    const rawHtml = doc.content || '';
    const headingRegex = /<h([23])[^>]*\bid=(?:\\*["'])([^"'\\]+)(?:\\*["'])[^>]*>([\s\S]*?)<\/h\1>/gi;
    const splits = [];
    let match;
    while ((match = headingRegex.exec(rawHtml)) !== null) {
      splits.push({
        index: match.index,
        endIndex: headingRegex.lastIndex,
        anchor: match[2],
        heading: stripHtmlToPlain(match[3])
      });
    }

    if (!splits.length) {
      SEARCH_INDEX.push({
        docKey,
        anchor: docKey,
        title: docTitle,
        subTitle: '',
        summary,
        body: `${summary} ${stripHtmlToPlain(rawHtml)}`.trim(),
        text: `${docTitle} ${summary} ${stripHtmlToPlain(rawHtml)}`
      });
      return;
    }

    const leadPlain = stripHtmlToPlain(rawHtml.slice(0, splits[0].index));
    SEARCH_INDEX.push({
      docKey,
      anchor: docKey,
      title: docTitle,
      subTitle: '',
      summary,
      body: `${summary} ${leadPlain}`.trim(),
      text: `${docTitle} ${summary} ${leadPlain}`
    });

    splits.forEach((sp, i) => {
      const nextStart = i + 1 < splits.length ? splits[i + 1].index : rawHtml.length;
      const bodyPlain = stripHtmlToPlain(rawHtml.slice(sp.endIndex, nextStart));
      SEARCH_INDEX.push({
        docKey,
        anchor: sp.anchor,
        title: docTitle,
        subTitle: sp.heading,
        summary: bodyPlain.slice(0, 96) || summary,
        body: bodyPlain || summary,
        text: `${sp.heading} ${bodyPlain}`
      });
    });
  });

  return SEARCH_INDEX;
}

function formatHighlightSnippet(text, query) {
  if (!text) return '';
  const lower = text.toLowerCase();
  const q = query.toLowerCase();
  const idx = lower.indexOf(q);
  if (idx === -1) return escapeHtml(text.slice(0, 76));
  const start = Math.max(0, idx - 22);
  const end = Math.min(text.length, idx + query.length + 46);
  const prefix = start > 0 ? '…' : '';
  const suffix = end < text.length ? '…' : '';
  const before = escapeHtml(text.slice(start, idx));
  const hit = escapeHtml(text.slice(idx, idx + query.length));
  const after = escapeHtml(text.slice(idx + query.length, end));
  return `${prefix}${before}<mark class="search-hl">${hit}</mark>${after}${suffix}`;
}

function initCommandPalette() {
  const trigger = document.getElementById('search-trigger');
  const backdrop = document.getElementById('search-modal');
  const input = document.getElementById('search-input');
  const resultsEl = document.getElementById('search-results');
  const closeBtn = document.getElementById('search-close');
  if (!trigger || !backdrop || !input || !resultsEl) return;

  let focusIdx = 0;
  let currentActions = [];
  let closeTimer = null;
  let lastSearchTrigger = null;

  function openModal() {
    if (closeTimer) {
      clearTimeout(closeTimer);
      closeTimer = null;
    }
    lastSearchTrigger = document.activeElement;
    document.body.style.overflow = 'hidden';
    backdrop.classList.remove('is-closing');
    backdrop.classList.add('show');
    input.value = '';
    renderList('');
    setTimeout(() => input.focus(), 30);
  }

  function closeModal(restoreFocus = true) {
    if (!backdrop.classList.contains('show')) return;
    const finish = () => {
      document.body.style.overflow = '';
      if (restoreFocus && lastSearchTrigger && typeof lastSearchTrigger.focus === 'function') {
        lastSearchTrigger.focus();
      }
    };
    if (isReducedMotion()) {
      backdrop.classList.remove('show', 'is-closing');
      finish();
      return;
    }
    backdrop.classList.add('is-closing');
    closeTimer = setTimeout(() => {
      backdrop.classList.remove('show', 'is-closing');
      closeTimer = null;
      finish();
    }, 150);
  }

  function updateFocus() {
    const nodes = Array.from(resultsEl.querySelectorAll('.search-result-item'));
    nodes.forEach((n, i) => {
      const active = i === focusIdx;
      n.classList.toggle('is-focused', active);
      if (active) n.scrollIntoView({ block: 'nearest' });
    });
  }

  function executeItem(idx) {
    const item = currentActions[idx];
    if (!item) return;
    closeModal(!item.href);
    if (item.href) {
      if (location.hash === item.href) {
        routeHash();
      } else {
        location.hash = item.href;
      }
    } else if (typeof item.run === 'function') {
      item.run();
    }
  }

  function renderList(rawQuery) {
    const q = rawQuery.trim().toLowerCase();
    currentActions = [];

    if (!q) {
      let lastGroup = '';
      let html = '';
      T.quickCommands.forEach((cmd, idx) => {
        currentActions.push(cmd);
        if (cmd.group !== lastGroup) {
          lastGroup = cmd.group;
          html += `<div class="cmd-group-title">${escapeHtml(lastGroup)}</div>`;
        }
        html += `
          <button type="button" class="search-result-item${idx === 0 ? ' is-focused' : ''}" data-cidx="${idx}">
            <div class="search-result-title">
              <span>${escapeHtml(cmd.title)}</span>
              <span class="seg-chip">${escapeHtml(cmd.badge)}</span>
            </div>
            <div class="search-result-summary">${escapeHtml(cmd.summary)}</div>
          </button>
        `;
      });
      resultsEl.innerHTML = html;
      focusIdx = 0;
      bindResultClicks();
      return;
    }

    T.quickCommands.forEach(cmd => {
      if (cmd.title.toLowerCase().includes(q) || cmd.summary.toLowerCase().includes(q)) {
        currentActions.push({
          ...cmd,
          snippetHtml: formatHighlightSnippet(cmd.summary, rawQuery.trim())
        });
      }
    });

    const scored = [];
    buildSearchIndex().forEach(entry => {
      const titleLow = entry.title.toLowerCase();
      const subLow = entry.subTitle.toLowerCase();
      const textLow = entry.text.toLowerCase();
      let score = 0;
      if (subLow && subLow.includes(q)) score += 90;
      if (!entry.subTitle && titleLow.includes(q)) score += 100;
      if (textLow.includes(q)) score += 30;
      if (!score) return;

      const displayTitle = entry.subTitle ? `${entry.title} › ${entry.subTitle}` : entry.title;
      const snippetSource = (entry.body && entry.body.toLowerCase().includes(q)) ? entry.body : entry.text;
      scored.push({
        score,
        title: displayTitle,
        badge: entry.subTitle ? T.badgeHeading : T.badgeDoc,
        href: `#${entry.anchor}`,
        snippetHtml: formatHighlightSnippet(snippetSource, rawQuery.trim())
      });
    });

    scored.sort((a, b) => b.score - a.score);
    const seenHrefs = new Set(currentActions.map(a => a.href).filter(Boolean));
    scored.slice(0, 18).forEach(item => {
      if (!seenHrefs.has(item.href)) {
        seenHrefs.add(item.href);
        currentActions.push(item);
      }
    });

    if (!currentActions.length) {
      resultsEl.innerHTML = `<div style="padding: 28px 16px; text-align: center; color: var(--muted); font-size: 0.86rem;">${T.searchEmpty(rawQuery)}</div>`;
      return;
    }

    resultsEl.innerHTML = currentActions.map((item, idx) => `
      <button type="button" class="search-result-item${idx === 0 ? ' is-focused' : ''}" data-cidx="${idx}">
        <div class="search-result-title">
          <span>${escapeHtml(item.title)}</span>
          <span class="seg-chip seg-chip-muted">${escapeHtml(item.badge || T.badgeGo)}</span>
        </div>
        <div class="search-result-summary">${item.snippetHtml || escapeHtml(item.summary)}</div>
      </button>
    `).join('');
    focusIdx = 0;
    bindResultClicks();
  }

  function bindResultClicks() {
    resultsEl.querySelectorAll('.search-result-item').forEach(el => {
      el.addEventListener('click', () => {
        const idx = parseInt(el.getAttribute('data-cidx') || '0', 10);
        executeItem(idx);
      });
    });
  }

  trigger.addEventListener('click', openModal);
  if (closeBtn) closeBtn.addEventListener('click', () => closeModal(true));
  backdrop.addEventListener('click', e => {
    if (e.target === backdrop) closeModal(true);
  });
  input.addEventListener('input', e => renderList(e.target.value));

  window.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      backdrop.classList.contains('show') && !backdrop.classList.contains('is-closing') ? closeModal(true) : openModal();
      return;
    }
    if (
      e.key === '/' &&
      !e.ctrlKey && !e.metaKey && !e.altKey &&
      !backdrop.classList.contains('show') &&
      !isEditableTarget(e.target)
    ) {
      e.preventDefault();
      openModal();
      return;
    }
    if (!backdrop.classList.contains('show') || backdrop.classList.contains('is-closing')) return;

    if (e.key === 'Escape') {
      e.preventDefault();
      closeModal(true);
    } else if (e.key === 'Tab') {
      const box = backdrop.querySelector('.search-modal-box');
      if (box) trapFocusInDialog(e, box);
    } else if (e.key === 'ArrowDown' && currentActions.length) {
      e.preventDefault();
      focusIdx = (focusIdx + 1) % currentActions.length;
      updateFocus();
    } else if (e.key === 'ArrowUp' && currentActions.length) {
      e.preventDefault();
      focusIdx = (focusIdx - 1 + currentActions.length) % currentActions.length;
      updateFocus();
    } else if (e.key === 'Enter' && currentActions.length) {
      e.preventDefault();
      executeItem(focusIdx);
    }
  });
}

function setMobileMenuState(open) {
  const btn = document.getElementById('mobile-toggle');
  if (btn) {
    btn.setAttribute('aria-expanded', String(open));
    btn.setAttribute('aria-label', open ? T.closeNavLabel : T.openNavLabel);
  }
  const backdrop = document.getElementById('sidebar-backdrop');
  if (backdrop) backdrop.classList.toggle('show', open);
}

function initMobileMenu() {
  const toggleBtn = document.getElementById('mobile-toggle');
  const sidebarEl = document.getElementById('docs-sidebar');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (!toggleBtn || !sidebarEl) return;

  const close = () => {
    sidebarEl.classList.remove('open');
    setMobileMenuState(false);
  };

  toggleBtn.addEventListener('click', e => {
    e.stopPropagation();
    const isOpening = !sidebarEl.classList.contains('open');
    sidebarEl.classList.toggle('open', isOpening);
    setMobileMenuState(isOpening);
  });

  if (backdrop) backdrop.addEventListener('click', close);

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && sidebarEl.classList.contains('open')) {
      close();
    }
  });
}

function initBackToTop() {
  const btn = document.getElementById('back-to-top');
  if (btn) {
    btn.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: preferredScrollBehavior() });
    });
  }

  const skipBtn = document.getElementById('skip-to-main');
  if (skipBtn) {
    skipBtn.addEventListener('click', () => {
      const docEl = document.getElementById('doc-container');
      if (docEl) {
        docEl.setAttribute('tabindex', '-1');
        docEl.focus();
      }
    });
  }
}

function isEditableTarget(el) {
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || !!el.isContentEditable;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function isReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function preferredScrollBehavior() {
  return isReducedMotion() ? 'instant' : 'smooth';
}

async function writeClipboardText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {}
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

async function copyElementText(el) {
  const ok = await writeClipboardText(el.textContent || '');
  if (!ok) {
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    if (sel) {
      sel.removeAllRanges();
      sel.addRange(range);
    }
  }
  return ok;
}

function trapFocusInDialog(e, container) {
  const focusable = Array.from(
    container.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
  ).filter(el => !el.hidden && el.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

let activeToastTimer = null;
function showToast(message) {
  let toast = document.getElementById('nyairo-site-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'nyairo-site-toast';
    toast.className = 'site-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add('is-visible');
  if (activeToastTimer) clearTimeout(activeToastTimer);
  activeToastTimer = setTimeout(() => {
    toast.classList.remove('is-visible');
  }, 2200);
}
