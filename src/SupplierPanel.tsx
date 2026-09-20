import {useEffect,useState} from 'react';
import {Building2,LoaderCircle,MapPin,RotateCcw,HardHat,Copy,Check} from 'lucide-react';
import {request} from './api';
import type {Candidate} from './types';
import './suppliers.css';

type Builder={id:string;name:string;role:string;project_fit:string;service_area:string;evidence:string;address:string|null;website:string;phone:string|null;source_url:string;services:string[];email:string|null;questions:string[]};
type BuilderResult={builders:Builder[];notice:string;retrieved_at:string;cache_hit:boolean;project:{label:string;location:string}};
export default function SupplierPanel({candidate,runId,city}:{candidate:Candidate;runId:string;city?:string}){
 const [retry,setRetry]=useState(0),[copied,setCopied]=useState<string|null>(null),[copyError,setCopyError]=useState('');
 const copyBrief=async(b:Builder)=>{try{await navigator.clipboard.writeText(`Hello ${b.name},\n\nWe are assessing ${data?.project.label||candidate.technology} at ${candidate.name}, ${data?.project.location||city||''}. This is a preliminary screening estimate.\n\n${b.questions.map(q=>`• ${q}`).join('\n')}\n\nPlease share your proposed scope, itemized estimate and next steps.`);setCopied(b.id);setCopyError('');}catch{setCopyError('Clipboard access unavailable. You can select and copy the questions below.');}};
 const [data,setData]=useState<BuilderResult|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
 useEffect(()=>{
  const controller=new AbortController();setLoading(true);setError('');setData(null);
  void request<BuilderResult>('/api/suppliers/search',{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify({run_id:runId,site_id:candidate.id})})
   .then(value=>{if(!controller.signal.aborted)setData(value);})
   .catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'Builder search unavailable.');})
   .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
  return()=>controller.abort();
 },[candidate.id,runId,retry]);
 const kind=candidate.technology==='wind'?'Onshore wind':candidate.surface_type==='rooftop'?'Rooftop solar':candidate.surface_type?.startsWith('parking')?'Solar parking canopy':'Ground-mounted solar';
 return <section className="supplier-panel" aria-label="Project construction partners">
  <div className="supplier-location"><MapPin size={20}/><div><strong>{candidate.name}</strong><span>{data?.project.location||city||`${candidate.latitude.toFixed(4)}, ${candidate.longitude.toFixed(4)}`}</span></div><span className="supplier-live">PROJECT BUILDERS</span></div>
  <div className="builder-project"><HardHat size={18}/><div><strong>{data?.project.label||`${kind} · ${candidate.capacity_mw<1?`${Math.round(candidate.capacity_mw*1000)} kW`:`${candidate.capacity_mw.toFixed(2)} MW`}`}</strong><span>Matched to this site · screening capacity</span></div></div>
  <div className="supplier-results" aria-live="polite" aria-busy={loading}>
   {loading?<div className="supplier-empty"><LoaderCircle className="spin" size={25}/><strong>Finding builders for your {kind.toLowerCase()} project…</strong><p>Checking company websites for construction experience and service in this area.</p></div>:error?<div className="supplier-empty"><Building2 size={25}/><strong>Builder search unavailable</strong><p>{error}</p><button onClick={()=>setRetry(v=>v+1)}><RotateCcw size={13}/>Retry search</button></div>:data?.builders.length?<><div className="supplier-count"><strong>{data.builders.length} potential construction partners</strong><span>Company-published services</span></div>{data.builders.map(b=><article className="supplier-card" key={b.id}>
    <div className="supplier-card-heading"><div><span>{b.role}</span><h3>{b.name}</h3></div><HardHat size={20}/></div>
    <p>{b.project_fit}</p><p className="builder-service"><MapPin size={12}/>Service area: {b.service_area}</p>
    {b.services?.length>0&&<div className="builder-capabilities" aria-label="Services mentioned on company website">{b.services.map(service=><span key={service}>{service}</span>)}</div>}
    <details className="supplier-details"><summary>Company details & project questions</summary><dl><div><dt>Company evidence</dt><dd>{b.evidence}</dd></div><div><dt>Project size</dt><dd>{data.project.label} · exact delivery capacity to confirm</dd></div><div><dt>Email</dt><dd>{b.email||'Not documented in source'}</dd></div><div><dt>Website</dt><dd>{b.website}</dd></div><div><dt>Phone</dt><dd>{b.phone||'Not documented in source'}</dd></div>{b.address&&<div><dt>Listed address</dt><dd>{b.address}</dd></div>}<div><dt>Source page</dt><dd>{b.source_url}</dd></div></dl><div className="builder-questions"><h4>Questions for this builder</h4><ul>{b.questions?.map(q=><li key={q}>{q}</li>)}</ul><button onClick={()=>void copyBrief(b)}>{copied===b.id?<Check size={14}/>:<Copy size={14}/>} {copied===b.id?'Inquiry copied':'Copy project inquiry'}</button><small>Copies a draft for you. No message is sent.</small>{copyError&&<p role="status">{copyError}</p>}</div></details>
   </article>)}</>:<div className="supplier-empty"><Building2 size={25}/><strong>No matching builders found</strong><p>The search did not find company-published evidence of construction services for this project in this area.</p><button onClick={()=>setRetry(v=>v+1)}><RotateCcw size={13}/>Check again</button></div>}
  </div>
  <p className="supplier-notice">{data?.notice||'Looking for firms that can design and build this project. Exact capacity, licensing and availability still need confirmation.'}</p>
  {data&&<p className="supplier-attribution">Company web sources · {data.cache_hit?'Last checked':'Checked'} {new Date(data.retrieved_at).toLocaleDateString()} · Contact details stay in this panel</p>}
 </section>;
}
