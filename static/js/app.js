/* My Money — vanilla JS only. Theme toggle, txn-form helpers,
   and pure-SVG charts (smooth gradient line / donut / bars),
   zero dependencies. */
(function () {
  "use strict";

  /* ---------------- theme ------------------------------------------------- */
  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    function syncThemeToggle() {
      var dark = document.documentElement.dataset.theme === "dark";
      toggle.textContent = dark ? "☀" : "☾";
      toggle.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
      toggle.title = dark ? "Switch to light theme" : "Switch to dark theme";
    }
    syncThemeToggle();
    toggle.addEventListener("click", function () {
      var cur = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
      var next = cur === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("mm-theme", next); } catch (e) {}
      syncThemeToggle();
    });
  }

  document.querySelectorAll(".flash-close").forEach(function (button) {
    button.addEventListener("click", function () {
      var flash = button.closest(".flash");
      if (flash) flash.remove();
    });
  });

  /* ---------------- txn form: nature -> loan dropdown hint ---------------- */
  var nature = document.getElementById("f-nature");
  var loanSel = document.getElementById("f-loan");
  var NEEDS_LOAN = ["loan_given", "loan_taken", "repayment_in", "repayment_out"];
  function syncLoan() {
    if (!nature || !loanSel) return;
    var need = NEEDS_LOAN.indexOf(nature.value) >= 0;
    loanSel.parentElement.hidden = !need;
    loanSel.required = need;
  }
  if (nature) {
    nature.addEventListener("change", syncLoan);
    syncLoan();
  }

  var category = document.getElementById("f-cat");
  var subcategory = document.getElementById("f-sub");
  if (category && subcategory) {
    var subcats = JSON.parse(category.dataset.subcats || "{}");
    category.addEventListener("change", function () {
      subcategory.replaceChildren(new Option("—", ""));
      (subcats[category.value] || []).forEach(function (name) {
        subcategory.add(new Option(name, name));
      });
      subcategory.parentElement.hidden = !(subcats[category.value] || []).length;
    });
    subcategory.parentElement.hidden = !subcategory.options.length ||
      (subcategory.options.length === 1 && !subcategory.value);
  }

  /* strip non-numeric junk from amount fields before submit */
  document.querySelectorAll("form").forEach(function (f) {
    f.addEventListener("submit", function () {
      var a = f.querySelector("input[name='amount']");
      if (a) a.value = a.value.replace(/[^0-9.,\-]/g, "").replace(/,/g, "");
    });
  });

  /* ---------------- chart helpers ----------------------------------------- */
  var NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs, text) {
    var n = ["div", "span", "i"].indexOf(tag) >= 0
      ? document.createElement(tag) : document.createElementNS(NS, tag);
    for (var k in (attrs || {})) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }
  function fmt(v) {
    if (Math.abs(v) >= 1e7) return (v / 1e7).toFixed(1) + "cr";
    if (Math.abs(v) >= 1e5) return (v / 1e5).toFixed(1) + "L";
    if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(1) + "k";
    return String(Math.round(v));
  }
  function scaleMax(vals) {
    var mx = Math.max(1, Math.max.apply(null, vals));
    var pw = Math.pow(10, Math.floor(Math.log(mx) / Math.LN10));
    var unit = pw / 2;
    return Math.ceil(mx / unit) * unit;
  }
  function mount(host, vbW, vbH) {
    host.innerHTML = "";
    var svg = el("svg", { viewBox: "0 0 " + vbW + " " + vbH, role: "img",
                          preserveAspectRatio: "none" });
    host.appendChild(svg);
    return svg;
  }
  /* catmull-rom → cubic bezier path */
  function smoothPath(pts) {
    var d = "M" + pts[0][0] + "," + pts[0][1];
    for (var i = 0; i < pts.length - 1; i++) {
      var p0 = pts[Math.max(0, i - 1)], p1 = pts[i],
          p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
      var c1x = p1[0] + (p2[0] - p0[0]) / 6,
          c1y = p1[1] + (p2[1] - p0[1]) / 6,
          c2x = p2[0] - (p3[0] - p1[0]) / 6,
          c2y = p2[1] - (p3[1] - p1[1]) / 6;
      d += "C" + c1x.toFixed(1) + "," + c1y.toFixed(1) + " " +
            c2x.toFixed(1) + "," + c2y.toFixed(1) + " " +
            p2[0].toFixed(1) + "," + p2[1].toFixed(1);
    }
    return d;
  }

  /* ---------------- cash flow line chart ---------------------------------- */
  var IN_COLOR = "#2dd4a7", EX_COLOR = "#f87171";
  function lineChart(host, data) {
    var keys = data.keys, labels = data.labels, series = data.series;
    var W = 620, H = 300, L = 52, R = 18, T = 26, B = 34;
    var svg = mount(host, W, H);
    var ins = keys.map(function (k) { return series[k].in || 0; });
    var exps = keys.map(function (k) { return series[k].expense || 0; });
    var mx = scaleMax(ins.concat(exps).concat([1]));
    var stepX = (W - L - R) / Math.max(1, keys.length - 1);
    var baseY = H - B, topY = T;
    function pt(v, i) { return [L + i * stepX, baseY - (baseY - topY) * (v / mx)]; }

    /* horizontal grid */
    var axis = css("--line", "#222e44");
    for (var g = 0; g <= 4; g++) {
      var y = topY + (baseY - topY) * g / 4;
      svg.appendChild(el("line", { x1: L, x2: W - R, y1: y, y2: y, stroke: axis,
                                   "stroke-width": 1,
                                   "stroke-dasharray": g === 4 ? "" : "2,5",
                                   opacity: g === 4 ? 1 : .55 }));
      svg.appendChild(el("text", { x: L - 8, y: y + 4, "text-anchor": "end",
        "font-size": 11, fill: css("--muted", "#8fa1b8") }, fmt(mx * (4 - g) / 4)));
    }
    /* month labels */
    keys.forEach(function (k, i) {
      if (i % 2 && i !== keys.length - 1) return;
      svg.appendChild(el("text", { x: L + i * stepX, y: H - 12,
        "text-anchor": "middle", "font-size": 11,
        fill: css("--muted", "#8fa1b8") }, labels[i]));
    });

    /* gradient fill under income line */
    var defs = el("defs");
    var grad = el("linearGradient", { id: "mmGradIn", x1: 0, y1: 0, x2: 0, y2: 1 });
    grad.appendChild(el("stop", { offset: "0%", "stop-color": IN_COLOR,
                                  "stop-opacity": .30 }));
    grad.appendChild(el("stop", { offset: "100%", "stop-color": IN_COLOR,
                                  "stop-opacity": 0 }));
    defs.appendChild(grad);
    svg.appendChild(defs);

    var pIn = ins.map(function (v, i) { return pt(v, i); });
    var pEx = exps.map(function (v, i) { return pt(v, i); });
    var dIn = smoothPath(pIn), dEx = smoothPath(pEx);
    var area = dIn + "L" + (L + (keys.length - 1) * stepX) + "," + baseY +
               "L" + L + "," + baseY + "Z";
    svg.appendChild(el("path", { d: area, fill: "url(#mmGradIn)" }));
    svg.appendChild(el("path", { d: dEx, fill: "none", stroke: EX_COLOR,
                                 "stroke-width": 1.6, opacity: .9,
                                 "stroke-linecap": "round" }));
    svg.appendChild(el("path", { d: dIn, fill: "none", stroke: IN_COLOR,
                                 "stroke-width": 2.6, "stroke-linecap": "round",
                                 "stroke-linejoin": "round" }));
    pIn.forEach(function (p, i) {
      svg.appendChild(el("circle", { cx: p[0], cy: p[1], r: 3.4,
        fill: css("--card", "#131c2f"), stroke: IN_COLOR, "stroke-width": 2 }));
    });
    /* peak dot is already covered by month dots — nothing more needed */
  }

  /* ---------------- donut + html legend ----------------------------------- */
  var PALETTE = ["#2dd4a7", "#3b82f6", "#8b5cf6", "#f59e0b", "#ef4444",
                 "#14b8a6", "#e879f9", "#a3e635", "#fb7185", "#60a5fa"];
  function donut(host, cats) {
    host.innerHTML = "";
    if (!cats.length) {
      host.innerHTML = "<p class='muted center' style='margin-top:70px'>No expenses in this period.</p>";
      return;
    }
    var W = 220, H = 220, cx = 110, cy = 110, r = 88, ir = 58;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, class: "donut-svg" });
    var total = cats.reduce(function (s, c) { return s + c.value; }, 0) || 1;
    var a0 = -Math.PI / 2;
    cats.slice(0, 10).forEach(function (c, i) {
      var frac = c.value / total;
      var a1 = a0 + frac * 2 * Math.PI - 0.02;   // small seam
      var large = frac > .5 ? 1 : 0;
      var d = [
        "M", cx + ir * Math.cos(a0), cy + ir * Math.sin(a0),
        "L", cx + r * Math.cos(a0), cy + r * Math.sin(a0),
        "A", r, r, 0, String(large), "1", cx + r * Math.cos(a1), cy + r * Math.sin(a1),
        "L", cx + ir * Math.cos(a1), cy + ir * Math.sin(a1),
        "A", ir, ir, 0, String(large), "0", cx + ir * Math.cos(a0), cy + ir * Math.sin(a0),
        "Z"].map(function (x) { return typeof x === "number" ? x.toFixed(2) : x; }).join(" ");
      var p = el("path", { d: d, fill: PALETTE[i % PALETTE.length], opacity: .95 });
      var t = el("title");
      t.textContent = c.name + ": ₹" + c.value.toFixed(0);
      p.appendChild(t);
      svg.appendChild(p);
      a0 = a1 + 0.02;
    });
    host.appendChild(svg);
    var ctr = el("div", { class: "donut-center" });
    ctr.appendChild(el("div", { class: "donut-total" }, "₹" + fmt(total)));
    ctr.appendChild(el("div", { class: "donut-sub" }, "total spent"));
    host.appendChild(ctr);

    /* legend */
    var lg = el("div", { class: "dlegend" });
    cats.slice(0, 5).forEach(function (c, i) {
      var row = el("div", { class: "lrow" });
      row.appendChild(el("i", { class: "ldot",
        style: "background:" + PALETTE[i % PALETTE.length] }));
      row.appendChild(el("span", { class: "lname" }, c.name));
      row.appendChild(el("span", { class: "lpct" },
        Math.round(c.value * 100 / total) + "%"));
      row.appendChild(el("span", { class: "lval" }, "₹" + fmt(c.value)));
      lg.appendChild(row);
    });
    if (cats.length > 5) {
      var rest = cats.slice(5).reduce(function (s, c) { return s + c.value; }, 0);
      var row = el("div", { class: "lrow" });
      row.appendChild(el("i", { class: "ldot", style: "background:#54607a" }));
      row.appendChild(el("span", { class: "lname" }, "Other"));
      row.appendChild(el("span", { class: "lpct" },
        Math.round(rest * 100 / total) + "%"));
      row.appendChild(el("span", { class: "lval" }, "₹" + fmt(rest)));
      lg.appendChild(row);
    }
    host.appendChild(lg);
  }

  /* ---------------- horizontal bars (kept for reuse) ----------------------- */
  function bars(host, rows) {
    host.innerHTML = "";
    if (!rows.length) {
      host.innerHTML = "<p class='muted'>No data yet.</p>";
      return;
    }
    var svg = mount(host, 560, 250);
    var vals = rows.map(function (r) { return r.value / 100; });
    var mx = scaleMax(vals);
    var colr = css("--accent", "#2dd4a7");
    rows.slice(0, 8).forEach(function (r, i) {
      var y = 28 + i * 26;
      var w = Math.max(2, 480 * (r.value / 100 / mx));
      svg.appendChild(el("text", { x: 40, y: y + 8, "font-size": 10,
        fill: css("--ink", "#e6edf7") }, r.name.slice(0, 24)));
      svg.appendChild(el("rect", { x: 200, y: y, width: w, height: 14, rx: 4,
        fill: colr, opacity: .85 }));
      svg.appendChild(el("text", { x: 550, y: y + 11, "text-anchor": "end",
        "font-size": 10, fill: css("--muted", "#8fa1b8") },
        "₹" + fmt(r.value / 100)));
    });
  }

  window.addEventListener("DOMContentLoaded", function () {
    var monthly = document.getElementById("chart-monthly");
    if (monthly && monthly.dataset.series) {
      lineChart(monthly, JSON.parse(monthly.dataset.series));
    }
    var cats = document.getElementById("chart-cats");
    if (cats && cats.dataset.cats) donut(cats, JSON.parse(cats.dataset.cats));
    var people = document.getElementById("chart-people");
    if (people && people.dataset.bars) bars(people, JSON.parse(people.dataset.bars));
  });
})();
