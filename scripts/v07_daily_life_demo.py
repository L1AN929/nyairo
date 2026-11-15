#!/usr/bin/env python3
"""0.7 第一条：让她在同一个实例里从家里做事、出门、读完一本书，再到店里买东西。

这是一个**实际驱动**脚本，不是打印样例：

* 新隔离根里的默认新世界（``coastal_town`` 生成包，含本轮新增的家中家具小事与
  书店/咖啡馆/海岸散步道），真实 Supply owner、真实 ``WorldBodyService`` +
  ``gateway.sock``、真实 Body/World 状态、真实 Activity/LR-2/LR-4/LR-5/AR-0；
* 唯一的“模型”是本文件的脚本替身：它只读取 AG-1 当轮真正提供的候选和事实，
  在合法集内选择，不联网、不发任何产品模型请求；
* 时间用注入的业务时钟机械推进（走完一段按世界包声明的秒数、停留按声明时长、
  阅读 PAUSE 后快进 31 逻辑分钟），不是真实等待；
* 阅读推进走正式阅读端口（每页 acquisition + bookmark + 可选的私人批注写入同一个
  owner 版本），PAUSE 由本单新增的“阅读专用默认短间隔条件”绑定，LR-4 到期扫出
  OPEN 资格，再由一次新鲜 Decision RESUME 同一 Activity，从原 checkpoint 继续到
  最后一页，LR-5 的页码完成条件把它正常 COMPLETE；
* 读完后再走到营业的 `home_goods_store`，店里由既有 `world_property_economy` 读出真正
  在卖的商品；Decision 选中 `buy:<catalog_item_id>` 后，经济 owner 用一次原子
  DEBIT+OWNED 记账，LR-5 只按这笔交易完成 Activity；同一 operation key 再执行一次只
  返回 `already_applied`，不二次扣款。

边界：全部决定是脚本替身，不代表真实模型会这样选；这是短程连续演示，不是一整天、
不是 G1、不是 24h 证据；购买用 owner 自己的即时到家模式，不跑两小时交付时序；根目录保留不删。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import action_intent  # noqa: E402
import autonomous_life_cycle as alc  # noqa: E402
import life_practice_context as lpc  # noqa: E402
import life_project_options as lpo  # noqa: E402
import one_day_life as odl  # noqa: E402
import world_move_opportunity_source as wmos  # noqa: E402
from model_continuation_writer import ModelContinuationWriter  # noqa: E402
from world_body_client import WorldBodyClient  # noqa: E402

import test_life_v1_integration as donor  # noqa: E402

JST = timezone(timedelta(hours=9))
HOME_SMALL_THINGS = ("stay:home:DESK_PAUSE", "stay:home:WINDOW_PAUSE",
                     "stay:home:TEA_PAUSE")
#: 0.7 第三阶段：白天读书时她真的写下一个「没弄懂」的地方。这条疑问由正式阅读响应
#: 端口（``unclear``）写进私人 items owner，之后再由 ``life_project_options`` 作为
#: ``QUESTION_FROM_MATERIAL`` 提案的唯一来源。文字只在这一页的上下文里有意义。
DAY_QUESTION = "窗外那条街，为什么比屋里先知道一天的开始？"
READING_NOTE = {"kind": "unclear", "note": DAY_QUESTION}
#: 0.7 第五阶段：三句**显式**分享文本（都是脚本替身写的短句，不是小册子正文，也不打印）。
#: 它们只用来证明 DRAFT / DEFERRED / LOCAL_DELIVERED / DROPPED / 换版拒绝这条生命周期。
SHARE_TEXT = "今晚这一页先留在这里，明天再接着看。"
DROP_SHARE_TEXT = "这句话想想还是不拿出去了。"
CHANGE_SHARE_TEXT = "等这一页真的写完，再把这段拿出去。"


def say(tag: str, text: str) -> None:
    print("[%s] %s" % (tag, text), flush=True)


def dump(tag: str, payload) -> None:
    print("[%s] %s" % (tag, json.dumps(payload, ensure_ascii=False, default=str)),
          flush=True)


def check(condition: bool, message: str, detail=None) -> None:
    if condition:
        return
    print("DEMO_FAIL: %s" % message)
    if detail is not None:
        print(json.dumps(detail, ensure_ascii=False, default=str, indent=1)[:6000])
    raise SystemExit(1)


class Clock:
    """一个显式业务时钟：只由本脚本在声明的位置推进。"""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def iso(self) -> str:
        return self.now.astimezone(timezone.utc).isoformat()

    def advance(self, seconds: int, *, because: str) -> str:
        self.now = self.now + timedelta(seconds=int(seconds))
        say("clock", "+%s 秒（%s）→ %s" % (seconds, because, self.now.astimezone(JST)
                                          .strftime("%Y-%m-%d %H:%M:%S %Z")))
        return self.iso()


def _refs(candidate) -> list[str]:
    return [str(ref) for ref in (candidate.get("target_refs") or [])]


def _legal(candidate) -> list[str]:
    return [str(item) for item in (candidate.get("legal_decisions") or [])]


def _bare(value) -> str:
    text = str(value or "")
    while text.startswith("artifact:"):
        text = text[len("artifact:"):]
    return text


class ScriptedDailyCognition:
    """只在本轮合法候选里按声明的意图选择；不读私有正文，不联网。"""

    model_id = "stub:v07-daily-life"
    model_version = "v07-daily-life-v1"

    def __init__(self, *, reading_artifact_id: str) -> None:
        self.reading_bare = _bare(reading_artifact_id)
        self.intent = ""
        self.place_wish = ""
        self.resume_activity = ""
        #: 脚本替身的一次具名购买意愿：在店里如果它真的在卖，就选它；否则选第一个
        #: owner 真正给出的可买商品。它不代表模型偏好。
        self.buy_wish = "buy:bedside_lamp"
        #: 脚本替身的一次具名摆放意愿：把家具挪到"她现在站着的那个已声明区域"，这样紧接着
        #: 就能在正确区域开灯。它只在这个区域确实是 owner 给出的合法候选时才生效。
        self.arrange_area_wish = ""
        #: 多世界链：只针对某一件具体家具的 ARRANGE / TOGGLE 意愿（按实例 id 认 owner 给出的
        #: 真实候选，不按顺序猜）。空串表示沿用默认行为。
        self.arrange_object_wish = ""
        self.toggle_object_wish = ""
        #: 多世界链：把手里的小物件放到哪件家具的实例台面（按桌面 owner 实例 id 认候选）。
        self.place_surface_object = ""
        #: 0.7 第三阶段：刚由 owner 创建的私人小册子的裸 id。项目创建后由脚本替身记住，
        #: 之后只按它选择同一个 artifact 的 CONTINUE/ABANDON，不按标题或顺序猜。
        self.project_bare = ""
        #: 0.7 第十阶段：``start_offered_work`` 不在本轮 offered 里硬认某一件，
        #: 而是从真实给出的、START 合法的可续写作品里挑第一件；这里列出本轮**不选**的
        #: 已知作品（例如主小册子要留给归档负控作“未被偷换”的对照）。它是脚本的声明，
        #: 不是降低候选上限，也不是替换候选来源。
        self.work_exclude: set = set()
        #: 装配后的 cycle，用来读取本轮**已经生成**的提案 kind（脚本替身只读，不造候选）。
        self.cycle = None
        #: 只在 ``propose_future`` 轮使用的 later_item 提案（脚本替身，不是产品模型）。
        self.later_item: dict | None = None
        #: 0.7 第八阶段：这一轮想在正式 Decision 顶层提交的 ``plan_today``（把哪一件既有
        #: fitem 排进今天）。它只是 Decision 的一个可选字段，写计划仍由既有 day-plan 域在
        #: Decision 提交后完成，脚本替身不碰文件。
        self.plan_today: dict | None = None
        #: 0.7 第八阶段：``consume`` 意图要吃的那个真实 object 实例 id（脚本替身只在本轮真正
        #: offered 的 ``object:<instance>`` 候选里认它）。
        self.consume_object = ""
        #: 0.7 第四阶段：脚本替身在本轮提交的一条有范围做法草案（原样进正式 Decision 的
        #: ``method_proposal`` 字段；校验、保存与状态都由既有 AG-1/owner 完成）。
        self.method_proposal: dict | None = None
        #: 脚本替身在本轮提交的一次显式评价（``method_ref`` 由本轮真正 offered 的句柄补上）。
        self.method_trial: dict | None = None
        #: 0.7 第十阶段：脚本替身故意点名一个**本轮没有 offered** 的旧做法句柄。它只用于
        #: 证明 AG-1 自己的 ``INVENTED_METHOD_REF`` 拒绝对正式 Decision 生效；正常轮次为空。
        self.method_ref_override: str = ""
        #: 0.7 第五阶段：脚本替身这一轮想做的分享动作（不含任何受众/渠道/正文以外的字段）。
        #: ``op=prepare`` 用 source_bare 认本轮真正 offered 的来源；其它动作按 share_id 认
        #: 本轮真正 offered 的草稿。句柄在 invoke() 里从 request 的真实 sharing 块取，不硬编码。
        self.sharing_request: dict | None = None
        #: 本轮真正从 request 的 sharing 块里回显了哪些 owner 句柄（供演示只读断言）。
        self.sharing_choice: dict | None = None
        #: 一次 typed 共处邀请的裸引用；脚本替身只选本轮真正 offered 的 ``co_presence:`` 候选。
        self.invitation_ref = ""
        self.calls = 0
        self.offered: list[dict] = []
        self.reads: list[dict] = []
        #: 第九阶段：记录**正式 Decision 调用实际收到**的 S03 claims 块（adapter 已做有界
        #: 投影），以及跨调用累计的来源/状态类别。演示只读这些字段，不另读 service 冒充。
        self.last_claims_block: dict | None = None
        #: 第十阶段：正式 Decision 调用实际收到的 practice 切片（含 withheld_methods 的具名
        #: 理由）。与 claims 一样只读 adapter 已做有界投影的那一块，不另行查 service。
        self.last_practice_slice: dict | None = None
        self.seen_claim_source_kinds: set = set()
        self.seen_claim_statuses: set = set()
        self.seen_claims_at: list[dict] = []

    def _is_reading(self, candidate) -> bool:
        return (self.reading_bare
                and any(_bare(ref) == self.reading_bare for ref in _refs(candidate)))

    def _raw(self, candidates, allowed):
        self._reasons = list(allowed[:1]) or ["NO_LEGAL_CANDIDATE"]
        offered = [{"candidate_id": c.get("candidate_id"), "kind": c.get("candidate_kind"),
                    "source_ref": c.get("source_ref"),
                    "targets": _refs(c), "legal": _legal(c)} for c in candidates]
        self.offered.append({"intent": self.intent, "candidates": offered})
        startable = [c for c in candidates if "START" in _legal(c)]
        continues = [c for c in candidates if "CONTINUE" in _legal(c)]
        pauses = [c for c in candidates if "PAUSE" in _legal(c)]
        resumes = [c for c in candidates if "RESUME" in _legal(c)]

        if self.intent == "home_small_thing":
            for prefix in HOME_SMALL_THINGS:
                for candidate in startable:
                    if any(ref.startswith(prefix) for ref in _refs(candidate)):
                        self.reads.append({"intent": self.intent, "chose": prefix})
                        return self._decision("START", candidate, "家具边的一件小事")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "home_object":
            # 家里真的有一件自己的物件：拿起它（PICK_UP）走的是既有 dailyact 端口，
            # 不靠 stay 标题冒充「整理了桌子」。
            for candidate in startable:
                if any(ref.startswith("dailyact:pick_up:") for ref in _refs(candidate)):
                    self.reads.append({"intent": self.intent, "chose": "pick_up"})
                    return self._decision("START", candidate, "拿起桌上的笔记本")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "home_object_back":
            # 0.7 本单：owner 自己投影出来的同地点台面就是 PLACE 的合法目的地（与
            # get_environment / REST 用同一份 surface 真相）。优先放回床边台面，其次任一
            # 真实台面，最后才退回这间房——房间是既有退路，不是用来绕开台面验收的。
            # 多世界链可以点名某件家具的实例台面（按桌面 owner 实例 id 认真实候选）。
            if self.place_surface_object:
                for candidate in startable:
                    for ref in _refs(candidate):
                        if (ref.startswith("dailyact:place:surface:")
                                and self.place_surface_object in ref):
                            self.reads.append({"intent": self.intent, "chose": ref})
                            return self._decision("START", candidate, "把手里的小物件放到窗边桌的桌面")
            for prefix in ("dailyact:place:surface:home:bed_surface:",
                           "dailyact:place:surface:", "dailyact:place:room:"):
                for candidate in startable:
                    if any(ref.startswith(prefix) for ref in _refs(candidate)):
                        self.reads.append({"intent": self.intent, "chose": prefix})
                        reason = ("把笔记本放回床边台面" if ":surface:" in prefix
                                  else "把笔记本放回这间房")
                        return self._decision("START", candidate, reason)
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "go_to":
            wanted = "place:%s" % self.place_wish if self.place_wish else ""
            for candidate in startable:
                if wanted and wanted in _refs(candidate):
                    self.reads.append({"intent": self.intent, "chose": wanted})
                    return self._decision("START", candidate, "走到想去的可达地点")
            for candidate in startable:
                if self._target_place(candidate):
                    self.reads.append({"intent": self.intent,
                                       "chose": self._target_place(candidate)})
                    return self._decision("START", candidate, "走到可达的地方")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "buy":
            purchases = [c for c in startable
                         if any(ref.startswith("buy:") for ref in _refs(c))]
            preferred = [c for c in purchases
                         if self.buy_wish and self.buy_wish in _refs(c)]
            if preferred or purchases:
                chosen = (preferred or purchases)[0]
                target = next(ref for ref in _refs(chosen) if ref.startswith("buy:"))
                self.reads.append({"intent": self.intent, "chose": target})
                return self._decision("START", chosen, "在营业的店里买一件真的在卖的东西")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "read":
            for candidate in startable:
                if self._is_reading(candidate):
                    self.reads.append({"intent": self.intent, "chose": "reading"})
                    return self._decision("START", candidate, "读自己的材料")
            # If the reading Activity is already running, CONTINUE is the only legal move.
            for candidate in continues:
                if self._is_reading(candidate) or candidate.get("activity_ref"):
                    self.reads.append({"intent": self.intent, "chose": "continue"})
                    return self._decision("CONTINUE", candidate, "继续读下一页")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "pause_reading":
            if continues:
                self.reads.append({"intent": self.intent, "chose": "PAUSE"})
                # No reevaluation_hint_ref: the cycle's reading-only default condition is what
                # binds the short interval, not the model.
                return self._decision("PAUSE", pauses[0] if pauses else continues[0],
                                      "先放下这本书")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "resume":
            wanted = str(self.resume_activity or "")
            preferred = [c for c in resumes
                         if wanted and str(c.get("activity_ref") or "") == wanted]
            if preferred or resumes:
                chosen = (preferred or resumes)[0]
                self.reads.append({"intent": self.intent, "chose": "RESUME",
                                   "activity_ref": chosen.get("activity_ref")})
                return self._decision("RESUME", chosen, "同一本书，从原停点接着读")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "continue":
            if continues:
                self.reads.append({"intent": self.intent, "chose": "CONTINUE"})
                return self._decision("CONTINUE", continues[0], "继续当前活动")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "toggle_lamp":
            # 绝对开关：owner 只把「与当前记录相反的绝对状态」作为候选给出，所以脚本替身
            # 只从真实候选里挑一个，不自己决定 on/off，也不盲切。多世界链可以点名某件灯。
            for candidate in startable:
                for ref in _refs(candidate):
                    if not ref.startswith("dailyact:toggle_fixture:"):
                        continue
                    if self.toggle_object_wish and not ref.endswith(":" + self.toggle_object_wish):
                        continue
                    self.reads.append({"intent": self.intent, "chose": ref})
                    return self._decision("START", candidate, "把房间里这盏灯的电源设成相反状态")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "arrange_lamp":
            # 只有 owner 自己给出的合法目标（另一个已声明区域，或家具自己的台面）可选。
            # 声明的意愿是把它挪到她此刻所在的区域（如果那确实是合法候选），这样随后就能
            # 在正确区域开灯；否则退回第一个 owner 给出的真实 ARRANGE 目标。多世界链可以
            # 再按实例 id 点名某一件家具。
            wish_prefix = ("dailyact:arrange:room:home:%s:" % self.arrange_area_wish
                           if self.arrange_area_wish else "")
            for prefix in (wish_prefix, "dailyact:arrange:room:", "dailyact:arrange:surface:"):
                if not prefix:
                    continue
                for candidate in startable:
                    for ref in _refs(candidate):
                        if not ref.startswith(prefix):
                            continue
                        if self.arrange_object_wish and not ref.endswith(":" + self.arrange_object_wish):
                            continue
                        self.reads.append({"intent": self.intent, "chose": ref})
                        return self._decision("START", candidate, "把这件自己买下的家具挪到另一处")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "write_project":
            # 0.7 第三阶段 (a)/(b)：只从 AG-1 当轮真正给出的 ``popt:`` 候选里选一个。
            # 候选的来源 kind/ref 由既有 life_project_options 决定，脚本替身不自己造候选；
            # 这里只按「本轮已经算出来的 kind」认 QUESTION_FROM_MATERIAL，不按顺序猜。
            kinds = {str(row.get("option_ref")): str(row.get("kind"))
                     for row in (getattr(self.cycle, "_round_project_options", None) or [])}
            for candidate in startable:
                source_ref = str(candidate.get("source_ref") or "")
                if (source_ref.startswith("popt:")
                        and kinds.get(source_ref) == lpo.KIND_QUESTION):
                    self.reads.append({"intent": self.intent, "chose": source_ref})
                    return self._decision("START", candidate, "把白天没弄懂的问题写成一个东西")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "research_start":
            # 0.7 第九阶段：只从本轮真正给出的 ``popt:`` 里认 RESEARCH_FROM_QUESTION，并只挑
            # 她刚写下的那条疑问（按 seed_text 认，不按顺序猜、不自己造候选）。
            kinds = {str(row.get("option_ref")): str(row.get("kind"))
                     for row in (getattr(self.cycle, "_round_project_options", None) or [])}
            wanted_question = str(getattr(self, "research_question", "") or "")
            for candidate in startable:
                source_ref = str(candidate.get("source_ref") or "")
                if not (source_ref.startswith("popt:")
                        and kinds.get(source_ref) == lpo.KIND_RESEARCH):
                    continue
                option = next((row for row in (self.cycle._round_project_options or [])
                               if str(row.get("option_ref")) == source_ref), {})
                if wanted_question and wanted_question not in str(
                        option.get("seed_text") or ""):
                    continue
                self.reads.append({"intent": self.intent, "chose": source_ref})
                return self._decision("START", candidate,
                                      "把刚写下的疑问和真实资料立成一件研究")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "review":
            # 0.7 第四阶段：只从本轮真正给出的 ``popt:`` 候选里认 REVIEW_FROM_EXPERIENCE，
            # 不按顺序猜、也不自己造候选。
            kinds = {str(row.get("option_ref")): str(row.get("kind"))
                     for row in (getattr(self.cycle, "_round_project_options", None) or [])}
            for candidate in startable:
                source_ref = str(candidate.get("source_ref") or "")
                if (source_ref.startswith("popt:")
                        and kinds.get(source_ref) == lpo.KIND_REVIEW):
                    self.reads.append({"intent": self.intent, "chose": source_ref})
                    return self._decision("START", candidate, "把确实发生过的事写成一份回顾")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent in ("continue_project", "abandon", "continue_research",
                           "abandon_research"):
            # 只认刚创建的那个 artifact：它的 refs 里必须出现自己的裸 id；恢复之后，同一个
            # 前台 Activity 的候选也用它自己的 activity_ref 认（仍然是本轮真正 offered 的句柄）。
            bare = self.project_bare
            wanted = ("ABANDON" if self.intent in ("abandon", "abandon_research")
                      else "CONTINUE")
            pool = (startable + continues + pauses) if wanted == "ABANDON" else continues
            foreground_ref = ""
            if self.cycle is not None:
                try:
                    foreground_ref = str(
                        self.cycle.runtime.canonical_state().get("foreground_activity_ref")
                        or "")
                except Exception:
                    foreground_ref = ""
            for candidate in pool:
                refs = _refs(candidate)
                named = bool(bare) and any(bare in ref for ref in refs)
                same_activity = bool(foreground_ref) and str(
                    candidate.get("activity_ref") or "") == foreground_ref
                if not (named or same_activity):
                    continue
                if wanted not in _legal(candidate):
                    continue
                self.reads.append({"intent": self.intent, "chose": wanted})
                return self._decision(wanted, candidate, "继续写自己刚起头的那本小册子"
                                      if wanted == "CONTINUE" else "先把它放下")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "pose_sleep":
            # 0.7 第六阶段：只从 owner 真正给出的躺姿候选里选，不自己写姿态。
            for candidate in startable:
                if any(str(ref).startswith("bodyact:pose:lying:")
                       for ref in _refs(candidate)):
                    self.reads.append({"intent": self.intent, "chose": "pose:lying"})
                    return self._decision("START", candidate, "躺到床上")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "rest_sleep":
            # 睡眠候选只在 owner 同时说 start_available 与 sleep_like_available 时存在；
            # 脚本替身不推断她累不累，只选 owner 真正给出来的那一条。
            for candidate in startable:
                if any(str(ref) == "bodyact:rest:start:sleep_like"
                       for ref in _refs(candidate)) or str(
                           candidate.get("source_ref") or "") == (
                               "bodyact:rest:start:sleep_like"):
                    self.reads.append({"intent": self.intent,
                                       "chose": "bodyact:rest:start:sleep_like"})
                    return self._decision("START", candidate, "开始正式的夜间睡眠会话")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "rest_wake":
            # 次日清晨：只选 owner 真正给出的 stop 候选（会话仍开着才存在）。
            for candidate in startable:
                if any(str(ref).startswith("bodyact:rest:stop:")
                       for ref in _refs(candidate)):
                    self.reads.append({"intent": self.intent, "chose": "rest:stop"})
                    return self._decision("START", candidate, "结束夜间睡眠会话")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "pose_stand":
            for candidate in startable:
                if any(str(ref).startswith("bodyact:pose:standing:")
                       for ref in _refs(candidate)):
                    self.reads.append({"intent": self.intent, "chose": "pose:standing"})
                    return self._decision("START", candidate, "起床站起来")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "execute_future":
            # 0.7 第六阶段：到期 fitem 由正常 PERSONAL_OPPORTUNITY 候选给出；这里的 START
            # 就是「真的动手做那件事」，不是只读浮现。
            for candidate in startable:
                if str(candidate.get("source_ref") or "").startswith("fitem:"):
                    self.reads.append({"intent": self.intent,
                                       "chose": str(candidate.get("source_ref"))})
                    return self._decision("START", candidate, "动手做昨晚留给自己的那件事")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "futures":
            # 到期后只读：记下 owner 路径真实给出的可执行候选，但本轮决定 NO_ACTION，
            # 不自动执行、不造空 Activity。
            for candidate in startable:
                source_ref = str(candidate.get("source_ref") or "")
                if source_ref.startswith("fitem:"):
                    self.reads.append({"intent": self.intent, "chose": source_ref})
                    return self._no_action()
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "propose_future":
            # 提案由 invoke() 附着到本轮 raw output；这里只给一个合法的静默决定。
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "share":
            # 分享是 Decision 的附带请求（sharing 字段），不改变本轮选中的候选；这里给一个
            # 合法静默决定，真正的 prepare/defer/send/drop 由 invoke() 从本轮真实块里回显。
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "accept_invitation":
            # typed 共处邀请：只认本轮真正 offered 且 START 合法的 co_presence 候选，
            # 不自己造候选、不把普通消息升级成邀请。
            wanted = str(self.invitation_ref or "")
            for candidate in startable:
                if wanted and any(wanted in ref for ref in _refs(candidate)):
                    self.reads.append({"intent": self.intent, "chose": wanted})
                    return self._decision("START", candidate, "接受一次明确的共同时间邀请")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent in ("plan_today", "reserved_observe", "propose_future"):
            # 0.7 第八阶段：显式排今天 / 只读观察今日预留位都由 invoke() 或既有字段完成，
            # 这一轮本身给一个合法静默决定。
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent in ("obligation_start", "self_planned_start"):
            # 0.7 第八阶段：只从本轮真正 offered 的 ``popt:`` 今日预留位里选，按它在
            # ``cycle._round_reserved_options`` 里的真实 kind 认（不自造、不按顺序猜）。
            import life_day_plan as _ldp
            wanted = (_ldp.KIND_OBLIGATION if self.intent == "obligation_start"
                      else _ldp.KIND_SELF_PLANNED)
            kinds = {str(row.get("option_ref")): str(row.get("kind"))
                     for row in (getattr(self.cycle, "_round_reserved_options", None) or [])}
            for candidate in startable:
                source_ref = str(candidate.get("source_ref") or "")
                if source_ref.startswith("popt:") and kinds.get(source_ref) == wanted:
                    self.reads.append({"intent": self.intent, "chose": source_ref})
                    return self._decision("START", candidate,
                                          "把今天的那件%s正式做起来" % (
                                              "义务" if wanted == _ldp.KIND_OBLIGATION
                                              else "自选"))
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "consume":
            wanted = "object:%s" % str(self.consume_object or "")
            for candidate in startable:
                if wanted and wanted in _refs(candidate):
                    self.reads.append({"intent": self.intent, "chose": wanted})
                    return self._decision("START", candidate, "吃掉手里/身边这件真东西")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "toilet":
            for candidate in startable:
                if any(str(ref).startswith("bodyact:toilet:") for ref in _refs(candidate)):
                    self.reads.append({"intent": self.intent, "chose": "toilet"})
                    return self._decision("START", candidate, "用这个地点声明的洗手间")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "start_work":
            # 0.7 第八阶段：让一件已创建但从未开工的 Artifact 由正式 Decision START 起来，
            # 只认本轮真正 offered 的、target_refs 含它自己的候选。候选既可能来自正常的
            # artifact continuation，也可能来自一件已到期的 fitem（同一个作品、同一份 owner
            # 候选来源）；后者只是入口不同，不伪造、不换目标，仍按真实 target 认作品。
            bare = self.project_bare
            for source_prefix in ("artifact:", "fitem:"):
                for candidate in startable:
                    refs = _refs(candidate)
                    if (bare and any(_bare(ref) == bare and str(ref).startswith("artifact:")
                                     for ref in refs)
                            and str(candidate.get("source_ref") or "").startswith(
                                source_prefix)):
                        self.reads.append({"intent": self.intent, "chose": bare,
                                           "via": source_prefix})
                        return self._decision("START", candidate,
                                              "正式开工自己刚建的这本小册子")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        if self.intent == "start_offered_work":
            # 0.7 第十阶段：不硬认某一件，也不替换任何候选来源。就从这一轮 AG-0 **真正
            # offered**、且 START 合法的真实可续写作品里挑第一件（跳过脚本显式排除的已知
            # 作品）。选中的 artifact 裸 id 原样记在 reads 里，后面只按这个真实句柄核对
            # Activity/ATA/ABANDON；不发明来源、不按随机 id 特判。
            for candidate in startable:
                if not str(candidate.get("source_ref") or "").startswith("artifact:"):
                    continue
                refs = [ref for ref in _refs(candidate)
                        if str(ref).startswith("artifact:")]
                if not refs:
                    continue
                bare = _bare(refs[0])
                if not bare or bare in self.work_exclude:
                    continue
                self.reads.append({"intent": self.intent, "chose": bare})
                return self._decision("START", candidate, "从本轮真正给出的可续写作品里挑一件开工")
            self.reads.append({"intent": self.intent, "chose": None})
            return self._no_action()
        self.reads.append({"intent": self.intent, "chose": None})
        return self._no_action()

    @staticmethod
    def _target_place(candidate) -> str:
        for ref in _refs(candidate):
            if ref.startswith("place:"):
                return ref
        return ""

    def _decision(self, decision: str, candidate, reason: str) -> dict:
        return {"decision": decision, "selected_candidate_id": candidate.get("candidate_id"),
                "reason_codes": list(self._reasons), "short_rationale": reason,
                "deferred_candidate_ids": []}

    def _no_action(self) -> dict:
        return {"decision": "NO_ACTION", "selected_candidate_id": None,
                "reason_codes": list(self._reasons),
                "short_rationale": "scripted demo: nothing this round",
                "deferred_candidate_ids": []}

    def _sharing_from_block(self, request) -> dict | None:
        """回显本轮真实 sharing 块里的句柄，返回一个 AG-1 形状的 sharing 请求或 ``None``。

        The scripted decision never invents a source/share id: a ``prepare`` names one artifact
        that ``sharing.sources`` really offered, and every draft action echoes one ``share_id`` +
        its current ``head_version_id`` from ``sharing.shares``.  When the wanted draft is not
        offered (for example a DROPPED share), this returns ``None`` -- the honest answer is "the
        round did not offer it", not a forced owner call.
        """

        spec = self.sharing_request or {}
        situation = getattr(request, "situation_context", None)
        block = (situation.get("sharing")
                 if isinstance(situation, dict) else None)
        if not isinstance(block, dict) or not spec:
            self.sharing_choice = {"offered": False, "reason": "NO_SHARING_BLOCK"}
            return None
        op = str(spec.get("op") or "")
        if op == "prepare":
            wanted = str(spec.get("source_bare") or "")
            sources = [row for row in (block.get("sources") or []) if isinstance(row, dict)]
            chosen = next((row for row in sources
                           if _bare(row.get("artifact_id")) == wanted), None)
            if chosen is None:
                self.sharing_choice = {"offered": False, "reason": "SOURCE_NOT_OFFERED"}
                return None
            self.sharing_choice = {"offered": True, "source": chosen.get("artifact_id"),
                                   "kind": chosen.get("kind")}
            return {"op": "prepare", "source_artifact_id": str(chosen.get("artifact_id")),
                    "share_text": str(spec.get("share_text") or ""),
                    "title": str(spec.get("title") or "")}
        wanted = str(spec.get("share_id") or "")
        rows = [row for row in (block.get("shares") or []) if isinstance(row, dict)]
        row = next((item for item in rows
                    if str(item.get("share_id") or "") == wanted), None)
        if row is None:
            self.sharing_choice = {"offered": False, "share_id": wanted,
                                   "allowed": list(block.get("allowed_share_ids") or [])}
            return None
        self.sharing_choice = {"offered": True, "share_id": wanted,
                               "state": row.get("state"),
                               "head": row.get("head_version_id")}
        action = {"op": op, "share_id": wanted,
                  "expected_head": str(row.get("head_version_id") or "")}
        if op == "defer":
            action["defer_condition"] = str(spec.get("defer_condition") or "")
        elif op == "drop":
            action["reason"] = str(spec.get("reason") or "换一个更想说的意思")
        return action

    def invoke(self, request, *, schema_retry_index: int = 0):
        import agency_decision_ag1 as ag1

        self.calls += 1
        situation = getattr(request, "situation_context", None)
        practice_block = situation.get("practice") if isinstance(situation, dict) else None
        if isinstance(practice_block, dict):
            # 正式 Decision 实际收到的 practice 切片（bounded）；withheld 的理由只能来自这里，
            # 不在演示侧另查 service 冒充“模型收到了”。
            self.last_practice_slice = dict(practice_block)
        claims_block = situation.get("claims") if isinstance(situation, dict) else None
        if isinstance(claims_block, dict):
            # The adapter's bounded projection is what the Decision *really* received; keep only
            # the category facts and short values it already bounded (no private text exists in
            # this block by construction), so the demo can assert what arrived, not what the
            # service would answer if asked again.
            rows = [dict(row) for row in (claims_block.get("claims") or [])
                    if isinstance(row, dict)]
            self.last_claims_block = {"claims": rows,
                                      "conflicts": list(claims_block.get("conflicts") or []),
                                      "not_permission": claims_block.get("not_permission")}
            self.seen_claims_at.append({"intent": self.intent,
                                        "claim_count": len(rows),
                                        "sources": sorted({str(r.get("source_kind") or "")
                                                           for r in rows}),
                                        "statuses": sorted({str(r.get("status") or "")
                                                            for r in rows})})
            for row in rows:
                if row.get("source_kind"):
                    self.seen_claim_source_kinds.add(str(row.get("source_kind")))
                if row.get("status"):
                    self.seen_claim_statuses.add(str(row.get("status")))
        candidates = [dict(row) for row in (getattr(request, "candidates", None) or [])]
        allowed = [str(code) for code in (getattr(request, "allowed_reason_codes", None) or [])]
        raw = self._raw(candidates, allowed)
        if self.sharing_request is not None:
            sharing = self._sharing_from_block(request)
            if sharing is not None:
                raw = dict(raw)
                raw["sharing"] = sharing
            self.sharing_request = None
        if self.later_item is not None:
            # 她的「给未来自己的一句话」走既有 later_item 字段：脚本替身只给提案内容，
            # 校验、落库与到期浮现都由既有 AG-1/life_future_items/AG-0 路径完成。
            raw = dict(raw)
            raw["later_item"] = dict(self.later_item)
            self.later_item = None
        if self.plan_today is not None:
            # 0.7 第八阶段：正式 Decision 顶层 ``plan_today``（只点名一件既有 fitem）。形状由
            # day-plan 域校验，落盘发生在 Decision 提交之后，脚本替身不碰文件。
            raw = dict(raw)
            raw["plan_today"] = dict(self.plan_today)
            self.plan_today = None
        if self.method_proposal is not None:
            # 草案原样进入正式 Decision 的可选字段；AG-1 校验、owner 保存为 TENTATIVE。
            raw = dict(raw)
            raw["method_proposal"] = dict(self.method_proposal)
            self.method_proposal = None
        if self.method_trial is not None:
            # 评价只能命名**本轮真正 offered** 的句柄（由 cycle 本轮读出的 method_refs 给出），
            # 脚本替身不自造句柄，也不在轮外手改做法状态。
            refs = list(self.cycle.practice_method_refs() or []) if self.cycle else []
            if refs:
                raw = dict(raw)
                raw["method_ref"] = refs[0]
                raw["method_trial"] = dict(self.method_trial, method_ref=refs[0])
            self.method_trial = None
        if self.method_ref_override:
            # 0.7 第十阶段：仍然走正式 AG-1 校验，不在这里预判“这个句柄已经退休”。AG-1 的
            # allowed_method_refs 来自 cycle 本轮真正 offered 的集合，旧 handle 不在其中，
            # 因此这条 Decision 必须由 AG-1 自己按 INVENTED_METHOD_REF 拒绝。
            raw = dict(raw)
            raw["method_ref"] = str(self.method_ref_override)
            self.method_ref_override = ""
        return ag1.CognitiveDecisionResponse(
            model_run_ref="model_run:v07-daily:%d" % self.calls,
            model_id=self.model_id, model_version=self.model_version,
            raw_output=raw, latency_ms=1)


class ScriptedReadingResponse:
    """正式阅读响应端口上的脚本替身：第一页记一条 ``unclear`` 疑问，其余页 ``no_change``。

    响应的形状就是 ``subject_reading_response`` 原本的协议（只有 kind/note）；把它变成
    文档版本的是既有的 ``private_daily_items.plan_page_annotation`` 与 owner CAS，这里
    不写盘、不碰 artifact。
    """

    def __init__(self) -> None:
        self.calls = 0

    def respond(self, context, *, observed_at: str):
        self.calls += 1
        if int(context.get("page") or 0) == 1:
            return dict(READING_NOTE)
        return {"kind": "no_change"}


class ScriptedContentClient:
    """续写内容模型替身：固定返回一段小编辑，不联网、无凭证、不读产品模型配置。

    它替的只是「模型写什么」；``ModelContinuationWriter`` 仍从 owner 的当前版本读
    正文、按既有合并规则拼接，再由 AR-0 与 Supply owner 做 CAS。正文从不打印。
    """

    payload = {"kind": "append_paragraph",
               "text": "今天先把这个疑问写成一小段：街上的人比屋里的人更早知道天亮了，"
                       "因为他们听见的不是钟声，是风。",
               "self_note": "stub: 把一个早晨的问题写成一小段"}

    def __init__(self) -> None:
        self.calls = 0
        self.last_prompt_chars = 0
        #: 第九阶段：记录**模型替身实际收到**的写作提示里有哪些文档上下文。它不是另造的
        #: dict，而是 ``ModelContinuationWriter`` 用 owner 读回的上下文拼出来、真的发到
        #: ``complete`` 的那一份；只留键与有界片段，不保存整段提示。
        self.last_prompt_context: dict = {}

    def complete(self, *, system_prompt: str, user_prompt: str, timeout_s: float = 0.0,
                 max_output_tokens: int = 0):
        from agency_model_client import ModelCallResult

        self.calls += 1
        self.last_prompt_chars = len(user_prompt)
        try:
            payload = json.loads(user_prompt)
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            research = payload.get("research_context")
            own = (research.get("own_research")
                   if isinstance(research, dict) else None)
            sources = (own.get("sources") if isinstance(own, dict) else None) or []
            earlier = payload.get("earlier_versions")
            self.last_prompt_context = {
                "keys": sorted(payload),
                "research_own_artifact": (own.get("artifact_id")
                                          if isinstance(own, dict) else None),
                "research_source_count": len(sources),
                "research_source_text_chars": [
                    len(row.get("text") or "") for row in sources
                    if isinstance(row, dict)],
                "earlier_version_count": len(earlier or []),
                "earlier_chars": [len(row.get("excerpt") or "") for row in (earlier or [])
                                  if isinstance(row, dict)],
                "previous_experience_count": len(payload.get("previous_experiences") or []),
            }
        return ModelCallResult(purpose="artifact_edit", provider="stub",
                               model="stub:v07-content", latency_ms=1,
                               text=json.dumps(self.payload, ensure_ascii=False))


def _activity(runtime, activity_id: str) -> dict:
    return dict(runtime.activity_read.get_activity(activity_id) or {})


def _foreground(runtime):
    return runtime.canonical_state().get("foreground_activity_ref")


def _substrate_placements(world_home):
    path = Path(world_home) / "data" / "world" / "body_substrate.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    return dict(document.get("placements") or {})


def _substrate_fixtures(world_home):
    path = Path(world_home) / "data" / "world" / "body_substrate.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    return dict(document.get("fixtures") or {})


class Demo:
    def __init__(self, *, base=None, start_at: datetime | None = None,
                 enable_lived_needs: bool = False) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="nyairo-v07-daily-", dir="/tmp"))
        self.base = base if base is not None else donor.DScenarioBase("runTest")
        self.start_at = start_at
        #: 第八阶段：主链这一实例显式 opt-in 虚拟体感档案（合成 operator 初值），在**建世界时**
        #: 就按既有的 Body 合法时钟机制写好 profile + 初始 runtime；正常主链的其它测试用默认
        #: ``False``，完全不受影响。
        self.enable_lived_needs = bool(enable_lived_needs)
        self.lived_needs: dict = {}
        self.base.setUp()
        self.services = None
        self.runtime = None
        #: 装配前 close() 也要安全（build 失败时不该因缺属性再抛一次）。
        self.gateway = None
        self.port = None

    def build(self) -> None:
        # The donor fixture's own world package is the *default generated* coastal_town
        # (``make_valid_docs``), i.e. exactly the new-world supply this ticket changed.
        self.prepared = self.base.prepare_scenario(keep_fixture_world=True)
        self.data = self.prepared.data
        self.memory = self.prepared.memory
        self.instance = Path(self.base.instance)
        self.world_home = Path(str(self.data["world_home"]))
        # 真实默认新实例在 `nyairo_day init` 里还会跑这一道：世界包自己的室内/场景/
        # 物件（worldgen.apply_interiors_to_package），再由 substrate owner 自己的
        # writer 把可拿物件放进这个全新根。演示不跑它，家里就只有「坐一会儿」，
        # 没有真正拿得起的东西。这里复用同一份正式 helper，不另造 fixture。
        from components.phone import demo as phone_demo
        package_dir = Path(str(self.data["world_package"]))
        self.world_prepared = phone_demo._prepare_world(
            self.world_home, package_dir, world_id=str(self.data["world_id"]))
        phone_demo._write_capabilities(self.world_home, package_dir=package_dir)
        # 业务钟必须先于 World 服务与 Body 初值确定：``WorldBodyService.startup`` 用它自己的
        # 时刻写 Body 的持久游标（``valid_from`` / ``last_advanced_at``），之后 Resolver 提交
        # 与 gateway 判到达也用同一 ``clock``。若先按真实 now 初始化、再喂更早的业务事件，
        # Body owner 会正当地拒绝「now 早于 last_persisted_at」。
        if self.start_at is not None:
            # 多世界链与跨日环境观察需要一个固定的世界日期（2026-11-10 入睡、11-11 晨起）；
            # 业务时钟本来就是注入的机械时钟，不读墙钟。调用方给的时刻即本演示的起点。
            start_clock = self.start_at.astimezone(JST)
        else:
            real_now = datetime.now(timezone.utc)
            # The next weekday at 11:00 JST: the bookstore (10-19), cafe (10-20) and the
            # coast (5-21) are all open; the demo never races the real wall clock.
            day = real_now.astimezone(JST)
            for _ in range(8):
                if day.weekday() < 5 and day.hour < 11:
                    break
                day = (day + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                        microsecond=0)
            start_clock = day.replace(hour=11, minute=0, second=0, microsecond=0)
        self.clock = Clock(start_clock.astimezone(timezone.utc))
        if self.enable_lived_needs:
            # 在 World 服务启动前、室内准备之后写体感档案：World 服务首次 tick 就会读到它。
            # Body 初值的持久锚点就落在本演示业务钟的起点（前 1 分钟），不是真实 now；
            # 合成 operator 初值，如实标注为 fixture，不冒充自然积累或人体测量。
            self.lived_needs = _stage8_enable_lived_needs(self.world_home, self.clock.now)
            _stage8_declare_capabilities(self.world_home)
        self.state_root = odl.life_state_root(self.instance)
        self.gateway = self._open_gateway(
            self.world_home, str(self.data["world_id"]),
            odl.instance_environment(self.instance))
        # The fixture caches the gateway it hands to ``instance_env``; point it at the
        # service this demo constructed so the runtime and provider see the same owner.
        self.base._gateway = self.gateway
        self.base.addCleanup(self.gateway.stop)
        self.env = self.base.instance_env()
        # 0.7 C: 柜台购买走既有经济 owner 的显式开关，和正式 ``--with-room-living`` 时
        # 手机侧传给 Life 的 feature 一致；交付用 owner 自己的即时到家模式（不再起
        # 交付时序进程），所以演示不依赖真实两小时。
        #: Per-demo environment overrides only (never a global config change).  The demo
        #: now reaches a real night + next morning in ONE continued scripted chain; the
        #: profile's per-day round/action counters are sized for a shorter day, so this
        #: longer demonstration carries a larger *demo* allowance.  The product request
        #: ceilings (``CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_*``) are untouched.
        self.env_overrides = {
            "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
            "CHIYO_WORLD_DAILY_RHYTHM_ENABLED": "1",
            "CHIYO_WORLD_DELIVERY_TIME_ENABLED": "0",
            "CHIYO_AUTONOMOUS_MAX_ROUNDS_PER_DAY": "96",
            "CHIYO_AUTONOMOUS_MAX_ACTIONS_PER_DAY": "64",
        }
        self.env.update(self.env_overrides)
        # 0.7 第四阶段：project/practice 的默认 factory 现在直接读 prepare profile 里已有的
        # ``CHIYO_LIFE_SUPPLY_SUBJECT``（同一个实例的真实 subject），所以这里不再手工补
        # ``NYAIRO_LIFE_SUBJECT``；默认装配自己就能接到同一个 Supply owner。
        import world_property_economy as economy
        genesis = economy.initialize_genesis(hermes_home=self.world_home,
                                             environment=self.env)
        check(genesis.get("status") in ("created", "already_initialized"),
              "经济 owner 的初始额度没有建立", genesis)
        self.provider = wmos.WorldMoveOpportunityProvider(
            client=WorldBodyClient(environment=self.env), action_intent=action_intent,
            bridge_dir=str(ROOT / "components" / "world" / "service" / "bridge"),
            environment=self.env, world_home=str(self.world_home),
            now=lambda: self.clock.now, observed_now=lambda: self.clock.now,
            valid_for_seconds=120)
        self.cognition = ScriptedDailyCognition(
            reading_artifact_id=str(self.data["reading_artifact_id"]))
        # 内容生成替身：ModelContinuationWriter 仍从 owner 的 head 读正文并走 AR-0 CAS，
        # 只有「写什么」是脚本答案。它不是产品模型，不发任何外部请求。
        self.content_client = ScriptedContentClient()
        self.writer = ModelContinuationWriter(self.content_client, max_new_chars=300)
        self.runtime, self.port = self.base.runtime_with_memory(
            self.cognition, self.env, self.data, self.memory)
        self.fixture = self.base.assemble(self.runtime, self.env,
                                          observation=self.provider, writer=self.writer)
        self.cycle = self.fixture.cycle
        # 0.7 第四阶段：practice/review 走默认装配（同一个 Supply owner + 同一个 Memory），
        # 不在这里手工关掉，也不手造第二个工作台。研究 seam 仍显式停用，只为让第三阶段的
        # QUESTION_FROM_MATERIAL 主正链保持唯一可选的疑问路径；生产里两者可以同时接线。
        self.reading_response = ScriptedReadingResponse()
        self.bind_journey_business_clock()
        self._bind_cycle()

    def _bind_cycle(self) -> None:
        """Attach the demo's own seams to a (re)assembled cycle, nothing owner-level."""

        self.cognition.cycle = self.cycle
        self.cycle.world_home = str(self.world_home)
        self.cycle.work_artifact_id = self.data["work_artifact_id"]
        # 显式装配边界：本演示要证明 ``QUESTION_FROM_MATERIAL`` 主正链。若研究工作台
        # 也接了线，同一条疑问会按既有规则变成 ``RESEARCH_FROM_QUESTION``；这里只把
        # research seam 显式停在这一条演示之外（不是绕过 owner，也不是造第二个库）。
        import research_work_context as rwc
        # 第九阶段要接回**默认装配已经建好**的真实研究工作台（不是另造服务）。停用前把那条
        # seam 原样留在这里，切回来后重新挂上；若装配本来就没接上，留的是同一个 NOT_WIRED
        # seam，Stage9 会按名停下而不是自己编一个 owner。
        self._assembled_research_seam = self.cycle.research
        self.cycle.research = rwc.ResearchSeam(None)
        self.cycle.reading_response_writer = self.reading_response

    def restore_research(self):
        """Put the default assembly's own research seam back on the cycle.

        Stage 3 deliberately took it off so ``QUESTION_FROM_MATERIAL`` was the only question
        path.  Nothing is rebuilt here: this is the same ``ResearchSeam`` object (and the same
        B-owned service) the assembly resolved from this instance's env, so a NOT_WIRED answer
        stays NOT_WIRED instead of being papered over.
        """

        seam = getattr(self, "_assembled_research_seam", None)
        if seam is None:
            import research_work_context as rwc

            seam = rwc.ResearchSeam(None)
        self.cycle.research = seam
        return seam

    def _open_gateway(self, world_home, world_id: str, environment):
        """Start the real World/Body service with **this demo's** business clock.

        ``WorldBodyService`` already accepts ``clock=`` and hands it to its ``WorldResolver``
        (``now=self._moment``).  The shared test fixture builds the service without one, and
        ``startup()`` writes the Body's first cursor at ``_moment()``; binding after startup
        would leave ``last_advanced_at`` on the host clock and then feed it earlier business
        events, which the Body owner must refuse (``now`` before ``last_persisted_at``).  So
        the clock has to be in place *before* ``startup()`` -- that is the only thing this
        helper adds to the fixture gateway.  Authority lease/TTL, socket timeouts and the
        one-shot authorization registry keep their own real-time clocks: ``clock=`` only
        moves the World/Body business moment, never the security face.
        """

        import threading
        import time

        import test_one_day_life as fixture_mod
        import world_body_config as wcfg
        import world_body_service as wbsvc

        class _BusinessClockWorldGateway(fixture_mod.WorldGateway):
            def __init__(inner) -> None:
                inner.world_home = Path(world_home)
                inner.config = wcfg.load_config(
                    dict(environment or {}), runtime_root=fixture_mod.WORLD_RUNTIME_ROOT,
                    world_home=inner.world_home, runtime_mode="canonical",
                    authority="production", world_id=world_id)
                inner.service = wbsvc.WorldBodyService(
                    inner.config, clock=lambda: self.clock.now,
                    tick_seconds=3600.0, instance_id="w05r-%d" % os.getpid())
                inner.startup_report = inner.service.startup()
                inner.socket_path = inner.service.gateway_socket_path
                inner._thread = threading.Thread(
                    target=inner.service.serve, kwargs={"max_seconds": 1800.0}, daemon=True)
                inner._thread.start()
                deadline = time.monotonic() + 10.0
                while not inner.socket_path.exists():
                    if time.monotonic() > deadline:
                        raise AssertionError(
                            "World gateway socket never appeared: %s" % inner.socket_path)
                    time.sleep(0.02)

        return _BusinessClockWorldGateway()

    def bind_journey_business_clock(self) -> None:
        """Put the life-side journey engine on the same business clock.

        ``JourneyWorldResolver`` accepts ``now=`` (the departure/settlement domain) but the
        shared ``runtime_with_memory`` helper does not forward one, so it falls back to the
        host wall clock.  A timed MOVE accepted there would then compare the host's "now"
        against owner travel records stamped in the demo's business domain and settle the
        arrival inside the same request.  Bind this **instance's** resolver (never a module
        global) to the demo clock; the owner's security face is untouched.
        """

        adapter = getattr(self.runtime, "world_action_adapter", None)
        resolver = getattr(adapter, "resolver", None)
        if resolver is not None:
            resolver._clock = lambda: self.clock.now

    def round(self, intent: str) -> dict:
        self.cognition.intent = intent
        before = self.cognition.calls
        out = self.cycle.evaluate_once(
            trigger_ref="evt:v07-daily:%s:%d" % (intent, self.cognition.calls + 1),
            observed_at=self.clock.iso(), act=True)
        offer = self.cognition.offered[-1]["candidates"] if self.cognition.offered else []
        say("round", "%s → outcome=%s decision=%s adoption=%s model_calls=%d offered=%d"
            % (intent, out.get("outcome"), out.get("decision"), out.get("adoption_status"),
               self.cognition.calls - before, len(offer)))
        if str(out.get("outcome")) in ("DECISION_FAILED", "STEP_REFUSED", "NO_STEP_AVAILABLE"):
            dump("round-detail", {key: out.get(key) for key in (
                "detail", "error", "reason", "decision_route", "step_path",
                "selected_candidate_ref", "adoption_status", "outcome")})
            dump("round-offer", _candidate_targets(self.cycle))
        return out

    def round_until(self, intent: str, *, attempts: int = 6) -> dict:
        """Run *intent* until the round is really evaluated (quiet gate may hold it briefly)."""

        out = self.round(intent)
        for _ in range(attempts - 1):
            if str(out.get("outcome")) != "QUIET":
                break
            out = self.round(intent)
        return out

    def share_round(self, request: dict, *, attempts: int = 6) -> dict:
        """Run one formal Decision round whose only extra request is a bounded share action."""

        self.cognition.sharing_request = dict(request)
        self.cognition.sharing_choice = None
        return self.round_until("share", attempts=attempts)

    def issue_disclosure_permit(self, source_artifact_id: str, version_id: str,
                                operation_id: str) -> dict:
        """The real operator CLI issues one exact disclosure permit (no model, no credentials).

        The subprocess gets a *synthetic* minimal environment: the CLI reads the instance's own
        socket/subject from its instance file and never the operator's model profile, so this demo
        cannot pick up user credentials or call a product model.
        """

        command = [sys.executable, "-B",
                   str(ROOT / "scripts" / "disclosure_authority_cli.py"),
                   "--instance", str(self.instance), "issue",
                   "--source", str(source_artifact_id),
                   "--version", str(version_id),
                   "--operation-id", str(operation_id)]
        clean_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.root),
            "TMPDIR": "/tmp",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60,
                                   env=clean_env, cwd=str(self.root))
        lines = (completed.stdout or "").strip().splitlines()
        try:
            payload = json.loads(lines[-1]) if lines else {}
        except ValueError:
            payload = {"status": "UNPARSEABLE"}
        return {"returncode": completed.returncode, **dict(payload)}

    def indoor_move(self, location: str) -> dict:
        """Move to an indoor area through the existing world owner path.

        ``chiyo_bundle.world_move_tool.move_handler`` is the real navigation entry: it
        resolves the node, follows ``world_living_skeleton``'s own route (through the
        building entrance for a non-entrance area) and calls the single writer
        ``apply_move_to``.  This demo never writes ``world_state.location`` itself -- a
        position written by hand would not be a measured move.

        The returned payload is the tool's own JSON result.
        """

        import chiyo_bundle.world_move_tool as world_move_tool

        def _call():
            return json.loads(world_move_tool.move_handler(
                {"location": location}, environment=dict(self.env),
                now=self.clock.now))

        result = _call()
        # Timed travel is enabled in this instance: an indoor hop can be ``started``
        # rather than ``arrived``.  The journey owner's own arrival still happens on the
        # business clock, so advance that clock by its estimated minutes and ask again
        # (bounded, and only through the same tool -- never by writing a position).
        for _ in range(6):
            if str(result.get("status")) in ("arrived", "already_there"):
                break
            minutes = (result.get("estimated_minutes")
                       or result.get("remaining_minutes") or 1)
            self.clock.advance(max(1, int(minutes)) * 60, because="室内移动计时")
            result = _call()
        return result

    def reopen_existing(self) -> dict:
        """Cold-start from the *same* persisted instance: close old handles, reopen owners.

        Order is the production order: the Life runtime is closed, the old World owner
        service is stopped and its thread joined, and the old Supply server is shut down
        through its own ``server.shutdown`` / ``server.server_close`` / ``drain`` /
        ``service.close`` lifecycle (the socket path is dropped only by the owner's own
        ``prepare_socket_path`` guard inside that shutdown, never by a bare unlink here).
        Only then is a **fresh** Supply server (same data root, same socket path), a
        **fresh** World service, a **fresh** Memory handle set (same SQLite files) and a
        **fresh** Life runtime + assembly built from the already-persisted instance and
        profile.  No new root, no re-prepare, no fixture setUp, no copy/restore, no seed
        rewrite: the reopened process only reads what the old one left on disk.
        """

        old_runtime = self.runtime
        old_gateway = self.gateway
        old_services = self.base.services
        old_memory = self.memory
        old_runtime.close()

        old_gateway.stop()
        old_world_thread = getattr(old_gateway, "_thread", None)
        old_thread_stopped = bool(old_world_thread is not None
                                  and not old_world_thread.is_alive())

        # Normal Supply owner shutdown: stop the old server + service, then rebuild a new
        # server/service object pair on the same data root.  The old writer fences are
        # released by ``service.close()`` so the new service can fence the same writer.
        old_services.stop()
        old_supply_thread = getattr(old_services, "_thread", None)
        old_supply_thread_stopped = bool(old_supply_thread is not None
                                         and not old_supply_thread.is_alive())
        old_supply_closed = bool(getattr(old_services, "_closed", False))
        new_services = type(old_services)(old_services.root)
        self.base.services = new_services

        # Fresh Memory handles over the same evidence/control files: the old consumer/port
        # objects are not kept in memory as "cold recovery" evidence.
        self.memory = donor.MemoryFixture(old_memory.root)

        data = odl.read_instance(self.instance)
        self.gateway = self._open_gateway(
            self.world_home, str(data["world_id"]),
            odl.instance_environment(self.instance))
        # The fixture caches the gateway it hands to ``instance_env``; point it at the new
        # owner so the reopened runtime and provider see the restarted service.
        self.base._gateway = self.gateway
        env = self.base.instance_env()
        env.update(self.env_overrides)
        self.env = env
        self.runtime, self.port = self.base.runtime_with_memory(
            self.cognition, env, self.data, self.memory)
        self.fixture = self.base.assemble(
            self.runtime, env, observation=self.provider, writer=self.writer)
        self.cycle = self.fixture.cycle
        self.bind_journey_business_clock()
        self._bind_cycle()
        return {
            "old_thread_stopped": old_thread_stopped,
            "old_supply_thread_stopped": old_supply_thread_stopped,
            "old_supply_closed": old_supply_closed,
            "runtime_replaced": self.runtime is not old_runtime,
            "gateway_replaced": self.gateway is not old_gateway,
            "supply_replaced": new_services is not old_services,
            "supply_server_closed_and_rebuilt": bool(
                old_supply_thread_stopped and old_supply_closed),
            "memory_replaced": self.memory is not old_memory,
            "supply_server": "reopened_same_data_root",
        }

    def close(self) -> None:
        if self.runtime is not None:
            try:
                self.runtime.close()
            except Exception:
                pass
        if self.gateway is not None:
            try:
                self.gateway.stop()
            except Exception:
                pass
        for step in (self.base.tearDown, self.base.doCleanups):
            try:
                step()
            except Exception:
                pass


def _candidate_targets(cycle) -> list[str]:
    """The last round's really-offered target refs (report only)."""
    view = getattr(cycle, "last_world_opportunity_view", None) or []
    return [str(item.get("target_ref")) for item in view if isinstance(item, dict)]


def _share_inbox(service) -> dict:
    """The sharing owner's own local-inbox counts (ids and numbers only, never text)."""

    answer = service.list_shares(include_text=False)
    data = dict((answer or {}).get("data") or {})
    inbox = dict(data.get("inbox") or {})
    return {"ok": bool(answer.get("ok")),
            "count": inbox.get("delivered_count"),
            "counts": dict(data.get("counts") or {})}


class _OtherWorldBase(donor.DScenarioBase):
    """A second, non-coastal world instance built the same way the default one is.

    It only overrides *which* generated package the fixture uses and how the reading material
    is chosen (explicit starter-library opt-in).  Everything else -- real Supply owner, real
    World/Body service, real runtime, real Memory -- is the donor fixture's own machinery.
    """

    def __init__(self, methodName: str = "runTest", *, theme: str = "mountain_town",
                 seed: int = 777777) -> None:
        self._theme = theme
        self._seed = seed
        super().__init__(methodName)

    def _make_world(self):
        from components.worldgen.instance import init_instance
        from components.worldgen.tests._setup import make_valid_docs

        package_dir = self.temp_package(make_valid_docs(seed=self._seed, theme=self._theme),
                                        prefix="v07-other-pkg-")
        home = Path(self.temp_dir(prefix="v07-other-world-")) / "world-home"
        init_instance(package_dir, home)
        return SimpleNamespace(package_dir=package_dir, home=home)

    def prepare_scenario(self, **kwargs):
        kwargs.pop("keep_fixture_world", None)
        memory = kwargs.pop("memory", None) or self.memory()
        result = self.prepare_instance(
            reading_title="", starter_reading=True, create_private_items=True,
            memory_db=memory.evidence_db, memory_controls_db=memory.controls_db,
            memory_subject=donor.MEMORY_OWNER, **kwargs)
        data = odl.read_instance(self.instance)
        return SimpleNamespace(result=result, data=data, memory=memory)


def _observe_ambient(demo) -> dict:
    """The authoritative World ambient read at the demo's *business* moment.

    The date comes from the world package's own timezone (``world_foundation.world_local_time``)
    and the event set from ``world_ambient_events`` -- the very projection the World owner
    calls -- never from ``date.today()`` or the wall clock.  ``observe_world`` is also called
    so the formal World observation is exercised in the same round.
    """

    import world_ambient_events as wae
    import world_foundation as wf
    import world_living_skeleton as wls
    import world_runtime_data as wrd

    env = dict(demo.env)
    env["CHIYO_WORLD_AMBIENT_EVENTS_ENABLED"] = "1"
    env["CHIYO_WORLD_LIVING_SKELETON_ENABLED"] = "1"
    wrd.reset_cache()
    world = wrd.active_world(env)
    state = json.loads((demo.world_home / "data" / "world" / "world_state.json")
                       .read_text(encoding="utf-8"))
    local = wf.world_local_time(state, demo.clock.now)
    day = local.date()
    active = wae.active_ambient_events(
        day, local, timezone_name=world.timezone_name,
        allowed_location_ids=world.locations, relevant_location_ids=None, environment=env)
    materialized = wae.materialize_ambient_events(
        day, timezone_name=world.timezone_name,
        allowed_location_ids=world.locations, environment=env)
    observed = wls.observe_world(hermes_home=str(demo.world_home), now=demo.clock.now,
                                 environment=env)
    observed_ambient = observed.get("ambient_events")
    return {
        "world_id": world.world_id,
        "timezone": world.timezone_name,
        "local_date": day.isoformat(),
        "active": active,
        "materialized": materialized,
        "observed_status": observed.get("status"),
        "observed_ambient": observed_ambient if isinstance(observed_ambient, list) else None,
    }


def _advance_to_due(demo, due: str, *, because: str) -> None:
    """Move the business clock forward to an owner-declared instant (never backwards)."""

    moment = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
    if moment <= demo.clock.now:
        return
    demo.clock.advance(int((moment - demo.clock.now).total_seconds()), because=because)


def _settle_move(demo, walked: dict, *, place: str) -> dict:
    """Let the owner settle one real MOVE only after the business clock reaches its due.

    The round must first answer ``IN_TRANSIT``; the business clock then advances by the
    route duration the World package itself declared (``expected_arrival_at``) -- segment by
    segment for a multi-hop route -- and only then does the journey owner's own settle
    confirm the arrival.  No position is written here and no duration is shortened.
    """

    check(walked.get("world_verb") == "MOVE" and walked.get("outcome") == "IN_TRANSIT",
          "去 %s 没有先返回正式出发（IN_TRANSIT）" % place, walked)
    journey = dict(walked.get("journey") or {})
    due = str(journey.get("expected_arrival_at") or "")
    check(bool(due), "去 %s 的出发缺少 owner 声明的到达时刻" % place, journey)
    check(datetime.fromisoformat(due.replace("Z", "+00:00")) > demo.clock.now,
          "业务钟已到达/越过 %s 的路线 due，出发没有真实在途" % place,
          {"now": demo.clock.iso(), "due": due})
    settled: dict = {}
    for _ in range(8):
        _advance_to_due(demo, due, because="按世界包路线耗时走到 %s" % place)
        settled = demo.cycle.settle_world_journey(observed_at=demo.clock.iso())
        check(settled.get("ok"), "去 %s 的旅程结算失败" % place, settled)
        if settled.get("arrived") or settled.get("journey_status") == "completed":
            break
        due = str(settled.get("expected_arrival_at") or "")
        check(bool(due), "去 %s 的下一段缺少 owner 声明的到达时刻" % place, settled)
    check(settled.get("arrived"), "没有真正到达 %s" % place, settled)
    return settled


def _end_night_session(demo, *, tag: str) -> dict:
    """Wake from a real sleep_like night on the owner's own terms.

    Under the unified business clock the overnight jump is genuine Body elapsed time, so by
    the time the clock reaches morning the owner may already have settled the session
    (recovery ``completed``, progress 1.0).  If the session is still open the formal REST
    ``stop`` is issued; if the owner already closed it, that is its own fact and no stop is
    invented.  Either way the wake is a formal POSE ``standing``.
    """

    # The owner tick is what advances Body time; let it settle the night at the business
    # morning first, then ask the owner what REST state actually exists.
    demo.gateway.service.tick()
    actions = (demo.gateway.service.gateway_surface.get_available_actions()
               .get("actions") or [])
    rest = next((dict(row) for row in actions if row.get("verb") == "REST"), {})
    current = dict(rest.get("current") or {})
    if rest.get("stop_available"):
        woken = demo.round_until("rest_wake")
        fact = dict(woken.get("body_action") or {})
        check(woken.get("decision") == "START" and fact.get("verb") == "REST"
              and str((fact.get("params") or {}).get("state")) == "stop"
              and fact.get("satisfied") is True,
              "%s 次日晨没有由正式 REST stop 结束会话" % tag, woken)
        how = "正式 REST stop → 会话结束"
    else:
        check(str(current.get("recovery_state")) == "completed"
              and str(current.get("recovery_mode")) == "sleep_like",
              "%s 夜间会话既没有 open stop，也不是 owner 自己结算的 completed/sleep_like"
              % tag, rest)
        how = "Body owner 自己的恢复规则已把 sleep_like 会话结算为 completed"
    stood = demo.round_until("pose_stand")
    fact = dict(stood.get("body_action") or {})
    check(stood.get("decision") == "START" and fact.get("verb") == "POSE"
          and str((fact.get("params") or {}).get("pose")) == "standing",
          "%s 起床没有走正式 POSE standing" % tag, stood)
    return {"how": how, "recovery": current}


def _formally_move(demo, place: str) -> dict:
    """One real MOVE through the offered candidate, settled by the journey owner itself."""

    demo.cognition.place_wish = place
    walked = demo.round_until("go_to")
    read = demo.cognition.reads[-1] if demo.cognition.reads else {}
    check(str(read.get("chose") or "") == "place:%s" % place,
          "没有走到 %s 的正式 MOVE 候选" % place,
          {"read": read, "outcome": walked.get("outcome")})
    return _settle_move(demo, walked, place=place)


def _formal_toggle(demo, object_id: str, expected_state: str, *, why: str) -> dict:
    """One absolute TOGGLE_FIXTURE for a named lamp through the real owner verb."""

    demo.cognition.toggle_object_wish = object_id
    out = demo.round_until("toggle_lamp")
    action = dict(out.get("daily_action") or {})
    check(action.get("verb") == "TOGGLE_FIXTURE" and action.get("satisfied") is True
          and str((action.get("params") or {}).get("state")) == expected_state,
          "%s没有走正式 TOGGLE_FIXTURE %s" % (why, expected_state),
          {"round": out, "offered": demo.cognition.offered[-1],
           "read": demo.cognition.reads[-1] if demo.cognition.reads else None})
    fixture = _substrate_fixtures(demo.world_home).get(object_id) or {}
    check(str(fixture.get("power")) == expected_state,
          "%s后电源不是 %s" % (why, expected_state), fixture)
    activity = _activity(demo.runtime, str(out.get("activity_ref") or ""))
    check(activity.get("status") == "COMPLETED", "%s的 Activity 没有 COMPLETED" % why, activity)
    check(_foreground(demo.runtime) is None, "%s后前台没有释放" % why,
          {"foreground": _foreground(demo.runtime)})
    return out


def _run_other_world_chain(theme: str = "mountain_town", seed: int = 777777) -> int:
    """A second full daily chain in a non-coastal, non-929711 generated world.

    Proves the same owners carry a *different* town: its own name/streets/feature venues and
    its own paged starter reading, a real read with PAUSE -> fresh RESUME -> last page, two
    real purchases (a powerable floor lamp and a window-side table), tick landing, ARRANGE to
    another home area, an absolute TOGGLE, and a real small object placed on the table's own
    instance surface.  Then a real sleep_like night, a cross-day wake, and the ambient day
    rollover -- all on this world's own package and business clock.
    """

    import starter_reading_library as starter_lib
    import world_living_skeleton as wls
    import world_property_economy as economy
    import world_room_environment as wre
    from components.worldgen.loader import load as load_package

    base = _OtherWorldBase(theme=theme, seed=seed)
    demo = Demo(base=base, start_at=datetime(2026, 11, 10, 11, 0, tzinfo=JST))
    try:
        demo.build()
        package_dir = Path(str(demo.data["world_package"]))
        world_doc = json.loads((package_dir / "world.json").read_text(encoding="utf-8"))
        package = load_package(package_dir)
        locations = sorted(package.locations)
        streets = sorted(name for name in locations if name.startswith("street_"))
        features = [name for name in ("hot_spring", "hiking_trail", "viewpoint", "inn",
                                      "craft_shop") if name in locations]
        starter = starter_lib.select_starter_reading(seed, theme)
        check(str(world_doc.get("theme")) == theme, "第二个世界主题不对", world_doc)
        check(str(demo.data.get("world_id")) != "coastal_town_929711",
              "第二个世界仍是默认海岸世界", demo.data.get("world_id"))
        check(bool(str(demo.data.get("reading_artifact_id") or "")),
              "starter reading 没有真的创建成 Supply 材料", demo.data.get("reading_artifact_id"))
        reading_view = odl.reading_view(demo.instance)
        check(str(reading_view.get("artifact_id") or "")
              == str(demo.data.get("reading_artifact_id")),
              "starter reading 没有成为实例的阅读材料", reading_view)
        check(bool(streets) and bool(features),
              "第二个世界没有自己的街道/特色场所", {"streets": streets, "features": features})
        say("other-world", "第二个世界 %s（seed=%d）：%s；街道 %s；特色场所 %s"
            % (world_doc.get("world_id"), seed, world_doc.get("name"), streets, features))
        say("other-reading", "起始分页读物《%s》：%d 页（本地原创库，随 seed/主题确定）"
            % (starter["title"], starter["pages"]))

        # 1. 正式出门到书店，读同一份材料：第 1 页 → 第 2 页 → 无 hint PAUSE。
        _formally_move(demo, "street_1")
        _formally_move(demo, "bookstore")
        first = demo.round("read")
        check(first.get("decision") == "START", "第二个世界的阅读没有正式开始", first)
        reading_activity = str(first.get("activity_ref") or "")
        plan = dict(first.get("reading") or {})
        check(int(plan.get("page") or 0) == 1, "第一页没有按正式阅读端口推进", plan)
        second = demo.round("continue")
        plan2 = dict(second.get("reading") or {})
        check(int(plan2.get("page") or 0) == 2, "第二页没有推进", plan2)
        paused = demo.round("pause_reading")
        check(paused.get("decision") == "PAUSE", "阅读 PAUSE 没有被采纳", paused)
        demo.clock.advance(31 * 60, because="另一个世界的阅读短间隔到期")
        maintained = demo.cycle.maintain_waiting(observed_at=demo.clock.iso())
        eligibilities = dict((demo.runtime.waiting_coordinator.eligibility_store
                              .load_state() or {}).get("eligibilities") or {})
        open_rows = [row for row in eligibilities.values()
                     if row.get("status") == "OPEN"
                     and str(row.get("activity_id")) == reading_activity]
        check(bool(open_rows) or int((maintained.get("due_agenda") or {})
                                     .get("emitted_eligibilities") or 0) >= 1,
              "另一个世界没有发出这本书的 OPEN 资格",
              {"maintained": maintained, "eligibilities": eligibilities})
        demo.cognition.resume_activity = reading_activity
        resumed = demo.round("resume")
        check(resumed.get("decision") == "RESUME", "没有 RESUME 同一本书",
              {"outcome": resumed.get("outcome"), "decision": resumed.get("decision"),
               "offered": demo.cognition.offered[-1]})
        after = _activity(demo.runtime, reading_activity)
        if str(after.get("status")) != "COMPLETED":
            demo.round("continue")
            after = _activity(demo.runtime, reading_activity)
        check(str(after.get("status")) == "COMPLETED",
              "另一个世界的最后一页没有让阅读 COMPLETED", after)
        say("other-read", "在书店读到最后一页：%s 页全部完成，PAUSE→fresh RESUME→COMPLETE"
                          "（Activity %s）" % (starter["pages"], reading_activity))

        # 2. 走到营业的家居店，买下暖光落地灯与窗边小桌（同一经济 owner，两次真实扣款）。
        _formally_move(demo, "street_1")
        _formally_move(demo, "home_goods_store")
        buys: dict[str, str] = {}
        for catalog_item, key in (("warm_floor_lamp", "lamp"),
                                  ("window_side_table", "table")):
            demo.cognition.buy_wish = "buy:%s" % catalog_item
            round_out = demo.round("buy")
            purchase = dict(round_out.get("purchase") or {})
            check(round_out.get("outcome") == "ACTED"
                  and purchase.get("status") in ("applied", "already_applied")
                  and str(purchase.get("ownership")) == "OWNED"
                  and str(purchase.get("catalog_item_id")) == catalog_item,
                  "没有真实买下 %s" % catalog_item,
                  {"outcome": round_out.get("outcome"), "purchase": purchase})
            buys[key] = str(purchase.get("item_instance_id") or "")
            check(bool(buys[key]), "购买回执没有实例 id", purchase)
        demo.gateway.service.tick()
        landed = _substrate_placements(demo.world_home)
        for key, item_id in buys.items():
            row = landed.get(item_id) or {}
            check(str(row.get("kind")) == "room" and str(row.get("location_id")) == "home",
                  "owner tick 没有把 %s 落在家" % key, {"item": item_id, "placement": row})
        say("other-buy", "店内买下暖光落地灯与窗边小桌（两笔 DEBIT + OWNED），owner tick 落到 home")

        # 3. 正式回家：灯在这间（非卧室）区域 ARRANGE + 绝对 TOGGLE on，房间光真的亮起来。
        _formally_move(demo, "street_1")
        _formally_move(demo, "home")
        home_state = json.loads((demo.world_home / "data" / "world" / "world_state.json")
                                .read_text(encoding="utf-8"))
        home_place = str((home_state.get("location") or {}).get("place_id"))
        home_area = str((home_state.get("location") or {}).get("area_id"))
        check(home_place == "home" and home_area and home_area != "bedroom",
              "回到家时不在一个非卧室的已声明区域", home_state.get("location"))
        demo.cognition.arrange_area_wish = home_area
        demo.cognition.arrange_object_wish = buys["lamp"]
        arranged_lamp = demo.round("arrange_lamp")
        arrange = dict(arranged_lamp.get("daily_action") or {})
        check(arrange.get("verb") == "ARRANGE" and arrange.get("satisfied") is True,
              "落地灯 ARRANGE 没有走正式家具端口", arranged_lamp)
        lamp_moved = _substrate_placements(demo.world_home).get(buys["lamp"]) or {}
        check(str(lamp_moved.get("area_id")) == home_area,
              "ARRANGE 后落地灯不在她的区域", {"lamp": buys["lamp"], "placement": lamp_moved})
        # 第一次绝对状态由 owner 自己给出的相反值决定（这里先 off 再 on，两次独立 execution）。
        _formal_toggle(demo, buys["lamp"], "off", why="新买的暖光落地灯第一次关")
        _formal_toggle(demo, buys["lamp"], "on", why="新买的暖光落地灯打开")
        light = wre.light_for_home(str(demo.world_home))
        check(str(light.get("status")) == "available" and str(light.get("power")) == "on"
              and str(light.get("band")) == "moderate",
              "房间光投影没有看到这盏灯亮着", light)
        say("other-light", "落地灯 ARRANGE 到 %s 后正式 TOGGLE on；房间光 status=%s band=%s"
            % (home_area, light.get("status"), light.get("band")))

        # 4. 窗边小桌 ARRANGE 到同一区域；把手里的小物件放到它的实例桌面。
        demo.cognition.arrange_object_wish = buys["table"]
        demo.cognition.arrange_area_wish = home_area
        arranged_table = demo.round("arrange_lamp")
        t_arrange = dict(arranged_table.get("daily_action") or {})
        check(t_arrange.get("verb") == "ARRANGE" and t_arrange.get("satisfied") is True,
              "窗边小桌 ARRANGE 没有走正式家具端口", arranged_table)
        table_moved = _substrate_placements(demo.world_home).get(buys["table"]) or {}
        check(str(table_moved.get("area_id")) == home_area,
              "ARRANGE 后小桌不在她的区域", {"table": buys["table"], "placement": table_moved})
        home_objects = [row for row in (demo.world_prepared.get("takeable") or [])
                        if str(row.get("location_id")) == "home"]
        check(bool(home_objects), "第二个世界的家里没有可拿物件", demo.world_prepared)
        book_object = str(home_objects[0].get("object_id"))
        picked = demo.round("home_object")
        daily = dict(picked.get("daily_action") or {})
        check(daily.get("verb") == "PICK_UP" and daily.get("satisfied") is True,
              "拿起小物件没有走正式物件端口", picked)
        demo.cognition.place_surface_object = buys["table"]
        placed = demo.round("home_object_back")
        placed_daily = dict(placed.get("daily_action") or {})
        check(placed_daily.get("verb") == "PLACE" and placed_daily.get("satisfied") is True,
              "把小物件放到桌面没有走正式物件端口", placed)
        book_placement = _substrate_placements(demo.world_home).get(book_object) or {}
        check(str(book_placement.get("kind")) == "surface"
              and str(book_placement.get("surface_id")) == "desk_surface:%s" % buys["table"],
              "小物件没有真的落在窗边桌的实例台面上",
              {"book": book_object, "placement": book_placement, "chosen": placed_daily.get("target_ref")})
        say("other-surface", "窗边小桌 ARRANGE 到 %s；把 %s 放到 desk_surface:%s（substrate 真变）"
            % (home_area, book_object, buys["table"]))

        # 5. 真实夜晚：沿正式 MOVE 步行测得负荷 → 关灯 → 卧室 → 躺下 → sleep_like → 次日晨停。
        # 业务钟现在也是 Body 的时域：白天活动的负荷会随真实 elapsed 回落，所以睡前要走够
        # 一段街区（仍是世界包自己的正式 MOVE 候选），把测得负荷累积到 sleep_like 门槛。
        for place in ("street_1", "street_2", "street_1", "bookstore",
                      "street_1", "street_2", "street_1", "bookstore",
                      "street_1", "street_2", "street_1", "bookstore",
                      "street_1", "home"):
            _formally_move(demo, place)
        demo.gateway.service.tick()
        current = wls.read_move_state(hermes_home=str(demo.world_home), environment=demo.env,
                                      now=demo.clock.now)
        if str((current or {}).get("world_location_id") or "") != "home/living":
            reached = demo.indoor_move("home/living")
            check(reached.get("ok") is True
                  and str(reached.get("status")) in ("arrived", "already_there"),
                  "没有经既有室内路径到 home/living", reached)
        _formal_toggle(demo, buys["lamp"], "off", why="睡前关灯")
        to_bedroom = demo.indoor_move("home/bedroom")
        check(to_bedroom.get("ok") is True
              and str(to_bedroom.get("status")) in ("arrived", "already_there"),
              "没有经既有室内路径到 home/bedroom", to_bedroom)
        posed = demo.round_until("pose_sleep")
        pose_fact = dict(posed.get("body_action") or {})
        check(posed.get("decision") == "START" and pose_fact.get("verb") == "POSE"
              and str((pose_fact.get("params") or {}).get("pose")) == "lying",
              "躺下没有走正式 POSE lying", posed)
        rest_now = next((dict(row) for row in
                         demo.gateway.service.gateway_surface.get_available_actions()
                         .get("actions") or [] if row.get("verb") == "REST"), {})
        check(rest_now.get("start_available") is True
              and rest_now.get("sleep_like_available") is True,
              "Body/World owner 没有给出 sleep_like 资格（不伪造）",
              {key: rest_now.get(key) for key in
               ("start_available", "sleep_like_available", "start_blocked_reason", "current")})
        slept = demo.round_until("rest_sleep")
        sleep_fact = dict(slept.get("body_action") or {})
        check(slept.get("decision") == "START" and sleep_fact.get("verb") == "REST"
              and str((sleep_fact.get("params") or {}).get("mode")) == "sleep_like",
              "sleep_like 没有由正式 REST start 开启", slept)
        night_ambient = _observe_ambient(demo)

        local_now = demo.clock.now.astimezone(JST)
        next_morning = (local_now + timedelta(days=1)).replace(
            hour=7, minute=0, second=0, microsecond=0)
        demo.clock.advance(int((next_morning - local_now).total_seconds()),
                           because="第二个世界睡到次日晨")
        _end_night_session(demo, tag="第二个世界")
        morning_ambient = _observe_ambient(demo)
        check(night_ambient["local_date"] == "2026-11-10"
              and morning_ambient["local_date"] == "2026-11-11",
              "环境事件读取的世界本地日期没有从 10 变 11",
              {"night": night_ambient["local_date"], "morning": morning_ambient["local_date"]})
        night_ids = {str(row.get("event_id")) for row in night_ambient["materialized"]}
        morning_ids = {str(row.get("event_id")) for row in morning_ambient["materialized"]}
        check(bool(night_ids) and bool(morning_ids), "单日环境事件集合为空，无法证明换日",
              {"night": sorted(night_ids), "morning": sorted(morning_ids)})
        check(not (night_ids & morning_ids), "旧日环境事件在次日仍然存在",
              sorted(night_ids & morning_ids))
        for row in morning_ambient["materialized"]:
            check(str(row.get("local_date")) == "2026-11-11",
                  "次日环境事件的 local_date 不是 11-11", row)
        say("other-night", "正式 sleep_like 跨夜：环境事件世界本地日期 2026-11-10 → 2026-11-11，"
                           "当日集合确定性更新（%d 条），旧日 %d 条全部退场"
            % (len(morning_ids), len(night_ids)))
        say("other-done", "第二个（%s/%d）世界完整链跑完：起始分页读物 → 书店读/PAUSE/RESUME/完成 → "
                          "家居店买暖光落地灯与窗边小桌 → owner tick 落地 → 回家 ARRANGE/TOGGLE on → "
                          "小物件放到窗边桌实例桌面 → 正式 sleep_like 跨夜 → 次日按 owner 状态收尾 → "
                          "环境事件换日" % (theme, seed))
        return 0
    finally:
        demo.close()


# ---------------------------------------------------------------------------------------------
# 0.7 第八阶段：自己排今天与义务 / 同作上下文与手机只读今天 / 线上点心与吃睡如厕体感实链
# ---------------------------------------------------------------------------------------------
STAGE8_PLAN_TITLE = "睡前把今天那半页补完"
STAGE8_OBLIG_TITLE = "把老师留的封底说明补在册子上"
STAGE8_SHARE_TEXT = "今晚这一小段先搁着，明天再说。"
STAGE8_SNACK = "virtual_snack_wafer"
STAGE8_TOILET = "stage8-toilet-01"
_BAND_RANK = {"none": 0, "mild": 1, "noticeable": 2, "strong": 3}


def _band_rank(value) -> int:
    return _BAND_RANK.get(str(value or "none"), 0)


def _stage8_body_dir(world_home) -> Path:
    return Path(world_home) / "data" / "body"


def _stage8_enable_lived_needs(world_home, start_at) -> dict:
    """显式 opt-in 的虚拟体感档案 + operator 合成初值；不是自然积累，不是人体测量。"""

    import body_lived_needs as bln
    import body_runtime as br

    body_dir = _stage8_body_dir(world_home)
    # 正常 enable 参数给三项都落到 noticeable/strong 档（thresholds: >=0.70 noticeable，
    # >=0.88 strong）：hunger/toilet 走明显饿/如厕（toilet 到 strong），thirst 也至少 noticeable。
    # 这是显式 opt-in 的合成 operator 初值，不是自然积累，也不是人体测量；不改 Body state/阈值。
    profile = bln.build_profile(
        hunger=0.82, thirst=0.78, toilet=0.90,
        note=("stage8 fixture: explicit synthetic operator initial values; "
              "not a natural accumulation, not a human measurement"))
    bln.save_profile(body_dir, profile)
    store = br.BodyStore(body_dir)
    state = store.load_runtime()
    if state is None:
        # 体感初值的持久时间锚落在**本演示业务钟起点稍稍之前**，不是真实 now：World 服务首次
        # startup 已用同一个注入时钟，若锚在未来会拒绝「now 早于 last_persisted_at」；若锚在
        # 真实 now（晚于业务起点），随后更早的业务事件同样会被 Body owner 正当拒绝。
        # 档位/回血与该绝对时刻无关。
        anchor = ((start_at if isinstance(start_at, datetime)
                   else datetime.now(timezone.utc)).astimezone(timezone.utc)
                  - timedelta(minutes=1))
        state = br.initial_runtime(at=anchor)
        state = br.validate_runtime(state)
    state = br.upgrade_to_v2(state, bln.initial_state(profile))
    store.save_runtime(state)
    initial = bln.initial_state(profile)
    return {"profile_ref": initial["profile_ref"], "initial_bands": bln.bands(state),
            "initial_levels": dict(state["lived_needs"]["levels"])}


def _stage8_declare_capabilities(world_home) -> None:
    """在 World 自己的声明文件里显式绑定：线上点心解饿 / 家里洗手间如厕。

    这是 World 侧既有的 ``consumable_catalog`` + ``lived_need_facilities`` 声明块，Life 不按
    商品名字硬调身体；没有声明的效果就真的没有。``_write_capabilities`` 会 setdefault 保留这里
    已写入的键，所以这里写的内容不会被室内准备覆盖。
    """

    import world_body_substrate as wbs

    path = wbs.capabilities_path(world_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            document = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            document = {}
    document.setdefault("schema_version", wbs.CAPABILITIES_SCHEMA)
    catalog = document.get("consumable_catalog")
    if not isinstance(catalog, dict):
        catalog = {}
    snack = catalog.get(STAGE8_SNACK)
    snack = dict(snack) if isinstance(snack, dict) else {}
    snack.update({"consumable": True, "free_to_consume": False,
                  "consumption_permit": "owned_purchase",
                  "lived_need_effects": {"hunger": 0.7}})
    catalog[STAGE8_SNACK] = snack
    document["consumable_catalog"] = catalog
    facilities = document.get("lived_need_facilities")
    if not isinstance(facilities, dict):
        facilities = {}
    facilities[STAGE8_TOILET] = {"facility_kind": "toilet", "location_id": "home",
                                 "lived_need_effects": {"toilet": 0.9},
                                 "label": "家里的洗手间"}
    document["lived_need_facilities"] = facilities
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


class _Stage8Base(_OtherWorldBase):
    """默认生成世界（coastal/929711）上构建的一个新实例，并显式 opt-in 虚拟体感档案。"""

    def __init__(self, methodName: str = "runTest", *, theme: str = "coastal_town",
                 seed: int = 929711, start_at: datetime | None = None) -> None:
        self._start_at = start_at or datetime.now(timezone.utc)
        super().__init__(methodName, theme=theme, seed=seed)

    def prepare_scenario(self, **kwargs):
        prepared = super().prepare_scenario(**kwargs)
        world_home = Path(str(prepared.data["world_home"]))
        self.lived_needs = _stage8_enable_lived_needs(
            world_home, self._start_at.astimezone(timezone.utc))
        _stage8_declare_capabilities(world_home)
        return prepared


def _stage8_bands(client) -> dict:
    answer = client.call("get_body_signals")
    data = getattr(answer, "data", None)
    return dict((data or {}).get("needs") or {})


def _stage8_snapshot_bands(client) -> dict:
    answer = client.get_snapshot()
    data = getattr(answer, "data", None)
    return dict((data or {}).get("lived_needs") or {})


def _stage8_state_files(*roots) -> dict:
    """必要状态文件的字节快照（非 SHA）；只收 JSON/DB 主文件，跳过 socket/临时件。"""

    suffixes = {".json", ".jsonl", ".db", ".sqlite", ".sqlite3"}
    snapshot: dict = {}
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        for path in sorted(root_path.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            if path.name.endswith(".tmp") or path.name.endswith("-wal") \
                    or path.name.endswith("-shm") or path.name.endswith("-journal"):
                continue
            try:
                snapshot[str(path)] = path.read_bytes()
            except OSError:
                continue
    return snapshot


def _stage9_state_files(*roots) -> dict:
    """第八阶段同一套必要状态文件，再**加上** owner 的 media 字节。

    Stage9 新写了一张 PNG，只收 JSON/DB 会把媒体排除在「读前后零变」之外；这里只增不减，
    原有文件集合的含义不变，新文件自动纳入。
    """

    snapshot = _stage8_state_files(*roots)
    for root in roots:
        media = Path(root) / "media"
        if not media.exists():
            continue
        for path in sorted(media.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            try:
                snapshot[str(path)] = path.read_bytes()
            except OSError:
                continue
    return snapshot


def _owner_tick_ping(service) -> bool:
    """One real admin round trip on the owner's own socket (``ping`` only)."""

    path = Path(str(getattr(service, "socket_path", "") or ""))
    if not path.exists():
        return False
    try:
        import socket as _socket
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(path))
            client.sendall(b'{"command": "ping", "argument": null}\n')
            buffer = b""
            while b"\n" not in buffer:
                chunk = client.recv(65536)
                if not chunk:
                    break
                buffer += chunk
        payload = json.loads(buffer.decode("utf-8").strip().splitlines()[0])
        return bool(payload.get("ok"))
    except (OSError, ValueError, IndexError):
        return False


@contextmanager
def _owner_tick_frozen(demo, *, label: str):
    """Freeze the World/Body owner's housekeeping tick for a pure-read window.

    ``world_body_service.serve`` runs exactly one ``tick()`` after every served
    connection, so a *live-window* settle can persist ``body/runtime.json`` +
    ``body/transitions.jsonl`` between the two byte snapshots even though every
    phone read itself is read-only.  This pauses only that housekeeping callable
    on the demo's own owner object: no fake store, no second service, the real
    socket stays bound and every read still goes to the real owner.  One admin
    round trip proves a tick already in flight has finished before the first
    snapshot; the real tick is restored in ``finally``.
    """

    service = getattr(getattr(demo, "gateway", None), "service", None)
    tick = getattr(service, "tick", None) if service is not None else None
    if service is None or not callable(tick):
        yield False
        return
    service.tick = lambda: None
    try:
        check(_owner_tick_ping(service),
              "%s：World/Body owner 的 tick 冻结探针没有应答（不赌时序）" % label)
        yield True
    finally:
        service.tick = tick


def _stage8_refuse(fn, *args, **kwargs) -> str:
    try:
        fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - a named refusal is the answer
        return str(getattr(exc, "reason_code", type(exc).__name__))[:80]
    return ""


def _run_stage8_chain(demo=None, booklet=None, booklet_bare="", keep_demo=False):
    """第八阶段：今日自选/义务、同作上下文与手机只读、线上点心与体感实链。

    两种入口共用同一段真链：
    * ``demo`` 传 ``None``（单测 helper）：新建一个隔离实例（``_Stage8Base``）并自建一本册子。
    * 传主链 ``demo`` + 晚间 QUESTION 生出的 ``booklet``/``booklet_bare``：接回**同一 root、
      同一本册子**闭环，不再另起实例或另造同名册子（返回前不关闭父级 demo，父级自己收）。

    主链模式的业务钟换成**真实当前业务钟**：World execution ledger / 钱包由各自 owner 用真实
    now 落账，Activity/计划用同一真实钟推进，四类记录才落在同一真实日、时间线能如实合并；
    主链已发生的 2026-11-10→11-11 跨夜证据不受影响也不回改。
    """

    import life_day_plan as ldp
    import world_property_economy as economy
    import world_phone_shopping as wps
    from components.phone import service as phone_service
    from components.phone import own_day_read, day_timeline
    from world_body_client import WorldBodyClient

    own_booklet = demo is None
    if own_booklet:
        start_at = datetime.now(JST)
        base = _Stage8Base(theme="coastal_town", seed=929711, start_at=start_at)
        demo = Demo(base=base, start_at=start_at)
        demo.build()
        # 业务钟设成真实当前业务钟（不吸附到 11:00）：World execution ledger / 钱包由各自 owner
        # 用真实 now 落账，本段 Activity/计划也用同一真实钟，四类记录才落在**同一真实日**、时间线
        # 能确定性合并（否则世界账按真实 now、计划按吸附后的 11:00，跨 +08 日界会错位一天）。
        demo.clock = Clock(datetime.now(JST).astimezone(timezone.utc))
        # 重新确认声明键在室内准备/_write_capabilities 之后仍在（setdefault 会保留，这里兜底）。
        _stage8_declare_capabilities(demo.world_home)
        # World 服务用它自己的真实当刻（Resolver 与 Body 的时间必须单调：body 的持久时间锚在
        # 真实现在，注入一个更早的业务钟会让回血结算被判成「now 早于 last_persisted_at」）。
        demo.gateway.service.tick()
        lnd = dict(getattr(base, "lived_needs", {}) or {})
        say("stage8", "新隔离实例已起；显式体感档案（hunger/thirst/toilet 合成初值=%s）"
            % lnd.get("initial_bands"))
    else:
        # 主链模式：沿用主链同一 root / 同一册子；本段用真实当前业务钟推进今日安排与手机读。
        demo.clock = Clock(datetime.now(JST).astimezone(timezone.utc))
        _stage8_declare_capabilities(demo.world_home)
        demo.gateway.service.tick()
        lnd = dict(getattr(demo, "lived_needs", {}) or {})
        say("stage8", "接回主链同一 root 的实例，同一本册子 %s；显式体感档案初值=%s"
            % (booklet, lnd.get("initial_bands")))
    world_home = demo.world_home
    state_root = demo.state_root
    subject = str(demo.data["subject_id"])
    clock_tag = "stage8" if own_booklet else "stage8-main"
    try:
        initial_snapshot = _stage8_snapshot_bands(WorldBodyClient(environment=demo.env))
        check(bool(initial_snapshot), "World/Body 快照没有报出 lived_needs 档位", initial_snapshot)
        check(all(_band_rank(initial_snapshot.get(name)) >= 2
                  for name in ("hunger", "thirst", "toilet")),
              "显式 profile 初值没有三项都到 noticeable/strong（thirst 不应是 mild）",
              initial_snapshot)
        say(clock_tag, "体感档位（初值）：%s（合成 operator 初值，非自然积累、非人体测量）"
            % initial_snapshot)

        # -- A. 一件真实的、多版本续写过的小册子：版本 / Memory 确证经历 / 做法关联 / 本地草稿 --
        # 单测 helper 自建一本（第一版带 work-source 标记，来历真实 OK）；主链模式直接复用
        # 晚间 QUESTION 生出的 project_artifact，不另造同名册子。
        if own_booklet:
            reading_ref = str(demo.data.get("reading_artifact_id") or "")
            reading_point = demo.port.resume_point(reading_ref) if reading_ref else None
            marker_payload = {
                "source_artifact_id": reading_ref,
                "source_version_id": str(getattr(reading_point, "head_version_id", "") or ""),
                "source_version_number": int(getattr(reading_point, "version_number", 0) or 0),
                "purpose": "quote",
                "note": "把读到的一小段取来续写",
                "taken_at": demo.clock.iso(),
            }
            marker_line = ("<!-- nyairo-work-source:v1 "
                           + json.dumps(marker_payload, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":"))
                           + " -->")
            first_content = marker_line + "\n第一版。"
            created = demo.port.create_artifact(
                workspace_id=str(demo.data["workspace_id"]),
                title="第八阶段的小册子（夹具原创）", content=first_content,
                grant_id=str(demo.data["artifact_grant_id"]),
                operation_key="stage8:booklet:create")
            booklet = "artifact:%s" % _bare(created.artifact_id)
            booklet_bare = _bare(booklet)
            head0 = demo.port.resume_point(booklet)
            demo.cognition.project_bare = booklet_bare
            started = demo.round_until("start_work", attempts=8)
            head1 = demo.port.resume_point(booklet)
            check(str(started.get("decision")) == "START"
                  and int(head1.version_number or 0) > int(head0.version_number or 0),
                  "小册子没有由正式 Decision 开工并写出新版本",
                  {"outcome": started.get("outcome"), "decision": started.get("decision"),
                   "before": head0.version_number, "after": head1.version_number,
                   "offer": demo.cognition.offered[-1] if demo.cognition.offered else None})
            for _ in range(2):
                demo.round_until("continue_project", attempts=8)
            head_multi = demo.port.resume_point(booklet)
            check(int(head_multi.version_number or 0) >= 3,
                  "小册子没有多版本续写", {"version": head_multi.version_number})

            # 本地分享草稿：来源就是这本 ACTIVE 前台的小册子（真实 sharing owner，非手拼）。
            demo.cognition.later_item = {"title": "集合变化用·分享轮", "in_minutes": 6}
            prepared = demo.share_round({"op": "prepare", "source_bare": booklet_bare,
                                         "share_text": STAGE8_SHARE_TEXT,
                                         "title": "第八阶段一小段"})
            share = dict(prepared.get("sharing") or {})
            check(str(share.get("state")) == "DRAFT", "本地分享没有落成 DRAFT",
                  {"outcome": prepared.get("outcome"), "sharing": share,
                   "choice": demo.cognition.sharing_choice})

        # 真实做法关联（两种模式都做）：只把**这本小册子自己**确证过的经历写进一条做法草案，
        # 由 owner 存为 TENTATIVE；不把别处回顾的方法硬算到这本册子上。
        practice = demo.cycle.practice

        def _booklet_experience() -> tuple[str, list[dict]]:
            # 先按 work_context 的 results（它按 artifact_ref == 这本册子扫 Memory）取这本书自己的
            # 可读结果——比"最近 N 条 practice 经历"更准确，主链跑到这里时最近的多是回顾/做法经历。
            rows: list[dict] = []
            try:
                wcx = demo.cycle.work_context_view(booklet)
                rows = [dict(r) for r in ((wcx.get("results") or {}).get("rows") or [])]
            except Exception:
                rows = []
            seen: list[dict] = []
            for row in rows:
                exp_id = str(row.get("experience_id") or "")
                if not exp_id:
                    continue
                try:
                    readable = practice.read_experience(exp_id) is not None
                except Exception:
                    readable = False
                seen.append({"id": exp_id, "artifact": _bare(row.get("artifact_ref")),
                             "readable": readable})
            found = next((r["id"] for r in seen if r["readable"]), "")
            if found:
                return found, seen
            # 兜底：扫一个更宽的 practice 窗口，按 artifact_ref 精确匹配这本册子。
            broad = [dict(row) for row in practice.experiences(limit=60)]
            for row in broad:
                exp_id = str(row.get("experience_id") or "")
                if not exp_id or _bare(row.get("artifact_ref")) != booklet_bare:
                    continue
                try:
                    readable = practice.read_experience(exp_id) is not None
                except Exception:
                    readable = False
                seen.append({"id": exp_id, "artifact": booklet_bare, "readable": readable})
                if readable:
                    return exp_id, seen
            return "", seen

        booklet_exp, readable_rows = _booklet_experience()
        if not booklet_exp:
            # 这件作品的写作结果若还没成为一个经治理仍可读的经历，就先真正续写一版再取。
            demo.cognition.project_bare = booklet_bare
            if not _foreground(demo.runtime):
                demo.round_until("start_work", attempts=8)
            demo.round_until("continue_project", attempts=8)
            demo.round_until("continue_project", attempts=8)
            booklet_exp, readable_rows = _booklet_experience()
        dump("stage8-exp", {"experiences": readable_rows, "chosen": booklet_exp})
        check(bool(booklet_exp), "小册子的写作结果没有进入 Memory 成为可读经历",
              {"experiences": readable_rows})
        # 先把册子开到可续写的前台（start_work 轮），再把做法草案交给紧随其后的 CONTINUE 轮——
        # method_proposal 只随一次真正的续写 Decision 提交，不能被 start_work 那一轮提前吃掉。
        demo.cognition.project_bare = booklet_bare
        if not _foreground(demo.runtime):
            started_m = demo.round_until("start_work", attempts=8)
            check(str(started_m.get("decision")) == "START",
                  "做法草案前没有正式开工这本册子", started_m)
        demo.cognition.method_proposal = {
            "title": "先把一页真的读到自己懂了再落笔",
            "instruction": "动笔前，先确认这一页自己是真的读懂了，只写它实际改变的那一点。",
            "scope": "仅在我有一页自己确证读懂的材料时",
            "uncertainty": "还不知道换成别的题材是否同样适用",
            "experience_ids": [booklet_exp], "review_refs": []}
        demo.cognition.later_item = {"title": "集合变化用·做法轮", "in_minutes": 6}
        demo.round_until("continue_project", attempts=8)
        method_created = dict(demo.cycle.last_method_created or {})
        check(bool(method_created.get("artifact_id")), "做法草案没有被 owner 保存",
              {"created": method_created, "error": demo.cycle.last_practice_error})

        # 收起前台，给后面的今日自选/义务腾位置（真实 ABANDON，不是伪造终态）。
        demo.cognition.project_bare = booklet_bare
        demo.round_until("abandon", attempts=8)
        check(_foreground(demo.runtime) is None, "小册子没有正常放下、前台未释放",
              {"foreground": _foreground(demo.runtime)})

        # -- B. 同作上下文：真来源、真引用 ---------------------------------------------------
        wc = demo.cycle.work_context_view(booklet)
        draft_rows = [r for r in ((wc.get("drafts") or {}).get("rows") or [])
                      if str(r.get("source_version_id") or "") or r.get("share_id")]
        check(str(wc.get("state")) == "OK", "同作上下文不是 OK", {"state": wc.get("state"),
                                                              "reason": wc.get("reason_code")})
        check(len(((wc.get("versions") or {}).get("entries") or [])) >= 2,
              "同作上下文没有真实的旧版本", wc.get("versions"))
        prov = dict(wc.get("provenance") or {})
        prov_state = str(prov.get("state"))
        check(prov_state == "OK", "同作上下文没有真实来历", prov)
        prov_row = dict(prov.get("provenance") or {})
        prov_source = _bare(prov_row.get("source_artifact_id"))
        reading_bare = _bare(str(demo.data.get("reading_artifact_id") or ""))
        check(bool(prov_source) and prov_source == reading_bare,
              "同作上下文的来历没有指向她真正读到的那份材料",
              {"source": prov_source, "reading": reading_bare, "provenance": prov_row})
        check(bool(str(prov_row.get("source_version_id") or "")),
              "同作上下文来历没有钉住读到的材料版本", prov_row)
        check((dict(prov.get("source_gate") or {})).get("source_blocked") is False,
              "同作上下文的来历来源链已不可读", prov.get("source_gate"))
        result_rows = (wc.get("results") or {}).get("rows") or []
        check(bool(result_rows), "同作上下文没有 Memory 确证结果", wc.get("results"))
        method_rows = (wc.get("methods") or {}).get("rows") or []
        check(bool(method_rows) and any(str(r.get("method_ref") or "").startswith("method:")
                                        for r in method_rows),
              "同作上下文没有证据指回该作品的做法", wc.get("methods"))
        check(bool(draft_rows), "同作上下文没有来源是该作品的本地草稿", wc.get("drafts"))
        say("stage8-work", "同作上下文 %s：旧版 %d、来历 %s、确证结果 %d、做法 %d（%s）、本地草稿 %d"
            % (booklet, len((wc.get("versions") or {}).get("entries") or []),
               (wc.get("provenance") or {}).get("state"), len(result_rows), len(method_rows),
               method_rows[0].get("method_ref"), len(draft_rows)))

        # -- C. 自己排今天 + 外部义务真执行（分段：自选先排进今天但未到点；义务先做；义务
        #      完成扣留后的下一轮，TODAY_SELF_PLANNED popt 才真正进入 AG-0 最终 offered）--
        day = ldp.business_date(demo.clock.iso())

        # 1) 一件真实的、绑定这本册子的 fitem，到期在后（本轮还不到点，不产出自选 popt）。
        demo.cognition.later_item = {"title": STAGE8_PLAN_TITLE, "in_minutes": 10,
                                     "source_refs": ["artifact:%s" % booklet_bare]}
        demo.round_until("propose_future", attempts=8)
        item = next((row.to_dict() for row in demo.cycle.future_items.open_items()
                     if str(row.title or "") == STAGE8_PLAN_TITLE), {})
        item_ref = str(item.get("item_ref") or "")
        check(item_ref.startswith("fitem:"), "没有真实 fitem 可排进今天", {"item_ref": item_ref})

        # 2) 正式 Decision 顶层 plan_today 把它排进今天（+08 业务日；未到点也能记成 PLANNED）。
        demo.cognition.plan_today = {"item_ref": item_ref}
        plan_round = demo.round_until("plan_today", attempts=8)
        plan_writes = [dict(r) for r in (demo.cycle.last_day_plan_writes or [])]
        plan_entry = next((dict(r) for r in demo.cycle.day_plan.for_date(day)
                           if str(r.get("item_ref")) == item_ref), {})
        check(any(str(r.get("status")) == "APPLIED" for r in plan_writes)
              and str(plan_entry.get("status")) == "PLANNED"
              and bool(str(plan_entry.get("decision_ref") or "")),
              "plan_today 没有落成 PLANNED 计划引用",
              {"writes": plan_writes, "entry": plan_entry,
               "outcome": plan_round.get("outcome")})
        # 幂等：同一 Decision 不重复；新 Decision 同 item 同天不多建。
        item_row = demo.cycle.future_items.get(item_ref).to_dict()
        same = demo.cycle.day_plan.plan(item_row, date=day,
                                        decision_ref=str(plan_entry.get("decision_ref")),
                                        now=demo.clock.iso())
        other = demo.cycle.day_plan.plan(item_row, date=day,
                                         decision_ref="decision:stage8:other",
                                         now=demo.clock.iso())
        check(same.get("created") is False and other.get("created") is False,
              "计划不幂等（重复/新 Decision 又建了一条）",
              {"same": same.get("created"), "other": other.get("created")})

        # 3) operator 正规写入口导入义务：未知目标具名拒绝；合法义务绑现场可续写的册子 + 同对象 basis。
        unknown_reason = _stage8_refuse(
            ldp.import_obligation, state_root,
            date=day, title="不该被写进去的义务", target_ref="artifact:does-not-exist",
            due_at=demo.clock.iso(), source_ref="operator:stage8:unknown",
            operation_key="oblig:stage8:unknown", subject_id=subject,
            target_verifier=demo.port.resume_point, now=demo.clock.iso())
        check(unknown_reason != "", "未知义务目标没有被具名拒绝", {"reason": unknown_reason})
        imported = ldp.import_obligation(
            state_root, date=day, title=STAGE8_OBLIG_TITLE, target_ref=booklet,
            due_at=demo.clock.iso(), source_ref="operator:stage8:1",
            operation_key="oblig:stage8:1", subject_id=subject,
            target_verifier=demo.port.resume_point, now=demo.clock.iso())
        obligation = dict(imported.get("entry") or {})
        obligation_ref = str(obligation.get("obligation_ref") or "")
        check(imported.get("created") is True and obligation_ref.startswith("oblig:")
              and str(obligation.get("target_ref")) == booklet,
              "义务没有被 operator 正规入口写成绑定册子的记录",
              {"created": imported.get("created"), "entry": obligation})
        replay = ldp.import_obligation(
            state_root, date=day, title=STAGE8_OBLIG_TITLE, target_ref=booklet,
            due_at=demo.clock.iso(), source_ref="operator:stage8:1",
            operation_key="oblig:stage8:1", subject_id=subject,
            target_verifier=demo.port.resume_point, now=demo.clock.iso())
        check(replay.get("created") is False, "同 operation key 重复导入又写了一条义务")

        # 4) 观察：自选还没到点，本轮只有 TODAY_OBLIGATION 预留 popt；它必须真 offered 且 START 合法。
        demo.cognition.later_item = {"title": "集合变化用·预留观察轮", "in_minutes": 6}
        demo.round_until("reserved_observe", attempts=8)
        reserved = {str(row.get("option_ref")): str(row.get("kind"))
                    for row in (demo.cycle._round_reserved_options or [])}
        offered_refs = {str(c.get("source_ref")) for c in
                        (demo.cognition.offered[-1]["candidates"] if demo.cognition.offered
                         else [])}
        kinds_offered = {kind for ref, kind in reserved.items()
                         if ref.startswith("popt:") and ref in offered_refs}
        check(ldp.KIND_OBLIGATION in reserved.values(),
              "project_options 没有真正产出今日义务预留 popt", {"reserved": reserved})
        check(ldp.KIND_OBLIGATION in kinds_offered,
              "AG-0 没有 offered 今日义务预留 popt",
              {"reserved": reserved, "offered_refs": sorted(offered_refs),
               "kinds": sorted(kinds_offered),
               "total": len(demo.cognition.offered[-1]["candidates"])
               if demo.cognition.offered else 0})
        check(len(demo.cognition.offered[-1]["candidates"]) <= 16,
              "今日义务预留位让一轮候选超过 AG-1 的 16 条上限",
              len(demo.cognition.offered[-1]["candidates"]))
        say("stage8-today", "计划 %s（%s）+ 义务 %s；自选未到点本轮暂不产出自选 popt；"
                            "AG-0 offered 义务 popt，候选 %d 条（≤16）"
            % (plan_entry.get("plan_ref"), plan_entry.get("status"), obligation_ref,
               len(demo.cognition.offered[-1]["candidates"])))

        # 5) 正式选这条义务 → 真续写册子（Supply/AR-0 回执）→ 结果由持久记录投影成 DONE。
        head_before = demo.port.resume_point(booklet)
        demo.cognition.project_bare = booklet_bare
        demo.cognition.later_item = {"title": "集合变化用·义务执行轮", "in_minutes": 6}
        exec_round = demo.round_until("obligation_start", attempts=8)
        head_after = demo.port.resume_point(booklet)
        obligations_after = [dict(r) for r in
                             demo.cycle.obligations_view(now=demo.clock.iso()).get("entries") or []]
        obligation_row = next((r for r in obligations_after
                               if str(r.get("obligation_ref")) == obligation_ref), {})
        used_popt = next((c.get("source_ref") for c in
                          (demo.cognition.offered[-1]["candidates"]
                           if demo.cognition.offered else [])
                          if str(reserved.get(str(c.get("source_ref")))) == ldp.KIND_OBLIGATION),
                         "")
        check(str(exec_round.get("decision")) == "START"
              and head_after.head_version_id != head_before.head_version_id,
              "选义务没有真的续写册子", {"outcome": exec_round.get("outcome"),
                                         "decision": exec_round.get("decision"),
                                         "before": head_before.version_number,
                                         "after": head_after.version_number,
                                         "offer": demo.cognition.offered[-1]})
        check(bool(used_popt), "义务候选不是 TODAY_OBLIGATION 预留位")
        check(str(obligation_row.get("result")) == "DONE", "义务结果没有被持久回执投影成 DONE",
              {"result": obligation_row.get("result"), "evidence": obligation_row.get("evidence")})
        evidence = dict(obligation_row.get("evidence") or {})
        # 独立证明：只从落盘 Action/Decision/owner 回执重建的持久证据也报 DONE（不靠内存 step）。
        obligation_entries = [dict(r) for r in demo.cycle.day_obligations.for_date(day)
                              if str(r.get("obligation_ref")) == obligation_ref]
        persisted = demo.cycle._persisted_obligation_evidence_for(obligation_entries, day=day)
        persisted_row = dict(persisted.get(obligation_ref) or {})
        check(str(persisted_row.get("status")) == "DONE"
              and str(persisted_row.get("evidence_path")) == "PERSISTED_ACTION_RECEIPT"
              and bool(persisted_row.get("version_id")),
              "持久记录没有把义务识别成 DONE/PERSISTED_ACTION_RECEIPT（不能只靠内存 step 猜）",
              {"persisted": persisted_row, "view_evidence": evidence})
        say("stage8-obligation", "义务 %s 经 %s → 册子 v%s→v%s；持久证据 %s/%s（version=%s）"
            % (obligation_ref, used_popt, head_before.version_number, head_after.version_number,
               persisted_row.get("status"), persisted_row.get("evidence_path"),
               persisted_row.get("version_id")))

        # 6) 放下册子，把业务钟推进到自选到期点，再看一次：义务已 DONE 被扣留，TODAY_SELF_PLANNED
        #    预留 popt 才真正进入本轮的最终 AG-0 offered（这是本单要断言的核心事实）。
        demo.cognition.project_bare = booklet_bare
        demo.round_until("abandon", attempts=8)
        due_dt = datetime.fromisoformat(str(item_row.get("due_at") or ""))
        advance_s = max(1, int((due_dt - demo.clock.now).total_seconds()) + 60)
        demo.clock.advance(advance_s, because="今日自选到点")
        demo.cognition.later_item = {"title": "集合变化用·自选观察轮", "in_minutes": 6}
        demo.round_until("reserved_observe", attempts=8)
        withheld = [dict(r) for r in (demo.cycle._round_obligation_withheld or [])]
        check(any(str(r.get("ref")) == obligation_ref
                  and str(r.get("reason_code")) == "OBLIGATION_ALREADY_DONE" for r in withheld),
              "已完成的义务没有被后续候选以 OBLIGATION_ALREADY_DONE 扣留",
              {"withheld": withheld})
        reserved2 = {str(row.get("option_ref")): str(row.get("kind"))
                     for row in (demo.cycle._round_reserved_options or [])}
        offered2 = {str(c.get("source_ref")) for c in
                    (demo.cognition.offered[-1]["candidates"] if demo.cognition.offered else [])}
        kinds2 = {kind for ref, kind in reserved2.items()
                  if ref.startswith("popt:") and ref in offered2}
        # 核心断言（不再只打印 False 放过）：TODAY_SELF_PLANNED 预留 popt 真的进入本轮的最终 offered。
        check(ldp.KIND_SELF_PLANNED in reserved2.values(),
              "义务完成后 project_options 仍没有产出今日自选预留 popt", {"reserved": reserved2})
        check(ldp.KIND_SELF_PLANNED in kinds2,
              "TODAY_SELF_PLANNED 始终没有进入真实最终 AG-0 offered（自选被截没解决）",
              {"reserved": reserved2, "offered_refs": sorted(offered2), "kinds": sorted(kinds2),
               "total": len(demo.cognition.offered[-1]["candidates"])
               if demo.cognition.offered else 0})
        check(ldp.KIND_OBLIGATION not in kinds2,
              "已 DONE 的义务 popt 又被 offered", {"kinds": sorted(kinds2)})
        reoffered = [row for row in (demo.cycle._round_reserved_options or [])
                     if str(row.get("kind")) == ldp.KIND_OBLIGATION]
        head_recheck = demo.port.resume_point(booklet)
        check(not reoffered and head_recheck.head_version_id == head_after.head_version_id,
              "重复提供/重复写了已完成的义务",
              {"reoffered": reoffered, "head": head_recheck.head_version_id,
               "expected": head_after.head_version_id})
        check(len(demo.cognition.offered[-1]["candidates"]) <= 16,
              "自选观察轮候选超过 AG-1 的 16 条上限",
              len(demo.cognition.offered[-1]["candidates"]))
        # 自选仍保持 PLANNED（不要求自选自动 DONE），义务不可由 future 取消/改期——这里只读确认。
        plan_view = demo.cycle.today_plan_view(now=demo.clock.iso())
        check(any(str(r.get("item_ref")) == item_ref and str(r.get("status")) == "PLANNED"
                  for r in plan_view.get("entries") or []),
              "今日自选在安排读面里不再是 PLANNED", plan_view)
        say("stage8-self-planned", "义务完成扣留后，TODAY_SELF_PLANNED popt 真正进入 AG-0 offered（%s）；"
                                   "计划 %s 仍 PLANNED；候选 %d 条（≤16）"
            % (sorted(kinds2), plan_entry.get("plan_ref"),
               len(demo.cognition.offered[-1]["candidates"])))

        # -- D. 线上点心 + CONSUME / TOILET（体感真执行） ------------------------------------
        phone_shop = wps.PhoneShopping(
            str(world_home), demo.env, local_owner_uid=os.getuid(),
            subject=economy.OWNER,
            gateway_socket=Path(world_home) / "run" / "gateway.sock",
            clock=lambda: demo.clock.now)
        bought = phone_shop.purchase(STAGE8_SNACK, "stage8:snack:1")
        bought_data = dict(bought.get("data") or {})
        snack_instance = str(bought_data.get("item_instance_id") or "")
        economy_path = Path(world_home) / "state" / "world_property_economy.json"
        economy_after_buy = json.loads(economy_path.read_text(encoding="utf-8"))
        txn_before = len(economy_after_buy.get("transactions") or [])
        items_before = len(economy_after_buy.get("items") or [])
        check(bool(bought.get("ok")) and str(bought_data.get("ownership")) == "OWNED"
              and bool(snack_instance),
              "PhoneShopping.purchase 没有落成 DEBIT+OWNED", bought)
        replay_buy = phone_shop.purchase(STAGE8_SNACK, "stage8:snack:1")
        replay_data = dict(replay_buy.get("data") or {})
        economy_after_replay = json.loads(economy_path.read_text(encoding="utf-8"))
        check(str(replay_data.get("status") or replay_buy.get("status")) in
              ("already_applied",) or replay_buy.get("status") == "already_applied",
              "同 key 重放购买不是 already_applied", {"replay": replay_buy.get("status")})
        check(len(economy_after_replay.get("transactions") or []) == txn_before
              and len(economy_after_replay.get("items") or []) == items_before,
              "同 key 重放又扣了一次钱或又写了一个实例",
              {"txn": [txn_before, len(economy_after_replay.get("transactions") or [])],
               "items": [items_before, len(economy_after_replay.get("items") or [])]})
        say("stage8-buy", "线上小店买下 %s（%s 实例 %s，DEBIT+OWNED，落 home/bedroom）；同 key 重放 %s 不二扣"
            % (STAGE8_SNACK, bought_data.get("current_balance"), snack_instance,
               replay_buy.get("status")))

        # 真实 World 服务在吃/如厕前先 tick 一次，让 Body 的 live-window 是此刻（relief 才真实落地）。
        demo.gateway.service.tick()
        client = WorldBodyClient(environment=demo.env)
        bands_before = _stage8_bands(client)
        check(_band_rank(bands_before.get("hunger")) >= 2
              and _band_rank(bands_before.get("toilet")) >= 2
              and _band_rank(bands_before.get("thirst")) >= 2,
              "体感初值没有达到可执行的饿/渴/如厕档位", bands_before)

        demo.cognition.consume_object = snack_instance
        consume_round = demo.round_until("consume", attempts=10)
        bands_after_eat = _stage8_bands(client)
        check(str(consume_round.get("world_verb")) == "CONSUME"
              and str(consume_round.get("adoption_status")) == "APPLIED",
              "点心没有走完整 AG-0→AG-1→AG-2→AR-0→WorldResolver 的 CONSUME",
              {k: consume_round.get(k) for k in
               ("outcome", "decision", "world_verb", "adoption_status", "detail", "error")})
        check(_band_rank(bands_after_eat.get("hunger")) < _band_rank(bands_before.get("hunger")),
              "吃了点心 hunger 档位没有按 World 显式声明真实下降",
              {"before": bands_before, "after": bands_after_eat})

        toilet_round = demo.round_until("toilet", attempts=10)
        bands_after_toilet = _stage8_bands(client)
        check(str(toilet_round.get("world_verb")) == "TOILET"
              and str(toilet_round.get("adoption_status")) == "APPLIED",
              "如厕没有走完整正式链的 TOILET",
              {k: toilet_round.get(k) for k in
               ("outcome", "decision", "world_verb", "adoption_status", "detail", "error")})
        check(_band_rank(bands_after_toilet.get("toilet")) < _band_rank(bands_before.get("toilet")),
              "用了声明的洗手间 toilet 档位没有真实下降",
              {"before": bands_before, "after": bands_after_toilet})
        say("stage8-needs", "体感实链：hunger %s→%s、toilet %s→%s（World 显式 lived_need_effects 驱动 Body，非 Life 硬调）"
            % (bands_before.get("hunger"), bands_after_eat.get("hunger"),
               bands_before.get("toilet"), bands_after_toilet.get("toilet")))

        # -- E. 手机今天真实只读：arrangements + timeline（真读入口，读前后底层零写） ----------
        day_iso = ldp.business_date(demo.clock.iso())
        clock_map = {"source": "business_clock", "reason_code": None,
                     "logical_at": demo.clock.iso(), "day": day_iso,
                     "run_id": None, "domain_id": None}
        psvc = phone_service.PhoneService(
            {"root": str(demo.instance), "subject": economy.OWNER,
             "world_home": str(world_home),
             "supply_socket": str(demo.base.services.supply_path),
             "private_items_artifact_id": str(demo.data["private_items_artifact_id"]),
             "artifact_grant_id": str(demo.data["artifact_grant_id"]),
             "workspace_id": str(demo.data["workspace_id"]),
             "reading_artifact_id": str(demo.data.get("reading_artifact_id") or ""),
             "world_body_socket": str(Path(world_home) / "run" / "gateway.sock"),
             "features": {}},
            environment=demo.env, world_clock=lambda: demo.clock.now,
            life_state_root=state_root)
        # 防回归（2026-11-09 23:05 父级红）：时间线按**业务日**过滤 World 执行账与钱包交易
        # （day_timeline._within_day：moment.date()==day）。若业务钟与 owner 真实落账日不在同一
        # +08 日界，来源仍报 ok（但 count=0），四类里会静默少 world_action/wallet_transaction。
        # 这里在读取前先钉住「业务日 == 最新 World 执行/钱包交易的 +08 日」，时钟一漂移就给出
        # 具名原因，而不是只在 kinds 断言里报缺类；只读，不补事件、不改记录时间。
        def _stage8_record_days(values):
            days = set()
            for value in values:
                moment = phone_service.today_moment(value)
                if moment is not None:
                    days.add(moment.date().isoformat())
            return days

        wallet_probe = psvc.wallet()
        wallet_rows_probe = list(((wallet_probe or {}).get("data") or {}).get("transactions") or [])
        wallet_days = _stage8_record_days(
            [tx.get("occurred_at") for tx in wallet_rows_probe if isinstance(tx, dict)])
        world_days = _stage8_record_days(
            [row.get("timestamp") for row in day_timeline._ledger_records(Path(world_home))
             if isinstance(row, dict)])
        check(day_iso in wallet_days and day_iso in world_days,
              "业务日与 World/钱包真实落账日不在同一 +08 日界（时间线会按日把它们过滤掉）",
              {"day": day_iso, "wallet_days": sorted(wallet_days),
               "world_days": sorted(world_days)})
        read_roots = (state_root, Path(world_home) / "state", Path(world_home) / "data",
                      Path(demo.base.services.root))
        # 先把本实例运行时的写缓冲有序落盘（AR-0/Decision/settlement 的快照），让随后的真实只读
        # 重开读到的是与 journal 一致的盘面——不是伪造、不是补写，只是把在途写先 settle。
        try:
            demo.runtime.flush_all()
        except Exception as exc:
            say("stage8", "flush_all 跳过：%s" % type(exc).__name__)
        # 纯读阶段与 owner 自己的后台 tick 分离：serve 循环每服务一条连接就会跑一次
        # tick()，而 lived-needs 的 live-window 结算会写 body/runtime.json +
        # body/transitions.jsonl。这里只在读窗口内暂停那个后台 tick（真 socket、真 owner
        # 不变），读完立刻恢复，不靠 sleep 赌时序，也不把读后的写说成「正常」。
        with _owner_tick_frozen(demo, label="stage8-phone"):
            before_bytes = _stage8_state_files(*read_roots)
            arrangements = own_day_read.arrangements_envelope(
                state_root=state_root, subject_id=subject, clock=clock_map)
            timeline = day_timeline.timeline_envelope(
                service=psvc, state_root=state_root, subject_id=subject, clock=clock_map,
                subject_view=False, world_home=Path(world_home), world_clock_injected=False)
            after_bytes = _stage8_state_files(*read_roots)
        changed = sorted(k for k in set(before_bytes) | set(after_bytes)
                         if before_bytes.get(k) != after_bytes.get(k))
        check(not changed, "手机只读读入了写：底层状态文件字节发生变化", {"changed": changed[:8]})

        arr_data = dict(arrangements.get("data") or {})
        plan_rows = list((arr_data.get("plan") or {}).get("entries") or [])
        obl_rows = list((arr_data.get("obligations") or {}).get("entries") or [])
        self_planned_visible = any(str(r.get("class")) == "SELF_PLANNED"
                                   for r in plan_rows)
        obligation_visible = any(
            str(r.get("obligation_ref")) == obligation_ref
            and str((r.get("execution") or {}).get("result")
                    or r.get("result")) == "DONE"
            for r in obl_rows)
        check(bool(arrangements.get("ok")) and self_planned_visible and obligation_visible,
              "手机 arrangements 没有如实投影 SELF_PLANNED 与 DONE 义务",
              {"ok": arrangements.get("ok"),
               "execution_source": (arr_data.get("sources") or {}).get("execution"),
               "plan": [{k: r.get(k) for k in ("class", "definition_status")} for r in plan_rows],
               "obligations": [{k: r.get(k) for k in ("obligation_ref", "result", "execution")}
                               for r in obl_rows]})

        tdata = dict(timeline.get("data") or {})
        entries = list(tdata.get("entries") or [])
        kinds = {str(r.get("kind")) for r in entries}
        need = {"activity", "plan", "world_action", "wallet_transaction"}
        check(bool(timeline.get("ok")) and need.issubset(kinds),
              "手机 timeline 没有把 Activity/计划/World 动作/钱包交易四类真实记录合并",
              {"ok": timeline.get("ok"), "kinds": sorted(kinds),
               "sources": {k: (v or {}).get("state")
                           for k, v in (tdata.get("sources") or {}).items()}})
        check(tdata.get("skeleton_only") is True and tdata.get("read_only") is True,
              "operator 视图不是 skeleton_only 只读", {"data_keys": sorted(tdata)})
        # 不泄漏私人正文/钱包私有备注：operator 骨架里既没有正文/说明/提示词键，也没有长正文串。
        forbidden = {"text", "body", "content", "caption", "scene", "generation_prompt",
                     "memo", "private_note", "diary_text", "summary"}
        leaked = [k for row in entries for k in forbidden if k in row]
        long_text = [k for row in entries for k, v in row.items()
                     if isinstance(v, str) and len(v) > 200]
        check(not leaked and not long_text, "手机时间线泄漏了私有正文/长文本字段",
              {"leaked": leaked[:5], "long_text": long_text[:5]})
        bases = {str(row.get("time_basis") or "") for row in entries}
        check(bases and "" not in bases,
              "时间线条目没有各自标注 time_basis（不同时域必须分开保留）", {"bases": sorted(bases)})
        check(str((tdata.get("ordering") or {}).get("sorted_by") or "") != "",
              "时间线没有如实标注时域/排序", tdata.get("ordering"))
        say("stage8-phone", "手机只读：arrangements 投影 SELF_PLANNED + DONE 义务；timeline 合入 %s；"
                            "operator skeleton_only；读前后 %d 个状态文件字节零变"
            % (sorted(kinds), len(before_bytes)))

        say("stage8-done", "第八阶段跑完：今日自选(plan)/义务(operator import→真实续航→DONE→ALREADY_DONE 扣留) "
                           "→ 同作上下文(多版本/来历/确证结果/做法/草稿) → 线上点心 DEBIT+OWNED→CONSUME→hunger 降 "
                           "→ TOILET→toilet 降 → 手机 arrangements/timeline 真实只读零写")
        # ``keep_demo`` 只服务单测：让第九阶段在同一隔离实例上接着跑；生产/演示默认仍返回码
        # 并在本段收尾，旧调用一字不变。
        if own_booklet and keep_demo:
            return demo
        return 0
    finally:
        # 主链模式由父级 main() 自己关闭 demo；本段只在自建实例（单测 helper）时收尾。
        if own_booklet and not keep_demo:
            demo.close()


# ---------------------------------------------------------------------------------------------
# 0.7 第九阶段：主链同 root 的研究作品闭环 / 同实例日记相册与六域时间线 / S03 与共同实践真来源
# ---------------------------------------------------------------------------------------------
STAGE9_QUESTION = "为什么早上的面包车总停在巷口不下货，是路太窄还是店主还没开门？"
STAGE9_NO_HIT_TOKEN = "stage9-no-such-material-token-9f3a"
STAGE9_NO_HIT_QUESTION = "如果资料里根本没提这件事，我还能研究什么？"
STAGE9_MATERIAL_TITLE = "巷口早晨观察（本地原创笔记）"
STAGE9_MATERIAL_TEXT = (
    "这是我在本机写下的观察笔记，不是从网上抓来的材料。\n"
    "十一月上旬的几个早晨，巷口的面包车都在七点四十左右停在修车铺前，\n"
    "司机下车去对面的早餐摊买了两次东西，车厢门一直没有打开。\n"
    "隔壁杂货店的老板说，巷口七点半到八点半不让卸货，怕堵住送孩子上学的路。\n"
    "这段说法只是杂货店老板当时的转述，我没有在告示牌上亲眼确认过。"
)
STAGE9_DIARY_TEXT = (
    "STAGE9_PRIVATE_DIARY_SENTINEL 今天把巷口那辆面包车的事写下来了。"
    "我问自己：我到底看见了什么，又只是听别人说？"
)
STAGE9_ALBUM_CAPTION = "STAGE9_PRIVATE_ALBUM_SENTINEL 窗台上的一小片光"
STAGE9_PRIVATE_TOKENS = ("STAGE9_PRIVATE_DIARY_SENTINEL",
                         "STAGE9_PRIVATE_ALBUM_SENTINEL")


def _stage9_png_bytes() -> bytes:
    """A deterministic, locally composed 1x1 PNG.  It is a technical fixture, not a photo."""

    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = b"\x00" + b"\x40\x60\x80"  # one RGB pixel, filter byte 0
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def _stage9_research_project(service, artifact_id: str) -> dict:
    answer = service.read_research(artifact_id)
    data = answer.get("data") if isinstance(answer, dict) else {}
    project = data.get("project") if isinstance(data, dict) else {}
    return dict(project or {})


def _stage9_practice_head(service, session_id: str) -> str:
    answer = service.read_practice(session_id)
    data = answer.get("data") if isinstance(answer, dict) else {}
    project = data.get("project") if isinstance(data, dict) else {}
    return str((project or {}).get("head_version_id") or "")


def _stage9_walk_strings(value, path=""):
    """Yield (path, string) for every string in a nested JSON-ish value."""

    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _stage9_walk_strings(item, "%s.%s" % (path, key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _stage9_walk_strings(item, "%s[%d]" % (path, index))


def _request_days(phone_service_mod, values):
    days = set()
    for value in values:
        moment = phone_service_mod.today_moment(value)
        if moment is not None:
            days.add(moment.date().isoformat())
    return days


def _run_stage9_chain(demo=None, booklet=None, booklet_bare=""):
    """第九阶段：同一默认实例上的研究闭环、私人日记相册六域时间线、S03 与共同实践真来源。

    ``demo`` 传主链对象时接回**同一个 root**；传 ``None``（单测 helper）时先跑第八阶段的
    同一隔离实例链（``keep_demo=True``），再在同一对象上继续，不另造第三实例、不复制旧根。
    """

    import base64
    import life_day_plan as ldp
    import life_project_options as lpo
    import private_daily_items as pdi
    import research_work_context as rwc
    import world_property_economy as economy
    from artifact_continuation_source import ArtifactContinuationSource
    from candidate_sources_ag0 import (CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
                                       CANDIDATE_STATUS_OPEN,
                                       CANDIDATE_STATUS_WITHDRAWN,
                                       SOURCE_PERSONAL_OPPORTUNITY, CandidateRecord)
    from components.phone import day_timeline
    from components.phone import service as phone_service

    own_instance = demo is None
    if own_instance:
        demo = _run_stage8_chain(keep_demo=True)
    try:
        state_root = odl.life_state_root(demo.instance)
        subject = str(demo.data["subject_id"])
        world_home = Path(demo.world_home)
        psvc = phone_service.PhoneService(
            {"root": str(demo.instance), "subject": subject,
             "world_home": str(world_home),
             "supply_socket": str(demo.base.services.supply_path),
             "private_items_artifact_id": str(demo.data["private_items_artifact_id"]),
             "artifact_grant_id": str(demo.data["artifact_grant_id"]),
             "workspace_id": str(demo.data["workspace_id"]),
             "reading_artifact_id": str(demo.data.get("reading_artifact_id") or ""),
             "world_body_socket": str(Path(world_home) / "run" / "gateway.sock"),
             "features": {}},
            environment=demo.env, world_clock=lambda: demo.clock.now,
            world_subject=economy.OWNER, life_state_root=state_root)
        items = odl.private_items_adapter(demo.instance)

        # =====================================================================================
        # A. 主链同一 root：恢复默认装配的研究 seam，新疑问 + 真实资料 → 研究 START/CONTINUE/ABANDON
        # =====================================================================================
        assembled_research = dict((demo.fixture.assembled or {}).get("research") or {})
        check(str(assembled_research.get("status")) == "READY",
              "默认装配没有接上真实研究工作台（不得另造服务）", assembled_research)
        seam = demo.restore_research()
        check(seam.availability().get("status") == "READY",
              "恢复的 seam 不是默认装配那条真实 ResearchWorkspaceService",
              seam.availability())
        research_service = seam.service
        imported = research_service.import_material(
            title=STAGE9_MATERIAL_TITLE, text=STAGE9_MATERIAL_TEXT,
            source_kind="user_supplied", origin="operator:stage9:local_original",
            operation_key="stage9:material:1")
        check(imported.get("ok") and str(imported.get("status")) in (
                  "applied", "already_applied"),
              "工作台 import_material 没有落成真实资料", imported)
        material = dict(((imported.get("data") or {}).get("material") or {}))
        material_id = str(material.get("artifact_id") or "")
        material_revision = str(material.get("source_revision") or "")
        check(bool(material_id) and bool(material_revision),
              "导入资料没有 artifact_id + 版本", material)
        unclear = items.mark_unclear(
            source_id=material_id, source_revision=material_revision,
            note=STAGE9_QUESTION, operation_key="stage9:unclear:1",
            now=demo.clock.iso())
        check(unclear.applied and bool(unclear.item_id),
              "新疑问没有经私人 items owner 写成 unclear", unclear.to_dict())

        # 本轮的疑问种子只取刚写在这份新资料上的那一条：项目候选本身仍由既有 owner 路径
        # 从真实 marker + 真实 workspace 命中构造；这里只把**本实例本轮**的种子范围收到
        # 这份资料（同一私有 items owner 读回），避免同一条上限里被更早的阅读疑问占位。
        _questions_provider = demo.cycle.project_questions_provider

        def _only_this_material(_provider=_questions_provider, _source=str(material_id)):
            rows = [dict(row) for row in (_provider() or []) if isinstance(row, dict)]
            return [row for row in rows if str(row.get("source_id")) == _source]

        demo.cycle.project_questions_provider = _only_this_material
        try:
            demo.cognition.research_question = STAGE9_QUESTION
            started = demo.round_until("research_start", attempts=10)
        finally:
            demo.cycle.project_questions_provider = _questions_provider
        created = dict(started.get("project_created") or {})
        research_id = str(created.get("artifact_id") or "")
        check(started.get("outcome") == "ACTED" and bool(created.get("research"))
              and research_id.startswith("artifact:"),
              "RESEARCH_FROM_QUESTION 候选没有被正式 Decision START 成研究作品",
              {"outcome": started.get("outcome"),
               "decision": started.get("decision"),
               "adoption_status": started.get("adoption_status"),
               "created": {key: created.get(key) for key in
                           ("option_ref", "research", "artifact_id", "status")}})
        option_row = next((dict(row) for row in (demo.cycle._round_project_options or [])
                           if str(row.get("option_ref")) == str(created.get("option_ref"))),
                          {})
        option_material = next((dict(row) for row in (option_row.get("materials") or [])
                                if str(row.get("artifact_id")) == material_id), {})
        check(str(option_row.get("kind")) == lpo.KIND_RESEARCH
              and str(option_material.get("source_revision")) == material_revision,
              "研究候选不是由真实资料命中（artifact_id + source_revision）构造",
              {"kind": option_row.get("kind"),
               "materials": [{key: row.get(key) for key in
                              ("artifact_id", "source_revision")}
                             for row in (option_row.get("materials") or [])]})

        first_project = _stage9_research_project(research_service, research_id)
        first_content = str(first_project.get("content") or "")
        parsed_first = rwc.research_module().parse_research_content(first_content)
        check(first_content.startswith("<!-- nyairo-research:v1")
              and parsed_first.get("marker_present") is True,
              "owner 研究作品没有 B 唯一定义的 nyairo-research:v1 marker",
              {"head": first_content[:24]})
        refs_first = [dict(row) for row in (parsed_first.get("source_refs") or [])]
        cited = next((row for row in refs_first
                      if _bare(row.get("artifact_id")) == _bare(material_id)), {})
        check(str(parsed_first.get("question") or "") == STAGE9_QUESTION
              and str(cited.get("source_revision")) == material_revision,
              "研究 marker 里的疑问/来源版本不是当时真实可读的那一份",
              {"question": parsed_first.get("question"),
               "cited": cited,
               "refs": [{key: row.get(key) for key in ("artifact_id", "source_revision")}
                        for row in refs_first]})
        version_after_start = int(first_project.get("version_number") or 0)
        check(version_after_start >= 2,
              "START 后 AR-0 没有在 owner 里写出新版本", first_project.get("version_number"))

        writer_ctx = dict(demo.writer.last_context or {})
        prompt_ctx = dict(demo.content_client.last_prompt_context or {})
        own_research = dict((writer_ctx.get("research_context") or {}).get("own_research")
                            or {})
        own_sources = [dict(row) for row in (own_research.get("sources") or [])]
        own_cited = next((row for row in own_sources
                          if _bare(row.get("artifact_id")) == _bare(material_id)), {})
        check(_bare(own_research.get("artifact_id")) == _bare(research_id)
              and str(own_cited.get("source_revision")) == material_revision
              and len(str(own_cited.get("text") or "")) > 0,
              "cycle._next_content 交给 writer 的 research_context 没有真实来源正文/版本",
              {"artifact": own_research.get("artifact_id"),
               "sources": [{key: row.get(key) for key in
                            ("artifact_id", "source_revision", "availability")}
                           for row in own_sources],
               "text_chars": [len(str(row.get("text") or "")) for row in own_sources]})
        check("research_context" in (prompt_ctx.get("keys") or [])
              and _bare(prompt_ctx.get("research_own_artifact")) == _bare(research_id)
              and int(prompt_ctx.get("research_source_count") or 0) >= 1,
              "脚本 writer 实际收到的提示里没有 research_context",
              prompt_ctx)

        demo.cognition.project_bare = _bare(research_id)
        continued = demo.round_until("continue_research", attempts=10)
        second_project = _stage9_research_project(research_service, research_id)
        version_after_continue = int(second_project.get("version_number") or 0)
        parsed_second = rwc.research_module().parse_research_content(
            str(second_project.get("content") or ""))
        refs_second = [dict(row) for row in (parsed_second.get("source_refs") or [])]
        check(continued.get("decision") == "CONTINUE"
              and version_after_continue > version_after_start,
              "同一 artifact 的正式 CONTINUE 没有写出新版本",
              {"decision": continued.get("decision"),
               "outcome": continued.get("outcome"),
               "versions": [version_after_start, version_after_continue]})
        check(parsed_second.get("marker_present") is True
              and refs_second == refs_first,
              "CONTINUE 的 writer 抹掉了 marker 或旧 source_refs",
              {"marker": parsed_second.get("marker_present"),
               "refs_same": refs_second == refs_first})
        continue_ctx = dict(demo.writer.last_context or {})
        earlier_versions = list(continue_ctx.get("earlier_versions") or [])
        continue_prompt = dict(demo.content_client.last_prompt_context or {})
        check(bool(earlier_versions)
              and len(str(earlier_versions[0].get("excerpt") or "")) > 0
              and int(continue_prompt.get("earlier_version_count") or 0) >= 1,
              "CONTINUE 的写作上下文没有带上一版引用",
              {"earlier": earlier_versions,
               "prompt": continue_prompt})
        check(not (continue_ctx.get("research_context") or {}) is None
              and "research_context" in (continue_prompt.get("keys") or []),
              "CONTINUE 的写作上下文没有研究文档情境", continue_prompt)

        abandoned = demo.round_until("abandon_research", attempts=10)
        check(abandoned.get("decision") == "ABANDON" and _foreground(demo.runtime) is None,
              "研究前台没有由正式 ABANDON 释放",
              {"decision": abandoned.get("decision"), "outcome": abandoned.get("outcome"),
               "foreground": _foreground(demo.runtime)})
        final_project = _stage9_research_project(research_service, research_id)
        check(str(final_project.get("lifecycle") or "") != "ARCHIVED",
              "ABANDON 被错误地变成归档", final_project.get("lifecycle"))

        # 零命中对照：工作台真的可用、真的扫过资料，只是这个 query 没有命中。
        zero_hits = seam.materials(query=STAGE9_NO_HIT_TOKEN)
        check(zero_hits == [] and seam.availability().get("status") == "READY"
              and not str(seam.last_error or ""),
              "零命中对照不是「工作台可用但没命中」", seam.availability())
        no_material_option = next((dict(row) for row in lpo.build_options(
            questions=[{"marker_id": "stage9-no-material", "note": STAGE9_NO_HIT_QUESTION,
                        "source_refs": []}],
            research_materials=zero_hits, research_available=True)), {})
        check(str(no_material_option.get("uncertainty")) == lpo.NO_MATERIAL_UNCERTAINTY,
              "零命中没有产生 NO_MATERIAL_UNCERTAINTY",
              no_material_option.get("uncertainty"))
        not_wired_status = rwc.ResearchSeam(None).availability().get("status")
        check(not_wired_status == "NOT_WIRED"
              and not_wired_status != seam.availability().get("status"),
              "NOT_WIRED 与「可用但零命中」没有分开", not_wired_status)

        # 只在本单新导入的资料上做换版负控：旧版本必须读不到，研究来源变 STALE。
        material_project = dict((research_service.read_material(material_id).get("data")
                                 or {}).get("material") or {})
        old_revision = str(material_project.get("source_revision") or material_revision)
        updated_content = rwc.research_module().build_material_content(
            text=STAGE9_MATERIAL_TEXT + "\n\n补记：后来我又在告示牌上确认了一次。",
            source_kind="user_supplied", origin="operator:stage9:local_original",
            title=STAGE9_MATERIAL_TITLE)
        revised = research_service.projects.commit_project(
            artifact_id=material_id, content=updated_content,
            expected_head=old_revision, operation_key="stage9:material:rev2")
        check(revised.get("ok") and str(revised.get("status")) in (
                  "applied", "already_applied"),
              "新导入资料换版失败", revised)
        stale_read = seam.read_material(material_id, expected_revision=old_revision)
        stale_project = _stage9_research_project(research_service, research_id)
        stale_checks = [dict(row) for row in (stale_project.get("source_checks") or [])]
        stale_cited = next((row for row in stale_checks
                            if _bare(row.get("artifact_id")) == _bare(material_id)), {})
        check(stale_read is None
              and str(stale_cited.get("status") or "").upper() == "STALE",
              "资料换版后旧版本仍被当成可读，或研究来源没标 STALE",
              {"old_read": stale_read is None,
               "check": {key: stale_cited.get(key) for key in
                         ("artifact_id", "status", "check")}})
        stale_writing = seam.writing_context(research_id) or {}
        stale_source = next((dict(row) for row in
                             ((stale_writing.get("own_research") or {}).get("sources") or [])
                             if _bare(row.get("artifact_id")) == _bare(material_id)), {})
        check(str(stale_source.get("availability") or "").upper() != "CURRENT"
              and not str(stale_source.get("text") or ""),
              "旧版本来源仍在写作上下文里给正文", stale_source)
        say("stage9-research", "研究闭环：资料 %s@%s → unclear → RESEARCH popt → START 建 marker（v%s）"
                               " → CONTINUE 到 v%s（marker/refs 保留、earlier_versions 实收）"
                               " → ABANDON 释放前台；零命中=NO_MATERIAL，NOT_WIRED 分开；"
                               "新资料换版旧版读不到且来源 STALE"
            % (_bare(material_id)[:8], material_revision[:8], version_after_start,
               version_after_continue))

        # =====================================================================================
        # C. S03：真实观察/报告/推断 + DISPUTED/STALE 进正式 Decision；claims 不越权写 World/Memory
        # =====================================================================================
        claims = demo.cycle.claims_service
        assembled_claims = dict((demo.fixture.assembled or {}).get("claims") or {})
        check(claims is not None and str(assembled_claims.get("status")) == "READY",
              "默认装配没有接上真实 S03 claims service", assembled_claims)
        subject_home = Path(demo.instance) / "memory"
        guard_roots = (world_home / "data", world_home / "state", subject_home)
        # claims 的越权判据只看 claims 自己的写；读 World 快照喂 OBSERVED 会经过
        # owner 的 serve 循环，而它每服务一条连接就顺手跑一次 tick（lived-needs 的
        # live-window 结算会写 body/runtime.json + body/transitions.jsonl）。这条守卫
        # 窗口内冻结那个后台 tick，读到的仍是真 owner 的 snapshot，claims 自己的
        # 合法写入不受影响（S03 文件不在 guard_roots 里）。
        with _owner_tick_frozen(demo, label="stage9-claims"):
            guard_before = _stage8_state_files(*guard_roots)
            now_claims = demo.clock.iso()
            world_snapshot = psvc.world()
            observed = claims.observe_world(world_snapshot, now=now_claims,
                                            operation_key="stage9:claims:observe:1")
            observed_claims = [dict(row) for row in (observed.get("claims") or [])]
            subject_claim = next((row for row in observed_claims
                                  if str(row.get("predicate")) == "subject_last_observed_place"),
                                 {})
            check(observed.get("source_kind") == "OBSERVED"
                  and bool(observed.get("world_revision"))
                  and bool(observed.get("receipt_ref"))
                  and bool(subject_claim.get("claim_id")),
                  "OBSERVED 不是来自真实 World snapshot/版本/回执",
                  {key: observed.get(key) for key in
                   ("source_kind", "world_id", "world_revision", "receipt_ref", "place_id")})
            closing_context = {"local_date": ldp.business_date(now_claims),
                               "timezone": "Asia/Tokyo"}
            report_a = claims.report(
                predicate_id="object_last_observed_place", scope_id="object:stage9-crate",
                value={"place_id": "bookstore", "place_name": "书店"},
                source_ref="operator_report:stage9:note-a", now=now_claims,
                operation_key="stage9:claims:report:a")
            report_b = claims.report(
                predicate_id="object_last_observed_place", scope_id="object:stage9-crate",
                value={"place_id": "cafe", "place_name": "咖啡馆"},
                source_ref="report:stage9:note-b", now=now_claims,
                operation_key="stage9:claims:report:b")
            closing_a = claims.report(
                predicate_id="place_closing_time", scope_id="place:bookstore",
                value="19:00", context=closing_context,
                source_ref="operator_report:stage9:closing-a", now=now_claims,
                revalidate_after_seconds=60, operation_key="stage9:claims:closing:a")
            closing_b = claims.report(
                predicate_id="place_closing_time", scope_id="place:bookstore",
                value="20:30", context=closing_context,
                source_ref="report:stage9:closing-b", now=now_claims,
                revalidate_after_seconds=60, operation_key="stage9:claims:closing:b")
            # "此刻大概还开着" 是时效性推断：给它和两条到期报告同一复核窗口。到期后它自己
            # 变成 STALE（不是被手工标旧），在下一步有界切片里以 STALE 身份出现。
            _infer_expiry = (datetime.fromisoformat(str(now_claims))
                             + timedelta(seconds=60)).isoformat()
            inferred = claims.infer(
                predicate_id="place_likely_open", scope_id="place:bookstore",
                value=True, basis_claim_ids=[str(subject_claim.get("claim_id"))],
                context=closing_context, now=now_claims, valid_to=_infer_expiry,
                operation_key="stage9:claims:infer:1")
            for answer in (report_a, report_b, closing_a, closing_b, inferred):
                check(bool(answer.get("ok")), "S03 合法写入被拒", answer)
            disputes = [dict(row) for row in
                        (claims.current_view(now=now_claims).get("conflicts") or [])]
            check(any(str(row.get("predicate")) == "object_last_observed_place"
                      for row in disputes),
                  "同一 slot 的两个合法报告没有并置成 DISPUTED", disputes)
            guard_after = _stage8_state_files(*guard_roots)
        guard_changed = sorted(key for key in set(guard_before) | set(guard_after)
                               if guard_before.get(key) != guard_after.get(key))
        check(not guard_changed, "claims 操作越权写了 World/Memory 文件",
              {"changed": guard_changed[:8]})

        # 正式 Decision 前让处境真的变一下（新疑问），避免被既有 quiet 门挡成零请求。
        reading_point = demo.port.resume_point(str(demo.data["reading_artifact_id"]))
        items.mark_unclear(
            source_id=str(demo.data["reading_artifact_id"]),
            source_revision=str(reading_point.head_version_id or ""),
            note="S03 观察轮要核对的疑问甲", operation_key="stage9:claims:unclear:a",
            now=demo.clock.iso())
        pre_round = demo.round_until("stage9_claims_pre", attempts=6)
        check(str(pre_round.get("outcome")) != "QUIET",
              "含 DISPUTED 的决策轮被 quiet 门挡成零请求", pre_round.get("outcome"))
        demo.clock.advance(61, because="S03 到期复核（业务钟推进，不改系统墙钟）")
        maintained = claims.maintenance(now=demo.clock.iso())
        check(int(maintained.get("count") or 0) >= 1,
              "maintenance 没有把到期报告标 STALE", maintained)
        items.mark_unclear(
            source_id=str(demo.data["reading_artifact_id"]),
            source_revision=str(reading_point.head_version_id or ""),
            note="S03 观察轮要核对的疑问乙", operation_key="stage9:claims:unclear:b",
            now=demo.clock.iso())
        post_round = demo.round_until("stage9_claims_post", attempts=6)
        check(str(post_round.get("outcome")) != "QUIET",
              "含 STALE 的决策轮被 quiet 门挡成零请求", post_round.get("outcome"))
        pre_seen = [row for row in demo.cognition.seen_claims_at
                    if row.get("intent") == "stage9_claims_pre"]
        post_seen = [row for row in demo.cognition.seen_claims_at
                     if row.get("intent") == "stage9_claims_post"]
        pre_statuses = {status for row in pre_seen for status in row.get("statuses") or []}
        post_statuses = {status for row in post_seen for status in row.get("statuses") or []}
        check("DISPUTED" in pre_statuses,
              "正式 Decision 实收的 claims 块没有 DISPUTED",
              {"pre": pre_seen})
        check("STALE" in post_statuses,
              "正式 Decision 实收的 claims 块没有 STALE",
              {"post": post_seen})
        check({"OBSERVED", "REPORTED", "INFERRED"}.issubset(
                  set(demo.cognition.seen_claim_source_kinds)),
              "正式 Decision 实收的 claims 没有覆盖 OBSERVED/REPORTED/INFERRED",
              {"sources": sorted(demo.cognition.seen_claim_source_kinds),
               "pre": pre_seen, "post": post_seen})
        check(demo.cognition.last_claims_block is not None
              and demo.cognition.last_claims_block.get("not_permission") is True,
              "Decision 实收的 claims 块不是非授权数据",
              demo.cognition.last_claims_block)
        say("stage9-claims", "S03：OBSERVED(world rev=%s) + 两个 REPORTED 冲突→DISPUTED + "
                             "INFERRED(basis) + maintenance→STALE；两次正式 Decision 实收 "
                             "sources=%s statuses=%s；claims 窗口内 3 类底层文件字节零变"
            % (str(observed.get("world_revision"))[:12],
               sorted(demo.cognition.seen_claim_source_kinds),
               sorted(set(demo.cognition.seen_claim_statuses))))

        # =====================================================================================
        # B. 同一实例：真实 PhoneService 日记 + Supply 相册导入；作品/续写候选隔离；六域时间线只读
        # =====================================================================================
        diary = psvc.diary_write(body=STAGE9_DIARY_TEXT, operation_key="stage9:diary:1")
        diary_id = str((diary.get("data") or {}).get("id") or "")
        check(diary.get("ok") and str(diary.get("status")) in ("applied", "OK")
              and bool(diary_id),
              "真实 PhoneService 没有写成私密日记", diary)
        diary_count = int(psvc.diary_list().get("count") or 0)
        diary_replay = psvc.diary_write(body=STAGE9_DIARY_TEXT,
                                        operation_key="stage9:diary:1")
        check(str(diary_replay.get("status")) == "already_applied"
              and int(psvc.diary_list().get("count") or 0) == diary_count,
              "同 operation key 的日记重放又写了一条", diary_replay)

        png = _stage9_png_bytes()
        png_b64 = base64.b64encode(png).decode("ascii")
        album = psvc.album_import(caption=STAGE9_ALBUM_CAPTION, content_type="image/png",
                                  data=png_b64, operation_key="stage9:album:1")
        album_id = str((album.get("data") or {}).get("artifact_id") or "")
        check(album.get("ok") and album_id.startswith("artifact:"),
              "真实相册导入没有落成 owner 工件", album)
        images = [dict(row) for row in (psvc.album_list().get("images") or [])]
        check(any(str(row.get("artifact_id")) == album_id for row in images),
              "相册列表里读不到刚导入的图", {"count": len(images)})
        album_count = len(images)
        album_replay = psvc.album_import(caption=STAGE9_ALBUM_CAPTION,
                                         content_type="image/png", data=png_b64,
                                         operation_key="stage9:album:1")
        check(str(album_replay.get("status")) in ("already_applied", "NO_CHANGE", "no_change")
              and len(psvc.album_list().get("images") or []) == album_count,
              "同 operation key 的相册重放又建了一件", album_replay)

        # 分类隔离：相册清单不进普通项目列表；也不进续写候选（真实 materialize/refresh）。
        import private_project_service as pps
        project_service = pps.PrivateProjectService(
            demo.port, str(demo.data["workspace_id"]), str(demo.data["artifact_grant_id"]))
        listed = project_service.list_projects()
        listed_ids = {str(row.get("artifact_id")) for row in (listed.get("projects") or [])}
        check(listed.get("ok") and album_id not in listed_ids,
              "相册清单出现在普通 PrivateProjectService 作品列表",
              {"count": len(listed_ids), "album": _bare(album_id)[:8]})
        check(_bare(research_id) in {_bare(item) for item in listed_ids}
              and (not booklet or _bare(booklet) in {_bare(item) for item in listed_ids}),
              "真实普通作品/研究被连带排除出项目列表",
              {"research": _bare(research_id)[:8],
               "booklet": _bare(booklet or "")[:8]})
        source = ArtifactContinuationSource(
            demo.port, subject_id=subject,
            exclude_artifact_ids=[str(demo.data["private_items_artifact_id"])],
            reading_artifact_id=str(demo.data.get("reading_artifact_id") or "") or None)
        candidates, _snap, _meta = source.materialize(observed_at=demo.clock.iso())
        candidate_refs = {str(ref) for row in candidates for ref in (row.source_refs or [])}
        check(not any(_bare(ref) == _bare(album_id) for ref in candidate_refs),
              "相册清单进入了 ArtifactContinuationSource 的续写候选",
              {"album": _bare(album_id)[:8]})
        check(any(_bare(ref) == _bare(research_id) for ref in candidate_refs)
              or any(_bare(row.source_ref) == _bare(research_id) for row in candidates),
              "研究作品被误排除出续写候选", {"candidates": len(candidates)})
        check(str(source.last_album_skip_reasons.get(album_id) or "")
              == "ALBUM_MANIFEST_NOT_A_WORK",
              "续写来源没有具名报告相册清单跳过", source.last_album_skip_reasons)
        album_point = demo.port.resume_point(album_id)
        fabricated_album_candidate = CandidateRecord(
            candidate_id="cand:stage9albumrefresh",
            candidate_kind=CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY,
            source_kind=SOURCE_PERSONAL_OPPORTUNITY,
            source_ref="artifact:%s" % album_id,
            availability_status=CANDIDATE_STATUS_OPEN,
            created_at=demo.clock.iso(), observed_at=demo.clock.iso(),
            recorded_at=demo.clock.iso(), idempotency_key="idem_cand:stage9album",
            source_revision=album_point.version_number,
            target_refs=["artifact:%s" % album_id],
            causal_parent_refs=["opp:stage9album"],
            provenance_refs=["opp:stage9album", "artifact:%s" % album_id])
        refreshed_album = source.refresh(fabricated_album_candidate,
                                         observed_at=demo.clock.iso())
        check(str(refreshed_album.availability_status) == CANDIDATE_STATUS_WITHDRAWN
              and str(refreshed_album.invalidation_reason)
              == "ALBUM_MANIFEST_NOT_A_WORK",
              "真实 refresh 没有撤回指向相册清单的续写候选",
              {"status": refreshed_album.availability_status,
               "reason": refreshed_album.invalidation_reason})

        # 六域时间线：同一个真实实例，Activity/计划/World/钱包沿用，日记与相册是本段写的。
        day_iso = ldp.business_date(demo.clock.iso())
        clock_map = {"source": "business_clock", "reason_code": None,
                     "logical_at": demo.clock.iso(), "day": day_iso,
                     "run_id": None, "domain_id": None}
        wallet_rows = list(((psvc.wallet() or {}).get("data") or {}).get("transactions") or [])
        wallet_days = _request_days(phone_service,
                                    [tx.get("occurred_at") for tx in wallet_rows
                                     if isinstance(tx, dict)])
        world_days = _request_days(phone_service,
                                   [row.get("timestamp") for row in
                                    day_timeline._ledger_records(Path(world_home))
                                    if isinstance(row, dict)])
        check(day_iso in wallet_days and day_iso in world_days,
              "业务日与 World/钱包真实落账日不在同一 +08 日界（时间线会过滤掉来源）",
              {"day": day_iso, "wallet_days": sorted(wallet_days),
               "world_days": sorted(world_days)})
        read_roots = (state_root, world_home / "state", world_home / "data",
                      Path(demo.base.services.root))
        try:
            demo.runtime.flush_all()
        except Exception as exc:
            say("stage9", "flush_all 跳过：%s" % type(exc).__name__)
        # 同 stage8：serve 循环每服务一条读连接就顺手跑一次 owner tick，
        # lived-needs live-window 结算会在两视角读取之间写 body/runtime.json 与
        # body/transitions.jsonl。读窗口内冻结这一个后台 tick（真实 owner/socket 不变），
        # 读完恢复；不删快照、不改 mtime 判定、不把读后写说成「正常不算」。
        with _owner_tick_frozen(demo, label="stage9-timeline"):
            before_bytes = _stage9_state_files(*read_roots)
            operator_view = day_timeline.timeline_envelope(
                service=psvc, state_root=state_root, subject_id=subject, clock=clock_map,
                subject_view=False, world_home=Path(world_home), world_clock_injected=False)
            subject_view = day_timeline.timeline_envelope(
                service=psvc, state_root=state_root, subject_id=subject, clock=clock_map,
                subject_view=True, world_home=Path(world_home), world_clock_injected=False)
            after_bytes = _stage9_state_files(*read_roots)
        changed = sorted(key for key in set(before_bytes) | set(after_bytes)
                         if before_bytes.get(key) != after_bytes.get(key))
        check(not changed, "手机时间线/日记/相册只读读入了写（含 media 字节）",
              {"changed": changed[:8]})
        operator_data = dict(operator_view.get("data") or {})
        entries = [dict(row) for row in (operator_data.get("entries") or [])]
        kinds = {str(row.get("kind")) for row in entries}
        need = {"activity", "plan", "world_action", "wallet_transaction",
                "diary_write", "album_saved"}
        check(bool(operator_view.get("ok")) and need.issubset(kinds),
              "六域时间线没有合并全部真实记录",
              {"ok": operator_view.get("ok"), "kinds": sorted(kinds),
               "sources": {key: (value or {}).get("state")
                           for key, value in (operator_data.get("sources") or {}).items()}})
        check(operator_data.get("skeleton_only") is True
              and operator_data.get("read_only") is True,
              "operator 视图不是 skeleton_only 只读", sorted(operator_data))
        for label, view in (("operator", operator_view), ("subject", subject_view)):
            data = dict(view.get("data") or {})
            rows = [dict(row) for row in (data.get("entries") or [])]
            leaked = []
            for path, text in _stage9_walk_strings({"entries": rows,
                                                    "body_snapshot": data.get("body_snapshot")}):
                if any(token in text for token in STAGE9_PRIVATE_TOKENS) \
                        or png_b64[:32] in text:
                    leaked.append(path)
            check(not leaked, "%s 视图泄漏了日记/相册私文或图片字节" % label,
                  {"leaked": leaked[:4]})
            forbidden = {"text", "body", "content", "caption", "scene",
                         "generation_prompt", "memo", "private_note", "diary_text",
                         "summary", "data", "base64"}
            keys = [key for row in rows for key in row]
            check(not (forbidden & set(keys)),
                  "%s 视图泄漏了私有字段名" % label, sorted(forbidden & set(keys)))
        subject_data = dict(subject_view.get("data") or {})
        subject_rows = [dict(row) for row in (subject_data.get("entries") or [])]
        detail_refs = {str(row.get("kind")): str(row.get("detail_ref") or "")
                       for row in subject_rows}
        check(subject_data.get("skeleton_only") is False
              and bool(detail_refs.get("diary_write"))
              and bool(detail_refs.get("album_saved"))
              and bool(detail_refs.get("activity")),
              "actor=model 的既有读口没有带 detail_ref（聚合仍不复制正文/字节）",
              {"skeleton_only": subject_data.get("skeleton_only"),
               "detail_refs": {key: bool(value) for key, value in detail_refs.items()}})
        bases = {str(row.get("time_basis") or "") for row in entries}
        check(bases and "" not in bases
              and str((operator_data.get("ordering") or {}).get("sorted_by") or "") != "",
              "六域时间线没有如实标注时域/排序",
              {"bases": sorted(bases),
               "ordering": (operator_data.get("ordering") or {}).get("sorted_by")})
        say("stage9-media", "同实例日记 %s（同 key 重放 %s）+ 相册 %s（本地合成 1x1 PNG，"
                            "同 key 重放 %s）；相册不进项目列表/续写候选（refresh 具名撤回）；"
                            "六域时间线 %s，operator skeleton_only、主体带 detail_ref，"
                            "两视角零私文/零字节、读前后 %d 个状态+media 文件字节零变"
            % (_bare(diary_id)[:10], diary_replay.get("status"), _bare(album_id)[:10],
               album_replay.get("status"), sorted(kinds), len(before_bytes)))

        # =====================================================================================
        # D. 共同实践：默认装配的真实 SharedPracticeLife + LifeSourceVerifier；四类真来源各写一条，
        #    伪造来源逐类具名拒绝且 owner 版本/entry 计数不变。
        # =====================================================================================
        contact_port = getattr(demo.cycle, "social_contacts", None)
        practice = getattr(contact_port, "practice", None)
        check(practice is not None, "默认装配没有接上真实共同实践服务（不得手工 stub）")
        description = practice.describe()
        verifier = getattr(practice.subject, "source_verifier", None)
        verifier_report = verifier.describe() if verifier is not None else {}
        check(description.get("source_verifier_bound") is True
              and type(verifier).__name__ == "LifeSourceVerifier"
              and all((verifier_report.get("bound") or {}).values()),
              "共同实践的来源核验不是真实 LifeSourceVerifier/读面未就绪",
              {"describe": {key: description.get(key) for key in
                            ("source_verifier_bound", "person_actor", "model_ready")},
               "verifier": verifier_report})
        check(type(verifier).__name__ not in ("AlwaysTrue", "FakeActionRead", "FakeDecisionRead"),
              "来源核验被 AlwaysTrue/Fake 替身顶替", type(verifier).__name__)

        decision_ref = str(started.get("decision_id") or "")
        adoption_ref = str(started.get("adoption_ref") or "")
        action_rows = [dict(row) for row in
                       (demo.runtime.action_read.list_actions() or [])]
        action_ref = str((action_rows[-1] if action_rows else {}).get("action_id") or "")
        check(bool(decision_ref) and demo.runtime.decision_read.get_decision(decision_ref)
              is not None, "没有本实例真实已提交 Decision 可引用", decision_ref)
        check(bool(adoption_ref)
              and verifier.get_adoption_by_id(adoption_ref) is not None,
              "没有本实例真实 adoption 可引用", adoption_ref)
        check(bool(action_ref) and demo.runtime.action_read.get_action(action_ref) is not None,
              "没有本实例真实 Action 可引用", action_ref)
        inbox = getattr(demo.cycle, "contact_inbox", None)
        if inbox is None:
            raise AssertionError("默认装配没有本地收件箱，无法导入明确脚本外部刺激")
        inbox_answer = inbox.submit({
            "event_kind": "MESSAGE",
            "body": "STAGE9_EXTERNAL_STIMULUS 我在巷口又看到那辆面包车了。",
            "source_ref": "operator:stage9:inbox",
            "operation_key": "stage9:inbox:1"})
        event_ref = str(inbox_answer.get("event_ref") or "")
        check(inbox_answer.get("ok") and bool(event_ref)
              and inbox.get_event(event_ref, include_body=False) is not None,
              "本地收件箱没有留下真实来话事件", inbox_answer.get("status"))

        opened = practice.subject.open_practice(
            title="一起记下巷口早晨的观察",
            opening_text="先把我看到的和听到的分开写：看见的是几次停车，听到的是别人的说法。",
            material_refs=[research_id],
            basis={"decision_ref": decision_ref},
            operation_key="stage9:practice:open:1")
        session_id = str(((opened.get("data") or {}).get("session_id")) or "")
        check(opened.get("ok") and session_id.startswith("artifact:"),
              "共同实践没有在 owner 里开出一段作品",
              {"status": opened.get("status"), "reason": opened.get("reason_code")})
        head = _stage9_practice_head(practice.subject, session_id)
        open_read = dict(practice.subject.read_practice(session_id).get("data") or {})
        open_basis = dict((open_read.get("work") or {}).get("basis") or {})
        check(str(open_basis.get("kind")) == "decision"
              and open_basis.get("verified") is True,
              "开场没有绑定真实 decision 来源", open_basis)

        person = practice.person.submit_user_contribution(
            session_id, text="我也看到过那辆车，但我没听见司机说什么。",
            expected_head=head, operation_key="stage9:practice:user:1")
        check(person.get("ok"), "用户侧自己那句话没有落成", person.get("reason_code"))
        head = _stage9_practice_head(practice.subject, session_id)
        response = practice.subject.submit_subject_response(
            session_id,
            text="我记下了：我亲眼看到的是车停着，没看到的是车门为什么没开。",
            basis={"event_ref": event_ref}, expected_head=head,
            operation_key="stage9:practice:response:1")
        check(response.get("ok"), "绑来话事件的她那一半回应没有落成",
              response.get("reason_code"))
        head = _stage9_practice_head(practice.subject, session_id)
        contribution = practice.subject.submit_subject_contribution(
            session_id, text="再补一条：面包车停的位置离杂货店门口还有两步远。",
            basis={"action_ref": action_ref}, expected_head=head,
            operation_key="stage9:practice:contribution:1")
        check(contribution.get("ok"), "绑真实 Action 的贡献没有落成",
              contribution.get("reason_code"))
        head = _stage9_practice_head(practice.subject, session_id)
        current_view = practice.subject.context_view(session_id)
        work_text = str((current_view.get("data") or {}).get("text") or "")
        work_commit = practice.subject.commit_work_text(
            session_id,
            text=(work_text.rstrip() + "\n\n最后把两件事并排放着：看见的停车，听见的规则。"),
            basis={"adoption_ref": adoption_ref}, expected_head=head,
            operation_key="stage9:practice:work:1")
        check(work_commit.get("ok"), "绑真实 adoption 的作品正文没有落成",
              work_commit.get("reason_code"))
        final_read = practice.subject.read_practice(session_id)
        final_data = dict(final_read.get("data") or {})
        basis_blocks = [dict(open_basis),
                        dict((final_data.get("work") or {}).get("basis") or {})]
        basis_blocks += [dict(entry.get("basis") or {}) for entry in
                         (final_data.get("responses") or [])]
        basis_blocks += [dict(entry.get("basis") or {}) for entry in
                         (final_data.get("contributions") or [])]
        kinds_seen = {str(block.get("kind") or "") for block in basis_blocks
                      if block.get("verified") is True}
        check({"decision", "event", "action", "adoption"}.issubset(kinds_seen),
              "四类真来源没有逐类以 verified basis 落进共同实践",
              {"kinds": sorted(kinds_seen),
               "counts": {"responses": final_data.get("response_count"),
                          "contributions": final_data.get("contribution_count")}})
        check(str(final_data.get("joint_state")) == "JOINT",
              "有经核验的主体回应后 joint 状态仍不是 JOINT", final_data.get("joint_state"))

        view_data = dict(practice.subject.context_view(session_id).get("data") or {})
        not_authority = dict(view_data.get("not_authority") or {})
        check(all(not_authority.get(key) is False for key in
                  ("activity", "session_willingness", "relationship", "conversation")),
              "共同实践上下文没有保留 not_authority 边界", not_authority)
        for entry in basis_blocks:
            check(set(entry) <= {"kind", "ref", "verified", "verified_id",
                                 "owner_status", "verified_at"},
                  "来源块复制了 owner 正文，而不只是身份/状态事实", sorted(entry))

        before_refusal = dict(practice.subject.read_practice(session_id).get("data") or {})
        before_head = str((before_refusal.get("project") or {}).get("head_version_id") or "")
        before_counts = (before_refusal.get("response_count"),
                         before_refusal.get("contribution_count"))
        fake_cases = (
            ("decision", practice.subject.submit_subject_response,
             {"decision_ref": "decision:stage9-does-not-exist"}),
            ("event", practice.subject.submit_subject_contribution,
             {"event_ref": "contact_evt:stage9-does-not-exist"}),
            ("action", practice.subject.submit_subject_contribution,
             {"action_ref": "action:stage9-does-not-exist"}),
            ("adoption", practice.subject.submit_subject_response,
             {"adoption_ref": "adoption:stage9-does-not-exist"}),
        )
        refusal_heads = []
        for kind, method, basis in fake_cases:
            refused = method(session_id, text="不应该落盘的伪造来源记录",
                             basis=basis, expected_head=before_head,
                             operation_key="stage9:practice:fake:%s" % kind)
            check(refused.get("ok") is False
                  and "SOURCE" in str(refused.get("reason_code") or ""),
                  "伪造的 %s 来源没有被具名拒绝" % kind,
                  {"reason": refused.get("reason_code"), "status": refused.get("status")})
            after_refusal = dict(practice.subject.read_practice(session_id).get("data")
                                 or {})
            refusal_heads.append(str((after_refusal.get("project") or {})
                                     .get("head_version_id") or ""))
            check((after_refusal.get("project") or {}).get("head_version_id")
                  == before_head
                  and (after_refusal.get("response_count"),
                       after_refusal.get("contribution_count")) == before_counts,
                  "被拒绝的 %s 伪造来源改动了 owner 版本/entry 计数" % kind,
                  {"head": (after_refusal.get("project") or {}).get("head_version_id"),
                   "counts": (after_refusal.get("response_count"),
                              after_refusal.get("contribution_count"))})
        impersonated = practice.person.submit_subject_response(
            session_id, text="用户来话不能冒她的决定",
            basis={"event_ref": event_ref}, expected_head=before_head,
            operation_key="stage9:practice:impersonate:1")
        check(impersonated.get("ok") is False
              and str(impersonated.get("reason_code"))
              == "SHARED_PRACTICE_ACTOR_NOT_ALLOWED",
              "用户来话能冒充主体的回应", impersonated.get("reason_code"))
        say("stage9-practice", "共同实践：真实 SharedPracticeLife + LifeSourceVerifier "
                               "(bound=%s)；decision/event/action/adoption 四类真来源逐类落账，"
                               "作品 v%s、%d 回应/%d 贡献；伪造四类来源均具名拒绝且 head/计数不变；"
                               "用户来话不能替主体回应"
            % (sorted((verifier_report.get("bound") or {}).items()),
               str((final_data.get("project") or {}).get("version_number") or "?"),
               int(final_data.get("response_count") or 0),
               int(final_data.get("contribution_count") or 0)))

        say("stage9-done", "第九阶段跑完：同 root 研究 START→CONTINUE→ABANDON（真实 marker/版本/两个"
                           "写作上下文）→ 零命中对照/换版负控 → 私人日记+相册导入与候选隔离 → "
                           "六域时间线两视角只读零写 → S03 OBSERVED/REPORTED/INFERRED+DISPUTED/STALE "
                           "进正式 Decision 且不越权写 World/Memory → 共同实践四类真来源落账、"
                           "四类伪造来源具名拒绝")
        # 返回本阶段由正式 Decision START/CONTINUE 写出、并已 ABANDON 的真实研究作品 id，
        # 供第十阶段的归档负控直接复用（不按标题或列表顺序猜）。
        return research_id
    finally:
        if own_instance and demo is not None:
            demo.close()


# ---------------------------------------------------------------------------------------------
# 0.7 第十阶段：有限处境表达/忙时回话、损失承接、遗忘屏障与恢复只读检视
# ---------------------------------------------------------------------------------------------
STAGE10_NORMAL_REPLY = "脚本替身：我在，先把手上这一步收完，马上回你。"
STAGE10_OVERLONG_REPLY = "长" * 700
STAGE10_LOCAL_STIMULUS = "STAGE10_LOCAL_STIMULUS 你现在在忙吗？"
STAGE10_SHARE_TEXT = "等这条支线归档前，先把这一小段留在这里。"
STAGE10_FORGET_SOURCE_TEXT = "第十阶段可处置支线来源：这一页只服务遗忘屏障验证。"
STAGE10_FORGET_EXPERIENCE_TEXT = (
    "STAGE10_FORGET_EXPERIENCE_SENTINEL 这是一条本单专门写入的可处置经历，"
    "只用来验证遗忘屏障与保留状态，不是自然生活经历。")


class _Stage10ReplyClient:
    """最底层 model client 的脚本替身：固定一句话，一次调用。

    替换的只是 provider 那一层；预算预留、prompt、边界、归档全部是生产代码。它不发任何
    网络请求，也不冒充产品模型。
    """

    send_split_capable = True
    provider = "scripted"
    model = "scripted-stage10-local-reply"

    def __init__(self, text: str) -> None:
        self.text = str(text)
        self.calls = 0

    def complete(self, *, system_prompt: str, user_prompt: str,
                 max_output_tokens=None):
        self.calls += 1
        return SimpleNamespace(text=self.text, provider=self.provider, model=self.model,
                               purpose="local_reply", latency_ms=1, status=200)


def _stage10_artifact_state(port, artifact_id: str) -> dict:
    """One artifact's owner-read identity: head, lifecycle, revision, versions and body equality.

    The body is kept only so two snapshots can be compared here; it is never printed.
    """

    point = port.resume_point(str(artifact_id))
    if point is None:
        return {"artifact": str(artifact_id), "present": False}
    versions = port.list_versions(str(artifact_id), limit=50) or {}
    entries = []
    for row in (versions.get("entries") or []):
        if isinstance(row, dict):
            entries.append((str(row.get("version_id") or ""),
                            int(row.get("version_number") or 0),
                            str(row.get("content_sha256") or "")))
    return {
        "artifact": str(getattr(point, "artifact_id", "") or artifact_id),
        "present": True,
        "head_version_id": str(getattr(point, "head_version_id", "") or ""),
        "version_number": int(getattr(point, "version_number", 0) or 0),
        "lifecycle": str(getattr(point, "lifecycle", "") or ""),
        "revision": int(getattr(point, "revision", 0) or 0),
        "resumable": bool(getattr(point, "resumable", False)),
        "content": str(getattr(point, "content", "") or ""),
        "versions": sorted(entries),
    }


def _stage10_round(demo, intent: str, *, attempts: int = 4) -> dict:
    """One formal round; only the existing quiet gate may hold it, and only a real clock jump."""

    out = demo.round(intent)
    for _ in range(max(0, attempts - 1)):
        if str(out.get("outcome")) != "QUIET":
            break
        demo.clock.advance(6 * 3600, because="stage10 静默门禁")
        out = demo.round(intent)
    return out


def _stage10_share_round(demo, request: dict, *, attempts: int = 4) -> dict:
    out = demo.share_round(request, attempts=attempts)
    for _ in range(max(0, attempts - 1)):
        if str(out.get("outcome")) != "QUIET":
            break
        demo.clock.advance(6 * 3600, because="stage10 分享静默门禁")
        out = demo.share_round(request, attempts=attempts)
    return out


def _stage10_decision_ids(demo) -> set:
    state = demo.runtime.decision_read.store.load_state() or {}
    return set((state.get("decisions_by_id") or {}).keys())


def _stage10_contact_port(demo, responder):
    """A real ``SocialContactPort`` over this instance's own inbox/protocol.

    Only the responder is injected; the inbox, the finite co-presence protocol, the claim and the
    reply archive are the instance's real owners.  The content half (shared practice) is left off
    this narrow ordinary-message check so a message cannot become a work step here.
    """

    import life_social_contacts as lsc

    port = lsc.SocialContactPort(
        demo.runtime, state_root=odl.life_state_root(demo.instance),
        subject_id=str(demo.data["subject_id"]), home=str(demo.world_home),
        inbox=demo.cycle.contact_inbox, protocol=demo.cycle.co_presence,
        responder=responder)
    port.practice = None
    return port


def _stage10_reply_port(demo, client, *, budget=None):
    """One real responder over an optional real ``LifeRequestBudget`` ledger."""

    from life_request_budget import BudgetedModelClient
    from local_message_responder import LocalMessageResponder

    inner = client
    if budget is not None:
        inner = BudgetedModelClient(client, budget)
    return _stage10_contact_port(demo, LocalMessageResponder(inner))


def _stage10_submit(demo, text: str, *, tag: str) -> dict:
    return demo.cycle.contact_inbox.submit({
        "event_kind": "LOCAL_MESSAGE",
        "body": text,
        "source_ref": "operator:stage10:local",
        "occurred_at": demo.clock.iso(),
        "operation_key": "stage10:message:%s" % tag,
    })


def _stage10_payload_state(path, event_id: str) -> dict:
    """Boolean/size-only read of one M0 row (never the body)."""

    import sqlite3

    connection = sqlite3.connect(str(path))
    try:
        row = connection.execute(
            "SELECT payload_erased,length(content) FROM evidence_events WHERE event_id=?",
            (str(event_id),)).fetchone()
    finally:
        connection.close()
    if row is None:
        return {"row_present": False, "payload_erased": False, "payload_present": False,
                "chars": 0}
    erased = bool(int(row[0] or 0))
    chars = int(row[1] or 0)
    return {"row_present": True, "payload_erased": erased,
            "payload_present": (not erased) or chars > 0, "chars": chars}


def _run_stage10_chain(demo=None, booklet=None, booklet_bare="", research_id=""):
    """第十阶段：同一实例上的有限处境/忙时回话、做法退休与来源归档、遗忘屏障与只读恢复检视。

    ``demo`` 传主链对象时接回**同一个 root / 同一个 Supply owner / 同一份 Memory**；传
    ``None``（单测 helper）时先跑第八、九阶段（``keep_demo=True``）再在同一对象上继续，
    不另造第三实例、不复制旧根、不把前序证据换成同名重造对象。
    """

    import hashlib
    import sqlite3

    import life_practice_context as lpc
    import life_sharing_context as lsc
    import life_situation_view as lsv
    import runtime_recovery_review as rrr
    import forget_request as fr
    from forget_request import ForgetCaseStore, ForgetGate, WAITING_RETENTION
    from life_artifact_port import EffectState
    from life_experience_port import (LifeExperiencePort, build_outcome_event,
                                      life_conversation_id)
    from life_memory_service import LifeMemoryService
    from life_request_budget import LifeRequestBudget
    from memory_authority import MemoryDeletionAuthority

    own_instance = demo is None
    if own_instance:
        demo = _run_stage8_chain(keep_demo=True)
        # 第九阶段返回它自己正式写出并 ABANDON 的真实研究作品 id；独立 helper 也走同一条
        # 正常创建/正式写路径，不在这里另造支线对象。
        research_id = _run_stage9_chain(demo=demo)
        check(str(research_id or "").startswith("artifact:"),
              "第十阶段单测 helper 的第九阶段没有返回真实 research_id",
              {"research_id": research_id})
    try:
        state_root = odl.life_state_root(demo.instance)
        subject = str(demo.data["subject_id"])
        practice = demo.cycle.practice
        sharing_service = demo.cycle.sharing_service
        check(practice is not None and practice.available() is True
              and str(practice.availability().get("status")) == "READY",
              "第十阶段没有接上既有 practice seam", type(practice).__name__)
        check(sharing_service is not None,
              "第十阶段没有接上既有 sharing owner", type(sharing_service).__name__)
        if not booklet:
            bare_work = str(getattr(demo.cycle, "work_artifact_id", "") or
                            demo.data.get("work_artifact_id") or "")
            booklet = "artifact:%s" % _bare(bare_work) if bare_work else ""
        booklet = str(booklet or "")
        booklet_bare = _bare(booklet) if not booklet_bare else str(booklet_bare)
        check(bool(booklet) and bool(booklet_bare),
              "主实例没有可作对照的作品/小册子", {"booklet": booklet})
        booklet_before = _stage10_artifact_state(demo.port, booklet)

        # =====================================================================================
        # A. [stage10-expression] 三列处境（真实 Body 读 + 本实例预算读 + 明确观察）与忙时回话
        # =====================================================================================
        situation_source = getattr(demo.cycle, "situation_source", None)
        situation_body_port = getattr(situation_source, "body_port", None)
        check(situation_source is not None and situation_body_port is not None
              and callable(getattr(situation_body_port, "body", None)),
              "默认装配没有接上真实处境读（LifeSituationView/WorldBodyReadPort）",
              {"source": type(situation_source).__name__,
               "body_port": type(situation_body_port).__name__})
        budget_view = demo.fixture.budget.view()
        budget_check = demo.fixture.budget.check()
        budget_read = {
            "source": "life_request_budget.check",
            "allowed": budget_check.get("allowed"),
            "state": budget_view.get("state"),
            "reason_code": (budget_check.get("reason_code")
                            or budget_view.get("reason_code") or ""),
            "observed_at": demo.clock.iso(),
            "path": budget_view.get("path"),
            "utc_day": budget_view.get("utc_day"),
            "run_id": budget_view.get("run_id"),
            "utc_day_calls": budget_view.get("utc_day_calls"),
            "utc_day_max": budget_view.get("utc_day_max"),
            "run_window_calls": budget_view.get("run_window_calls"),
            "run_window_max": budget_view.get("run_window_max"),
        }
        now_iso = demo.clock.iso()
        future_iso = (demo.clock.now + timedelta(hours=1)).astimezone(
            timezone.utc).isoformat()
        past_observed = (demo.clock.now - timedelta(hours=2)).astimezone(
            timezone.utc).isoformat()
        past_until = (demo.clock.now - timedelta(hours=1)).astimezone(
            timezone.utc).isoformat()
        observations = [
            {"observation_ref": "observation:stage10:current",
             "source_ref": "operator:stage10:note",
             "observed_at": now_iso, "valid_until": future_iso},
            {"observation_ref": "observation:stage10:unverified",
             "source_ref": "operator:stage10:note", "observed_at": now_iso},
            {"observation_ref": "observation:stage10:expired",
             "source_ref": "operator:stage10:note",
             "observed_at": past_observed, "valid_until": past_until},
            {"observation_ref": "observation:stage10:leak-mood",
             "source_ref": "operator:stage10:note",
             "observed_at": now_iso, "valid_until": future_iso, "mood": "calm"},
            {"observation_ref": "observation:stage10:leak-fatigue",
             "source_ref": "operator:stage10:note",
             "observed_at": now_iso, "valid_until": future_iso, "fatigue": 0.2},
            {"observation_ref": "observation:stage10:leak-body",
             "source_ref": "operator:stage10:note",
             "observed_at": now_iso, "valid_until": future_iso,
             "body_capacity_state": "AVAILABLE"},
        ]
        with _owner_tick_frozen(demo, label="stage10-situation"):
            before_state = _stage9_state_files(demo.instance)
            view = lsv.read_life_situation(
                home=str(demo.world_home), now=now_iso,
                body_port=situation_body_port, budget=budget_read,
                observations=observations)
            after_state = _stage9_state_files(demo.instance)
        check(before_state == after_state,
              "处境三列读取改写了实例状态文件字节",
              {"before": len(before_state), "after": len(after_state)})
        check(view.get("writes_performed") == 0
              and view.get("reads_performed") in (0, 1),
              "处境视图不是零写", {"writes": view.get("writes_performed"),
                                  "reads": view.get("reads_performed")})
        check(set(view.get("dimensions") or {}) == {lsv.DIMENSION_BODY,
                                                   lsv.DIMENSION_RESOURCE_BUDGET},
              "处境维度没有保持 body/resource_budget 两列",
              view.get("dimensions"))
        body_column = dict(view.get("body") or {})
        budget_column = dict(view.get("resource_budget") or {})
        knowledge = dict(view.get("knowledge") or {})
        check("body_capacity_source" in str(body_column.get("derived_by") or "")
              and "budget" in str(budget_column.get("derived_by") or ""),
              "body/预算两列不是各自 owner 的投影",
              {"body": body_column.get("derived_by"),
               "budget": budget_column.get("derived_by")})
        check(not any(key in budget_column for key in
                      ("body_capacity_state", "fatigue", "mood", "health")),
              "预算列混入了 body/情绪字段", sorted(budget_column))
        check(knowledge.get("knowledge_is_not_a_capacity_dimension") is True
              and "body_capacity_state" not in knowledge,
              "knowledge 列被当成能力维度", sorted(knowledge))
        validity = {str(item.get("observation_ref")): item
                    for item in (knowledge.get("observations") or [])}
        expect = {
            "observation:stage10:current": lsv.VALIDITY_CURRENT,
            "observation:stage10:unverified": lsv.VALIDITY_UNVERIFIED,
            "observation:stage10:expired": lsv.VALIDITY_EXPIRED,
        }
        for ref, wanted in expect.items():
            check(str((validity.get(ref) or {}).get("validity")) == wanted,
                  "观察 %s 的有效期标签不是 %s" % (ref, wanted),
                  validity.get(ref))
        for ref in ("observation:stage10:leak-mood",
                    "observation:stage10:leak-fatigue",
                    "observation:stage10:leak-body"):
            item = dict(validity.get(ref) or {})
            check(str(item.get("validity")) == lsv.VALIDITY_INVALID
                  and str(item.get("reason_code")) == lsv.REASON_OBSERVATION_AUTHORITY_FIELD,
                  "越权观察字段没有 INVALID + OBSERVATION_AUTHORITY_FIELD_LEAK", item)
        cap_kwargs = lsv.ag1_capacity_kwargs(view)
        check(set(cap_kwargs) == {"body_capacity_state", "body_capacity_ref",
                                  "resource_availability_state", "resource_budget_ref"},
              "AG-1 能力 kwargs 不是四件套", sorted(cap_kwargs))
        say("stage10-expression",
            "三列处境：body=%s(%s)/预算=%s(%s)/knowledge current=%s unverified=%s "
            "expired=%s invalid=%s；writes=0，读前后 %d 个状态文件字节零变；观察均为本脚本输入"
            % (body_column.get("state"), body_column.get("reason_code"),
               budget_column.get("state"), budget_column.get("reason_code"),
               (knowledge.get("counts") or {}).get("current"),
               (knowledge.get("counts") or {}).get("unverified"),
               (knowledge.get("counts") or {}).get("expired"),
               (knowledge.get("counts") or {}).get("invalid"), len(before_state)))

        # 一条真实 ACTIVE Activity：不另造对象、也不替换任何候选来源。脚本从这一轮 AG-0
        # **真正 offered**、且 START 合法的真实可续写作品里挑一件开工，写出真实版本；
        # 如果本轮真的先给出主小册子，那就是它（只排除阅读材料这种非作品文档）。
        # 这不是降低候选上限或关闭世界机会——本轮仍保留真实 World 源与全部个人机会。
        import candidate_sources_ag0 as _ag0

        world_adapter_before = demo.runtime.candidate_registry.get_adapter(
            _ag0.SOURCE_WORLD_OPPORTUNITY)
        active_points_before = {}
        for row in (demo.port.list_artifacts() or []):
            if str(row.get("lifecycle")) != "ACTIVE":
                continue
            point = demo.port.resume_point(str(row.get("artifact_id") or ""))
            if point is None:
                continue
            active_points_before[_bare(getattr(point, "artifact_id", "")
                                       or row.get("artifact_id"))] = {
                "version_number": int(getattr(point, "version_number", 0) or 0),
                "head_version_id": str(getattr(point, "head_version_id", "") or ""),
            }
        demo.cycle.work_artifact_id = ""
        demo.cognition.project_bare = ""
        # 只排除阅读材料：它是资料，不是“可续写的作品”，走的是 read 而不是 start_work。
        demo.cognition.work_exclude = {value for value in
                                       (demo.cognition.reading_bare,) if value}
        started = _stage10_round(demo, "start_offered_work", attempts=6)
        expression_bare = str((demo.cognition.reads[-1] if demo.cognition.reads else {})
                              .get("chose") or "")
        expression_artifact = "artifact:%s" % expression_bare
        expression_activity = str(started.get("activity_ref") or "")
        expression_offer = dict(demo.cognition.offered[-1] if demo.cognition.offered else {})
        expression_candidates = [dict(row) for row in
                                 (expression_offer.get("candidates") or [])]
        expression_targets = [str(ref) for candidate in expression_candidates
                              for ref in (candidate.get("targets") or [])]
        expression_point = demo.port.resume_point(expression_artifact)
        expression_before = dict(active_points_before.get(expression_bare) or {})
        check(str(started.get("decision")) == "START" and bool(expression_activity)
              and _activity(demo.runtime, expression_activity).get("status") == "ACTIVE"
              and bool(expression_bare),
              "忙时回话前没有真实 ACTIVE Activity",
              {"outcome": started.get("outcome"), "decision": started.get("decision"),
               "activity": expression_activity, "chose": expression_bare,
               "booklet": booklet_bare,
               "offer": demo.cognition.offered[-1] if demo.cognition.offered else None})
        check(bool(expression_offer)
              and any(_bare(ref) == expression_bare for ref in expression_targets)
              and expression_point is not None
              and str(getattr(expression_point, "lifecycle", "")) == "ACTIVE",
              "表达目标不是本轮 AG-0 真正 offered 的真实可续写作品",
              {"chose": expression_bare, "targets": expression_targets[:12],
               "lifecycle": getattr(expression_point, "lifecycle", None)})
        check(int(getattr(expression_point, "version_number", 0) or 0)
              > int(expression_before.get("version_number") or 0)
              and str(getattr(expression_point, "head_version_id", "") or "")
              != str(expression_before.get("head_version_id") or ""),
              "正式 Decision/AR-0 没有在选中的真实作品上写出新版本",
              {"before": expression_before,
               "after": {"version_number": getattr(expression_point, "version_number", None),
                         "head_version_id": getattr(expression_point, "head_version_id", None)}})
        check(any(str(candidate.get("kind")) ==
                  _ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY
                  for candidate in expression_candidates),
              "表达轮没有保留真实 World 机会（世界源被清空或替换）",
              {"kinds": sorted({str(candidate.get("kind"))
                                for candidate in expression_candidates})})
        check(demo.runtime.candidate_registry.get_adapter(
                  _ag0.SOURCE_WORLD_OPPORTUNITY) is world_adapter_before,
              "候选注册表里的 World 适配器被替换过", {"world_adapter": world_adapter_before})
        say("stage10-expression",
            "真实 ACTIVE：脚本从本轮 %d 个 offered 候选（World=%d、个人=%d）里选中作品 %s，"
            "正式 START 写出新版本 v%s"
            % (len(expression_candidates),
               len([c for c in expression_candidates
                    if str(c.get("kind")) == _ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY]),
               len([c for c in expression_candidates
                    if str(c.get("kind")) ==
                    _ag0.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY]),
               expression_bare,
               getattr(expression_point, "version_number", None)))
        # ATOMIC 由 LR-3 自己的 owner 写口设置，不是手改 Activity 状态。
        atomic_profile = demo.runtime.interruption_coordinator.configure_activity_attention(
            activity_id=expression_activity,
            attention_occupancy=__import__("activity_interruption").OCCUPANCY_ATOMIC,
            interruptibility=__import__("activity_interruption").INTERRUPTIBILITY_ATOMIC,
            atomic_region_ref="atomic_region:stage10:expression:1",
            max_atomic_duration_seconds=60,
            action_ref="action:stage10:expression:1")
        atomic_view = demo.runtime.interruption_coordinator.get_current_life_view()
        check(str(atomic_view.get("attention_occupancy")) == "ATOMIC"
              and str(atomic_view.get("communication_availability")) == "BOUNDARY_DEFERRED",
              "LR-3 没有把这条真实 Activity 读成 ATOMIC/BOUNDARY_DEFERRED",
              {"attention": atomic_view.get("attention_occupancy"),
               "availability": atomic_view.get("communication_availability")})

        # 第九阶段留过一条外部刺激来话（有意没在那一阶段回答）。先用**上一轮真实端口**
        # （stage5 已显式停用 responder）把它按 OBSERVED_ONLY 收掉，免得本阶段的正常回复
        # 把别人的来话也算成一次模型调用；这不是另造第二个 consumer，而是同一个 port 自己的
        # 一步，且不会给那条消息编任何正文。
        existing_contacts = getattr(demo.cycle, "social_contacts", None)
        if existing_contacts is not None and callable(
                getattr(existing_contacts, "tick_ordinary_messages", None)):
            existing_contacts.tick_ordinary_messages(limit=10)
        calls_before = int(demo.fixture.budget.view().get("utc_day_calls") or 0)
        normal_client = _Stage10ReplyClient(STAGE10_NORMAL_REPLY)
        normal_port = _stage10_reply_port(demo, normal_client, budget=demo.fixture.budget)
        demo.cycle.social_contacts = normal_port
        activity_before = _activity(demo.runtime, expression_activity)
        foreground_before = str(_foreground(demo.runtime) or "")
        submitted = _stage10_submit(demo, STAGE10_LOCAL_STIMULUS, tag="normal")
        event_ref = str(submitted.get("event_ref") or "")
        normal_tick = normal_port.tick_ordinary_messages(limit=5)
        normal_row = next((dict(row) for row in (normal_tick.get("processed") or [])
                           if str(row.get("event_ref")) == event_ref), {})
        normal_reply = dict(((demo.cycle.contact_inbox.reply(
            event_ref, include_text=True) or {}).get("data") or {}).get("reply") or {})
        activity_after = _activity(demo.runtime, expression_activity)
        calls_after = int(demo.fixture.budget.view().get("utc_day_calls") or 0)
        check(bool(event_ref) and bool(submitted.get("ok")),
              "普通本地来话没有进入既有的 contact inbox", submitted.get("reason_code"))
        check(str(normal_row.get("communication_availability")) == "BOUNDARY_DEFERRED"
              and bool(normal_row.get("foreground_atomic"))
              and bool(normal_row.get("boundary_deferred"))
              and bool(normal_row.get("no_pause"))
              and normal_row.get("pause_requested") is False
              and normal_row.get("activity_started") is False,
              "ATOMIC 前台下的普通来话没有读成 BOUNDARY_DEFERRED/no_pause",
              {key: normal_row.get(key) for key in
               ("communication_availability", "foreground_atomic", "boundary_deferred",
                "no_pause", "pause_requested", "activity_started")})
        check(normal_row.get("responded") is True
              and (normal_row.get("response") or {}).get("archived") is True
              and normal_reply.get("reply_present") is True
              and str(normal_reply.get("text") or "") == STAGE10_NORMAL_REPLY
              and normal_reply.get("delivered_to_user") is False,
              "本地回复没有真实归档到 life_local_contact（或冒充已投递）",
              {"responded": normal_row.get("responded"),
               "response": normal_row.get("response"),
               "reply_present": normal_reply.get("reply_present"),
               "chars": normal_reply.get("reply_chars")})
        check(normal_client.calls == 1 and calls_after == calls_before + 1,
              "正常回复没有走本实例 request ledger 的一次真实预留",
              {"client_calls": normal_client.calls, "before": calls_before,
               "after": calls_after})
        check(activity_after.get("activity_id") == activity_before.get("activity_id")
              and activity_after.get("status") == activity_before.get("status")
              and activity_after.get("revision") == activity_before.get("revision")
              and str(_foreground(demo.runtime) or "") == foreground_before,
              "回话动过 Activity id/state/revision 或前台",
              {"before": {k: activity_before.get(k)
                          for k in ("activity_id", "status", "revision")},
               "after": {k: activity_after.get(k)
                         for k in ("activity_id", "status", "revision")},
               "foreground": foreground_before})
        replay = demo.cycle.contact_inbox.submit({
            "event_kind": "LOCAL_MESSAGE", "body": STAGE10_LOCAL_STIMULUS,
            "source_ref": "operator:stage10:local", "occurred_at": demo.clock.iso(),
            "operation_key": "stage10:message:normal"})
        replay_tick = normal_port.tick_ordinary_messages(limit=5)
        summary = dict(demo.cycle.contact_inbox.summary().get("data") or {})
        check(str(replay.get("status")) == "already_applied"
              and str(replay.get("event_ref") or "") == event_ref
              and not (replay_tick.get("processed") or [])
              and normal_client.calls == 1
              and int(summary.get("replies_stored") or 0) == 1,
              "同 operation_key 重放产生了第二次调用或第二句",
              {"status": replay.get("status"), "event_same": replay.get("event_ref") == event_ref,
               "calls": normal_client.calls,
               "replies_stored": summary.get("replies_stored")})
        say("stage10-expression",
            "忙时回话：ATOMIC/BOUNDARY_DEFERRED、no_pause、Activity id/state/revision 与前台不变；"
            "回复归档 %s 字、delivered_to_user=False；脚本 client 调用 %d 次；本实例预算 %s→%s；"
            "同 key 重放 already_applied、未二调用/二句"
            % (normal_reply.get("reply_chars"), normal_client.calls, calls_before, calls_after))

        # 负控：超长、空答案、零预算。三者都走真实 SocialContactPort + 真实 responder。
        over_client = _Stage10ReplyClient(STAGE10_OVERLONG_REPLY)
        over_port = _stage10_reply_port(demo, over_client, budget=demo.fixture.budget)
        over_event = str(_stage10_submit(demo, "STAGE10_LOCAL_STIMULUS 长回复那一次",
                                         tag="overlong").get("event_ref") or "")
        over_row = next((dict(row) for row in
                         (over_port.tick_ordinary_messages(limit=5).get("processed") or [])
                         if str(row.get("event_ref")) == over_event), {})
        over_reply = dict(demo.cycle.contact_inbox.reply(over_event) or {})
        check((over_row.get("response") or {}).get("reason_code") == "LOCAL_REPLY_TOO_LONG"
              and over_row.get("responded") is False
              and over_reply.get("status") == "absent"
              and over_reply.get("data") is not None
              and (over_reply.get("data") or {}).get("reply", {}).get("reply_present") is False,
              "超长回复没有被具名拒绝且未截断", over_row.get("response"))

        empty_client = _Stage10ReplyClient("")
        empty_port = _stage10_reply_port(demo, empty_client, budget=demo.fixture.budget)
        empty_event = str(_stage10_submit(demo, "STAGE10_LOCAL_STIMULUS 空答案那一次",
                                          tag="empty").get("event_ref") or "")
        empty_row = next((dict(row) for row in
                          (empty_port.tick_ordinary_messages(limit=5).get("processed") or [])
                          if str(row.get("event_ref")) == empty_event), {})
        empty_reply = dict(demo.cycle.contact_inbox.reply(empty_event) or {})
        check((empty_row.get("response") or {}).get("reason_code") == "LOCAL_REPLY_EMPTY_ANSWER"
              and empty_row.get("responded") is False
              and (empty_reply.get("data") or {}).get("reply", {}).get("reply_present") is False,
              "空答案没有被具名拒绝", empty_row.get("response"))

        zero_view = demo.fixture.budget.view()
        zero_calls = int(zero_view.get("utc_day_calls") or 0)
        zero_budget = LifeRequestBudget(state_root, utc_day_max=zero_calls)
        zero_client = _Stage10ReplyClient("零预算下不该出现的正文")
        zero_port = _stage10_reply_port(demo, zero_client, budget=zero_budget)
        zero_event = str(_stage10_submit(demo, "STAGE10_LOCAL_STIMULUS 零预算那一次",
                                         tag="zero-budget").get("event_ref") or "")
        zero_row = next((dict(row) for row in
                         (zero_port.tick_ordinary_messages(limit=5).get("processed") or [])
                         if str(row.get("event_ref")) == zero_event), {})
        zero_reply = dict(demo.cycle.contact_inbox.reply(zero_event) or {})
        zero_after = int(demo.fixture.budget.view().get("utc_day_calls") or 0)
        check((zero_row.get("response") or {}).get("reason_code")
              == "LOCAL_REPLY_BUDGET_REFUSED"
              and zero_row.get("responded") is False
              and zero_client.calls == 0
              and zero_after == zero_calls
              and (zero_reply.get("data") or {}).get("reply", {}).get("reply_present") is False,
              "零预算没有具名拒绝、底层 client 仍被调用或写了假正文",
              {"response": zero_row.get("response"), "client_calls": zero_client.calls,
               "before": zero_calls, "after": zero_after})
        say("stage10-expression",
            "负控：超长=LOCAL_REPLY_TOO_LONG（未截断）、空=LOCAL_REPLY_EMPTY_ANSWER、"
            "零预算=LOCAL_REPLY_BUDGET_REFUSED（底层 client 0 次、无正文）；"
            "正常/超长/空各消耗本实例账一次，零预算未增账")

        # 收起表达 Activity：前台若仍占着就走真实 ABANDON；若 ATOMIC 边界入口已经释放了
        # 前台，就不再伪造一次终态 Decision。这里只要求真实前台槽位释放，供下一段正常开工。
        demo.cognition.project_bare = expression_bare
        abandoned = None
        if _foreground(demo.runtime) is not None:
            abandoned = _stage10_round(demo, "abandon", attempts=4)
            if _foreground(demo.runtime) is not None:
                import activity_interruption as _ai

                demo.runtime.interruption_coordinator.assess_events(
                    [{"event_kind": _ai.EVENT_KIND_COMMUNICATION,
                      "event_id": "comm_evt:stage10:expression:hold",
                      "channel_ref": "channel:stage10_local",
                      "message_ref": "msg:stage10:expression:hold",
                      "message_class": _ai.MESSAGE_CLASS_EXPLICIT_HOLD_REQUEST,
                      "request_boundary_pause": True,
                      "source_refs": ["evt:stage10:expression:hold"],
                      "occurred_at": demo.clock.iso(),
                      "observed_at": demo.clock.iso()}],
                    apply_immediate_pause=False)
                demo.runtime.interruption_coordinator.handle_boundary_reached({
                    "boundary_ref": "atomic_exit:stage10:expression:1",
                    "activity_ref": expression_activity,
                    "source_ref": "executor_boundary:stage10:expression:1",
                    "boundary_kind": "ATOMIC_EXIT",
                    "atomic_region_ref": "atomic_region:stage10:expression:1",
                    "occurred_at": demo.clock.iso(), "observed_at": demo.clock.iso(),
                    "recorded_at": demo.clock.iso(), "enqueued_at": demo.clock.iso(),
                    "causal_parent_refs": [],
                    "source_refs": ["atomic_exit:stage10:expression:1",
                                    "executor_boundary:stage10:expression:1"]})
                demo.cognition.project_bare = expression_bare
                abandoned = _stage10_round(demo, "abandon", attempts=4)
        check(_foreground(demo.runtime) is None,
              "表达支线没有释放真实前台槽位",
              {"abandoned": abandoned,
               "foreground": _foreground(demo.runtime)})

        # =====================================================================================
        # B. [stage10-loss] 真实方法退休 + 真实来源归档；旧句柄由 AG-1 拒绝，归档目标不换目标
        # =====================================================================================
        methods_answer = practice.service.list_methods(limit=20)
        method_rows = [dict(row) for row in
                       (((methods_answer or {}).get("data") or {}).get("projects") or [])]
        chosen_method = next(
            (row for row in method_rows
             if "一页真的读到" in str(row.get("title") or "")
             and str(row.get("method_state")) in ("TENTATIVE", "TRIAL", "CONTESTED")), None)
        if chosen_method is None:
            chosen_method = next(
                (row for row in method_rows
                 if str(row.get("method_state")) in ("TENTATIVE", "TRIAL", "CONTESTED")), None)
        check(chosen_method is not None, "前序没有可退休的真实做法", method_rows)
        method_id = str(chosen_method.get("artifact_id") or "")
        old_ref = lpc.method_ref_for(method_id)
        method_read0 = dict(practice.read_method(method_id) or {})
        method_versions0 = (demo.port.list_versions(method_id, limit=50) or {}).get("entries") or []
        head0 = str(method_read0.get("head_version_id") or "")
        check(bool(method_id) and bool(head0) and bool(old_ref),
              "可退休做法没有稳定 id/head/句柄",
              {"artifact": method_id, "head": head0, "ref": old_ref})
        retired = practice.service.retire_method(
            method_id, expected_head=head0, operation_key="stage10:retire:1")
        retired_data = dict((retired or {}).get("data") or {})
        method_read1 = dict(practice.read_method(method_id) or {})
        head_retired = str(method_read1.get("head_version_id") or "")
        method_versions1 = (demo.port.list_versions(method_id, limit=50) or {}).get("entries") or []
        check(retired.get("ok") is True
              and str(retired_data.get("method_state")) == "RETIRED"
              and str(method_read1.get("method_state")) == "RETIRED",
              "真实做法没有由 owner CAS 写成 RETIRED",
              {"status": retired.get("status"), "reason": retired.get("reason_code"),
               "data": retired_data})
        check(bool(head_retired)
              and method_read1.get("content_available") is not False
              and bool(str(method_read1.get("instruction") or method_read1.get("excerpt") or ""))
              and len(method_versions1) >= len(method_versions0),
              "退休后正文/历史版本没有保留",
              {"head": head_retired, "versions_before": len(method_versions0),
               "versions_after": len(method_versions1)})
        say("stage10-loss", "做法退休：%s → RETIRED（head=%s，历史版本 %d 条保留，正文仍可读）"
            % (method_id, head_retired[:24], len(method_versions1)))

        # 这一轮要问的是「与这条做法相关的事」。与既有第 4 阶段脚本替身一样，这里只声明本轮
        # Decision 的问题对象；B 仍用自己确定性的检索去匹配，退休做法因此进入本轮 read 的
        # withheld 列表，而不是因为“问了别的题目”完全没被扫到。
        query_title = str(chosen_method.get("title") or "退休做法")
        original_query = getattr(demo.cycle, "_practice_query", None)
        demo.cycle._practice_query = lambda: query_title
        try:
            context_round = _stage10_round(demo, "continue", attempts=4)
        finally:
            if original_query is not None:
                demo.cycle._practice_query = original_query
        context = dict(getattr(demo.cognition, "last_practice_slice", None) or {})
        offered_refs = list(demo.cycle.practice_method_refs() or [])
        withheld = dict(context.get("withheld_methods") or {})
        reason_codes = list(withheld.get("reason_codes") or [])
        check(str(context_round.get("outcome")) != "QUIET"
              and old_ref not in offered_refs
              and lpc.LOSS_METHOD_RETIRED in reason_codes,
              "退休做法仍被 offered，或模型上下文没有 withheld_methods 具名理由",
              {"outcome": context_round.get("outcome"), "offered": offered_refs,
               "withheld": withheld, "error": demo.cycle.last_practice_error})
        say("stage10-loss", "下一正式 Decision：旧句柄不在 offered（%d 个句柄），"
                            "withheld_methods 具名 %s"
            % (len(offered_refs), reason_codes))

        before_ids = _stage10_decision_ids(demo)
        demo.cognition.method_ref_override = old_ref
        bad_round = _stage10_round(demo, "continue", attempts=4)
        after_ids = _stage10_decision_ids(demo)
        bad_text = "%s %s" % (bad_round.get("detail") or "", demo.cycle.last_error or "")
        check(str(bad_round.get("outcome")) == "DECISION_FAILED"
              and "INVENTED_METHOD_REF" in bad_text
              and before_ids == after_ids,
              "旧 method_ref 没有由正式 AG-1 按 INVENTED_METHOD_REF 拒绝，或 Decision 仍提交",
              {"outcome": bad_round.get("outcome"), "detail": bad_text[:240],
               "decisions_before": len(before_ids), "after": len(after_ids)})
        say("stage10-loss", "脚本 Decision 点旧 handle：正式 AG-1 DECISION_FAILED / "
                            "INVENTED_METHOD_REF，Decision 计数 %d→%d"
            % (len(before_ids), len(after_ids)))

        good_round = _stage10_round(demo, "continue", attempts=4)
        good_ids = _stage10_decision_ids(demo)
        check(str(good_round.get("outcome")) != "DECISION_FAILED"
              and len(good_ids) == len(after_ids) + 1,
              "不点旧 handle 的合法 Decision 没有正常提交",
              {"outcome": good_round.get("outcome"), "decision": good_round.get("decision"),
               "before": len(after_ids), "after": len(good_ids),
               "detail": good_round.get("detail")})
        trial = practice.service.record_trial(
            method_id, outcome="UNKNOWN", detail="退休后不应再记评价。",
            expected_head=head_retired, operation_key="stage10:retire:trial:1")
        revise = practice.service.revise_method(
            method_id, instruction="退休后不应再修订。", expected_head=head_retired,
            operation_key="stage10:retire:revise:1")
        method_read2 = dict(practice.read_method(method_id) or {})
        check(trial.get("ok") is False
              and str(trial.get("reason_code")) == "PRACTICE_METHOD_RETIRED"
              and revise.get("ok") is False
              and str(revise.get("reason_code")) == "PRACTICE_METHOD_RETIRED"
              and str(method_read2.get("head_version_id") or "") == head_retired,
              "退休做法的 trial/revise 没有被拒绝，或 head 被改动",
              {"trial": trial.get("reason_code"), "revise": revise.get("reason_code"),
               "head": method_read2.get("head_version_id")})
        say("stage10-loss", "退休后 trial/revise 均 PRACTICE_METHOD_RETIRED，head 未变；"
                            "随后不点旧 handle 的合法 Decision 正常提交")

        # 真实归档支线：直接复用第九阶段已经由正式 Decision START/CONTINUE 写出、并已
        # ABANDON 的同一个 research_id（由 _run_stage9_chain 返回、显式传进来）。不新建
        # 支线、也不为归档额外补一轮 START；prepare 绑定它当前的 head，再走 owner CAS 归档。
        loss_artifact = str(research_id or "")
        loss_bare = _bare(loss_artifact)
        check(bool(loss_artifact) and loss_artifact.startswith("artifact:")
              and bool(loss_bare),
              "第十阶段没有拿到第九阶段返回的真实 research_id",
              {"research_id": research_id})
        loss_before_archive = _stage10_artifact_state(demo.port, loss_artifact)
        check(loss_before_archive.get("present") is True
              and str(loss_before_archive.get("lifecycle")) == "ACTIVE"
              and int(loss_before_archive.get("version_number") or 0) >= 2
              and bool(loss_before_archive.get("head_version_id"))
              and str(loss_before_archive.get("content") or "").startswith(
                  "<!-- nyairo-research:v1"),
              "复用的研究作品不是第九阶段正常写出、当前仍 ACTIVE 的真实版本",
              {key: loss_before_archive.get(key) for key in
               ("present", "lifecycle", "version_number", "head_version_id")})
        booklet_before_archive = _stage10_artifact_state(demo.port, booklet)
        say("stage10-loss",
            "归档复用第九阶段真实研究 %s（v%s，head=%s）；不再新建/额外 START 支线"
            % (loss_artifact, loss_before_archive.get("version_number"),
               str(loss_before_archive.get("head_version_id") or "")[:24]))

        demo.cycle.work_artifact_id = loss_bare
        demo.cognition.project_bare = loss_bare
        prepared = _stage10_share_round(
            demo, {"op": "prepare", "source_bare": loss_bare,
                   "share_text": STAGE10_SHARE_TEXT, "title": "研究作品归档前的一小段"})
        prepared_share = dict(prepared.get("sharing") or {})
        draft_id = str(prepared_share.get("share_id") or "")
        draft_owner = dict((sharing_service.read_share(draft_id) or {}).get("data") or {})
        draft_bound_version = str(((draft_owner.get("share") or {}).get("source") or {})
                                  .get("version_id") or "")
        check(str(prepared_share.get("state")) == "DRAFT" and bool(draft_id)
              and draft_bound_version == loss_before_archive.get("head_version_id"),
              "正式 Decision 的 prepare 没有绑定归档支线当前 head",
              {"outcome": prepared.get("outcome"), "sharing": prepared_share,
               "choice": demo.cognition.sharing_choice,
               "bound": draft_bound_version,
               "head": loss_before_archive.get("head_version_id")})
        point_before_archive = demo.port.resume_point(loss_artifact)
        archived = demo.port.archive_artifact(
            artifact_id=loss_artifact,
            expected_revision=int(getattr(point_before_archive, "revision", 0) or 0),
            grant_id=str(demo.data["artifact_grant_id"]),
            operation_key="stage10:loss:archive:1")
        loss_after_archive = _stage10_artifact_state(demo.port, loss_artifact)
        check(bool(getattr(archived, "applied", False))
              and str(getattr(archived, "effect_state", "")) == EffectState.APPLIED
              and loss_after_archive.get("lifecycle") == "ARCHIVED",
              "Supply owner 没有把支线合法归档到 ARCHIVED",
              {"effect": getattr(archived, "effect_state", None),
               "status": getattr(archived, "status", None),
               "lifecycle": loss_after_archive.get("lifecycle")})
        check(loss_after_archive.get("head_version_id")
              == loss_before_archive.get("head_version_id")
              and loss_after_archive.get("version_number")
              == loss_before_archive.get("version_number")
              and loss_after_archive.get("versions") == loss_before_archive.get("versions")
              and loss_after_archive.get("content") == loss_before_archive.get("content"),
              "归档改动了支线的 head/版本/正文（归档不是删除）",
              {"before": {k: loss_before_archive.get(k) for k in
                          ("head_version_id", "version_number", "lifecycle")},
               "after": {k: loss_after_archive.get(k) for k in
                         ("head_version_id", "version_number", "lifecycle")}})
        say("stage10-loss", "支线 %s 由 owner 合法归档（expected revision CAS），"
                            "head/版本/正文保留；主小册子 %s 未参与归档"
            % (loss_artifact, booklet))

        demo.cognition.project_bare = loss_bare
        refused_continue = _stage10_round(demo, "continue_project", attempts=4)
        loss_after_continue = _stage10_artifact_state(demo.port, loss_artifact)
        offered_targets = []
        if demo.cognition.offered:
            offered_targets = [str(ref) for candidate in
                               (demo.cognition.offered[-1].get("candidates") or [])
                               for ref in _refs(candidate)]
        named_refusal = "WORK_NOT_CONTINUABLE" in (
            str(refused_continue.get("detail") or "")
            + " " + str(demo.cycle.last_error or ""))
        targeted_offered = any(loss_bare in ref for ref in offered_targets)
        check(loss_after_continue.get("versions") == loss_after_archive.get("versions")
              and loss_after_continue.get("head_version_id")
              == loss_after_archive.get("head_version_id")
              and loss_after_continue.get("resumable") is False,
              "已归档目标被续写或换了目标", loss_after_continue)
        check(named_refusal or (not targeted_offered),
              "已归档目标既没有被 owner 具名拒 WORK_NOT_CONTINUABLE，也不在合法候选集里",
              {"outcome": refused_continue.get("outcome"),
               "detail": str(refused_continue.get("detail") or "")[:200],
               "targeted_offered": targeted_offered,
               "offered_targets": [ref for ref in offered_targets
                                   if loss_bare in ref][:4]})
        check(_stage10_artifact_state(demo.port, booklet).get("head_version_id")
              == booklet_before_archive.get("head_version_id"),
              "归档后续写轮偷换了主小册子", {"booklet": booklet})
        say("stage10-loss", "对已归档目标续写：零新版、head/版本不变，"
                            "拒绝路径=%s"
            % ("WORK_NOT_CONTINUABLE" if named_refusal
               else "目标已不在本轮合法候选集"))

        draft_before = lsc.read_draft(sharing_service, draft_id)
        inbox_before = _share_inbox(sharing_service)
        booklet_before_send = _stage10_artifact_state(demo.port, booklet)
        loss_before_send = _stage10_artifact_state(demo.port, loss_artifact)
        send_out = _stage10_share_round(demo, {"op": "send_local", "share_id": draft_id})
        refused_send = dict(send_out.get("sharing") or {})
        draft_after = lsc.read_draft(sharing_service, draft_id)
        inbox_after = _share_inbox(sharing_service)
        booklet_after_send = _stage10_artifact_state(demo.port, booklet)
        loss_after_send = _stage10_artifact_state(demo.port, loss_artifact)
        check(refused_send.get("ok") is False
              and str(refused_send.get("status")) == "stale_source"
              and str(refused_send.get("reason_code")) == "SOURCE_ARCHIVED"
              and not refused_send.get("delivered"),
              "来源归档后 send_local 没有到真实 sharing owner 并被 SOURCE_ARCHIVED 拒",
              {"outcome": send_out.get("outcome"), "sharing": refused_send,
               "choice": demo.cognition.sharing_choice})
        check(draft_after.get("head_version_id") == draft_before.get("head_version_id")
              and draft_after.get("state") == draft_before.get("state")
              and draft_after.get("receipt") is None
              and inbox_after.get("count") == inbox_before.get("count")
              and booklet_after_send == booklet_before_send
              and loss_after_send.get("head_version_id")
              == loss_before_send.get("head_version_id")
              and loss_after_send.get("versions") == loss_before_send.get("versions"),
              "被拒的发送改动了草稿/收件箱/主小册子/支线",
              {"draft_state": draft_after.get("state"),
               "receipt": draft_after.get("receipt"),
               "inbox": [inbox_before.get("count"), inbox_after.get("count")]})
        say("stage10-loss", "归档来源的草稿 send_local：sharing owner 具名 SOURCE_ARCHIVED，"
                            "草稿 head/state/receipt、inbox、主小册子与支线版本零变")
        # 归档/拒续/拒送不碰主小册子：它仍是 ACTIVE、没有被当作归档目标；若它没有被表达轮
        # 选中，head 从进入第十阶段起也一直不变（被选中时只允许表达 START 本身写出新版本）。
        booklet_untouched = (expression_bare == booklet_bare
                             or booklet_after_send.get("head_version_id")
                             == booklet_before.get("head_version_id"))
        check(booklet_after_send.get("lifecycle") == "ACTIVE"
              and loss_artifact != booklet
              and booklet_untouched,
              "归档路径把主小册子换成了别作或被改动",
              {"booklet": booklet,
               "lifecycle": booklet_after_send.get("lifecycle"),
               "head_before": booklet_before.get("head_version_id"),
               "head_after": booklet_after_send.get("head_version_id"),
               "archived": loss_artifact})
        # 只读交接给集成测试：用前序真正写出的 research_id 完成归档负控，且世界源始终未被替换。
        demo.stage10_trace = {
            "research_id": loss_artifact,
            "expression_artifact": expression_artifact,
            "booklet": booklet,
            "archived_lifecycle": loss_after_send.get("lifecycle"),
            "booklet_head_before_archive": booklet_before_archive.get("head_version_id"),
            "booklet_head_after_send": booklet_after_send.get("head_version_id"),
            "send_refusal_reason": str(refused_send.get("reason_code") or ""),
            "expression_world_candidates": len(
                [c for c in expression_candidates
                 if str(c.get("kind")) == _ag0.CANDIDATE_KIND_CONSIDER_WORLD_OPPORTUNITY]),
            "expression_personal_candidates": len(
                [c for c in expression_candidates
                 if str(c.get("kind")) == _ag0.CANDIDATE_KIND_CONSIDER_PERSONAL_OPPORTUNITY]),
            "world_adapter_preserved": demo.runtime.candidate_registry.get_adapter(
                _ag0.SOURCE_WORLD_OPPORTUNITY) is world_adapter_before,
        }

        # =====================================================================================
        # C. [stage10-forget-recovery] 遗忘屏障先行、真实保留、公开读口扣留；随后只读恢复检视
        # =====================================================================================
        owner = "chiyo"
        # 受控子作用域：同一 owner、同一 Memory/Supply，但用一条专门的 conversation 承载这条
        # 可处置经历。既有 Memory 的“遗忘后旧自述按 epoch 抑制”只作用于这个子会话，不会把
        # 主会话里的小册子经历整段扣留；这不是第二个实例，也不是手改 DB。
        conversation = life_conversation_id(owner) + ":stage10-forget"
        evidence_db = Path(demo.memory.evidence_db)
        controls_db = Path(demo.memory.controls_db)
        forget_root = Path(demo.instance) / "stage10-forget"
        retained_root = forget_root / "retained"
        retained_root.mkdir(parents=True, exist_ok=True)
        authority_db = forget_root / "authority.db"
        cases_db = forget_root / "cases.db"
        MemoryDeletionAuthority(authority_db, owner=owner,
                                conversation=conversation).initialize()

        forget_source = demo.port.create_artifact(
            workspace_id=str(demo.data["workspace_id"]),
            title="第十阶段可处置支线来源（夹具原创）",
            content=STAGE10_FORGET_SOURCE_TEXT,
            grant_id=str(demo.data["artifact_grant_id"]),
            operation_key="stage10:forget:source:1")
        forget_source_id = "artifact:%s" % _bare(forget_source.artifact_id)
        forget_source_point = demo.port.resume_point(forget_source_id)
        synthetic_event = build_outcome_event(
            subject=owner, source_ref="stage10:forget:synthetic:1",
            text=STAGE10_FORGET_EXPERIENCE_TEXT, occurred_at=demo.clock.iso(),
            refs=[("artifact", _bare(forget_source_id)),
                  ("artifactversion", str(getattr(forget_source_point,
                                                  "head_version_id", "") or ""))])
        # 这条经历只住在受控子会话里；来源仍是本单新建的支线 artifact。
        import dataclasses

        synthetic_event = dataclasses.replace(synthetic_event,
                                              conversation_id=conversation)
        appended = demo.memory.port.append_outcome(synthetic_event)
        synthetic_event_id = str(synthetic_event.event_id)
        check(str(appended.get("status")) == "inserted"
              and str(appended.get("event_id") or "") == synthetic_event_id,
              "合成可处置经历没有经真实 M0 写入路径落账",
              {"status": appended.get("status"), "event": synthetic_event_id[:24]})

        store = ForgetCaseStore(cases_db, evidence_db=evidence_db,
                                controls_db=controls_db, authority_db=authority_db,
                                owner=owner, conversation=conversation)
        store.initialize()
        gate = ForgetGate(store)
        read_port = LifeExperiencePort(
            evidence_db, controls_db=controls_db, subject=owner,
            authority_db=authority_db, forget_gate=gate)
        memory_service = LifeMemoryService(
            read_port, owner=owner, database=str(evidence_db),
            controls_database=str(controls_db), authority_database=str(authority_db),
            source="stage10", forget_gate=gate)
        check(str(memory_service.status().get("status")) == "READY",
              "真实 Memory 公开读口没有就绪", memory_service.status())
        listing = memory_service.list_experiences(limit=50)
        main_rows = [dict(row) for row in
                     (((listing.get("data") or {}).get("experiences")) or [])
                     if str(row.get("experience_id") or "") != synthetic_event_id]
        booklet_row = next(
            (row for row in main_rows
             if _bare(row.get("artifact_ref")) == booklet_bare), None)
        main_row = booklet_row or (main_rows[0] if main_rows else {})
        main_exp_id = str(main_row.get("experience_id") or "")
        main_before = memory_service.read_experience(main_exp_id) if main_exp_id else {}
        check(bool(main_exp_id)
              and main_before.get("ok") is True
              and bool(((main_before.get("data") or {}).get("experience") or {}).get("text")),
              "主实例没有一条已确证经历在遗忘前公开可读",
              {"count": len(main_rows), "id": main_exp_id[:24]})

        # 真实保留副本：在请求前把当前 evidence 做一份页一致备份，作为随后 WAITING_RETENTION
        # 的实际来源；它不是凭空声称的“备份仍在”。
        retained_copy = retained_root / "evidence.pre-forget.sqlite"
        source_conn = sqlite3.connect(str(evidence_db))
        target_conn = sqlite3.connect(str(retained_copy))
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
            source_conn.close()
        retained_state = _stage10_payload_state(retained_copy, synthetic_event_id)
        check(retained_state.get("payload_present") is True,
              "保留副本没有包含目标正文，不能作为真实 retention 源", retained_state)

        requested = store.request(
            operation_key="stage10:forget:1", event_ids=[synthetic_event_id],
            retention_pending=True, requesting_owner=owner)
        requested_state = dict(requested or {})
        current_state = _stage10_payload_state(evidence_db, synthetic_event_id)
        target_after = memory_service.read_experience(synthetic_event_id)
        main_after = memory_service.read_experience(main_exp_id) if main_exp_id else {}
        allowed_retention = {fr.REASON_OFFLINE_BACKUP,
                             fr.REASON_DERIVED_NOT_COVERED,
                             fr.REASON_PHYSICAL_RETAINED}
        check(requested_state.get("status") == WAITING_RETENTION
              and str(requested_state.get("reason")) in allowed_retention
              and int(requested_state.get("barrier_seq") or 0) >= 1
              and int(requested_state.get("owner_acks") or 0) >= 1,
              "真实 forget request 没有先落屏障并如实停 WAITING_RETENTION",
              {key: requested_state.get(key) for key in
               ("status", "reason", "barrier_seq", "owner_acks", "uncovered")})
        check(current_state.get("payload_erased") is True
              and current_state.get("payload_present") is False
              and retained_state.get("payload_present") is True,
              "当前副本未被清除，或保留副本被误报为已清除",
              {"current": current_state, "retained": retained_state})
        check(target_after.get("ok") is False
              and str(target_after.get("status")) == "NOT_FOUND"
              and read_port.read(synthetic_event_id) is None,
              "被遗忘目标仍能从公开 Memory 读口读回",
              {"status": target_after.get("status"),
               "reason": target_after.get("reason")})
        check(main_after.get("ok") is True
              and ((main_after.get("data") or {}).get("experience") or {}).get("text")
              == ((main_before.get("data") or {}).get("experience") or {}).get("text"),
              "主实例的其它确证经历被遗忘请求连带扣留/改动",
              {"before": bool(main_before.get("ok")), "after": bool(main_after.get("ok")),
               "after_status": main_after.get("status"),
               "after_reason": main_after.get("reason"),
               "main_id": main_exp_id[:24]})
        seq_before_replay = MemoryDeletionAuthority(
            authority_db, owner=owner, conversation=conversation).projection().seq
        replay = store.request(operation_key="stage10:forget:1",
                               event_ids=[synthetic_event_id], retention_pending=True,
                               requesting_owner=owner)
        seq_after_replay = MemoryDeletionAuthority(
            authority_db, owner=owner, conversation=conversation).projection().seq
        cases = store.list_cases(limit=10)
        check(str(replay.get("status")) == "DUPLICATE"
              and str(replay.get("case_id")) == str(requested_state.get("case_id"))
              and int(replay.get("barrier_seq") or 0)
              == int(requested_state.get("barrier_seq") or 0)
              and seq_after_replay == seq_before_replay
              and len(cases) == 1,
              "同 operation_key 重放产生了第二个 case/generation",
              {"status": replay.get("status"), "cases": len(cases),
               "seq": [seq_before_replay, seq_after_replay]})
        say("stage10-forget-recovery",
            "遗忘：真实 barrier seq=%s 先行，owner ack=%s，当前副本正文已清、目标公开读口 "
            "NOT_FOUND，主实例经历仍可读；保留副本真实存在 → WAITING_RETENTION/%s（未冒 COMPLETE）；"
            "同 key 重放 DUPLICATE、case=%d、authority seq=%s"
            % (requested_state.get("barrier_seq"), requested_state.get("owner_acks"),
               requested_state.get("reason"), len(cases), seq_after_replay))

        with _owner_tick_frozen(demo, label="stage10-recovery-review"):
            before_review = _stage9_state_files(demo.instance)
            review = rrr.review(Path(demo.instance))
            after_review = _stage9_state_files(demo.instance)
        check(before_review == after_review,
              "只读恢复检视改写了实例必要文件字节",
              {"before": len(before_review), "after": len(after_review)})
        instance_check = dict(review.get("instance_check") or {})
        wal_rows = list((review.get("wal") or {}).get("rows") or [])
        writer = dict(review.get("writer") or {})
        gates = dict(review.get("gates") or {})
        check(review.get("read_only") is True
              and review.get("flock_taken") is False
              and writer.get("flock_probed") is False,
              "恢复检视不是纯读（取了 flock 或声称只读为假）",
              {"read_only": review.get("read_only"),
               "flock_taken": review.get("flock_taken"),
               "writer": writer})
        check(str(instance_check.get("status")) == "OK"
              and str(review.get("schema_version")) == rrr.REVIEW_SCHEMA,
              "恢复检视没有按真实现场读出实例 schema",
              {"instance_check": instance_check,
               "review_schema": review.get("schema_version")})
        check(str((gates.get("backup_restore") or {}).get("status")) == "NOT_IMPLEMENTED"
              and str((gates.get("migration") or {}).get("status")) == "NOT_IMPLEMENTED",
              "恢复检视把不存在的 backup/migration 入口说成可用", gates)
        check(str(review.get("class")) in ("ASSEMBLABLE", "MANUAL", "UNKNOWN",
                                           "NEEDS_EXISTING_RECOVERY"), review.get("class"))
        say("stage10-forget-recovery",
            "恢复检视：class=%s reasons=%s instance=%s review_schema=%s WAL(source=%s) "
            "flock_taken=False flock_probed=False backup_restore=%s migration=%s；"
            "前后 %d 个必要状态文件字节零变"
            % (review.get("class"), (review.get("reasons") or [])[:4],
               instance_check.get("status"), review.get("schema_version"),
               [str(row.get("store")) for row in wal_rows],
               (gates.get("backup_restore") or {}).get("status"),
               (gates.get("migration") or {}).get("status"), len(before_review)))

        say("stage10-done",
            "第十阶段跑完：同 root 三列处境/ATOMIC 忙时回话（真归档+幂等+三负控+本实例预算）→ "
            "做法 RETIRED 且 AG-1 拒旧 handle、合法下一轮继续 → 复用第九阶段真实研究、owner 归档、"
            "拒续不换目标、归档来源 send 被 SOURCE_ARCHIVED 拒且三件零变 → "
            "真实遗忘屏障/公开读口扣留/主经历仍可读/"
            "WAITING_RETENTION → 恢复 review 纯读、flock 不取、backup/migration NOT_IMPLEMENTED")
        return 0
    finally:
        if own_instance and demo is not None:
            demo.close()


_NETWORK_GUARD_INSTALLED = False


def install_network_guard() -> dict:
    """Install the offline acceptance guard: no INET, no DNS, no outbound connect.

    Only the demo process is guarded; AF_UNIX stays available because the instance's own
    Supply/World owners talk over it.  This changes nothing system-wide and never touches the
    operator's model profile or the worker gateway -- a product path that tried to reach the
    network would fail loudly here instead of being masked by a live endpoint.
    """

    global _NETWORK_GUARD_INSTALLED
    import socket as _socket

    if _NETWORK_GUARD_INSTALLED:
        return {"already": True}
    real_socket = _socket.socket

    class _GuardedSocket(real_socket):  # type: ignore[misc, valid-type]
        def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
            resolved = _socket.AF_INET if family in (-1, None) else family
            if resolved in (_socket.AF_INET, _socket.AF_INET6):
                raise AssertionError("OFFLINE_INET_FORBIDDEN")
            if fileno is None:
                super().__init__(family, type, proto)
            else:
                super().__init__(family, type, proto, fileno)

    def _deny(*_args, **_kwargs):
        raise AssertionError("OFFLINE_NETWORK_FORBIDDEN")

    def _refused(call) -> bool:
        try:
            call()
        except AssertionError:
            return True
        except Exception:
            return False
        return False

    _socket.socket = _GuardedSocket
    _socket.create_connection = _deny
    _socket.getaddrinfo = _deny
    unix_ok = False
    try:
        probe = _GuardedSocket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        probe.close()
        unix_ok = True
    except Exception:
        unix_ok = False
    _NETWORK_GUARD_INSTALLED = True
    return {
        "inet_refused": _refused(lambda: _GuardedSocket(_socket.AF_INET, _socket.SOCK_STREAM)),
        "inet6_refused": _refused(lambda: _GuardedSocket(_socket.AF_INET6, _socket.SOCK_STREAM)),
        "dns_refused": _refused(lambda: _socket.getaddrinfo("example.com", 80)),
        "connect_refused": _refused(
            lambda: _socket.create_connection(("192.0.2.1", 80), timeout=0.01)),
        "unix_ok": unix_ok,
    }


def main() -> int:
    guard = install_network_guard()
    say("stage9-network", "产品路径离线守卫：AF_INET=%s AF_INET6=%s DNS=%s create_connection=%s，"
                          "AF_UNIX=%s（未改全局网络/施工网关）"
        % (guard.get("inet_refused"), guard.get("inet6_refused"), guard.get("dns_refused"),
           guard.get("connect_refused"), guard.get("unix_ok")))
    demo = Demo(start_at=datetime(2026, 11, 10, 11, 0, tzinfo=JST),
                enable_lived_needs=True)
    try:
        demo.build()
        say("root", "新隔离根 %s（保留不删）" % demo.root)
        say("clock", "业务时钟 %s（JST %s）"
            % (demo.clock.iso(), demo.clock.now.astimezone(JST).strftime("%Y-%m-%d %H:%M %Z")))
        say("world", "街区 %s；默认新世界（本轮模板改动已生成）"
            % demo.data.get("world_id"))

        # -- 1. 家里：从正常候选里选一件家具边的小事 ------------------------------
        home = demo.round("home_small_thing")
        stay = dict(home.get("stay") or {})
        chosen_target = str(stay.get("target_ref") or "")
        check(home.get("decision") == "START" and stay.get("phase") == "START",
              "家中家具小事没有正式开始", home)
        check(any(chosen_target.startswith(prefix) for prefix in HOME_SMALL_THINGS),
              "选中的不是本轮新增的家具候选", {"chosen": chosen_target,
                                               "offered": _candidate_targets(demo.cycle)})
        say("home", "选择了 %s（%s 秒），Activity 已 ACTIVE，停点由 LR-5 记录"
            % (chosen_target, stay.get("planned_seconds")))
        demo.clock.advance(int(stay.get("planned_seconds") or 300), because="家具边停留")
        ended = demo.round("continue")
        end_stay = dict(ended.get("stay") or {})
        check(end_stay.get("phase") in ("END", "ONGOING"), "停留没有到达结束段", ended)
        if end_stay.get("phase") == "ONGOING":
            demo.clock.advance(int(end_stay.get("remaining_seconds") or 1),
                               because="家具边停留补足")
            ended = demo.round("continue")
            end_stay = dict(ended.get("stay") or {})
        stay_activity = str(ended.get("activity_ref") or home.get("activity_ref") or "")
        check(_activity(demo.runtime, stay_activity).get("status") == "COMPLETED",
              "家具小事没有正常完成", {"activity": _activity(demo.runtime, stay_activity)})
        say("home", "完成：Activity %s 由 LR-5 的声明时长完成条件正常 COMPLETED"
            % stay_activity)

        # -- 1b. 家里一件真实的物件动作：拿起再放回，验证 substrate 真的变了 ----------
        home_objects = [row for row in (demo.world_prepared.get("takeable") or [])
                        if str(row.get("location_id")) == "home"]
        check(bool(home_objects), "默认新世界的家里没有可拿物件（室内准备没跑）",
              demo.world_prepared)
        home_object = str(home_objects[0].get("object_id"))
        picked = demo.round("home_object")
        daily = dict(picked.get("daily_action") or {})
        check(daily.get("verb") == "PICK_UP" and daily.get("satisfied") is True,
              "拿起笔记本没有走正式物件端口或没有真实生效", picked)
        in_hand = _substrate_placements(demo.world_home).get(home_object) or {}
        check(str(in_hand.get("kind")) == "hand",
              "PICK_UP 后笔记本不在手里", {"object": home_object, "placement": in_hand})
        say("home-object", "拿起 %s（%s）：Owner 复核 satisfied=%s，substrate 里已在手上"
            % (home_object, home_objects[0].get("name"), daily.get("satisfied")))
        put_back = demo.round("home_object_back")
        daily_back = dict(put_back.get("daily_action") or {})
        check(daily_back.get("verb") == "PLACE" and daily_back.get("satisfied") is True,
              "放下笔记本没有走正式物件端口或没有真实生效", put_back)
        after_place = _substrate_placements(demo.world_home).get(home_object) or {}
        check(str(after_place.get("kind")) == "surface"
              and str(after_place.get("surface_id")) == "bed_surface",
              "PLACE 没有真的放到 owner 的 bed_surface 上",
              {"object": home_object, "placement": after_place,
               "chosen": daily_back.get("target_ref")})
        place_activity = str(put_back.get("activity_ref") or "")
        check(_activity(demo.runtime, place_activity).get("status") == "COMPLETED",
              "surface PLACE 后 Activity 没有正常完成",
              {"activity": _activity(demo.runtime, place_activity)})
        check(_foreground(demo.runtime) is None,
              "surface PLACE 完成后前台没有被释放",
              {"foreground": _foreground(demo.runtime)})
        say("home-object", "放回 %s：kind=%s %s，owner 复核 Activity COMPLETED 且前台已释放"
            % (home_object, after_place.get("kind"), after_place.get("surface_id")))

        # -- 2. 出门：家 → 街口 → 书店（正式旅程，逐段结算） ----------------------
        for leg, place in (("go_to", "street_1"), ("go_to", "bookstore")):
            demo.cognition.intent = leg
            demo.cognition.place_wish = place
            out = demo.round(leg)
            chosen = ""
            read = demo.cognition.reads[-1] if demo.cognition.reads else {}
            chosen = str(read.get("chose") or "")
            check(chosen.startswith("place:"), "本轮没有可走的正式目的地", {
                "read": read, "outcome": out.get("outcome")})
            check(out.get("world_verb") == "MOVE" and out.get("outcome") == "IN_TRANSIT",
                  "出门没有走正式 MOVE", out)
            if place == "bookstore":
                # 本轮观察发生在街口：白天的移动候选必须能走到书店/咖啡馆/花店与营业中的
                # 经济店（唯一能真的买东西的地方），并保留相邻街道（海岸在 street_3 后面），
                # 且整轮不超过 AG-1 的 16 条。保留只决定名额内留谁，不增加候选总数。
                offered_targets = sorted({
                    str(ref) for item in demo.cognition.offered[-1]["candidates"]
                    for ref in (item.get("targets") or [])
                    if str(ref).startswith("place:")})
                for wanted in ("place:bookstore", "place:cafe", "place:flower_shop",
                               "place:home_goods_store"):
                    check(wanted in offered_targets,
                          "街口白天的移动候选缺少 %s" % wanted,
                          {"offered_places": offered_targets})
                check(any(target in offered_targets
                          for target in ("place:street_2", "place:street_3")),
                      "街口候选没有保留相邻街道，海岸会被永久截没",
                      {"offered_places": offered_targets})
                check(len(demo.cognition.offered[-1]["candidates"]) <= 16,
                      "一轮候选超过 AG-1 的 16 条上限",
                      demo.cognition.offered[-1])
                say("district", "街口白天 MOVE 候选：%s（相邻街道保留，共 %d 条候选）"
                    % (offered_targets, len(demo.cognition.offered[-1]["candidates"])))
            # 出发先在业务钟未到路线 due 时返回 IN_TRANSIT；随后只按世界包自己声明的
            # 路线耗时把业务钟推到 owner 的到达时刻，再由 owner 自己的结算确认到达。
            settled = _settle_move(demo, out, place=place)
            say("walk", "到达 %s（owner 确认位置；Activity 按到达事实完成）"
                % settled.get("location_id"))

        here = str(demo.runtime.canonical_state().get("foreground_activity_ref") or "")
        check(here == "", "到达书店后前台没有释放", here)
        state = json.loads((demo.world_home / "data" / "world" / "world_state.json")
                           .read_text(encoding="utf-8"))
        check(str((state.get("location") or {}).get("place_id")) == "bookstore",
              "位置不在书店", state.get("location"))
        say("bookstore", "位置=%s；书店营业中的候选来自世界包自己的营业事实"
            % (state.get("location") or {}).get("place_id"))

        # -- 3. 在书店读自己的材料：第 1 页 + 一条私人批注 ------------------------
        first = demo.round("read")
        check(first.get("decision") == "START", "阅读没有正式开始", first)
        reading_activity = str(first.get("activity_ref") or "")
        check(bool(reading_activity), "阅读没有产生 Activity", first)
        plan = dict(first.get("reading") or {})
        check(int(plan.get("page") or 0) == 1, "第一页没有按正式阅读端口推进", plan)
        annotation = dict(plan.get("annotation") or {})
        # 0.7 第三阶段：白天的真实阅读响应返回的是正式协议里的 ``unclear``，经既有纯
        # 规划器与 owner CAS 落进同一个私人 items 版本；不是 demo 直接写文件。
        check(annotation.get("kind") == "unclear" and annotation.get("state") == "APPLIED",
              "白天的疑问没有按正式阅读响应协议落库",
              {"annotation": annotation, "plan": plan})
        check(bool(annotation.get("item_id")), "unclear 没有产生 owner item id", annotation)
        say("read", "第 1/%s 页已取得；停点与一条 unclear 疑问走同一个 owner 版本（批注=%s）"
            % (plan.get("pages"), annotation.get("state")))

        # -- 4. 继续到第 2 页，然后模型无 hint 的 PAUSE ---------------------------
        second = demo.round("continue")
        plan2 = dict(second.get("reading") or {})
        check(int(plan2.get("page") or 0) == 2, "第二页没有推进", plan2)
        paused = demo.round("pause_reading")
        check(paused.get("decision") == "PAUSE", "阅读 PAUSE 没有被采纳", paused)
        paused_activity = _activity(demo.runtime, reading_activity)
        check(paused_activity.get("status") == "PAUSED",
              "阅读 Activity 不是 PAUSED", paused_activity)
        conditions = dict((demo.runtime.waiting_coordinator.condition_store
                           .load_state() or {}).get("conditions") or {})
        reading_conditions = [row for row in conditions.values()
                              if str((row.get("parameters") or {}).get("source") or "")
                              == "READING_SHORT_BREAK"]
        check(len(reading_conditions) == 1,
              "没有绑定唯一的阅读默认短间隔条件", conditions)
        spec = dict(reading_conditions[0].get("parameters") or {})
        check(str(reading_conditions[0].get("condition_kind")) == "TIME_AT_OR_AFTER"
              and bool(spec.get("target_time")),
              "阅读默认条件不是既有 TIME_AT_OR_AFTER", reading_conditions[0])
        say("pause", "无 hint 的阅读 PAUSE 由阅读专用默认条件绑定：%s（%s 秒后到期）"
            % (reading_conditions[0].get("condition_id"), spec.get("seconds")))

        # -- 5. 快进 31 逻辑分钟：owner 维护扫出 OPEN 资格 ------------------------
        demo.clock.advance(31 * 60, because="阅读短间隔到期")
        maintained = demo.cycle.maintain_waiting(observed_at=demo.clock.iso())
        after_conditions = dict((demo.runtime.waiting_coordinator.condition_store
                                 .load_state() or {}).get("conditions") or {})
        satisfied_now = [row for row in after_conditions.values()
                         if str(row.get("condition_id")) == reading_conditions[0].get("condition_id")
                         and str(row.get("status")) == "SATISFIED"]
        due = dict(maintained.get("due_agenda") or {})
        check(bool(satisfied_now) or int(due.get("emitted_eligibilities") or 0) >= 1,
              "LR-4 到期维护没有满足条件",
              {"maintained": maintained, "clock": demo.clock.iso(),
               "conditions": after_conditions})
        check(int(maintained.get("llm_calls") or 0) == 0, "维护花了模型调用", maintained)
        eligibilities = dict((demo.runtime.waiting_coordinator.eligibility_store
                              .load_state() or {}).get("eligibilities") or {})
        open_rows = [row for row in eligibilities.values()
                     if row.get("status") == "OPEN"
                     and str(row.get("activity_id")) == reading_activity]
        check(len(open_rows) == 1, "没有发出这一本书的 OPEN 资格", eligibilities)
        say("maintain", "零模型维护满足条件并发出 OPEN 资格 %s；Activity 仍 PAUSED"
            % open_rows[0].get("eligibility_id"))

        # -- 6. 新鲜 Decision RESUME 同一 Activity，从原 checkpoint 继续 ----------
        demo.cognition.resume_activity = reading_activity
        pre_resume = _activity(demo.runtime, reading_activity)
        resumed = demo.round("resume")
        after = _activity(demo.runtime, reading_activity)
        check(resumed.get("decision") == "RESUME", "没有 RESUME",
              {"out": {k: resumed.get(k) for k in ("outcome", "decision", "detail",
                                                    "error", "reason", "step_path")},
               "offered": demo.cognition.offered[-1],
               "eligibility": {k: open_rows[0].get(k) for k in (
                   "eligibility_id", "status", "activity_id", "activity_revision",
                   "resume_condition_ref", "waiting_episode_ref", "waiting_episode_id",
                   "expires_at", "created_at")},
               "activity": {k: after.get(k) for k in (
                   "status", "revision", "checkpoint_ref", "foreground_released")}})
        check(str(after.get("activity_id")) == reading_activity,
              "RESUME 恢复的不是同一个 Activity", after)
        check(str(after.get("status")) in ("ACTIVE", "COMPLETED"),
              "RESUME 后 Activity 既没有回到 ACTIVE，也没有继续完成", after)
        say("resume", "同一 Activity %s 从 checkpoint=%s 恢复（本轮读到第 %s 页）"
            % (reading_activity, pre_resume.get("checkpoint_ref"),
               (resumed.get("reading") or {}).get("page")))

        # -- 7. 读到最后一段：批注/停点完成，LR-5 页码条件 COMPLETE --------------
        # The resume round may already execute the next reading step (RESUME adoption +
        # CONTINUE in one formal round).  If it did not, one fresh CONTINUE finishes it.
        plan3 = dict(resumed.get("reading") or {})
        if str(after.get("status")) != "COMPLETED":
            last_round = demo.round("continue")
            plan3 = dict(last_round.get("reading") or {})
            after = _activity(demo.runtime, reading_activity)
        check(int(plan3.get("page") or 0) == int(plan3.get("pages") or 0),
              "最后一段不是最后一页", plan3)
        check(after.get("status") == "COMPLETED",
              "最后一页没有让阅读正常 COMPLETED", after)
        say("complete", "最后一页 %s/%s 已读；LR-5 页码完成条件使其 COMPLETED（不是等新版本）"
            % (plan3.get("page"), plan3.get("pages")))

        # -- 8b. 到营业的实体店买东西：正式候选 → Decision → 经济 owner 记账 ------
        # 购买不是世界 bridge 的动词：她先在正式 MOVE 候选里走到店，店里由既有经济
        # owner 读出真正在卖的商品；Decision 选中的是 ``buy:<catalog_item_id>``，
        # 记账、余额与产权都由 ``world_property_economy.purchase_item`` 一次写入。
        for place in ("street_1", "home_goods_store"):
            demo.cognition.place_wish = place
            walked = demo.round("go_to")
            read = demo.cognition.reads[-1] if demo.cognition.reads else {}
            check(str(read.get("chose") or "").startswith("place:"),
                  "没有走到 %s 的正式 MOVE 候选" % place,
                  {"read": read, "outcome": walked.get("outcome")})
            _settle_move(demo, walked, place=place)
        store_state = json.loads((demo.world_home / "data" / "world" / "world_state.json")
                                 .read_text(encoding="utf-8"))
        check(str((store_state.get("location") or {}).get("place_id")) == "home_goods_store",
              "位置不在家居店", store_state.get("location"))

        bought = demo.round("buy")
        purchase = dict(bought.get("purchase") or {})
        check(bought.get("outcome") == "ACTED", "购买没有走通经济 owner", bought)
        check(purchase.get("status") in ("applied", "already_applied"),
              "经济 owner 没有确认购买", purchase)
        check(purchase.get("ownership") == "OWNED", "买下后产权不是 OWNED", purchase)
        purchase_activity = str(bought.get("activity_ref") or "")
        check(_activity(demo.runtime, purchase_activity).get("status") == "COMPLETED",
              "购买 Activity 没有正常结束",
              {"activity": _activity(demo.runtime, purchase_activity),
               "progress_state": bought.get("progress_state"),
               "progress_ingest_error": bought.get("progress_ingest_error"),
               "checkpoint_ref": bought.get("checkpoint_ref")})
        store = next((item for item in (demo.provider.last_catalog or {}).get("opportunities", [])
                      if str(item.get("location_id")) == "home_goods_store"), {})
        store_name = str(store.get("name") or "home_goods_store")
        shelf = [item for item in (demo.cycle.last_world_opportunity_view or [])
                 if str(item.get("target_ref") or "").startswith("buy:")]
        # 候选在 AG-1 面前长什么样：准确商品名、地点、价格/余额和自然时长。
        live = dict(getattr(demo.cycle, "last_live_context", None) or {})
        chosen_ref = str((demo.cognition.reads[-1] or {}).get("chose") or "")
        shown = next((row for row in (live.get("opportunities") or [])
                      if str(row.get("target_ref")) == chosen_ref), {})
        if shown:
            say("store-shelf", "%s（%s 秒）— %s"
                % (shown.get("label"), shown.get("duration_seconds"),
                   shown.get("description")))
        say("store", "%s（%s）营业中，候选商品 %d 件；Decision 选了 %s"
            % (store_name, "home_goods_store", len(shelf),
               purchase.get("catalog_item_id")))
        say("buy", "买了「%s」（%s %s），交易 %s，余额 %s；Activity %s 由 owner 交易 COMPLETED"
            % (purchase.get("item_name"), purchase.get("price"), purchase.get("currency"),
               purchase.get("transaction_id"), purchase.get("current_balance"),
               purchase_activity))

        economy_path = demo.world_home / "state" / "world_property_economy.json"
        economy_doc = json.loads(economy_path.read_text(encoding="utf-8"))
        transactions = [row for row in economy_doc.get("transactions") or []
                        if row.get("purchase_intent_id") == purchase.get("purchase_intent_id")]
        owned = [row for row in economy_doc.get("items") or []
                 if row.get("item_instance_id") == purchase.get("item_instance_id")]
        check(len(transactions) == 1 and len(owned) == 1,
              "经济账本不是一笔购买 + 一个 OWNED 实例",
              {"transactions": transactions, "items": owned})
        check(str(owned[0].get("disposition")) == "OWNED"
              and str(owned[0].get("catalog_item_id")) == str(purchase.get("catalog_item_id")),
              "买下的实例不是这件商品的 OWNED 产权", owned[0])
        say("ledger", "经济 owner 账本：1 笔 DEBIT %s 与 1 个 OWNED 实例，位置 %s"
            % (transactions[0].get("amount"), owned[0].get("current_location")))

        # 同一个 operation key 的重复执行由既有幂等接口直接复核：不二扣、不产生第二条流水。
        import world_property_economy as economy
        replay = economy.purchase_item(
            catalog_item_id=str(purchase.get("catalog_item_id")),
            purchase_intent_id=str(purchase.get("purchase_intent_id")),
            main_self_verified=True, hermes_home=str(demo.world_home),
            environment=demo.env, now=demo.clock.now)
        replay_doc = json.loads(economy_path.read_text(encoding="utf-8"))
        check(replay.get("status") == "already_applied", "同 key 重放不是 already_applied", replay)
        check(len(replay_doc.get("transactions") or []) == len(economy_doc.get("transactions") or [])
              and len(replay_doc.get("items") or []) == len(economy_doc.get("items") or []),
              "同 key 重放又扣了一次钱或又写了一个实例",
              {"transactions_before": len(economy_doc.get("transactions") or []),
               "transactions_after": len(replay_doc.get("transactions") or []),
               "items_before": len(economy_doc.get("items") or []),
               "items_after": len(replay_doc.get("items") or [])})
        say("buy-replay", "同 operation key 重放 status=%s，流水/实例数不变（未二次扣款）"
            % replay.get("status"))

        # -- 8. 默认街区的四个地点：实际生成、白天营业、闭店无候选 ----------------
        catalog = dict(getattr(demo.provider, "last_catalog", None) or {})
        by_location = {str(item.get("location_id")): item
                       for item in (catalog.get("opportunities") or [])}
        from components.worldgen.loader import load as load_package
        package_dir = Path(str(demo.data.get("world_package") or ""))
        package_locations = set(load_package(package_dir).locations)
        rules = json.loads((package_dir / "rules.json").read_text(encoding="utf-8"))
        location_rules = dict(rules.get("location_rules") or {})
        for name in ("bookstore", "cafe", "flower_shop", "beach"):
            check(name in package_locations, "默认新街区没有生成 %s" % name,
                  sorted(package_locations))
            rule = dict(location_rules.get(name) or {})
            open_hour = int(rule.get("open_hour") or 0)
            close_hour = int(rule.get("close_hour") or 0)
            check(open_hour <= 11 < close_hour, "%s 白天 11:00 不是营业中" % name, rule)
            if close_hour > open_hour:
                check(not (open_hour <= 23 < close_hour),
                      "%s 深夜 23:00 仍然营业，闭店事实不对" % name, rule)
            descriptors = wmos._stay_descriptors(
                rule.get("affordances"), observed_at=demo.clock.iso(),
                location_id=name)
            check(bool(descriptors), "%s 白天没有任何可执行的生活候选" % name, rule)
        say("district", "默认新街区实际生成：%s" % sorted(package_locations))
        say("district", "本轮 catalog 地点（最近 %d 个以内）：%s"
            % (12, sorted(by_location)))
        for name in ("cafe", "beach", "bookstore", "flower_shop"):
            entry = by_location.get(name)
            if entry is None:
                why = ("在街区包里存在，但这轮最近地点上限没有把它列进来"
                       if name in package_locations else "本街区包没有生成这个地点")
                say("district", "%s：%s（如实缺席，不冒充营业）" % (name, why))
                continue
            availability = str((entry.get("availability") or {}).get("status") or "")
            titles = [(item.get("affordance_id"), item.get("title"),
                       item.get("suggested_seconds"))
                      for item in (entry.get("affordances") or [])]
            say("district", "%s：%s，affordances=%s" % (name, availability, titles))
        say("district", "只有 home_goods_store 挂着经济商品（库存/价格/余额来自经济 owner）；"
                        "花店、咖啡馆、书店只逛/坐/读，不把「看看」冒充成买到")

        # -- 10. 正式回家：买下的灯经 owner tick 落地，再在正确区域开灯、挪到另一区域 ------
        # 交付不是新的 writer：owner 自己的 tick 把已购、已到家、未摆放的实例交给既有
        # ``world_phone_shopping.materialize_owned_item`` 落到 home/bedroom。这里只触发一次
        # 真实 owner tick，随后只读盘上事实。
        demo.gateway.service.tick()
        lamp_id = str(purchase.get("item_instance_id") or "")
        check(bool(lamp_id), "购买回执没有实例 id", purchase)
        landed = _substrate_placements(demo.world_home).get(lamp_id) or {}
        check(str(landed.get("kind")) == "room"
              and str(landed.get("location_id")) == "home"
              and str(landed.get("area_id")) == "bedroom",
              "owner tick 没有把新买的灯落在家里的卧室",
              {"lamp": lamp_id, "placement": landed})
        say("home-return", "owner tick 落地：新买的灯实例 %s 已在 %s/%s（substrate placement）"
            % (lamp_id, landed.get("location_id"), landed.get("area_id")))

        # 正式旅程回家：store → street_1 → home。street_1 的移动候选必须保留 home，否则
        # "走回去用自己买的东西" 永远到不了。
        for place in ("street_1", "home"):
            demo.cognition.place_wish = place
            walked = demo.round("go_to")
            read = demo.cognition.reads[-1] if demo.cognition.reads else {}
            check(str(read.get("chose") or "").startswith("place:"),
                  "没有走到 %s 的正式 MOVE 候选" % place,
                  {"read": read, "outcome": walked.get("outcome")})
            if place == "home":
                # This round's observation was taken at street_1; the MOVE candidates now
                # on offer must still contain the home the owner really offers.
                street_places = sorted({
                    str(ref) for item in demo.cognition.offered[-1]["candidates"]
                    for ref in (item.get("targets") or []) if str(ref).startswith("place:")})
                check("place:home" in street_places,
                      "街口的移动候选把 home 挤出名额，买的东西走不回去",
                      {"offered_places": street_places})
                say("home-return", "街口的移动候选保留了 home：%s" % street_places)
            _settle_move(demo, walked, place=place)

        home_state = json.loads((demo.world_home / "data" / "world" / "world_state.json")
                                .read_text(encoding="utf-8"))
        home_area = str((home_state.get("location") or {}).get("area_id") or "")
        check(str((home_state.get("location") or {}).get("place_id")) == "home"
              and bool(home_area), "没有真正回到家", home_state.get("location"))
        say("home-return", "回到 home（当前区域 %s）；灯此刻在 bedroom" % home_area)

        # 10a. 正式 ARRANGE：把这件自己买下的家具挪到她此刻所在的、owner 声明过的区域。
        demo.cognition.arrange_area_wish = home_area
        arranged = demo.round("arrange_lamp")
        arrange = dict(arranged.get("daily_action") or {})
        check(arrange.get("verb") == "ARRANGE" and arrange.get("satisfied") is True,
              "ARRANGE 没有走正式家具端口或没有真实生效", arranged)
        moved = _substrate_placements(demo.world_home).get(lamp_id) or {}
        check(str(moved.get("kind")) == "room" and str(moved.get("area_id")) == home_area,
              "ARRANGE 后灯没有真的挪到目标区域",
              {"lamp": lamp_id, "placement": moved, "target_area": home_area,
               "chosen": arrange.get("target_ref")})
        arrange_activity = str(arranged.get("activity_ref") or "")
        check(_activity(demo.runtime, arrange_activity).get("status") == "COMPLETED",
              "ARRANGE 后 Activity 没有正常完成",
              {"activity": _activity(demo.runtime, arrange_activity)})
        check(_foreground(demo.runtime) is None, "ARRANGE 完成后前台没有释放")
        say("arrange", "ARRANGE：灯挪到 %s/%s（substrate 真变），owner 复核 satisfied，Activity COMPLETED"
            % (moved.get("location_id"), moved.get("area_id")))

        # 10b. 正式 TOGGLE off：UNKNOWN → off 是这盏灯第一次被写下的绝对电源事实。
        # 每次 TOGGLE 都是独立的 Decision / AR-0 / owner 回执 → 自己的 Activity COMPLETED；
        # 完成证据按「本次 execution 的 progress evidence」归属，而不是只按
        # ``world_fact:fixture:<id>:<state>`` 这个值——所以同一盏灯后面还能再 off/on。
        lamp_unrecorded = _substrate_fixtures(demo.world_home).get(lamp_id) or {}
        check(lamp_unrecorded.get("power") is None,
              "灯在正式 TOGGLE 前已经有电源记录，第一次 off 不再是第一次事实",
              {"lamp": lamp_id, "fixture": lamp_unrecorded})
        toggle_evidence: list[str] = []

        def _toggle(expected_state: str, *, why: str) -> dict:
            out = demo.round_until("toggle_lamp")
            action = dict(out.get("daily_action") or {})
            check(action.get("verb") == "TOGGLE_FIXTURE" and action.get("satisfied") is True
                  and str((action.get("params") or {}).get("state")) == expected_state,
                  "%s没有走正式 TOGGLE_FIXTURE %s" % (why, expected_state),
                  {"round": out, "offered": demo.cognition.offered[-1],
                   "read": demo.cognition.reads[-1] if demo.cognition.reads else None})
            fixture = _substrate_fixtures(demo.world_home).get(lamp_id) or {}
            check(str(fixture.get("power")) == expected_state,
                  "%s后 lamp 电源不是 %s" % (why, expected_state), fixture)
            activity = _activity(demo.runtime, str(out.get("activity_ref") or ""))
            check(activity.get("status") == "COMPLETED",
                  "%s的 Activity 没有正常 COMPLETED" % why, activity)
            check(_foreground(demo.runtime) is None, "%s后前台没有释放" % why,
                  {"foreground": _foreground(demo.runtime)})
            ref = str(activity.get("completion_evidence_ref") or "")
            check(bool(ref) and ref not in toggle_evidence,
                  "%s的完成证据缺失或复用了别的 execution" % why,
                  {"evidence": ref, "seen": toggle_evidence, "activity": activity})
            toggle_evidence.append(ref)
            say("toggle", "%s：正式 TOGGLE_FIXTURE → power=%s，Activity COMPLETED，"
                         "完成证据 %s（第 %d 次独立 execution）"
                % (why, expected_state, ref, len(toggle_evidence)))
            return out

        _toggle("off", why="10b 买回后正式关灯")
        _toggle("on", why="10c 正式开灯")

        say("home-use", "完整链：店买 bedside_lamp → owner tick 落地 home/bedroom → 正式回家 → "
                        "ARRANGE 到 %s → 正式 off → 正式 on（off/on 两个绝对状态都真实写入，"
                        "各自独立 Activity）" % home_area)

        # -- 11. 傍晚：白天真实的疑问，经正常候选写成自己的小册子 --------------------
        # 场景事实先核对：业务钟推进到 20:00（不真实等待），她确实在家，灯确实开着。
        evening_local = demo.clock.now.astimezone(JST).replace(
            hour=20, minute=0, second=0, microsecond=0)
        if evening_local <= demo.clock.now.astimezone(JST):
            evening_local = evening_local + timedelta(days=1)
        demo.clock.advance(
            int((evening_local - demo.clock.now.astimezone(JST)).total_seconds()),
            because="傍晚回到家")
        evening_state = json.loads(
            (demo.world_home / "data" / "world" / "world_state.json").read_text(
                encoding="utf-8"))
        check(str((evening_state.get("location") or {}).get("place_id")) == "home",
              "傍晚不在家，场景事实不对", evening_state.get("location"))
        evening_lamp = _substrate_fixtures(demo.world_home).get(lamp_id) or {}
        check(str(evening_lamp.get("power")) == "on",
              "傍晚灯的电源不是 on，场景事实不对", evening_lamp)
        say("evening", "业务钟 %s（JST %s）：在家；灯 power=on（刚刚由正式 TOGGLE on 写下的"
                       "真实场景事实，不是写作触发器）"
            % (demo.clock.now.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
               demo.clock.now.isoformat()))

        # 白天那条 unclear 是正式阅读响应写进私人 items owner 的；这里只经 owner 的
        # 既有读取缝确认它还在，并确认正常 project_options 用它生成了 QUESTION 提案。
        questions = []
        if callable(getattr(demo.cycle, "project_questions_provider", None)):
            questions = [dict(row) for row in
                         (demo.cycle.project_questions_provider() or [])]
        check(any(DAY_QUESTION in str(row.get("note") or "") for row in questions),
              "白天的疑问没有留在私人 items owner 里", {"count": len(questions)})

        before_heads = {
            "work": demo.port.resume_point(str(demo.data["artifact_id"])).head_version_id,
            "reading": demo.port.resume_point(
                str(demo.data["reading_artifact_id"])).head_version_id,
            "items": demo.port.resume_point(
                str(demo.data["private_items_artifact_id"])).head_version_id,
        }
        created_round = demo.round("write_project")
        created = dict(created_round.get("project_created") or {})
        option = next((dict(row) for row in demo.cycle.last_project_options
                       if str(row.get("option_ref")) == str(created.get("option_ref"))), {})
        safe_option = {key: option.get(key) for key in
                       ("option_ref", "kind", "basis_ref", "source_refs")}
        check(created_round.get("outcome") == "ACTED" and bool(created.get("artifact_id")),
              "正常候选里的询问没有由 owner 创建成作品",
              {"outcome": created_round.get("outcome"),
               "decision": created_round.get("decision"),
               "adoption_status": created_round.get("adoption_status"),
               "option": safe_option})
        check(str(option.get("kind")) == "QUESTION_FROM_MATERIAL",
              "主正链提案不是 QUESTION_FROM_MATERIAL", option)
        check(str(option.get("basis_ref") or "").startswith("question:"),
              "提案没有绑定她写下的那条疑问", option)
        project_artifact = str(created.get("artifact_id"))
        project_bare = _bare(project_artifact)
        first_head = demo.port.resume_point(project_artifact)
        check(first_head.artifact_id == project_artifact
              and int(first_head.version_number or 0) >= 2,
              "owner 创建后没有通过 AR-0 写出第一段版本",
              {"artifact_id": first_head.artifact_id,
               "version_number": first_head.version_number,
               "receipt": created_round.get("step_receipt")})
        check(len(demo.cognition.offered[-1]["candidates"]) <= 16,
              "傍晚提案轮候选超过 AG-1 的 16 条上限",
              demo.cognition.offered[-1])
        for name, ref in (("work", demo.data["artifact_id"]),
                          ("reading", demo.data["reading_artifact_id"]),
                          ("items", demo.data["private_items_artifact_id"])):
            check(demo.port.resume_point(str(ref)).head_version_id == before_heads[name],
                  "创建新作品时误写了别的作品/材料（%s）" % name,
                  {"before": before_heads[name]})
        say("project", "owner 创建 %s（kind=%s，basis=%s，来源 refs=%s），AR-0 写出 v%s"
            % (project_artifact, option.get("kind"), option.get("basis_ref"),
               (option.get("source_refs") or [])[:2], first_head.version_number))

        # 后续轮次对**同一个** artifact 正式 CONTINUE：真实 ArtifactContinuationSource
        # 识别它可续，Decision 精确选中，ModelContinuationWriter 用脚本 transport 产生
        # 一段内容，经 AR-0 与 Supply owner CAS 成新版本。
        demo.cognition.project_bare = project_bare
        continued_round = demo.round("continue_project")
        second_head = demo.port.resume_point(project_artifact)
        check(continued_round.get("decision") == "CONTINUE",
              "后续轮次没有 CONTINUE 刚创建的同一 artifact",
              {"outcome": continued_round.get("outcome"),
               "decision": continued_round.get("decision")})
        check(second_head.artifact_id == project_artifact,
              "CONTINUE 落到了另一个 artifact",
              {"artifact_id": second_head.artifact_id,
               "version_number": second_head.version_number})
        check(int(second_head.version_number or 0) > int(first_head.version_number or 0),
              "CONTINUE 没有产生新版本",
              {"before": first_head.version_number, "after": second_head.version_number,
               "receipt": continued_round.get("step_receipt")})
        check(int(getattr(demo.writer, "call_count", 0)) >= 1,
              "续写没有经过 ModelContinuationWriter")
        for name, ref in (("work", demo.data["artifact_id"]),
                          ("reading", demo.data["reading_artifact_id"]),
                          ("items", demo.data["private_items_artifact_id"])):
            check(demo.port.resume_point(str(ref)).head_version_id == before_heads[name],
                  "续写新作品时误写了别的作品/材料（%s）" % name,
                  {"before": before_heads[name]})
        say("continue", "同一 artifact %s：v%s → v%s（writer calls=%d，原白天材料/其它作品未变）"
            % (project_artifact, first_head.version_number, second_head.version_number,
               demo.writer.call_count))

        # -- 14. 第五阶段：新作品分享/撤回/换版复核 → 普通消息 → typed 共处 → 恢复续写 ------
        # 此刻小册子 Activity 仍是本轮 CONTINUE 之后的 ACTIVE 前台。下面每个动作都只经由既有
        # owner（sharing / Supply / LR-2/LR-4/LR-5 / AG-1/AG-2）与既有本地入站，不替换 sharing
        # service、不改两个 anchor、不外发；普通消息与共处事件是具名外部测试刺激，不冒充她的
        # 自然生活，也不把维护脚本的动作说成主体自发行为。
        sharing_service = demo.cycle.sharing_service
        check(sharing_service is not None, "默认装配没有接上 sharing owner")
        check(bool(demo.cycle.contact_inbox) and bool(demo.cycle.social_contacts)
              and bool(demo.cycle.co_presence), "默认装配没有接上本地联系/共处端口")
        booklet_activity = str(continued_round.get("activity_ref") or "")
        check(bool(booklet_activity), "小册子没有 Activity，不能作为共处前台", continued_round)
        check(_activity(demo.runtime, booklet_activity).get("status") == "ACTIVE",
              "插入分享/共处前小册子不是 ACTIVE",
              _activity(demo.runtime, booklet_activity))
        source_head = demo.port.resume_point(project_artifact)
        source_version = str(source_head.head_version_id or "")
        check(bool(source_version), "小册子没有 head 版本")

        # (a) 新前台来源：ACTIVE 前台实际绑定的 artifact 必须进入有界来源（limit=3、去重、
        # private items 不进入）。这里只读 cycle 的 sharing 块，看不到也不能拿到正文。
        block = demo.cycle.sharing_decision_context()
        offer_sources = [dict(row) for row in (block.get("sources") or [])]
        offered_ids = [str(row.get("artifact_id")) for row in offer_sources]
        check(len(offer_sources) <= 3, "分享来源超过 3 条", offer_sources)
        check(project_artifact in offered_ids,
              "ACTIVE 前台的小册子没有进入分享来源", offered_ids)
        private_bare = str(demo.data["private_items_artifact_id"]).replace("artifact:", "")
        check(private_bare not in [text.replace("artifact:", "") for text in offered_ids],
              "private items 被当成可分享来源", offered_ids)
        check(len(set(offered_ids)) == len(offered_ids), "分享来源没有去重", offered_ids)
        say("share-source", "ACTIVE 前台来源 %s（共 %d 条，limit=3，private items 已排除）"
            % (project_artifact, len(offer_sources)))

        # (b1) prepare → DRAFT（显式短文本；来源是同一件小册子，不是整篇正文）。
        prepared = demo.share_round({"op": "prepare", "source_bare": project_bare,
                                     "share_text": SHARE_TEXT, "title": "晚间的这一小段"})
        share = dict(prepared.get("sharing") or {})
        draft_id = str(share.get("share_id") or "")
        check(prepared.get("outcome") == "SHARE_ACTED" and share.get("ok") is True
              and str(share.get("state")) == "DRAFT" and bool(draft_id),
              "正式 Decision 的 prepare 没有落成 DRAFT",
              {"outcome": prepared.get("outcome"), "sharing": share,
               "choice": demo.cognition.sharing_choice})
        check((demo.cognition.sharing_choice or {}).get("offered") is True,
              "脚本替身没有从本轮真实来源里回显小册子", demo.cognition.sharing_choice)
        say("share-draft", "prepare → %s（state=DRAFT，来源 %s @ %s）"
            % (draft_id, project_artifact, source_version))

        # (b2) defer（明确条件）→ DEFERRED。
        deferred_out = demo.share_round({"op": "defer", "share_id": draft_id,
                                         "defer_condition": "等她把这页写完再决定"})
        deferred = dict(deferred_out.get("sharing") or {})
        check(deferred.get("ok") is True and str(deferred.get("state")) == "DEFERRED",
              "defer 没有落成 DEFERRED",
              {"outcome": deferred_out.get("outcome"), "sharing": deferred})
        say("share-defer", "%s → DEFERRED（条件由本轮 Decision 明确给出）" % draft_id)

        # (b3) 真实 operator CLI 签发**精确**披露许可，然后 send_local → LOCAL_DELIVERED。
        permit = demo.issue_disclosure_permit(project_artifact, source_version,
                                              "v07:stage5:disclose:1")
        check(permit.get("returncode") == 0 and str(permit.get("status")) == "ALLOW"
              and bool(permit.get("grant_id")),
              "真实 operator CLI 没有签发精确披露许可", permit)
        say("share-permit", "operator CLI 签发披露许可 %s（source %s @ %s）"
            % (permit.get("grant_id"), project_artifact, source_version))
        inbox_before = _share_inbox(sharing_service)
        sent_out = demo.share_round({"op": "send_local", "share_id": draft_id})
        sent = dict(sent_out.get("sharing") or {})
        receipt = dict(sent.get("receipt") or {})
        inbox_after = _share_inbox(sharing_service)
        check(str(sent.get("state")) == "LOCAL_DELIVERED" and bool(receipt.get("delivery_id")),
              "send_local 没有落成 LOCAL_DELIVERED + receipt",
              {"outcome": sent_out.get("outcome"), "sharing": sent})
        check(inbox_after["count"] == (inbox_before["count"] or 0) + 1,
              "本地 inbox 没有 +1", {"before": inbox_before, "after": inbox_after})
        check(receipt.get("local_only") is True
              and receipt.get("external_channel_sent") is False
              and receipt.get("read_by_audience") is False
              and receipt.get("understood") is False,
              "receipt 冒充了外部发送/已读/理解", receipt)
        say("share-deliver", "%s → LOCAL_DELIVERED；inbox %s→%s，receipt %s"
                             "（local_only，无外发/已读/理解）"
            % (draft_id, inbox_before["count"], inbox_after["count"],
               receipt.get("delivery_id")))

        # 重复发送：owner 的读侧直接按已投递拒绝，同一 receipt，不产生第二条投递。
        duplicate = sharing_service.apply_action(
            share_id=draft_id, action="send_local",
            expected_head=str(sent.get("head_version_id") or ""))
        check(duplicate.get("ok") is False
              and str(duplicate.get("reason_code")) == "SHARE_ALREADY_DELIVERED",
              "重复发送没有被 owner 按已投递拒绝", duplicate)
        check(_share_inbox(sharing_service)["count"] == inbox_after["count"],
              "重复发送又投了一次", _share_inbox(sharing_service))
        say("share-idem", "同草稿再 send → %s；同 receipt，inbox 仍 %s"
            % (duplicate.get("reason_code"), inbox_after["count"]))

        # (b4) 另一份不同意图的草稿：prepare → drop → DROPPED；正式 Decision 不再 offer 它，
        # owner 自己也拒绝发送。已投递的那一份不受 drop 影响（drop 不是撤回已送内容）。
        drop_prep = demo.share_round({"op": "prepare", "source_bare": project_bare,
                                      "share_text": DROP_SHARE_TEXT,
                                      "title": "另一个想说的意思"})
        drop_draft = str((drop_prep.get("sharing") or {}).get("share_id") or "")
        check(bool(drop_draft), "第二份草稿没有建立", drop_prep)
        drop_out = demo.share_round({"op": "drop", "share_id": drop_draft,
                                     "reason": "先不说了"})
        dropped = dict(drop_out.get("sharing") or {})
        check(dropped.get("ok") is True and str(dropped.get("state")) == "DROPPED",
              "drop 没有落成 DROPPED",
              {"outcome": drop_out.get("outcome"), "sharing": dropped})
        blocked = demo.share_round({"op": "send_local", "share_id": drop_draft})
        choice = dict(demo.cognition.sharing_choice or {})
        check(choice.get("offered") is False,
              "DROPPED 草稿仍被当成可发草稿", {"choice": choice})
        read_drop = dict((sharing_service.read_share(drop_draft) or {}).get("data") or {})
        check(str((read_drop.get("share") or {}).get("state")) == "DROPPED",
              "直接读回不是 DROPPED", read_drop.get("share"))
        owner_drop = sharing_service.apply_action(
            share_id=drop_draft, action="send_local",
            expected_head=str(dropped.get("head_version_id") or ""))
        check(owner_drop.get("ok") is False
              and str(owner_drop.get("reason_code")) == "SHARE_TERMINAL_STATE",
              "owner 没有按终态拒绝 dropped 发送", owner_drop)
        check(_share_inbox(sharing_service)["count"] == inbox_after["count"],
              "drop 后发送让 inbox 增加")
        still_delivered = dict((sharing_service.read_share(draft_id) or {}).get("data") or {})
        check(str((still_delivered.get("share") or {}).get("state")) == "LOCAL_DELIVERED",
              "drop 另一份草稿把已投递内容撤回了", still_delivered.get("share"))
        say("share-drop", "%s → DROPPED；正式 Decision 不再 offer，owner 直接复核 %s；"
                          "已投递的 %s 仍 LOCAL_DELIVERED，inbox 仍 %s"
            % (drop_draft, owner_drop.get("reason_code"), draft_id,
               _share_inbox(sharing_service)["count"]))

        # (b5) 最短换版复核：草稿形成后她继续写同一件作品，发送前 owner 的来源检查必须拒绝。
        change_prep = demo.share_round({"op": "prepare", "source_bare": project_bare,
                                        "share_text": CHANGE_SHARE_TEXT,
                                        "title": "等写完再说的一小段"})
        change_draft = str((change_prep.get("sharing") or {}).get("share_id") or "")
        check(bool(change_draft), "换版前草稿没有建立", change_prep)
        demo.cognition.project_bare = project_bare
        advanced = demo.round_until("continue_project")
        moved_head = demo.port.resume_point(project_artifact)
        check(str(advanced.get("decision")) == "CONTINUE"
              and moved_head.head_version_id != source_version,
              "同一作品没有真的换版",
              {"decision": advanced.get("decision"), "before": source_version,
               "after": moved_head.head_version_id,
               "read": demo.cognition.reads[-1] if demo.cognition.reads else None,
               "offer": demo.cognition.offered[-1] if demo.cognition.offered else None})
        inbox_before_stale = _share_inbox(sharing_service)
        stale_out = demo.share_round({"op": "send_local", "share_id": change_draft})
        stale = dict(stale_out.get("sharing") or {})
        check(stale.get("ok") is False and str(stale.get("status")) == "stale_source",
              "来源换版后 send 没有被复核拒绝",
              {"outcome": stale_out.get("outcome"), "sharing": stale})
        check(_share_inbox(sharing_service)["count"] == inbox_before_stale["count"],
              "来源换版的拒绝仍然投递了")
        say("share-stale", "换版 %s→%s 后 send 被既有来源复核拒绝（%s），inbox 未增"
            % (source_version, moved_head.head_version_id,
               stale.get("reason_code") or stale.get("status")))

        # (c1) 普通消息：既有本地入站投递 → 实际 communication availability 读面；OBSERVATION_ONLY，
        # 私人 Activity 的 status/revision/checkpoint/前台都不动。零产品模型：本演示显式停用普通
        # 消息的模型回复，只验证投递与可用性读面（回复本身不是本单要证明的东西）。
        contacts = demo.cycle.social_contacts
        if getattr(contacts, "responder", None) is not None:
            contacts.responder = None
            say("message", "显式停用普通消息的模型回复（本演示零产品模型请求）")
        before_private = dict(_activity(demo.runtime, booklet_activity) or {})
        before_foreground = _foreground(demo.runtime)
        message = demo.cycle.contact_inbox.submit({
            "event_kind": "LOCAL_MESSAGE",
            "text": "路过时看见窗里还亮着灯。",
            "source_ref": "channel:v07-stage5",
            "occurred_at": demo.clock.iso(),
            "operation_key": "v07:stage5:message:1",
        })
        check(message.get("ok") is True, "普通消息没有进入既有本地入站", message)
        message_tick = contacts.tick(now=demo.clock.iso())
        observed = next((dict(row) for row in (message_tick.get("processed") or [])
                         if str(row.get("kind")) == "LOCAL_MESSAGE"), {})
        check(str(observed.get("effect")) == "OBSERVATION_ONLY"
              and observed.get("pause_requested") is False
              and observed.get("activity_started") is False,
              "普通消息没有被当成 OBSERVATION_ONLY", observed)
        check(str(observed.get("communication_availability") or "")
              in ("OPEN", "LIMITED", "BOUNDARY_DEFERRED"),
              "没有读到实际 communication availability",
              observed.get("communication_availability"))
        after_private = dict(_activity(demo.runtime, booklet_activity) or {})
        check(after_private.get("status") == before_private.get("status") == "ACTIVE"
              and after_private.get("revision") == before_private.get("revision")
              and after_private.get("checkpoint_ref") == before_private.get("checkpoint_ref"),
              "普通消息改动了私人 Activity 的 status/revision/checkpoint",
              {"before": {key: before_private.get(key) for key in
                          ("status", "revision", "checkpoint_ref")},
               "after": {key: after_private.get(key) for key in
                         ("status", "revision", "checkpoint_ref")}})
        check(_foreground(demo.runtime) == before_foreground == booklet_activity,
              "普通消息改了前台", {"before": before_foreground,
                                   "after": _foreground(demo.runtime)})
        say("message", "普通消息 OBSERVATION_ONLY：availability=%s，no_pause=%s；"
                       "私人 Activity status/revision/checkpoint/前台未变"
            % (observed.get("communication_availability"), observed.get("no_pause")))

        # (c2) typed CO_PRESENCE_INVITATION → 一次正式 Decision START → AG-2 交接把原小册子
        # WAITING（wait_dep:co_presence-*，释放前台）并启动共同 Activity。没有提前 park，
        # ATOMIC 不会被强行交接；普通消息也没有被升级成邀请。
        invitation_id = "inv:v07:stage5:1"
        invitation = demo.cycle.contact_inbox.submit({
            "event_kind": "CO_PRESENCE_INVITATION",
            "invitation_id": invitation_id,
            "scope": "一起把傍晚这段坐完",
            "source_ref": "channel:v07-stage5",
            "occurred_at": demo.clock.iso(),
            "duration_seconds": 7200,
            "requires_engagement": True,
            "operation_key": "v07:stage5:invite:1",
        })
        check(invitation.get("ok") is True, "typed 邀请没有进入本地入站", invitation)
        demo.cognition.invitation_ref = "co_presence:%s" % invitation_id
        demo.cognition.intent = "accept_invitation"
        invite_tick = contacts.tick(now=demo.clock.iso())
        accepted = next((dict(row) for row in (invite_tick.get("processed") or [])
                         if str(row.get("kind")) == "CO_PRESENCE_INVITATION"), {})
        check(accepted.get("outcome") == "ACCEPTED"
              and accepted.get("activity_started") is True
              and accepted.get("park_applied") is True,
              "typed 邀请没有按正式 Decision 启动共同时间",
              {"processed": accepted, "choice": demo.cognition.reads[-1:]})
        joint_ref = str(accepted.get("joint_activity_ref") or "")
        private_ref = str(accepted.get("private_activity_ref") or "")
        check(private_ref == booklet_activity,
              "被交接的不是原来的小册子 Activity",
              {"private": private_ref, "booklet": booklet_activity})
        private_waiting = dict(_activity(demo.runtime, booklet_activity) or {})
        co_conditions = dict((demo.runtime.waiting_coordinator.condition_store
                              .load_state() or {}).get("conditions") or {})
        waiting_condition = next(
            (row for row in co_conditions.values()
             if str(row.get("condition_id")) == str(
                 private_waiting.get("resume_condition_ref"))), {})
        check(private_waiting.get("status") == "WAITING"
              and str(waiting_condition.get("waiting_on_ref") or "").startswith(
                  "wait_dep:co_presence-"),
              "小册子没有 WAITING 在 wait_dep:co_presence-* 上",
              {"activity": {key: private_waiting.get(key) for key in
                            ("status", "resume_condition_ref", "checkpoint_ref")},
               "condition": waiting_condition})
        check(_foreground(demo.runtime) == joint_ref,
              "共同时间不是唯一前台",
              {"foreground": _foreground(demo.runtime), "joint": joint_ref})
        check(_activity(demo.runtime, joint_ref).get("status") == "ACTIVE",
              "共同 Activity 不是 ACTIVE", _activity(demo.runtime, joint_ref))
        handoff = dict(accepted.get("handoff") or {})
        check(str(handoff.get("activity_ref")) == booklet_activity
              and str(handoff.get("waiting_on_ref") or "").startswith("wait_dep:co_presence-"),
              "交接不是对原 Activity 的 LR-4 WAITING", handoff)
        say("co-presence", "typed 邀请被正式 Decision 接受：joint=%s ACTIVE；小册子 %s → WAITING"
                           "（%s），原 artifact/checkpoint 保留"
            % (joint_ref, booklet_activity, private_waiting.get("resume_condition_ref")))

        # (c3) 显式 CO_PRESENCE_END → 共同 Activity 由 LR-5 正常 COMPLETED；LR-4 只为原作品开
        # 恢复资格，原作仍 WAITING，绝不直接恢复。
        end_submit = demo.cycle.contact_inbox.submit({
            "event_kind": "CO_PRESENCE_END",
            "invitation_id": invitation_id,
            "occurred_at": demo.clock.iso(),
            "explicit_end": True,
            "reason": "EXPLICIT_END",
            "operation_key": "v07:stage5:end:1",
        })
        check(end_submit.get("ok") is True, "end 事件没有进入本地入站", end_submit)
        end_tick = contacts.tick(now=demo.clock.iso())
        ended = next((dict(row) for row in (end_tick.get("processed") or [])
                      if str(row.get("kind")) == "CO_PRESENCE_END"), {})
        check(ended.get("outcome") == "ENDED" and ended.get("joint_completed") is True,
              "显式 end 没有完成共同 Activity", ended)
        check(bool(ended.get("new_eligibility_refs")),
              "LR-4 没有发出新的恢复资格", ended)
        check(_activity(demo.runtime, joint_ref).get("status") == "COMPLETED",
              "共同 Activity 没有正常 COMPLETED", _activity(demo.runtime, joint_ref))
        still_waiting = dict(_activity(demo.runtime, booklet_activity) or {})
        check(still_waiting.get("status") == "WAITING",
              "end 之后小册子被直接恢复了（不应绕过 fresh Decision）",
              still_waiting.get("status"))
        eligibilities = dict((demo.runtime.waiting_coordinator.eligibility_store
                              .load_state() or {}).get("eligibilities") or {})
        booklet_eligible = [row for row in eligibilities.values()
                            if str(row.get("activity_id")) == booklet_activity
                            and str(row.get("status")) == "OPEN"]
        check(bool(booklet_eligible), "没有为原小册子开出 OPEN 资格", eligibilities)
        say("co-presence-end", "显式 end → joint COMPLETED；LR-4 只开资格 %s；小册子仍 WAITING"
            % [row.get("eligibility_id") for row in booklet_eligible])

        # (c4) 下一轮新鲜 Decision RESUME 同一 Activity/同一作品；再真实续写一版证明可做事。
        demo.cognition.resume_activity = booklet_activity
        resumed = demo.round_until("resume")
        back = dict(_activity(demo.runtime, booklet_activity) or {})
        check(str(resumed.get("decision")) == "RESUME"
              and str(back.get("activity_id")) == booklet_activity
              and str(back.get("status")) in ("ACTIVE", "COMPLETED"),
              "没有由新鲜 Decision RESUME 同一 Activity",
              {"outcome": resumed.get("outcome"), "decision": resumed.get("decision"),
               "activity": {key: back.get(key) for key in
                            ("activity_id", "status", "checkpoint_ref")},
               "offered": demo.cognition.offered[-1]})
        demo.cognition.project_bare = project_bare
        after_resume = demo.port.resume_point(project_artifact)
        if after_resume.head_version_id == moved_head.head_version_id:
            # The resume round only re-entered the Activity; one real CONTINUE proves the same
            # work can still be written after coming back.
            demo.round_until("continue_project")
        recovered = demo.port.resume_point(project_artifact)
        check(recovered.artifact_id == project_artifact
              and recovered.head_version_id != moved_head.head_version_id,
              "恢复后没有真实续写一版",
              {"before": moved_head.head_version_id,
               "after": recovered.head_version_id})
        check(_activity(demo.runtime, booklet_activity).get("status")
              in ("ACTIVE", "COMPLETED"),
              "恢复后原 Activity 不是 ACTIVE/COMPLETED",
              _activity(demo.runtime, booklet_activity).get("status"))
        say("co-presence-resume", "fresh RESUME 同一 Activity %s → %s；续写到 %s（v%s→v%s）"
            % (booklet_activity, back.get("status"), recovered.artifact_id,
               moved_head.version_number, recovered.version_number))

        # -- 12. 「给未来自己的问题」：带 due 提交，到期前不浮现，到期后只读浮现 ----
        # 先把刚写的小册子正常放下（真实 ABANDON，不是伪造终态），腾出前台；这一轮
        # 之所以会被真正评估，是因为上一轮 CONTINUE 已经真实改动了作品版本。
        calls_before_abandon = demo.cognition.calls
        ended = demo.round("abandon")
        check(demo.cognition.calls > calls_before_abandon,
              "放下小册子这一轮没有被真正评估（QUIET）", ended.get("outcome"))
        check(ended.get("decision") == "ABANDON" and _foreground(demo.runtime) is None,
              "没有正常结束小册子 Activity 以腾出前台",
              {"outcome": ended.get("outcome"), "decision": ended.get("decision"),
               "read": demo.cognition.reads[-1] if demo.cognition.reads else None,
               "offer": demo.cognition.offered[-1] if demo.cognition.offered else None})
        say("abandon", "小册子 Activity 由真实 ABANDON 结束，前台已释放")

        # 到期放到**次晨 08:00**（本单允许把 due 调到次晨）：这样夜里的 TOGGLE/睡眠
        # 那一轮 fitem 还不是 due，个人来源不会把一个 World 机会挤出 16 条；次日晨先
        # 开灯、再到 due 时刻做「浮现但不自动执行」的证明并接手它。
        next_morning_due = (
            demo.clock.now.astimezone(JST) + timedelta(days=1)).replace(
                hour=8, minute=0, second=0, microsecond=0)
        demo.cognition.later_item = {
            "title": "睡前再想想早晨那个问题",
            "due_at": next_morning_due.astimezone(timezone.utc).isoformat(),
            "source_refs": ["artifact:%s" % project_bare],
        }
        proposed = demo.round("propose_future")
        propose_writes = [row for row in (proposed.get("future_item_writes") or [])
                          if str(row.get("op")) == "propose"]
        check(bool(propose_writes) and str(propose_writes[0].get("status")) == "APPLIED",
              "later_item 没有由正式路径落库", proposed.get("future_item_writes"))
        item_ref = str(propose_writes[0].get("item_ref") or "")
        due_at = str(propose_writes[0].get("due_at") or "")
        check(item_ref.startswith("fitem:") and bool(due_at),
              "future item 没有 item_ref/due_at", propose_writes[0])
        stored = demo.cycle.future_items.get(item_ref)
        check(stored is not None and stored.status == "OPEN"
              and project_artifact in (stored.source_refs or []),
              "future item 没有绑定刚写的作品",
              ({"item_ref": stored.item_ref, "status": stored.status,
                "source_refs": list(stored.source_refs)} if stored else None))
        say("future", "later_item 经正式 store 落库：%s，due=%s，来源绑定作品 %s"
            % (item_ref, due_at, project_artifact))

        # 到期前：既不是 due，也不会以 fitem 候选出现。这里问的是**同一真实候选源**
        # （ArtifactContinuationSource），不是手拼候选；到期后同一路径会给出 fitem。
        check(item_ref not in [row["item_ref"]
                               for row in demo.cycle.due_future_items(demo.clock.iso())],
              "到期前 future item 已经被当成 due")
        pre_due_candidates, _snap, _meta = demo.cycle.artifact_source.materialize(
            observed_at=demo.clock.iso())
        check(not any(str(row.source_ref or "").startswith("fitem:")
                      for row in pre_due_candidates),
              "到期前正常候选源已经给出 fitem", [row.source_ref for row in pre_due_candidates])
        check(len(pre_due_candidates) <= 16, "到期前候选源超过 16 条",
              len(pre_due_candidates))


        # -- 15. 0.7 第六阶段：真实夜晚、正式睡眠、次日承接与同目录冷重启 -------------
        # 15a. 睡前真实身体活动。睡眠资格只由 Body owner 自己的疲劳门槛决定；这里只沿正式
        # MOVE 候选做一段街区步行，再由 owner 自己的 tick 结算测得负荷。不写 fatigue、
        # 不改阈值、不调任何参数。
        # 业务钟现在就是 Body/gateway 的同一时域：从午后到傍晚的机械快进也按 owner 自己的
        # 规则结算（疲劳向基线回落），所以睡前要真的走够一段街区才能把测得负荷累积到
        # sleep_like 门槛，而不是靠墙钟缝隙或手改数值。只走世界包自己给出的正式 MOVE 候选。
        night_walk = ("street_1", "street_2", "street_1", "street_3",
                      "street_1", "street_2", "street_1", "street_3",
                      "street_1", "street_2", "street_1", "street_3",
                      "street_1", "home")
        for place in night_walk:
            demo.cognition.place_wish = place
            walked = demo.round_until("go_to")
            read = demo.cognition.reads[-1] if demo.cognition.reads else {}
            check(str(read.get("chose") or "") == "place:%s" % place,
                  "夜间步行没有走到 %s 的正式 MOVE 候选" % place,
                  {"read": read, "outcome": walked.get("outcome")})
            _settle_move(demo, walked, place=place)
        # 真实 owner tick：把这段步行的测得负荷结算进 Body（只是结算，不是伪造疲劳）。
        demo.gateway.service.tick()
        say("night-walk", "睡前沿正式 MOVE 候选真实步行 %d 段；owner tick 结算测得负荷"
            % len(night_walk))

        # 15b. 回到 lamp 所在的 living（既有室内移动路径），正式 TOGGLE off，
        # 再正式室内移动到 bedroom。位置从不手写：每一段都经 world_move_tool 自己的
        # 节点解析、路线（非入口区域先到建筑入口）与唯一 writer apply_move_to。
        import world_living_skeleton as wls
        current = wls.read_move_state(
            hermes_home=str(demo.world_home), environment=demo.env, now=demo.clock.now)
        current_node = str((current or {}).get("world_location_id") or "")
        if current_node != "home/living":
            reached_living = demo.indoor_move("home/living")
            check(reached_living.get("ok") is True
                  and str(reached_living.get("status")) in ("arrived", "already_there"),
                  "没有经既有室内路径到 home/living", reached_living)
        lamp_awake = _substrate_fixtures(demo.world_home).get(lamp_id) or {}
        check(str(lamp_awake.get("power")) == "on",
              "睡前 lamp 的电源不是 on，场景事实不对（傍晚开着的灯才是要关掉的）", lamp_awake)
        # 睡前这次 off 与白天 10b 那次 off 是同一盏灯、同一个绝对状态值，但这是两个
        # 独立的真实 execution：各自的 Decision/AR-0/回执，各自的 evidence 归属。
        _toggle("off", why="睡前正式关灯（第二次独立 off execution）")
        to_bedroom = demo.indoor_move("home/bedroom")
        check(to_bedroom.get("ok") is True
              and str(to_bedroom.get("status")) in ("arrived", "already_there"),
              "没有经既有室内路径回到 home/bedroom", to_bedroom)
        bedroom = wls.read_move_state(
            hermes_home=str(demo.world_home), environment=demo.env, now=demo.clock.now)
        check(str((bedroom or {}).get("world_location_id") or "") == "home/bedroom",
              "室内移动后位置不是 home/bedroom", bedroom)
        say("sleep-toggle", "正式 TOGGLE_FIXTURE → power=off；室内移动 home → home/bedroom"
            "（route=%s）" % (to_bedroom.get("route") or to_bedroom.get("status")))
        sleep_offer = (demo.cognition.offered[-1]["candidates"]
                       if demo.cognition.offered else [])
        world_raw = len(getattr(demo.cycle, "last_world_opportunity_view", None) or [])
        say("night-candidates", "World 原始机会 %d 条；AG-1 16 条上限内共 %d 条"
            % (world_raw, len(sleep_offer)))

        # 15b2. 跨日环境事件：沿权威世界时区/业务时刻读口观察，不读 date.today/墙钟。
        night_ambient = _observe_ambient(demo)
        check(night_ambient["local_date"] == "2026-11-10",
              "入睡前世界本地日期不是 2026-11-10", night_ambient)
        say("ambient-night", "环境事件读取（世界时区 %s）：本地日期 %s，当日集合 %d 条"
            % (night_ambient["timezone"], night_ambient["local_date"],
               len(night_ambient["materialized"])))

        # 15c. 正式 POSE lying，然后由 owner 自己的可用性决定 sleep_like 是否存在。
        posed = demo.round_until("pose_sleep")
        pose_fact = dict(posed.get("body_action") or {})
        check(posed.get("decision") == "START" and pose_fact.get("verb") == "POSE"
              and str((pose_fact.get("params") or {}).get("pose")) == "lying"
              and pose_fact.get("satisfied") is True,
              "躺下没有走正式 bodyact POSE 端口", posed)
        say("sleep-pose", "正式 POSE → lying（owner 复核 %s=%s）"
            % (pose_fact.get("fact_key"), pose_fact.get("expected_value")))
        rest_now = next(
            (dict(row) for row in
             demo.gateway.service.gateway_surface.get_available_actions().get("actions") or []
             if row.get("verb") == "REST"), {})
        check(rest_now.get("start_available") is True
              and rest_now.get("sleep_like_available") is True,
              "Body/World owner 没有给出 sleep_like 资格（不伪造）",
              {key: rest_now.get(key) for key in
               ("start_available", "sleep_like_available", "start_blocked_reason",
                "current")})
        slept = demo.round_until("rest_sleep")
        sleep_fact = dict(slept.get("body_action") or {})
        check(slept.get("decision") == "START" and sleep_fact.get("verb") == "REST"
              and str((sleep_fact.get("params") or {}).get("mode")) == "sleep_like"
              and sleep_fact.get("satisfied") is True,
              "sleep_like 没有由正式 REST start 端口开启", slept)
        sleep_activity = str(slept.get("activity_ref") or "")
        session = next(
            (dict(row).get("current") for row in
             demo.gateway.service.gateway_surface.get_available_actions().get("actions") or []
             if row.get("verb") == "REST"), {})
        check(str(session.get("recovery_state")) == "active"
              and str(session.get("recovery_mode")) == "sleep_like",
              "owner 的恢复会话不是 active/sleep_like",
              session)
        say("sleep-start", "正式 REST start:sleep_like → owner recovery=active mode=sleep_like；"
                           "Activity %s 由会话事实 COMPLETED" % sleep_activity)

        # 15d. 业务钟到次日晨，再正式 stop:rest，然后正式站起来。
        local_now = demo.clock.now.astimezone(JST)
        next_morning = (local_now + timedelta(days=1)).replace(
            hour=7, minute=0, second=0, microsecond=0)
        demo.clock.advance(
            int((next_morning - local_now).total_seconds()), because="睡到次日晨")
        wake = _end_night_session(demo, tag="主链")
        say("sleep-stop", "业务钟到次日 %s（JST）；%s；正式 POSE → standing。"
                          "同一业务钟下这一跳是真实的 Body elapsed，不是手改恢复状态。"
            % (demo.clock.now.astimezone(JST).strftime("%Y-%m-%d %H:%M"), wake["how"]))

        # 15d2. 次日晨再观察同一世界：本地日期真实从 10 变成 11，旧日事件全部退场。
        morning_ambient = _observe_ambient(demo)
        check(morning_ambient["local_date"] == "2026-11-11",
              "晨起后世界本地日期不是 2026-11-11", morning_ambient)
        night_ambient_ids = {str(row.get("event_id"))
                             for row in night_ambient["materialized"]}
        morning_ambient_ids = {str(row.get("event_id"))
                               for row in morning_ambient["materialized"]}
        check(bool(night_ambient_ids) and bool(morning_ambient_ids),
              "单日环境事件集合为空，无法证明换日",
              {"night": sorted(night_ambient_ids), "morning": sorted(morning_ambient_ids)})
        check(not (night_ambient_ids & morning_ambient_ids),
              "旧日环境事件在次日仍然存在（按观察日平移重生）",
              sorted(night_ambient_ids & morning_ambient_ids))
        for row in morning_ambient["materialized"]:
            check(str(row.get("local_date")) == "2026-11-11",
                  "次日环境事件的 local_date 不是 2026-11-11", row)
        check(str(night_ambient.get("observed_status")) == "available"
              and str(morning_ambient.get("observed_status")) == "available",
              "正式 World 观察在两次时刻都不可用",
              {"night": night_ambient.get("observed_status"),
               "morning": morning_ambient.get("observed_status")})
        say("ambient-morning", "环境事件：世界本地日期 2026-11-10 → 2026-11-11；"
                               "当日集合 %d → %d 条且无一沿用旧日 id（旧日 %d 条全部退场）"
            % (len(night_ambient_ids), len(morning_ambient_ids), len(night_ambient_ids)))

        # 次日晨的第二个绝对状态：回 living 正式 TOGGLE on（off→on 第四次独立 execution）。
        morning_move = demo.indoor_move("home/living")
        check(morning_move.get("ok") is True
              and str(morning_move.get("status")) in ("arrived", "already_there"),
              "次日晨没有经既有室内路径到 home/living", morning_move)
        _toggle("on", why="次日晨正式开灯（第二次独立 on execution）")
        check(len(toggle_evidence) == 4 and len(set(toggle_evidence)) == 4,
              "连续 off→on→off→on 的四个完成证据不是各自独立",
              {"evidence": toggle_evidence})
        say("morning-toggle", "次日晨正式 TOGGLE_FIXTURE → power=on；本链共 4 次真实"
                              "TOGGLE（off→on→off→on），4 个独立 Activity、4 个互不相同的"
                              "完成证据")


        due_moment = datetime.fromisoformat(due_at)
        demo.clock.advance(max(1, int((due_moment - demo.clock.now).total_seconds()) + 60),
                           because="future item 到期")
        open_before = len(demo.cycle.future_items.open_items())
        # The baseline is the head this same work is at *before* the due round: the inserted
        # sharing/co-presence stage already moved it forward, so the old stage-3 baseline would
        # fail for the wrong reason.
        head_before_due = demo.port.resume_point(project_artifact)
        # 同一 round 里顺带重复提交完全相同的提案：既有幂等键必须复用同一条 item。
        demo.cognition.later_item = {
            "title": "睡前再想想早晨那个问题", "due_at": due_at,
            "source_refs": ["artifact:%s" % project_bare]}
        due_round = demo.round("futures")
        check(item_ref in [row["item_ref"]
                           for row in demo.cycle.due_future_items(demo.clock.iso())],
              "到期后 future item 没有出现在 due 读取里")
        chosen = str((demo.cognition.reads[-1] or {}).get("chose") or "")
        check(chosen.startswith("fitem:"),
              "到期后没有由正常 source 路径浮现为可执行候选", demo.cognition.reads[-1])
        check(due_round.get("decision") == "NO_ACTION",
              "到期浮现被脚本替身自动执行了",
              {"outcome": due_round.get("outcome"), "decision": due_round.get("decision")})
        check(len(demo.cognition.offered[-1]["candidates"]) <= 16,
              "到期轮候选超过 AG-1 的 16 条上限", demo.cognition.offered[-1])
        still_open = demo.cycle.future_items.get(item_ref)
        check(still_open is not None and still_open.status == "OPEN",
              "到期只读浮现却把 item 改掉了",
              ({"item_ref": still_open.item_ref, "status": still_open.status}
               if still_open else None))
        unchanged = demo.port.resume_point(project_artifact)
        check(unchanged.head_version_id == head_before_due.head_version_id,
              "到期候选被自动执行，作品被写了新版本",
              {"artifact_id": unchanged.artifact_id,
               "version_number": unchanged.version_number,
               "before": head_before_due.version_number})
        dup_writes = [row for row in (due_round.get("future_item_writes") or [])
                      if str(row.get("op")) == "propose"]
        check(bool(dup_writes) and str(dup_writes[0].get("status")) == "APPLIED"
              and dup_writes[0].get("created") is False
              and str(dup_writes[0].get("item_ref")) == item_ref,
              "重复提案没有按幂等键复用同一条 item",
              {"writes": dup_writes, "expected": item_ref})
        check(len(demo.cycle.future_items.open_items()) == open_before,
              "重复提案新增了第二条 future item")
        say("future", "到期前不 due；到期后 owner 路径给出可执行 %s（START 合法），"
                      "本轮 NO_ACTION，item 仍 OPEN，作品 v%s 未变；同内容重复提案 "
                      "created=False、open 数仍为 %d"
            % (chosen, unchanged.version_number, open_before))



        # 15e. 接手昨晚到期 fitem：正常 PERSONAL_OPPORTUNITY 候选 → Decision START →
        # 同一 artifact 真的写出新版本（不是只打印候选）。
        head_before_wake = demo.port.resume_point(project_artifact)
        due_now = [dict(row) for row in demo.cycle.due_future_items(demo.clock.iso())
                   if str(row.get("item_ref")) == item_ref]
        check(bool(due_now), "次日晨昨晚的 future item 不在 due", item_ref)
        executed = demo.round_until("execute_future")
        head_after_wake = demo.port.resume_point(project_artifact)
        check(str(executed.get("decision")) == "START",
              "到期 fitem 没有由正式 Decision 接手",
              {"outcome": executed.get("outcome"),
               "decision": executed.get("decision"),
               "offer": demo.cognition.offered[-1]})
        check(head_after_wake.artifact_id == project_artifact
              and head_after_wake.head_version_id != head_before_wake.head_version_id,
              "接手 fitem 没有在同一 artifact 上产生可核结果",
              {"artifact": head_after_wake.artifact_id,
               "before": head_before_wake.version_number,
               "after": head_after_wake.version_number,
               "receipt": executed.get("step_receipt")})
        wake_activity = str(executed.get("activity_ref") or "")
        say("wake-future", "次日晨接手昨晚 fitem %s：同一 artifact %s v%s→v%s（Activity %s）"
            % (item_ref, project_artifact, head_before_wake.version_number,
               head_after_wake.version_number, wake_activity))

        # 15f. 冷重启前的书签：灯的位置/电源、册子 head、本地 inbox 计数、future item、
        # 一笔未完成 Activity 的 id/status/checkpoint/恢复资格、预算账。
        bookmark = dict(_activity(demo.runtime, wake_activity) or {})
        check(str(bookmark.get("status")) in ("ACTIVE", "WAITING", "PAUSED"),
              "冷重启前没有一项非终态书签 Activity", bookmark)
        lamp_place_before = dict(_substrate_placements(demo.world_home).get(lamp_id) or {})
        lamp_power_before = dict(_substrate_fixtures(demo.world_home).get(lamp_id) or {})
        head_restart_before = demo.port.resume_point(project_artifact)
        inbox_restart_before = _share_inbox(sharing_service)
        future_restart_before = {
            row.item_ref: {"status": row.status, "due_at": row.due_at}
            for row in demo.cycle.future_items.open_items()}
        budget_restart_before = dict(demo.cycle.budget.requests.view())
        economy_before = json.loads(economy_path.read_text(encoding="utf-8"))
        transactions_before = len(economy_before.get("transactions") or [])
        eligibility_before = dict((demo.runtime.waiting_coordinator.eligibility_store
                                   .load_state() or {}).get("eligibilities") or {})
        say("bookmark", "冷重启书签：lamp=%s/%s power=%s；册子 head v%s；inbox=%s；"
                        "future open=%d；Activity %s=%s"
            % (lamp_place_before.get("location_id"), lamp_place_before.get("area_id"),
               lamp_power_before.get("power"), head_restart_before.version_number,
               inbox_restart_before["count"], len(future_restart_before),
               wake_activity, bookmark.get("status")))

        # 15g. 同目录冷重启：关旧 runtime/World 句柄，从已持久 instance 重开 owner 与 runner。
        reopened = demo.reopen_existing()
        check(reopened.get("old_thread_stopped") is True
              and reopened.get("runtime_replaced") is True
              and reopened.get("gateway_replaced") is True,
              "旧 World 线程没有退出或没有真正重开服务", reopened)
        check(reopened.get("old_supply_thread_stopped") is True
              and reopened.get("old_supply_closed") is True
              and reopened.get("supply_replaced") is True
              and reopened.get("memory_replaced") is True
              and str(reopened.get("supply_server")) == "reopened_same_data_root",
              "旧 Supply 服务端没有正常关停并重开（仍是同一进程）", reopened)
        lamp_place_after = dict(_substrate_placements(demo.world_home).get(lamp_id) or {})
        lamp_power_after = dict(_substrate_fixtures(demo.world_home).get(lamp_id) or {})
        head_restart_after = demo.port.resume_point(project_artifact)
        inbox_restart_after = _share_inbox(demo.cycle.sharing_service)
        future_restart_after = {
            row.item_ref: {"status": row.status, "due_at": row.due_at}
            for row in demo.cycle.future_items.open_items()}
        budget_restart_after = dict(demo.cycle.budget.requests.view())
        bookmark_after = dict(_activity(demo.runtime, wake_activity) or {})
        for name, before, after in (
                ("lamp placement", lamp_place_before, lamp_place_after),
                ("lamp power", lamp_power_before, lamp_power_after),
                ("booklet head", {"head": head_restart_before.head_version_id,
                                  "version": head_restart_before.version_number},
                 {"head": head_restart_after.head_version_id,
                  "version": head_restart_after.version_number}),
                ("inbox", inbox_restart_before, inbox_restart_after),
                ("future items", future_restart_before, future_restart_after),
                ("budget", budget_restart_before, budget_restart_after),
                ("activity", {key: bookmark.get(key) for key in
                              ("activity_id", "status", "checkpoint_ref")},
                 {key: bookmark_after.get(key) for key in
                  ("activity_id", "status", "checkpoint_ref")})):
            check(before == after, "冷重启后 %s 不一致" % name,
                  {"before": before, "after": after})
        # 重启不自动重发分享、不重复扣款、不把已落地物件再搬一次。
        economy_after = json.loads(economy_path.read_text(encoding="utf-8"))
        check(len(economy_after.get("transactions") or []) == transactions_before,
              "冷重启后经济账本多了一笔交易", {"before": transactions_before,
                                          "after": len(economy_after.get("transactions") or [])})
        check(dict((demo.runtime.waiting_coordinator.eligibility_store.load_state()
                    or {}).get("eligibilities") or {}) == eligibility_before,
              "冷重启后恢复资格被自动改写")
        say("cold-restart", "同目录冷重启：旧 World 与旧 Supply 服务端线程均已退出，新的 "
                            "Supply server/service（同数据目录）、World 服务与 Life runner 都"
                            "是新对象；lamp/册子 head/inbox/future/预算/Activity 逐项相等；"
                            "分享未重发、未重复扣款")

        # 15h. 重启后的新鲜 Decision 推进同一条 Activity（或同一 artifact），不重头。
        demo.cognition.project_bare = project_bare
        advanced_after = demo.round_until("continue_project")
        back_after = dict(_activity(demo.runtime, wake_activity) or {})
        check(str(back_after.get("activity_id")) == wake_activity,
              "重启后的新鲜 Decision 没有接续同一 Activity",
              {"outcome": advanced_after.get("outcome"),
               "decision": advanced_after.get("decision"),
               "activity": {key: back_after.get(key) for key in
                            ("activity_id", "status", "checkpoint_ref")},
               "offer": demo.cognition.offered[-1]})
        head_final = demo.port.resume_point(project_artifact)
        check(head_final.head_version_id != head_restart_before.head_version_id
              or str(back_after.get("status")) == "COMPLETED",
              "重启后没有真的推进下一步",
              {"before": head_restart_before.head_version_id,
               "after": head_final.head_version_id,
               "activity_status": back_after.get("status")})
        say("restart-continue", "重启后 fresh Decision %s：同一 Activity %s 接续，作品 %s → v%s"
            % (advanced_after.get("decision"), wake_activity, project_artifact,
               head_final.version_number))
        # 第六阶段到此把书签验证完；为了后面的第四阶段能在同一实例里开启新作品，用一次
        # 正式 ABANDON 结束这本小册子 Activity（不是把未完成书签伪装成终态——书签此前
        # 已经跨重启逐项核对过，这一步是真实放下它）。
        closed_booklet = demo.round_until("abandon")
        check(closed_booklet.get("decision") == "ABANDON"
              and _foreground(demo.runtime) is None,
              "第六阶段结束时没有用正式 ABANDON 腾出前台",
              {"outcome": closed_booklet.get("outcome"),
               "decision": closed_booklet.get("decision"),
               "foreground": _foreground(demo.runtime)})
        say("restart-close", "书签验证后由正式 ABANDON 放下小册子 Activity，前台释放")

        # -- 10c. 故障下一轮仍能继续：一次厂商超时不是永久锁 ----------------------
        # 放在第四阶段之前：此时最后一个决策轮是 NO_ACTION，且回顾 artifact 还没被创建，
        # 所以下一轮的真实候选仍是原来的有界供给。
        fail_intent = demo.cognition.intent

        class _OneTimeout:
            def __init__(inner, real):
                inner.real = real
                inner.used = False

            def invoke(inner, request, *, schema_retry_index: int = 0):
                if not inner.used:
                    inner.used = True
                    raise RuntimeError("synthetic provider timeout")
                return inner.real.invoke(request, schema_retry_index=schema_retry_index)

        demo.runtime.decision_service.cognition_adapter = _OneTimeout(demo.cognition)
        # 上一轮是同一状态的 NO_ACTION，所以这一状态会先按既有 reconsideration 门禁保持安静；
        # 门禁有界：连续 3 次相同状态后会强制放行一次。这里让它自己走到那一次（不手工清
        # 门禁、不改 TTL），超时注入才真正被评估到。
        failed = None
        for _attempt in range(6):
            failed = demo.round("continue")
            if str(failed.get("outcome")) != "QUIET":
                break
        check(failed is not None and failed.get("outcome") == "DECISION_FAILED",
              "合成一次 provider 超时没有被记成安全失败轮", failed)
        say("fault", "合成一次 provider 超时：本轮 outcome=%s（记失败，不写永久锁）"
            % failed.get("outcome"))
        requests = getattr(demo.cycle.budget, "requests", None)
        check(getattr(requests, "failstop", None) is False,
              "日常入口误启了实验性 bounded failstop",
              {"budget": type(requests).__name__})
        check(not (demo.state_root / "bounded_failstop.json").exists(),
              "日常失败写了永久 bounded_failstop 标记", str(demo.state_root))
        calls_before_follow = demo.cognition.calls
        follow = demo.round("continue")
        check(demo.cognition.calls == calls_before_follow + 1,
              "失败后的下一轮没有真的再调用一次 adapter",
              {"before": calls_before_follow, "after": demo.cognition.calls})
        check(follow.get("outcome") == "NO_ACTION",
              "失败后的下一轮没有回到正常决策（不冒效果推进）", follow)
        say("fault", "下一轮真的再调用一次 adapter 并正常 NO_ACTION（intent=%s，累计调用=%d）"
            % (fail_intent, demo.cognition.calls))
        demo.runtime.decision_service.cognition_adapter = demo.cognition

        # -- 13. 第四阶段：确证经历 → 自主回顾 → 有范围做法 → 真实使用与两次评价 ----
        # 这一段的每条经历都来自前面真实 owner 完成的活动（阅读批注、作品 v2/v3 的
        # AR-0 版本），经正式 AG-2 handoff 进入本实例 Memory；脚本只做 Decision 选择、
        # 内容生成与评价意图，不注入回忆、不手写 lexp。
        practice = demo.cycle.practice
        availability = dict(practice.availability())
        check(str(availability.get("status")) == "READY"
              and str(availability.get("memory")) == "BOUND",
              "默认装配没有接上 practice/Memory（不应手工关 seam）", availability)
        seeded = practice.experiences(limit=4)
        check(len(seeded) >= 2, "真实 handoff 没有留下至少两条可读经历",
              {"count": len(seeded)})
        check(all(str(row.get("experience_id") or "") for row in seeded),
              "可读经历没有稳定 id", seeded)
        say("memory", "默认 Memory 可读确证经历 %d 条；practice seam=%s（%s）"
            % (len(seeded), availability.get("status"), availability.get("memory")))

        # 13a. 正常候选里出现一个回顾选项；Decision 选中即由 owner 建真正 review artifact。
        demo.cognition.project_bare = ""
        review_round = demo.round_until("review")
        created_review = dict(review_round.get("review_created") or {})
        review_artifact = str(created_review.get("artifact_id") or "")
        check(review_round.get("outcome") == "ACTED"
              and str(created_review.get("kind")) == "review" and bool(review_artifact),
              "回顾选项没有由 owner 真正创建成 artifact",
              {"outcome": review_round.get("outcome"),
               "decision": review_round.get("decision"),
               "created": created_review})
        check(len(demo.cognition.offered[-1]["candidates"]) <= 16,
              "回顾轮候选超过 AG-1 的 16 条上限", demo.cognition.offered[-1])
        owner_answer = practice.service.read_review(review_artifact)
        owner_data = dict((owner_answer or {}).get("data") or {})
        # The owner's own parse is over the whole content (marker line included); ``body`` is
        # already the marker-stripped text, so re-parsing that would always answer "not a doc".
        facts = practice.facts_of(owner_data.get("content") or "")
        check(owner_answer.get("ok") is True, "owner 读不回刚建的回顾", owner_answer)
        check(isinstance(facts, dict) and str(facts.get("kind")) == "review",
              "回顾文档的第一行标记不是 nyairo-practice:v1 kind=review",
              {"parsed": bool(facts), "kind": (facts or {}).get("kind")})
        cited_ids = [str(x) for x in (owner_data.get("experience_ids") or [])]
        check(bool(cited_ids), "回顾没有引用任何源 lexp", {"experience_ids": cited_ids})
        say("review", "owner 建回顾 %s（标记 parse=%s kind=%s，引用 %d 条源经历）"
            % (review_artifact, bool(facts), (facts or {}).get("kind"), len(cited_ids)))

        # 13b. 回顾自身的写作结果已经是一条可读经历；自动回顾种子必须排除它，普通经历仍可选。
        after_review = practice.experiences(limit=6)
        own_ids = {str(row.get("experience_id")) for row in after_review
                   if _bare(row.get("artifact_ref")) == _bare(review_artifact)}
        check(bool(own_ids), "回顾的写作结果没有进入 Memory（后续试用将无证据）",
              {"review": review_artifact, "count": len(after_review)})
        classification = practice.review_result_artifact_ids(
            [row.get("artifact_ref") for row in after_review])
        check(_bare(review_artifact) in {_bare(ref) for ref in
                                         (classification.get("review_ids") or ())},
              "owner 没有把刚写的回顾分类为 review", classification)
        say("review", "回顾自身写作结果已进入 Memory 且被 owner 分类为 review（%d 条自身结果）"
            % len(own_ids))

        # 13c. 正式 Decision 提交一条有范围做法草案；owner 存为 TENTATIVE（不测试直接建）。
        demo.cognition.project_bare = _bare(review_artifact)
        proposal_evidence = [row.get("experience_id") for row in practice.experiences(limit=4)]
        check(bool(proposal_evidence), "没有可读经历可支撑做法草案")
        demo.cognition.method_proposal = {
            "title": "先把一条确证经历读完，再写它带来的变化",
            "instruction": "动笔前先读一条自己确认过的经历，只写它实际改变的那一点。",
            "scope": "仅在我有可读回顾与可读经历的时候",
            "uncertainty": "还不知道换成别的题材是否同样适用",
            "experience_ids": [proposal_evidence[0]],
            "review_refs": [review_artifact],
        }
        proposal_round = demo.round_until("continue_project")
        created_method = dict(demo.cycle.last_method_created or {})
        method_artifact = str(created_method.get("artifact_id") or "")
        check(proposal_round.get("outcome") == "ACTED" and bool(method_artifact),
              "正式 Decision 的做法草案没有由 owner 保存",
              {"outcome": proposal_round.get("outcome"),
               "method": created_method,
               "error": demo.cycle.last_practice_error})
        check(str(created_method.get("method_state")) == "TENTATIVE",
              "做法草案的初始状态不是 TENTATIVE", created_method)
        check(len(demo.cognition.offered[-1]["candidates"]) <= 16,
              "做法草案轮候选超过 16 条", demo.cognition.offered[-1])
        say("method", "正式 Decision proposal 由 owner 存为 %s（草案 %s，head=%s）"
            % (created_method.get("method_state"), method_artifact,
               created_method.get("head_version_id")))

        def _trial(verdict: str, evidence: list[str], detail: str) -> dict:
            demo.cognition.method_trial = {"outcome": verdict, "detail": detail,
                                           "experience_ids": list(evidence)}
            out = demo.round_until("continue_project")
            check(len(demo.cognition.offered[-1]["candidates"]) <= 16,
                  "评价轮候选超过 16 条", demo.cognition.offered[-1])
            check(bool(demo.cycle.practice_method_refs()),
                  "本轮没有向 Decision 提供可命名的做法句柄")
            return out

        # 13d. 后续轮次的正式请求里真的带着 method:<hash>；Decision 用本轮句柄记 HELPED。
        helped_out = _trial(lpc.OUTCOME_HELPED, proposal_evidence[:1],
                            "这一轮按这条做法写，写出来的结果自己读得回来。")
        helped = dict(helped_out.get("method_trial") or {})
        usage = dict(helped_out.get("method_usage") or {})
        method_ref = str(helped.get("method_ref") or usage.get("method_ref") or "")
        check(bool(method_ref.startswith(lpc.METHOD_REF_PREFIX)),
              "Decision 没有落成可命名的做法句柄", {"usage": usage, "trial": helped})
        visible = [str(row.get("method_ref")) for row in
                   ((demo.cycle.last_practice_context or {}).get("methods") or [])]
        check(method_ref in visible, "本轮 offered 的做法句柄没有出现在决策处境里",
              {"wanted": method_ref, "offered": visible})
        check(str(helped.get("outcome")).upper() == lpc.OUTCOME_HELPED
              and str(helped.get("method_state")) == "TRIAL",
              "有证据的 HELPED 没有让做法进入 TRIAL",
              {"trial": helped, "error": demo.cycle.last_practice_error})
        say("trial", "句柄 %s 可见；Decision 记 HELPED，owner 状态=%s（trials=%s）"
            % (method_ref, helped.get("method_state"), helped.get("trials_recorded")))

        # 13e. 更新后的 owner head + 本轮 offered 句柄，另一条真实经历记 NOT_HELPED → CONTESTED。
        earlier = [str(row.get("experience_id")) for row in practice.experiences(limit=6)]
        other = next((item for item in earlier
                      if item not in set(proposal_evidence[:1])), "")
        check(bool(other), "没有第二条不同且可读的经历用于反例评价",
              {"experiences": len(earlier)})
        not_helped_out = _trial(lpc.OUTCOME_NOT_HELPED, [other],
                                "换一件别的事时，这条做法没有帮上忙。")
        not_helped = dict(not_helped_out.get("method_trial") or {})
        check(str(not_helped.get("outcome")).upper() == lpc.OUTCOME_NOT_HELPED
              and str(not_helped.get("method_state")) == "CONTESTED",
              "另一条经历的 NOT_HELPED 没有让做法变成 CONTESTED",
              {"trial": not_helped, "error": demo.cycle.last_practice_error})
        say("trial", "另一条真实经历记 NOT_HELPED，owner 状态=%s（旧版本与评价保留）"
            % not_helped.get("method_state"))

        # 13f. 负控：没有可读经历时 HELPED 被既有证据门拒绝；做法 head/状态/trial 数不变。
        before_refusal = practice.read_method(method_artifact) or {}
        before_trials = len(before_refusal.get("trials") or [])
        refused_out = _trial(lpc.OUTCOME_HELPED, ["lexp:v07-not-readable"],
                             "没有任何可读经历支撑的评价。")
        refused = dict(refused_out.get("method_trial") or {})
        after_refusal = practice.read_method(method_artifact) or {}
        check(str(refused.get("status")) == "REFUSED",
              "无可读经历的 HELPED 没有按既有规则被拒绝",
              {"trial": refused, "error": demo.cycle.last_practice_error})
        check(str(after_refusal.get("method_state")) == "CONTESTED"
              and str(after_refusal.get("head_version_id"))
              == str(before_refusal.get("head_version_id"))
              and len(after_refusal.get("trials") or []) == before_trials,
              "被拒绝的评价改动了做法 head/状态/trial 数",
              {"before": {"state": before_refusal.get("method_state"),
                          "head": before_refusal.get("head_version_id"),
                          "trials": before_trials},
               "after": {"state": after_refusal.get("method_state"),
                         "head": after_refusal.get("head_version_id"),
                         "trials": len(after_refusal.get("trials") or [])}})
        say("trial", "负控：无可读证据的 HELPED 被拒绝（%s）；方法 head/状态/trial 数未变"
            % (refused.get("reason_code") or refused.get("status")))

        # 13g. 真实结束回顾 Activity 后，再从**同一批**可读经历重建自动候选：回顾自身的多版
        # 写作结果都必须被排除，普通经历仍然长出一个回顾选项。
        demo.cognition.method_trial = None
        demo.cognition.project_bare = _bare(review_artifact)
        ended_review = demo.round_until("abandon")
        check(ended_review.get("decision") == "ABANDON" and _foreground(demo.runtime) is None,
              "回顾 Activity 没有由真实 ABANDON 结束",
              {"outcome": ended_review.get("outcome"),
               "decision": ended_review.get("decision")})
        check(len(demo.cognition.offered[-1]["candidates"]) <= 16,
              "结束回顾的候选轮超过 16 条", demo.cognition.offered[-1])
        latest = practice.experiences(limit=6)
        own_ids_all = {str(row.get("experience_id")) for row in latest
                       if _bare(row.get("artifact_ref")) == _bare(review_artifact)}
        check(len(own_ids_all) >= 1, "回顾自身的写作结果不在可读经历里",
              {"own": sorted(own_ids_all)})
        rebuilt = demo.cycle.project_options(now=demo.clock.iso())
        again = next((dict(row) for row in rebuilt
                      if str(row.get("kind")) == lpo.KIND_REVIEW), None)
        check(again is not None, "排除自身结果后，普通经历没有重新长出回顾选项",
              {"kinds": [row.get("kind") for row in rebuilt],
               "review_own": sorted(own_ids_all)})
        auto_ids = {str(row.get("experience_id")) for row in (again.get("experiences") or [])}
        check(auto_ids and not (auto_ids & own_ids_all),
              "自动回顾种子用上了回顾自己的写作结果",
              {"auto": sorted(auto_ids), "own": sorted(own_ids_all)})
        check(len(rebuilt) <= lpo.MAX_OPTIONS + 2, "回顾重建的选项数超出既有预算",
              {"count": len(rebuilt)})
        say("review", "结束回顾后重建候选：自动种子排除 %d 条回顾自身写作结果，"
                      "普通经历仍产出回顾选项（%d 条）"
            % (len(own_ids_all), len(auto_ids)))

        say("done", "一条真实 owner 链跑完：家中家具停留 → 拿起/放下自己的笔记本（substrate "
                    "真变）→ 街口（书店/咖啡馆/花店与相邻街道都在白天候选）→ 书店 → 读到第 2 页 "
                    "（第 1 页留了一条正式 unclear 疑问）→ 无 hint PAUSE + 阅读默认短间隔 → "
                    "OPEN → RESUME 同一 Activity → 最后一页 COMPLETE → 走到营业的家居店 → "
                    "买下真实在卖的商品（经济 owner 一笔 DEBIT + OWNED）→ 同 key 重放 "
                    "already_applied → owner tick 把灯落地 → 正式回家 → 自主 ARRANGE 到另一区域 "
                    "→ 正式 TOGGLE off → 正式 TOGGLE on（off/on 各自独立 Activity，证据按 "
                    "execution 归属）→ 傍晚灯真的 on 时由 QUESTION_FROM_MATERIAL 提案让 owner "
                    "创建小册子并写出 v2 → 对同一 artifact CONTINUE 到 v3 → 本地分享/共处 → "
                    "提交带 due future item：到期前不浮现，到期后以可执行 fitem 候选出现且不"
                    "自动执行、重复提案不新增 → 【第六阶段】睡前沿正式 MOVE 候选真实步行并由 "
                    "owner tick 结算测得负荷 → 在 living 正式 TOGGLE off（第二次独立 off）→ "
                    "既有室内移动 home/living → home/bedroom → 正式 POSE lying → owner 给出 "
                    "sleep_like 资格后正式 REST start:sleep_like（recovery active/mode="
                    "sleep_like）→ 业务钟到次日晨 → 会话由 owner 自身规则结算（仍开着则正式 REST "
                    "stop）→ POSE standing → 正式 TOGGLE "
                    "on（第二次独立 on，四次真实 TOGGLE 各一个独立 Activity）→ 接手昨晚 due fitem "
                    "并在同一 artifact 写出新版本 → 同目录冷重启（旧 World 与旧 Supply 服务端"
                    "线程都退出、同数据目录重建新对象，Life runner 真重开；灯/册子 head/inbox/"
                    "future/预算/Activity 逐项相等；分享未重发、未重复扣款）→ 重启后 fresh "
                    "Decision 接续同一 Activity → 正式 ABANDON 放下 → 默认 Memory 里的确证经历"
                    "经正常候选写成"
                    "真正回顾（owner marker kind=review）→ 回顾自身写作结果被自动种子排除而普通"
                    "经历仍可选 → 正式 Decision 草案由 owner 存为 TENTATIVE → 本轮句柄可见、"
                    "HELPED→TRIAL、另一经历 NOT_HELPED→CONTESTED → 无可读证据的评价被拒绝且方法"
                    "未变")
        say("boundary", "决定、阅读响应与续写内容是脚本替身；业务时钟机械推进，且同一注入"
                        "时钟同时供 World/Resolver/gateway 判到达与 Body 结算（跨夜跳跃是真实的 "
                        "Body elapsed，sleep_like 会话的收尾由 owner 自己的规则给出）；"
                        "research seam 被显式停用，以便走 QUESTION_FROM_MATERIAL 而非 "
                        "RESEARCH_FROM_QUESTION；practice/review 走默认装配，方法草案与 "
                        "HELPED/NOT_HELPED 是脚本显式意图，不是自然形成的方法，也不证明做法"
                        "客观有效；本演示更长，只对**本 demo 进程**放宽了 profile 的每日轮次/动作"
                        "计数（产品请求上限与产品行为未改）；没有产品模型请求、没有外网、根目录保留")

        # -- 16. 第二个世界（非海岸、非 929711）的完整日常链 -------------------------
        # 同一条生成→prepare→Supply/World/Body/Life 路径，换一个主题与 seed：不同小镇名/
        # 街道/特色场所/起始分页读物，书店读/PAUSE/RESUME/完成、两件新家居的真实购买与
        # 使用、正式 sleep_like 跨夜与环境事件换日，都在这第二个世界里重跑一遍。
        _run_other_world_chain()
        # -- 17. 第八阶段：接回**本主链同一 root、同一本册子**的今日自选/义务、同作上下文、
        # 线上点心体感与手机只读。小册子就是晚间 QUESTION 生出的 project_artifact，已续写多版、
        # 有 Memory 确证经历和本地分享草稿；不再另起第三个 root / 另造同名册子。
        _run_stage8_chain(demo=demo, booklet=project_artifact, booklet_bare=project_bare)
        # -- 18. 第九阶段：同一 root 的研究闭环、同实例日记/相册六域时间线、S03 与共同实践真来源。
        research_id = _run_stage9_chain(demo=demo, booklet=project_artifact,
                                        booklet_bare=project_bare)
        # -- 19. 第十阶段：同一 root 的有限处境/忙时回话、做法退休与来源归档、遗忘屏障与恢复检视。
        # 归档负控直接复用第九阶段返回的那个真实研究作品 id，不另造支线。
        _run_stage10_chain(demo=demo, booklet=project_artifact, booklet_bare=project_bare,
                           research_id=research_id)
        return 0
    finally:
        demo.close()


if __name__ == "__main__":
    raise SystemExit(main())
