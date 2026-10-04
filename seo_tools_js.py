"""Client-side JavaScript for the free tools (vanilla, inline, no network calls).

Pure functions live on ``GeshTool`` so tests can run them in Node; DOM wiring is guarded
by ``typeof document``. User text is only ever written with ``textContent`` (never
``innerHTML``), so a crafted ``?inputs=`` link cannot inject markup.
"""

from __future__ import annotations

import json

from seo_tools_data import OVERLAP, SECTORS

COMMON_JS = r"""
var GeshTool = (typeof GeshTool !== 'undefined') ? GeshTool : {};
(function (G) {
  'use strict';
  G.parseInputs = function (s) {
    var out = Object.create(null);
    if (!s) return out;
    String(s).split(',').forEach(function (part) {
      var i = part.indexOf(':');
      if (i < 1) return;
      var k = part.slice(0, i).trim();
      if (k && !(k in out)) out[k] = part.slice(i + 1).trim();
    });
    return out;
  };
  G.num = function (v) {
    if (v === null || v === undefined || String(v).trim() === '') return NaN;
    var n = Number(String(v).replace(/[$,%\s]/g, ''));
    return isFinite(n) ? n : NaN;
  };
  G.pct = function (x, d) { return (x * 100).toFixed(d === undefined ? 1 : d) + '%'; };
  G.money = function (x) {
    var neg = x < 0, a = Math.abs(x);
    var s = a.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return (neg ? '-$' : '$') + s;
  };
  G.mulberry32 = function (a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  };
  G.hashSeed = function (s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  };
  if (typeof document !== 'undefined') {
    G.el = function (tag, text, cls) {
      var e = document.createElement(tag);
      if (text !== undefined && text !== null) e.textContent = text;
      if (cls) e.className = cls;
      return e;
    };
    G.table = function (head, rows) {
      var t = G.el('table', null, 't'), thead = G.el('thead'), tr = G.el('tr');
      head.forEach(function (h) { tr.appendChild(G.el('th', h)); });
      thead.appendChild(tr); t.appendChild(thead);
      var tb = G.el('tbody');
      rows.forEach(function (r) {
        var row = G.el('tr');
        r.forEach(function (c) { row.appendChild(G.el('td', c)); });
        tb.appendChild(row);
      });
      t.appendChild(tb); return t;
    };
    G.setup = function (cfg) {
      // cfg: {read(): inputs string|null, fill(obj), run(): void, build(): inputs string}
      var out = document.getElementById('tool-out');
      var status = document.getElementById('share-status');
      function go() { out.textContent = ''; cfg.run(out); }
      document.getElementById('tool-form').addEventListener('submit', function (ev) {
        ev.preventDefault(); go();
      });
      document.getElementById('share-btn').addEventListener('click', function () {
        var url = location.origin + location.pathname + '?inputs=' + encodeURIComponent(cfg.build());
        try { history.replaceState(null, '', url); } catch (e) { /* ignore */ }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(url).then(
            function () { status.textContent = 'Link copied.'; },
            function () { status.textContent = 'Copy the address bar to share.'; });
        } else { status.textContent = 'Copy the address bar to share.'; }
      });
      var q = new URLSearchParams(location.search).get('inputs');
      if (q) { cfg.fill(G.parseInputs(q), q); go(); }
    };
  }
})(GeshTool);
"""

KELLY_JS = r"""
(function (G) {
  'use strict';
  G.kelly = function (pPct, b) {
    var p = pPct / 100;
    if (!(p > 0 && p < 1) || !(b > 0)) {
      return { error: 'Enter a win probability between 0 and 100 and a payoff above 0.' };
    }
    var edge = p - (1 - p) / b;
    var full = Math.max(0, edge);
    return { edge: edge, full: full, half: full / 2, quarter: full / 4, hasEdge: edge > 0 };
  };
  if (typeof document === 'undefined') return;
  var f = function (id) { return document.getElementById(id); };
  G.setup({
    fill: function (o) {
      if (o.p !== undefined) f('k-p').value = o.p;
      if (o.b !== undefined) f('k-b').value = o.b;
      if (o.bank !== undefined) f('k-bank').value = o.bank;
    },
    build: function () {
      var s = 'p:' + f('k-p').value + ',b:' + f('k-b').value;
      return f('k-bank').value ? s + ',bank:' + f('k-bank').value : s;
    },
    run: function (out) {
      var r = G.kelly(G.num(f('k-p').value), G.num(f('k-b').value));
      if (r.error) { out.appendChild(G.el('p', r.error, 'err')); return; }
      var bank = G.num(f('k-bank').value);
      var rows = [['Full Kelly', r.full], ['Half Kelly', r.half], ['Quarter Kelly', r.quarter]]
        .map(function (x) {
          var row = [x[0], G.pct(x[1], 1)];
          row.push(isFinite(bank) && bank > 0 ? G.money(bank * x[1]) : '—');
          return row;
        });
      out.appendChild(G.table(['Sizing', 'Fraction of bankroll', 'Amount'], rows));
      out.appendChild(G.el('p', r.hasEdge
        ? 'Edge per $1 risked: ' + (r.edge).toFixed(3) + '. Full Kelly is an upper bound, not a target.'
        : 'No positive edge at these inputs: the formula gives 0%. Nothing to size.', 'muted'));
    }
  });
})(GeshTool);
"""

MC_JS = r"""
(function (G) {
  'use strict';
  var N = 1000, BINS = 30;
  G.simulate = function (o) {
    var mu = o.mu / 100, sigma = o.sigma / 100, value = o.value, days = Math.round(o.days);
    if (!isFinite(mu) || !isFinite(sigma) || !isFinite(value) || !isFinite(days) ||
        mu < -1 || mu > 2 || sigma < 0 || sigma > 3 || value <= 0 || days < 1 || days > 252) {
      return { error: 'Check the inputs: return -100% to 200%, volatility 0% to 300%, value above 0, horizon 1 to 252 days.' };
    }
    var rnd = G.mulberry32(G.hashSeed([o.mu, o.sigma, o.value, days].join('|')));
    var dt = 1 / 252, drift = (mu - 0.5 * sigma * sigma) * dt, vol = sigma * Math.sqrt(dt);
    var ends = new Array(N);
    for (var i = 0; i < N; i++) {
      var lr = 0;
      for (var d = 0; d < days; d++) {
        var u1 = rnd() || 1e-12, u2 = rnd();
        lr += drift + vol * Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
      }
      ends[i] = value * Math.exp(lr);
    }
    ends.sort(function (a, b) { return a - b; });
    var q = function (p) {
      var pos = p * (N - 1), lo = Math.floor(pos), hi = Math.ceil(pos);
      return ends[lo] + (ends[hi] - ends[lo]) * (pos - lo);
    };
    var min = ends[0], max = ends[N - 1], span = (max - min) || 1, bins = [];
    for (var b = 0; b < BINS; b++) bins.push(0);
    ends.forEach(function (e) { bins[Math.min(BINS - 1, Math.floor((e - min) / span * BINS))]++; });
    return {
      paths: N, days: days, value: value, p5: q(0.05), p50: q(0.5), p95: q(0.95),
      min: min, max: max, bins: bins,
      worstPct: q(0.05) / value - 1
    };
  };
  if (typeof document === 'undefined') return;
  var f = function (id) { return document.getElementById(id); };
  function chart(r) {
    var NS = 'http://www.w3.org/2000/svg', W = 600, H = 180, m = Math.max.apply(null, r.bins);
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + (H + 24));
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Histogram of 1,000 simulated ending portfolio values');
    svg.style.width = '100%';
    var bw = W / r.bins.length;
    r.bins.forEach(function (c, i) {
      var h = m ? (c / m) * H : 0, rect = document.createElementNS(NS, 'rect');
      rect.setAttribute('x', i * bw + 1); rect.setAttribute('y', H - h);
      rect.setAttribute('width', bw - 2); rect.setAttribute('height', h);
      rect.setAttribute('fill', '#00d4ff'); rect.setAttribute('opacity', '0.8');
      svg.appendChild(rect);
    });
    var span = (r.max - r.min) || 1, x = (r.p5 - r.min) / span * W;
    var line = document.createElementNS(NS, 'line');
    line.setAttribute('x1', x); line.setAttribute('x2', x);
    line.setAttribute('y1', 0); line.setAttribute('y2', H);
    line.setAttribute('stroke', '#ff6b81'); line.setAttribute('stroke-width', '2');
    svg.appendChild(line);
    var t = document.createElementNS(NS, 'text');
    t.setAttribute('x', Math.min(W - 4, x + 6)); t.setAttribute('y', 14);
    t.setAttribute('fill', '#ff6b81'); t.setAttribute('font-size', '12');
    if (x > W - 150) { t.setAttribute('text-anchor', 'end'); t.setAttribute('x', x - 6); }
    t.textContent = '5th percentile';
    svg.appendChild(t);
    [['min', r.min, 0, 'start'], ['max', r.max, W, 'end']].forEach(function (a) {
      var l = document.createElementNS(NS, 'text');
      l.setAttribute('x', a[2]); l.setAttribute('y', H + 18); l.setAttribute('text-anchor', a[3]);
      l.setAttribute('fill', '#8fa3bd'); l.setAttribute('font-size', '12');
      l.textContent = G.money(a[1]); svg.appendChild(l);
    });
    return svg;
  }
  function read() {
    return { mu: G.num(f('m-mu').value), sigma: G.num(f('m-sigma').value),
             value: G.num(f('m-value').value), days: G.num(f('m-days').value) };
  }
  G.setup({
    fill: function (o) {
      if (o.mu !== undefined) f('m-mu').value = o.mu;
      if (o.sigma !== undefined) f('m-sigma').value = o.sigma;
      if (o.value !== undefined) f('m-value').value = o.value;
      if (o.days !== undefined) f('m-days').value = o.days;
    },
    build: function () {
      return 'mu:' + f('m-mu').value + ',sigma:' + f('m-sigma').value +
             ',value:' + f('m-value').value + ',days:' + f('m-days').value;
    },
    run: function (out) {
      var r = G.simulate(read());
      if (r.error) { out.appendChild(G.el('p', r.error, 'err')); return; }
      out.appendChild(G.table(['Over ' + r.days + ' trading days', 'Ending value', 'Change'], [
        ['Worst plausible (5th percentile)', G.money(r.p5), G.pct(r.p5 / r.value - 1, 1)],
        ['Median', G.money(r.p50), G.pct(r.p50 / r.value - 1, 1)],
        ['Best plausible (95th percentile)', G.money(r.p95), G.pct(r.p95 / r.value - 1, 1)]
      ]));
      out.appendChild(chart(r));
      out.appendChild(G.el('p', '1,000 simulated paths. About 1 in 20 finished at or below the 5th-percentile value; real losses can be larger.', 'muted'));
    }
  });
})(GeshTool);
"""

XRAY_JS = r"""
(function (G) {
  'use strict';
  var LINE = /^\s*\$?([A-Za-z][A-Za-z.\-]{0,5})\s*[,:=\s]\s*\$?([0-9]*\.?[0-9]+)\s*%?\s*$/;
  G.parsePositions = function (text) {
    var map = Object.create(null), order = [], skipped = [];
    String(text || '').split(/\r?\n/).forEach(function (line) {
      if (!line.trim()) return;
      var m = LINE.exec(line);
      var w = m ? Number(m[2]) : NaN;
      if (!m || !(w > 0) || !isFinite(w)) { skipped.push(line.trim().slice(0, 40)); return; }
      var t = m[1].toUpperCase();
      if (!(t in map)) { map[t] = 0; order.push(t); }
      map[t] += w;
    });
    return { positions: order.map(function (t) { return [t, map[t]]; }), skipped: skipped };
  };
  G.analyze = function (positions) {
    var total = positions.reduce(function (s, p) { return s + p[1]; }, 0);
    if (!positions.length || !(total > 0)) return { error: 'Add at least one ticker with a weight above 0.' };
    var rows = positions.map(function (p) {
      return { t: p[0], w: p[1] / total, s: Object.prototype.hasOwnProperty.call(G.SECTORS, p[0]) ? G.SECTORS[p[0]] : 'Unclassified' };
    }).sort(function (a, b) { return b.w - a.w; });
    var sec = Object.create(null);
    rows.forEach(function (r) { sec[r.s] = (sec[r.s] || 0) + r.w; });
    var sectors = Object.keys(sec).map(function (k) { return [k, sec[k]]; })
      .sort(function (a, b) { return b[1] - a[1]; });
    var hhi = rows.reduce(function (s, r) { return s + r.w * r.w; }, 0);
    var flags = [];
    rows.forEach(function (r) {
      if (r.w > 0.20 && r.s !== 'Diversified funds') flags.push(r.t + ' is ' + G.pct(r.w, 0) + ' of the book (single position above 20%).');
    });
    sectors.forEach(function (s) {
      if (s[1] > 0.40 && s[0] !== 'Unclassified' && s[0] !== 'Diversified funds') {
        flags.push(s[0] + ' is ' + G.pct(s[1], 0) + ' of the book (single sector above 40%).');
      }
    });
    if ((sec['Unclassified'] || 0) > 0.25) {
      flags.push(G.pct(sec['Unclassified'], 0) + ' is unclassified, so the sector table understates the picture.');
    }
    var held = Object.create(null);
    rows.forEach(function (r) { held[r.t] = true; });
    Object.keys(G.OVERLAP.groups).forEach(function (name) {
      var hit = G.OVERLAP.groups[name].filter(function (t) { return held[t]; });
      if (hit.length > 1) flags.push('Overlap: ' + hit.join(' and ') + ' all track the ' + name + ' index, so they largely hold the same stocks.');
    });
    var broad = [];
    G.OVERLAP.broad.forEach(function (name) {
      G.OVERLAP.groups[name].forEach(function (t) { if (held[t]) broad.push(t); });
    });
    var mega = G.OVERLAP.megacaps.filter(function (t) { return held[t]; });
    if (broad.length && mega.length) {
      flags.push('Overlap: ' + mega.join(', ') + ' also sit inside ' + broad.join(', ') + ' (large index constituents); the combined exposure is higher than the weights shown.');
    }
    return {
      rows: rows, sectors: sectors, top5: rows.slice(0, 5), hhi: hhi,
      effective: 1 / hhi, score: Math.round(100 * (1 - hhi)), flags: flags,
      unclassified: sec['Unclassified'] || 0
    };
  };
  if (typeof document === 'undefined') return;
  var f = function (id) { return document.getElementById(id); };
  G.setup({
    fill: function (o, raw) {
      var lines = String(raw).split(',').map(function (p) { return p.replace(':', ' '); });
      f('x-in').value = lines.join('\n');
    },
    build: function () {
      var p = G.parsePositions(f('x-in').value).positions;
      return p.map(function (x) { return x[0] + ':' + x[1]; }).join(',');
    },
    run: function (out) {
      var parsed = G.parsePositions(f('x-in').value), r = G.analyze(parsed.positions);
      if (r.error) { out.appendChild(G.el('p', r.error, 'err')); return; }
      out.appendChild(G.el('h3', 'Diversification score: ' + r.score + ' / 100'));
      out.appendChild(G.el('p', 'Effective number of holdings: ' + r.effective.toFixed(1) + ' (score = 100 × (1 − HHI); weights normalised to 100%).', 'muted'));
      out.appendChild(G.el('h3', 'Sector mix'));
      out.appendChild(G.table(['Sector', 'Weight'], r.sectors.map(function (s) { return [s[0], G.pct(s[1], 1)]; })));
      out.appendChild(G.el('h3', 'Top 5 concentrations'));
      out.appendChild(G.table(['Ticker', 'Weight', 'Sector'], r.top5.map(function (x) { return [x.t, G.pct(x.w, 1), x.s]; })));
      out.appendChild(G.el('h3', 'Flags'));
      if (r.flags.length) {
        var ul = G.el('ul');
        r.flags.forEach(function (x) { ul.appendChild(G.el('li', x)); });
        out.appendChild(ul);
      } else { out.appendChild(G.el('p', 'No concentration or overlap flags at these thresholds.', 'muted')); }
      if (parsed.skipped.length) out.appendChild(G.el('p', 'Skipped lines: ' + parsed.skipped.join(' | '), 'err'));
    }
  });
})(GeshTool);
"""


def _json(obj: object) -> str:
    return (
        json.dumps(obj, separators=(",", ":"), sort_keys=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def kelly_script() -> str:
    return COMMON_JS + KELLY_JS


def monte_carlo_script() -> str:
    return COMMON_JS + MC_JS


def xray_script() -> str:
    data = f"var GeshTool = GeshTool || {{}};\nGeshTool.SECTORS={_json(SECTORS)};\nGeshTool.OVERLAP={_json(OVERLAP)};\n"
    return data + COMMON_JS + XRAY_JS
