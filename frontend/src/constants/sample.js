// Example arrival order: T2 waits on T1, an unrelated T3 commits, then T1
// and T2 resolve. Mirrors the engine unit tests.
export const SAMPLE=[
 {op:'begin',tx:'T1'},
 {op:'begin',tx:'T2'},
 {op:'write',tx:'T1',key:'sample_A',value:80},
 {op:'begin',tx:'T3'},
 {op:'write',tx:'T3',key:'sample_C',value:45},
 {op:'commit',tx:'T3'},
 {op:'write',tx:'T2',key:'sample_A',value:90},
 {op:'write',tx:'T2',key:'sample_B',value:95},
 {op:'commit',tx:'T1'},
 {op:'commit',tx:'T2'}
];
