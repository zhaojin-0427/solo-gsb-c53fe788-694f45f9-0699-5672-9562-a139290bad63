# WARC 离线回放台

用 Python + FastAPI + warcio + SQLite + 原生 JavaScript 实现的 WARC 离线回放台。
上传 WARC / WARC.gz 档案后，选择入口 URL 与截止时间即可离线回放，全程不访问实时外网。

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

启动后开放两个**相互隔离的源**：

| 端口 | 用途 |
|------|------|
| `8000` | 管理端：上传档案、选择入口与截止时间、查看回放记录与缺档清单 |
| `8001` | 回放源：仅离线回放，所有响应带严格 CSP，禁止页面连接实时网络 |

数据（SQLite 库与索引）持久化在 `replay-data` 卷中。

也可以本地直接运行（Python 3.11+）：

```bash
pip install -r requirements.txt
python -m app.main        # 管理端 :8000，回放源 :8001
```

## 使用流程

1. 打开管理端 <http://localhost:8000>。
2. **上传档案**：选择 `.warc` 或 `.warc.gz` 文件。损坏的档案（gzip 破损、
   记录畸形、无任何可用记录）会整体导入失败并提示原因，不留半成品。
3. 在档案列表点**选择**，从下拉清单挑**入口 URL**，设定**截止时间（UTC）**
   （默认为全档案最晚采集时间），点**开始回放**。
4. 预览在隔离的回放源（`:8001`）中以 iframe 打开，也可点"在新标签打开预览"。
5. 页面顶部信息条列出**原址、命中采集时间、重定向跳数**；管理端第 5 区
   的**回放记录 / 缺档清单**列出每次请求的原址、命中时间与缺档原因。

## 回放规则

- **取档**：跳转与 HTTP(S) GET 只取该 URL **不晚于截止时间的最近一条记录**；
  只有更晚记录时标缺（"不借未来版本"）。
- **revisit**：按负载摘要复用**更早**响应的正文，允许跨 URL；展示与日志中
  保留 revisit 自身的 URL 与采集时间。找不到基体（同摘要的更早响应）时标缺。
- **重定向**：自动跟随 301/302/303/307/308，检测**重定向环**与过长链并标缺。
- **HTML**：相对引用按**最终页面 URL** 或其 `<base href>` 解析后改写为回放
  地址；`srcset`、内联 `style`、`<style>` 块一并处理。
- **CSS**：`url()` 与 `@import` 按**样式表自身 URL** 解析并**保留查询串**；
  跨 URL revisit 复用来的 CSS，其图片仍按该 CSS 自己的地址解析。
- **脚本**：注入的 shim 拦截 `fetch` / `XMLHttpRequest` / `sendBeacon`，
  脚本拼接的绝对 URL 的 GET 同样从归档加载。
- **非 GET**：一律 405 阻止并记入缺档清单。
- **隔离**：管理端与回放源是不同源（不同端口）；回放响应的 CSP
  `default-src 'none'` 仅放行同源与 `data:`/`blob:`，页面无法访问实时外网。

## 缺档清单

管理端第 5 区默认显示缺档清单（可切换"全部记录"），也可调 API：

```bash
curl 'http://localhost:8000/api/archives/1/log?missing_only=true'
```

每条缺档记录包含：请求时间、**原址**、**缺档原因**（无记录 / 仅有未来记录 /
revisit 缺基体 / 重定向环 / 非 GET 被阻止）。命中记录则包含**命中采集时间**
与 HTTP 状态。回放中的缺档页面本身也会展示原址与原因。

## 主要 API

管理端（`:8000`）：

- `POST /api/archives` — 上传导入（multipart，`file` 字段）
- `GET /api/archives` / `DELETE /api/archives/{id}`
- `GET /api/archives/{id}/urls` — 档案内 URL 清单（捕获次数、时间范围）
- `GET /api/archives/{id}/log?missing_only=1` — 回放记录 / 缺档清单
- `POST /api/archives/{id}/log/clear`

回放源（`:8001`）：

- `GET /web/{archive_id}/{cutoff_epoch}/{urlencoded_url}` — 回放入口（仅 GET/HEAD）

## 测试

```bash
python tests/make_sample_warc.py /tmp/sample.warc.gz   # 生成覆盖各规则的样例档案
python tests/smoke_test.py                              # 37 项冒烟断言
```

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `ADMIN_PORT` / `REPLAY_PORT` | `8000` / `8001` | 两个源的监听端口 |
| `DATA_DIR` | `./data`（容器内 `/data`） | SQLite 与上传临时文件目录 |
| `ADMIN_ORIGINS` | `localhost/127.0.0.1:8000` | 允许嵌入预览的管理端来源（CSP `frame-ancestors`） |
