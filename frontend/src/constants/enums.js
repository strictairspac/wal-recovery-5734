export const ROLES = {admin:'管理员',operator:'操作员',viewer:'观察员'};
export const DRILL_STATUSES = {queued:'等待执行',running:'执行中',completed:'已完成',failed:'失败'};
export const WAIT_OUTCOMES = {waiting:'等待中',granted:'已放行',deadlock_victim:'等待环撤销',stale:'已失效',unresolved:'断电时仍在等待'};
export const ABORT_KINDS = {deadlock:'等待环撤销',explicit:'显式撤销'};
export const DEFER_STATUSES = {queued:'已顺延',blocked:'等待中',executed:'已补执行',dropped:'随撤销丢弃'};
export const TRACE_PHASES = {redo:'重做',redo_abort_undo:'重做撤销记录',undo_loser:'崩溃时撤销'};
export const UNPERSISTED_STATES = {blocked_no_wal:'等锁中，未写日志',deferred_no_wal:'顺延中，未写日志'};
