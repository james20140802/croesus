function cssVar(name, fallback) {
  var v = getComputedStyle(document.documentElement).getPropertyValue(name);
  return (v && v.trim()) || fallback;
}

function initCharts() {
  if (typeof echarts === 'undefined') return;   // graceful degrade if vendor missing
  var ink = cssVar('--ink', '#211E18');
  var soft = cssVar('--ink-soft', '#6F6857');
  var line = cssVar('--line', '#E0D6C2');
  var gilt = cssVar('--gilt', '#9A7616');
  // 양피지/금빛 정체성에 맞춘 차분한 팔레트
  var palette = [gilt, '#2E6B4B', '#8A6BA8', '#C2A14A', '#A6402F', '#3B6E8F', '#936313'];

  document.querySelectorAll('[data-chart]').forEach(function (el) {
    if (el.offsetParent === null) return;            // 숨김(모바일 desktop-only) 스킵
    if (el.__inited) return; el.__inited = true;
    var kind = el.getAttribute('data-chart');
    if (['donut', 'macro-history', 'scatter', 'bands', 'price'].indexOf(kind) === -1) {
      el.__inited = false;                           // 알 수 없는 종류 — 빈 프레임 대신 건너뜀
      return;
    }
    var data = JSON.parse(el.getAttribute('data-series') || '[]');
    var chart = echarts.init(el, null, { renderer: 'svg' });
    var axis = { axisLine: { lineStyle: { color: line } }, axisLabel: { color: soft },
                 splitLine: { lineStyle: { color: line, opacity: 0.5 } } };
    var opt = { color: palette, textStyle: { color: ink, fontFamily: 'inherit' } };

    if (kind === 'donut') {
      opt.tooltip = { trigger: 'item', formatter: '{b}: {d}%' };
      opt.series = [{ type: 'pie', radius: ['52%', '74%'], avoidLabelOverlap: true,
        itemStyle: { borderColor: cssVar('--surface', '#fff'), borderWidth: 2 },
        label: { color: soft, fontSize: 11 }, data: data }];
    } else if (kind === 'macro-history') {
      opt.tooltip = { trigger: 'axis' };
      opt.grid = { left: 36, right: 14, top: 16, bottom: 28 };
      opt.xAxis = Object.assign({ type: 'category', data: data.map(function (d) { return d.date; }) }, axis);
      opt.yAxis = Object.assign({ type: 'value', max: 100 }, axis);
      opt.series = [{ type: 'line', smooth: true, showSymbol: false,
        lineStyle: { color: gilt, width: 2 },
        areaStyle: { color: gilt, opacity: 0.12 },
        data: data.map(function (d) { return d.amplifier_score; }) }];
    } else if (kind === 'scatter') {
      var gateColor = { pass: cssVar('--ok', '#2E6B4B'), warn: cssVar('--warn', '#936313'),
                        block: cssVar('--bad', '#A6402F'), none: soft };
      opt.tooltip = { formatter: function (p) { return p.data[3] + ' (' + p.data[2] + ')'; } };
      opt.grid = { left: 44, right: 16, top: 16, bottom: 36 };
      opt.xAxis = Object.assign({ name: '상승여력', nameTextStyle: { color: soft } }, axis);
      opt.yAxis = Object.assign({ name: '확신도', nameTextStyle: { color: soft } }, axis);
      opt.series = [{ type: 'scatter', symbolSize: 16,
        itemStyle: { color: function (p) { return gateColor[p.data[4]] || soft; }, opacity: 0.85 },
        data: data.map(function (d) { return [d.upside, d.confidence, d.gate, d.symbol, d.gate]; }) }];
    } else if (kind === 'price') {
      opt.tooltip = { trigger: 'axis' };
      opt.grid = { left: 52, right: 14, top: 16, bottom: 28 };
      opt.xAxis = Object.assign({ type: 'category', data: data.map(function (d) { return d.date; }),
        axisLabel: { color: soft, showMaxLabel: true } }, axis);
      opt.yAxis = Object.assign({ type: 'value', scale: true }, axis);
      opt.series = [{ type: 'line', smooth: true, showSymbol: false,
        lineStyle: { color: gilt, width: 2 },
        areaStyle: { color: gilt, opacity: 0.10 },
        data: data.map(function (d) { return d.close; }) }];
    } else if (kind === 'bands') {
      var keys = Object.keys(data);
      opt.tooltip = { trigger: 'axis' };
      opt.grid = { left: 48, right: 16, top: 16, bottom: 28 };
      opt.xAxis = Object.assign({ type: 'category', data: keys }, axis);
      opt.yAxis = Object.assign({ type: 'value' }, axis);
      opt.series = [{ type: 'bar', barWidth: '52%',
        itemStyle: { color: gilt, borderRadius: [4, 4, 0, 0] },
        data: keys.map(function (k) { return data[k]; }) }];
      var price = parseFloat(el.getAttribute('data-price') || '0');
      if (price > 0) {
        opt.series[0].markLine = { symbol: 'none', label: { formatter: '현재가', color: soft },
          lineStyle: { color: cssVar('--bad', '#A6402F'), type: 'dashed' },
          data: [{ yAxis: price }] };
      }
    }
    chart.setOption(opt);
    window.addEventListener('resize', function () { chart.resize(); });
  });
}
window.addEventListener('DOMContentLoaded', initCharts);
document.body && document.body.addEventListener('htmx:afterSwap', initCharts);
