#!/usr/bin/env python3
"""lib.py 纯逻辑全量单测：hash / 增量 merge / needs_fetch / 原子写 / 项目根定位 /
并发 map。覆盖每条增量判定分支——这是整个 skill 正确性的核心。
跑：python3 tests/test_lib.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import lib  # noqa: E402

PASS = FAIL = 0


def eq(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL {name}: got={got!r} want={want!r}")


def test_content_hash():
    eq("hash stable", lib.content_hash("a b"), lib.content_hash("a b"))
    eq("hash normalize ws", lib.content_hash("a  b"), lib.content_hash("a b"))
    eq("hash strip", lib.content_hash("  x "), lib.content_hash("x"))
    assert lib.content_hash("a") != lib.content_hash("b")
    eq("hash len", len(lib.content_hash("x")), 16)
    eq("hash multi-part", lib.content_hash("a", "b"), lib.content_hash("a b"))


def test_index_and_needs_fetch():
    items = [{"id": "1", "_hash": "h1"}, {"id": "2", "_hash": "h2"}]
    idx = lib.index_by(items, "id")
    eq("index keys", sorted(idx), ["1", "2"])
    eq("needs new", lib.needs_fetch(idx, "3", "h3"), True)          # 新增
    eq("needs changed", lib.needs_fetch(idx, "1", "hX"), True)       # hash 变
    eq("needs unchanged", lib.needs_fetch(idx, "1", "h1"), False)    # hash 同
    eq("index skips missing key", lib.index_by([{"x": 1}], "id"), {})


def test_merge_items():
    existing = [{"id": "1", "v": "old"}, {"id": "2", "v": "keep"}]
    incoming = [{"id": "1", "v": "new"}, {"id": "3", "v": "add"}]
    merged, stats = lib.merge_items(existing, incoming, "id")
    by = {m["id"]: m["v"] for m in merged}
    eq("merge overwrite", by["1"], "new")     # incoming 覆盖
    eq("merge keep", by["2"], "keep")          # existing 独有保留
    eq("merge add", by["3"], "add")            # 新增
    eq("stats added", stats["added"], 1)
    eq("stats updated", stats["updated"], 1)
    eq("stats kept", stats["kept"], 1)
    eq("merge total", len(merged), 3)


def test_merge_empty():
    merged, stats = lib.merge_items([], [{"id": "1"}], "id")
    eq("merge from empty", len(merged), 1)
    eq("merge empty incoming", lib.merge_items([{"id": "1"}], [], "id")[0], [{"id": "1"}])


def test_atomic_json():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sub", "x.json")
        eq("load default", lib.load_json(p, []), [])
        lib.write_json_atomic(p, {"k": "中文"})
        eq("roundtrip", lib.load_json(p, None), {"k": "中文"})
        eq("no tmp left", [f for f in os.listdir(os.path.dirname(p)) if f.endswith(".tmp")], [])


def test_find_root():
    with tempfile.TemporaryDirectory() as d:
        deep = os.path.join(d, "a", "b")
        os.makedirs(deep)
        open(os.path.join(d, "hugo.yaml"), "w").close()
        eq("find root up", lib.find_root(deep), os.path.abspath(d))
        with tempfile.TemporaryDirectory() as d2:
            eq("find root fallback", lib.find_root(d2), os.path.abspath(d2))


def test_twitter_media_helpers():
    import sync_twitter as tw
    eq("img pbs", tw.is_image_url("https://pbs.twimg.com/media/X.jpg"), True)
    eq("video host", tw.is_image_url("https://video.twimg.com/tweet_video/X.mp4"), False)
    eq("mp4 ext", tw.is_image_url("https://x.com/a.mp4"), False)
    eq("png ok", tw.is_image_url("https://pbs.twimg.com/media/Y.png"), True)
    eq("name seq", tw.media_filename("https://pbs.twimg.com/media/Z.jpg", 0), "img_01.jpg")
    eq("name ext default", tw.media_filename("https://x/noext", 2), "img_03.jpg")


def test_weixin_helpers():
    import sync_weixin as wx
    eq("sn from url", wx.extract_sn("http://mp.weixin.qq.com/s?sn=abc&x=1"), "abc")
    eq("sn fallback hashes", len(wx.extract_sn("http://x/s?nope=1")), 16)
    eq("date cn", wx.to_date("2024年3月16日 22:25"), "2024-03-16")
    eq("date iso", wx.to_date("2024-03-16T22:24:49+08:00"), "2024-03-16")
    eq("date single digit", wx.to_date("2024年3月6日"), "2024-03-06")
    eq("slug space", wx.slugify("我的 OKR"), "我的_OKR")
    eq("slug strip unsafe", wx.slugify("a/b:c"), "abc")
    # norm_title：跨源去重，抹平 emoji/空格/标点
    eq("norm emoji variant", wx.norm_title("再合并了两个 PR 🎉"), wx.norm_title("再合并了两个 PR"))
    eq("norm slash variant", wx.norm_title("DCA / 马丁格尔策略"), wx.norm_title("DCA_马丁格尔策略"))
    eq("norm distinct", wx.norm_title("10000 用户！") != wx.norm_title("瞎谈下近况"), True)
    # 图片动态兜底信号 + 扩展名
    eq("titleonly true", wx.body_is_titleonly("# 标题"), True)
    eq("titleonly false", wx.body_is_titleonly("# 标题\n\n正文"), False)
    eq("titleonly blanklines", wx.body_is_titleonly("# 标题\n\n  \n"), True)
    eq("ext from wx_fmt", wx._img_ext("https://x/0?wx_fmt=png&tp=webp"), ".png")
    eq("ext default", wx._img_ext("https://x/0?foo=1"), ".jpg")
    # clean_body：剥头部块 + 截尾部垃圾
    md = "# T\n> 公众号: x\n\n---\n\n# T\n\n正文。\n\nLike the Author\n打赏垃圾"
    eq("clean strips header+junk", wx.clean_body(md), "# T\n\n正文。")
    eq("clean strips date footer", wx.clean_body("a\n---\n# T\n\n广东,2024年9月8日 23:37,"), "# T")
    eq("clean keeps real body", wx.clean_body("a\n---\n# T\n\n正文内容"), "# T\n\n正文内容")
    # strip_trailing_junk：通用尾部垃圾剔除（迁移路径用，不做头部 split）
    eq("strip 收录于 block", wx.strip_trailing_junk("正文。\n\n收录于\nMicroblog\n广东\n,\n2024年3月4日 21:42\n,"), "正文。")
    eq("strip Like the Author", wx.strip_trailing_junk("正文。\n\nLike the Author\n\n收录于\nMicroblog"), "正文。")
    eq("strip keeps clean body", wx.strip_trailing_junk("正文一\n\n正文二"), "正文一\n\n正文二")
    # strip 不做头部 split：正文里的 '---' 分隔线必须保留（clean_body 才切头部）
    eq("strip keeps hr divider", wx.strip_trailing_junk("上文\n\n---\n\n下文"), "上文\n\n---\n\n下文")
    # front matter 解析
    meta, body = wx.parse_front_matter('---\ntitle: "x"\nauthor: "n"\ncategories:\n  - 公众号\n---\n\n正文')
    eq("fm title", meta.get("title"), "x")
    eq("fm body", body, "正文")


def test_weixin_figcaptions():
    import sync_weixin as wx
    # parse_figcaptions：取 figcaption 内文，去内层标签、反转义、丢空
    page = ('<figure><img src="a"><figcaption style="x">基础设施</figcaption></figure>'
            '<figcaption><span>部署到 NLB</span></figcaption>'
            '<figcaption>AT&amp;T</figcaption><figcaption>   </figcaption>')
    eq("parse caps", wx.parse_figcaptions(page), {"基础设施", "部署到 NLB", "AT&T"})
    eq("parse none", wx.parse_figcaptions("<p>无图注</p>"), set())
    # fold_figcaptions：图片后紧跟、命中图注集合 → 包成 figure；其余原样
    caps = {"基础设施", "部署到 NLB"}
    src = "正文。\n\n![Image](images/img_001.png)\n\n基础设施\n\n-   列表项"
    want = ("正文。\n\n<figure>\n\n![Image](images/img_001.png)\n\n"
            "<figcaption>基础设施</figcaption>\n\n</figure>\n\n-   列表项")
    eq("fold hit", wx.fold_figcaptions(src, caps), want)
    # 不命中图注集合 → 图后段落是真正文，绝不动
    body = "![Image](images/x.png)\n\n这是真正文不是图注"
    eq("fold miss keeps body", wx.fold_figcaptions(body, caps), body)
    eq("fold empty caps noop", wx.fold_figcaptions(body, set()), body)
    # 无图片 → 即便文本等于图注也不动（必须紧跟图片）
    eq("fold needs image", wx.fold_figcaptions("基础设施", caps), "基础设施")


def test_claude_stats():
    import claude_stats as cs
    # line_count：空串 0，单行 1，多行按换行数 + 1
    eq("lc empty", cs.line_count(""), 0)
    eq("lc one", cs.line_count("a"), 1)
    eq("lc multi", cs.line_count("a\nb\nc"), 3)
    eq("lc trailing nl", cs.line_count("a\n"), 2)
    # churn：Write 全计新增、删 0；Edit 按 old/new 行数；未知工具 0,0
    eq("churn write", cs.churn_from_tool_use("Write", {"content": "a\nb\nc"}), (3, 0))
    eq("churn edit", cs.churn_from_tool_use("Edit", {"old_string": "x\ny", "new_string": "z"}), (1, 2))
    eq("churn multiedit", cs.churn_from_tool_use(
        "MultiEdit", {"edits": [{"old_string": "a", "new_string": "b\nc"},
                                {"old_string": "d\ne", "new_string": "f"}]}), (3, 3))
    eq("churn unknown", cs.churn_from_tool_use("Read", {}), (0, 0))
    # parse_ts：ISO8601（含 Z）→ aware datetime；坏值/空 → None
    ts = cs.parse_ts("2026-06-13T03:58:00.998Z")
    eq("ts year", (ts.year, ts.month, ts.day), (2026, 6, 13))
    eq("ts aware", ts.tzinfo is not None, True)
    eq("ts bad", cs.parse_ts("not-a-date"), None)
    eq("ts empty", cs.parse_ts(""), None)


def test_gh_translate_select():
    import gh_translate as gt
    h = lib.content_hash("Realtime camera filters")
    repos = [
        {"name": "A", "description": "Realtime camera filters"},                       # 新增 → 需译
        {"name": "B", "description": "x", "description_zh": "已译", "description_zh_hash": lib.content_hash("x")},  # 最新 → 跳过
        {"name": "C", "description": "y", "description_zh": "旧译", "description_zh_hash": "stale"},                # 描述变 → 重译
        {"name": "D", "description": ""},                                              # 无描述 → 跳过
    ]
    eq("need new", gt.needs_translation(repos[0]), True)
    eq("skip current", gt.needs_translation(repos[1]), False)
    eq("retranslate stale", gt.needs_translation(repos[2]), True)
    eq("skip empty", gt.needs_translation(repos[3]), False)
    _ = h


def test_gh_tag():
    import gh_tag as gt
    eq("vocab has 14", len(gt.TAGS), 14)
    eq("flutter in vocab", "Flutter" in gt.TAGSET, True)
    # clean_tags：只留词表内、去重保序、丢非法
    eq("clean valid", gt.clean_tags(["Android", "Library"]), ["Android", "Library"])
    eq("clean drops invalid", gt.clean_tags(["Android", "Banana", "ios"]), ["Android"])  # 大小写敏感
    eq("clean dedup", gt.clean_tags(["Tool", "Tool", "AI"]), ["Tool", "AI"])
    eq("clean empty", gt.clean_tags(None), [])
    # 增量门控
    r = {"name": "X", "description": "d", "language": "Go"}
    eq("need tag new", gt.needs_tagging(r), True)
    r2 = {**r, "tags": ["Tool"], "tags_hash": gt.tag_hash(r)}
    eq("skip tagged current", gt.needs_tagging(r2), False)
    eq("retag changed", gt.needs_tagging({**r2, "description": "changed"}), True)


def test_github_carry_enrichment():
    import sync_github as gh
    ex_index = {
        "A": {"name": "A", "description_zh": "甲", "description_zh_hash": "h1",
              "tags": ["Tool"], "tags_hash": "t1"},
        "B": {"name": "B"},  # 无富化字段
    }
    incoming = [{"name": "A", "_hash": "x"}, {"name": "B", "_hash": "y"},
                {"name": "C", "_hash": "z"}]  # C 是新 repo，无既有记录
    gh.carry_enrichment(incoming, ex_index)
    by = {r["name"]: r for r in incoming}
    eq("carry zh", by["A"]["description_zh"], "甲")          # 译文带过来
    eq("carry zh hash", by["A"]["description_zh_hash"], "h1")
    eq("carry tags", by["A"]["tags"], ["Tool"])              # 标签带过来
    eq("carry tags hash", by["A"]["tags_hash"], "t1")
    eq("no enrich stays bare", "tags" in by["B"], False)     # B 无富化 → 不凭空造
    eq("new repo bare", "description_zh" in by["C"], False)  # 新 repo 不受影响


def test_zhihu_helpers():
    import sync_zhihu as zh
    # to_date：unix 秒 → YYYY-MM-DD；非法/0 → 空
    eq("zh date", zh.to_date(1545837253)[:4], "2018")  # 2018-12-xx（本地时区，只校验年）
    eq("zh date bad", zh.to_date("x"), "")
    eq("zh date zero", zh.to_date(0), "")
    # img_ext：从 URL 路径取，未知回退 .jpg
    eq("zh ext png", zh.img_ext("https://z/v2-abc_r.png?source=x"), ".png")
    eq("zh ext webp", zh.img_ext("https://z/x.webp"), ".webp")
    eq("zh ext default", zh.img_ext("https://z/noext?q=1"), ".jpg")


def test_zhihu_md():
    import zhihu_md as z
    # 代码块：language-xxx → ```xxx 围栏，剥内部高亮 span，&gt; 反转义
    h = ('<div class="highlight"><pre><code class="language-ts">'
         '<span class="kr">const</span> x <span class="o">=&gt;</span> 1</code></pre></div>')
    md = z.to_markdown(h)
    eq("md code fence", "```ts" in md and "const x => 1" in md, True)
    eq("md code no span", "<span" not in md, True)
    # noscript 重复图丢弃，figure 只留真实图
    h2 = '<figure><noscript><img src="/a.jpg"></noscript><img src="/b.jpg"></figure>'
    eq("md drop noscript dup", z.to_markdown(h2).count("!["), 1)
    eq("md keeps real img", "![](/b.jpg)" in z.to_markdown(h2), True)
    # img 懒加载：跳过 data: 占位，优先 data-actualsrc > data-original > src
    eq("md img skip placeholder", z.to_markdown(
        '<img src="data:image/svg+xml;utf8,x" data-original="https://z/real.jpg">'),
       "![](https://z/real.jpg)")
    eq("md img actualsrc priority", z.to_markdown(
        '<img src="https://z/thumb.jpg" data-actualsrc="https://z/full.jpg">'),
       "![](https://z/full.jpg)")
    eq("md img plain src", z.to_markdown('<img src="https://z/a.png">'), "![](https://z/a.png)")
    eq("md img all placeholder skipped", z.to_markdown('<img src="data:image/svg+xml;utf8,x">'), "")
    # 标题 / 强调 / 链接 / 行内 code
    eq("md heading", z.to_markdown("<h2>标题</h2>"), "## 标题")
    # 强调用 HTML 标签（绕开中文全角标点旁 ** 不渲染的 CommonMark CJK 边界问题）
    eq("md bold", z.to_markdown("<p>这是<b>粗</b>体</p>"), "这是<strong>粗</strong>体")
    eq("md bold cjk punct", z.to_markdown("<p><strong>要点：</strong>正文</p>"),
       "<strong>要点：</strong>正文")
    eq("md italic", z.to_markdown("<p><em>斜</em>体</p>"), "<em>斜</em>体")
    eq("md link", z.to_markdown('<a href="http://x">t</a>'), "[t](http://x)")
    # 知乎用户提及（/people/ 链接）→ 去链接只留文本；含不带 @ 的昵称；非 people 链接（如注解文档）保留
    eq("md user mention strip", z.to_markdown(
        '<a href="https://www.zhihu.com/people/abc">@justjavac</a>'), "@justjavac")
    eq("md user mention nickname", z.to_markdown(
        '<a href="https://www.zhihu.com/people/xyz">周源</a>'), "周源")
    eq("md keep doc at-link", z.to_markdown(
        '<a href="http://kotlinlang.org/x">@JvmField</a>'), "[@JvmField](http://kotlinlang.org/x)")
    # 知乎外链中转 link.zhihu.com/?target= → 解码成目标 url 直接用
    eq("md resolve redirect", z.to_markdown(
        '<a href="https://link.zhihu.com/?target=https%3A//github.com/a/b">b</a>'),
       "[b](https://github.com/a/b)")
    eq("md resolve redirect fn", z.resolve_redirect(
        "http://link.zhihu.com/?target=http%3A//nekocode.cn/"), "http://nekocode.cn/")
    eq("md non-redirect untouched", z.resolve_redirect("https://github.com/x"), "https://github.com/x")
    eq("md inline code", z.to_markdown("用 <code>useMemo</code> 包"), "用 `useMemo` 包")
    # 列表（有序/无序）/ 引用 / br 硬换行
    eq("md ul", z.to_markdown("<ul><li>a</li><li>b</li></ul>"), "- a\n- b")
    eq("md ol", z.to_markdown("<ol><li>a</li><li>b</li></ol>"), "1. a\n2. b")
    # li 内嵌 <p>（知乎常见）：段落分隔不得把 marker 与内容拆开
    eq("md li wrap p", z.to_markdown("<ul><li><p>甲</p></li><li><p>乙</p></li></ul>"), "- 甲\n- 乙")
    # li 多段落：marker 接首段，次段按缩进续行
    eq("md li multi p", z.to_markdown("<ul><li><p>甲</p><p>乙</p></li></ul>"), "- 甲\n\n  乙")
    eq("md blockquote", z.to_markdown("<blockquote><p>引</p></blockquote>"), "> 引")
    # blockquote 内代码块：还原后每行都要带 "> " 前缀，否则代码行跳出引用、渲染错乱
    eq("md blockquote code", z.to_markdown(
        '<blockquote><pre><code class="language-xml">&lt;a/&gt;\nb</code></pre></blockquote>'),
       "> ```xml\n> <a/>\n> b\n> ```")
    # blockquote 内 文字 + 代码：空行也带引用前缀
    eq("md blockquote text+code", z.to_markdown(
        '<blockquote><p>说明</p><pre><code class="language-js">x</code></pre></blockquote>'),
       "> 说明\n>\n> ```js\n> x\n> ```")
    eq("md br hardbreak", z.to_markdown("a<br>b"), "a  \nb")
    eq("md empty", z.to_markdown(""), "")
    eq("md no span residue", "<span" not in z.to_markdown("<p><span>x</span>y</p>"), True)
    # markdown 图片本地化辅助
    eq("md extract imgs", z.extract_md_img_urls("![](u1) x ![](u2) ![](u1)"), ["u1", "u2"])
    eq("md localize", z.localize_md_imgs("![](u1) ![](u2)", {"u1": "/local/1.jpg"}),
       "![](/local/1.jpg) ![](u2)")


def test_parallel_map():
    eq("pmap square", lib.parallel_map([1, 2, 3], lambda x: x * x, workers=3), [1, 4, 9])
    eq("pmap empty", lib.parallel_map([], lambda x: x), [])
    # 单条异常 → None 占位，不中断其余
    out = lib.parallel_map([1, 0, 2], lambda x: 10 // x, workers=3)
    eq("pmap fault isolation", (out[0], out[1], out[2]), (10, None, 5))


def main():
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
