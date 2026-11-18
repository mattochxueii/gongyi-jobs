# 总指挥官指令手册 (PROMPTS_PLAYBOOK)

本文件记录各阶段发给 **Gemini 4 Argon** 与 **Gemini 4 Hydrogen** 的标准作战指令包（已做好目录隔离，可并行执行）。

---

## 战役 1A：发给 `Gemini 4 Argon`（Python 增量抓取底座 + Schema 审校）

> 复制下方整段给 **Gemini 4 Argon**（打开工作区 `gongyi-jobs` 后执行）：

```text
请先阅读工作区根目录的 AGENTS.md、BLUEPRINT.md、schemas/*.json、config/sources.json 和 scripts/validate_data.py。
你本次的任务是作为“架构与核心底座工程师”，完成两件事（严禁修改 site/ 目录，严禁向 data/jobs.json 或 data/companies.json 写入任何假数据）：

1. 【Schema 审校（按需微调）】
   检查 schemas/job.schema.json 是否足以精准承载“河南省省直及地市事业单位公开招聘公告及岗位表附件”以及“高福利民企白名单”。如确有遗漏关键字段（如招聘岗位层级、面试/笔试时间或报名方式等），请补充完善 schema，并同步更新 scripts/validate_data.py 与 tests/test_validate_data.py，确保校验逻辑严格一致。

2. 【建设 scrapers/ Python 增量抓取与解析底座】
   在 scrapers/ 目录下建设模块化、零冗余的 Python 3.13 引擎：
   - scrapers/requirements.txt：保持轻量（如 httpx, beautifulsoup4, lxml, pdfplumber, python-docx, openpyxl）。
   - scrapers/core/http_client.py：带礼貌限速、超时重试、自动识别国内政务网站编码（UTF-8 / GBK / GB18030）的请求封装。
   - scrapers/core/extractors.py：从 HTML 正文、PDF、.docx、.xlsx 中提取干净纯文本和表格文本的工具（遇到旧版二进制 .doc/.xls 等暂无法直接解析的格式时优雅降级并给出明确提示）。
   - scrapers/core/storage.py：基于 URL + 内容 SHA-256 哈希做本地状态去重（data/raw_cache/crawl_state.json），仅将“全新或内容发生变更”的公告写入 raw_inbox/<source_id>_<short_hash>.txt（顶部附带源URL、标题、发布时间、附件链接等元信息），避免重复消耗后续 AI 解析额度。
   - scrapers/core/merger.py：提供将结构化 JSON 安全合入 data/jobs.json（指纹去重）、把 deadline_at < 今天 的岗位自动标记 expired 并支持按月归档到 data/archive/YYYY-MM.json 的逻辑。
   - scrapers/base.py 与 scrapers/run.py：统一爬虫适配器基类与 CLI 入口（支持 --source / --dry-run）。
   - tests/test_scrapers_core.py：用 tests/fixtures/ 下的本地样例测试上述编码识别、去重哈希、附件提取与合入归档逻辑。

验收标准（必须亲自跑通并贴出结果）：
1. python scripts/validate_data.py 输出 [OK]
2. python -m unittest discover -s tests -v 全部通过
3. 列出所有新建/修改的文件路径，不要改动 site/ 或 data/*.json 中的正式数据。
```

---

## 战役 1B：发给 `Gemini 4 Hydrogen`（Astro 纯静态网站 MVP + Cloudflare Pages 配置）

> 复制下方整段给 **Gemini 4 Hydrogen**（打开工作区 `gongyi-jobs` 后执行，可与上面 Argon 任务并行）：

```text
请先阅读工作区根目录的 AGENTS.md、BLUEPRINT.md、schemas/job.schema.json 和 schemas/company.schema.json。
你本次的任务是作为“前端工程兵”，在 site/ 目录下搭建纯静态公益招聘网站 MVP（严禁修改 scrapers/、schemas/，严禁向根目录 data/jobs.json 或 data/companies.json 写入任何假数据）。

具体要求：
1. 技术栈：在 site/ 目录下初始化 Astro + Tailwind CSS 纯静态项目（必须能在 Cloudflare Pages 纯静态托管，不依赖任何后端服务器或 SSR 运行时）。
2. 数据读取方式：
   - 构建时读取上层目录 ../data/jobs.json 和 ../data/companies.json。
   - 必须保证当 data/*.json 为空数组（0条记录）时，页面和构建依然完全正常，并展示清晰的“数据初始化中/暂无匹配岗位”公益引导状态。
   - 如需本地开发看样式效果，可在 tests/fixtures/ 下放一份开发预览样例，并在 site/ 中仅通过显式环境变量（如 PREVIEW_FIXTURES=1）切换预览，默认生产构建永远只读 ../data/*.json。
3. 页面与核心功能（中文界面，简洁克制、可读性强、移动端适配）：
   - 首页 (/)：项目公益宗旨、活跃事业编与白名单企业数统计、最后更新时间、快捷搜索框及两大专区入口。
   - 事业编专区 (/public)：聚焦河南省公开招考，支持纯前端实时多维筛选（城市、最低学历、编制类型、是否限应届、关键字/专业搜索），每条岗位高亮显示“报名截止倒计时”（如剩X天/今日截止/已截止）和官方原文/附件直达外链。
   - 高福利民企专区 (/companies)：白名单企业与在招岗位展示，支持按休假制度（周末双休/弹性工作/做四休三）、城市、行业、福利标签筛选；每一条福利标签旁必须展示来自官网的原文引用 (evidence_quote) 与溯源链接。
   - 公益说明与纠错页 (/about)：免责声明、数据客观溯源原则、以及“提交纠错/推荐好企业”入口指引。
4. 部署文档：在 site/DEPLOY_CLOUDFLARE.md 写明 Cloudflare Pages 构建参数（Root directory、Build command、Output directory）。

验收标准（必须亲自跑通并贴出结果）：
1. 在 site/ 目录下执行 npm run build 成功产出 site/dist/，零报错（分别在默认空 data/*.json 与 PREVIEW_FIXTURES=1 两种状态下均构建验证通过）。
2. 确认 git status 未污染根目录 data/jobs.json 和 data/companies.json，未改动 scrapers/。
```
