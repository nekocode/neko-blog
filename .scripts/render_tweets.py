#!/usr/bin/env python3
"""从 tweets.json 生成按「年+半年」分页的 Hugo 静态推文页。
- 最新半年 -> content/twitter/_index.md（落地页 /twitter/）
- 其余半年 -> content/twitter/<year>-h<n>.md（/twitter/<year>-h<n>/）
- 每页含导航条（列出全部半年，当前高亮）
- 图片走 static/twitter/images，绝对路径 /twitter/images/...
设计：代码层确定性渲染，自包含 HTML，section 布局只渲染 .Content。"""
import json, os, re, html

OUT_DIR = 'content/twitter'
IMG_BASE = '/twitter/'  # local_media 形如 images/<id>/x.jpg -> /twitter/images/<id>/x.jpg
data = json.load(open(f'{OUT_DIR}/tweets.json', encoding='utf-8'))

MONTHS = {m: i for i, m in enumerate(
    ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], 1)}

def parse(s):
    p = s.split()
    return int(p[5]), MONTHS[p[1]], int(p[2])  # year, month, day

def fmt_date(s):
    y, m, d = parse(s)
    return f"{y}-{m:02d}-{d:02d}"

def fmt_num(n):
    try: n = int(n)
    except (TypeError, ValueError): return None
    return f"{n:,}"  # 千分符完整数字，如 1,234,567

# URL 仅含 ASCII；限定 RFC 3986 合法字符，遇 CJK/全角标点（紧贴链接后的正文）即停止，
# 避免 \S+ 把链接后无空格的中文一并吞进 href。
URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")

def render_text(text, has_media):
    text = text or ''
    if has_media:
        text = re.sub(r'\s*https://t\.co/\w+\s*$', '', text)
    esc = html.escape(text)
    esc = URL_RE.sub(lambda m: f'<a href="{m.group(0)}" rel="nofollow noopener" target="_blank">{m.group(0)}</a>', esc)
    return esc.replace('\n', '<br>')

def render_images(paths):
    if not paths: return ''
    imgs = ''.join(
        f'<a href="{IMG_BASE}{p}" target="_blank"><img src="{IMG_BASE}{p}" loading="lazy" alt=""></a>'
        for p in paths)
    cls = 'tw-imgs' + (' single' if len(paths) == 1 else '')
    return f'<div class="{cls}">{imgs}</div>'

def render_quote(q):
    if not q: return ''
    author = html.escape(q.get('author', ''))
    name = html.escape(q.get('name', '') or author)
    body = render_text(q.get('text', ''), bool(q.get('local_media')))
    imgs = render_images(q.get('local_media'))
    head = f'<span class="tw-qname">{name}</span> <span class="tw-qhandle">@{author}</span>'
    url = q.get('url', '')
    if url:
        head = f'<a href="{url}" rel="nofollow noopener" target="_blank" class="tw-qlink">{head}</a>'
    return f'<blockquote class="tw-quote">{head}<div class="tw-qtext">{body}</div>{imgs}</blockquote>'

def render_metrics(t):
    parts = []
    def add(icon, val, label):
        v = fmt_num(val)
        if v is not None:
            parts.append(f'<span title="{label}">{icon} {v}</span>')
    add('👁', t.get('views'), '查看'); add('❤️', t.get('likes'), '点赞')
    add('🔁', t.get('retweets'), '转发'); add('💬', t.get('replies'), '回复')
    return f'<div class="tw-metrics">{"".join(parts)}</div>'

def render_card(t):
    date = fmt_date(t['created_at'])
    url = t.get('url', '')
    link = f'<a href="{url}" rel="nofollow noopener" target="_blank" class="tw-permalink">{date}</a>' if url else date
    return (f'<article class="tw-card"><header class="tw-head">{link}</header>'
            f'<div class="tw-text">{render_text(t["text"], bool(t.get("local_media")))}</div>'
            f'{render_images(t.get("local_media"))}{render_quote(t.get("quoted_tweet"))}'
            f'{render_metrics(t)}</article>')

# ---- 分桶：(year, half) half=1 上半年 / 2 下半年 ----
buckets = {}
for x in data:
    y, m, _ = parse(x['created_at'])
    half = 1 if m <= 6 else 2
    buckets.setdefault((y, half), []).append(x)

# 新->旧 排序
periods = sorted(buckets.keys(), reverse=True)
def label(p): return f"{p[0]} {'上' if p[1]==1 else '下'}半年"
def slug(p): return f"{p[0]}-h{p[1]}"            # 扁平文件名 2026-h1.md
def url_of(p): return f'/twitter/{p[0]}/h{p[1]}/'  # 层级 URL /twitter/2026/h1/（front matter url 显式指定）

def period_date(p):
    # 该半年内最新推文的日期，供首页排序/展示
    latest = max(buckets[p], key=lambda x: int(x['id']))
    y, m, d = parse(latest['created_at'])
    return f"{y}-{m:02d}-{d:02d}"

def period_reads(p):
    # 该半年所有推文的浏览量之和
    return sum(int(t.get('views') or 0) for t in buckets[p])

# 导航条 + 样式已移至 Hugo（layouts/_partials/period-nav.html + static/css/sources.css），
# 不再由本脚本写死进每个 md。md 只承载卡片正文。

# 清理旧的半年页与热度页（_index 与各 <year>-h<n>.md / hot*.md 下方重写）
for f in os.listdir(OUT_DIR):
    if re.match(r'\d{4}-h\d\.md$', f) or re.match(r'hot(-\d+)?\.md$', f):
        os.remove(os.path.join(OUT_DIR, f))

def page_body(period):
    # 仅卡片：外层 tw-wrap、导航条、计数行均由 Hugo 渲染（single.html + period-nav.html）
    return '\n'.join(render_card(t) for t in
                     sorted(buckets[period], key=lambda x: int(x['id']), reverse=True))

# 每个半年都是普通页（含 date 供排序，dateLabel 供首页展示半年区间）
def date_label(p):
    return f"{p[0]}/上" if p[1] == 1 else f"{p[0]}/下"

for p in periods:
    front = (f'---\ntitle: 推文 · {label(p)}\n'
             f'url: {url_of(p)}\n'
             f'date: {period_date(p)}\n'
             f'dateLabel: {date_label(p)}\n'
             f'count: {len(buckets[p])}\n'  # 供 period-nav.html 渲染每期条数
             f'reads: {period_reads(p)}\n---\n\n')
    with open(os.path.join(OUT_DIR, f'{slug(p)}.md'), 'w', encoding='utf-8') as f:
        f.write(front + page_body(p) + '\n')

# _index 归档页：列出全部半年，菜单「推文」直指最新页
index_links = ''.join(
    f'<li><a href="{url_of(p)}">{label(p)}</a> <span class="tw-count">· {len(buckets[p])} 条</span></li>'
    for p in periods)
index_body = ('<div class="tw-wrap"><ul class="tw-archive">'
              + index_links + '</ul></div>')
with open(os.path.join(OUT_DIR, '_index.md'), 'w', encoding='utf-8') as f:
    f.write(f'---\ntitle: 推文\n---\n\n{index_body}\n')

print(f"生成 {len(periods)} 个半年页 + 归档页：")
for p in periods:
    print(f"  {label(p):14} {len(buckets[p]):3} 条  {period_date(p)}  -> {url_of(p)}")
print(f"最新页（菜单指向）: {url_of(periods[0])}")

# ---- 热度页：全量按浏览量降序，每页固定 30 条（hot.md=/twitter/hot/，hot-N.md=/twitter/hot/N/）----
# 无 date/dateLabel：故被菜单 latestOf 与半年导航 period-nav 自动排除，零回归。
HOT_PER_PAGE = 30


def tweet_heat(t):
    try:
        return int(t.get('views') or 0)
    except (TypeError, ValueError):
        return 0


hot_sorted = sorted(data, key=tweet_heat, reverse=True)
hot_pages = [hot_sorted[i:i + HOT_PER_PAGE] for i in range(0, len(hot_sorted), HOT_PER_PAGE)]
for idx, chunk in enumerate(hot_pages, 1):
    fname = 'hot.md' if idx == 1 else f'hot-{idx}.md'
    url = '/twitter/hot/' if idx == 1 else f'/twitter/hot/{idx}/'
    front = (f'---\ntitle: 推文 · 热度\n'
             f'url: {url}\n'
             f'hot: true\n'           # single.html 据此切到 hot-nav；source-tabs 据此高亮
             f'page: {idx}\n'
             f'pages: {len(hot_pages)}\n'
             f'count: {len(data)}\n---\n\n')
    body = '\n'.join(render_card(t) for t in chunk)
    with open(os.path.join(OUT_DIR, fname), 'w', encoding='utf-8') as f:
        f.write(front + body + '\n')
print(f"热度页（/twitter/hot/）: {len(hot_pages)} 页 · {len(data)} 条 · {HOT_PER_PAGE}/页")
