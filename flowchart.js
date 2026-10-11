/* Read-only native graph view. Selection and zoom never change task data. */
window.WorkflowDiagram = (() => {
  const byId = id => document.getElementById(id);
  const svgNS = 'http://www.w3.org/2000/svg';
  const kinds = {
    1: ['open', '打开网页', '↗'], 2: ['click', '点击元素', '↖'],
    3: ['extract', '提取数据', '▤'], 4: ['input', '输入文字', 'T'],
    5: ['custom', '自定义操作', '⋯'], 6: ['select', '选择选项', '⌄'],
    7: ['hover', '移动鼠标', '↖'], 8: ['loop', '循环', '↻'],
    9: ['judge', '条件判断', '◇'], 10: ['branch', '条件分支', '⑂']
  };
  let task = null, signature = '', selected = null, scale = 1, fitMode = true;
  let links = [], visited = new Set(), nodes = new Map(), generation = 0;
  const viewport = byId('workflow-viewport'), stage = byId('workflow-stage');
  const surface = byId('workflow-surface'), canvas = byId('workflow-nodes');
  const svg = byId('workflow-links'), inspector = byId('workflow-inspector');

  function element(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = String(value ?? '');
    return item;
  }
  function vector(tag, attributes) {
    const item = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, value);
    return item;
  }
  function short(value, length = 76) {
    const text = String(value ?? '').replace(/\s+/g, ' ').trim();
    return text.length > length ? text.slice(0, length - 1) + '…' : text;
  }
  function kind(node) { return kinds[node.option] || ['custom', '原版操作', '⋯']; }
  function pageLoop(node) {
    return node.option === 8 && Number(node.parameters?.loopType) === 0 &&
      (node.sequence || []).some(id => {
        const judge = task.graph[id];
        return judge?.option === 9 && (judge.sequence || []).some(branchId => {
          const branch = task.graph[branchId];
          return branch?.option === 10 && Number(branch.parameters?.class) === 2 &&
            (branch.sequence || []).some(clickId => task.graph[clickId]?.option === 2 &&
              task.graph[clickId].parameters?.xpath === branch.parameters?.value);
        });
      });
  }
  function condition(parameters) {
    return ({0: '默认分支', 1: '页面文字满足条件', 2: '指定元素存在',
      3: '循环元素文字满足条件', 4: '循环内元素存在', 5: '脚本条件满足',
      6: '脚本条件满足', 7: '脚本条件满足', 8: '表达式条件满足'})[parameters.class] || '按原版条件判断';
  }
  function loopText(node) {
    const p = node.parameters || {}, count = Number(p.exitCount || 0);
    if (pageLoop(node)) return count ? `最多采集 ${count + 1} 页` : '翻页直到没有下一页';
    return ({0: count ? `最多重复 ${count} 次` : '按结束条件持续循环',
      1: '逐条处理匹配的列表记录', 2: '逐个处理指定元素',
      3: '逐个处理文本 / 关键词', 4: '逐个处理网址',
      5: '按 JavaScript 结果循环', 6: '按系统命令结果循环',
      7: '按 Python 结果循环'})[p.loopType] || '执行循环中的步骤';
  }
  function summary(node) {
    const p = node.parameters || {};
    switch (node.option) {
      case 1: return p.useLoop ? '打开当前循环的网址' : short(p.links || p.url || task.url);
      case 2: return Number(p.newTab) === 1 ? '在新标签页打开目标' : '点击定位到的网页元素';
      case 3: {
        const fields = p.params || [];
        return `${fields.length} 个字段 · ${short(fields.map(f => f.name).join('、'), 54)}`;
      }
      case 4: return p.useLoop ? '输入当前循环的文本' : `输入：${short(p.value, 58)}`;
      case 5: return '执行原版自定义操作';
      case 6: return `选择：${short(p.optionValue || '指定下拉选项')}`;
      case 7: return '移动到定位的网页元素';
      case 8: return loopText(node);
      case 9: return `${(node.sequence || []).length} 个条件分支`;
      case 10: return condition(p);
      default: return '查看该原版节点的配置';
    }
  }
  function terminal(label, className = '') {
    return element('div', 'wf-terminal ' + className, label);
  }
  function wire(from, to, type = 'normal', frame = null) { links.push({from, to, type, frame}); }
  function select(index, scroll = false) {
    if (!nodes.has(index)) return;
    selected = index;
    canvas.querySelectorAll('.wf-node').forEach(button => {
      const active = Number(button.dataset.nodeId) === index;
      button.classList.toggle('wf-selected', active);
      button.setAttribute('aria-pressed', String(active));
    });
    showDetails(task.graph[index]);
    if (scroll) nodes.get(index).scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'smooth'});
  }
  function header(node, index) {
    const [name, label, icon] = kind(node);
    const button = element('button', `wf-node wf-kind-${name}`);
    button.type = 'button'; button.dataset.nodeId = index;
    button.setAttribute('aria-label', `查看步骤 ${index}：${node.title || label}`);
    button.setAttribute('aria-pressed', 'false'); button.draggable = false;
    if (node.option === 9) {
      const diamond = vector('svg', {viewBox: '0 0 320 132', 'aria-hidden': 'true', class: 'wf-diamond'});
      diamond.append(vector('path', {d: 'M160 2 L318 66 L160 130 L2 66 Z'})); button.append(diamond);
    }
    const content = element('span', 'wf-node-content');
    const top = element('span', 'wf-node-top');
    top.append(element('span', 'wf-type-icon', icon), element('span', 'wf-type-name', label),
      element('span', 'wf-node-id', '#' + index));
    content.append(top, element('span', 'wf-node-title', node.title || label),
      element('span', 'wf-node-summary', summary(node)));
    button.append(content); button.onclick = () => select(index);
    nodes.set(index, button); return button;
  }
  function sequence(ids, parent) {
    const list = element('div', 'wf-sequence');
    const children = ids.map(id => unit(id));
    children.forEach(child => list.append(child));
    for (let i = 1; i < children.length; i++) wire(children[i - 1], children[i]);
    parent.append(list); return children;
  }
  function unit(index) {
    const node = Number.isInteger(index) && index > 0 ? task.graph[index] : null;
    if (!node || visited.has(index)) return element('div', 'wf-issue', '节点引用异常，请在原版检查');
    visited.add(index);
    const [name] = kind(node), container = element('div', `wf-unit wf-unit-${name}`);
    container.dataset.unitId = index;
    const card = header(node, index); container.append(card);
    const ids = Array.isArray(node.sequence) ? node.sequence : [];
    if (node.option === 9) {
      const branches = element('div', 'wf-branches');
      const lanes = ids.map(id => unit(id)); lanes.forEach(lane => branches.append(lane));
      const join = element('div', 'wf-join'); join.setAttribute('aria-label', '分支汇合');
      container.append(branches, join);
      for (const lane of lanes) { wire(card, lane); wire(lane, join); }
      if (!lanes.length) branches.append(element('div', 'wf-empty', '暂无条件分支'));
    } else if (node.option === 8 || node.option === 10 || ids.length) {
      const body = element('div', 'wf-group-body'); container.append(body);
      const children = sequence(ids, body);
      if (children.length) wire(card, children[0]);
      else body.append(element('div', 'wf-empty', '暂无内部步骤'));
      if (node.option === 8 && children.length) {
        const end = terminal('本轮完成', 'wf-round-end'); body.append(end);
        wire(children[children.length - 1], end);
        wire(end, children[0], 'repeat', container);
      }
    }
    return container;
  }
  function detail(label, value, parent = inspector) {
    if (value === undefined || value === null || value === '') return;
    const row = element('div', 'wf-detail-row');
    row.append(element('dt', '', label), element('dd', '', value)); parent.append(row);
  }
  function showDetails(node) {
    const [name, label, icon] = kind(node), p = node.parameters || {};
    inspector.replaceChildren();
    const heading = element('div', `wf-inspector-heading wf-kind-${name}`);
    heading.append(element('span', 'wf-detail-icon', icon), element('span', '', `${label} · #${selected}`));
    inspector.append(heading, element('h3', 'wf-detail-title', node.title || label),
      element('p', 'wf-detail-description', summary(node)));
    const data = element('dl', 'wf-detail-data'); inspector.append(data);
    detail('等待时间', p.wait === undefined ? null : `${p.wait} 秒`, data);
    if (node.option === 1) detail('网址', p.useLoop ? '使用循环中的网址' : p.links || p.url || task.url, data);
    if (node.option === 4) detail('输入内容', p.useLoop ? '使用循环中的文本' : p.value, data);
    if (node.option === 6) detail('选项', p.optionValue, data);
    if (node.option === 8) {
      detail('循环方式', loopText(node), data);
      detail('循环文本 / 网址', p.textList, data);
    }
    if (node.option === 10) detail('分支条件', condition(p), data);
    if (node.option === 3) {
      inspector.append(element('h4', 'wf-detail-subheading', '采集字段'));
      const fields = element('ul', 'wf-fields');
      for (const field of p.params || []) {
        const item = element('li', 'wf-field');
        item.append(element('strong', '', field.name), element('small', '', field.relative ? '列表相对定位' : '页面定位'));
        if (field.default) item.append(element('span', 'wf-field-default', '默认值：' + field.default));
        fields.append(item);
      }
      inspector.append(fields);
    }
    const locators = [];
    if (p.xpath) locators.push(['节点 XPath', p.xpath]);
    if (node.option === 10 && Number(p.class) === 2 && p.value) locators.push(['条件 XPath', p.value]);
    if (node.option === 3) for (const field of p.params || [])
      if (field.relativeXPath) locators.push([field.name, field.relativeXPath]);
    if (locators.length) {
      const disclosure = element('details', 'wf-locators');
      disclosure.append(element('summary', '', '查看定位信息'));
      for (const [label, path] of locators) {
        disclosure.append(element('div', 'wf-locator-label', label), element('code', '', path));
      }
      inspector.append(disclosure);
    }
    inspector.append(element('p', 'wf-readonly-note', '当前为只读预览。可让 AI 修改，或前往 EasySpider 手工编辑。'));
  }
  function rect(item) {
    const box = item.getBoundingClientRect(), origin = surface.getBoundingClientRect();
    return {x: (box.left - origin.left + box.width / 2) / scale,
      top: (box.top - origin.top) / scale, bottom: (box.bottom - origin.top) / scale,
      right: (box.right - origin.left) / scale};
  }
  function redraw() {
    if (!task || !surface.isConnected) return;
    const width = surface.offsetWidth, height = surface.offsetHeight;
    svg.replaceChildren(); svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('width', width); svg.setAttribute('height', height);
    const defs = vector('defs', {});
    for (const [id, color] of [['wf-arrow', '#91a2ba'], ['wf-repeat-arrow', '#40a987']]) {
      const marker = vector('marker', {id, markerWidth: 8, markerHeight: 8, refX: 6,
        refY: 4, orient: 'auto', markerUnits: 'userSpaceOnUse'});
      marker.append(vector('path', {d: 'M1 1 L6 4 L1 7', fill: 'none', stroke: color,
        'stroke-width': 1.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round'})); defs.append(marker);
    }
    svg.append(defs);
    for (const {from, to, type, frame} of links) {
      const a = rect(from), b = rect(to), start = a.bottom + 2, end = b.top - 4;
      let d;
      if (type === 'repeat') {
        const side = rect(frame).right - 14, bottom = start + 10, top = end - 11;
        d = `M${a.x} ${start} V${bottom} H${side - 7} Q${side} ${bottom} ${side} ${bottom - 7} V${top + 7} Q${side} ${top} ${side - 7} ${top} H${b.x + 7} Q${b.x} ${top} ${b.x} ${top + 7} V${end}`;
      } else if (Math.abs(a.x - b.x) < 1) d = `M${a.x} ${start} V${end}`;
      else {
        const middle = (start + end) / 2;
        d = `M${a.x} ${start} C${a.x} ${middle} ${b.x} ${middle} ${b.x} ${end}`;
      }
      svg.append(vector('path', {d, fill: 'none', stroke: type === 'repeat' ? '#40a987' : '#91a2ba',
        'stroke-width': 1.5, 'stroke-dasharray': type === 'repeat' ? '5 4' : 'none',
        'stroke-linecap': 'round', 'stroke-linejoin': 'round',
        'marker-end': `url(#${type === 'repeat' ? 'wf-repeat-arrow' : 'wf-arrow'})`}));
    }
    stage.style.width = Math.ceil(width * scale) + 'px';
    stage.style.height = Math.ceil(height * scale) + 'px';
  }
  function zoom(value, fit = false) {
    scale = Math.max(.3, Math.min(1.75, value)); fitMode = fit;
    surface.style.transform = `scale(${scale})`;
    byId('workflow-zoom-value').textContent = Math.round(scale * 100) + '%'; redraw();
  }
  function fit() {
    if (task && viewport.clientWidth && surface.offsetWidth)
      zoom(Math.min(1, (viewport.clientWidth - 32) / surface.offsetWidth), true);
  }
  function render(value, result) {
    task = value;
    if (!task?.graph?.length) { clear(); return; }
    const next = JSON.stringify([task.graph, task.outputParameters, task.url, result.pagination, result.page_limit]);
    if (next === signature) return;
    signature = next; generation++;
    links = []; visited = new Set(); nodes = new Map(); canvas.replaceChildren();
    const chain = element('div', 'wf-sequence wf-root'); canvas.append(chain);
    const start = terminal('开始', 'wf-start'), end = terminal('结束', 'wf-finish'); chain.append(start);
    const children = (task.graph[0].sequence || []).map(index => unit(index));
    children.forEach(child => chain.append(child)); chain.append(end);
    const sequence = [start, ...children, end];
    for (let i = 1; i < sequence.length; i++) wire(sequence[i - 1], sequence[i]);
    const count = visited.size, loops = [...visited].filter(id => task.graph[id].option === 8).length;
    const fields = new Set([...visited].flatMap(id => task.graph[id].option === 3 ?
      (task.graph[id].parameters.params || []).map(field => field.name) : []));
    byId('workflow-summary').replaceChildren(...[
      `${count} 个节点`, `${loops} 个循环`, `${fields.size} 个字段`,
      result.pagination === 'page_limit' ? `最多 ${result.page_limit} 页` :
        result.pagination === 'until_end' ? '翻页至末页' : '不翻页'
    ].map(label => element('span', 'wf-stat', label)));
    if (!nodes.has(selected)) selected = nodes.keys().next().value ?? null;
    if (selected !== null) select(selected);
    else inspector.replaceChildren(element('p', 'wf-empty', '流程中暂无执行节点'));
    const current = generation;
    requestAnimationFrame(() => {
      if (current !== generation || !task) return;
      if (fitMode) fit(); else redraw();
    });
  }
  function clear() {
    task = null; signature = ''; selected = null; generation++;
    links = []; nodes.clear(); visited.clear(); canvas.replaceChildren(); svg.replaceChildren();
    inspector.replaceChildren(); byId('workflow-summary').replaceChildren();
    stage.style.width = stage.style.height = '0px'; scale = 1; fitMode = true;
  }
  byId('workflow-zoom-out').onclick = () => zoom(scale - .1);
  byId('workflow-zoom-in').onclick = () => zoom(scale + .1);
  byId('workflow-fit').onclick = () => { fit(); viewport.scrollLeft = 0; };
  new ResizeObserver(() => { if (task && fitMode) fit(); }).observe(viewport);
  return {render, clear};
})();
