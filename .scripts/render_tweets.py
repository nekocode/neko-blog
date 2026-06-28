#!/usr/bin/env python3
"""从 tweets.json 生成按「年+半年」分页的 Hugo 静态推文页。
- 最新半年 -> content/twitter/_index.md（落地页 /twitter/）
- 其余半年 -> content/twitter/<year>-h<n>.md（/twitter/<year>-h<n>/）
- 每页含导航条（列出全部半年，当前高亮）
- 图片走 static/twitter/images，绝对路径 /twitter/images/...
- 自串话题（同 conversation_id 且同作者、≥2 条）渲染为 Thread：独立卡片 + 左侧竖线连接
设计：代码层确定性渲染，自包含 HTML，section 布局只渲染 .Content。
纯函数（build_items 及渲染辅助）与副作用（main 的读写）分离，便于单测。"""
import json, os, re, html

OUT_DIR = 'content/twitter'
IMG_BASE = '/twitter/'  # local_media 形如 images/<id>/x.jpg -> /twitter/images/<id>/x.jpg

MONTHS = {m: i for i, m in enumerate(
    ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], 1)}

HOT_PER_PAGE = 30


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
    # X 源文本已把 & < > 编码为 &amp; &lt; &gt;；先还原再统一转义，避免双重转义出 &amp;amp;。
    text = html.unescape(text)
    esc = html.escape(text)
    esc = URL_RE.sub(lambda m: f'<a href="{m.group(0)}" rel="nofollow noopener" target="_blank">{m.group(0)}</a>', esc)
    return esc.replace('\n', '<br>')

def render_images(paths):
    if not paths: return ''
    # 裸 <img>，不套 <a>：全站正文图统一不跳转（与知乎/公众号/博客一致）
    imgs = ''.join(
        f'<img src="{IMG_BASE}{p}" loading="lazy" alt="">'
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

def render_card(t, reply=False):
    # reply=True：话题内的回复卡，加 tw-reply（左缩进，竖线连接）。每卡都保留自己的日期头部。
    date = fmt_date(t['created_at'])
    url = t.get('url', '')
    link = f'<a href="{url}" rel="nofollow noopener" target="_blank" class="tw-permalink">{date}</a>' if url else date
    cls = 'tw-card tw-reply' if reply else 'tw-card'
    return (f'<article class="{cls}"><header class="tw-head">{link}</header>'
            f'<div class="tw-text">{render_text(t["text"], bool(t.get("local_media")))}</div>'
            f'{render_images(t.get("local_media"))}{render_quote(t.get("quoted_tweet"))}'
            f'{render_metrics(t)}</article>')


# ---- 话题分组（纯函数，可单测）----
def build_items(tweets):
    """把扁平推文列表组装成「条目」序列：自串话题(Thread) 或 单推。
    分组键 = (conversation_id, author)：同一话题且同作者、成员≥2 → Thread，
    组内按 id 升序（旧→新，同官方阅读序）。缺 conversation_id 的老数据各自为单推（降级）。
    以各组首次出现位置定序，保证对无 conversation_id 的旧数据零回归。
    返回 list[dict]：{'tweets': [...], 'is_thread': bool}。"""
    groups, order = {}, []
    for t in tweets:
        cid = t.get('conversation_id') or t.get('id')
        key = (cid, t.get('author'))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)
    items = []
    for key in order:
        members = groups[key]
        if len(members) >= 2:
            members = sorted(members, key=lambda t: int(t['id']))
            items.append({'tweets': members, 'is_thread': True})
        else:
            items.append({'tweets': members, 'is_thread': False})
    return items

def render_item(item):
    ts = item['tweets']
    if not item['is_thread']:
        return render_card(ts[0])
    cards = render_card(ts[0]) + ''.join(render_card(t, reply=True) for t in ts[1:])
    return f'<div class="tw-thread">{cards}</div>'

def item_latest(item):
    return max(item['tweets'], key=lambda t: int(t['id']))

def item_sort_id(item):
    return int(item_latest(item)['id'])

def item_heat(item):
    return max((int(t.get('views') or 0) for t in item['tweets']), default=0)

def half_of(created_at):
    y, m, _ = parse(created_at)
    return (y, 1 if m <= 6 else 2)


def main():
    data = json.load(open(f'{OUT_DIR}/tweets.json', encoding='utf-8'))
    items = build_items(data)

    # ---- 分桶：(year, half) half=1 上半年 / 2 下半年。话题整体落到「最新成员」所在半年，不拆分 ----
    buckets = {}
    for it in items:
        buckets.setdefault(half_of(item_latest(it)['created_at']), []).append(it)

    # 新->旧 排序
    periods = sorted(buckets.keys(), reverse=True)
    def label(p): return f"{p[0]} {'上' if p[1]==1 else '下'}半年"
    def slug(p): return f"{p[0]}-h{p[1]}"             # 扁平文件名 2026-h1.md
    def url_of(p): return f'/twitter/{p[0]}/h{p[1]}/'  # 层级 URL（front matter url 显式指定）

    def bucket_tweets(p):
        return [t for it in buckets[p] for t in it['tweets']]

    def period_date(p):
        latest = max(bucket_tweets(p), key=lambda x: int(x['id']))
        return fmt_date(latest['created_at'])

    def period_reads(p):
        return sum(int(t.get('views') or 0) for t in bucket_tweets(p))

    def date_label(p):
        return f"{p[0]}/上" if p[1] == 1 else f"{p[0]}/下"

    # 清理旧的半年页与热度页（_index 与各 <year>-h<n>.md / hot*.md 下方重写）
    for f in os.listdir(OUT_DIR):
        if re.match(r'\d{4}-h\d\.md$', f) or re.match(r'hot(-\d+)?\.md$', f):
            os.remove(os.path.join(OUT_DIR, f))

    def page_body(period):
        # 仅卡片：外层 tw-wrap、导航条、计数行均由 Hugo 渲染（single.html + period-nav.html）
        ordered = sorted(buckets[period], key=item_sort_id, reverse=True)
        return '\n'.join(render_item(it) for it in ordered)

    for p in periods:
        front = (f'---\ntitle: 推文 · {label(p)}\n'
                 f'url: {url_of(p)}\n'
                 f'date: {period_date(p)}\n'
                 f'dateLabel: {date_label(p)}\n'
                 f'count: {len(bucket_tweets(p))}\n'  # 供 period-nav.html 渲染每期条数（按推文计）
                 f'reads: {period_reads(p)}\n---\n\n')
        with open(os.path.join(OUT_DIR, f'{slug(p)}.md'), 'w', encoding='utf-8') as f:
            f.write(front + page_body(p) + '\n')

    # _index 归档页：列出全部半年，菜单「推文」直指最新页
    index_links = ''.join(
        f'<li><a href="{url_of(p)}">{label(p)}</a> '
        f'<span class="tw-count">· {len(bucket_tweets(p))} 条</span></li>'
        for p in periods)
    index_body = ('<div class="tw-wrap"><ul class="tw-archive">'
                  + index_links + '</ul></div>')
    with open(os.path.join(OUT_DIR, '_index.md'), 'w', encoding='utf-8') as f:
        f.write(f'---\ntitle: 推文\n---\n\n{index_body}\n')

    print(f"生成 {len(periods)} 个半年页 + 归档页：")
    for p in periods:
        print(f"  {label(p):14} {len(bucket_tweets(p)):3} 条  {period_date(p)}  -> {url_of(p)}")
    print(f"最新页（菜单指向）: {url_of(periods[0])}")

    # ---- 热度页：全量按浏览量降序，按 HOT_PER_PAGE 分页；话题整体保留不跨页拆分（故单页略超额）----
    # 无 date/dateLabel：故被菜单 latestOf 与半年导航 period-nav 自动排除，零回归。
    hot_items = sorted(items, key=item_heat, reverse=True)
    hot_pages, cur, cur_n = [], [], 0
    for it in hot_items:
        n = len(it['tweets'])
        if cur and cur_n + n > HOT_PER_PAGE:
            hot_pages.append(cur); cur, cur_n = [], 0
        cur.append(it); cur_n += n
    if cur:
        hot_pages.append(cur)

    for idx, chunk in enumerate(hot_pages, 1):
        fname = 'hot.md' if idx == 1 else f'hot-{idx}.md'
        url = '/twitter/hot/' if idx == 1 else f'/twitter/hot/{idx}/'
        front = (f'---\ntitle: 推文 · 热度\n'
                 f'url: {url}\n'
                 f'hot: true\n'           # single.html 据此切到 hot-nav；source-tabs 据此高亮
                 f'page: {idx}\n'
                 f'pages: {len(hot_pages)}\n'
                 f'count: {len(data)}\n---\n\n')
        body = '\n'.join(render_item(it) for it in chunk)
        with open(os.path.join(OUT_DIR, fname), 'w', encoding='utf-8') as f:
            f.write(front + body + '\n')
    print(f"热度页（/twitter/hot/）: {len(hot_pages)} 页 · {len(data)} 条 · 约 {HOT_PER_PAGE}/页")


if __name__ == '__main__':
    main()
