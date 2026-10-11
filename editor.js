/* Original EasySpider designer handoff and confirmed edits to the same task. */
window.WorkflowEditor = (() => {
  let task = null, version = 1, proposalId = null, native = null, undoCount = 0;
  const el = id => document.getElementById(id);
  async function perform(fn) {
    error(); busy = true; updateControls();
    el('workflow-card').querySelectorAll('button').forEach(button => button.disabled = true);
    try { await fn(); }
    catch (exc) { error(exc.message); }
    finally {
      busy = false; updateControls();
      el('workflow-card').querySelectorAll('button').forEach(button => button.disabled = false);
      el('undo-edit').disabled = !undoCount;
    }
  }
  function call(path, extra = {}) {
    return api('/api/editor/' + path, {session, version, ...extra});
  }
  function accept(state) { hydrate(state); }
  function proposal(value) {
    proposalId = value?.proposal_id || null;
    el('edit-proposal').classList.toggle('hidden', !value);
    if (!value) return;
    el('edit-summary').textContent = value.summary + (value.questions?.length ? '\n请补充：' + value.questions.join('；') : '');
    el('edit-changes').replaceChildren();
    for (const change of value.changes || []) {
      const row = document.createElement('div'); row.className = 'patch-change';
      row.append(textElement('div', change.path), textElement('code',
        JSON.stringify(change.before) + ' → ' + JSON.stringify(change.after)));
      el('edit-changes').append(row);
    }
    el('confirm-edit').classList.toggle('hidden', !value.operations?.length || !!value.questions?.length);
    el('confirm-edit').textContent = native ? '确认写回并刷新原版设计器' : '确认应用修改';
  }
  function render(result) {
    if (!result?.task?.graph?.length) { clear(); return; }
    task = result.task; version = result.edit_version || 1;
    window.WorkflowDiagram.render(task, result);
    el('workflow-card').classList.remove('hidden');
    el('workflow-version').textContent = `${task.name} · ${task.graph.length - 1} 个节点 · 本地第 ${version} 版`;
    el('native-profile').value = result.profile_relative_source || '';
    el('samples-state').textContent = result.samples_stale ?
      '流程已改变，请在原版设计器调试，正式采集前运行任务检查完整结果。' : '查看试采样例后，可将流程交给原版设计器修改和调试。';
    el('edit-message').maxLength = config?.max_user_message_chars || 1000;
  }
  function receive(state) {
    native = state.native || null;
    undoCount = state.undo_count || 0;
    el('undo-edit').disabled = !undoCount;
    el('native-status').textContent = native ?
      `EasySpider 任务 ID：${native.task_id}。${native.message || ''}` :
      '请先启动 EasySpider，并用显示的资料目录启动带用户信息的设计 Chrome。';
    el('native-open').textContent = native ? '打开当前任务的原版设计器' : '在 EasySpider 原版设计器打开流程';
    el('native-view').classList.toggle('hidden', !native);
    if (native) {
      el('native-view').href = native.editor_url;
      el('native-profile').value = native.profile_relative_source;
    }
    proposal(state.pending_edit);
  }
  function clear() {
    task = null; native = null; version = 1; undoCount = 0;
    window.WorkflowDiagram.clear();
    el('workflow-card').classList.add('hidden'); proposal(null);
  }
  async function propose(message) {
    const limit = config?.max_user_message_chars || 1000;
    if (!message?.trim() || charCount(message.trim()) > limit) throw new Error(`修改要求须为 1 到 ${limit} 字`);
    accept(await call('propose', {message:message.trim(), ...credentials()}));
  }
  el('native-open').onclick = () => perform(async () => accept(await call('native/open')));
  el('native-read').onclick = () => perform(async () => accept(await call('native/read')));
  el('native-observe').onclick = () => perform(async () => {
    const data = await call('native/observe');
    if (data.state) accept(data.state);
    el('native-page').textContent = `${data.message}：${data.observation.title} · ${data.observation.elements.length} 个元素候选 · ${data.observation.lists.length} 组列表`;
  });
  el('native-copy-profile').onclick = () => perform(async () => {
    await navigator.clipboard.writeText(el('native-profile').value);
    el('native-status').textContent = '资料目录已复制，请粘贴到 EasySpider 首页的“指定用户信息目录”。';
  });
  el('propose-edit').onclick = () => perform(() => propose(el('edit-message').value));
  el('confirm-edit').onclick = () => perform(async () => accept(await call('apply', {proposal_id:proposalId})));
  el('discard-edit').onclick = () => perform(async () => accept(await call('discard', {proposal_id:proposalId})));
  el('undo-edit').onclick = () => perform(async () => accept(await call('undo')));
  return {render, receive, clear, hasTask:() => !!task,
    proposeFromChat:message => perform(() => propose(message))};
})();
