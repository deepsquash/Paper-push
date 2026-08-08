"""PaperPush 核心流程：抓取 → 匹配 → 去重 → 推送 → 报告。

main.py（CLI）与 Web 应用（app.py）共用；Web 端通过 run_once 的日志回调收集实时日志。
"""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import matcher, storage
from .config import load_feeds, load_journals, load_settings
from .models import Paper
from .library import Library
from .push.report import render_report
from .push.wechat import push_wechat
from .sources import crossref, pubmed
from .sources import biorxiv

log = logging.getLogger("paperpush")

# Web 端内存环形日志（最多保留 800 行）
_LOG_BUFFER: List[str] = []
_LOG_LOCK = threading.Lock()
MAX_LOG = 800


class BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        with _LOG_LOCK:
            _LOG_BUFFER.append(line)
            if len(_LOG_BUFFER) > MAX_LOG:
                del _LOG_BUFFER[: len(_LOG_BUFFER) - MAX_LOG]


def get_logs(since: int = 0) -> Dict[str, object]:
    with _LOG_LOCK:
        lines = _LOG_BUFFER[since:]
    return {"logs": lines, "total": len(_LOG_BUFFER)}


def _sort_key(p):
    return (p.published_online or "") or (p.published_print or "") or ""


def _paper_to_dict(p: Paper, m, is_new: bool) -> dict:
    return {
        "key": Library.key_for(p),
        "doi": p.doi,
        "title": p.title,
        "journal": p.journal,
        "published_online": p.published_online,
        "is_early_access": p.is_early_access,
        "is_new": is_new,
        "authors": [a.full_name for a in p.authors if a.full_name],
        "abstract": (p.abstract or "")[:2000],
        "url": p.url or f"https://doi.org/{p.doi}",
        "reasons": m.reasons,
    }


def evaluate_library(config_dir: str | Path = None, days: int = 180) -> dict:
    """保存 Feed 后立即用本地半年文献库重算首页，不等待下一次网络抓取。"""
    config_dir = Path(config_dir) if config_dir else Path(__file__).resolve().parent.parent / "config"
    settings = load_settings(config_dir)
    feeds = load_feeds(config_dir)
    library = Library(Path(settings.storage).with_name("library.db"))
    try:
        papers = library.papers_as_models(days=days)
        reactions = library.reaction_map(Library.key_for(p) for p in papers)
        details = {}
        counts = {}
        for feed in feeds:
            items = []
            for paper in papers:
                match = matcher.match_paper(paper, feed)
                if match:
                    item = _paper_to_dict(paper, match, False)
                    item["reaction"] = reactions.get(item["key"], "")
                    if item["reaction"] != "hidden":
                        items.append(item)
            items.sort(key=lambda item: item.get("published_online") or "", reverse=True)
            details[feed.name] = items[: settings.max_papers_per_feed]
            counts[feed.name] = len(items)
        return {"ok": True, "details": details, "feeds": counts, "library_total": len(papers)}
    finally:
        library.close()


def run_once(
    config_dir: str | Path = None,
    *,
    push: bool = True,
    report: bool = True,
    since_days: Optional[int] = None,
    log_ctx: Optional[dict] = None,
    callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """执行一次完整流程。config_dir 缺省时使用仓库内 config/。

    callback(level, message) 用于 Web 端实时日志；log_ctx 记录当前 run 状态。
    返回统计信息字典。
    """
    config_dir = Path(config_dir) if config_dir else Path(__file__).resolve().parent.parent / "config"
    settings = load_settings(config_dir)
    journals = load_journals(config_dir)
    feeds = load_feeds(config_dir)
    result = {
        "ok": True,
        "fetched": 0,
        "early": 0,
        "new": 0,
        "pushed": False,
        "error": "",
        "feeds": {},
        "details": {},
    }
    library = Library(Path(settings.storage).with_name("library.db"))

    def info(msg: str) -> None:
        log.info(msg)
        if callback:
            callback("info", msg)
        if log_ctx:
            log_ctx["status"] = msg

    def warn(msg: str) -> None:
        log.warning(msg)
        if callback:
            callback("warn", msg)

    if not feeds:
        warn("feeds.yaml 中没有配置任何 feed，请在网页的 Feed 管理中添加")
        result["ok"] = False
        result["error"] = "no feeds configured"
        return result

    since_days = since_days or settings.lookback_days
    since = date.today() - timedelta(days=since_days)
    info(f"PaperPush 启动：回看 {since_days} 天（{since.isoformat()} 起），"
         f"{len(journals)} 个期刊，{len(feeds)} 个 feed")

    # 1. Crossref 抓取（含提前在线文章）
    issns_by_journal = {
        name: [v["print"]] + ([v["online"]] if v.get("online") else [])
        for name, v in journals.items() if v.get("source", "crossref") == "crossref" and v.get("print")
    }
    try:
        papers = crossref.fetch_recent(
            issns_by_journal,
            since,
            mailto=settings.crossref_mailto,
            excluded_doi_prefixes=settings.exclude_doi_prefixes or None,
        )
    except Exception as e:  # noqa: BLE001
        warn(f"Crossref 抓取失败：{e}")
        result["ok"] = False
        result["error"] = f"crossref: {e}"
        return result
    result["fetched"] = len(papers)
    result["early"] = sum(1 for p in papers if p.is_early_access)
    info(f"Crossref 抓到 {len(papers)} 篇（其中提前在线 {result['early']} 篇）")
    if not papers:
        warn("没有抓到任何文章，请检查网络/ISSN 配置")

    # bioRxiv neuroscience 与期刊文章统一进入本地文献库。
    try:
        preprints = biorxiv.fetch(since, date.today(), "neuroscience", max_pages=5)
        papers.extend(preprints)
        info(f"bioRxiv · Neuroscience 抓到 {len(preprints)} 篇")
    except Exception as e:  # noqa: BLE001
        warn(f"bioRxiv 抓取失败（不影响期刊文章）：{e}")
    library.upsert([p for p in papers if p.journal != "bioRxiv · Neuroscience"], source="crossref")
    library.upsert([p for p in papers if p.journal == "bioRxiv · Neuroscience"], source="biorxiv", category="neuroscience")
    result["fetched"] = len(papers)
    result["early"] = sum(1 for p in papers if p.is_early_access)

    # 2. 摘要补充 + fulltext 通道预计算
    try:
        pubmed.enrich_abstracts(papers)
    except Exception as e:  # noqa: BLE001
        warn(f"PubMed 摘要补充失败（不影响主流程）：{e}")
    ft_dois_by_feed = {}
    for feed in feeds:
        if "fulltext" in feed.keyword_fields and feed.keyword_terms:
            jks = feed.journals or list(journals.keys())
            try:
                hits = pubmed.fulltext_hit_dois(feed.keyword_terms, jks)
                info(f"feed『{feed.name}』PMC 全文命中 {len(hits)} 篇")
                ft_dois_by_feed[feed.name] = hits
            except Exception as e:  # noqa: BLE001
                warn(f"feed『{feed.name}』全文搜索失败：{e}")

    # 3. feed 匹配
    feed_results: dict = {f.name: [] for f in feeds}
    for paper in papers:
        for feed in feeds:
            match = matcher.match_paper(paper, feed, ft_dois_by_feed.get(feed.name))
            if match:
                feed_results[feed.name].append((paper, match))

    for name, items in feed_results.items():
        items.sort(key=lambda t: _sort_key(t[0]), reverse=True)
        feed_results[name] = items[: settings.max_papers_per_feed]
        result["feeds"][name] = len(items)
        info(f"feed『{name}』命中 {len(items)} 篇")
    matched_dois = [p.doi for items in feed_results.values() for p, _ in items]

    # 4. 去重 + 推送
    store = storage.Storage(settings.storage)
    new_dois = store.record(matched_dois)
    result["new"] = len(new_dois)
    info(f"新增（首次出现）{len(new_dois)} 篇")
    for name, items in feed_results.items():
        result["details"][name] = [
            _paper_to_dict(p, m, p.doi.lower() in new_dois) for p, m in feed_results[name]
        ]
    reactions = library.reaction_map(
        item["key"] for items in result["details"].values() for item in items
    )
    for items in result["details"].values():
        for item in items:
            item["reaction"] = reactions.get(item["key"], "")
    # 已标记为“不感兴趣”的文章不再出现在 Feed 看板。
    result["details"] = {
        name: [item for item in items if item.get("reaction") != "hidden"]
        for name, items in result["details"].items()
    }

    new_feed_results = {
        name: [(p, m) for p, m in items if p.doi.lower() in new_dois]
        for name, items in feed_results.items()
        if any(p.doi.lower() in new_dois for p, _ in items)
    }

    report_url = ""
    if report:
        index = render_report(feed_results, settings.report_dir, date.today(), new_dois)
        info(f"看板已生成：{index}")

    if push and new_feed_results:
        result["pushed"] = push_wechat(
            settings.wechat,
            [(name, [p for p, _ in items]) for name, items in new_feed_results.items()],
            report_url=report_url,
        )
        if result["pushed"]:
            info("微信推送成功")

    # 5. Zotero 自动添加（可选）
    if settings.zotero.enabled and new_feed_results:
        try:
            from . import zotero

            coll_key = settings.zotero.collection_key
            coll_map = {}
            if settings.zotero.use_feed_collection:
                coll_map = {c["data"]["name"]: c["key"] for c in zotero.list_collections(settings.zotero)}
            added = 0
            for name, items in new_feed_results.items():
                key = coll_map.get(name, coll_key)
                for paper, _ in items:
                    item_key = zotero.add_paper(settings.zotero, paper.doi, name)
                    if item_key:
                        added += 1
                        if key and not zotero.add_to_collection(settings.zotero, item_key, key):
                            warn(f"加入 collection 失败：{paper.doi}")
            info(f"Zotero 自动添加完成：{added} 篇")
        except Exception as e:  # noqa: BLE001
            warn(f"Zotero 自动添加失败：{e}")

    store.close()
    library.close()
    info("本轮完成")
    return result
