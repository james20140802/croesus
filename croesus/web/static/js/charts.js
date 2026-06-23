function initCharts() {
  if (typeof echarts === 'undefined') return;   // graceful degrade if vendor missing
  document.querySelectorAll('[data-chart]').forEach(function (el) {
    if (el.offsetParent === null) return;            // 숨김(모바일 desktop-only) 스킵
    if (el.__inited) return; el.__inited = true;
    var kind = el.getAttribute('data-chart');
    var data = JSON.parse(el.getAttribute('data-series') || '[]');
    var chart = echarts.init(el);
    var opt;
    if (kind === 'donut') {
      opt = { tooltip: {}, series: [{ type: 'pie', radius: ['45%','70%'], data: data }] };
    } else if (kind === 'macro-history') {
      opt = { tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: data.map(d => d.date) },
        yAxis: { type: 'value' },
        series: [{ type: 'line', smooth: true, data: data.map(d => d.amplifier_score) }] };
    } else if (kind === 'scatter') {
      var gateColor = { pass: '#1a7f37', warn: '#9a6700', block: '#cf222e', none: '#888' };
      opt = { tooltip: { formatter: p => p.data[3] + ' (' + p.data[2] + ')' },
        xAxis: { name: '업사이드' }, yAxis: { name: '확신도' },
        series: [{ type: 'scatter', symbolSize: 16,
          itemStyle: { color: p => gateColor[p.data[4]] || '#888' },
          data: data.map(d => [d.upside, d.confidence, d.gate, d.symbol, d.gate]) }] };
    } else if (kind === 'bands') {
      var keys = Object.keys(data);
      opt = { tooltip: {}, xAxis: { type: 'category', data: keys },
        yAxis: { type: 'value' },
        series: [{ type: 'bar', data: keys.map(k => data[k]) }] };
    }
    if (opt) chart.setOption(opt);
    window.addEventListener('resize', function () { chart.resize(); });
  });
}
window.addEventListener('DOMContentLoaded', initCharts);
document.body && document.body.addEventListener('htmx:afterSwap', initCharts);
