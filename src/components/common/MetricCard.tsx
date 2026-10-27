import type {LucideIcon} from 'lucide-react';
export function MetricCard({label,value,icon:Icon,tone='blue',detail}:{label:string;value:string|number;icon:LucideIcon;tone?:string;detail?:string}){return <article className="metric-card"><div className={`metric-icon ${tone}`}><Icon size={18}/></div><div><p>{label}</p><strong>{value}</strong>{detail&&<small>{detail}</small>}</div></article>}
