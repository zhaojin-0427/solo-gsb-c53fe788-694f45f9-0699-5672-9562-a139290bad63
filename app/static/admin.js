/* WARC 离线回放台 管理界面逻辑（原生 JS，无外部依赖） */
'use strict';

var previewOrigin = null;

function api(path, options) {
  return fetch(path, options).then(function (r) {
    if (!r.ok) return r.text().then(function (t) { throw new Error(r.status + ' ' + t); });
    return r.json();
  });
}

function esc(s) {
  var d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function fmtEpoch(epoch) {
  if (!epoch) return '';
  return new Date(epoch * 1000).toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC');
}

/* ---------- 档案 ---------- */
function loadArchives() {
  return api('/api/archives').then(function (rows) {
    var tb = document.querySelector('#archives tbody');
    tb.innerHTML = rows.map(function (r) {
      var status = r.status === 'ready' ? '就绪'
        : r.status === 'failed' ? '失败：' + esc(r.error || '')
        : '导入中…';
      return '<tr><td>' + r.id + '</td><td>' + esc(r.filename) + '</td><td class="st-' + r.status + '">' +
        status + '</td><td>' + r.record_count + '</td><td>' + esc(r.imported_at || '') +
        '</td><td><button data-id="' + r.id + '" class="del">删除</button></td></tr>';
    }).join('');
    var importing = rows.some(function (r) { return r.status === 'importing'; });
    if (importing) setTimeout(loadArchives, 1500);
  });
}

document.getElementById('upload-form').addEventListener('submit', function (e) {
  e.preventDefault();
  var f = document.getElementById('file').files[0];
  if (!f) return;
  var st = document.getElementById('upload-status');
  st.textContent = '上传中…';
  var fd = new FormData();
  fd.append('file', f);
  api('/api/archives', { method: 'POST', body: fd })
    .then(function () { st.textContent = '已上传，导入中…'; loadArchives(); })
    .catch(function (err) { st.textContent = '上传失败：' + err.message; });
});

document.querySelector('#archives tbody').addEventListener('click', function (e) {
  if (!e.target.classList.contains('del')) return;
  if (!confirm('删除该档案及其全部记录？')) return;
  api('/api/archives/' + e.target.dataset.id, { method: 'DELETE' }).then(loadArchives);
});

/* ---------- 打开回放 ---------- */
function toTs14(d) {
  function p(n) { return String(n).padStart(2, '0'); }
  return d.getUTCFullYear() + p(d.getUTCMonth() + 1) + p(d.getUTCDate()) +
    p(d.getUTCHours()) + p(d.getUTCMinutes()) + p(d.getUTCSeconds());
}

document.getElementById('replay-form').addEventListener('submit', function (e) {
  e.preventDefault();
  var url = document.getElementById('entry-url').value.trim();
  if (url && !/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(url)) url = 'http://' + url;
  var cv = document.getElementById('cutoff').value;
  var d = cv ? new Date(cv) : new Date();
  window.open(previewOrigin + '/replay/' + toTs14(d) + '/' + url, '_blank');
});

/* ---------- 捕获查询 ---------- */
document.getElementById('captures-form').addEventListener('submit', function (e) {
  e.preventDefault();
  var url = document.getElementById('cap-url').value.trim();
  api('/api/captures?url=' + encodeURIComponent(url)).then(function (rows) {
    document.querySelector('#captures tbody').innerHTML = rows.length
      ? rows.map(function (r) {
          return '<tr><td>' + esc(r.ts) + '</td><td>' + esc(r.type) + '</td><td>' +
            (r.status == null ? '' : r.status) + '</td><td>' + (r.size == null ? '' : r.size) +
            '</td><td class="digest">' + esc(r.digest || '') + '</td></tr>';
        }).join('')
      : '<tr><td colspan="5">无捕获</td></tr>';
  });
});

/* ---------- 缺档清单 ---------- */
function loadMissing() {
  api('/api/missing').then(function (rows) {
    document.querySelector('#missing tbody').innerHTML = rows.length
      ? rows.map(function (r) {
          return '<tr><td>' + fmtEpoch(r.epoch) + '</td><td>' + esc(r.url) + '</td><td>' +
            esc(r.reason) + '</td><td>' + esc(r.referer || '') + '</td></tr>';
        }).join('')
      : '<tr><td colspan="4">暂无缺档</td></tr>';
  });
}

document.getElementById('missing-refresh').addEventListener('click', loadMissing);
document.getElementById('missing-clear').addEventListener('click', function () {
  api('/api/missing', { method: 'DELETE' }).then(loadMissing);
});

/* ---------- 启动 ---------- */
api('/api/config').then(function (cfg) {
  previewOrigin = location.protocol + '//' + location.hostname + ':' + cfg.preview_port;
  loadArchives();
  loadMissing();
});
