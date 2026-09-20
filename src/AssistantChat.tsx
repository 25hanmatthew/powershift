import {useEffect,useRef,useState} from 'react';
import {ArrowUpRight,Check,ChevronRight,LoaderCircle,MessageSquare,Send,Square,Sparkles,RotateCcw} from 'lucide-react';
import type {Candidate,Dataset,Plan,Result} from './types';
import {readAssistantEvents} from './assistantStream';
import './assistant-chat.css';

type Activity={id:string;name:string;label:string;ok?:boolean;duration_ms?:number};
type Reply={type:'done';answer:string;sites:Candidate[];sources:Dataset[];followups:string[];result:Result|null;usage:{input_tokens:number;output_tokens:number}};
type Event=Reply|{type:'status';message:string}|({type:'tool_start'|'tool_end'}&Activity)|{type:'error';message:string};
type Message={role:'user'|'assistant';content:string;sites?:Candidate[];sources?:Dataset[];followups?:string[];activities?:Activity[];interrupted?:boolean;runId?:string};
type Props={result:Result|null;plan:Plan;selected:Candidate|null;busy:boolean;configured:boolean|null;onResult:(result:Result)=>void;onSelect:(id:string)=>void};
const safeSource=(url:string)=>{try{const u=new URL(url);return u.protocol==='https:'||u.protocol==='http:'?u.href:null;}catch{return null;}};

export default function AssistantChat({result,plan,selected,busy,configured,onResult,onSelect}:Props){
 const [messages,setMessages]=useState<Message[]>([]),[draft,setDraft]=useState(''),[running,setRunning]=useState(false),[status,setStatus]=useState(''),[error,setError]=useState('');
 const [activities,setActivities]=useState<Activity[]>([]);
 const controller=useRef<AbortController|null>(null),generation=useRef(0),end=useRef<HTMLDivElement>(null),input=useRef<HTMLTextAreaElement>(null);
 const view={weights:plan.weights,constraints:plan.constraints,target_mw:plan.target_mw,technology:plan.technology,historical_intelligence:Boolean(plan.historical_intelligence),use_operating_evidence:plan.use_operating_evidence!==false};
 const contextKey=JSON.stringify([result?.run_id,selected?.id,view,busy]);
 const latest=useRef({contextKey,onResult});latest.current={contextKey,onResult};
 const stop=(message='Stopped. Your map is unchanged.')=>{generation.current++;controller.current?.abort();controller.current=null;setRunning(false);setStatus('');setActivities([]);setError(message);setMessages(old=>old.map((m,i)=>i===old.length-1&&m.role==='user'?{...m,interrupted:true}:m));};
 useEffect(()=>{if(controller.current)stop('The map context changed. Send your question again for the current analysis.');},[contextKey]);
 useEffect(()=>()=>{generation.current++;controller.current?.abort();},[]);
 useEffect(()=>{end.current?.scrollIntoView({block:'nearest'});},[messages,status,activities]);
 const send=async(text=draft)=>{
  const message=text.trim();if(!message||controller.current||busy||configured!==true)return;
  const requestController=new AbortController();controller.current=requestController;const ticket=++generation.current,context=contextKey;
  const history=messages.filter(m=>!m.interrupted).slice(-12).map(({role,content})=>({role,content}));
  setMessages(old=>[...old,{role:'user',content:message}]);setDraft('');setError('');setRunning(true);setStatus('Understanding your request…');setActivities([]);
  let completed=false;let steps:Activity[]=[];
  const isCurrent=()=>ticket===generation.current&&latest.current.contextKey===context;
  try{
   const response=await fetch('/api/assistant/chat',{method:'POST',headers:{'Content-Type':'application/json'},signal:requestController.signal,
    body:JSON.stringify({message,history,run_id:result?.run_id??null,selected_site_id:selected?.id??null,view:result?view:null})});
   await readAssistantEvents(response,raw=>{
    if(!isCurrent())return;const event=raw as Event;
    if(event.type==='error')throw new Error(event.message);
    if(event.type==='status')setStatus(event.message);
    if(event.type==='tool_start'||event.type==='tool_end'){
     const activity={id:event.id,name:event.name,label:event.label,ok:event.type==='tool_end'?event.ok:undefined,duration_ms:event.duration_ms};
     steps=[...steps.filter(s=>s.id!==activity.id),activity];setActivities(steps);setStatus(event.label+(event.name==='search_sites'?' · first-time searches can take several minutes.':'…'));
    }
    if(event.type==='done'){
     completed=true;controller.current=null;setRunning(false);setStatus('');setActivities([]);
     setMessages(old=>[...old,{role:'assistant',content:event.answer,sites:event.sites,sources:event.sources,followups:event.followups,activities:steps,runId:event.result?.run_id??result?.run_id}]);
     if(event.result)latest.current.onResult(event.result);
    }
   });
   if(!completed&&isCurrent())throw new Error('The response was interrupted. Your map is unchanged. Please retry.');
  }catch(e){if(isCurrent()&&!requestController.signal.aborted){setError(e instanceof Error?e.message:'The assistant could not finish.');setDraft(message);}}
  finally{if(ticket===generation.current){controller.current=null;setRunning(false);setStatus('');setActivities([]);if(!completed)setMessages(old=>old.map((m,i)=>i===old.length-1&&m.role==='user'?{...m,interrupted:true}:m));}}
 };
 const suggestions=selected?[`Explain ${selected.name}'s strengths and limitations.`,'Compare this site with the highest-ranked alternative.','Prioritize grid proximity and update my shortlist.']:['Explain the current shortlist and its trade-offs.','Compare the top two sites.','Prioritize existing developed land.'];
 return <section className="assistant-chat" aria-label="PowerShift AI assistant">
  <header className="assistant-header"><div className="assistant-eyebrow"><Sparkles size={13}/>POWERSHIFT ASSISTANT</div><div className="assistant-title"><h2>Plan with evidence.</h2><button aria-label="Start a new conversation" title="New conversation" disabled={running} onClick={()=>{setMessages([]);setError('');setDraft('');input.current?.focus();}}><RotateCcw size={16}/></button></div><p>Ask, compare, and refine your next energy project.</p></header>
  <div className="assistant-context"><span className="live-dot"/><span>{busy?'Collecting site evidence…':selected?selected.name:result?.city?`${result.city.name} · ${result.candidates.length} shortlisted sites`:result?'Current screening analysis':'No analysis loaded'}</span>{result?.mode==='demo'&&<b>DEMO</b>}</div>
  <div className="assistant-conversation" role="log" aria-label="Planning conversation" aria-live="polite" aria-relevant="additions text">
   {!messages.length&&<div className="assistant-welcome"><span className="assistant-mark"><MessageSquare size={25}/></span><h3>What would you like to explore?</h3><p>I can investigate site evidence, compare options, and run a new scenario using your priorities.</p><div className="assistant-suggestions">{suggestions.map(s=><button key={s} disabled={running||busy||configured!==true} onClick={()=>void send(s)}>{s}<ChevronRight size={14}/></button>)}</div></div>}
   {messages.map((m,i)=><article className={`assistant-message ${m.role}`} key={i}><div className="assistant-speaker">{m.role==='user'?'YOU':<><Sparkles size={11}/>POWERSHIFT</>}</div><p>{m.content}</p>
    {!!m.activities?.length&&<details className="assistant-activity"><summary>{m.activities.length} actions checked</summary>{m.activities.map(a=><div key={a.id}>{a.ok?<Check size={12}/>:<span>!</span>}{a.label}{a.ok===false?' · unavailable':''}</div>)}</details>}
    {!!m.sites?.length&&<div className="assistant-site-links">{m.sites.map(c=><button key={c.id} disabled={m.runId!==result?.run_id} onClick={()=>onSelect(c.id)}><span><strong>{c.name}</strong><small>{c.capacity_mw.toLocaleString()} MW · {c.score?.toFixed(1)??'Excluded'} / 100</small></span><ArrowUpRight size={15}/></button>)}</div>}
    {!!m.sources?.length&&<div className="assistant-sources"><span>Evidence</span>{m.sources.map(s=>{const url=safeSource(s.source_url);return url?<a key={s.id} href={url} target="_blank" rel="noreferrer">{s.name}<ArrowUpRight size={11}/></a>:<span key={s.id}>{s.name}</span>;})}</div>}
    {i===messages.length-1&&!!m.followups?.length&&<div className="assistant-followups">{m.followups.map(s=><button key={s} disabled={running||busy||configured!==true} onClick={()=>void send(s)}>{s}</button>)}</div>}
    {m.interrupted&&<small className="assistant-interrupted">Not completed</small>}
   </article>)}
   {running&&<div className="assistant-working" role="status"><LoaderCircle size={16} className="spin"/><span>{status}</span></div>}
   {activities.map(a=><div className="assistant-live-step" key={a.id}>{a.ok===undefined?<span className="live-dot"/>:a.ok?<Check size={12}/>:<span>!</span>}{a.label}{a.ok===false?' · unavailable':''}</div>)}
   <div ref={end}/>
  </div>
  {error&&<div className="assistant-error" role="alert">{error}</div>}
  {configured===false&&<div className="assistant-error">Connect OpenAI on the server to enable the assistant. You can still explore the Sites tab.</div>}
  <form className="assistant-composer" onSubmit={e=>{e.preventDefault();void send();}}><label className="sr-only" htmlFor="assistant-question">Ask PowerShift</label><textarea id="assistant-question" ref={input} rows={2} maxLength={2000} value={draft} onChange={e=>setDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}} placeholder={selected?'Ask about this site…':'Ask a question or describe your goal…'} disabled={configured===false}/><div><small>AI explanations · source-backed screening</small>{running?<button type="button" aria-label="Stop assistant" onClick={()=>stop()}><Square size={15}/></button>:<button type="submit" aria-label="Send message" disabled={!draft.trim()||busy||configured!==true}><Send size={16}/></button>}</div></form>
 </section>;
}
