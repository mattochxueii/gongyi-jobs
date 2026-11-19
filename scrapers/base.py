"""Unified scraper adapter base class and registry."""

from __future__ import annotations

from scrapers.adapters.henan import (
    HenanGovBaseAdapter,
    HenanHaustRscAdapter,
    HenanHautRscAdapter,
    HenanHrssAdapter,
    HenanHrssYwzlAdapter,
    HenanLuoyangRsksAdapter,
    HenanPingdingshanRsjAdapter,
    HenanRsksAdapter,
    HenanXuchangRsjAdapter,
    HenanZhengzhouRsjAdapter,
    HenanZhoukouRsksAdapter,
    classify_announcement_title,
    is_recruitment_announcement,
)
from scrapers.adapters.saas import (
    DifyJobsAdapter,
    EmqJobsAdapter,
    FeishuHireAdapter,
    GreenhouseAdapter,
    LeverAdapter,
    MokaAdapter,
    SaasBaseAdapter,
)
from scrapers.core.base_adapter import (
    _INLINE_DATE_RE,
    RECRUIT_KEYWORDS,
    AnnouncementStub,
    BaseScraperAdapter,
    CrawlRunSummary,
)

ADAPTER_REGISTRY: dict[str, type[BaseScraperAdapter]] = {
    "generic_gov": BaseScraperAdapter,
    "henan_hrss": HenanHrssAdapter,
    "henan_hrss_ywzl": HenanHrssYwzlAdapter,
    "henan_rsks": HenanRsksAdapter,
    "henan_luoyang_rsks": HenanLuoyangRsksAdapter,
    "henan_xuchang_rsj": HenanXuchangRsjAdapter,
    "henan_zhoukou_rsks": HenanZhoukouRsksAdapter,
    "henan_zhengzhou_rsj": HenanZhengzhouRsjAdapter,
    "henan_pingdingshan_rsj": HenanPingdingshanRsjAdapter,
    "henan_haut_rsc": HenanHautRscAdapter,
    "henan_haust_rsc": HenanHaustRscAdapter,
    "feishu_hire": FeishuHireAdapter,
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "moka": MokaAdapter,
    "emq_jobs": EmqJobsAdapter,
    "dify_jobs": DifyJobsAdapter,
}


def get_adapter_class(adapter_name: str) -> type[BaseScraperAdapter]:
    """Return adapter class by name, defaulting to BaseScraperAdapter."""
    return ADAPTER_REGISTRY.get(adapter_name, BaseScraperAdapter)


__all__ = [
    "ADAPTER_REGISTRY",
    "AnnouncementStub",
    "BaseScraperAdapter",
    "CrawlRunSummary",
    "DifyJobsAdapter",
    "EmqJobsAdapter",
    "FeishuHireAdapter",
    "GreenhouseAdapter",
    "HenanGovBaseAdapter",
    "HenanHaustRscAdapter",
    "HenanHautRscAdapter",
    "HenanHrssAdapter",
    "HenanHrssYwzlAdapter",
    "HenanLuoyangRsksAdapter",
    "HenanPingdingshanRsjAdapter",
    "HenanRsksAdapter",
    "HenanXuchangRsjAdapter",
    "HenanZhengzhouRsjAdapter",
    "HenanZhoukouRsksAdapter",
    "LeverAdapter",
    "MokaAdapter",
    "RECRUIT_KEYWORDS",
    "SaasBaseAdapter",
    "_INLINE_DATE_RE",
    "classify_announcement_title",
    "get_adapter_class",
    "is_recruitment_announcement",
]
