import {createContext,useContext,useMemo,useState,type ReactNode} from 'react';
type ToastFn=(message:string,type?:'success'|'error')=>void;const C=createContext<ToastFn>(()=>{});
export function ToastProvider({children}:{children:ReactNode}){const [toast,setToast]=useState<{message:string;type:string}|null>(null);const show=useMemo<ToastFn>(()=>(message,type='success')=>{setToast({message,type});window.setTimeout(()=>setToast(null),3200)},[]);return <C.Provider value={show}>{children}{toast&&<div role="status" className={`toast ${toast.type}`}>{toast.message}</div>}</C.Provider>}
export const useToast=()=>useContext(C);
