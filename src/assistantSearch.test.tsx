// @vitest-environment jsdom
import {act} from 'react';
import {createRoot, type Root} from 'react-dom/client';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import App from './App';
import AssistantChat from './AssistantChat';
import {REGIONAL_DEMO_PLAN} from './types';

vi.mock('./MapView',()=>({default:({resultKey}:{resultKey:string})=><div data-testid="map" data-run={resultKey}/> }));

let host:HTMLDivElement,root:Root;
beforeEach(()=>{
 vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
 HTMLElement.prototype.scrollIntoView=vi.fn();
 host=document.createElement('div');document.body.append(host);root=createRoot(host);
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();vi.unstubAllGlobals();});

function stream(){
 let writer:ReadableStreamDefaultController<Uint8Array>;
 const response=new Response(new ReadableStream<Uint8Array>({start(c){writer=c;}}));
 return {response,emit:(value:unknown)=>writer.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(value)}\n\n`)),close:()=>writer.close()};
}
const reply={type:'done',answer:'Checked the search evidence.',sites:[],sources:[],followups:[],result:null,usage:{input_tokens:1,output_tokens:1}};
function services(chat:()=>Response,configured=true){
 const fetcher=vi.fn(async(url:string)=>{
  if(url==='/api/assistant/chat')return chat();
  if(url==='/api/health')return Response.json({services:{openai:configured}});
  if(url==='/api/datasets')return Response.json([]);
  if(url==='/api/intelligence')return Response.json({status:'idle',model_loaded:false});
  throw new Error(`Unexpected request: ${url}`);
 });
 vi.stubGlobal('fetch',fetcher);return fetcher;
}

it('routes initial and main-bar searches through one conversation and applies the agent result',async()=>{
 const first=stream(),second=stream();let calls=0;
 const fetcher=services(()=>++calls===1?first.response:second.response);
 await act(async()=>root.render(<App/>));
 expect(calls).toBe(1);
 expect(host.textContent).toContain(REGIONAL_DEMO_PLAN.query);
 expect((host.querySelector('.search-submit') as HTMLButtonElement).disabled).toBe(true);
 const body=()=>JSON.parse((fetcher.mock.calls.filter(([url])=>url==='/api/assistant/chat').at(-1) as unknown as [string,RequestInit])[1].body as string);
 expect(body().message).toBe(REGIONAL_DEMO_PLAN.query);
 expect(body().intent).toBe('search');
 expect(body().view.weights).toEqual(REGIONAL_DEMO_PLAN.weights);
 await act(async()=>{first.emit(reply);first.close();});
 expect((host.querySelector('.search-submit') as HTMLButtonElement).disabled).toBe(false);
 const input=host.querySelector('input[aria-label="Energy planning request"]') as HTMLInputElement;
 await act(async()=>{
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,'Find 5 solar rooftops in Davis, CA');
  input.dispatchEvent(new Event('input',{bubbles:true}));
 });
 await act(async()=>host.querySelector('.map-search')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
 expect(calls).toBe(2);
 expect(body().message).toBe('Find 5 solar rooftops in Davis, CA');
 expect(body().history).toHaveLength(2);
 expect(host.querySelector('#assistant-tab')?.getAttribute('aria-selected')).toBe('true');
 await act(async()=>{second.emit({...reply,result:{run_id:'agent-search',plan:REGIONAL_DEMO_PLAN,candidates:[],excluded:[],selected_ids:[],portfolio:{},datasets:[],verification:[],telemetry:[],mode:'live',explanation:'No eligible sites.'}});second.close();});
 expect(host.querySelector('[data-testid="map"]')?.getAttribute('data-run')).toBe('agent-search');
 expect(fetcher.mock.calls.some(([url])=>url==='/api/city/search')).toBe(false);
});

it('shows a missing-key error without bypassing the agent',async()=>{
 const fetcher=services(()=>{throw new Error('Must not call chat');},false);
 await act(async()=>root.render(<App/>));
 expect(host.textContent).toContain('Connect OpenAI on the server to run this search through the assistant.');
 expect((host.querySelector('#assistant-question') as HTMLTextAreaElement).value).toBe(REGIONAL_DEMO_PLAN.query);
 expect(fetcher.mock.calls.every(([url])=>!url.includes('/search')&&!url.includes('/chat'))).toBe(true);
});

it('stops a submitted search and ignores a late map result',async()=>{
 const pending=stream(),onResult=vi.fn(),onRunningChange=vi.fn();
 const fetcher=vi.fn(async()=>pending.response);vi.stubGlobal('fetch',fetcher);
 await act(async()=>root.render(<AssistantChat result={null} plan={REGIONAL_DEMO_PLAN} selected={null} configured={true} onResult={onResult} onSelect={()=>{}} incomingRequest={{id:1,message:'Find solar in Davis, CA'}} onRunningChange={onRunningChange}/>));
 expect(onRunningChange).toHaveBeenLastCalledWith(true);
 await act(async()=>(host.querySelector('[aria-label="Stop assistant"]') as HTMLButtonElement).click());
 const signal=(fetcher.mock.calls[0] as unknown as [string,RequestInit])[1].signal;
 expect(signal?.aborted).toBe(true);
 await act(async()=>{pending.emit({...reply,result:{run_id:'late'}});pending.close();});
 expect(onResult).not.toHaveBeenCalled();
 expect(onRunningChange).toHaveBeenLastCalledWith(false);
 expect(host.textContent).toContain('Not completed');
 expect(fetcher).toHaveBeenCalledTimes(1);
});

it('consumes a queued request once, but allows a deliberate repeat with a new ID',async()=>{
 const fetcher=vi.fn(async()=>new Response(`data: ${JSON.stringify(reply)}\n\n`));vi.stubGlobal('fetch',fetcher);
 const render=(id:number,configured:boolean|null)=>root.render(<AssistantChat result={null} plan={REGIONAL_DEMO_PLAN} selected={null} configured={configured} onResult={()=>{}} onSelect={()=>{}} incomingRequest={{id,message:'Find solar in Davis, CA'}}/>);
 await act(async()=>render(1,null));expect(fetcher).not.toHaveBeenCalled();
 await act(async()=>render(1,true));expect(fetcher).toHaveBeenCalledTimes(1);
 await act(async()=>render(1,true));expect(fetcher).toHaveBeenCalledTimes(1);
 await act(async()=>render(2,true));expect(fetcher).toHaveBeenCalledTimes(2);
});
