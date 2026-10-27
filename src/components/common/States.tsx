import {AlertTriangle,Inbox,LoaderCircle} from 'lucide-react';
export function LoadingState({message='Loading...'}:{message?:string}){return <div className="state"><LoaderCircle className="spin"/><p>{message}</p></div>}
export function ErrorState({message='Unable to load data.',retry}:{message?:string;retry?:()=>void}){return <div className="state error"><AlertTriangle/><p>{message}</p>{retry&&<button className="button secondary" onClick={retry}>Retry</button>}</div>}
export function EmptyState({message='No data found.'}:{message?:string}){return <div className="state"><Inbox/><p>{message}</p></div>}
