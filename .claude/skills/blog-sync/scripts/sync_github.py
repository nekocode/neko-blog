#!/usr/bin/env python3
"""抓取用户 GitHub 仓库 → content/github/repos.json（增量）→ 渲染。

数据源：gh graphql 一次分页拿全量仓库（含 pushedAt 与默认分支 commit oid）。
hash：默认分支最新 commit oid（无则用 pushedAt）。存进每条记录 _hash，
下次对比即可知哪些仓库有新提交 → 结构化报告 added/updated/kept。
gh 是纯 HTTP，无需浏览器，可与其他源并行。"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

QUERY = """
query($login:String!, $cursor:String){
  user(login:$login){
    repositories(first:100, after:$cursor, ownerAffiliations:OWNER,
                 privacy:PUBLIC,
                 orderBy:{field:STARGAZERS, direction:DESC}){
      pageInfo{ hasNextPage endCursor }
      nodes{
        name description stargazerCount forkCount url homepageUrl
        isArchived isFork isPrivate pushedAt
        primaryLanguage{ name }
      }
    }
  }
}
"""


# 已合并 PR 搜索：对应 github.com/pulls?q=is:pr author:<login> archived:false is:merged -user:<login>
# 即「给他人仓库贡献且已合并」的 PR（-user:<login> 排除自己的仓库）。search type:ISSUE 涵盖 PR。
PR_QUERY = """
query($q:String!, $cursor:String){
  search(query:$q, type:ISSUE, first:100, after:$cursor){
    pageInfo{ hasNextPage endCursor }
    nodes{
      ... on PullRequest {
        title url number mergedAt additions deletions
        repository{ nameWithOwner url stargazerCount isPrivate primaryLanguage{ name } }
      }
    }
  }
}
"""


def current_login() -> str:
    return lib.run_text(["gh", "api", "user", "--jq", ".login"], "github").strip()


def fetch_repos(login: str) -> list:
    """分页拉全量仓库节点。"""
    nodes, cursor = [], None
    while True:
        cmd = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"login={login}"]
        cmd += ["-f", f"cursor={cursor}"] if cursor else ["-F", "cursor="]
        page = lib.run_json(cmd, "github")["data"]["user"]["repositories"]
        nodes.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = page["pageInfo"]["endCursor"]


def fetch_pulls(login: str) -> list:
    """分页拉「已合并、贡献于他人公开仓库」的全部 PR 节点。
    is:public 服务端过滤私有仓库；再按 repository.isPrivate 兜底，绝不外泄私有库 PR。"""
    q = f"is:pr author:{login} archived:false is:merged is:public -user:{login}"
    nodes, cursor = [], None
    while True:
        cmd = ["gh", "api", "graphql", "-f", f"query={PR_QUERY}", "-f", f"q={q}"]
        cmd += ["-f", f"cursor={cursor}"] if cursor else ["-F", "cursor="]
        page = lib.run_json(cmd, "github")["data"]["search"]
        for n in page["nodes"]:
            if not n.get("url"):  # 跳过 type:ISSUE 里的空壳节点
                continue
            if (n.get("repository") or {}).get("isPrivate"):  # 兜底：私有库 PR 一律剔除
                continue
            nodes.append(n)
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = page["pageInfo"]["endCursor"]


def map_pull(node: dict) -> dict:
    """graphql PR 节点 → pulls.json schema（与 render_github.py PR 表对齐）。
    _hash = title + 目标仓库 + mergedAt：合并后基本不变，增量门控用。"""
    repo = node.get("repository") or {}
    lang = (repo.get("primaryLanguage") or {}).get("name") or ""
    merged = node.get("mergedAt") or ""
    return {
        "title": node.get("title") or "",
        "url": node.get("url") or "",
        "number": node.get("number"),
        "repo": repo.get("nameWithOwner") or "",
        "repo_url": repo.get("url") or "",
        "repo_stars": repo.get("stargazerCount", 0),
        "repo_language": lang,
        "merged_at": merged[:10],
        "additions": node.get("additions", 0),
        "deletions": node.get("deletions", 0),
        "_hash": lib.content_hash(node.get("title") or "", repo.get("nameWithOwner") or "", merged),
    }


def sync_pulls(root: str, login: str) -> dict:
    """抓已合并 PR → content/github/pulls.json（search 返回权威全集，整体替换）。"""
    out = os.path.join(root, "content", "github", "pulls.json")
    ex_index = lib.index_by(lib.load_json(out, []), "url")
    incoming = [map_pull(n) for n in fetch_pulls(login)]
    incoming.sort(key=lambda p: p["merged_at"], reverse=True)
    in_index = lib.index_by(incoming, "url")
    added = [p["url"] for p in incoming if p["url"] not in ex_index]
    updated = [p["url"] for p in incoming
               if p["url"] in ex_index and lib.needs_fetch(ex_index, p["url"], p["_hash"])]
    removed = [u for u in ex_index if u not in in_index]
    lib.write_json_atomic(out, incoming)
    lib.log("github", "synced_pulls", login=login, total=len(incoming),
            added=len(added), updated=len(updated), removed=len(removed))
    return {"total": len(incoming), "added": len(added),
            "updated": len(updated), "removed": len(removed)}


# gh_translate / gh_tag 写入的 AI 富化字段：由独立增量流程产出，sync 抓取不得覆盖。
ENRICH_FIELDS = ("description_zh", "description_zh_hash", "tags", "tags_hash")


def carry_enrichment(incoming: list, ex_index: dict) -> None:
    """把已有记录的 AI 富化字段（中文描述 / 标签）并入新抓取记录，就地修改 incoming。
    否则每次同步用裸记录整体替换，会抹掉译文与标签、并清空增量门控 hash → 下游 AI 全量重做。"""
    for r in incoming:
        old = ex_index.get(r["name"])
        if not old:
            continue
        for field in ENRICH_FIELDS:
            if field in old:
                r[field] = old[field]


def map_repo(node: dict) -> dict:
    """graphql 节点 → repos.json schema（与 render_github.py 对齐）。
    _hash = 可分析内容指纹（name+description+language）。刻意不用 commit oid：
    改代码不改仓库的语义分类，内容未变即可跳过下游 AI 分析（category/tag）。"""
    lang = (node.get("primaryLanguage") or {}).get("name") or ""
    pushed = node.get("pushedAt") or ""
    desc = node.get("description") or ""
    return {
        "name": node["name"],
        "description": desc,
        "stars": node.get("stargazerCount", 0),
        "forks": node.get("forkCount", 0),
        "language": lang,
        "url": node.get("url") or "",
        "homepage": node.get("homepageUrl") or "",
        "archived": bool(node.get("isArchived")),
        "updated": pushed[:10],
        "_hash": lib.content_hash(node["name"], desc, lang),
    }


def run(root: str, login: str | None, include_forks: bool, render: bool) -> dict:
    login = login or current_login()
    out = os.path.join(root, "content", "github", "repos.json")
    existing = lib.load_json(out, [])
    ex_index = lib.index_by(existing, "name")

    nodes = fetch_repos(login)
    nodes = [n for n in nodes if not n.get("isPrivate")]  # 私有库永不收录（兜底，服务端已 privacy:PUBLIC）
    if not include_forks:
        nodes = [n for n in nodes if not n.get("isFork")]
    incoming = [map_repo(n) for n in nodes]
    carry_enrichment(incoming, ex_index)  # 保留已有译文/标签，勿被同步抹掉
    incoming.sort(key=lambda r: r["stars"], reverse=True)

    # GitHub 每次返回完整权威集（公开+非 fork），用 incoming 整体替换：
    # 既增量报告变化，又能剔除已删除/转私有/转 fork 的旧条目（merge-keep 会泄漏它们）。
    in_index = lib.index_by(incoming, "name")
    added = [r["name"] for r in incoming if r["name"] not in ex_index]
    updated = [r["name"] for r in incoming
               if r["name"] in ex_index and lib.needs_fetch(ex_index, r["name"], r["_hash"])]
    removed = [name for name in ex_index if name not in in_index]
    lib.write_json_atomic(out, incoming)
    lib.log("github", "synced", login=login, total=len(incoming),
            added=len(added), updated=len(updated), removed=len(removed))
    stats = {"added": len(added), "updated": len(updated), "removed": len(removed)}
    changed = added + updated

    pulls = sync_pulls(root, login)  # 已合并 PR（贡献于他人仓库）→ pulls.json

    if render:
        script = os.path.join(root, ".scripts", "render_github.py")
        lib.run_text([sys.executable, script], "github", timeout=120)
    return {"source": "github", "total": len(incoming), "changed": len(changed),
            **stats, "pulls": pulls}


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync GitHub repos → repos.json (incremental)")
    ap.add_argument("--root", default=lib.find_root(), help="项目根（默认自动定位 hugo.yaml）")
    ap.add_argument("--user", default=None, help="GitHub 登录名（默认当前 gh 账号）")
    ap.add_argument("--include-forks", action="store_true", help="包含 fork 仓库")
    ap.add_argument("--no-render", action="store_true", help="只更新 JSON，不渲染")
    a = ap.parse_args()
    res = run(a.root, a.user, a.include_forks, render=not a.no_render)
    print(__import__("json").dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
