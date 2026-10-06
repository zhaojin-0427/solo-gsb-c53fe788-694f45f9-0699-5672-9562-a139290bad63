"""生成覆盖各回放规则的样例 WARC：

- 入口 http://example.com/ 301 -> /home（重定向跟随）
- /home：HTML 含 <base>、相对 link/img/srcset、内联 style（带查询串）、绝对脚本地址、表单
- style.css：相对 url()、@import、带查询串的字体引用
- print.css：跨 URL revisit（复用 style.css 正文），其图片应按 print.css 自身地址解析
- loop-a <-> loop-b：重定向环
- future：只有晚于截止时间的记录（不借未来版本）
- orphan：revisit 无基体
- search?q=test：带查询串的入口
- app.js：脚本拼接绝对 URL 的 GET（由 shim 拦截）
"""
import base64
import hashlib
import io
import sys
from datetime import datetime, timedelta, timezone

from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

T0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def ts(delta_seconds=0, delta_days=0):
    return (T0 + timedelta(seconds=delta_seconds, days=delta_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def payload_digest(body):
    return "sha1:" + base64.b32encode(hashlib.sha1(body).digest()).decode("ascii")


STYLE_CSS = b"""body { background: url('img/bg.png'); }
@import url("theme.css");
.icon { src: url(/static/fonts/a.woff2?v=1.2); }
"""

THEME_CSS = b".t { background: url('../img/theme-bg.png'); }"

HOME_HTML = b"""<!doctype html>
<html><head>
<base href="http://example.com/home">
<link rel="stylesheet" href="static/style.css">
<link rel="stylesheet" href="http://example.com/assets/print.css">
<script src="http://example.com/app.js"></script>
<style>body{background:url('img/inline-bg.png?size=small')}</style>
</head><body>
<img src="img/logo.png">
<img srcset="img/logo.png 1x, img/logo@2x.png 2x">
<a href="/next">next</a>
<a href="http://example.com/search?q=test">search</a>
<form action="/submit" method="post"><button>go</button></form>
</body></html>"""

APP_JS = b"""fetch('http://example.com/api/data.json').then(function(r){return r.json();});
var x = new XMLHttpRequest();
x.open('GET', 'https://example.com/api/data.json');
x.send();
"""

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def build_records():
    """(url, status, headers, body, date, extra_warc_headers, record_type)"""
    R = []

    def add(url, status, ctype, body, date, **kw):
        R.append((url, status, ctype, body, date, kw))

    add("http://example.com/", 301, "text/plain", b"", ts(),
        http_headers=[("Location", "/home")])
    add("http://example.com/home", 200, "text/html; charset=utf-8", HOME_HTML, ts(1))
    add("http://example.com/static/style.css", 200, "text/css", STYLE_CSS, ts(2))
    add("http://example.com/static/theme.css", 200, "text/css", THEME_CSS, ts(2))
    add("http://example.com/app.js", 200, "application/javascript", APP_JS, ts(2))
    add("http://example.com/api/data.json", 200, "application/json",
        b'{"ok": true}', ts(2))
    add("http://example.com/img/logo.png", 200, "image/png", PNG, ts(2))
    add("http://example.com/img/logo@2x.png", 200, "image/png", PNG, ts(2))
    add("http://example.com/img/inline-bg.png?size=small", 200, "image/png",
        PNG, ts(2))
    add("http://example.com/static/img/bg.png", 200, "image/png", b"STATIC-BG", ts(2))
    add("http://example.com/assets/img/bg.png", 200, "image/png", b"ASSETS-BG", ts(2))
    add("http://example.com/static/fonts/a.woff2?v=1.2", 200, "font/woff2",
        b"WOFF2", ts(2))
    add("http://example.com/img/theme-bg.png", 200, "image/png", PNG, ts(2))
    add("http://example.com/next", 200, "text/html", b"<html><body>next page</body></html>", ts(2))
    add("http://example.com/search?q=test", 200, "text/html",
        b"<html><body>search results</body></html>", ts(2))
    # 重定向环
    add("http://example.com/loop-a", 301, "text/plain", b"", ts(),
        http_headers=[("Location", "/loop-b")])
    add("http://example.com/loop-b", 301, "text/plain", b"", ts(),
        http_headers=[("Location", "/loop-a")])
    # 仅有未来记录
    add("http://example.com/future", 200, "text/html",
        b"<html><body>future</body></html>", ts(delta_days=10))
    # 跨 URL revisit：print.css 复用 style.css 的正文
    add("http://example.com/assets/print.css", 200, "text/css", None, ts(3),
        record_type="revisit", digest=payload_digest(STYLE_CSS))
    # revisit 无基体
    add("http://example.com/orphan", 200, "text/html", None, ts(3),
        record_type="revisit",
        digest="sha1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    return R


def write_sample(path):
    records = build_records()
    with open(path, "wb") as fh:
        writer = WARCWriter(fh, gzip=True)
        for url, status, ctype, body, date, kw in records:
            rtype = kw.get("record_type", "response")
            http_headers = [("Content-Type", ctype)] + kw.get("http_headers", [])
            reason = {200: "OK", 301: "Moved Permanently"}.get(status, "OK")
            headers = StatusAndHeaders(
                f"{status} {reason}", http_headers, protocol="HTTP/1.1")
            warc_headers = {"WARC-Date": date}
            if rtype == "revisit":
                warc_headers["WARC-Payload-Digest"] = kw["digest"]
                warc_headers["WARC-Profile"] = (
                    "http://netpreserve.org/warc/1.0/revisit/"
                    "identical-payload-digest")
                warc_headers["WARC-Refers-To"] = (
                    "<urn:uuid:00000000-0000-0000-0000-000000000000>")
                payload = io.BytesIO(b"")
            else:
                warc_headers["WARC-Payload-Digest"] = payload_digest(body)
                payload = io.BytesIO(body)
            rec = writer.create_warc_record(
                url, rtype, payload=payload, http_headers=headers,
                warc_headers_dict=warc_headers,
                warc_content_type="application/http; msgtype=response",
            )
            writer.write_record(rec)
    return len(records)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample.warc.gz"
    n = write_sample(out)
    print(f"wrote {out} with {n} records")
