# Cloudflare Pages 纯静态部署指南

本前端站点基于 **Astro + Tailwind CSS** 构建，采用纯静态输出模式（`output: 'static'`），无需任何 Cloudflare Workers / Pages Functions 后端运行时。

---

## 1. Cloudflare Pages 构建参数

在 Cloudflare Dashboard 中连接本 GitHub 仓库后，按以下参数配置 **Build & deployments**：

| 配置项 | 填写值 | 说明 |
| --- | --- | --- |
| **Framework preset** | `Astro`（或 `None`） | 纯静态生成 |
| **Root directory** | `site` | 前端项目位于仓库 `site/` 子目录 |
| **Build command** | `npm run build` | 执行 `astro build` 生成静态产物 |
| **Build output directory** | `dist` | 相对于 Root directory，对应仓库路径 `site/dist` |

---

## 2. 运行环境与环境变量要求

1. **Node.js 版本**：必须在 Cloudflare Pages 环境变量中设置 `NODE_VERSION` 为 `22` 或 `24`（本工程安装的 Astro 7 要求 Node.js `>= 22.23.4`）。
2. **禁止设置预览开关**：
   - **生产环境严禁配置 `PREVIEW_FIXTURES=1`**。
   - 默认构建时，[src/lib/data.ts](./src/lib/data.ts) 会读取上层目录 `../data/jobs.json` 与 `../data/companies.json`。
   - Cloudflare Pages 即使设置了 Root directory 为 `site`，也会完整克隆整个 Git 仓库，因此构建期可直接访问 `../data/*.json`。
   - 若 `data/*.json` 中 `items` 为空数组，站点依然能零报错完成构建，并渲染友好的“数据初始化中”引导界面。

---

## 3. 本地开发与双模式构建验证

在 `site/` 目录下执行：

```bash
# 安装依赖
npm install

# 1) 默认生产模式构建（读取 ../data/jobs.json 与 ../data/companies.json）
npm run build

# 2) 本地样式预览模式（读取 ../tests/fixtures/preview_site_dataset.json）
# Linux / macOS:
PREVIEW_FIXTURES=1 npm run dev
PREVIEW_FIXTURES=1 npm run build

# Windows PowerShell:
$env:PREVIEW_FIXTURES="1"; npm run build; Remove-Item Env:PREVIEW_FIXTURES
```

---

## 4. 自动更新机制

当采集流水线更新根目录 `data/jobs.json` 或 `data/companies.json` 并推送至默认分支（如 `master` / `main`）时，Cloudflare Pages 会自动触发一次静态重建，将最新岗位与白名单数据同步至全球 CDN 边缘节点。

---

## 5. 自定义域名绑定（`fairjobs.ccwu.cc`）

1. 完成首次 `*.pages.dev` 构建并确认页面可正常访问后，进入 Cloudflare Pages 项目的 **Custom domains** -> **Set up a custom domain**，填写 `fairjobs.ccwu.cc`。
2. 在 `ccwu.cc` 域名管理面板中，为 `fairjobs` 添加一条 **CNAME 记录**指向 Cloudflare 分配的 `<project>.pages.dev` 目标地址（如 Cloudflare 页面同时提示 TXT 验证记录，按页面指示一并补齐）。
3. 回到 Cloudflare Pages 等待域名状态变为 **Active**（SSL/TLS 证书自动签发），随后通过 `https://fairjobs.ccwu.cc` 核验首页、`/public`、`/companies` 与 `/about` 加载正常。
