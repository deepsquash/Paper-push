# PaperPush 文献速递

按自定义 feed 规则（关键词/第一作者/最后作者，支持与/或/非逻辑）监控 18 个核心期刊，
抓取**提前在线（ahead of print）**文章，命中后推送到微信（Server酱），并生成网页看板
（GitHub Pages），可一键将原文添加至 Zotero。

## 功能

- **网页端完整产品**：看板 / Feed 管理 / 期刊管理 / 设置 / 手动运行（实时日志）全部在浏览器完成
- **覆盖提前在线文章**：基于 Crossref `published-online` 日期抓取，期刊官网 Early Online 上线即收录
- **灵活 feed 规则**（网页可视化编辑，亦可手改 `config/feeds.yaml`）：
  - 关键词：标题 / 摘要 / 全文（全文仅对 PMC 开放获取文章有效）
  - 关键词逻辑：`any`（或）/ `all`（与）/ `exclude`（非）
  - 第一作者 / 最后作者（通讯/资深作者），`author_match: any/all`
  - 期刊白名单（可选，不填 = 全部关注期刊）
- **期刊管理**：网页增删期刊、编辑 ISSN
- **微信即时推送**：Server酱（SendKey 在网页「设置」填写），新命中实时送达
- **Zotero 一键入库**：看板文章卡片直接点击「添加至 Zotero」（网页配置 API key 即可，无需浏览器插件）
- **每日定时**：内置 09:00（北京时间）自动运行；也可手动触发
- **去重**：SQLite 记录已见 DOI，同一篇文章只推送一次
- 作者超过 6 位时展示前 3 + 后 3

## 产品化能力（当前版本）

- **中英文与移动端**：响应式网页、中文优先界面、语言切换入口；PWA manifest + service worker，
  可在手机浏览器“添加到主屏幕”，为后续封装 iOS/Android App 保留同一套 API
- **Web of Science 风格检索**：Feed 支持 `AND / OR / NOT`、嵌套括号、引号短语、
  通配符 `*` / `?`，以及字段 `TS/TI/AB/FT/AU/FA/LA/SO`
- **期刊发现库**：内置 57 个来源（Nature/Science/Cell 系、神经科学主流期刊和
  `bioRxiv · Neuroscience`），点击期刊可查看本地半年文献并触发首次回填
- **本地文献库**：SQLite 持久化文章、来源同步状态、收藏和屏蔽反馈；后续刷新增量更新
- **相对时间**：一周以内显示“几天前”，一天以内显示“几小时前”
- **个性化反馈**：文章可收藏或标记不感兴趣；屏蔽文章从发现流和 Feed 看板移除

复杂检索示例：

```text
("synaptic plasticity" OR hippocamp*) AND memory
TS=((optogenetic* OR chemogenetic*) AND memory) AND LA=(Tonegawa OR Buzsaki)
(TI=(astrocyte OR microglia) OR AB="glial cell") AND NOT SO=review
```

字段含义：`TS` 主题、`TI` 标题、`AB` 摘要、`FT` 全文、`AU` 任意作者、
`FA` 第一作者、`LA` 最后作者、`SO` 期刊/来源。

## 目录结构

```
config/
  journals.yaml    关注期刊与 ISSN（网页「期刊管理」编辑）
  feeds.yaml       feed 匹配规则（网页「Feed 管理」编辑）
  settings.yaml    全局设置（网页「设置」编辑：推送、Zotero、抓取参数）
paperpush/
  sources/         Crossref / PubMed 数据源
  matcher.py       feed 匹配引擎
  pipeline.py      核心流程（抓取→匹配→去重→推送→报告），CLI 与 Web 共用
  storage.py       SQLite 去重
  push/            微信推送 + HTML 看板
  zotero.py        Zotero API
web/               Web 前端（单页应用）
app.py             Web 服务入口（含每日定时任务）
main.py            CLI 入口（可选，兼容命令行运行）
start.bat          Windows 一键启动
.github/workflows/ GitHub Actions 定时任务（可选）
```

## 快速开始（网页完整版，推荐）

### 1. 一键启动

Windows 双击 **`start.bat`**（首次自动安装依赖），或命令行：

```bash
pip install -r requirements.txt
python app.py          # 浏览器自动打开 http://localhost:8080
```

服务器 / Docker 部署（同一 API 可供后续 iOS、Android、微信小程序调用）：

```bash
docker build -t paperpush .
docker run -d -p 8080:8080 -v paperpush-data:/app/data --name paperpush paperpush
```

生产模式使用 Waitress；`python app.py --host 0.0.0.0` 可直接监听局域网/服务器地址。
对外公网服务前仍应增加用户登录、HTTPS、密钥加密和数据库迁移（当前是单用户本地产品）。

### 2. 使用流程

打开 http://localhost:8080 后：

1. **设置** 页：填写 Server酱 SendKey（微信推送）、Zotero API Key + User ID（一键入库）
2. **Feed 管理** 页：新建/编辑筛选规则（期刊、关键词、作者、逻辑），点「保存全部 Feed」
3. **期刊管理** 页：增删关注期刊与 ISSN（默认已含 18 个核心期刊）
4. **运行** 页：点「立即运行」查看实时日志；之后每日 09:00（北京时间）自动运行
5. **看板** 页：按 Feed 分组查看命中文章，一键「添加至 Zotero」

> 定时任务与手动运行共用同一流程：抓取（含提前在线）→ 匹配 → 去重 → 微信推送 + Zotero 入库。

### CLI 模式（可选）

```bash
python main.py --since-days 3 --no-push   # 抓取 → 匹配 → 生成 dist/index.html
```

## GitHub Actions（可选，二选一）

网页完整版自带每日定时任务，无需 GitHub Actions。若仍希望云端运行备份，
保持 `.github/workflows/daily.yml` 并配置 secret `SCT_SENDKEY` 即可
（注意：云端运行使用 push 到仓库的 feeds.yaml，与本机网页编辑的配置需保持同步）。

## Zotero 配置

1. [创建 API key](https://www.zotero.org/settings/keys)（允许读写）
2. User ID：zotero.org 个人页 URL 中的数字
3. 「设置」页填入并启用；可选：固定 Collection Key，或「按 Feed 名自动归类」
   （需先在 Zotero 中建好同名分组）
4. 配置后可选择：看板手动「添加至 Zotero」或「设置」页启用自动入库（每次运行新命中自动添加）

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
