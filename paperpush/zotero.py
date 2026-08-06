"""Zotero Web API：一键将论文（按 DOI）添加进 Zotero 条目库。

仅本地运行模式可用（需要 Zotero API key）；GitHub Actions 云端不配置，
网页看板使用 Zotero Connector 浏览器插件完成一键抓取。
"""
from __future__ import annotations

import logging
from typing import List

import requests

from .config import ZoteroSettings

log = logging.getLogger(__name__)

API = "https://api.zotero.org"


def add_paper(settings: ZoteroSettings, doi: str, feed_name: str = "") -> str:
    """通过 Zotero Translation Server 按 DOI 抓取并创建条目。

    返回新创建的 item key（字符串）；失败或未启用返回空字符串。
    """
    if not settings.enabled or not settings.effective_api_key:
        log.info("Zotero 未启用或缺少 API key，跳过添加 %s", doi)
        return ""
    headers = {"Zotero-API-Key": settings.effective_api_key, "Content-Type": "application/json"}
    item = {"contentType": "fulltext", "content": doi}
    try:
        resp = requests.post(f"{API}/users/{settings.user_id}/items", json=item, headers=headers, timeout=60)
        resp.raise_for_status()
        keys = resp.json().get("successful") or {}
        if keys:
            key = next(iter(keys.values()))
            log.info("Zotero 添加成功 %s -> %s", doi, key)
            return str(key)
        log.warning("Zotero 未返回创建结果：%s", resp.json())
        return ""
    except requests.RequestException as e:
        log.error("Zotero 添加失败 %s: %s", doi, e)
        return ""


def list_collections(settings: ZoteroSettings) -> List[dict]:
    """列出所有 collection 及其名称（用于按 feed 名匹配）。"""
    if not settings.effective_api_key:
        return []
    try:
        resp = requests.get(
            f"{API}/users/{settings.user_id}/collections",
            headers={"Zotero-API-Key": settings.effective_api_key},
            params={"limit": 100},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error("Zotero 获取 collection 失败: %s", e)
        return []


def add_to_collection(settings: ZoteroSettings, item_key: str, collection_key: str) -> bool:
    """将已创建条目加入指定 collection。"""
    try:
        resp = requests.post(
            f"{API}/users/{settings.user_id}/collections/{collection_key}/items",
            json=[{"itemKey": item_key}],
            headers={"Zotero-API-Key": settings.effective_api_key, "Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Zotero 添加 collection 失败: %s", e)
        return False
