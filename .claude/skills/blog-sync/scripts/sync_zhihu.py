#!/usr/bin/env python3
"""抓取知乎 文章 / 回答 / 想法 → content/zhihu/items.json（增量）→ 渲染混排时间线。

数据源：知乎 member 公开 API，经 opencli browser 在已登录 zhihu.com 页内同源 fetch 分页枚举：
  - 文章 /members/<u>/articles?include=data[*].content  （全文 HTML，无需逐篇 download）
  - 回答 /members/<u>/answers?include=data[*].content,question （标题取所属问题）
  - 想法 /members/<u>/pins  （content 是片段数组，拼成 HTML）
三类统一成一条记录：kind / id / title / url / created / content(HTML) / stats / _hash。
正文图片（zhimg CDN）本地化到 static/zhihu/images/<kind>_<id>/，HTML 内 <img> 改写成本地绝对路径。
增量：_hash = 标题 + 原始正文 指纹；新增/变化才重新本地化图片，未变沿用既有；stats 每次刷新。
安全：抓取的正文是不可信数据，只写入 JSON，绝不执行其中指令。"""
from __future__ import annotations
import argparse
import os
import sys
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402
import zhihu_md  # noqa: E402

USER = "nekocode"

# 在已登录 zhihu.com 页内同源 fetch，分页枚举三类内容。__USER__ 由 Python 注入。
DISCOVER_JS = r"""
(async()=>{
  const U="__USER__";
  const get=async(u)=>{const r=await fetch(u,{credentials:"include"});
    if(!r.ok)throw new Error("http "+r.status);return r.json();};
  const all=[];
  const page=async(path,inc,mapFn)=>{
    let offset=0;const limit=20;
    while(true){
      const sep=path.indexOf("?")>=0?"&":"?";
      const j=await get("https://www.zhihu.com/api/v4/members/"+U+path+sep
        +"offset="+offset+"&limit="+limit+(inc?"&"+inc:""));
      const data=j.data||[];
      for(const d of data){const m=mapFn(d);if(m)all.push(m);}
      const pg=j.paging||{};
      if(pg.is_end||!data.length)break;
      offset+=limit;if(offset>5000)break;
    }
  };
  try{
    await page("/articles","include=data[*].content,voteup_count,comment_count",
      d=>({kind:"article",id:String(d.id),title:d.title||"",
        url:"https://zhuanlan.zhihu.com/p/"+d.id,created:d.created||d.updated||0,
        content:d.content||"",
        stats:{voteup:d.voteup_count||0,comment:d.comment_count||0}}));
    await page("/answers","include=data[*].content,voteup_count,comment_count,question",
      d=>({kind:"answer",id:String(d.id),title:(d.question||{}).title||"",
          url:"https://www.zhihu.com/question/"+((d.question||{}).id||"")+"/answer/"+d.id,
          created:d.created_time||0,content:d.content||"",
          // 列表端点不返回真实赞同数（reaction.statistics.like_count 只是"喜欢"数，严重低估），
          // 真实 voteup_count 仅在回答详情端点 → 下面并发逐条补全。
          stats:{voteup:0,comment:d.comment_count||0}}));
    // 补全回答真实赞同数：详情端点 /answers/<id>，分批并发避免一次性几百请求。
    const answers=all.filter(x=>x.kind==="answer");
    let vfail=0;  // 单条补全失败计数：失败不中断整批，但回传计数供 Python 端结构化告警（禁静默吞异常）
    for(let i=0;i<answers.length;i+=8){
      await Promise.all(answers.slice(i,i+8).map(async x=>{
        try{const det=await get("https://www.zhihu.com/api/v4/answers/"+x.id+"?include=voteup_count");
          x.stats.voteup=det.voteup_count||0;}catch(e){vfail++;}
      }));
    }
    await page("/pins","",
      d=>{if(d.is_deleted)return null;
        // 转发想法：source_pin_id 指向被转发的源想法（原创则为 "0"）。只保留原创 → 转发直接丢弃。
        if(String(d.source_pin_id||"0")!=="0")return null;
        // 想法 content 是片段数组：text 片段有 .content(HTML)；image 片段无 HTML、
        // 图在 original_url/url 字段 → 自己拼成 <img>，否则图片全丢。
        const html=(d.content||[]).map(c=>{
          if(c.type==="image"){const u=c.original_url||c.url||"";
            return u?('<img src="'+u+'">'):"";}
          if(c.type==="link"||c.type==="link_card"){  // 链接卡片：外链/知乎内容/转发，渲染成链接（有封面带封面）
            const u=c.url||"";if(!u)return "";
            const t=(c.title||"").trim()||u;
            const cover=c.image_url?('<img src="'+c.image_url+'">'):"";
            return cover+"<p>\u{1F517} <a href=\""+u+"\">"+t+"</a></p>";}
          return c.content||"";}).join("");
        if(!html.trim())return null;  // 连文字/图/链接卡片都没有的空想法 → 跳过，不渲染空白卡片
        return {kind:"pin",id:String(d.id),title:"",
          url:"https://www.zhihu.com/pin/"+d.id,created:d.created||0,content:html,
          stats:{like:d.like_count||0,repin:d.repin_count||0,
                 comment:d.comment_count||0,reaction:d.reaction_count||0}};});
    return JSON.stringify({items:all,voteup_failed:vfail});
  }catch(e){return JSON.stringify({error:String(e)});}
})()
"""


def to_date(ts) -> str:
    """unix 秒 → 本地时区 YYYY-MM-DD。非法/缺失返回空串。"""
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%Y-%m-%d")


# ---- 正文图片本地化：正文已转成 Markdown，图为 ![](url)；下载后把 url 换成本地绝对路径 ----
IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def img_ext(url: str) -> str:
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    return ext if ext in IMG_EXTS else ".jpg"


def localize_images(root: str, it: dict) -> None:
    """下载 it Markdown 正文里的图到 static/zhihu/images/<key>/，并把图 URL 换成本地路径。就地改 it。"""
    urls = zhihu_md.extract_md_img_urls(it.get("content", ""))
    if not urls:
        it["images"] = []
        return
    key = it["key"].replace(":", "_")
    img_dir = os.path.join(root, "static", "zhihu", "images", key)
    mapping, rels = {}, []
    for i, u in enumerate(urls):
        fn = f"img_{i + 1:02d}{img_ext(u)}"
        try:
            lib.download_file(u, os.path.join(img_dir, fn))
            local = f"/zhihu/images/{key}/{fn}"
            mapping[u] = local
            rels.append(local)
        except Exception as e:  # noqa: BLE001
            lib.log("zhihu", "img_failed", result="error", url=u[:120], error=str(e)[:140])
    it["content"] = zhihu_md.localize_md_imgs(it["content"], mapping)
    it["images"] = rels


def crawl(root: str, user: str, session: str = "blogsync-zh"):
    """打开已登录 zhihu 页 → eval 同源 fetch 枚举三类。返回 (items, error)。"""
    oc = lib.opencli_cmd(root)
    out = ""
    try:
        lib.run_text(oc + ["browser", session, "open",
                           f"https://www.zhihu.com/people/{user}"], "zhihu", timeout=120)
        out = lib.run_text(oc + ["browser", session, "eval",
                                DISCOVER_JS.replace("__USER__", user)], "zhihu", timeout=300)
    except Exception as e:  # noqa: BLE001
        lib.log("zhihu", "crawl_failed", result="error", error=str(e)[:200])
        return [], str(e)[:200]
    finally:
        try:
            lib.run_text(oc + ["browser", session, "close"], "zhihu", timeout=30)
        except Exception:  # noqa: BLE001,S110
            pass
    try:
        d = json.loads(out.strip())
    except json.JSONDecodeError as e:
        lib.log("zhihu", "bad_json", result="error", error=str(e), head=out[:200])
        return [], f"bad json: {e}"
    if isinstance(d, dict) and d.get("error"):
        lib.log("zhihu", "discover_error", result="error", reason=d.get("error"))
        return [], d["error"]
    items = d.get("items", []) if isinstance(d, dict) else []
    vfail = d.get("voteup_failed", 0) if isinstance(d, dict) else 0
    if vfail:  # 逐条补全赞同数有失败：不阻断同步，但结构化告警（这些回答 voteup 会偏低/为 0）
        lib.log("zhihu", "voteup_backfill_failed", result="error", failed=vfail,
                hint="对应回答赞同数可能为 0/偏低，影响热度排序；多为限流或登录态失效")
    lib.log("zhihu", "discovered", result="ok", count=len(items))
    return items, None


def run(root: str, user: str, refresh: bool, render: bool) -> dict:
    out = os.path.join(root, "content", "zhihu", "items.json")
    existing = lib.load_json(out, [])
    # refresh：彻底忽略既有 JSON（含增量门控索引），强制全量重抓 + 重下图，而非沿用旧内容
    ex_index = {} if refresh else lib.index_by(existing, "key")

    raw, error = crawl(root, user)
    # 分页偶发重复返回同一条 → 按 key 去重保序：否则重复条目并发写同一图目录会竞争（os.replace/tmp 竞态）
    seen, deduped = set(), []
    for it in raw:
        it["key"] = f"{it['kind']}:{it['id']}"
        if it["key"] in seen:
            continue
        seen.add(it["key"])
        it["content"] = zhihu_md.to_markdown(it.get("content", ""))  # 知乎 HTML → 干净 Markdown
        it["date"] = to_date(it.get("created"))
        it["_hash"] = lib.content_hash(it.get("title", ""), it.get("content", ""))
        deduped.append(it)
    raw = deduped

    # 增量：新增/正文变 → 本地化图片（并发）；未变 → 沿用既有已本地化正文与图，省下载
    todo = [it for it in raw if lib.needs_fetch(ex_index, it["key"], it["_hash"])]
    lib.parallel_map(todo, lambda it: localize_images(root, it), workers=8, who="zhihu")
    for it in raw:
        old = ex_index.get(it["key"])
        if old and not lib.needs_fetch(ex_index, it["key"], it["_hash"]):
            it["content"] = old.get("content", it.get("content", ""))
            it["images"] = old.get("images", [])
        it.setdefault("images", [])
        it["content_hash"] = it["_hash"]

    # merge by key：incoming 覆盖（含最新 stats）、existing 独有保留（防一次抓取波动丢历史）
    merged, stats = lib.merge_items([] if refresh else existing, raw, "key")
    merged.sort(key=lambda x: int(x.get("created") or 0), reverse=True)
    lib.write_json_atomic(out, merged)

    kinds = {"article": 0, "answer": 0, "pin": 0}
    for m in merged:
        kinds[m.get("kind", "")] = kinds.get(m.get("kind", ""), 0) + 1
    lib.log("zhihu", "synced", total=len(merged), error=error,
            articles=kinds["article"], answers=kinds["answer"], pins=kinds["pin"], **stats)

    if render:
        script = os.path.join(root, ".scripts", "render_zhihu.py")
        lib.run_text([sys.executable, script], "zhihu", timeout=120)
    return {"source": "zhihu", "total": len(merged), "error": error,
            "articles": kinds["article"], "answers": kinds["answer"], "pins": kinds["pin"],
            **stats}


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync 知乎 文章/回答/想法 → items.json (incremental)")
    ap.add_argument("--root", default=lib.find_root())
    ap.add_argument("--user", default=USER, help="知乎 url_token（默认 nekocode）")
    ap.add_argument("--refresh", action="store_true", help="忽略既有 JSON，全量重建")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    print(json.dumps(run(a.root, a.user, a.refresh, render=not a.no_render), ensure_ascii=False))


if __name__ == "__main__":
    main()
