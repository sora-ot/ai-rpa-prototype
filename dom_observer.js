// Runs inside Selenium, with configuration in arguments[0]. No page DOM is mutated.
const config = arguments[0] || {};
const limit = config.limit || 1000, focusMode = config.focus || 'balanced';
const styleCache = new WeakMap(), rectCache = new WeakMap(), visibleCache = new WeakMap();
const absoluteCache = new WeakMap(), xpathCache = new WeakMap(), uniqueCache = new Map();
const style = e => {if (!styleCache.has(e)) styleCache.set(e, getComputedStyle(e)); return styleCache.get(e);};
const rect = e => {if (!rectCache.has(e)) rectCache.set(e, e.getBoundingClientRect()); return rectCache.get(e);};
const visible = e => {
  if (!visibleCache.has(e)) {
    const r = rect(e), s = style(e);
    visibleCache.set(e, r.width > 0 && r.height > 0 && s.display !== 'none' &&
      s.visibility !== 'hidden' && Number(s.opacity || 1) > 0 &&
      (!e.checkVisibility || e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})));
  }
  return visibleCache.get(e);
};
const absolute = e => {
  if (absoluteCache.has(e)) return absoluteCache.get(e);
  let i = 1;
  for (let n = e.previousElementSibling; n; n = n.previousElementSibling) if (n.tagName === e.tagName) i++;
  const tag = e.namespaceURI === 'http://www.w3.org/2000/svg' ? "*[local-name()='" + e.localName + "']" : e.tagName.toLowerCase();
  const value = (e.parentElement ? absolute(e.parentElement) : '') + '/' + tag + '[' + i + ']';
  absoluteCache.set(e, value); return value;
};
const unique = path => {
  if (!uniqueCache.has(path)) {
    try {uniqueCache.set(path, document.evaluate('count(' + path + ')', document, null, XPathResult.NUMBER_TYPE, null).numberValue === 1);}
    catch {uniqueCache.set(path, false);}
  }
  return uniqueCache.get(path);
};
const literal = value => value.includes('"') ? (value.includes("'") ? null : "'" + value + "'") : '"' + value + '"';
const cut = (value, n) => {
  const text = String(value || '');
  if (n < text.length && n > 0 && /[\uD800-\uDBFF]/.test(text[n - 1]) && /[\uDC00-\uDFFF]/.test(text[n])) n--;
  return text.slice(0, n);
};
const clean = (value, n = 180) => cut(String(value || '').trim().replace(/\s+/g, ' '), n);
const nextToken = e => {
  const label = clean(e.innerText || e.getAttribute('aria-label'));
  if (/上一页|上页|\b(prev|previous)\b/i.test(label)) return '';
  if (/下一页|下页/.test(label)) return '下一页';
  if (/\bnext\b/i.test(label)) return 'next';
  if (e.getAttribute('rel') === 'next' || e.parentElement?.matches('li.next')) return 'rel';
  return '';
};
const isNext = e => e.matches('a,button,[role="button"]') && !!nextToken(e);
const xp = e => {
  if (xpathCache.has(e)) return xpathCache.get(e);
  let path = '';
  const tag = e.localName;
  if (e.namespaceURI !== 'http://www.w3.org/2000/svg') {
    if (tag === 'a' && e.parentElement?.matches('li.next')) {
      const p = '//li[contains(concat(" ",normalize-space(@class)," ")," next ")]/a'; if (unique(p)) path = p;
    }
    const token = isNext(e) ? nextToken(e) : '';
    if (!path && token === '下一页') {
      const p = '//' + tag + '[contains(normalize-space(.),"下一页") or contains(normalize-space(.),"下页")]'; if (unique(p)) path = p;
    }
    if (!path && token === 'next') {
      const p = '//' + tag + '[contains(translate(normalize-space(.),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"next")]'; if (unique(p)) path = p;
    }
    if (!path && e.getAttribute('rel') === 'next') {const p = '//' + tag + '[@rel="next"]'; if (unique(p)) path = p;}
    const role = e.getAttribute('role'), label = clean(e.innerText);
    if (!path && ['tab', 'button'].includes(role) && label.length > 0 && label.length <= 30) {
      const l = literal(label), r = literal(role);
      if (l && r) {const p = '//' + tag + '[@role=' + r + ' and normalize-space(.)=' + l + ']'; if (unique(p)) path = p;}
    }
    for (const attr of ['id', 'data-testid', 'name', 'aria-label']) {
      if (path) break;
      const value = e.getAttribute(attr), quoted = value && literal(value);
      if (quoted) {const p = '//' + tag + '[@' + attr + '=' + quoted + ']'; if (unique(p)) path = p;}
    }
    const cls = e.classList?.[0];
    if (!path && cls && /^[\w-]+$/.test(cls)) {
      const p = '//' + tag + '[contains(concat(" ",normalize-space(@class)," ")," ' + cls + ' ")]'; if (unique(p)) path = p;
    }
  }
  path = path || absolute(e); xpathCache.set(e, path); return path;
};
const safeUrl = value => {
  if (/^(?:data|blob|javascript):/i.test(value || '')) return '[inline resource]';
  try {
    const u = new URL(value, location.href);
    for (const key of [...u.searchParams.keys()]) if (/token|secret|session|auth|code|password|credential|(?:^|[_-])(?:api[_-]?)?key(?:$|[_-])/i.test(key)) u.searchParams.set(key, '[redacted]');
    u.hash = ''; return u.href;
  } catch {return '';}
};
const sensitive = e => e.matches('input,textarea,[contenteditable]') &&
  (e.type === 'password' || e.type === 'hidden' ||
   /password|passwd|secret|token|credential|verification|验证码|密码|(?:^|[\s_-])(?:auth|session|otp)(?:$|[\s_-])/i.test(
     [e.name, e.id, e.getAttribute('placeholder'), e.getAttribute('autocomplete')].join(' ')));
const editable = e => {
  if (sensitive(e) || e.getAttribute('aria-hidden') === 'true' || e.closest('[inert]') || style(e).pointerEvents === 'none') return false;
  if (e.matches('input')) return !['hidden', 'button', 'submit', 'reset', 'checkbox', 'radio', 'file', 'password'].includes(e.type) && !e.disabled && !e.readOnly;
  if (e.matches('textarea')) return !e.disabled && !e.readOnly;
  return e.isContentEditable || e.getAttribute('role') === 'textbox';
};
const iconHint = e => clean([e, ...e.querySelectorAll('svg,use,img')].slice(0, 8).map(n =>
  (n.getAttribute('href') || n.getAttribute('xlink:href') || '').split('#')[1] ||
  n.getAttribute('alt') || n.getAttribute('aria-label') || n.getAttribute('title') ||
  (n.localName === 'img' ? n.getAttribute('class') : '')).filter(Boolean).join(' '), 100);
const semanticLabel = e => clean(e.getAttribute('aria-label') || e.getAttribute('title') ||
  (e.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ') ||
  [...(e.labels || [])].map(label => label.innerText).join(' ') || e.querySelector('svg title')?.textContent || e.getAttribute('alt'));
const clickable = e => !e.disabled && e.getAttribute('aria-disabled') !== 'true' &&
  (e.matches('a[href],button,summary,input[type="submit"],input[type="button"],input[type="checkbox"],input[type="radio"],[onclick]') ||
   /^(button|tab|menuitem|menuitemcheckbox|menuitemradio|option|checkbox|radio|switch|treeitem|combobox)$/.test(e.getAttribute('role') || '') ||
   e.hasAttribute('tabindex') || e.hasAttribute('aria-controls') || style(e).cursor === 'pointer');
const iconControl = e => {const r = rect(e); return !e.disabled && r.width <= 180 && r.height <= 120 && !!iconHint(e);};
const controlTarget = e => {
  const explicit = e.closest('button,a[href],[role="button"],[role="tab"],summary'); if (explicit) return explicit;
  if (!e.matches('svg,use,img,path')) return e;
  for (let p = e.parentElement, depth = 0; p && depth < 4; p = p.parentElement, depth++) if (clickable(p)) return p;
  return e.parentElement && iconControl(e.parentElement) ? e.parentElement : e;
};
const ignored = e => e.matches('html,body,head,script,style,noscript,template,meta,link');
const ownText = e => clean([...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' '));
const navigationCache = new WeakMap();
const inNavigation = e => {
  if (!e) return false;
  if (!navigationCache.has(e)) navigationCache.set(e,
    e.matches('nav,header,footer,[role="navigation"]') ||
    /(?:^|[\s_-])(?:nav|navigation|menu|header|footer)(?:$|[\s_-])/i.test((e.id || '') + ' ' + (e.getAttribute('class') || '')) ||
    inNavigation(e.parentElement));
  return navigationCache.get(e);
};
const eligible = e => !ignored(e) && !sensitive(e) && visible(e) &&
  (!!clean(e.innerText || e.textContent) || !!semanticLabel(e) || !!iconHint(e) || editable(e) || clickable(e) ||
   e.matches('select,option,img,video,audio,canvas,svg,use,path,progress,meter') ||
   !!e.getAttribute('placeholder') || !!e.getAttribute('href') || !!e.getAttribute('src'));
const elementInfo = (e, id) => {
  const r = rect(e), editables = editable(e) ? [e] : [...e.querySelectorAll('input,textarea,[contenteditable],[role="textbox"]')].filter(n => visible(n) && editable(n));
  const info = {id, tag: e.localName, dom_id: e.id || '', name: e.getAttribute('name') || '',
    text: clean(e.innerText || e.textContent), own_text: ownText(e), class_name: clean(e.getAttribute('class'), 260),
    title_attr: clean(e.getAttribute('title'), 400), placeholder: clean(e.getAttribute('placeholder')),
    aria_label: clean(e.getAttribute('aria-label')), semantic_label: semanticLabel(e), icon_hint: iconHint(e),
    href: e.getAttribute('href') ? safeUrl(e.getAttribute('href')) : '', role: e.getAttribute('role') || '', type: e.getAttribute('type') || '',
    editable: editable(e), editable_descendants: editables.length, editable_xpath: editables.length === 1 ? xp(editables[0]) : '',
    clickable: clickable(e), disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true',
    readonly: !!e.readOnly, selected: e.getAttribute('aria-selected') === 'true' || e.getAttribute('aria-pressed') === 'true' || !!e.selected,
    in_viewport: r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth,
    is_next: isNext(e), bbox: [r.x, r.y, r.width, r.height].map(Math.round),
    xpath: xp(e), absolute_xpath: absolute(e), parent_tag: e.parentElement?.localName || '',
    landmark: e.closest('main,nav,header,footer,[role="main"],[role="navigation"]')?.localName || ''};
  if ((info.clickable || info.editable || iconControl(e)) && info.in_viewport) {
    const x = r.x + r.width / 2, y = r.y + r.height / 2;
    if (x >= 0 && x < innerWidth && y >= 0 && y < innerHeight) {
      const top = document.elementFromPoint(x, y);
      info.obscured = !!top && top !== e && !e.contains(top);
      if (info.obscured) info.covered_by = clean(top.getAttribute('aria-label') || top.getAttribute('class') || top.tagName, 90);
    }
  }
  return info;
};

function findLists(root) {
// Lists are ranked by repeated layout and field structure, without business keywords.
const shape = e => [...e.children].slice(0, 12).map(n => n.localName).join(',');
const groupsFound = [], groupKeys = new Set();
for (const parent of [...(root.nodeType === 1 && root.matches('main,article,section,ul,ol,table,tbody,thead,dl,div,[role="list"],[role="grid"]') ? [root] : []), ...root.querySelectorAll('main,article,section,ul,ol,table,tbody,thead,dl,div,[role="list"],[role="grid"]')]) {
  const children = [...parent.children].filter(visible); if (children.length < 2) continue;
  const buckets = new Map();
  for (const e of children) {
    const cls = clean(e.getAttribute('class')).split(/\s+/).slice(0, 2).join(' ');
    for (const key of [e.localName + '|class|' + cls, e.localName + '|shape|' + shape(e)]) {
      if (!buckets.has(key)) buckets.set(key, []); buckets.get(key).push(e);
    }
  }
  for (const group of buckets.values()) {
    if (group.length < 2) continue;
    const key = absolute(group[0]) + '|' + group.length + '|' + absolute(group[group.length - 1]);
    if (groupKeys.has(key)) continue; groupKeys.add(key);
    const sample = group.slice(0, 6), first = group[0], tag = first.localName;
    const average = fn => sample.reduce((sum, e) => sum + fn(e), 0) / sample.length;
    const consistency = sample.filter(e => shape(e) === shape(first)).length / sample.length;
    const textLength = average(e => clean(e.innerText, 500).length);
    const descendants = average(e => e.querySelectorAll('*').length);
    const features = {repeat: Math.min(group.length, 30), structure: Math.round(consistency * 20),
      text_density: Math.min(20, Math.round(average(e => clean(e.innerText, 500).length) / 12)),
      field_structure: Math.min(12, Math.round(descendants / 6)),
      substantive_text: textLength >= 16 ? 12 : 0,
      short_labels: textLength <= 8 ? -10 : 0,
      aggregate_region: descendants > 100 ? -20 : 0,
      nested_records: average(e => e.querySelectorAll('article,li,tr,[role="listitem"],[role="row"]').length) >= 2 ? -20 : 0,
      many_links: average(e => e.querySelectorAll('a[href]').length) > 8 ? -12 : 0,
      links: sample.some(e => e.querySelector('a[href]')) ? 5 : 0,
      headings: sample.some(e => e.querySelector('h1,h2,h3,h4,[role="heading"]')) ? 8 : 0,
      metadata: sample.some(e => e.querySelector('time,[datetime]') || /\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}|\d{1,2}:\d{2}/.test(e.innerText || '')) ? 8 : 0,
      records: /^(li|tr|article|dt|dd)$/.test(tag) || /^(row|article|listitem)$/.test(first.getAttribute('role') || '') ? 8 : 0,
      main_context: parent.closest('main,article,[role="main"]') ? 12 : 0,
      navigation_context: inNavigation(parent) ? -25 : 0};
    let path = xp(parent) + '/' + tag;
    const common = [...first.classList].filter(c => /^[\w-]+$/.test(c) && group.every(e => e.classList.contains(c))).slice(0, 2);
    if (common.length) path += '[' + common.map(c => 'contains(concat(" ",normalize-space(@class)," ")," ' + c + ' ")').join(' and ') + ']';
    const matching = candidatePath => {
      const result = document.evaluate(candidatePath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      return result.snapshotLength === group.length && group.every((e, i) => result.snapshotItem(i) === e);
    };
    if (!matching(path)) {
      const layout = [...first.children].slice(0, 12).map((e, i) => "local-name(*[" + (i + 1) + "])='" + e.localName + "'");
      if (group.every(e => e.children.length === first.children.length)) layout.push('count(*)=' + first.children.length);
      if (layout.length) path += '[' + layout.join(' and ') + ']';
      if (!matching(path)) continue;
    }
    const score = Object.values(features).reduce((sum, value) => sum + value, 0);
    groupsFound.push({parent, group, score, features, path});
  }
}
groupsFound.sort((a, b) => b.score - a.score);
return groupsFound;
}

if (config.mode === 'dom') {
  const resolve = path => document.evaluate(path, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
  let root = config.root_xpath ? resolve(config.root_xpath) : document.body;
  if (!root || root.nodeType !== 1) throw new Error('DOM 区域已变化，请重新观察页面');
  if (config.scope === 'search') {
    const form = root.closest('form,[role="search"]');
    if (form) root = form;
    else for (let d = 0; root.parentElement && d < 4; d++) {
      if (root.parentElement === document.body || rect(root.parentElement).height > 400) break;
      root = root.parentElement;
    }
  } else for (let d = 0; root.parentElement && d < (config.depth || 0); d++) root = root.parentElement;
  const known = new Map((config.known || []).map(e => [e.absolute_xpath, e.id]));
  let nextId = Math.max(0, ...(config.known || []).map(e => Number(e.id.slice(1)) || 0));
  const listKey = e => e.xpath + '|' + e.first_xpath;
  const knownLists = new Map((config.known_lists || []).map(g => [listKey(g), g.id]));
  let nextListId = Math.max(0, ...(config.known_lists || []).map(g => Number(g.id.slice(1)) || 0));
  const foundLists = findLists(root).map(({group, score, features, path}) => {
    const item = {xpath: path, first_xpath: absolute(group[0]), count: group.length, score, features,
      samples: group.slice(0, 2).map(e => clean(e.innerText, 260))};
    return {...item, id: knownLists.get(listKey(item)) || 'l' + (++nextListId), first: group[0]};
  });
  const firstLists = new Map();
  for (const g of foundLists) {if (!firstLists.has(g.first)) firstLists.set(g.first, []); firstLists.get(g.first).push(g.id);}
  const parts = [], entries = [];
  let length = 0, removed = 0;
  const push = s => {parts.push(s); length += s.length;};
  const escape = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const attrs = /^(?:id|class|role|name|type|placeholder|title|alt|href|src|datetime|for|colspan|rowspan|disabled|readonly|checked|selected|hidden|open|aria-[\w-]+|data-testid|data-test|data-qa|data-action)$/;
  const walk = node => {
    if (node.nodeType === 3) {push(escape(node.textContent.replace(/\s+/g, ' '))); return;}
    if (node.nodeType !== 1) return;
    const e = node;
    if (e.matches('head,script,style,noscript,template,meta,link') || sensitive(e)) {removed++; return;}
    const start = length, tag = e.localName;
    let id = '';
    if (eligible(e)) {id = known.get(absolute(e)) || 'e' + (++nextId);}
    let opening = '<' + tag;
    if (id) opening += ' data-rpa-id="' + id + '"';
    if (firstLists.has(e)) opening += ' data-rpa-list="' + firstLists.get(e).join(' ') + '"';
    for (const a of e.attributes) {
      if (!attrs.test(a.name) || /token|secret|password|credential/i.test(a.name)) continue;
      const value = ['href', 'src'].includes(a.name) ? safeUrl(a.value) : a.value;
      opening += ' ' + a.name + '="' + escape(cut(value, 2000)) + '"';
    }
    // Render state augments markup. Form values and textarea contents are not serialized.
    if (!visible(e) || e.matches('input,textarea,select,button,[role],summary') || clickable(e))
      opening += ' data-rpa-visible="' + visible(e) + '" data-rpa-editable="' + editable(e) + '"';
    push(opening + '>');
    if (!e.matches('input,textarea')) for (const child of e.childNodes) walk(child);
    push('</' + tag + '>');
    if (id) entries.push({e, id, start, end: length});
  };
  walk(root);
  const html = parts.join('');
  let offset = Math.min(config.offset || 0, html.length), end = Math.min(offset + config.max_chars, html.length);
  if (offset > 0 && /[\uDC00-\uDFFF]/.test(html[offset] || '') && /[\uD800-\uDBFF]/.test(html[offset - 1])) offset--;
  if (end < html.length && /[\uD800-\uDBFF]/.test(html[end - 1] || '') && /[\uDC00-\uDFFF]/.test(html[end])) end--;
  const relevant = entries.filter(n => n.start < end && n.end > offset);
  const elements = relevant.map(n => elementInfo(n.e, n.id));
  const nodeIds = new Map(relevant.map(n => [n.e, n.id]));
  const lists = foundLists.filter(g => nodeIds.has(g.first) || relevant.some(n => g.first.contains(n.e)))
    .map(({first, ...item}) => item);
  for (let i = 0; i < relevant.length; i++) elements[i].parent_id = nodeIds.get(relevant[i].e.parentElement) || '';
  // Hash the complete cleaned tree so pagination can detect asynchronous DOM changes.
  let hash = 2166136261;
  const stableHtml = html.replace(/ data-rpa-id="e\d+"/g, '').replace(/ data-rpa-list="[l\d ]+"/g, '');
  for (let i = 0; i < stableHtml.length; i++) hash = Math.imul(hash ^ stableHtml.charCodeAt(i), 16777619) >>> 0;
  return {url: safeUrl(location.href), elements, lists, dom: {scope: config.scope, root_tag: root.localName,
    html: html.slice(offset, end), offset, next_offset: end < html.length ? end : null,
    total_chars: html.length, truncated: end < html.length, removed_nodes: removed,
    fingerprint: String(hash), note: 'data-rpa-id 可选择元素；data-rpa-list 标记首条记录及列表 ID；分页片段可能从标签中间开始。'}};
}

const all = [...document.querySelectorAll('*')].filter(e => !ignored(e) && !e.closest('script,style,noscript,template'));
const valid = all.filter(eligible), validSet = new Set(valid);
const allControls = [...new Set(all.map(controlTarget))].filter(e => validSet.has(e) &&
  (editable(e) || e.matches('select') || clickable(e) || iconControl(e)));
const inputs = allControls.filter(editable);
const searchInputs = inputs.filter(e => /search|query|keyword|搜索|关键字|关键词|检索/i.test(
  [e.id, e.name, e.getAttribute('class'), e.getAttribute('placeholder'), semanticLabel(e)].join(' ')) || e.type === 'search');
const searchGroups = searchInputs.map(input => {
  let buttons = [];
  for (let p = input.parentElement, d = 0; p && d < 5; p = p.parentElement, d++) {
    if (p === document.body || rect(p).height > 400) break;
    buttons.push(...allControls.filter(e => e !== input && !editable(e) && !e.contains(input) && p.contains(e) && (clickable(e) || iconControl(e))));
  }
  buttons = [...new Set(buttons)];
  const hints = e => [semanticLabel(e), iconHint(e), e.innerText, e.getAttribute('class')].join(' ');
  const likely = buttons.filter(e => /search|submit|magnif|搜索|检索|查询/i.test(hints(e)) || (e.type === 'submit' && input.form && e.form === input.form));
  const fallback = buttons.filter(e => !/\b(add|close|clear|attach|upload|cancel)\b|添加|关闭|清除|附件|上传/i.test(hints(e)));
  return {input, buttons: (likely.length ? likely : fallback).slice(0, 16)};
});
const relatedControls = searchGroups.flatMap(g => [g.input, ...g.buttons]);

const groupsFound = findLists(document);
const picked = groupsFound.slice(0, config.maxLists || 80);
const lists = picked.map(({group, score, features, path}, i) => ({id: 'l' + (i + 1), xpath: path,
  first_xpath: absolute(group[0]), count: group.length, score, features,
  samples: group.slice(0, 2).map(e => clean(e.innerText, 260))}));
const interleave = (queues, batch = 6) => {
  const result = [], offsets = queues.map(() => 0);
  while (queues.some((q, i) => offsets[i] < q.length)) for (let i = 0; i < queues.length; i++) {
    result.push(...queues[i].slice(offsets[i], offsets[i] + batch)); offsets[i] += batch;
  }
  return result;
};
const priorityLinks = allControls.filter(isNext);
const priorityControls = allControls.filter(e => /搜索|检索|筛选|排序|展开|更多|下一页|\b(sort|search|filter|next|more)\b/i.test(
  [e.innerText, semanticLabel(e), iconHint(e), e.getAttribute('placeholder')].join(' ')));
const otherControls = allControls.slice().sort((a, b) => Number(inNavigation(a)) - Number(inNavigation(b)));
const controlBudget = focusMode === 'controls' ? limit : Math.max(200, Math.floor(limit * .4));
const controls = [...new Set([...relatedControls, ...inputs, ...priorityControls, ...otherControls])].slice(0, controlBudget);
const records = interleave(picked.map(({group}) => group.slice(0, 2).flatMap(e => [e, ...e.querySelectorAll('*')])));
const regions = [...document.querySelectorAll('main,nav,header,footer,[role="main"],[role="navigation"]')];
const regionBuckets = regions.map(root => valid.filter(e => root.contains(e)));
const diverse = interleave([...regionBuckets, valid.filter(e => !regions.some(root => root.contains(e)))]);
const ownContent = valid.filter(e => ownText(e) && !inNavigation(e));
const ordered = focusMode === 'content' ? [...priorityLinks, ...relatedControls, ...records, ...ownContent, ...diverse, ...controls] :
  [...priorityLinks, ...controls, ...records, ...ownContent, ...diverse];
const chosen = [], chosenSet = new Set();
for (const e of ordered) {
  if (chosen.length >= limit) break;
  if (!validSet.has(e) || chosenSet.has(e)) continue;
  chosenSet.add(e); chosen.push(e);
}
const nodeIds = new Map(chosen.map((e, i) => [e, 'e' + (i + 1)]));
const elements = chosen.map(e => elementInfo(e, nodeIds.get(e)));
for (let i = 0; i < chosen.length; i++) elements[i].parent_id = nodeIds.get(chosen[i].parentElement) || '';
const searchForms = searchGroups.filter(g => nodeIds.has(g.input)).map(g => ({input_id: nodeIds.get(g.input),
  submit_ids: g.buttons.filter(e => nodeIds.has(e)).map(e => nodeIds.get(e)),
  label: semanticLabel(g.input) || g.input.placeholder || '搜索输入', query: clean(g.input.value, 200)}));
return {url: safeUrl(location.href), title: document.title, text: cut(document.body?.innerText || '', config.textChars || 6000),
  elements, lists, search_forms: searchForms, observation_stats: {focus: focusMode, budget: limit,
    scanned_nodes: all.length, eligible_elements: valid.length, elements: elements.length,
    omitted_elements: valid.length - elements.length, candidate_coverage: valid.length ? elements.length / valid.length : 1,
    visible_controls: allControls.length, controls_returned: allControls.filter(e => chosenSet.has(e)).length,
    lists_found: groupsFound.length, lists_returned: lists.length, truncated: valid.length > elements.length},
  next_candidates: elements.filter(e => e.is_next).map(e => ({id: e.id, text: e.text, href: e.href, xpath: e.xpath}))};
