#!/usr/bin/env python3
"""统计本机 Claude Code 近况用量 → data/claude_stats.json（供首页 Build-in-Public 展示）。

数据源：~/.claude/projects/**/*.jsonl —— 全机所有项目的全部会话转录。
  - Token：每条 assistant 消息 message.usage 的 input/output/cache_creation/cache_read 累加。
  - 代码改动：tool_use 中 Write/Edit/MultiEdit，按行数算新增(+)与删除(-)，churn = 增 + 删。

口径：只统计**最近 90 天**（按事件 timestamp 过滤），再除以 3 得**30 天月均**——
反映当下的活跃速率，而非被早期历史稀释的全程总量。

去重：fork / resume 会把同一事件复制进多个转录文件，按事件级唯一 uuid 跨文件去重，避免重复计数。

近似口径（无法精确还原 git diff，故标注）：
  - Write 覆盖已有文件时无前镜像，整文件行数全计为新增。
  - Edit 按 old_string / new_string 文本行数计增删；replace_all 多处替换只按一次计（保守）。
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

# message.usage 里参与「总 token」累加的字段（含 cache 读写，反映真实处理量）
USAGE_FIELDS = ("input_tokens", "output_tokens",
                "cache_creation_input_tokens", "cache_read_input_tokens")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}
WINDOW_DAYS = 90   # 统计窗口：只看最近 90 天
PERIOD_DAYS = 30   # 平均周期：换算成 30 天月均（窗口 / 周期 = 3 个月）


def parse_ts(s: str):
    """解析事件 ISO8601 时间戳（'...Z'），返回 aware datetime；失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def line_count(text: str) -> int:
    """文本行数：空串记 0，否则换行数 + 1。"""
    return 0 if not text else text.count("\n") + 1


def churn_from_tool_use(name: str, inp: dict) -> tuple[int, int]:
    """从一次 Write/Edit/MultiEdit 输入算 (新增行, 删除行)。未知工具返回 (0, 0)。"""
    if name == "Write":
        return line_count(inp.get("content", "")), 0
    if name == "Edit":
        return line_count(inp.get("new_string", "")), line_count(inp.get("old_string", ""))
    if name == "MultiEdit":
        add = rem = 0
        for e in inp.get("edits") or []:
            add += line_count(e.get("new_string", ""))
            rem += line_count(e.get("old_string", ""))
        return add, rem
    return 0, 0


def scan_file(path: str, seen: set, acc: dict, cutoff: datetime) -> int:
    """扫一个转录文件，按 uuid 去重 + 只取 cutoff 之后的事件，累加进 acc。
    坏行/无时间戳/窗口外的事件跳过。返回本文件计入窗口的事件数。"""
    contributed = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = parse_ts(o.get("timestamp"))
            if ts is None or ts < cutoff:  # 无时间戳或早于窗口 → 不计
                continue
            uid = o.get("uuid")
            if uid is not None:  # 跨文件事件级去重（fork/resume 会复制同一 uuid）
                if uid in seen:
                    continue
                seen.add(uid)
            contributed += 1
            msg = o.get("message") or {}
            usage = msg.get("usage")
            if isinstance(usage, dict):
                acc["assistant_msgs"] += 1
                for fld in USAGE_FIELDS:
                    acc[fld] += int(usage.get(fld) or 0)
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if (isinstance(b, dict) and b.get("type") == "tool_use"
                            and b.get("name") in EDIT_TOOLS):
                        add, rem = churn_from_tool_use(b["name"], b.get("input") or {})
                        acc["added"] += add
                        acc["removed"] += rem
                        acc["edits"] += 1
    return contributed


def run(root: str, projects_dir: str, write: bool) -> dict:
    files = sorted(glob.glob(os.path.join(projects_dir, "**", "*.jsonl"), recursive=True))
    acc = {f: 0 for f in USAGE_FIELDS}
    acc.update(added=0, removed=0, edits=0, assistant_msgs=0)
    seen: set = set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    sessions = 0  # 窗口内有事件的会话数
    for path in files:
        try:
            if scan_file(path, seen, acc, cutoff) > 0:
                sessions += 1
        except OSError as e:  # noqa: PERF203
            lib.log("claude_stats", "scan_failed", result="error", file=path, error=str(e)[:160])

    window_tokens = sum(acc[f] for f in USAGE_FIELDS)
    window_churn = acc["added"] + acc["removed"]
    months = WINDOW_DAYS / PERIOD_DAYS  # 90 / 30 = 3，把窗口总量摊成 30 天月均

    def monthly(x):
        return round(x / months)

    out = {
        "window_days": WINDOW_DAYS,
        "period_days": PERIOD_DAYS,
        "sessions": sessions,
        "assistant_msgs": acc["assistant_msgs"],
        "edits": acc["edits"],
        # 30 天月均（供首页展示）
        "monthly_tokens": monthly(window_tokens),
        "monthly_code_lines_added": monthly(acc["added"]),
        "monthly_code_lines_removed": monthly(acc["removed"]),
        "monthly_code_lines_churn": monthly(window_churn),
        # 窗口（90 天）原始总量，便于核对
        "window_total_tokens": window_tokens,
        "window_code_lines_churn": window_churn,
    }
    lib.log("claude_stats", "scanned", result="ok", sessions=sessions, window_days=WINDOW_DAYS,
            monthly_tokens=out["monthly_tokens"], monthly_churn=out["monthly_code_lines_churn"],
            edits=acc["edits"])
    if write:
        lib.write_json_atomic(os.path.join(root, "data", "claude_stats.json"), out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="统计本机 Claude Code 最近 90 天用量，输出 30 天月均")
    ap.add_argument("--root", default=lib.find_root())
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"),
                    help="Claude Code 转录目录（默认 ~/.claude/projects）")
    ap.add_argument("--no-write", action="store_true", help="只打印，不写 data/claude_stats.json")
    a = ap.parse_args()
    out = run(a.root, a.projects, write=not a.no_write)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
