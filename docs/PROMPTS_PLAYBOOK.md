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


---

## 战役 2A：发给 `Gemini 4 Hydrogen`（接通河南省真实官网站点 + 实抓进入 `raw_inbox/`）

> 说明：本步骤需要访问外网/国内政务网（执行探测脚本时如触发沙箱联网审批，请点允许）。严禁伪造任何 URL 或公告内容。

```text
请先阅读 AGENTS.md、BLUEPRINT.md、config/sources.json、scrapers/base.py 和 scrapers/core/extractors.py。
你本次的任务是作为“河南省采集适配器工程师”，打通真实的河南省事业编官方数据源并生成第一批真实待解析文本（严禁修改 site/，严禁直接往 data/jobs.json 写假数据）：

1. 【修复一个底座小问题】
   在 scrapers/core/extractors.py 约第190行，去掉 `" appendix"` 的前导空格；如实测政务网站通过动态下载接口（如带 Content-Disposition 或 download/down 参数）提供附件，请顺手完善附件发现逻辑。

2. 【实测探测河南省官方站点真实栏目页（必须真实联网读取，禁止猜测 subpath）】
   - 探测 https://hrss.henan.gov.cn 与 http://www.hnrsks.com 的真实导航/栏目页结构（如“公示公告 / 招考录用 / 事业单位公开招聘”栏目 URL、分页/AJAX JSON 接口、编码与正文容器选择器）。
   - 如果其中某个栏目近期主要是“面试/体检/拟聘用公示”而非“正在报名招考公告”，请在适配器里设计合理的标题关键词过滤/分类逻辑（过滤掉明显的“成绩查询/体检通知/拟聘用公示”，优先保留“招聘公告/招考简章/选聘/引进”）。
   - 如省直页面近期在招公告较少，可同步补充 1~2 个真实的河南省重点地市人社局/人事考试网（如郑州、洛阳等，必须联网实测确认根域名和列表页 URL 可访问）。

3. 【更新配置与专用适配器】
   - 将实测确认可用的真实 list_urls 填入 config/sources.json，并将对应源的 url_verified 与 enabled 设为 true。
   - 完善 scrapers/base.py 中的 HenanHrssAdapter、HenanRsksAdapter（或拆为 scrapers/adapters/ 独立模块并在 ADAPTER_REGISTRY 注册），确保列表页解析和详情页正文/附件抽取准确无噪音。
   - 在 tests/test_henan_adapters.py 补充针对真实网页结构片段的离线单元测试（fixture 放 tests/fixtures/）。

4. 【跑通真实增量抓取】
   - 运行 python -m scrapers.run --max-items 8（真实抓取首批公告到 raw_inbox/，观察是否有解码乱码或附件下载报错）。
   - 再次运行一次 python -m scrapers.run --max-items 8 验证增量去重（第二次应全部输出 unchanged，0 新写入）。

验收标准（必须亲自跑通并贴出结果）：
1. python scripts/validate_data.py 输出 [OK]
2. python -m unittest discover -s tests -v 全部通过
3. 贴出两次运行 python -m scrapers.run --max-items 8 的真实终端输出，以及 raw_inbox/ 中生成的文件列表和其中 1 份文件的前 40 行内容摘要。
```

---

## 战役 2B：发给 `Gemini 4 Hydrogen`（首批高福利民企白名单核验 + 招聘 SaaS 适配器）

> 说明：建议在战役 2A 验收通过后再发本指令（避免同时修改 `config/sources.json`）。如果希望先并行收集企业官网信息，可先只做 `data/companies.json` 核验。

```text
请先阅读 AGENTS.md、schemas/company.schema.json、schemas/job.schema.json 和 scrapers/base.py。
你本次的任务是完成“高福利民营企业白名单冷启动 + 官方招聘页适配器”（严禁捏造福利或网址，严禁修改 site/）：

1. 【真实官网核验首批 5~10 家高福利民企】
   - 选取在河南省有岗位、支持远程办公、或行业内公认且官网有明确书面说明的优质民营企业（优先寻找官网使用 Moka、飞书招聘、北森、Greenhouse、Lever 等标准招聘页的公司）。
   - 对每一家企业，必须真实访问其官网或官方招聘页，摘录逐字原文作为 evidence_quote（例如官网明确写了“周末双休”“不打卡”“补充医疗保险”“带薪年假”等）及对应 evidence_url。找不到官网明文依据的福利一律不写。
   - 按 schemas/company.schema.json 写入 data/companies.json。

2. 【实现至少 1 个通用招聘 SaaS 适配器】
   - 根据上述核验企业实际使用的招聘系统（如飞书招聘 / Moka / Greenhouse / Lever API），在 scrapers/adapters/ 实现对应的适配器并注册到 ADAPTER_REGISTRY，在 config/sources.json 登记并启用。
   - 对于可以直接拿到结构化 JSON 的 SaaS 接口，直接转为符合 job.schema.json 的标准化岗位列表，或写入可控的待合入中间文件，再调用 scrapers/core/merger.py 合入 data/jobs.json（绝不浪费 AI Token）。

验收标准（必须亲自跑通并贴出结果）：
1. python scripts/validate_data.py 输出 [OK]（展示实际通过校验的 companies 数量与 jobs 数量）
2. python -m unittest discover -s tests -v 全部通过
3. 列出每家入库企业的 evidence_url 与原文引述摘要，确认全部来自真实可访问页面。
```


---

## 战役 3：发给 `Gemini 4 Hydrogen`（`raw_inbox/` 河南事业编真实解析入库 + 过期归档 + 静态站全量端到端验证）

> 说明：本步骤严格控制额度——只精细拆分**近期活跃**公告里的具体岗位表，对 2026 年 2–6 月已过期的大体积联考公告只读前 120 行生成“公告级历史归档记录”，绝不把 363 KB 大表全量逐行喂给 AI。

```text
请先阅读 AGENTS.md、schemas/job.schema.json、scripts/validate_data.py 和 scrapers/core/merger.py。
你本次的任务是将 raw_inbox/ 中已抓取的 12 份真实河南公告整理为标准 JSON，安全合入系统并跑通整站静态构建（严禁修改 site/ 源码，严禁捏造任何不存在于原文中的字段）：

1. 【分类处置 raw_inbox/*.txt（严格省额度）】
   - 跳过噪音：忽略 `henan_xuchang_rsj_1c9f4f19e5ca.txt`（《西部某市公安局招聘辅警公告》，非事业编且属噪音）。
   - 近期活跃公告精细拆分：重点读取 `henan_xuchang_rsj_5cc79c914978.txt`（许昌职业技术学院 2026 年第二批考核招聘博士研究生公告，报名至 2026-11-30），根据正文与附带的 `.xlsx` 计划表按真实学科/岗位组拆分为结构化岗位记录（可先用简短 Python 脚本把 `.xlsx` 管线输出转换成结构化草稿再校对，确保每条均带真实 `source_url`、`apply_start_at`、`deadline_at: "2026-11-30"`、`education_min: "博士"`、`majors`、`headcount` 与 `attachments`）。
   - 近期进程中/刚截止公告：读取 `henan_luoyang_rsks_d9fe499bc92a.txt`（大学生乡村医生专项计划）与 `henan_luoyang_rsks_fc479adc014f.txt`（2027 军队文职公开招考）正文关键时间节点，各生成 1 条准确的公告级记录（如实填写 `published_at` 与网报 `deadline_at`，若网报已过则在 `summary` 与 `other_requirements` 如实写明当前后续审核/缴费节点）。
   - 历史已过期公告（2026 年 2–6 月，含 363KB / 160KB 大表）：**严禁全文逐行读表浪费上下文**！每份文件只读前 120 行（元数据 + 正文公告），各生成 1 条“公告级记录”（保留原 `attachments` 官链），如实填写其历史 `deadline_at`（或 `published_at`）及 `status: "expired"`。

2. 【安全合入、校验与过期归档】
   - 调用 `scrapers/core/merger.py` 中的 `merge_jobs_into_dataset`（`archive_expired=True`）将上述解析结果安全合入 `data/jobs.json`（保留原有的 69 条高福利民企岗位不被覆盖），并自动将过期公告归档至 `data/archive/YYYY-MM.json`。
   - 确认 `data/jobs.json` 中既包含这批真实的河南省事业编/招考在招岗位，又包含全部 69 条高福利民企岗位；若已全部归档处理完毕，清理已入库的 `raw_inbox/*.txt`（保留 `.gitkeep`）或记录处理日志。

3. 【整站静态构建端到端验收】
   - 运行 `python scripts/validate_data.py` 确保通过。
   - 运行 `python -m unittest discover -s tests -v` 确保全部单元测试通过。
   - 进入 `site/` 目录执行默认生产构建 `npm run build`（禁止开 `PREVIEW_FIXTURES=1`），核验 `site/dist/public/index.html` 与 `site/dist/companies/index.html` 均真实渲染出了本次入库的河南事业编岗位与 8 家高福利民企。

验收标准（必须亲自跑通并贴出结果）：
1. 贴出 `python scripts/validate_data.py` 输出（展示 `jobs`、`companies`、`sources` 数量）及 `data/archive/` 归档文件清单。
2. 贴出 `python -m unittest discover -s tests -v` 结果与 `site/` 下 `npm run build` 完整输出。
3. 核查 `site/dist/public/index.html` 中确实包含真实许昌职业技术学院岗位及官方附件链接，无虚构数据。
```
