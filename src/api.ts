import type { Plan, Result } from './types';
export async function request<T>(path:string,options?:RequestInit):Promise<T> {
 const response=await fetch(path,options);
 if(!response.ok) {const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:`Request failed (${response.status}). Check your inputs and server connection.`);}
 return response.json() as Promise<T>;
}
export async function runAnalysis(plan:Plan,onProgress:(stage:number,detail:string)=>void,onResult:(result:Result)=>void,onError:(message:string)=>void) {
 const {run_id}=await request<{run_id:string}>('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(plan)});
 const events=new EventSource(`/api/runs/${run_id}/events`);
 events.onmessage=({data})=>{
  const event=JSON.parse(data);
  if(event.type==='progress') onProgress(event.stage,event.detail);
  if(event.type==='result') {events.close();onResult(event.data);}
  if(event.type==='error') {events.close();onError(event.message);}
 };
 events.onerror=()=>{events.close();onError('The analysis connection was interrupted. Your previous result is still available.');};
 return ()=>events.close();
}
