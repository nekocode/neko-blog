#!/usr/bin/env python3
"""从 articles.json 生成微信公众号文章页（content/weixin/<slug>/index.md）。
一文一目录，front matter + 正文 markdown + 本地图片引用。articles.json 是
唯一真相源，本脚本确定性重建 md 目录（不动 images/ 已下载的图片）。"""
import json, os

OUT_DIR = 'content/weixin'
articles = json.load(open(f'{OUT_DIR}/articles.json', encoding='utf-8'))


def front_matter(a):
    title = a['title'].replace('"', '\\"')
    fm = ['---', f'title: "{title}"']
    if a.get('date'):
        fm.append(f'date: {a["date"]}')
    fm += [f'author: "{a.get("author", "nekocode")}"',
           f'account: "{a.get("account", "")}"',
           f'reads: {a.get("read_num", 0)}',
           f'original_url: "{a.get("original_url", "")}"', '---', '']
    return '\n'.join(fm)


def stat_bar(a):
    """正文顶部的互动数据条（阅读/点赞/在看/转发/评论）。无 stats 则不显示。"""
    s = a.get('stats') or {}
    if not s:
        return ''
    parts = [('👁', s.get('read', 0), '阅读'), ('❤️', s.get('like', 0), '点赞'),
             ('👀', s.get('look', 0), '在看'), ('🔁', s.get('share', 0), '转发'),
             ('💬', s.get('comment', 0), '评论')]
    spans = ''.join(f'<span title="{lbl}">{ico} {n:,}</span>' for ico, n, lbl in parts)
    # 样式见 static/css/sources.css 的 .wx-stats（全站 <head> 加载）
    return f'<div class="wx-stats">{spans}</div>\n\n'


for a in articles:
    d = os.path.join(OUT_DIR, a['slug'])
    os.makedirs(d, exist_ok=True)
    body = a.get('content', '')
    with open(os.path.join(d, 'index.md'), 'w', encoding='utf-8') as f:
        f.write(front_matter(a) + stat_bar(a) + body.rstrip() + '\n')

total_reads = sum(a.get('read_num', 0) for a in articles)
# _index 归档页：简介 + 总阅读量。文章列表由主题 list.html 用 .RelPermalink 自动渲染——
# 手搓链接会用错 slug（Hugo 会 lowercase + 删全角标点），导致 404。交给 Hugo 才对。
intro = (f'抓取自我在微信公众号「Nekocode」和「AgileByte」里写的文章。'
         f'<br><span style="color:#888;font-size:.9em">{len(articles)} 篇 · 共 {total_reads:,} 阅读</span>')
with open(f'{OUT_DIR}/_index.md', 'w', encoding='utf-8') as f:
    f.write(f'---\ntitle: 公众号文章\n---\n\n{intro}\n')

print(f"生成 {len(articles)} 篇文章页 + 归档页 -> {OUT_DIR}/")
