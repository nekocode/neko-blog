#!/usr/bin/env python3
"""GitHub repos.json 的 AI 打标：select 选出待打标 repo，apply 校验并写回 tags。

固定标签词表（TAGS）是**单一真相源**，写死在此——打标只能从中选，apply 强制校验、丢弃非法标签。
增量：每 repo 存 tags + tags_hash（= name+description+language 的 hash）。仓库身份未变 → 跳过；
新增/名称·描述·语言变化 → 重新打标。打标由 subagent 并发完成（可读仓库源码判断），
本脚本只做确定性的「选择 / 校验回写」。"""
from __future__ import annotations
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

# ---- 固定标签词表（受控词汇，唯一真相源）。新增标签须改这里。----
TAGS = [
    "Android", "iOS", "Web", "Backend", "Desktop", "Flutter",  # 平台
    "Game", "AI", "Infra", "Graphics", "Security",             # 领域
    "Tool", "Library", "Plugin",                               # 类型
]
TAGSET = set(TAGS)


def repos_path(root: str) -> str:
    return os.path.join(root, "content", "github", "repos.json")


def tag_hash(repo: dict) -> str:
    """打标依据 = 仓库身份（名称+描述+语言）。变了才重打。"""
    return lib.content_hash(repo.get("name", ""), repo.get("description", ""), repo.get("language", ""))


def needs_tagging(repo: dict) -> bool:
    return not (repo.get("tags") and repo.get("tags_hash") == tag_hash(repo))


def select(root: str) -> list:
    """待打标列表 [{name, description, language, url}]（含 url 便于 agent 按需看源码）。"""
    return [{"name": r["name"], "description": r.get("description", ""),
             "language": r.get("language", ""), "url": r.get("url", "")}
            for r in lib.load_json(repos_path(root), []) if needs_tagging(r)]


def clean_tags(raw) -> list:
    """只保留词表内标签，去重保序，丢弃非法/空。"""
    out = []
    for t in raw or []:
        t = (t or "").strip()
        if t in TAGSET and t not in out:
            out.append(t)
    return out


def apply(root: str, mapping: dict, render: bool) -> dict:
    """把 {name: [tags]} 校验后写回 repos.json。返回 {applied, dropped}（dropped=被丢弃的非法标签数）。"""
    path = repos_path(root)
    repos = lib.load_json(path, [])
    applied = dropped = 0
    for r in repos:
        if r["name"] not in mapping:
            continue
        raw = mapping[r["name"]]
        proposed = [(t or "").strip() for t in (raw or []) if (t or "").strip()]
        valid = clean_tags(raw)
        dropped += len(proposed) - len(valid)  # 非空提交里被丢弃的（非法 + 重复），口径准确
        if valid:
            r["tags"] = valid
            r["tags_hash"] = tag_hash(r)
            applied += 1
        elif proposed:  # 提交了标签但全非法：不静默——保留旧标签，记日志，下次仍会重选
            lib.log("github", "tag_all_invalid", result="error", name=r["name"],
                    proposed=proposed, reason="标签全部不在词表，保留旧标签，下次仍会重选")
    lib.write_json_atomic(path, repos)
    lib.log("github", "tagged", result="ok", applied=applied, dropped=dropped, total=len(repos))
    if render:
        lib.run_text([sys.executable, os.path.join(root, ".scripts", "render_github.py")], "github", timeout=120)
    return {"applied": applied, "dropped": dropped}


def main() -> None:
    ap = argparse.ArgumentParser(description="GitHub repo AI 打标（select / apply / tags）")
    ap.add_argument("mode", choices=["select", "apply", "tags"])
    ap.add_argument("--root", default=lib.find_root())
    ap.add_argument("--input", help="apply 模式：JSON 文件 {name: [tags]}")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()

    if a.mode == "tags":
        print(json.dumps(TAGS, ensure_ascii=False))
        return
    if a.mode == "select":
        todo = select(a.root)
        lib.log("github", "select_tags", result="ok", todo=len(todo))
        print(json.dumps(todo, ensure_ascii=False, indent=2))
        return
    raw = lib.load_json(a.input, {})
    if isinstance(raw, list):  # 兼容 [{name, tags}]
        raw = {x["name"]: x.get("tags") for x in raw}
    print(json.dumps(apply(a.root, raw, render=not a.no_render), ensure_ascii=False))


if __name__ == "__main__":
    main()
