# PaperPush 文献速递

按自定义 feed 规则（关键词/第一作者/最后作者，支持与/或/非逻辑）监控 18 个核心期刊，
抓取**提前在线（ahead of print）**文章，命中后推送到微信（Server酱），并生成网页看板
（GitHub Pages），可一键将原文添加至 Zotero。

## 功能

- **覆盖提前在线文章**：基于 Crossref `published-online` 日期抓取，期刊官网 Early Online 上线即收录
- **网页可视化编辑 Feed**：运行 `python webadmin.py`，浏览器操作（见下方「网页编辑器」），无需手改配置文件
- **灵活 feed 规则**（`config/feeds.yaml`，亦可网页编辑）：
  - 关键词：标题 / 摘要 / 全文（全文仅对 PMC 开放获取文章有效）
  - 关键词逻辑：`any`（或）/ `all`（与）/ `exclude`（非）
  - 第一作者 / 最后作者（通讯/资深作者），`author_match: any/all`
  - 期刊白名单（可选，不填 = 全部关注期刊）
- **微信即时推送**：Server酱，新命中实时送达，附原文链接
- **网页看板**：GitHub Pages 每日更新，按 feed 分组，含命中理由、提前在线标记、摘要；
  作者超过 6 位时展示前 3 + 后 3
- **Zotero 一键入库**：
  - 网页看板：点击「+ 添加至 Zotero」→ 浏览器需安装 [Zotero Connector](https://www.zotero.org/download/connectors)
  - 本地运行：配置 API key 后自动写入（可选，见下）
- **去重**：SQLite 记录已见 DOI，同一篇文章只推送一次

## 目录结构

```
config/
  journals.yaml    关注期刊与 ISSN（增删期刊改这里）
  feeds.yaml       feed 匹配规则（用网页编辑器修改，见下）
  settings.yaml    全局设置（回看天数、推送、Zotero）
paperpush/
  sources/         Crossref / PubMed 数据源
  matcher.py       feed 匹配引擎
  storage.py       SQLite 去重
  push/            微信推送 + HTML 看板
  zotero.py        Zotero Web API
main.py            主程序入口
webadmin.py        本地网页 Feed 编辑器（重点功能，见下）
.github/workflows/ GitHub Actions 定时任务
```

## 网页编辑器（推荐）

不需要手改配置文件，浏览器可视化增删改 Feed：

```bash
python webadmin.py
# 自动打开 http://localhost:8080
```

- 每个 Feed 可独立设置：名称、期刊多选（不勾选 = 全部）、关键词（含 any/all 逻辑与排除词）、
  搜索字段（标题/摘要/全文）、第一作者、最后作者、作者逻辑
- 点「保存并推送」→ 自动写入 `config/feeds.yaml` 并 git push → GitHub Actions 下次运行即生效
- git 身份自动从 `gh` 登录账号获取；如需自定义：`GIT_USER_NAME` / `GIT_USER_EMAIL` 环境变量
- 默认端口 8080（Windows 保留端口段 8749-8848 不可用）；被占用时 `python webadmin.py --port 9000`
- 不想自动推送：`python webadmin.py --no-push`（只写本地文件）

## 快速开始

### 1. 本地试跑

```bash
pip install -r requirements.txt
python main.py --since-days 3 --no-push   # 抓取 → 匹配 → 生成 dist/index.html
```

浏览器打开 `dist/index.html` 查看看板。

### 2. 配置微信推送（Server酱）

1. 打开 [Server酱](https://sct.ftqq.com)，微信扫码登录，复制 SendKey
2. 本地运行：
   ```powershell
   $env:SCT_SENDKEY = "SCTxxxxxxxxxx"
   python main.py
   ```
3. GitHub Actions 运行：在仓库 Settings → Secrets and variables → Actions 中
   添加 secret `SCT_SENDKEY`

### 3. 部署到 GitHub Actions + Pages（每日自动运行）

1. 推送代码到 GitHub 仓库（见「连接 GitHub」）
2. 添加 secret `SCT_SENDKEY`
3. 仓库 Settings → Pages → Source 选择 **GitHub Actions**
4. 在 `config/settings.yaml` 中把 `report_url` 改为
   `https://<你的用户名>.github.io/<仓库名>/`
5. 手动触发测试：Actions → Daily paper push → Run workflow
6. 每日 09:00（北京时间）自动运行

### 4. Zotero（可选，本地模式）

1. [Zotero API key](https://www.zotero.org/settings/keys) 创建 key（允许读写）
2. 找到你的 user_id（zotero.org 个人页 URL 中的数字）
3. 编辑 `config/settings.yaml` 的 `zotero` 段，设置 `enabled: true`、
   `user_id: <你的ID>`，可选 `collection_key`（固定分组）或
   `use_feed_collection: true`（自动归入与 feed 同名的分组，需先在 Zotero 建好）
4. 本地运行：
   ```powershell
   $env:ZOTERO_API_KEY = "xxxx"
   python main.py
   ```

> 注意：Zotero API key 属于个人凭据，**不要**配置到 GitHub Actions，
> 云端看板请用 Zotero Connector 插件方式一键添加。

## Feed 规则详解

以 `config/feeds.yaml` 示例为准，每条规则说明：

```yaml
feeds:
  - name: "突触与学习记忆"      # 必填，唯一
    journals: [Nature, Cell]    # 可选；只在这些期刊中匹配，省略 = 全部
    keywords:
      terms: ["synaptic plasticity", "hippocamp"]  # 关键词（子串匹配，不区分大小写）
      match: any                # any=命中任意词(OR)；all=全部命中(AND)
      fields: [title, abstract] # title / abstract / fulltext（fulltext 依赖 PMC 开放获取）
      exclude: ["erratum"]      # 非逻辑：标题/摘要出现即排除
    first_authors: ["Sakmann"]  # 可选，第一作者姓名片段
    last_authors: ["Markram"]   # 可选，最后作者姓名片段
    author_match: all           # any=任一作者条件命中；all=一作+末作都命中
```

规则组合示例：

| 需求 | 写法 |
| --- | --- |
| 只看 A 课题组（一作 X + 通讯 Y） | `first_authors: [X]` + `last_authors: [Y]` + `author_match: all` |
| 关键词必须同时出现 | `match: all` |
| 排除综述 | `exclude: ["review"]` |
| 只看某期刊任意新文章 | 只写 `journals`，不写任何条件 |
| 跟踪某作者任何位置 | 关键词留空时暂不支持，可用 `first_authors` 与 `last_authors` 两个 feed 组合 |

## 数据源与限制

- **Crossref**（主）：统一覆盖全部 18 个期刊，含提前在线文章；约数分钟到数小时的收录滞后
- **PubMed**（辅）：为命中候选补充摘要；PMC 全文索引用于 `fulltext` 关键词匹配
- **全文搜索限制**：Cell/Nature 等绝大多数文章非开放获取，PMC 拿不到全文，
  `fulltext` 字段仅对 OA 文章生效；订阅期刊文章请用 title/abstract 字段
- 推送当天新命中；重复文章（eLife 版本化 DOI、print/online 双收录）已自动合并

## 常见问题

- **某期刊抓不到**：检查 `config/journals.yaml` 的 ISSN 是否有效
  （在线 ISSN 未注册到 Crossref 的会返回 404，清空 `online` 即可，文章仍挂在 print ISSN 下）
- **今天没有推送**：可能没有新命中（去重后），或 `SCT_SENDKEY` 未配置
- **预览匹配结果**：`python main.py --no-push`，然后看 `dist/index.html`
