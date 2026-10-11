const $ = id => document.getElementById(id);
const defaultModels = {deepseek:'deepseek-flash', gpt:'gpt-4o-mini', gemini:'gemini-2.5-flash'};
let session = sessionStorage.getItem('ai_rpa_session');
let config = null, messages = [], latestPlan = null, revision = 0;
let stateStatus = 'new', busy = false, timer = null, conversationEpoch = 0;

function show(id, yes = true) { $(id).classList.toggle('hidden', !yes); }
function error(message = '') { $('error').textContent = message; }
function statusLabel(status) {
  return status === 'closed' ? '自动控制浏览器已关闭，网页探索已结束。' : status;
}
function charCount(value) { return Array.from(value).length; }
function credentials() {
  return {provider:$('provider').value, model:$('model').value.trim(), api_key:$('api-key').value.trim()};
}
async function api(path, body) {
  const res = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
function textElement(tag, value, className) {
  const element = document.createElement(tag);
  element.textContent = value;
  if (className) element.className = className;
  return element;
}
function renderPlan(bubble, plan, number) {
  if (plan.risk_assessment) {
    const review = plan.risk_assessment;
    const label = `AI \u5b89\u5168\u5ba1\u6838: ${review.risk || 'unknown'}${review.reason ? ` - ${review.reason}` : ''}`;
    bubble.append(textElement('div', label, 'meta'));
  }
  bubble.append(textElement('div', `AI · 第 ${number} 版流程`, 'who'));
  bubble.append(textElement('h3', plan.task_name || '采集流程'));
  bubble.append(textElement('div', '起始网址：' + (plan.start_url || '待确定') +
    '\n选择理由：' + (plan.url_reason || '未说明'), 'meta'));
  const steps = document.createElement('ol');
  (plan.steps || []).forEach(step => steps.append(textElement('li', step)));
  bubble.append(steps);
  const pagination = plan.pagination === 'until_end' ? '直到没有下一页' :
    plan.pagination === 'page_limit' ? `最多 ${plan.page_limit || '?'} 页` : '不翻页';
  bubble.append(textElement('div', '采集字段：' + ((plan.fields || []).join('、') || '待确定') +
    '\n翻页方式：' + pagination, 'meta'));
  if (plan.notes) bubble.append(textElement('p', plan.notes, 'hint'));
  if (plan.needs_clarification) {
    bubble.append(textElement('h3', '请继续补充'));
    const questions = document.createElement('ol');
    (plan.questions || []).forEach(question => questions.append(textElement('li', question)));
    bubble.append(questions);
  }
}
function renderMessages(items) {
  const log = $('chat-log');
  log.replaceChildren();
  let number = 0;
  items.forEach(item => {
    const bubble = document.createElement('div');
    bubble.className = 'bubble ' + item.role;
    if (item.role === 'user') {
      bubble.append(textElement('div', '你', 'who'));
      bubble.append(textElement('div', item.content || ''));
    } else {
      if (item.edit) {
        bubble.append(textElement('div', 'AI · 局部修改建议', 'who'));
        bubble.append(textElement('p', item.edit.summary || ''));
        (item.edit.questions || []).forEach(q => bubble.append(textElement('p', q)));
        bubble.append(textElement('div', '请在下方流程图区域查看改动并确认应用。', 'hint'));
      } else {
        number += 1;
        renderPlan(bubble, item.plan, number);
      }
    }
    log.append(bubble);
  });
  log.scrollTop = log.scrollHeight;
}
function updateControls() {
  const limit = config?.max_user_message_chars || 1000;
  const turns = config?.max_plan_turns || 15;
  const count = charCount($('chat-input').value.trim());
  const exhausted = revision >= turns;
  const waiting = busy || stateStatus === 'running' || stateStatus === 'planning' || stateStatus === 'awaiting_login';
  $('chat-input').maxLength = limit;
  $('char-count').textContent = `${count}/${limit} 字`;
  $('turn-hint').textContent = `已规划 ${revision}/${turns} 轮；每条消息最多 ${limit} 字。`;
  $('chat-input').disabled = waiting || exhausted;
  $('new-task').disabled = waiting;
  $('change-settings').disabled = waiting;
  $('send').disabled = waiting || exhausted || !count || count > limit;
  $('run').disabled = waiting || stateStatus !== 'planned' || !latestPlan ||
    latestPlan.needs_clarification || !latestPlan.start_url;
  $('chat-hint').textContent = stateStatus === 'planning' || busy ? 'AI 正在处理，请稍候。' :
    stateStatus === 'awaiting_login' ? '请先在 Chrome 中完成登录并点击“已完成登录”。' :
    stateStatus === 'running' ? '已确认流程，正在探索网页。' : exhausted ?
    (latestPlan?.needs_clarification ? '已到对话上限且仍需补充，请开始新对话。' : '已到对话上限，可以确认当前流程或开始新对话。') :
    (!latestPlan ? '请输入采集需求，AI 会先返回一版流程。' :
      latestPlan.needs_clarification ? '请在下方回答一个或多个问题，AI 会保留此前要求并更新流程。' :
      (window.WorkflowEditor?.hasTask() ? '已有流程图：继续输入将生成局部修改建议，确认后才应用。' : '可以确认当前流程，或继续发送修改意见。'));
}
function enterStage(chat) {
  show('setup-card', !chat);
  show('chat-card', chat);
  if (chat) updateControls();
}
function fillTable(id, rows) {
  const table = $(id); table.replaceChildren(); if (!rows.length) return;
  const columns = Object.keys(rows[0]);
  const head = document.createElement('tr');
  columns.forEach(column => head.append(textElement('th', column)));
  table.append(head);
  rows.forEach(row => {
    const tr = document.createElement('tr');
    columns.forEach(column => tr.append(textElement('td', String(row[column] ?? ''))));
    table.append(tr);
  });
}
function renderResult(result) {
  fillTable('samples', result.samples || []);
  fillTable('mapping', result.mapping || []);
  $('pagination-result').textContent = result.pagination === 'until_end' ?
    '已生成翻页循环：直到没有下一页。下一页 XPath：' + result.next_xpath :
    result.pagination === 'page_limit' ?
      `已生成翻页循环：最多 ${result.page_limit} 页。下一页 XPath：${result.next_xpath}` :
      '本流程不翻页。';
  show('profile-path', !!result.profile_dir);
  if (result.profile_dir) $('profile-path').textContent =
    '本次浏览器资料目录：' + result.profile_dir +
    '。下方的本地测试命令会自动使用此目录。若在源码版 EasySpider 界面执行，请先关闭 AI 探索浏览器，在任务执行页的“用户本地浏览器数据目录”填写相对路径：' +
    result.profile_relative_source +
    '，再点“本地直接执行（带用户信息模式）”。执行前请退出原版设计模式并关闭探索 Chrome，以释放资料目录。只勾选带用户信息模式而不填写此目录，仍会使用原版的另一套登录资料。打包版请填写相对于其数据目录的路径；EasySpider 当前界面不接受绝对路径。';
  show('result-card');
  $('save').textContent = result.samples_stale ? '保存当前流程' : '样例正确，保存流程';
  window.WorkflowEditor?.render(result);
}
function showRunCommand(file) {
  const filename = String(file || '').split(/[\\/]/).pop();
  $('run-command').textContent = filename ?
    `python run_native.py --task-file "generated/${filename}"` : '';
}
function hydrate(state) {
  messages = state.messages || [];
  latestPlan = state.plan || null;
  revision = state.revision || 0;
  stateStatus = state.status;
  $('status').textContent = statusLabel(state.status);
  renderMessages(messages);
  show('progress-card', stateStatus === 'running' || stateStatus === 'awaiting_login' ||
    stateStatus === 'error' || stateStatus === 'closed');
  show('login-card', stateStatus === 'awaiting_login');
  show('result-card', stateStatus === 'preview' || stateStatus === 'saved');
  show('saved-card', stateStatus === 'saved');
  if (stateStatus !== 'saved') show('easyspider-editor-link', false);
  if (stateStatus === 'preview' || stateStatus === 'saved') renderResult(state.result);
  else window.WorkflowEditor?.clear();
  if (stateStatus === 'saved') {
    $('saved-path').textContent = state.file;
    showRunCommand(state.file);
  }
  if (stateStatus === 'error' || stateStatus === 'closed') error(state.error);
  if (stateStatus === 'running' || stateStatus === 'planning' || stateStatus === 'awaiting_login') {
    timer = setInterval(poll, 1200);
    poll();
  }
  updateControls();
  window.WorkflowEditor?.receive(state);
}
async function sendMessage() {
  const epoch = conversationEpoch;
  const message = $('chat-input').value.trim();
  const limit = config.max_user_message_chars;
  if (!message) return error('请输入采集需求或修改意见');
  if (charCount(message) > limit) return error(`每条消息最多 ${limit} 字`);
  if (window.WorkflowEditor?.hasTask()) {
    await window.WorkflowEditor.proposeFromChat(message);
    $('chat-input').value = ''; updateControls(); return;
  }
  if (revision >= config.max_plan_turns) return error('本次对话已到轮数上限');
  error(); busy = true; updateControls();
  renderMessages([...messages, {role:'user', content:message}]);
  $('chat-hint').textContent = 'AI 正在生成新的采集流程…';
  try {
    const data = await api('/api/plan', {session, message, ...credentials()});
    if (epoch !== conversationEpoch) return;
    session = data.session; sessionStorage.setItem('ai_rpa_session', session);
    messages = data.messages; latestPlan = data.plan; revision = data.revision;
    stateStatus = latestPlan.needs_clarification ? 'clarifying' : 'planned';
    $('chat-input').value = '';
    ['progress-card','result-card','saved-card'].forEach(id => show(id, false));
    renderMessages(messages);
  } catch (exc) {
    if (epoch !== conversationEpoch) return;
    renderMessages(messages);
    error(exc.message);
  } finally {
    if (epoch === conversationEpoch) { busy = false; updateControls(); }
  }
}
async function poll() {
  const pollingSession = session;
  try {
    const res = await fetch('/api/state?session=' + encodeURIComponent(pollingSession));
    const state = await res.json();
    if (pollingSession !== session) return;
    if (!res.ok) throw new Error(state.error);
    stateStatus = state.status;
    show('login-card', stateStatus === 'awaiting_login');
    if ((stateStatus === 'planned' || stateStatus === 'clarifying') && timer) {
      clearInterval(timer); timer = null;
      if (state.revision !== revision) {
        messages = state.messages || [];
        latestPlan = state.plan;
        revision = state.revision;
        renderMessages(messages);
      }
    }
    $('status').textContent = statusLabel(state.status);
    $('events').replaceChildren();
    (state.events || []).forEach(event => $('events').append(textElement('li', event)));
    if (stateStatus === 'preview' || stateStatus === 'saved') {
      clearInterval(timer); timer = null;
      renderResult(state.result);
      if (stateStatus === 'saved') { show('saved-card'); $('saved-path').textContent = state.file; }
      $('result-card').scrollIntoView({behavior:'smooth'});
    } else if (stateStatus === 'error' || stateStatus === 'closed') {
      clearInterval(timer); timer = null; error(state.error);
    }
    updateControls();
    window.WorkflowEditor?.receive(state);
  } catch (exc) {
    if (pollingSession !== session) return;
    clearInterval(timer); timer = null; error(exc.message);
  }
}

function updateConfigHint() {
  const provider = $('provider').value;
  $('config-hint').textContent = '本地 .env：' +
    (config.configured_keys[provider] ? '已配置当前服务的 Key' : '当前服务尚未配置 Key') +
    '。页面不会保存手动输入的 Key。';
}
$('provider').onchange = () => {
  $('model').value = defaultModels[$('provider').value];
  if (config) updateConfigHint();
};
$('enter-chat').onclick = async () => {
  error();
  if (!config.configured_keys[$('provider').value] && !$('api-key').value.trim())
    return error('请填写当前服务的 API Key，或在本地 .env 中配置');
  if (!/^[A-Za-z0-9._-]{1,100}$/.test($('model').value.trim()))
    return error('模型名仅可包含字母、数字、点、下划线和连字符');
  sessionStorage.setItem('ai_rpa_provider', $('provider').value);
  sessionStorage.setItem('ai_rpa_model', $('model').value.trim());
  try {
    if (session) {
      const res = await fetch('/api/state?session=' + encodeURIComponent(session));
      if (res.ok) hydrate(await res.json());
      else { session = null; sessionStorage.removeItem('ai_rpa_session'); }
    }
    enterStage(true);
    $('chat-input').focus();
  } catch (exc) { error(exc.message); }
};
$('change-settings').onclick = () => {
  clearInterval(timer); timer = null; enterStage(false);
};
$('new-task').onclick = async () => {
  if (window.WorkflowEditor?.hasTask() && session) {
    try { await api('/api/editor/debug/close', {session}); }
    catch (exc) { return error(exc.message); }
  }
  if (stateStatus === 'awaiting_login' && session) api('/api/cancel-login', {session}).catch(() => {});
  conversationEpoch += 1;
  clearInterval(timer); timer = null; session = null; sessionStorage.removeItem('ai_rpa_session');
  messages = []; latestPlan = null; revision = 0; stateStatus = 'new'; busy = false;
  $('chat-input').value = ''; error(); renderMessages(messages);
  ['progress-card','login-card','result-card','saved-card'].forEach(id => show(id, false));
  window.WorkflowEditor?.clear();
  updateControls(); $('chat-input').focus();
};
$('chat-input').oninput = updateControls;
$('chat-input').onkeydown = event => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); if (!$('send').disabled) sendMessage(); }
};
$('send').onclick = sendMessage;
$('run').onclick = async () => {
  const epoch = conversationEpoch;
  error(); busy = true; updateControls();
  try {
    await api('/api/run', {session, ...credentials()});
    if (epoch !== conversationEpoch) return;
    stateStatus = 'running'; show('progress-card');
    $('status').textContent = '正在启动浏览器…';
    timer = setInterval(poll, 1200); await poll();
  } catch (exc) { if (epoch === conversationEpoch) error(exc.message); }
  finally { if (epoch === conversationEpoch) { busy = false; updateControls(); } }
};
$('login-complete').onclick = async () => {
  error(); $('login-complete').disabled = true;
  try { await api('/api/login-complete', {session}); $('status').textContent = '正在验证登录并重新打开目标页…'; }
  catch (exc) { error(exc.message); }
  finally { $('login-complete').disabled = false; }
};
$('cancel-login').onclick = async () => {
  error(); $('cancel-login').disabled = true;
  try { await api('/api/cancel-login', {session}); $('status').textContent = '正在取消探索…'; }
  catch (exc) { error(exc.message); }
  finally { $('cancel-login').disabled = false; }
};
$('save').onclick = async () => {
  error(); $('save').disabled = true;
  try {
    const data = await api('/api/save', {session});
    if (data.state) hydrate(data.state);
    stateStatus = 'saved'; $('saved-path').textContent = data.file;
    showRunCommand(data.file);
    show('saved-card'); $('saved-card').scrollIntoView({behavior:'smooth'});
  } catch (exc) { error(exc.message); }
  finally { $('save').disabled = false; updateControls(); }
};
$('import').onclick = async () => {
  error(); $('import').disabled = true;
  try {
    const data = await api('/api/import', {session});
    $('import-result').textContent = '已导入 EasySpider，任务 ID：' + data.task_id +
      `。本地带用户信息测试：python run_native.py --task-id ${data.task_id}。若在 EasySpider 界面执行，请使用上方显示的资料目录。`;
    const link = $('easyspider-editor-link');
    link.href = 'http://127.0.0.1:8074/taskGrid/FlowChart_CN.html?id=' + data.task_id +
      '&wsport=8084&backEndAddressServiceWrapper=' + encodeURIComponent('http://127.0.0.1:8074');
    show('easyspider-editor-link');
    if (data.state) hydrate(data.state);
  } catch (exc) { error(exc.message); }
  finally { $('import').disabled = false; }
};
async function initialize() {
  try {
    const res = await fetch('/api/config');
    if (!res.ok) throw new Error('无法读取本地配置');
    config = await res.json();
    const provider = sessionStorage.getItem('ai_rpa_provider') || config.provider;
    $('provider').value = provider;
    $('model').value = sessionStorage.getItem('ai_rpa_model') ||
      (provider === config.provider ? config.model : defaultModels[provider]);
    updateConfigHint();
    if (session) $('enter-chat').textContent = '继续对话';
    updateControls();
  } catch (exc) { error(exc.message); }
}
initialize();
