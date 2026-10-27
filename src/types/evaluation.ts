export interface EvaluationMetric { name:string; value:number; unit:'percent'|'seconds'; }
export interface EvaluationTest { id:string; claimId:string; expected:string; actual:string; result:'PASS'|'FAIL'; latency:string; }
