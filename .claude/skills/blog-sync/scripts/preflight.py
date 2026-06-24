#!/usr/bin/env python3
"""环境预检：判断每个数据源是否就绪，输出结构化 JSON 供编排层决策。

- github：gh 安装 + 登录。
- twitter/weixin(download)：opencli 可用 + browser bridge（opencli doctor）绿；
  且需 Chrome 已登录对应站点（doctor 检测不到登录态，仅检测 bridge）。
- weixin(search)：public 策略，只要 opencli 可用即可。
不抛异常——把每项 ready/原因汇报出去，让编排层只跑就绪的源。"""
from __future__ import annotations
import os
import sys
import json
import shutil
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402


def _ok(cmd) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return p.returncode == 0, (p.stderr or p.stdout).strip()[:200]
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def check(root: str) -> dict:
    report = {}

    gh = shutil.which("gh")
    if not gh:
        report["github"] = {"ready": False, "reason": "gh 未安装"}
    else:
        ok, msg = _ok(["gh", "auth", "status"])
        report["github"] = {"ready": ok, "reason": "ok" if ok else f"gh 未登录: {msg}"}

    oc = lib.opencli_cmd(root)
    oc_ok, oc_msg = _ok(oc + ["--version"])
    if not oc_ok:
        for src in ("twitter", "weixin"):
            report[src] = {"ready": False, "reason": f"opencli 不可用: {oc_msg}"}
    else:
        doc_ok, doc_msg = _ok(oc + ["doctor"])
        # twitter / weixin / zhihu 都需 browser bridge + 站点登录（同源 fetch 各自后台/公开 API）
        report["twitter"] = {"ready": doc_ok,
                             "reason": "ok（仍需 Chrome 已登录 X）" if doc_ok else f"browser bridge 未就绪: {doc_msg}"}
        report["weixin"] = {"ready": doc_ok,
                            "reason": "ok（仍需 Chrome 已登录 mp.weixin.qq.com）" if doc_ok else f"browser bridge 未就绪: {doc_msg}"}
        report["zhihu"] = {"ready": doc_ok,
                           "reason": "ok（仍需 Chrome 已登录 zhihu.com）" if doc_ok else f"browser bridge 未就绪: {doc_msg}"}

    report["_ready_sources"] = [k for k, v in report.items()
                                if not k.startswith("_") and v.get("ready")]
    return report


def main() -> None:
    root = lib.find_root()
    print(json.dumps(check(root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
