/* ==========================================================================
   nyairo · Monochrome Manga Collage Hero (Variant B) & Streamlined Homepage
   - Shared trilingual production module (ZH / EN / JA)
   - Loads crisp compressed WebP ink plates (Light multiply / Dark inverted screen)
   ========================================================================== */
(function () {
  'use strict';

  const ASSET_VER = '20261123-v080-61';
  const BODY_CLS = document.body ? document.body.classList : null;
  const PATH_NAME = (window.location && window.location.pathname) || '/';
  const LANG_RAW = (document.documentElement.lang || 'zh-CN').toLowerCase();
  const LANG = (BODY_CLS && BODY_CLS.contains('ny-lang-ja')) || /^\/ja(\/|$)/i.test(PATH_NAME)
    ? 'ja'
    : ((BODY_CLS && BODY_CLS.contains('ny-lang-en')) || /^\/en(\/|$)/i.test(PATH_NAME)
      ? 'en'
      : (LANG_RAW.startsWith('ja') ? 'ja' : (LANG_RAW.startsWith('en') ? 'en' : 'zh')));
  const ART_BASE = LANG === 'zh' ? 'assets/collage' : '../assets/collage';

  const SHEETS = [
    {
      id: 'study',
      idx: '01',
      enTitle: "A Room of One's Own",
      swatch: true,
      anchorSide: 'br',
      compact: { l: 54.4, t: 10.5, w: 146, r: -2.4, s: 0.97, z: 3 },
      expanded: { l: 5.0, t: 6.5, w: 174, r: -1.1, s: 1.0, z: 10 },
      depth: { x: -7, y: -5 },
      stagger: 0.02
    },
    {
      id: 'coast',
      idx: '02',
      enTitle: 'Walkable Seaside Town',
      anchorSide: 'br',
      compact: { l: 62.0, t: 6.5, w: 156, r: -1.0, s: 0.97, z: 5 },
      expanded: { l: 18.6, t: 9.0, w: 186, r: 0.0, s: 1.0, z: 11 },
      depth: { x: 5, y: 4 },
      stagger: 0.04
    },
    {
      id: 'bookstore',
      idx: '03',
      enTitle: 'Twelve Living Shops',
      anchorSide: 'bl',
      compact: { l: 72.2, t: 7.0, w: 150, r: 1.4, s: 0.97, z: 6 },
      expanded: { l: 33.4, t: 4.5, w: 164, r: 0.7, s: 1.0, z: 12 },
      depth: { x: -5, y: 4 },
      stagger: 0.06
    },
    {
      id: 'centerpiece',
      idx: '04',
      enTitle: 'A Living World Beyond the Window',
      anchorSide: 'bl',
      compact: { l: 60.4, t: 20.5, w: 304, r: 0.0, s: 0.98, z: 10 },
      expanded: { l: 46.2, t: 13.5, w: 320, r: 0.0, s: 1.0, z: 16 },
      depth: { x: 13, y: 9 },
      stagger: 0.00
    },
    {
      id: 'rain',
      idx: '05',
      enTitle: 'Rain Against the Pane',
      rightEdge: true,
      anchorSide: 'bl',
      compact: { l: 81.0, t: 10.8, w: 148, r: 1.8, s: 0.97, z: 7 },
      expanded: { l: 75.0, t: 5.0, w: 176, r: 0.5, s: 1.0, z: 13 },
      depth: { x: 9, y: 6 },
      stagger: 0.08
    },
    {
      id: 'onsen',
      idx: '06',
      enTitle: 'Seven Living Worlds',
      swatch: true,
      rightEdge: true,
      anchorSide: 'tl',
      compact: { l: 69.2, t: 37.5, w: 168, r: -1.4, s: 0.97, z: 8 },
      expanded: { l: 71.0, t: 47.0, w: 214, r: 0.0, s: 1.0, z: 14 },
      depth: { x: 14, y: 10 },
      stagger: 0.10
    },
    {
      id: 'trunk',
      idx: '07',
      enTitle: 'Lossless World Trunk',
      anchorSide: 'tl',
      compact: { l: 81.5, t: 36.5, w: 162, r: 2.0, s: 0.96, z: 9 },
      expanded: { l: 49.5, t: 57.0, w: 196, r: -0.6, s: 1.0, z: 15 },
      depth: { x: 8, y: 6 },
      stagger: 0.12
    }
  ];

  const I18N = {
    zh: {
      trackAria: 'nyairo 日漫线稿手账拼贴',
      dotsAria: '切换画片注脚',
      eyebrow: 'NAGIURA ARCHIVE · 07 PLATES',
      heading: '一间推窗见海的屋子，<br><span class="ny-collage-accent">一段自己走的时间。</span>',
      sub: '离开聊天窗口之后，属于 TA 的七个日常切面',
      heroCue: '向下滚动，摊开 TA 的七幕二次元日常',
      cueIdle: '向下滚动，摊开手账拼贴 ↓',
      cueInteractive: '✦ 点击任意画片或右下角编号，查看手账注脚',
      plates: {
        study: {
          alt: '黑白日漫线稿：海边木窗书房、白纱帘、灯塔、画本与茶杯',
          desc: '推窗见海的临海书房，每一盏灯与家具都按真实步程安放'
        },
        coast: {
          alt: '黑白日漫线稿：夏云与绣球花映衬的临海小镇石阶坡道',
          desc: '免选模板一键随机整座小镇，沿临海坡道按真实距离步行'
        },
        bookstore: {
          alt: '黑白日漫线稿：街角两层木质旧书店、条纹雨篷与打盹小猫',
          desc: '亮着暖灯的旧书店与咖啡角，12 类街坊小店错峰营业'
        },
        centerpiece: {
          alt: '黑白日漫线稿：弯月下海湾小镇、堤岸栈桥与灯塔全景',
          desc: '离开聊天窗口后，属于 TA 的整座临海小镇仍在昼夜运转'
        },
        rain: {
          alt: '黑白日漫线稿：挂着晴天娃娃的细雨木窗、热茶与绣球花瓣笔记',
          desc: '窗外微雨、桌茶渐凉，不想帮的杂活干脆说不也不记仇'
        },
        onsen: {
          alt: '黑白日漫线稿：弯月下山间温泉石阶小巷、旅馆檐廊与热气',
          desc: '临海凪浦、山间汤川、水车穗村与近郊栞泽，随时热切换风貌'
        },
        trunk: {
          alt: '黑白日漫线稿：海景露台上装着地图、手账、油灯与灯塔雪球的手提箱',
          desc: '所有记忆与整座专属小镇无损装进离线旅行箱，随时打包带走'
        }
      }
    },
    en: {
      trackAria: 'nyairo Inked Archival Collage Exhibition',
      dotsAria: 'Select chapter note',
      eyebrow: 'NAGIURA ARCHIVE · 07 PLATES',
      heading: 'A room looking out to sea,<br><span class="ny-collage-accent">and time that walks on its own.</span>',
      sub: 'Seven everyday glimpses after you close the chat window.',
      heroCue: 'Scroll down to unfold seven inked scenes of their day',
      cueIdle: 'Scroll to unfold the 7 plates ↓',
      cueInteractive: '✦ Click any plate or chapter number to read its note',
      plates: {
        study: {
          alt: 'Black-and-white manga line art: seaside wooden study room with lamp, open sketchbook, tea mug, and distant lighthouse',
          desc: 'A seaside study where every lamp, shelf, and doorway sits on real step distances.'
        },
        coast: {
          alt: 'Black-and-white manga line art: seaside stone steps lined with hydrangeas overlooking harbor boats and cumulus clouds',
          desc: 'Generate a procedural town in one click and walk its coastal slopes on real road timers.'
        },
        bookstore: {
          alt: 'Black-and-white manga line art: two-story corner secondhand bookstore with striped awning and sleeping cat',
          desc: 'Warm-lit secondhand bookstores and corner cafes across 12 staggered-hour shop archetypes.'
        },
        centerpiece: {
          alt: 'Black-and-white manga line art: crescent moon panorama of a seaside harbor town, pier, anchored boats, and cape lighthouse',
          desc: 'Even after you close the chat window, their seaside town keeps turning through day and night.'
        },
        rain: {
          alt: 'Black-and-white manga line art: rainy wooden window with smiling teru-teru-bozu charm, hot tea mug, and notebook',
          desc: 'Rain on the window, cooling tea on the desk — and an honest right to say no without grudges.'
        },
        onsen: {
          alt: 'Black-and-white manga line art: curved mountain hot-spring stone alley with ryokan eaves, lanterns, and rising steam',
          desc: 'Coastal Nagiura, hot-spring Onsawa, rural Inobe, and suburban Wakaba — switch worlds anytime.'
        },
        trunk: {
          alt: 'Black-and-white manga line art: open travel trunk on a seaside balcony holding map scrolls, journals, lantern, and lighthouse snowglobe',
          desc: 'Pack every local memory and the entire town into an offline .nyairo-suitcase.zip anytime.'
        }
      }
    },
    ja: {
      trackAria: 'nyairo モノクロ線画ギャラリー',
      dotsAria: '注釈プレートを切り替え',
      eyebrow: 'NAGIURA ARCHIVE · 07 PLATES',
      heading: '海を望むひとつの部屋と、<br><span class="ny-collage-accent">みずから流れる時間。</span>',
      sub: 'ウィンドウを閉じたあとに続く、七つの暮らしの断片',
      heroCue: '下にスクロールして、七つの線画シーンをひらく',
      cueIdle: 'スクロールして 7 枚の線画をひらく ↓',
      cueInteractive: '✦ 任意の絵または右下の番号をクリックして注釈を見る',
      plates: {
        study: {
          alt: 'モノクロ漫画線画：海と灯台を望む木枠窓の書斎、ランプ、開いたノート、湯気の立つマグカップ',
          desc: '海が見える書斎。ランプも家具も、確かな歩幅と距離のなかに置かれています。'
        },
        coast: {
          alt: 'モノクロ漫画線画：紫陽花と入道雲を望む港町の石段の坂道',
          desc: 'テンプレート不要で街を丸ごと生成。海沿いの石段を実際の道路距離で歩きます。'
        },
        bookstore: {
          alt: 'モノクロ漫画線画：角地の木造古書店と丸まって眠る猫',
          desc: '古書店や喫茶店など 12 業種の店が、それぞれの営業時間で静かに灯りをともします。'
        },
        centerpiece: {
          alt: 'モノクロ漫画線画：三日月と灯台が見守る海辺の港町全景、桟橋と小舟',
          desc: 'ウィンドウを閉じたあとも、海辺の街では昼と夜のリズムが静かに流れ続けます。'
        },
        rain: {
          alt: 'モノクロ漫画線画：てるてる坊主が揺れる雨の窓辺、湯気の立つお茶と開いた手帳',
          desc: '雨音と冷めていくお茶。気の乗らない頼みごとは、気まずさを残さず素直に断れます。'
        },
        onsen: {
          alt: 'モノクロ漫画線画：三日月の下で湯けむりが立ちのぼる山あいの温泉街と石段の路地',
          desc: '海辺の凪浦、温泉街の湯川、水車の穂村、街角の栞沢——暮らしの舞台はいつでも切り替え可能。'
        },
        trunk: {
          alt: 'モノクロ漫画線画：海辺のテラスに開かれた旅行トランク、地図の巻物、手帖、ランタンと灯台のスノードーム',
          desc: 'すべてのローカル記憶と街ごとオフラインの旅行鞄に収め、いつでも無劣化で持ち出せます。'
        }
      }
    }
  };

  const EDGES = [
    { from: 'study', to: 'coast', fromPin: 'br', toPin: 'tl', dotted: false },
    { from: 'coast', to: 'bookstore', fromPin: 'tr', toPin: 'bl', dotted: true },
    { from: 'coast', to: 'centerpiece', fromPin: 'br', toPin: 'tl', dotted: false },
    { from: 'bookstore', to: 'centerpiece', fromPin: 'br', toPin: 'tc', dotted: true },
    { from: 'centerpiece', to: 'rain', fromPin: 'tr', toPin: 'bl', dotted: false },
    { from: 'centerpiece', to: 'onsen', fromPin: 'br', toPin: 'tl', dotted: false },
    { from: 'centerpiece', to: 'trunk', fromPin: 'br', toPin: 'tr', dotted: true },
    { from: 'trunk', to: 'onsen', fromPin: 'tr', toPin: 'bl', dotted: false }
  ];

  function createSvgEl(tag, attrs) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        el.setAttribute(k, String(v));
      }
    }
    return el;
  }

  function clamp(v, min, max) {
    return v < min ? min : (v > max ? max : v);
  }

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function getHomeRoot() {
    return document.getElementById('view-home') || document.querySelector('main.home-view');
  }

  function mountHeroCollage() {
    const t = I18N[LANG];
    const homeView = getHomeRoot();
    const homeContainer = homeView ? homeView.querySelector('.home-container') : null;
    const heroSection = homeContainer ? homeContainer.querySelector('.hero-section, .ja-hero') : null;
    if (!homeView || !homeContainer || !heroSection) return;
    if (document.getElementById('ny-collage-track')) return;

    document.body.classList.add('ny-variant-topright', 'ny-style-ink');

    const track = document.createElement('section');
    track.id = 'ny-collage-track';
    track.className = 'is-hero-topright';
    track.setAttribute('aria-label', t.trackAria);

    const sticky = document.createElement('div');
    sticky.className = 'ny-collage-sticky';

    const paper = document.createElement('div');
    paper.className = 'ny-collage-paper-grain';
    paper.setAttribute('aria-hidden', 'true');

    const grid = document.createElement('div');
    grid.className = 'ny-collage-grid';
    grid.setAttribute('aria-hidden', 'true');

    const canvas = document.createElement('div');
    canvas.className = 'ny-collage-canvas';

    const svg = createSvgEl('svg', {
      class: 'ny-collage-svg',
      'aria-hidden': 'true'
    });

    const frame = document.createElement('div');
    frame.className = 'ny-collage-frame';
    frame.setAttribute('aria-hidden', 'true');
    ['tl', 'tr', 'bl', 'br'].forEach(pos => {
      const h = document.createElement('span');
      h.className = `ny-frame-handle ${pos}`;
      frame.appendChild(h);
    });

    const sheetsLayer = document.createElement('div');
    sheetsLayer.className = 'ny-sheets-layer';

    const dotsBar = document.createElement('div');
    dotsBar.className = 'ny-collage-dots';
    dotsBar.setAttribute('role', 'group');
    dotsBar.setAttribute('aria-label', t.dotsAria);

    const sheetEls = {};
    const dotEls = {};

    SHEETS.forEach(meta => {
      const plateCopy = t.plates[meta.id] || { alt: '', desc: '' };
      const el = document.createElement('div');
      let cls = 'ny-sheet';
      if (meta.rightEdge) cls += ' is-right-edge';
      el.className = cls;
      el.setAttribute('data-sheet', meta.id);
      el.setAttribute('tabindex', '0');
      el.setAttribute('role', 'button');
      el.setAttribute('aria-pressed', 'false');
      el.setAttribute('aria-label', `${meta.enTitle} · ${plateCopy.desc}`);

      el.style.setProperty('--depth-x', `${meta.depth.x}px`);
      el.style.setProperty('--depth-y', `${meta.depth.y}px`);

      if (meta.swatch) {
        const sw = document.createElement('div');
        sw.className = 'ny-sheet-swatch';
        sw.setAttribute('aria-hidden', 'true');
        el.appendChild(sw);
      }

      const art = document.createElement('div');
      art.className = 'ny-sheet-art';

      const outline = document.createElement('span');
      outline.className = 'ny-sheet-outline';
      outline.setAttribute('aria-hidden', 'true');
      art.appendChild(outline);

      ['tl', 'tr', 'bl', 'br'].forEach(pos => {
        const h = document.createElement('span');
        h.className = `ny-sheet-handle ${pos}`;
        h.setAttribute('aria-hidden', 'true');
        art.appendChild(h);
      });

      const img = document.createElement('img');
      img.src = `${ART_BASE}/${meta.id}.webp?v=${ASSET_VER}`;
      img.alt = plateCopy.alt;
      img.loading = 'eager';
      img.decoding = 'sync';
      art.appendChild(img);
      el.appendChild(art);

      const cap = document.createElement('div');
      cap.className = 'ny-sheet-caption';
      const titleSpan = document.createElement('span');
      titleSpan.className = 'ny-cap-en';
      titleSpan.textContent = meta.enTitle;
      const descSpan = document.createElement('span');
      descSpan.className = 'ny-cap-zh';
      descSpan.textContent = plateCopy.desc;
      cap.appendChild(titleSpan);
      cap.appendChild(descSpan);
      el.appendChild(cap);

      sheetsLayer.appendChild(el);
      sheetEls[meta.id] = el;

      const dotBtn = document.createElement('button');
      dotBtn.type = 'button';
      dotBtn.className = 'ny-dot-btn';
      dotBtn.textContent = meta.idx;
      dotBtn.setAttribute('data-target-sheet', meta.id);
      dotBtn.title = meta.enTitle;
      dotsBar.appendChild(dotBtn);
      dotEls[meta.id] = dotBtn;
    });

    const edgeRecords = [];
    const anchorSquares = [];

    EDGES.forEach(edge => {
      const line = createSvgEl('line', {
        class: 'ny-edge-line' + (edge.dotted ? ' is-dotted' : '')
      });
      svg.appendChild(line);
      edgeRecords.push({ ...edge, el: line });
    });

    SHEETS.forEach(meta => {
      const sq = createSvgEl('rect', {
        class: 'ny-edge-anchor',
        width: '6',
        height: '6',
        'data-anchor-sheet': meta.id
      });
      svg.appendChild(sq);
      anchorSquares.push({ meta, el: sq });
    });

    const lockup = document.createElement('div');
    lockup.className = 'ny-collage-lockup';
    lockup.innerHTML = [
      `<span class="ny-collage-eyebrow">${t.eyebrow}</span>`,
      `<h2 class="ny-collage-heading">${t.heading}</h2>`,
      `<p class="ny-collage-sub">${t.sub}</p>`,
      `<span class="ny-collage-cue"><span class="ny-collage-cue-dot" aria-hidden="true"></span><span id="ny-collage-cue-text">${t.cueIdle}</span></span>`
    ].join('');
    const cueText = lockup.querySelector('#ny-collage-cue-text');

    canvas.appendChild(svg);
    canvas.appendChild(frame);
    canvas.appendChild(sheetsLayer);
    canvas.appendChild(lockup);
    canvas.appendChild(dotsBar);

    const heroOverlay = document.createElement('div');
    heroOverlay.className = 'ny-hero-overlay';

    let heroCopyStage = heroSection.querySelector('#hero-copy-stage');
    if (heroCopyStage) {
      const specRail = heroCopyStage.querySelector('.hero-spec-rail');
      heroOverlay.appendChild(heroCopyStage);
      if (specRail) heroOverlay.appendChild(specRail);
    } else {
      heroCopyStage = document.createElement('div');
      heroCopyStage.className = 'hero-copy-stage';
      heroCopyStage.id = 'hero-copy-stage';
      const specRail = heroSection.querySelector('.hero-spec-rail');
      Array.from(heroSection.children).forEach(child => {
        if (child !== specRail) {
          heroCopyStage.appendChild(child);
        }
      });
      heroOverlay.appendChild(heroCopyStage);
      if (specRail) heroOverlay.appendChild(specRail);
    }

    const scrollCue = document.createElement('button');
    scrollCue.type = 'button';
    scrollCue.id = 'ny-hero-scroll-cue';
    scrollCue.className = 'ny-hero-scroll-cue';
    const cueArrow = document.createElement('span');
    cueArrow.className = 'ny-hero-scroll-arrow';
    cueArrow.setAttribute('aria-hidden', 'true');
    cueArrow.textContent = '↓';
    const cueSpan = document.createElement('span');
    cueSpan.textContent = t.heroCue;
    scrollCue.appendChild(cueArrow);
    scrollCue.appendChild(cueSpan);
    scrollCue.addEventListener('click', () => {
      const targetTop = track.getBoundingClientRect().top + window.scrollY + Math.max(0, track.offsetHeight - sticky.offsetHeight);
      window.scrollTo({ top: Math.round(targetTop * 0.95), behavior: 'smooth' });
    });
    heroOverlay.appendChild(scrollCue);

    sticky.appendChild(paper);
    sticky.appendChild(grid);
    sticky.appendChild(heroOverlay);
    sticky.appendChild(canvas);
    track.appendChild(sticky);
    heroSection.replaceWith(track);

    let activeSheetId = null;
    let userPicked = false;
    let targetPx = 0;
    let targetPy = 0;
    let curPx = 0;
    let curPy = 0;
    let pointerRafId = null;
    let scrollRafId = null;

    function setActiveSheet(id, isUserAction) {
      const wasUserPicked = userPicked;
      if (isUserAction) userPicked = true;
      activeSheetId = (activeSheetId === id && isUserAction && wasUserPicked) ? null : id;

      Object.entries(sheetEls).forEach(([sid, node]) => {
        const on = sid === activeSheetId;
        node.classList.toggle('is-active', on);
        node.setAttribute('aria-pressed', String(on));
      });
      Object.entries(dotEls).forEach(([sid, btn]) => {
        btn.classList.toggle('is-active', sid === activeSheetId);
      });
      edgeRecords.forEach(rec => {
        const hit = !!activeSheetId && (rec.from === activeSheetId || rec.to === activeSheetId);
        rec.el.classList.toggle('is-highlighted', hit);
      });
    }

    Object.entries(sheetEls).forEach(([id, node]) => {
      node.addEventListener('click', () => {
        if (!sticky.classList.contains('is-interactive') && window.innerWidth > 900) return;
        setActiveSheet(id, true);
      });
      node.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          if (!sticky.classList.contains('is-interactive') && window.innerWidth > 900) return;
          setActiveSheet(id, true);
        }
      });
    });

    Object.entries(dotEls).forEach(([id, btn]) => {
      btn.addEventListener('click', () => setActiveSheet(id, true));
    });

    function tickPointer() {
      curPx += (targetPx - curPx) * 0.12;
      curPy += (targetPy - curPy) * 0.12;
      canvas.style.setProperty('--ny-px', curPx.toFixed(4));
      canvas.style.setProperty('--ny-py', curPy.toFixed(4));

      if (Math.abs(targetPx - curPx) > 0.002 || Math.abs(targetPy - curPy) > 0.002) {
        pointerRafId = requestAnimationFrame(tickPointer);
      } else {
        pointerRafId = null;
      }
    }

    sticky.addEventListener('pointermove', e => {
      if (window.innerWidth <= 900) return;
      const rect = sticky.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      targetPx = clamp(((e.clientX - rect.left) / rect.width) * 2 - 1, -1, 1);
      targetPy = clamp(((e.clientY - rect.top) / rect.height) * 2 - 1, -1, 1);
      if (pointerRafId === null) {
        pointerRafId = requestAnimationFrame(tickPointer);
      }
    }, { passive: true });

    sticky.addEventListener('pointerleave', () => {
      targetPx = 0;
      targetPy = 0;
      if (pointerRafId === null) {
        pointerRafId = requestAnimationFrame(tickPointer);
      }
    }, { passive: true });

    function getAnchorPoint(box, pin) {
      switch (pin) {
        case 'tl': return { x: box.x, y: box.y };
        case 'tr': return { x: box.x + box.w, y: box.y };
        case 'bl': return { x: box.x, y: box.y + box.h };
        case 'br': return { x: box.x + box.w, y: box.y + box.h };
        case 'tc': return { x: box.x + box.w * 0.5, y: box.y };
        case 'bc': return { x: box.x + box.w * 0.5, y: box.y + box.h };
        default:   return { x: box.x + box.w * 0.5, y: box.y + box.h * 0.5 };
      }
    }

    function updateLayout() {
      scrollRafId = null;
      if (homeView.style.display === 'none') return;

      const isMobile = window.innerWidth <= 900;
      const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (isMobile) {
        heroOverlay.style.opacity = '1';
        heroOverlay.style.transform = 'none';
        heroOverlay.classList.remove('is-hidden-phase');
        sticky.classList.add('is-interactive');
        if (!activeSheetId && !userPicked) setActiveSheet('centerpiece', false);
        return;
      }

      const cRect = canvas.getBoundingClientRect();
      const cw = cRect.width || 1280;
      const ch = cRect.height || 780;

      let p = 1;
      if (!reduced) {
        const tRect = track.getBoundingClientRect();
        const sRect = sticky.getBoundingClientRect();
        const maxScroll = Math.max(1, tRect.height - sRect.height);
        p = clamp(-tRect.top / maxScroll, 0, 1);
      }

      frame.style.opacity = clamp(1 - p / 0.34, 0, 1).toFixed(3);
      grid.style.opacity = (clamp((p - 0.14) / 0.58, 0, 1) * 0.52).toFixed(3);

      const heroOp = reduced ? 0 : clamp(1 - p / 0.30, 0, 1);
      const heroY = reduced ? 0 : lerp(0, -34, clamp(p / 0.32, 0, 1));
      heroOverlay.style.opacity = heroOp.toFixed(3);
      heroOverlay.style.transform = `translate3d(0, ${heroY.toFixed(1)}px, 0)`;
      heroOverlay.classList.toggle('is-hidden-phase', heroOp <= 0.08);

      const lockOp = reduced ? 1 : clamp((p - 0.38) / 0.30, 0, 1);
      const lockY = reduced ? 0 : lerp(16, 0, lockOp);
      lockup.style.opacity = lockOp.toFixed(3);
      lockup.style.transform = `translate3d(0, ${lockY.toFixed(1)}px, 0)`;
      sticky.classList.toggle('is-expanded-phase', p >= 0.38 || reduced);

      const interactive = p >= 0.45 || reduced;
      sticky.classList.toggle('is-interactive', interactive);
      dotsBar.classList.toggle('is-visible', interactive);

      if (cueText) {
        cueText.textContent = interactive ? t.cueInteractive : t.cueIdle;
      }

      if ((p >= 0.78 || reduced) && !userPicked && !activeSheetId) {
        setActiveSheet('centerpiece', false);
      } else if (p < 0.35 && !userPicked && activeSheetId) {
        setActiveSheet(null, false);
      }

      const scaleW = clamp(cw / 1440, 0.72, 1.06);
      const boxes = {};

      SHEETS.forEach(meta => {
        const node = sheetEls[meta.id];
        const compactBox = meta.compact;
        const localP = clamp((p - meta.stagger) / (1 - meta.stagger), 0, 1);
        const ep = easeInOutCubic(localP);

        const lPct = lerp(compactBox.l, meta.expanded.l, ep);
        const tPct = lerp(compactBox.t, meta.expanded.t, ep);
        const wPx = lerp(compactBox.w, meta.expanded.w, ep) * scaleW;
        const rot = lerp(compactBox.r, meta.expanded.r, ep);
        const sc = lerp(compactBox.s, meta.expanded.s, ep);

        const x = (lPct / 100) * cw;
        const y = (tPct / 100) * ch;

        node.style.left = `${x.toFixed(1)}px`;
        node.style.top = `${y.toFixed(1)}px`;
        node.style.width = `${wPx.toFixed(1)}px`;
        node.style.zIndex = String(ep > 0.5 ? meta.expanded.z : compactBox.z);
        node.style.setProperty('--sheet-rot', `${rot.toFixed(2)}deg`);
        node.style.setProperty('--sheet-scale', sc.toFixed(3));

        const artNode = node.querySelector('.ny-sheet-art');
        const artH = (artNode && artNode.offsetHeight) ? artNode.offsetHeight : (wPx * 0.9);
        boxes[meta.id] = { x, y, w: wPx, h: artH };
      });

      const drawRaw = clamp((p - 0.18) / 0.64, 0, 1);
      const drawT = easeInOutCubic(drawRaw);
      const edgeOp = clamp((p - 0.15) / 0.22, 0, 1);
      edgeRecords.forEach(rec => {
        const b1 = boxes[rec.from];
        const b2 = boxes[rec.to];
        if (!b1 || !b2) return;
        const pt1 = getAnchorPoint(b1, rec.fromPin);
        const pt2 = getAnchorPoint(b2, rec.toPin);
        rec.el.setAttribute('x1', pt1.x.toFixed(1));
        rec.el.setAttribute('y1', pt1.y.toFixed(1));
        rec.el.setAttribute('x2', lerp(pt1.x, pt2.x, drawT).toFixed(1));
        rec.el.setAttribute('y2', lerp(pt1.y, pt2.y, drawT).toFixed(1));
        rec.el.style.opacity = edgeOp.toFixed(3);
      });

      const dotOp = clamp((p - 0.34) / 0.44, 0, 1);
      anchorSquares.forEach(({ meta, el }) => {
        const b = boxes[meta.id];
        if (!b) return;
        const pt = getAnchorPoint(b, meta.anchorSide);
        el.setAttribute('x', (pt.x - 3).toFixed(1));
        el.setAttribute('y', (pt.y - 3).toFixed(1));
        el.style.opacity = dotOp.toFixed(3);
      });
    }

    function requestUpdate() {
      if (scrollRafId === null) {
        scrollRafId = requestAnimationFrame(updateLayout);
      }
    }

    window.addEventListener('scroll', requestUpdate, { passive: true });
    window.addEventListener('resize', requestUpdate, { passive: true });
    window.addEventListener('hashchange', () => setTimeout(requestUpdate, 80));

    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(requestUpdate);
      ro.observe(canvas);
      Object.values(sheetEls).forEach(n => ro.observe(n));
    }

    updateLayout();
    setTimeout(requestUpdate, 180);
  }

  function revealAllInside(root) {
    if (!root) return;
    if (root.hasAttribute && root.hasAttribute('data-reveal')) {
      root.classList.add('is-revealed');
    }
    root.querySelectorAll('[data-reveal], .reveal-cascade-item').forEach(el => {
      el.classList.add('is-revealed');
    });
  }

  function triggerLayoutSync() {
    requestAnimationFrame(() => {
      window.dispatchEvent(new Event('resize'));
    });
  }

  function createDisclosure(id, summaryText) {
    const details = document.createElement('details');
    details.className = 'ny-compact-disclosure';
    details.id = id;

    const summary = document.createElement('summary');
    summary.className = 'ny-disclosure-summary';
    const label = document.createElement('span');
    label.textContent = summaryText;
    const chev = document.createElement('span');
    chev.className = 'ny-disclosure-chevron';
    chev.setAttribute('aria-hidden', 'true');
    chev.textContent = '▾';
    summary.appendChild(label);
    summary.appendChild(chev);

    const body = document.createElement('div');
    body.className = 'ny-disclosure-body';

    details.appendChild(summary);
    details.appendChild(body);

    details.addEventListener('toggle', () => {
      if (details.open) {
        revealAllInside(details);
        triggerLayoutSync();
      }
    });

    return { details, body };
  }

  function buildHub(hubSection, cfg) {
    if (!hubSection || hubSection.querySelector('.ny-hub-shell')) return null;

    hubSection.classList.add('ny-has-hub');

    const head = hubSection.querySelector('.section-head');
    if (head && cfg.headEyebrow && cfg.headDesc) {
      const eyebrow = head.querySelector('.section-eyebrow');
      const desc = head.querySelector('.section-desc');
      if (eyebrow) eyebrow.textContent = cfg.headEyebrow;
      if (desc) desc.textContent = cfg.headDesc;
    }

    const shell = document.createElement('div');
    shell.className = 'ny-hub-shell';

    const bar = document.createElement('div');
    bar.className = 'ny-hub-bar';

    const tabList = document.createElement('div');
    tabList.className = 'ny-hub-tabs';
    tabList.setAttribute('role', 'tablist');
    tabList.setAttribute('aria-label', cfg.tablistAria);

    const panelsWrap = document.createElement('div');
    panelsWrap.className = 'ny-hub-panels';

    const tabBtns = [];
    const panels = [];
    const tabIndexById = {};

    cfg.tabs.forEach((spec, idx) => {
      tabIndexById[spec.id] = idx;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ny-hub-tab' + (idx === 0 ? ' is-active' : '');
      btn.id = `ny-hub-tab-${spec.id}`;
      btn.setAttribute('role', 'tab');
      btn.setAttribute('aria-selected', idx === 0 ? 'true' : 'false');
      btn.setAttribute('aria-controls', `ny-hub-panel-${spec.id}`);
      btn.tabIndex = idx === 0 ? 0 : -1;
      btn.textContent = spec.label;
      tabList.appendChild(btn);
      tabBtns.push(btn);

      const panel = document.createElement('div');
      panel.className = 'ny-hub-panel';
      panel.id = `ny-hub-panel-${spec.id}`;
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', btn.id);
      if (idx !== 0) panel.hidden = true;
      spec.populate(panel);
      panelsWrap.appendChild(panel);
      panels.push(panel);
    });

    bar.appendChild(tabList);
    if (cfg.statusLink) {
      const statusLink = document.createElement('a');
      statusLink.href = cfg.statusLink.href;
      statusLink.className = cfg.statusLink.className;
      if (cfg.statusLink.style) statusLink.setAttribute('style', cfg.statusLink.style);
      statusLink.innerHTML = cfg.statusLink.html;
      bar.appendChild(statusLink);
    }

    shell.appendChild(bar);
    shell.appendChild(panelsWrap);
    hubSection.appendChild(shell);

    const activateTab = (idx, moveFocus) => {
      tabBtns.forEach((btn, i) => {
        const on = i === idx;
        btn.classList.toggle('is-active', on);
        btn.setAttribute('aria-selected', String(on));
        btn.tabIndex = on ? 0 : -1;
        if (on && moveFocus) btn.focus();
      });
      panels.forEach((panel, i) => {
        const on = i === idx;
        panel.hidden = !on;
        if (on) revealAllInside(panel);
      });
      triggerLayoutSync();
    };

    tabBtns.forEach((btn, idx) => {
      btn.addEventListener('click', () => activateTab(idx, false));
      btn.addEventListener('keydown', e => {
        if (e.key === 'ArrowRight') {
          e.preventDefault();
          activateTab((idx + 1) % tabBtns.length, true);
        } else if (e.key === 'ArrowLeft') {
          e.preventDefault();
          activateTab((idx - 1 + tabBtns.length) % tabBtns.length, true);
        }
      });
    });

    revealAllInside(hubSection);

    return {
      hubSection,
      activateById(tabId) {
        if (Object.prototype.hasOwnProperty.call(tabIndexById, tabId)) {
          activateTab(tabIndexById[tabId], false);
        }
      }
    };
  }

  function appendSubFoot(panel, href, text, className, styleText) {
    const subFoot = document.createElement('div');
    subFoot.className = 'ny-hub-subfoot';
    const link = document.createElement('a');
    link.href = href;
    link.className = className || 'btn-outline';
    if (styleText) link.setAttribute('style', styleText);
    link.textContent = text;
    subFoot.appendChild(link);
    panel.appendChild(subFoot);
  }

  function streamlineZh() {
    const archEl = document.getElementById('home-arch');
    const contrastEl = document.getElementById('home-contrast');
    const capEl = document.getElementById('home-capabilities');
    const galEl = document.getElementById('home-gallery');
    const trustEl = document.getElementById('home-trust');

    const hub = buildHub(archEl, {
      headEyebrow: '核心机制 · 一屏看透',
      headDesc: '空间有距离、记忆归本机、对话接得住生活；点击下方页签按需切换细节。',
      tablistAria: '核心机制与能力展示切换',
      statusLink: {
        href: '#status-matrix',
        className: 'btn-outline',
        html: '已完成 / 未完成对照 <span class="arrow-nudge">→</span>'
      },
      tabs: [
        {
          id: 'bento',
          label: '四大机制',
          populate(panel) {
            const bentoGrid = archEl ? archEl.querySelector('.bento-grid') : null;
            const ecoStrip = archEl ? archEl.querySelector('.eco-strip') : null;
            if (bentoGrid) panel.appendChild(bentoGrid);
            if (ecoStrip) ecoStrip.remove();
          }
        },
        {
          id: 'contrast',
          label: '对话对比',
          populate(panel) {
            const shell = contrastEl ? contrastEl.querySelector('.contrast-shell') : null;
            if (shell) panel.appendChild(shell);
            appendSubFoot(panel, '#configuration', '人设与模型写法 →', 'btn-outline');
          }
        },
        {
          id: 'gallery',
          label: '小镇与人设',
          populate(panel) {
            if (!galEl) return;
            Array.from(galEl.querySelectorAll('.gallery-block')).forEach(block => {
              panel.appendChild(block);
            });
          }
        },
        {
          id: 'caps',
          label: '已实装清单 (8)',
          populate(panel) {
            const capGrid = capEl ? capEl.querySelector('.cap-grid') : null;
            if (capGrid) panel.appendChild(capGrid);
            appendSubFoot(panel, '#status-matrix', '查看完整「已完成 / 未完成」对照表 →', 'btn-outline');
          }
        }
      ]
    });

    if (hub) {
      const trustBar = trustEl ? trustEl.querySelector('.trust-verify-bar') : null;
      if (trustBar) {
        const factsWrap = trustBar.querySelector('.trust-verify-facts');
        if (factsWrap) {
          const byokPill = document.createElement('span');
          byokPill.className = 'trust-fact-pill is-static';
          const pillTxt = document.createElement('span');
          pillTxt.textContent = '自带密钥 · DeepSeek / OpenAI / Claude / Ollama';
          byokPill.appendChild(pillTxt);
          factsWrap.insertBefore(byokPill, factsWrap.firstChild);
        }
        const trustCard = document.createElement('div');
        trustCard.className = 'ny-hub-trust-bar';
        trustCard.appendChild(trustBar);
        archEl.appendChild(trustCard);
      }

      [contrastEl, capEl, galEl, trustEl].forEach(node => {
        if (node) node.remove();
      });
    }

    // Slim #home-evolution: keep Sunday countdown + top 2 releases visible
    const evoSection = document.getElementById('home-evolution');
    if (evoSection) {
      const evoStage = document.getElementById('evo-stage');
      const feed = document.getElementById('home-release-feed');
      const archivedRows = feed ? Array.from(feed.querySelectorAll('.release-row')).slice(2) : [];

      if ((evoStage || archivedRows.length > 0) && !document.getElementById('ny-evo-disclosure')) {
        const countLabel = archivedRows.length > 0 ? `与更早 ${archivedRows.length} 个版本记录` : '';
        const { details, body } = createDisclosure(
          'ny-evo-disclosure',
          `展开完整版本演进时间轴（v0.1 → v1.0 规划）${countLabel}`
        );

        if (evoStage) body.appendChild(evoStage);
        if (archivedRows.length > 0) {
          const archivedFeed = document.createElement('div');
          archivedFeed.className = 'release-feed is-archived';
          archivedRows.forEach(row => archivedFeed.appendChild(row));
          body.appendChild(archivedFeed);
        }

        const anchor = feed || document.getElementById('weekly-release-bar');
        if (anchor) {
          anchor.insertAdjacentElement('afterend', details);
        } else {
          evoSection.appendChild(details);
        }
      }
    }

    // Slim #home-faq
    const faqSection = document.getElementById('home-faq');
    if (faqSection) {
      const onboard = faqSection.querySelector('.onboard-steps');
      if (onboard) onboard.remove();
      const faqTitle = document.getElementById('faq-heading');
      if (faqTitle) faqTitle.textContent = '常见疑问解答';
    }

    // Fold SmartScreen + API Key setup grid in #home-downloads
    const guideGrid = document.getElementById('beginner-guide-grid');
    if (guideGrid && !document.getElementById('ny-guide-disclosure')) {
      const { details, body } = createDisclosure(
        'ny-guide-disclosure',
        '首次安装防卡壳：Windows 蓝框放行图解 & 3 分钟填密钥 / 连 Ollama 傻瓜指南'
      );
      guideGrid.parentNode.insertBefore(details, guideGrid);
      body.appendChild(guideGrid);
    }

    return {
      hub,
      anchorMap: {
        'home-contrast': 'contrast',
        'home-gallery': 'gallery',
        'home-capabilities': 'caps',
        'home-arch': 'bento',
        'home-trust': 'bento'
      }
    };
  }

  function streamlineEn() {
    const capEl = document.getElementById('en-capabilities');
    const contrastEl = document.getElementById('en-contrast');
    const galEl = document.getElementById('en-gallery');

    const headLink = capEl ? capEl.querySelector('.section-head > a.btn-outline') : null;
    if (headLink) headLink.remove();

    const hub = buildHub(capEl, {
      headEyebrow: 'Deterministic Backbone · One View',
      headDesc: 'Deterministic coordinates and local SQLite memory stay on disk while tabs below switch between capabilities, dialogue contrast, worlds & personas, and BYOK support.',
      tablistAria: 'Switch mechanism and showcase views',
      statusLink: {
        href: 'docs/?doc=status-matrix',
        className: 'btn-outline',
        html: 'Status Matrix <span class="arrow-nudge">→</span>'
      },
      tabs: [
        {
          id: 'caps',
          label: 'Capabilities (8)',
          populate(panel) {
            const capGrid = capEl ? capEl.querySelector('.cap-grid') : null;
            if (capGrid) panel.appendChild(capGrid);
          }
        },
        {
          id: 'contrast',
          label: 'Dialogue Contrast',
          populate(panel) {
            const shell = contrastEl ? contrastEl.querySelector('.contrast-shell') : null;
            if (shell) panel.appendChild(shell);
            appendSubFoot(panel, 'docs/?doc=configuration', 'Persona & Model Guide →', 'btn-outline');
          }
        },
        {
          id: 'gallery',
          label: 'Worlds & Personas',
          populate(panel) {
            if (!galEl) return;
            Array.from(galEl.children).forEach(child => {
              if (!child.classList.contains('section-head')) {
                panel.appendChild(child);
              }
            });
          }
        },
        {
          id: 'eco',
          label: 'Local & BYOK',
          populate(panel) {
            const eco = document.getElementById('en-ecosystem');
            if (eco) panel.appendChild(eco);
          }
        }
      ]
    });

    if (hub) {
      [contrastEl, galEl].forEach(node => {
        if (node) node.remove();
      });
    }

    const dlSection = document.getElementById('en-downloads');
    const guideGrid = dlSection ? dlSection.querySelector('.setup-guides-grid') : null;
    if (guideGrid && !document.getElementById('ny-guide-disclosure')) {
      const { details, body } = createDisclosure(
        'ny-guide-disclosure',
        'First-Launch Guide: Windows SmartScreen 2-Step Walkthrough & 2-Min API Key / Ollama Setup'
      );
      guideGrid.parentNode.insertBefore(details, guideGrid);
      body.appendChild(guideGrid);
    }

    return {
      hub,
      anchorMap: {
        'en-contrast': 'contrast',
        'en-gallery': 'gallery',
        'en-capabilities': 'caps',
        'en-ecosystem': 'eco'
      }
    };
  }

  function streamlineJa() {
    const howEl = document.getElementById('ja-how');
    const contrastEl = document.getElementById('ja-contrast');
    const capEl = document.getElementById('ja-capabilities');
    const galEl = document.getElementById('ja-gallery');

    const hub = buildHub(howEl, {
      headEyebrow: 'アーキテクチャと暮らしの骨格',
      headDesc: '座標・徒歩距離・温度湿度・SQLite記憶をすべてローカルに保ち、下のタブで対話比較・街と人物・8つの実装済み機能を切り替えられます。',
      tablistAria: '仕組み・対話比較・街と機能タブ',
      statusLink: {
        href: 'docs/?doc=status-matrix',
        className: 'magpie-btn-ghost',
        style: 'height:36px;padding:0 14px;font-size:0.84rem;',
        html: '実装ステータス表 →'
      },
      tabs: [
        {
          id: 'how',
          label: '仕組み',
          populate(panel) {
            const flow = howEl ? howEl.querySelector('.hub-flow-shell') : null;
            if (flow) panel.appendChild(flow);
          }
        },
        {
          id: 'contrast',
          label: '対話比較',
          populate(panel) {
            const shell = contrastEl ? contrastEl.querySelector('.contrast-shell') : null;
            if (shell) panel.appendChild(shell);
            appendSubFoot(
              panel,
              'docs/?doc=configuration',
              'SOUL.md 設定ガイド →',
              'magpie-btn-ghost',
              'height:36px;padding:0 14px;font-size:0.84rem;'
            );
          }
        },
        {
          id: 'gallery',
          label: '街と人物',
          populate(panel) {
            if (!galEl) return;
            Array.from(galEl.children).forEach(child => {
              if (!child.classList.contains('section-head')) {
                panel.appendChild(child);
              }
            });
          }
        },
        {
          id: 'caps',
          label: '実装済み (8)',
          populate(panel) {
            if (!capEl) return;
            Array.from(capEl.children).forEach(child => {
              if (!child.classList.contains('section-head')) {
                panel.appendChild(child);
              }
            });
          }
        }
      ]
    });

    if (hub) {
      [contrastEl, capEl, galEl].forEach(node => {
        if (node) node.remove();
      });
    }

    const dlSection = document.getElementById('ja-downloads');
    const guideGrid = dlSection ? dlSection.querySelector('.setup-guides-grid') : null;
    if (guideGrid && !document.getElementById('ny-guide-disclosure')) {
      const { details, body } = createDisclosure(
        'ny-guide-disclosure',
        '初回起動ガイド：Windows SmartScreen 2ステップ図解 ＆ API キー / Ollama 接続手順'
      );
      guideGrid.parentNode.insertBefore(details, guideGrid);
      body.appendChild(guideGrid);
    }

    return {
      hub,
      anchorMap: {
        'ja-how': 'how',
        'ja-contrast': 'contrast',
        'ja-gallery': 'gallery',
        'ja-capabilities': 'caps'
      }
    };
  }

  function installSmartAnchorRedirect(streamResult) {
    if (!streamResult || !streamResult.hub) return;
    const { hub, anchorMap } = streamResult;

    document.addEventListener('click', e => {
      const link = e.target && e.target.closest ? e.target.closest('a[href^="#"], a[data-home-scroll]') : null;
      if (!link) return;
      const rawHash = (link.getAttribute('data-home-scroll') || link.getAttribute('href') || '').replace(/^#/, '');
      if (!rawHash || !Object.prototype.hasOwnProperty.call(anchorMap, rawHash)) return;

      e.preventDefault();
      e.stopPropagation();
      hub.activateById(anchorMap[rawHash]);

      const headerOffset = LANG === 'zh' ? 76 : 74;
      const top = Math.max(0, hub.hubSection.getBoundingClientRect().top + window.scrollY - headerOffset);
      window.scrollTo({ top, behavior: 'smooth' });
    }, true);
  }

  function streamlineLowerSections() {
    const homeView = getHomeRoot();
    if (!homeView || homeView.dataset.nyStreamlined === '1') return;
    homeView.dataset.nyStreamlined = '1';

    let result = null;
    if (LANG === 'zh') result = streamlineZh();
    else if (LANG === 'en') result = streamlineEn();
    else result = streamlineJa();

    installSmartAnchorRedirect(result);
    triggerLayoutSync();
    setTimeout(triggerLayoutSync, 180);
  }

  function init() {
    mountHeroCollage();
    streamlineLowerSections();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
