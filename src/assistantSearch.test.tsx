// @vitest-environment jsdom
import {act} from 'react';
import {createRoot, type Root} from 'react-dom/client';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import App from './App';
import AssistantChat from './AssistantChat';
import {REGIONAL_DEMO_PLAN} from './types';
import type {Candidate,Result} from './types';
import type {SearchReview} from './searchReview';

vi.mock('./MapView',()=>({default:({resultKey,region,candidates,excluded,review,onReviewComplete}:{region:string;candidates:Candidate[];excluded:Candidate[];resultKey:string;review:SearchReview|null;onReviewComplete:()=>void})=><div data-testid="map" data-run={resultKey} data-region={region} data-sites={candidates.length+excluded.length} data-review-count={review?.stops.length||0}><button onClick={onReviewComplete}>Complete test tour</button></div> }));

let host:HTMLDivElement,root:Root;
beforeEach(()=>{
 vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
 vi.stubGlobal('matchMedia',()=>({matches:false}));
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

it('routes initial and assistant searches through one conversation and applies the agent result',async()=>{
 const first=stream(),second=stream();let calls=0;
 const fetcher=services(()=>++calls===1?first.response:second.response);
 await act(async()=>root.render(<App/>));
 expect(calls).toBe(1);
 expect(host.textContent).toContain(REGIONAL_DEMO_PLAN.query);
 expect(host.querySelector('.map-search')).toBeNull();
 const body=()=>JSON.parse((fetcher.mock.calls.filter(([url])=>url==='/api/assistant/chat').at(-1) as unknown as [string,RequestInit])[1].body as string);
 expect(body().message).toBe(REGIONAL_DEMO_PLAN.query);
 expect(body().intent).toBe('search');
 expect(body().view.weights).toEqual(REGIONAL_DEMO_PLAN.weights);
 await act(async()=>{first.emit(reply);first.close();});
 expect(host.querySelector('[aria-label="Stop assistant"]')).toBeNull();
 const input=host.querySelector('#assistant-question') as HTMLTextAreaElement;
 await act(async()=>{
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(input,'Find 5 solar rooftops in Davis, CA');
  input.dispatchEvent(new Event('input',{bubbles:true}));
 });
 await act(async()=>host.querySelector('.assistant-composer')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
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

it('keeps the map review active after the assistant finishes, then restores the shortlist',async()=>{
 const pending=stream(),next=stream();let requests=0;const fetcher=services(()=>++requests===1?pending.response:next.response);
 await act(async()=>root.render(<App/>));
 const candidate={id:'roof',site_id:'roof',name:'Measured rooftop',technology:'solar',surface_type:'rooftop',surface_area_m2:500,longitude:-121.4,latitude:38.6,geometry:{type:'Polygon',coordinates:[]},capacity_mw:1,resource_value:5.5,resource_unit:'kWh/m²/day',grid_distance_km:1,slope_deg:2,protected_overlap_pct:0,developed_pct:100,natural_pct:0,developed_surface_verified:true,annual_gwh:2,components:{resource:80,environment:90,grid:90,buildability:80,reuse:100},confidence:'High',provenance:'computed',evidence_ids:[],limitations:[],vintage:'Fixture',land_cover:'developed',score:85,rank:1,selected:true} as Candidate;
 const result={run_id:'reviewed-agent-search',plan:REGIONAL_DEMO_PLAN,candidates:[candidate],excluded:[],selected_ids:['roof'],portfolio:{},datasets:[],verification:[],telemetry:[],mode:'live',explanation:'Measured results',operating_evidence:{plant_count:0,complete_plants:0,investigations:[],reason:'No local observations'}} as unknown as Result;
 await act(async()=>{pending.emit({...reply,result});pending.close();});
 expect(host.querySelector('[data-testid="map"]')?.getAttribute('data-review-count')).toBe('1');
 expect(host.querySelector('#assistant-tab')?.getAttribute('aria-selected')).toBe('true');
 expect(host.querySelector('.map-search')).toBeNull();
 expect(host.querySelector('#assistant-view .assistant-site-review')?.textContent).toContain('Measured rooftop');
 expect(host.querySelector('.assistant-conversation')?.textContent).not.toContain(reply.answer);
 await act(async()=>Array.from(host.querySelectorAll('button')).find(b=>b.textContent==='Complete test tour')!.click());
 expect(host.querySelector('[aria-label="Stop assistant"]')).toBeNull();
 expect(host.querySelector('#assistant-view .assistant-site-review')).toBeNull();
 expect(host.querySelector('#sites-tab')?.getAttribute('aria-selected')).toBe('true');
 expect(host.querySelector('.site-cards')?.textContent).toContain('Measured rooftop');
 expect(host.querySelector('#candidate-shortlist .operating-decisions')).toBeNull();
 expect(host.querySelector('.assistant-conversation .operating-decisions')?.textContent).toContain('No local observations');
 expect(host.querySelector('.assistant-conversation')?.textContent).toContain(reply.answer);
 expect(host.querySelector('[data-testid="map"]')?.getAttribute('data-run')).toBe('reviewed-agent-search');
 await act(async()=>(host.querySelector('[aria-label="Start a new conversation"]') as HTMLButtonElement).click());
 expect(host.querySelector('[data-testid="map"]')?.getAttribute('data-run')).toBe('');
 expect(host.querySelector('[data-testid="map"]')?.getAttribute('data-region')).toBe('us');
 expect(host.querySelector('[data-testid="map"]')?.getAttribute('data-sites')).toBe('0');
 expect(host.querySelector('.site-cards')).toBeNull();
 expect(host.textContent).not.toContain('Measured rooftop');
 expect(host.querySelector('.assistant-conversation')?.textContent).not.toContain(reply.answer);
 expect(host.querySelector('#assistant-tab')?.getAttribute('aria-selected')).toBe('true');
 expect(requests).toBe(1);
 const input=host.querySelector('#assistant-question') as HTMLTextAreaElement;
 await act(async()=>{
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(input,'Find solar in Boston, MA');
  input.dispatchEvent(new Event('input',{bubbles:true}));
 });
 await act(async()=>host.querySelector('.assistant-composer')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
 const sent=JSON.parse((fetcher.mock.calls.filter(([url])=>url==='/api/assistant/chat').at(-1) as unknown as [string,RequestInit])[1].body as string);
 expect(sent.run_id).toBeNull();expect(sent.selected_site_id).toBeNull();expect(sent.history).toEqual([]);
 await act(async()=>{next.emit(reply);next.close();});

});
