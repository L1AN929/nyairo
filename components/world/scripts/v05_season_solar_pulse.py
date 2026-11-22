#!/usr/bin/env python3
"""v0.5 / v0.8 真正的四季与二十四节气唯一确定性计算真源（纯标准库、零写盘、零随机、llm_calls=0）。

对标设计参考：
- 第 66 章：四季昼夜与微气候怎样穿过一间屋子
- 第 92 章：节气、生日与纪念日——有心意但绝不搞打卡债

核心不变式：
1. 100% 纯标准库确定性运算，零磁盘写入，零随机数，零联网，零模型调用（``llm_calls == 0``）；
2. 二十四节气采用寿星万年历 20/21 世纪常数表与紫金山天文台闰差修正表，节气索引 ``0..23``
   按公历 1-12 月自然顺序排列，与 ``v05_unified_world_fabric.encode_shortwave_weather_callsign``
   及桌面端 ``v05_stage.go`` 的 5-bit ``solar_term_idx`` 完全一致；
3. 反打卡债契约：``check_in_debt=False``、``forced_greeting=False``、``affection_points=False``；
4. 中立默认文案（统一使用 ``TA``，零 ``unicode.So`` 表情符号）。
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

PULSE_SCHEMA = "world.v05.season_solar_pulse.v1"
DEFAULT_TZ = timezone(timedelta(hours=8))

VALID_SEASONS: tuple[str, ...] = ("spring", "summer", "autumn", "winter")

SEASON_LABELS_CN: dict[str, str] = {
    "spring": "春日",
    "summer": "夏时",
    "autumn": "秋意",
    "winter": "冬景",
}

# (solar_term_idx, term_id, term_name_cn, month, c21, c20, solar_season, phenology_note_cn)
SOLAR_TERMS_24: tuple[tuple[int, str, str, int, float, float, str, str], ...] = (
    (0, "xiaohan", "小寒", 1, 5.4055, 6.11, "winter", "寒气渐深，窗边泡热茶时白汽格外明显"),
    (1, "dahan", "大寒", 1, 20.12, 20.84, "winter", "岁末风紧，傍晚适合早点拧亮书桌暖灯"),
    (2, "lichun", "立春", 2, 3.87, 4.6295, "spring", "寒意初松，向阳坡角的风软了一分"),
    (3, "yushui", "雨水", 2, 18.73, 19.4599, "spring", "檐角微润，石阶缝里的青苔慢慢返青"),
    (4, "jingzhe", "惊蛰", 3, 5.63, 6.3826, "spring", "春气微动，巷口老树枝头鼓起浅绿新芽"),
    (5, "chunfen", "春分", 3, 20.646, 21.4155, "spring", "昼夜平分，午后东窗照进来的光最匀净"),
    (6, "qingming", "清明", 4, 4.81, 5.59, "spring", "风清景明，微雨刚停时坡道空气格外清爽"),
    (7, "guyu", "谷雨", 4, 20.1, 20.888, "spring", "春雨温润，窗台绿植抽新叶正快"),
    (8, "lixia", "立夏", 5, 5.52, 6.318, "summer", "树冠渐密，午后穿堂风里带了点暖意"),
    (9, "xiaoman", "小满", 5, 21.04, 21.86, "summer", "初夏风爽，小院晾晒的衣物干得利落"),
    (10, "mangzhong", "芒种", 6, 5.678, 6.5, "summer", "空气微润，出门散步自然往傍晚挪了挪"),
    (11, "xiazhi", "夏至", 6, 21.37, 22.2, "summer", "白昼最长，晚上七点半天边还留着暮光"),
    (12, "xiaoshu", "小暑", 7, 7.108, 7.928, "summer", "暑气渐盛，树荫石阶与穿堂风最解热"),
    (13, "dashu", "大暑", 7, 22.83, 23.65, "summer", "盛夏蝉声细密，冰箱里的冷麦茶最抢手"),
    (14, "liqiu", "立秋", 8, 7.5, 8.35, "autumn", "晚风初爽，傍晚路面的热气散得早了些"),
    (15, "chushu", "处暑", 8, 23.13, 23.95, "autumn", "暑意渐收，天高云淡适合顺坡走远路"),
    (16, "bailu", "白露", 9, 7.646, 8.44, "autumn", "清晨石栏微凉，夜里开窗适合只留小缝"),
    (17, "qiufen", "秋分", 9, 23.042, 23.822, "autumn", "秋光清透，书桌前最耐安安静静久坐"),
    (18, "hanlu", "寒露", 10, 8.318, 9.098, "autumn", "露水偏凉，坡道长椅边开始攒起干爽落叶"),
    (19, "shuangjiang", "霜降", 10, 23.438, 24.218, "autumn", "深秋风脆，巷口傍晚常飘来烤红薯甜香"),
    (20, "lidong", "立冬", 11, 7.438, 8.218, "winter", "初冬收风，端上桌的热茶比夏天凉得快些"),
    (21, "xiaoxue", "小雪", 11, 22.36, 23.08, "winter", "天黑明显变早，玻璃内侧容易结一层薄雾"),
    (22, "daxue", "大雪", 12, 7.18, 7.9, "winter", "寒气沉实，渡口与面包铺都温着驱寒热饮"),
    (23, "dongzhi", "冬至", 12, 21.94, 22.6, "winter", "白昼最短，屋里暖灯与一碗热汤圆最熨帖"),
)

SOLAR_TERM_DAY_DELTAS: dict[tuple[int, int], int] = {
    (2019, 0): -1,
    (1982, 0): 1,
    (2082, 1): 1,
    (2026, 3): -1,
    (2084, 5): 1,
    (2008, 9): 1,
    (2016, 12): 1,
    (2002, 14): 1,
    (2089, 19): 1,
    (2089, 20): 1,
    (1978, 21): 1,
    (1954, 22): 1,
    (2021, 23): -1,
}

SEASON_MICROCLIMATE_PROFILES: dict[str, dict[str, Any]] = {
    "spring": {
        "sunlight_window": "06:30-18:00",
        "sunrise_min": 390,
        "sunset_min": 1080,
        "sun_arc": "gentle_mid",
        "ambient_temp_base_c": 16.5,
        "indoor_window_temp_shift_c": -1.5,
        "tea_cooling_multiplier": 1.00,
        "frost_ease": "morning_cool_only",
        "frost_temp_threshold_c": 13.0,
        "frost_melt_multiplier": 1.15,
        "soundscape_palette": "spring_breeze_shimmer",
        "sound_hint_cn": "窗外春风轻拂，偶有远林清淡泛音",
        "window_motif": "floating_petals_pale_green_beam",
        "blank_grove_tone_cn": "新抽浅绿草芽与零星落瓣",
        "diner_seasonal_special_cn": "春笋若竹汤",
        "seasonal_thermal_note_cn": "春日气温温和，端上桌的热饮散热平缓",
        "seasonal_spark_note_cn": "春日天光渐柔，窗玻璃只在微凉晨间容易留一层浅雾。",
        "tea_cue_short_cn": "茶温平缓",
    },
    "summer": {
        "sunlight_window": "05:30-19:30",
        "sunrise_min": 330,
        "sunset_min": 1170,
        "sun_arc": "high_bright",
        "ambient_temp_base_c": 27.5,
        "indoor_window_temp_shift_c": 1.5,
        "tea_cooling_multiplier": 0.78,
        "frost_ease": "rain_mist_only",
        "frost_temp_threshold_c": 11.0,
        "frost_melt_multiplier": 1.65,
        "soundscape_palette": "summer_cicada_evening_pulse",
        "sound_hint_cn": "树荫下微风轻晃，傍晚带一点轻柔夏虫脉动",
        "window_motif": "high_sun_swaying_leaf_shadow",
        "blank_grove_tone_cn": "浓密绿荫与晚风凉石阶",
        "diner_seasonal_special_cn": "冷麦茶配梅子冷面",
        "seasonal_thermal_note_cn": "夏时屋里偏暖，热饮留温比秋冬更久些",
        "seasonal_spark_note_cn": "夏时日照充沛，窗面不易久留水雾，画下的浅痕很快就会散开。",
        "tea_cue_short_cn": "茶留温久",
    },
    "autumn": {
        "sunlight_window": "06:30-17:30",
        "sunrise_min": 390,
        "sunset_min": 1050,
        "sun_arc": "low_golden_slant",
        "ambient_temp_base_c": 15.0,
        "indoor_window_temp_shift_c": -2.5,
        "tea_cooling_multiplier": 1.28,
        "frost_ease": "crisp_dusk_mist",
        "frost_temp_threshold_c": 14.5,
        "frost_melt_multiplier": 0.95,
        "soundscape_palette": "autumn_crisp_leaf_rustle",
        "sound_hint_cn": "秋风干爽，拂过干叶带起细碎沙沙声",
        "window_motif": "amber_slant_drifting_ginkgo_maple",
        "blank_grove_tone_cn": "金黄银杏与红枫落叶铺在石阶边",
        "diner_seasonal_special_cn": "秋刀鱼配热味增汤",
        "seasonal_thermal_note_cn": "秋意微凉，傍晚桌边的热茶散热略快",
        "seasonal_spark_note_cn": "秋意清爽，傍晚降温时玻璃偶有薄雾，风里带着干叶气息。",
        "tea_cue_short_cn": "茶凉略快",
    },
    "winter": {
        "sunlight_window": "07:45-16:30",
        "sunrise_min": 465,
        "sunset_min": 990,
        "sun_arc": "low_pale_early_dusk",
        "ambient_temp_base_c": 5.5,
        "indoor_window_temp_shift_c": -4.5,
        "tea_cooling_multiplier": 1.45,
        "frost_ease": "easy_winter_condensation",
        "frost_temp_threshold_c": 16.5,
        "frost_melt_multiplier": 0.62,
        "soundscape_palette": "winter_muffled_glass_hum",
        "sound_hint_cn": "冬夜木窗外风声低沉，室内透着安静暖意",
        "window_motif": "early_dusk_warm_lamp_frost_rim_snow",
        "blank_grove_tone_cn": "清瘦枝影与石阶背阴处薄霜残雪",
        "diner_seasonal_special_cn": "萝卜牛腩热锅与热玉米浓汤",
        "seasonal_thermal_note_cn": "冬景寒意沉实，端上桌的热饮凉得比暖季明显更快",
        "seasonal_spark_note_cn": "冬景天黑得早，玻璃内侧容易哈气凝雾，指尖留下的字迹也会停留更久。",
        "tea_cue_short_cn": "茶凉较快",
    },
}

REGULAR_SEASONAL_TWEAKS: dict[str, dict[str, str]] = {
    "spring": {
        "auntie_lin": "艾草盐卷与软餐包",
        "uncle_zhou": "窗边晾晒植物图鉴旧册",
        "student_chen": "帆布包里插着写生铅笔",
        "feeder_su": "矮墙根冒出新草芽、刚换净猫水碗",
        "keeper_he": "渡口木亭泡上明前新茶",
    },
    "summer": {
        "auntie_lin": "冰镇柠檬盐面包",
        "uncle_zhou": "推开木格窗挂半幅竹帘引风",
        "student_chen": "烘衣房外晚风微热、手边搁着冰汽水",
        "feeder_su": "把猫碗挪进老树浓荫底下避晒",
        "keeper_he": "大玻璃壶里镇着解暑凉麦茶",
    },
    "autumn": {
        "auntie_lin": "烤栗子吐司与肉桂苹果卷",
        "uncle_zhou": "门口木箱摆出夹银杏叶的秋日诗集",
        "student_chen": "薄外套兜里揣着干爽梧桐叶",
        "feeder_su": "落叶堆边橘猫正揣爪晒斜阳",
        "keeper_he": "茶亭小炉换上醇厚焙茶",
    },
    "winter": {
        "auntie_lin": "热乎红豆奶油包与厚吐司",
        "uncle_zhou": "挂上厚棉门帘、小暖器旁烘着旧纸香",
        "student_chen": "借着烘衣机暖气烘暖微凉指尖",
        "feeder_su": "替避风纸箱多垫了一层厚旧毛巾",
        "keeper_he": "小炉一直温着老姜茶与热玉米浓汤",
    },
}

SEASONAL_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "节气",
    "时令",
    "四季",
    "换季",
    "天黑",
    "天亮",
    "日照",
    "日出",
    "日落",
    "冷暖",
    "天色",
    "春天",
    "春日",
    "夏天",
    "夏日",
    "秋天",
    "秋日",
    "冬天",
    "冬日",
    *(row[2] for row in SOLAR_TERMS_24),
)

ANTI_CHORE_CONTRACT: dict[str, bool] = {
    "check_in_debt": False,
    "forced_greeting": False,
    "affection_points": False,
}


def normalize_season(value: Any) -> Optional[str]:
    """Return canonical lowercase season name if valid, otherwise ``None``."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    return cleaned if cleaned in VALID_SEASONS else None


def calendar_season_for_month(month: int) -> str:
    """Meteorological calendar season matching Ch.66 & ``world_living_skeleton._season``."""
    m = int(month)
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    if m in (9, 10, 11):
        return "autumn"
    return "winter"


def parse_iso_to_default_tz(now_iso: Optional[str | datetime] = None) -> datetime:
    """Parse an ISO timestamp or datetime into UTC+8 without disk writes."""
    if isinstance(now_iso, datetime):
        dt = now_iso if now_iso.tzinfo is not None else now_iso.replace(tzinfo=DEFAULT_TZ)
        return dt.astimezone(DEFAULT_TZ).replace(microsecond=0)
    if isinstance(now_iso, str) and now_iso.strip():
        raw = now_iso.strip()
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=DEFAULT_TZ)
            return dt.astimezone(DEFAULT_TZ).replace(microsecond=0)
        except ValueError:
            pass
    return datetime.now(DEFAULT_TZ).replace(microsecond=0)


def solar_term_day_of_month(year: int, term_idx: int) -> int:
    """Compute exact solar-term day of month via ShouXing constants + PMO deltas."""
    idx = int(term_idx)
    if idx < 0 or idx >= len(SOLAR_TERMS_24):
        raise ValueError(f"term_idx must be within 0..23, got {term_idx!r}")
    yr = int(year)
    row = SOLAR_TERMS_24[idx]
    c_val = row[5] if 1900 <= yr <= 1999 else row[4]
    y = yr % 100
    leap_adjust = 1 if idx < 4 else 0
    day = int(y * 0.2422 + c_val) - int((y - leap_adjust) // 4) + SOLAR_TERM_DAY_DELTAS.get((yr, idx), 0)
    return max(1, min(28, int(day)))


def solar_term_date(year: int, term_idx: int) -> date:
    """Return Gregorian ``date`` of the specified solar term in ``year``."""
    idx = int(term_idx)
    month = SOLAR_TERMS_24[idx][3]
    return date(int(year), month, solar_term_day_of_month(int(year), idx))


def resolve_solar_term(now_dt: datetime) -> dict[str, Any]:
    """Resolve current and upcoming 24 solar term in UTC+8 calendar day."""
    local_dt = parse_iso_to_default_tz(now_dt)
    cur_date = local_dt.date()
    idx_a = (cur_date.month - 1) * 2
    idx_b = idx_a + 1
    date_a = solar_term_date(cur_date.year, idx_a)
    date_b = solar_term_date(cur_date.year, idx_b)

    if cur_date < date_a:
        cur_idx = (idx_a - 1) % 24
        cur_year = cur_date.year - 1 if idx_a == 0 else cur_date.year
        cur_term_date = solar_term_date(cur_year, cur_idx)
        next_idx = idx_a
        next_term_date = date_a
    elif cur_date < date_b:
        cur_idx = idx_a
        cur_term_date = date_a
        next_idx = idx_b
        next_term_date = date_b
    else:
        cur_idx = idx_b
        cur_term_date = date_b
        next_idx = (idx_b + 1) % 24
        next_year = cur_date.year + 1 if idx_b == 23 else cur_date.year
        next_term_date = solar_term_date(next_year, next_idx)

    cur_row = SOLAR_TERMS_24[cur_idx]
    next_row = SOLAR_TERMS_24[next_idx]
    days_since = (cur_date - cur_term_date).days
    days_until_next = (next_term_date - cur_date).days

    return {
        "solar_term_idx": int(cur_row[0]),
        "term_id": cur_row[1],
        "term_name_cn": cur_row[2],
        "solar_season": cur_row[6],
        "term_date": cur_term_date.isoformat(),
        "is_term_day": days_since == 0,
        "days_since_term": int(days_since),
        "next_term_idx": int(next_row[0]),
        "next_term_id": next_row[1],
        "next_term_name_cn": next_row[2],
        "next_term_date": next_term_date.isoformat(),
        "days_until_next_term": int(days_until_next),
        "phenology_note_cn": cur_row[7],
    }


def resolve_effective_season(
    now_dt: datetime,
    explicit_season: Optional[str] = None,
) -> tuple[str, str]:
    """Return ``(season, season_source)`` honoring explicit world-state overrides first.

    When no explicit override is active, align the live season with the authoritative
    24-solar-term ``solar_season`` (e.g. post-立冬 dates resolve to ``winter`` rather
    than contradicting ``立冬后·秋意``).
    """
    explicit = normalize_season(explicit_season)
    if explicit is not None:
        return explicit, "world_state_override"
    local_dt = parse_iso_to_default_tz(now_dt)
    solar_term = resolve_solar_term(local_dt)
    solar_season = normalize_season(solar_term.get("solar_season"))
    if solar_season is not None:
        return solar_season, "calendar_live"
    return calendar_season_for_month(local_dt.month), "calendar_live"


def _build_compact_cue_cn(
    *,
    season_label_cn: str,
    solar_term: dict[str, Any],
    profile: dict[str, Any],
) -> str:
    sun_win = str(profile["sunlight_window"])
    term_name = str(solar_term["term_name_cn"])
    if bool(solar_term.get("is_term_day")):
        tea_short = str(profile["tea_cue_short_cn"])
        cue = f"时令:今日{term_name}·{season_label_cn}({sun_win}日照,{tea_short})"
    else:
        next_name = str(solar_term["next_term_name_cn"])
        days_until = int(solar_term["days_until_next_term"])
        cue = f"时令:{term_name}后·{season_label_cn}({sun_win}日照,离{next_name}{days_until}天)"
    return cue[:56]


def project_season_solar_pulse(
    *,
    now_iso: Optional[str | datetime] = None,
    explicit_season: Optional[str] = None,
    weather: str = "clear",
) -> dict[str, Any]:
    """Pure, 100% zero-write projection of season, 24 solar terms, and microclimate."""
    local_dt = parse_iso_to_default_tz(now_iso)
    season, season_source = resolve_effective_season(local_dt, explicit_season=explicit_season)
    cal_season = calendar_season_for_month(local_dt.month)
    season_label_cn = SEASON_LABELS_CN[season]
    solar_term = resolve_solar_term(local_dt)
    profile = SEASON_MICROCLIMATE_PROFILES[season]

    cur_min = local_dt.hour * 60 + local_dt.minute
    sunrise_min = int(profile["sunrise_min"])
    sunset_min = int(profile["sunset_min"])
    is_daylight_now = bool(sunrise_min <= cur_min < sunset_min)
    is_early_dusk_now = bool((sunset_min - 60) <= cur_min < (sunset_min + 90))

    microclimate = {
        "sunlight_window": str(profile["sunlight_window"]),
        "sunrise_min": sunrise_min,
        "sunset_min": sunset_min,
        "is_daylight_now": is_daylight_now,
        "is_early_dusk_now": is_early_dusk_now,
        "sun_arc": str(profile["sun_arc"]),
        "ambient_temp_base_c": float(profile["ambient_temp_base_c"]),
        "indoor_window_temp_shift_c": float(profile["indoor_window_temp_shift_c"]),
        "tea_cooling_multiplier": float(profile["tea_cooling_multiplier"]),
        "frost_ease": str(profile["frost_ease"]),
        "frost_temp_threshold_c": float(profile["frost_temp_threshold_c"]),
        "frost_melt_multiplier": float(profile["frost_melt_multiplier"]),
        "soundscape_palette": str(profile["soundscape_palette"]),
        "sound_hint_cn": str(profile["sound_hint_cn"]),
        "window_motif": str(profile["window_motif"]),
        "blank_grove_tone_cn": str(profile["blank_grove_tone_cn"]),
        "diner_seasonal_special_cn": str(profile["diner_seasonal_special_cn"]),
        "seasonal_thermal_note_cn": str(profile["seasonal_thermal_note_cn"]),
        "seasonal_spark_note_cn": str(profile["seasonal_spark_note_cn"]),
    }

    compact_cue = _build_compact_cue_cn(
        season_label_cn=season_label_cn,
        solar_term=solar_term,
        profile=profile,
    )

    return {
        "schema_version": PULSE_SCHEMA,
        "season": season,
        "season_label_cn": season_label_cn,
        "season_source": season_source,
        "calendar_season": cal_season,
        "weather": str(weather or "clear").strip().lower() or "clear",
        "solar_term": solar_term,
        "microclimate": microclimate,
        "regular_seasonal_tweaks": copy.deepcopy(REGULAR_SEASONAL_TWEAKS[season]),
        "compact_context_cue_cn": compact_cue,
        "anti_chore_contract": dict(ANTI_CHORE_CONTRACT),
        "llm_calls": 0,
        "zero_write": True,
    }


__all__ = [
    "ANTI_CHORE_CONTRACT",
    "DEFAULT_TZ",
    "PULSE_SCHEMA",
    "REGULAR_SEASONAL_TWEAKS",
    "SEASONAL_CONTEXT_KEYWORDS",
    "SEASON_LABELS_CN",
    "SEASON_MICROCLIMATE_PROFILES",
    "SOLAR_TERMS_24",
    "SOLAR_TERM_DAY_DELTAS",
    "VALID_SEASONS",
    "calendar_season_for_month",
    "normalize_season",
    "parse_iso_to_default_tz",
    "project_season_solar_pulse",
    "resolve_effective_season",
    "resolve_solar_term",
    "solar_term_date",
    "solar_term_day_of_month",
]
