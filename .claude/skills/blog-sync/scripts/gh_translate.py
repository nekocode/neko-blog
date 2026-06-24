#!/usr/bin/env python3
"""GitHub repos.json 的 AI 中文描述：select 选出待翻译 repo，apply 写回译文。

增量：每条 repo 存 description_zh + description_zh_hash（= 被翻译的英文描述的 hash）。
英文描述未变（hash 一致）→ 译文最新 → 跳过；新增/描述变化 → 重译。
翻译本身由 subagent 并发完成（见 SKILL.md），本脚本只做确定性的「选择」与「回写」，
把不确定的 LLM 工作和确定的数据流分离。"""
from __future__ import annotations
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402


def repos_path(root: str) -> str:
    return os.path.join(root, "content", "github", "repos.json")


def needs_translation(repo: dict) -> bool:
    desc = (repo.get("description") or "").strip()
    if not desc:
        return False  # 无英文描述，无可译
    return not (repo.get("description_zh") and repo.get("description_zh_hash") == lib.content_hash(desc))


def select(root: str) -> list:
    """返回待翻译列表 [{name, description}]（增量：跳过译文已对应当前英文描述的）。"""
    return [{"name": r["name"], "description": r["description"]}
            for r in lib.load_json(repos_path(root), []) if needs_translation(r)]


def apply(root: str, mapping: dict, render: bool) -> int:
    """把 {name: 中文} 写回 repos.json，并记录 description_zh_hash 供下次增量判断。"""
    path = repos_path(root)
    repos = lib.load_json(path, [])
    applied = empty = 0
    for r in repos:
        if r["name"] not in mapping:
            continue
        zh = (mapping.get(r["name"]) or "").strip()
        if not zh:  # 译文为空：不静默——记日志，下次仍会重选重译
            empty += 1
            lib.log("github", "translate_empty", result="error", name=r["name"],
                    reason="译文为空，未写入，下次仍会重选")
            continue
        r["description_zh"] = zh
        r["description_zh_hash"] = lib.content_hash(r.get("description") or "")
        applied += 1
    lib.write_json_atomic(path, repos)
    lib.log("github", "translated", result="ok", applied=applied, empty=empty, total=len(repos))
    if render:
        lib.run_text([sys.executable, os.path.join(root, ".scripts", "render_github.py")], "github", timeout=120)
    return applied


def main() -> None:
    ap = argparse.ArgumentParser(description="GitHub repo 描述 AI 中文化（select / apply）")
    ap.add_argument("mode", choices=["select", "apply"])
    ap.add_argument("--root", default=lib.find_root())
    ap.add_argument("--input", help="apply 模式：译文 JSON 文件，{name: 中文} 或 [{name, description_zh}]")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()

    if a.mode == "select":
        todo = select(a.root)
        lib.log("github", "select", result="ok", todo=len(todo))
        print(json.dumps(todo, ensure_ascii=False, indent=2))
        return

    raw = lib.load_json(a.input, {})
    if isinstance(raw, list):  # 兼容 [{name, description_zh}] 形态
        raw = {x["name"]: (x.get("description_zh") or x.get("zh") or "") for x in raw}
    print(json.dumps({"applied": apply(a.root, raw, render=not a.no_render)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
