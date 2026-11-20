"""Demo transactions shared by the seeder and the engine tests."""

RECORDS=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'sample_A','value':75},{'op':'commit','tx':'T1'},{'op':'checkpoint'},{'op':'begin','tx':'T2'},{'op':'write','tx':'T2','key':'sample_B','value':40},{'op':'begin','tx':'T3'},{'op':'write','tx':'T3','key':'sample_A','value':60},{'op':'commit','tx':'T3'},{'op':'write','tx':'T2','key':'sample_B','value':35},{'op':'commit','tx':'T2'}]
# Arrival order that exercises the concurrency rules:
#  * T2's write at 7 waits for T1's sample_A; its write at 8 is deferred.
#  * T3 is unrelated and keeps working while T2 waits.
#  * At 16 T5's request closes the cycle T5 -> T4 -> T5.  T5 is rolled back
#    before its lock is released, then T4's blocked write at 15 continues.
#  * T5's commit at 18 arrives after its abort and is skipped.
CONCURRENT=[
    {'op':'begin','tx':'T1'},
    {'op':'begin','tx':'T2'},
    {'op':'write','tx':'T1','key':'sample_A','value':80},
    {'op':'begin','tx':'T3'},
    {'op':'write','tx':'T3','key':'sample_C','value':45},
    {'op':'commit','tx':'T3'},
    {'op':'write','tx':'T2','key':'sample_A','value':90},
    {'op':'write','tx':'T2','key':'sample_B','value':95},
    {'op':'commit','tx':'T1'},
    {'op':'commit','tx':'T2'},
    {'op':'begin','tx':'T4'},
    {'op':'begin','tx':'T5'},
    {'op':'write','tx':'T4','key':'sample_B','value':70},
    {'op':'write','tx':'T5','key':'sample_C','value':60},
    {'op':'write','tx':'T4','key':'sample_C','value':40},
    {'op':'write','tx':'T5','key':'sample_B','value':80},
    {'op':'commit','tx':'T4'},
    {'op':'commit','tx':'T5'},
]
