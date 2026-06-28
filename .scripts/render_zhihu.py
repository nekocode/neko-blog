#!/usr/bin/env python3
"""从 items.json 生成知乎「混排时间线」页（文章/回答/想法 按时间倒序混在一起）。
- 按「年+半年」分页（同推文）：每个半年 -> content/zhihu/<year>-h<n>.md（/zhihu/<year>/h<n>/），
  每页含半年导航条；最新半年也是普通页，菜单「知乎」直指它（见 hugo.yaml）。
- _index.md 为归档页（/zhihu/）：仅列出全部半年，不嵌正文，避免与最新期重复。
- 长文（文章/回答）显示标题+正文，想法无标题；正文 HTML 已内联本地化图片。
- 图片走 /zhihu/images/...（static/zhihu/images），正文里已是绝对路径。
设计：代码层确定性渲染，自包含 HTML，section 布局只渲染 .Content。items.json 是唯一真相源。"""
import json
import os
import re

OUT_DIR = 'content/zhihu'
data = json.load(open(f'{OUT_DIR}/items.json', encoding='utf-8'))

KIND_LABEL = {'article': '文章', 'answer': '回答', 'pin': '想法'}


def parse_date(s):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s or '')
    return (int(m[1]), int(m[2]), int(m[3])) if m else (1970, 1, 1)


def fmt_num(n):
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return None


def pin_likes(s):
    """想法赞数：优先 reaction_count（新字段）；仅当其缺失（旧数据无该字段）才回落 like_count。
    用 is None 判断而非 `or`：避免 reaction=0（冷门想法常见）被误判为缺失而错误回落到 like。"""
    r = s.get('reaction')
    return r if r is not None else s.get('like')


def render_stats(it):
    s = it.get('stats') or {}
    if it.get('kind') == 'pin':
        parts = [('❤️', pin_likes(s), '赞'), ('🔁', s.get('repin'), '转发'),
                 ('💬', s.get('comment'), '评论')]
    else:
        parts = [('❤️', s.get('voteup'), '赞'), ('💬', s.get('comment'), '评论')]
    spans = []
    for ico, val, lbl in parts:
        v = fmt_num(val)
        if v is not None:
            spans.append(f'<span title="{lbl}">{ico} {v}</span>')
    return f'<div class="zh-metrics">{"".join(spans)}</div>' if spans else ''


def render_title(it):
    title = (it.get('title') or '').strip()
    if not title:
        return ''
    url = it.get('url', '')
    inner = f'<a href="{url}" rel="nofollow noopener" target="_blank">{title}</a>' if url else title
    return f'<h3 class="zh-title">{inner}</h3>'


def render_card(it):
    kind = it.get('kind')
    date = it.get('date', '')
    url = it.get('url', '')
    badge = f'<span class="zh-kind zh-{kind}">{KIND_LABEL.get(kind, "")}</span>'
    link = (f'<a href="{url}" rel="nofollow noopener" target="_blank" class="zh-permalink">{date}</a>'
            if url else date)
    parts = [f'<article class="zh-card zh-{kind}">',
             f'<header class="zh-head">{badge}{link}</header>']
    title = render_title(it)
    if title:
        parts.append(title)
    # 正文是 Markdown：用空行隔断外层 HTML 块，goldmark 才会把段落/代码块/列表正常渲染 + chroma 高亮
    parts += ['<div class="zh-text">', '', it.get('content', '').strip(), '', '</div>']
    stats = render_stats(it)
    if stats:
        parts.append(stats)
    parts.append('</article>')
    return '\n'.join(parts)


# ---- 分桶：(year, half) half=1 上半年 / 2 下半年 ----
buckets = {}
for x in data:
    y, m, _ = parse_date(x.get('date', ''))
    buckets.setdefault((y, 1 if m <= 6 else 2), []).append(x)

periods = sorted(buckets.keys(), reverse=True)


def label(p):
    return f"{p[0]} {'上' if p[1] == 1 else '下'}半年"


def slug(p):
    return f"{p[0]}-h{p[1]}"


def url_of(p):
    return f'/zhihu/{p[0]}/h{p[1]}/'


def period_date(p):
    return max((x.get('date', '') for x in buckets[p]), default='')


def item_likes(it):
    # 单条赞数：回答/文章取 voteup，想法取 reaction（同 render_stats / 热度页口径）
    s = it.get('stats') or {}
    v = pin_likes(s) if it.get('kind') == 'pin' else s.get('voteup')
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def period_likes(p):
    # 该半年所有条目的赞数之和，供首页 link 右侧展示
    return sum(item_likes(x) for x in buckets[p])


def date_label(p):
    return f"{p[0]}/上" if p[1] == 1 else f"{p[0]}/下"


# 导航条 + 样式已移至 Hugo（layouts/_partials/period-nav.html + static/css/sources.css），
# 不再由本脚本写死进每个 md。md 只承载卡片正文。

# 清理旧的半年页与热度页（_index 与各 <year>-h<n>.md / hot*.md 下方重写）
os.makedirs(OUT_DIR, exist_ok=True)
for f in os.listdir(OUT_DIR):
    if re.match(r'\d{4}-h\d\.md$', f) or re.match(r'hot(-\d+)?\.md$', f):
        os.remove(os.path.join(OUT_DIR, f))


def page_body(period):
    # 仅卡片：外层 zh-wrap、导航条、计数行均由 Hugo 渲染（single.html + period-nav.html）
    return '\n'.join(render_card(x) for x in
                     sorted(buckets[period], key=lambda x: x.get('date', ''), reverse=True))


if not periods:
    with open(f'{OUT_DIR}/_index.md', 'w', encoding='utf-8') as f:
        f.write('---\ntitle: 知乎\n---\n\n暂无内容。\n')
    print('知乎无数据，仅写空 _index')
    raise SystemExit(0)

# 每个半年都是普通页（含 url 层级地址 + date 排序 + dateLabel 首页展示）
for p in periods:
    front = (f'---\ntitle: 知乎 · {label(p)}\n'
             f'url: {url_of(p)}\n'
             f'date: {period_date(p)}\n'
             f'dateLabel: {date_label(p)}\n'
             f'count: {len(buckets[p])}\n'  # 供 period-nav.html 渲染每期条数
             f'likes: {period_likes(p)}\n---\n\n')  # 供首页 link 右侧展示赞数
    with open(os.path.join(OUT_DIR, f'{slug(p)}.md'), 'w', encoding='utf-8') as f:
        f.write(front + page_body(p) + '\n')

# _index 归档页：列出全部半年，菜单「知乎」直指最新页
index_links = ''.join(
    f'<li><a href="{url_of(p)}">{label(p)}</a> <span class="zh-count">· {len(buckets[p])} 条</span></li>'
    for p in periods)
index_body = ('<div class="zh-wrap"><ul class="zh-archive">'
              + index_links + '</ul></div>')
with open(os.path.join(OUT_DIR, '_index.md'), 'w', encoding='utf-8') as f:
    f.write(f'---\ntitle: 知乎\n---\n\n{index_body}\n')

print(f"生成 {len(periods)} 个半年页 + 归档页：")
for p in periods:
    print(f"  {label(p):14} {len(buckets[p]):3} 条  {period_date(p)}  -> {url_of(p)}")
print(f"最新页（菜单指向）: {url_of(periods[0])}")

# ---- 热度页：全量按赞数降序，按 HOT_PER_PAGE 分页（hot.md=/zhihu/hot/，hot-N.md=/zhihu/hot/N/）----
# 跨类型口径：回答/文章取 voteup，想法取 reaction（同 render_stats 的赞数来源）。
# 无 date/dateLabel：故被菜单 latestOf 与半年导航 period-nav 自动排除，零回归。
HOT_PER_PAGE = 30


hot_sorted = sorted(data, key=item_likes, reverse=True)
hot_pages = [hot_sorted[i:i + HOT_PER_PAGE] for i in range(0, len(hot_sorted), HOT_PER_PAGE)]
for idx, chunk in enumerate(hot_pages, 1):
    fname = 'hot.md' if idx == 1 else f'hot-{idx}.md'
    url = '/zhihu/hot/' if idx == 1 else f'/zhihu/hot/{idx}/'
    front = (f'---\ntitle: 知乎 · 热度\n'
             f'url: {url}\n'
             f'hot: true\n'           # single.html 据此切到 hot-nav；source-tabs 据此高亮
             f'page: {idx}\n'
             f'pages: {len(hot_pages)}\n'
             f'count: {len(data)}\n---\n\n')
    body = '\n'.join(render_card(x) for x in chunk)
    with open(os.path.join(OUT_DIR, fname), 'w', encoding='utf-8') as f:
        f.write(front + body + '\n')
print(f"热度页（/zhihu/hot/）: {len(hot_pages)} 页 · {len(data)} 条 · {HOT_PER_PAGE}/页")
