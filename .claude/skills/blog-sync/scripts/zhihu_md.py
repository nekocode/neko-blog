#!/usr/bin/env python3
"""知乎正文 HTML → Markdown 转换器（纯函数，标准库 HTMLParser，零外部依赖）。

为什么转 MD 而非保留 HTML：知乎正文充斥 9000+ 噪音 <span>、pygments 高亮 span（博客无对应
CSS → 维持 HTML 即无高亮纯文本 + 满屏 span）、重复的 <noscript> 图、莫名 <br>。转成干净 Markdown
后交给 Hugo goldmark + chroma 统一渲染、代码按 language-xxx 正确高亮，与微信/blog 风格一致。

针对知乎实际标签集处理：p/div、h1-h6、ul/ol/li（含嵌套）、blockquote、pre.highlight+code、
行内 code、a、b/strong、i/em、figure/figcaption、img、br。其余标签透明剥离（只保留文字）。"""
from __future__ import annotations
import re
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs, unquote


def resolve_redirect(href: str) -> str:
    """知乎外链中转 link.zhihu.com/?target=<目标> → 解码出目标 url 直接用。非中转原样返回。"""
    if href and "link.zhihu.com" in href:
        target = parse_qs(urlparse(href).query).get("target")
        if target:
            return unquote(target[0])
    return href

_LANG_RE = re.compile(r"language-(\w+)")
_WS_RE = re.compile(r"\s+")
# 强调用 HTML 标签而非 Markdown **/*：中文里「**词：**后文」这类全角标点结尾的强调，
# 会触发 CommonMark 的 CJK flanking 规则导致 ** 不渲染（字面显示）。HTML 标签由 goldmark
# (unsafe) 直接渲染，绕开该边界问题，中英文一致可靠。
_INLINE_TAG = {"b": "strong", "strong": "strong", "i": "em", "em": "em"}


class _Converter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)  # 自动把 &gt; 等实体转回字符
        self.stack = [[]]        # 输出缓冲栈，栈底为主输出；a/强调/blockquote/figcaption 用子缓冲
        self.code_blocks = []    # 代码块先存这里、正文放占位符，最后还原（避免被空白规整破坏）
        self.in_pre = False
        self.pre_lang = ""
        self.pre_buf = []
        self.in_code = False     # 行内 code
        self.noscript_depth = 0  # >0 时丢弃内容（noscript 是重复图）
        self.list_stack = []     # 每层 [kind, counter]，kind=ul/ol
        self.li_markers = []     # li 子缓冲对应的 (indent, marker) 栈
        self.href = None

    # ---- 缓冲栈 ----
    def emit(self, s: str) -> None:
        self.stack[-1].append(s)

    def push(self) -> None:
        self.stack.append([])

    def pop(self) -> str:
        return "".join(self.stack.pop())

    def block(self) -> None:
        """块级分隔：插入段落空行标记，最后统一规整。"""
        self.emit("\n\n")

    # ---- 标签 ----
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "noscript":
            self.noscript_depth += 1
            return
        if self.noscript_depth:
            return
        if tag == "pre":
            self.in_pre, self.pre_buf, self.pre_lang = True, [], ""
            return
        if self.in_pre:
            if tag == "code":
                m = _LANG_RE.search(d.get("class", ""))
                self.pre_lang = m.group(1) if m else ""
            return  # pre 内其它标签（高亮 span）忽略，只收文字
        if tag == "br":
            self.emit("  \n")  # 硬换行（想法的软换行靠它保留）
            return
        if tag == "img":
            # 知乎懒加载：外层 <img> 的 src 常是 data:svg 占位，真实图在 data-actualsrc/data-original
            # （后者是 _r 原图，更高清）。跳过 data: 占位，按优先级取真实地址，否则整图丢失/取到占位。
            src = ""
            for attr in ("data-actualsrc", "data-original", "src"):
                v = d.get(attr, "")
                if v and not v.startswith("data:"):
                    src = v
                    break
            if src:
                self.block()
                self.emit(f"![]({src})")
                self.block()
            return
        if tag in ("p", "div", "section"):
            self.block()
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.block()
            self.emit("#" * int(tag[1]) + " ")
        elif tag in ("ul", "ol"):
            if not self.list_stack:
                self.block()
            self.list_stack.append([tag, 0])
        elif tag == "li":
            # li 用子缓冲捕获：知乎常在 li 里塞 <p>，其段落分隔会把 marker 和内容拆开、破坏列表。
            # 捕获后 strip 掉内部首尾空行，结束时再拼成「marker + 内容（多段落缩进续行）」。
            lvl = max(0, len(self.list_stack) - 1)
            top = self.list_stack[-1] if self.list_stack else ["ul", 0]
            if top[0] == "ol":
                top[1] += 1
                marker = f"{top[1]}. "
            else:
                marker = "- "
            self.li_markers.append(("  " * lvl, marker))
            self.push()
        elif tag == "blockquote":
            self.block()
            self.push()
        elif tag == "figure":
            self.block()
        elif tag == "figcaption":
            self.push()
        elif tag == "code":
            self.in_code = True
            self.emit("`")
        elif tag == "a":
            self.href = d.get("href")
            self.push()
        elif tag in _INLINE_TAG:
            self.push()

    def handle_endtag(self, tag):
        if tag == "noscript":
            self.noscript_depth = max(0, self.noscript_depth - 1)
            return
        if self.noscript_depth:
            return
        if tag == "pre":
            self.in_pre = False
            self.code_blocks.append((self.pre_lang, "".join(self.pre_buf)))
            self.block()
            self.emit(f"\x00CB{len(self.code_blocks) - 1}\x00")
            self.block()
            return
        if self.in_pre:
            return
        if tag in ("p", "div", "section", "h1", "h2", "h3", "h4", "h5", "h6", "figure"):
            self.block()
        elif tag == "li":
            content = self.pop().strip()
            indent, marker = self.li_markers.pop() if self.li_markers else ("", "- ")
            if not content:
                self.emit("\n" + indent + marker.rstrip())
            else:
                # 多段落 li：首行接 marker，后续行按 (indent + marker 宽度) 缩进续行
                lines = content.split("\n")
                cont = indent + " " * len(marker)
                rest = [(cont + ln if ln.strip() else "") for ln in lines[1:]]
                self.emit("\n" + indent + marker + lines[0]
                          + ("\n" + "\n".join(rest) if rest else ""))
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            if not self.list_stack:
                self.block()
        elif tag == "blockquote":
            # 先压缩引用内多余空行（此时代码还是占位符、安全），再把代码块占位符就地还原成多行，
            # 这样下面逐行加 "> " 时代码每一行都带引用前缀——否则代码行会"跳出"引用、引用与代码全乱。
            text = re.sub(r"\n{3,}", "\n\n", self.pop().strip())
            text = self._restore_code(text)
            quoted = "\n".join(("> " + ln) if ln else ">" for ln in text.split("\n"))
            self.emit(quoted)
            self.block()
        elif tag == "figcaption":
            cap = self.pop().strip()
            if cap:
                self.block()
                self.emit(f"*{cap}*")
                self.block()
        elif tag == "code":
            self.in_code = False
            self.emit("`")
        elif tag == "a":
            text = self.pop().strip()
            href, self.href = self.href, None
            href = resolve_redirect(href)  # 知乎外链中转 link.zhihu.com → 目标 url
            # 知乎用户主页链接（@用户提及）→ 去链接、只留文本（含不带 @ 的昵称提及）
            if text and href and "zhihu.com/people/" not in href:
                self.emit(f"[{text}]({href})")
            elif text:
                self.emit(text)
        elif tag in _INLINE_TAG:
            text = self.pop().strip()
            if text:
                name = _INLINE_TAG[tag]
                self.emit(f"<{name}>{text}</{name}>")

    def handle_data(self, data):
        if self.noscript_depth:
            return
        if self.in_pre:
            self.pre_buf.append(data)
            return
        if self.in_code:
            self.emit(data)
            return
        text = _WS_RE.sub(" ", data)  # HTML 折叠空白：多空白/换行 → 单空格
        if text:
            self.emit(text)

    def _restore_code(self, text: str) -> str:
        """把代码块占位符 \\x00CB{i}\\x00 还原成 ```lang 围栏。供 blockquote 与最终输出共用。"""
        for i, (lang, code) in enumerate(self.code_blocks):
            text = text.replace(f"\x00CB{i}\x00", f"```{lang}\n{code.strip(chr(10))}\n```")
        return text

    def result(self) -> str:
        # 先压缩多余空行（代码块仍是占位符、不受影响），再还原代码块——避免规整压掉代码内空行。
        # 注意：不剥行尾空白——Markdown 硬换行正是「行尾两空格 + \n」，剥了想法就丢换行。
        text = re.sub(r"\n{3,}", "\n\n", "".join(self.stack[0]))
        return self._restore_code(text).strip()


def to_markdown(html: str) -> str:
    """知乎正文 HTML → 干净 Markdown。空输入返回空串。纯函数，可单测。"""
    if not html:
        return ""
    conv = _Converter()
    conv.feed(html)
    conv.close()
    return conv.result()


# ---- Markdown 图片本地化辅助（转换后正文里的图是 ![](url)）----
MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def extract_md_img_urls(md: str) -> list:
    """抽出 Markdown 正文里全部图片 URL（去重保序）。"""
    out, seen = [], set()
    for u in MD_IMG_RE.findall(md or ""):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def localize_md_imgs(md: str, url_to_local: dict) -> str:
    """把 Markdown 图片 URL 按映射替换成本地路径（未命中保留原 URL）。"""
    def repl(m):
        url = m.group(1)
        return f"![]({url_to_local.get(url, url)})"
    return MD_IMG_RE.sub(repl, md or "")
