"""Henan Province provincial & municipal official recruitment adapters."""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from scrapers.core.base_adapter import _INLINE_DATE_RE, AnnouncementStub, BaseScraperAdapter
from scrapers.core.extractors import ATTACHMENT_EXTENSIONS, HtmlExtractionResult

RECRUIT_POSITIVE_KEYWORDS = (
    "招聘",
    "招考",
    "招募",
    "选聘",
    "引进",
    "简章",
    "招才引智",
    "公开招聘",
    "专项计划",
    "文职人员",
)

POST_EXAM_EXCLUDE_KEYWORDS = (
    "拟聘用",
    "拟录用",
    "拟招聘人员",
    "拟聘人员",
    "聘用人员名单",
    "录用人员名单",
    "拟安置",
    "拟加分",
    "体检",
    "体能",
    "复检",
    "考察",
    "政审",
    "成绩",
    "笔试成绩",
    "合格分数线",
    "最低合格分数线",
    "面试",
    "资格复审",
    "资格确认",
    "资格审查",
    "资格审核",
    "递补",
    "加分",
    "核减",
    "核销",
    "取消部分职位",
    "取消岗位",
    "免缴",
    "退费",
    "温馨提醒",
    "注意事项",
    "报名有关情况",
    "报名即将开始",
    "报考指南",
    "考试大纲",
    "准考证",
    "九问九答",
    "现场确认",
    "咨询电话",
    "笔试工作",
    "笔试有关事宜",
    "考务工作",
    "关于举行",
    "分组公告",
)

NON_RECRUIT_EXCLUDE_KEYWORDS = (
    "职业资格",
    "执业药师",
    "建造师",
    "造价工程师",
    "安全工程师",
    "消防工程师",
    "勘察设计",
    "测绘师",
    "监理工程师",
    "社会工作者",
    "咨询工程师",
    "城乡规划师",
    "环境影响评价",
    "注册计量师",
    "新闻记者",
    "出版专业",
    "翻译专业",
    "审计专业",
    "高级经济",
    "职称",
    "讲课答辩",
    "劳动能力鉴定",
    "工伤",
    "特殊工种",
    "病退",
    "提前退休",
    "稳岗补贴",
    "稳岗返还",
    "扩岗补助",
    "岗位补贴",
    "社保补贴",
    "培训补贴",
    "工资保证金",
    "创业培训",
    "预算公开",
    "决算公开",
    "询价采购",
    "招标代理",
    "竞争性磋商",
    "创业导师",
    "定补资金",
    "招聘会",
    "专场招聘",
    "双选会",
    "招聘活动",
    "就业专项活动",
    "见习单位",
    "惩戒名单",
    "劳务派遣",
    "辅警",
    "警务辅助",
    "公开遴选",
    "遴选公务员",
    "选调生",
    "中央机关",
    "驻外机构",
    "法规",
    "违纪违规",
    "处理规定",
    "管理办法",
)

OUT_OF_PROVINCE_KEYWORDS = (
    "宁夏",
    "海南",
    "北京市",
    "山东省",
    "浙江省",
    "江苏省",
    "四川省",
    "太原市",
    "陕西",
    "河北",
    "安徽",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "云南",
    "贵州",
    "甘肃",
    "青海",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "福建",
    "江西",
    "重庆",
    "天津",
    "上海",
    "新疆",
    "西藏",
    "国家烟草专卖局",
)

_BINARY_SUFFIXES = tuple(ATTACHMENT_EXTENSIONS.keys())


def classify_announcement_title(title: str) -> str:
    """Classify an announcement title into recruitment / post_exam_notice / non_recruitment / other."""
    norm = re.sub(r"\s+", " ", (title or "").strip())
    if not norm:
        return "other"
    if any(kw in norm for kw in POST_EXAM_EXCLUDE_KEYWORDS):
        return "post_exam_notice"
    if any(kw in norm for kw in NON_RECRUIT_EXCLUDE_KEYWORDS):
        return "non_recruitment"
    if any(kw in norm for kw in RECRUIT_POSITIVE_KEYWORDS):
        return "recruitment"
    return "other"


def is_recruitment_announcement(title: str, *, allow_out_of_province: bool = True) -> bool:
    """Return True if title represents an active/primary recruitment announcement."""
    if classify_announcement_title(title) != "recruitment":
        return False
    if not allow_out_of_province:
        henan_locs = (
            "河南",
            "郑州",
            "洛阳",
            "开封",
            "平顶山",
            "安阳",
            "鹤壁",
            "新乡",
            "焦作",
            "濮阳",
            "许昌",
            "漯河",
            "三门峡",
            "南阳",
            "商丘",
            "信阳",
            "周口",
            "驻马店",
            "济源",
            "航空港",
        )
        if not any(loc in title for loc in henan_locs):
            if any(prov in title for prov in OUT_OF_PROVINCE_KEYWORDS):
                return False
            return False
    return True


def _is_direct_binary_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(_BINARY_SUFFIXES)


_SHORT_INLINE_DATE_RE = re.compile(r"(?<!\d)(2\d)-(1[0-2]|0?[1-9])-(3[01]|[12]\d|0?[1-9])(?=$|\D)")
_TRAILING_DATE_RE = re.compile(r"\s*(?:20)?\d{2}[-年/.](?:1[0-2]|0?[1-9])[-月/.](?:3[01]|[12]\d|0?[1-9])日?$")
_LEADING_BADGE_RE = re.compile(r"^(?:\[(?:置顶|链接)\]\s*)+")


def _extract_date_near_link(a_tag: Any, *, allow_two_digit_year: bool = False) -> str | None:
    candidates: list[Any] = [a_tag]
    if a_tag.parent is not None and len(a_tag.parent.find_all("a", href=True)) <= 2:
        candidates.append(a_tag.parent)
    row_parent = a_tag.find_parent(("li", "tr"))
    if row_parent is not None and row_parent not in candidates and len(row_parent.find_all("a", href=True)) <= 3:
        candidates.append(row_parent)

    for container in candidates:
        text = container.get_text(" ", strip=True)
        m = _INLINE_DATE_RE.search(text)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if allow_two_digit_year:
            m2 = _SHORT_INLINE_DATE_RE.search(text)
            if m2:
                return f"20{int(m2.group(1)):02d}-{int(m2.group(2)):02d}-{int(m2.group(3)):02d}"
    return None


class HenanGovBaseAdapter(BaseScraperAdapter):
    """Shared base for Henan provincial and municipal recruitment adapters."""

    allow_out_of_province: bool = True
    allowed_hosts: tuple[str, ...] = ()
    list_container_selectors: tuple[str, ...] = ()
    list_decompose_selectors: tuple[str, ...] = ()
    require_date_or_pattern: re.Pattern[str] | None = None
    detail_path_pattern: re.Pattern[str] | None = None
    detail_url_pattern: re.Pattern[str] | None = None
    allow_two_digit_list_dates: bool = False
    title_suffix_pattern: re.Pattern[str] | None = None

    def should_keep_title(self, title: str, *, page_url: str = "") -> bool:
        if not self.filter_by_keywords:
            return True
        return is_recruitment_announcement(title, allow_out_of_province=self.allow_out_of_province)

    def _is_allowed_host(self, netloc: str) -> bool:
        allowed: set[str] = set()
        if self.base_url:
            base_host = urlparse(self.base_url).netloc.lower()
            if base_host:
                allowed.add(base_host)
        allowed.update(h.lower() for h in self.allowed_hosts if h)
        return not allowed or netloc.lower() in allowed

    def _extract_stub_title_and_date(self, a_tag: Any) -> tuple[str, str | None]:
        title = re.sub(r"\s+", " ", str(a_tag.get("title") or a_tag.get_text(" ", strip=True))).strip()
        pub_date = _extract_date_near_link(a_tag, allow_two_digit_year=self.allow_two_digit_list_dates)
        return title, pub_date

    def _collect_stubs_from_containers(
        self,
        soup: BeautifulSoup,
        page_url: str,
        *,
        container_selectors: tuple[str, ...],
        require_date_or_pattern: re.Pattern[str] | None = None,
        detail_path_pattern: re.Pattern[str] | None = None,
        detail_url_pattern: re.Pattern[str] | None = None,
    ) -> list[AnnouncementStub]:
        stubs: list[AnnouncementStub] = []
        seen_urls: set[str] = set()

        anchors: list[Any] = []
        for sel in container_selectors:
            matched = soup.select(f"{sel} a[href]")
            if matched:
                anchors.extend(matched)
        if not anchors:
            anchors = soup.find_all("a", href=True)

        for a_tag in anchors:
            raw_href = str(a_tag.get("href") or "").strip()
            if not raw_href or raw_href.startswith(("javascript:", "mailto:", "#")):
                continue
            full_url = urljoin(page_url, raw_href)
            parsed = urlparse(full_url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if not self._is_allowed_host(parsed.netloc):
                continue
            if _is_direct_binary_url(full_url):
                continue
            if detail_path_pattern and not detail_path_pattern.search(parsed.path):
                continue
            if detail_url_pattern and not detail_url_pattern.search(full_url):
                continue

            title, pub_date = self._extract_stub_title_and_date(a_tag)
            if len(title) < 6:
                continue

            if require_date_or_pattern and pub_date is None and not require_date_or_pattern.search(parsed.path):
                continue
            if not self.should_keep_title(title, page_url=page_url):
                continue
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            stubs.append(AnnouncementStub(url=full_url, title=title, published_at=pub_date))

        return stubs

    def parse_list_page(self, html: str, page_url: str) -> list[AnnouncementStub]:
        if not self.list_container_selectors:
            return super().parse_list_page(html, page_url)
        soup = BeautifulSoup(html or "", "html.parser")
        for sel in self.list_decompose_selectors:
            for node in soup.select(sel):
                node.decompose()
        return self._collect_stubs_from_containers(
            soup,
            page_url,
            container_selectors=self.list_container_selectors,
            require_date_or_pattern=self.require_date_or_pattern,
            detail_path_pattern=self.detail_path_pattern,
            detail_url_pattern=self.detail_url_pattern,
        )

    def clean_html_result(
        self,
        html_res: HtmlExtractionResult,
        stub: AnnouncementStub,
    ) -> HtmlExtractionResult:
        if self.title_suffix_pattern and html_res.title:
            html_res.title = self.title_suffix_pattern.sub("", html_res.title).strip()
        return html_res


class EpointGovAdapter(HenanGovBaseAdapter):
    """Shared base for Epoint (新点政务 CMS) municipal portals."""

    list_container_selectors = ("ul li",)
    list_decompose_selectors = (".ewb-page",)
    detail_title_selectors = ("#detail-infotitle", "h1.title", "h1", "title")
    detail_content_selectors = (".details-box", ".details", ".article-content")


class BodaUniversityAdapter(HenanGovBaseAdapter):
    """Shared base for Henan university HR portals powered by Boda CMS (博达网站群)."""

    detail_title_selectors = ("h1", "h3", "meta[name='pageTitle']", "title")
    detail_content_selectors = ("#vsb_content", ".v_news_content", ".contentr-con-con", ".sub_main_content")
    detail_path_pattern = re.compile(r"(/info/\d+/\d+\.htm|/nry\.jsp)", re.IGNORECASE)


class LongsiGovAdapter(HenanGovBaseAdapter):
    """Shared base for municipal portals powered by Longsi CMS (龙思政务)."""

    list_container_selectors = ("ul.doc_list li", ".doc_list li")
    detail_path_pattern = re.compile(r"/\d+\.html$", re.IGNORECASE)
    detail_title_selectors = ("h1.ls-article-title", "meta[name='ArticleTitle']", "h1", "title")
    detail_content_selectors = (".ls-article-info", ".j-fontContent", ".article-content", ".content")


class TrsWcmGovAdapter(HenanGovBaseAdapter):
    """Shared base for government portals powered by TRS WCM (拓尔思)."""

    allow_two_digit_list_dates = True
    detail_path_pattern = re.compile(r"/t\d+(?:_\d+)?\.html$", re.IGNORECASE)
    detail_title_selectors = ("div.title .h1", "h1", "meta[name='ArticleTitle']", "title")
    detail_content_selectors = ("div.view.TRS_UEDITOR", "div.TRS_UEDITOR", ".trs_paper_default", "#zoom", ".article-content")

    def _extract_stub_title_and_date(self, a_tag: Any) -> tuple[str, str | None]:
        em = a_tag.select_one("em")
        span = a_tag.select_one("span")
        pub_date = _extract_date_near_link(em or span or a_tag, allow_two_digit_year=self.allow_two_digit_list_dates)
        raw_title = a_tag.get("title")
        if not raw_title:
            raw_title = a_tag.get_text(" ", strip=True)
            raw_title = _TRAILING_DATE_RE.sub("", raw_title)
        title = re.sub(r"\s+", " ", str(raw_title)).strip()
        return title, pub_date


class HenanHrssAdapter(HenanGovBaseAdapter):
    """Adapter for Henan Provincial Department of Human Resources and Social Security."""

    adapter_name = "henan_hrss"
    list_container_selectors = (".news-list li", ".list-con li", ".article-list li", ".ty-list li")
    detail_title_selectors = (
        "h1",
        ".art-tit",
        ".article-title",
        ".trs_title",
        ".title",
        "meta[name='ArticleTitle']",
        "title",
    )
    detail_content_selectors = (
        ".article-content",
        ".art-con",
        "#artContent",
        ".trs_editor_view",
        "#zoom",
        ".content",
        "article",
        "main",
    )


class HenanRsksAdapter(HenanGovBaseAdapter):
    """Adapter for Henan Personnel Examination Center (www.hnrsks.com)."""

    adapter_name = "henan_rsks"
    list_container_selectors = ("ul#articleListTable li", "ul.list_news_dl li")
    detail_title_selectors = (".hnks-wzbt", "meta[name='ArticleTitle']", "h1", "title")
    detail_content_selectors = (".hnks-wznr", "#content", ".article-content", ".content")
    title_suffix_pattern = re.compile(r"_河南省人事考试中心$")


class HenanLuoyangRsksAdapter(HenanGovBaseAdapter):
    """Adapter for Luoyang Personnel Examination Center (www.lysrsks.cn)."""

    adapter_name = "henan_luoyang_rsks"
    list_container_selectors = ("ul.newslist li",)
    detail_title_selectors = ("h1", ".title", "title")
    detail_content_selectors = ("#content", ".main_txt", ".article-content")

    def should_keep_title(self, title: str, *, page_url: str = "") -> bool:
        if not self.filter_by_keywords:
            return True
        # classfiy=45 ("招聘转载") mixes other-province civil service announcements
        allow_external = "classfiy=45" not in page_url
        return is_recruitment_announcement(title, allow_out_of_province=allow_external)


class HenanXuchangRsjAdapter(EpointGovAdapter):
    """Adapter for Xuchang HRSS Bureau (rsj.xuchang.gov.cn)."""

    adapter_name = "henan_xuchang_rsj"
    require_date_or_pattern = re.compile(r"/zxdt/\d+/\d{8}/[0-9a-f-]+\.html$", re.IGNORECASE)


class HenanZhoukouRsksAdapter(HenanGovBaseAdapter):
    """Adapter for Zhoukou Personnel Examination Center (www.zkrsks.com JSON API)."""

    adapter_name = "henan_zhoukou_rsks"
    detail_title_selectors = ("h1", ".zk_contit", ".title", "title")
    detail_content_selectors = (".article-content", ".zk_nr", "#articleContent", ".content")

    def parse_list_page(self, html: str, page_url: str) -> list[AnnouncementStub]:
        raw = (html or "").strip()
        if raw.startswith("{"):
            try:
                payload = json.loads(raw)
                items = payload.get("body", {}).get("data") or []
            except (json.JSONDecodeError, AttributeError):
                items = []

            stubs: list[AnnouncementStub] = []
            seen_urls: set[str] = set()
            base_host = urlparse(self.base_url).netloc.lower()

            for item in items:
                if not isinstance(item, dict):
                    continue
                title = re.sub(r"\s+", " ", str(item.get("contentTitle") or "")).strip()
                if len(title) < 6 or not self.should_keep_title(title, page_url=page_url):
                    continue

                raw_url = str(item.get("contentUrl") or item.get("htmlUrl") or "").strip()
                if raw_url:
                    full_url = urljoin(self.base_url, raw_url)
                    if base_host and urlparse(full_url).netloc.lower() != base_host:
                        continue
                else:
                    content_num = str(item.get("contentNum") or "").strip()
                    if not content_num:
                        continue
                    full_url = f"{self.base_url.rstrip('/')}/new.html?contentNum={content_num}"

                if _is_direct_binary_url(full_url) or full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                rel_time = str(item.get("releaseTimeValue") or "").strip()
                pub_date = rel_time[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", rel_time) else None
                stubs.append(AnnouncementStub(url=full_url, title=title, published_at=pub_date))

            return stubs

        return super().parse_list_page(html, page_url)

    def fetch_detail_html(self, stub: AnnouncementStub) -> tuple[str, str, str | None, str | None]:
        parsed = urlparse(stub.url)
        qs = parse_qs(parsed.query)
        content_nums = qs.get("contentNum")
        if content_nums and parsed.path.endswith("new.html"):
            content_num = content_nums[0]
            api_url = f"{self.base_url.rstrip('/')}/zkrswz/zkrsksw/website/getContentInfo"
            resp = self.http_client.get(
                api_url,
                params={"contentNum": content_num, "systemClassify": "3"},
            )
            payload = json.loads(resp.text)
            data = payload.get("body", {}).get("data") or {}
            title = re.sub(r"\s+", " ", str(data.get("contentTitle") or stub.title)).strip()
            rel_time = str(data.get("releaseTimeValue") or "").strip()
            pub_date = rel_time[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", rel_time) else stub.published_at
            content_html = str(data.get("contentInfo") or "")
            source_org = html_lib.escape(str(data.get("contentSource") or ""))
            wrapped_html = (
                "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
                f"<title>{html_lib.escape(title)}</title></head><body>"
                f"<h1>{html_lib.escape(title)}</h1>"
                f"<div class='meta'>发布时间：{html_lib.escape(pub_date or '')} 来源：{source_org}</div>"
                f"<div class='article-content'>{content_html}</div>"
                "</body></html>"
            )
            return stub.url, wrapped_html, title, pub_date

        return super().fetch_detail_html(stub)

    def clean_html_result(
        self,
        html_res: HtmlExtractionResult,
        stub: AnnouncementStub,
    ) -> HtmlExtractionResult:
        # Filter stray UEditor placeholder anchors (e.g. "1.", ".", "3.") pointing to outdated template files
        named_atts = [
            att
            for att in html_res.attachments
            if not re.fullmatch(r"[\d.\s、]+", att.name or "")
            and not (att.name or "").strip().lower().startswith(("www.", "http://", "https://"))
        ]
        if named_atts:
            html_res.attachments = named_atts
        return html_res


class HenanHrssYwzlAdapter(HenanGovBaseAdapter):
    """Adapter for Henan HRSS Business Column public-institution recruitment (ywzl.hrss.henan.gov.cn)."""

    adapter_name = "henan_hrss_ywzl"
    list_container_selectors = ("td.xin2zuo table tr",)
    detail_title_selectors = ("div.title7", ".title7", "h1", "meta[name='ArticleTitle']", "title")
    detail_content_selectors = ("#zwxinx", "td.context6", ".article-content", ".content")
    detail_path_pattern = re.compile(r"/sitegroup/root/html/[^/]+/[0-9a-f]+\.html$", re.IGNORECASE)


class HenanZhengzhouRsjAdapter(HenanGovBaseAdapter):
    """Adapter for Zhengzhou HRSS Bureau recruitment column (zzrs.zhengzhou.gov.cn)."""

    adapter_name = "henan_zhengzhou_rsj"
    allowed_hosts = ("zzrs.zhengzhou.gov.cn", "public.zhengzhou.gov.cn")
    list_container_selectors = ("ul.news-list",)
    detail_title_selectors = (".big-title", ".content-title", "meta[name='ArticleTitle']", "h1", "title")
    detail_content_selectors = (".news_content_content", ".content-txt", "#zoom", ".article-content")
    detail_path_pattern = re.compile(r"/\d+\.jhtml$", re.IGNORECASE)

    def _extract_stub_title_and_date(self, a_tag: Any) -> tuple[str, str | None]:
        span = a_tag.select_one("span")
        em = a_tag.select_one("em")
        raw_title = a_tag.get("title") or (span.get_text(" ", strip=True) if span else a_tag.get_text(" ", strip=True))
        title = re.sub(r"\s+", " ", str(raw_title)).strip()
        pub_date = _extract_date_near_link(em or a_tag)
        return title, pub_date


class HenanPingdingshanRsjAdapter(HenanGovBaseAdapter):
    """Adapter for Pingdingshan HRSS Bureau recruitment column (rsj.pds.gov.cn)."""

    adapter_name = "henan_pingdingshan_rsj"
    list_container_selectors = ("ul.channel-list li",)
    detail_title_selectors = ("h1.title", "meta[name='ArticleTitle']", "h1", "title")
    detail_content_selectors = ("div.article", ".content", "#Content")
    detail_path_pattern = re.compile(r"/contents/\d+/\d+\.html$", re.IGNORECASE)


class HenanXinxiangRsjAdapter(LongsiGovAdapter):
    """Adapter for Xinxiang HRSS Bureau (hrss.xinxiang.gov.cn)."""

    adapter_name = "henan_xinxiang_rsj"
    title_suffix_pattern = re.compile(r"_新乡市人力资源和社会保障局$")


class HenanSanmenxiaRsjAdapter(HenanGovBaseAdapter):
    """Adapter for Sanmenxia HRSS Bureau (rsj.smx.gov.cn)."""

    adapter_name = "henan_sanmenxia_rsj"
    list_container_selectors = ("div.article ul li", "ul.onewz li")
    detail_path_pattern = re.compile(r"/\d+/20\d{2}/\d+/\d+\.html$", re.IGNORECASE)
    detail_title_selectors = ("p.title", "h1", "title")
    detail_content_selectors = ("div.nn", ".article-content", "#zoom", ".content")

    def _extract_stub_title_and_date(self, a_tag: Any) -> tuple[str, str | None]:
        qians = a_tag.select_one("span.qians")
        hous = a_tag.select_one("span.hous")
        pub_date = _extract_date_near_link(hous or a_tag)
        raw_title = a_tag.get("title") or (qians.get_text(" ", strip=True) if qians else a_tag.get_text(" ", strip=True))
        title = _TRAILING_DATE_RE.sub("", re.sub(r"\s+", " ", str(raw_title)).strip())
        title = _LEADING_BADGE_RE.sub("", title).strip()
        return title, pub_date

    def clean_html_result(
        self,
        html_res: HtmlExtractionResult,
        stub: AnnouncementStub,
    ) -> HtmlExtractionResult:
        if html_res.title and html_res.title.strip() == "首页轮播":
            html_res.title = ""
        return super().clean_html_result(html_res, stub)


class HenanShangqiuRsksAdapter(HenanGovBaseAdapter):
    """Adapter for Shangqiu Personnel Examination Center (www.sqrsks.cn)."""

    adapter_name = "henan_shangqiu_rsks"
    list_container_selectors = ("div.list_div ul li",)
    detail_url_pattern = re.compile(r"index/show/index&id=\d+", re.IGNORECASE)
    detail_title_selectors = (".contents h1", "h1", "title")
    detail_content_selectors = (".contents", ".conrights", "#content")
    title_suffix_pattern = re.compile(r"-商丘市人事考试中心\|商丘人事考试网$")


class HenanJiyuanRsjAdapter(TrsWcmGovAdapter):
    """Adapter for Jiyuan Demonstration Zone HRSS Bureau (rbj.jiyuan.gov.cn)."""

    adapter_name = "henan_jiyuan_rsj"
    list_container_selectors = ("ul.news li", "div.list-text div.bd")
    title_suffix_pattern = re.compile(r"[-_]\s*济源(?:人事考试_)?济源产城融合示范区人力资源和社会保障局$")


class HenanHautRscAdapter(BodaUniversityAdapter):
    """Adapter for Henan University of Technology HR Office (rsc.haut.edu.cn)."""

    adapter_name = "henan_haut_rsc"
    list_container_selectors = ("div.sub_main_list li",)
    detail_title_selectors = ("h1", "meta[name='pageTitle']", "title")
    detail_content_selectors = ("#vsb_content", ".v_news_content", ".sub_main_content")
    title_suffix_pattern = re.compile(r"-河南工业大学人事处$")

    def should_keep_title(self, title: str, *, page_url: str = "") -> bool:
        if title.strip().startswith("["):
            return False
        return super().should_keep_title(title, page_url=page_url)


class HenanHaustRscAdapter(BodaUniversityAdapter):
    """Adapter for Henan University of Science and Technology HR Office (rsc.haust.edu.cn)."""

    adapter_name = "henan_haust_rsc"
    list_container_selectors = ("ul.article-list li",)
    detail_title_selectors = ("h3", "meta[name='pageTitle']", "h1", "title")
    detail_content_selectors = (".contentr-con-con", ".v_news_content", ".contentr-con")
    detail_path_pattern = re.compile(r"/info/\d+/\d+\.htm$", re.IGNORECASE)
    title_suffix_pattern = re.compile(r"-河南科技大学人事处$")
