function cssVar(name, fallback) {
  var v = getComputedStyle(document.documentElement).getPropertyValue(name);
  return (v && v.trim()) || fallback;
}

// 포트폴리오 추이 차트(평가액/수익률) — 토글로 전환되므로 별도 빌더로 둔다.
function equityOption(data, mode) {
  var ink = cssVar('--ink', '#211E18');
  var soft = cssVar('--ink-soft', '#6F6857');
  var line = cssVar('--line', '#E0D6C2');
  var gilt = cssVar('--gilt', '#9A7616');
  var ok = cssVar('--ok', '#2E6B4B');
  var isReturn = mode === 'return';
  var color = isReturn ? ok : gilt;
  var axis = { axisLine: { lineStyle: { color: line } }, axisLabel: { color: soft },
               splitLine: { lineStyle: { color: line, opacity: 0.5 } } };
  return {
    color: [color], textStyle: { color: ink, fontFamily: 'inherit' },
    tooltip: { trigger: 'axis', formatter: function (ps) {
      var p = ps[0], v = p.data;
      var shown = isReturn
        ? (v >= 0 ? '+' : '') + Number(v).toFixed(1) + '%'
        : Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 });
      return p.axisValue + '<br/>' + (isReturn ? '수익률 ' : '평가액 ') + shown;
    } },
    grid: { left: 56, right: 16, top: 16, bottom: 28 },
    xAxis: Object.assign({ type: 'category', data: data.map(function (d) { return d.date; }) }, axis),
    yAxis: Object.assign({ type: 'value', scale: true,
      axisLabel: { color: soft, formatter: isReturn ? '{value}%' : null } }, axis),
    series: [{ type: 'line', smooth: true, showSymbol: false,
      lineStyle: { color: color, width: 2, type: isReturn ? 'dashed' : 'solid' },
      areaStyle: isReturn ? null : { color: gilt, opacity: 0.10 },
      data: data.map(function (d) { return isReturn ? d.return_pct : d.market_value; }) }],
  };
}

// 토글 버튼(평가액/수익률) 클릭 시 추이 차트를 다시 그린다.
window.toggleEquity = function (btn) {
  var group = btn.closest('.segmented');
  if (group) group.querySelectorAll('.seg').forEach(function (b) {
    b.classList.toggle('active', b === btn); });
  var mode = btn.getAttribute('data-mode');
  var el = document.querySelector('[data-chart="equity"]');
  if (el && el.__chart) {
    el.setAttribute('data-mode', mode);
    el.__chart.setOption(equityOption(el.__data, mode), true);
  }
};

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
    if (['donut', 'macro-history', 'scatter', 'bands', 'price', 'equity'].indexOf(kind) === -1) {
      el.__inited = false;                           // 알 수 없는 종류 — 빈 프레임 대신 건너뜀
      return;
    }
    var data = JSON.parse(el.getAttribute('data-series') || '[]');
    var chart = echarts.init(el, null, { renderer: 'svg' });
    var axis = { axisLine: { lineStyle: { color: line } }, axisLabel: { color: soft },
                 splitLine: { lineStyle: { color: line, opacity: 0.5 } } };
    var opt = { color: palette, textStyle: { color: ink, fontFamily: 'inherit' } };

    if (kind === 'donut') {
      opt.tooltip = { trigger: 'item', formatter: function (p) {
        return p.name + ': ' + p.percent.toFixed(1) + '%'; } };
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
      // 확신도는 '낮음/보통/높음' 등급이므로 1·2·3 레벨로 환산해 세로축에 배치한다.
      // 상승여력은 극단값(예: DCF 이상치)이 있어 축을 망가뜨리므로 가독 범위로
      // 클램프하고, 실제 값은 툴팁에 보여 정직성을 유지한다.
      var gateColor = { pass: cssVar('--ok', '#2E6B4B'), warn: cssVar('--warn', '#936313'),
                        block: cssVar('--bad', '#A6402F'), none: soft };
      var confCats = ['낮음', '보통', '높음'];
      var confLevel = { high: 2, medium: 1, low: 0 };
      var clamp = function (v) { return Math.max(-100, Math.min(150, v)); };
      opt.tooltip = { formatter: function (p) {
        var pct = p.data[5];
        return p.data[3] + '<br/>상승여력 ' + (pct >= 0 ? '+' : '') + Math.round(pct)
          + '%<br/>확신도 ' + (confCats[p.data[1]] || '—'); } };
      opt.grid = { left: 60, right: 20, top: 18, bottom: 42 };
      opt.xAxis = Object.assign({ type: 'value', name: '상승여력(%)', nameLocation: 'middle',
        nameGap: 26, nameTextStyle: { color: soft }, min: -100, max: 150,
        axisLabel: { color: soft, formatter: '{value}%' } }, axis);
      opt.yAxis = Object.assign({ type: 'category', name: '확신도', nameTextStyle: { color: soft },
        data: confCats, boundaryGap: true,
        axisLabel: { color: soft } }, axis);
      opt.series = [{ type: 'scatter', symbolSize: 15,
        itemStyle: { color: function (p) { return gateColor[p.data[4]] || soft; }, opacity: 0.85,
          borderColor: cssVar('--surface', '#fff'), borderWidth: 1 },
        data: data.map(function (d) {
          var pct = (d.upside || 0) * 100;
          return [clamp(pct), confLevel[d.confidence] || 0, d.gate, d.symbol, d.gate, pct];
        }) }];
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
    } else if (kind === 'equity') {
      // 평가액과 수익률은 단위·스케일이 달라 한 축에 겹치면 읽기 어렵다.
      // 한 번에 하나만 그리고, 토글(toggleEquity)로 전환한다.
      el.__data = data;
      el.__chart = chart;
      opt = equityOption(data, el.getAttribute('data-mode') || 'value');
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
