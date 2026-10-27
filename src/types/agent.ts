export type AgentStatus='PENDING'|'RUNNING'|'COMPLETED'|'FAILED';
export type AgentType='DOCUMENT'|'POLICY'|'CODING'|'FRAUD'|'DECISION';
export interface AgentResult { id:string; claimId:string; type:AgentType; name:string; status:AgentStatus; startedAt?:string; completedAt?:string; duration?:string; summary:string; confidence:number; details:Record<string,string|number|undefined>; }
