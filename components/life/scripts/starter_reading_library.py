#!/usr/bin/env python3
"""A small, local starter-reading library (one deterministic pick per theme + seed).

This is *content*, not authority: nothing here reads the network, nothing here decides that
she has read anything.  It only gives a brand-new instance a multi-page reading material to
open on its first day, chosen by the same ``seed``/``theme`` that generated its world.

Rules this module keeps:

* the same ``(seed, theme)`` always returns the same title **and** the same pages;
* different themes return different material, and sampled seeds vary inside a theme;
* the library is finite, so two different seeds may legitimately collide -- there is no
  promise of an injective seed -> text mapping;
* pages are separated by the same ``%%PAGE%%`` marker the reading port already understands.

Callers must only use this when they have *no* explicit reading text of their own: an
explicit ``reading_content`` (including the empty string) is the existing explicit-text
semantic and is never replaced here.
"""

from __future__ import annotations

import hashlib
from typing import Any

#: The reading port's own page marker (kept identical to ``one_day_life.PAGE_MARKER``).
PAGE_MARKER = "%%PAGE%%"

#: Local, original starter material.  Each entry is ``title`` + ordered ``pages`` (>= 2).
_STARTER_LIBRARY: dict[str, tuple[dict[str, Any], ...]] = {
    "coastal_town": (
        {
            "entry_id": "tide-notebook",
            "title": "潮汐笔记",
            "pages": [
                "第一页：退潮以后，沙滩上会留下一层细密的波纹，像谁用梳子梳过。"
                "我在扉页上写下：今天从潮水退到最远的地方开始。",
                "第二页：港口的老伯说，看云脚就能猜到下午有没有风。"
                "他把这句话说得像一条家规，我把它抄在页边，留了一条很宽的空白。",
                "第三页：傍晚回去时，鞋里全是沙。我把它们倒在窗台上，"
                "忽然想到，镇子的名字就是从这样一次涨潮里来的。",
            ],
        },
        {
            "entry_id": "lighthouse-letters",
            "title": "灯塔来信",
            "pages": [
                "第一页：灯塔的窗很小，却能看见整条海岸线。"
                "书里夹着一封没寄出的信，收信人的名字被海水洇开了。",
                "第二页：我读到一半，停下来听了一会儿浪。"
                "原来安静不是没有声音，是声音变得很慢。",
                "第三页：信的最后一行写着：「等灯再亮起来，我就回来。」"
                "我把书合上，觉得这句话应该留到明天再读完。",
            ],
        },
        {
            "entry_id": "harbor-mornings",
            "title": "港口清晨",
            "pages": [
                "第一页：天还没亮，鱼市已经在卸货了。"
                "冰块碰撞的声音比人声更早醒来。",
                "第二页：有人在码头边修网，手指又快又稳。"
                "我学着数他们打结的次数，数到第七下就乱了。",
                "第三页：太阳出来时，海面被分成一片一片的亮。"
                "我想，这大概就是小镇每天重新开始的样子。",
            ],
        },
    ),
    "residential_town": (
        {
            "entry_id": "street-corner-calendar",
            "title": "街角日历",
            "pages": [
                "第一页：住宅区的路口有一棵很老的树，树下的长椅总有人坐着。"
                "我把每天看见的座位空着还是有人，记在日历的边角。",
                "第二页：便利店的灯在傍晚最先亮起来，比路灯还早。"
                "收银台后的店员记得每个常客买什么。",
                "第三页：夜里回家，楼道的声控灯一层一层亮上去。"
                "我忽然觉得，这条街也在用它的方式等我回来。",
            ],
        },
        {
            "entry_id": "balcony-garden",
            "title": "阳台花园",
            "pages": [
                "第一页：窗台上的薄荷又长高了一截，叶子边缘有点卷。"
                "书里说这是缺水，也可能是风太大。",
                "第二页：我把花盆挪到墙角，浇水的时候小心避开刚冒出来的新芽。"
                "楼下的猫在阴影里看着我做这些。",
                "第三页：傍晚风停，薄荷的味道散到屋里。"
                "原来照顾一株植物，也需要先学会什么都不做。",
            ],
        },
        {
            "entry_id": "quiet-evenings",
            "title": "安静傍晚",
            "pages": [
                "第一页：晚饭后，街上的声音会慢慢分开：碗筷声、电视声、"
                "远处的车声，各走各的。",
                "第二页：我把椅子挪到窗边，读一段就抬头看一眼对面楼。"
                "有一扇窗一直亮着，像有人在等什么。",
                "第三页：等那扇窗灭了，我才发现自己也读到了最后一页。"
                "有些晚上不需要发生什么，就已经过完了。",
            ],
        },
    ),
    "mountain_town": (
        {
            "entry_id": "spring-steam-notes",
            "title": "汤烟手记",
            "pages": [
                "第一页：入秋的第一场雾，是从汤屋的烟囱里被风吹散的。"
                "镇上的人说，看见雾顺着山道往下走，就该收被褥了。",
                "第二页：我沿着石阶往上走，雾在脚边绕来绕去，像一条不认路的河。"
                "湿气一路跟到山腰，又忽然不见了。",
                "第三页：回到屋里，我把这件事记下来。"
                "原来山里的天气不是预报出来的，是被人一天天看出来的。",
            ],
        },
        {
            "entry_id": "trail-map",
            "title": "山道图志",
            "pages": [
                "第一页：登山道的入口挂着一张手绘图，叉号是塌方，圆圈是能歇脚的地方。"
                "笔画很旧，却每一条都还准。",
                "第二页：走到第三个圆圈时，我把水壶放下，听风穿过松林。"
                "图上的字说这里能看见整片山谷，我没有回头验证。",
                "第三页：下山前，我在图的背面画了一条自己的线。"
                "不知道以后会不会有人照着走。",
            ],
        },
        {
            "entry_id": "winter-lantern",
            "title": "冬夜灯笼",
            "pages": [
                "第一页：汤街的灯笼一到傍晚就逐点亮起来，第一家总是旅馆。"
                "亮灯的次序，比钟表更能说明天黑了。",
                "第二页：我在灯笼下站了一会儿，热气从脚底慢慢升上来。"
                "书里说冬天的山城最懂得如何把冷藏起来。",
                "第三页：回到家，窗户上结了一层白。"
                "我呵了口气，在窗上画了一条山道，很快又化掉了。",
            ],
        },
    ),
    "rural_farming": (
        {
            "entry_id": "field-season-notes",
            "title": "田间节气记",
            "pages": [
                "第一页：插秧那几天，村里没人按时吃饭。"
                "日头到头顶之前要把秧苗插完，早上带的茶会在田埂上晒温。",
                "第二页：书里夹着一张褪色的节气表，有人用铅笔画了个圈，"
                "旁边写着「这天之前把水放干」。",
                "第三页：傍晚收工，田里的水映着天。"
                "我忽然明白，节气不是写在纸上的，是写在水里的。",
            ],
        },
        {
            "entry_id": "orchard-days",
            "title": "果园的日子",
            "pages": [
                "第一页：水车转得很慢，把渠里的水一点点送到果园。"
                "果树的影子随着太阳挪动，像在数一天的长度。",
                "第二页：守园的老人教我分辨哪颗果子熟了："
                "不只看颜色，还要听它被敲时发出的声音。",
                "第三页：我摘下一颗，在衣服上蹭了蹭。"
                "甜味出来的时候，蝉声也刚好停了。",
            ],
        },
        {
            "entry_id": "village-morning",
            "title": "村庄清晨",
            "pages": [
                "第一页：清晨的村子先醒的是鸡，然后是远处的拖拉机。"
                "炊烟一家接一家升起来，像在互相打招呼。",
                "第二页：早市上，菜摊的大婶把最水灵的几把菜留给熟客。"
                "她说这些菜天没亮就从地里拔出来了。",
                "第三页：回家的路上，我把手插进口袋。"
                "村子不大，但每天早晨都像重新铺开的一页纸。",
            ],
        },
    ),
}

THEMES: tuple[str, ...] = tuple(_STARTER_LIBRARY)


def _index_for(seed: int, theme: str) -> int:
    entries = _STARTER_LIBRARY[theme]
    digest = hashlib.sha1(("%s|%d|starter-reading" % (theme, int(seed))).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % len(entries)


def select_starter_reading(seed: int, theme: str) -> dict[str, Any]:
    """The deterministic starter reading for one new ``(seed, theme)`` world.

    Returns a copy: ``title``, ``content`` (pages joined by ``%%PAGE%%``), ``pages`` count,
    ``entry_id`` and the inputs it was selected from.  Unknown themes raise ``ValueError`` --
    silently inventing a generic text would make two different worlds look the same.
    """

    if theme not in _STARTER_LIBRARY:
        raise ValueError("no starter reading for theme %r" % (theme,))
    entries = _STARTER_LIBRARY[theme]
    entry = entries[_index_for(seed, theme)]
    pages = list(entry["pages"])
    return {
        "title": entry["title"],
        "content": ("\n%s\n" % PAGE_MARKER).join(pages),
        "pages": len(pages),
        "entry_id": entry["entry_id"],
        "theme": theme,
        "seed": int(seed),
        "source": "starter_library",
    }


def library_size(theme: str) -> int:
    """How many starter texts a theme has (finite; collisions across seeds are expected)."""

    return len(_STARTER_LIBRARY.get(theme, ()))


__all__ = ["PAGE_MARKER", "THEMES", "library_size", "select_starter_reading"]
