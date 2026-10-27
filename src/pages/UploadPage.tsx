import {FileText,Trash2,UploadCloud} from 'lucide-react';
import {useRef,useState,type DragEvent} from 'react';
import {useNavigate} from 'react-router-dom';
import {PageHeader} from '../components/common/PageHeader';
import {ProgressBar} from '../components/common/ProgressBar';
import {patients,payers,providers} from '../data';
import {useToast} from '../hooks/useToast';
import {uploadClaim} from '../services/claimsService';
import type {ClaimUploadResponse} from '../types/claim';

export function UploadPage(){
  const [form,setForm]=useState({patientId:'',providerId:'',payerId:'',policyId:'',serviceDate:'',claimType:'OUTPATIENT'});
  const [file,setFile]=useState<File>();
  const [error,setError]=useState('');
  const [errors,setErrors]=useState<Record<string,string>>({});
  const [progress,setProgress]=useState(0);
  const [stage,setStage]=useState('');
  const [result,setResult]=useState<ClaimUploadResponse>();
  const input=useRef<HTMLInputElement>(null);
  const nav=useNavigate();
  const toast=useToast();
  const choose=(selected?:File)=>{setError('');setResult(undefined);if(!selected)return;if(selected.type!=='application/pdf')return setError('Only PDF files are supported.');if(selected.size>10*1024*1024)return setError('The selected file exceeds the 10 MB limit.');setFile(selected)};
  const drop=(event:DragEvent)=>{event.preventDefault();choose(event.dataTransfer.files[0])};
  const submit=async()=>{
    const next:Record<string,string>={};
    Object.entries(form).forEach(([key,value])=>{if(!value)next[key]='Required'});
    if(!file)setError('Select a claim PDF before creating the claim.');
    setErrors(next);
    if(Object.keys(next).length||!file)return;
    setError('');setProgress(30);setStage('Uploading claim document...');
    try{
      const uploaded=await uploadClaim(file,form,form.claimType);
      setProgress(100);setStage('Submission complete');setResult(uploaded);
      toast(`${uploaded.claim_id} uploaded successfully`);
      window.dispatchEvent(new Event('claims:changed'));
    }catch(caught){
      setProgress(0);setStage('');
      setError(caught instanceof Error?caught.message:'Unable to upload claim document. Please try again.');
    }
  };
  return <><PageHeader title="Submit New Claim" subtitle="Create a claim and securely upload its PDF document."/><div className="form-layout">
    <section className="panel form-panel"><div className="section-title"><span>1</span><div><h2>Claim information</h2><p>Enter member, provider and coverage details.</p></div></div><div className="form-grid">
      {[['patientId','Patient ID',patients.map(x=>[x.id,`${x.id} — ${x.name}`])],['providerId','Provider ID',providers.map(x=>[x.id,`${x.id} — ${x.name}`])],['payerId','Payer',payers.map(x=>[x.id,x.name])],['policyId','Policy ID',(payers.find(x=>x.id===form.payerId)?.policies??Array.from({length:8},(_,i)=>`POL100${i+1}`)).map(x=>[x,x])]].map(([key,label,opts])=><label key={key as string}><span>{label as string} *</span><select value={form[key as keyof typeof form]} onChange={e=>setForm({...form,[key as string]:e.target.value})}><option value="">Select {String(label).toLowerCase()}</option>{(opts as string[][]).map(([value,text])=><option value={value} key={value}>{text}</option>)}</select>{errors[key as string]&&<small className="field-error">{errors[key as string]}</small>}</label>)}
      <label><span>Claim Type *</span><select value={form.claimType} onChange={e=>setForm({...form,claimType:e.target.value})}><option>OUTPATIENT</option><option>INPATIENT</option><option>EMERGENCY</option><option>PHARMACY</option></select></label>
      <label><span>Date of Service *</span><input type="date" value={form.serviceDate} onChange={e=>setForm({...form,serviceDate:e.target.value})}/>{errors.serviceDate&&<small className="field-error">{errors.serviceDate}</small>}</label>
    </div></section>
    <section className="panel form-panel"><div className="section-title"><span>2</span><div><h2>Claim document</h2><p>Upload one PDF, up to 10 MB.</p></div></div>
      {!file?<div className="dropzone" tabIndex={0} role="button" onClick={()=>input.current?.click()} onKeyDown={e=>{if(e.key==='Enter'||e.key===' ')input.current?.click()}} onDragOver={e=>e.preventDefault()} onDrop={drop}><UploadCloud/><b>Drag and drop your claim PDF</b><p>or choose it from your computer</p><button className="button secondary" type="button">Browse File</button><input ref={input} hidden type="file" accept="application/pdf,.pdf" onChange={e=>choose(e.target.files?.[0])}/></div>:<div className="file-card"><FileText/><div><b>{file.name}</b><span>{(file.size/1024/1024).toFixed(2)} MB · PDF</span></div><button className="icon-button" onClick={()=>setFile(undefined)} aria-label="Remove file"><Trash2/></button></div>}
      {error&&<p className="alert error">{error}</p>}
      {stage&&<div className="upload-progress"><div><span>{stage}</span><b>{progress}%</b></div><ProgressBar value={progress}/></div>}
      {result&&<div className="alert success"><b>Claim Created Successfully</b><p>Claim ID: {result.claim_id}</p><p>Document: {result.filename}</p><p>Status: {result.status}</p><p>S3 Object: {result.s3_object_key}</p><button className="button secondary" onClick={()=>nav(`/claims/${result.claim_id}`)}>View claim</button></div>}
      <div className="form-actions"><button className="button primary" disabled={stage==='Uploading claim document...'} onClick={submit}>{stage==='Uploading claim document...'?'Uploading...':'Create Claim'}</button></div>
    </section></div></>;
}
