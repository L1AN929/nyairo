/**
 * nyairo Official Website Controller (v0.6.0 — Enhanced Motion Edition)
 * - Circular clip-path View Transition theme switcher (Dark <-> Light)
 * - Dual-layer Header Nav physics (hover magnetic pill + scrollspy spring lamp + scroll progress)
 * - Smooth container height morphing & directional slide transitions
 * - 4-Scene Real Desktop Stage with auto-play progress bar & hover pause
 * - 2D Spring Floating Thumb & progress track on 6-phase Evolution Stepper
 * - IntersectionObserver staggered scroll reveal + pointer-tracking radial spotlight
 * - SPA 3-Column Documentation Router + Scrollspy TOC + animated Ctrl+K Command Palette
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
      kicker: '01 · 7 套风貌与生成',
      title: '内置海滨、温泉、田园、街区等多套世界',
      desc: '随包附带凪浦、汤川、穗村、栞泽等 7 套示例世界；桌面端「生成新小镇」支持按 4 种地形与种子热切换，离线编辑器可改建（零配置全自动生成规划中）。'
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
      kicker: '内置世界编辑器 · 离线可视化',
      title: '自己画墙、开窗、摆家具，导出给 TA 住的新街区',
      desc: '无需写任何配置文件——打开内置世界编辑器直接框选房间、铺木地板、放咖啡桌或书架，并自动校验通道与导出 .world 世界包。'
    }
  ]
];

const STAGE_WORLD_PRESETS = {
  nagiura: {
    label: '凪浦 · 海滨小镇',
    title: '街区与房间 · 凪浦',
    dark: 'assets/screenshots/desktop-world-dark.webp?v=20261113-v060-13',
    light: 'assets/screenshots/desktop-world-light.webp?v=20261113-v060-15',
    alt: 'nyairo 桌面端：凪浦海滨小镇街区总图',
    desc: '默认临海小镇「凪浦」含 17 处海湾与坡道地点，支持桌面端按 4 种地形种子生成新小镇或用离线编辑器自由布置（零配置全自动生成规划中）。'
  },
  onsawa: {
    label: '汤川 · 山间温泉',
    title: '街区与房间 · 汤川',
    dark: 'assets/screenshots/desktop-world-onsawa-dark.webp?v=20261113-v060-20',
    light: 'assets/screenshots/desktop-world-onsawa-light.webp?v=20261113-v060-20',
    alt: 'nyairo 桌面端：汤川山间温泉小镇街区总图与翠泽汤屋详情',
    desc: '山间温泉小镇「汤川」含 15 处山坡地点与露天翠泽汤屋，出门沿登山道步行真实耗时，桌面端可随时热切换体验不同地貌。'
  },
  inobe: {
    label: '穗村 · 田园乡野',
    title: '街区与房间 · 穗村',
    dark: 'assets/screenshots/desktop-world-inobe-dark.webp?v=20261113-v060-20',
    light: 'assets/screenshots/desktop-world-inobe-light.webp?v=20261113-v060-20',
    alt: 'nyairo 桌面端：穗村田园乡野小镇街区总图与水车公园路线',
    desc: '田园农庄村落「穗村」含 14 处水渠、农田与水车公园节点，每段田埂步程均按路网距离真实计时。'
  },
  wakaba: {
    label: '栞泽 · 日常街区',
    title: '街区与房间 · 栞泽',
    dark: 'assets/screenshots/desktop-world-wakaba-dark.webp?v=20261113-v060-20',
    light: 'assets/screenshots/desktop-world-wakaba-light.webp?v=20261113-v060-20',
    alt: 'nyairo 桌面端：栞泽日常街区总图与栞泽书店陈设',
    desc: '日常居住街区「栞泽」含 16 处近郊商店街、向阳咖啡店与栞泽书店，桌面端支持一键切换或导入编辑器按心意改建。'
  }
};

const STAGE_SCENES_META = [
  {
    title: '实机演示',
    kind: 'video',
    dark: 'assets/screenshots/desktop-demo-dark.mp4?v=20261113-v060-14',
    light: 'assets/screenshots/desktop-demo-light.mp4?v=20261113-v060-15',
    stillDark: 'assets/screenshots/desktop-overview-dark.webp?v=20261113-v060-13',
    stillLight: 'assets/screenshots/desktop-overview-light.webp?v=20261113-v060-15'
  },
  { title: '状态总览', dark: 'assets/screenshots/desktop-overview-dark.webp?v=20261113-v060-13', light: 'assets/screenshots/desktop-overview-light.webp?v=20261113-v060-15' },
  { title: '街区与房间', dark: 'assets/screenshots/desktop-world-dark.webp?v=20261113-v060-13', light: 'assets/screenshots/desktop-world-light.webp?v=20261113-v060-15' },
  { title: '流式交谈', dark: 'assets/screenshots/desktop-talk-dark.webp?v=20261113-v060-13', light: 'assets/screenshots/desktop-talk-light.webp?v=20261113-v060-15' },
  { title: '文稿与共读', dark: 'assets/screenshots/desktop-notes-dark.webp?v=20261113-v060-13', light: 'assets/screenshots/desktop-notes-light.webp?v=20261113-v060-15' },
  { title: '地图编辑', dark: 'assets/screenshots/desktop-editor.webp?v=20261113-v060-26', light: 'assets/screenshots/desktop-editor.webp?v=20261113-v060-26' }
];

const layoutSyncFns = [];
let syncHeaderLampFn = null;
let openStageLightboxFn = null;
let syncStageVideosFn = null;

function bootstrap() {
  initTheme();
  initHeaderPhysics();
  initSegmentedControls();
  initInstallDock();
  initProductStage();
  initStageLightbox();
  initEvolutionTimeline();
  initFaqAccordion();
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
    themeBtn.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme') || 'dark';
      switchTheme(cur === 'dark' ? 'light' : 'dark');
    });
  }
}

function switchTheme(nextTheme) {
  const reduced = isReducedMotion();
  if (!document.startViewTransition || reduced) {
    applyTheme(nextTheme);
    return;
  }

  document.documentElement.classList.add('vt-switching');
  const transition = document.startViewTransition(() => {
    applyTheme(nextTheme);
  });

  transition.ready.then(() => {
    const easing = 'cubic-bezier(0.25, 0.8, 0.25, 1)';
    document.documentElement.animate(
      { opacity: [0, 1] },
      { duration: 280, easing, fill: 'both', pseudoElement: '::view-transition-new(root)' }
    );
    const iconEl = document.getElementById('theme-icon');
    if (iconEl && typeof iconEl.animate === 'function') {
      iconEl.animate(
        [
          { transform: 'rotate(-36deg) scale(0.84)' },
          { transform: 'rotate(0deg) scale(1)' }
        ],
        { duration: 400, easing: 'cubic-bezier(0.2, 0.9, 0.25, 1.02)' }
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
  try {
    localStorage.setItem('nyairo-theme', next);
    localStorage.setItem('chiyo-theme', next);
  } catch {}
  if (typeof syncStageVideosFn === 'function') syncStageVideosFn();

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

  // Scroll Progress + Homepage Section Scrollspy + Back-to-Top visibility
  const backTopBtn = document.getElementById('back-to-top');
  let ticking = false;
  function onScrollFrame() {
    ticking = false;
    const docEl = document.documentElement;
    const maxScroll = docEl.scrollHeight - window.innerHeight;
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
      const capEl = document.getElementById('home-capabilities');
      const archEl = document.getElementById('home-arch');
      const prevEl = document.getElementById('home-preview');
      let activeId = 'nav-home';
      if (faqEl && faqEl.getBoundingClientRect().top <= 240) {
        activeId = 'nav-faq';
      } else if (capEl && capEl.getBoundingClientRect().top <= 240) {
        activeId = 'nav-capabilities';
      } else if (archEl && archEl.getBoundingClientRect().top <= 240) {
        activeId = 'nav-arch';
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
   4. 3-in-1 Unified Install Dock (Directional Slide + Height Morph)
   ========================================================================== */
function initInstallDock() {
  const bar = document.getElementById('install-seg');
  const viewport = document.getElementById('install-viewport');

  if (bar) {
    const order = ['win', 'cli', 'agent'];
    let currentMode = 'win';

    const tabs = Array.from(bar.querySelectorAll('.seg-btn'));
    const panels = {
      win: document.getElementById('install-panel-win'),
      cli: document.getElementById('install-panel-cli'),
      agent: document.getElementById('install-panel-agent')
    };

    function selectMode(mode, focusBtn = false) {
      if (mode === currentMode && !focusBtn) return;
      const prevIdx = order.indexOf(currentMode);
      const nextIdx = order.indexOf(mode);
      const isPrev = nextIdx < prevIdx;
      currentMode = mode;

      tabs.forEach(btn => {
        const isMatch = btn.getAttribute('data-install') === mode;
        btn.classList.toggle('is-active', isMatch);
        btn.setAttribute('aria-selected', String(isMatch));
        btn.tabIndex = isMatch ? 0 : -1;
        if (isMatch && focusBtn) btn.focus();
      });

      morphContainerHeight(viewport, () => {
        Object.entries(panels).forEach(([k, el]) => {
          if (!el) return;
          const active = k === mode;
          el.hidden = !active;
          if (active) {
            el.classList.toggle('dir-prev', isPrev);
          }
        });
      });

      syncSegThumb(bar);
    }

    tabs.forEach((btn, idx) => {
      btn.addEventListener('click', () => selectMode(btn.getAttribute('data-install')));
      btn.addEventListener('keydown', e => {
        let nextIdx;
        if (e.key === 'ArrowRight') nextIdx = (idx + 1) % tabs.length;
        if (e.key === 'ArrowLeft') nextIdx = (idx - 1 + tabs.length) % tabs.length;
        if (e.key === 'Home') nextIdx = 0;
        if (e.key === 'End') nextIdx = tabs.length - 1;
        if (nextIdx === undefined) return;
        e.preventDefault();
        selectMode(tabs[nextIdx].getAttribute('data-install'), true);
      });
    });

    try {
      const ua = navigator.userAgent || '';
      if (/\bLinux\b/i.test(ua) && !/\bAndroid\b/i.test(ua)) {
        currentMode = 'cli';
        tabs.forEach(btn => {
          const isMatch = btn.getAttribute('data-install') === 'cli';
          btn.classList.toggle('is-active', isMatch);
          btn.setAttribute('aria-selected', String(isMatch));
          btn.tabIndex = isMatch ? 0 : -1;
        });
        Object.entries(panels).forEach(([k, el]) => {
          if (el) el.hidden = k !== 'cli';
        });
        requestAnimationFrame(() => syncSegThumb(bar));
      }
    } catch {}
  }

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
      morphContainerHeight(viewport, () => {
        fb.hidden = false;
      });
    });
  });

  // Copy setup.exe SHA256 (install dock + trust verify bar)
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

  // Agent Prompt Drawer (Smooth Height Morph) & Copy
  const drawerBtn = document.getElementById('agent-toggle-drawer');
  const drawerPre = document.getElementById('agent-code-drawer');
  const drawerLbl = document.getElementById('drawer-toggle-label');
  if (drawerBtn && drawerPre) {
    drawerBtn.addEventListener('click', () => {
      morphContainerHeight(viewport, () => {
        const nextOpen = drawerPre.hidden;
        drawerPre.hidden = !nextOpen;
        drawerBtn.setAttribute('aria-expanded', String(nextOpen));
        if (drawerLbl) {
          drawerLbl.textContent = nextOpen ? '收起指令全文' : '展开查看指令全文';
        }
      });
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

  function syncStageVideos() {
    if (!darkV && !lightV) return;
    const theme = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    const activeV = theme === 'light' ? lightV : darkV;
    const idleV = theme === 'light' ? darkV : lightV;
    const prevTime = idleV && Number.isFinite(idleV.currentTime) ? idleV.currentTime : 0;
    if (idleV && !idleV.paused) idleV.pause();

    const lbOpen = !!document.getElementById('stage-lightbox')?.classList.contains('show');
    const homeHidden = document.getElementById('view-home')?.style.display === 'none';
    const shouldPlay = currentIdx === 0 && !stageEl.classList.contains('is-out-of-view') && !lbOpen && !homeHidden && !isReducedMotion();

    if (activeV) {
      if (activeV.preload !== 'auto') activeV.preload = 'auto';
      if (prevTime > 0 && activeV.currentTime < 0.05 && Number.isFinite(activeV.duration) && activeV.duration > prevTime) {
        activeV.currentTime = prevTime;
      }
      if (shouldPlay) {
        activeV.play().catch(() => {});
      } else if (!activeV.paused) {
        activeV.pause();
      }
    }
  }
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
    if (worldStrip) worldStrip.hidden = index !== 2;
    restartAutoProgress();
    syncStageVideos();
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

  // Pause auto-cycle & video while offscreen so Scene 0 always plays cleanly in view
  if (typeof IntersectionObserver !== 'undefined') {
    stageEl.classList.add('is-out-of-view');
    const stageIo = new IntersectionObserver(
      entries => {
        entries.forEach(entry => {
          stageEl.classList.toggle('is-out-of-view', !entry.isIntersecting);
        });
        syncStageVideos();
      },
      { threshold: 0.12 }
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
  const worldImgDark = document.getElementById('stage-world-img-dark');
  const worldImgLight = document.getElementById('stage-world-img-light');
  if (worldBar && worldImgDark && worldImgLight) {
    const worldBtns = Array.from(worldBar.querySelectorAll('.stage-world-pill'));
    const selectWorld = (slug, byUser = true) => {
      const preset = STAGE_WORLD_PRESETS[slug];
      if (!preset) return;
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
      worldImgDark.src = preset.dark;
      worldImgLight.src = preset.light;
      worldImgDark.alt = preset.alt;
      worldImgLight.alt = preset.alt;
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
  const tabs = Array.from(document.querySelectorAll('#lightbox-tabs .lightbox-tab'));
  if (!backdrop || !imgDark || !imgLight) return;

  let activeIdx = 0;
  let onSceneSync = null;
  let closeTimer = null;
  let lastTrigger = null;

  function syncLightboxMedia(meta) {
    const reduced = isReducedMotion();
    const isVideo = meta?.kind === 'video' && !reduced && backdrop.classList.contains('show');
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
    }
  }

  function renderLightboxScene(idx) {
    activeIdx = (idx + STAGE_SCENES_META.length) % STAGE_SCENES_META.length;
    const meta = STAGE_SCENES_META[activeIdx];
    if (counterEl) counterEl.textContent = `0${activeIdx + 1} / 0${STAGE_SCENES_META.length}`;
    if (titleEl) titleEl.textContent = meta.title;
    syncLightboxMedia(meta);
    tabs.forEach((t, i) => t.classList.toggle('is-active', i === activeIdx));
    if (typeof onSceneSync === 'function') onSceneSync(activeIdx);
  }

  function closeLightbox() {
    if (!backdrop.classList.contains('show')) return;
    backdrop.setAttribute('aria-hidden', 'true');
    if (vidDark && !vidDark.paused) vidDark.pause();
    if (vidLight && !vidLight.paused) vidLight.pause();
    const finishClose = () => {
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
    backdrop.classList.remove('is-closing');
    backdrop.classList.add('show');
    backdrop.setAttribute('aria-hidden', 'false');
    renderLightboxScene(idx || 0);
    if (typeof syncStageVideosFn === 'function') syncStageVideosFn();
    if (closeBtn) setTimeout(() => closeBtn.focus(), 20);
  };

  if (closeBtn) closeBtn.addEventListener('click', closeLightbox);
  if (prevBtn) prevBtn.addEventListener('click', () => renderLightboxScene(activeIdx - 1));
  if (nextBtn) nextBtn.addEventListener('click', () => renderLightboxScene(activeIdx + 1));
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const idx = parseInt(tab.getAttribute('data-lb-scene') || '0', 10);
      renderLightboxScene(idx);
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
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      renderLightboxScene(activeIdx + 1);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      renderLightboxScene(activeIdx - 1);
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

    // 1. Subtle Hero scroll fade (strictly 'none' at sy <= 4 for crisp ClearType)
    if (heroCopy) {
      if (sy <= 4 || vw <= 768) {
        if (heroCopy.style.transform) heroCopy.style.transform = '';
        if (heroCopy.style.opacity) heroCopy.style.opacity = '';
      } else if (sy < 620) {
        const hp = Math.min(1, (sy - 4) / 560);
        heroCopy.style.opacity = (1 - hp * 0.42).toFixed(3);
        heroCopy.style.transform = `translate3d(0, ${(-hp * 14).toFixed(1)}px, 0)`;
      }
    }

    // 2. Gentle perspective settle on desktop preview stage
    if (vw <= 768) {
      if (halo.style.transform) halo.style.transform = '';
      halo.classList.remove('stage-3d-active');
    } else {
      const rect = halo.getBoundingClientRect();
      const startY = vh * 0.95;
      const endY = vh * 0.40;
      const raw = (startY - rect.top) / Math.max(1, startY - endY);
      const clamped = Math.max(0, Math.min(1, raw));
      const p = 1 - Math.pow(1 - clamped, 2.1);

      if (p >= 0.994) {
        if (halo.style.transform) halo.style.transform = '';
        halo.classList.remove('stage-3d-active');
      } else {
        const rotX = ((1 - p) * 4.5).toFixed(2);
        const ty = ((1 - p) * 16).toFixed(1);
        const sc = (0.976 + p * 0.024).toFixed(4);
        halo.classList.add('stage-3d-active');
        halo.style.transform = `perspective(1400px) rotateX(${rotX}deg) translate3d(0, ${ty}px, 0) scale(${sc})`;
      }
    }
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
  const targets = Array.from(document.querySelectorAll('[data-reveal]'));
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
    { rootMargin: '0px 0px -8% 0px', threshold: 0.08 }
  );

  targets.forEach(el => io.observe(el));
}

function initSpotlights() {
  const cards = Array.from(document.querySelectorAll('.spotlight-surface'));
  if (!cards.length) return;

  cards.forEach(card => {
    card.addEventListener('pointermove', e => {
      if (e.pointerType === 'touch') return;
      const rect = card.getBoundingClientRect();
      card.style.setProperty('--mx', `${(e.clientX - rect.left).toFixed(1)}px`);
      card.style.setProperty('--my', `${(e.clientY - rect.top).toFixed(1)}px`);
    }, { passive: true });
  });
}

function initBentoMapInteraction() {
  const lampBtn = document.getElementById('bento-lamp-btn');
  const lampLabel = document.getElementById('bento-lamp-label');
  const mapVisual = document.getElementById('bento-map-visual');
  if (!lampBtn || !mapVisual) return;

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
    const applyScenario = idx => {
      const data = scenarios[idx];
      if (!data) return;
      tabs.forEach((btn, i) => {
        const active = i === idx;
        btn.classList.toggle('is-active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      Object.entries(fieldMap).forEach(([key, el]) => {
        if (el) el.textContent = data[key];
      });
    };

    tabsWrap.addEventListener('click', e => {
      const btn = e.target.closest('.contrast-tab');
      if (!btn) return;
      const idx = Number(btn.getAttribute('data-contrast'));
      if (!Number.isNaN(idx)) applyScenario(idx);
    });
  }

  document.querySelectorAll('.persona-copy-btn[data-persona-copy]').forEach(btn => {
    let resetTimer = null;
    btn.addEventListener('click', async () => {
      const key = btn.getAttribute('data-persona-copy');
      const target = document.getElementById(`persona-text-${key}`);
      if (!target) return;
      const ok = await copyElementText(target);
      btn.classList.toggle('is-copied', ok);
      btn.textContent = ok ? '已复制模板' : '已选中，请按 Ctrl+C';
      if (resetTimer) clearTimeout(resetTimer);
      resetTimer = setTimeout(() => {
        btn.classList.remove('is-copied');
        btn.textContent = '复制模板';
      }, 1800);
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

      if (location.hash !== desiredHash) {
        history.pushState(null, '', desiredHash);
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
        <a href="#home-arch" class="sidebar-mobile-chip" data-mobile-home="home-arch">核心机制</a>
        <a href="#home-capabilities" class="sidebar-mobile-chip" data-mobile-home="home-capabilities">已装功能</a>
        <a href="#home-faq" class="sidebar-mobile-chip" data-mobile-home="home-faq">常见问题</a>
        <a href="#roadmap" class="sidebar-mobile-chip">未来规划</a>
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
        if (location.hash !== desiredHash) {
          history.pushState(null, '', desiredHash);
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
  routeHash();
}

function routeHash(autoCloseSidebar = true) {
  const urlParams = new URLSearchParams(location.search);
  const queryDoc = (urlParams.get('doc') || urlParams.get('p') || '').trim();
  const rawHash = location.hash.replace('#', '').trim();
  const target = rawHash || queryDoc;

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
      target === 'home-arch' ? document.getElementById('nav-arch') :
      target === 'home-capabilities' ? document.getElementById('nav-capabilities') :
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

  renderTOC(tocItems);
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

function renderTOC(tocList) {
  if (activeTocCleanup) {
    activeTocCleanup();
    activeTocCleanup = null;
  }

  const tocListEl = document.getElementById('toc-list');
  if (!tocListEl) return;

  if (!tocList || tocList.length === 0) {
    tocListEl.innerHTML = '<li style="padding: 4px 12px; color: var(--muted); font-size: 0.78rem;">本页无子章节</li>';
    return;
  }

  tocListEl.innerHTML = tocList.map((item, i) => `
    <li class="toc-item">
      <a href="#${escapeHtml(item.id)}" class="toc-link${i === 0 ? ' active' : ''}" data-anchor="${escapeHtml(item.id)}">${escapeHtml(item.text)}</a>
    </li>
  `).join('');

  const links = Array.from(tocListEl.querySelectorAll('.toc-link'));
  links.forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      const id = link.getAttribute('data-anchor');
      const el = document.getElementById(id);
      if (el) {
        el.scrollIntoView({ behavior: preferredScrollBehavior(), block: 'start' });
        history.replaceState(null, '', `#${id}`);
        links.forEach(l => l.classList.toggle('active', l === link));
      }
    });
  });

  const onScroll = () => {
    let activeId = tocList[0].id;
    for (const item of tocList) {
      const h = document.getElementById(item.id);
      if (h && h.getBoundingClientRect().top <= 110) {
        activeId = item.id;
      }
    }
    links.forEach(l => l.classList.toggle('active', l.getAttribute('data-anchor') === activeId));
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  activeTocCleanup = () => window.removeEventListener('scroll', onScroll);
}

function bindDocCopyButtons() {
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
      title: '下载 Windows 桌面客户端 (v0.6.0 · 5.10 MB)',
      summary: '直接获取 nyairo-0.6.0-setup.exe 安装包（免管理员权限）',
      badge: '下载',
      run: () => {
        const a = document.createElement('a');
        a.href = 'downloads/nyairo-0.6.0-setup.exe';
        a.download = 'nyairo-0.6.0-setup.exe';
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
      run: () => writeClipboardText('curl -fsSL https://nyairo.com/install.sh | bash')
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
      group: '常用文档',
      title: '在 Windows 上安装（桌面客户端 + WSL2）',
      summary: '图形化安装向导与环境准备说明',
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
      title: '地图编辑器与世界包',
      summary: '离线修改房间与街道、切换内置示例世界及当前模板生成边界',
      badge: '世界',
      href: '#world-editor'
    },
    {
      group: '常用文档',
      title: 'v0.6.0 版本动态与更新说明',
      summary: '查看日常相处回执、未来问题、双人共读及当前公开测试版边界说明',
      badge: 'NEW',
      href: '#changelog-v0-6-0'
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
      title: '未来规划：随机生成专属世界与无损搬家',
      summary: '查看千人千面随机虚拟世界（规划中，尚未实现）及跨设备迁移方向',
      badge: '规划',
      href: '#roadmap'
    }
  ];

  function openModal() {
    if (closeTimer) {
      clearTimeout(closeTimer);
      closeTimer = null;
    }
    backdrop.classList.remove('is-closing');
    backdrop.classList.add('show');
    input.value = '';
    renderList('');
    setTimeout(() => input.focus(), 30);
  }

  function closeModal() {
    if (!backdrop.classList.contains('show')) return;
    if (isReducedMotion()) {
      backdrop.classList.remove('show', 'is-closing');
      return;
    }
    backdrop.classList.add('is-closing');
    closeTimer = setTimeout(() => {
      backdrop.classList.remove('show', 'is-closing');
      closeTimer = null;
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
    closeModal();
    if (item.href) {
      location.hash = item.href;
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
  backdrop.addEventListener('click', e => {
    if (e.target === backdrop) closeModal();
  });

  input.addEventListener('input', e => renderList(e.target.value));

  window.addEventListener('keydown', e => {
    const lightboxOpen = document.getElementById('stage-lightbox')?.classList.contains('show');
    if (lightboxOpen) return;

    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      backdrop.classList.contains('show') && !backdrop.classList.contains('is-closing') ? closeModal() : openModal();
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
      closeModal();
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
