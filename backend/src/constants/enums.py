ROLES = ('admin', 'operator', 'viewer')
DRILL_STATUSES = ('queued', 'running', 'completed', 'failed')

# Shared vocabulary of the concurrent WAL model.  The frontend mirrors these
# identifiers in frontend/src/constants/enums.js with Chinese labels.
WAIT_OUTCOMES = ('waiting', 'granted', 'deadlock_victim', 'stale', 'unresolved')
ABORT_KINDS = ('deadlock', 'explicit')
DEFER_STATUSES = ('queued', 'blocked', 'executed', 'dropped')
TRACE_PHASES = ('redo', 'redo_abort_undo', 'undo_loser')
