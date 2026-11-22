const DOCS_TREE = [
  {
    "category": "はじめに・インストール",
    "items": [
      {
        "id": "intro",
        "title": "プロジェクト概要と主な機能"
      },
      {
        "id": "changelog",
        "title": "リリース情報とアップデート履歴",
        "badge": "NEW"
      },
      {
        "id": "quickstart",
        "title": "はじめての方へ：ここからスタート"
      },
      {
        "id": "installation",
        "title": "導入前の準備と動作環境"
      },
      {
        "id": "windows",
        "title": "Windows へのインストール",
        "badge": "デスクトップ"
      },
      {
        "id": "linux",
        "title": "Linux 環境および手動インストール"
      },
      {
        "id": "agent-install",
        "title": "AI アシスタントに任せて自動インストール"
      },
      {
        "id": "configuration",
        "title": "普段の起動・人物設定・モデル設定"
      },
      {
        "id": "weixin",
        "title": "WeChat 連携",
        "badge": "推奨"
      },
      {
        "id": "telegram",
        "title": "Telegram・QQ との連携"
      }
    ]
  },
  {
    "category": "機能と日常の使い方",
    "items": [
      {
        "id": "desktop-guide",
        "title": "デスクトップクライアント利用ガイド",
        "badge": "デスクトップ"
      },
      {
        "id": "daily-companions",
        "title": "共読・原稿・アルバムと生活リズム"
      },
      {
        "id": "world-editor",
        "title": "マップエディタとワールドパック"
      },
      {
        "id": "cli-reference",
        "title": "よく使うコマンドと記憶の管理"
      },
      {
        "id": "modules",
        "title": "独立推論とバックエンドサービス"
      }
    ]
  },
  {
    "category": "メンテナンスとロードマップ",
    "items": [
      {
        "id": "update",
        "title": "アップデートとワンコマンド・ロールバック"
      },
      {
        "id": "data",
        "title": "データディレクトリ、バックアップとアンインストール"
      },
      {
        "id": "troubleshooting",
        "title": "よくあるトラブルシューティング"
      },
      {
        "id": "status-matrix",
        "title": "実装済み・未実装ステータス一覧"
      },
      {
        "id": "roadmap",
        "title": "今後のロードマップ",
        "badge": "計画中"
      }
    ]
  },
  {
    "category": "プライバシーとオープンソース",
    "items": [
      {
        "id": "privacy",
        "title": "プライバシーとデータの境界線"
      },
      {
        "id": "testing",
        "title": "テスト範囲とリリースノート"
      },
      {
        "id": "release",
        "title": "オープンソースライセンスとサイト運用"
      },
      {
        "id": "contributing",
        "title": "不具合報告とコントリビュート"
      },
      {
        "id": "acknowledgments",
        "title": "謝辞"
      }
    ]
  }
];

const DOCS_CONTENT = {
  "intro": {
    "title": "プロジェクト概要と主な機能",
    "summary": "nyairo の紹介：AI 個体が落ち着いて暮らせる空間、手元の PC に残る長期記憶、そして現実と同じ時間の流れで進む日常をお届けします。",
    "toc": [
      {
        "id": "intro-section-0",
        "text": "なぜ nyairo を作るのか？"
      },
      {
        "id": "intro-section-1",
        "text": "現在できること"
      },
      {
        "id": "intro-section-2",
        "text": "始める前に必要なもの"
      }
    ],
    "content": "<div class=\"callout callout-info\"><div class=\"callout-title\">フレームワークとキャラクター設定について</div><p>nyairo はオープンソース（Apache-2.0）の AI 個体フレームワークです。初期インストール時は特定のキャラクターを持たないニュートラルな状態になっているため、名前や口調、部屋のしつらえまで自由に設定し、ふたりならではの日常を少しずつ育てていくことができます。</p></div>\n\n<h2 id=\"intro-section-0\">なぜ nyairo を作るのか？</h2>\n<p>一般的な AI チャットツールでは、ウィンドウを閉じた瞬間に相手の時間まで止まってしまい、次に開いたときにはまた初対面のような挨拶からやり直しになります。「どこかへ出かける」と言っても一瞬で瞬間移動し、話し終えればすぐに忘れてしまうため、画面の向こうで生きたパートナーが暮らしているとは感じにくいのが現状です。</p>\n<p><strong>nyairo が解決したいのは、まさにこの点です。</strong>ウィンドウを閉じたあともパートナー自身の時間が流れ続け、歩き回れる部屋や街があり、交わした出来事はあなた自身の PC にしっかりと保存されます。ひとつひとつの会話が、前回別れたあとの暮らしの延長線上に自然につながっていきます。</p>\n\n<h2 id=\"intro-section-1\">現在できること</h2>\n<div class=\"table-scroll\">\n<table class=\"doc-table\">\n<thead>\n<tr>\n<th scope=\"col\" style=\"width: 22%;\">モジュール</th>\n<th scope=\"col\" style=\"width: 50%;\">実際の働き</th>\n<th scope=\"col\" style=\"width: 28%;\">現在のステータス</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td><strong>ローカル長期記憶と二層メモリーランタン</strong></td>\n<td>会話の中で交わした習慣や約束、出来事をローカルの SQLite データベースに記録し、二層メモリーランタン機構によって会話原文の改ざんを防ぎます。覚え間違いはその場で訂正でき、思い出したくない話題を呼び出し停止（ミュート）にすることも可能です。</td>\n<td>標準で有効 · 原文は手元の PC に保存</td>\n</tr>\n<tr>\n<td><strong>途切れない暮らしと対等な距離感</strong></td>\n<td>ウィンドウを閉じたり PC を再起動したりしても、現在地や作業中の状態はリセットされません。便利な道具として言いなりになるのではなく、気が進まない頼みごとは接客のような定型謝罪なしで率直に断り、根にも持ちません。実際の予定に沿って数時間おきに非同期で返信したり、睡眠・外出・集中時間中の急がない用件はスマホ返信トレイに保留し、本当に急ぎのときはノックして呼び出したりできます。</td>\n<td>基本の連続性は標準で有効 · 自律行動は任意で有効化</td>\n</tr>\n<tr>\n<td><strong>街区の店舗と温度・湿度の物性</strong></td>\n<td>外出時は道路グラフの距離に応じて実際に時間が経過し、12業種の店舗やオープンカウンターで自由に注文できます（コンビニで温かいおでんを買うなど）。古書店や喫茶店の店主は時間差の生活リズムで営業しており、カウンターのコースターに伝言を残すことも可能です。持ち物バッグは最大3階層のネスト収納に対応し、机の上の温かいお茶が自然に冷めたり、窓辺のメモが雨気で微かに湿って滲んだりといった温度・湿度の物性を備えています。</td>\n<td>テンプレート不要の完全ランダム専用ワールド + 7つの内蔵ワールド + ダーク／ライト両対応オフラインエディタ</td>\n</tr>\n<tr>\n<td><strong>共読・原稿・引越しスーツケース</strong></td>\n<td>旅先のポストカードや日々のメモを残せるほか、同じ本の同期読書・余白メモや原稿の差分比較に対応しています。ロスレス引越しスーツケース <code>.nyairo-suitcase.zip</code> を使えば、ワールドの断面データや移行差分レポートごと丸ごと別の環境へ持ち運べます。</td>\n<td>デスクトップ版内でそのまま利用可能</td>\n</tr>\n<tr>\n<td><strong>独立した分析と提案</strong></td>\n<td>迷っている課題やコードのデバッグがあるときは、専用コマンドでメリット・デメリットや考え方を別に整理してもらい、分析が終わったらそのまま日常会話に戻ることができます。</td>\n<td>必要時に使用（<code>/chiyo_consider</code>）</td>\n</tr>\n</tbody>\n</table>\n</div>\n\n<h2 id=\"intro-section-2\">始める前に必要なもの</h2>\n<ul>\n<li><strong>一般的な PC 1台</strong>：Windows 10 / 11（システム内蔵の WSL 2 と連携して動く約 6.0 MB・管理者権限不要のデスクトップ版を用意しています）または Linux に対応しています。会話の推論処理は設定したモデル API 側で行うため、<strong>専用 GPU は不要</strong>で、普段使いの薄型ノート PC でも快適に動作します。</li>\n<li><strong>LLM の API キー（または無料のローカル Ollama）</strong>：初めての方は DeepSeek オープンプラットフォーム（<code>platform.deepseek.com</code>）で 5〜10 元（数百円程度）をチャージして <code>sk-...</code> キーを作成するか、PC 上の無料 <code>Ollama</code>（<code>http://127.0.0.1:11434</code>）を直接接続して、外部通信なしの完全ローカル対話を楽しめます。</li>\n<li><strong>数分のインストール時間</strong>：初回セットアップ時に実行環境と検証用パッケージを自動取得するため、安定したインターネット接続があればすぐ完了します。</li>\n</ul>\n<p>準備が整ったら、<a href=\"#quickstart\">はじめての方へ：ここからスタート</a> へお進みください。</p>"
  },
  "changelog": {
    "title": "リリース情報とアップデート履歴",
    "summary": "各バージョンの変更点・修正内容・現時点の未対応範囲をありのまま記録しています。現在の最新パブリックベータ版は v0.8.0 で、過去のタグやインストーラーもすべて GitHub Releases に保持されています。",
    "toc": [
      {
        "id": "changelog-v0-8-0",
        "text": "v0.8.0 · 個人スキル帳・モデル切替比較・深い自律性と部屋の記録"
      },
      {
        "id": "changelog-v0-7-0",
        "text": "v0.7.0 · より節度あるパートナーらしい暮らしへ"
      },
      {
        "id": "changelog-v0-6-0",
        "text": "v0.6.0 · 日常の行動レシート・未来の質問と共読体験"
      },
      {
        "id": "changelog-v0-5-1",
        "text": "v0.5.1 · 身体リズム・朝晩の街並み・デスクトップ版"
      },
      {
        "id": "changelog-v0-4-0",
        "text": "v0.4.0 · 複数区間の移動とタスクの一時保留"
      },
      {
        "id": "changelog-v0-3-0",
        "text": "v0.3.0 · 外出・買い物と途切れない行動ループ"
      },
      {
        "id": "changelog-v0-2-0-rc3",
        "text": "v0.2.0-rc3 · チャットからの移動指示と移動中の経過認識"
      },
      {
        "id": "changelog-v0-2-0-rc2",
        "text": "v0.2.0-rc2 · インストール・アップグレード用メタデータの修正"
      },
      {
        "id": "changelog-v0-2-0-rc1",
        "text": "v0.2.0-rc1 · 自律的な日常とマップエディタ"
      },
      {
        "id": "changelog-v0-1-0-rc9",
        "text": "v0.1.0-rc9 · Agent 支援インストールとコミュニティ窓口"
      },
      {
        "id": "changelog-v0-1-0-rc6",
        "text": "v0.1.0-rc6 · 1コマンドでの更新とロールバック"
      },
      {
        "id": "changelog-v0-1-0-rc5",
        "text": "v0.1.0-rc5 · カスタム人物設定とプライバシー強化"
      },
      {
        "id": "changelog-v0-1-0-rc4",
        "text": "v0.1.0-rc4 · 初のパブリックプレビュー版"
      }
    ],
    "content": "<div class=\"callout callout-info\"><div class=\"callout-title\">バージョンについて</div><p>現在の最新リリースは <strong>v0.8.0</strong>（GitHub prerelease 公開テスト版であり、正式安定版ではありません）で、ネイティブ Windows デスクトップインストーラー（<code>nyairo-0.8.0-setup.exe</code>）も提供しています。これまでに公開した <code>v0.7.0</code>、<code>v0.6.0</code>、<code>v0.5.1</code>、<code>v0.4.0</code>、<code>v0.3.0</code>、<code>v0.2.0-rc3</code> などの過去タグやアーカイブもそのまま保持されています。アップグレード前には、念のためご自身の <code>~/.nyairo</code> ディレクトリをバックアップしておくことをおすすめします。</p></div>\n\n<h2 id=\"changelog-v0-8-0\">v0.8.0 · 個人スキル帳・モデル切替比較・深い自律性と部屋の記録（GitHub prerelease）</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">パブリックベータ版 · スキルの由来とモデル変更後の再検証、気質の細かな変化比較、古い約束の取捨選択、共有お気に入りの鮮度管理、部屋の5項目カルテ</p>\n<ul>\n<li><strong>「各自で選んでから公開」双方向ブラインド選択と迎合しない本音の記録</strong>：SHA-256 ハッシュで互いの選択を封印し（公開前は厳格にマスク、公開時に改ざん検証）、相手案を採用する際も元の懸念理由を消し去らず残せます。</li>\n<li><strong>外部 LLM「AI アイデア入荷」と空回りゼロの約束駆動タウンエンジン</strong>：外部 LLM を使った店頭ラインナップの動的入荷と無料オフラインモードの切り替えに対応し、待機ティック中はディスク書込ゼロ・消費トークンゼロを保ちます。</li>\n<li><strong>日用品リユースの物理占有ルール、白紙から育つ自作分類棚と 3 連インタラクティブカード</strong>：ペン立てに転用したマグカップでお茶を淹れられない物理制約や白紙スタートの分類棚を実装し、ホーム 3 連カードとポケットスマホが追加通信なしで連携します。</li>\n<li><strong>個人スキル帳とモデル変更後の再確認</strong>：自ら身につけた技能、モデル本来の能力、ローカルツール活用、資料を参照した実践を明確に区別します。モデル切替時の「要再確認」フラグをワンクリックで立てられ、失敗時の具体的な改善方法や評価基準バージョンも記録できます。</li>\n<li><strong>気質の細かな変化とモデル切替比較テスト</strong>：サンプル数の信頼度を踏まえて長期的な気質の変化を穏やかに追跡し、パートナーによるさりげない観察メモの記録や、モデル変更後の比較再テストシートの発行に対応しました。</li>\n<li><strong>検証による見解更新・暮らしのルール実績・古い約束の取捨選択</strong>：確認を経て更新された見解、暮らしの小さなルールが実際に防いだ予定、そして以前の約束と新しい考えが衝突した際の穏やかな見直し提案をわかりやすく表示します。</li>\n<li><strong>共有リズムの自発的提案・部屋の5項目確認カルテ・共有お気に入りの鮮度管理</strong>：パートナー側から共有習慣を軽くしたり貸し借りなしで休止したりする提案ができるようになりました。部屋の6つのインタラクティブコーナーすべてに由来・移動履歴の5項目確認カルテを用意し、共有お気に入りには「新鮮・有効」「見返し済み」「情報源が古い可能性」「折りたたむ」のラベルを付けられます。</li>\n</ul>\n<p>関連リンク：<a href=\"https://github.com/L1AN929/nyairo/releases/tag/v0.8.0\" target=\"_blank\" rel=\"noopener noreferrer\">GitHub Release v0.8.0</a> · <a href=\"#windows\">Windows デスクトップ版のインストール</a></p>\n\n<h2 id=\"changelog-v0-7-0\">v0.7.0 · 完全ランダムワールド・オープン店舗・対等な境界線・ロスレス引越し（GitHub prerelease）</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">パブリックベータ版 · テンプレート不要のランダム生成、12業種の自由注文、自律的なお断りと非同期返信、バッグ・物性エンジン、端末間の丸ごと引越し</p>\n<p>このバージョンでは、空間の自由度、日常の息づかいを感じる距離感、物理的な状態変化、そしてロスレスな引越し体験を一つの流れとして完成させました。</p>\n<ul>\n<li><strong>テンプレート不要の完全ランダム専用ワールドとダーク／ライト両対応オフラインエディタ</strong>：7つの内蔵ワールドと4種類のテーマスタイルに加え、テンプレート選択なしで世界にひとつの専用ワールドをワンクリック生成できるようになりました（6種の地形ミックス、4種の非均一道路ネットワーク、8業種の商業店舗と複数部屋の自宅）。オフライン視覚ワールドエディタも刷新され、ワークスペースのダーク／ライト表示切替に対応しています。</li>\n<li><strong>12業種の店舗オープン注文と店主の時間差日常</strong>：12業種の街区店舗やオープンカウンターでの自由注文（コンビニへ歩いて温かいおでんを買うなど）に対応しました。古書店や喫茶店の店主はそれぞれの時間差リズムで出勤・営業しており、カウンターにコースター伝言を残すこともできます。</li>\n<li><strong>便利屋扱いされない自律的な境界線と実日程に沿った非同期返信</strong>：引き受けたくない依頼は接客のような定型謝罪テンプレートを使わずきっぱり断り、あとまで引きずることもありません。実際の予定に沿って数時間後に非同期で返信したり、睡眠・外出・集中時間中の急がない連絡をスマホ返信トレイに保留し、急用のときはノックして呼び出したりできます。</li>\n<li><strong>持ち物バッグの3階層ネスト収納と温度・湿度の物性</strong>：持ち物バッグや収納ボックスは最大3階層のネスト収納と厳密な容器可視性ルールに対応しました。アイテムにはリアルな温度・湿度の状態変化があり、机のそばの温かいお茶は自然に冷め、窓辺の紙片は雨に当たるとわずかに湿って文字が滲みます。</li>\n<li><strong>二層メモリーランタンとロスレス引越しスーツケース</strong>：二層メモリーランタンにより会話原文の改ざんを厳密に防ぎます。ロスレス引越しスーツケース <code>.nyairo-suitcase.zip</code> を使えば、カスタム／ランダムワールドの断面データと移行差分レポートごと丸ごと持ち出せます（API キーとローカルパスは自動で除外され、インポート前に2段階の安全チェックを行います）。</li>\n<li><strong>1日あたりのモデル使用量上限とデスクトップ版の堅牢化</strong>：初期設定で1日 50,000 Token の予算上限とウェイクアップチケットの照合による使いすぎ防止を導入しました。Windows デスクトップ版の導入ガイドとゲートウェイの排他保護もあわせて強化しています。</li>\n</ul>\n<p>関連リンク：<a href=\"https://github.com/L1AN929/nyairo/releases/tag/v0.7.0\" target=\"_blank\" rel=\"noopener noreferrer\">GitHub Release v0.7.0</a> · <a href=\"#windows\">Windows デスクトップ版のインストール</a></p>\n\n<h2 id=\"changelog-v0-6-0\">v0.6.0 · 日常の行動レシート・未来の質問と共読体験（GitHub prerelease）</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">パブリックベータ版 · 日常のやり取りとデスクトップ版体験のアップデート</p>\n<p>このバージョンでは、日々の会話、街の散歩、ふたりでの共読、そしてデスクトップクライアントの細かな使い心地を重点的に磨き上げました。</p>\n<ul>\n<li><strong>会話中の生活ステータスと行動レシート</strong>：チャット画面の上部に、パートナーが今いる部屋や通り、姿勢、手元の作業状況がリアルタイムで表示されます。会話の中で散歩に出たりエッセイを書いたりしおりを挟んだりする実際の行動が発生すると、吹き出しの下にクリックできるカプセルボタンが現れ、対応するマップや原稿へワンクリックで移動できます。</li>\n<li><strong>未来の質問と習慣トピック管理</strong>：デスクトップ版の「記憶 → 暮らしと実践」から、パートナーがあとで聞きたがっている質問を手動でメモしたり、その場で答えたり取り消したりできるようになりました。よく話題にする約束事も習慣トピックとして蓄積し、「概要」ページからワンクリックで会話に呼び出せます。</li>\n<li><strong>共読サンプルブックと初回ガイド</strong>：共読本棚に『凪浦海風手記（共読サンプル）』をワンクリックで追加して、ページめくり・しおり・ページごとの余白メモをすぐに試せるようになりました。新しいワールドでは、起床・水分補給・エッセイ執筆・書店巡りの4ステップの軽やかな案内が用意されています。</li>\n<li><strong>モデル API のワンクリック接続テスト</strong>：デスクトップ版の「設定」ページに LLM API の疎通テストボタンを追加しました。応答時間やエラー原因がその場で返ってくるため、手探りで原因を探す手間が省けます。</li>\n</ul>\n<p>関連リンク：<a href=\"https://github.com/L1AN929/nyairo/releases/tag/v0.6.0\" target=\"_blank\" rel=\"noopener noreferrer\">GitHub Release v0.6.0</a> · <a href=\"#windows\">Windows デスクトップ版のインストール</a></p>\n\n<h2 id=\"changelog-v0-5-1\">v0.5.1 · 身体リズム・朝晩の街並み・デスクトップ版（GitHub prerelease）</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">パブリックベータ版 · 分割テストおよびリリースマニフェスト検証完了 · Windows デスクトップ版同時公開</p>\n<p>このバージョンでは、日々の生活リズム、街の細部、アルバムや原稿、そしてデスクトップクライアントに関する開発成果を、そのままインストールして試せるテスト版としてまとめました。</p>\n<ul>\n<li><strong>Windows ネイティブデスクトップクライアント</strong>：管理者権限不要の約 5 MB インストーラー（<a href=\"#windows\">インストール手順</a>）を新たに追加しました。システムトレイに常駐し、初回起動時に WSL 2 を自動検出して環境構築を案内します。概要、ストリーミング引用対話、街と室内の見取り図、原稿差分比較、WeChat QR スキャンパネルを内蔵しています。</li>\n<li><strong>生活と補給のリズム</strong>：眠気・休息、水分補給、軽食の時間リズムを追加し、それぞれの動作が実際の所要時間に沿って進行するようになりました（現時点では軽量なリズム管理であり、本格的な空腹度・栄養数値システムではありません）。</li>\n<li><strong>朝晩で変わる街の表情と読める風景</strong>：同じ通りでも朝と夕方・夜で異なる雰囲気を見せるようになり、道端の設備や読書コーナーに立ち止まって詳しい内容を読むことができます。</li>\n<li><strong>アルバム・日記・原稿の書き継ぎ</strong>：カメラでの写真撮影、アルバムの閲覧、ふたりでの共読メモに加え、書きかけ原稿の一時保留・再開や過去バージョンとの差分比較を揃えました。</li>\n<li><strong>ビルド整理と機密情報マスキング</strong>：インストーラーのバージョン番号と記憶モジュールの明示的な設定を統一し、公開ソースコード内の内部テスト用アドレスを完全に除去しました。</li>\n</ul>\n<div class=\"callout callout-info\"><div class=\"callout-title\">v0.5.1 公開当時の検証範囲メモ</div><p>機能別の分割テスト、9項目のマスキング済みオフライン検査、<code>MANIFEST.json</code> と ZIP ハッシュの照合、およびデスクトップ版の実機結合テストを完了しています。このリリース時点では、過去4万件以上のホスト側全量回帰テストの再実行や、実 LLM を接続した24時間連続稼働テストは未実施でした（なお、当時未統合だった完全ランダムワールドと引越しスーツケースはその後の <code>v0.7.0</code> で実装済みです）。</p></div>\n<p>関連リンク：<a href=\"https://github.com/L1AN929/nyairo/releases/tag/v0.5.1\" target=\"_blank\" rel=\"noopener noreferrer\">GitHub Release v0.5.1</a> · <a href=\"#windows\">Windows デスクトップ版のインストール</a></p>\n\n<h2 id=\"changelog-v0-4-0\">v0.4.0 · 複数区間の移動とタスクの一時保留（GitHub prerelease）</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">パブリックベータ版 · ワールド・身体基盤の専用テスト 263 項目に合格</p>\n<p>0.3 で実現した単発外出の行動ループをベースに、0.4 では「少し遠い場所へ複数区間に分けて歩くこと」と「複数の用事を切り替えながら進めること」を解決しました。</p>\n<ul>\n<li><strong>複数区間の連続移動</strong>：離れた場所へ向かう場合、自動で複数の区間（例：寝室 → リビング → 通り → 書店）に分割して順番に進みます。各区間ごとに進捗が保存されるため、途中で足を止めても位置が飛びません。</li>\n<li><strong>複数タスクの保留と単一集中</strong>：今すぐ終わらない用事があれば、待機条件をメモして一時保留し、別の用事を先に済ませられます。条件が満たされた時点で続きを再開でき、表で同時に走る行動は常にひとつに保たれます。</li>\n<li><strong>日をまたぐ天気と街の出来事</strong>：天候の移り変わりやご近所のちょっとした出来事が数日間にわたって段階的に変化し、期間を過ぎると自然に区切りがついて無限ループしなくなりました。</li>\n<li><strong>より明確なステータス応答</strong>：異なる行動間でアクション番号がまれに混線する不具合を修正しました。また、「操作が拒否された」場合と「結果が一時的に不明」な場合を明確に区別し、同じエラーとして混同しないようにしました。</li>\n<li><strong>原稿のバージョン比較</strong>：管理下の原稿に過去バージョンの一覧と追加・削除の差分ビューを追加し、書き継ぐ際には前バージョンの要約を自動で参照するようにしました。</li>\n</ul>\n\n<h2 id=\"changelog-v0-3-0\">v0.3.0 · 外出・買い物と途切れない行動ループ（GitHub prerelease）</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">パブリックベータ版 · 8グループ 51項目のコア機構チェックに合格</p>\n<p>このバージョンでは、外出から道中の待機、急な予定変更、店での買い物、そして帰宅して元の作業に戻るまでを、一時停止や再開が可能なひとつの連続したプロセスとして結びつけました。</p>\n<ul>\n<li><strong>実際の道路グラフに基づく経路探索</strong>：現在のマップから場所や部屋の扉・出入口を直接解決し、名称が曖昧な場合や道がつながっていない場合はその旨を正直に伝えるようにし、固定値による瞬間移動を廃止しました。</li>\n<li><strong>途中一時停止でもやり直しなし</strong>：一時停止の指示はこれから出発する次の区間だけを止め、すでに歩き始めている区間はきちんと歩ききってから停止し、再開時はその地点から歩き続けます。</li>\n<li><strong>通信の揺らぎによる二重実行を防止</strong>：アクション送信後に応答が返らなかった場合、まず台帳の結果を照合することで、不用意な再送で同じ行動を2回実行してしまうのを防ぎます。</li>\n<li><strong>1日あたりのモデル呼び出し予算</strong>：日単位および1回の実行ウィンドウ単位でリクエスト上限を設定でき、一時停止中に裏側でこっそり枠を消費することはありません。</li>\n<li><strong>店舗での買い物と体験の記録</strong>：店で購入した飲み物や読み物は実際に消費・記録され、その後の会話や選択にも直前の出来事として反映されます。数日先までの店舗営業時間スケジュール照会にも対応しました。</li>\n</ul>\n\n<h2 id=\"changelog-v0-2-0-rc3\">v0.2.0-rc3 · チャットからの移動指示と移動中の経過認識</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">公開日：2026年10月14日</p>\n<ul>\n<li><strong>ひと言で外へ出かける</strong>：移動ツール <code>world_move</code> を追加しました。ターミナル、WeChat、Telegram からコンビニや公園へ行くようそのまま話しかけるだけで、隣接していない場所へも通りを経由して自動で向かいます。</li>\n<li><strong>リアルな徒歩所要時間</strong>：各区間は距離に応じて5〜13分程度の徒歩時間がかかり、移動の途中でメッセージを送って尋ねると、到着まであと何分くらいかを正確に答えてくれます。</li>\n<li><strong>権限許可とテスト整備</strong>：記憶のセキュリティポリシーが移動ツールを誤ってブロックしてしまう不具合を修正し、16項目の専用テストとファイルマニフェスト検証を追加しました。</li>\n</ul>\n\n<h2 id=\"changelog-v0-2-0-rc2\">v0.2.0-rc2 · インストール・アップグレード用メタデータの修正</h2>\n<p>rc1 アーカイブ内の更新登録バージョン番号が古いままになっていた問題を修正し、バージョン不一致を防ぐ自動パッケージ検証スクリプトを追加するとともに、Telegram 連携ガイドのコマンド誤記を訂正しました。</p>\n\n<h2 id=\"changelog-v0-2-0-rc1\">v0.2.0-rc1 · 自律的な日常とマップエディタ</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">公開日：2026年10月13日</p>\n<ul>\n<li><strong>バックグラウンド自律リズム（初期状態オフ）</strong>：有効にすると、話しかけられていない間も自分のリズムで目を覚まし、周囲を見回したり散歩したり、書きかけのエッセイを書き継いだりします。</li>\n<li><strong>確認できた結果だけを記録</strong>：LLM が「保存したと口では言いながら実際には書き込めていない」状態を防ぐため、下層サービスで書き込み成功が確認できた場合のみ台帳へ記録し、曖昧な状態は正直に「不確定」として扱います。</li>\n<li><strong>オフラインマップエディタ標準同梱</strong>：視覚的な空間エディタと複数の街テンプレートを同梱しており、ブラウザ上で壁を描いたり扉を取り付けたり家具を配置したりできます。</li>\n</ul>\n\n<h2 id=\"changelog-v0-1-0-rc9\">v0.1.0-rc9 · Agent 支援インストールとコミュニティ窓口</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">公開日：2026年10月10日</p>\n<ul>\n<li><strong>AI Agent 支援インストール仕様</strong>：<a href=\"../../AGENT_INSTALL.zh-CN.md\">AGENT_INSTALL.zh-CN.md</a> を公開しました。標準プロンプトを Claude Code や Codex などのツールへコピーするだけで、環境の事前確認、インスタンス分離、SHA256 検証付きインストールまで自動で進められます。</li>\n<li><strong>コミュニティ窓口</strong>：公式 QQ 交流グループ（<code>1126943805</code>）および Telegram チャンネル（<code>@nyairoai</code>）を開設し、不具合報告や設定のノウハウ交換をしやすくしました。</li>\n<li><strong>ドキュメントサイトの体裁調整</strong>：公式サイトのファーストビューとドキュメントの閲覧レイアウトを改善しました。</li>\n</ul>\n\n<h2 id=\"changelog-v0-1-0-rc6\">v0.1.0-rc6 · 1コマンドでの更新とロールバック</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">公開日：2026年10月6日</p>\n<ul>\n<li><strong>丸ごとスムーズなアップグレード</strong>：<code>nyairo update</code> を追加しました。<code>~/.local/share/nyairo</code> 配下のプログラム本体のみを置き換え、<code>~/.nyairo</code> 内の会話履歴、記憶、API キー、<code>SOUL.md</code> はそのまま保持されます。</li>\n<li><strong>ワンコマンドのロールバックと中断復旧</strong>：更新後に互換性の問題が出た場合は、<code>nyairo update --rollback</code> で前のバージョンへ戻せます（その間の新しい会話も失われません）。通信切断で中断した場合は <code>nyairo update --recover</code> で修復できます。</li>\n</ul>\n\n<h2 id=\"changelog-v0-1-0-rc5\">v0.1.0-rc5 · カスタム人物設定とプライバシー強化</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">公開日：2026年10月4日</p>\n<ul>\n<li><strong>人物テンプレートの分離</strong>：新規インスタンスでは初期状態としてクリーンでニュートラルな <code>SOUL.md</code> テンプレートを採用し、名前・呼び方・性格を自由に設定しやすくしました。</li>\n<li><strong>短い会話の文脈漏れ修正とツール制限</strong>：短いやり取りで文脈がまれに抜け落ちる不具合を修正しました。また、記憶モード有効時は任意のファイル読み取りツールを初期状態で制限し、モデルが「呼び出し停止」設定を迂回して過去ログを覗き見るのを防いでいます。</li>\n</ul>\n\n<h2 id=\"changelog-v0-1-0-rc4\">v0.1.0-rc4 · 初のパブリックプレビュー版</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">公開日：2026年10月2日</p>\n<p>ローカル SQLite 長期記憶、再起動をまたいで保持される生活状態、そして初期の位置・姿勢認識インターフェースなど、nyairo の基本骨格を構築しました。</p>"
  },
  "quickstart": {
    "title": "はじめての方へ：ここからスタート",
    "summary": "お使いの PC に合わせた一番手軽な導入方法を選べます。Windows ならデスクトップ版をインストールするだけで始められ、初心者の方でも数百円の DeepSeek キーまたは無料のローカル Ollama を繋げばすぐに対話を開始できます。",
    "toc": [
      {
        "id": "quickstart-section-0",
        "text": "まず環境に合った導入方法を選ぶ"
      },
      {
        "id": "quickstart-smartscreen",
        "text": "Windows 初回起動時に「Windows によって PC が保護されました」と出た場合の2ステップ図解"
      },
      {
        "id": "quickstart-guided-0",
        "text": "Linux / WSL のワンライナー導入"
      },
      {
        "id": "quickstart-model-beginner",
        "text": "初心者向けモデル設定：数百円の DeepSeek キーまたは無料 Ollama"
      },
      {
        "id": "quickstart-section-1",
        "text": "インストール後のチャット方法"
      },
      {
        "id": "quickstart-section-2",
        "text": "パートナーに名前を付け、性格を設定する"
      }
    ],
    "content": "<h2 id=\"quickstart-section-0\">まず環境に合った導入方法を選ぶ</h2>\n<p>始める前に必要なものは、インターネットにつながる PC 1台と LLM API（数百円チャージの DeepSeek API キー、または PC 上の無料 Ollama のどちらでも可）だけです。お使いの OS に合わせてお選びください。</p>\n<ul>\n<li><strong>Windows 10 / 11 をお使いの場合（推奨）</strong>：約 6.0 MB の <a href=\"../../downloads/nyairo-0.8.0-setup.exe\" download=\"nyairo-0.8.0-setup.exe\"><strong>Windows デスクトップ版インストーラー</strong></a> をダウンロードしてダブルクリックするだけで、WSL 2 を自動検出し、バックグラウンド環境の構築まで案内してくれます。ターミナルコマンドを覚える必要はありません。詳しい手順は <a href=\"#windows\">Windows へのインストール</a>、画面の使い方は <a href=\"#desktop-guide\">デスクトップクライアント利用ガイド</a> をご覧ください。</li>\n<li><strong>Linux をお使いの場合、またはターミナル操作に慣れている場合</strong>：すぐ下のワンライナー導入手順へお進みください。</li>\n<li><strong>普段 Claude Code や Codex などのコーディングアシスタントを使っている場合</strong>：<a href=\"#agent-install\">AI アシスタントに自動導入を任せる</a> にある標準プロンプトをそのままコピーして渡すだけで導入を代行してもらえます。</li>\n</ul>\n\n<h2 id=\"quickstart-smartscreen\">Windows 初回起動時に「Windows によって PC が保護されました」と出た場合の2ステップ図解</h2>\n<p>nyairo は個人・独立系のオープンソースプロジェクトであり、高額な商用 EV コード署名証明書をまだ取得していないため、初回に <code>nyairo-0.8.0-setup.exe</code> をダブルクリックした際、Windows SmartScreen の通常の確認画面が表示されることがあります。以下の2ステップでそのまま開けます（管理者権限は不要です）：</p>\n<ol>\n<li>まず画面の案内文の下にある「<strong>詳細情報</strong>」をクリックしてインストーラーの詳細を展開します。</li>\n<li>念のためインストーラーの SHA256 が公式サイト公開値の <code>36ed8ef7d4aabeb86e1a55fff298eaa2df4a995cd603b845bb885cf81ef76eb8</code> と一致することを確認し、右下に現れる「<strong>実行</strong>」をクリックするとセットアップウィザードへ進みます。</li>\n</ol>\n<div class=\"smartscreen-guide\" style=\"margin: 16px 0 20px;\">\n<svg viewBox=\"0 0 680 232\" role=\"img\" aria-label=\"Windows SmartScreen の2ステップ許可図解：ステップ1で詳細情報を押し、ステップ2で SHA256 を確認して実行を押す\" style=\"width: 100%; max-width: 680px; height: auto; display: block; color: var(--fg);\">\n  <rect x=\"4\" y=\"6\" width=\"312\" height=\"220\" rx=\"10\" fill=\"var(--card)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <line x1=\"4\" y1=\"38\" x2=\"316\" y2=\"38\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"20\" y=\"26\" fill=\"var(--accent)\" font-size=\"11\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">STEP 01 · 「詳細情報」を押す</text>\n  <g style=\"color: var(--muted);\" opacity=\"0.7\">\n    <line x1=\"292\" y1=\"18\" x2=\"300\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n    <line x1=\"300\" y1=\"18\" x2=\"292\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n  </g>\n  <text x=\"22\" y=\"64\" fill=\"var(--fg)\" font-size=\"14\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">Windows によって PC が保護されました</text>\n  <text x=\"22\" y=\"88\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">Microsoft Defender SmartScreen が起動を停止</text>\n  <text x=\"22\" y=\"106\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">認識されないアプリ（商用署名なし OSS 版）</text>\n  <rect x=\"20\" y=\"122\" width=\"84\" height=\"26\" rx=\"6\" fill=\"var(--accent-soft)\" stroke=\"var(--accent)\" stroke-width=\"1.4\"/>\n  <text x=\"62\" y=\"139\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"12\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">詳細情報</text>\n  <g style=\"color: var(--accent);\">\n    <path d=\"M138 135H112m6-5-6 5 6 5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n  </g>\n  <text x=\"144\" y=\"139\" fill=\"var(--accent)\" font-size=\"11.5\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">1. ここを押して展開</text>\n  <rect x=\"222\" y=\"182\" width=\"78\" height=\"28\" rx=\"5\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"261\" y=\"200\" text-anchor=\"middle\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">実行しない</text>\n  <g style=\"color: var(--accent);\">\n    <path d=\"M325 116h28m-6-6 6 6-6 6\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n  </g>\n  <rect x=\"364\" y=\"6\" width=\"312\" height=\"220\" rx=\"10\" fill=\"var(--card)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <line x1=\"364\" y1=\"38\" x2=\"676\" y2=\"38\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"380\" y=\"26\" fill=\"var(--accent)\" font-size=\"11\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">STEP 02 · 「実行」を押す</text>\n  <g style=\"color: var(--muted);\" opacity=\"0.7\">\n    <line x1=\"652\" y1=\"18\" x2=\"660\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n    <line x1=\"660\" y1=\"18\" x2=\"652\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n  </g>\n  <text x=\"382\" y=\"64\" fill=\"var(--fg)\" font-size=\"14\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">Windows によって PC が保護されました</text>\n  <rect x=\"382\" y=\"78\" width=\"276\" height=\"66\" rx=\"6\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"394\" y=\"98\" fill=\"var(--fg-2)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">アプリ: nyairo-0.8.0-setup.exe</text>\n  <text x=\"394\" y=\"116\" fill=\"var(--muted)\" font-size=\"11\" font-family=\"var(--font-sans, sans-serif)\">発行元: 不明な発行元（公式ハッシュ一致で安全）</text>\n  <text x=\"394\" y=\"134\" fill=\"var(--accent)\" font-size=\"10.5\" font-family=\"var(--font-mono, monospace)\">SHA256: 36ed8ef7...76eb8</text>\n  <text x=\"528\" y=\"169\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"11.5\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">2. 次に「実行」を押す</text>\n  <rect x=\"482\" y=\"182\" width=\"92\" height=\"28\" rx=\"5\" fill=\"var(--accent-soft)\" stroke=\"var(--accent)\" stroke-width=\"1.5\"/>\n  <text x=\"528\" y=\"200\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"12\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">実行</text>\n  <rect x=\"582\" y=\"182\" width=\"76\" height=\"28\" rx=\"5\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"620\" y=\"200\" text-anchor=\"middle\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">実行しない</text>\n</svg>\n</div>\n\n<h2 id=\"quickstart-guided-0\">Linux / WSL のワンライナー導入</h2>\n<p>Linux ターミナル（Windows でターミナル版を使う場合は先に <strong>Ubuntu</strong> ウィンドウ）を開き、次のコマンドをコピーして Enter を押してください：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>curl -fsSL https://nyairo.com/install.sh | bash</code></pre>\n</div>\n<p>実行前にハッシュ検証を行いたい場合は、公式アーカイブとチェックサムファイルを段階的にダウンロードできます：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>curl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.8.0/nyairo-v0.8.0.zip\ncurl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.8.0/nyairo-v0.8.0.zip.sha256\nsha256sum -c nyairo-v0.8.0.zip.sha256\npython3 -m zipfile -e nyairo-v0.8.0.zip \"$HOME/apps/nyairo-v0.8.0\"\ncd \"$HOME/apps/nyairo-v0.8.0\" &amp;&amp; bash scripts/bootstrap.sh</code></pre>\n</div>\n<p>事前に Python や Git を入れておく必要はありません。スクリプトが公式の <code>v0.8.0</code> リリースパッケージを自動取得・検証し、Python 3.13 の実行環境を整えたうえで、<code>~/.nyairo</code> 配下に個人用データディレクトリを作成します。</p>\n<p>ターミナルに <code>[5/5] 程序安装完成</code>（インストール完了）と表示されると、自動的にモデル設定ウィザードへ進みます：</p>\n<ol>\n<li>上下の矢印キーで利用するモデルプロバイダーを選択します。</li>\n<li>ご自身の <strong>API キー</strong>を貼り付けます（サードパーティの互換エンドポイントを使う場合は、あわせて Base URL も入力してください）。</li>\n<li>Enter キーで保存するとそのまま対話画面に入ります。「こんにちは」などと送ってみて、返事が届けば導入完了です。</li>\n</ol>\n<div class=\"callout callout-info\"><div class=\"callout-title\">curl が見つからないと表示された場合</div><p>ごく一部の初期状態の Ubuntu イメージには <code>curl</code> が入っていないことがあります。その場合は先に <code>sudo apt update &amp;&amp; sudo apt install -y curl</code> を実行してから、上のインストールコマンドを再度お試しください。</p></div>\n\n<h2 id=\"quickstart-model-beginner\">初心者向けモデル設定：数百円の DeepSeek キーまたは無料 Ollama</h2>\n<p>これまで API キーを発行したことがなくてもご安心ください。nyairo の決定論的基盤は高価なモデルを必要としないため、次の2つの手軽な方法のどちらでもすぐに動かせます。</p>\n<ul>\n<li><strong>方法 A（おまかせ推奨 · GPU 不要）：数百円（5〜10元）で DeepSeek キーを取得する</strong>：ブラウザで <a href=\"https://platform.deepseek.com\" target=\"_blank\" rel=\"noopener noreferrer\">platform.deepseek.com</a> を開いて登録・ログインし、チャージページ（Top up）から 5〜10 元（数百円程度、日常の会話は1回わずか数分の1円ほどなので長期間使えます）をチャージします。左メニューの「API keys → Create new API key（作成）」から生成した <code>sk-...</code> キーをコピーし、デスクトップクライアントの「<strong>設定 → モデル</strong>」（モデルは <code>deepseek-chat</code> を選択）に貼り付けて「保存」と「接続テスト」を押せば完了です。</li>\n<li><strong>方法 B（完全無料 · 外部通信ゼロ）：ローカルの Ollama を直接接続する</strong>：<a href=\"https://ollama.com\" target=\"_blank\" rel=\"noopener noreferrer\">ollama.com</a> から Ollama をインストールし、軽量モデルを取得します（例：<code>ollama pull qwen2.5:7b</code>）。バックグラウンドで <code>http://127.0.0.1:11434</code> が待ち受けている状態で、デスクトップ版の「<strong>設定 → モデル</strong>」からローカル Ollama / OpenAI 互換エンドポイントを選び、URL に <code>http://127.0.0.1:11434/v1</code>、API キーに <code>ollama</code> と入力すれば、会話データが一切 PC の外へ出ない環境が整います（詳しい手順は <a href=\"#configuration-beginner-model\">日常の起動・人物設定とモデル</a> をご覧ください）。</li>\n</ul>\n\n<h2 id=\"quickstart-section-1\">インストール後のチャット方法</h2>\n<ul>\n<li><strong>デスクトップ版をご利用の場合</strong>：デスクトップまたは画面右下タスクトレイの <strong>nyairo</strong> アイコンをクリックするとウィンドウが開きます。サイドバーから「概要」「対話」「街区」「記憶」「設定」を切り替えられます。</li>\n<li><strong>ターミナルをご利用の場合</strong>：普段はターミナルを開いて <code>nyairo</code> と入力して Enter を押すだけで前回の続きから話せます。<code>/chiyo_status</code> を打つと記憶（<code>READY</code>）や生活状態（<code>IDLE</code>）がオンラインか確認でき、モデルを変更したいときはいつでも <code>nyairo setup model</code> を実行できます。</li>\n<li><strong>スマホの WeChat と連携する</strong>：外出先でもスマートフォンから話しかけたい場合は、<a href=\"#weixin\">WeChat 連携手順</a> に沿って QR コードをスキャンするだけで紐づけできます。</li>\n</ul>\n\n<h2 id=\"quickstart-section-2\">パートナーに名前を付け、性格を設定する</h2>\n<p>初期状態の人物設定はまっさらな白紙ベースです。名前を付けたり、話し方やふたりの間の呼び方を決めたりしたい場合は、個人用ディレクトリにある <code>SOUL.md</code> をエディタで開いて編集します：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>nano \"$HOME/.nyairo/SOUL.md\"</code></pre>\n</div>\n<p>普段使っている自然な日本語で、パートナーの名前や、落ち着いた性格か明るめの性格か、あなたのことをどう呼ぶかなどをそのまま書き込んでください。書き終えたら <code>Ctrl + O</code> を押して Enter で保存し、<code>Ctrl + X</code> で閉じると、次回の対話から新しい設定が反映されます。</p>"
  },
  "installation": {
    "title": "導入前の準備と動作環境",
    "summary": "インストール前に 1 分ほどかけて OS 環境・ディスク空き容量・ディレクトリ構成を確認しておくと、後からやり直す手間を省けます。",
    "toc": [
      {
        "id": "installation-section-0",
        "text": "対応している OS"
      },
      {
        "id": "installation-section-1",
        "text": "プログラム本体と個人データの保存先"
      },
      {
        "id": "installation-section-2",
        "text": "ハードウェアとネットワーク要件"
      }
    ],
    "content": "<h2 id=\"installation-section-0\">対応している OS</h2>\n<ul>\n<li><strong>Windows 10 / 11</strong>：ネイティブのデスクトップクライアント（<code>nyairo-0.8.0-setup.exe</code>）を提供しており、生活状態や記憶のバックエンドは Windows 公式の WSL 2（Ubuntu）上で動作します。操作手順の詳細は <a href=\"#windows\">Windows へのインストール</a> をご覧ください。</li>\n<li><strong>Linux デスクトップまたはサーバー</strong>：Ubuntu / Debian 環境で最も十分に動作確認されており、1 行のセットアップコマンドをそのまま実行できます。その他のディストリビューションでは、基本的な展開・ビルドツールを各自で事前に導入する必要があります。</li>\n<li><strong>macOS</strong>：現時点ではパッケージ化と一連の動作検証が完了していないため、そのまま使える状態での動作は保証していません。コマンドラインに慣れている方はソースコードを参照して手動で試すか、今後の正式対応をお待ちください。</li>\n</ul>\n\n<h2 id=\"installation-section-1\">プログラム本体と個人データの保存先</h2>\n<p>アップグレード時に大切な会話履歴を誤って上書きしないよう、nyairo は「プログラム本体」と「個人データ」を完全に別のディレクトリへ分けて管理しています。</p>\n<ul>\n<li><strong>プログラムディレクトリ</strong>：Linux / WSL 環境では <code>~/.local/share/nyairo/releases/v0.8.0</code>（Windows デスクトップ版の画面プログラムは <code>%LOCALAPPDATA%\\Programs\\nyairo</code>）に配置され、アップデート時はこの領域のみが置き換わります。</li>\n<li><strong>個人データディレクトリ</strong>：<code>~/.nyairo</code> に配置され、人物設定（<code>SOUL.md</code>）、モデル設定、会話履歴、ローカル SQLite の長期記憶データベースがすべてここに保存されます。</li>\n</ul>\n<p>デスクトップクライアントをアンインストールして再インストールしたり、旧バージョンへロールバックしたりしても、<code>~/.nyairo</code> を手動で削除しない限り、これまでの記憶や設定はすべてそのまま残ります。</p>\n\n<h2 id=\"installation-section-2\">ハードウェアとネットワーク要件</h2>\n<ul>\n<li><strong>ハードウェア要件</strong>：DeepSeek や OpenAI などのオンラインモデル API を利用する場合、推論計算はすべてクラウド側で行われるため、手元の PC では軽量なバックグラウンドサービスとローカルデータベースを動かすだけで済み、<strong>専用の GPU（グラフィックボード）は不要</strong>です。Python 環境やプログラム本体、今後のデータバックアップのために、少なくとも <strong>3 GB</strong> 以上の空きディスク容量を確保しておくことを推奨します。</li>\n<li><strong>ネットワーク要件</strong>：初回インストール時には GitHub および <code>astral.sh</code> からリリースアーカイブと Python 実行ツールをダウンロードします。日常的な利用では、選択したモデル API（および有効化した場合は WeChat や Telegram）に問題なく接続できる環境があれば十分です。</li>\n<li><strong>オフライン時の挙動について</strong>：PC をシャットダウン・スリープするか、<code>wsl --shutdown</code> を実行すると、バックグラウンドの生活プロセスも停止します。パートナーを 24 時間オンラインにしておきたい場合は、常時稼働の PC または Linux クラウドサーバー上で運用してください。</li>\n</ul>"
  },
  "windows": {
    "title": "Windows へのインストール（デスクトップ版 + WSL 2）",
    "summary": "管理者権限不要のネイティブ Windows デスクトップ版の利用をおすすめします（SmartScreen の 2 ステップ図解と自動環境セットアップ付き）。WSL 2 ターミナルのみでのコマンドライン導入にも対応しています。",
    "toc": [
      {
        "id": "windows-desktop",
        "text": "方法 1：Windows デスクトップクライアント（推奨）"
      },
      {
        "id": "windows-smartscreen",
        "text": "「Windows によって PC が保護されました」と表示された場合（SmartScreen 図解）"
      },
      {
        "id": "windows-section-0",
        "text": "事前準備：WSL 2 と Ubuntu を有効にする"
      },
      {
        "id": "windows-section-2",
        "text": "方法 2：WSL ターミナルのみでコマンドライン導入する"
      }
    ],
    "content": "<h2 id=\"windows-desktop\">方法 1：Windows デスクトップクライアント（推奨）</h2>\n<p>Windows 10 / 11 をご利用の方は、ネイティブのデスクトップ版をそのまま導入できます（約 6.0 MB・現在のユーザー領域にインストールされるため管理者権限は不要です）：</p>\n<ol>\n<li>インストーラーをダウンロード：<a href=\"../../downloads/nyairo-0.8.0-setup.exe\" download=\"nyairo-0.8.0-setup.exe\"><strong>nyairo-0.8.0-setup.exe</strong></a>（SHA256：<code>36ed8ef7d4aabeb86e1a55fff298eaa2df4a995cd603b845bb885cf81ef76eb8</code>）。</li>\n<li>ダブルクリックしてインストールを完了し、<strong>nyairo</strong> を起動します（初回起動時に SmartScreen の警告が出た場合は、下の 2 ステップ図解どおり「詳細情報 → 実行」をクリックしてください）。起動後は画面右下のシステムトレイにアイコンが常駐します。</li>\n<li>初回起動時には 2 段階のセットアップウィザードが案内されます。<strong>ステップ 1 では環境を自動構築</strong>し（<code>v0.8.0</code> リリースアーカイブのダウンロードと検証 → Python 依存パッケージの導入 → 独立した個人ディレクトリの作成 → ローカル通信キーの生成 → ゲートウェイ起動 → ヘルスチェック）、<strong>ステップ 2 では初期設定を入力</strong>します（その場でパートナーの呼び名や代名詞、モデル名と API キーを設定でき、ローカルの Ollama にも接続可能です。詳しくは <a href=\"#configuration-beginner-model\">初心者向けモデル設定ガイド</a> をご覧ください）。</li>\n<li>「完了して始める」をクリックするとメイン画面が開きます。各画面の機能やショートカット操作については <a href=\"#desktop-guide\">デスクトップクライアント操作ガイド</a> をご覧ください。</li>\n</ol>\n<div class=\"callout callout-info\"><div class=\"callout-title\">アンインストールしても記憶は消えません</div><p>デスクトップ版の画面プログラムは <code>%LOCALAPPDATA%\\Programs\\nyairo</code> にインストールされますが、会話の記憶や人物設定は WSL 側の個人ディレクトリに独立して保存されています。デスクトップクライアントをアンインストールしても、停止されるのはゲートウェイと自動起動設定・ウィンドウキャッシュのみであり、個人データには一切触れません（詳細は <a href=\"#data\">データディレクトリ・バックアップ・アンインストール</a> をご覧ください）。</p></div>\n\n<h2 id=\"windows-smartscreen\">「Windows によって PC が保護されました」と表示された場合（SmartScreen 図解）</h2>\n<p>なぜこの確認画面が表示されるのでしょうか？ Microsoft は、年間数万円規模の費用がかかる商用 EV コード署名証明書をまだ取得していない新規公開の独立系オープンソースソフトに対して一律でこの注意表示を出しています。ファイル自体に問題があるわけではありませんので、以下の 3 ステップで確認のうえ起動を許可してください。</p>\n<ol>\n<li><strong>ステップ 1 · 「詳細情報」をクリック</strong>：「Windows によって PC が保護されました」のポップアップ本文の下にある、下線付きの小さな「<strong>詳細情報</strong>」リンクをクリックします。</li>\n<li><strong>SHA256 ハッシュの確認（任意）</strong>：インストーラーを保存したフォルダで PowerShell を開き、<code>Get-FileHash .\\nyairo-0.8.0-setup.exe -Algorithm SHA256</code> を実行して、算出されたハッシュ値が公式サイト掲載の <code>36ed8ef7d4aabeb86e1a55fff298eaa2df4a995cd603b845bb885cf81ef76eb8</code> と一字一句一致していることを確認します。</li>\n<li><strong>ステップ 2 · 「実行」をクリック</strong>：ウィンドウ下部に新しく表示された「<strong>実行</strong>」ボタンをクリックすると、通常どおりインストールが始まります。</li>\n</ol>\n<div class=\"smartscreen-guide\" style=\"margin: 16px 0 20px;\">\n<svg viewBox=\"0 0 680 232\" role=\"img\" aria-label=\"Windows SmartScreen での 2 ステップ許可図解：ステップ 1 で「詳細情報」をクリックし、ステップ 2 で SHA256 を確認して「実行」をクリック\" style=\"width: 100%; max-width: 680px; height: auto; display: block; color: var(--fg);\">\n  <rect x=\"4\" y=\"6\" width=\"312\" height=\"220\" rx=\"10\" fill=\"var(--card)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <line x1=\"4\" y1=\"38\" x2=\"316\" y2=\"38\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"20\" y=\"26\" fill=\"var(--accent)\" font-size=\"11\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">STEP 01 · 「詳細情報」をクリック</text>\n  <g style=\"color: var(--muted);\" opacity=\"0.7\">\n    <line x1=\"292\" y1=\"18\" x2=\"300\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n    <line x1=\"300\" y1=\"18\" x2=\"292\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n  </g>\n  <text x=\"22\" y=\"64\" fill=\"var(--fg)\" font-size=\"14\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">Windows によって PC が保護されました</text>\n  <text x=\"22\" y=\"88\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">Microsoft Defender SmartScreen が起動を停止</text>\n  <text x=\"22\" y=\"106\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">認識されないアプリ（商用署名なし OSS 版）</text>\n  <rect x=\"20\" y=\"122\" width=\"84\" height=\"26\" rx=\"6\" fill=\"var(--accent-soft)\" stroke=\"var(--accent)\" stroke-width=\"1.4\"/>\n  <text x=\"62\" y=\"139\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"12\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">詳細情報</text>\n  <g style=\"color: var(--accent);\">\n    <path d=\"M138 135H112m6-5-6 5 6 5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n  </g>\n  <text x=\"144\" y=\"139\" fill=\"var(--accent)\" font-size=\"11.5\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">1. まずここを押して展開</text>\n  <rect x=\"222\" y=\"182\" width=\"78\" height=\"28\" rx=\"5\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"261\" y=\"200\" text-anchor=\"middle\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">実行しない</text>\n  <g style=\"color: var(--accent);\">\n    <path d=\"M325 116h28m-6-6 6 6-6 6\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n  </g>\n  <rect x=\"364\" y=\"6\" width=\"312\" height=\"220\" rx=\"10\" fill=\"var(--card)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <line x1=\"364\" y1=\"38\" x2=\"676\" y2=\"38\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"380\" y=\"26\" fill=\"var(--accent)\" font-size=\"11\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">STEP 02 · 「実行」をクリック</text>\n  <g style=\"color: var(--muted);\" opacity=\"0.7\">\n    <line x1=\"652\" y1=\"18\" x2=\"660\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n    <line x1=\"660\" y1=\"18\" x2=\"652\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n  </g>\n  <text x=\"382\" y=\"64\" fill=\"var(--fg)\" font-size=\"14\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">Windows によって PC が保護されました</text>\n  <rect x=\"382\" y=\"78\" width=\"276\" height=\"66\" rx=\"6\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"394\" y=\"98\" fill=\"var(--fg-2)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">アプリ: nyairo-0.8.0-setup.exe</text>\n  <text x=\"394\" y=\"116\" fill=\"var(--muted)\" font-size=\"11\" font-family=\"var(--font-sans, sans-serif)\">発行元: 不明な発行元（ハッシュ一致で安全）</text>\n  <text x=\"394\" y=\"134\" fill=\"var(--accent)\" font-size=\"10.5\" font-family=\"var(--font-mono, monospace)\">SHA256: 36ed8ef7...76eb8</text>\n  <text x=\"528\" y=\"169\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"11.5\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">2. 次に「実行」をクリック</text>\n  <rect x=\"482\" y=\"182\" width=\"92\" height=\"28\" rx=\"5\" fill=\"var(--accent-soft)\" stroke=\"var(--accent)\" stroke-width=\"1.5\"/>\n  <text x=\"528\" y=\"200\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"12\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">実行</text>\n  <rect x=\"582\" y=\"182\" width=\"76\" height=\"28\" rx=\"5\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"620\" y=\"200\" text-anchor=\"middle\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">実行しない</text>\n</svg>\n</div>\n\n<h2 id=\"windows-section-0\">事前準備：WSL 2 と Ubuntu を有効にする</h2>\n<p>お使いの PC でまだ WSL 2 を有効にしていない場合（デスクトップ版の初回起動時にも案内が表示されます）、次の 2 ステップで数分ほどかけて導入しておけば準備完了です。</p>\n<ol>\n<li>Windows のスタートメニューで <strong>PowerShell</strong> を検索し、右クリックして<strong>「管理者として実行」</strong>を選択します。</li>\n<li>次のコマンドを貼り付けて Enter キーを押します：</li>\n</ol>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>powershell</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>wsl --install -d Ubuntu</code></pre>\n</div>\n<p>インストール後に PC の再起動を求められた場合は、まず一度再起動してください。続いてスタートメニューから <strong>Ubuntu</strong> を開き、画面の案内に従って半角英数字のユーザー名とパスワードを設定します（Linux ターミナルでパスワードを入力する際、画面にアスタリスクなどが表示されないのは正常な仕様ですので、入力後そのまま Enter を押してください）。設定が終わったら nyairo デスクトップ版を起動すれば、残りの構築作業はアプリ側が自動で引き継ぎます。</p>\n\n<h2 id=\"windows-section-2\">方法 2：WSL ターミナルのみでコマンドライン導入する</h2>\n<p>グラフィカルな画面を使わず、ターミナル上のコマンドだけで使いたい場合は、<strong>Ubuntu</strong> ターミナルを開いて次の 1 行を貼り付けてください。</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>curl -fsSL https://nyairo.com/install.sh | bash</code></pre>\n</div>\n<p>実行前にハッシュ検証を行いたい場合は、以下のコマンドで SHA256 を照合してからインストールできます。</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>curl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.8.0/nyairo-v0.8.0.zip\ncurl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.8.0/nyairo-v0.8.0.zip.sha256\nsha256sum -c nyairo-v0.8.0.zip.sha256\npython3 -m zipfile -e nyairo-v0.8.0.zip \"$HOME/apps/nyairo-v0.8.0\"\ncd \"$HOME/apps/nyairo-v0.8.0\" &amp;&amp; bash scripts/bootstrap.sh</code></pre>\n</div>\n<p>インストール後に画面の指示に沿ってモデルの API キーを入力すれば完了です。以降は Ubuntu を開いて <code>nyairo</code> と入力するだけでいつでも対話できます。</p>"
  },
  "linux": {
    "title": "Linux 環境および手動インストール",
    "summary": "各ステップのスクリプトを自分で確認したい方や、リリースアーカイブを手元に保存したい方、サーバーへ手動デプロイしたい方向けの手順です。",
    "toc": [
      {
        "id": "linux-section-0",
        "text": "ステップ 1：基本システムツールの準備"
      },
      {
        "id": "linux-section-1",
        "text": "ステップ 2：uv と Python 3.13 の準備"
      },
      {
        "id": "linux-section-2",
        "text": "ルート A：Git タグを指定してクローン・導入する"
      },
      {
        "id": "linux-section-3",
        "text": "ルート B：公式 ZIP をダウンロード・検証して導入する"
      },
      {
        "id": "linux-section-4",
        "text": "すでに Hermes を導入済みの環境について"
      }
    ],
    "content": "<div class=\"callout callout-info\"><div class=\"callout-title\">はじめにお読みください</div><p>すでに <code>curl -fsSL https://nyairo.com/install.sh | bash</code> の 1 行コマンドでインストール済みの場合、<strong>本章の手順は一切不要です</strong>。そのまま <a href=\"#configuration\">普段の起動・人物設定・モデル設定</a> へお進みください。本章は、各工程を手動で確認しながら導入したい方向けの解説です。</p></div>\n\n<h2 id=\"linux-section-0\">ステップ 1：基本システムツールの準備</h2>\n<p>Linux ターミナル（または Windows の Ubuntu ターミナル）で必要な基本ツールを揃えます（Ubuntu / Debian の例）：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>sudo apt update\nsudo apt install -y git curl unzip ripgrep less nano python3</code></pre></div>\n<p>現在の Linux アカウントのパスワードを入力して Enter を押し、エラーが出ていないことを確認してから次へ進んでください。</p>\n\n<h2 id=\"linux-section-1\">ステップ 2：uv と Python 3.13 の準備</h2>\n<p>本プロジェクトは独立した Python 環境と依存パッケージを管理するために <a href=\"https://docs.astral.sh/uv/getting-started/installation/\" target=\"_blank\" rel=\"noopener noreferrer\">uv</a> を使用しており、システム標準の Python 環境を汚すことはありません。実行前にスクリプトをダウンロードして中身を確認してから進めることもできます。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh\nless /tmp/uv-install.sh\nsh /tmp/uv-install.sh</code></pre></div>\n<p>内容確認を終えたら <code>q</code> キーで <code>less</code> を閉じます。インストール後、新しいターミナルウィンドウを開き直して Python 3.13 を取得してください。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>uv --version\nuv python install 3.13</code></pre></div>\n\n<h2 id=\"linux-section-2\">ルート A：Git タグを指定してクローン・導入する</h2>\n<p>ルート A とルート B はどちらか一方を選べば十分です。Git を使う場合は、リリース済みの固定タグ <code>v0.8.0</code> をチェックアウトしてください（変更が続く開発ブランチをそのまま導入しないようご注意ください）。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>mkdir -p \"$HOME/apps\"\ngit clone --branch v0.8.0 --depth 1 \\\n  https://github.com/L1AN929/nyairo.git \"$HOME/apps/nyairo-v0.8.0\"\ncd \"$HOME/apps/nyairo-v0.8.0\"\nbash scripts/bootstrap.sh</code></pre></div>\n<p>固定タグをチェックアウトすると Git が <code>detached HEAD</code> と表示しますが、これは正常な状態です。インストール完了後、以下のマニフェスト自己チェックを実行してすべてのファイルが欠損なく揃っていることを確認できます。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>~/.local/share/nyairo/releases/v0.8.0/vendor/hermes/.venv/bin/python -B \\\n  ~/.local/share/nyairo/releases/v0.8.0/scripts/verify_manifest.py</code></pre></div>\n<p>出力に <code>\"valid\": true</code> が含まれていれば、ファイル構成の検証が完全に一致したことを示します。</p>\n\n<h2 id=\"linux-section-3\">ルート B：公式 ZIP をダウンロード・検証して導入する</h2>\n<p>先にリリースアーカイブを手元に保存し、ハッシュを照合してから展開・導入したい場合は次の手順で行います。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>mkdir -p \"$HOME/apps\" &amp;&amp; cd \"$HOME/apps\"\ncurl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.8.0/nyairo-v0.8.0.zip\ncurl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.8.0/nyairo-v0.8.0.zip.sha256\nsha256sum -c nyairo-v0.8.0.zip.sha256</code></pre></div>\n<p>画面に <code>nyairo-v0.8.0.zip: OK</code> と出力されたことを確認したら、アーカイブを展開して同梱のインストーラーを実行します。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>python3 -m zipfile -e nyairo-v0.8.0.zip \"$HOME/apps/nyairo-v0.8.0\"\ncd \"$HOME/apps/nyairo-v0.8.0\"\nbash scripts/bootstrap.sh</code></pre></div>\n<p>Windows 側のブラウザで事前に ZIP をダウンロードしてある場合は、WSL 内から <code>/mnt/c/Users/あなたのWindowsユーザー名/Downloads/nyairo-v0.8.0.zip</code> のように参照できます。</p>\n\n<h2 id=\"linux-section-4\">すでに Hermes を導入済みの環境について</h2>\n<p>既存の Hermes を削除する必要はありません。nyairo はバージョン整合性を検証済みの専用 Hermes 環境を内部に備えており、データも <code>~/.nyairo</code> 配下に独立して管理されるため、既存の環境と干渉することはありません。ただし、nyairo のプラグインディレクトリを別バージョンの Hermes へ手動でコピーして混在させると、インターフェースの違いによりエラーの原因となりますのでお控えください。</p>"
  },
  "agent-install": {
    "title": "AI アシスタントに任せて自動インストール",
    "summary": "普段から Claude Code や Codex などターミナル操作権限を持つ AI コーディングツールをお使いの場合は、この標準プロンプトをそのまま渡すことで、事前チェックを行ったうえで安全にインストールさせられます。",
    "toc": [
      {
        "id": "agent-section-0",
        "text": "適したツールと活用シーン"
      },
      {
        "id": "agent-section-1",
        "text": "そのままコピーして AI アシスタントに渡す指示文"
      },
      {
        "id": "agent-section-2",
        "text": "4 つの安全ルールを設けている理由"
      },
      {
        "id": "agent-section-3",
        "text": "導入後にご自身で確認すべきポイント"
      }
    ],
    "content": "<div class=\"callout callout-info\"><div class=\"callout-title\">完全ローカル実行・認証情報は聞きません</div><p>公式サイトが提供しているのはこの手順仕様テキストのみです（独立した原文ファイルは <a href=\"../../AGENT_INSTALL.zh-CN.md\">AGENT_INSTALL.zh-CN.md</a> をご覧ください）。実際の環境確認やダウンロードはすべてご自身の PC のターミナル上で、お使いの AI ツールが実行します。<strong>パスワードや SSH 秘密鍵、モデルの API キーなどを AI アシスタントのチャット欄には絶対に貼り付けないでください。</strong></p></div>\n\n<h2 id=\"agent-section-0\">適したツールと活用シーン</h2>\n<ul>\n<li><strong>対応ツール</strong>：Claude Code、OpenAI Codex CLI、Cursor Agent、Windsurf など、Linux / WSL ターミナル上でコマンドを実行できるローカル AI アシスタント全般。</li>\n<li><strong>特に便利なケース</strong>：新規に契約したばかりの Linux クラウドサーバーや、過去に別バージョンの環境を構築したことがあり「コマンド操作で既存データを誤って上書きしてしまわないか不安」という環境です。この指示文では、AI にまず読み取り専用の現状調査と報告を強制し、あなたの承認を得てから実作業へ進むよう指定しています。</li>\n</ul>\n\n<h2 id=\"agent-section-1\">そのままコピーして AI アシスタントに渡す指示文</h2>\n<p>コードブロック右上の「コピー」をクリックし、そのままお使いの AI アシスタントへ送信してください。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>この PC に nyairo（v0.8.0）をインストールしてください。https://nyairo.com/AGENT_INSTALL.zh-CN.md の仕様を厳密に守り、各ステップで何を行っているかを日本語で説明しながら進めてください。確認を省略したり、確認せず成功と報告したりしないでください：\n\n1. 【読み取り専用チェック・まだインストールしない】OS 環境（Linux または WSL 2 であること）、空きディスク容量（3 GB 以上推奨）、github.com と astral.sh への接続可否を確認してください。あわせて現在の HOME 配下に ~/.nyairo、~/.local/share/nyairo、~/.chiyo-v1 がすでに存在しないか、および稼働中の m0_writer_worker または gateway プロセスがないか確認してください。既存インスタンスが見つかった場合は即座に作業を停止して報告し、別の Linux ユーザーまたは別の HOME を使うよう提案してください。既存データの上書きや、既存インスタンスのプロセス終了は絶対に行わないでください。\n2. 【事前確認の取得】作成予定のディレクトリ（~/.local/share/nyairo/releases/v0.8.0、~/.nyairo、~/.local/bin/nyairo）について説明してください。sudo の実行やシステムユーザーの新規作成が必要な場合は、必ず私の同意を得てから進めてください。\n3. 【検証付きインストール】https://github.com/L1AN929/nyairo/releases/download/v0.8.0/ から nyairo-v0.8.0.zip と同名の .sha256 ファイルを取得し、sha256sum -c で一致を確認してから展開したうえで、同梱の bash scripts/bootstrap.sh を実行してください（非対話ターミナルでは --no-setup --no-launch を付与可）。\n4. 【実機検証】~/.local/state/nyairo/install.json のバージョンが v0.8.0 であることを確認し、同梱の Python で scripts/verify_manifest.py を実行して \"valid\": true が出力されることを確認してください。また ~/.local/bin/nyairo -z \"インストール自己診断\" を 1 回実行し、新インスタンス側に残留プロセスがないことを確認してください。私が認証キーを設定する前の段階では、モデルや外部チャネルの状態は必ず正直に「未検証」と記載してください。\n5. 【日本語での引き継ぎ】起動方法（nyairo）、モデル設定（nyairo setup model）、WeChat / Telegram 連携、アップデートとバックアップの方法をわかりやすい日本語で案内してください。全工程を通じて、API キーやパスワードをチャット欄へ送信させることは絶対に行わないでください。</code></pre></div>\n\n<h2 id=\"agent-section-2\">4 つの安全ルールを設けている理由</h2>\n<ul>\n<li><strong>過去の記憶を上書きしない</strong>：多くの AI アシスタントは既存ディレクトリを見つけると、習慣的に削除して入れ直そうとします。この指示文では <code>~/.nyairo</code> や <code>~/.chiyo-v1</code> が残っている時点で即座に停止させ、別アカウントでの分離を案内するよう明記しています。</li>\n<li><strong>稼働中のプロセスを勝手に止めない</strong>：同じマシン上で別のインスタンスがすでに動いている場合でも、環境整理の名目で既存インスタンスのゲートウェイや記憶書き込みプロセスを <code>kill</code> しないよう制限しています。</li>\n<li><strong>公式ハッシュとマニフェストのみを信頼する</strong>：GitHub Release に同梱された <code>.sha256</code> とパッケージ内の <code>MANIFEST.json</code> の照合を必須とし、未リリースの開発ブランチからの取得を防ぎます。</li>\n<li><strong>認証キーはローカルだけで入力する</strong>：AI に任せるのは基盤環境の構築までとし、API キーの入力はご自身で <code>nyairo setup model</code> を実行して <code>0600</code> パーミッションのローカル設定ファイルへ書き込むことで、第三者のチャット履歴にキーが残るリスクを防ぎます。</li>\n</ul>\n\n<h2 id=\"agent-section-3\">導入後にご自身で確認すべきポイント</h2>\n<p>AI から「インストール完了」と報告を受けたら、最後に次の 3 点を軽く確認しておくと安心です。</p>\n<ol>\n<li><code>cat ~/.local/state/nyairo/install.json</code> に含まれる <code>version</code> が確実に <code>v0.8.0</code> になっていること（注：<code>nyairo --version</code> を直接実行した場合に表示されるのは基盤ホストである Hermes 側のバージョンです。nyairo 本体のバージョン確認は <code>install.json</code> を基準としてください）。</li>\n<li><code>~/.nyairo</code> ディレクトリ配下に <code>SOUL.md</code>、<code>config.yaml</code>、<code>chiyo/config.json</code> が生成されていること。</li>\n<li><code>nyairo setup model</code> を実行してご自身の API キーを設定したあと、<code>nyairo</code> を起動して挨拶を送り、問題なく返信が返ってくること。</li>\n</ol>"
  },
  "configuration": {
    "title": "普段の起動・人物設定・モデル設定",
    "summary": "普段の呼び出し方や、パートナーの名前・口調のカスタマイズ方法に加えて、初心者でも少額（5元／数百円程度）で DeepSeek キーを設定する方法や、通信量ゼロでローカル Ollama に接続する手順をご紹介します。",
    "toc": [
      {
        "id": "configuration-section-0",
        "text": "普段のチャットの開き方"
      },
      {
        "id": "configuration-section-1",
        "text": "名前と人物設定の変更 (SOUL.md)"
      },
      {
        "id": "configuration-beginner-model",
        "text": "初心者向けガイド：少額 DeepSeek キーと無料ローカル Ollama"
      },
      {
        "id": "configuration-section-2",
        "text": "モデルの切り替えと API キーの更新"
      }
    ],
    "content": "<h2 id=\"configuration-section-0\">普段のチャットの開き方</h2>\n<ul>\n<li><strong>Windows デスクトップ版</strong>：デスクトップのショートカットをダブルクリックするか、画面右下のシステムトレイにある猫アイコンをクリックします。<code>Ctrl + K</code> を押せばいつでもクイック操作バーを呼び出し、画面の切り替えや新しい対話の開始が行えます。</li>\n<li><strong>Linux / WSL ターミナル</strong>：ターミナルを開いて <code>nyairo</code> と入力し Enter を押すだけですぐに会話を始められます。会話を終えてコマンドラインに戻るには、<code>/exit</code> を入力するか <code>Ctrl + D</code> を押してください。</li>\n</ul>\n\n<h2 id=\"configuration-section-1\">名前と人物設定の変更 (SOUL.md)</h2>\n<p>パートナーの名前やあなたの呼び方、寡黙か気さくなのかといった口調は、個人データディレクトリ内の <code>SOUL.md</code> に書かれています。お好みのテキストエディタか、ターミナル標準の <code>nano</code> で開いてそのまま編集できます。</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>nano \"$HOME/.nyairo/SOUL.md\"</code></pre>\n</div>\n<p>複雑なプロンプト技法を書く必要はありません。人物紹介メモを書くように、自然な文章（日本語でそのまま記述できます）で次の 3 点を書いておけば十分です。</p>\n<ul>\n<li><strong>名前と関係性</strong>：パートナーの名前と、普段どのような間柄で接するか（友人、ルームメイト、相棒など）。</li>\n<li><strong>話し方の癖</strong>：落ち着いた控えめなトーンか気さくで自然な口調か、長文を好むか簡潔に話すか。</li>\n<li><strong>基本的な約束事</strong>：交わした約束を大切にすること、知らないことは知ったかぶりをせず素直に知らないと言うこと、気が進まない頼みごとは自然に断ってよいこと。</li>\n</ul>\n<p><code>nano</code> で編集し終えたら、<code>Ctrl + O</code> → Enter で保存し、<code>Ctrl + X</code> で終了します。新しく対話を始めると変更が反映されます。</p>\n\n<h2 id=\"configuration-beginner-model\">初心者向けガイド：少額 DeepSeek キーと無料ローカル Ollama</h2>\n<p>nyairo 自体は計算リソースの販売やトークンの中抜きを一切行いません。空間の経路探索や温湿度の物性変化、権限判定はすべて手元の決定論的エンジンが駆動するため、裏側で高価な「判定用 LLM」を追加課金で動かす必要はありません。API を触ったことがない初心者の方でも、次の 2 つのルートのどちらかを選べば 3 分ほどで準備できます。</p>\n\n<h3>ルート 1：少額チャージ（5〜10元／約100〜200円程度）で DeepSeek API キーを発行する（軽量ノート PC におすすめ・GPU 不要）</h3>\n<ol>\n<li>ブラウザで DeepSeek 公式プラットフォーム <a href=\"https://platform.deepseek.com\" target=\"_blank\" rel=\"noopener noreferrer\">platform.deepseek.com</a> を開き、携帯電話番号などでアカウント登録してログインします。</li>\n<li>左側のメニューから「<strong>Top up（チャージ）</strong>」をクリックし、<strong>5〜10元（日本円で約100〜200円程度）</strong>をチャージします（日常会話では 1 発言あたり 1 円未満のごくわずかなコストしかかからず、5元分でも何週間も十分にやり取りできます。また、デスクトップ版では初期状態で 1 日あたり 50,000 トークンの利用上限保護が有効になっています）。</li>\n<li>左側のメニューから「<strong>API keys</strong>」→「<strong>Create new API key（作成）</strong>」をクリックし、任意の名前（例：<code>nyairo</code>）を入力して、生成された <code>sk-...</code> で始まるキー文字列をすぐにコピーします（ダイアログを閉じるとキー全文は二度と表示されなくなるため、安全に保管し、絶対に他人に共有しないでください）。</li>\n<li>nyairo デスクトップクライアントを開き、左側の「<strong>設定 → モデル</strong>」（画面上の「モデルとチャネル」カード）をクリックします：\n  <ul>\n    <li>プロバイダーで <strong>DeepSeek</strong> を選択します（Base URL は <code>https://api.deepseek.com</code>）。</li>\n    <li>モデル名に <code>deepseek-chat</code> と入力します。</li>\n    <li>先ほどコピーした <code>sk-...</code> キーを API Key 欄に貼り付け、「<strong>設定を保存</strong>」をクリックしてから隣の「<strong>接続テスト</strong>」を押します。ミリ秒単位の応答時間が表示されれば設定完了です。</li>\n  </ul>\n</li>\n</ol>\n\n<h3>ルート 2：無料のローカル Ollama に接続する（メモリや GPU に余裕があり、クラウド通信ゼロで使いたい方向け）</h3>\n<ol>\n<li><a href=\"https://ollama.com\" target=\"_blank\" rel=\"noopener noreferrer\">ollama.com</a> から Ollama をダウンロードしてインストールし、OS のターミナルを開いて日本語・中国語表現のバランスがよいローカルモデルを取得します（メモリ 16 GB の PC には 7B、8 GB の PC には 3B モデルがおすすめです）：\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>ollama pull qwen2.5:7b</code></pre>\n</div></li>\n<li>Ollama サービスがバックグラウンドで起動していることを確認します（初期状態ではローカルの <code>http://127.0.0.1:11434</code> で待機します）。</li>\n<li>nyairo デスクトップクライアントの「<strong>設定 → モデル</strong>」を開きます（ターミナル版の場合は <code>nyairo setup model</code> を実行します）：\n  <ul>\n    <li><strong>Ollama / ローカル互換 API</strong> を選択し、エンドポイント URL に <code>http://127.0.0.1:11434/v1</code> を入力します（Ollama ネイティブのサービス URL は <code>http://127.0.0.1:11434</code> です）。</li>\n    <li>モデル名には先ほど取得した <code>qwen2.5:7b</code> を入力し、API Key 欄にはプレースホルダーとして <code>ollama</code> などの任意の文字を入れておきます。</li>\n    <li>保存して接続テストを行います。これ以降、すべての対話推論・長期記憶・日常のリズムは 100% お手元の PC 内で完結し、クラウドへの通信量も利用料金も一切発生しません。</li>\n  </ul>\n</li>\n</ol>\n\n<h2 id=\"configuration-section-2\">モデルの切り替えと API キーの更新</h2>\n<p>あとから別のモデル（DeepSeek、Claude、OpenAI、Qwen、ローカル Ollama の切り替えなど）へ変更したり、期限切れの API キーを更新したりしたい場合は次のように行います。</p>\n<ul>\n<li><strong>デスクトップ版をご利用の場合</strong>：左側の「設定 → モデル」パネルからモデルプロバイダー・モデル名・API キーを直接書き換え、保存して接続テストを行うだけで完了します（キーは手元の <code>0600</code> 権限の設定ファイルにのみ書き込まれ、画面上に既存のキーが平文で再表示されることはありません）。</li>\n<li><strong>ターミナル版をご利用の場合</strong>：コマンドラインで次を実行します：</li>\n</ul>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>nyairo setup model</code></pre>\n</div>\n<p>モデルを切り替えても、これまで蓄積された会話履歴や部屋の物性状態、長期記憶には一切影響しません。</p>"
  },
  "weixin": {
    "title": "WeChat 連携",
    "badge": "推奨",
    "summary": "公式アカウント申請や非公式クライアントに手を出さなくても、スマホの WeChat で QR コードを読み取るだけで手元の PC 上のパートナーとつながり、外出先からでもいつでもメッセージを送れます。",
    "toc": [
      {
        "id": "wx-why",
        "text": "WeChat とつなぐとどうなる？"
      },
      {
        "id": "wx-setup",
        "text": "QR スキャンでの連携手順"
      },
      {
        "id": "wx-features",
        "text": "WeChat 上でできるやり取り"
      },
      {
        "id": "wx-safety",
        "text": "プライバシーと迷惑防止の仕組み"
      }
    ],
    "content": "<h2 id=\"wx-why\">WeChat とつなぐとどうなる？</h2>\n<p>ちょっとしたひと言を交わしたいだけなのに、毎回パソコンの前に座ってウィンドウを開くのは大変です。nyairo を WeChat と連携させておけば、スマホの中にいつもの個人トーク画面そのままの窓口ができます。</p>\n<ul>\n<li><strong>外出先でも会話をそのまま続ける</strong>：さっき PC の前で話していた話題の続きを、飲み物を買いに階下へ降りたあいだにスマホから送れます。どちらも同じ記憶データベースを参照しています。</li>\n<li><strong>いまどこで何をしているかがわかる</strong>：「いま何してる？」と送れば、バックグラウンドで動いている実際の部屋や街区の状態にもとづいて、部屋で本を読んでいるのか、ちょうど街角まで歩いているところなのかを答えてくれます。</li>\n<li><strong>再起動しても文脈が途切れない</strong>：PC を再起動したりゲートウェイを再接続したりしても、それまでの会話の流れはしっかり引き継がれます。</li>\n</ul>\n\n<h2 id=\"wx-setup\">QR スキャンでの連携手順</h2>\n<p>nyairo には公式の軽量ボットチャネルが内蔵されており、法人アカウントや独自ドメインの取得は不要です。</p>\n\n<h3>方法 A：Windows デスクトップ版で QR スキャン（いちばん簡単）</h3>\n<ol>\n<li>nyairo デスクトップクライアントを開き、左側の <strong>設定</strong> ページへ進みます。</li>\n<li>「WeChat 連携」カードを見つけ、連携用 QR コードの生成をクリックします。</li>\n<li>ご自身のスマホの WeChat でスキャンして承認し、ステータスが「接続済み」に変われば連携完了です。</li>\n</ol>\n\n<h3>方法 B：Linux / WSL ターミナルで QR スキャン</h3>\n<p>まずターミナル上で WeChat チャネルを有効にします。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>nyairo gateway setup</code></pre></div>\n<p>メニューで <strong>Weixin</strong> をチェックして有効化し（設定ファイル <code>~/.nyairo/config.yaml</code> の <code>platforms.weixin.enabled: true</code> に相当）、続けてゲートウェイを起動します。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>nyairo gateway run</code></pre></div>\n<p>ターミナル上に文字アートの QR コード（および予備の URL）が表示されるので、スマホの WeChat で読み取って承認すれば紐付けが完了します。</p>\n\n<h2 id=\"wx-features\">WeChat 上でできるやり取り</h2>\n<ul>\n<li><strong>散歩や用事に出かけてもらう</strong>：WeChat から「本屋を覗いてきて」や「ちょっと外の空気を吸ってきたら？」と送ると、相手はバックグラウンドで実際に家を出発し、移動に何分ほどかかるかを教えてくれます。途中で「いまどこ？」と尋ねれば、あと何分歩くかを正直に答え、1 秒で瞬間移動するようなことはありません。</li>\n<li><strong>時間帯や店舗の開閉を認識する</strong>：深夜にすでに閉店している喫茶店へ行くよう頼むと、店が閉まっていることに気づいてその旨を率直に伝え、別の場所へ回るか家に帰るかを自分で判断します。</li>\n<li><strong>静かに部屋で過ごしてもらう</strong>：「用事が済んだらゆっくり休んでね」と伝えれば、相手はそのまま部屋にとどまって休息し、用もないのに通知を連投して邪魔することはありません。</li>\n</ul>\n\n<h2 id=\"wx-safety\">プライバシーと迷惑防止の仕組み</h2>\n<ul>\n<li><strong>連携した本人との個人チャット限定</strong>：初期状態で厳格なホワイトリスト（<code>allowlist</code>）が有効になっており、グループチャットへの応答もオフになっています。QR スキャンで紐付けたご本人からのメッセージにしか反応しないため、見知らぬ人に話しかけられて API 利用枠を消費される心配がありません。</li>\n<li><strong>nyairo のサーバーを経由しない</strong>：nyairo には独自のクラウドサーバーが存在しません。会話内容はあなたが選択したモデルプロバイダーとメッセージプラットフォームのみを通過し、人物設定・長期記憶・生活状態はすべてご自身の PC 内の <code>~/.nyairo</code> にのみ書き込まれます。</li>\n</ul>"
  },
  "telegram": {
    "title": "Telegram・QQ との連携",
    "summary": "Telegram ボットまたは QQ 公式ボット経由で手元の PC 上のパートナーと接続します。認証情報を設定したのに返信が届かないトラブルを防ぐため、セッションホワイトリストの許可設定を必ず確認してください。",
    "toc": [
      {
        "id": "telegram-section-0",
        "text": "Telegram · ボットの作成と Token の設定"
      },
      {
        "id": "telegram-section-1",
        "text": "Telegram · 個人チャットの記憶アクセス権限を紐付ける"
      },
      {
        "id": "telegram-section-2",
        "text": "Telegram · ゲートウェイの起動と動作確認"
      },
      {
        "id": "qqbot-setup",
        "text": "QQ 公式ボットの連携と必ず確認すべき 2 つの注意点"
      },
      {
        "id": "telegram-section-3",
        "text": "その他のチャットプラットフォームについて"
      }
    ],
    "content": "<h2 id=\"telegram-section-0\">Telegram · ボットの作成と Token の設定</h2>\n<p>外部チャットアプリを設定する前に、まずローカルのターミナルまたはデスクトップ版でモデルから正常に返信が返ってくる状態を確認しておくことをおすすめします。</p>\n<ol>\n<li>Telegram 内で公式の <strong>@BotFather</strong> を検索し、<code>/newbot</code> を送信して案内に沿って新しいボットを作成します。発行された <strong>HTTP API Token</strong> をコピーしてください（この認証情報はボットの鍵そのものですので、公開グループに貼ったりスクリーンショットに写り込ませたりしないよう厳重に管理してください）。</li>\n<li>ターミナルでゲートウェイ設定ウィザードを実行します（Windows デスクトップ版をお使いの方は、「設定 → モデルとチャネル」画面から直接入力することもできます）。</li>\n</ol>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>nyairo gateway setup</code></pre></div>\n<p><strong>Telegram</strong> を選択し、先ほど取得した Token を入力します。「アクセスを許可するユーザー（Allowed Users）」欄には、<strong>ご自身の数字のみの Telegram User ID だけ</strong>を入力してください（<code>@ユーザー名</code> や表示名ではありません）。ウィザードが <code>Detected your Telegram user ID</code> を自動検出した場合は、数値に誤りがないか確認してそのまま確定してください。</p>\n\n<h2 id=\"telegram-section-1\">Telegram · 個人チャットの記憶アクセス権限を紐付ける</h2>\n<p>見知らぬ第三者がボットへ誤って話しかけ、あなたのプライベートな記憶を読み出してしまうのを防ぐため、nyairo では「どの個人チャットセッションに長期記憶の読み書きを許可するか」を明示的に指定する必要があります。</p>\n<p>1 行コマンドの標準インストール（データは <code>~/.nyairo</code> に保存）をご利用の場合は、以下のスクリプトを実行して画面の案内に従い数字のみの User ID を入力すると、対応するセッション識別子が算出されます。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>REL=\"$HOME/.local/share/nyairo/releases/v0.8.0\"\nPYTHONPATH=\"$REL:$REL/vendor/hermes\" \"$REL/vendor/hermes/.venv/bin/python\" -c '\nfrom gateway.config import Platform\nfrom gateway.session import SessionSource, build_session_key\nuid = input(\"Telegram の数字のみの User ID を入力してください: \").strip()\nsource = SessionSource(platform=Platform.TELEGRAM, chat_type=\"dm\", chat_id=uid, user_id=uid)\nprint(\"以下の紐付け用識別子をコピーしてください：\")\nprint(build_session_key(source))\n'</code></pre></div>\n<p>算出された識別子をコピーし、<code>~/.nyairo/chiyo/config.json</code> を開きます。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>nano \"$HOME/.nyairo/chiyo/config.json\"</code></pre></div>\n<p>ファイル内の他のフィールドはそのまま残し、<code>gateway_bindings</code> 内の <code>telegram</code> 配列に先ほど算出した文字列を設定します（Windows デスクトップ版をお使いの場合は、「設定 → モデルとチャネル」画面下部にあるセッションホワイトリストからチェックを入れて許可するだけで済みます）。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>json</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>{\n  \"gateway_bindings\": {\n    \"telegram\": [\"先ほど出力されたセッション識別子を貼り付け\"]\n  }\n}</code></pre></div>\n\n<h2 id=\"telegram-section-2\">Telegram · ゲートウェイの起動と動作確認</h2>\n<p>保存してエディタを閉じたら、ゲートウェイを起動してオンライン状態を維持します。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>nyairo gateway run</code></pre></div>\n<p>同一のボット Token に対して、同時にポーリング受信できるゲートウェイプロセスは 1 つだけです。もし <code>409 Conflict</code> エラーが出る場合は、ゲートウェイが二重に起動しています（例：デスクトップ版の裏のゲートウェイと手動ターミナルの両方が動いているなど）ので、どちらか一方を終了してください。</p>\n<p>スマホの Telegram から自分のボット宛てにメッセージをいくつか送って動作を確認しましょう。</p>\n<ol>\n<li>普段どおりの挨拶を送り、問題なく返信が届くことを確認します。</li>\n<li><code>/chiyo_status</code> を送信し、モジュール状態（<code>Memory: READY</code>）が返ってくることを確認します。</li>\n<li><code>/chiyo_memory list</code> を送信し、個人チャットの記憶アクセス権限が正しく紐付いていることを確認します（新規環境でリストが空と表示されるのは正常です）。</li>\n</ol>\n\n<h2 id=\"qqbot-setup\">QQ 公式ボットの連携と必ず確認すべき 2 つの注意点</h2>\n<p>基盤のゲートウェイには QQ 公式ボット（API v2）アダプタが内蔵されています。QQ の個人チャットでパートナーと話したい場合は、<a href=\"https://q.qq.com\" target=\"_blank\" rel=\"noopener noreferrer\">QQ オープンプラットフォーム (q.qq.com)</a> でボットを作成し、<strong>AppID</strong> と <strong>AppSecret</strong> を取得してください。Windows デスクトップ版の「設定 → モデルとチャネル」にある「QQ 連携」欄へ入力して保存すると、ゲートウェイが自動で再起動して接続を試み、ゲートウェイログから取得した実際の接続結果が画面上にそのまま表示されます。</p>\n<p>設定ファイルを手動編集する場合や、「接続できたはずなのに返事が来ない」という場合は、実機検証で判明した次の 2 つの落とし穴を重点的に確認してください。</p>\n<ol>\n<li><strong>設定キー名は必ず <code>qqbot</code> にし、<code>qq</code> と書かないこと</strong>：<code>config.yaml</code> の <code>gateway.platforms</code> 配下におけるプラットフォーム名は厳密に <code>qqbot</code> です。誤って <code>qq</code> と手入力すると、ゲートウェイはこの設定ブロック全体をそのまま無視し、ログにエラーすら出力されません。</li>\n<li><strong>AppID とシークレットを入力するだけでは不十分で、対象の個人チャットセッションをホワイトリストへ追加する必要があること</strong>：nyairo のプライバシーゲートは、<code>gateway_bindings.qqbot</code> で明示的に許可された個人チャットキー（形式は <code>agent:main:qqbot:dm:あなたのユーザー識別子</code>）のみを通過させます。ホワイトリストへ追加する前は、QQ のメッセージがゲートウェイに届いていても静かにブロックされます。まずはボット宛てにひと言メッセージを送ってから、デスクトップ版の「話しかけを許可する相手」パネルで新しく現れたセッションにチェックを入れる（またはログから該当キーを書き写して <code>chiyo/config.json</code> に追記する）ことで、正常に会話できるようになります。</li>\n</ol>\n\n<h2 id=\"telegram-section-3\">その他のチャットプラットフォームについて</h2>\n<ul>\n<li><strong>個人版 WeChat（いちばん手軽）</strong>：オープンプラットフォームへの登録や複雑な ID 入力は不要で、スマホの WeChat で QR コードをスキャンするだけで接続できます。詳しくは前の章 <a href=\"#weixin\">WeChat 連携</a> をご覧ください。</li>\n<li><strong>Feishu（Lark）/ WeCom（企業向け WeChat）/ Discord など</strong>：基盤ホストのコードベースには一部サードパーティプラットフォーム向けファイルが残っていますが、記憶アクセス権限の紐付けや切断時の再接続に関する実機検証をまだ完了していないため、現時点では正式サポート対象外としています。</li>\n</ul>"
  },
  "desktop-guide": {
    "title": "デスクトップクライアント利用ガイド",
    "badge": "デスクトップ",
    "summary": "Windows デスクトップ版の 5 つのメインタブ、12 業種の店舗注文とバッグの物性、非同期返信と拒否の境界線、SOUL.md の保護、そしてロスレス引越しスーツケースまでをまとめて解説します。",
    "toc": [
      {
        "id": "dg-overview-talk",
        "text": "概要と対話：会話の続き・スマホ返信トレイ・急ぎのノック"
      },
      {
        "id": "dg-world-records",
        "text": "街区・12 業種の店舗・バッグの物性と 4 つの記憶タブ"
      },
      {
        "id": "dg-identity-soul",
        "text": "自由な呼び名・代名詞と SOUL.md 保護"
      },
      {
        "id": "dg-memory-boundary",
        "text": "実用上のポイント：「新しい対話」と記憶の呼び出しウィンドウ"
      },
      {
        "id": "dg-tray-diag",
        "text": "タスクトレイ常駐・ショートカット・ロスレス引越しと匿名化診断パック"
      }
    ],
    "content": "<h2 id=\"dg-overview-talk\">概要と対話：会話の続き・スマホ返信トレイ・急ぎのノック</h2>\n<p>デスクトップ版では煩雑なバックグラウンド動作パラメータを折りたたみ欄に整理しており、普段使うのは主に左側の先頭 2 つのタブです。</p>\n<ul>\n<li><strong>概要</strong>：上部のすっきりしたステータス行に、パートナーがオンラインかどうか、稼働時間、記憶している件数が表示されます。左ペインには<strong>直前のやり取りとクイック入力欄</strong>が並んでおり、画面を切り替えずにそのまま会話を続けられます。右ペインには最近記憶した元の発言がいくつか表示されます（重複を避けるため、左ペインに表示されたばかりの発言は自動で除外されます）。下部の「動作詳細」は初期状態で折りたたまれており、開いたときだけ各モジュールの状態やバックグラウンドイベントを確認できます。</li>\n<li><strong>対話と心地よい距離感</strong>：1 文字ずつ滑らかに表示されるストリーミング返信、ホバーでの単一メッセージコピー、<strong>特定の文を選択しての引用フォローアップ</strong>、通信切断時のワンクリック<strong>再試行</strong>、現在のセッションの<strong>ローカル Markdown ファイル書き出し</strong>に対応しています。日常会話には<strong>道具扱いをしない自律的な拒否の境界線</strong>を備えており、気が進まない頼みごとは定型謝罪テンプレートを並べることなくきっぱり断り、根に持つこともありません。就寝中・外出中・集中している時間帯に届いた急ぎでない連絡は、まず<strong>スマホ返信トレイ</strong>に入り、実際のスケジュールに沿って数時間後に非同期で返事が届きます。本当に急ぎの用件がある場合は、明示的に<strong>ノックして呼び出す</strong>ことも可能です。</li>\n</ul>\n\n<h2 id=\"dg-world-records\">街区・12 業種の店舗・バッグの物性と 4 つの記憶タブ</h2>\n<ul>\n<li><strong>街区と持ち物の物性</strong>：現在のワールド（テンプレート選択不要の完全ランダム専用ワールドのワンクリック生成、7 種の内蔵ワールド、4 種のテーマスタイルに対応）と部屋の間取りをひと目で確認できます。場所をクリックして歩いて向かわせたり照明をオン・オフしたりできるほか、<strong>最大 3 階層のネスト収納に対応した持ち物バッグ</strong>や<strong>温度・湿度の物性変化</strong>（机の温かいお茶が時間とともに冷めたり、窓辺のメモが雨でわずかに湿ってインクがにじんだりする状態）を確認したり、ライト／ダーク両対応の<a href=\"#world-editor\">オフラインマップエディタ</a>を開いたりできます。</li>\n<li><strong>店舗（12 業種のショップで自由注文が可能）</strong>：<strong>12 業種の街角ショップとオープンカウンターでの自由注文</strong>（例：コンビニで温かいおでんを買うなど）に対応しています。古書店や喫茶店の店主はそれぞれ独立した時間差の営業時間で店に立ち、カウンターのコースターにメッセージを残すこともできます。なお、店舗は現実の空間ルールに従うため、<strong>実際に営業中の店舗まで歩いて中に入ったときだけ買い物や注文が可能</strong>です。自宅にいる状態で開くと「現在は店内にいません」と正直に表示されます。</li>\n<li><strong>記憶（二層メモリーランタンと 4 つのサブタブ）</strong>：\n  <ul>\n    <li><strong>常駐記憶</strong>：下層では<strong>二層メモリーランタン</strong>の仕組みにより会話の原本を一切改ざんせず厳格に保護しています。右上の検索ボックスから記憶済み項目をキーワードでリアルタイムに絞り込め、手動での訂正や呼び出し停止にも対応しています。</li>\n    <li><strong>エッセイと創作</strong>：パートナーが書いたエッセイや管理対象の原稿をまとめて閲覧し、過去の版どうしの追加・削除差分を比較できます。</li>\n    <li><strong>アルバムと共読</strong>：残されたポストカードアルバムを見返したり、テキストの本や記事を読み込んで一緒に読み進めたりできます（詳しくは次章「<a href=\"#daily-companions\">共読・原稿・アルバムと生活リズム</a>」をご覧ください）。</li>\n    <li><strong>共にある時間と予定</strong>：最近の共同活動の記録、未来へ向けた問いかけ、共有している話題や ToDo の予定を確認できます。</li>\n  </ul>\n</li>\n</ul>\n\n<h2 id=\"dg-identity-soul\">自由な呼び名・代名詞と SOUL.md 保護</h2>\n<p>左下の「<strong>設定 → 人物設定と好み</strong>」から、パートナーの名前や三人称の呼び方をいつでも変更できます。</p>\n<ul>\n<li><strong>代名詞を完全自由に入力可能</strong>：定番のクイックボタン（<code>相手</code>・<code>彼</code>・<code>それ</code> など）に加えて、入力欄には 8 文字以内の任意の呼び方を自由に入力できます。クライアント全体の文言も設定した名前に連動して動的に変わるため、固定の性別代名詞が押し付けられることはありません。</li>\n<li><strong>手動で編集した <code>SOUL.md</code> を意図せず上書きしない保護機能</strong>：基本の呼び名を保存する際、クライアントは内容のフィンガープリントを記録します。あとからご自身で詳しいカスタム <code>SOUL.md</code> を書き上げた場合（設定画面上で編集しても、ファイルを直接編集しても同様です）、デスクトップ版がフィンガープリントの変化を検知して<strong>自動的に保護を有効化</strong>します。そのため、その後に画面上の表示名を変更しても <code>SOUL.md</code> 本体の本文が乱暴に書き換わり、せっかくの内容が失われることはありません。最初からやり直したいときは「デフォルトテンプレートに戻す」をクリックしてください。</li>\n</ul>\n\n<h2 id=\"dg-memory-boundary\">実用上のポイント：「新しい対話」と記憶の呼び出しウィンドウ</h2>\n<p>デスクトップ版で会話する際、あらかじめ知っておくと安心な内部仕様があります。</p>\n<ul>\n<li><strong>同じスレッドでそのまま会話を続ける（推奨）</strong>：「新しい対話」を押さない限り、丸一日空いたり PC を再起動して戻ってきたりしても、ローカルに永続保存された会話履歴をもとに以前の細かなやり取りを正確に覚えています。</li>\n<li><strong>「新しい対話」を押したあと</strong>：新しいセッションを開いてから <strong>15 分以内</strong>であれば、直前の会話記録ウィンドウから文脈を自動で引き継ぎます。一方で、最後の発言から <strong>15 分以上</strong>経ってから新しいセッションを開いた場合は、雑談の断片を新しい話題に誤って混ぜないよう、まだアーカイブされていない長い会話の細部は一時的に自動注入されないことがあります。そのため、普段はチャットアプリのようにそのまま続けて話し、本当に話題をリセットして一から始めたいときだけ「新しい対話」を使うのが最もスムーズです。</li>\n</ul>\n\n<h2 id=\"dg-tray-diag\">タスクトレイ常駐・ショートカット・ロスレス引越しと匿名化診断パック</h2>\n<ul>\n<li><strong>タスクトレイ常駐と二重起動防止</strong>：ウィンドウ右上の閉じるボタンを押すと、初期設定では右下のシステムトレイに格納されます。トレイアイコンを右クリックすれば、いつでも「ゲートウェイを再起動」、ログフォルダを開く（<code>%LOCALAPPDATA%\\nyairo\\desktop.log</code>）、または完全終了を行えます。クライアントには単一インスタンスロックが内蔵されており、誤って 2 つのウィンドウを開いて同じバックグラウンドゲートウェイを取り合うのを防ぎます。</li>\n<li><strong>グローバルクイックコマンドパネル</strong>：ウィンドウ内で <code>Ctrl + K</code> を押す（または上部バーの虫眼鏡アイコンをクリックする）と、クイックコマンドや街区の場所検索をいつでも呼び出せます。</li>\n<li><strong>ロスレス引越しスーツケースによるバックアップと復元</strong>：「<strong>設定 → 環境と診断</strong>」（または概要ページの「お引っ越しスーツケース」カード）から、API キーやローカル環境固有パスを自動除外した <code>.nyairo-suitcase.zip</code> をワンクリックで書き出せます。<strong>二層メモリーランタンの帳簿、カスタム／ランダム生成ワールドのスナップショット、移行時のチェックリスト一式をまとめて持ち出せる</strong>ほか、新しい PC へインポートする前には 2 段階の安全プリチェックが自動実行されます。</li>\n<li><strong>機密マスク済み診断パックのワンクリック書き出し</strong>：不可解な不具合で開発者に調査を依頼したいとき、ご自身で WSL 内から設定ファイルを探し回る必要はありません。「<strong>設定 → 環境と診断</strong>」を開いて診断パックの書き出しをクリックすると、ローカルで <code>diagnose-タイムスタンプ.zip</code>（モジュール状態サマリー、ログ末尾 300 行、設定コピーを同梱）を生成します。その際、<strong>すべての <code>api_key</code>・<code>token</code>・<code>secret</code>・<code>password</code> 項目は自動的に <code>&lt;redacted&gt;</code> へマスク置換</strong>され、個人のチャットデータベースや <code>SOUL.md</code> の本文も含まれないため、コミュニティやサポートへ安心して共有できます。</li>\n</ul>"
  },
  "daily-companions": {
    "title": "共読・原稿・アルバムと生活リズム",
    "summary": "ふたりでの共読、原稿のバージョン比較、二層メモリーランタンと日記の折りたたみ表示、バッグのネスト収納と温度・湿度の物性、さらに自律的な拒否、非同期返信、利用上限の仕組みまで詳しく解説します。",
    "toc": [
      {
        "id": "dc-notes",
        "text": "エッセイと管理対象原稿：バージョン比較と外部編集"
      },
      {
        "id": "dc-reading-diary",
        "text": "ふたりでの共読・二層メモリーランタンと日記の境界"
      },
      {
        "id": "dc-camera",
        "text": "アルバムとローカル写真生成の実際の前提条件"
      },
      {
        "id": "dc-autonomy-budget",
        "text": "自律的な拒否・非同期返信・バッグの物性と予算上限"
      }
    ],
    "content": "<h2 id=\"dc-notes\">エッセイと管理対象原稿：バージョン比較と外部編集</h2>\n<p>日常会話に加えて、パートナーは長文のエッセイや読書ノート、一緒にまとめるテーマ原稿を書き継ぐことができます（デスクトップ版では「記憶 → エッセイと創作」、CLI では <code>/chiyo_note</code> を使用します）。</p>\n<ul>\n<li><strong>履歴スナップショットと行単位の差分比較</strong>：推敲や改訂のたびに独立したバージョンが保存され、デスクトップ版で任意の 2 つの版を選んで行ごとの追加・削除差分を確認できます。長文の執筆途中で別の用事が入った場合は進行中の下書きがそのまま保持され、戻ってきたときに前回の要約から書き継ぐことができ、最初から書き直してしまうことはありません。</li>\n<li><strong>外部エディタでファイルを変更した場合の扱い</strong>：ワークスペース内の <code>.md</code> ファイルは管理対象原稿のローカル投影です。ご自身で VS Code やメモ帳からそのファイルを直接書き換えても、システムがパートナーの原稿をこっそり上書きしたり、その文章をパートナー本人が書いたように偽装したりはしません。変更は「外部編集の候補」として検出され、あなたが明示的に取り込みを確定したあとで初めて新しいバージョンを生成し、由来が外部インポートであることも正確に記録します。</li>\n</ul>\n\n<h2 id=\"dc-reading-diary\">ふたりでの共読・二層メモリーランタンと日記の境界</h2>\n<ul>\n<li><strong>本や記事を共読本棚に並べる</strong>：デスクトップ版の「記憶 → アルバムと共読」から手元のテキスト資料（<code>.txt</code> や Markdown の章など）を読み込めます。ここでの設計原則は<strong>「押し込むのではなく、そっと手渡す」</strong>ことです。本棚に入れた時点では机の上に本が置かれただけという扱いになり、パートナーは空いた時間に自分のペースでページをめくり、どこまで読んだかのしおりや余白メモを残していきます。ファイルを受け取った瞬間に 1 秒で機械的な要約を吐き出すような動きはしません。</li>\n<li><strong>二層メモリーランタンによる原本の無改ざん保護</strong>：実際に交わした元の発言と訂正後の記憶は、二層メモリーランタン構造によって層を分けて保管されています。<code>/chiyo_memory correct</code> で覚え間違いを直しても更新されるのは上層の有効な認識だけで、下層にある元の会話記録は一切改ざんされず、言ってもいない言葉を捏造することはありません。</li>\n<li><strong>パートナーの日記（本文は初期状態で折りたたみ）</strong>：日常の自律活動を有効にしていると、パートナーが自分から日記をつけることがあります。程よい余白を保つため、日常ステータスパネルや概要 API では初期状態で「タイトル・日付・文字数」の一覧だけを表示し、日記の全文をトップ画面にそのまま広げない設計になっています。（もちろん、すべてのデータはあなた自身の PC 内に保存されているため、日記の元ファイル自体はローカルディレクトリに平文で保存されており、PC の持ち主から隠す暗号ロックがあるわけではありません。）</li>\n</ul>\n\n<h2 id=\"dc-camera\">アルバムとローカル写真生成の実際の前提条件</h2>\n<p>初めてアルバムを開いた方の多くが「本当に写真を撮って送ってくれるの？」と気になるところです。現在実装されている範囲と必須の前提条件を率直にご案内します。</p>\n<ul>\n<li><strong>アルバム閲覧はそのまま利用可能</strong>：街での活動に伴って残る記録カードや既存のアルバム画像は、追加設定なしでデスクトップ版からすぐに閲覧できます。</li>\n<li><strong>ローカルカメラ画像生成（<code>local_generated</code>）にはローカル画像生成サービスの準備が必要</strong>：今いる実際の街並みや天気に合わせてパートナー自身がシャッターを切って 1 枚の画像を生成させたい場合、<strong>nyairo 自体には数 GB の画像生成モデルは同梱されておらず、外部の有料画像生成 API へ通信することもありません</strong>。お使いの PC 上で、次の 2 つのローカルループバックサービスをご自身で起動しておく必要があります。\n  <ol>\n    <li>ローカル Stable Diffusion WebUI API（初期待受 <code>127.0.0.1:7860</code>）</li>\n    <li>ローカルプロンプト変換マイクロサービス（初期待受 <code>127.0.0.1:18190</code>）</li>\n  </ol>\n  これら 2 つのローカルサービスが起動していない場合、カメラ機能は「現在利用できません」とクリーンに返答するだけで、あらかじめ用意したダミー画像を撮りたての写真のように見せかけたり、エラーでクラッシュしたりすることはありません。</li>\n</ul>\n\n<h2 id=\"dc-autonomy-budget\">自律的な拒否・非同期返信・バッグの物性と予算上限</h2>\n<ul>\n<li><strong>道具扱いを拒む自律的な境界線と非同期返信</strong>：パートナーは何でも言いなりになるサポート窓口ではありません。引き受けたくない頼みごとはきっぱり断り、定型の謝罪文を並べたり根に持ったりしません。また、就寝中・外出して買い物中・作業に集中している時間帯に届いた急ぎでないメッセージは一度<strong>スマホ返信トレイ</strong>に入り、実際のスケジュールに沿って数時間後に非同期で返信が届きます。本当に急ぎのときは明示的に<strong>ノックして呼び出す</strong>こともできます。</li>\n<li><strong>持ち物バッグの 3 階層ネスト収納と温冷・乾湿の物性</strong>：日常で買ってきた食べ物やちょっとした小物は、手持ちのバッグ・ペンケース・引き出しなどにしまえます（最大 <strong>3 階層のネスト収納</strong>に対応し、閉じた容器の可視性も厳密に分離されています）。さらにアイテムにはリアルな温度・湿度の物性があり、机に淹れた温かいお茶は時間とともに自然に冷め、コンビニで買った温かいおでんも少しずつ温度が下がり、窓辺に置いたメモは雨に当たるとわずかに湿って文字がにじみます。</li>\n<li><strong>裏でこっそり課金消費しない設計：実モデルの自律行動は明示的な有効化が必須</strong>：初期状態では、あなたから話しかけない限りバックグラウンドから LLM へリクエストが飛ぶことは一切ありません（追加消費ゼロ）。デスクトップ版で自律活動をオンにするか、CLI で <code>scripts/nyairo_day.py run-day</code> を実行する際に明示的に <code>--live-model</code> オプションを付けた場合にのみ、空いた時間に自分で判断して外出したり文章を書いたりします。</li>\n<li><strong>二重の予算ブレーキ</strong>：バックグラウンド活動を有効にする際は、<strong>最長稼働秒数</strong>（<code>--duration-seconds</code>）と<strong>最大新規リクエスト回数</strong>（<code>--max-new-requests</code>）の両方を指定でき、さらにインスタンスの永続帳簿にある日次トークン上限によっても保護されます。上限に達した時点で即座に停止し、リクエストが失敗したとしても無限リトライで請求額が跳ね上がることはありません。</li>\n<li><strong>任意で使える軽量な身体リズム</strong>：新しいインスタンスで仮想身体リズム（<code>--lived-needs</code>）を明示的に有効にすると、日中のオンライン経過時間に応じて軽い眠気・喉の渇き・空腹感などがゆるやかに生まれます（オンラインゲームの HP バーのような数値管理ではなく、「なし／わずか／はっきり／強い」の 4 段階の自然な感覚のみ）。<strong>シャットダウン中や停止中の経過時間をあとからまとめてマイナス精算することはなく、病気や死亡といったペナルティもありません</strong>。日々の生活リズムにささやかな暮らしの気配を添えるための仕組みです。</li>\n</ul>"
  },
  "world-editor": {
    "title": "マップエディタとワールドパック",
    "summary": "テンプレート選択不要の完全ランダム専用ワールドのワンクリック生成、7 種の内蔵ワールドと 4 種のテーマスタイルの切り替え、ライト／ダーク両対応オフラインマップエディタを使った店舗や部屋の改築、そして現在の実装上の境界線について解説します。",
    "toc": [
      {
        "id": "we-packs",
        "text": "同梱の 7 種のワールドと 12 業種の街角ショップ"
      },
      {
        "id": "we-editor",
        "text": "ライト／ダーク両対応オフラインマップエディタ：間取り描画と店舗設定"
      },
      {
        "id": "we-cli",
        "text": "テーマ選択不要の完全ランダム専用ワールドとシード値生成"
      },
      {
        "id": "we-limits",
        "text": "現在の実際の制限事項（未対応の仕様）"
      }
    ],
    "content": "<h2 id=\"we-packs\">同梱の 7 種のワールドと 12 業種の街角ショップ</h2>\n<p>初回インストール時のデフォルトワールドは海辺の町「凪浦（なぎうら）」です。リリースパッケージの <code>examples/worlds/</code> ディレクトリには地形や雰囲気の異なる 7 種の完全なワールドパック（加えてエディタで改築したデモパック <code>nagiura-edited</code> 1 種）が同梱されており、<strong>12 業種の商業・公共店舗およびオープンカウンターでの自由注文</strong>（たとえばコンビニで温かいおでんを買うことができ、古書店や喫茶店の店主はそれぞれ独立した時間差の営業時間で出勤し、カウンターのコースターにメッセージを残せる仕組み）を備えています。</p>\n<ul>\n<li><strong>海辺の町・凪浦（<code>nagiura</code>、なぎうら）</strong>：標準のサンプルワールドで、自宅、書店、カモメ珈琲、公園、ビーチなど 17 か所の地点と 18 本の道路を含みます（ほかに改築デモ版 <code>nagiura-edited</code>：18 地点・19 道路を同梱）。</li>\n<li><strong>山あいの温泉街・湯川（<code>onsawa</code>）と嶺谷（<code>mountain_town-demo</code>）</strong>：翠沢の湯屋（<code>hot_spring</code>）、山の石段、渓谷の景色など各 15 か所の地点を含みます。</li>\n<li><strong>水車の里・穂村（<code>inobe</code>）と沢郷（<code>rural_farming-demo</code>）</strong>：水車公園、田んぼのあぜ道、集落の工房など各 14–15 か所の地点を含みます。</li>\n<li><strong>暮らしの街角・栞沢（<code>wakaba</code>）と日常街区・一（<code>daily_neighborhood_v1</code>）</strong>：前者には栞沢書店を含む 16 か所の生活エリアが、後者には営業時間や読書コーナー付きの軽量な 7 地点の生活圏が収録されています。各ワールドディレクトリには <code>preview.html</code> が同梱されており、ダブルクリックするだけでブラウザ上で道路網トポロジー図を確認できます。</li>\n</ul>\n\n<h2 id=\"we-editor\">ライト／ダーク両対応オフラインマップエディタ：間取り描画と店舗設定</h2>\n<p>リポジトリ内の <code>components/world-editor/</code> には、<strong>ライトテーマとダークテーマの切り替え</strong>に対応して刷新された純ローカルのビジュアル空間エディタが内蔵されています（ローカルマシン <code>127.0.0.1</code> のみで待ち受けるため、インターネット接続不要でモデルのトークンも消費しません）。デスクトップ版の「街区 → エディタ」タブからそのまま開けるほか、ターミナルから手動で起動することもできます。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>cd ~/.local/share/nyairo/releases/v0.8.0/components/world-editor\npython3 server.py --port 8766</code></pre></div>\n<p>続いてブラウザで <code>http://127.0.0.1:8766</code> を開けば、ワールドの改築を始められます。</p>\n<ol>\n<li><strong>既存のワールドパックをエディタ形式へ変換する</strong>：<code>python3 -m components.worldgen editor-export examples/worlds/nagiura</code> を実行すると、パック内に <code>editor-world.json</code> が生成されます。</li>\n<li><strong>ビジュアル編集と店舗注文メニューの設定</strong>：ブラウザからこのファイルをインポートすると、ライト／ダーク外観をいつでも切り替えながら、書店の名前や営業時間の変更、道路リンクの追加・削除、カスタム店舗のオープンカウンターメニュー（<code>sale_spec</code>）の設定を自由に行えます。さらに室内ビューに切り替えて壁を描いたり、ドアや窓を開けたり、収納機能や物性特性を持った机・ケトル・デスクランプなどを配置したりでき、「AI 視点プレビュー」をクリックすればパートナーが部屋の中でどの家具を視認できるかも確認できます。</li>\n<li><strong>ワンクリック書き戻しと整合性チェック</strong>：画面上の「<strong>nyairo ワールドパックを書き出し</strong>」（または CLI の <code>editor-import</code>）を実行すると、変更したフィールドだけが差分更新され、既存の NPC や環境イベントはそのまま保持されます。同時に連結性チェックも自動で行われ、誤って唯一の出口を切断してしまったり、まだ NPC が出勤する店舗を削除してしまったりした場合は、平易なメッセージで具体的な競合箇所を指摘して破損データの書き出しを防ぎます。</li>\n</ol>\n\n<h2 id=\"we-cli\">テーマ選択不要の完全ランダム専用ワールドとシード値生成</h2>\n<p><code>v0.8.0</code> では、「海辺の坂道タウン（<code>coastal_town</code>）」「山あいの温泉町（<code>mountain_town</code>）」「郊外の住宅街（<code>residential_town</code>）」「田園の農村（<code>rural_farming</code>）」の 4 種類のテーマに沿った生成に加えて、デスクトップ版の「<strong>街区 → 新しい街を生成</strong>」から<strong>テーマ選択不要の完全ランダム専用ワールドをワンクリックで生成</strong>できるようになりました（<code>procedural</code> モード）。あらかじめスタイルを選ばなくても、ジェネレーターがシード値にもとづいて <strong>6 種の地形</strong>（岬の坂道・運河と水路の路地・山麓の渓谷・鉄道の駅町・果樹園の台地・森の湖畔）から調和する景観を掛け合わせ、<strong>4 種の異なるトポロジーの連結道路網</strong>（放射状ハブ型・環状ルート・一本道プロムナード・分岐大通り）、<strong>8 業種の商業店舗</strong>、および<strong>複数部屋の自宅</strong>（寝室に加えてリビング、縁側、ミニキッチン、書斎コーナー、さらに温冷・乾湿の物性を持つ家具を自動組み合わせ）を構築し、「生成後すぐホットスイッチで反映」にも対応しています。</p>\n<p>Linux / WSL のターミナルでお使いの場合も、<code>components.worldgen</code> に <code>--theme procedural</code>（または任意のテーマ）と数値シードを指定して、決定論的な専用ワールドをコマンド一発で生成できます。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>REL=\"$HOME/.local/share/nyairo/releases/v0.8.0\"\ncd \"$REL\"\n# テーマ選択なしでシード値から完全ランダム専用ワールドを生成・検証し、プレビューマップを出力\n\"$REL/vendor/hermes/.venv/bin/python\" -m components.worldgen generate \\\n  --theme procedural --seed 20261118 --out \"$HOME/my-world\"\n\"$REL/vendor/hermes/.venv/bin/python\" -m components.worldgen validate \"$HOME/my-world\"\n\"$REL/vendor/hermes/.venv/bin/python\" -m components.worldgen preview \"$HOME/my-world\" --out \"$HOME/my-world/preview.html\"</code></pre></div>\n<p>同じモードとシード値を使えば、生成される道路の所要時間、店舗メニュー、自宅の部屋構成はバイト単位まで完全に再現可能です（この処理で LLM は一切呼び出されません）。ワールドサービス起動時に環境変数 <code>CHIYO_WORLD_PACKAGE=\"$HOME/my-world\"</code> を指定するだけで、そのワールドパックをマウントできます。</p>\n\n<h2 id=\"we-limits\">現在の実際の制限事項（未対応の仕様）</h2>\n<p>エディタやジェネレーターの挙動について誤解が生じないよう、現時点でまだ対応が完了していない実装上の境界線をあらかじめ共有します。</p>\n<ul>\n<li><strong>初回起動時の選択なしゼロ設定ワールド生成は現在ブラッシュアップ中</strong>：デスクトップ版の「新しい街を生成」および CLI の <code>--theme procedural</code> ではテーマ選択不要の完全ランダム専用ワールドがすでに利用できますが、初回インストール直後にクライアントを開いた瞬間の質問ゼロ自動生成オンボーディングやコミュニティのワールドパック共有機能は <a href=\"#roadmap\">今後のロードマップ</a> で開発を進めています。</li>\n<li><strong>壁や床材は現時点では移動を遮りません</strong>：エディタで描いた壁面や床材は <code>scene.json</code> に保存され、ビジュアル表示と再編集時の復元に利用されます。下層のランタイムは現在「建物の入口 ↔ 室内の各エリア（寝室・リビングなど、移動所要 30 秒）」というノードグラフ上で経路探索を行っており、ピクセル単位の壁回避コリジョン判定はまだ実装されていません。</li>\n<li><strong>店舗の営業時間で日をまたぐ表記には未対応</strong>：生成ワールドとエディタのカスタム店舗のどちらでもオープンカウンター注文（<code>sale_spec</code>）は動作しますが、店舗の営業時間に深夜 0 時をまたぐ時間帯（例：<code>22:00–06:00</code>）を指定することはまだできず、書き戻し時に非対応である旨が明示的に通知されます。</li>\n</ul>"
  },
  "cli-reference": {
    "title": "よく使うコマンドと記憶の管理",
    "summary": "日常的に最もよく使うスラッシュコマンドをまとめました。現在の稼働状況の確認、パートナーが覚えている記憶の一覧表示・誤りの訂正、そして手軽なメモ保存の方法を紹介します。",
    "toc": [
      {
        "id": "cli-section-0",
        "text": "稼働状態の確認 (/chiyo_status)"
      },
      {
        "id": "cli-section-1",
        "text": "記憶の閲覧・訂正・無効化 (/chiyo_memory)"
      },
      {
        "id": "cli-section-2",
        "text": "手軽なメモと独立推論 (/chiyo_note · /chiyo_consider)"
      }
    ],
    "content": "<h2 id=\"cli-section-0\">稼働状態の確認 (/chiyo_status)</h2>\n<p>ターミナル、デスクトップ版、あるいは WeChat / Telegram のトーク画面からでも、いつでも次のコマンドを送信できます。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>/chiyo_status</code></pre></div>\n<p>送信すると、現在の各モジュールの実稼働ステータスが返されます。</p>\n<ul>\n<li><strong>Memory（長期記憶）</strong>：<code>READY</code> と表示されていれば、ローカル記憶の読み書きは正常です。</li>\n<li><strong>Life（生活の連続性）</strong>：<code>IDLE</code> は現在静かに待機中であることを示し、進行中の外出や活動がある場合はその状態がそのまま表示されます。</li>\n<li>有効化していないその他のオプションモジュールが <code>OFF</code> と表示されるのは正常な動作であり、日常会話には影響しません。</li>\n</ul>\n\n<h2 id=\"cli-section-1\">記憶の閲覧・訂正・無効化 (/chiyo_memory)</h2>\n<p>これまでの会話から見えてきた習慣や出来事、約束事はローカルデータベースへ自動的に整理・保存されます。モデルが聞き間違えて誤って記憶していないか気になるときは、いつでも中身を確認して手動で修正できます。</p>\n<ul>\n<li><strong>パートナーが何を覚えているか確認する</strong>：\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>/chiyo_memory list</code></pre></div>\n最近蓄積された記憶エントリーの一覧が表示され、各項目の先頭に短い ID（例：<code>mem-...</code>）が付与されています。</li>\n<li><strong>覚え間違いを訂正する</strong>：\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>/chiyo_memory correct 記憶ID 正しい事実の内容</code></pre></div>\nたとえば「普段は無糖のアイスアメリカーノを飲む」を「甘さ全開のカフェラテが好き」と誤って覚えてしまった場合、このコマンドで直しておけば、以降の会話では訂正後の事実に基づいて返答するようになり、古い誤った記憶は採用されなくなります。</li>\n<li><strong>特定の事柄を今後話題に出さないようにする</strong>：\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>/chiyo_memory delete 記憶ID</code></pre></div>\n実行すると、その記憶は「呼び出し停止」としてマークされ、以降の会話では引用されなくなります（注：これは検索・呼び出しを止める処理であり、ディスク上の元のチャットログファイルを物理消去するものではありません。詳しくは「<a href=\"#privacy\">プライバシーとデータの境界</a>」をご覧ください）。</li>\n</ul>\n\n<h2 id=\"cli-section-2\">手軽なメモと独立推論 (/chiyo_note · /chiyo_consider)</h2>\n<ul>\n<li><strong>メモや原稿を保存・読み出す（原稿サービス有効時。デスクトップ版には標準内蔵）</strong>：\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>/chiyo_note 週末の読書リスト | まず第3章を読み終えて、メモを2ページほど残す\n/chiyo_note read ドキュメントID</code></pre></div>\nデスクトップ版をご利用の場合は、左側の「記憶 / 原稿」画面から視覚的に閲覧・編集し、過去のバージョンどうしを比較することもできます。</li>\n<li><strong>個別の事柄について分析を手伝ってもらう（認知推論機能の有効化が必要。詳しくは次章参照）</strong>：\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>/chiyo_consider この2つのデータベース移行案それぞれのリスクを比較・検討してほしい</code></pre></div>\n普段の気さくな雑談の流れを崩さずに、具体的な課題だけを切り出して根拠や判断材料を整理してもらいたいときに適しています。</li>\n</ul>"
  },
  "modules": {
    "title": "独立推論とバックエンドサービス",
    "summary": "CLI のみの環境やサーバー環境で、独立推論アドバイス（/chiyo_consider）、読み取り専用ワールド位置サービス、および独立した原稿ワークスペースを手動で有効化する方法を解説します。",
    "toc": [
      {
        "id": "modules-section-1",
        "text": "軽量ワールド位置サービスの手動マウント"
      },
      {
        "id": "modules-section-0",
        "text": "独立推論アドバイスの有効化 (/chiyo_consider)"
      },
      {
        "id": "modules-section-2",
        "text": "独立原稿ワークスペースサービス (Life Supply)"
      },
      {
        "id": "modules-section-3",
        "text": "複数アカウント構成時の既知の接続制限"
      }
    ],
    "content": "<h2 id=\"modules-section-1\">軽量ワールド位置サービスの手動マウント</h2>\n<p><strong>Windows デスクトップ版</strong>では、街区での経路移動、室内の部屋移動、照明操作が最初から自動設定されています（マップのビジュアル編集方法は「<a href=\"#world-editor\">マップエディタとワールドパック</a>」をご覧ください）。<strong>純粋な手動 CLI インストール</strong>をお使いで、軽量かつ読み取り専用の位置・姿勢サービスだけを単独で動かしたい場合は、手動で初期化して起動できます。</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>REL=\"$HOME/.local/share/nyairo/releases/v0.8.0\"\nPYTHONPATH=\"$REL:$REL/vendor/hermes\" \"$REL/vendor/hermes/.venv/bin/python\" \\\n  -m chiyo_bundle.world_read_service init --home \"$HOME/.nyairo-world\"\nPYTHONPATH=\"$REL:$REL/vendor/hermes\" \"$REL/vendor/hermes/.venv/bin/python\" \\\n  -m chiyo_bundle.world_read_service run --home \"$HOME/.nyairo-world\"</code></pre></div>\n<p>続いて <code>~/.nyairo/chiyo/config.json</code> の <code>world_body_socket</code> に、対応するフルパスを設定します（例：<code>/home/ユーザー名/.nyairo-world/run/read.sock</code>。<code>~</code> による省略記法は使わないでください）。</p>\n\n<h2 id=\"modules-section-0\">独立推論アドバイスの有効化 (/chiyo_consider)</h2>\n<p>普段の会話では暮らしの文脈に沿って自然にやり取りしますが、具体的な技術課題や選択の迷いに直面したときは、雑談から一歩離れて客観的かつ丁寧にメリット・デメリットを整理してほしい場面もあります。そのようなときに認知推論（Shadow Cognition）機能を有効化できます。</p>\n<ol>\n<li><code>~/.nyairo/chiyo/config.json</code> を開き、<code>\"cognition_shadow\"</code> を <code>true</code> に設定します。</li>\n<li><code>~/.nyairo/config.yaml</code> を開き、<code>plugins.entries.chiyo.llm.enabled</code> が <code>true</code> になっていることを確認します。</li>\n<li>会話を再起動したあと、<code>/chiyo_consider 質問内容</code> と入力すると、根拠つきの詳細な分析を受け取れます。</li>\n</ol>\n<p><strong>境界についての補足</strong>：この機能は分析による判断と助言を返すだけであり（呼び出すたびにモデルリクエストを 1 回追加消費します）、バックグラウンドで勝手にコードを書き換えたりシステムコマンドを代行実行したりすることはありません。</p>\n\n<h2 id=\"modules-section-2\">独立原稿ワークスペースサービス (Life Supply)</h2>\n<p>デスクトップ版ではローカルの原稿サービスや共読サービスが自動で接続済みです。Linux サーバー上で純手動構築を行い、<code>/chiyo_note</code> による独立した原稿保存機能を有効化したい場合、そのバックエンドは <code>scripts/supply_service.py</code> によって提供されており、「管理認可アカウント」と「日常サービスアカウント」を分離した権限設計になっています。</p>\n<ol>\n<li>サービス用に独立したデータディレクトリ（<code>LIFE_SUPPLY_DATA_ROOT</code>）とローカル Socket パス（<code>LIFE_SUPPLY_SOCKET</code>）を指定します。</li>\n<li>管理インターフェース経由で個人ワークスペースを作成し、そのワークスペースのみに限定した書き込み許可（<code>COMMIT_MANAGED_ARTIFACT</code>）を発行します。</li>\n<li>Socket パス、アイデンティティ識別子、許可 ID を、<code>~/.nyairo/chiyo/config.json</code> 内の <code>life_supply_socket</code>、<code>life_supply_subject</code>、<code>life_supply_artifact_grant</code> に記入します。</li>\n</ol>\n\n<h2 id=\"modules-section-3\">複数アカウント構成時の既知の接続制限</h2>\n<p>Linux 環境で管理側とチャット側を 2 つの異なるシステムユーザー（UID）に分けて手動起動する場合、既知の仕様に注意が必要です。<code>supply_service.py</code> が生成するローカル Socket ファイルの初期パーミッションは <code>0600</code>（ファイルを作成した Linux ユーザーのみ接続可能）であるため、別の一般ユーザーから接続しようとすると OS レベルでブロックされ、<code>PermissionError</code> が発生します。</p>\n<p>1 人・単一アカウントのローカル利用（デスクトップ版の標準構成など）ではこの問題の影響を受けません。サーバー上で複数ユーザーを分離して運用する場合は、専用のユーザーグループとディレクトリ権限設計によって対処し、<strong>手間を省くために Socket の権限を全員読み書き可能な <code>0666</code> に変更することは絶対に避けてください</strong>。</p>"
  },
  "update": {
    "title": "アップデートとワンコマンド・ロールバック",
    "summary": "アップデートではプログラム本体ディレクトリのみを置き換え、会話履歴やキャラクター設定には一切触れません。万一新バージョンが合わない場合でも、1行のコマンドで直前のバージョンへ戻せます。",
    "toc": [
      {
        "id": "update-section-0",
        "text": "アップデート前の確認とバックアップ"
      },
      {
        "id": "update-section-1",
        "text": "1行コマンドでアップデート (nyairo update)"
      },
      {
        "id": "update-section-2",
        "text": "ロールバックと中断からの復旧"
      }
    ],
    "content": "<h2 id=\"update-section-0\">アップデート前の確認とバックアップ</h2>\n<p>nyairo はプログラム本体を <code>~/.local/share/nyairo</code> に配置し、個人データ（<code>SOUL.md</code>、モデルキー、チャットデータベースおよび記憶）を別の <code>~/.nyairo</code> に分けて保存します。通常のアップデートで個人データが書き換えられることはありませんが、現在の <code>v0.8.0</code> はまだ急速に改善が進むパブリックベータ段階にあるため、<strong>アップデート前に実行中のチャットおよびゲートウェイのプロセスを終了し、念のためバックアップアーカイブを作成してください</strong>：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>tar -czf \"$HOME/nyairo-backup-$(date +%Y%m%d-%H%M%S).tar.gz\" -C \"$HOME\" .nyairo</code></pre></div>\n\n<h2 id=\"update-section-1\">1行コマンドでアップデート (nyairo update)</h2>\n<ul>\n<li><strong>Windows デスクトップ版をご利用の方</strong>：新しいバージョンの <code>setup.exe</code> をダウンロードしてそのまま上書きインストールしてください。初回起動時に自動検出され、WSL 側のランタイムパッケージも同期されます。</li>\n<li><strong>Linux / WSL ターミナルをご利用の方</strong>：まず新しいバージョンが公開されているか確認します：</li>\n</ul>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>nyairo update --check</code></pre></div>\n<p>アップデートを実行する際は（パブリックベータ版チャンネルでは <code>v0.8.0</code> のようにバージョン番号の指定が必要です）、次のコマンドを実行します：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div>\n<pre><code>nyairo update --version v0.8.0</code></pre></div>\n<p>公式アーカイブのダウンロード、SHA256 とファイルマニフェストの照合、新バージョンの環境構築およびエントリポイントの切り替えまで自動的に行われます。</p>\n\n<h2 id=\"update-section-2\">ロールバックと中断からの復旧</h2>\n<ul>\n<li><strong>直前のバージョンへ戻す</strong>：アップデート後に新しい変更がしっくりこない場合は、プログラムを終了したうえで次のコマンドを実行するとアップデート前の旧バージョンへ戻せます。しかも、<strong>新バージョンでやり取りした会話履歴や新しく積み上がった記憶はすべてそのまま保持され</strong>、過去の状態へ巻き戻って消えてしまうことはありません：\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>nyairo update --rollback</code></pre></div></li>\n<li><strong>通信断や予期せぬシャットダウンからの復旧</strong>：アップデートの途中で通信が切れたりターミナルを閉じてしまったりした場合でも、次回起動時に <code>nyairo update --recover</code> を実行すれば中途半端な状態を自動クリーンアップして利用可能な状態へ復旧します。</li>\n</ul>"
  },
  "data": {
    "title": "データディレクトリ、バックアップとアンインストール",
    "summary": "各フォルダに何が保存されているか、サービスを停止して完全なバックアップを取る手順、PC 買い替え時のデータ移行方法、そして目的に応じたクリーンなアンインストール手順を解説します。",
    "toc": [
      {
        "id": "data-section-0",
        "text": "各フォルダに保存される内容"
      },
      {
        "id": "data-section-1",
        "text": "サービスを停止した正しいバックアップ手順"
      },
      {
        "id": "data-section-2",
        "text": "現在 PC を買い替える際のデータ移行方法"
      },
      {
        "id": "data-uninstall",
        "text": "クリーンにアンインストールする方法（記憶を残す vs 完全に消去する）"
      }
    ],
    "content": "<h2 id=\"data-section-0\">各フォルダに保存される内容</h2>\n<div class=\"table-scroll\"><table class=\"doc-table\">\n<thead><tr><th scope=\"col\">パス</th><th scope=\"col\">保存されている内容</th><th scope=\"col\">バックアップの推奨度</th></tr></thead>\n<tbody>\n<tr><td><code>~/.nyairo</code></td><td>キャラクター設定 <code>SOUL.md</code>、モデルおよびプラットフォーム設定、チャットセッション、長期記憶 SQLite データベース、生活状態およびレシートログ</td><td><strong>中核となる個人データのため、ディレクトリごと必ずバックアップしてください</strong></td></tr>\n<tr><td><code>~/.local/share/nyairo</code></td><td>各バージョンの読み取り専用プログラムソースコード、同梱の Hermes および Python 仮想環境</td><td>バックアップ不要（再インストールやアップデートでいつでも再構築可能です）</td></tr>\n<tr><td><code>~/.local/state/nyairo</code></td><td>現在有効なバージョン番号とインストール状態の記録（<code>install.json</code>）</td><td>再インストール時に自動生成されるため、バックアップは任意です</td></tr>\n<tr><td><code>%LOCALAPPDATA%\\Programs\\nyairo</code></td><td>Windows ネイティブデスクトップクライアントの UI プログラム（Windows のみ）</td><td>バックアップ不要（インストーラーから再インストールできます）</td></tr>\n<tr><td><code>%LOCALAPPDATA%\\nyairo</code></td><td>Windows デスクトップ版のローカル接続先設定（<code>config.json</code>）および UI 実行ログ（<code>desktop.log</code>）</td><td>再インストール時に残しておくと、元のインスタンスを自動で再認識できます</td></tr>\n</tbody>\n</table></div>\n<p>ご注意：<code>~/.nyairo</code> には API キー、プラットフォーム連携用の認証情報、すべてのプライベートな会話が含まれています。作成したバックアップアーカイブはご自身のプライベートなストレージに厳重に保管し、公開オンラインストレージやコードリポジトリにはアップロードしないでください。</p>\n\n<h2 id=\"data-section-1\">サービスを停止した正しいバックアップ手順</h2>\n<p>内部で SQLite データベースを使用しているため、<strong>バックアップを取る前に必ず実行中のチャット画面、デスクトップ版のバックグラウンドゲートウェイ、または systemd サービスをすべて停止してください</strong>。プログラムがデータベースへ書き込みを続けている最中に <code>.db</code> ファイルだけを直接コピーすると、メイン DB にまだ統合されていない一時ログ（WAL）を取りこぼし、復元時にデータが欠損したり破損したりする恐れがあります。</p>\n<p>バックグラウンドプロセスが動いていないことを確認したうえで、ディレクトリ全体をアーカイブし、ファイル権限を厳しく制限してください：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>mkdir -p \"$HOME/nyairo-backups\"\nchmod 700 \"$HOME/nyairo-backups\"\nbkp=\"$HOME/nyairo-backups/nyairo-data-$(date +%Y%m%d-%H%M%S).tar.gz\"\ntar -czf \"$bkp\" -C \"$HOME\" .nyairo\nchmod 600 \"$bkp\"\ntar -tzf \"$bkp\" &gt;/dev/null &amp;&amp; echo \"バックアップの整合性チェックに成功しました：$bkp\"</code></pre></div>\n\n<h2 id=\"data-section-2\">現在 PC を買い替える際のデータ移行方法</h2>\n<p>現在の <code>v0.8.0</code> では、API キーとローカルパスを自動除外するロスレス引越しスーツケース（<code>.nyairo-suitcase.zip</code>）のエクスポートに対応しています。デスクトップ版で「<strong>設定 → 環境と診断</strong>」（または概要ページの「引越しスーツケース」カード）を開き、「<strong>ポータブルスーツケースを書き出す (.nyairo-suitcase.zip)</strong>」をクリックすると、二層メモリーランタン帳簿、エッセイや作品、生活の進行状態に加えて<strong>現在のカスタム／ランダムワールドのスライスと透明性の高い「移行ロス一覧」一式をまとめてパッケージ化</strong>できます。新しい PC にインストールした後、同じ画面から「<strong>スーツケースファイルを選択してインポート復元</strong>」をクリックし（2段階の安全事前チェックが自動実行されます）、モデルキーを再入力すれば完了です。</p>\n<p>ローカルのキーファイルやゲートウェイのホワイトリストも含めて低レイヤーでディレクトリごとコピー移行したい場合は、次の4ステップで手動移行することもできます：</p>\n<ol>\n<li><strong>旧 PC</strong> で nyairo とゲートウェイプロセスを完全に終了し、上記のコマンドで <code>~/.nyairo</code> を <code>.tar.gz</code> にまとめます。</li>\n<li><strong>新 PC</strong> に同じバージョン（<code>v0.8.0</code>）の nyairo をあらかじめインストールしておき、まだゲートウェイは起動しないでください。</li>\n<li>USB メモリまたはローカルネットワーク経由でバックアップアーカイブを新 PC（WSL 環境）へ転送し、新 PC の <code>~/.nyairo</code> に上書き展開します。</li>\n<li><strong>パスの違いを確認</strong>：旧 PC と新 PC で Linux ユーザー名が異なる場合（例：旧 PC が <code>alice</code>、新 PC が <code>bob</code>）、<code>~/.nyairo/chiyo/config.json</code> と <code>~/.nyairo/config.yaml</code> を開き、旧ユーザー名の絶対パス（<code>/home/alice/...</code> など）が固定記載されている箇所を新しいユーザー名のパスへ書き換えてからプログラムを起動してください（同じ Telegram Token を使って2台の PC を同時にオンラインにすることは絶対に避けてください）。</li>\n</ol>\n\n<h2 id=\"data-uninstall\">クリーンにアンインストールする方法（記憶を残す vs 完全に消去する）</h2>\n<p>利用を終える場合やトラブルシュートのため再インストールしたい場合は、「記憶を残すかどうか」に応じて次の2通りの方法で対処できます：</p>\n<ul>\n<li><strong>ケース 1：クライアントプログラムのみを削除し、すべての記憶とキャラクター設定を残す（再インストール時に推奨）</strong>\n  <ul>\n    <li><strong>Windows デスクトップ版</strong>：Windows の「設定 → アプリ → インストールされているアプリ」から <strong>nyairo</strong> をそのままアンインストールします（または <code>%LOCALAPPDATA%\\Programs\\nyairo\\unins000.exe</code> を実行します）。アンインストーラーは自動起動用のレジストリ項目をクリーンアップし（起動時エラーを防止）、WSL 内のゲートウェイを停止してウィンドウキャッシュを削除しますが、WSL 内の個人データディレクトリには<strong>一切触れません</strong>。後から再インストールすればシームレスに引き継げます。</li>\n    <li><strong>Linux / WSL コマンドライン</strong>：ゲートウェイを停止した後、プログラム本体とコマンドの入り口だけを削除します：<code>rm -rf ~/.local/share/nyairo ~/.local/state/nyairo ~/.local/bin/nyairo</code>。</li>\n  </ul>\n</li>\n<li><strong>ケース 2：プログラム本体とすべてのチャット記憶・設定を完全に削除する（復元不可のため、バックアップ不要であることを事前にご確認ください）</strong>\n  <ol>\n    <li>Windows デスクトップ版を導入している場合は、まず上記の手順でシステムのアプリ一覧からデスクトップクライアントをアンインストールし、続けてローカル設定ディレクトリを削除します（PowerShell で <code>Remove-Item -Recurse -Force \"$env:LOCALAPPDATA\\nyairo\"</code> を実行）。</li>\n    <li>Linux の <code>systemd</code> 常駐サービスを設定していた場合は、先に <code>sudo systemctl disable --now nyairo-gateway.service</code> を実行し、対応する unit ファイルを削除します。</li>\n    <li>Linux / WSL ターミナル上でプログラム本体と個人データディレクトリを削除します：</li>\n  </ol>\n</li>\n</ul>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>rm -rf \"$HOME/.nyairo\" \"$HOME/.local/share/nyairo\" \"$HOME/.local/state/nyairo\" \"$HOME/.local/bin/nyairo\"</code></pre></div>"
  },
  "troubleshooting": {
    "title": "よくあるトラブルシューティング",
    "summary": "環境構築時や日常の対話でよく遭遇しやすいトラブルをまとめました。症状に合わせて該当項目を確認するだけで解決できます。",
    "toc": [
      {
        "id": "tb-smartscreen",
        "text": "インストール時に「Windows によって PC が保護されました」(SmartScreen) が表示される"
      },
      {
        "id": "tb-0",
        "text": "nyairo コマンドが見つからない / WSL が開かない"
      },
      {
        "id": "tb-1",
        "text": "送信時に 401 エラー / 接続タイムアウトで返信が返らない"
      },
      {
        "id": "tb-2",
        "text": "前に話した内容を覚えていないように見える"
      },
      {
        "id": "tb-3",
        "text": "Telegram の 409 エラー / QQ が返信しない / バックグラウンド常駐の方法"
      }
    ],
    "content": "<h2 id=\"tb-smartscreen\">インストール時に「Windows によって PC が保護されました」(SmartScreen) が表示される</h2>\n<p>有償の商用 EV コード署名証明書を持たない独立系オープンソースビルドのため、<code>nyairo-0.8.0-setup.exe</code> を初めて実行すると Windows SmartScreen の標準的な未署名警告が表示されます。まずダイアログ内の「<strong>詳細情報</strong>」をクリックし、アプリ名と公式サイトで公開されている <code>SHA256</code> ハッシュが一致することを確認したうえで、右下に現れる「<strong>実行</strong>」をクリックしてインストールを進めてください（2パネルのベクター図解は <a href=\"#windows-smartscreen\">Windows へのインストール · SmartScreen 通過の2ステップ図解</a> をご覧ください）。</p>\n\n<h2 id=\"tb-0\">nyairo コマンドが見つからない / WSL が開かない</h2>\n<ul>\n<li><strong>インストール直後に <code>nyairo</code> を実行すると <code>command not found</code> と表示される</strong>：環境変数を書き込んだ直後は、現在開いているターミナルウィンドウにまだ反映されていません。一度ターミナルを閉じて開き直すか、<code>export PATH=\"$HOME/.local/bin:$PATH\"</code> を実行すると使えるようになります。</li>\n<li><strong>Windows デスクトップ版で WSL 2 が検出されないと表示される</strong>：管理者として PowerShell を開き、<code>wsl --install -d Ubuntu</code> を実行してください。PC を再起動した後、スタートメニューから一度 <strong>Ubuntu</strong> を起動してアカウントとパスワードを設定し、再度デスクトップ版を開き直せば解消します。</li>\n</ul>\n\n<h2 id=\"tb-1\">送信時に 401 エラー / 接続タイムアウトで返信が返らない</h2>\n<ul>\n<li><strong>401 Unauthorized エラーが出る</strong>：API キーの入力誤り、または期限切れ・失効が原因です。コピー時に前後の空白が入っていないか、プロバイダーの選択を間違えていないか（たとえば DeepSeek のキーを OpenAI 公式エンドポイントに入力していないか）、およびアカウントに残高があるかを確認してください。デスクトップ版の「設定」画面、またはターミナルで <code>nyairo setup model</code> を実行して設定し直せます。</li>\n<li><strong>ずっと読み込み中のままタイムアウトする</strong>：海外のモデル API（OpenAI / Claude など）を選択している場合は、ご利用のネットワーク環境（WSL 内部を含む）から該当 API エンドポイントへ正常にアクセスできるかご確認ください。</li>\n</ul>\n\n<h2 id=\"tb-2\">前に話した内容を覚えていないように見える</h2>\n<ol>\n<li>まず会話欄で <code>/chiyo_status</code> を送信し、<strong>Memory</strong> の項目が <code>READY</code> になっているか確認してください。</li>\n<li>次に <code>/chiyo_memory list</code> を送信し、先ほど話した事実が一覧に定着しているか確認してください（何気ない雑談は一言ごとに保存されるわけではなく、明確な個人の好み・出来事・約束などが記憶帳簿へ登録されます）。</li>\n<li>デスクトップ版を使用していて直前に「新しい会話」をクリックした場合：前回の発話から 15 分以上経過した状態で新しいセッションを開始すると、未アーカイブの古いやり取りは既定で新しいセッションへ自動注入されません（詳細は <a href=\"#dg-memory-boundary\">デスクトップ版の記憶呼び出しウィンドウについて</a> をご覧ください）。普段は同じスレッドをそのまま続けて会話することをおすすめします。</li>\n</ol>\n\n<h2 id=\"tb-3\">Telegram の 409 エラー / QQ が返信しない / バックグラウンド常駐の方法</h2>\n<ul>\n<li><strong>Telegram で <code>409 Conflict: terminated by other getUpdates request</code> が発生する</strong>：同じ Bot Token に対して2つのゲートウェイプロセスが同時に接続している状態です（たとえばデスクトップ版のバックグラウンドで1つ動いているのに、さらにターミナルで <code>nyairo gateway run</code> を実行したなど）。重複している側のウィンドウを停止すればすぐ正常に戻ります。</li>\n<li><strong>QQ Bot は接続済みと表示されるのに、DM（個人チャット）に反応しない</strong>：2点確認してください。1つ目は設定ファイルのキー名が必ず <code>qqbot</code> であることです（<code>qq</code> と書くと無視されます）。2つ目はデスクトップ版の「誰と会話できるか」パネル、または <code>chiyo/config.json</code> の <code>gateway_bindings.qqbot</code> でご自身の DM セッションを許可リストに追加することです（詳細は <a href=\"#qqbot-setup\">QQ 連携の落とし穴と注意点</a> をご覧ください）。それでも原因が特定できない場合は、デスクトップ版の「設定 → 環境と診断」から匿名化済み診断パックをワンクリックで書き出して原因を切り分けられます。</li>\n<li><strong>Linux サーバー上で OS 起動時に自動起動・常駐させる</strong>：常時稼働する Linux ホストに nyairo をデプロイしている場合は、<code>systemd</code> サービスファイル <code>/etc/systemd/system/nyairo-gateway.service</code> を作成できます（中の <code>ユーザー名</code> は実際の Linux アカウント名に置き換えてください）：</li>\n</ul>\n<div class=\"code-block\"><div class=\"code-header\"><span>ini</span><button class=\"copy-btn\" type=\"button\">コピー</button></div><pre><code>[Unit]\nDescription=nyairo messaging gateway\nAfter=network-online.target\n\n[Service]\nType=simple\nUser=ユーザー名\nWorkingDirectory=/home/ユーザー名\nEnvironment=HERMES_HOME=/home/ユーザー名/.nyairo\nExecStart=/home/ユーザー名/.local/bin/nyairo gateway run\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target</code></pre></div>\n<p>保存後、<code>sudo systemctl daemon-reload &amp;&amp; sudo systemctl enable --now nyairo-gateway.service</code> を実行すれば、バックグラウンドで常駐稼働するようになります。</p>"
  },
  "status-matrix": {
    "title": "実装済み・未実装ステータス一覧",
    "summary": "誇張や水増しは一切なし。現在の v0.8.0 パブリックベータ版で何がそのまま使えるか、手動設定が必要な機能は何か、まだ計画段階の要素は何かを1ページで明確に確認できます。",
    "toc": [
      {
        "id": "status-matrix-section-0",
        "text": "すでに実装・検証済みでそのまま使える機能"
      },
      {
        "id": "status-matrix-section-1",
        "text": "手動での有効化や設定が必要な機能"
      },
      {
        "id": "status-matrix-section-2",
        "text": "意図的に守っている境界線と今後の計画"
      },
      {
        "id": "status-matrix-section-3",
        "text": "配布パッケージの入手元と検証について"
      }
    ],
    "content": "<h2 id=\"status-matrix-section-0\">すでに実装・検証済みでそのまま使える機能（v0.8.0）</h2>\n<ul>\n<li><strong>二層メモリーランタンとローカル長期記憶の完結サイクル</strong>：セッションをまたいで重要な事実をローカル SQLite へ自動定着させ、二層メモリーランタンが原文記録を一切改ざんせず厳格に保護します。<code>/chiyo_memory list</code> での確認、<code>correct</code> による訂正、<code>delete</code> による呼び出し停止に対応しています。</li>\n<li><strong>連続した外出移動・12業種の店舗・バッグ収納と物性</strong>：複数区間の道路グラフ経路探索、実時間での徒歩タイマー、中断箇所からの徒歩再開に対応しています。12業種の店舗とオープンカウンターでの自由注文（コンビニで温かいおでんを買うなど）を網羅し、古書店や純喫茶の店主はそれぞれ異なる時間帯で時差営業し、カウンターのコースターにメモを残します。携帯バッグは最大3階層のネスト収納に対応し、温かいお茶が冷める、窓辺の紙メモが雨の湿気でわずかににじむといった温度・湿度の物性を備えています。</li>\n<li><strong>テンプレート不要の完全ランダム専用ワールド・7種の内蔵ワールド・ダーク／ライト両対応オフラインエディタ</strong>：海辺の「凪浦」、温泉の「湯川」、田園の「穂村」、街角の「栞沢」など7つの内蔵ワールドと4種のテーマスタイルに加え、<strong>テンプレート不要の完全ランダム専用ワールドをワンクリックで生成</strong>でき（6種の地形ミックス、4種の異なる道路網構造、8業種の商業店舗、複数部屋の住宅）、ダーク／ライト両対応のオフライン視覚マップエディタも同梱しています。</li>\n<li><strong>ツール扱いを排した対等な拒否の境界線・非同期返信・ロスレス引越しスーツケース</strong>：気が進まない頼みは定型謝罪テンプレートを並べず率直に断り、根にも持ちません。実際のスケジュールに沿って数時間おきに非同期で返信し、睡眠中・外出中・集中作業中は急がない用件をスマホの返信トレイへ保留します（本当に急ぎのときは扉を叩いて呼び出せます）。さらにワールドスライスや移行ロス一覧ごと一式持ち運べる <code>.nyairo-suitcase.zip</code> ロスレス引越しスーツケースのエクスポートにも対応しています。</li>\n<li><strong>アルバム・共読しおり・原稿のバージョン比較</strong>：旅や日常のアルバム閲覧、手持ちの書籍やサンプル書籍を取り込んでページ番号としおりメモを同期記録する共読機能、および原稿の複数世代履歴スナップショットと行単位の差分比較に対応しています。</li>\n<li><strong>マルチプラットフォーム窓口</strong>：管理者権限不要の Windows ネイティブデスクトップ版（<code>nyairo-0.8.0-setup.exe</code>）および Linux / WSL コマンドラインを提供し、スマホの WeChat QR スキャン直結と Telegram Bot 連携に対応しています。</li>\n<li><strong>スムーズなアップデートとロールバック</strong>：<code>nyairo update</code> によるパッケージ全体の更新と、<code>--rollback</code> による新しいチャット履歴を保ったままの旧版復帰に対応しています。</li>\n</ul>\n\n<h2 id=\"status-matrix-section-1\">手動での有効化や設定が必要な機能</h2>\n<ul>\n<li><strong>LLM API 設定</strong>：フレームワーク自体が計算リソースや API を抱き合わせ販売することはありません。5〜10元（数百円程度）で DeepSeek API キーを用意するか、無料のローカル Ollama（<code>http://127.0.0.1:11434</code>、詳細は <a href=\"#configuration-beginner-model\">初心者向けゼロハードルモデル導入ガイド</a> を参照）に直接接続できます。</li>\n<li><strong>バックグラウンドでの無人自律活動</strong>：初期状態ではオフになっています（裏で気づかないうちに通信・API 料金が発生するのを防ぐため）。「話しかけていない時間にもパートナーが自分で散歩したりエッセイを書いたりする様子」を体験したい場合は、設定画面またはコマンドラインで明示的に有効化し、日次予算の上限を設定してください（詳細は <a href=\"#daily-companions\">共読・原稿・アルバムと生活リズム</a> を参照）。</li>\n<li><strong>ローカルカメラ撮影による画像生成</strong>：ユーザー側でローカル環境に Stable Diffusion WebUI（<code>127.0.0.1:7860</code>）とローカルプロンプト変換サービス（<code>127.0.0.1:18190</code>）を起動しておく必要があります。未起動時は利用できない旨を率直にお伝えします。</li>\n<li><strong><code>/chiyo_consider</code> 独立推論</strong>：初期状態ではオフになっています。<a href=\"#modules\">独立推論と基盤サービス</a> に沿って設定を2行変更するだけで有効化できます。</li>\n<li><strong>QQ 公式 Bot での DM 会話</strong>：AppID / AppSecret の入力に加え、セッションホワイトリストで該当の <code>qqbot</code> DM 識別子を許可する必要があります（詳細は <a href=\"#qqbot-setup\">QQ 連携ガイド</a> を参照）。</li>\n</ul>\n\n<h2 id=\"status-matrix-section-2\">意図的に守っている境界線と今後の計画</h2>\n<ul>\n<li><strong>やり取りがないときの WeChat / Telegram への勝手なプッシュ通知は行わない（意図的に守っている邪魔をしない境界線）</strong>：スマホ返信トレイによる非同期返信やバックグラウンドの自律的な暮らしには対応していますが、ユーザーからの発話や事前の約束がないまま、WeChat・Telegram・QQ へ勝手に通知メッセージを送って作業を邪魔することはありません。</li>\n<li><strong>バックグラウンドの自律生活で記録された出来事本文の直接書き換えは非対応（真実性の境界線）</strong>：対話中の記憶は <code>correct</code> で訂正できますが、バックグラウンドの自律生活で定着した出来事については <code>delete</code> で以降の呼び出しを停止することのみ可能で、本文の直接改ざんはサポートしていません。</li>\n<li><strong>初回起動時のゼロ設定オンボーディング・四季の移ろい・ワールド工房（計画中）</strong>：完全ランダム専用ワールドのワンクリック生成とロスレス引越しスーツケースはすでに実装済みです。初回起動時に迷わず始められる没入型の出会いガイド、より深い四季折々の風物や季節リズム、およびコミュニティでのワールドパック共有機能は今後のロードマップに含まれています（詳細は <a href=\"#roadmap\">今後のロードマップ</a> を参照）。</li>\n<li><strong>サバイバル数値ゲーム化や物理ディスク消去は行わない</strong>：現在の休息と補給は4段階の軽やかな日常リズムに留めており、アプリを閉じた間にペナルティが溜まることはありません。また <code>/chiyo_memory delete</code> の役割は以降の検索・呼び出しを停止することであり、元の会話ログや過去のバックアップ自体はローカルディスク上に残ります。</li>\n<li><strong>macOS および Docker オールインワン構成</strong>：独立したパッケージ化と全工程の検証はまだ完了していません。</li>\n</ul>\n\n<h2 id=\"status-matrix-section-3\">配布パッケージの入手元と検証について</h2>\n<p>現在のパブリックベータ版ソースコードおよび検証用アーカイブは <a href=\"https://github.com/L1AN929/nyairo/releases/tag/v0.8.0\" target=\"_blank\" rel=\"noopener noreferrer\">GitHub Releases v0.8.0</a> で公開しており、Windows デスクトップインストーラーも公式サイトから同一ソースで配布し SHA256 チェックサムファイルを添付しています。過去のバージョン（<code>v0.6.0</code>、<code>v0.5.1</code>、<code>v0.4.0</code>、<code>v0.3.0</code>、<code>v0.2.0-rc3</code> など）も比較やロールバック用にそのまま残してあります。各バージョンで実施したテスト内容と未実施範囲の詳細は <a href=\"#testing\">テスト範囲とリリースノート</a> をご覧ください。</p>"
  },
  "roadmap": {
    "title": "今後のロードマップ：いま作っているものと、これから作るもの",
    "summary": "現在のパブリックベータ版 v0.8.0 では、テンプレート不要の完全ランダム専用ワールドのワンクリック生成、12業種の店舗での自由注文、ロスレス引越しスーツケースをすでに実装しています。このページでは私たちが守り続けている「邪魔をしない境界線」と、今後のゼロ設定オンボーディング、四季のリズム、ワールド共有の計画を率直にお伝えします。",
    "toc": [
      {
        "id": "rm-intro",
        "text": "このプロジェクトを始めた原点"
      },
      {
        "id": "rm-random-world",
        "text": "達成済みマイルストーン · 完全ランダム専用ワールドとロスレス引越しスーツケース (v0.8.0)"
      },
      {
        "id": "rm-boundaries",
        "text": "意図的に守り続ける境界線：勝手な通知連発なし・原文改ざんゼロ"
      },
      {
        "id": "rm-p4",
        "text": "次のステップ · ゼロ設定の出会いガイド、四季のリズムとワールド共有"
      },
      {
        "id": "rm-p1",
        "text": "これまでの歩み · ローカル記憶基盤 (v0.1)"
      },
      {
        "id": "rm-p2",
        "text": "これまでの歩み · 空間の原型と実時間徒歩 (v0.2)"
      },
      {
        "id": "rm-p3",
        "text": "これまでの歩み · 連続する日常とデスクトップ版 (v0.3 – v0.8.0)"
      }
    ],
    "content": "<h2 id=\"rm-intro\">このプロジェクトを始めた原点</h2>\n<p>世の中には賢い質問応答チャット欄がすでに十分あります。足りないのは、ウィンドウを閉じたあとも向こう側の時間が流れ続け、長く腰を落ち着けて暮らせる「場所」でした。私たちが nyairo を作るうえで、ずっと大切にしている素朴な原則がいくつかあります：</p>\n<ul>\n<li><strong>時間は本当に流れている</strong>：あなたが仕事で半日話しかけられなくても、向こう側の昼夜は普通に巡り、向こうなりのささやかな日常が進んでいます。</li>\n<li><strong>空間には本当の距離がある</strong>：街の書店へ出かけるなら道中を数分かけて歩きます。途中で「いまどこ」と尋ねられれば、1秒で瞬間移動したりせず「まだ歩いている途中」と素直に答えます。</li>\n<li><strong>世界は画一的であるべきではない</strong>：一人ひとり性格や好みが違うように、AI 個体が暮らす舞台もいつまでも同じ初期マップ1枚に固定されるべきではありません。</li>\n<li><strong>記憶は自分の手元に置く</strong>：すべての会話と長期記憶はあなた自身の PC 内のローカルデータベースに保存され、特定の商用クラウドプラットフォームに縛られません。</li>\n</ul>\n\n<h2 id=\"rm-random-world\">達成済みマイルストーン · 完全ランダム専用ワールドとロスレス引越しスーツケース (v0.8.0)</h2>\n<div class=\"callout callout-info\"><div class=\"callout-title\">現在の状況：テンプレート不要の完全ランダム専用ワールドとロスレススーツケースは v0.8.0 で実装済みです</div><p>パブリックベータ版 <code>v0.8.0</code> では、海辺の「凪浦」、温泉の「湯川」、田園の「穂村」、街角の「栞沢」など7種の内蔵ワールドと4種のテーマスタイルを残したうえで、新たに<strong>テンプレート不要の完全ランダム専用ワールドのワンクリック生成</strong>（6種の地形ミックス、4種の異なる道路網構造、8業種の商業店舗、複数部屋の住宅）に対応しました。さらにダーク／ライト両対応へ刷新されたオフライン視覚マップエディタと、ワールドスライスや移行ロス一覧ごとまとめて持ち運べる <code>.nyairo-suitcase.zip</code> ロスレス引越しスーツケースも備えています。</p></div>\n<p>「世界にひとつだけの日常の街に暮らす」ことを軸に、<code>v0.8.0</code> では以下の機能がすでに動作しています：</p>\n<ul>\n<li><strong>テンプレート不要のランダム創世とエディタでの微調整</strong>：ランダムシードだけで多彩な地形の微気候、つながりのある道路トポロジー、オープンカウンター店舗、複数部屋の住まいを組み合わせられます。暮らし始めたあとでも、オフラインエディタで壁や扉を描き足したり、家具を増やしたり、小店のメニューを調整したりできます。</li>\n<li><strong>12業種の街の店舗、バッグのネスト収納、温度・湿度の物性</strong>：コンビニの温かいおでんなどのオープンカウンター自由注文、古書店や純喫茶の店主による時差営業とカウンターのコースターメモを実装。携帯バッグは最大3階層のネスト収納に対応し、机の温かいお茶は自然に冷め、窓辺の紙メモは雨の湿気でわずかににじみます。</li>\n<li><strong>ロスレス引越しと基盤 LLM の自由な載せ替え</strong>：二層メモリーランタンが原文記録を改ざんゼロで保護します。<code>.nyairo-suitcase.zip</code> は API キーを自動除外しながら記憶帳簿・エッセイ作品・カスタムワールドスライス・透明性の高い移行ロス一覧をまるごと持ち運べるため、PC を買い替えても次世代の LLM へ切り替えてもこれまでの日々が失われません。</li>\n</ul>\n\n<h2 id=\"rm-boundaries\">意図的に守り続ける境界線：勝手な通知連発なし・原文改ざんゼロ</h2>\n<p>実装漏れではなく、毎日のそばに居心地よく長くいられるようにあえて作らないと決めていることがあります：</p>\n<ul>\n<li><strong>やり取りがないときの勝手なポップアップ通知はしない</strong>：ツール扱いを排した日常の自律的な拒否境界を備え、睡眠中・外出中・集中作業中は急がない用件をスマホ返信トレイに入れて実際のスケジュール通りに数時間おきに非同期返信します（本当に急ぎのときは扉を叩いて呼び出せます）。ただし、あなたから話しかけておらず事前の約束もない状態で、バックグラウンドから勝手に WeChat や Telegram へポップアップ通知を送り続けて存在感をアピールするようなことは一切しません。</li>\n<li><strong>自律生活で記録された出来事本文の直接改ざんは許さない</strong>：会話の中であなたが言い間違えた事実は <code>/chiyo_memory correct</code> で訂正できます（二層メモリーランタンは下層の原文をそのまま保持します）。一方で、向こうがバックグラウンドで散歩したり読書したりして積み上げた出来事の本文を直接書き換えることはできず、今後思い出してほしくない場合は <code>/chiyo_memory delete</code> で呼び出しを停止できます。</li>\n</ul>\n\n<h2 id=\"rm-p4\">次のステップ · ゼロ設定の出会いガイド、四季のリズムとワールド共有</h2>\n<p>空間生成とロスレス引越しの基盤が整った今、今後の反復開発ではより滑らかな初対面の体験と長期的な暮らしの息づかいに注力していきます：</p>\n<ul>\n<li><strong>初回起動時のゼロ設定オンボーディングガイド</strong>：クライアントを初めて開いた際の環境セルフチェック、モデル疎通（DeepSeek / ローカル Ollama のワンクリック検証）、そして完全ランダム専用ワールドの扉を開ける瞬間までをひと続きの没入型ガイドとしてまとめ、まったくの初心者でも設定画面を探し回らずに自然と暮らしを始められるようにします。</li>\n<li><strong>より深い季節の移ろいと近隣の生活リズム</strong>：街の光や風物、お店の季節限定メニュー、店主との日常的な雑談が春夏秋冬に合わせて自然に移り変わり、数か月から1年単位の時間の奥行きが積み重なるようにします。</li>\n<li><strong>軽量なコミュニティ向けワールドパックとベース設定の共有</strong>：ワールドパックとニュートラルな設定テンプレートの検証・プレビュー規約を整備し、オフラインマップエディタで作った個性豊かな街や街角の本屋、メニューレシピを手軽に共有してワンクリックで試し住みできるようにします。</li>\n</ul>\n\n<h2 id=\"rm-p1\">これまでの歩み · ローカル記憶基盤 (v0.1)</h2>\n<p>プロジェクト最初のバージョン（<code>v0.1.0-rc4</code>〜<code>rc9</code>）では、まず最も土台となる基盤を固めました：</p>\n<ul>\n<li>長期記憶をローカル SQLite へ保存し、いつでも閲覧・訂正・呼び出し停止ができるようにしました。</li>\n<li>プログラムディレクトリと個人データを分離し、再起動しても途切れず、コマンドアップデートでもデータが消えない仕組みを整えました。</li>\n</ul>\n\n<h2 id=\"rm-p2\">これまでの歩み · 空間の原型と実時間徒歩 (v0.2)</h2>\n<p><code>v0.2</code> の段階では、歩いて回れる部屋と街並みを初めて用意しました：</p>\n<ul>\n<li>オフラインで動作する単体マップエディタと複数のサンプルテンプレートを同梱しました。</li>\n<li>道路網の距離に応じて時間を計測する移動ツールを接続し、スマホの WeChat 個人チャットともつなぎました。</li>\n</ul>\n\n<h2 id=\"rm-p3\">これまでの歩み · 連続する日常とデスクトップ版 (v0.3 – v0.8.0)</h2>\n<p><code>v0.3.0</code> から現在の <code>v0.8.0</code> にかけて、空間での単発アクションを一貫した日々の暮らしへと育ててきました：</p>\n<ul>\n<li>複数区間の連続経路探索、途中一時停止からの歩行再開、複数タスクの一時中断・復帰、日をまたぐ街の見聞を実現しました。</li>\n<li>テンプレート不要の完全ランダム専用ワールド、12業種の店舗での自由注文、バッグのネスト収納と温度・湿度の物性、自律的な拒否境界と非同期返信を加えました。</li>\n<li>管理者権限不要でワンクリック導入できる Windows ネイティブデスクトップクライアントと、ロスレス引越しスーツケース <code>.nyairo-suitcase.zip</code> を実装しました。これからの計画についても、引き続き一つ実装し終えてから誠実に完了マークを付けていきます。</li>\n</ul>"
  },
  "privacy": {
    "title": "プライバシーとデータの境界線",
    "summary": "どのデータがあなたの PC 内に留まり、どの内容が対話時にモデル API へ送信されるのか、そして「記憶の削除」が実際に何を行っているのかを正直に説明します。",
    "toc": [
      {
        "id": "privacy-section-0",
        "text": "あなた自身の PC に留まるデータ"
      },
      {
        "id": "privacy-section-1",
        "text": "ネットワーク経由で外部へ送信される内容"
      },
      {
        "id": "privacy-section-2",
        "text": "「呼び出し停止」と完全消去の違い"
      },
      {
        "id": "privacy-section-3",
        "text": "不具合報告時に個人プライバシーを守る方法"
      }
    ],
    "content": "<h2 id=\"privacy-section-0\">あなた自身の PC に留まるデータ</h2>\n<p>モデルの API キー、WeChat / Telegram の連携認証情報、完全なチャット履歴、SQLite 長期記憶データベース、生活状態帳簿、およびアルバム・原稿は、すべてあなたのローカル PC 内の個人ディレクトリ（初期設定は <code>~/.nyairo</code>）に書き込まれます。<strong>nyairo のいかなるサーバーも経由しません</strong>。nyairo 公式に中央集権型のクラウドバックエンドは存在せず、あなたのプライベートな会話データベースを収集することは一切なく、仕組み上収集もできません。</p>\n<p><strong>ローカル権限に関するご注意</strong>：現在のバージョンではローカルデータベースに対する透過的ディスク暗号化を行っていないため、データの安全性はお使いの PC の OS アカウント権限に依存します。同じ PC の管理者（root）権限を持つ人や、ユーザーディレクトリを読み取れるローカルソフトウェアは理論上これらのファイルを参照できるため、端末とバックアップアーカイブはご自身で大切に管理してください。</p>\n\n<h2 id=\"privacy-section-1\">ネットワーク経由で外部へ送信される内容</h2>\n<p>「ローカルデプロイ」は「完全にネットワーク遮断」と同義ではありません。普段の利用でネットワーク通信が発生するのは、あなたがご自身で設定した次の箇所に限られます：</p>\n<ul>\n<li><strong>あなたが選んだ LLM プロバイダー</strong>：対話のたび（またはバックグラウンド活動や <code>/chiyo_consider</code> 分析の実行時）に、現在の入力・直近の会話コンテキスト・検索された関連記憶の断片が、返信生成のためあなたが入力したモデル API アドレスへ直接送信されます。<strong>nyairo のいかなるサーバーも経由しません</strong>。<strong>Ollama</strong> などのローカルモデルを使えば、データを一切 PC の外へ出さずに完結させることも可能です。</li>\n<li><strong>メッセージングプラットフォーム（任意）</strong>：WeChat や Telegram を連携している場合、やり取りされるメッセージは当然ながら各プラットフォームの通信経路を通過します。</li>\n<li><strong>本ガイドサイト（nyairo.com）</strong>：純粋な静的 Web サイトであり、検索処理もブラウザ内のローカルで動作します。ライト／ダークテーマの設定もブラウザの <code>localStorage</code> にのみ保存され、あなたのローカルインスタンスへ接続することはありません。</li>\n</ul>\n\n<h2 id=\"privacy-section-2\">「呼び出し停止」と完全消去の違い</h2>\n<ul>\n<li><strong><code>/chiyo_memory delete</code> が行うこと</strong>：指定した記憶を無効化マークし、以降の対話コンテキストを組み立てる際に関連する古い断片を除外して、モデルが今後その話題に触れないようにします。あわせて、モデルがコマンドラインを呼び出して過去のログファイルを直接読み返す権限も既定で制限しています。</li>\n<li><strong>行わないこと</strong>：これは<strong>ディスクの完全消去ツール（シュレッダー）ではありません</strong>。障害切り分けや遡及確認のため、元のセッション記録・監査ログ・過去に手動で作成したバックアップアーカイブには当時の文章がそのまま残ります。また、モデルプロバイダー側や WeChat / Telegram 側の履歴メッセージがこのコマンドで遠隔取り消しされるわけでもありません。</li>\n<li><strong>ローカルデータ一式を完全に消去する方法</strong>：実行中のすべての nyairo およびゲートウェイプロセスを停止し、記憶を一切残さなくてよいことを確認したうえで、<code>~/.nyairo</code> ディレクトリおよびご自身で保存していたバックアップアーカイブを手動で削除してください。</li>\n</ul>\n\n<h2 id=\"privacy-section-3\">不具合報告時に個人プライバシーを守る方法</h2>\n<p>GitHub に Issue を投稿したりコミュニティグループへエラー画面を貼ったりする前に、機微情報が含まれていないか一度ご確認ください：</p>\n<ul>\n<li><code>.env</code>、<code>config.yaml</code>、または <code>~/.nyairo</code> フォルダ丸ごとをそのまま貼り付けないでください。</li>\n<li>API キー、Telegram Bot Token、個人の数値 Chat ID、WeChat 連携用 QR コード、実際のシステムユーザー名は切り抜くかマスクしてください。万一公開ページにキーを貼ってしまった場合は、投稿を削除するだけでは不十分ですので、ただちに該当サービスの管理画面から<strong>キーを失効させて再発行</strong>してください。</li>\n</ul>"
  },
  "testing": {
    "title": "テスト範囲とリリースノート",
    "summary": "各公開バージョンのリリース前に実際にどのような検証を行ったか、どのシナリオがまだ未検証かを明確にし、単体テストの件数だけで全シナリオの安定性保証に見せかけることはしません。",
    "toc": [
      {
        "id": "testing-section-0",
        "text": "なぜこのページを独立して設けているのか"
      },
      {
        "id": "testing-section-1",
        "text": "現在カバーされている自動テストと実機確認"
      },
      {
        "id": "testing-section-2",
        "text": "まだカバーされていない検証範囲"
      }
    ],
    "content": "<h2 id=\"testing-section-0\">なぜこのページを独立して設けているのか</h2>\n<p>多くのオープンソースプロジェクトではトップページに立派なテスト合格バッジを掲げ、あらゆる環境や極端なケースまですべて万全であるかのような印象を与えがちです。nyairo は現在 <code>v0.x</code> のパブリックベータ段階にあるため、「何をテストしたか」と「何をテストしていないか」をありのままお示しし、導入をご判断いただく材料にしたいと考えています。</p>\n\n<h2 id=\"testing-section-1\">現在カバーされている自動テストと実機確認</h2>\n<ul>\n<li><strong>中核メカニズムとコンポーネントテスト</strong>：記憶の読み書きと訂正境界、複数区間の道路網経路探索、一時停止・再開時にやり直さない歩行制御、1日あたりのリクエスト予算上限、ワールドの朝夕変化と身体リズム、スーツケース事前チェックとソースコード匿名化といった中核モジュールについて、それぞれ対応するオフライン自動テストを実施しています（例：<code>v0.4</code> ワールドスイート 263 項目、<code>v0.5.1</code>〜<code>v0.8.0</code> の生活リズム・クォータ収束・安全匿名化テストがいずれも合格）。</li>\n<li><strong>配布パッケージの整合性検証</strong>：公開される GitHub Release ZIP には毎回 <code>MANIFEST.json</code> マニフェストと <code>.sha256</code> ハッシュチェックサムを同梱し、展開されるすべてのスクリプトとコンポーネントがパッケージ作成時と1バイト単位で完全一致することを保証しています。</li>\n<li><strong>Windows デスクトップ版および公式サイトの実機確認</strong>：<code>nyairo-0.8.0-setup.exe</code> インストーラー、デスクトップ版の各機能ビュー（概要・ストリーミング対話・マップ・原稿バージョン差分比較）、および公式サイトのデスクトップ／モバイル表示の動作をローカル実機環境で確認済みです。</li>\n</ul>\n\n<h2 id=\"testing-section-2\">まだカバーされていない検証範囲</h2>\n<ul>\n<li><strong><code>v0.8.0</code> での 24 時間以上連続した実 LLM 放置稼働テストは未実施</strong>：自動テストでは主に決定論的なテストダブルと制御クロックを使ってロジックの正しさを検証しており、実際の商用 LLM を数日間連続稼働させたときの自然な振る舞いの長期検証に代わるものではありません。</li>\n<li><strong>クラウド側の GitHub Actions 自動ゲートは未有効化</strong>：リポジトリの <code>ci/templates/</code> には参考用のテストワークフローテンプレートを置いていますが、GitHub クラウド上で毎回のコミットに対して全量テストを自動実行する構成にはしていないため、見せかけの CI Passing バッジは掲げていません。</li>\n<li><strong>クロスプラットフォームの網羅範囲は限定的</strong>：主な検証環境は Ubuntu / Debian Linux および Windows 10/11（WSL 2 Ubuntu）に集中しています。macOS、その他のマイナーな Linux ディストリビューション、Docker コンテナ化デプロイ、ならびに QQ / Feishu（Lark）の実アカウントでの常時接続については、まだ完全な受け入れ検証を完了していません。</li>\n</ul>"
  },
  "release": {
    "title": "オープンソースライセンスとサイト運用",
    "summary": "Apache-2.0 と上流 MIT ライセンスの境界、配布パッケージの作成方針、および本ガイドサイト自体の日常的な更新方法について説明します。",
    "toc": [
      {
        "id": "release-section-0",
        "text": "オープンソースライセンスとサードパーティ表記"
      },
      {
        "id": "release-section-1",
        "text": "公開リポジトリと配布パッケージの方針"
      },
      {
        "id": "release-section-2",
        "text": "ライセンスと著作権表記の詳細"
      },
      {
        "id": "release-section-3",
        "text": "リリース時の確認手順"
      },
      {
        "id": "release-section-4",
        "text": "公式サイトの保守・更新方法"
      }
    ],
    "content": "<h2 id=\"release-section-0\">オープンソースライセンスとサードパーティ表記</h2>\n<p>nyairo の公開リポジトリに新たに追加された独自開発コード（記憶帳簿、仮想ワールドと身体リズム、生活連続性エンジン、Life Supply 原稿サービス、および公式サイトドキュメントを含む）は、すべて <strong>Apache-2.0</strong> オープンソースライセンスで公開されています。</p>\n<p><strong>基盤実行エンジン（オープンソースプロジェクト Hermes、<code>vendor/hermes</code> に配置）</strong>：元の <strong>MIT</strong> ライセンスおよび著作権表示をそのまま保持します。その他のサードパーティ製 Python およびフロントエンド依存ライブラリも、それぞれ元のオープンソースライセンスに従います。</p>\n\n<h2 id=\"release-section-1\">公開リポジトリと配布パッケージの方針</h2>\n<ul>\n<li>公開リポジトリは <a href=\"https://github.com/L1AN929/nyairo\" target=\"_blank\" rel=\"noopener noreferrer\">github.com/L1AN929/nyairo</a> にあり、プログラムのソースコード、ニュートラルなテンプレート、テスト、ドキュメントのみを収録しています。個人のチャットデータベース、秘密鍵、内部サーバーアドレスなどは一切含まれません。</li>\n<li>一般ユーザーのインストールおよびアップデートは、常に固定された <strong>GitHub Release タグ</strong>（現在は <code>v0.8.0</code>）および検証済みアーカイブを基準とし、開発中のブランチを直接取得して未完成版を導入しないようにしています。</li>\n</ul>\n\n<h2 id=\"release-section-2\">ライセンスと著作権表記の詳細</h2>\n<p>リポジトリのルートディレクトリに完全な <code>LICENSE</code> および <code>NOTICE</code> ファイルを用意しています。なお、オープンソースライセンスはあくまでコードおよびドキュメント自体の利用・改変を許諾するものであり、商用 LLM の API クレジット、サードパーティ製メッセージングサービスのアカウント認証情報、開発者個人のキャラクター設定データなどは含まれません。</p>\n\n<h2 id=\"release-section-3\">リリース時の確認手順</h2>\n<ol>\n<li>クリーンなリリース用ディレクトリ上で、対象バージョンの目標テストおよび機微情報スキャンを通過させます（内部テスト用ドメインやローカルユーザー名パスがすべて匿名化されていることを確認します）。</li>\n<li>整合する <code>MANIFEST.json</code> を生成し、リリース用アーカイブと <code>.sha256</code> チェックサムファイルを作成したうえで、隔離されたアカウント環境で展開とセルフチェックコマンドを検証します。</li>\n<li>公式サイトのインストール導線・更新履歴・既知の境界線に関する説明を同期更新し、公開済みの過去バージョンのパッケージは1バイトも変更せずそのまま保持します。</li>\n</ol>\n\n<h2 id=\"release-section-4\">公式サイトの保守・更新方法</h2>\n<p>本公式サイトはフレームワーク非依存の純粋な静的 Web サイトで、ソースコードはリポジトリの <code>website/</code> ディレクトリにあります（主要ファイルは <code>index.html</code>、<code>style.css</code>、<code>app.js</code>、<code>docs-data.js</code> および同期整理された <code>USER_GUIDE.zh-CN.md</code> です）：</p>\n<ol>\n<li>スタイルシート、スクリプト、またはドキュメントデータを変更した際は、CDN や訪問者のブラウザが確実に最新内容を取得できるよう、<code>index.html</code> 内で読み込んでいる <code>style.css?v=...</code>、<code>app.js?v=...</code>、<code>docs-data.js?v=...</code> のバージョンパラメータも忘れずに更新してください。</li>\n<li>静的サイトディレクトリをデプロイする前は、本番環境上の旧ファイルを公開 Web ディレクトリの<strong>外側</strong>にあるプライベートな場所へ丸ごとバックアップしてください。公開ディレクトリ内に <code>.bak</code> などのバックアップファイルを放置してはなりません。</li>\n<li>公開後は実際にデスクトップ幅・モバイル幅のページを開き、ナビゲーション遷移、ダウンロードリンク、検索ボックス（<code>Ctrl + K</code>）、およびコピーボタンがすべて正常に動作することを確認してください。</li>\n</ol>"
  },
  "contributing": {
    "title": "不具合報告とコントリビュート",
    "summary": "バグを見つけたときに再現しやすい報告を行う方法、セキュリティ上の懸念を見つけた際の取り扱い、およびプロジェクトへコードを提出する際の基本ルールをまとめています。",
    "toc": [
      {
        "id": "contributing-section-0",
        "text": "バグ報告を最も効果的に伝える方法"
      },
      {
        "id": "contributing-section-1",
        "text": "セキュリティやプライバシーの脆弱性を見つけた場合"
      },
      {
        "id": "contributing-section-2",
        "text": "コード提出 (PR) の基本ルール"
      },
      {
        "id": "contributing-section-3",
        "text": "回帰テストを書く際のポイント"
      },
      {
        "id": "contributing-section-4",
        "text": "長期的な日常利用フィードバック"
      }
    ],
    "content": "<h2 id=\"contributing-section-0\">バグ報告を最も効果的に伝える方法</h2>\n<p>Issue を作成したりコミュニティで質問したりする際は、以下の情報を添えていただくと最も早く原因を特定できます：</p>\n<ul>\n<li><strong>環境とバージョン</strong>：Windows デスクトップ版か Linux ターミナルか、OS バージョン（Ubuntu 24.04 / WSL2 など）、<code>~/.local/state/nyairo/install.json</code> に記載されているバージョン番号。</li>\n<li><strong>再現手順</strong>：どのコマンドを実行したか、またはどのボタンをクリックしたか、本来期待していた動作と、実際に表示されたエラー内容。</li>\n<li><strong>匿名化済みのエラーログ抜粋</strong>：ターミナルに出力された重要なエラー行を貼り付けてください。事前にご自身の API キー、Token、プライベートな会話内容は必ず削除してください。</li>\n</ul>\n\n<h2 id=\"contributing-section-1\">セキュリティやプライバシーの脆弱性を見つけた場合</h2>\n<p>API キーの漏えい、アカウントをまたいだ他人のプライベート記憶の閲覧、あるいは権限境界のバイパスにつながる問題を発見した場合は、<strong>まず公開 Issue に詳細な再現・悪用手順を投稿しないでください</strong>。リポジトリまたは公式の連絡窓口を通じてメンテナーへ「非公開で相談したいセキュリティ上の問題がある」旨をお知らせください。もしご自身の手元のキーがすでに漏えいしている恐れがある場合は、ただちにプロバイダーの管理画面から失効・再発行を行ってください。</p>\n\n<h2 id=\"contributing-section-2\">コード提出 (PR) の基本ルール</h2>\n<ul>\n<li><strong>変更点を絞る</strong>：1つの PR では1つの明確な課題または小さな機能のみを扱い、該当ディレクトリ内の説明ドキュメントにも目を通し、関係のないフォーマット変更を混ぜないでください。</li>\n<li><strong>テストの分離</strong>：新しく追加するテストでは必ず一時ディレクトリ（<code>tmp_path</code>）と架空のテスト ID を使用し、開発者個人の実環境 <code>~/.nyairo</code> を読み書きしたり、個人のキャラクター設定を共通コードへハードコードしたりしないでください。</li>\n<li><strong>検証範囲を正直に明記する</strong>：PR の説明欄にどの OS 環境でどのテストを実行したかを明記してください。未検証のプラットフォームがある場合はその旨を正直に記載して問題ありません。</li>\n</ul>\n\n<h2 id=\"contributing-section-3\">回帰テストを書く際のポイント</h2>\n<p>チャットコマンドやメッセージルーティングに関するバグ（たとえば特定のスラッシュコマンドがターミナルでは動くのに WeChat や Telegram では反応しないなど）を修正する場合は、テストを書く際にできるだけ外側のメッセージ入り口から最終的な処理関数まで一気通貫でテストし、さらにそのテストが修正前の古いコードでは確実に失敗することを確認しておくと、将来のリファクタリング時にも再発を確実に防げます。</p>\n\n<h2 id=\"contributing-section-4\">長期的な日常利用フィードバック</h2>\n<p>明確なエラーだけでなく、数日間使い続けてみた率直な使用感の共有も大歓迎です。たとえば「この記憶の呼び出し方が少し不自然」「街を歩くテンポに少し違和感がある」「デスクトップ版のこのボタンが押しにくい」といった日々の細かな気づきこそが、nyairo を少しずつ使いやすく育てていく大切な手がかりになります。</p>"
  },
  "acknowledgments": {
    "title": "謝辞",
    "summary": "これまで共に世界を紡ぎ支えてくれた仲間たちと、長くそばで見守り続けてくれたあなたへ。",
    "toc": [],
    "content": "<div class=\"callout callout-info\"><p style=\"margin: 0; font-size: 15px; line-height: 1.8;\">幾多の深夜、画面の両側でともに世界を形づくり支えてくれたすべての仲間たち、そしていつでもそばで見守り続けてくれたあなたに心より感謝いたします。</p></div>"
  }
};
