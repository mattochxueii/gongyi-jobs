"""Standardized public recruitment SaaS & official JSON/HTML API adapters.

Supports zero-AI-token deterministic extraction into job.schema.json records for:
- Feishu Hire (*.jobs.feishu.cn public CSRF + /api/v1/search/job/posts)
- Beisen Zhiye (*.zhiye.com / custom Beisen portals BSGlobal + /api/Jobad/GetJobAdPageList)
- Hotjob Wecruit (wecruit.hotjob.cn/wecruit/positionInfo/listPosition*)
- Greenhouse Boards API (boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true)
- Lever Postings API (api.lever.co/v0/postings/{site}?mode=json)
- Moka Recruitment Portal (embedded <input id="init-data"> JSON)
- EMQ / Dify / Henan Corporate Official Portals (Hanwei, Gltech, Xyyl, Weihua, Lomon, Suntront)
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scrapers.core.base_adapter import BaseScraperAdapter, CrawlRunSummary
from scrapers.core.merger import merge_jobs_into_dataset
from scrapers.core.storage import TZ_CN, now_iso_cn

SOURCE_TO_COMPANY_ID: dict[str, str] = {
    "ent_songmics_feishu": "songmics-home",
    "ent_lilith_feishu": "lilith-games",
    "ent_moka_self": "moka-hr",
    "ent_trip_moka": "trip-com",
    "ent_canonical_greenhouse": "canonical-ubuntu",
    "ent_mongodb_greenhouse": "mongodb-inc",
    "ent_emq_jobs": "emq-technologies",
    "ent_dify_jobs": "dify-langgenius",
    "ent_mxbc_beisen": "mixue-bingcheng",
    "ent_zzpxx_beisen": "pingxingxian-edu",
    "ent_jiean_feishu": "jiean-tech",
    "ent_guoquan_moka": "guoquan-food",
    "ent_hanwei_jobs": "hanwei-tech",
    "ent_gltech_jobs": "gltech-cn",
    "ent_suntront_jobs": "suntront-tech",
    "ent_xyyl_jobs": "xiangyu-medical",
    "ent_weihua_jobs": "weihua-group",
    "ent_lomon_jobs": "lomonbillions",
    "ent_autobio_hotjob": "autobio-zhengzhou",
    "ent_popmart_beisen": "popmart-inc",
    "ent_iflytek_beisen": "iflytek-inc",
    "ent_rigol_feishu": "rigol-inc",
    "ent_sosen_beisen": "sosen-inc",
    "ent_juicedata_jobs": "juicedata-inc",
    "ent_keyence_beisen": "keyence-china",
    "ent_auxgroup_beisen": "auxgroup-inc",
}

_PLATFORM_LABELS: dict[str, str] = {
    "feishu_hire": "飞书招聘",
    "beisen": "北森招聘",
    "hotjob": "大易招聘官网",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "moka": "Moka招聘",
    "emq_jobs": "EMQ招聘官网",
    "dify_jobs": "Dify招聘官网",
    "juicefs_jobs": "官网直招",
    "hanwei_jobs": "官网直招",
    "gltech_jobs": "官网直招",
    "suntront_jobs": "官网直招",
    "xyyl_jobs": "官网直招",
    "weihua_jobs": "官网直招",
    "lomon_jobs": "官网直招",
}

_CITY_TO_PROVINCE: dict[str, str] = {
    "郑州市": "河南省",
    "洛阳市": "河南省",
    "许昌市": "河南省",
    "周口市": "河南省",
    "新乡市": "河南省",
    "安阳市": "河南省",
    "南阳市": "河南省",
    "开封市": "河南省",
    "平顶山市": "河南省",
    "焦作市": "河南省",
    "鹤壁市": "河南省",
    "濮阳市": "河南省",
    "漯河市": "河南省",
    "三门峡市": "河南省",
    "商丘市": "河南省",
    "信阳市": "河南省",
    "驻马店市": "河南省",
    "济源市": "河南省",
    "北京市": "北京市",
    "上海市": "上海市",
    "天津市": "天津市",
    "重庆市": "重庆市",
    "深圳市": "广东省",
    "广州市": "广东省",
    "中山市": "广东省",
    "珠海市": "广东省",
    "东莞市": "广东省",
    "佛山市": "广东省",
    "杭州市": "浙江省",
    "宁波市": "浙江省",
    "温州市": "浙江省",
    "嘉兴市": "浙江省",
    "苏州市": "江苏省",
    "南京市": "江苏省",
    "无锡市": "江苏省",
    "南通市": "江苏省",
    "常州市": "江苏省",
    "徐州市": "江苏省",
    "成都市": "四川省",
    "绵阳市": "四川省",
    "德阳市": "四川省",
    "攀枝花市": "四川省",
    "武汉市": "湖北省",
    "襄阳市": "湖北省",
    "宜昌市": "湖北省",
    "厦门市": "福建省",
    "福州市": "福建省",
    "泉州市": "福建省",
    "昆明市": "云南省",
    "楚雄州": "云南省",
    "西安市": "陕西省",
    "长沙市": "湖南省",
    "合肥市": "安徽省",
    "芜湖市": "安徽省",
    "南昌市": "江西省",
    "济南市": "山东省",
    "青岛市": "山东省",
    "大连市": "辽宁省",
    "沈阳市": "辽宁省",
    "朝阳市": "辽宁省",
    "石家庄市": "河北省",
    "太原市": "山西省",
    "呼和浩特市": "内蒙古",
    "长春市": "吉林省",
    "哈尔滨市": "黑龙江省",
    "南宁市": "广西",
    "海口市": "海南省",
    "贵阳市": "贵州省",
    "兰州市": "甘肃省",
    "金昌市": "甘肃省",
    "西宁市": "青海省",
    "银川市": "宁夏",
    "乌鲁木齐市": "新疆",
    "远程": "全国/远程",
}

_CITY_ALIAS_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("郑州市", ("郑州", "航空港", "zhengzhou")),
    ("洛阳市", ("洛阳", "luoyang")),
    ("许昌市", ("许昌", "xuchang")),
    ("周口市", ("周口", "zhoukou")),
    ("新乡市", ("新乡", "长垣", "xinxiang")),
    ("安阳市", ("安阳", "内黄", "anyang")),
    ("南阳市", ("南阳", "西峡", "nanyang")),
    ("开封市", ("开封", "kaifeng")),
    ("平顶山市", ("平顶山", "pingdingshan")),
    ("焦作市", ("焦作", "温县", "孟州", "沁阳", "jiaozuo")),
    ("鹤壁市", ("鹤壁", "hebi")),
    ("濮阳市", ("濮阳", "puyang")),
    ("漯河市", ("漯河", "luohe")),
    ("三门峡市", ("三门峡", "sanmenxia")),
    ("商丘市", ("商丘", "shangqiu")),
    ("信阳市", ("信阳", "xinyang")),
    ("驻马店市", ("驻马店", "zhumadian")),
    ("济源市", ("济源", "jiyuan")),
    ("北京市", ("北京", "beijing")),
    ("上海市", ("上海", "shanghai")),
    ("深圳市", ("深圳", "shenzhen")),
    ("广州市", ("广州", "guangzhou")),
    ("中山市", ("中山", "zhongshan")),
    ("珠海市", ("珠海", "zhuhai")),
    ("东莞市", ("东莞", "dongguan")),
    ("佛山市", ("佛山", "foshan")),
    ("杭州市", ("杭州", "hangzhou")),
    ("宁波市", ("宁波", "ningbo")),
    ("苏州市", ("苏州", "suzhou")),
    ("南京市", ("南京", "nanjing")),
    ("无锡市", ("无锡", "wuxi")),
    ("南通市", ("南通", "海门", "如东", "启东", "nantong")),
    ("常州市", ("常州", "changzhou")),
    ("成都市", ("成都", "chengdu")),
    ("攀枝花市", ("攀枝花", "panzhihua")),
    ("德阳市", ("德阳", "绵竹", "deyang")),
    ("武汉市", ("武汉", "wuhan")),
    ("襄阳市", ("襄阳", "南漳", "xiangyang")),
    ("厦门市", ("厦门", "xiamen")),
    ("福州市", ("福州", "fuzhou")),
    ("昆明市", ("昆明", "kunming")),
    ("楚雄州", ("楚雄", "禄丰", "chuxiong")),
    ("西安市", ("西安", "xian", "xi'an")),
    ("长沙市", ("长沙", "changsha")),
    ("重庆市", ("重庆", "chongqing")),
    ("天津市", ("天津", "tianjin")),
    ("合肥市", ("合肥", "hefei")),
    ("济南市", ("济南", "jinan")),
    ("青岛市", ("青岛", "qingdao")),
    ("石家庄市", ("石家庄", "shijiazhuang")),
    ("太原市", ("太原", "taiyuan")),
    ("沈阳市", ("沈阳", "shenyang")),
    ("朝阳市", ("朝阳市", "朝阳县", "朝阳东锆", "辽宁朝阳")),
    ("兰州市", ("兰州", "lanzhou")),
    ("金昌市", ("金昌", "jinchang")),
    ("南昌市", ("南昌", "nanchang")),
)

_HENAN_CITIES = {c for c, p in _CITY_TO_PROVINCE.items() if p == "河南省"}

_GB_CITY_PREFIX_MAP: dict[str, str] = {
    "11": "北京市",
    "12": "天津市",
    "31": "上海市",
    "50": "重庆市",
    "4101": "郑州市",
    "4102": "开封市",
    "4103": "洛阳市",
    "4104": "平顶山市",
    "4105": "安阳市",
    "4106": "鹤壁市",
    "4107": "新乡市",
    "4108": "焦作市",
    "4109": "濮阳市",
    "4110": "许昌市",
    "4111": "漯河市",
    "4112": "三门峡市",
    "4113": "南阳市",
    "4114": "商丘市",
    "4115": "信阳市",
    "4116": "周口市",
    "4117": "驻马店市",
    "4190": "济源市",
    "4401": "广州市",
    "4403": "深圳市",
    "4404": "珠海市",
    "4419": "东莞市",
    "3301": "杭州市",
    "3201": "南京市",
    "3205": "苏州市",
    "5101": "成都市",
    "4201": "武汉市",
    "3502": "厦门市",
    "5301": "昆明市",
    "6101": "西安市",
    "4301": "长沙市",
    "3401": "合肥市",
}

_NON_MAINLAND_EXCLUDE_RE = re.compile(
    r"\b(japan|japanese|tokyo|osaka|india|bangalore|bengaluru|korea|korean|seoul|"
    r"australia|sydney|melbourne|anz|new zealand|singapore|indonesia|jakarta|thailand|"
    r"bangkok|vietnam|malaysia|kuala lumpur|philippines|manila|united states|usa|"
    r"canada|toronto|emea|europe|uk|london|germany|berlin|munich|france|paris|"
    r"spain|brazil|latam)\b",
    re.I,
)
_CHINA_OR_GLOBAL_REMOTE_RE = re.compile(
    r"(china|beijing|shanghai|shenzhen|hangzhou|guangzhou|chengdu|zhengzhou|suzhou|henan|"
    r"mandarin|中国|北京|上海|深圳|杭州|广州|成都|郑州|苏州|河南|globally remote|remote globally|"
    r"home based - apac|远程)",
    re.I,
)
_SALARY_RE = re.compile(r"薪资(?:范围|待遇)?[：:\s]*([^\n\r。；;]{3,50})")
_WORK_HOURS_RE = re.compile(r"(?:工作时间|上班时间)[：:\s]*([^\n\r。；;]{4,60})")

_SALARY_KEEP_ANCHOR_RE = re.compile(
    r"^(月薪|年薪|综合收入|年收入|日薪|时薪|底薪|安家费|安家补贴|购房补贴|住房补贴|生活补助|实习补贴|实习津贴)$",
    re.I,
)
_SALARY_ANCHOR_RE = re.compile(
    r"(?P<anchor>综合薪资|综合薪酬|综合收入|期望薪资|薪资范围|薪资区间|薪资标准|薪资待遇|薪资水平|薪资体系|薪资结构|薪资"
    r"|薪酬范围|薪酬标准|薪酬待遇|薪酬|工资待遇|基本工资|月薪|年薪|年收入|底薪|日薪|时薪"
    r"|实习薪资|实习工资|实习补贴|实习津贴|实习待遇|安家费|安家补贴|购房补贴|住房补贴|生活补助"
    r"|Salary|Compensation|Pay(?:\s+range)?)"
    r"(?P<gap>[^\n\r。；;】\]]{0,26}?)"
    r"(?P<val>(?:保底|税前|约|可达|高达|最高)?\s*(?:[￥¥$]|RMB|CNY)?\s*\d+(?:\.\d+)?(?:\s*[kKwW万元块])?"
    r"(?:\s*[-~～至到/]\s*(?:保底|税前)?\s*(?:[￥¥$]|RMB|CNY)?\s*\d+(?:\.\d+)?\s*\+?)?"
    r"\s*(?:元|RMB|CNY|\$|￥|¥|k|K|w|W|万|千|块|薪)(?:[^\n\r。；;]{0,36}))",
    re.I,
)
_STANDALONE_SALARY_RE = re.compile(
    r"(?:(?:税前|约)?\s*(?:[￥¥$]|RMB|CNY)?\s*\d+(?:\.\d+)?(?:\s*[kK])?\s*[-~～至到]\s*\d+(?:\.\d+)?\s*[kK]\s*(?:/\s*[月年]|\s*[*×x]\s*\d+(?:\s*[-~～]\s*\d+)?薪)?(?![a-zA-Z0-9×x*]))"
    r"|(?:(?:税前|约)?\s*(?:[￥¥$]|RMB|CNY)?\s*\d+(?:\.\d+)?(?:\s*(?:万元?|W|w))?\s*[-~～至到]\s*\d+(?:\.\d+)?\s*(?:万元?|W|w)\s*(?:/\s*[年月]|元\s*/\s*[年月]|\s*[*×x]\s*\d+(?:\s*[-~～]\s*\d+)?薪))"
    r"|(?:(?<!\d)\d{4,6}(?:\s*元)?\s*[-~～至到]\s*\d{4,6}\s*元\s*/\s*[月年天日])"
    r"|(?:(?<!\d)\d{2,4}\s*元\s*/\s*(?:天|日|小时)(?:\s*(?:实习|兼职)?(?:补贴|津贴|薪资))?)"
    r"|(?:(?:时薪|日薪)\s*\d+(?:\.\d+)?(?:\s*[-~～]\s*\d+(?:\.\d+)?)?\s*元(?:\s*[每/一]?(?:小时|天|日))?)",
    re.I,
)
_DEGREE_SALARY_RE = re.compile(
    r"(?:本科|硕士|博士)[^\n\r。；;]{0,8}\d+(?:\.\d+)?(?:\s*[万wWkK元])?(?:\s*[-~～至到]\s*\d+(?:\.\d+)?)?\s*(?:万|W|w|k|K|元)"
    r"(?:\s*[·、,，/]\s*(?:本科|硕士|博士)[^\n\r。；;]{0,8}\d+(?:\.\d+)?(?:\s*[万wWkK元])?(?:\s*[-~～至到]\s*\d+(?:\.\d+)?)?\s*(?:万|W|w|k|K|元)){0,2}",
    re.I,
)
_SALARY_UNIT_RE = re.compile(r"(?:[kKwW万千元块￥¥$]|薪|RMB|CNY|USD|/月|/年|/天|/日|小时)", re.I)
_KPI_OR_SPEC_NOISE_RE = re.compile(
    r"(?:合同额|签约额|销售额|营业额|业绩指标|年度任务|完成任务|项目额|项目总额|项目规模|流水|GMV|融资|\d+\s*[kK]\s*[×xX*]\s*\d+\s*[kK]|\d+\s*kV|\d+\s*[kKmMgGtT][bB])",
    re.I,
)
_PERK_NOISE_RE = re.compile(
    r"(?:健身|语言学习|AI\s*体验|团建|旅游|免费体检|年度体检|购机|书费|咖啡|下午茶|活动经费|内推)",
    re.I,
)
_INTERN_ALLOWANCE_RE = re.compile(r"(?:实习|日薪|时薪|安家|购房|住房|生活补助|薪酬|月薪|年薪|工资|薪资)")
_NEGOTIABLE_ONLY_RE = re.compile(r"^(?:薪资|薪酬|年薪|月薪|工资|待遇)?[：:\s]*(?:面议|薪资面议|一人一议|具体面谈|negotiable|tbd)[！!。~]*$", re.I)
_COMPANY_POLICY_SALARY_MAP: dict[str, str] = {
    # Verified on Beisen campus resource https://portal-oss.zhiye.com/612085/resource/4dda6986-d78a-48e2-8c8c-3e3f57c2fbee.html
    "sosen-inc": "官网校招政策：综合年薪8-15万元/年（深圳房补1200元/月）",
    # Verified on Beisen campus resource https://portal-oss.zhiye.com/106200/resource/1487c8bc-9ba6-4abd-850d-c201d23e8d20.html
    "auxgroup-inc": "官网校招政策：13-14薪+额外激励，优秀者首年可破50万",
}

_JD_BENEFIT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("周末双休", re.compile(r"(周末双休|双休)")),
    ("弹性工作", re.compile(r"(弹性工作|弹性办公|弹性打卡|flexible working|remote-first)", re.I)),
    ("混合办公", re.compile(r"(混合办公|混合工作政策|hybrid working model|hybrid work|hybrid role)", re.I)),
    ("不打卡", re.compile(r"(拒绝无意义的打卡|不打卡)")),
    ("五险一金", re.compile(r"(六险一金|五险一金)")),
    ("补充医疗", re.compile(r"(补充医疗|补充商业保险|补充商业医疗|商业保险|雇主责任险|团体意外险|medical insurance|health and vision)", re.I)),
    ("年度体检", re.compile(r"(年度免费体检|年度体检|全面体检|免费体检|健康体检|定期体检)")),
    ("带薪年假", re.compile(r"(带薪年假|福利年假|年休假|全薪病假|全薪假期|annual holiday leave|generous parental leave|paid time off)", re.I)),
    ("餐饮补贴", re.compile(r"(免费三餐|免费就餐|营养餐厅|内部食堂|工作餐|餐补|餐饮补贴|用餐补助)")),
    ("住房支持", re.compile(r"(购房补贴|员工宿舍|酒店式公寓|智慧公寓|高端人才公寓|宾馆化宿舍|免费住宿)")),
    ("通勤班车", re.compile(r"(免费班车|通勤班车|班车接送|交通补助|交通补贴)")),
    ("节日礼金", re.compile(r"(节日福利|节假日福利|生日福利|高温补贴|婚育礼金)")),
    ("学习发展预算", re.compile(r"(learning and development budget|语言学习补贴|课程学习)", re.I)),
)


def strip_html_text(raw_html: str) -> str:
    """Convert HTML snippet to clean plain text."""
    if not raw_html:
        return ""
    unescaped = html_lib.unescape(str(raw_html))
    if "<" in unescaped and ">" in unescaped:
        soup = BeautifulSoup(unescaped, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
    else:
        text = unescaped
    lines = [re.sub(r"[ \t\u00a0\u3000]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def normalize_cn_city(
    *hints: Any,
    city_id: int | str | None = None,
    default_city: str = "多城市",
) -> tuple[str, str]:
    """Normalize location hints and optional GB city_id into (province, city)."""
    if city_id is not None:
        code_str = re.sub(r"\D", "", str(city_id))
        if len(code_str) >= 4 and code_str[:4] in _GB_CITY_PREFIX_MAP:
            std_city = _GB_CITY_PREFIX_MAP[code_str[:4]]
            return _CITY_TO_PROVINCE.get(std_city, "中国"), std_city
        if len(code_str) >= 2 and code_str[:2] in _GB_CITY_PREFIX_MAP:
            std_city = _GB_CITY_PREFIX_MAP[code_str[:2]]
            return _CITY_TO_PROVINCE.get(std_city, "中国"), std_city

    joined = " ".join(str(h) for h in hints if h).strip()
    lower = joined.lower()

    # Check explicit city aliases (when city_id is absent or unmapped)
    for std_city, aliases in _CITY_ALIAS_PATTERNS:
        if any(alias in lower for alias in aliases):
            return _CITY_TO_PROVINCE.get(std_city, "中国"), std_city

    if any(tok in lower for tok in ("远程", "remote", "home based", "distributed", "anywhere")):
        return "全国/远程", "远程"

    return _CITY_TO_PROVINCE.get(default_city, "中国"), default_city


def parse_date_value(value: Any) -> str | None:
    """Parse ISO-8601, dotted YYYY.MM.DD, or epoch seconds/milliseconds into YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e11:  # milliseconds
            ts /= 1000.0
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=TZ_CN).strftime("%Y-%m-%d")
    raw = str(value).strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{10,13}", raw):
        return parse_date_value(int(raw))
    m = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})", raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 1990 or not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _pick_preferred_city_hint(
    text: str,
    *,
    preferred_city: str | None = None,
    preferred_province: str | None = None,
) -> str:
    """Resolve best city hint when a multi-city string lists several cities."""
    if not text:
        return ""
    if preferred_city and preferred_city not in {"多城市", "全省"}:
        short_pref = preferred_city.rstrip("市")
        if short_pref and short_pref in text:
            return preferred_city
    if preferred_province == "河南省":
        for std_city, aliases in _CITY_ALIAS_PATTERNS:
            if std_city in _HENAN_CITIES and any(a in text.lower() for a in aliases):
                return std_city
    return text


def _prefer_and_dedup_jobs(
    records: list[dict[str, Any]],
    *,
    preferred_city: str | None = None,
    preferred_province: str | None = None,
    max_items: int = 12,
) -> list[dict[str, Any]]:
    """Deduplicate by id/source_url while prioritizing preferred city/province cleanly."""
    seen_ids: set[str] = set()
    unique: list[dict[str, Any]] = []
    for rec in records:
        key = str(rec.get("id") or rec.get("source_url") or "")
        if key and key not in seen_ids:
            seen_ids.add(key)
            unique.append(rec)

    def _tier(rec: dict[str, Any]) -> int:
        city = str(rec.get("city") or "")
        prov = str(rec.get("province") or "")
        if preferred_city and preferred_city not in {"多城市", "全省"} and city == preferred_city:
            return 3
        if preferred_province and prov == preferred_province:
            return 2
        if city == "远程":
            return 1
        return 0

    if preferred_city or preferred_province:
        # Python sort is guaranteed stable: items keep original recency within each tier.
        unique.sort(key=_tier, reverse=True)
    return unique[: max(0, max_items)]


def _clip(text: str, max_len: int) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max(0, max_len - 1)].rstrip() + "…"


def _clean_salary_candidate(raw: str) -> str | None:
    """Validate and concisely normalize a candidate salary expression; return None for non-numeric text."""
    s = re.sub(r"[ \t\u00a0\u3000]+", " ", (raw or "").strip())
    s = re.sub(r"^[：:\-\s]+", "", s)
    s = re.sub(r"[~。；;！!\s]+$", "", s)
    if not s or _NEGOTIABLE_ONLY_RE.match(s):
        return None
    # Reject strings without explicit digits or compensation units (blocks '架构 · 早期期权', '福利】', etc.)
    if not re.search(r"\d", s) or not _SALARY_UNIT_RE.search(s):
        return None
    if _KPI_OR_SPEC_NOISE_RE.search(s):
        return None
    if _PERK_NOISE_RE.search(s) and not _INTERN_ALLOWANCE_RE.search(s):
        return None

    # Trim non-numeric trailing clauses/notes such as '详情面谈（不定时工作制）' or '，以及早期员工丰厚期权'
    s = re.split(r"(?:详情面谈|详情面议|具体面议|具体面谈)", s, maxsplit=1)[0].strip()
    parts = [p.strip() for p in re.split(r"[，,]", s) if p.strip()]
    kept_parts: list[str] = []
    for idx, part in enumerate(parts):
        if idx == 0 or re.search(r"\d", part) or re.match(r"^(?:月度发放|多薪|含补贴|含奖金)", part):
            kept_parts.append(part)
        else:
            break
    s = "，".join(kept_parts)
    s = re.sub(r"(\d+(?:\.\d+)?(?:[-~～]\d+(?:\.\d+)?)?\s*万元?)之间$", r"\1", s)
    s = re.sub(r"(?:\s*[（(][^）)]*(?:具体|视个人|根据不同城市|不定时)[^）)]*[）)])+$", "", s)
    s = re.sub(r"[，,：:\s]+$", "", s).strip()
    # Clean stray unmatched closing brackets at tail
    if s.endswith(("）", ")")) and s.count("(") + s.count("（") < s.count(")") + s.count("）"):
        s = s[:-1].strip()
    # Ensure pure 'N年' work experience never slips past
    if re.search(r"\d+\s*[-~～]?\s*\d*\s*年以上?(?:工作)?经验", s) and not re.search(r"[kKwW万元￥¥$薪]", s):
        return None
    if not re.search(r"\d", s) or not _SALARY_UNIT_RE.search(s):
        return None
    return _clip(s, 80)


def extract_salary_range(
    plain_text: str,
    *,
    salary_hint: str | None = None,
    policy_salary: str | None = None,
) -> str | None:
    """Extract normalized salary_range from API hint, JD text, or official policy attribution."""
    if salary_hint:
        cleaned_hint = _clean_salary_candidate(salary_hint)
        if cleaned_hint:
            return cleaned_hint

    text = strip_html_text(plain_text) if plain_text else ""
    if text:
        lines = [ln.strip() for ln in re.split(r"[\n\r]+", text) if ln.strip()]

        # 1) Degree-tiered salary standards (e.g. '本科15万·硕士20万', '本科20-30万，硕士30-50万')
        for ln in lines:
            if _PERK_NOISE_RE.search(ln) and not _INTERN_ALLOWANCE_RE.search(ln):
                continue
            m_deg = _DEGREE_SALARY_RE.search(ln)
            if m_deg and ("硕士" in m_deg.group(0) or "博士" in m_deg.group(0) or re.search(r"年薪|薪酬|月薪|本科\d", ln)):
                cleaned = _clean_salary_candidate(m_deg.group(0))
                if cleaned:
                    return cleaned

        # 2) Explicit anchored salary clauses (keeps meaningful period anchors such as 年薪/月薪/日薪/时薪/安家费)
        for ln in lines:
            if _PERK_NOISE_RE.search(ln) and not _INTERN_ALLOWANCE_RE.search(ln):
                continue
            if _KPI_OR_SPEC_NOISE_RE.search(ln) and not re.search(r"(?:月薪|年薪|薪资|薪酬|底薪|日薪|时薪)", ln):
                continue
            for m in _SALARY_ANCHOR_RE.finditer(ln):
                anchor = m.group("anchor").strip()
                gap = m.group("gap").strip()
                val = m.group("val").strip()
                # Tightly re-anchor if gap itself contains a tighter salary keyword (e.g. '薪资体系：基本工资...年薪保底40W')
                sub_matches = list(
                    re.finditer(
                        r"(月薪|年薪|综合收入|年收入|日薪|时薪|底薪|基本工资|实习补贴|实习津贴|实习薪资|安家费|安家补贴|购房补贴|住房补贴)",
                        gap,
                    )
                )
                if sub_matches:
                    last_sub = sub_matches[-1]
                    anchor = last_sub.group(1)
                    gap = gap[last_sub.end() :].strip(" ：:\t")
                # Prefer direct anchor+val when gap is descriptive prose like '基本工资+奖金激励，' or '大部分销售员的...在'
                val_clean = re.sub(r"^(?:在|为|达|到)\s*", "", val)
                if _SALARY_KEEP_ANCHOR_RE.match(anchor) and not val_clean.lower().startswith(anchor.lower()):
                    if gap in {"", ":", "：", "： ", "范围", "待遇", "约为", "约"} or re.match(r"^(?:在|为|是)$", gap):
                        candidate = f"{anchor}：{val_clean}" if "：" not in anchor and not val_clean.startswith(("：", ":")) else f"{anchor}{val_clean}"
                    elif len(gap) <= 6 and not any(ch in gap for ch in "+，,"):
                        candidate = f"{anchor}{gap}{val_clean}"
                    else:
                        candidate = f"{anchor}{val_clean}"
                else:
                    candidate = val_clean
                cleaned = _clean_salary_candidate(candidate)
                if cleaned:
                    return cleaned

        # 3) Unanchored unambiguous numeric salary expressions (e.g. '30K~40K/月', '税前5k-7k', '8-15k', '160元/天 实习津贴')
        for ln in lines:
            if _PERK_NOISE_RE.search(ln) and not _INTERN_ALLOWANCE_RE.search(ln):
                continue
            if _KPI_OR_SPEC_NOISE_RE.search(ln):
                continue
            m_std = _STANDALONE_SALARY_RE.search(ln)
            if m_std:
                cleaned = _clean_salary_candidate(m_std.group(0))
                if cleaned:
                    return cleaned

    if policy_salary and not re.search(r"(?:博士|该岗位|本岗位)[^\n]{0,12}(?:薪资面议|一人一议)", text):
        return _clip(policy_salary, 80)
    return None


def _format_num_clean(val: Any) -> str | None:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    if num.is_integer():
        return str(int(num))
    return f"{num:.1f}".rstrip("0").rstrip(".")


def _format_feishu_salary(job_post_info: dict[str, Any] | None) -> str | None:
    """Format Feishu Hire job_post_info.min_salary / max_salary (Monthly salary range)."""
    if not isinstance(job_post_info, dict):
        return None
    cur = job_post_info.get("currency")
    if cur not in (None, 0, 1, "1", "CNY", "RMB"):
        return None
    lo = _format_num_clean(job_post_info.get("min_salary"))
    hi = _format_num_clean(job_post_info.get("max_salary"))
    if not lo and not hi:
        return None
    if lo and hi:
        if float(lo) >= 1000 or float(hi) >= 1000:
            return f"{lo}元/月" if lo == hi else f"{lo}-{hi}元/月"
        return f"{lo}K/月" if lo == hi else f"{lo}K-{hi}K/月"
    if lo:
        return f"{lo}+元/月" if float(lo) >= 1000 else f"{lo}K+/月"
    assert hi is not None
    return f"最高{hi}元/月" if float(hi) >= 1000 else f"最高{hi}K/月"


def _format_moka_salary(item: dict[str, Any]) -> str | None:
    """Format structured Moka init-data salary fields if populated."""
    raw_sal = str(item.get("salary") or "").strip()
    if raw_sal:
        cleaned = _clean_salary_candidate(raw_sal)
        if cleaned:
            return cleaned
    lo = _format_num_clean(item.get("minSalary"))
    hi = _format_num_clean(item.get("maxSalary"))
    if not lo and not hi:
        return None
    pay_period = str(item.get("payPeriod") or "").lower()
    suffix = "/年" if "year" in pay_period or "年" in pay_period else "/月"
    unit = str(item.get("salaryUnit") or "").strip()
    if lo and hi:
        if float(lo) >= 1000 or float(hi) >= 1000:
            return f"{lo}-{hi}{unit or '元'}{suffix}"
        if "w" in unit.lower() or "万" in unit:
            return f"{lo}-{hi}万{suffix}"
        return f"{lo}K-{hi}K{suffix}"
    bound = f"{lo}+" if lo else f"最高{hi}"
    num_v = float(lo or hi or 0)
    if num_v >= 1000:
        return f"{bound}{unit or '元'}{suffix}"
    return f"{bound}K{suffix}"


def _extract_jd_signals(
    plain_text: str,
    source_url: str,
    company_profile: dict[str, Any],
    *,
    salary_hint: str | None = None,
    policy_salary: str | None = None,
) -> tuple[str, str | None, str | None, list[dict[str, Any]]]:
    """Extract verified work_schedule, work_hours, salary_range, and benefits_verified from JD + company claims."""
    lines = [ln.strip() for ln in re.split(r"[\n\r]+", plain_text) if ln.strip()]
    benefits: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for line in lines:
        if len(line) < 4:
            continue
        for tag, pat in _JD_BENEFIT_RULES:
            if pat.search(line):
                quote = _clip(line, 280)
                key = (tag, quote)
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    benefits.append(
                        {
                            "tag": tag[:40],
                            "evidence_quote": quote,
                            "evidence_url": source_url,
                        }
                    )
                if len(benefits) >= 4:
                    break
        if len(benefits) >= 4:
            break

    # Supplement with verified company-level benefit claims (always grounded in companies.json)
    for claim in company_profile.get("benefit_claims") or []:
        if len(benefits) >= 5:
            break
        tag = str(claim.get("label") or "").strip()[:40]
        quote = str(claim.get("evidence_quote") or "").strip()
        ev_url = str(claim.get("evidence_url") or "").strip()
        if not tag or len(quote) < 2:
            continue
        key = (tag, quote)
        if key not in seen_pairs and not any(b["tag"] == tag for b in benefits):
            seen_pairs.add(key)
            benefits.append(
                {
                    "tag": tag,
                    "evidence_quote": _clip(quote, 295),
                    "evidence_url": ev_url or source_url,
                }
            )

    work_hours: str | None = None
    m_hours = _WORK_HOURS_RE.search(plain_text)
    if m_hours:
        work_hours = _clip(m_hours.group(1), 100)

    salary_range = extract_salary_range(
        plain_text,
        salary_hint=salary_hint,
        policy_salary=policy_salary,
    )

    comp_sched = str(company_profile.get("work_schedule") or "其他")
    if "周末双休" in plain_text or "双休" in plain_text:
        job_schedule = "周末双休"
    elif "不定时工作制" in plain_text and comp_sched == "周末双休":
        # Role explicitly states irregular working hours in its own JD despite company general claim
        job_schedule = "未明确"
    elif comp_sched in {"周末双休", "弹性工作", "做四休三"}:
        job_schedule = comp_sched
    elif any(k in plain_text.lower() for k in ("弹性工作", "弹性办公", "不打卡", "remote-first", "flexible working")):
        job_schedule = "弹性工作"
    else:
        job_schedule = "未明确"

    return job_schedule, work_hours, salary_range, benefits


def _infer_remote_policy(location_text: str, body_text: str) -> str:
    loc_l = (location_text or "").lower()
    body_l = (body_text or "").lower()
    has_loc_remote = any(k in loc_l for k in ("home based", "remote", "远程", "distributed"))
    has_loc_office = any(k in loc_l for k in ("office based", "office-based", "onsite", "线下"))

    if has_loc_remote and has_loc_office:
        return "混合办公"
    if has_loc_remote:
        return "可远程"
    if has_loc_office or any(k in body_l[:500] for k in ("office-based role", "office based", "线下实习", "全职到岗", "全日制到岗")):
        return "线下办公"
    if loc_l.strip() == "hybrid" or any(
        k in loc_l + " " + body_l[:1500]
        for k in ("混合办公", "混合工作", "hybrid working", "hybrid model", "hybrid role", "申请远程")
    ):
        return "混合办公"
    if any(k in loc_l + " " + body_l[:1200] for k in ("远程办公", "remote-first", "globally remote", "home based - apac")):
        return "可远程"
    return "未明确"


def _make_job_id(company_id: str, raw_id: str) -> str:
    safe_raw = re.sub(r"[^a-zA-Z0-9_-]", "-", str(raw_id).strip()).strip("-")
    candidate = f"ent-{company_id}-{safe_raw}".lower()
    if len(candidate) <= 120 and len(candidate) >= 4:
        return candidate
    digest = hashlib.sha256(f"{company_id}:{raw_id}".encode("utf-8")).hexdigest()[:16]
    return f"ent-{company_id[:40]}-{digest}"


def _is_china_eligible_global_role(title: str, location: str, body: str) -> bool:
    """Filter global ATS boards (Greenhouse/Lever) for roles open to Mainland China candidates."""
    title_loc = f"{title} | {location}"
    if re.search(r"(china|beijing|shanghai|shenzhen|hangzhou|guangzhou|chengdu|zhengzhou|suzhou|mandarin|中国|北京|上海|深圳|杭州|郑州)", title_loc, re.I):
        # Skip Taiwan-only when Beijing/China/Remote is absent
        if "taipei" in title_loc.lower() and not re.search(r"(beijing|shanghai|shenzhen|china|apac|remote)", title_loc, re.I):
            return False
        return True

    # Remote / APAC roles: exclude country-locked non-China postings
    if "home based - apac" in location.lower() or "globally remote" in body[:800].lower() or "remote globally" in body[:800].lower():
        if _NON_MAINLAND_EXCLUDE_RE.search(title_loc) or _NON_MAINLAND_EXCLUDE_RE.search(body[:600]):
            return False
        if "home based - apac" in location.lower() or "globally remote" in body[:800].lower():
            return True

    return False


class SaasBaseAdapter(BaseScraperAdapter):
    """Base adapter for standardized SaaS recruitment platforms (zero AI tokens)."""

    adapter_name: str = "saas_base"
    source_type: str = "verified_saas"

    def _resolve_company_profile(self) -> dict[str, Any]:
        """Look up verified company metadata from data/companies.json or source_config."""
        explicit = self.source_config.get("_company_profile")
        if isinstance(explicit, dict):
            return explicit

        companies_path = self.state_store.root_dir / "data" / "companies.json"
        target_cid = str(
            self.source_config.get("company_id")
            or SOURCE_TO_COMPANY_ID.get(self.source_id, "")
        ).strip()

        if companies_path.exists():
            try:
                data = json.loads(companies_path.read_text(encoding="utf-8"))
                items = data.get("items") or []
                for comp in items:
                    if not isinstance(comp, dict):
                        continue
                    if target_cid and comp.get("company_id") == target_cid:
                        return comp
                # Fallback domain matching
                base_host = urlparse(self.base_url).netloc.lower()
                for comp in items:
                    if not isinstance(comp, dict):
                        continue
                    c_host = urlparse(str(comp.get("career_url") or "")).netloc.lower()
                    if base_host and c_host and base_host == c_host and base_host not in {"app.mokahr.com", "boards-api.greenhouse.io", "api.lever.co"}:
                        return comp
            except (OSError, json.JSONDecodeError):
                pass

        return {
            "company_id": target_cid or re.sub(r"[^a-z0-9-]", "-", self.source_id.lower()).strip("-"),
            "name": self.source_name.split("（", 1)[0].strip() or self.source_name,
            "cities": [str(self.source_config.get("city") or "多城市")],
            "work_schedule": "其他",
            "benefit_claims": [],
        }

    def _build_job_record(
        self,
        *,
        raw_id: str,
        title: str,
        source_url: str,
        apply_url: str | None,
        location_text: str,
        body_text: str,
        published_at: str | None = None,
        department: str | None = None,
        city_id: int | str | None = None,
        education_req: str | None = None,
        experience_req: str | None = None,
        extra_tags: list[str] | None = None,
        company_profile: dict[str, Any] | None = None,
        salary_hint: str | None = None,
        policy_salary: str | None = None,
    ) -> dict[str, Any]:
        company = company_profile or self._resolve_company_profile()
        company_id = str(company["company_id"])
        org_name = str(company["name"])
        default_city = str((company.get("cities") or ["多城市"])[0])

        province, city = normalize_cn_city(location_text, title, city_id=city_id, default_city=default_city)
        job_schedule, work_hours, salary_range, benefits_verified = _extract_jd_signals(
            body_text,
            source_url,
            company,
            salary_hint=salary_hint,
            policy_salary=policy_salary,
        )
        remote_policy = _infer_remote_policy(location_text, body_text)

        summary_src = strip_html_text(body_text) or f"{org_name} 官网公开在招岗位（{city}）。"
        summary = _clip(summary_src, 420)
        if not summary:
            summary = f"{org_name} 官网公开在招岗位（{city}）。"

        platform_label = _PLATFORM_LABELS.get(self.adapter_name, "官网直招")
        source_name = _clip(f"{org_name}({platform_label})", 120)

        tag_candidates = ["高福利民企", city, job_schedule]
        if remote_policy in {"可远程", "混合办公"}:
            tag_candidates.append(remote_policy)
        for b in benefits_verified[:3]:
            tag_candidates.append(str(b["tag"]))
        if extra_tags:
            tag_candidates.extend(extra_tags)

        tags: list[str] = []
        for t in tag_candidates:
            cleaned_t = t.strip()[:40]
            if cleaned_t and cleaned_t not in tags and cleaned_t != "未明确":
                tags.append(cleaned_t)

        ts = now_iso_cn()
        digest_payload = json.dumps(
            {
                "id": raw_id,
                "title": title,
                "city": city,
                "url": source_url,
                "dept": department,
                "body": summary[:200],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        content_hash = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()

        return {
            "id": _make_job_id(company_id, raw_id),
            "category": "enterprise",
            "title": _clip(title, 200),
            "org_name": _clip(org_name, 150),
            "province": _clip(province, 40),
            "city": _clip(city, 60),
            "source_url": source_url,
            "apply_url": apply_url,
            "source_name": source_name,
            "source_type": self.source_type,
            "published_at": published_at,
            "deadline_at": None,
            "status": "active",
            "scraped_at": ts,
            "last_seen_at": ts,
            "content_hash": content_hash,
            "summary": summary,
            "tags": tags[:10],
            "enterprise_meta": {
                "company_id": company_id,
                "work_schedule": job_schedule,
                "work_hours": work_hours,
                "salary_range": salary_range,
                "remote_policy": remote_policy,
                "department": _clip(department, 100) if department else None,
                "experience_req": _clip(experience_req, 80) if experience_req else None,
                "education_req": _clip(education_req, 60) if education_req else None,
                "benefits_verified": benefits_verified,
            },
        }

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        """Fetch and transform active enterprise jobs from SaaS endpoints."""
        raise NotImplementedError

    def run(
        self,
        *,
        dry_run: bool = False,
        max_items: int = 12,
        fetch_attachments: bool = False,
        probe_base_when_empty: bool = True,
    ) -> CrawlRunSummary:
        """Fetch standardized SaaS JSON jobs and merge directly into data/jobs.json (zero AI tokens)."""
        del fetch_attachments, probe_base_when_empty
        summary = CrawlRunSummary(
            source_id=self.source_id,
            source_name=self.source_name,
            dry_run=dry_run,
        )
        try:
            jobs = self.fetch_jobs(max_items=max_items)
            summary.list_urls_scanned = max(1, len(self.list_urls))
            summary.announcements_fetched = len(jobs)
        except Exception as exc:
            summary.errors.append(f"SaaS fetch failed ({self.source_id}): {exc}")
            return summary

        if dry_run:
            for job in jobs:
                st = self.state_store.check_status(job["source_url"], job["content_hash"])
                if st == "new":
                    summary.new_count += 1
                elif st == "changed":
                    summary.changed_count += 1
                else:
                    summary.unchanged_count += 1
            return summary

        if jobs:
            merge_res = merge_jobs_into_dataset(
                jobs,
                root_dir=self.state_store.root_dir,
                archive_expired=False,
                dry_run=False,
            )
            summary.new_count = merge_res.inserted
            summary.changed_count = merge_res.updated
            summary.unchanged_count = merge_res.unchanged

            ts = now_iso_cn()
            for job in jobs:
                self.state_store.records[job["source_url"]] = {
                    "source_id": self.source_id,
                    "url": job["source_url"],
                    "title": job["title"],
                    "published_at": job.get("published_at"),
                    "content_hash": job["content_hash"],
                    "first_seen_at": self.state_store.records.get(job["source_url"], {}).get("first_seen_at", ts),
                    "last_seen_at": ts,
                    "inbox_file": "data/jobs.json",
                }
            self.state_store.save_state()

        return summary


class FeishuHireAdapter(SaasBaseAdapter):
    """Adapter for Feishu Hire (*.jobs.feishu.cn) public CSRF + search/job/posts JSON API."""

    adapter_name = "feishu_hire"

    @staticmethod
    def extract_website_path(portal_html: str, portal_url: str) -> str:
        m = re.search(r'<script[^>]*id="js-websiteInfo"[^>]*>(.*?)</script>', portal_html or "", re.S)
        if m:
            try:
                info = json.loads(m.group(1))
                wpath = (info.get("website_info") or {}).get("path")
                if isinstance(wpath, str) and wpath.strip():
                    return wpath.strip()
            except json.JSONDecodeError:
                pass
        path_seg = urlparse(portal_url).path.strip("/").split("/")[0]
        return path_seg or "index"

    @staticmethod
    def _extract_portal_salary_policies(portal_html: str) -> tuple[dict[str, str], set[str]]:
        """Extract degree-tiered campus policies and explicit negotiable-role exclusions from Feishu portal HTML."""
        if not portal_html:
            return {}, set()
        unescaped = portal_html.replace('\\"', '"').replace("\\n", "\n")
        policies: dict[str, str] = {}
        negotiable_roles: set[str] = set()

        m_rd = re.search(r"研发岗位[：:]\s*([^\n\"<]{6,60})", unescaped)
        if m_rd and re.search(r"\d", m_rd.group(1)):
            tier = re.sub(r"[，,]\s*博士.*$", "", m_rd.group(1)).strip(" ，,;；")
            if tier:
                policies["rd"] = _clip(f"官网校招政策：研发岗{tier}", 80)

        m_mkt = re.search(r"营销岗位[：:]\s*([^\n\"<]{4,40})", unescaped)
        if m_mkt and re.search(r"\d", m_mkt.group(1)):
            policies["mkt"] = _clip(f"官网校招政策：营销岗{m_mkt.group(1).strip()}", 80)

        for m_neg in re.finditer(r"⭐\s*([^\n:：\"<]{2,30})[：:]\s*[^\n\"<]{0,20}(?:薪资面议|一人一议)", unescaped):
            negotiable_roles.add(m_neg.group(1).strip())

        return policies, negotiable_roles

    @staticmethod
    def _pick_portal_policy(
        title: str,
        policies: dict[str, str],
        negotiable_roles: set[str],
    ) -> str | None:
        if not policies or "实习" in title:
            return None
        if any(role in title for role in negotiable_roles):
            return None
        if any(k in title for k in ("营销", "销售", "市场", "商务")) and "mkt" in policies:
            return policies["mkt"]
        return policies.get("rd")

    def parse_posts_payload(
        self,
        posts: list[dict[str, Any]],
        *,
        portal_origin: str,
        website_path: str,
        preferred_city: str | None = None,
        max_items: int = 12,
        portal_policies: dict[str, str] | None = None,
        negotiable_roles: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        candidates: list[tuple[int, dict[str, Any]]] = []
        policies_map = portal_policies or {}
        neg_set = negotiable_roles or set()

        for item in posts:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not jid or not title:
                continue

            city_names = [
                str(c.get("name")).strip()
                for c in (item.get("city_list") or [])
                if isinstance(c, dict) and c.get("name")
            ]
            city_info_name = str((item.get("city_info") or {}).get("name") or "").strip()
            if city_info_name and city_info_name not in city_names:
                city_names.insert(0, city_info_name)

            loc_text = ", ".join(city_names)
            desc = str(item.get("description") or "").strip()
            req = str(item.get("requirement") or "").strip()
            full_body = "\n".join(part for part in (desc, req) if part)
            api_salary = _format_feishu_salary(item.get("job_post_info"))
            policy_salary = self._pick_portal_policy(title, policies_map, neg_set)

            detail_url = f"{portal_origin.rstrip('/')}/{website_path}/position/{jid}/detail"
            dept = (item.get("department_info") or {}).get("name") or (item.get("job_category") or {}).get("name")
            pub_date = parse_date_value(item.get("publish_time"))

            pref_rank = 0
            if preferred_city and preferred_city not in {"多城市", "全省"}:
                short_pref = preferred_city.rstrip("市")
                if any(short_pref in cn for cn in city_names):
                    pref_rank = 2
                elif any(x in loc_text for x in ("远程", "全国")):
                    pref_rank = 1

            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text=loc_text,
                body_text=full_body,
                published_at=pub_date,
                department=str(dept).strip() if dept else None,
                company_profile=company,
                salary_hint=api_salary,
                policy_salary=policy_salary,
            )
            candidates.append((pref_rank, rec))

        # Keep preferred city first while preserving recency order within tier
        candidates.sort(key=lambda pair: -pair[0])
        return [rec for _, rec in candidates[: max(0, max_items)]]

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = self.list_urls or [self.base_url]
        preferred_city = str(self.source_config.get("city") or "").strip() or None
        collected_posts: list[dict[str, Any]] = []
        portal_origin = self.base_url.rstrip("/")
        wpath = "index"
        portal_policies: dict[str, str] = {}
        negotiable_roles: set[str] = set()

        for portal_url in target_urls:
            parsed = urlparse(portal_url)
            portal_origin = f"{parsed.scheme}://{parsed.netloc}"
            page_resp = self.http_client.get(portal_url)
            wpath = self.extract_website_path(page_resp.text, page_resp.url)
            p_pol, p_neg = self._extract_portal_salary_policies(page_resp.text)
            portal_policies.update(p_pol)
            negotiable_roles.update(p_neg)

            csrf_resp = self.http_client.request(
                "POST",
                f"{portal_origin}/api/v1/csrf/token",
                headers={"Referer": page_resp.url, "Origin": portal_origin},
            )
            csrf_token = ((json.loads(csrf_resp.text).get("data") or {}).get("token") or "").strip()

            api_headers: dict[str, str] = {
                "Referer": page_resp.url,
                "Origin": portal_origin,
                "Content-Type": "application/json",
                "Website-Path": wpath,
            }
            if csrf_token:
                api_headers["x-csrf-token"] = csrf_token

            # Scan up to 80 latest posts so city filtering (e.g. Zhengzhou) has plenty of candidates
            for offset in range(0, 80, 20):
                post_resp = self.http_client.request(
                    "POST",
                    f"{portal_origin}/api/v1/search/job/posts",
                    headers=api_headers,
                    json_body={
                        "limit": 20,
                        "offset": offset,
                        "keyword": "",
                        "job_category_id_list": [],
                        "location_code_list": [],
                        "portal_type": 2,
                        "portal_entrance": 1,
                    },
                )
                payload = json.loads(post_resp.text)
                if payload.get("code") != 0:
                    raise RuntimeError(f"Feishu Hire API error: {payload.get('message')}")
                batch = (payload.get("data") or {}).get("job_post_list") or []
                if not batch:
                    break
                collected_posts.extend(batch)
                if len(batch) < 20:
                    break

        return self.parse_posts_payload(
            collected_posts,
            portal_origin=portal_origin,
            website_path=wpath,
            preferred_city=preferred_city,
            max_items=max_items,
            portal_policies=portal_policies,
            negotiable_roles=negotiable_roles,
        )


class GreenhouseAdapter(SaasBaseAdapter):
    """Adapter for Greenhouse Boards public JSON API (/v1/boards/{token}/jobs?content=true)."""

    adapter_name = "greenhouse"

    @staticmethod
    def _normalize_api_url(url: str) -> str:
        if "boards-api.greenhouse.io" in url:
            return url
        parsed = urlparse(url)
        slug = parsed.path.strip("/").split("/")[0]
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    def parse_jobs_payload(self, payload: dict[str, Any], *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        raw_jobs = payload.get("jobs") or []
        scored: list[tuple[int, dict[str, Any]]] = []

        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            abs_url = str(item.get("absolute_url") or "").strip()
            if not jid or not title or not abs_url:
                continue

            loc_name = str((item.get("location") or {}).get("name") or "").strip()
            plain_body = strip_html_text(str(item.get("content") or ""))
            if not _is_china_eligible_global_role(title, loc_name, plain_body):
                continue

            depts = [str(d.get("name")).strip() for d in (item.get("departments") or []) if isinstance(d, dict) and d.get("name")]
            dept = depts[0] if depts else None
            pub_date = parse_date_value(item.get("updated_at") or item.get("first_published"))

            # Prefer explicit China city / Mandarin roles over broad APAC-remote
            priority = 2 if re.search(r"(china|beijing|shanghai|shenzhen|hangzhou|zhengzhou|mandarin)", f"{title} {loc_name}", re.I) else 1
            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=abs_url,
                apply_url=abs_url,
                location_text=loc_name,
                body_text=plain_body,
                published_at=pub_date,
                department=dept,
                company_profile=company,
            )
            scored.append((priority, rec))

        scored.sort(key=lambda pair: (pair[0], pair[1].get("published_at") or ""), reverse=True)
        return [rec for _, rec in scored[: max(0, max_items)]]

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = [self._normalize_api_url(u) for u in (self.list_urls or [self.base_url])]
        merged_raw: list[dict[str, Any]] = []
        for api_url in target_urls:
            resp = self.http_client.get(api_url)
            payload = json.loads(resp.text)
            merged_raw.extend(payload.get("jobs") or [])
        return self.parse_jobs_payload({"jobs": merged_raw}, max_items=max_items)


class LeverAdapter(SaasBaseAdapter):
    """Adapter for Lever Postings public JSON API (api.lever.co/v0/postings/{site}?mode=json)."""

    adapter_name = "lever"

    def parse_postings_payload(self, postings: list[dict[str, Any]], *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        results: list[dict[str, Any]] = []

        for item in postings:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("text") or "").strip()
            hosted_url = str(item.get("hostedUrl") or "").strip()
            if not jid or not title or not hosted_url:
                continue

            cats = item.get("categories") or {}
            loc_name = str(cats.get("location") or "").strip()
            workplace = str(item.get("workplaceType") or "").strip()
            loc_combined = f"{loc_name} {workplace}".strip()

            list_texts = []
            for lst in item.get("lists") or []:
                if isinstance(lst, dict):
                    hdr = str(lst.get("text") or "").strip()
                    cnt = strip_html_text(str(lst.get("content") or ""))
                    if hdr or cnt:
                        list_texts.append(f"{hdr}\n{cnt}".strip())

            plain_body = "\n".join(
                part
                for part in (
                    str(item.get("descriptionPlain") or "").strip(),
                    "\n".join(list_texts),
                    str(item.get("additionalPlain") or "").strip(),
                )
                if part
            )

            if not _is_china_eligible_global_role(title, loc_combined, plain_body):
                continue

            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=hosted_url,
                apply_url=str(item.get("applyUrl") or hosted_url),
                location_text=loc_combined,
                body_text=plain_body,
                published_at=parse_date_value(item.get("createdAt")),
                department=str(cats.get("department") or cats.get("team") or "").strip() or None,
                company_profile=company,
            )
            results.append(rec)
            if len(results) >= max_items:
                break

        return results

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = self.list_urls or [self.base_url]
        all_postings: list[dict[str, Any]] = []
        for url in target_urls:
            resp = self.http_client.get(url)
            data = json.loads(resp.text)
            if isinstance(data, list):
                all_postings.extend(data)
        return self.parse_postings_payload(all_postings, max_items=max_items)


class MokaAdapter(SaasBaseAdapter):
    """Adapter for Moka public recruitment portals via embedded init-data JSON."""

    adapter_name = "moka"

    @staticmethod
    def extract_init_data(html_text: str) -> dict[str, Any]:
        m = re.search(r'<input[^>]*id="init-data"[^>]*value="([^"]*)"', html_text or "")
        if not m:
            raise ValueError("Moka init-data hidden input not found in portal HTML")
        return json.loads(html_lib.unescape(m.group(1)))

    def parse_portal_html(self, html_text: str, portal_url: str, *, max_items: int = 12) -> list[dict[str, Any]]:
        data = self.extract_init_data(html_text)
        company = self._resolve_company_profile()
        raw_jobs = data.get("jobs") or []
        base_portal = portal_url.split("#", 1)[0].rstrip("/")

        results: list[dict[str, Any]] = []
        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").lower()
            if status and status != "open":
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not jid or not title:
                continue

            loc_obj = item.get("location") or {}
            if not loc_obj and isinstance(item.get("locations"), list) and item["locations"]:
                loc_obj = item["locations"][0] or {}
            address = str(loc_obj.get("address") or "").strip()
            city_id = loc_obj.get("cityId")
            country = str(loc_obj.get("country") or "").strip()
            if country and country not in {"中国", "China"}:
                continue

            dept_obj = item.get("department") or {}
            dept = str(dept_obj.get("name") or "").strip() or None
            zhineng = str((item.get("zhineng") or {}).get("name") or "").strip() or None
            pub_date = parse_date_value(item.get("publishedAt") or item.get("openedAt") or item.get("updatedAt"))

            detail_url = f"{base_portal}#/job/{jid}"
            body_parts = [f"官方在招职位：{title}"]
            if dept:
                body_parts.append(f"所属部门：{dept}")
            elif zhineng and zhineng != "其它":
                body_parts.append(f"职能类别：{zhineng}")
            if address:
                body_parts.append(f"办公地点：{address}")
            if item.get("commitment"):
                body_parts.append(f"职位性质：{item['commitment']}")
            if item.get("description"):
                desc_txt = strip_html_text(str(item["description"]))
                if desc_txt:
                    body_parts.append(desc_txt)

            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text=address,
                body_text="\n".join(body_parts),
                published_at=pub_date,
                department=dept or (zhineng if zhineng and zhineng != "其它" else None),
                city_id=city_id,
                company_profile=company,
                salary_hint=_format_moka_salary(item),
            )
            results.append(rec)
            if len(results) >= max_items:
                break

        return results

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = self.list_urls or [self.base_url]
        all_jobs: list[dict[str, Any]] = []
        fetch_cap = max(max_items * 3, 60)
        for url in target_urls:
            resp = self.http_client.get(url)
            all_jobs.extend(self.parse_portal_html(resp.text, resp.url, max_items=fetch_cap))
        return _prefer_and_dedup_jobs(
            all_jobs,
            preferred_city=str(self.source_config.get("city") or "").strip() or None,
            preferred_province=str(self.source_config.get("province") or "").strip() or None,
            max_items=max_items,
        )


class EmqJobsAdapter(SaasBaseAdapter):
    """Adapter for EMQ careers public JSON API (community-sites.emqx.com/api/v1/jobs)."""

    adapter_name = "emq_jobs"
    source_type = "company_official"

    def parse_api_payload(self, payload: dict[str, Any], *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        items = (payload.get("data") or {}).get("items") or []
        results: list[dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("enable") not in (1, True, None):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("titleCN") or item.get("titleEN") or "").strip()
            if not jid or not title:
                continue

            work_place = str(item.get("workPlaceCN") or item.get("workPlaceEN") or "").strip()
            body = strip_html_text(str(item.get("contentCN") or item.get("contentEN") or ""))
            detail_url = f"{self.base_url.rstrip('/')}/zh/job?id={jid}"
            pub_date = parse_date_value(item.get("updateAt") or item.get("createAt"))

            extra_tags = [p.strip() for p in re.split(r"[,，/、]", work_place) if p.strip()][:3]
            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text=work_place,
                body_text=body,
                published_at=pub_date,
                extra_tags=extra_tags,
                company_profile=company,
            )
            results.append(rec)
            if len(results) >= max_items:
                break

        return results

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = self.list_urls or [self.base_url]
        all_items: list[dict[str, Any]] = []
        for url in target_urls:
            resp = self.http_client.get(url)
            payload = json.loads(resp.text)
            all_items.extend((payload.get("data") or {}).get("items") or [])
        return self.parse_api_payload({"data": {"items": all_items}}, max_items=max_items)


class DifyJobsAdapter(SaasBaseAdapter):
    """Adapter for Dify official careers JSON endpoint (join.dify.ai)."""

    adapter_name = "dify_jobs"
    source_type = "company_official"

    def parse_api_payload(self, payload: dict[str, Any], *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        jobs = payload.get("jobs") or []
        results: list[dict[str, Any]] = []

        for item in jobs:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not jid or not title:
                continue

            location = str(item.get("location") or "").strip()
            work_site = str(item.get("work_site") or "").strip()
            loc_combined = " ".join(part for part in (work_site, location) if part)
            if not _CHINA_OR_GLOBAL_REMOTE_RE.search(loc_combined):
                continue

            content_text = strip_html_text(str(item.get("content") or ""))
            benefits_text = strip_html_text(str(item.get("benefits") or ""))
            full_body = "\n".join(part for part in (benefits_text, content_text) if part)
            detail_url = f"{self.base_url.rstrip('/')}/roles#{jid}"

            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=detail_url,
                apply_url=urljoin(self.base_url.rstrip("/") + "/", "roles"),
                location_text=loc_combined,
                body_text=full_body,
                published_at=parse_date_value(item.get("updated_at") or item.get("created_at")),
                department=str(item.get("department") or "").strip() or None,
                company_profile=company,
            )
            results.append(rec)
            if len(results) >= max_items:
                break

        return results

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = self.list_urls or [self.base_url]
        all_jobs: list[dict[str, Any]] = []
        for url in target_urls:
            resp = self.http_client.get(url)
            payload = json.loads(resp.text)
            all_jobs.extend(payload.get("jobs") or [])
        return self.parse_api_payload({"jobs": all_jobs}, max_items=max_items)


_LOC_LINE_RE = re.compile(r"(?:【\s*工作地点\s*】|工作地点[：:\s]+)([^\n\r]{1,60})")
_PAREN_CITY_RE = re.compile(r"[（(]([^()（）]{2,12})[）)]")


class BeisenAdapter(SaasBaseAdapter):
    """Generic adapter for Beisen (北森 *.zhiye.com / custom portals) public JSON API."""

    adapter_name = "beisen"
    source_type = "verified_saas"

    @staticmethod
    def extract_portal_id(html_text: str) -> str:
        """Extract PortalId from Beisen embedded BSGlobal script block."""
        m = re.search(r"var\s+BSGlobal\s*=\s*(\{.*?\});", html_text or "", re.S)
        if not m:
            raise ValueError("Beisen BSGlobal script block not found in portal HTML")
        bs = json.loads(m.group(1))
        portal_id = str(bs.get("PortalId") or "").strip()
        if not portal_id:
            raise ValueError("Beisen BSGlobal missing PortalId")
        return portal_id

    @staticmethod
    def _extract_location_hint(item: dict[str, Any], duty: str, require: str) -> str:
        loc_parts: list[str] = []
        loc_names = item.get("LocNames")
        if isinstance(loc_names, list):
            loc_parts.extend(str(x).strip() for x in loc_names if x)
        if item.get("DetailAddress"):
            loc_parts.append(str(item["DetailAddress"]).strip())
        m_loc = _LOC_LINE_RE.search(f"{duty}\n{require}")
        if m_loc:
            loc_parts.append(m_loc.group(1).strip())
        title = str(item.get("JobAdName") or "")
        for p_match in _PAREN_CITY_RE.findall(title):
            if not re.search(r"^[J0-9-]+$", p_match):
                loc_parts.append(p_match.strip())
        # Some portals prefix cities with hyphen e.g. 'Java开发-郑州(J14484)'
        for seg in re.split(r"[-/—–\s]+", title):
            if any(a in seg.lower() for _, aliases in _CITY_ALIAS_PATTERNS for a in aliases):
                loc_parts.append(seg.strip())
        return ", ".join(dict.fromkeys(p for p in loc_parts if p))

    def parse_jobad_list(
        self,
        items: list[dict[str, Any]],
        *,
        portal_origin: str,
        preferred_city: str | None = None,
        preferred_province: str | None = None,
        max_items: int = 12,
    ) -> list[dict[str, Any]]:
        """Normalize Beisen GetJobAdPageList Data rows into job.schema.json items."""
        company = self._resolve_company_profile()
        records: list[dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            raw_status = item.get("Status")
            status = "1" if raw_status is None else str(raw_status).strip()
            if status not in {"1", "True", "true", "open", ""}:
                continue

            raw_ad_id = str(item.get("JobAdId") or item.get("Id") or "").strip()
            title = str(item.get("JobAdName") or "").strip()
            if not raw_ad_id or not title:
                continue

            duty = strip_html_text(str(item.get("Duty") or ""))
            require = strip_html_text(str(item.get("Require") or ""))
            raw_loc = self._extract_location_hint(item, duty, require)
            city_hint = _pick_preferred_city_hint(
                raw_loc,
                preferred_city=preferred_city,
                preferred_province=preferred_province,
            )

            extra_lines: list[str] = []
            sal_field = str(item.get("Salary") or "").strip()
            if sal_field:
                extra_lines.append(f"薪资范围：{sal_field}")
            if item.get("WorkTime"):
                extra_lines.append(f"工作时间：{item['WorkTime']}")
            if item.get("Welfare"):
                extra_lines.append(f"福利待遇：{strip_html_text(str(item['Welfare']))}")
            if item.get("Insurance"):
                extra_lines.append(f"保险福利：{strip_html_text(str(item['Insurance']))}")
            body = "\n".join(p for p in [*extra_lines, duty, require] if p)

            pub_date = parse_date_value(item.get("PostDate")) or parse_date_value(item.get("ChangeDate"))
            detail_url = f"{portal_origin.rstrip('/')}/SocialDetail?id={raw_ad_id}"
            company_id = str(company.get("company_id") or "")
            raw_cat_id = str(item.get("CategoryId") or "").strip()
            policy_salary = None
            if (
                company_id in _COMPANY_POLICY_SALARY_MAP
                and "实习" not in title
                and (raw_cat_id == "2" or "27届" in title or "2027" in title)
            ):
                policy_salary = _COMPANY_POLICY_SALARY_MAP[company_id]

            rec = self._build_job_record(
                raw_id=raw_ad_id,
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text=city_hint or raw_loc,
                body_text=body,
                published_at=pub_date,
                department=str(item.get("Category") or "").strip() or None,
                education_req=str(item.get("Degree") or "").strip() or None,
                experience_req=str(item.get("YearsOfWorking") or "").strip() or None,
                company_profile=company,
                salary_hint=sal_field or None,
                policy_salary=policy_salary,
            )
            records.append(rec)

        return _prefer_and_dedup_jobs(
            records,
            preferred_city=preferred_city,
            preferred_province=preferred_province,
            max_items=max_items,
        )

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target_urls = self.list_urls or [self.base_url]
        preferred_city = str(self.source_config.get("city") or "").strip() or None
        preferred_province = str(self.source_config.get("province") or "").strip() or None
        all_items: list[dict[str, Any]] = []
        portal_origin = self.base_url.rstrip("/")

        for portal_url in target_urls:
            page_resp = self.http_client.get(portal_url)
            parsed = urlparse(page_resp.url or portal_url)
            portal_origin = f"{parsed.scheme}://{parsed.netloc}"
            portal_id = self.extract_portal_id(page_resp.text)

            headers = {
                "Content-Type": "application/json",
                "Origin": portal_origin,
                "Referer": page_resp.url or portal_url,
            }
            # Scan up to 5 pages (300 listings) so multi-city portals surface Henan roles
            for page_idx in range(5):
                api_resp = self.http_client.request(
                    "POST",
                    f"{portal_origin}/api/Jobad/GetJobAdPageList",
                    headers=headers,
                    json_body={"PageIndex": page_idx, "PageSize": 60, "PortalId": portal_id},
                )
                payload = json.loads(api_resp.text)
                if payload.get("Code") not in (200, 0, None) and not payload.get("Data"):
                    raise RuntimeError(f"Beisen API error: {payload.get('Message')}")
                batch = payload.get("Data") or []
                if not batch:
                    break
                all_items.extend(batch)
                if len(batch) < 60:
                    break
                # Stop early once enough non-prioritized items exist and no location filter needed
                if page_idx >= 1 and (not preferred_city or preferred_city in {"多城市", "全国"}) and len(all_items) >= max_items * 2:
                    break

        return self.parse_jobad_list(
            all_items,
            portal_origin=portal_origin,
            preferred_city=preferred_city,
            preferred_province=preferred_province,
            max_items=max_items,
        )


class HotjobAdapter(SaasBaseAdapter):
    """Generic adapter for Hotjob Wecruit portals (wecruit.hotjob.cn/wecruit/positionInfo/*)."""

    adapter_name = "hotjob"
    source_type = "verified_saas"

    @staticmethod
    def extract_suite_key(url: str) -> str:
        """Extract SU... tenant suite key from a Hotjob portal URL."""
        m = re.search(r"/(SU[a-zA-Z0-9]+)(?:/|$)", url or "")
        if not m:
            raise ValueError(f"Hotjob SU suite key not found in URL: {url}")
        return m.group(1)

    def parse_positions_payload(
        self,
        items: list[dict[str, Any]],
        *,
        details_by_id: dict[str, dict[str, Any]] | None = None,
        suite_key: str,
        portal_origin: str = "https://wecruit.hotjob.cn",
        preferred_city: str | None = None,
        preferred_province: str | None = None,
        max_items: int = 12,
    ) -> list[dict[str, Any]]:
        """Transform Hotjob position summaries + optional detail payloads into job records."""
        company = self._resolve_company_profile()
        detail_map = details_by_id or {}
        records: list[dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            post_id = str(item.get("postId") or "").strip()
            title = str(item.get("postName") or "").strip()
            if not post_id or not title:
                continue
            rtype = int(item.get("recruitType") or 2)
            det = detail_map.get(post_id) or {}

            raw_loc = str(det.get("workPlaceStr") or item.get("workPlaceStr") or "").strip()
            city_hint = _pick_preferred_city_hint(
                raw_loc,
                preferred_city=preferred_city,
                preferred_province=preferred_province,
            )
            work_content = strip_html_text(str(det.get("workContent") or ""))
            service_cond = strip_html_text(str(det.get("serviceCondition") or ""))
            extra_parts = []
            if item.get("projectName"):
                extra_parts.append(f"招聘项目：{item['projectName']}")
            if item.get("subject"):
                extra_parts.append(f"相关专业：{item['subject']}")
            body = "\n".join(p for p in [*extra_parts, work_content, service_cond] if p)

            detail_url = f"{portal_origin.rstrip('/')}/{suite_key}/mc/detail?postId={post_id}&recruitType={rtype}"
            pub_date = parse_date_value(det.get("publishDate") or item.get("publishDate") or item.get("publishFirstDate"))
            raw_sal = str(
                det.get("salary")
                or det.get("salaryStr")
                or det.get("salaryName")
                or item.get("salary")
                or item.get("salaryStr")
                or item.get("salaryName")
                or ""
            ).strip() or None

            rec = self._build_job_record(
                raw_id=post_id,
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text=city_hint or raw_loc,
                body_text=body,
                published_at=pub_date,
                department=str(item.get("department") or item.get("postTypeName") or "").strip() or None,
                education_req=str(det.get("education") or item.get("educationStr") or "").strip() or None,
                experience_req=str(item.get("workYears") or "").strip() or None,
                company_profile=company,
                salary_hint=raw_sal,
            )
            records.append(rec)

        return _prefer_and_dedup_jobs(
            records,
            preferred_city=preferred_city,
            preferred_province=preferred_province,
            max_items=max_items,
        )

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        portal_url = (self.list_urls or [self.base_url])[0]
        parsed = urlparse(portal_url)
        portal_origin = f"{parsed.scheme}://{parsed.netloc}"
        suite_key = self.extract_suite_key(portal_url)
        preferred_city = str(self.source_config.get("city") or "").strip() or None
        preferred_province = str(self.source_config.get("province") or "").strip() or None

        summaries: list[dict[str, Any]] = []
        seen_post_ids: set[str] = set()
        for rtype in (2, 1):
            resp = self.http_client.request(
                "POST",
                f"{portal_origin}/wecruit/positionInfo/listPosition/{suite_key}",
                data={"recruitType": str(rtype), "pageSize": "30", "currentPage": "1"},
            )
            payload = json.loads(resp.text)
            rows = ((payload.get("data") or {}).get("pageForm") or {}).get("pageData") or []
            for row in rows:
                pid = str(row.get("postId") or "").strip()
                if pid and pid not in seen_post_ids:
                    seen_post_ids.add(pid)
                    row.setdefault("recruitType", rtype)
                    summaries.append(row)

        # Rank summaries first to avoid fetching JD details for excluded cities/excess items
        prelim = self.parse_positions_payload(
            summaries,
            suite_key=suite_key,
            portal_origin=portal_origin,
            preferred_city=preferred_city,
            preferred_province=preferred_province,
            max_items=max_items,
        )
        selected_ids = {rec["id"].rsplit("-", 1)[-1]: rec for rec in prelim}
        selected_summaries = [
            s for s in summaries if _make_job_id(str(self._resolve_company_profile()["company_id"]), str(s.get("postId"))) in {r["id"] for r in prelim}
        ]
        if not selected_summaries:
            selected_summaries = summaries[:max_items]
        del selected_ids

        details_by_id: dict[str, dict[str, Any]] = {}
        for row in selected_summaries[:max_items]:
            pid = str(row.get("postId") or "")
            rtype = int(row.get("recruitType") or 2)
            det_resp = self.http_client.request(
                "POST",
                f"{portal_origin}/wecruit/positionInfo/listPositionDetail/{suite_key}",
                data={"postId": pid, "recruitType": str(rtype)},
            )
            det_data = json.loads(det_resp.text).get("data")
            if isinstance(det_data, dict):
                details_by_id[pid] = det_data

        return self.parse_positions_payload(
            selected_summaries,
            details_by_id=details_by_id,
            suite_key=suite_key,
            portal_origin=portal_origin,
            preferred_city=preferred_city,
            preferred_province=preferred_province,
            max_items=max_items,
        )


class HanweiJobsAdapter(SaasBaseAdapter):
    """Adapter for Hanwei Technology (汉威科技 hanwei.cn/join_us.html) careers page."""

    adapter_name = "hanwei_jobs"
    source_type = "company_official"

    def parse_portal_html(self, html_text: str, page_url: str, *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        soup = BeautifulSoup(html_text or "", "html.parser")
        results: list[dict[str, Any]] = []
        base_link = page_url.split("#", 1)[0]

        for idx, card in enumerate(soup.select("div.p_loopitem")):
            raw_text = card.get_text("\n", strip=True)
            if "岗位职责" not in raw_text and not card.select_one("a[href^='mailto:']"):
                continue
            lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip() and ln.strip() not in {"查看岗位 +", "职位申请 +"}]
            if not lines:
                continue
            title = lines[0]
            pub_date = None
            city_hint = "郑州"
            for ln in lines[1:6]:
                if not pub_date:
                    pub_date = parse_date_value(ln)
                if any(a in ln for _, aliases in _CITY_ALIAS_PATTERNS for a in aliases):
                    city_hint = ln

            body = "\n".join(lines[1:])
            slug = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
            detail_url = f"{base_link}#job-{slug}"
            rec = self._build_job_record(
                raw_id=f"{slug}-{idx}",
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text=city_hint,
                body_text=body,
                published_at=pub_date,
                company_profile=company,
            )
            results.append(rec)
            if len(results) >= max_items:
                break

        return results

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target = (self.list_urls or [urljoin(self.base_url.rstrip("/") + "/", "join_us.html")])[0]
        resp = self.http_client.get(target)
        return self.parse_portal_html(resp.text, resp.url or target, max_items=max_items)


class GltechJobsAdapter(SaasBaseAdapter):
    """Adapter for Gltech (光力科技 gltech.cn/recruitment/) official recruitment page."""

    adapter_name = "gltech_jobs"
    source_type = "company_official"

    def parse_portal_html(self, html_text: str, page_url: str, *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        soup = BeautifulSoup(html_text or "", "html.parser")
        results: list[dict[str, Any]] = []
        base_link = page_url.split("#", 1)[0]

        for idx, item in enumerate(soup.select("li.xz-a019-item")):
            lines = [ln.strip() for ln in item.get_text("\n", strip=True).splitlines() if ln.strip() and ln.strip() != "立即申请"]
            if not lines:
                continue
            title = lines[0]
            body = "\n".join(lines[1:])
            pub_date = None
            salary = lines[1] if len(lines) > 1 and ("k" in lines[1].lower() or "面议" in lines[1]) else None
            dept = None
            exp = None
            edu = None
            loc_parts: list[str] = []

            for ln in lines[1:]:
                if not pub_date:
                    pub_date = parse_date_value(ln)
                if ln.startswith("工作地点："):
                    loc_parts.append(ln.split("：", 1)[1].strip())
                elif ln.startswith("部门："):
                    dept = ln.split("：", 1)[1].strip()
                elif ln.startswith("工作经验："):
                    exp = ln.split("：", 1)[1].strip()
                elif ln.startswith("学历："):
                    edu = ln.split("：", 1)[1].strip()

            raw_loc = " ".join(loc_parts).strip()
            if not raw_loc and len(lines) > 3:
                raw_loc = lines[3]
            loc_text = _pick_preferred_city_hint(
                raw_loc,
                preferred_city=str(self.source_config.get("city") or "郑州市"),
                preferred_province="河南省",
            )
            # Handle regional sales roles listing province without city (e.g., "安徽，江苏")
            if loc_text and not any(a in loc_text.lower() for _, aliases in _CITY_ALIAS_PATTERNS for a in aliases):
                if "江苏" in loc_text:
                    loc_text = "南京市"
                elif "安徽" in loc_text:
                    loc_text = "合肥市"
                elif "湖北" in loc_text:
                    loc_text = "武汉市"
            if salary and f"薪资待遇：{salary}" not in body:
                body = f"薪资待遇：{salary}\n{body}"

            slug = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
            detail_url = f"{base_link}#job-{slug}"
            rec = self._build_job_record(
                raw_id=f"{slug}-{idx}",
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text=loc_text,
                body_text=body,
                published_at=pub_date,
                department=dept,
                education_req=edu,
                experience_req=exp,
                company_profile=company,
                salary_hint=salary,
            )
            results.append(rec)

        return _prefer_and_dedup_jobs(
            results,
            preferred_city="郑州市",
            preferred_province="河南省",
            max_items=max_items,
        )

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target = (self.list_urls or [urljoin(self.base_url.rstrip("/") + "/", "recruitment/")])[0]
        resp = self.http_client.get(target)
        return self.parse_portal_html(resp.text, resp.url or target, max_items=max_items)


class XyylJobsAdapter(SaasBaseAdapter):
    """Adapter for Xiangyu Medical (翔宇医疗 xyyl.com/jrwm.html & xyzp.html)."""

    adapter_name = "xyyl_jobs"
    source_type = "company_official"

    @staticmethod
    def extract_detail_urls(html_text: str, page_url: str) -> list[str]:
        soup = BeautifulSoup(html_text or "", "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "xyzp_detail/" in href and "undefined" not in href:
                full = urljoin(page_url, href)
                if full not in seen:
                    seen.add(full)
                    urls.append(full)
        return urls

    @staticmethod
    def extract_policy_salary(rcwh_html: str) -> str | None:
        """Extract verified salary + housing allowance standard from xyyl.com/rcwh.html."""
        txt = strip_html_text(rcwh_html)
        if not txt:
            return None
        m_sal = re.search(r"薪资待遇[：:\s]*(\d+[-~～]\d+\s*元\s*/\s*月)", txt)
        m_house = re.search(r"(?:一次性)?购房补贴\s*(\d+[-~～]\d+\s*万元?)", txt)
        if m_sal and m_house:
            sal_s = m_sal.group(1).replace(" ", "")
            house_s = m_house.group(1).replace(" ", "")
            return _clip(f"官网人才政策：月薪{sal_s}（购房补贴{house_s}）", 80)
        if m_sal:
            return _clip(f"官网人才政策：月薪{m_sal.group(1).replace(' ', '')}", 80)
        return None

    def parse_detail_html(
        self,
        html_text: str,
        detail_url: str,
        *,
        policy_salary: str | None = None,
    ) -> dict[str, Any] | None:
        company = self._resolve_company_profile()
        soup = BeautifulSoup(html_text or "", "html.parser")
        for t in soup(["script", "style"]):
            t.decompose()
        main_el = soup.select_one("main, .main, .content") or soup.body
        raw_txt = main_el.get_text("\n", strip=True) if main_el else soup.get_text("\n", strip=True)
        # Focus starting from 最低学历 or title heading to avoid header navigation noise
        lines = [ln.strip() for ln in raw_txt.splitlines() if ln.strip()]
        idx_edu = next((i for i, ln in enumerate(lines) if ln.startswith("最低学历")), -1)
        if idx_edu > 0:
            title = next(
                (lines[k] for k in range(idx_edu - 1, -1, -1) if lines[k] not in {"面议", "首页"} and not re.match(r"^\d+[-~]\d+", lines[k])),
                lines[0],
            )
            relevant_lines = lines[max(0, idx_edu - 2) :]
        else:
            title_tag = soup.title.get_text(strip=True) if soup.title else ""
            title = title_tag.split("-")[0].strip()
            relevant_lines = lines
        if not title:
            return None

        # Trim trailing footer
        end_idx = next((i for i, ln in enumerate(relevant_lines) if ln.startswith("版权所有") or ln == "相关推荐"), len(relevant_lines))
        body_lines = relevant_lines[:end_idx]
        body_text = "\n".join(body_lines)

        def _kv(label: str) -> str | None:
            for i, ln in enumerate(body_lines):
                if ln.startswith(label):
                    after = ln.split("：", 1)[-1].strip() if "：" in ln else ""
                    if after:
                        return after
                    if i + 1 < len(body_lines):
                        return body_lines[i + 1]
            return None

        loc_text = _kv("工作地区") or "河南省安阳市内黄县"
        edu_req = _kv("最低学历")
        exp_req = _kv("经验要求")
        m_id = re.search(r"xyzp_detail/(\d+)", detail_url)
        raw_id = m_id.group(1) if m_id else hashlib.sha1(detail_url.encode("utf-8")).hexdigest()[:8]

        return self._build_job_record(
            raw_id=raw_id,
            title=title,
            source_url=detail_url,
            apply_url=detail_url,
            location_text=loc_text,
            body_text=body_text,
            education_req=edu_req,
            experience_req=exp_req,
            company_profile=company,
            policy_salary=policy_salary,
        )

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        list_pages = self.list_urls or [
            urljoin(self.base_url.rstrip("/") + "/", "jrwm.html"),
            urljoin(self.base_url.rstrip("/") + "/", "xyzp.html"),
        ]
        policy_salary: str | None = None
        try:
            rcwh_resp = self.http_client.get(urljoin(self.base_url.rstrip("/") + "/", "rcwh.html"))
            policy_salary = self.extract_policy_salary(rcwh_resp.text)
        except Exception:
            policy_salary = None

        detail_urls: list[str] = []
        seen: set[str] = set()
        for lp in list_pages:
            resp = self.http_client.get(lp)
            for du in self.extract_detail_urls(resp.text, resp.url or lp):
                if du not in seen:
                    seen.add(du)
                    detail_urls.append(du)

        results: list[dict[str, Any]] = []
        for du in detail_urls[: max(0, max_items)]:
            det_resp = self.http_client.get(du)
            rec = self.parse_detail_html(det_resp.text, det_resp.url or du, policy_salary=policy_salary)
            if rec:
                results.append(rec)
        return results


class WeihuaJobsAdapter(SaasBaseAdapter):
    """Adapter for Weihua Group (卫华集团 job.craneweihua.com) AJAX POST /filter endpoint."""

    adapter_name = "weihua_jobs"
    source_type = "company_official"

    _DEFAULT_UNITS: tuple[tuple[int, str, str], ...] = (
        (4, "卫华集团", "新乡市"),
        (5, "河南卫华", "新乡市"),
        (12, "郑州卫莱", "郑州市"),
        (11, "蒲瑞公司", "新乡市"),
        (8, "云中未来", "新乡市"),
        (14, "江苏卫华海洋", "南通市"),
    )

    def parse_unit_payload(
        self,
        items: list[dict[str, Any]],
        *,
        unit_name: str,
        default_unit_city: str,
        portal_origin: str,
    ) -> list[dict[str, Any]]:
        """Convert Weihua /filter JSON rows into job.schema.json items."""
        company = self._resolve_company_profile()
        records: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("name") or "").strip()
            if not jid or not title:
                continue
            content_plain = strip_html_text(str(item.get("content") or ""))
            content2_plain = strip_html_text(str(item.get("content2") or ""))
            combined_content = "\n".join(p for p in (content2_plain, content_plain) if p)
            loc_addr = str(item.get("address") or "").strip() or default_unit_city
            depart = str(item.get("depart") or unit_name).strip()
            num = str(item.get("num") or "").strip()
            prefix = f"招聘单位：{unit_name} | 部门：{depart}"
            if num:
                prefix += f" | 招聘人数：{num}"
            body = f"{prefix}\n{combined_content}" if combined_content else prefix
            detail_url = f"{portal_origin.rstrip('/')}/detail?id={jid}"
            rec = self._build_job_record(
                raw_id=jid,
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text=loc_addr,
                body_text=body,
                department=f"{unit_name}-{depart}" if depart != unit_name else unit_name,
                company_profile=company,
            )
            records.append(rec)
        return records

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        portal_url = (self.list_urls or [self.base_url])[0]
        parsed = urlparse(portal_url)
        portal_origin = f"{parsed.scheme}://{parsed.netloc}"

        # Initialize PHP session cookie on /recruit before calling /filter
        self.http_client.get(f"{portal_origin}/recruit?type=1&unit_id=4")

        all_records: list[dict[str, Any]] = []
        for rtype in (1, 2):
            for uid, uname, ucity in self._DEFAULT_UNITS:
                for page_idx in range(1, 4):
                    resp = self.http_client.request(
                        "POST",
                        f"{portal_origin}/filter",
                        data={
                            "type": str(rtype),
                            "address_id": "0",
                            "attr_id": "0",
                            "unit_id": str(uid),
                            "page": str(page_idx),
                        },
                        headers={
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": f"{portal_origin}/recruit?type={rtype}&unit_id={uid}",
                            "Origin": portal_origin,
                        },
                    )
                    payload = json.loads(resp.text)
                    batch = payload.get("data") or []
                    if not isinstance(batch, list) or not batch:
                        break
                    all_records.extend(
                        self.parse_unit_payload(
                            batch,
                            unit_name=uname,
                            default_unit_city=ucity,
                            portal_origin=portal_origin,
                        )
                    )
                    if len(batch) < 10:
                        break

        return _prefer_and_dedup_jobs(
            all_records,
            preferred_city=str(self.source_config.get("city") or "新乡市"),
            preferred_province="河南省",
            max_items=max_items,
        )


class LomonJobsAdapter(SaasBaseAdapter):
    """Adapter for Lomon Billions (龙佰集团 lomonbillions.com/jobs/10921)."""

    adapter_name = "lomon_jobs"
    source_type = "company_official"

    _UNIT_LIST: tuple[tuple[str, str], ...] = (
        ("集团总部", "焦作市"),
        ("攀枝花公司", "攀枝花市"),
        ("丰源公司", "攀枝花市"),
        ("龙佰锂能", "焦作市"),
        ("中炭新材料", "焦作市"),
        ("云南新立", "楚雄州"),
        ("德阳公司", "德阳市"),
        ("甘肃佰利联", "金昌市"),
        ("襄阳公司", "襄阳市"),
        ("云南国钛", "楚雄州"),
        ("甘肃国钛", "金昌市"),
        ("朝阳东锆", "朝阳市"),
        ("上海公司", "上海市"),
        ("佰利联新材料", "焦作市"),
        ("东方钪业", "焦作市"),
    )

    def parse_portal_html(self, html_text: str, page_url: str, *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        soup = BeautifulSoup(html_text or "", "html.parser")
        base_link = page_url.split("#", 1)[0]

        first_span = soup.select_one("span.zwtit")
        if not first_span:
            return []
        job_box = first_span.find_parent("ul").find_parent("div")
        job_uls = [ul for ul in job_box.find_all("ul") if any(li.select_one("span.zwtit") for li in ul.find_all("li"))]
        if not job_uls:
            return []

        # ul[0] aggregates "全部", while ul[1..N] correspond 1-to-1 to the 15 subsidiary tabs
        unit_uls = job_uls[1:] if len(job_uls) > 1 else job_uls
        records: list[dict[str, Any]] = []
        for u_idx, ul in enumerate(unit_uls):
            unit_name, unit_city = self._UNIT_LIST[u_idx] if u_idx < len(self._UNIT_LIST) else ("集团总部", "焦作市")
            for j_idx, li in enumerate(ul.find_all("li")):
                title_el = li.select_one("span.zwtit")
                if not title_el:
                    continue
                title = title_el.get_text(" ", strip=True)
                lines = [ln.strip() for ln in li.get_text("\n", strip=True).splitlines() if ln.strip()]
                body = "\n".join(lines[1:]) if len(lines) > 1 else title
                body = f"所属基地：{unit_name}（{unit_city}）\n{body}"

                slug = hashlib.sha1(f"{unit_name}:{title}:{j_idx}".encode("utf-8")).hexdigest()[:8]
                detail_url = f"{base_link}#unit-{u_idx}-{slug}"
                rec = self._build_job_record(
                    raw_id=f"{u_idx}-{j_idx}-{slug}",
                    title=title,
                    source_url=detail_url,
                    apply_url=detail_url,
                    location_text=unit_city,
                    body_text=body,
                    department=unit_name,
                    company_profile=company,
                )
                records.append(rec)

        return _prefer_and_dedup_jobs(
            records,
            preferred_city=str(self.source_config.get("city") or "焦作市"),
            preferred_province="河南省",
            max_items=max_items,
        )

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target = (self.list_urls or [urljoin(self.base_url.rstrip("/") + "/", "jobs/10921")])[0]
        resp = self.http_client.get(target)
        return self.parse_portal_html(resp.text, resp.url or target, max_items=max_items)


class SuntrontJobsAdapter(SaasBaseAdapter):
    """Adapter for Suntront Tech (新天科技 suntront.com/recruitment/recruitment-1.html)."""

    adapter_name = "suntront_jobs"
    source_type = "company_official"

    def parse_portal_html(self, html_text: str, page_url: str, *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        soup = BeautifulSoup(html_text or "", "html.parser")
        base_link = page_url.split("#", 1)[0]
        results: list[dict[str, Any]] = []

        for idx, card in enumerate(soup.select("div.station-item")):
            h3 = card.find("h3")
            title = h3.get_text(" ", strip=True) if h3 else ""
            if not title:
                continue
            lines = [ln.strip() for ln in card.get_text("\n", strip=True).splitlines() if ln.strip() and ln.strip() != title]
            body = "\n".join(lines) or f"新天科技股份有限公司官方招聘岗位：{title}"
            slug = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
            detail_url = f"{base_link}#job-{slug}"
            rec = self._build_job_record(
                raw_id=f"{slug}-{idx}",
                title=title,
                source_url=detail_url,
                apply_url=detail_url,
                location_text="郑州市",
                body_text=body,
                company_profile=company,
            )
            results.append(rec)
            if len(results) >= max_items:
                break

        return results

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target = (self.list_urls or [urljoin(self.base_url.rstrip("/") + "/", "recruitment/recruitment-1.html")])[0]
        resp = self.http_client.get(target)
        return self.parse_portal_html(resp.text, resp.url or target, max_items=max_items)


class JuicefsJobsAdapter(SaasBaseAdapter):
    """Adapter for Juicedata / JuiceFS official careers page (juicefs.com/zh-cn/careers)."""

    adapter_name = "juicefs_jobs"
    source_type = "company_official"

    def parse_portal_html(self, html_text: str, page_url: str, *, max_items: int = 12) -> list[dict[str, Any]]:
        company = self._resolve_company_profile()
        soup = BeautifulSoup(html_text or "", "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        lines = [ln.strip() for ln in soup.get_text("\n", strip=True).splitlines() if ln.strip()]
        base_link = page_url.split("#", 1)[0]

        duty_indices = [i for i, ln in enumerate(lines) if ln == "目标与职责" and i >= 1]
        results: list[dict[str, Any]] = []
        stop_tokens = {"产品", "文档", "资源", "社区", "公司", "热门主题"}

        for k, start_idx in enumerate(duty_indices):
            title = lines[start_idx - 1]
            if not title or len(title) > 80:
                continue
            end_idx = duty_indices[k + 1] - 1 if k + 1 < len(duty_indices) else len(lines)
            block: list[str] = []
            seen_loc_section = False
            after_loc_count = 0
            for ln in lines[start_idx:end_idx]:
                if seen_loc_section and (ln in stop_tokens or ln.startswith("Copyright ")):
                    break
                block.append(ln)
                if ln == "工作地点与薪资":
                    seen_loc_section = True
                elif seen_loc_section:
                    after_loc_count += 1
                    if after_loc_count >= 3:
                        break

            loc_line = ""
            for j, bln in enumerate(block):
                if bln == "工作地点与薪资" and j + 1 < len(block):
                    loc_line = block[j + 1]
                    break
            body = "\n".join(block)
            slug = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
            detail_url = f"{base_link}#job-{slug}"
            rec = self._build_job_record(
                raw_id=f"{slug}-{k}",
                title=title,
                source_url=detail_url,
                apply_url=base_link,
                location_text=loc_line or "杭州市",
                body_text=body,
                company_profile=company,
            )
            results.append(rec)
            if len(results) >= max_items:
                break

        return results

    def fetch_jobs(self, *, max_items: int = 12) -> list[dict[str, Any]]:
        target = (self.list_urls or [urljoin(self.base_url.rstrip("/") + "/", "zh-cn/careers")])[0]
        resp = self.http_client.get(target)
        return self.parse_portal_html(resp.text, resp.url or target, max_items=max_items)
