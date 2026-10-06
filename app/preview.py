"""预览源应用：只服务归档回放，不允许访问实时外网。

与管理界面分端口运行（不同源）。所有响应带严格 CSP，
非 GET 请求一律 405 阻止。
"""
import html as html_mod
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import db, replaycore, rewrite

app = FastAPI(title="WARC Replay Preview", docs_url=None, redoc_url=None)

CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline' 'unsafe-eval' 'self' data: blob:; "
    "style-src 'unsafe-inline' 'self'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "media-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-src 'self' data:; "
    "worker-src 'self' blob:; "
    "form-action 'self'; "
    "base-uri 'self'"
)

# 不透出的归档响应头（逐跳头 / 我们自行设置的头 / 影响隔离的头）
DROP_HEADERS = {
    "connection", "keep-alive", "transfer-encoding", "content-length",
    "content-encoding", "te", "trailer", "upgrade", "set-cookie",
    "content-security-policy", "content-security-policy-report-only",
    "strict-transport-security",
}

TS14_RE = re.compile(r"^\d{14}$")
CHARSET_RE = re.compile(r"charset=([\w.\-]+)", re.I)


def parse_ts(ts):
    if TS14_RE.match(ts):
        return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def fix_scheme(url):
    """容错：路径里的 'http:/host' 还原为 'http://host'。"""
    m = re.match(r"^(https?):/(?!/)", url)
    if m:
        return m.group(1) + "://" + url[m.end():]
    return url


def decode_text(body, headers):
    charset = "utf-8"
    ctype = replaycore.header_value(headers, "content-type") or ""
    m = CHARSET_RE.search(ctype)
    if m:
        charset = m.group(1)
    try:
        return body.decode(charset, errors="replace")
    except (LookupError, ValueError):
        return body.decode("utf-8", errors="replace")


def log_missing(conn, url, ts, reason, request):
    conn.execute(
        "INSERT INTO missing (url, cutoff, reason, referer, epoch) VALUES (?,?,?,?,?)",
        (url, ts, reason, request.headers.get("referer"), time.time()),
    )
    conn.commit()


def error_page(entry_url, ts, exc):
    rows = "".join(
        "<li>%s &rarr; <code>%s</code> @ %s</li>"
        % (
            hop["status"] if hop["status"] is not None else "-",
            html_mod.escape(hop["url"]),
            html_mod.escape(hop["ts"]),
        )
        for hop in exc.chain
    )
    return HTMLResponse(
        """<!DOCTYPE html><html><head><meta charset="utf-8"><title>回放失败</title>
<style>body{font:14px sans-serif;margin:2em;background:#f6f7f9;color:#222}
code{background:#eee;padding:1px 4px}</style></head><body>
<h1>无法回放</h1>
<p>入口：<code>%s</code>（截止 %s）</p>
<p>出错地址：<code>%s</code></p>
<p>原因：<strong>%s</strong></p>
%s
<p><a href="/">返回预览首页</a></p>
</body></html>"""
        % (
            html_mod.escape(entry_url),
            html_mod.escape(ts),
            html_mod.escape(exc.url),
            html_mod.escape(exc.reason),
            ("<p>已跟随的跳转链：</p><ol>%s</ol>" % rows) if rows else "",
        ),
        status_code=508 if "环" in exc.reason else 404,
    )


@app.get("/replay/{ts}/{url:path}")
async def replay(ts: str, url: str, request: Request):
    query = request.url.query
    if query:  # 目标 URL 自身的查询串原样拼回
        url = url + "?" + query
    url = fix_scheme(url)
    try:
        cutoff = parse_ts(ts)
    except ValueError:
        return PlainTextResponse("无效时间戳: %s" % ts, status_code=400)

    conn = db.connect()
    try:
        try:
            chain = replaycore.follow(conn, url, cutoff)
        except replaycore.ReplayError as exc:
            log_missing(conn, exc.url, ts, exc.reason, request)
            return error_page(url, ts, exc)
    finally:
        conn.close()

    final = chain[-1]
    body = final["body"] or b""
    mime = (final["mime"] or "").lower()
    since = time.time()

    extra_headers = {
        "X-WARC-Original-URL": quote(final["url"], safe=""),
        "X-WARC-Capture-Date": final["ts"],
        "X-WARC-Record-Type": final["type"],
        "Cache-Control": "no-cache",
    }
    if final["base_url"]:
        extra_headers["X-WARC-Base-URL"] = quote(final["base_url"], safe="")
        extra_headers["X-WARC-Base-Date"] = final["base_ts"] or ""

    if "text/html" in mime:
        text = decode_text(body, final["headers"])
        out = rewrite.rewrite_html(text, url, final["url"], ts, chain, since)
        resp = HTMLResponse(out, status_code=final["status"] or 200)
        resp.headers["Content-Security-Policy"] = CSP
    elif "text/css" in mime:
        text = decode_text(body, final["headers"])
        # 按样式表自身 URL 解析相对资源（跨 URL revisit 时同样是该 CSS 的地址）
        out = rewrite.rewrite_css(text, final["url"], ts)
        resp = Response(out.encode("utf-8"), media_type="text/css; charset=utf-8")
    else:
        resp_headers = {}
        for k, v in final["headers"]:
            if k.lower() in DROP_HEADERS:
                continue
            resp_headers[k] = v
        ctype = final["mime"] or "application/octet-stream"
        resp = Response(body, status_code=final["status"] or 200, headers=resp_headers)
        resp.headers["Content-Type"] = ctype

    for k, v in extra_headers.items():
        resp.headers[k] = v
    return resp


@app.api_route("/replay/{rest:path}", methods=["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def replay_non_get(rest: str, request: Request):
    return JSONResponse(
        {"error": "WARC 回放仅允许 GET，非 GET 请求已被阻止", "method": request.method},
        status_code=405,
    )


@app.get("/__api/missing")
async def missing_since(since: float = 0):
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT url, reason, referer, cutoff, epoch FROM missing "
            "WHERE epoch>=? ORDER BY epoch DESC LIMIT 200",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!DOCTYPE html><html><head><meta charset="utf-8"><title>WARC 回放预览源</title>
<style>body{font:14px sans-serif;margin:2em;max-width:60em}code{background:#eee;padding:1px 4px}</style>
</head><body>
<h1>WARC 回放预览源</h1>
<p>这是回放专用源，只服务归档内容，不访问实时外网。请从管理界面选择入口 URL 与截止时间进入。</p>
<p>回放地址格式：<code>/replay/&lt;YYYYMMDDHHMMSS&gt;/&lt;原始URL&gt;</code></p>
</body></html>"""
