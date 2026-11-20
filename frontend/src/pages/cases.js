import {store} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {crashCasesAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {esc,formObject} from '../utils/html.js';
import {action} from '../hooks/action.js';
export function casesPage(){const first=store.logs[0];return `<div class="section-title"><h2>故障注入点</h2><span>崩溃点为已持久化的原操作条数</span></div>${table(['场景','日志集','断电点'],store.cases.map(c=>[esc(c.name),esc(store.logs.find(l=>l.id===c.log_set_id)?.name),c.crash_after]))}<section class="split"><div><h2>选择断电时刻</h2><p>0表示没有任何操作落盘；N表示前N条原操作已经按执行顺序写入日志。断电点按原操作位置计算，等待中的写入如果还没拿到锁，就没有日志记录，也没有落盘。</p><p>环里的撤销是先回滚后放锁：断电点若停在环撤销之前，被撤销的写入仍可能留在日志里，恢复时要连同未提交事务一起退净。</p><p class="big-note">断电位置，决定哪些值留下。</p></div>${session.role!=='viewer'?`<form id="case-form"><label>场景名称<input name="name" value="交班断电点16" required></label><label>日志集<select name="log_set_id">${store.logs.map(l=>`<option value="${l.id}">${esc(l.name)} · ${l.records.length}条</option>`).join('')}</select></label><label>断电点<input name="crash_after" type="number" min="0" max="200" value="${first?Math.min(8,first.records.length):8}" required></label><button type="submit">保存故障场景</button></form>`:'<p class="readonly">观察员可复核故障场景。</p>'}</section>`;}
export function bindCases(render){action(document.querySelector('#case-form'),async f=>{const d=formObject(f);d.log_set_id=Number(d.log_set_id);d.crash_after=Number(d.crash_after);await crashCasesAPI.create(d);await render();});}
