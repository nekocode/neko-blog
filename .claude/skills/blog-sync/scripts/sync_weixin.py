#!/usr/bin/env python3
"""抓取微信公众号文章 → content/weixin/articles.json（增量）→ 渲染 md。

数据源：
  - 发现：公众号后台「发表记录」API（mp.weixin.qq.com/cgi-bin/appmsgpublish），
    经 opencli browser 在已登录页内同源 fetch 分页枚举**全部已发文章**（可靠，搜狗做不到）。
  - 正文：opencli weixin download --url U --output D（cookie）把 md+图片落盘，返回 saved 路径。
去重：跨源用**归一化标题**（只留中日韩+字母数字）——后台短链 /s/id 与现有 ?sn= 不同源，
  且标题含 emoji/标点变体，单靠 sn/slug 会误判重复。归一化标题命中即同一篇，不重抓。
首跑迁移：把现存 content/weixin/<slug>/index.md（手工版）解析进 JSON，不重抓、不破坏。
清洗：download 的正文带 adapter 头部块 + 尾部微信打赏 UI 垃圾，确定性剥离。"""
from __future__ import annotations
import argparse
import os
import re
import sys
import json
import glob
import html
import shutil
import tempfile
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

# 尾部微信 UI 垃圾的起始标记，取最早出现处截断
JUNK_MARKERS = ("Name cleared", "微信扫一扫赞赏作者", "Like the Author",
                "收录于", "预览时标签不可点", "继续滑动看下一个", "轻触阅读原文")


def extract_sn(url: str) -> str:
    qs = parse_qs(urlparse(url).query)
    return (qs.get("sn") or [""])[0] or lib.content_hash(url)


def slugify(title: str) -> str:
    s = re.sub(r"\s+", "_", (title or "untitled").strip())
    return re.sub(r'[/\\:*?"<>|]', "", s)[:80] or "untitled"


def norm_title(title: str) -> str:
    """归一化标题：只留中日韩 + 字母数字（小写）。跨源去重的稳定 key——
    抹平 emoji、空格、标点、全半角差异，使后台短链文章与现有手工文章能对上。"""
    return re.sub(r"[^0-9a-z一-鿿]", "", (title or "").lower())


def to_date(s: str) -> str:
    """统一日期：'2024年3月16日 22:25' 或 '2024-03-16T..' → '2024-03-16'。"""
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s or "")
    return f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}" if m else (s or "")[:10]


# 尾部「地区,YYYY年M月D日 HH:MM,」发布元信息行（adapter 偶尔附带，非正文）
FOOTER_RE = re.compile(r"\s*[^\n]{0,20}\d{4}年\d{1,2}月\d{1,2}日[^\n]*$")

# 尾部「本文首发于微信公众号，[阅读原文](...)。」自荐块——adapter 自动附加，非正文。
# 连同其上的 --- 分隔线与 > 引用前缀整体剥离；仅当它落在正文最后一行时命中（$ 锚定文末，
# [^\n]* 不跨行），绝不误删正文中段同名文字。
SELF_PUBLISH_RE = re.compile(r"\s*(?:-{3,}\s*)?>?\s*本文首发于微信公众号[^\n]*$")


def strip_trailing_junk(body: str) -> str:
    """截断尾部微信 UI 垃圾（赞赏/收录/Like the Author/本文首发 等）+ 去发布元信息页脚。
    通用于下载与迁移两条路径——只从尾部第一个垃圾标记处截断，不碰正文。幂等。"""
    cut = len(body)
    for mark in JUNK_MARKERS:
        i = body.find(mark)
        if i != -1:
            cut = min(cut, i)
    body = SELF_PUBLISH_RE.sub("", body[:cut])
    return FOOTER_RE.sub("", body).strip()


def clean_body(md: str) -> str:
    """剥离 adapter 头部块（首个 '\\n---\\n' 之前）+ 截断尾部微信 UI 垃圾 + 去发布元信息页脚。
    注意：头部 split 仅适用于 adapter 下载产物，正文里的 '---' 分隔线不能这样切——
    故迁移路径只调 strip_trailing_junk，绝不做头部 split。"""
    parts = md.split("\n---\n", 1)
    body = parts[1] if len(parts) == 2 else md
    return strip_trailing_junk(body)


def parse_front_matter(text: str):
    """解析现存 index.md 的 YAML front matter（顶层 key: value，忽略缩进列表项）。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[3:end].splitlines():
        if line[:1] not in (" ", "-", "") and ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    return meta, text[end + 4:].lstrip("\n")


def list_images(d: str) -> list:
    if not os.path.isdir(d):
        return []
    return [f"images/{f}" for f in sorted(os.listdir(d)) if not f.startswith(".")]


def article_from_md(root: str, path: str) -> dict | None:
    """把现存 content/weixin/<slug>/index.md 解析成 articles.json 记录（迁移，不重抓）。"""
    meta, body = parse_front_matter(open(path, encoding="utf-8").read())
    url = meta.get("original_url", "")
    if not url:
        return None
    body = strip_trailing_junk(body)  # 迁移路径同样剔除尾部微信 UI 垃圾（下载路径在 clean_body 已处理）
    slug = os.path.basename(os.path.dirname(path))
    title = meta.get("title", slug)
    chash = lib.content_hash(title, body)
    return {
        "sn": extract_sn(url), "title": title, "slug": slug,
        "date": to_date(meta.get("date", "")), "author": meta.get("author", "nekocode"),
        "account": meta.get("account") or None,  # 从 front matter 读归属账号（多账号下区分）
        "original_url": url, "content": body, "images": list_images(os.path.join(os.path.dirname(path), "images")),
        "content_hash": chash, "_hash": chash,
    }


def article_from_download(root: str, item: dict, author: str = "") -> dict | None:
    """weixin download 落盘 → 读 saved 正文 + 搬运图片 → articles.json 记录。
    author 非空时在搬运图片前校验作者、不符直接丢弃（绝不污染 content/）——可选防护，
    默认 author='' 关闭（当前发现走后台同账号 API，无串号；保留以防多账号切换落到错号）。"""
    url = item.get("url") or item.get("link") or ""
    sn = extract_sn(url)
    with tempfile.TemporaryDirectory() as staging:
        cmd = lib.opencli_cmd(root) + ["weixin", "download", "--url", url, "--output", staging, "-f", "json"]
        try:
            res = lib.run_json(cmd, "weixin", timeout=180)
        except Exception as e:  # noqa: BLE001
            lib.log("weixin", "download_failed", result="error", sn=sn, url=url, error=str(e)[:200])
            return None
        if isinstance(res, list):  # adapter 有时把单结果包成 [{...}]
            res = res[0] if res else {}
        saved = res.get("saved")
        if res.get("status") != "success" or not saved or not os.path.exists(saved):
            lib.log("weixin", "download_bad", result="error", sn=sn, status=res.get("status"))
            return None
        got_author = res.get("author") or ""
        if author and got_author != author:  # 搜狗串号 → 搬图前丢弃
            lib.log("weixin", "author_skip", result="ok", sn=sn, got=got_author, want=author)
            return None
        title = res.get("title") or item.get("title") or "untitled"
        # slug 带 sn 短码后缀：标题撞 slug（截断/标点归并）或并发下载时，避免两篇共用同一图片目录互相覆盖
        slug = f"{slugify(title)}-{sn[:8]}"
        body = clean_body(open(saved, encoding="utf-8").read())
        # 搬运 adapter 下好的图片到 content/weixin/<slug>/images/
        src_img = os.path.join(os.path.dirname(saved), "images")
        images = []
        if os.path.isdir(src_img):
            dst = os.path.join(root, "content", "weixin", slug, "images")
            os.makedirs(dst, exist_ok=True)
            for f in sorted(os.listdir(src_img)):
                shutil.copy2(os.path.join(src_img, f), os.path.join(dst, f))
                images.append(f"images/{f}")
        date = to_date(res.get("publish_time") or item.get("publish_time", ""))
        chash = lib.content_hash(title, body)
        return {
            "sn": sn, "title": title, "slug": slug, "date": date,
            "author": res.get("author") or "nekocode", "original_url": url,
            "content": body, "images": images, "content_hash": chash, "_hash": chash,
            "stats": item.get("stats") or {}, "read_num": (item.get("stats") or {}).get("read", 0),
            "account": item.get("account"),
        }


# 图片动态兜底：weixin download 抽不到这类文章的图（正文只剩标题），
# 改用 browser 在渲染后的页面里取内容图（from=appmsg 是正文图的稳定标记，排除头像/二维码）。
CONTENT_IMG_JS = (
    "(()=>{const out=[],seen=new Set();"
    "for(const i of document.querySelectorAll('img')){"
    "const s=i.getAttribute('data-src')||i.getAttribute('src')||'';"
    "if(s.includes('mmbiz.qpic.cn')&&s.includes('from=appmsg')&&!seen.has(s)){seen.add(s);out.push(s);}}"
    "return JSON.stringify(out);})()"
)


def body_is_titleonly(body: str) -> bool:
    """正文除标题(#)行外无实质内容 → adapter 抽取失败的信号。"""
    return not [ln for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def _img_ext(url: str) -> str:
    m = re.search(r"wx_fmt=(\w+)", url)
    return "." + m.group(1) if m else ".jpg"


def fetch_content_images(root: str, url: str, session: str = "blogsync-wxart") -> list:
    """browser 打开文章页，提取正文内容图 URL（from=appmsg）。"""
    oc = lib.opencli_cmd(root)
    try:
        lib.run_text(oc + ["browser", session, "open", url], "weixin", timeout=120)
        out = lib.run_text(oc + ["browser", session, "eval", CONTENT_IMG_JS], "weixin", timeout=60)
    except Exception as e:  # noqa: BLE001
        lib.log("weixin", "img_fallback_failed", result="error", url=url, error=str(e)[:160])
        return []
    finally:
        try:
            lib.run_text(oc + ["browser", session, "close"], "weixin", timeout=30)
        except Exception:  # noqa: BLE001,S110
            pass
    try:
        return json.loads(out.strip())
    except json.JSONDecodeError:
        return []


def apply_image_fallback(root: str, art: dict) -> bool:
    """图片动态补图：取内容图 → 下载 → 用图片重建正文。直接改写 art，返回是否补到。"""
    urls = fetch_content_images(root, art["original_url"])
    if not urls:
        return False
    img_dir = os.path.join(root, "content", "weixin", art["slug"], "images")
    rels = []
    for i, u in enumerate(urls):
        u = u.replace("&tp=webp", "")  # 去掉转码参数取原格式
        fn = f"img_{i + 1:03d}{_img_ext(u)}"
        try:
            lib.download_file(u, os.path.join(img_dir, fn))
            rels.append(f"images/{fn}")
        except Exception as e:  # noqa: BLE001
            lib.log("weixin", "img_dl_failed", result="error", url=u[:120], error=str(e)[:140])
    if not rels:
        return False
    art["content"] = f"# {art['title']}\n\n" + "\n".join(f"![]({r})" for r in rels)
    art["images"] = rels
    art["content_hash"] = art["_hash"] = lib.content_hash(art["title"], art["content"])
    lib.log("weixin", "img_fallback_ok", result="ok", slug=art["slug"], images=len(rels))
    return True


# 图注还原：微信用 <figure><img><figcaption>图注</figcaption></figure> 放图注，但 adapter 经 turndown
# 会把 figcaption 拍平成「图片后的普通段落」——信息在返回 markdown 时已丢失，光看 md 无法和真正文区分。
# 解法：回原文 HTML 取 figcaption 文本集合，只把「图片后紧跟、且文本命中该集合」的段落包成 figure，
# 渲染为图注。命中确切图注文本才动 → 绝不误删真正文。
FIGCAPTION_RE = re.compile(r"<figcaption[^>]*>(.*?)</figcaption>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
IMG_LINE_RE = re.compile(r"^!\[[^\]]*\]\([^)]+\)$")


def parse_figcaptions(page: str) -> set:
    """从 HTML 文本解析 <figcaption> 内文（去内层标签 + 反转义实体）。纯函数，可单测。"""
    caps = set()
    for raw in FIGCAPTION_RE.findall(page):
        text = html.unescape(TAG_RE.sub("", raw)).strip()
        if text:
            caps.add(text)
    return caps


def extract_figcaptions(url: str) -> set | None:
    """拉文章 HTML，取 <figcaption> 文本集合。拉取失败返回 None（与「无图注」的空集区分，便于下次重试）。"""
    try:
        page = lib.http_get_text(url)
    except Exception as e:  # noqa: BLE001
        lib.log("weixin", "figcaption_fetch_failed", result="error", url=url, error=str(e)[:160])
        return None
    return parse_figcaptions(page)


def fold_figcaptions(content: str, captions: set) -> str:
    """把「图片行后紧跟、且文本命中图注集合」的段落，连同图片一起包成 <figure><figcaption>。"""
    if not captions:
        return content
    lines = content.split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if IMG_LINE_RE.match(line.strip()):
            j = i + 1
            while j < n and not lines[j].strip():  # 跳过图片与图注间的空行
                j += 1
            if j < n and lines[j].strip() in captions:
                # 空行包裹，让 goldmark 在 figure 块内仍按 markdown 渲染图片（保留页面 bundle 相对路径）
                out += ["<figure>", "", line.strip(), "",
                        f"<figcaption>{lines[j].strip()}</figcaption>", "", "</figure>"]
                i = j + 1
                continue
        out.append(line)
        i += 1
    return "\n".join(out)


def apply_figcaptions(art: dict) -> bool:
    """给单篇补图注。已处理 / 无图则跳过；拉取失败不打标记，留待下次重试。改写 art，返回是否改动。"""
    content = art.get("content", "")
    if art.get("captioned") or "<figcaption" in content or "![" not in content:
        return False
    caps = extract_figcaptions(art.get("original_url", ""))
    if caps is None:  # 拉取失败，下次同步再试
        return False
    art["captioned"] = True  # 标记已处理，避免每次同步重复拉 HTML
    new = fold_figcaptions(content, caps)
    if new == content:
        return False
    art["content"] = new
    art["content_hash"] = art["_hash"] = lib.content_hash(art.get("title", ""), new)
    lib.log("weixin", "figcaption_folded", result="ok", slug=art.get("slug"), caps=len(caps))
    return True


# 在已登录的公众号后台页内同源 fetch，分页枚举全部已发文章 + 互动数据。token 从当前 URL 自取。
# 两个接口按 appmsgid 合并：list_ex 的 appmsgex 有 link/is_deleted（无 stats）；
# 普通变体的 appmsg_info 有 read_num/like_num/old_like_num/share_num/comment_num（无 link）。
PUBLISH_JS = (
    "(async()=>{const token=new URLSearchParams(location.search).get('token');"
    "if(!token)return JSON.stringify({error:'no token (not logged in to mp.weixin.qq.com)'});"
    "const page=async(q)=>{const r=await fetch('https://mp.weixin.qq.com/cgi-bin/appmsgpublish?'+q,"
    "{credentials:'include'});const j=await r.json();"
    "if(j.base_resp&&j.base_resp.ret!==0)throw new Error(j.base_resp.err_msg||'api error');"
    "return JSON.parse(j.publish_page);};"
    "const tk=`&token=${token}&lang=zh_CN&f=json&ajax=1`;"
    "const arts=[];let begin=0,total=1;"  # pass1: 文章 + 链接 + 删除标记
    "try{while(begin<total){const pp=await page(`sub=list&begin=${begin}&count=20"
    "&type=101_1&free_publish_type=1&sub_action=list_ex`+tk);total=pp.total_count;"
    "for(const it of pp.publish_list){const info=JSON.parse(it.publish_info);"
    "for(const a of (info.appmsgex||[])){if(!a.is_deleted&&a.link)"
    "arts.push({appmsgid:a.appmsgid,title:a.title,url:a.link,t:a.update_time});}}"
    "begin+=20;if(!pp.publish_list.length)break;}"
    "const stat={};begin=0;total=1;"  # pass2: 互动数据按 appmsgid 建表
    "while(begin<total){const pp=await page(`sub=list&begin=${begin}&count=20`+tk);total=pp.total_count;"
    "for(const it of pp.publish_list){const info=JSON.parse(it.publish_info);"
    "for(const a of (info.appmsg_info||[]))stat[a.appmsgid]={read:a.read_num||0,like:a.like_num||0,"
    "look:a.old_like_num||0,share:a.share_num||0,comment:a.comment_num||0};}"
    "begin+=20;if(!pp.publish_list.length)break;}"
    "for(const x of arts)x.stats=stat[x.appmsgid]||null;"  # join
    "return JSON.stringify(arts);}catch(e){return JSON.stringify({error:String(e)});}})()"
)


# 当前登录账号始终会爬；EXTRA_ACCOUNTS 是在它之外**额外切换**进去爬的服务号（昵称→后台 username）。
EXTRA_ACCOUNTS = {"AgileByte": "gh_8112e079e950"}

# whoami：当前账号昵称 + 全部可切换账号的 昵称→username 映射（用于切回原账号）。
WHOAMI_JS = (
    "(async()=>{const t=new URLSearchParams(location.search).get('token');"
    "if(!t)return JSON.stringify({error:'no token (not logged in)'});"
    "const me=await (await fetch(`https://mp.weixin.qq.com/misc/appmsganalysis?action=report&type=single_v2"
    "&begin_date=2024-01-01&end_date=2024-01-02&token=${t}&lang=zh_CN&f=json&ajax=1`,{credentials:'include'})).json();"
    "const al=await (await fetch(`https://mp.weixin.qq.com/cgi-bin/switchacct?action=get_acct_list"
    "&token=${t}&lang=zh_CN&f=json&ajax=1`,{credentials:'include'})).json();const map={};"
    "for(const g of ['biz_list','service_biz_list','sub_biz_list'])"
    "for(const x of ((al[g]||{}).list||[]))map[x.nickname]=x.username;"
    "return JSON.stringify({nickname:(me.user_info||{}).nick_name,accounts:map});})()"
)


def _switch_js(username: str) -> str:
    return ("(async()=>{const t=new URLSearchParams(location.search).get('token');"
            "const r=await fetch('https://mp.weixin.qq.com/cgi-bin/switchacct?action=switch'"
            f"+`&token=${{t}}&lang=zh_CN&f=json&ajax=1&username={username}`,"
            "{method:'POST',credentials:'include'});return await r.text();})()")


def crawl_accounts(root: str, extras: list, session: str = "blogsync-mp"):
    """爬当前账号 + 依次切换到 extras 各账号，每篇打上 account 昵称。无论成败都切回原账号。
    返回 (items, primary_nickname, error)。error 非 None 表示爬取中途失败（供摘要上浮，勿伪装成功）。"""
    oc = lib.opencli_cmd(root)
    home = "https://mp.weixin.qq.com/"

    def ev(js, timeout=180):
        return lib.run_text(oc + ["browser", session, "eval", js], "weixin", timeout=timeout)

    def parse(out):
        try:
            return json.loads(out.strip())
        except json.JSONDecodeError:
            return None

    items, primary_nick, primary_user, error = [], None, None, None
    try:
        lib.run_text(oc + ["browser", session, "open", home], "weixin", timeout=120)
        who = parse(ev(WHOAMI_JS, 90)) or {}
        primary_nick = who.get("nickname")
        name2user = who.get("accounts") or {}
        primary_user = name2user.get(primary_nick)
        if extras and not primary_user:  # 拿不到原账号 username → 切号后无法切回，提前告警
            lib.log("weixin", "no_primary_user", result="error", nickname=primary_nick,
                    reason="原账号昵称不在可切换映射，切号后将无法切回")

        def discover(nick):
            d = parse(ev(PUBLISH_JS))
            if isinstance(d, dict):
                lib.log("weixin", "discover_error", result="error", account=nick, reason=d.get("error"))
                return
            for it in (d or []):
                it["account"] = nick
            items.extend(d or [])
            lib.log("weixin", "discovered", result="ok", account=nick, count=len(d or []))

        discover(primary_nick)
        for uname in extras:
            if primary_user and uname == primary_user:
                continue
            ev(_switch_js(uname), 60)
            lib.run_text(oc + ["browser", session, "open", home], "weixin", timeout=120)  # 重载取新 token
            discover((parse(ev(WHOAMI_JS, 90)) or {}).get("nickname") or uname)
    except Exception as e:  # noqa: BLE001
        error = str(e)[:200]
        lib.log("weixin", "crawl_failed", result="error", error=error)
    finally:
        # 无论成功/异常都切回原账号，避免把用户后台会话留在 extra 账号上（劫持其手工操作）
        if primary_user:
            try:
                ev(_switch_js(primary_user), 60)
                lib.run_text(oc + ["browser", session, "open", home], "weixin", timeout=120)
            except Exception as e:  # noqa: BLE001
                lib.log("weixin", "switchback_failed", result="error", error=str(e)[:160])
        try:
            lib.run_text(oc + ["browser", session, "close"], "weixin", timeout=30)
        except Exception:  # noqa: BLE001,S110
            pass
    return items, primary_nick, error


def run(root: str, author: str, refresh: bool, render: bool) -> dict:
    out = os.path.join(root, "content", "weixin", "articles.json")
    existing = lib.load_json(out, [])
    results = [] if refresh else list(existing)

    # 0. 既有正文重新清洗（幂等）：让新增的尾部垃圾规则（如「本文首发于微信公众号」）
    #    也作用于历史文章，无需 --refresh 重抓。仅在内容变化时更新 hash。
    recleaned = 0
    for a in results:
        c = a.get("content", "")
        nc = strip_trailing_junk(c)
        if nc != c:
            a["content"] = nc
            a["content_hash"] = a["_hash"] = lib.content_hash(a.get("title", ""), nc)
            recleaned += 1

    # 1. 多账号发现：当前登录账号 + EXTRA_ACCOUNTS（切换进去爬，结束切回）。每篇带 account。
    found, primary, crawl_error = crawl_accounts(root, list(EXTRA_ACCOUNTS.values()))
    primary = primary or "nekocode"
    for a in results:
        a.setdefault("account", primary)  # 既有文章归属当前主账号
    # 跨账号去重 key = 账号 + 归一化标题（不同号可能有同名文章）
    def akey(x):
        nt = norm_title(x.get("title"))
        if not nt:  # 标题纯 emoji/标点 → 归一化为空，回退稳定标识，避免不同篇互相误判重复
            nt = x.get("sn") or extract_sn(x.get("url") or x.get("original_url") or "")
        return f"{x.get('account') or primary}|{nt}"

    # 2. 迁移现存手工 index.md（归属主账号，不重抓不破坏）
    known = {akey(a) for a in results}
    migrated = 0
    for path in glob.glob(os.path.join(root, "content", "weixin", "*", "index.md")):
        art = article_from_md(root, path)
        if art:
            art["account"] = art.get("account") or primary  # 已存 account 则保留，否则归主账号
            if akey(art) not in known:
                known.add(akey(art))
                results.append(art)
                migrated += 1

    # 3. 已有文章：刷新永久链接 original_url + stats（浏览量累积，每次同步都更新）
    by = {akey(a): a for a in results}
    refreshed = stats_n = 0
    for it in found:
        a = by.get(akey(it))
        if not a:
            continue
        if it.get("url") and a.get("original_url") != it["url"]:
            a["original_url"] = it["url"]
            refreshed += 1
        if it.get("stats"):
            a["stats"] = it["stats"]
            a["read_num"] = it["stats"].get("read", 0)  # 顶层冗余一份，方便跨源汇总访问量
            stats_n += 1

    # 4. 下载未见过的文章
    todo = [it for it in found if akey(it) not in known]
    downloaded = [a for a in lib.parallel_map(
        todo, lambda it: article_from_download(root, it, author), workers=4, who="weixin") if a]
    # 图片动态兜底：adapter 抽不到图的（正文仅标题）用 browser 补内容图。串行（少见、复用单会话）。
    for a in downloaded:
        if not a["images"] and body_is_titleonly(a["content"]):
            apply_image_fallback(root, a)
    added = 0
    for a in downloaded:
        if akey(a) not in known:
            known.add(akey(a))
            results.append(a)
            added += 1

    # 5. 图注还原：回原文 HTML 取 figcaption，把被 turndown 拍平成正文的图注重新包成 <figure>。
    #    覆盖新老文章（带 captioned 标记，幂等，已处理的不重复拉 HTML）。HTTP-bound，并发拉取。
    cand = [a for a in results if not a.get("captioned")
            and "<figcaption" not in a.get("content", "") and "![" in a.get("content", "")]
    folded = sum(1 for x in lib.parallel_map(cand, apply_figcaptions, workers=4, who="weixin") if x)

    results.sort(key=lambda a: a.get("date", ""), reverse=True)
    lib.write_json_atomic(out, results)
    lib.log("weixin", "synced", found=len(found), migrated=migrated, added=added,
            refreshed=refreshed, stats=stats_n, folded=folded, recleaned=recleaned,
            error=crawl_error,
            total_reads=sum(a.get("read_num", 0) for a in results), total=len(results))

    if render:
        script = os.path.join(root, ".scripts", "render_weixin.py")
        lib.run_text([sys.executable, script], "weixin", timeout=120)
    # error 非 None：爬取中途失败（如掉登录 / bridge 死），摘要上浮，勿被当作"无新内容"
    return {"source": "weixin", "total": len(results), "migrated": migrated, "added": added,
            "refreshed": refreshed, "stats": stats_n, "folded": folded, "recleaned": recleaned,
            "error": crawl_error,
            "total_reads": sum(a.get("read_num", 0) for a in results), "found": len(found)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync 微信公众号 → articles.json (incremental)")
    ap.add_argument("--root", default=lib.find_root())
    ap.add_argument("--author", default="", help="非空则只收该作者文章（后台均为本号，一般无需设）")
    ap.add_argument("--refresh", action="store_true", help="忽略既有 JSON，全量重建")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    print(json.dumps(run(a.root, a.author, a.refresh, render=not a.no_render), ensure_ascii=False))


if __name__ == "__main__":
    main()
