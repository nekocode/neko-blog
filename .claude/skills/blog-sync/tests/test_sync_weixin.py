#!/usr/bin/env python3
"""sync_weixin.py 正文清洗纯逻辑单测：尾部微信 UI 垃圾 + 「本文首发于微信公众号」自荐块剥离。
覆盖每条剥离分支与幂等性——确保只删尾部垃圾、绝不误伤正文。
跑：python3 tests/test_sync_weixin.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import sync_weixin as w  # noqa: E402

PASS = FAIL = 0


def eq(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL {name}:\n  got ={got!r}\n  want={want!r}")


def test_strip_self_publish():
    body = ("正文第一段\n\n正文第二段\n\n---\n\n"
            "> 本文首发于微信公众号，[阅读原文](https://mp.weixin.qq.com/s?x=1)。")
    eq("self-publish + 分隔线整体剥离", w.strip_trailing_junk(body),
       "正文第一段\n\n正文第二段")


def test_strip_self_publish_no_rule():
    body = "正文\n\n> 本文首发于微信公众号，[阅读原文](https://x)。"
    eq("无 --- 仅引用前缀也剥离", w.strip_trailing_junk(body), "正文")


def test_keep_midbody_mention():
    # 「本文首发于微信公众号」若在正文中段（后面还有内容），不应被剥离
    body = "本文首发于微信公众号，但后面还有正文。\n\n真正的结尾段落。"
    eq("中段同名文字保留", w.strip_trailing_junk(body), body)


def test_idempotent():
    body = ("正文\n\n---\n\n> 本文首发于微信公众号，[阅读原文](https://x)。")
    once = w.strip_trailing_junk(body)
    eq("二次清洗幂等", w.strip_trailing_junk(once), once)


def test_clean_untouched():
    body = "# 标题\n\n![](images/img_001.png)\n\n正文内容结尾"
    eq("干净正文不动", w.strip_trailing_junk(body), body)


def test_junk_markers_still_work():
    body = "正文\n\n微信扫一扫赞赏作者\n更多垃圾"
    eq("既有 UI 垃圾标记仍生效", w.strip_trailing_junk(body), "正文")


def test_self_publish_after_figure():
    body = ("![](images/img_008.png)\n\n<figcaption>活跃交易</figcaption>\n\n</figure>\n\n"
            "---\n\n> 本文首发于微信公众号，[阅读原文](https://x)。")
    eq("figure 后的自荐块剥离", w.strip_trailing_junk(body),
       "![](images/img_008.png)\n\n<figcaption>活跃交易</figcaption>\n\n</figure>")


def main():
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
