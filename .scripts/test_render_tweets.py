#!/usr/bin/env python3
"""render_tweets.build_items 纯逻辑单测：自串成组 / 单推不成组 / 缺字段降级 /
跨半年归桶 / 分组排序。覆盖话题串联的全部判定分支。
跑：python3 .scripts/test_render_tweets.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_tweets as rt  # noqa: E402

PASS = FAIL = 0


def eq(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


def tw(id, conv=None, author="nekocode_cn", created="Mon Jun 01 00:00:00 +0000 2026", views=0):
    return {"id": id, "conversation_id": conv, "author": author,
            "created_at": created, "views": views, "text": ""}


def test_self_thread_groups():
    # 同 conversation_id + 同作者 + ≥2 → Thread，组内按 id 升序
    items = rt.build_items([tw("3", "1"), tw("1", "1"), tw("2", "1")])
    eq("thread count", len(items), 1)
    eq("is_thread", items[0]["is_thread"], True)
    eq("thread order asc", [t["id"] for t in items[0]["tweets"]], ["1", "2", "3"])


def test_singletons_no_group():
    # 不同 conversation_id → 各自单推
    items = rt.build_items([tw("10", "10"), tw("20", "20")])
    eq("singleton count", len(items), 2)
    eq("not thread", [it["is_thread"] for it in items], [False, False])


def test_missing_conversation_id_degrades():
    # 缺 conversation_id 的老数据：以自身 id 兜底，互不成组（零回归）
    items = rt.build_items([{"id": "1", "author": "a", "created_at": "Mon Jun 01 00:00:00 +0000 2026"},
                            {"id": "2", "author": "a", "created_at": "Mon Jun 01 00:00:00 +0000 2026"}])
    eq("degrade to singletons", len(items), 2)
    eq("degrade not thread", all(not it["is_thread"] for it in items), True)


def test_reply_to_other_not_threaded():
    # 同 conversation_id 但作者不同（回复他人，正常时间线不会出现）→ 不串
    items = rt.build_items([tw("1", "1", author="nekocode_cn"), tw("2", "1", author="someone")])
    eq("cross-author not threaded", len(items), 2)
    eq("cross-author singletons", all(not it["is_thread"] for it in items), True)


def test_item_helpers():
    item = {"tweets": [tw("5", "1", views=10, created="Mon Jun 30 00:00:00 +0000 2026"),
                       tw("9", "1", views=3, created="Tue Jul 01 00:00:00 +0000 2026")],
            "is_thread": True}
    eq("latest by id", rt.item_latest(item)["id"], "9")
    eq("sort id = latest", rt.item_sort_id(item), 9)
    eq("heat = max views", rt.item_heat(item), 10)
    eq("half of latest", rt.half_of(rt.item_latest(item)["created_at"]), (2026, 2))


def test_render_text_entities():
    # X 源文本含 &amp; &lt; &gt;：还原后单次转义，浏览器最终显示 & < >（而非 &amp;）
    eq("amp 不双重转义", rt.render_text("灵感 &amp; 基因", False), "灵感 &amp; 基因")
    eq("lt/gt 不双重转义", rt.render_text("svgr &gt; react 18", False), "svgr &gt; react 18")
    eq("裸 & 也正确转义", rt.render_text("a & b", False), "a &amp; b")


def main():
    for fn in [test_self_thread_groups, test_singletons_no_group,
               test_missing_conversation_id_degrades, test_reply_to_other_not_threaded,
               test_item_helpers, test_render_text_entities]:
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
