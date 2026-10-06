# WARC 离线回放台

用 Python + FastAPI + warcio + SQLite + 原生 JavaScript 实现的 WARC 离线回放工具。
上传 WARC / WARC.gz 档案后，选择入口 URL 与截止时间，即可在隔离的预览源中离线回放网页，
不访问实时外网。

## 功能特性

- **上传导入**：支持 `.warc` 与 `.warc.gz`；损坏档案（截断、CRC 错误、非 WARC 内容）整体导入失败并标记原因，不留半截数据。
- **记录选择**：跳转链每一跳与每个 HTTP(S) GET 都只取"该 URL 不晚于截止时间的最近一条记录"，绝不借用未来版本。
- **revisit 复用**：revisit 记录按 payload 摘要复用更早响应的正文（允许跨 URL），但保留自身 URL 与采集时间；缺少基体或缺少摘要时标缺。
- **重定向**：自动跟随 301/302/303/307/308，检测重定向环（508）并限制最大跳数。
- **地址解析**：
  - HTML 相对引用按最终页面 URL 或其 `<base>` 解析；
  - CSS 相对资源按样式表自身 URL 解析，`url()` 中的查询串原样保留；
  - 跨 URL revisit 的 CSS，其中图片仍按该 CSS 自己的地址解析与查找。
- **脚本请求**：注入补丁脚本，脚本拼接的绝对 URL GET（fetch / XHR）也从归档加载；非 GET 请求在浏览器端与服务器端双重阻止（405）。
- **源隔离**：管理界面（8000）与回放预览（8001）分端口运行（不同源）；回放页带严格 CSP，且所有静态资源本地化，两个源都不会访问实时外网。
- **缺档清单**：回放页顶部横幅实时列出原址、最终地址、命中时间与缺档原因；管理界面提供全局缺档清单。

## 快速开始（Docker Compose 一键启动）

```bash
docker compose up --build
```

启动后：

| 地址 | 用途 |
| --- | --- |
| http://localhost:8000 | 管理界面（上传档案、打开回放、捕获查询、缺档清单） |
| http://localhost:8001 | 回放预览源（只服务归档内容） |

使用流程：

1. 打开 http://localhost:8000 ，在"1. 上传档案"中选择 WARC/WARC.gz 文件上传，等待状态变为"就绪"。
2. 在"2. 打开回放"中填写**入口 URL** 与**截止时间**（留空为当前时间），点击后将在预览源新标签页打开回放。
3. 回放页顶部横幅显示原址、最终地址、命中时间与跳转链；"缺档"计数与"详情"实时列出本页缺档原因。
4. 在管理界面"4. 缺档清单"查看全局缺档记录（缺失 URL、原因、来源页面），可刷新或清空。
5. 回放地址格式为 `http://localhost:8001/replay/<YYYYMMDDHHMMSS>/<原始URL>`，也可以手工构造。

想快速体验，可以生成并上传示例档案（覆盖重定向、revisit、CSS 相对资源、重定向环、未来记录等场景）：

```bash
pip install -r requirements.txt
python examples/make_sample.py   # 生成 examples/sample.warc.gz
```

然后上传 `examples/sample.warc.gz`，以入口 `http://example.com/`、截止时间 `2025-01-01 00:00:00` 打开回放。

## 本地开发

```bash
pip install -r requirements.txt
python -m app.main
```

环境变量：`ADMIN_PORT`（默认 8000）、`PREVIEW_PORT`（默认 8001）、`DATA_DIR`（默认 `./data`，存 SQLite 与上传的档案）。

## 回放规则说明

- **取记录**：对每个请求的 URL，取采集时间 ≤ 截止时间的最近一条记录；同一 URL 有更新捕获也不会用。
- **revisit**：按 `WARC-Payload-Digest` 找不晚于 revisit 采集时间的、带正文的最近响应作为基体（可跨 URL）；找不到即标缺"revisit 缺少基体"。
- **重定向**：逐跳解析，每跳独立按上述规则取记录；Location 相对地址按当前跳 URL 解析；发现环或超过 20 跳即报错并记入缺档清单。
- **缺档**：无 ≤ 截止时间的记录、revisit 缺基体、重定向环等都会记入缺档清单，并在回放页横幅与管理界面展示。

## 项目结构

```
app/
  main.py        # 启动入口：同进程跑管理源与预览源
  db.py          # SQLite 存储（archives / records / missing）
  importer.py    # WARC 导入与完整性校验（损坏即失败）
  replaycore.py  # 记录选择、revisit 复用、重定向跟随与环检测
  rewrite.py     # HTML/CSS 重写、补丁脚本与横幅注入
  admin.py       # 管理源（上传、捕获查询、缺档清单）
  preview.py     # 预览源（回放、缺档 API、非 GET 阻止）
  static/        # 管理界面（原生 JS，无外部依赖）
examples/
  make_sample.py # 生成覆盖各场景的示例 WARC
```
