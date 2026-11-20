/** @typedef {{id:number,name:string,initial:object,records:object[],description:string}} LogSet */
/** @typedef {{id:number,name:string,log_set_id:number,crash_after:number}} CrashCase */
/** @typedef {{id:number,crash_case_id:number,label:string,status:string,snapshot:object,result:object|null}} Recovery */
/** @typedef {{id:number,tx:string,key:string,holder:string,request_lsn:number,outcome:string,grant_lsn:number|null}} Wait */
/** @typedef {{tx:string,kind:string,reason:string,cycle:string[],undone:object[],released:string[],dropped_ops:number[]}} Abort */
export {};
