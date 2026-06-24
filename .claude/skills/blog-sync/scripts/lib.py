#!/usr/bin/env python3
"""blog-sync 共享库：结构化日志 / 原子写 / 内容 hash / 增量 merge / 并发下载。

设计原则：纯函数无副作用，I/O 集中在少量薄封装里，便于单测全量覆盖。
所有「内容是否更新」的判断都收敛到这里的 needs_fetch + content_hash，
各 sync 脚本只负责把数据源映射成统一结构。"""
from __future__ import annotations
import json
import os
import re
import sys
import time
import hashlib
import tempfile
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


# ---- 结构化日志：who/what/when/result + 上下文，输出 stderr（不污染 stdout 的 JSON）----

def log(who: str, what: str, result: str = "ok", **ctx) -> None:
    rec = {"who": who, "what": what, "when": _now(), "result": result}
    rec.update(ctx)
    print(json.dumps(rec, ensure_ascii=False), file=sys.stderr, flush=True)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---- 项目根定位：向上找 hugo.yaml，找不到回退到 cwd ----

def find_root(start: str | None = None) -> str:
    base = os.path.abspath(start or os.getcwd())
    d = base
    while True:
        if os.path.exists(os.path.join(d, "hugo.yaml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return base
        d = parent


# ---- 内容 hash：normalize 空白后 sha256 前 16 hex。用于判断正文是否变化 ----

def content_hash(*parts: str) -> str:
    norm = " ".join(re.sub(r"\s+", " ", (p or "").strip()) for p in parts)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


# ---- JSON 读写：缺失返回默认值；写入用临时文件 + rename 原子替换 ----

def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path: str, data) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---- 增量核心：按 key 建索引 + 判断是否需要昂贵重抓 + 合并 ----

def index_by(items: list, key: str) -> dict:
    return {it[key]: it for it in items if key in it}


def needs_fetch(existing_index: dict, key_value, new_hash, hash_field: str = "_hash") -> bool:
    """新增（key 不存在）或 hash 变化 → 需要重抓昂贵内容（正文 / 媒体）。"""
    old = existing_index.get(key_value)
    if old is None:
        return True
    return old.get(hash_field) != new_hash


def merge_items(existing: list, incoming: list, key: str):
    """按 key 合并：incoming 覆盖同 key、追加新 key；existing 独有者保留。
    返回 (merged_list, stats)。incoming 在前保持「最新优先」序，再补 existing 独有。"""
    merged: dict = {}
    added = updated = 0
    for it in incoming:
        k = it[key]
        if k in {x[key] for x in existing if key in x}:
            updated += 1
        else:
            added += 1
        merged[k] = it
    kept = 0
    inc_keys = {it[key] for it in incoming if key in it}
    for it in existing:
        k = it.get(key)
        if k is not None and k not in inc_keys:
            merged[k] = it
            kept += 1
    return list(merged.values()), {"added": added, "updated": updated, "kept": kept}


# ---- 薄 I/O 封装：跑命令收 JSON / 并发任务 / 下载文件 ----

def run_json(cmd: list, who: str, timeout: int = 180):
    """跑命令、解析 stdout JSON。失败抛 RuntimeError，带命令与 stderr 上下文（禁吞异常）。"""
    log(who, "exec", cmd=" ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"cmd failed exit={p.returncode}: {' '.join(cmd)}\n{p.stderr.strip()[:600]}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"non-JSON output from {' '.join(cmd)}: {e}; head={p.stdout[:300]!r}")


def run_text(cmd: list, who: str, timeout: int = 180) -> str:
    log(who, "exec", cmd=" ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"cmd failed exit={p.returncode}: {' '.join(cmd)}\n{p.stderr.strip()[:600]}")
    return p.stdout


def parallel_map(jobs: list, fn, workers: int = 8, who: str = "parallel") -> list:
    """并发执行 fn(job)，保持输入顺序返回结果。单个失败记录但不中断（None 占位）。"""
    results: list = [None] * len(jobs)
    if not jobs:
        return results
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut_to_i = {ex.submit(fn, j): i for i, j in enumerate(jobs)}
        for fut in as_completed(fut_to_i):
            i = fut_to_i[fut]
            try:
                results[i] = fut.result()
            except Exception as e:  # noqa: BLE001 — 汇总错误，单条失败不拖垮整批
                log(who, "job_failed", result="error", index=i, error=str(e)[:200])
    return results


def download_file(url: str, dest: str, timeout: int = 60) -> str:
    """下载 url 到 dest（已存在则跳过，天然增量）。返回 dest。"""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "blog-sync/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 仅取受信任站点媒体
        data = r.read()
    tmp = dest + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):  # 写入/替换中途异常时清理半成品，勿留孤儿 .tmp
            os.remove(tmp)
    return dest


def http_get_text(url: str, timeout: int = 60) -> str:
    """GET 文本（HTML 等）。UA 伪装成浏览器，避免站点对默认 UA 反爬返回空壳。"""
    req = urllib.request.Request(url, headers={"User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 仅取受信任站点页面
        return r.read().decode("utf-8", "replace")


def opencli_cmd(root: str) -> list:
    """优先用项目本地 opencli（node_modules/.bin），回退到全局 PATH。"""
    local = os.path.join(root, "node_modules", ".bin", "opencli")
    if os.path.exists(local):
        return [local]
    return ["npx", "--no-install", "opencli"]
