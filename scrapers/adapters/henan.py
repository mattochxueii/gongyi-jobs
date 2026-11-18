"""Henan Province provincial & municipal official recruitment adapters."""

from __future__ import annotations

import html as html_lib
import json
import re
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
    "拟安置",
    "拟加分",
    "体检",
    "体能",
    "复检",
    "考察",
    "政审",
    "成绩",
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
    "招聘会",
    "专场招聘",
    "双选会",
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
        if not any(loc in title for loc in ("河南", "洛阳", "郑州", "许昌", "周口", "平顶山", "新乡", "开封", "南阳")):
            if any(prov in title for prov in OUT_OF_PROVINCE_KEYWORDS):
                return False
            return False
    return True


def _is_direct_binary_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(_BINARY_SUFFIXES)


def _extract_date_near_link(a_tag: Any) -> str | None:
    if a_tag.parent is not None:
        text = a_tag.parent.get_text(" ", strip=True)
        m = _INLINE_DATE_RE.search(text)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


class HenanGovBaseAdapter(BaseScraperAdapter):
    """Shared base for Henan provincial and municipal recruitment adapters."""

    allow_out_of_province: bool = True

    def should_keep_title(self, title: str, *, page_url: str = "") -> bool:
        if not self.filter_by_keywords:
            return True
        return is_recruitment_announcement(title, allow_out_of_province=self.allow_out_of_province)

    def _collect_stubs_from_containers(
        self,
        soup: BeautifulSoup,
        page_url: str,
        *,
        container_selectors: tuple[str, ...],
        require_date_or_pattern: re.Pattern[str] | None = None,
    ) -> list[AnnouncementStub]:
        base_host = urlparse(self.base_url).netloc.lower()
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
            if base_host and parsed.netloc.lower() != base_host:
                continue
            if _is_direct_binary_url(full_url):
                continue

            title = re.sub(r"\s+", " ", str(a_tag.get("title") or a_tag.get_text(" ", strip=True))).strip()
            if len(title) < 6:
                continue

            pub_date = _extract_date_near_link(a_tag)
            if require_date_or_pattern and pub_date is None and not require_date_or_pattern.search(parsed.path):
                continue
            if not self.should_keep_title(title, page_url=page_url):
                continue
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            stubs.append(AnnouncementStub(url=full_url, title=title, published_at=pub_date))

        return stubs


class HenanHrssAdapter(HenanGovBaseAdapter):
    """Adapter for Henan Provincial Department of Human Resources and Social Security."""

    adapter_name = "henan_hrss"
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

    def parse_list_page(self, html: str, page_url: str) -> list[AnnouncementStub]:
        soup = BeautifulSoup(html or "", "html.parser")
        return self._collect_stubs_from_containers(
            soup,
            page_url,
            container_selectors=(".news-list li", ".list-con li", ".article-list li", ".ty-list li"),
        )


class HenanRsksAdapter(HenanGovBaseAdapter):
    """Adapter for Henan Personnel Examination Center (www.hnrsks.com)."""

    adapter_name = "henan_rsks"
    detail_title_selectors = (".hnks-wzbt", "meta[name='ArticleTitle']", "h1", "title")
    detail_content_selectors = (".hnks-wznr", "#content", ".article-content", ".content")

    def parse_list_page(self, html: str, page_url: str) -> list[AnnouncementStub]:
        soup = BeautifulSoup(html or "", "html.parser")
        return self._collect_stubs_from_containers(
            soup,
            page_url,
            container_selectors=("ul#articleListTable li", "ul.list_news_dl li"),
        )

    def clean_html_result(
        self,
        html_res: HtmlExtractionResult,
        stub: AnnouncementStub,
    ) -> HtmlExtractionResult:
        if html_res.title:
            cleaned = re.sub(r"_河南省人事考试中心$", "", html_res.title).strip()
            html_res.title = cleaned
        return html_res


class HenanLuoyangRsksAdapter(HenanGovBaseAdapter):
    """Adapter for Luoyang Personnel Examination Center (www.lysrsks.cn)."""

    adapter_name = "henan_luoyang_rsks"
    detail_title_selectors = ("h1", ".title", "title")
    detail_content_selectors = ("#content", ".main_txt", ".article-content")

    def should_keep_title(self, title: str, *, page_url: str = "") -> bool:
        if not self.filter_by_keywords:
            return True
        # classfiy=45 ("招聘转载") mixes other-province civil service announcements
        allow_external = "classfiy=45" not in page_url
        return is_recruitment_announcement(title, allow_out_of_province=allow_external)

    def parse_list_page(self, html: str, page_url: str) -> list[AnnouncementStub]:
        soup = BeautifulSoup(html or "", "html.parser")
        return self._collect_stubs_from_containers(
            soup,
            page_url,
            container_selectors=("ul.newslist li",),
        )


class HenanXuchangRsjAdapter(HenanGovBaseAdapter):
    """Adapter for Xuchang HRSS Bureau (rsj.xuchang.gov.cn)."""

    adapter_name = "henan_xuchang_rsj"
    detail_title_selectors = ("#detail-infotitle", "h1.title", "h1", "title")
    detail_content_selectors = (".details-box", ".details", ".article-content")

    _DETAIL_PATH_RE = re.compile(r"/zxdt/\d+/\d{8}/[0-9a-f-]+\.html$", re.IGNORECASE)

    def parse_list_page(self, html: str, page_url: str) -> list[AnnouncementStub]:
        soup = BeautifulSoup(html or "", "html.parser")
        # Remove pagination block so page numbers aren't inspected
        for pag in soup.select(".ewb-page"):
            pag.decompose()
        return self._collect_stubs_from_containers(
            soup,
            page_url,
            container_selectors=("ul li",),
            require_date_or_pattern=self._DETAIL_PATH_RE,
        )


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
