import {AlertTriangle,ArrowLeft,BrainCircuit,FileSearch,FileText,LoaderCircle,Play,ShieldAlert,WandSparkles} from 'lucide-react';
import {useEffect,useState} from 'react';
import {Link,useParams} from 'react-router-dom';
import {ExpandableCard} from '../components/common/ExpandableCard';
import {ErrorState,LoadingState} from '../components/common/States';
import {RiskBadge,StatusBadge,labelize} from '../components/common/StatusBadge';
import {useAsync} from '../hooks/useAsync';
import {useToast} from '../hooks/useToast';
import {getClaimById,getEnterpriseStatus,getLatestClaimExtraction,getRAGEvidence,normalizeClaimExtraction,runClaimOcr} from '../services/claimsService';
import type {Claim} from '../types/claim';
import type {ClaimOcrResponse,NormalizationResponse} from '../types/extraction';

const tabs=['Overview','AI Processing','RAG Evidence','Enterprise Systems','Fraud Analysis','Audit Trail'];

export function ClaimDetailsPage(){
  const {claimId=''}=useParams();
  const [tab,setTab]=useState('Overview');
  const {data:claim,loading,error,retry}=useAsync(()=>getClaimById(claimId),[claimId]);
  const {data:evidence=[]}=useAsync(()=>getRAGEvidence(claimId),[claimId]);
  const {data:systems=[]}=useAsync(getEnterpriseStatus,[]);
  if(loading)return <LoadingState message="Loading claim..."/>;
  if(error)return <ErrorState message={error} retry={retry}/>;
  if(!claim)return <div className="not-found"><h1>Claim not found</h1><p>No claim exists with ID {claimId}.</p><Link className="button primary" to="/claims">Back to claims</Link></div>;
  return <>
    <Link className="back-link" to="/claims"><ArrowLeft size={16}/>Back to claims</Link>
    <section className="claim-hero"><div><div className="eyebrow">Claim record</div><h1>{claim.id} <StatusBadge status={claim.status}/></h1><p>{claim.patientName} · {claim.providerName} · {claim.payerName}</p></div><div className="claim-hero-stats"><span>Claim amount<b>₹{claim.amount.toLocaleString('en-IN')}</b></span><span>Date of service<b>{new Date(claim.serviceDate).toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'})}</b></span><span>Final decision<b>{claim.finalDecision}</b></span></div></section>
    <div className="tabs" role="tablist">{tabs.map(item=><button role="tab" aria-selected={tab===item} className={tab===item?'active':''} onClick={()=>setTab(item)} key={item}>{item}</button>)}</div>
    {tab==='Overview'&&<Overview claim={claim}/>} 
    {tab==='AI Processing'&&<AIProcessing claim={claim}/>} 
    {tab==='RAG Evidence'&&<section className="detail-stack"><div className="panel intro-panel"><FileSearch/><div><h2>Retrieval evidence</h2><p>“{evidence[0]?.query}”</p></div></div>{evidence.map(item=><ExpandableCard key={item.id} title={item.documentName} meta={<span className="score">{Math.round(item.relevance*100)}% relevant</span>}><p className="quote">“{item.excerpt}”</p><div className="agent-meta"><span>Page {item.page}</span><span>{item.documentType}</span><button className="text-button">View source</button></div></ExpandableCard>)}</section>}
    {tab==='Enterprise Systems'&&<section className="integration-grid">{systems.map(item=><ExpandableCard key={item.id} title={item.name} meta={<span className="connected"><i/>{item.status} · {item.latency}ms</span>}><div className="details-grid"><div><span>Last request</span><b>{item.lastRequest}</b></div><div><span>Response</span><b>{item.responseStatus}</b></div>{Object.entries(item.details).map(([key,value])=><div key={key}><span>{key}</span><b>{value}</b></div>)}</div><h4>Supported operations</h4><div className="chips">{item.operations.map(operation=><span key={operation}>{operation}</span>)}</div></ExpandableCard>)}</section>}
    {tab==='Fraud Analysis'&&<section className="panel fraud-panel"><ShieldAlert/><div><h2>Fraud risk: <RiskBadge risk={claim.risk}/></h2><p>Risk score <b>{claim.fraudScore.toFixed(2)}</b></p>{claim.id==='CLM10007'?<p>Duplicate claim check failed. Matching claim: <b>CLM09001</b>.</p>:<p>Duplicate claim, high amount, patient history and provider pattern checks passed.</p>}<h3>Historical claims</h3><p>CLM09001 — ₹42,000 &nbsp; · &nbsp; CLM09501 — ₹3,000</p></div></section>}
    {tab==='Audit Trail'&&<section className="panel"><h2>Audit trail</h2><div className="timeline"><p><i/>Claim submitted <small>{new Date(claim.submittedAt).toLocaleString()}</small></p><p><i/>Document validation completed <small>Mock workflow</small></p><p><i/>Current status: {claim.status} <small>System generated</small></p></div></section>}
  </>;
}

function AIProcessing({claim}:{claim:Claim}){
  const [ocr,setOcr]=useState<ClaimOcrResponse>();
  const [normalization,setNormalization]=useState<NormalizationResponse>();
  const [ocrLoading,setOcrLoading]=useState(false);
  const [normalizationLoading,setNormalizationLoading]=useState(false);
  const [loadError,setLoadError]=useState('');
  const toast=useToast();
  const hasDocument=claim.document.uploadStatus==='UPLOADED'&&claim.document.s3Key!=='Not uploaded';
  const busy=ocrLoading||normalizationLoading;
  useEffect(()=>{
    const controller=new AbortController();
    setLoadError('');
    void getLatestClaimExtraction(claim.id,{signal:controller.signal}).then(setNormalization).catch(error=>{if(!controller.signal.aborted)setLoadError(error instanceof Error?error.message:'Unable to load the latest extraction.')});
    return ()=>controller.abort();
  },[claim.id]);
  const runOcr=async()=>{setOcrLoading(true);try{const result=await runClaimOcr(claim.id);setOcr(result);setNormalization(undefined);toast(`Tesseract OCR completed for ${claim.id}`)}catch(error){toast(error instanceof Error?error.message:'OCR could not be completed.','error')}finally{setOcrLoading(false)}};
  const normalize=async()=>{setNormalizationLoading(true);try{const result=await normalizeClaimExtraction(claim.id);setNormalization(result);toast(`Extraction ${result.extraction.extraction_status.toLowerCase().replaceAll('_',' ')}`)}catch(error){toast(error instanceof Error?error.message:'Normalization could not be completed.','error')}finally{setNormalizationLoading(false)}};
  return <section className="detail-stack processing-workflow">
    <article className="panel workflow-header"><div className="section-title"><span><BrainCircuit size={16}/></span><div><h2>OCR and extraction workflow</h2><p>Run local Tesseract OCR, inspect its evidence, then normalize the extracted fields. This does not approve or deny the claim.</p></div></div><div className="workflow-actions"><button className="button secondary" disabled={!hasDocument||busy} onClick={runOcr}>{ocrLoading?<LoaderCircle className="spin" size={16}/>:<Play size={16}/>}Run OCR</button><button className="button primary" disabled={!hasDocument||busy} onClick={normalize}>{normalizationLoading?<LoaderCircle className="spin" size={16}/>:<WandSparkles size={16}/>}Normalize Extraction</button></div></article>
    <article className="panel"><div className="panel-head"><div><h2>Document readiness</h2><p>The PDF remains private; only its S3 object key is displayed.</p></div><StatusBadge status={hasDocument?'READY':'NOT_READY'}/></div><div className="readiness-grid"><Info label="Claim ID" value={claim.id}/><Info label="Claim lifecycle status" value={labelize(claim.status)}/><Info label="Document name" value={claim.document.filename}/><Info label="S3 object key" value={hasDocument?claim.document.s3Key:'Not uploaded'} mono/></div>{!hasDocument&&<div className="workflow-note warning"><AlertTriangle size={16}/><span>Upload a PDF for this claim before running OCR or normalization.</span></div>}{loadError&&<div className="workflow-note warning"><AlertTriangle size={16}/><span>{loadError}</span></div>}</article>
    {ocr&&<OcrResult result={ocr}/>} 
    {!ocr&&hasDocument&&<article className="panel empty-workflow"><FileText/><div><h2>Raw OCR not loaded in this browser session</h2><p>Run OCR to inspect candidate fields. Normalize Extraction can also use an existing raw artifact already stored in S3.</p></div></article>}
    {normalization&&<NormalizedResult result={normalization}/>} 
  </section>;
}

function OcrResult({result}:{result:ClaimOcrResponse}){return <article className="panel"><div className="panel-head"><div><h2>Tesseract OCR result</h2><p>Raw OCR quality is evidence for review, not a claim decision.</p></div><StatusBadge status={result.extraction_status}/></div><div className="result-metrics"><Info label="OCR engine" value="Tesseract"/><Info label="Pages" value={String(result.page_count)}/><Info label="Average word confidence" value={result.average_word_confidence===null?'Unavailable':`${result.average_word_confidence.toFixed(2)}%`}/><Info label="Review indicated" value={result.review_required?'Yes':'No'}/></div><div className="artifact-list"><Info label="Local JSON path" value={result.local_output_path} mono/><Info label="Raw S3 artifact key" value={result.s3_result_key??'Not uploaded'} mono/><Info label="Document Claim ID" value={result.document_claim_id??'Not extracted'} mono/><Info label="Claim ID match" value={result.claim_id_matches_document===null?'Not verifiable':result.claim_id_matches_document?'Yes':'No'}/></div>{result.claim_id_matches_document===false&&<div className="workflow-note warning"><AlertTriangle size={16}/><span>The Claim ID printed in the document does not match this application claim.</span></div>}<h3 className="workflow-subtitle">Candidate fields</h3><div className="table-wrap"><table><thead><tr><th>Field</th><th>Value</th><th>Found</th><th>Confidence</th><th>Validation</th><th>Review reason</th></tr></thead><tbody>{Object.entries(result.candidate_fields).map(([field,candidate])=>{const review=result.field_review[field];return <tr key={field}><td><b>{labelize(field)}</b></td><td>{candidate.value??candidate.candidates[0]?.raw_value??'—'}</td><td>{candidate.found?'Yes':'No'}</td><td>{review?.confidence==null?'—':`${review.confidence.toFixed(2)}%`}</td><td><StatusBadge status={review?.validation_outcome??'UNKNOWN'}/></td><td className="wrap-cell">{review?.review_reasons.join('; ')||'None'}</td></tr>})}</tbody></table></div></article>}

function NormalizedResult({result}:{result:NormalizationResponse}){const extraction=result.extraction;const fields:[string,string|null][]=[['Patient ID',extraction.patient_id],['Patient name',extraction.patient_name],['Provider ID',extraction.provider_id],['Payer ID',extraction.payer_id],['Policy ID',extraction.policy_id],['Diagnosis code',extraction.diagnosis_code],['Procedure code',extraction.procedure_code],['Amount',extraction.amount],['Currency',extraction.currency],['Date of service',extraction.date_of_service]];return <article className="panel normalized-result"><div className="panel-head"><div><h2>Normalized extraction</h2><p>Extraction status is separate from the claim lifecycle status.</p></div><StatusBadge status={extraction.extraction_status}/></div><div className="result-metrics"><Info label="Review required" value={extraction.review_required?'Yes':'No'}/><Info label="Normalized artifact key" value={result.normalized_artifact_key} mono/><Info label="Raw OCR artifact key" value={result.raw_ocr_artifact_key} mono/></div><div className="normalized-grid">{fields.map(([label,value])=><Info key={label} label={label} value={value??'Not extracted'}/>)}</div><h3 className="workflow-subtitle">Review flags</h3>{extraction.review_flags.length===0?<div className="workflow-note success">No validation review flags were generated.</div>:<div className="review-flags">{extraction.review_flags.map((flag,index)=><div className={`review-flag ${flag.severity.toLowerCase()}`} key={`${flag.code}-${flag.field}-${index}`}><StatusBadge status={flag.severity}/><div><b>{labelize(flag.field)} · {flag.code}</b><p>{flag.message}</p></div></div>)}</div>}{extraction.failure_reason&&<div className="workflow-note warning"><AlertTriangle size={16}/><span>{extraction.failure_reason}</span></div>}</article>}

function Info({label,value,mono=false}:{label:string;value:string;mono?:boolean}){return <div className="workflow-info"><span>{label}</span><b className={mono?'mono':''}>{value}</b></div>}

function Overview({claim}:{claim:Claim}){const groups=[['Claim Summary',[['Claim ID',claim.id],['Claim type','Medical'],['Status',claim.status],['Submission date',new Date(claim.submittedAt).toLocaleDateString()]]],['Patient Information',[['Patient name',claim.patientName],['Patient ID',claim.patientId],['Date of birth',claim.patientId==='PAT1001'?'12 May 1974':'Synthetic record'],['Gender',claim.patientId==='PAT1001'?'M':'—']]],['Provider Information',[['Provider name',claim.providerName],['Provider ID',claim.providerId]]],['Payer Information',[['Payer name',claim.payerName],['Payer ID',claim.payerId],['Policy ID',claim.policyId],['Eligibility',claim.eligibility]]],['Clinical Information',[['Diagnosis',claim.diagnosisDescription],['ICD-10 code',claim.diagnosis],['Procedure',claim.procedureDescription],['CPT code',claim.procedure],['Date of Service',new Date(claim.serviceDate).toLocaleDateString()]]],['Billing Information',[['Claim amount',`₹${claim.amount.toLocaleString('en-IN')}`],['Coverage',`${claim.coveragePercent}%`],['Patient responsibility',`₹${claim.patientResponsibility.toLocaleString('en-IN')}`]]],['Document Information',[['Filename',claim.document.filename],['Document type',claim.document.type],['Upload status',claim.document.uploadStatus],['S3 object key',claim.document.s3Key]]]] as const;return <section className="overview-grid">{groups.map(([title,rows])=><article className="panel info-card" key={title}><h2>{title}</h2>{rows.map(([key,value])=><div key={key}><span>{key}</span><b>{value}</b></div>)}</article>)}</section>}
