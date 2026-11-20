import {table} from '../components/common/table.js';
import {badge} from '../components/common/status.js';
import {esc} from '../utils/html.js';
import {download} from '../api/client.js';
import {message} from '../hooks/action.js';
import {WAIT_OUTCOMES,ABORT_KINDS,DEFER_STATUSES,TRACE_PHASES,UNPERSISTED_STATES} from '../constants/enums.js';
const phase=t=>TRACE_PHASES[t.phase]||t.phase;
const move=t=>t.phase==='undo_loser'||t.phase==='redo_abort_undo'?`${t.after} → ${t.before}`:`${t.before} → ${t.after}`;
function waitsBlock(r){
 const waits=r.waits||[];
 const aborts=r.aborts||[];
 const deferred=r.deferred_ops||[];
 const skipped=r.skipped_ops||[];
 if(!waits.length&&!aborts.length&&!deferred.length&&!skipped.length)return '<p class="notice">本次输入没有等待，也没有撤销。所有写入都直接拿到锁。</p>';
 return `<h2>等待与撤销</h2>${table(['等待事务','请求键','当前持有者','请求原操作位','结果','放行执行序'],waits.map(w=>[esc(w.tx),esc(w.key),esc(w.holder),w.request_lsn,esc(WAIT_OUTCOMES[w.outcome]||w.outcome),w.grant_lsn??'—']))}
 ${aborts.length?`<h2>撤销原因</h2><div class="reasons">${aborts.map(a=>`<div class="reason"><b>${esc(a.tx)} · ${esc(ABORT_KINDS[a.kind]||a.kind)}</b><p>${esc(a.reason)}</p><p class="mini">回滚写入：${a.undone.length?esc(a.undone.map(u=>`#${u.exec_seq} ${u.key}`).join('，')):'无'} · 释放锁：${a.released.length?esc(a.released.join('，')):'无'} · 被丢弃顺延操作：${a.dropped_ops.length?esc(a.dropped_ops.join('，')):'无'}</p></div>`).join('')}</div>`:''}
 ${deferred.length?`<h2>顺延操作</h2>${table(['原操作位','事务','操作','键','延后结果','放行执行序'],deferred.map(d=>[d.pos,esc(d.tx),esc(d.op),esc(d.key??'—'),esc(DEFER_STATUSES[d.status]||d.status),d.exec_seq??'—']))}`:''}
 ${skipped.length?`<h2>撤销后不再执行</h2>${table(['原操作位','事务','操作','原因'],skipped.map(s=>[s.pos,esc(s.tx),esc(s.op),esc(s.reason)]))}`:''}`;
}
function crashBlock(r){
 const writes=r.unpersisted_writes||[];
 const active=r.crash_active||[];
 return `<h2>断电时仍未结束</h2>${table(['事务','状态','已持锁','已写日志条数','等待目标','顺延中的操作'],active.map(a=>[esc(a.tx),esc(a.status==='blocked'?'等待锁':'执行中'),esc((a.holding||[]).join('，')||'无'),a.logged_writes,a.waiting_for?`${esc(a.waiting_for.key)} / ${esc(a.waiting_for.holder)}`:'无',(a.pending||[]).length]))}
 <h2>没拿到锁的写入</h2>${writes.length?table(['原操作位','事务','键','值','状态'],writes.map(u=>[u.pos,esc(u.tx),esc(u.key),u.value,esc(UNPERSISTED_STATES[u.state]||u.state)])):'<p class="notice">断电前所有写入都已拿到锁并写入日志。</p>'}`;
}
export function detailPage(job){
 if(!job)return '<section class="empty"><h2>还没有恢复记录</h2><p>先选择故障场景并提交恢复任务，再逐条查看等待、撤销与重做。</p><a href="#runs">进入恢复队列 →</a></section>';
 const r=job.result;
 return `<div class="section-title"><h2>${esc(job.label)} <small>#${job.id}</small></h2>${badge(job.status)}</div>${r?`${r.error?`<p class="notice error">${esc(r.error)}</p>`:`<section class="verdict"><div><p>恢复账本核对</p><h2>${r.invariant_passed?'提交结果一致':'恢复失败'}</h2></div><p>检查点LSN ${r.checkpoint_lsn??'—'}（原操作位 ${r.checkpoint_pos??'—'}）· 断电点 ${r.crash_after??'—'}<br>重做 ${r.redo_count??0} 次 / 撤销记录回放 ${r.abort_undo_count??0} 次 / 断电撤销 ${r.undo_count??0} 次<br>提交（全量）${esc((r.committed||[]).join('，')||'无')} · 撤销（全量）${esc((r.aborted||[]).join('，')||'无')}<br>检查点后提交 ${esc((r.committed_after_checkpoint||[]).join('，')||'无')} · 检查点后撤销 ${esc((r.aborted_after_checkpoint||[]).join('，')||'无')}<br>断电时未结束 ${esc((r.losers||[]).join('，')||'无')} · 等待环撤销 ${r.deadlock_count??0} 次</p></section>${table(['账本键','检查点值','重做后','恢复后','独立提交结果'],Object.keys(r.recovered||{}).map(k=>[esc(k),r.durable_checkpoint[k],r.after_redo[k],r.recovered[k],r.oracle[k]]))}
 <h2>日志恢复轨迹</h2><p class="mini">执行序是真正写入日志的次序；原操作位是录入时的下标。等待后补做的写入，执行序会晚于原操作位。</p>${table(['执行序','原操作位','事务','阶段','键','before → after'],(r.trace||[]).map(t=>[t.lsn,t.pos,esc(t.tx),esc(phase(t)),esc(t.key),move(t)]))}
 ${waitsBlock(r)}
 ${crashBlock(r)}
 <h2>已提交读数</h2><p>恢复账本 ${esc(JSON.stringify(r.recovered))}；独立按已提交事务重算 ${esc(JSON.stringify(r.oracle))}，两者${r.invariant_passed?'一致':'不一致'}。未提交与已撤销的值均已退净。</p>
 <p class="checksum">输入摘要 ${esc(r.input_sha256||'无')}<br>结果摘要 ${esc(r.result_sha256||'无')}<br>模型 ${esc(r.model_version||'')}</p>`}<div class="toolbar"><button id="export">导出日志与恢复证据</button><button class="secondary" id="refresh">刷新状态</button></div><details><summary>查看冻结输入</summary><pre>${esc(JSON.stringify(job.snapshot,null,2))}</pre></details>`: '<p class="notice">后台恢复计算中，请刷新状态。</p>'}
`;
}
export function bindDetail(job,render){document.querySelector('#refresh')?.addEventListener('click',render);document.querySelector('#export')?.addEventListener('click',()=>download(job.id).catch(e=>message(e.message,true)));}
