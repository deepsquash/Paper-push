#!/usr/bin/env python3
"""PaperPush 文献速递主程序。

用法:
  python main.py [--config-dir config] [--since-days N] [--no-push] [--no-report]

流程: Crossref 抓取(含提前在线) → PubMed 补摘要/全文索引 → feed 匹配
     → SQLite 去重 → 微信推送(新命中) + 网页看板(全部命中)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from paperpush import matcher, storage
from paperpush.config import load_feeds, load_journals, load_settings
from paperpush.push.report import render_report
from paperpush.push.wechat import push_wechat
from paperpush.sources import crossref, pubmed

log = logging.getLogger("paperpush")


def _setup_logging() -> None:
    # Windows 控制台为 GBK 时强制 UTF-8 输出，避免中文日志乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _sort_key(p):
    return (p.published_online or "") or (p.published_print or "") or ""


def main() -> int:
    parser = argparse.ArgumentParser(description="PaperPush 文献速递")
    parser.add_argument("--config-dir", default=str(Path(__file__).parent / "config"))
    parser.add_argument("--since-days", type=int, default=None, help="覆盖 settings 的回看天数")
    parser.add_argument("--no-push", action="store_true", help="跳过微信推送")
    parser.add_argument("--no-report", action="store_true", help="跳过网页看板生成")
    args = parser.parse_args()

    _setup_logging()
    config_dir = Path(args.config_dir)
    settings = load_settings(config_dir)
    journals = load_journals(config_dir)
    feeds = load_feeds(config_dir)

    if not feeds:
        log.error("feeds.yaml 中没有配置任何 feed")
        return 1

    since_days = args.since_days if args.since_days is not None else settings.lookback_days
    since = date.today() - timedelta(days=since_days)
    log.info("PaperPush 启动：回看 %d 天（%s 起），%d 个期刊，%d 个 feed",
             since_days, since.isoformat(), len(journals), len(feeds))

    # ---- 1. Crossref 抓取（含提前在线文章）----
    issns_by_journal = {
        name: [v["print"]] + ([v["online"]] if v.get("online") else [])
        for name, v in journals.items()
    }
    papers = crossref.fetch_recent(
        issns_by_journal,
        since,
        mailto=settings.crossref_mailto,
        excluded_doi_prefixes=settings.exclude_doi_prefixes or None,
    )
    log.info("Crossref 抓到 %d 篇（其中提前在线 %d 篇）",
             len(papers), sum(1 for p in papers if p.is_early_access))
    if not papers:
        log.warning("没有抓到任何文章，请检查网络/ISSN 配置")

    # ---- 2. 摘要补充 + fulltext 通道预计算 ----
    pubmed.enrich_abstracts(papers)
    ft_dois_by_feed = {}
    for feed in feeds:
        if "fulltext" in feed.keyword_fields and feed.keyword_terms:
            jks = feed.journals or list(journals.keys())
            try:
                hits = pubmed.fulltext_hit_dois(feed.keyword_terms, jks)
                log.info("feed『%s』PMC 全文命中 %d 篇", feed.name, len(hits))
                ft_dois_by_feed[feed.name] = hits
            except Exception as e:  # noqa: BLE001
                log.warning("feed『%s』全文搜索失败：%s", feed.name, e)

    # ---- 3. feed 匹配 ----
    feed_results: dict = {f.name: [] for f in feeds}
    for paper in papers:
        for feed in feeds:
            match = matcher.match_paper(paper, feed, ft_dois_by_feed.get(feed.name))
            if match:
                feed_results[feed.name].append((paper, match))

    for name, items in feed_results.items():
        items.sort(key=lambda t: _sort_key(t[0]), reverse=True)
        feed_results[name] = items[: settings.max_papers_per_feed]
        log.info("feed『%s』命中 %d 篇", name, len(items))
    matched_dois = [p.doi for items in feed_results.values() for p, _ in items]

    # ---- 4. 去重 + 推送 ----
    store = storage.Storage(settings.storage)
    new_dois = store.record(matched_dois)
    log.info("新增（首次出现）%d 篇", len(new_dois))

    new_feed_results = {
        name: [(p, m) for p, m in items if p.doi.lower() in new_dois]
        for name, items in feed_results.items()
        if any(p.doi.lower() in new_dois for p, _ in items)
    }

    report_url = settings.report_url
    pushed = False
    if not args.no_report:
        index = render_report(feed_results, settings.report_dir, date.today(), new_dois)
        if not report_url:
            log.info("看板已生成：%s（settings.yaml 配置 report_url 后微信推送会附上链接）", index)

    if not args.no_push and new_feed_results:
        pushed = push_wechat(
            settings.wechat,
            [(name, [p for p, _ in items]) for name, items in new_feed_results.items()],
            report_url=report_url,
        )

    # ---- 5. Zotero（本地模式，可选）----
    if settings.zotero.enabled and new_feed_results:
        from paperpush import zotero

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
                        log.warning("加入 collection 失败：%s", paper.doi)
        log.info("Zotero 添加完成：%d 篇", added)

    store.close()
    log.info("完成%s", "（已推送）" if pushed else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
