"""HTML/CSS 重写：把页面内引用改写到回放源，并注入补丁脚本与信息横幅。

解析基准：
- HTML 相对引用按最终页面 URL 或其 <base> 解析；
- CSS 相对资源按样式表自身 URL 解析，查询串保留；
- 跨 URL revisit 的 CSS 仍按该 CSS 自己的地址解析（调用方传入的即自身 URL）。
"""
import html as html_mod
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

SKIP_PREFIXES = ("data:", "javascript:", "mailto:", "tel:", "about:", "blob:", "ws:", "wss:")

# 需要改写的标签属性
URL_ATTRS = {
    "a": ("href",), "link": ("href",), "area": ("href",),
    "img": ("src",), "script": ("src",), "iframe": ("src",), "frame": ("src",),
    "embed": ("src",), "video": ("src", "poster"), "audio": ("src",),
    "source": ("src",), "track": ("src",), "input": ("src", "formaction"),
    "form": ("action",), "button": ("formaction",), "object": ("data",),
    "body": ("background",), "table": ("background",),
    "td": ("background",), "th": ("background",),
}

CSS_URL_RE = re.compile(r"""url\(\s*(["']?)(.*?)\1\s*\)""", re.S)
CSS_IMPORT_RE = re.compile(r"""@import\s+(?:url\(\s*)?(["'])(.*?)\1\s*\)?""", re.I)
BASE_HREF_RE = re.compile(r"""<base[^>]+href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
META_REFRESH_URL_RE = re.compile(r"(?i)(url\s*=\s*)(.+)$")


def replay_url(ts, abs_url):
    return "/replay/%s/%s" % (ts, abs_url)


def should_skip(url):
    url = url.strip()
    return not url or url.startswith("#") or url.lower().startswith(SKIP_PREFIXES)


def rewrite_css(text, css_url, ts):
    """按样式表自身 URL 解析相对资源；url() 里的查询串由 urljoin 原样保留。"""

    def repl_import(m):
        quote, target = m.group(1), m.group(2)
        if should_skip(target) or target.startswith("/replay/"):
            return m.group(0)
        return "@import %s%s%s" % (quote, replay_url(ts, urljoin(css_url, target)), quote)

    def repl_url(m):
        quote, target = m.group(1), m.group(2)
        if should_skip(target) or target.startswith("/replay/"):
            return m.group(0)
        quote = quote or ""
        return "url(%s%s%s)" % (quote, replay_url(ts, urljoin(css_url, target)), quote)

    text = CSS_IMPORT_RE.sub(repl_import, text)
    return CSS_URL_RE.sub(repl_url, text)


def rewrite_srcset(value, base_url, ts):
    out = []
    for cand in value.split(","):
        cand = cand.strip()
        if not cand:
            continue
        parts = cand.split()
        if not should_skip(parts[0]):
            parts[0] = replay_url(ts, urljoin(base_url, parts[0]))
        out.append(" ".join(parts))
    return ", ".join(out)


def find_base_url(text, page_url):
    """取文档里第一个 <base href>，按页面 URL 解析为绝对地址。"""
    m = BASE_HREF_RE.search(text)
    if not m:
        return page_url, False
    href = next(g for g in m.groups() if g is not None)
    href = html_mod.unescape(href).strip()
    if should_skip(href):
        return page_url, False
    return urljoin(page_url, href), True


# 注入到 <head> 最前端的补丁脚本：脚本拼接的绝对 URL GET 也走归档，非 GET 阻止。
PATCH_SCRIPT = """<script>(function(){
'use strict';
var PREFIX = location.origin + '/replay/__TS__/';
function route(u) {
  if (typeof u !== 'string') return u;
  if (!/^(https?:)?\\/\\//i.test(u)) return u;
  try {
    var abs = new URL(u, document.baseURI);
    if (abs.origin === location.origin) return u;
    if (abs.protocol === 'http:' || abs.protocol === 'https:') return PREFIX + abs.href;
  } catch (e) {}
  return u;
}
if (window.fetch) {
  var origFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    init = init || {};
    var method = init.method || (input && input.method) || 'GET';
    if (String(method).toUpperCase() !== 'GET') {
      return Promise.reject(new TypeError('WARC 回放：已阻止非 GET 请求 (' + method + ')'));
    }
    if (typeof input === 'string') input = route(input);
    else if (input && input.url) input = new Request(route(input.url), input);
    return origFetch(input, init);
  };
}
var origOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url) {
  if (String(method || 'GET').toUpperCase() !== 'GET') {
    throw new TypeError('WARC 回放：已阻止非 GET 请求 (' + method + ')');
  }
  arguments[1] = route(url);
  return origOpen.apply(this, arguments);
};
if (navigator.sendBeacon) navigator.sendBeacon = function() { return false; };
window.WebSocket = function() { throw new TypeError('WARC 回放：已阻止 WebSocket'); };
window.EventSource = function() { throw new TypeError('WARC 回放：已阻止 EventSource'); };
})();</script>"""

BANNER = """<div id="__warc_banner" style="position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#1f2733;color:#e8edf3;font:13px/1.5 sans-serif;padding:6px 10px;border-bottom:2px solid #4a90d9;box-sizing:border-box;">
<strong>WARC 回放</strong>
入口 <code style="color:#9ecbff">__ENTRY__</code> &rarr; 最终 <code style="color:#9ecbff">__FINAL__</code>
&middot; 命中 <code style="color:#9ecbff">__HITTS__</code> &middot; __HOPS__ 跳
&middot; 缺档 <strong id="__warc_miss_count" style="color:#ff9c9c">0</strong>
<details style="display:inline-block;margin-left:8px;"><summary style="cursor:pointer;display:inline">详情</summary>
<div style="max-height:40vh;overflow:auto;background:#141a22;padding:6px 10px;border:1px solid #3a4656;position:absolute;left:0;right:0;">
<div>__CHAIN__</div>
<div id="__warc_miss_list" style="margin-top:4px"></div>
</div></details>
</div>
<script>(function(){
var since = __SINCE__;
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function refresh(){
  fetch('/__api/missing?since=' + since).then(function(r){return r.json();}).then(function(rows){
    document.getElementById('__warc_miss_count').textContent = rows.length;
    var el = document.getElementById('__warc_miss_list');
    el.innerHTML = rows.length
      ? rows.map(function(r){return '<div style="color:#ffb3b3">&#10007; '+esc(r.url)+' &mdash; '+esc(r.reason)+'</div>';}).join('')
      : '<div style="color:#8fd18f">本页暂无缺档</div>';
  }).catch(function(){});
}
refresh(); setInterval(refresh, 2000);
})();</script>"""


class HTMLRewriter(HTMLParser):
    def __init__(self, page_url, base_url, has_base, ts):
        super().__init__(convert_charrefs=False)
        self.page_url = page_url
        self.base_url = base_url
        self.has_base = has_base
        self.ts = ts
        self.out = []
        self.head_seen = False
        self.banner_injected = False

    # ---- 内部 ----
    def rewrite_link(self, value):
        if value is None or should_skip(value):
            return value
        return replay_url(self.ts, urljoin(self.base_url, value.strip()))

    def render_attrs(self, tag, attrs):
        is_refresh = tag == "meta" and any(
            k and k.lower() == "http-equiv" and (v or "").lower() == "refresh"
            for k, v in attrs
        )
        parts = []
        for k, v in attrs:
            low = k.lower() if k else ""
            if v is not None:
                if low in URL_ATTRS.get(tag, ()):
                    v = self.rewrite_link(v)
                elif low == "srcset":
                    v = rewrite_srcset(v, self.base_url, self.ts)
                elif low == "style":
                    v = rewrite_css(v, self.base_url, self.ts)
                elif tag == "base" and low == "href" and not should_skip(v):
                    # base 指向原 base 绝对地址的回放 URL，保证脚本拼接的相对引用也落在归档内
                    v = replay_url(self.ts, urljoin(self.page_url, v.strip()))
                elif is_refresh and low == "content":
                    v = META_REFRESH_URL_RE.sub(
                        lambda m: m.group(1) + self.rewrite_link(m.group(2).strip()), v
                    )
                parts.append(' %s="%s"' % (k, html_mod.escape(v, quote=True)))
            else:
                parts.append(" %s" % k)
        return "".join(parts)

    def inject_head_extras(self):
        if not self.has_base:
            # 无 base 时注入指向最终页面 URL 的 base，使相对引用语义与原站一致
            self.out.append(
                '<base href="%s">' % html_mod.escape(replay_url(self.ts, self.page_url), quote=True)
            )
        self.out.append(PATCH_SCRIPT.replace("__TS__", self.ts))

    # ---- HTMLParser 事件 ----
    def handle_starttag(self, tag, attrs):
        if not self.head_seen and tag not in ("html", "head"):
            # 文档没有 <head>：在第一个实际元素前注入 base 与补丁脚本
            self.head_seen = True
            self.inject_head_extras()
        self.out.append("<%s%s>" % (tag, self.render_attrs(tag, attrs)))
        if tag == "head" and not self.head_seen:
            self.head_seen = True
            self.inject_head_extras()
        if tag == "body" and not self.banner_injected:
            self.banner_injected = True
            self.out.append("__BANNER__")

    def handle_startendtag(self, tag, attrs):
        self.out.append("<%s%s/>" % (tag, self.render_attrs(tag, attrs)))

    def handle_endtag(self, tag):
        if tag == "html" and not self.banner_injected:
            self.banner_injected = True
            self.out.append("__BANNER__")
        self.out.append("</%s>" % tag)

    def handle_data(self, data):
        self.out.append(data)

    def handle_comment(self, data):
        self.out.append("<!--%s-->" % data)

    def handle_decl(self, decl):
        self.out.append("<!%s>" % decl)

    def handle_pi(self, data):
        self.out.append("<?%s>" % data)

    def handle_entityref(self, name):
        self.out.append("&%s;" % name)

    def handle_charref(self, name):
        self.out.append("&#%s;" % name)

    def get_output(self):
        if not self.banner_injected:
            self.out.append("__BANNER__")
            self.banner_injected = True
        return "".join(self.out)


def build_banner(entry_url, chain, since):
    final = chain[-1]
    rows = []
    for hop in chain:
        line = "%s &rarr; <code>%s</code> @ %s" % (
            hop["status"] if hop["status"] is not None else "-",
            html_mod.escape(hop["url"]),
            html_mod.escape(hop["ts"]),
        )
        if hop["type"] == "revisit":
            line += "（revisit，正文取自 <code>%s</code> @ %s）" % (
                html_mod.escape(hop["base_url"] or "?"),
                html_mod.escape(hop["base_ts"] or "?"),
            )
        rows.append("<div>%s</div>" % line)
    banner = BANNER
    banner = banner.replace("__ENTRY__", html_mod.escape(entry_url))
    banner = banner.replace("__FINAL__", html_mod.escape(final["url"]))
    banner = banner.replace("__HITTS__", html_mod.escape(final["ts"]))
    banner = banner.replace("__HOPS__", str(len(chain)))
    banner = banner.replace("__CHAIN__", "".join(rows))
    banner = banner.replace("__SINCE__", repr(since))
    return banner


def rewrite_html(text, entry_url, final_url, ts, chain, since):
    """重写 HTML：属性链接走回放源，注入 base/补丁脚本/信息横幅。"""
    base_url, has_base = find_base_url(text, final_url)
    rewriter = HTMLRewriter(final_url, base_url, has_base, ts)
    rewriter.feed(text)
    rewriter.close()
    out = rewriter.get_output()
    return out.replace("__BANNER__", build_banner(entry_url, chain, since))
