export interface RAGEvidence { id:string; claimId:string; query:string; documentName:string; page:number; relevance:number; excerpt:string; documentType:string; }
export interface EnterpriseSystem { id:string; name:string; status:'CONNECTED'|'DISCONNECTED'; latency:number; lastRequest:string; operations:string[]; endpoint:string; responseStatus:string; details:Record<string,string>; }
