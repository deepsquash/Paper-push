#!/usr/bin/env python3
"""本地 Web 编辑器：在浏览器中可视化增删改 feed 规则，保存后自动推送到 GitHub。

用法:
  python webadmin.py [--port 8765] [--no-push]

打开 http://localhost:8765 即可操作。保存 feed 后自动写入 config/feeds.yaml
并 git commit + push（GitHub Actions 定时任务下次运行即生效）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
FEEDS_PATH = CONFIG_DIR / "feeds.yaml"
JOURNALS_PATH = CONFIG_DIR / "journals.yaml"

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PaperPush · Feed 编辑器</title>
<style>
:root { --bg:#0f1419; --card:#1a2029; --fg:#e6e9ee; --muted:#9aa4b2; --accent:#4da3ff;
        --danger:#ff5c5c; --ok:#4ec94e; --border:#262d38; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
header { padding:18px 28px; border-bottom:1px solid var(--border);
         display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; }
header h1 { margin:0; font-size:20px; }
.toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
main { max-width:860px; margin:0 auto; padding:20px 16px 80px; }
.card { background:var(--card); border:1px solid var(--border); border-radius:10px;
        padding:16px 18px; margin:14px 0; }
.card .head { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
.card .head .fname { font-size:15px; font-weight:600; }
.card .head .actions { display:flex; gap:8px; }
label { display:block; font-size:12px; color:var(--muted); margin:10px 0 4px; }
input[type=text], textarea, select { width:100%; background:#121826; color:var(--fg);
        border:1px solid #2c3545; border-radius:6px; padding:7px 10px; font-size:13px;
        font-family:inherit; }
textarea { min-height:64px; resize:vertical; }
.row { display:flex; gap:14px; flex-wrap:wrap; }
.row > div { flex:1; min-width:200px; }
.checkgroup { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:13px; }
.checkgroup label { display:inline-flex; align-items:center; gap:5px; margin:0; cursor:pointer; }
.radio { display:inline-flex; gap:14px; align-items:center; font-size:13px; margin-top:6px; }
button { background:var(--accent); color:#0b1520; border:none; border-radius:6px;
         padding:8px 16px; font-size:13px; font-weight:600; cursor:pointer; }
button:hover { filter:brightness(1.1); }
button.ghost { background:transparent; border:1px solid #3a4454; color:var(--fg); font-weight:normal; }
button.danger { background:transparent; border:1px solid #5a2d2d; color:var(--danger); font-weight:normal; }
button.primary { font-size:14px; padding:10px 22px; }
#toast { position:fixed; right:20px; bottom:20px; background:#26303f; border:1px solid var(--border);
         border-radius:8px; padding:10px 16px; font-size:13px; display:none; max-width:420px; }
#toast.ok { border-color:var(--ok); color:var(--ok); }
#toast.err { border-color:var(--danger); color:var(--danger); }
.empty { color:var(--muted); text-align:center; padding:40px 0; }
.hint { color:var(--muted); font-size:12px; margin-top:8px; line-height:1.6; }
details { margin-top:8px; }
summary { color:var(--accent); font-size:12px; cursor:pointer; }
</style>
</head>
<body>
<header>
  <h1>📚 PaperPush · Feed 编辑器</h1>
  <div class="toolbar">
    <button class="ghost" id="btnAdd" onclick="addCard()">＋ 新建 Feed</button>
    <button class="primary" id="btnSave" onclick="saveAll()">💾 保存并推送</button>
  </div>
</header>
<main>
  <p class="hint">修改后点击「保存并推送」：自动写入 config/feeds.yaml 并 git push，
  GitHub Actions 将在每日 09:00 或手动触发时按新规则运行。期刊、关键词、作者均为可选项，
  不勾选期刊 = 关注全部期刊。</p>
  <div id="cards"></div>
  <div class="empty" id="emptyHint" style="display:none">还没有任何 Feed，点击右上角「＋ 新建 Feed」开始</div>
</main>
<div id="toast"></div>
<script>
const JOURNALS = %JOURNALS%;

let feeds = [];

function esc(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function newFeed() {
  return { name:"", journals:[], keywords:{ terms:[], match:"any", fields:["title","abstract"], exclude:[] },
           first_authors:[], last_authors:[], author_match:"any" };
}

function termsToText(arr){ return (arr||[]).join("\\n"); }
function textToTerms(s){ return s.split(/[\\n,，;；]+/).map(x=>x.trim()).filter(Boolean); }

function feedToCard(f, i) {
  const allSel = f.journals.length === 0;
  const jCheck = JOURNALS.map(j =>
    `<label><input type="checkbox" data-i="${i}" data-j="${esc(j)}" class="jsel" ${allSel||f.journals.includes(j)?"checked":""}> ${esc(j)}</label>`).join("");
  const fieldChk = ["title","abstract","fulltext"].map(fd =>
    `<label><input type="checkbox" data-i="${i}" data-f="${fd}" class="fsel" ${(f.keywords.fields||[]).includes(fd)?"checked":""}> ${fd==="fulltext"?"全文(PMC)":fd==="abstract"?"摘要":"标题"}</label>`).join("");
  return `<div class="card" data-i="${i}">
    <div class="head">
      <span class="fname" onclick="this.contentEditable=(this.contentEditable!=='true')">${esc(f.name||"(未命名 Feed)")}</span>
      <div class="actions">
        <button class="ghost" onclick="moveCard(${i},-1)">↑</button>
        <button class="ghost" onclick="moveCard(${i},1)">↓</button>
        <button class="danger" onclick="delCard(${i})">删除</button>
      </div>
    </div>
    <label>Feed 名称</label>
    <input type="text" data-i="${i}" class="fname-in" value="${esc(f.name)}" placeholder="例如：突触与学习记忆">
    <label>期刊（不勾选 = 全部关注期刊）<span id="jsel${i}" style="float:right"><a href="javascript:setAllJ(${i},true)" style="color:var(--accent)">全选</a> / <a href="javascript:setAllJ(${i},false)" style="color:var(--accent)">全不选</a></span></label>
    <div class="checkgroup">${jCheck}</div>
    <div class="row">
      <div>
        <label>关键词（每行一个，不区分大小写）</label>
        <textarea data-i="${i}" class="terms" placeholder="synaptic plasticity&#10;hippocamp">${esc(termsToText(f.keywords.terms))}</textarea>
        <div class="radio"><label><input type="radio" data-i="${i}" name="km${i}" class="km" value="any" ${f.keywords.match!=="all"?"checked":""}> 任一命中(OR)</label>
        <label><input type="radio" data-i="${i}" name="km${i}" class="km" value="all" ${f.keywords.match==="all"?"checked":""}> 全部命中(AND)</label></div>
        <label>在哪些字段中搜索</label>
        <div class="checkgroup">${fieldChk}</div>
      </div>
      <div>
        <label>排除词（出现即排除，每行一个）</label>
        <textarea data-i="${i}" class="excl" placeholder="review&#10;erratum">${esc(termsToText(f.keywords.exclude))}</textarea>
        <label>第一作者（每行一个，姓名或姓氏片段）</label>
        <textarea data-i="${i}" class="fa" placeholder="Sakmann">${esc(termsToText(f.first_authors))}</textarea>
        <label>最后作者（通讯/资深作者）</label>
        <textarea data-i="${i}" class="la" placeholder="Markram">${esc(termsToText(f.last_authors))}</textarea>
        <div class="radio"><label><input type="radio" data-i="${i}" name="am${i}" class="am" value="any" ${f.author_match!=="all"?"checked":""}> 任一作者条件</label>
        <label><input type="radio" data-i="${i}" name="am${i}" class="am" value="all" ${f.author_match==="all"?"checked":""}> 一作+末作都需命中</label></div>
      </div>
    </div>
  </div>`;
}

function render() {
  document.getElementById("cards").innerHTML = feeds.map(feedToCard).join("");
  document.getElementById("emptyHint").style.display = feeds.length ? "none" : "block";
}

function setAllJ(i, on) {
  document.querySelectorAll(`input.jsel[data-i="${i}"]`).forEach(c => c.checked = on);
}

function collect(i) {
  const root = document.querySelector(`.card[data-i="${i}"]`);
  const q = s => root.querySelector(s);
  const jsel = [...root.querySelectorAll(".jsel")].filter(c=>c.checked).map(c=>c.dataset.j);
  const kw = {
    terms: textToTerms(q(".terms").value),
    match: q(".km:checked").value,
    fields: [...root.querySelectorAll(".fsel")].filter(c=>c.checked).map(c=>c.dataset.f),
    exclude: textToTerms(q(".excl").value),
  };
  return {
    name: q(".fname-in").value.trim(),
    journals: jsel,
    keywords: kw,
    first_authors: textToTerms(q(".fa").value),
    last_authors: textToTerms(q(".la").value),
    author_match: q(".am:checked").value,
  };
}

function addCard(){ feeds.push(newFeed()); render(); }
function delCard(i){ feeds.splice(i,1); render(); }
function moveCard(i,d){ const t=feeds.splice(i,1)[0]; feeds.splice(Math.min(Math.max(i+d,0),feeds.length),0,t); render(); }

function toast(msg, ok=true){
  const t=document.getElementById("toast");
  t.textContent=msg; t.className=ok?"ok":"err"; t.style.display="block";
  clearTimeout(t._h); t._h=setTimeout(()=>t.style.display="none", 5000);
}

async function saveAll(){
  const data = feeds.map((_,i)=>collect(i)).filter(f=>f.name);
  const btn=document.getElementById("btnSave"); btn.disabled=true;
  try {
    const r = await fetch("/api/feeds", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({feeds:data})});
    const j = await r.json();
    if (j.ok) toast(j.pushed ? "✅ 已保存并推送到 GitHub" : "✅ 已保存（"+j.message+"）");
    else toast("❌ 保存失败："+j.message, false);
  } catch(e){ toast("❌ 网络错误："+e, false); }
  btn.disabled=false;
}

async function load(){
  try {
    const r = await fetch("/api/feeds"); const j = await r.json();
    feeds = j.feeds; render();
  } catch(e){ toast("❌ 加载失败："+e, false); }
}
load();
</script>
</body>
</html>"""


def load_journal_names() -> list:
    data = yaml.safe_load(JOURNALS_PATH.read_text(encoding="utf-8")) or {}
    return list((data.get("journals") or {}).keys())


def clean_feed(f: dict) -> dict:
    """去掉空字段，保证生成的 YAML 干净。"""
    out = {"name": f.get("name", "")}
    if f.get("journals"):
        out["journals"] = f["journals"]
    kw = {}
    for key in ("terms", "exclude"):
        if f.get("keywords", {}).get(key):
            kw[key] = f["keywords"][key]
    if kw:
        kw["match"] = f["keywords"].get("match", "any")
        fields = f["keywords"].get("fields") or []
        if kw and fields:
            kw["fields"] = fields
        out["keywords"] = kw
    if f.get("first_authors"):
        out["first_authors"] = f["first_authors"]
    if f.get("last_authors"):
        out["last_authors"] = f["last_authors"]
    if f.get("first_authors") or f.get("last_authors"):
        out["author_match"] = f.get("author_match", "any")
    return out


def render_feeds_yaml(feeds: list) -> str:
    header = """# 由 webadmin.py 网页编辑器生成。如需手改请保持此结构，或改用编辑器。
#
# 结构说明：
#   name           必填，feed 名称
#   journals       可选，仅在这些期刊中匹配；省略或为空 = 所有关注期刊
#   keywords       可选：
#     terms        关键词列表（大小写不敏感）
#     match        any = 命中任意一个词（OR）；all = 所有词都要命中（AND）
#     fields       搜索范围：title / abstract / fulltext（fulltext 仅对 PMC 开放获取文章有效）
#     exclude      排除词（NOT）：标题或摘要中出现即不命中
#   first_authors  可选，第一作者姓名片段
#   last_authors   可选，最后作者（通讯/资深）姓名片段
#   author_match   any = 任一作者条件；all = 一作与末作都满足
"""
    body = yaml.dump({"feeds": [clean_feed(f) for f in feeds]}, allow_unicode=True, sort_keys=False)
    return header + "\n" + body


def _git_identity() -> list:
    """自动获取 git 身份：优先环境变量，其次 gh 登录账号，返回 -c 参数列表。"""
    name = os.environ.get("GIT_USER_NAME") or ""
    email = os.environ.get("GIT_USER_EMAIL") or ""
    if not (name and email):
        try:
            login = subprocess.run(
                ["gh", "api", "user", "--jq", ".login"],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
            if login:
                name, email = login, f"{login}@users.noreply.github.com"
        except (subprocess.SubprocessError, OSError):
            pass
    if not (name and email):
        return []
    return ["-c", f"user.name={name}", "-c", f"user.email={email}"]


def git_push() -> str:
    """提交并推送 feeds.yaml；返回错误信息（成功返回空字符串）。"""
    identity = _git_identity()
    try:
        subprocess.run(
            ["git", "-C", str(ROOT), "add", "config/feeds.yaml"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(ROOT), *identity, "commit", "-m", "Update feeds via webadmin"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(ROOT), "push", "origin", "main"],
            check=True, capture_output=True, text=True, timeout=120,
        )
        return ""
    except subprocess.CalledProcessError as e:
        out = (e.stdout or "") + (e.stderr or "")
        if "nothing to commit" in out:
            return ""
        return out.strip()[-400:]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            page = PAGE.replace("%JOURNALS%", json.dumps(load_journal_names(), ensure_ascii=False))
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/feeds":
            data = yaml.safe_load(FEEDS_PATH.read_text(encoding="utf-8")) or {}
            body = json.dumps(
                {"journals": load_journal_names(), "feeds": data.get("feeds", [])},
                ensure_ascii=False,
            ).encode("utf-8")
            self._send(200, body)
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if self.path != "/api/feeds":
            self._send(404, b'{"error":"not found"}')
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            feeds = payload.get("feeds", [])
            names = [f.get("name", "") for f in feeds]
            if not all(names):
                self._send(400, json.dumps({"ok": False, "message": "存在未命名 Feed，请填写名称"}).encode())
                return
            if len(set(names)) != len(names):
                self._send(400, json.dumps({"ok": False, "message": "Feed 名称重复，请改为唯一名称"}).encode())
                return
            FEEDS_PATH.write_text(render_feeds_yaml(feeds), encoding="utf-8")
            err = git_push() if PUSH else "推送已禁用（--no-push）"
            self._send(200, json.dumps({"ok": True, "pushed": not err, "message": err or "已推送到 GitHub"}).encode())
        except Exception as e:  # noqa: BLE001
            self._send(400, json.dumps({"ok": False, "message": str(e)}).encode())


def main() -> int:
    parser = argparse.ArgumentParser(description="PaperPush Feed 网页编辑器")
    parser.add_argument("--port", type=int, default=8080, help="默认 8080（避开 Windows 保留端口段）")
    parser.add_argument("--no-push", action="store_true", help="保存时不自动 git push")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    global PUSH
    args = parser.parse_args()
    PUSH = not args.no_push

    url = f"http://localhost:{args.port}"
    print(f"Feed 编辑器已启动：{url}")
    print("编辑完成后点击「保存并推送」即可生效（GitHub Actions 下次运行使用新规则）。")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
