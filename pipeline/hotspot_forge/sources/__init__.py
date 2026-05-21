"""三层信号源。每个源是一个 fetch_*() 函数，返回 list[HotspotSignal]。
内部 catch 失败——单源挂掉不影响其他源。
"""
from .global_longtail import fetch_global_longtail
from .hotsearch_cn import fetch_hotsearch_cn
from .rss_finance import fetch_rss_finance

__all__ = ["fetch_rss_finance", "fetch_hotsearch_cn", "fetch_global_longtail"]
