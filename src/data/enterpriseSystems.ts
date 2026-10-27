import type { EnterpriseSystem } from '../types/rag';
export const enterpriseSystems:EnterpriseSystem[]=[
{id:'ehr',name:'Mock EHR',status:'CONNECTED',latency:142,lastRequest:'GET /patients/PAT1001',operations:['Patient Lookup','Medical History','Medication History'],endpoint:'GET /patients/PAT1001',responseStatus:'200 OK',details:{Patient:'PAT1001 — John Smith',DOB:'12 May 1974',Gender:'M','Medical history':'Hypertension, Pneumonia',Medication:'Amlodipine 5 mg, once daily'}},
{id:'payer',name:'Mock Payer',status:'CONNECTED',latency:186,lastRequest:'GET /coverage/POL1001/99223',operations:['Eligibility','Coverage','Claim submission'],endpoint:'GET /eligibility/PAT1001',responseStatus:'200 OK',details:{Payer:'ABC Insurance',Eligibility:'ACTIVE',Policy:'POL1001','CPT 99223':'COVERED',Authorization:'NOT REQUIRED',Coverage:'80%'}},
{id:'provider',name:'Mock Provider',status:'CONNECTED',latency:96,lastRequest:'GET /providers/PROV1001',operations:['Provider lookup','Provider status validation'],endpoint:'GET /providers/PROV1001',responseStatus:'200 OK',details:{Provider:'ABC Hospital',Status:'ACTIVE'}},
{id:'coding',name:'Mock Coding API',status:'CONNECTED',latency:118,lastRequest:'GET /cpt/99223',operations:['ICD-10 Validation','CPT Validation'],endpoint:'GET /codes/validate',responseStatus:'200 OK',details:{'ICD-10 J18.9':'VALID','CPT 99223':'VALID'}},
];
