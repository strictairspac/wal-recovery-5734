import unittest

from src.services.wal_engine import recover, validate

BASE = {'sample_A': 100, 'sample_B': 50}
SEED = [
    {'op': 'begin', 'tx': 'T1'},
    {'op': 'write', 'tx': 'T1', 'key': 'sample_A', 'value': 75},
    {'op': 'commit', 'tx': 'T1'},
    {'op': 'checkpoint'},
    {'op': 'begin', 'tx': 'T2'},
    {'op': 'write', 'tx': 'T2', 'key': 'sample_B', 'value': 40},
    {'op': 'begin', 'tx': 'T3'},
    {'op': 'write', 'tx': 'T3', 'key': 'sample_A', 'value': 60},
    {'op': 'commit', 'tx': 'T3'},
    {'op': 'write', 'tx': 'T2', 'key': 'sample_B', 'value': 35},
    {'op': 'commit', 'tx': 'T2'},
]

WAIT = [
    {'op': 'begin', 'tx': 'T1'},
    {'op': 'begin', 'tx': 'T2'},
    {'op': 'write', 'tx': 'T1', 'key': 'sample_A', 'value': 10},
    {'op': 'write', 'tx': 'T2', 'key': 'sample_A', 'value': 20},
    {'op': 'commit', 'tx': 'T1'},
    {'op': 'write', 'tx': 'T2', 'key': 'sample_B', 'value': 30},
    {'op': 'commit', 'tx': 'T2'},
]

DEADLOCK = [
    {'op': 'begin', 'tx': 'T1'},
    {'op': 'begin', 'tx': 'T2'},
    {'op': 'write', 'tx': 'T1', 'key': 'sample_A', 'value': 10},
    {'op': 'write', 'tx': 'T2', 'key': 'sample_B', 'value': 20},
    {'op': 'write', 'tx': 'T1', 'key': 'sample_B', 'value': 11},
    {'op': 'write', 'tx': 'T2', 'key': 'sample_A', 'value': 21},
    {'op': 'commit', 'tx': 'T1'},
    {'op': 'commit', 'tx': 'T2'},
]


class SeedRegression(unittest.TestCase):
    def test_checkpoint_recovery_is_unchanged(self):
        result = recover(BASE, SEED, 10)
        self.assertEqual(result['recovered'], {'sample_A': 60, 'sample_B': 50})
        self.assertEqual(result['losers'], ['T2'])
        self.assertEqual(result['undo_count'], 2)
        self.assertTrue(result['invariant_passed'])
        self.assertEqual(result['checkpoint_lsn'], 4)

    def test_no_crash_keeps_every_commit(self):
        result = recover(BASE, SEED, 11)
        self.assertEqual(result['recovered'], {'sample_A': 60, 'sample_B': 35})
        self.assertEqual(result['committed'], ['T1', 'T3', 'T2'])


class Waiting(unittest.TestCase):
    def test_conflicting_write_waits_then_continues(self):
        self.assertTrue(validate({'sample_A': 100, 'sample_B': 100}, WAIT))
        result = recover({'sample_A': 100, 'sample_B': 100}, WAIT, 7)
        self.assertEqual(result['recovered'], {'sample_A': 20, 'sample_B': 30})
        self.assertEqual([w['outcome'] for w in result['waits']], ['granted'])
        self.assertEqual(result['deadlock_count'], 0)

    def test_later_ops_of_waiter_are_deferred_in_order(self):
        # T2 blocks on sample_A at position 4; its write of sample_B at 6 is
        # deferred until T1 commits, then both run in arrival order.
        result = recover({'sample_A': 100, 'sample_B': 100}, WAIT, 4)
        self.assertEqual([(e['pos'], e['status']) for e in result['deferred_ops']],
                         [(4, 'blocked')])
        self.assertEqual(result['losers'], ['T1', 'T2'])
        self.assertEqual(result['unpersisted_writes'],
                         [{'pos': 4, 'tx': 'T2', 'key': 'sample_A',
                           'value': 20, 'state': 'blocked_no_wal'}])
        full = recover({'sample_A': 100, 'sample_B': 100}, WAIT, 7)
        self.assertEqual([(e['pos'], e['status']) for e in full['deferred_ops']],
                         [(4, 'executed')])
        self.assertEqual(full['deferred_ops'][0]['exec_seq'],
                         full['waits'][0]['grant_lsn'])

    def test_blocked_write_has_no_wal_before_lock(self):
        result = recover({'sample_A': 100, 'sample_B': 100}, WAIT, 4)
        self.assertIn(4, [item['pos'] for item in result['unpersisted_writes']])
        self.assertEqual(result['redo_count'], 1)
        self.assertEqual(result['trace'][0]['pos'], 3)

    def test_abort_while_waiting_cancels_without_lock(self):
        records = [
            {'op': 'begin', 'tx': 'T1'},
            {'op': 'begin', 'tx': 'T2'},
            {'op': 'write', 'tx': 'T1', 'key': 'sample_A', 'value': 5},
            {'op': 'write', 'tx': 'T2', 'key': 'sample_A', 'value': 6},
            {'op': 'write', 'tx': 'T2', 'key': 'sample_B', 'value': 7},
            {'op': 'abort', 'tx': 'T2'},
            {'op': 'commit', 'tx': 'T1'},
        ]
        result = recover({'sample_A': 100, 'sample_B': 100}, records, 7)
        self.assertEqual(result['recovered'], {'sample_A': 5, 'sample_B': 100})
        self.assertEqual(result['aborted'], ['T2'])
        self.assertEqual(result['waits'][0]['outcome'], 'stale')
        self.assertEqual([event['status'] for event in result['deferred_ops']],
                         ['dropped', 'dropped'])
        self.assertTrue(result['invariant_passed'])

    def test_transaction_after_commit_cannot_queue(self):
        records = WAIT + [{'op': 'write', 'tx': 'T2', 'key': 'sample_B',
                           'value': 1}]
        with self.assertRaises(ValueError):
            validate({'sample_A': 1, 'sample_B': 2}, records)

    def test_unrelated_transaction_keeps_running_while_waiting(self):
        records = [
            {'op': 'begin', 'tx': 'T1'},
            {'op': 'begin', 'tx': 'T2'},
            {'op': 'begin', 'tx': 'T3'},
            {'op': 'write', 'tx': 'T1', 'key': 'sample_A', 'value': 1},
            {'op': 'write', 'tx': 'T2', 'key': 'sample_A', 'value': 2},
            {'op': 'write', 'tx': 'T3', 'key': 'sample_B', 'value': 3},
            {'op': 'commit', 'tx': 'T3'},
            {'op': 'commit', 'tx': 'T1'},
        ]
        result = recover({'sample_A': 0, 'sample_B': 0}, records, 7)
        self.assertEqual(result['committed'], ['T3'])
        self.assertEqual(result['recovered'], {'sample_A': 0, 'sample_B': 3})
        self.assertEqual(result['waits'][0]['outcome'], 'unresolved')


class Deadlock(unittest.TestCase):
    def test_one_victim_is_rolled_back_before_locks_release(self):
        self.assertTrue(validate({'sample_A': 100, 'sample_B': 100}, DEADLOCK))
        result = recover({'sample_A': 100, 'sample_B': 100}, DEADLOCK, 8)
        self.assertEqual(result['deadlock_victims'], ['T2'])
        abort = result['aborts'][0]
        self.assertEqual(abort['tx'], 'T2')
        self.assertEqual(abort['cycle'], ['T2', 'T1', 'T2'])
        self.assertEqual(abort['released'], ['sample_B'])
        self.assertEqual([row['key'] for row in abort['undone']], ['sample_B'])
        self.assertIn('等待环', abort['reason'])
        self.assertEqual(result['recovered'], {'sample_A': 10, 'sample_B': 11})
        self.assertEqual(result['committed'], ['T1'])
        self.assertTrue(result['invariant_passed'])

    def test_winner_after_deadlock_waits_for_release(self):
        result = recover({'sample_A': 100, 'sample_B': 100}, DEADLOCK, 6)
        self.assertEqual(result['deadlock_victims'], ['T2'])
        outcomes = [(w['tx'], w['outcome']) for w in result['waits']]
        self.assertIn(('T2', 'deadlock_victim'), outcomes)
        self.assertIn(('T1', 'granted'), outcomes)
        # At crash time T1's write on sample_B is durable, T2's undo is durable.
        self.assertEqual(result['recovered'], {'sample_A': 100, 'sample_B': 100})
        self.assertEqual(result['aborted'], ['T2'])
        self.assertEqual(result['losers'], ['T1'])
        self.assertTrue(result['invariant_passed'])

    def test_ops_after_deadlock_victim_are_skipped(self):
        result = recover({'sample_A': 100, 'sample_B': 100}, DEADLOCK, 8)
        self.assertEqual([(item['pos'], item['tx'], item['op'])
                          for item in result['skipped_ops']],
                         [(8, 'T2', 'commit')])
        self.assertEqual(result['recovered'], {'sample_A': 10, 'sample_B': 11})

    def test_deadlock_needs_a_cycle_not_one_way_wait(self):
        records = [
            {'op': 'begin', 'tx': 'T1'},
            {'op': 'begin', 'tx': 'T2'},
            {'op': 'write', 'tx': 'T1', 'key': 'sample_A', 'value': 1},
            {'op': 'write', 'tx': 'T2', 'key': 'sample_A', 'value': 2},
            {'op': 'commit', 'tx': 'T1'},
            {'op': 'commit', 'tx': 'T2'},
        ]
        result = recover({'sample_A': 100, 'sample_B': 100}, records, 6)
        self.assertEqual(result['deadlock_count'], 0)
        self.assertEqual(result['recovered']['sample_A'], 2)


class CrashBoundaries(unittest.TestCase):
    def test_crash_before_resolution_keeps_wait_visible(self):
        result = recover({'sample_A': 100, 'sample_B': 100}, DEADLOCK, 5)
        self.assertEqual(result['waits'][0]['outcome'], 'unresolved')
        self.assertEqual(result['crash_active'][0]['waiting_for']['holder'], 'T2')
        self.assertEqual(result['unpersisted_writes'][0]['pos'], 5)
        self.assertEqual(result['recovered'], {'sample_A': 100, 'sample_B': 100})

    def test_crash_at_zero_returns_initial(self):
        result = recover(BASE, SEED, 0)
        self.assertEqual(result['recovered'], BASE)
        self.assertEqual(result['trace'], [])
        self.assertTrue(result['invariant_passed'])

    def test_explicit_abort_is_redone_from_wal(self):
        records = [
            {'op': 'begin', 'tx': 'T1'},
            {'op': 'write', 'tx': 'T1', 'key': 'sample_A', 'value': 7},
            {'op': 'abort', 'tx': 'T1'},
        ]
        result = recover({'sample_A': 1, 'sample_B': 2}, records, 3)
        self.assertEqual(result['recovered'], {'sample_A': 1, 'sample_B': 2})
        self.assertEqual(result['abort_undo_count'], 1)
        self.assertEqual(result['aborted'], ['T1'])


class Validation(unittest.TestCase):
    def assertRejected(self, initial, records, fragment):
        with self.assertRaises(ValueError) as ctx:
            validate(initial, records)
        self.assertIn(fragment, str(ctx.exception))

    def test_checkpoint_blocks_while_a_transaction_waits(self):
        records = WAIT[:5] + [{'op': 'checkpoint'}]
        self.assertRejected({'sample_A': 1, 'sample_B': 2}, records, '静止检查点')

    def test_unknown_key_rejected(self):
        records = [{'op': 'begin', 'tx': 'T1'},
                   {'op': 'write', 'tx': 'T1', 'key': 'nope', 'value': 1},
                   {'op': 'commit', 'tx': 'T1'}]
        self.assertRejected({'sample_A': 1}, records, '初始账本')

    def test_duplicate_transaction_rejected(self):
        records = [{'op': 'begin', 'tx': 'T1'}, {'op': 'commit', 'tx': 'T1'},
                   {'op': 'begin', 'tx': 'T1'}]
        self.assertRejected({'sample_A': 1}, records, '不得重复')

    def test_operation_after_commit_rejected(self):
        records = WAIT + [{'op': 'write', 'tx': 'T2', 'key': 'sample_B',
                           'value': 1}]
        self.assertRejected({'sample_A': 1, 'sample_B': 2}, records, '已结束')

    def test_empty_inputs_rejected(self):
        self.assertRejected({}, SEED, '1—30')
        self.assertRejected(BASE, [], '1—30')

    def test_crash_point_out_of_range(self):
        with self.assertRaises(ValueError):
            recover(BASE, SEED, 12)
        with self.assertRaises(ValueError):
            recover(BASE, SEED, -1)

    def test_value_and_key_limits(self):
        records = [{'op': 'begin', 'tx': 'T1'},
                   {'op': 'write', 'tx': 'T1', 'key': 'sample_A',
                    'value': 1000000001}]
        self.assertRejected({'sample_A': 1}, records, '超限')
        self.assertRejected({'sample_A': 1.5}, SEED, '整数')


if __name__ == '__main__':
    unittest.main()
