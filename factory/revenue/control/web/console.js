'use strict';
// The token stays in this page's memory. No cookie, storage or agent-owned session.
let token = '', identity = null, policy = null;
const ownerPage = location.pathname === '/owner';
const el = (id) => document.getElementById(id);
const show = (id, value) => { el(id).textContent = JSON.stringify(value, null, 2); };
el('purpose').textContent = ownerPage ? '所有者専用の承認画面です。実行先・内容・予算・根拠を確認して承認します。' : 'エージェントの作業状況と根拠を確認します。承認権限は専用の所有者アカウントに限られます。';
async function api(path, payload) {
  const response = await fetch('/api/' + path, { method: payload ? 'POST' : 'GET', credentials: 'omit', cache: 'no-store',
    headers: { Authorization: 'Bearer ' + token, ...(payload ? { 'Content-Type': 'application/json' } : {}) },
    body: payload ? JSON.stringify({schema_version:'revenue-controller/0.2',idempotency_key:crypto.randomUUID(),payload}) : undefined });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '接続できませんでした');
  return data;
}
async function guard(task) { el('error').textContent = ''; try { await task(); } catch (error) { el('error').textContent = error.message; } }
function reviewRef() { const value = el('review-ref').value.trim(); if (!value) throw new Error('確認記録を入力してください'); return value; }
async function refresh() {
  const [state, proposals, opportunities] = await Promise.all([api('summary'), api('proposals'), api('opportunities')]);
  show('summary', state); show('opportunities', opportunities); el('proposals').replaceChildren();
  for (const proposal of proposals) {
    const article = document.createElement('article'), pre = document.createElement('pre');
    pre.textContent = JSON.stringify(proposal, null, 2); article.append(pre);
    if (ownerPage && identity.role === 'owner_approver') {
      const button = document.createElement('button'); button.textContent = 'この内容だけを10分間承認';
      button.addEventListener('click', () => guard(async () => {
        const approval = await api('approve', { bindings: proposal.bindings, expires_at: new Date(Date.now() + 600000).toISOString(), evidence_review_ref: reviewRef() });
        button.disabled = true; const receipt = document.createElement('pre'); receipt.textContent = JSON.stringify(approval, null, 2); article.append(receipt);
      })); article.append(button);
    }
    el('proposals').append(article);
  }
}
el('login').addEventListener('submit', (event) => { event.preventDefault(); guard(async () => {
  token = el('token').value; el('token').value = ''; identity = await api('identity');
  if (ownerPage && identity.role !== 'owner_approver') { token = ''; throw new Error('所有者専用の資格情報が必要です'); }
  policy = await api('policy'); el('identity').textContent = identity.actor_id + ' / ' + identity.role;
  el('connected').hidden = false; el('owner-controls').hidden = !(ownerPage && identity.role === 'owner_approver'); await refresh();
}); });
el('refresh').addEventListener('click', () => guard(refresh));
el('logout').addEventListener('click', () => { token = ''; location.reload(); });
el('stop').addEventListener('click', () => guard(async () => { await api('stop', {reason:'OWNER_STOP'}); await refresh(); }));
el('resume').addEventListener('click', () => guard(async () => { await api('resume', {policy_sha256:policy.sha256,review_ref:reviewRef()}); await refresh(); }));
el('budget').addEventListener('submit', (event) => { event.preventDefault(); guard(async () => {
  const now = Date.now(); await api('budget', {caps:{cash:Number(el('cash').value),work:Number(el('work').value),human:Number(el('human').value)},cost_basis_ref:reviewRef(),starts_at:new Date(now).toISOString(),ends_at:new Date(now+86400000).toISOString()}); await refresh();
}); });
