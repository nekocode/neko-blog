#!/bin/sh -ev

# 构建 neko-island（PixiJS 应用）→ dist/，供 Hugo 挂载为 /_island/ 静态资源
( cd external/neko-island && npm install --no-audit --no-fund && npm run build )

# 构建：清空旧产物后全量生成
rm -rf public
hugo

# 部署：本地 public 上传/覆盖到 OSS（apex 主域 nekocode.cn 桶；www 桶仅做 301 跳转，不部署内容）
# 刻意不加 --delete：每次 build 资源带 hash，文件名会变。若删除桶内「本地已无」的旧产物，
# 持有旧 HTML 缓存（未刷新）的用户会因引用的旧 hash 资源 404 而白屏。保留旧资源 → 平滑过渡。
# 代价：桶内会累积历史产物，需要时再人工清理。
aliyun --profile default oss sync ./public/ oss://web-nekocode-cn/ --force

# 刷新 CDN：整目录刷新 apex 主域，让 CDN 回源拉取最新产物。
# HTML 文件名不变（index.html），不刷新则边缘节点会继续吐旧缓存；hash 资源虽改名无需刷，
# 但整目录刷一次最省心、不漏。Directory 类型递归刷新该路径下全部文件。
aliyun --profile default cdn RefreshObjectCaches --ObjectPath 'https://nekocode.cn/' --ObjectType Directory
