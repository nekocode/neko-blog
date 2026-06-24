#!/usr/bin/env python3
"""从 repos.json 生成 GitHub 仓库表格 + tag 过滤页。
- /github/         全部仓库 + 顶部 tag tab 栏
- /github/tag/<t>/ 每个 tag 一个独立静态页（仅含该 tag 的仓库），同样的 tab 栏
一仓库一行：仓库名 / star / fork / 语言 / 描述 / tags。按 star 倒序。
tag 页用扁平文件 + url front matter + _build.list:never：独立 path、不污染首页列表、无空中间节点。"""
import json, os, html, glob

OUT_DIR = 'content/github'
repos = sorted(json.load(open(f'{OUT_DIR}/repos.json', encoding='utf-8')),
               key=lambda r: r['stars'], reverse=True)

# 已合并 PR（贡献于他人仓库）：sync_github.py 写入；首次渲染早于同步时可能缺失，容缺。
PULLS_PATH = f'{OUT_DIR}/pulls.json'
pulls = (sorted(json.load(open(PULLS_PATH, encoding='utf-8')),
                key=lambda p: p['merged_at'], reverse=True)
         if os.path.exists(PULLS_PATH) else [])

# tag 词表顺序（与 gh_tag.py 的 TAGS 一致），只展示有仓库的 tag
TAGS_ORDER = ['Android', 'iOS', 'Web', 'Backend', 'Desktop', 'Flutter',
              'Game', 'AI', 'Infra', 'Graphics', 'Security', 'Tool', 'Library', 'Plugin']
present = [t for t in TAGS_ORDER if any(t in r.get('tags', []) for r in repos)]

LANG_COLOR = {
    'Java': '#b07219', 'Kotlin': '#A97BFF', 'Python': '#3572A5', 'Rust': '#dea584',
    'TypeScript': '#3178c6', 'JavaScript': '#f1e05a', 'C++': '#f34b7d', 'C': '#555555',
    'C#': '#178600', 'Go': '#00ADD8', 'Dart': '#00B4AB', 'Swift': '#F05138',
    'HTML': '#e34c26', 'CSS': '#563d7c', 'Svelte': '#ff3e00', 'Shell': '#89e051',
    'PowerShell': '#012456', 'Vim Script': '#199f4b',
}


def fmt_num(n):
    try:
        return f"{int(n):,}"  # 千分符完整数字，如 2,153
    except (TypeError, ValueError):
        return "0"  # 字段缺失/非数值时降级为 0，不让整页渲染崩溃


def tag_slug(t): return t.lower()
def tag_url(t): return f'/github/{tag_slug(t)}/'


def render_row(r, idx):
    name = html.escape(r['name'])
    repo = f'<a href="{html.escape(r["url"])}" class="gh-name" target="_blank" rel="noopener">{name}</a>'
    if r['homepage']:
        repo += (f' <a href="{html.escape(r["homepage"])}" rel="nofollow noopener" target="_blank"'
                 f' class="gh-home" title="主页">↗</a>')
    if r['archived']:
        repo += ' <span class="gh-archived">已归档</span>'
    if r['language']:
        c = LANG_COLOR.get(r['language'], '#999')
        lang = f'<span class="gh-lang"><i style="background:{c}"></i>{html.escape(r["language"])}</span>'
    else:
        lang = ''
    desc = html.escape(r.get('description_zh') or r['description'])
    tags = ''.join(f'<a class="gh-tag" href="{tag_url(t)}">{html.escape(t)}</a>' for t in r.get('tags', []))
    return (f'<tr><td class="gh-c-idx">{idx}</td><td class="gh-c-repo">{repo}</td>'
            f'<td class="gh-c-num">🌟 {fmt_num(r["stars"])}</td>'
            f'<td class="gh-c-num">🔱 {fmt_num(r["forks"])}</td>'
            f'<td class="gh-c-lang">{lang}</td>'
            f'<td class="gh-c-desc">{desc}<div class="gh-tags">{tags}</div></td></tr>')


def render_source_tabs(active):
    """顶层来源 tab：仓库 / PR（各带数量），当前高亮。active ∈ {'repo','pr'}。
    高于 tag tab 一级：仓库页其下再接 tag tab 栏，PR 页则无。"""
    def item(label, url, count, cur):
        cnt = f' <span class="gh-tab-n">{count}</span>'
        cls = 'gh-stab gh-stab-cur' if cur else 'gh-stab'
        return (f'<span class="{cls}">{label}{cnt}</span>' if cur
                else f'<a class="{cls}" href="{url}">{label}{cnt}</a>')
    items = [item('仓库', '/github/', len(repos), active == 'repo'),
             item('PR', '/github/pulls/', len(pulls), active == 'pr')]
    return f'<nav class="gh-stabs">{"".join(items)}</nav>'


def render_tabs(active):
    """顶部 tab 栏：全部 + 各 tag（带数量），当前高亮。active=None 表示「全部」。"""
    def item(label, url, count, cur):
        cnt = f' <span class="gh-tab-n">{count}</span>' if count is not None else ''
        if cur:
            return f'<span class="gh-tab gh-tab-cur">{label}{cnt}</span>'
        return f'<a class="gh-tab" href="{url}">{label}{cnt}</a>'
    items = [item('全部', '/github/', len(repos), active is None)]
    for t in present:
        n = sum(1 for r in repos if t in r.get('tags', []))
        items.append(item(html.escape(t), tag_url(t), n, t == active))
    return f'<nav class="gh-tabs">{"".join(items)}</nav>'


# 样式已移至 static/css/sources.css（全站 <head> 加载），不再写死进 md
HEAD = ('<thead><tr><th class="gh-c-idx">#</th><th>仓库</th><th class="gh-c-num">🌟</th>'
        '<th class="gh-c-num">🔱</th><th>语言</th><th>描述</th></tr></thead>')


def page_body(subset, active):
    total_stars = sum(r['stars'] for r in subset)
    summary = f'<div class="gh-summary">{len(subset)} 个仓库 · 共 {fmt_num(total_stars)} stars</div>'
    rows = '\n'.join(render_row(r, i) for i, r in enumerate(subset, 1))
    return (render_source_tabs('repo') + render_tabs(active) + summary
            + f'<table class="gh-table">{HEAD}<tbody>\n{rows}\n</tbody></table>')


# ---- PR 页：标题 / 目标仓库(🌟) / 合并日期，按合并日期降序，单页全部 ----
PR_HEAD = ('<thead><tr><th class="gh-c-idx">#</th><th>PR</th>'
           '<th>目标仓库</th><th class="gh-c-num">合并</th></tr></thead>')


def render_pull_row(p, idx):
    title = html.escape(p.get('title') or '')
    pr = f'<a href="{html.escape(p["url"])}" class="gh-pr-title" target="_blank" rel="noopener">{title}</a>'
    repo_name = html.escape(p.get('repo') or '')
    repo = (f'<a href="{html.escape(p["repo_url"])}" class="gh-name" target="_blank" rel="noopener">{repo_name}</a>'
            if p.get('repo_url') else repo_name)
    stars = f'<span class="gh-pr-star">🌟 {fmt_num(p.get("repo_stars", 0))}</span>'
    return (f'<tr><td class="gh-c-idx">{idx}</td>'
            f'<td class="gh-c-desc">{pr}</td>'
            f'<td class="gh-c-repo">{repo} {stars}</td>'
            f'<td class="gh-c-num">{p.get("merged_at") or ""}</td></tr>')


def pulls_body():
    repo_count = len({p.get('repo') for p in pulls if p.get('repo')})
    summary = f'<div class="gh-summary">{len(pulls)} 个已合并 PR · 贡献于 {repo_count} 个仓库</div>'
    rows = '\n'.join(render_pull_row(p, i) for i, p in enumerate(pulls, 1))
    return (render_source_tabs('pr') + summary
            + f'<table class="gh-table">{PR_HEAD}<tbody>\n{rows}\n</tbody></table>')


# 清理旧 tag 页，按当前词表重写
for f in glob.glob(f'{OUT_DIR}/tag-*.md'):
    os.remove(f)

# 全部仓库页（落地 /github/）
with open(f'{OUT_DIR}/_index.md', 'w', encoding='utf-8') as f:
    f.write(f'---\ntitle: GitHub\n---\n\n{page_body(repos, None)}\n')

# 每个 tag 一个独立静态页 /github/tag/<slug>/
for t in present:
    subset = [r for r in repos if t in r.get('tags', [])]
    front = (f'---\ntitle: "GitHub · {t}"\n'
             f'url: {tag_url(t)}\n'
             f'build:\n  list: never\n  render: always\n---\n\n')
    with open(f'{OUT_DIR}/tag-{tag_slug(t)}.md', 'w', encoding='utf-8') as f:
        f.write(front + page_body(subset, t) + '\n')

# PR 页 /github/pulls/（独立静态页，不污染首页列表）。无数据也生成，避免来源 tab 链接 404。
pr_front = ('---\ntitle: "GitHub · PR"\n'
            'url: /github/pulls/\n'
            'build:\n  list: never\n  render: always\n---\n\n')
with open(f'{OUT_DIR}/pulls.md', 'w', encoding='utf-8') as f:
    f.write(pr_front + pulls_body() + '\n')

print(f"生成 GitHub 主页 + {len(present)} 个 tag 页：{', '.join(present)}")
print(f"PR 页（/github/pulls/）: {len(pulls)} 个已合并 PR")
