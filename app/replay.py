"""回放源（独立端口/源）：只从归档取内容，绝不访问实时外网。

- GET /web/{archive_id}/{cutoff}/{url:path}  回放入口（仅 GET/HEAD）
- 非 GET 一律 405 阻止并记入日志
- 所有响应带严格 CSP，禁止页面连接实时网络
"""
from html import escape
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import config, db, resolve
from .rewrite import (
    banner_html,
    inject_head,
    make_wrapper,
    rewrite_css,
    rewrite_html,
)

HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding", "content-encoding",
    "content-length", "content-security-policy", "content-security-policy-report-only",
    "strict-transport-security", "x-frame-options", "set-cookie",
}


def create_replay_app():
    app = FastAPI(title="WARC 回放台 · 回放源", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (
            "<h1>WARC 回放源</h1><p>此源仅用于离线回放，请从管理端 （端口 "
            f"{config.ADMIN_PORT}）选择入口 URL 与截止时间进入。</p>"
        )

    @app.api_route(
        "/web/{archive_id}/{cutoff}/{url:path}",
        methods=["POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    def blocked_non_get(archive_id: int, cutoff: int, url: str, request: Request):
        """非 GET 请求：阻止并记录。"""
        orig = unquote(url)
        conn = db.connect()
        try:
            known = conn.execute(
                "SELECT 1 FROM archives WHERE id=?", (archive_id,)).fetchone()
        finally:
            conn.close()
        if known:
            db.log_access(archive_id, orig, cutoff, hit=False,
                          reason=f"非 GET 请求已被阻止（{request.method}）",
                          via=request.headers.get("sec-fetch-dest"))
        return JSONResponse(
            {"error": "non-GET request blocked by offline replay",
             "method": request.method, "url": orig},
            status_code=405,
            headers=_csp_headers(),
        )

    @app.get("/web/{archive_id}/{cutoff}/{url:path}")
    def web(archive_id: int, cutoff: int, url: str, request: Request):
        orig_url = unquote(url)
        via = request.headers.get("sec-fetch-dest")
        conn = db.connect()
        try:
            archive = conn.execute(
                "SELECT id FROM archives WHERE id=? AND status='ok'",
                (archive_id,)).fetchone()
            if archive is None:
                return _info_response(
                    "档案不存在或导入失败", orig_url,
                    "档案不存在或导入失败", cutoff)

            def on_hop(hop_url, status, hit_ts, location):
                db.log_access(archive_id, hop_url, cutoff, hit=True,
                              http_status=status, hit_ts=hit_ts,
                              record_type="redirect", via=via)

            try:
                result = resolve.resolve_chain(conn, archive_id, orig_url,
                                               cutoff, on_hop=on_hop)
            except resolve.Miss as miss:
                db.log_access(archive_id, orig_url, cutoff, hit=False,
                              reason=miss.reason, via=via)
                return _info_response("缺档", orig_url, miss.reason, cutoff,
                                      chain=miss.chain)

            rec = result["record"]
            db.log_access(archive_id, orig_url, cutoff, hit=True,
                          http_status=result["status"], hit_ts=rec["ts"],
                          record_type=rec["record_type"], via=via)
            return _build_response(archive_id, cutoff, orig_url, result)
        finally:
            conn.close()

    @app.api_route("/{path:path}",
                   methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    def catch_all(path: str):
        return JSONResponse({"error": "not found", "path": path},
                            status_code=404, headers=_csp_headers())

    return app


def _csp_headers(extra=None):
    headers = {
        "Content-Security-Policy": config.REPLAY_CSP,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if extra:
        headers.update(extra)
    return headers


def _build_response(archive_id, cutoff, orig_url, result):
    rec = result["record"]
    final_url = result["url"]
    payload = result["payload"]
    headers = result["headers"]
    ctype = (headers.get("content-type") or rec["content_type"] or "").split(";")[0].strip().lower()

    wrap = make_wrapper(archive_id, cutoff)
    body = payload

    if ctype == "text/html":
        try:
            html = payload.decode("utf-8", errors="replace")
            html = rewrite_html(html, final_url, wrap)
            banner = banner_html(orig_url, final_url, rec["ts"],
                                 len(result["chain"]), result["base_note"])
            body = inject_head(html, wrap.prefix, banner).encode("utf-8")
        except Exception:
            body = payload  # 改写失败时退回原始内容，仍离线可用
    elif ctype == "text/css" or (not ctype and final_url.lower().endswith(".css")):
        # CSS 相对资源按样式表自身 URL 解析（跨 URL revisit 亦然），保留查询串
        css = payload.decode("utf-8", errors="replace")
        body = rewrite_css(css, final_url, wrap).encode("utf-8")

    resp_headers = {"Content-Type": headers.get("content-type") or ctype or "application/octet-stream"}
    # 保留少量安全的原始头
    for name, value in headers.items():
        if name in HOP_BY_HOP or name.lower() == "content-type":
            continue
        if name.lower().startswith(("x-archive", "content-security")):
            continue
        resp_headers[name] = value

    status = result["status"]
    if status < 200 or status in (204, 304) or status >= 600:
        status = 200  # 归档状态无法直接回放时降级为 200

    return Response(
        content=body,
        status_code=status,
        headers=_csp_headers({
            **resp_headers,
            "X-Archive-Orig-URL": final_url,
            "X-Archive-Date": rec["ts"],
        }),
    )


def _info_response(title, orig_url, reason, cutoff, chain=None):
    """缺档/错误说明页：列出原址与缺档原因（及重定向链）。"""
    rows = [
        ("原址", orig_url),
        ("截止时间", str(cutoff)),
        ("缺档原因", reason),
    ]
    chain_html = ""
    if chain:
        hops = "".join(
            "<li>[%s] %s &rarr; %s</li>" % (
                h["status"], escape(h["url"]), escape(h["location"]))
            for h in chain
        )
        chain_html = f"<h3>重定向链</h3><ol>{hops}</ol>"
    items = "".join(
        f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in rows
    )
    html = f"""<!doctype html><meta charset="utf-8">
<title>{escape(title)}</title>
<body style="font:14px/1.7 sans-serif;max-width:900px;margin:40px auto;padding:0 16px">
<h2>⚠ {escape(title)}</h2>
<table border="1" cellspacing="0" cellpadding="6"
       style="border-collapse:collapse;word-break:break-all">{items}</table>
{chain_html}
<p style="color:#666">该请求已记入管理端的缺档清单。</p>
</body>"""
    return HTMLResponse(html, status_code=404, headers=_csp_headers())
