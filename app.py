#!/usr/bin/env python3
"""PaperPush 网页端完整版。

全部功能在浏览器完成：看板、Feed 管理、期刊管理、设置、手动运行（实时日志）、
Zotero 一键添加；内置每日 09:00（北京时间）定时任务。

用法:
  python app.py [--port 8080]
或双击 start.bat。

首次使用：浏览器打开 http://localhost:8080 → 设置页填写 Server酱 SendKey 与
Zotero API key → Feed 管理添加规则 → 运行页点「立即运行」。
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml
from flask import Flask, jsonify, request, send_from_directory

from paperpush.config import ROOT_DIR
from paperpush import pipeline
from paperpush.pipeline import get_logs

log = logging.getLogger("paperpush.app")
CONFIG_DIR = ROOT_DIR / "config"
FEEDS_PATH = CONFIG_DIR / "feeds.yaml"
JOURNALS_PATH = CONFIG_DIR / "journals.yaml"
SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
RUNS_DB = ROOT_DIR / "data" / "runs.db"

CST = timezone(timedelta(hours=8))  # 北京时间（无需 tzdata）
SCHEDULE_HOUR = 9  # 每日 09:00 北京时间

app = Flask(__name__, static_folder="web", static_url_path="/")

# ---- 运行状态 ----
RUN_STATE = {
    "running": False,
    "source": "",
    "started_at": "",
    "last_result": None,
}
_RUN_LOCK = threading.Lock()


# ================= 工具 =================

def _init_runs_db() -> None:
    (ROOT_DIR / "data").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(RUNS_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            source TEXT,
            result TEXT
        )"""
    )
    conn.commit()
    conn.close()


def _save_run(started_at: str, source: str, result: dict) -> None:
    try:
        conn = sqlite3.connect(RUNS_DB)
        conn.execute(
            "INSERT INTO runs (started_at, finished_at, source, result) VALUES (?, ?, ?, ?)",
            (
                started_at,
                datetime.now().isoformat(timespec="seconds"),
                source,
                json.dumps(result, ensure_ascii=False),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001
        log.warning("保存运行记录失败：%s", e)


def _latest_run() -> Optional[dict]:
    try:
        conn = sqlite3.connect(RUNS_DB)
        row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row[0],
            "started_at": row[1],
            "finished_at": row[2],
            "source": row[3],
            "result": json.loads(row[4]),
        }
    except Exception:  # noqa: BLE001
        return None


def _run_history(limit: int = 20) -> list:
    try:
        conn = sqlite3.connect(RUNS_DB)
        rows = conn.execute(
            "SELECT id, started_at, finished_at, source, result FROM runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        out = []
        for r in rows:
            try:
                res = json.loads(r[4])
            except Exception:  # noqa: BLE001
                res = {}
            out.append({
                "id": r[0], "started_at": r[1], "finished_at": r[2], "source": r[3],
                "fetched": res.get("fetched", 0), "new": res.get("new", 0),
                "ok": res.get("ok", False),
                "feed_counts": res.get("feeds", {}),
            })
        return out
    except Exception:  # noqa: BLE001
        return []


def _start_run(source: str = "manual") -> bool:
    """后台线程执行一次完整流程，返回是否成功启动。"""
    with _RUN_LOCK:
        if RUN_STATE["running"]:
            return False
        RUN_STATE["running"] = True
        RUN_STATE["source"] = source
        RUN_STATE["started_at"] = datetime.now().isoformat(timespec="seconds")
        RUN_STATE["last_result"] = None

    def worker():
        try:
            result = pipeline.run_once(
                CONFIG_DIR,
                push=True,
                report=True,
                callback=lambda level, msg: None,
            )
            result["finished_at"] = datetime.now().isoformat(timespec="seconds")
        except Exception as e:  # noqa: BLE001
            result = {
                "ok": False, "fetched": 0, "early": 0, "new": 0, "pushed": False,
                "error": str(e), "feeds": {}, "details": {},
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            }
            log.exception("运行异常")
        with _RUN_LOCK:
            RUN_STATE["last_result"] = result
            RUN_STATE["running"] = False
        _save_run(RUN_STATE["started_at"], source, result)

    threading.Thread(target=worker, daemon=True, name="paperpush-run").start()
    return True


def _scheduler_loop() -> None:
    """每日 SCHEDULE_HOUR 点（北京时间）自动运行。"""
    log.info("定时任务已启动：每日 %02d:00（北京时间）", SCHEDULE_HOUR)
    while True:
        now = datetime.now(CST)
        target = now.replace(hour=SCHEDULE_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        secs = (target - now).total_seconds()
        log.info("下次自动运行：%s（%.1f 小时后）", target.isoformat(), secs / 3600)
        time.sleep(secs)
        log.info("定时任务触发")
        _start_run(source="scheduled")


# ================= 配置读写 =================

def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def _normalize_feed(f: dict) -> dict:
    """把前端传来的 feed 规范化（补默认值），与 matcher 兼容。"""
    kw = f.get("keywords") or {}
    out = {"name": str(f.get("name", "")).strip()}
    if f.get("journals"):
        out["journals"] = [str(j) for j in f["journals"]]
    terms = [str(t).strip() for t in (kw.get("terms") or []) if str(t).strip()]
    exclude = [str(t).strip() for t in (kw.get("exclude") or []) if str(t).strip()]
    if terms or exclude:
        out["keywords"] = {"terms": terms}
        if exclude:
            out["keywords"]["exclude"] = exclude
        if kw.get("match") == "all":
            out["keywords"]["match"] = "all"
        fields = [str(x) for x in (kw.get("fields") or []) if str(x) in ("title", "abstract", "fulltext")]
        if terms and fields:
            out["keywords"]["fields"] = fields
    fa = [str(a).strip() for a in (f.get("first_authors") or []) if str(a).strip()]
    la = [str(a).strip() for a in (f.get("last_authors") or []) if str(a).strip()]
    if fa:
        out["first_authors"] = fa
    if la:
        out["last_authors"] = la
    if fa or la:
        out["author_match"] = "all" if f.get("author_match") == "all" else "any"
    return out


def _feeds_with_defaults(feeds: list) -> list:
    """GET 返回规范化结构（补默认字段，前端无需防御 undefined）。"""
    out = []
    for f in feeds:
        kw = f.get("keywords") or {}
        out.append({
            "name": f.get("name", ""),
            "journals": [str(j) for j in (f.get("journals") or [])],
            "keywords": {
                "terms": [str(t) for t in (kw.get("terms") or [])],
                "match": kw.get("match", "any"),
                "fields": [str(x) for x in (kw.get("fields") or ["title", "abstract"])],
                "exclude": [str(t) for t in (kw.get("exclude") or [])],
            },
            "first_authors": [str(a) for a in (f.get("first_authors") or [])],
            "last_authors": [str(a) for a in (f.get("last_authors") or [])],
            "author_match": f.get("author_match", "any"),
        })
    return out


def _settings_dict() -> dict:
    s = _read_yaml(SETTINGS_PATH)
    settings = s.get("settings", {})
    push = s.get("push", {}).get("wechat", {})
    zotero = s.get("zotero", {})
    return {
        "lookback_days": settings.get("lookback_days", 2),
        "max_papers_per_feed": settings.get("max_papers_per_feed", 25),
        "crossref_mailto": settings.get("crossref_mailto", ""),
        "exclude_doi_prefixes": settings.get("exclude_doi_prefixes", []),
        "wechat": {
            "enabled": bool(push.get("enabled", True)),
            "sendkey": push.get("sendkey", ""),
            "max_papers_per_day": push.get("max_papers_per_day", 20),
            "title_prefix": push.get("title_prefix", "📚 文献速递"),
        },
        "zotero": {
            "enabled": bool(zotero.get("enabled", False)),
            "api_key": zotero.get("api_key", ""),
            "user_id": int(zotero.get("user_id", 0)),
            "collection_key": zotero.get("collection_key", ""),
            "use_feed_collection": bool(zotero.get("use_feed_collection", False)),
        },
    }


def _save_settings(payload: dict) -> None:
    w = payload.get("wechat") or {}
    z = payload.get("zotero") or {}
    data = {
        "settings": {
            "lookback_days": int(payload.get("lookback_days", 2)),
            "max_papers_per_feed": int(payload.get("max_papers_per_feed", 25)),
            "crossref_mailto": str(payload.get("crossref_mailto", "")),
            "exclude_doi_prefixes": [str(x) for x in (payload.get("exclude_doi_prefixes") or [])],
            "report_dir": "dist",
            "storage": "data/seen.db",
            "report_url": "",
        },
        "push": {
            "wechat": {
                "enabled": bool(w.get("enabled", True)),
                "sendkey_env": "SCT_SENDKEY",
                "sendkey": str(w.get("sendkey", "")).strip(),
                "max_papers_per_day": int(w.get("max_papers_per_day", 20)),
                "title_prefix": str(w.get("title_prefix", "📚 文献速递")),
            }
        },
        "zotero": {
            "enabled": bool(z.get("enabled", False)),
            "api_key_env": "ZOTERO_API_KEY",
            "api_key": str(z.get("api_key", "")).strip(),
            "user_id": int(z.get("user_id", 0) or 0),
            "collection_key": str(z.get("collection_key", "")).strip(),
            "use_feed_collection": bool(z.get("use_feed_collection", False)),
        },
    }
    _write_yaml(SETTINGS_PATH, data)


# ================= API =================

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/state")
def api_state():
    with _RUN_LOCK:
        running = RUN_STATE["running"]
        source = RUN_STATE["source"]
        started = RUN_STATE["started_at"]
        last = RUN_STATE["last_result"]
    latest = _latest_run()
    return jsonify({
        "running": running,
        "source": source,
        "started_at": started,
        "last_result": last,
        "latest_run": latest,
        "history": _run_history(),
    })


@app.post("/api/run")
def api_run():
    if not _start_run("manual"):
        return jsonify({"ok": False, "message": "已有任务在运行中，请等待完成"}), 409
    return jsonify({"ok": True, "message": "已开始运行，请查看日志"})


@app.get("/api/logs")
def api_logs():
    since = int(request.args.get("since", 0) or 0)
    return jsonify(get_logs(since))


@app.get("/api/feeds")
def api_get_feeds():
    data = _read_yaml(FEEDS_PATH)
    return jsonify({"journals": list((_read_yaml(JOURNALS_PATH).get("journals") or {}).keys()),
                    "feeds": _feeds_with_defaults(data.get("feeds", []))})


@app.post("/api/feeds")
def api_save_feeds():
    payload = request.get_json(force=True)
    feeds = [_normalize_feed(f) for f in payload.get("feeds", [])]
    names = [f["name"] for f in feeds]
    if not all(names):
        return jsonify({"ok": False, "message": "存在未命名 Feed，请填写名称"}), 400
    if len(set(names)) != len(names):
        return jsonify({"ok": False, "message": "Feed 名称重复"}), 400
    if not names:
        return jsonify({"ok": False, "message": "至少需要保留一个 Feed"}), 400
    _write_yaml(FEEDS_PATH, {"feeds": feeds})
    return jsonify({"ok": True, "message": f"已保存 {len(feeds)} 个 Feed，下次运行即生效"})


@app.get("/api/journals")
def api_get_journals():
    data = _read_yaml(JOURNALS_PATH)
    journals = data.get("journals", {})
    return jsonify({
        "journals": [
            {"name": k, "print": (v.get("print") or ""), "online": (v.get("online") or "")}
            for k, v in journals.items()
        ]
    })


@app.post("/api/journals")
def api_save_journals():
    payload = request.get_json(force=True)
    items = payload.get("journals", [])
    journals = {}
    for it in items:
        name = str(it.get("name", "")).strip()
        if not name:
            continue
        p = str(it.get("print", "")).strip()
        o = str(it.get("online", "")).strip()
        journals[name] = {"print": p, "online": o}
    if not journals:
        return jsonify({"ok": False, "message": "至少需要一个期刊"}), 400
    _write_yaml(JOURNALS_PATH, {"journals": journals})
    return jsonify({"ok": True, "message": f"已保存 {len(journals)} 个期刊"})


@app.get("/api/settings")
def api_get_settings():
    return jsonify(_settings_dict())


@app.post("/api/settings")
def api_save_settings():
    try:
        _save_settings(request.get_json(force=True))
        return jsonify({"ok": True, "message": "设置已保存"})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "message": str(e)}), 400


@app.post("/api/zotero/add")
def api_zotero_add():
    from paperpush import zotero
    from paperpush.config import load_settings

    payload = request.get_json(force=True)
    doi = str(payload.get("doi", "")).strip()
    feed_name = str(payload.get("feed", ""))
    if not doi:
        return jsonify({"ok": False, "message": "缺少 DOI"}), 400
    settings = load_settings(CONFIG_DIR)
    if not settings.zotero.enabled or not settings.zotero.effective_api_key:
        return jsonify({"ok": False, "message": "Zotero 未启用或未配置 API key（见「设置」页）"}), 400
    key = zotero.add_paper(settings.zotero, doi, feed_name)
    if not key:
        return jsonify({"ok": False, "message": "Zotero 添加失败，请查看日志"}), 502
    coll = settings.zotero.collection_key
    if settings.zotero.use_feed_collection:
        try:
            coll_map = {c["data"]["name"]: c["key"] for c in zotero.list_collections(settings.zotero)}
            coll = coll_map.get(feed_name, coll)
        except Exception:  # noqa: BLE001
            pass
    if coll:
        zotero.add_to_collection(settings.zotero, key, coll)
    return jsonify({"ok": True, "message": f"已添加到 Zotero{f'（{feed_name} 分组）' if feed_name else ''}"})


# ================= 启动 =================

def main() -> int:
    parser = argparse.ArgumentParser(description="PaperPush 网页端完整版")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-scheduler", action="store_true", help="不启动每日定时任务")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), pipeline.BufferHandler()],
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    _init_runs_db()
    if not args.no_scheduler:
        threading.Thread(target=_scheduler_loop, daemon=True, name="paperpush-scheduler").start()

    if not args.no_browser:
        import webbrowser
        webbrowser.open(f"http://localhost:{args.port}")

    log.info("PaperPush 网页版已启动：http://localhost:%d", args.port)
    log.info("每日 %02d:00（北京时间）自动运行；也可在「运行」页手动触发", SCHEDULE_HOUR)
    app.run(host="127.0.0.1", port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
