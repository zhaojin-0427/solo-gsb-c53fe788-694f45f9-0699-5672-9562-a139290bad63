"""生成示例 sample.warc.gz，覆盖回放台的主要场景：

- 入口 http://example.com/ 301 跳转到 /index
- /index 有两个时间的捕获（验证"不晚于截止时间的最近记录"）
- CSS 相对图片（带查询串）、@import、srcset、inline style、<base> 页面
- 跨 URL revisit（/other 复用 /page2.html 正文）
- 跨 URL 的 CSS revisit（cdn 上的 theme.css 复用 style.css 正文）
- 重定向环 /loop1 <-> /loop2
- 只有未来捕获的 /future-only（截止前请求应标缺）
- 无基体的 revisit /orphan（应标缺）
"""
import base64
import hashlib
import io
import os

from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.warc.gz")

T2020 = "2020-06-01T12:00:00Z"
T2021 = "2021-06-01T12:00:00Z"
T2030 = "2030-06-01T12:00:00Z"


def digest_of(body):
    return "sha1:" + base64.b32encode(hashlib.sha1(body).digest()).decode("ascii")


def add_response(writer, url, status, ctype, body, date, extra_headers=None):
    headers = [("Content-Type", ctype), ("Content-Length", str(len(body)))]
    if extra_headers:
        headers.extend(extra_headers)
    http = StatusAndHeaders(status, headers, protocol="HTTP/1.1")
    record = writer.create_warc_record(
        url, "response", payload=io.BytesIO(body), http_headers=http,
        warc_headers_dict={"WARC-Date": date},
    )
    writer.write_record(record)


def add_revisit(writer, url, date, orig_url, orig_date, digest, ctype):
    http = StatusAndHeaders("200 OK", [("Content-Type", ctype)], protocol="HTTP/1.1")
    record = writer.create_warc_record(
        url, "revisit", payload=io.BytesIO(b""), http_headers=http,
        warc_headers_dict={
            "WARC-Date": date,
            "WARC-Profile": "http://netpreserve.org/warc/1.0/revisit/identical-payload-digest",
            "WARC-Refers-To-Target-URI": orig_url,
            "WARC-Refers-To-Date": orig_date,
            "WARC-Payload-Digest": digest,
        },
    )
    writer.write_record(record)


INDEX_2020 = b"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Index 2020</title>
<link rel="stylesheet" href="style.css">
<script src="http://example.com/app.js"></script>
</head><body>
<h1>Index (2020 capture)</h1>
<img src="img/top.png" srcset="img/top.png 1x, img/top@2x.png 2x">
<div style="background: url('img/bg.png')"></div>
<a href="other">other (revisit)</a>
<a href="basepage">base page</a>
<a href="loop1">loop</a>
<a href="future-only">future only</a>
<a href="altpage">alt page (css revisit)</a>
</body></html>"""

INDEX_2030 = b"<html><body><h1>Index (2030 capture, should NOT appear)</h1></body></html>"

STYLE_CSS = b"""/* style.css */
@import 'reset.css';
.hero { background: url(img/logo.png?v=2) no-repeat; }
@font-face { src: url("fonts/a.woff2?x=1"); }
"""

RESET_CSS = b"html,body{margin:0;background:url(../img/reset-bg.png)}"

BASEPAGE = b"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><base href="http://example.com/sub/"><title>base</title></head>
<body><h1>page with base</h1><img src="pic.png"><a href="next.html">next</a></body></html>"""

PAGE2 = b"<html><head><title>page2</title></head><body><h1>page2 body (reused by /other)</h1></body></html>"

ALTPAGE = b"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>alt</title>
<link rel="stylesheet" href="http://cdn.other.com/css/theme.css">
</head><body><h1>alt page uses revisited css</h1></body></html>"""

APP_JS = """// app.js: 脚本拼接的绝对 URL GET 也应从归档加载
fetch('http://example.com/api/data.json').then(function(r){return r.json();}).then(function(d){
  document.title = 'json:' + d.ok;
});
fetch('http://example.com/api/submit', {method: 'POST'}).catch(function(e){
  console.log('blocked as expected', e);
});
""".encode("utf-8")

DATA_JSON = b'{"ok": true}'

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c626001000000ffff030000060005"
    "57bfabd40000000049454e44ae426082"
)


def main():
    with open(OUT, "wb") as fh:
        writer = WARCWriter(fh, gzip=True)

        # 入口重定向链
        add_response(writer, "http://example.com/", "301 Moved Permanently",
                     "text/html; charset=utf-8", b"<html>redirect</html>", T2020,
                     extra_headers=[("Location", "/index")])
        add_response(writer, "http://example.com/index", "200 OK",
                     "text/html; charset=utf-8", INDEX_2020, T2020)
        add_response(writer, "http://example.com/index", "200 OK",
                     "text/html; charset=utf-8", INDEX_2030, T2030)

        # 样式与静态资源
        add_response(writer, "http://example.com/style.css", "200 OK",
                     "text/css", STYLE_CSS, T2020)
        add_response(writer, "http://example.com/reset.css", "200 OK",
                     "text/css", RESET_CSS, T2020)
        add_response(writer, "http://example.com/img/logo.png?v=2", "200 OK",
                     "image/png", PNG, T2020)
        add_response(writer, "http://example.com/img/top.png", "200 OK",
                     "image/png", PNG, T2020)
        add_response(writer, "http://example.com/img/top@2x.png", "200 OK",
                     "image/png", PNG, T2020)
        add_response(writer, "http://example.com/img/bg.png", "200 OK",
                     "image/png", PNG, T2020)
        add_response(writer, "http://example.com/img/reset-bg.png", "200 OK",
                     "image/png", PNG, T2020)
        add_response(writer, "http://example.com/fonts/a.woff2?x=1", "200 OK",
                     "font/woff2", b"wOF2fake", T2020)
        add_response(writer, "http://example.com/app.js", "200 OK",
                     "application/javascript", APP_JS, T2020)
        add_response(writer, "http://example.com/api/data.json", "200 OK",
                     "application/json", DATA_JSON, T2020)

        # 带 <base> 的页面及其相对资源
        add_response(writer, "http://example.com/basepage", "200 OK",
                     "text/html; charset=utf-8", BASEPAGE, T2020)
        add_response(writer, "http://example.com/sub/pic.png", "200 OK",
                     "image/png", PNG, T2020)
        add_response(writer, "http://example.com/sub/next.html", "200 OK",
                     "text/html; charset=utf-8", b"<html><body>next</body></html>", T2020)

        # 跨 URL revisit：/other 复用 /page2.html 的正文
        add_response(writer, "http://example.com/page2.html", "200 OK",
                     "text/html; charset=utf-8", PAGE2, T2020)
        add_revisit(writer, "http://example.com/other", T2021,
                    "http://example.com/page2.html", T2020,
                    digest_of(PAGE2), "text/html; charset=utf-8")

        # 跨 URL 的 CSS revisit：cdn 上的 theme.css 复用 style.css 正文，
        # 其中相对图片仍按 theme.css 自己的地址解析
        add_response(writer, "http://example.com/altpage", "200 OK",
                     "text/html; charset=utf-8", ALTPAGE, T2020)
        add_revisit(writer, "http://cdn.other.com/css/theme.css", T2021,
                    "http://example.com/style.css", T2020,
                    digest_of(STYLE_CSS), "text/css")
        add_response(writer, "http://cdn.other.com/css/img/logo.png?v=2", "200 OK",
                     "image/png", PNG, T2020)
        add_response(writer, "http://cdn.other.com/reset.css", "200 OK",
                     "text/css", RESET_CSS, T2020)
        add_response(writer, "http://cdn.other.com/img/reset-bg.png", "200 OK",
                     "image/png", PNG, T2020)
        add_response(writer, "http://cdn.other.com/css/fonts/a.woff2?x=1", "200 OK",
                     "font/woff2", b"wOF2fake", T2021)

        # 重定向环
        add_response(writer, "http://example.com/loop1", "302 Found",
                     "text/html", b"", T2020,
                     extra_headers=[("Location", "/loop2")])
        add_response(writer, "http://example.com/loop2", "302 Found",
                     "text/html", b"", T2020,
                     extra_headers=[("Location", "/loop1")])

        # 只有未来捕获的地址
        add_response(writer, "http://example.com/future-only", "200 OK",
                     "text/html", b"<html><body>only in 2030</body></html>", T2030)

        # 无基体的 revisit（摘要不存在于任何响应）
        add_revisit(writer, "http://example.com/orphan", T2020,
                    "http://gone.example.com/orig", "2019-01-01T00:00:00Z",
                    "sha1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "text/html")

    print("written:", OUT)


if __name__ == "__main__":
    main()
