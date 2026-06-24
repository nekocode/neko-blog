#!/usr/bin/env python3
"""抓取用户推文 → content/twitter/tweets.json（增量）→ 渲染。

数据源：opencli twitter tweets <user>（cookie 策略，需 Chrome 登录 X + OpenCLI 扩展）。
增量：按不可变的 tweet id 去重合并；media_urls 是公开 CDN 链接，
仅对「新推文 / 尚无本地图」的条目并发下载到 static/twitter/images/<id>/，
天然增量（已存在文件跳过）。媒体下载是纯 HTTP，与 opencli 抓取解耦。"""
from __future__ import annotations
import argparse
import os
import sys
import json
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402


VIDEO_HINT = ("video.twimg.com",)
VIDEO_EXT = (".mp4", ".m3u8", ".ts", ".webm")


def is_image_url(url: str) -> bool:
    """只本地化图片：渲染层用 <img>，视频留给 X 原链（与现存约定一致）。"""
    low = url.lower()
    if any(h in low for h in VIDEO_HINT):
        return False
    return os.path.splitext(urlparse(url).path)[1].lower() not in VIDEO_EXT


def media_filename(url: str, idx: int) -> str:
    """序号命名 img_NN.ext，与现存 tweets.json 的 local_media 约定一致。"""
    ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
    return f"img_{idx + 1:02d}{ext}"


def download_media(root: str, tweet: dict) -> list:
    """下载一条推文的图片 media_urls → static/twitter/images/<id>/。返回 local_media 相对路径。"""
    tid = str(tweet.get("id") or "")
    urls = [u for u in (tweet.get("media_urls") or []) if is_image_url(u)]
    if not tid or not urls:
        return []
    dest_dir = os.path.join(root, "static", "twitter", "images", tid)
    rels = []
    for i, u in enumerate(urls):
        fn = media_filename(u, i)
        try:
            lib.download_file(u, os.path.join(dest_dir, fn))
            rels.append(f"images/{tid}/{fn}")
        except Exception as e:  # noqa: BLE001
            lib.log("twitter", "media_failed", result="error", id=tid, url=u, error=str(e)[:160])
    return rels


def normalize(tweet: dict) -> dict:
    """补齐渲染所需字段，确保结构稳定。
    _hash = 可分析内容指纹（正文 + 引用推文正文），刻意排除点赞/转发/查看等
    高频变动但与语义无关的 metrics——内容未变即可跳过下游 AI 分析（category/tag）。"""
    tweet.setdefault("local_media", [])
    tweet.setdefault("media_urls", [])
    q = tweet.get("quoted_tweet") or {}
    tweet["_hash"] = lib.content_hash(tweet.get("text", ""), q.get("text", ""))
    return tweet


def run(root: str, user: str | None, limit: int, render: bool) -> dict:
    out = os.path.join(root, "content", "twitter", "tweets.json")
    existing = lib.load_json(out, [])
    ex_index = lib.index_by(existing, "id")

    cmd = lib.opencli_cmd(root) + ["twitter", "tweets"]
    if user:
        cmd.append(user)
    cmd += ["--limit", str(limit), "-f", "json"]
    fetched = lib.run_json(cmd, "twitter", timeout=300)
    if isinstance(fetched, dict):  # 兼容 {data:[...]} 包裹
        fetched = fetched.get("data") or fetched.get("tweets") or []

    incoming = [normalize(t) for t in fetched if t.get("id")]
    # adapter 不返回 local_media：内容未变的推文，从既有记录原样继承本地图，避免 merge 覆盖丢图。
    for t in incoming:
        old = ex_index.get(t["id"])
        if old and not lib.needs_fetch(ex_index, t["id"], t["_hash"]):
            t["local_media"] = old.get("local_media", [])
            oq, nq = old.get("quoted_tweet") or {}, t.get("quoted_tweet") or {}
            if nq:
                nq["local_media"] = oq.get("local_media", [])

    def wants(t):  # 有图但尚无本地副本 → 需下载（新推文或上次缺图）
        return any(is_image_url(u) for u in t.get("media_urls") or []) and not t.get("local_media")

    todo = [t for t in incoming if wants(t)]
    for t, rel in zip(todo, lib.parallel_map(todo, lambda t: download_media(root, t), workers=8, who="twitter")):
        t["local_media"] = rel or []
    # 引用推文独立下载：父推自身无图（不在 todo）但引用了带图推文时，此前会漏下引用图。
    qtodo = []
    for t in incoming:
        q = t.get("quoted_tweet")
        if q and q.get("id") and wants(q):
            qtodo.append(q)
    for q, rel in zip(qtodo, lib.parallel_map(qtodo, lambda q: download_media(root, q), workers=8, who="twitter")):
        q["local_media"] = rel or []

    merged, stats = lib.merge_items(existing, incoming, "id")
    # 回填 _hash：X 时间线深度有限，更老的推文不会再被抓到，但其 text 已存于本地，
    # 直接据此补算内容指纹，保证「每条推文都有 _hash」——下游 AI 分析才不会漏掉老推文。
    backfilled = 0
    for t in merged:
        t.setdefault("media_urls", [])
        t.setdefault("local_media", [])
        if not t.get("_hash"):
            q = t.get("quoted_tweet") or {}
            t["_hash"] = lib.content_hash(t.get("text", ""), q.get("text", ""))
            backfilled += 1
    merged.sort(key=lambda t: int(t.get("id", 0)), reverse=True)
    lib.write_json_atomic(out, merged)
    downloaded = len(todo) + len(qtodo)
    lib.log("twitter", "synced", total=len(merged), fetched=len(incoming),
            downloaded=downloaded, backfilled=backfilled, **stats)

    if render:
        script = os.path.join(root, ".scripts", "render_tweets.py")
        lib.run_text([sys.executable, script], "twitter", timeout=120)
    return {"source": "twitter", "total": len(merged), "downloaded": downloaded,
            "backfilled": backfilled, **stats}


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync X/Twitter → tweets.json (incremental)")
    ap.add_argument("--root", default=lib.find_root())
    ap.add_argument("--user", default=None, help="X 用户名（默认登录账号）")
    ap.add_argument("--limit", type=int, default=250,
                    help="抓取上限（增量合并）。X 时间线深度有限，实际约 200 封顶，更老推文靠增量保留")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    print(json.dumps(run(a.root, a.user, a.limit, render=not a.no_render), ensure_ascii=False))


if __name__ == "__main__":
    main()
