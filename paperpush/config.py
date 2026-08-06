from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import yaml

from .models import FeedRule

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = ROOT_DIR / "config"


@dataclass
class WeChatSettings:
    enabled: bool = True
    sendkey_env: str = "SCT_SENDKEY"
    max_papers_per_day: int = 20
    title_prefix: str = "📚 文献速递"


@dataclass
class ZoteroSettings:
    enabled: bool = False
    api_key_env: str = "ZOTERO_API_KEY"
    user_id: int = 0
    collection_key: str = ""
    use_feed_collection: bool = False

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")


@dataclass
class Settings:
    lookback_days: int = 2
    max_papers_per_feed: int = 25
    storage: str = "data/seen.db"
    crossref_mailto: str = ""
    report_dir: str = "dist"
    report_url: str = ""  # 看板公开地址（用于微信推送中附链接），Actions 中自动填充
    exclude_doi_prefixes: list = field(default_factory=list)  # 需要排除的 DOI 前缀（新闻/评论）
    wechat: WeChatSettings = field(default_factory=WeChatSettings)
    zotero: ZoteroSettings = field(default_factory=ZoteroSettings)


def load_journals(config_dir: Path = DEFAULT_CONFIG_DIR) -> Dict[str, Dict[str, str]]:
    path = config_dir / "journals.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("journals", {})


def load_feeds(config_dir: Path = DEFAULT_CONFIG_DIR) -> List[FeedRule]:
    path = config_dir / "feeds.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    feeds = []
    for item in data.get("feeds", []):
        kw = item.get("keywords") or {}
        feeds.append(
            FeedRule(
                name=str(item.get("name", "unnamed")),
                journals=[str(j) for j in (item.get("journals") or [])],
                keyword_terms=[str(t) for t in (kw.get("terms") or [])],
                keyword_match=str(kw.get("match", "any")),
                keyword_fields=[str(f) for f in (kw.get("fields") or ["title", "abstract"])],
                exclude_terms=[str(t) for t in (kw.get("exclude") or [])],
                first_authors=[str(a) for a in (item.get("first_authors") or [])],
                last_authors=[str(a) for a in (item.get("last_authors") or [])],
                author_match=str(item.get("author_match", "any")),
            )
        )
    return feeds


def load_settings(config_dir: Path = DEFAULT_CONFIG_DIR) -> Settings:
    path = config_dir / "settings.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    s = data.get("settings", {})
    push = data.get("push", {}).get("wechat", {})
    zotero = data.get("zotero", {})
    return Settings(
        lookback_days=int(s.get("lookback_days", 2)),
        max_papers_per_feed=int(s.get("max_papers_per_feed", 25)),
        storage=str(s.get("storage", "data/seen.db")),
        crossref_mailto=str(s.get("crossref_mailto", "")),
        report_dir=str(s.get("report_dir", "dist")),
        report_url=str(s.get("report_url", "")),
        exclude_doi_prefixes=[str(x) for x in s.get("exclude_doi_prefixes", [])],
        wechat=WeChatSettings(
            enabled=bool(push.get("enabled", True)),
            sendkey_env=str(push.get("sendkey_env", "SCT_SENDKEY")),
            max_papers_per_day=int(push.get("max_papers_per_day", 20)),
            title_prefix=str(push.get("title_prefix", "📚 文献速递")),
        ),
        zotero=ZoteroSettings(
            enabled=bool(zotero.get("enabled", False)),
            api_key_env=str(zotero.get("api_key_env", "ZOTERO_API_KEY")),
            user_id=int(zotero.get("user_id", 0)),
            collection_key=str(zotero.get("collection_key", "")),
            use_feed_collection=bool(zotero.get("use_feed_collection", False)),
        ),
    )
