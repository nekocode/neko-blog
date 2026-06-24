#!/usr/bin/env python3
"""聚合各源总数据 → data/stats.json，供首页展示。
总阅读量 = 微信 read_num + 推文 views（知乎公开 API 不提供阅读量，不计入）；
总点赞 = 微信 like + 推文 likes + 知乎点赞（文章/回答 voteup + 想法 like）；
GitHub Star = 仓库 stars 之和。读各源 JSON 真相源，确定性求和。应在各源 sync 之后运行。"""
import json


def load(p):
    try:
        return json.load(open(p, encoding='utf-8'))
    except FileNotFoundError:
        return []


def i(x):
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


repos = load('content/github/repos.json')
tweets = load('content/twitter/tweets.json')
articles = load('content/weixin/articles.json')
zhihu = load('content/zhihu/items.json')

# 知乎点赞：文章/回答用 stats.voteup，想法用 stats.reaction（赞数新字段，旧 like 基本恒 0）；每条只有其一，相加安全
zhihu_likes = sum(i((x.get('stats') or {}).get('voteup')) + i((x.get('stats') or {}).get('reaction'))
                  for x in zhihu)

reads = sum(i(a.get('read_num')) for a in articles) + sum(i(t.get('views')) for t in tweets)
likes = (sum(i((a.get('stats') or {}).get('like')) for a in articles)
         + sum(i(t.get('likes')) for t in tweets) + zhihu_likes)
stars = sum(i(r.get('stars')) for r in repos)

stats = {
    'reads': reads, 'likes': likes, 'stars': stars,
    'articles': len(articles), 'tweets': len(tweets), 'repos': len(repos), 'zhihu': len(zhihu),
}
with open('data/stats.json', 'w', encoding='utf-8') as f:
    json.dump(stats, f, ensure_ascii=False, indent=2)

print(f"data/stats.json：阅读 {reads:,} · 点赞 {likes:,}（含知乎 {zhihu_likes:,}）· Star {stars:,} "
      f"（{len(articles)} 文 / {len(tweets)} 推 / {len(repos)} 仓 / {len(zhihu)} 知乎）")
