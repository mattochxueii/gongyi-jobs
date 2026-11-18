# 项目总纲与作战蓝图 (BLUEPRINT)

## 1. 项目定位与目标
- **性质**：公益开源/静态化招聘信息聚合导航站（部署于 GitHub + Cloudflare Pages，零后端服务器成本）。
- **核心覆盖**：
  1. **事业编 / 体制内公开招考**：首期试点 **河南省**（省人社厅、省人事考试中心及重点地市官方公告）。
  2. **高福利民营企业白名单**：只做正向客观收录（明确双休/弹性工作/高福利），所有福利标签必须附带官网招聘原文引用与来源链接。
- **架构原则**：Git 即数据库 + 纯静态构建 + Python 确定性抓取 + AI 增量结构化。

---

## 2. 目录结构规范
```text
gongyi-jobs/
├── AGENTS.md                 # AI 协作铁律（所有模型必读）
├── BLUEPRINT.md              # 本总纲文档
├── README.md                 # 项目说明与本地运行指南
├── config/
│   └── sources.json          # 采集源注册表（河南省政务站 + 民企白名单源）
├── schemas/
│   ├── job.schema.json       # 岗位数据契约
│   ├── company.schema.json   # 民企白名单数据契约
│   └── source.schema.json    # 采集源配置契约
├── data/
│   ├── jobs.json             # 线上展示用全量活跃/已归档岗位库
│   ├── companies.json        # 线上展示用高福利民企白名单库
│   └── archive/              # 按月归档的已截止历史岗位
├── raw_inbox/                # 待 AI 结构化解析的增量纯文本队列（不入 Git）
├── scrapers/                 # Python 增量采集、附件抽取与入库引擎
├── scripts/
│   └── validate_data.py      # 全量数据契约与完整性校验器
├── tests/                    # 自动化单元测试与测试夹具
└── site/                     # 纯静态网站源码（Astro + Tailwind CSS）
```

---

## 3. 协同作战分工（指挥官 + Argon + Hydrogen）
- **总指挥官（当前主会话）**：负责契约锁定、拆解任务指令、代码与数据验收、安全与合规把关、Git 节奏控制。
- **Gemini 4 Argon（按周配额，精锐重炮）**：负责高难度结构设计、Python 抓取底座与复杂政务附件解析设计、代码深层审计。
- **Gemini 4 Hydrogen（短周期配额，主力工程兵）**：负责静态前端站点搭建、具体省市站点/SaaS 适配器编写、日常读取 `raw_inbox/` 增量文本转为标准 JSON。

---

## 4. 数据更新流水线（SOP）
1. **增量抓取（Python 零 AI 消耗）**：
   - 运行采集脚本抓取 `config/sources.json` 中的启用源。
   - 对接标准招聘 SaaS（Moka / 北森 / 飞书等）直接生成标准化待审核 JSON。
   - 政务公告抓取正文及 Word/Excel/PDF 附件纯文本，计算 `content_hash`，对比历史已处理记录；仅将**全新或变更**公告写入 `raw_inbox/*.txt`。
2. **增量解析（Hydrogen 批处理）**：
   - 仅读取 `raw_inbox/` 内的新增文件，按 `schemas/job.schema.json` 抽取结构化记录并合入 `data/jobs.json`。
3. **校验与生命周期管理（Python）**：
   - 运行 `python scripts/validate_data.py` 检查字段合法性、去重指纹与证据引用完整性。
   - 过期岗位（`deadline_at < 今天`）自动标为 `expired` 或移入 `data/archive/`。
4. **发布同步**：
   - `git commit && git push` 推送至 GitHub，触发 Cloudflare Pages 自动构建上线。
