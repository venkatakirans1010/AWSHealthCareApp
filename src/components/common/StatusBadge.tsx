import type {ClaimStatus,RiskLevel} from '../../types/claim';
export const labelize=(v:string)=>v.toLowerCase().replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
export function StatusBadge({status}:{status:ClaimStatus|string}){return <span className={`badge status-${status.toLowerCase()}`}>{labelize(status)}</span>}
export function RiskBadge({risk}:{risk:RiskLevel}){return <span className={`risk risk-${risk.toLowerCase()}`}><i/>{labelize(risk)}</span>}
