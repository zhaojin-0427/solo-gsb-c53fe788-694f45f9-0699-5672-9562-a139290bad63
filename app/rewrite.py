"""内容改写：

- HTML：相对引用按最终页面 URL 或其 <base> 解析，改写成回放绝对地址；
  注入运行时 shim，拦截脚本拼接的绝对 URL 的 GET 请求，同样从归档加载。
- CSS：url()/ @import 按样式表自身 URL 解析（保留查询串）；
  跨 URL revisit 复用来的 CSS，其图片仍按该 CSS 自己的地址解析。
"""
import json
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import quote, urljoin

# 会被改写的 URL 属性（data 仅对 <object> 有意义，误伤可忽略）
URL_ATTRS = {
    "href", "src", "action", "formaction", "poster", "background",
    "longdesc", "cite", "manifest", "data",
}
SRCSET_ATTRS = {"srcset", "imagesrcset"}
SKIP_PREFIXES = (
    "javascript:", "mailto:", "tel:", "sms:", "data:", "blob:",
    "about:", "chrome:", "ftp:", "#",
)

# 注入到页面最前端的运行时 shim：拦截 fetch/XHR/sendBeacon，
# 把脚本拼接的绝对 http(s) URL 改指到回放端点（非 GET 由回放端 405 阻止）。
SHIM_JS = r"""
(function () {
  var PREFIX = window.__REPLAY_PREFIX__;
  if (!PREFIX || window.__replayShimInstalled) return;
  window.__replayShimInstalled = true;
  function wrap(u) {
    if (typeof u !== "string") return u;
    var t = u.replace(/^\s+|\s+$/g, "");
    if (!t || t.charAt(0) === "#") return u;
    if (t.indexOf(PREFIX) === 0) return u;
    if (/^(https?:)?\/\//i.test(t) || /^https?:/i.test(t)) {
      var a;
      try { a = new URL(t, location.href); } catch (e) { return u; }
      if (a.origin === location.origin) return u;
      return PREFIX + encodeURIComponent(a.href);
    }
    return u;
  }
  window.__replayWrap = wrap;
  if (window.fetch) {
    var ofetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      try {
        if (typeof input === "string") input = wrap(input);
        else if (input && input.url) input = new Request(wrap(input.url), input);
      } catch (e) {}
      return ofetch(input, init);
    };
  }
  var oopen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u) {
    var args = Array.prototype.slice.call(arguments);
    try { args[1] = wrap(u); } catch (e) {}
    return oopen.apply(this, args);
  };
  if (navigator.sendBeacon) {
    var obeacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = function (u, d) {
      try { u = wrap(u); } catch (e) {}
      return obeacon(u, d);
    };
  }
})();
""".strip()

CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
CSS_IMPORT_RE = re.compile(
    r"@import\s+(?:url\(\s*)?(['\"])([^'\"]+)\1\s*\)?([^;]*);", re.I)


def make_wrapper(archive_id, cutoff_epoch):
    """生成 原始绝对 URL -> 回放地址 的包装函数。"""
    prefix = f"/web/{archive_id}/{cutoff_epoch}/"

    def wrap(abs_url):
        return prefix + quote(abs_url, safe="")

    wrap.prefix = prefix
    return wrap


def _is_skippable(url):
    low = url.strip().lower()
    return not low or low.startswith(SKIP_PREFIXES)


def resolve_and_wrap(url, base, wrap):
    """相对引用按 base 解析成绝对 URL 后包装；非 http(s) 原样返回。"""
    if _is_skippable(url):
        return url
    if url.strip().startswith(wrap.prefix):
        return url  # 已是回放地址
    abs_url = urljoin(base, url.strip())
    if not abs_url.lower().startswith(("http://", "https://")):
        return url
    return wrap(abs_url)


def rewrite_css(text, css_url, wrap):
    """CSS 中的 url() 与 @import 按样式表 URL（css_url）解析，保留查询串。"""

    def fix(u):
        u = u.strip()
        if _is_skippable(u):
            return u
        if u.startswith(wrap.prefix):
            return u  # 已被 @import 规则改写，避免二次包装
        abs_url = urljoin(css_url, u)  # urljoin 保留相对引用自带的查询串
        if not abs_url.lower().startswith(("http://", "https://")):
            return u
        return wrap(abs_url)

    def sub_url(m):
        q = m.group(1) or ""
        return "url(%s%s%s)" % (q, fix(m.group(2)), q)

    def sub_import(m):
        return "@import url(%s%s%s)%s;" % (
            m.group(1), fix(m.group(2)), m.group(1), m.group(3))

    text = CSS_URL_RE.sub(sub_url, text)
    text = CSS_IMPORT_RE.sub(sub_import, text)
    return text


def rewrite_srcset(value, base, wrap):
    if "data:" in value:  # data URI 含逗号，放弃解析以免破坏
        return value
    out = []
    for cand in value.split(","):
        cand = cand.strip()
        if not cand:
            continue
        tokens = cand.split()
        tokens[0] = resolve_and_wrap(tokens[0], base, wrap)
        out.append(" ".join(tokens))
    return ", ".join(out)


class HTMLRewriter(HTMLParser):
    def __init__(self, page_url, wrap):
        super().__init__(convert_charrefs=False)
        self.base = page_url  # 初始为最终页面 URL；遇到 <base> 后更新
        self.wrap = wrap
        self.out = []
        self.in_style = False

    # -- 标签 --
    def handle_starttag(self, tag, attrs):
        self._emit_tag(tag, attrs, selfclose=False)

    def handle_startendtag(self, tag, attrs):
        self._emit_tag(tag, attrs, selfclose=True)

    def _emit_tag(self, tag, attrs, selfclose):
        t = tag.lower()
        new_attrs = []
        for name, value in attrs:
            lname = name.lower()
            if value is None:
                new_attrs.append((name, None))
                continue
            if lname in URL_ATTRS:
                if t == "base" and lname == "href":
                    # 后续相对引用按 base 解析
                    self.base = urljoin(self.base, value.strip())
                    value = self.wrap(self.base)
                else:
                    value = resolve_and_wrap(value, self.base, self.wrap)
            elif lname in SRCSET_ATTRS:
                value = rewrite_srcset(value, self.base, self.wrap)
            elif lname == "style":
                value = rewrite_css(value, self.base, self.wrap)
            new_attrs.append((name, value))

        s = "<" + tag
        for name, value in new_attrs:
            if value is None:
                s += " " + name
            else:
                s += ' %s="%s"' % (name, escape(value, quote=True))
        s += "/>" if selfclose else ">"
        self.out.append(s)
        if t == "style" and not selfclose:
            self.in_style = True

    def handle_endtag(self, tag):
        self.out.append("</%s>" % tag)
        if tag.lower() == "style":
            self.in_style = False

    # -- 文本与其它节点 --
    def handle_data(self, data):
        if self.in_style:
            self.out.append(rewrite_css(data, self.base, self.wrap))
        else:
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

    def result(self):
        return "".join(self.out)


def rewrite_html(html, page_url, wrap):
    rw = HTMLRewriter(page_url, wrap)
    rw.feed(html)
    rw.close()
    return rw.result()


def inject_head(html, prefix, banner_html):
    """把 shim 与横幅注入 <head> 之后（无 head 则置于文档最前），
    保证 shim 先于页面脚本执行。"""
    inj = (
        "<script>window.__REPLAY_PREFIX__=%s;</script>" % json.dumps(prefix)
        + "<script>" + SHIM_JS + "</script>"
        + (banner_html or "")
    )
    m = re.search(r"<head[^>]*>", html, re.I)
    if m:
        return html[: m.end()] + inj + html[m.end():]
    m = re.search(r"<html[^>]*>", html, re.I)
    if m:
        return html[: m.end()] + inj + html[m.end():]
    return inj + html


def banner_html(orig_url, final_url, hit_ts, hops, base_note):
    """页面顶部信息条：原址、命中采集时间、重定向跳数。"""
    items = [
        ("原址", orig_url),
        ("命中采集时间", hit_ts),
    ]
    if final_url != orig_url:
        items.append(("最终地址", final_url))
    if hops:
        items.append(("重定向", f"{hops} 跳"))
    if base_note:
        items.append(("revisit", base_note))
    body = " · ".join(
        '<span style="opacity:.75">%s:</span> %s' % (escape(k), escape(str(v)))
        for k, v in items
    )
    return (
        '<div id="__warc-banner" style="all:initial;display:block;position:sticky;'
        "top:0;z-index:2147483647;background:#1f2933;color:#e4e7eb;"
        "font:12px/1.6 monospace;padding:4px 10px;border-bottom:1px solid #52606d;"
        'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
        "&#8634; 离线回放 " + body + "</div>"
    )
