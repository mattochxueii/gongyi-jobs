# 公益岗位聚合网站（Henan Pilot + Good-Benefit Companies）

一个零后端成本、基于静态生成（Cloudflare Pages）与增量采集解析流水线的公益招聘信息导航项目。

## 快速校验命令
```bash
# 校验 data/*.json 与 config/sources.json 是否符合契约
python scripts/validate_data.py

# 运行单元测试
python -m unittest discover -s tests
```

## 工作流程摘要
1. 详见 `BLUEPRINT.md` 与 `AGENTS.md`。
2. 严禁向 `data/jobs.json` 写入虚构的 Mock 测试数据。
