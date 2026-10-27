export function ProgressBar({value}:{value:number}){return <div className="progress" aria-label={`${value}% complete`}><span style={{width:`${value}%`}}/></div>}
