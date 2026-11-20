import {store} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {recoveriesAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {badge} from '../components/common/status.js';
import {esc,formObject} from '../utils/html.js';
import {action} from '../hooks/action.js';
export function runsPage(){return `<section class="split compact"><div><h2>发起一次恢复演练</h2><p>提交时冻结操作顺序与断电点。后台按执行顺序重做日志，撤销已记录的显式/环内回滚，再按逆序撤销断电时未结束的事务，最后与独立提交结果对照。</p></div>${session.role!=='viewer'?`<form id="run-form"><label>故障场景<select name="crash_case_id">${store.cases.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></label><label>记录标签<input name="label" value="交班并发核对" required></label><button type="submit">冻结历史并执行 →</button></form>`:'<p class="readonly">观察员可查看下方恢复结果。</p>'}</section><div class="section-title"><h2>恢复队列</h2><button class="secondary" id="refresh">刷新状态</button></div>${table(['编号','恢复记录','状态','等待/撤销','恢复核对','证据'],store.runs.map(r=>[r.id,esc(r.label),badge(r.status),r.result?`${r.result.waits?.length??0}次等待 / ${r.result.aborts?.length??0}次撤销`:'待计算',r.result?.invariant_passed?'与提交事务一致':r.result?'需检查':'待计算',`<a href="#detail/${r.id}">查看恢复轨迹 →</a>`]))}`;}
export function bindRuns(render){document.querySelector('#refresh').onclick=render;action(document.querySelector('#run-form'),async f=>{const d=formObject(f);d.crash_case_id=Number(d.crash_case_id);const job=await recoveriesAPI.create(d);location.hash='#detail/'+job.id;});}
