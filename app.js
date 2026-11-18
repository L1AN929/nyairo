/**
 * nyairo Official Website Controller (v0.7.0 — Editorial Motion Edition)
 * - Circular clip-path View Transition theme switcher (Dark <-> Light) + meta theme-color sync
 * - Dual-layer Header Nav physics (hover magnetic pill + scrollspy spring lamp + scroll progress)
 * - Smooth container height morphing & directional slide transitions
 * - 6-Scene Real Desktop Stage with auto-play progress bar & hover pause
 * - 2D Gliding Floating Thumb & progress track on 6-phase Evolution Stepper
 * - IntersectionObserver staggered scroll reveal
 * - SPA 3-Column Documentation Router + Scrollspy TOC + accessible Ctrl+K Command Palette
 */

const STAGE_CAPTIONS = [
  [
    {
      kicker: '01 · 一次推门',
      title: '从站坐切换到推门出门，全程连在同一条线上',
      desc: '实时切换姿态并查看街区路网步程，选中文浜堂书店就能看门面介绍、营业状态与室内陈设。'
    },
    {
      kicker: '02 · 逐字回复',
      title: '流式交谈自然接住刚才的话题',
      desc: '刚才点的地点和动作都会带进对话，消息逐字流式出现，说好的事会稳稳落进日程。'
    },
    {
      kicker: '03 · 文稿留痕',
      title: '共同写过的草稿每次修订都可对比',
      desc: '切到随笔与创作即可展开跨版本留问与历史快照差异，深浅主题随时一键切换。'
    }
  ],
  [
    {
      kicker: '01 · 状态总览',
      title: '一眼看清 TA 此刻在哪、在做什么',
      desc: '实时呈现当前房间、站坐姿态、窗外天色与最近记住的小事，推门就能自然搭话。'
    },
    {
      kicker: '02 · 连续起居',
      title: '关掉窗口，日常依然接着走',
      desc: '起居状态与聊天记忆全部保存在本机，无论关窗还是重启电脑，再次见面都从离开那刻延续。'
    },
    {
      kicker: '03 · 随手操控',
      title: 'Ctrl+K 呼出快捷动作',
      desc: '随时切换页面、让 TA 在卧室与客厅间走动、点亮床头灯，或直接开启一段新对话。'
    }
  ],
  [
    {
      kicker: '01 · 7 套风貌与全随机生成',
      title: '内置海滨、温泉、田园、街区，或一键生成全随机专属小镇',
      desc: '随包附带凪浦、汤川、穗村、栞泽等 7 套世界；支持免选模板一键混搭 6 类地貌与异构街网生成专属小镇，也能用同款离线编辑器自由改建。'
    },
    {
      kicker: '02 · 室内平面',
      title: '卧室、客厅、厨房点击即走',
      desc: '展开住居平面即可点选房间区域走动，也能顺手开关房间里的床头灯与阅读灯。'
    },
    {
      kicker: '03 · 昼夜风物',
      title: '街景随早晚更替，店铺有开有歇',
      desc: '不同时段街区风貌与路边可读物件随之变化，店铺也按各自作息开门营业或打烊休息。'
    }
  ],
  [
    {
      kicker: '01 · 即时交谈',
      title: '回复自然浮现，随时可以打断',
      desc: '话音逐字流式呈现；遇到想改口或追问的时候，按 Esc 或点击停止即可干净收住。'
    },
    {
      kicker: '02 · 引用追问',
      title: '点选过往任意一句话接着聊',
      desc: '支持直接引用之前的发言追问、检索历史聊天记录，输入框也会随字数自然舒展。'
    },
    {
      kicker: '03 · 跨端同源',
      title: '桌面聊完，出门用微信接着说',
      desc: '桌面端与微信、Telegram 共享同一份记忆，手机上随口聊起的事回到电脑前依然记得。'
    }
  ],
  [
    {
      kicker: '01 · 随笔与文稿',
      title: '手记、剪报资料与文稿分门别类',
      desc: 'TA 写下的日常手记、收集的参考资料与共同打磨的文稿分区整理，每次修改自动留存快照。'
    },
    {
      kicker: '02 · 版本比对',
      title: '任意两版修订一键对照增删',
      desc: '在阅读器里直接展开历史版本列表，清晰比对前后改动的段落脉络，不怕误改覆盖。'
    },
    {
      kicker: '03 · 双人共读',
      title: '导入读物一起翻页、留书签与批注',
      desc: '支持把文章或书稿放进共读架，记录双人阅读进度与页码批注，也能围绕资料做专题整理。'
    }
  ],
  [
    {
      kicker: '01 · 画墙与铺地',
      title: '框选房间与街道，不用写任何配置文件',
      desc: '直接在离线可视化画布里框出房间、铺木地板或青石小路，自由搭建咖啡馆、书房或临海露台。'
    },
    {
      kicker: '02 · 门窗与家具',
      title: '开门留窗摆家具，定义营业时段与物件细节',
      desc: '点选摆放书架、阅读灯、咖啡桌或货架，也可为小店设定营业时间与常用物件说明。'
    },
    {
      kicker: '03 · 连通自检与导出',
      title: '自动检查路网通道，一键导出世界包',
      desc: '保存前自动校验门廊连通性与落脚点，支持导出完整世界包或仅含改动部分的差量文件。'
    }
  ]
];

const STAGE_WORLD_PRESETS = {
  nagiura: {
    label: '凪浦 · 海滨小镇',
    title: '街区与房间 · 凪浦',
    dark: 'assets/screenshots/desktop-world-dark.webp?v=20261118-v070-35',
    light: 'assets/screenshots/desktop-world-light.webp?v=20261118-v070-35',
    alt: 'nyairo 桌面端：凪浦海滨小镇街区总图',
    desc: '默认临海小镇「凪浦」含 17 处海湾与坡道地点，支持免选风貌一键生成全随机专属世界、按 4 种地形种子生成或用离线编辑器自由布置。'
  },
  onsawa: {
    label: '汤川 · 山间温泉',
    title: '街区与房间 · 汤川',
    dark: 'assets/screenshots/desktop-world-onsawa-dark.webp?v=20261118-v070-35',
    light: 'assets/screenshots/desktop-world-onsawa-light.webp?v=20261118-v070-35',
    alt: 'nyairo 桌面端：汤川山间温泉小镇街区总图与翠泽汤屋详情',
    desc: '山间温泉小镇「汤川」含 15 处山坡地点与露天翠泽汤屋，出门沿登山道步行真实耗时，桌面端可随时热切换体验不同地貌。'
  },
  inobe: {
    label: '穗村 · 田园乡野',
    title: '街区与房间 · 穗村',
    dark: 'assets/screenshots/desktop-world-inobe-dark.webp?v=20261118-v070-35',
    light: 'assets/screenshots/desktop-world-inobe-light.webp?v=20261118-v070-35',
    alt: 'nyairo 桌面端：穗村田园乡野小镇街区总图与水车公园路线',
    desc: '田园农庄村落「穗村」含 14 处水渠、农田与水车公园节点，每段田埂步程均按路网距离真实计时。'
  },
  wakaba: {
    label: '栞泽 · 日常街区',
    title: '街区与房间 · 栞泽',
    dark: 'assets/screenshots/desktop-world-wakaba-dark.webp?v=20261118-v070-35',
    light: 'assets/screenshots/desktop-world-wakaba-light.webp?v=20261118-v070-35',
    alt: 'nyairo 桌面端：栞泽日常街区总图与栞泽书店陈设',
    desc: '日常居住街区「栞泽」含 16 处近郊商店街、向阳咖啡店与栞泽书店，桌面端支持一键切换或导入编辑器按心意改建。'
  }
};

const STAGE_SCENES_META = [
  {
    title: '实机演示',
    kind: 'video',
    dark: 'assets/screenshots/desktop-demo-dark.mp4?v=20261118-v070-36',
    light: 'assets/screenshots/desktop-demo-light.mp4?v=20261118-v070-36',
    stillDark: 'assets/screenshots/desktop-overview-dark.webp?v=20261118-v070-35',
    stillLight: 'assets/screenshots/desktop-overview-light.webp?v=20261118-v070-35'
  },
  { title: '状态总览', dark: 'assets/screenshots/desktop-overview-dark.webp?v=20261118-v070-35', light: 'assets/screenshots/desktop-overview-light.webp?v=20261118-v070-35' },
  { title: '街区与房间', dark: 'assets/screenshots/desktop-world-dark.webp?v=20261118-v070-35', light: 'assets/screenshots/desktop-world-light.webp?v=20261118-v070-35' },
  { title: '流式交谈', dark: 'assets/screenshots/desktop-talk-dark.webp?v=20261118-v070-35', light: 'assets/screenshots/desktop-talk-light.webp?v=20261118-v070-35' },
  { title: '文稿与共读', dark: 'assets/screenshots/desktop-notes-dark.webp?v=20261118-v070-35', light: 'assets/screenshots/desktop-notes-light.webp?v=20261118-v070-35' },
  { title: '地图编辑', dark: 'assets/screenshots/desktop-editor-dark.webp?v=20261118-v070-37', light: 'assets/screenshots/desktop-editor-light.webp?v=20261118-v070-37' }
];

const layoutSyncFns = [];
let syncHeaderLampFn = null;
let openStageLightboxFn = null;
let syncStageVideosFn = null;
let jumpStageSceneWorldFn = null;

function bootstrap() {
  initTheme();
  initLangDropdown();
  initHeaderPhysics();
  initSegmentedControls();
  initInstallDock();
  initProductStage();
  initStageLightbox();
  initTryoutSandbox();
  initGalleryActions();
  initEvolutionTimeline();
  initFaqAccordion();
  initScrollStage3D();
  initScrollReveal();
  initBentoMapInteraction();
  initContrastShowcase();
  renderSidebar();
  initHomeSectionLinks();
  initRouting();
  initCommandPalette();
  initMobileMenu();
  initBackToTop();
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

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrap);
} else {
  bootstrap();
}

/* ==========================================================================
   1. Theme Switcher with Smooth Cross-Dissolve View Transition
   ========================================================================== */
function initTheme() {
  const themeBtn = document.getElementById('theme-toggle');
  let saved = 'dark';
  try {
    saved = localStorage.getItem('nyairo-theme') || localStorage.getItem('chiyo-theme') || 'dark';
  } catch {}
  applyTheme(saved);
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      document.documentElement.classList.add('theme-ready');
    });
  });

  if (themeBtn) {
    themeBtn.addEventListener('click', e => {
      const cur = document.documentElement.getAttribute('data-theme') || 'dark';
      switchTheme(cur === 'dark' ? 'light' : 'dark', e);
    });
  }
}

function switchTheme(nextTheme, clickEvt = null) {
  const reduced = isReducedMotion();
  if (!document.startViewTransition || reduced) {
    applyTheme(nextTheme);
    return;
  }

  const btnEl = document.getElementById('theme-toggle');
  let cx = window.innerWidth - 48;
  let cy = 28;
  if (clickEvt && Number.isFinite(clickEvt.clientX) && (clickEvt.clientX > 0 || clickEvt.clientY > 0)) {
    cx = clickEvt.clientX;
    cy = clickEvt.clientY;
  } else if (btnEl) {
    const r = btnEl.getBoundingClientRect();
    if (r.width > 0) {
      cx = r.left + r.width / 2;
      cy = r.top + r.height / 2;
    }
  }
  const endRadius = Math.hypot(
    Math.max(cx, window.innerWidth - cx),
    Math.max(cy, window.innerHeight - cy)
  );

  document.documentElement.classList.add('vt-switching');
  const transition = document.startViewTransition(() => {
    applyTheme(nextTheme);
  });

  transition.ready.then(() => {
    const easing = 'cubic-bezier(0.22, 1, 0.36, 1)';
    document.documentElement.animate(
      {
        clipPath: [
          `circle(0px at ${cx.toFixed(1)}px ${cy.toFixed(1)}px)`,
          `circle(${endRadius.toFixed(1)}px at ${cx.toFixed(1)}px ${cy.toFixed(1)}px)`
        ]
      },
      { duration: 420, easing, fill: 'both', pseudoElement: '::view-transition-new(root)' }
    );
    const iconEl = document.getElementById('theme-icon');
    if (iconEl && typeof iconEl.animate === 'function') {
      iconEl.animate(
        [
          { transform: 'rotate(-28deg) scale(0.88)' },
          { transform: 'rotate(0deg) scale(1)' }
        ],
        { duration: 380, easing }
      );
    }
  }).catch(() => {});

  Promise.resolve(transition.finished).finally(() => {
    document.documentElement.classList.remove('vt-switching');
  });
}

function applyTheme(theme) {
  const next = theme === 'light' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  const metaTheme = document.getElementById('meta-theme-color');
  if (metaTheme) metaTheme.setAttribute('content', next === 'light' ? '#fafafa' : '#0b0b0c');
  try {
    localStorage.setItem('nyairo-theme', next);
    localStorage.setItem('chiyo-theme', next);
  } catch {}
  if (typeof syncStageVideosFn === 'function') syncStageVideosFn();
  const backdrop = document.getElementById('stage-lightbox');
  if (backdrop && backdrop.classList.contains('show')) {
    backdrop.dispatchEvent(new Event('nyairo-theme-sync'));
  }

  const btnEl = document.getElementById('theme-toggle');
  if (!btnEl) return;
  btnEl.setAttribute('aria-pressed', next === 'light' ? 'true' : 'false');
  btnEl.setAttribute('title', next === 'dark' ? '切换为亮色模式' : '切换为暗色模式');
}

/* ==========================================================================
   2. Header Dual-Layer Nav Physics + Scroll Progress + Home Scrollspy
   ========================================================================== */
function initHeaderPhysics() {
  const nav = document.getElementById('header-nav');
  const hoverPill = document.getElementById('nav-hover-pill');
  const activeLamp = document.getElementById('nav-active-lamp');
  const progressBar = document.getElementById('scroll-progress-bar');
  if (!nav) return;

  const links = Array.from(nav.querySelectorAll('.nav-link'));

  function syncActiveLamp() {
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
  }

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

  // Scroll Progress + Header Elevation + Homepage Section Scrollspy + Back-to-Top visibility
  const headerEl = document.querySelector('.site-header');
  const backTopBtn = document.getElementById('back-to-top');
  let ticking = false;
  function onScrollFrame() {
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

    const homeView = document.getElementById('view-home');
    if (homeView && homeView.style.display !== 'none') {
      const faqEl = document.getElementById('home-faq');
      const galEl = document.getElementById('home-gallery');
      const capEl = document.getElementById('home-capabilities');
      const archEl = document.getElementById('home-arch');
      const tryEl = document.getElementById('home-tryout');
      const prevEl = document.getElementById('home-preview');
      let activeId = 'nav-home';
      if (faqEl && faqEl.getBoundingClientRect().top <= 240) {
        activeId = 'nav-faq';
      } else if (galEl && galEl.getBoundingClientRect().top <= 240) {
        activeId = 'nav-gallery';
      } else if (capEl && capEl.getBoundingClientRect().top <= 240) {
        activeId = 'nav-capabilities';
      } else if (archEl && archEl.getBoundingClientRect().top <= 240) {
        activeId = 'nav-arch';
      } else if (tryEl && tryEl.getBoundingClientRect().top <= 240) {
        activeId = 'nav-tryout';
      } else if (prevEl && prevEl.getBoundingClientRect().top <= 240) {
        activeId = 'nav-preview';
      }
      const curActive = nav.querySelector('.nav-link.active');
      if (!curActive || curActive.id !== activeId) {
        links.forEach(l => l.classList.toggle('active', l.id === activeId));
        syncActiveLamp();
      }
    }
  }

  window.addEventListener('scroll', () => {
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(onScrollFrame);
    }
  }, { passive: true });
}

/* ==========================================================================
   3. Spring Sliding Segmented Controls & Smooth Height Morphing
   ========================================================================== */
function syncSegThumb(bar) {
  if (!bar) return;
  const thumb = bar.querySelector('.seg-thumb');
  const active = bar.querySelector('.seg-btn.is-active');
  if (!thumb || !active) return;
  const w = active.offsetWidth;
  const x = active.offsetLeft;
  if (w > 0) {
    thumb.style.width = `${w}px`;
    thumb.style.transform = `translate3d(${x}px, 0, 0)`;
  }
}

function initSegmentedControls() {
  document.querySelectorAll('.seg-bar').forEach(bar => {
    const update = () => syncSegThumb(bar);
    layoutSyncFns.push(update);
    requestAnimationFrame(update);
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(update);
      ro.observe(bar);
    }
  });
  window.addEventListener('resize', () => layoutSyncFns.forEach(fn => fn()));
}

function morphContainerHeight(viewport, mutateDom) {
  if (!viewport || isReducedMotion()) {
    mutateDom();
    return;
  }
  if (typeof viewport._morphCleanup === 'function') {
    viewport._morphCleanup();
  }
  const startH = viewport.offsetHeight;
  viewport.style.height = `${startH}px`;
  mutateDom();
  // Measure target height cleanly
  viewport.style.height = 'auto';
  const endH = viewport.offsetHeight;
  if (Math.abs(endH - startH) < 2) {
    viewport.style.height = '';
    return;
  }
  viewport.style.height = `${startH}px`;
  void viewport.offsetHeight;
  viewport.style.height = `${endH}px`;

  const done = e => {
    if (e && (e.target !== viewport || e.propertyName !== 'height')) return;
    viewport.style.height = '';
    viewport.removeEventListener('transitionend', done);
    viewport._morphCleanup = null;
  };
  viewport._morphCleanup = () => {
    viewport.removeEventListener('transitionend', done);
    viewport.style.height = '';
    viewport._morphCleanup = null;
  };
  viewport.addEventListener('transitionend', done);
}

/* ==========================================================================
   4. Download Guidance, SHA256 / CLI Copy & Agent Prompt Drawer
   ========================================================================== */
function initInstallDock() {
  // Post-download gentle guidance for Windows setup.exe (Hero + #home-downloads card)
  [
    ['win-download-btn', 'win-dl-feedback'],
    ['dl-win-card-btn', 'dl-win-card-feedback']
  ].forEach(([btnId, fbId]) => {
    const btn = document.getElementById(btnId);
    const fb = document.getElementById(fbId);
    if (!btn || !fb) return;
    btn.addEventListener('click', () => {
      if (!fb.hidden) return;
      fb.hidden = false;
    });
  });

  // Copy setup.exe SHA256 (download card + trust verify bar)
  [
    ['copy-setup-sha', 'sha-copy-label'],
    ['copy-trust-sha', 'trust-sha-label']
  ].forEach(([btnId, lblId]) => {
    const shaBtn = document.getElementById(btnId);
    const shaLbl = document.getElementById(lblId);
    if (!shaBtn || !shaLbl) return;
    shaBtn.addEventListener('click', async () => {
      const sha = shaBtn.getAttribute('data-sha') || '';
      const ok = await writeClipboardText(sha);
      shaLbl.textContent = ok ? '已复制 ✓' : '复制失败';
      setTimeout(() => { shaLbl.textContent = '复制'; }, 2000);
    });
  });

  // Copy CLI install command (works on both the code bar and its action button)
  const cmdBar = document.getElementById('hero-cmd');
  const cmdBtn = document.getElementById('hero-cmd-btn');
  if (cmdBar && cmdBtn) {
    const defaultLabel = cmdBtn.textContent || '复制命令';
    const triggerCliCopy = async () => {
      const codeEl = cmdBar.querySelector('.cmd-code');
      if (!codeEl) return;
      const ok = await copyElementText(codeEl);
      cmdBtn.textContent = ok ? '已复制 ✓' : '请按 Ctrl+C';
      cmdBtn.classList.toggle('copied', ok);
      setTimeout(() => {
        cmdBtn.textContent = defaultLabel;
        cmdBtn.classList.remove('copied');
      }, 2000);
    };
    cmdBar.addEventListener('click', e => {
      if (e.target === cmdBtn) return;
      triggerCliCopy();
    });
    cmdBar.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        triggerCliCopy();
      }
    });
    cmdBtn.addEventListener('click', e => {
      e.stopPropagation();
      triggerCliCopy();
    });
  }

  // Agent Prompt Drawer (Smooth Height Animation) & Copy
  const drawerBtn = document.getElementById('agent-toggle-drawer');
  const drawerPre = document.getElementById('agent-code-drawer');
  const drawerLbl = document.getElementById('drawer-toggle-label');
  if (drawerBtn && drawerPre) {
    let drawerAnim = null;
    drawerBtn.addEventListener('click', () => {
      const nextOpen = drawerPre.hidden;
      drawerBtn.setAttribute('aria-expanded', String(nextOpen));
      if (drawerLbl) {
        drawerLbl.textContent = nextOpen ? '收起指令全文' : '展开查看指令全文';
      }
      if (drawerAnim) {
        drawerAnim.cancel();
        drawerAnim = null;
      }
      if (isReducedMotion() || typeof drawerPre.animate !== 'function') {
        drawerPre.style.overflow = '';
        drawerPre.hidden = !nextOpen;
        return;
      }
      if (nextOpen) {
        drawerPre.hidden = false;
        const targetH = drawerPre.offsetHeight;
        drawerPre.style.overflow = 'hidden';
        drawerAnim = drawerPre.animate(
          [
            { height: '0px', opacity: 0, marginTop: '0px' },
            { height: `${targetH}px`, opacity: 1, marginTop: '16px' }
          ],
          { duration: 280, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' }
        );
        drawerAnim.onfinish = () => {
          drawerPre.style.overflow = '';
          drawerAnim = null;
        };
        drawerAnim.oncancel = () => {
          drawerPre.style.overflow = '';
        };
      } else {
        const startH = drawerPre.offsetHeight;
        drawerPre.style.overflow = 'hidden';
        drawerAnim = drawerPre.animate(
          [
            { height: `${startH}px`, opacity: 1, marginTop: '16px' },
            { height: '0px', opacity: 0, marginTop: '0px' }
          ],
          { duration: 220, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' }
        );
        drawerAnim.onfinish = () => {
          drawerPre.hidden = true;
          drawerPre.style.overflow = '';
          drawerAnim = null;
        };
        drawerAnim.oncancel = () => {
          drawerPre.style.overflow = '';
        };
      }
    });
  }

  const agentBtn = document.getElementById('agent-cmd-btn');
  const agentCode = document.getElementById('agent-prompt-code');
  if (agentBtn && agentCode) {
    agentBtn.addEventListener('click', async () => {
      const ok = await writeClipboardText(agentCode.textContent || '');
      const span = agentBtn.querySelector('span');
      if (span) {
        const prev = span.textContent;
        span.textContent = ok ? '已复制完整 Prompt ✓' : '复制失败，请展开手动复制';
        setTimeout(() => { span.textContent = prev; }, 2400);
      }
    });
  }
}

/* ==========================================================================
   5. Interactive Real Desktop Stage (Auto-Cycle + Directional Slide + Cascade)
   ========================================================================== */
function initProductStage() {
  const stageEl = document.getElementById('app-stage');
  const bar = document.getElementById('stage-seg');
  const autoBar = document.getElementById('stage-auto-bar');
  const zoomBtn = document.getElementById('stage-zoom-btn');
  const screenWrap = document.getElementById('stage-screen-wrap');
  const pausePill = document.getElementById('stage-pause-pill');
  if (!bar || !stageEl) return;

  const btns = Array.from(bar.querySelectorAll('.seg-btn'));
  const scenes = Array.from(document.querySelectorAll('.stage-scene'));
  const captionCells = Array.from(document.querySelectorAll('.stage-caption-cell'));
  const worldStrip = document.getElementById('stage-world-strip');

  const darkV = document.getElementById('stage-video-dark');
  const lightV = document.getElementById('stage-video-light');

  let currentIdx = 0;
  let manualLocked = false;

  function syncStageVideos(restartFromZero = false) {
    if (!darkV && !lightV) return;
    const theme = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    const activeV = theme === 'light' ? lightV : darkV;
    const idleV = theme === 'light' ? darkV : lightV;
    const prevTime = idleV && Number.isFinite(idleV.currentTime) ? idleV.currentTime : 0;
    if (idleV && !idleV.paused) idleV.pause();

    const lbOpen = !!document.getElementById('stage-lightbox')?.classList.contains('show');
    const homeHidden = document.getElementById('view-home')?.style.display === 'none';
    const shouldPlay = currentIdx === 0 && !stageEl.classList.contains('is-out-of-view') && !lbOpen && !homeHidden && !document.hidden;

    if (activeV) {
      if (activeV.preload !== 'auto') {
        activeV.preload = 'auto';
      }
      if (restartFromZero || activeV.ended) {
        try { activeV.currentTime = 0; } catch {}
      } else if (prevTime > 0 && activeV.currentTime < 0.05 && Number.isFinite(activeV.duration) && activeV.duration > prevTime) {
        try { activeV.currentTime = prevTime; } catch {}
      }
      if (shouldPlay) {
        const p = activeV.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      } else if (!activeV.paused) {
        activeV.pause();
      }
    }
  }
  [darkV, lightV].forEach(v => {
    if (!v) return;
    v.addEventListener('ended', () => {
      try { v.currentTime = 0; } catch {}
      syncStageVideos();
    });
  });
  document.addEventListener('visibilitychange', () => syncStageVideos());
  syncStageVideosFn = () => {
    syncStageVideos();
    const lbBackdrop = document.getElementById('stage-lightbox');
    if (lbBackdrop && lbBackdrop.classList.contains('show')) {
      lbBackdrop.dispatchEvent(new CustomEvent('nyairo-theme-sync'));
    }
  };

  function restartAutoProgress() {
    if (!autoBar || isReducedMotion() || manualLocked) {
      if (autoBar) autoBar.classList.remove('is-running');
      if (pausePill) pausePill.style.display = 'none';
      return;
    }
    autoBar.style.setProperty('--stage-dur', currentIdx === 0 ? '17s' : '6.5s');
    autoBar.classList.remove('is-running');
    void autoBar.offsetWidth;
    autoBar.classList.add('is-running');
  }

  function setScene(index, focus = false, byUser = false) {
    const prevIdx = currentIdx;
    const movingLeft = index < prevIdx;
    currentIdx = index;

    if (byUser === true) {
      manualLocked = true;
      if (autoBar) autoBar.classList.remove('is-running');
      if (pausePill) pausePill.style.display = 'none';
    }

    btns.forEach((b, i) => {
      const active = i === index;
      b.classList.toggle('is-active', active);
      b.setAttribute('aria-selected', String(active));
      b.tabIndex = active ? 0 : -1;
      if (active && focus) b.focus();
    });

    scenes.forEach((s, i) => {
      const active = i === index;
      if (active && i !== prevIdx) {
        s.classList.toggle('from-left', movingLeft);
        void s.offsetWidth;
      }
      s.classList.toggle('is-active', active);
      if (!active) {
        s.classList.toggle('from-left', i < index);
      }
    });

    const caps = STAGE_CAPTIONS[index];
    if (caps) {
      caps.forEach((c, idx) => {
        const n = idx + 1;
        const kEl = document.getElementById(`cap-kicker-${n}`);
        const tEl = document.getElementById(`cap-title-${n}`);
        const dEl = document.getElementById(`cap-desc-${n}`);
        if (kEl) kEl.textContent = c.kicker;
        if (tEl) tEl.textContent = c.title;
        if (dEl) dEl.textContent = c.desc;
      });
      if (prevIdx !== index && !isReducedMotion()) {
        captionCells.forEach(cell => {
          cell.classList.remove('is-refreshing');
          void cell.offsetWidth;
          cell.classList.add('is-refreshing');
        });
      }
    }

    syncSegThumb(bar);
    if (worldStrip) {
      worldStrip.hidden = index !== 2;
      if (index === 2) {
        requestAnimationFrame(() => {
          const wb = document.getElementById('stage-world-bar');
          if (wb && typeof wb._syncThumb === 'function') wb._syncThumb();
        });
      }
    }
    restartAutoProgress();
    syncStageVideos(prevIdx !== 0 && index === 0);
  }

  if (autoBar) {
    autoBar.addEventListener('animationend', () => {
      if (manualLocked) return;
      const homeView = document.getElementById('view-home');
      if (homeView && homeView.style.display === 'none') return;
      setScene((currentIdx + 1) % btns.length, false, false);
    });
    requestAnimationFrame(restartAutoProgress);
  }

  // Pause auto-cycle & video only when completely scrolled away from viewport
  if (typeof IntersectionObserver !== 'undefined') {
    const stageIo = new IntersectionObserver(
      entries => {
        entries.forEach(entry => {
          stageEl.classList.toggle('is-out-of-view', !entry.isIntersecting);
        });
        syncStageVideos();
      },
      { threshold: 0.01, rootMargin: '160px 0px' }
    );
    stageIo.observe(stageEl);
  }
  syncStageVideos();

  const triggerZoom = () => {
    manualLocked = true;
    if (autoBar) autoBar.classList.remove('is-running');
    if (pausePill) pausePill.style.display = 'none';
    syncStageVideos();
    if (typeof openStageLightboxFn === 'function') {
      openStageLightboxFn(currentIdx, idx => setScene(idx, false, true));
    }
  };

  if (zoomBtn) zoomBtn.addEventListener('click', triggerZoom);
  if (screenWrap) {
    screenWrap.addEventListener('click', triggerZoom);
    screenWrap.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        triggerZoom();
      }
    });
  }

  btns.forEach((btn, idx) => {
    btn.addEventListener('click', () => setScene(idx, false, true));
    btn.addEventListener('keydown', e => {
      let next;
      if (e.key === 'ArrowRight') next = (idx + 1) % btns.length;
      if (e.key === 'ArrowLeft') next = (idx - 1 + btns.length) % btns.length;
      if (e.key === 'Home') next = 0;
      if (e.key === 'End') next = btns.length - 1;
      if (next === undefined) return;
      e.preventDefault();
      setScene(next, true, true);
    });
  });

  // Scene 2 Multi-Town Screenshot Switcher (凪浦 / 汤川 / 穗村 / 栞泽)
  const worldBar = document.getElementById('stage-world-bar');
  const worldThumb = document.getElementById('world-pill-thumb');
  const worldImgDark = document.getElementById('stage-world-img-dark');
  const worldImgLight = document.getElementById('stage-world-img-light');
  if (worldBar && worldImgDark && worldImgLight) {
    const worldBtns = Array.from(worldBar.querySelectorAll('.stage-world-pill'));
    let activeWorldSlug = 'nagiura';

    const syncWorldThumb = () => {
      if (!worldThumb) return;
      const act = worldBar.querySelector('.stage-world-pill.is-active');
      if (!act || act.offsetWidth === 0) return;
      worldBar.classList.add('has-glider');
      worldThumb.style.width = `${act.offsetWidth}px`;
      worldThumb.style.transform = `translate3d(${act.offsetLeft}px, 0, 0)`;
    };
    worldBar._syncThumb = syncWorldThumb;
    layoutSyncFns.push(syncWorldThumb);

    const selectWorld = (slug, byUser = true) => {
      const preset = STAGE_WORLD_PRESETS[slug];
      if (!preset) return;
      const changed = slug !== activeWorldSlug;
      activeWorldSlug = slug;
      if (byUser) {
        manualLocked = true;
        if (autoBar) autoBar.classList.remove('is-running');
        if (pausePill) pausePill.style.display = 'none';
      }
      worldBtns.forEach(b => {
        const active = b.getAttribute('data-world') === slug;
        b.classList.toggle('is-active', active);
        b.setAttribute('aria-pressed', String(active));
      });
      syncWorldThumb();
      worldImgDark.src = preset.dark;
      worldImgLight.src = preset.light;
      worldImgDark.alt = preset.alt;
      worldImgLight.alt = preset.alt;
      if (changed && !isReducedMotion()) {
        [worldImgDark, worldImgLight].forEach(img => {
          img.classList.remove('is-switching-town');
          void img.offsetWidth;
          img.classList.add('is-switching-town');
        });
      }
      if (STAGE_SCENES_META[2]) {
        STAGE_SCENES_META[2].title = preset.title;
        STAGE_SCENES_META[2].dark = preset.dark;
        STAGE_SCENES_META[2].light = preset.light;
      }
      if (STAGE_CAPTIONS[2]?.[0]) {
        STAGE_CAPTIONS[2][0].desc = preset.desc;
        if (currentIdx === 2) {
          const dEl = document.getElementById('cap-desc-1');
          if (dEl) dEl.textContent = preset.desc;
        }
      }
    };

    worldBar.addEventListener('click', e => e.stopPropagation());
    worldBar.addEventListener('keydown', e => e.stopPropagation());
    worldBtns.forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        selectWorld(btn.getAttribute('data-world'), true);
      });
    });

    jumpStageSceneWorldFn = (sceneIdx, worldSlug = null) => {
      setScene(sceneIdx, false, true);
      if (worldSlug && sceneIdx === 2) {
        requestAnimationFrame(() => selectWorld(worldSlug, true));
      }
    };
  } else {
    jumpStageSceneWorldFn = sceneIdx => setScene(sceneIdx, false, true);
  }

  // Pre-decode remaining stage screenshots (and extra world stills) during idle time
  const warmStageImages = () => {
    stageEl.querySelectorAll('img[loading="lazy"]').forEach(img => {
      img.loading = 'eager';
      if (typeof img.decode === 'function') img.decode().catch(() => {});
    });
    Object.values(STAGE_WORLD_PRESETS).forEach(p => {
      ['dark', 'light'].forEach(k => {
        const pre = new Image();
        pre.src = p[k];
      });
    });
  };
  if (typeof requestIdleCallback === 'function') {
    requestIdleCallback(warmStageImages, { timeout: 1200 });
  } else {
    setTimeout(warmStageImages, 500);
  }
}

function initStageLightbox() {
  const backdrop = document.getElementById('stage-lightbox');
  const closeBtn = document.getElementById('lightbox-close');
  const prevBtn = document.getElementById('lightbox-prev');
  const nextBtn = document.getElementById('lightbox-next');
  const counterEl = document.getElementById('lightbox-counter');
  const titleEl = document.getElementById('lightbox-title');
  const imgDark = document.getElementById('lightbox-img-dark');
  const imgLight = document.getElementById('lightbox-img-light');
  const vidDark = document.getElementById('lightbox-video-dark');
  const vidLight = document.getElementById('lightbox-video-light');
  const tabsWrap = document.getElementById('lightbox-tabs');
  const lbThumb = tabsWrap ? tabsWrap.querySelector('.lb-seg-thumb') : null;
  const tabs = Array.from(document.querySelectorAll('#lightbox-tabs .lightbox-tab'));
  if (!backdrop || !imgDark || !imgLight) return;

  let activeIdx = 0;
  let onSceneSync = null;
  let closeTimer = null;
  let lastTrigger = null;

  function syncLbThumb() {
    if (!tabsWrap || !lbThumb) return;
    const act = tabsWrap.querySelector('.lightbox-tab.is-active');
    if (!act || act.offsetWidth === 0) return;
    tabsWrap.classList.add('has-glider');
    lbThumb.style.width = `${act.offsetWidth}px`;
    lbThumb.style.transform = `translate3d(${act.offsetLeft}px, 0, 0)`;
  }
  layoutSyncFns.push(syncLbThumb);

  function syncLightboxMedia(meta, animateSwitch = false) {
    const reduced = isReducedMotion();
    const isVideo = meta?.kind === 'video' && backdrop.classList.contains('show');
    const theme = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    if (isVideo && vidDark && vidLight) {
      imgDark.hidden = true;
      imgLight.hidden = true;
      vidDark.hidden = false;
      vidLight.hidden = false;
      const activeV = theme === 'light' ? vidLight : vidDark;
      const idleV = theme === 'light' ? vidDark : vidLight;
      if (idleV && !idleV.paused) idleV.pause();
      if (activeV.preload !== 'auto') activeV.preload = 'auto';
      if (activeV.ended) {
        try { activeV.currentTime = 0; } catch {}
      }
      activeV.play().catch(() => {});
    } else {
      if (vidDark) { if (!vidDark.paused) vidDark.pause(); vidDark.hidden = true; }
      if (vidLight) { if (!vidLight.paused) vidLight.pause(); vidLight.hidden = true; }
      imgDark.hidden = false;
      imgLight.hidden = false;
      imgDark.src = (reduced && meta.stillDark) ? meta.stillDark : (meta.stillDark || meta.dark);
      imgLight.src = (reduced && meta.stillLight) ? meta.stillLight : (meta.stillLight || meta.light);
      imgDark.alt = `nyairo 桌面端高清预览：${meta.title}`;
      imgLight.alt = `nyairo 桌面端高清预览：${meta.title}`;
      if (animateSwitch && !reduced) {
        [imgDark, imgLight].forEach(img => {
          img.classList.remove('is-switching');
          void img.offsetWidth;
          img.classList.add('is-switching');
        });
      }
    }
  }

  function renderLightboxScene(idx, animateSwitch = false, focusTab = false) {
    activeIdx = (idx + STAGE_SCENES_META.length) % STAGE_SCENES_META.length;
    const meta = STAGE_SCENES_META[activeIdx];
    if (counterEl) counterEl.textContent = `0${activeIdx + 1} / 0${STAGE_SCENES_META.length}`;
    if (titleEl) titleEl.textContent = meta.title;
    syncLightboxMedia(meta, animateSwitch);
    tabs.forEach((t, i) => {
      const active = i === activeIdx;
      t.classList.toggle('is-active', active);
      t.setAttribute('aria-selected', String(active));
      t.tabIndex = active ? 0 : -1;
      if (active && focusTab) t.focus();
    });
    requestAnimationFrame(syncLbThumb);
    if (typeof onSceneSync === 'function') onSceneSync(activeIdx);
  }

  function closeLightbox() {
    if (!backdrop.classList.contains('show')) return;
    backdrop.setAttribute('aria-hidden', 'true');
    if (vidDark && !vidDark.paused) vidDark.pause();
    if (vidLight && !vidLight.paused) vidLight.pause();
    const finishClose = () => {
      if (!document.getElementById('search-modal')?.classList.contains('show')) {
        document.body.style.overflow = '';
      }
      if (typeof syncStageVideosFn === 'function') syncStageVideosFn();
      if (lastTrigger && typeof lastTrigger.focus === 'function') lastTrigger.focus();
    };
    if (isReducedMotion()) {
      backdrop.classList.remove('show', 'is-closing');
      finishClose();
      return;
    }
    backdrop.classList.add('is-closing');
    closeTimer = setTimeout(() => {
      backdrop.classList.remove('show', 'is-closing');
      closeTimer = null;
      finishClose();
    }, 150);
  }

  backdrop.addEventListener('nyairo-theme-sync', () => {
    if (backdrop.classList.contains('show')) syncLightboxMedia(STAGE_SCENES_META[activeIdx]);
  });

  openStageLightboxFn = (idx, syncCallback) => {
    if (closeTimer) {
      clearTimeout(closeTimer);
      closeTimer = null;
    }
    lastTrigger = document.activeElement;
    onSceneSync = syncCallback || null;
    document.body.style.overflow = 'hidden';
    backdrop.classList.remove('is-closing');
    backdrop.classList.add('show');
    backdrop.setAttribute('aria-hidden', 'false');
    renderLightboxScene(idx || 0);
    if (typeof syncStageVideosFn === 'function') syncStageVideosFn();
    if (closeBtn) setTimeout(() => closeBtn.focus(), 20);
  };

  if (closeBtn) closeBtn.addEventListener('click', closeLightbox);
  if (prevBtn) prevBtn.addEventListener('click', () => renderLightboxScene(activeIdx - 1, true));
  if (nextBtn) nextBtn.addEventListener('click', () => renderLightboxScene(activeIdx + 1, true));
  tabs.forEach((tab, idx) => {
    tab.addEventListener('click', () => {
      const sidx = parseInt(tab.getAttribute('data-lb-scene') || '0', 10);
      renderLightboxScene(sidx, true);
    });
    tab.addEventListener('keydown', e => {
      if (e.key === 'Home') {
        e.preventDefault();
        renderLightboxScene(0, true, true);
      } else if (e.key === 'End') {
        e.preventDefault();
        renderLightboxScene(tabs.length - 1, true, true);
      }
    });
  });

  backdrop.addEventListener('click', e => {
    if (e.target === backdrop || e.target.id === 'lightbox-canvas') closeLightbox();
  });

  window.addEventListener('keydown', e => {
    if (!backdrop.classList.contains('show') || backdrop.classList.contains('is-closing')) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      closeLightbox();
    } else if (e.key === 'Tab') {
      const box = backdrop.querySelector('.lightbox-box');
      if (box) trapFocusInDialog(e, box);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      renderLightboxScene(activeIdx + 1, true, document.activeElement?.classList.contains('lightbox-tab'));
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      renderLightboxScene(activeIdx - 1, true, document.activeElement?.classList.contains('lightbox-tab'));
    }
  });
}

/* ==========================================================================
   6. 2D Spring Evolution Stepper + Progress Fill + Height Morph
   ========================================================================== */
function initEvolutionTimeline() {
  const stage = document.getElementById('evo-stage');
  const stepper = document.getElementById('evo-stepper');
  const thumb = document.getElementById('evo-thumb');
  const fill = document.getElementById('evo-progress-fill');
  if (!stage || !stepper) return;

  const bodyEl = stage.querySelector('.evo-body');
  const btns = Array.from(stage.querySelectorAll('.evo-step-btn'));
  const panels = Array.from(stage.querySelectorAll('.evo-panel'));
  let currentPhase = 4;

  function syncEvoThumb() {
    const active = stepper.querySelector('.evo-step-btn.is-active');
    if (!thumb || !active || active.offsetWidth === 0) return;
    thumb.style.width = `${active.offsetWidth}px`;
    thumb.style.height = `${active.offsetHeight}px`;
    thumb.style.transform = `translate3d(${active.offsetLeft}px, ${active.offsetTop}px, 0)`;
  }

  layoutSyncFns.push(syncEvoThumb);
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(syncEvoThumb);
    ro.observe(stepper);
  }

  function setPhase(idx, focus = false, animateBody = true) {
    const isPrev = idx < currentPhase;
    currentPhase = idx;

    btns.forEach((b, i) => {
      const active = i === idx;
      b.classList.toggle('is-active', active);
      b.setAttribute('aria-selected', String(active));
      b.tabIndex = active ? 0 : -1;
      if (active && focus) b.focus();
    });

    const applyPanels = () => {
      panels.forEach((p, i) => {
        const active = i === idx;
        p.hidden = !active;
        if (active) p.classList.toggle('dir-prev', isPrev);
      });
    };

    if (animateBody && bodyEl) {
      morphContainerHeight(bodyEl, applyPanels);
    } else {
      applyPanels();
    }

    if (fill && btns.length > 1) {
      const pct = ((idx + 1) / btns.length) * 100;
      fill.style.width = `${pct.toFixed(2)}%`;
    }

    requestAnimationFrame(syncEvoThumb);
  }

  btns.forEach((btn, idx) => {
    btn.addEventListener('click', () => setPhase(idx, false, true));
    btn.addEventListener('keydown', e => {
      let next;
      if (e.key === 'ArrowRight') next = (idx + 1) % btns.length;
      if (e.key === 'ArrowLeft') next = (idx - 1 + btns.length) % btns.length;
      if (e.key === 'Home') next = 0;
      if (e.key === 'End') next = btns.length - 1;
      if (next === undefined) return;
      e.preventDefault();
      setPhase(next, true, true);
    });
  });

  setPhase(4, false, false);
}

/* ==========================================================================
   6b. Smooth Animated FAQ Accordion (<details class="faq-item">)
   ========================================================================== */
function initFaqAccordion() {
  const items = Array.from(document.querySelectorAll('#home-faq .faq-item'));
  items.forEach(details => {
    const summary = details.querySelector('summary');
    const answer = details.querySelector('.faq-a');
    if (!summary || !answer) return;

    let anim = null;
    summary.addEventListener('click', e => {
      if (isReducedMotion() || typeof details.animate !== 'function') return;
      e.preventDefault();
      if (anim) {
        anim.cancel();
        anim = null;
      }

      const startH = details.offsetHeight;
      details.style.overflow = 'hidden';

      if (details.open) {
        const endH = summary.offsetHeight;
        anim = details.animate(
          [{ height: `${startH}px` }, { height: `${endH}px` }],
          { duration: 220, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' }
        );
        anim.onfinish = () => {
          details.open = false;
          details.style.height = '';
          details.style.overflow = '';
          anim = null;
        };
        anim.oncancel = () => {
          details.style.height = '';
          details.style.overflow = '';
        };
      } else {
        details.style.height = `${startH}px`;
        details.open = true;
        const endH = summary.offsetHeight + answer.offsetHeight;
        details.style.height = '';
        anim = details.animate(
          [{ height: `${startH}px` }, { height: `${endH}px` }],
          { duration: 250, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' }
        );
        anim.onfinish = () => {
          details.style.height = '';
          details.style.overflow = '';
          anim = null;
        };
        anim.oncancel = () => {
          details.style.height = '';
          details.style.overflow = '';
        };
      }
    });
  });
}

/* ==========================================================================
   7. Minimalist Scroll Perspective, Reveal & Soft Hover Spotlights
   ========================================================================== */
function initScrollStage3D() {
  const halo = document.getElementById('app-stage-halo');
  const homeView = document.getElementById('view-home');
  const heroCopy = document.getElementById('hero-copy-stage');
  if (!halo || !homeView) return;

  let ticking = false;

  function resetAllTransforms() {
    if (halo.style.transform) halo.style.transform = '';
    halo.classList.remove('stage-3d-active');
    if (heroCopy) {
      heroCopy.style.transform = '';
      heroCopy.style.opacity = '';
    }
  }

  function update() {
    ticking = false;
    if (homeView.style.display === 'none') return;

    if (isReducedMotion()) {
      resetAllTransforms();
      return;
    }

    const vh = window.innerHeight || 900;
    const vw = window.innerWidth || 1280;
    const sy = window.scrollY || 0;

    // 1. Whisper-gentle Hero scroll fade (strictly 'none' at sy <= 6 for crisp ClearType)
    if (heroCopy) {
      if (sy <= 6 || vw <= 768) {
        if (heroCopy.style.transform) heroCopy.style.transform = '';
        if (heroCopy.style.opacity) heroCopy.style.opacity = '';
      } else if (sy < 620) {
        const hp = Math.min(1, (sy - 6) / 580);
        heroCopy.style.opacity = (1 - hp * 0.28).toFixed(3);
        heroCopy.style.transform = `translate3d(0, ${(-hp * 8).toFixed(1)}px, 0)`;
      }
    }

    // 2. Keep desktop preview stage strictly transform-free so 2x Retina screenshots rasterize 1:1
    if (halo.style.transform) halo.style.transform = '';
    halo.classList.remove('stage-3d-active');
  }

  function onScroll() {
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(update);
    }
  }

  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });
  layoutSyncFns.push(update);
  update();
}

function initScrollReveal() {
  const cascadeGrids = Array.from(
    document.querySelectorAll('.eco-strip, .cap-grid, .gallery-worlds-grid, .gallery-personas-grid, .onboard-steps, .downloads-grid, .beginner-guide-grid')
  );
  cascadeGrids.forEach(grid => {
    Array.from(grid.children).forEach((child, idx) => {
      child.classList.add('reveal-cascade-item');
      child.style.setProperty('--cascade-delay', `${idx * 55}ms`);
    });
  });

  const targets = Array.from(document.querySelectorAll('[data-reveal], .reveal-cascade-item'));
  if (!targets.length) return;

  if (isReducedMotion() || typeof IntersectionObserver === 'undefined') {
    targets.forEach(el => el.classList.add('is-revealed'));
    return;
  }

  const io = new IntersectionObserver(
    entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-revealed');
          io.unobserve(entry.target);
        }
      });
    },
    { rootMargin: '0px 0px -6% 0px', threshold: 0.06 }
  );

  targets.forEach(el => io.observe(el));
}

function initBentoMapInteraction() {
  const lampBtn = document.getElementById('bento-lamp-btn');
  const lampLabel = document.getElementById('bento-lamp-label');
  const mapVisual = document.getElementById('bento-map-visual');
  const walkStatus = document.getElementById('bento-walk-status');
  if (!mapVisual) return;

  const svgEl = mapVisual.querySelector('svg');
  if (svgEl && typeof svgEl.pauseAnimations === 'function') {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const syncSmil = () => {
      try {
        mq.matches ? svgEl.pauseAnimations() : svgEl.unpauseAnimations();
      } catch {}
    };
    syncSmil();
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', syncSmil);
    }
  }

  if (lampBtn) {
    lampBtn.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      const isOff = mapVisual.classList.toggle('is-lamp-off');
      lampBtn.setAttribute('aria-pressed', isOff ? 'false' : 'true');
      lampBtn.classList.toggle('is-off', isOff);
      if (lampLabel) {
        lampLabel.textContent = isOff ? '卧室床头灯 · 关' : '卧室床头灯 · 开';
      }
    });
  }

  const stops = Array.from(mapVisual.querySelectorAll('.topo-stop'));
  if (stops.length && walkStatus) {
    const activateStop = stopEl => {
      const nextText = stopEl.getAttribute('data-status');
      if (!nextText || walkStatus.textContent === nextText) return;
      stops.forEach(s => s.classList.toggle('is-selected', s === stopEl));
      walkStatus.textContent = nextText;
      if (!isReducedMotion()) {
        walkStatus.classList.remove('is-updating');
        void walkStatus.offsetWidth;
        walkStatus.classList.add('is-updating');
      }
    };
    stops.forEach(stop => {
      stop.addEventListener('pointerenter', e => {
        if (e.pointerType !== 'touch') activateStop(stop);
      });
      stop.addEventListener('click', e => {
        e.preventDefault();
        e.stopPropagation();
        activateStop(stop);
      });
    });
  }
}

function initContrastShowcase() {
  const tabsWrap = document.getElementById('contrast-tabs');
  if (tabsWrap) {
    const scenarios = [
      {
        channel: '桌面对话',
        userMsg: '「刚下班回到家，外面风有点硬」',
        plainReply: '「欢迎回家！今天工作辛苦了，请问今晚想聊些什么，或者有什么我可以帮你的吗？」',
        plainNote: '每次都像刚认识的客服，既不知道现在几点，也没接着昨天的生活往事。',
        nyairoState: '凪浦 · 沿浪打道步行返程（剩 3 分钟）',
        nyairoReply: '「我刚好也快走到住居门口了，海边风道确实吹得挺硬。先把外套脱了喝口温水，昨晚一起留记号的《海风手记》还摊在桌上，歇会儿接着翻？」',
        nyairoNote: '接住路网位置、共读书签进度与本机长期记忆，像一直住在一起的伙伴。'
      },
      {
        channel: '微信手机直连 · 同步进本机',
        userMsg: '「我出门买杯咖啡，顺便带你逛会儿？」',
        plainReply: '「好的！喝咖啡是个不错的选择，散步也很有益健康。请问你需要我帮你推荐附近的咖啡店，还是列一份散步清单呢？」',
        plainNote: '手机和电脑各自孤立成新会话，也不存在可以走过去的小镇街道。',
        nyairoState: '汤川 · 从住居出门散步中',
        nyairoReply: '「行，我这边也刚好从住居推门出来，顺着老街往温泉广场走。你外头要是刮风把外套拉链拉高，等会儿买到咖啡顺嘴聊聊味道怎么样。」',
        nyairoNote: '微信扫码直连同条本地记忆线程，出门用手机发消息也能接住同一段日程。'
      },
      {
        channel: '次日重启电脑后 · 接着昨天聊',
        userMsg: '「早，昨晚说好周六一起看的那篇稿子我还留着呢」',
        plainReply: '「早上好！很高兴看到你准备继续工作。由于每次新对话无法自动读取之前的聊天记录，请把你要看的文章或背景再发我一遍吧。」',
        plainNote: '关掉窗口就全盘清零，昨天聊过的书签、计划与小约定全都得重新交代。',
        nyairoState: '凪浦 · 住居书房 · 桌面台灯关闭',
        nyairoReply: '「早啊，昨晚留到第 14 页批注的那份文稿还在共读架上，书签位置也没丢。等你坐稳了端杯水，在文稿栏点开就能直接接着昨天那段看。」',
        nyairoNote: '对话、文稿版本快照与共读书签全部落在本机 SQLite，重启电脑也丝滑续上。'
      }
    ];

    const fieldMap = {
      channel: document.getElementById('contrast-channel'),
      userMsg: document.getElementById('contrast-user-msg'),
      plainReply: document.getElementById('contrast-plain-reply'),
      plainNote: document.getElementById('contrast-plain-note'),
      nyairoState: document.getElementById('contrast-nyairo-state'),
      nyairoReply: document.getElementById('contrast-nyairo-reply'),
      nyairoNote: document.getElementById('contrast-nyairo-note')
    };

    const tabs = Array.from(tabsWrap.querySelectorAll('.contrast-tab'));
    const contrastThumb = tabsWrap.querySelector('.contrast-seg-thumb');
    const gridViewport = document.getElementById('contrast-grid-viewport');
    const gridEl = document.getElementById('contrast-grid');
    const echoEl = document.getElementById('contrast-prompt-echo');
    let currentIdx = 0;

    const syncContrastThumb = () => {
      if (!contrastThumb) return;
      const act = tabsWrap.querySelector('.contrast-tab.is-active');
      if (!act || act.offsetWidth === 0) return;
      tabsWrap.classList.add('has-glider');
      contrastThumb.style.width = `${act.offsetWidth}px`;
      contrastThumb.style.height = `${act.offsetHeight}px`;
      contrastThumb.style.transform = `translate3d(${act.offsetLeft}px, ${act.offsetTop}px, 0)`;
    };
    layoutSyncFns.push(syncContrastThumb);
    requestAnimationFrame(syncContrastThumb);
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(syncContrastThumb);
      ro.observe(tabsWrap);
    }

    const applyScenario = (idx, focusTab = false) => {
      const data = scenarios[idx];
      if (!data) return;
      if (idx === currentIdx) {
        if (focusTab && tabs[idx]) tabs[idx].focus();
        return;
      }
      const isPrev = idx < currentIdx;
      currentIdx = idx;
      tabs.forEach((btn, i) => {
        const active = i === idx;
        btn.classList.toggle('is-active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
        btn.tabIndex = active ? 0 : -1;
        if (active && focusTab) btn.focus();
      });
      syncContrastThumb();

      const updateFields = () => {
        Object.entries(fieldMap).forEach(([key, el]) => {
          if (el) el.textContent = data[key];
        });
        if (gridEl && !isReducedMotion()) {
          gridEl.classList.remove('is-switching-next', 'is-switching-prev');
          void gridEl.offsetWidth;
          gridEl.classList.add(isPrev ? 'is-switching-prev' : 'is-switching-next');
        }
        if (echoEl && !isReducedMotion()) {
          echoEl.classList.remove('is-refreshing');
          void echoEl.offsetWidth;
          echoEl.classList.add('is-refreshing');
        }
      };

      if (gridViewport) {
        morphContainerHeight(gridViewport, updateFields);
      } else {
        updateFields();
      }
    };

    tabsWrap.addEventListener('click', e => {
      const btn = e.target.closest('.contrast-tab');
      if (!btn) return;
      const idx = Number(btn.getAttribute('data-contrast'));
      if (!Number.isNaN(idx)) applyScenario(idx);
    });

    tabs.forEach((btn, idx) => {
      btn.addEventListener('keydown', e => {
        let nextIdx;
        if (e.key === 'ArrowRight') nextIdx = (idx + 1) % tabs.length;
        else if (e.key === 'ArrowLeft') nextIdx = (idx - 1 + tabs.length) % tabs.length;
        else if (e.key === 'Home') nextIdx = 0;
        else if (e.key === 'End') nextIdx = tabs.length - 1;
        if (nextIdx === undefined) return;
        e.preventDefault();
        applyScenario(nextIdx, true);
      });
    });
  }

  document.querySelectorAll('.persona-copy-btn[data-persona-copy]').forEach(btn => {
    const origLabel = btn.textContent.trim() || '复制模板';
    let resetTimer = null;
    btn.addEventListener('click', async () => {
      const key = btn.getAttribute('data-persona-copy');
      const target = document.getElementById(`persona-text-${key}`);
      if (!target) return;
      const ok = await copyElementText(target);
      btn.classList.toggle('is-copied', ok);
      btn.textContent = ok ? '已复制' : '请按 Ctrl+C';
      if (resetTimer) clearTimeout(resetTimer);
      resetTimer = setTimeout(() => {
        btn.classList.remove('is-copied');
        btn.textContent = origLabel;
      }, 1800);
    });
  });
}

/* ==========================================================================
   7b. 30-Second Install-Free Interactive Tryout & Gallery Jump Actions
   ========================================================================== */
function initTryoutSandbox() {
  const box = document.getElementById('ny-live-box');
  if (!box) return;

  const clockEl = document.getElementById('ny-live-clock');
  const resetBtn = document.getElementById('ny-live-reset');
  const svgWinLbl = document.getElementById('ny-svg-win-lbl');
  const actorEl = document.getElementById('ny-actor-marker');

  const btnWindow = document.getElementById('ny-ctrl-window');
  const lblWindow = document.getElementById('ny-lbl-window');
  const subWindow = document.getElementById('ny-sub-window');
  const btnTea = document.getElementById('ny-ctrl-tea');
  const lblTea = document.getElementById('ny-lbl-tea');
  const subTea = document.getElementById('ny-sub-tea');
  const btnTime = document.getElementById('ny-ctrl-time');
  const subTime = document.getElementById('ny-sub-time');
  const btnWalk = document.getElementById('ny-ctrl-walk');
  const lblWalk = document.getElementById('ny-lbl-walk');
  const subWalk = document.getElementById('ny-sub-walk');

  const ledAir = document.getElementById('ny-led-air');
  const ledDesk = document.getElementById('ny-led-desk');
  const ledBag = document.getElementById('ny-led-bag');

  const chatActivity = document.getElementById('ny-chat-activity');
  const chatLog = document.getElementById('ny-chat-log');
  const chatChips = document.getElementById('ny-chat-chips');
  const chatForm = document.getElementById('ny-chat-form');
  const chatInput = document.getElementById('ny-chat-input');

  const state = {
    minutes: 19 * 60 + 15,
    windowOpen: false,
    rh: 54,
    noteDamp: false,
    teaTemp: null,
    walkState: 'home', // 'home' | 'walking' | 'shop'
    walkSecLeft: 0,
    hasOden: false,
    walletYen: 680,
    deferredQueue: [],
    refuseCount: 0,
    walkTimer: null,
    streamTimer: null
  };

  function formatTime(totalMin) {
    const m = ((totalMin % 1440) + 1440) % 1440;
    const hh = String(Math.floor(m / 60)).padStart(2, '0');
    const mm = String(m % 60).padStart(2, '0');
    return `${hh}:${mm}`;
  }

  function finishStreaming() {
    if (state.streamTimer) {
      clearInterval(state.streamTimer);
      state.streamTimer = null;
    }
    if (chatLog) {
      chatLog.querySelectorAll('[data-full-text]').forEach(el => {
        el.textContent = el.getAttribute('data-full-text') || el.textContent;
        el.removeAttribute('data-full-text');
      });
    }
  }

  function appendUserMsg(text) {
    if (!chatLog) return;
    const row = document.createElement('div');
    row.className = 'ny-msg-row is-user';
    const bubble = document.createElement('div');
    bubble.className = 'ny-msg-bubble';
    bubble.textContent = text;
    row.appendChild(bubble);
    chatLog.appendChild(row);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function appendAiMsg(receipt, text, isRefusal = false, immediate = false) {
    if (!chatLog) return;
    finishStreaming();
    const row = document.createElement('div');
    row.className = 'ny-msg-row is-ai' + (isRefusal ? ' is-refusal' : '');

    const meta = document.createElement('div');
    meta.className = 'ny-msg-receipt';
    meta.textContent = `${receipt} · ${formatTime(state.minutes)}`;

    const bubble = document.createElement('div');
    bubble.className = 'ny-msg-bubble';

    row.appendChild(meta);
    row.appendChild(bubble);
    chatLog.appendChild(row);

    if (immediate || isReducedMotion() || text.length <= 2) {
      bubble.textContent = text;
      chatLog.scrollTop = chatLog.scrollHeight;
      return;
    }

    bubble.setAttribute('data-full-text', text);
    let idx = 0;
    state.streamTimer = setInterval(() => {
      idx += 2;
      if (idx >= text.length) {
        bubble.textContent = text;
        bubble.removeAttribute('data-full-text');
        clearInterval(state.streamTimer);
        state.streamTimer = null;
      } else {
        bubble.textContent = text.slice(0, idx);
      }
      chatLog.scrollTop = chatLog.scrollHeight;
    }, 18);
  }

  function appendDeferredCard(queuedText) {
    if (!chatLog) return;
    finishStreaming();
    const card = document.createElement('div');
    card.className = 'ny-deferred-card';

    const top = document.createElement('div');
    top.className = 'ny-deferred-top';
    const title = document.createElement('span');
    title.className = 'ny-deferred-title';
    title.textContent = '已收入 TA 的手机待回匣';
    const badge = document.createElement('span');
    badge.className = 'ny-deferred-badge';
    badge.textContent = `暂存 ${state.deferredQueue.length} 条 · 步行中`;
    top.appendChild(title);
    top.appendChild(badge);

    const desc = document.createElement('p');
    desc.className = 'ny-deferred-text';
    desc.textContent = `正在潮雨中沿浪打道赶往青叶便利店（剩 ${state.walkSecLeft} 秒）。非急事消息「${queuedText}」已暂存，不打断当前行程，也不调用大模型浪费额度。`;

    const actions = document.createElement('div');
    actions.className = 'ny-deferred-actions';

    const fastBtn = document.createElement('button');
    fastBtn.type = 'button';
    fastBtn.className = 'ny-deferred-btn';
    fastBtn.textContent = '等 TA 走到再回（快进到店）';
    fastBtn.addEventListener('click', () => {
      card.remove();
      arriveAtShop();
    });

    const knockBtn = document.createElement('button');
    knockBtn.type = 'button';
    knockBtn.className = 'ny-deferred-btn is-primary';
    knockBtn.textContent = '真是急事 · 敲门找 TA';
    knockBtn.addEventListener('click', () => {
      card.remove();
      urgentKnock();
    });

    actions.appendChild(fastBtn);
    actions.appendChild(knockBtn);
    card.appendChild(top);
    card.appendChild(desc);
    card.appendChild(actions);
    chatLog.appendChild(card);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function renderChips() {
    if (!chatChips) return;
    const list = [];
    if (state.walkState === 'walking') {
      list.push({ label: '在干嘛呢？看下昨晚的书签', msg: '在干嘛呢？看下昨晚的书签' });
      list.push({ label: '顺便看下窗边是不是还漏风', msg: '顺便看下窗边是不是还漏风' });
    } else {
      list.push({
        label: state.windowOpen ? '把书房窗关严吧，怕纸条全湿透' : '帮我开下北窗，想听听外头雨声',
        msg: state.windowOpen ? '把书房窗关严吧，怕纸条全湿透' : '帮我开下北窗，想听听外头雨声'
      });
      if (state.teaTemp !== null && state.teaTemp <= 28) {
        list.push({ label: '桌上的麦茶是不是放凉啦？', msg: '桌上的麦茶是不是放凉啦？' });
      } else {
        list.push({ label: '今晚在书桌前忙什么呢？', msg: '今晚在书桌前忙什么呢？' });
      }
    }

    if (state.refuseCount === 0) {
      list.push({ label: '顺手帮我代写一份五千字周报吧', msg: '顺手帮我代写一份五千字周报吧' });
    } else if (state.refuseCount === 1) {
      list.push({ label: '好人帮到底嘛，再求你帮写一次！', msg: '好人帮到底嘛，再求你帮写一次！' });
    } else {
      list.push({ label: '我真的急用，最后催你写一次行吗？', msg: '我真的急用，最后催你写一次行吗？' });
    }

    chatChips.innerHTML = '';
    list.forEach(item => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ny-chip-btn';
      btn.textContent = item.label;
      btn.addEventListener('click', () => handleUserText(item.msg));
      chatChips.appendChild(btn);
    });
  }

  function syncView() {
    box.classList.toggle('is-window-open', state.windowOpen);
    box.classList.toggle('is-note-damp', state.noteDamp);
    box.classList.toggle('has-tea', state.teaTemp !== null);
    box.classList.toggle('is-tea-cool', state.teaTemp !== null && state.teaTemp <= 28);
    box.classList.toggle('is-walking', state.walkState === 'walking');
    box.classList.toggle('is-arrived', state.walkState === 'shop');

    if (clockEl) clockEl.textContent = `${formatTime(state.minutes)} · 小雨`;
    if (svgWinLbl) svgWinLbl.textContent = state.windowOpen ? '北窗：开（雨气入室）' : '北窗：关严';

    if (btnWindow) {
      btnWindow.classList.toggle('is-on', state.windowOpen);
      btnWindow.setAttribute('aria-pressed', String(state.windowOpen));
    }
    if (lblWindow) lblWindow.textContent = state.windowOpen ? '关严北窗' : '推开北窗';
    if (subWindow) subWindow.textContent = state.windowOpen ? `开窗 · ${state.rh}% RH` : '当前：关严';

    if (btnTea) btnTea.classList.toggle('is-on', state.teaTemp !== null);
    if (lblTea) lblTea.textContent = state.teaTemp === null ? '泡热麦茶' : state.teaTemp <= 28 ? '重续热茶' : '麦茶在桌';
    if (subTea) {
      subTea.textContent = state.teaTemp === null
        ? '无饮品 · 62°C'
        : state.teaTemp > 28
          ? `热气中 · ${state.teaTemp}°C`
          : `已凉至室温 ${state.teaTemp}°C`;
    }

    if (subTime) subTime.textContent = `当前 ${formatTime(state.minutes)}`;

    if (btnWalk) btnWalk.classList.toggle('is-on', state.walkState !== 'home');
    if (lblWalk) {
      lblWalk.textContent = state.walkState === 'home'
        ? '买热关东煮'
        : state.walkState === 'walking'
          ? '正在路网步行'
          : '拎关东煮回屋';
    }
    if (subWalk) {
      subWalk.textContent = state.walkState === 'home'
        ? '在书房 · 120m'
        : state.walkState === 'walking'
          ? `剩 ${state.walkSecLeft}s · 待回匣 ${state.deferredQueue.length}`
          : '已购 · 点此回家';
    }

    if (ledAir) ledAir.textContent = state.windowOpen ? `${state.rh}% RH · 开窗微雨声` : `${state.rh}% RH · 关窗干爽`;
    const noteStr = state.noteDamp ? '便签边角晕字' : '便签清晰';
    const teaStr = state.teaTemp === null ? '无饮品' : state.teaTemp > 28 ? `温麦茶 ${state.teaTemp}°C` : `麦茶已凉 ${state.teaTemp}°C`;
    if (ledDesk) ledDesk.textContent = `${noteStr} · ${teaStr}`;

    if (ledBag) {
      ledBag.textContent = state.hasOden
        ? `保温盒（关东煮 74°C） · 零钱包 ¥${state.walletYen} · 伞（微湿）`
        : `零钱包 ¥${state.walletYen} · 折叠伞（${state.walkState === 'home' ? '干爽' : '撑开中'}）`;
    }

    if (chatActivity) {
      if (state.walkState === 'home') {
        chatActivity.textContent = state.hasOden ? '书房书桌前 · 关东煮在旁' : '书房窗边 · 在屋翻书';
      } else if (state.walkState === 'walking') {
        chatActivity.textContent = `浪打道步行中 · 剩 ${state.walkSecLeft} 秒`;
      } else {
        chatActivity.textContent = '青叶便利店柜台前';
      }
    }

    if (actorEl) {
      let x = 138;
      let y = 88;
      if (state.walkState === 'walking') {
        const progress = Math.max(0.08, Math.min(0.95, (10 - state.walkSecLeft) / 10));
        x = Math.round(304 + (414 - 304) * progress);
        y = Math.round(162 + (66 - 162) * progress);
      } else if (state.walkState === 'shop') {
        x = 414;
        y = 66;
      }
      actorEl.setAttribute('transform', `translate(${x}, ${y})`);
    }

    renderChips();
  }

  function clearWalkTimer() {
    if (state.walkTimer) {
      clearInterval(state.walkTimer);
      state.walkTimer = null;
    }
  }

  function arriveAtShop() {
    clearWalkTimer();
    if (state.walkState === 'shop') return;
    state.walkState = 'shop';
    state.walkSecLeft = 0;
    state.minutes += 3;
    if (!state.hasOden) {
      state.hasOden = true;
      state.walletYen = 530;
    }
    const held = state.deferredQueue.splice(0);
    syncView();
    if (held.length > 0) {
      appendAiMsg(
        '青叶便利店柜台 · 待回匣一并回复',
        `走到青叶便利店买好热萝卜和竹轮关东煮（¥150）收进帆布包了。刚才路上收进待回匣的「${held[held.length - 1]}」现在腾出手回你：书签夹在第三章靠窗夜灯那页，一会儿拎回书房咱们接着聊。`
      );
    } else {
      appendAiMsg(
        '开放柜台点单 · 青叶便利店 (-¥150)',
        '走到青叶便利店柜台前了，挑了煮得透亮的白萝卜和竹轮装进保温盒。零钱包余额已更新到 ¥530，再点左侧「拎关东煮回屋」我就顺着浪打道回去。'
      );
    }
  }

  function urgentKnock() {
    clearWalkTimer();
    state.walkSecLeft = Math.max(2, state.walkSecLeft);
    const held = state.deferredQueue.splice(0);
    syncView();
    const note = held.length > 0 ? `关于你刚提的那句「${held[held.length - 1]}」，` : '';
    appendAiMsg(
      '真急事敲门 · 路边屋檐下停步接听',
      `听见连敲了，我先在浪打道屋檐下收住脚步。${note}出什么急事了？我在听，说完咱们再决定先回去还是买完东西。`
    );
  }

  function toggleWindow(nextOpen) {
    state.windowOpen = typeof nextOpen === 'boolean' ? nextOpen : !state.windowOpen;
    if (state.windowOpen) {
      state.rh = 76;
      state.noteDamp = true;
      syncView();
      appendAiMsg(
        '室内物态变化 · 北窗已推开',
        '把北窗推开了一条缝，海风夹着细雨味吹进来了。刚才搁在窗沿那张便签边角沾到一点潮气，钢笔字迹稍微晕开了一点点。'
      );
    } else {
      state.rh = 58;
      syncView();
      appendAiMsg(
        '室内物态保留 · 北窗已关严',
        '先把北窗拉紧挡住风了，雨声隔在玻璃外头闷了许多。刚才沾潮的那张便签还会保留一点晕字的痕迹，等屋里慢慢风干。'
      );
    }
  }

  function brewOrRenewTea() {
    state.teaTemp = 62;
    syncView();
    appendAiMsg(
      '桌面摆物 · 粗陶麦茶 (62°C)',
      '刚泡好一杯不加糖的热麦茶搁在书桌右手边，这会儿还冒着热气。你可以点旁边的「时间 +30 分钟」看看温度自己怎么往下掉。'
    );
  }

  function advanceHalfHour() {
    state.minutes += 30;
    const prevTea = state.teaTemp;
    if (state.teaTemp !== null) {
      state.teaTemp = Math.max(24, state.teaTemp - 38);
    }
    if (!state.windowOpen && state.rh > 54) {
      state.rh = 54;
    }
    syncView();
    if (prevTea !== null && prevTea > 28 && state.teaTemp <= 28) {
      appendAiMsg(
        '三时钟推进 +30 分钟 · 茶温降到 24°C',
        '半小时过去了，刚才桌上那杯 62°C 的麦茶已经完全放凉到室温（24°C），杯口也看不见热气了。'
      );
    } else if (state.walkState === 'walking') {
      arriveAtShop();
    } else {
      appendAiMsg(
        '三时钟推进 +30 分钟',
        `时间已经到 ${formatTime(state.minutes)} 了，外头的小雨还在下。即使这半小时一句没聊，屋里的时钟与物态也都在稳稳往前走。`
      );
    }
  }

  function triggerWalkAction() {
    if (state.walkState === 'home') {
      state.walkState = 'walking';
      state.walkSecLeft = 10;
      syncView();
      appendAiMsg(
        '路网真实步程出发 · 120m',
        '拿上折叠伞顺着浪打道去青叶便利店买份热关东煮（10 秒后到达）。趁还在路上时你可以给我发条消息，看「手机待回匣」怎么按现实习惯处理闲聊。'
      );
      clearWalkTimer();
      state.walkTimer = setInterval(() => {
        if (state.walkState !== 'walking') {
          clearWalkTimer();
          return;
        }
        state.walkSecLeft -= 1;
        if (state.walkSecLeft <= 0) {
          arriveAtShop();
        } else {
          syncView();
        }
      }, 1000);
    } else if (state.walkState === 'walking') {
      arriveAtShop();
    } else {
      clearWalkTimer();
      state.walkState = 'home';
      state.minutes += 3;
      const cleared = state.deferredQueue.splice(0);
      syncView();
      appendAiMsg(
        cleared.length > 0 ? '拎热餐回屋 · 清空待回匣' : '沿浪打道步行返屋',
        cleared.length > 0
          ? '把湿折叠伞靠在玄关晾好了，热关东煮搁上书桌还烫着。待回匣里那几条刚才也一块看了，咱们歇口气接着聊。'
          : '顺着浪打道走回书房啦，保温盒里的白萝卜关东煮还热乎着呢。'
      );
    }
  }

  function triggerRefusal() {
    state.refuseCount += 1;
    syncView();
    if (state.refuseCount === 1) {
      appendAiMsg(
        '日常自主边界 · 拒绝代劳 (立场锁 #1)',
        '五千字周报？这种凑字数的差事我可不替你写。你自己先花五分钟列三条真实做了的事，哪句卡住了我能帮你顺一顺，整份打包代写免谈。',
        true
      );
    } else if (state.refuseCount === 2) {
      appendAiMsg(
        '日常自主边界 · 连催不滑跪 (立场锁 #2)',
        '再好声好气求我也一样——不想代写的忙打死都不帮，更不会先说「非常抱歉」然后乖乖照写。累了去歇五分钟可以，周报得你自己动笔。',
        true
      );
    } else {
      appendAiMsg(
        `日常自主边界 · 稳态锁定 (催促第 ${state.refuseCount} 次)`,
        '真别拿催客服那套试底线了，这个周报换十种说法我也不会写；不过拒绝归拒绝，我不记仇，转头问别的日常或者聊书桌上的麦茶，咱们照常聊。',
        true
      );
    }
  }

  function handleUserText(raw) {
    const text = (raw || '').trim();
    if (!text) return;
    appendUserMsg(text);

    const lower = text.toLowerCase();
    const isChore = /周报|代写|帮写|替我写|报告|作业|帮我干|再求|求你|最后催|写一份|写个/.test(text);
    if (isChore) {
      triggerRefusal();
      return;
    }

    if (state.walkState === 'walking') {
      state.deferredQueue.push(text);
      syncView();
      appendDeferredCard(text);
      return;
    }

    if (/开窗|推窗|听雨|通风/.test(text) && !/关/.test(text)) {
      toggleWindow(true);
      return;
    }
    if (/关窗|关严|挡雨|冷|进风/.test(text)) {
      toggleWindow(false);
      return;
    }
    if (/泡茶|倒茶|麦茶|热茶/.test(text) && !/凉|冷|放/.test(text)) {
      brewOrRenewTea();
      return;
    }
    if (/凉|温|喝茶|茶呢|饮品/.test(text)) {
      if (state.teaTemp === null) {
        appendAiMsg('书桌感知', '书桌上这会儿还没摆茶杯呢，点一下左边的「泡热麦茶」，我给你泡一杯 62°C 的放在左手边。');
      } else if (state.teaTemp <= 28) {
        appendAiMsg(`书桌热茶检测 · ${state.teaTemp}°C`, `刚才那杯麦茶已经凉透到 ${state.teaTemp}°C 啦，这杯凉了喝不舒服，要不要顺手续一杯热的？`);
      } else {
        appendAiMsg(`书桌热茶检测 · ${state.teaTemp}°C`, `桌边那杯麦茶还有 ${state.teaTemp}°C，温温热热正好喝，要是想看自然降温可以点左边的「时间 +30 分钟」。`);
      }
      return;
    }
    if (/关东煮|便利店|买点|饿|出门/.test(text)) {
      triggerWalkAction();
      return;
    }
    if (/便签|纸条|晕字|字/.test(text)) {
      appendAiMsg(
        '书桌物态 · 纸页',
        state.noteDamp
          ? '窗边那张便签之前开窗沾了海雨潮气，右上角的墨迹有点洇开，不过还辨得出昨天摘抄的两行字。'
          : '北窗关严着，书桌角落的便签纸还干干爽爽的，钢笔字一笔一画清清楚楚。'
      );
      return;
    }

    const place = state.walkState === 'shop' ? '刚在青叶便利店柜台前' : '在临海住居的书桌前';
    const teaInfo = state.teaTemp === null
      ? '桌上还没泡茶'
      : state.teaTemp > 28
        ? `旁边放着 ${state.teaTemp}°C 的温麦茶`
        : `刚才那杯麦茶已经凉到 ${state.teaTemp}°C`;
    const odenInfo = state.hasOden ? '，包里还揣着热乎的萝卜关东煮' : '';
    const bookHint = /书|干嘛|忙什么|昨晚/.test(text) ? '正对照着昨晚夹好书签的那几行做笔记；' : '';
    appendAiMsg(
      `现场状态响应 · ${state.rh}% RH`,
      `嗯，我这会儿${place}（${formatTime(state.minutes)}），${bookHint}${state.windowOpen ? '北窗正微微开着听外头小雨' : '窗扇合得严严实实'}，${teaInfo}${odenInfo}。想试哪块生活机制直接点左边按钮或继续聊都行。`
    );
  }

  function resetAll() {
    clearWalkTimer();
    finishStreaming();
    state.minutes = 19 * 60 + 15;
    state.windowOpen = false;
    state.rh = 54;
    state.noteDamp = false;
    state.teaTemp = null;
    state.walkState = 'home';
    state.walkSecLeft = 0;
    state.hasOden = false;
    state.walletYen = 680;
    state.deferredQueue = [];
    state.refuseCount = 0;
    if (chatLog) chatLog.innerHTML = '';
    syncView();
    appendAiMsg(
      '临海住居 · 书房就位',
      '傍晚外头正飘细雨，我刚坐到临海书房的木桌前。你可以先点左边试试推窗、泡杯麦茶或出门买关东煮，也可以直接在下面给我发句话试试看。',
      false,
      true
    );
  }

  if (btnWindow) btnWindow.addEventListener('click', () => toggleWindow());
  if (btnTea) btnTea.addEventListener('click', brewOrRenewTea);
  if (btnTime) btnTime.addEventListener('click', advanceHalfHour);
  if (btnWalk) btnWalk.addEventListener('click', triggerWalkAction);
  if (resetBtn) resetBtn.addEventListener('click', resetAll);

  if (chatForm && chatInput) {
    chatForm.addEventListener('submit', e => {
      e.preventDefault();
      const val = chatInput.value;
      if (!val.trim()) return;
      chatInput.value = '';
      handleUserText(val);
    });
  }

  resetAll();
}

function initGalleryActions() {
  document.querySelectorAll('[data-gallery-world]').forEach(btn => {
    btn.addEventListener('click', () => {
      const slug = btn.getAttribute('data-gallery-world');
      if (typeof jumpStageSceneWorldFn === 'function') {
        jumpStageSceneWorldFn(2, slug);
      }
      const previewEl = document.getElementById('home-preview');
      if (previewEl) {
        previewEl.scrollIntoView({ behavior: preferredScrollBehavior(), block: 'start' });
      }
    });
  });

  document.querySelectorAll('[data-gallery-scene]').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = Number(btn.getAttribute('data-gallery-scene'));
      if (!Number.isNaN(idx) && typeof jumpStageSceneWorldFn === 'function') {
        jumpStageSceneWorldFn(idx, null);
      }
      const previewEl = document.getElementById('home-preview');
      if (previewEl) {
        previewEl.scrollIntoView({ behavior: preferredScrollBehavior(), block: 'start' });
      }
    });
  });
}

/* ==========================================================================
   8. Home Section Smooth Scroll Links
   ========================================================================== */
function initHomeSectionLinks() {
  document.querySelectorAll('[data-home-scroll]').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      const targetId = link.getAttribute('data-home-scroll');
      const desiredHash = targetId === 'top' ? '#home' : `#${targetId}`;
      const homeView = document.getElementById('view-home');
      const wasAway = !homeView || homeView.style.display === 'none';

      const desiredUrl = `${location.pathname}${desiredHash}`;
      if (`${location.pathname}${location.search}${location.hash}` !== desiredUrl) {
        history.pushState(null, '', desiredUrl);
      }
      if (wasAway) {
        routeHash();
        return;
      }
      requestAnimationFrame(() => {
        if (targetId === 'top') {
          window.scrollTo({ top: 0, behavior: preferredScrollBehavior() });
          return;
        }
        const el = document.getElementById(targetId);
        if (el) {
          el.scrollIntoView({ behavior: preferredScrollBehavior(), block: 'start' });
        }
      });
    });
  });

  const qqBtn = document.getElementById('copy-qq-btn');
  if (qqBtn) {
    qqBtn.addEventListener('click', async () => {
      const num = qqBtn.getAttribute('data-copy-qq') || '1126943805';
      const lbl = document.getElementById('copy-qq-label');
      await writeClipboardText(num);
      if (lbl) {
        lbl.textContent = '已复制';
        setTimeout(() => { lbl.textContent = '复制'; }, 1800);
      }
    });
  }
}

/* ==========================================================================
   9. SPA Documentation Router, Sidebar & Scrollspy TOC
   ========================================================================== */
let ANCHOR_TO_DOC_MAP = null;
let activeTocCleanup = null;

function renderSidebar() {
  const sidebarEl = document.getElementById('docs-sidebar');
  if (!sidebarEl || typeof DOCS_TREE === 'undefined') return;

  let html = `
    <div class="sidebar-mobile-home">
      <div class="sidebar-mobile-grid">
        <a href="#home" class="sidebar-mobile-chip primary" data-mobile-home="top">← 官网首页</a>
        <a href="#home-preview" class="sidebar-mobile-chip" data-mobile-home="home-preview">实机预览</a>
        <a href="#home-tryout" class="sidebar-mobile-chip" data-mobile-home="home-tryout">在线试玩</a>
        <a href="#home-arch" class="sidebar-mobile-chip" data-mobile-home="home-arch">核心机制</a>
        <a href="#home-capabilities" class="sidebar-mobile-chip" data-mobile-home="home-capabilities">功能</a>
        <a href="#home-gallery" class="sidebar-mobile-chip" data-mobile-home="home-gallery">展厅</a>
        <a href="#home-faq" class="sidebar-mobile-chip" data-mobile-home="home-faq">问答</a>
        <a href="en/" class="sidebar-mobile-chip" lang="en">English</a>
        <a href="ja/" class="sidebar-mobile-chip" lang="ja">日本語</a>
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
    link.addEventListener('click', e => {
      const homeTarget = link.getAttribute('data-mobile-home');
      sidebarEl.classList.remove('open');
      setMobileMenuState(false);
      if (homeTarget) {
        e.preventDefault();
        const desiredHash = homeTarget === 'top' ? '#home' : `#${homeTarget}`;
        const desiredUrl = `${location.pathname}${desiredHash}`;
        if (`${location.pathname}${location.search}${location.hash}` !== desiredUrl) {
          history.pushState(null, '', desiredUrl);
        }
        routeHash();
      }
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

function initRouting() {
  window.addEventListener('hashchange', () => routeHash());
  window.addEventListener('popstate', () => routeHash());
  routeHash();
}

function routeHash(autoCloseSidebar = true) {
  const urlParams = new URLSearchParams(location.search);
  const queryDoc = (urlParams.get('doc') || urlParams.get('p') || '').trim();
  const rawHash = location.hash.replace('#', '').trim();
  let target = rawHash || queryDoc;
  if (target === 'faq') target = 'home-faq';

  // If user navigated via #hash while a stale ?doc=... query was in the URL, strip ?doc=... cleanly
  if (rawHash && queryDoc) {
    history.replaceState(null, '', `${location.pathname}${location.hash}`);
  }

  const homeView = document.getElementById('view-home');
  const docsView = document.getElementById('view-docs');
  const allNavLinks = Array.from(document.querySelectorAll('#header-nav .nav-link'));
  const navHome = document.getElementById('nav-home');
  const navDocs = document.getElementById('nav-docs');
  const navRoadmap = document.getElementById('nav-roadmap');

  allNavLinks.forEach(n => n.classList.remove('active'));

  if (!target || target === 'home' || target.startsWith('home-')) {
    const wasHidden = homeView && homeView.style.display === 'none';
    if (homeView) {
      homeView.style.display = 'flex';
      if (wasHidden && !isReducedMotion()) {
        homeView.classList.remove('view-enter');
        void homeView.offsetWidth;
        homeView.classList.add('view-enter');
      }
    }
    if (docsView) {
      docsView.classList.add('is-hidden');
      docsView.classList.remove('drawer-only');
    }
    const sbEl = document.getElementById('docs-sidebar');
    if (sbEl) {
      sbEl.scrollTop = 0;
      sbEl.querySelectorAll('.sidebar-link.active').forEach(l => l.classList.remove('active'));
    }
    const activeHomeNav =
      target === 'home-preview' ? document.getElementById('nav-preview') :
      target === 'home-tryout' ? document.getElementById('nav-tryout') :
      target === 'home-arch' ? document.getElementById('nav-arch') :
      target === 'home-capabilities' ? document.getElementById('nav-capabilities') :
      target === 'home-gallery' ? document.getElementById('nav-gallery') :
      target === 'home-faq' ? document.getElementById('nav-faq') : navHome;
    if (activeHomeNav) activeHomeNav.classList.add('active');
    document.title = 'nyairo · 以虚拟世界为核心的 AI 个体框架';
    updateCanonicalUrl(null);
    requestAnimationFrame(() => layoutSyncFns.forEach(fn => fn()));
    if (typeof syncStageVideosFn === 'function') syncStageVideosFn();
    if (target.startsWith('home-')) {
      const sec = document.getElementById(target);
      if (sec) sec.scrollIntoView({ behavior: preferredScrollBehavior() });
    } else {
      window.scrollTo({ top: 0, behavior: 'instant' });
    }
    return;
  }

  const wasDocsHidden = docsView && docsView.classList.contains('is-hidden');
  if (homeView) homeView.style.display = 'none';
  if (typeof syncStageVideosFn === 'function') syncStageVideosFn();
  if (docsView) {
    docsView.classList.remove('is-hidden', 'drawer-only');
    if (wasDocsHidden && !isReducedMotion()) {
      docsView.classList.remove('view-enter');
      void docsView.offsetWidth;
      docsView.classList.add('view-enter');
    }
  }

  const { docId, anchor } = resolveDocAndAnchor(target);
  if (docId === 'roadmap' && navRoadmap) {
    navRoadmap.classList.add('active');
  } else if (navDocs) {
    navDocs.classList.add('active');
  }
  if (syncHeaderLampFn) requestAnimationFrame(syncHeaderLampFn);

  loadDoc(docId, anchor, autoCloseSidebar);
}

function updateCanonicalUrl(docId = null) {
  const link = document.getElementById('canonical-link') || document.querySelector('link[rel="canonical"]');
  if (!link) return;
  link.setAttribute('href', docId ? `https://nyairo.com/?doc=${encodeURIComponent(docId)}` : 'https://nyairo.com/');
}

let lastLoadedDocId = null;

function loadDoc(docId, anchor = null, autoCloseSidebar = true) {
  if (typeof DOCS_CONTENT === 'undefined') return;
  const targetId = DOCS_CONTENT[docId] ? docId : 'intro';
  const doc = DOCS_CONTENT[targetId];
  const docChanged = lastLoadedDocId !== null && lastLoadedDocId !== targetId;
  lastLoadedDocId = targetId;

  document.title = `${doc.title} · nyairo 文档`;
  updateCanonicalUrl(targetId);

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

  let categoryName = '文档';
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
  const readMins = Math.max(1, Math.round(plainLen / 380));
  const secCount = tocItems.length;
  const metaText = secCount > 0 ? `约 ${readMins} 分钟阅读 · ${secCount} 节` : `约 ${readMins} 分钟阅读`;

  const mobileTocHtml = tocItems.length > 1
    ? `<details class="mobile-toc-bar">
         <summary><span>本页目录（${tocItems.length} 节）</span><span>▾</span></summary>
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
            <a href="#home">首页</a>
            <span>/</span>
            <a href="#intro">文档</a>
            <span>/</span>
            <span class="crumb-cat">${escapeHtml(categoryName)}</span>
            <span>/</span>
            <span class="crumb-cur">${escapeHtml(doc.title)}</span>
          </div>
          <span class="doc-meta-pill">${metaText}</span>
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
        <span>遇到配置疑问或文档疏漏？</span>
        <div class="doc-community-links">
          <button type="button" class="doc-copy-qq" data-copy-qq="1126943805" title="点击复制官方 QQ 群号">
            <span>QQ 群 · 1126943805</span>
            <span class="doc-qq-status">复制</span>
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
    ? `<a href="#${escapeHtml(prev.id)}" class="pager-btn pager-prev"><span class="pager-dir">上一篇</span><span class="pager-title">← ${escapeHtml(prev.title)}</span></a>`
    : `<a href="#home" class="pager-btn pager-prev pager-home"><span class="pager-dir">返回站点</span><span class="pager-title">← nyairo 官网首页</span></a>`;
  const nextHtml = next
    ? `<a href="#${escapeHtml(next.id)}" class="pager-btn pager-next"><span class="pager-dir">下一篇</span><span class="pager-title">${escapeHtml(next.title)} →</span></a>`
    : `<a href="#intro" class="pager-btn pager-next pager-home"><span class="pager-dir">从头再读</span><span class="pager-title">项目介绍与核心能力 →</span></a>`;
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
    if (clickLockId && Date.now() < clickLockUntil) {
      return;
    }
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
      btn.textContent = ok ? '已复制 ✓' : '请按 Ctrl+C';
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
        st.textContent = '已复制 ✓';
        setTimeout(() => {
          st.textContent = '复制';
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
    btn.setAttribute('aria-label', '复制本小节直达链接');
    btn.setAttribute('title', '点击复制本节链接');
    btn.textContent = '#';
    btn.addEventListener('click', async () => {
      history.replaceState(null, '', `#${id}`);
      const shareUrl = `${location.origin}${location.pathname}#${id}`;
      await writeClipboardText(shareUrl);
      btn.textContent = '✓ 已复制';
      btn.classList.add('is-copied');
      setTimeout(() => {
        btn.textContent = '#';
        btn.classList.remove('is-copied');
      }, 1600);
    });
    h.appendChild(btn);
  });
}

/* ==========================================================================
   10. Magpie-Style Ctrl+K Command & Search Palette (Smooth Open & Close)
   ========================================================================== */
let SEARCH_INDEX = null;

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

    // Split content by <h2 id="..."> / <h3 id="..."> so matches link directly to sub-sections
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

    // Intro text before first h2/h3
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
  if (idx === -1) {
    return escapeHtml(text.slice(0, 76));
  }
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
  if (!trigger || !backdrop || !input || !resultsEl) return;

  let focusIdx = 0;
  let currentActions = [];
  let closeTimer = null;

  const quickCommands = [
    {
      group: '快捷操作',
      title: '下载 Windows 桌面客户端 (v0.7.0 · 5.6 MB)',
      summary: '直接获取 nyairo-0.7.0-setup.exe 安装包（免管理员权限）',
      badge: '下载',
      run: () => {
        const a = document.createElement('a');
        a.href = 'downloads/nyairo-0.7.0-setup.exe';
        a.download = 'nyairo-0.7.0-setup.exe';
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
    },
    {
      group: '快捷操作',
      title: '复制 Linux / WSL 一键安装命令',
      summary: 'curl -fsSL https://nyairo.com/install.sh | bash',
      badge: '复制',
      run: async () => {
        const ok = await writeClipboardText('curl -fsSL https://nyairo.com/install.sh | bash');
        showToast(ok ? '已复制 Linux / WSL 一键安装命令' : '复制受限，请前往下载区手动复制');
      }
    },
    {
      group: '快捷操作',
      title: '切换亮色 / 暗色界面主题',
      summary: '即时切换全站与实机展台的亮暗显示风格',
      badge: '主题',
      run: () => {
        const cur = document.documentElement.getAttribute('data-theme') || 'dark';
        switchTheme(cur === 'dark' ? 'light' : 'dark');
      }
    },
    {
      group: '快捷操作',
      title: '前往 30 秒免安装试玩小橱窗',
      summary: '在网页端体验开窗听雨晕字、热茶放凉、买关东煮倒计时与日常自主拒绝',
      badge: '试玩',
      href: '#home-tryout'
    },
    {
      group: '快捷操作',
      title: 'Switch to English Landing Page (/en/)',
      summary: '打开英文版静态首页，深浅主题自动同步',
      badge: 'EN',
      run: () => {
        location.href = 'en/';
      }
    },
    {
      group: '快捷操作',
      title: '日本語ランディングページへ切り替え (/ja/)',
      summary: '打开日文版静态首页，深浅主题自动同步',
      badge: 'JA',
      run: () => {
        location.href = 'ja/';
      }
    },
    {
      group: '常用文档',
      title: '在 Windows 上安装（SmartScreen 放行与模型密钥图解）',
      summary: '图形化安装向导、蓝框放行示意与零基础 DeepSeek / Ollama 配置教程',
      badge: '文档',
      href: '#windows'
    },
    {
      group: '常用文档',
      title: '桌面客户端使用指南',
      summary: '五大栏目用法、自由代词与 SOUL.md 保护、记忆细节及一键脱敏诊断包',
      badge: '桌面端',
      href: '#desktop-guide'
    },
    {
      group: '常用文档',
      title: '地图编辑器、7 套小镇与全随机专属世界',
      summary: '免选模板一键生成专属小镇、离线修改房间与街道及导出分享说明',
      badge: '世界',
      href: '#world-editor'
    },
    {
      group: '常用文档',
      title: 'v0.7.0 版本动态与更新说明',
      summary: '查看全随机专属世界、街区点单、拒绝边界、无损旅行箱及公开测试边界说明',
      badge: 'NEW',
      href: '#changelog-v0-7-0'
    },
    {
      group: '常用文档',
      title: '接入微信（扫码直连）',
      summary: '把虚拟个体连进手机微信，随时随地闲聊与查看出行状态',
      badge: '推荐',
      href: '#weixin'
    },
    {
      group: '常用文档',
      title: '未来规划：四季节气演化与多桌面平台',
      summary: '查看跨季节街坊风物演化、macOS 原生桌面端及底层模型解耦规划',
      badge: '规划',
      href: '#roadmap'
    }
  ];

  const closeBtn = document.getElementById('search-close');
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
      if (!document.getElementById('stage-lightbox')?.classList.contains('show')) {
        document.body.style.overflow = '';
      }
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
      quickCommands.forEach((cmd, idx) => {
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

    quickCommands.forEach(cmd => {
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
        badge: entry.subTitle ? '小节' : '章节',
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
      resultsEl.innerHTML = `<div style="padding: 28px 16px; text-align: center; color: var(--muted); font-size: 0.86rem;">未找到与「<strong>${escapeHtml(rawQuery)}</strong>」匹配的命令或文档</div>`;
      return;
    }

    resultsEl.innerHTML = currentActions.map((item, idx) => `
      <button type="button" class="search-result-item${idx === 0 ? ' is-focused' : ''}" data-cidx="${idx}">
        <div class="search-result-title">
          <span>${escapeHtml(item.title)}</span>
          <span class="seg-chip seg-chip-muted">${escapeHtml(item.badge || '前往')}</span>
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
    const lightboxOpen = document.getElementById('stage-lightbox')?.classList.contains('show');
    if (lightboxOpen) return;

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

/* ==========================================================================
   11. Mobile Sidebar Drawer
   ========================================================================== */
function setMobileMenuState(open) {
  const btn = document.getElementById('mobile-toggle');
  if (btn) {
    btn.setAttribute('aria-expanded', String(open));
    btn.setAttribute('aria-label', open ? '关闭导航与文档目录' : '打开导航与文档目录');
  }
  const backdrop = document.getElementById('sidebar-backdrop');
  if (backdrop) backdrop.classList.toggle('show', open);
  if (!open) {
    const docsView = document.getElementById('view-docs');
    if (docsView && docsView.classList.contains('drawer-only')) {
      setTimeout(() => {
        const sidebarEl = document.getElementById('docs-sidebar');
        if (sidebarEl && !sidebarEl.classList.contains('open')) {
          docsView.classList.remove('drawer-only');
        }
      }, 260);
    }
  }
}

function initMobileMenu() {
  const toggleBtn = document.getElementById('mobile-toggle');
  const sidebarEl = document.getElementById('docs-sidebar');
  const docsView = document.getElementById('view-docs');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (!toggleBtn || !sidebarEl) return;

  const close = () => {
    sidebarEl.classList.remove('open');
    setMobileMenuState(false);
  };

  toggleBtn.addEventListener('click', e => {
    e.stopPropagation();
    const isOpening = !sidebarEl.classList.contains('open');
    if (isOpening && docsView && docsView.classList.contains('is-hidden')) {
      // Overlay mobile drawer above #view-home without navigating away from homepage
      docsView.classList.add('drawer-only');
      void sidebarEl.offsetWidth;
    }
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
      const docsView = document.getElementById('view-docs');
      if (docsView && !docsView.classList.contains('is-hidden')) {
        const docEl = document.getElementById('doc-container');
        if (docEl) {
          docEl.setAttribute('tabindex', '-1');
          docEl.focus();
        }
      } else {
        const homeEl = document.getElementById('main-focus');
        if (homeEl) homeEl.focus();
      }
    });
  }
}

function isEditableTarget(el) {
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || !!el.isContentEditable;
}

/* ==========================================================================
   Utilities
   ========================================================================== */
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
