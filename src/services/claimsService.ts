import axios from 'axios';
import {payers,patients,providers} from '../data';
import type {Claim,ClaimApiResponse,ClaimListApiResponse,ClaimUploadResponse,CreateClaimPayload} from '../types/claim';
import type {ClaimOcrResponse,NormalizationResponse,ProcessingRequestOptions} from '../types/extraction';
import {apiClient} from './apiClient';
import {getAgentResults,getEnterpriseStatus,getRAGEvidence,processClaim,runEvaluation} from './mockClaimService';

const patientName=(id:string)=>patients.find(item=>item.id===id)?.name??id;
const providerName=(id:string)=>providers.find(item=>item.id===id)?.name??id;
const payerName=(id:string)=>payers.find(item=>item.id===id)?.name??id;

function toClaim(item:ClaimApiResponse):Claim{return {id:item.claim_id,patientId:item.patient_id,patientName:patientName(item.patient_id),providerId:item.provider_id,providerName:providerName(item.provider_id),payerId:item.payer_id,payerName:payerName(item.payer_id),policyId:'Not provided',diagnosis:'—',diagnosisDescription:'Pending processing',procedure:'—',procedureDescription:'Pending processing',amount:0,submittedAt:item.created_at,serviceDate:item.created_at,status:item.status,expectedStatus:'APPROVED',risk:'LOW',fraudScore:0,eligibility:'ACTIVE',coverage:'YES',authorization:'NOT REQUIRED',authorizationEvidence:'NOT APPLICABLE',coveragePercent:0,patientResponsibility:0,processingTime:'Not started',document:{filename:item.document_name,type:item.claim_type,uploadStatus:item.s3_object_key?'UPLOADED':'NOT UPLOADED',s3Key:item.s3_object_key??'Not uploaded'},finalDecision:'Pending',confidence:0};}

export function getApiErrorMessage(error:unknown):string{if(!axios.isAxiosError(error))return 'An unexpected error occurred. Please try again.';if(!error.response)return 'Unable to reach the claims service. Check that the backend is running.';const detail=error.response.data?.detail;if(Array.isArray(detail))return detail.map(item=>`${item.loc?.at(-1)??'Field'}: ${item.msg}`).join(' ');if(typeof detail==='string')return detail;if(error.response.status===400)return 'The request could not be processed. Check the claim information.';if(error.response.status===404)return 'The requested claim was not found.';if(error.response.status===422)return 'Some claim fields need correction.';return 'The claims service could not complete the request. Please try again.';}

export async function getClaims():Promise<Claim[]>{const {data}=await apiClient.get<ClaimListApiResponse>('/claims');return data.claims.map(toClaim);}
export async function getClaimById(id:string):Promise<Claim|undefined>{try{return toClaim((await apiClient.get<ClaimApiResponse>(`/claims/${encodeURIComponent(id)}`)).data);}catch(error){if(axios.isAxiosError(error)&&error.response?.status===404)return undefined;throw new Error(getApiErrorMessage(error));}}
export async function createClaim(payload:CreateClaimPayload):Promise<Claim>{try{const {data}=await apiClient.post<ClaimApiResponse>('/claims',{patient_id:payload.patientId,provider_id:payload.providerId,payer_id:payload.payerId,claim_type:'OUTPATIENT',document_name:payload.filename});return toClaim(data);}catch(error){throw new Error(getApiErrorMessage(error));}}
export async function uploadClaim(file:File,payload:Omit<CreateClaimPayload,'filename'>,claimType:string):Promise<ClaimUploadResponse>{const body=new FormData();body.append('file',file);body.append('patient_id',payload.patientId);body.append('provider_id',payload.providerId);body.append('payer_id',payload.payerId);body.append('claim_type',claimType);try{return (await apiClient.post<ClaimUploadResponse>('/claims/upload',body)).data;}catch(error){throw new Error(getApiErrorMessage(error));}}

export async function runClaimOcr(claimId:string,options:ProcessingRequestOptions={}):Promise<ClaimOcrResponse>{try{return (await apiClient.post<ClaimOcrResponse>(`/claims/${encodeURIComponent(claimId)}/ocr`,undefined,{signal:options.signal,timeout:120000})).data;}catch(error){throw new Error(getApiErrorMessage(error));}}
export async function normalizeClaimExtraction(claimId:string,options:ProcessingRequestOptions={}):Promise<NormalizationResponse>{try{return (await apiClient.post<NormalizationResponse>(`/claims/${encodeURIComponent(claimId)}/normalize`,undefined,{signal:options.signal,timeout:60000})).data;}catch(error){throw new Error(getApiErrorMessage(error));}}
export async function getLatestClaimExtraction(claimId:string,options:ProcessingRequestOptions={}):Promise<NormalizationResponse|undefined>{try{return (await apiClient.get<NormalizationResponse>(`/claims/${encodeURIComponent(claimId)}/extraction`,{signal:options.signal})).data;}catch(error){if(axios.isAxiosError(error)&&error.response?.status===404)return undefined;throw new Error(getApiErrorMessage(error));}}

export {getAgentResults,getEnterpriseStatus,getRAGEvidence,processClaim,runEvaluation};
