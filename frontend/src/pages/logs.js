import {store} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {logSetsAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {esc,formObject} from '../utils/html.js';
import {SAMPLE} from '../constants/sample.js';
import {action} from '../hooks/action.js';
export function logsPage(){const sample=store.logs[0];return `<div class="section-title"><h2>事务日志集</h2><span>最多200条操作 / 30个键</span></div>${table(['日志集','初始账本','操作数','说明'],store.logs.map(s=>[esc(s.name),esc(JSON.stringify(s.initial)),s.records.length,esc(s.description)]))}<section class="split"><div><h2>按到达顺序录入一段历史</h2><p>数组下标即输入位：begin / write / commit / abort / checkpoint。写入只允许初始账本已有键；事务ID不可复用。检查点要求没有活动事务。</p><p>同一键的写锁由活动事务持有。冲突的写入不撤销，而是等待；等待事务之后的操作按到达顺序顺延，无关事务照常执行。只有等待环才撤销环里的一笔：先回滚再放锁，被卡住的写入接着做。</p><p class="big-note">先等到锁，才写日志。</p><details><summary>查看示例并发操作顺序</summary><pre>${esc(JSON.stringify(SAMPLE,null,2))}</pre></details></div>${session.role!=='viewer'?`<form id="log-form"><label>日志集名<input name="name" value="shift-20261120" required></label><label>说明<input name="description" value="交班并发写入与断电恢复"></label><label>初始账本 JSON<textarea name="initial" rows="2" required>${esc(JSON.stringify(sample?.initial||{sample_A:100,sample_B:100,sample_C:50}))}</textarea></label><label>操作顺序 JSON 数组<textarea name="records" rows="12" required>${esc(JSON.stringify(sample?.records||SAMPLE,null,2))}</textarea></label><button type="submit">校验并保存日志集</button></form>`:'<p class="readonly">观察员仅查看，新增需要操作员或管理员。</p>'}</section>`;}
export function bindLogs(render){action(document.querySelector('#log-form'),async f=>{const d=formObject(f);d.initial=JSON.parse(d.initial);d.records=JSON.parse(d.records);await logSetsAPI.create(d);await render();});}
