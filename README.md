# Nekocode 的博客

[nekocode.cn](https://nekocode.cn) · 基于 [Hugo](https://gohugo.io) + hugo-xmin 主题。

聚合个人多平台内容于一站：手写博客，外加自动同步的 GitHub / X / 微信公众号 / 知乎。

## 结构

```
content/        博客文章 + 各源数据（github/twitter/weixin/zhihu）
layouts/        各源页面模板
.scripts/       数据 → 页面 的渲染脚本
.claude/skills/blog-sync/   抓取并增量同步各源到结构化 JSON
deploy.sh       构建并部署
```

## 开发

```bash
hugo server        # 本地预览
./deploy.sh        # 构建 + 部署
```

内容同步交由 `blog-sync` skill 驱动（详见 `.claude/skills/blog-sync/SKILL.md`）。
