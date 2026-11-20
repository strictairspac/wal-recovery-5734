"""Concurrent WAL teaching model.

A log set is an *arrival order* of transaction operations.  Every transaction
takes strict write locks that are held until commit/abort.  A write that hits a
key held by another transaction does **not** fail: it waits.  While a
transaction waits, its later operations are deferred in arrival order, while
unrelated transactions keep executing.  Only a wait-for cycle aborts a
transaction: the victim's writes are rolled back first, then its locks are
released, and the writes it blocked can continue.

A crash happens at an original input position (``crash_after``).  Operations
still waiting at that moment never produced a WAL record, so the write never
reached disk.  Recovery replays the durable WAL prefix (redo, including the
undo records of aborts that had already executed), then undoes every
transaction that was still unfinished at the crash point.  An independent
winner-only oracle is computed from the last quiescent checkpoint.
"""
import copy
import hashlib
import json
import re
from collections import deque

from src.constants.enums import ABORT_KINDS, DEFER_STATUSES, WAIT_OUTCOMES

OPS = ('begin', 'write', 'commit', 'abort', 'checkpoint')
TX_RE = re.compile(r'[A-Za-z0-9_-]{1,30}')
KEY_RE = re.compile(r'[A-Za-z0-9_-]{1,50}')
MAX_ABS = 1000000000
MAX_LOGS = 200
MAX_KEYS = 30


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_initial(initial):
    if not isinstance(initial, dict) or not initial or len(initial) > MAX_KEYS:
        raise ValueError('需1—30个键及1—200条日志')
    for key, value in initial.items():
        if (not isinstance(key, str) or not KEY_RE.fullmatch(key)
                or not _is_int(value) or abs(value) > MAX_ABS):
            raise ValueError('初始键为字母数字下划线短横线，值为绝对值不超过10亿的整数')


def _check_record(pos, record):
    if not isinstance(record, dict) or record.get('op') not in OPS:
        raise ValueError(f'输入位 {pos}: 未知操作')
    op = record['op']
    if op == 'checkpoint':
        return
    tx = record.get('tx')
    if not isinstance(tx, str) or not TX_RE.fullmatch(tx):
        raise ValueError(f'输入位 {pos}: 需要事务标识')
    if op == 'write':
        key = record.get('key')
        value = record.get('value')
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise ValueError(f'输入位 {pos}: 写入键格式无效')
        if not _is_int(value) or abs(value) > MAX_ABS:
            raise ValueError(f'输入位 {pos}: 数值超限')


def _check_records(initial, records):
    if not isinstance(records, list):
        raise ValueError('操作顺序必须是数组')
    for pos, record in enumerate(records, 1):
        _check_record(pos, record)
        if record['op'] == 'write' and record.get('key') not in initial:
            raise ValueError(f'输入位 {pos}: 写入键必须存在于初始账本')


def _row(row, **extra):
    out = dict(row)
    out['lsn'] = row['exec_seq']
    out['pos'] = row['orig_pos']
    out.update(extra)
    return out


class _Simulation:
    """Execute an arrival-ordered schedule up to the crash position."""

    def __init__(self, initial, records, stop_after):
        self.initial = copy.deepcopy(initial)
        self.state = copy.deepcopy(initial)
        self.records = records
        self.stop_after = stop_after
        self.locks = {}
        self.txs = {}
        self.used = set()
        self.auto_aborted = set()
        self.wal = []
        self.waits = []
        self.aborts = []
        self.deferred = []
        self.skipped = []
        self.exec_seq = 0
        self.wait_seq = 0
        self.tick = 0
        self.checkpoint_lsn = 0
        self.checkpoint_pos = 0
        self.durable = copy.deepcopy(initial)

    # ---- bookkeeping -------------------------------------------------
    def _next_exec(self):
        self.exec_seq += 1
        return self.exec_seq

    def _mark_deferred(self, event, status, seq=None):
        if not event:
            return
        event['status'] = status
        if status == 'executed':
            event['executed_tick'] = self.tick
            event['exec_seq'] = seq
        elif status == 'blocked':
            event['blocked_tick'] = self.tick
        elif status == 'dropped':
            event['dropped_tick'] = self.tick

    def _defer_event(self, pos, tx, op, key, value):
        event = {'pos': pos, 'tx': tx, 'op': op, 'key': key, 'value': value,
                 'queued_tick': self.tick, 'executed_tick': None,
                 'exec_seq': None, 'status': 'queued'}
        self.deferred.append(event)
        return event

    # ---- public ------------------------------------------------------
    def run(self):
        for pos, record in enumerate(self.records, 1):
            if pos > self.stop_after:
                break
            self.tick = pos
            self._accept(pos, record)
            self._wake_all()
        for wait in self.waits:
            if wait['outcome'] == 'waiting':
                wait['outcome'] = 'unresolved'
        return self

    # ---- arrival -----------------------------------------------------
    def _accept(self, pos, record):
        op = record['op']
        tx = record.get('tx')
        if op == 'checkpoint':
            self._checkpoint(pos)
            return
        if op == 'begin':
            if tx in self.used:
                raise ValueError(f'输入位 {pos}: 事务标识不得重复')
            self.used.add(tx)
            self.txs[tx] = {
                'id': tx, 'status': 'running', 'begin_pos': pos,
                'writes': [], 'pending': deque(), 'blocked': None,
                'blocked_holder': None, 'wait': None, 'holding': set(),
            }
            seq = self._next_exec()
            self.wal.append({'type': 'begin', 'exec_seq': seq, 'orig_pos': pos,
                             'exec_tick': self.tick, 'tx': tx})
            return
        if tx in self.auto_aborted:
            self.skipped.append({
                'pos': pos, 'tick': self.tick, 'tx': tx, 'op': op,
                'key': record.get('key'), 'value': record.get('value'),
                'reason': '事务已因等待环被撤销，之后的操作不再执行',
            })
            return
        target = self.txs.get(tx)
        if target is None or target['status'] in ('committed', 'aborted'):
            raise ValueError(f'输入位 {pos}: 事务未开启或已结束')
        if target['status'] == 'blocked' and op == 'abort':
            # An abort is a cancellation: it does not wait for the lock.
            self._cancel_waits(target)
            self._explicit_abort(target, pos, None)
            return
        if target['status'] == 'blocked':
            earlier = [item['op'] for item in target['pending']
                       if item['op'] in ('commit', 'abort')]
            if earlier:
                raise ValueError(f'输入位 {pos}: 事务已排在 {earlier[0]} 之后，不能再执行操作')
            item = {'pos': pos, 'op': op, 'key': record.get('key'),
                    'value': record.get('value')}
            item['deferred'] = self._defer_event(pos, tx, op, record.get('key'),
                                                 record.get('value'))
            target['pending'].append(item)
            return
        self._execute(pos, op, record.get('key'), record.get('value'), target)

    # ---- execution ---------------------------------------------------
    def _execute(self, pos, op, key, value, target, deferred_event=None):
        tx = target['id']
        if op == 'write':
            holder = self.locks.get(key)
            if holder is not None and holder != tx:
                event = deferred_event or self._defer_event(pos, tx, op, key, value)
                self._block(target, pos, key, value, holder, event)
                return
            self._apply_write(pos, tx, key, value, deferred_event)
        elif op == 'commit':
            seq = self._next_exec()
            released = self._release(target)
            target['status'] = 'committed'
            self.wal.append({'type': 'commit', 'exec_seq': seq, 'orig_pos': pos,
                             'exec_tick': self.tick, 'tx': tx, 'released': released})
            self._mark_deferred(deferred_event, 'executed', seq)
        elif op == 'abort':
            self._explicit_abort(target, pos, deferred_event)
        else:  # pragma: no cover - _check_record already rejects this
            raise ValueError(f'输入位 {pos}: 未知操作')

    def _apply_write(self, pos, tx, key, value, deferred_event=None):
        holder = self.locks.get(key)
        if holder is not None and holder != tx:
            raise RuntimeError('internal: write applied while lock is held')
        target = self.txs[tx]
        self.locks[key] = tx
        target['holding'].add(key)
        before = self.state[key]
        self.state[key] = value
        seq = self._next_exec()
        row = {'exec_seq': seq, 'orig_pos': pos, 'exec_tick': self.tick,
               'tx': tx, 'key': key, 'before': before, 'after': value}
        target['writes'].append(row)
        self.wal.append(dict(row, type='write'))
        self._mark_deferred(deferred_event, 'executed', seq)
        return row

    # ---- waits / deadlock --------------------------------------------
    def _block(self, target, pos, key, value, holder, deferred_event=None):
        tx = target['id']
        target['status'] = 'blocked'
        target['blocked'] = {'pos': pos, 'op': 'write', 'key': key, 'value': value,
                             'deferred': deferred_event}
        target['blocked_holder'] = holder
        self.wait_seq += 1
        wait = {'id': self.wait_seq, 'tx': tx, 'key': key, 'holder': holder,
                'request_lsn': pos, 'request_tick': self.tick,
                'wait_seq': self.wait_seq, 'outcome': 'waiting',
                'grant_tick': None, 'grant_lsn': None}
        target['wait'] = wait
        self.waits.append(wait)
        self._mark_deferred(deferred_event, 'blocked')
        cycle = self._find_cycle(tx)
        if cycle:
            self._deadlock_abort(target, cycle, wait, pos, key, holder)

    def _find_cycle(self, start):
        path = [start]
        seen = {start}
        current = start
        while True:
            target = self.txs.get(current)
            if target is None or target['status'] != 'blocked':
                return None
            nxt = target.get('blocked_holder')
            if nxt is None:
                return None
            path.append(nxt)
            if nxt == start:
                return path
            if nxt in seen:
                return None
            seen.add(nxt)
            current = nxt

    def _deadlock_abort(self, target, cycle, wait, pos, key, holder):
        tx = target['id']
        undone = self._rollback(target)
        released = self._release(target)
        target['status'] = 'auto_aborted'
        self.auto_aborted.add(tx)
        target['blocked'] = None
        target['blocked_holder'] = None
        target['wait'] = None
        for item in target['pending']:
            self._mark_deferred(item.get('deferred'), 'dropped')
        dropped = [item['pos'] for item in target['pending']]
        target['pending'].clear()
        wait['outcome'] = 'deadlock_victim'
        chain = ' → '.join(cycle)
        reason = (f'检测到等待环 {chain}：{tx} 在输入位 {pos} 请求 {key} 时等待 {holder}，'
                  f'形成环；先回滚 {len(undone)} 条写入再释放锁 '
                  f'{released or "无"}，被阻塞的写入随后继续。')
        seq = self._next_exec()
        detail = {'tx': tx, 'kind': ABORT_KINDS[0], 'reason': reason,
                  'request_lsn': pos, 'tick': self.tick, 'cycle': cycle,
                  'undone': undone, 'released': released,
                  'dropped_ops': dropped, 'exec_seq': seq}
        self.aborts.append(detail)
        self.wal.append({'type': 'abort', 'exec_seq': seq, 'orig_pos': pos,
                         'exec_tick': self.tick, 'tx': tx, 'undo': undone,
                         'cycle': cycle, 'kind': ABORT_KINDS[0]})

    # ---- commit / abort ----------------------------------------------
    def _explicit_abort(self, target, pos, deferred_event=None):
        tx = target['id']
        undone = self._rollback(target)
        released = self._release(target)
        target['status'] = 'aborted'
        seq = self._next_exec()
        self.wal.append({'type': 'abort', 'exec_seq': seq, 'orig_pos': pos,
                         'exec_tick': self.tick, 'tx': tx, 'undo': undone,
                         'cycle': [], 'kind': ABORT_KINDS[1]})
        self.aborts.append({'tx': tx, 'kind': ABORT_KINDS[1],
                            'reason': (f'事务在输入位 {pos} 显式撤销，先回滚 {len(undone)} 条'
                                       f'写入再释放锁 {released or "无"}。'),
                            'request_lsn': pos, 'tick': self.tick, 'cycle': [],
                            'undone': undone, 'released': released,
                            'dropped_ops': [], 'exec_seq': seq})
        self._mark_deferred(deferred_event, 'executed', seq)

    def _cancel_waits(self, target):
        head = target.get('blocked')
        if head:
            self._mark_deferred(head.get('deferred'), 'dropped')
        for item in target['pending']:
            self._mark_deferred(item.get('deferred'), 'dropped')
        target['pending'].clear()
        target['blocked'] = None
        target['blocked_holder'] = None
        if target.get('wait'):
            target['wait']['outcome'] = 'stale'
        target['wait'] = None

    def _rollback(self, target):
        undone = []
        for row in reversed(target['writes']):
            self.state[row['key']] = row['before']
            undone.append({'exec_seq': row['exec_seq'], 'orig_pos': row['orig_pos'],
                           'tx': row['tx'], 'key': row['key'],
                           'before': row['before'], 'after': row['after']})
        target['writes'] = []
        return undone

    def _release(self, target):
        keys = sorted(target['holding'])
        for key in keys:
            if self.locks.get(key) == target['id']:
                del self.locks[key]
        target['holding'].clear()
        return keys

    # ---- wakeups -----------------------------------------------------
    def _wake_all(self):
        while True:
            candidate = None
            for wait in self.waits:
                if wait['outcome'] != 'waiting':
                    continue
                if self.locks.get(wait['key']) is None:
                    candidate = wait
                    break
            if candidate is None:
                return
            self._grant(candidate)

    def _grant(self, wait):
        target = self.txs.get(wait['tx'])
        if target is None or target['status'] != 'blocked' or not target['blocked']:
            wait['outcome'] = 'stale'
            return
        head = target['blocked']
        target['status'] = 'running'
        target['blocked'] = None
        target['blocked_holder'] = None
        target['wait'] = None
        wait['outcome'] = 'granted'
        wait['grant_tick'] = self.tick
        self._apply_write(head['pos'], target['id'], head['key'], head['value'],
                          head.get('deferred'))
        wait['grant_lsn'] = self.exec_seq
        self._drain(target)

    def _drain(self, target):
        while target['pending'] and target['status'] == 'running':
            item = target['pending'].popleft()
            self._execute(item['pos'], item['op'], item.get('key'),
                          item.get('value'), target, item.get('deferred'))
        if target['pending'] and target['status'] != 'blocked':
            nxt = target['pending'][0]
            self._mark_deferred(nxt.get('deferred'), 'dropped')
            raise ValueError(f'输入位 {nxt["pos"]}: 事务已结束，不能再执行操作')

    # ---- checkpoint --------------------------------------------------
    def _checkpoint(self, pos):
        active = [tx for tx, target in self.txs.items()
                  if target['status'] in ('running', 'blocked')]
        if active:
            raise ValueError(f'输入位 {pos}: 仅支持无活动事务（含等待中）的静止检查点')
        self.durable = copy.deepcopy(self.state)
        self.checkpoint_lsn = self.exec_seq + 1
        self.checkpoint_pos = pos
        seq = self._next_exec()
        self.wal.append({'type': 'checkpoint', 'exec_seq': seq, 'orig_pos': pos,
                         'exec_tick': self.tick, 'image': copy.deepcopy(self.state)})

    # ---- crash view --------------------------------------------------
    def crash_view(self):
        active = []
        unpersisted = []
        for tx, target in sorted(self.txs.items()):
            if target['status'] not in ('running', 'blocked'):
                continue
            wait = target.get('wait')
            item = {'tx': tx, 'status': target['status'],
                    'holding': sorted(target['holding']),
                    'logged_writes': len(target['writes']),
                    'waiting_for': None,
                    'pending': [{'pos': pending['pos'], 'op': pending['op'],
                                 'key': pending.get('key'), 'value': pending.get('value')}
                                for pending in target['pending']]}
            if wait:
                item['waiting_for'] = {'key': wait['key'], 'holder': wait['holder'],
                                       'request_lsn': wait['request_lsn']}
            active.append(item)
            if target['status'] == 'blocked' and target.get('blocked'):
                head = target['blocked']
                unpersisted.append({'pos': head['pos'], 'tx': tx, 'key': head['key'],
                                    'value': head['value'], 'state': 'blocked_no_wal'})
                for pending in target['pending']:
                    if pending['op'] == 'write':
                        unpersisted.append({'pos': pending['pos'], 'tx': tx,
                                            'key': pending.get('key'),
                                            'value': pending.get('value'),
                                            'state': 'deferred_no_wal'})
        unpersisted.sort(key=lambda item: item['pos'])
        return active, unpersisted


def _run(initial, records, stop_after):
    return _Simulation(initial, records, stop_after).run()


def validate(initial, records):
    _check_initial(initial)
    if not isinstance(records, list) or not records or len(records) > MAX_LOGS:
        raise ValueError('需1—30个键及1—200条日志')
    _check_records(initial, records)
    _run(initial, records, len(records))
    return True


def recover(initial, records, crash_after):
    validate(initial, records)
    if not _is_int(crash_after) or not 0 <= crash_after <= len(records):
        raise ValueError('崩溃点超出日志范围')

    sim = _run(initial, records, crash_after)
    state = copy.deepcopy(initial)
    durable = copy.deepcopy(initial)
    live = set()
    live_writes = {}
    writes = []
    winners = []
    aborted = []
    committed_all = []
    aborted_all = []
    checkpoint = 0
    checkpoint_pos = 0
    trace = []

    # Redo: replay the durable WAL prefix in execution (disk) order.
    for event in sim.wal:
        kind = event['type']
        if kind == 'begin':
            live.add(event['tx'])
            live_writes.setdefault(event['tx'], [])
        elif kind == 'write':
            row = {key: event[key] for key in
                   ('exec_seq', 'orig_pos', 'exec_tick', 'tx', 'key', 'before', 'after')}
            state[row['key']] = row['after']
            live.add(row['tx'])
            live_writes.setdefault(row['tx'], []).append(row)
            writes.append(row)
            trace.append(_row(row, phase='redo'))
        elif kind == 'abort':
            for undo in event['undo']:
                row = {key: undo[key] for key in
                       ('exec_seq', 'orig_pos', 'tx', 'key', 'before', 'after')}
                state[row['key']] = row['before']
                trace.append(_row(dict(row, exec_tick=event['exec_tick']),
                                  phase='redo_abort_undo'))
            aborted.append(event['tx'])
            aborted_all.append(event['tx'])
            live.discard(event['tx'])
            live_writes.pop(event['tx'], None)
        elif kind == 'commit':
            winners.append(event['tx'])
            committed_all.append(event['tx'])
            live.discard(event['tx'])
            live_writes.pop(event['tx'], None)
        elif kind == 'checkpoint':
            checkpoint = event['exec_seq']
            checkpoint_pos = event['orig_pos']
            durable = copy.deepcopy(event['image'])
            state = copy.deepcopy(event['image'])
            trace = []
            writes = []
            winners = []
            aborted = []
            live = set()
            live_writes = {}

    # Undo: crash-time unfinished transactions, reverse disk order.
    before_undo = copy.deepcopy(state)
    losers = sorted(live)
    undo_rows = []
    for tx in losers:
        undo_rows.extend(live_writes.get(tx, []))
    undo_rows.sort(key=lambda row: row['exec_seq'], reverse=True)
    for row in undo_rows:
        state[row['key']] = row['before']
        trace.append(_row(row, phase='undo_loser'))

    # Independent winner-only oracle from the durable checkpoint image.
    oracle = copy.deepcopy(durable)
    for row in writes:
        if row['tx'] in winners:
            oracle[row['key']] = row['after']

    crash_active, unpersisted = sim.crash_view()
    checksum = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    source = json.dumps({'initial': initial, 'records': records,
                         'crash_after': crash_after}, sort_keys=True)
    deadlocks = [event for event in sim.aborts if event['kind'] == ABORT_KINDS[0]]
    return {
        'simulation': True,
        'model_version': 'strict-wal-concurrent-v2',
        'crash_after': crash_after,
        'checkpoint_lsn': checkpoint,
        'checkpoint_pos': checkpoint_pos,
        'durable_checkpoint': durable,
        'after_redo': before_undo,
        'recovered': state,
        'oracle': oracle,
        'invariant_passed': state == oracle,
        'committed': committed_all,
        'aborted': aborted_all,
        'committed_after_checkpoint': winners,
        'aborted_after_checkpoint': aborted,
        'losers': losers,
        'redo_count': len([row for row in trace if row['phase'] == 'redo']),
        'undo_count': len(undo_rows),
        'abort_undo_count': len([row for row in trace
                                 if row['phase'] == 'redo_abort_undo']),
        'trace': trace,
        'waits': sim.waits,
        'aborts': sim.aborts,
        'deadlock_count': len(deadlocks),
        'deadlock_victims': [event['tx'] for event in deadlocks],
        'deferred_ops': sim.deferred,
        'skipped_ops': sim.skipped,
        'crash_active': crash_active,
        'unpersisted_writes': unpersisted,
        'wal_events': len(sim.wal),
        'result_sha256': checksum,
        'input_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'boundary': ('Python WAL 教学模型；严格写锁、等待顺延、等待环撤销、静止检查点、'
                     '整数键值、日志前缀持久。没有注入真实 PostgreSQL 崩溃，'
                     '未执行数据库物理恢复。'),
        'vocabulary': {'waits': list(WAIT_OUTCOMES), 'aborts': list(ABORT_KINDS),
                       'deferred': list(DEFER_STATUSES)},
    }
