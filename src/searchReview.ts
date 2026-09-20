import type { Candidate, Result } from './types';


export interface ReviewStop {candidate:Candidate;excluded:boolean;label:string;facts:string[]}
export interface SearchReview {id:string;stops:ReviewStop[]}

/** Presentation pacing only; these checks use the returned measurements. */
export function reviewSchedule(stops:ReviewStop[]) {
 let totalMs=0;
 const visits=stops.map((stop,index)=>{
  const hash=[...stop.candidate.site_id].reduce((n,c)=>(n*31+c.charCodeAt(0))>>>0,index);
  const durationMs=[350,420,500,620,760,900][hash%6];
  const startMs=totalMs;totalMs+=durationMs;
  return {startMs,durationMs};
 });
 return {visits,totalMs};
}

export function reviewStop(c:Candidate,excluded=false):ReviewStop {
 const facts:string[]=[];
 if(excluded)facts.push(...(c.exclusion_reasons||[]).slice(0,2));
 else{
  facts.push(c.surface_type?`${Math.round(c.surface_area_m2||0).toLocaleString()} m² mapped ${c.surface_type.replaceAll('_',' ')}`:
   `${c.resource_value.toFixed(1)} ${c.resource_unit}`);
  facts.push(`${c.grid_distance_km.toFixed(1)} km to mapped transmission · ${c.protected_overlap_pct===0?'no protected overlap':`${c.protected_overlap_pct.toFixed(1)}% protected overlap`}`);
 }
 if(c.operating_evidence?.applied)facts.push(`ISD + PUDL: ${(100*(c.operating_evidence.weather_downside_share||0)).toFixed(1)}% historical wind downside`);
 else if(!excluded)facts.push(c.surface_type?'Roof structure / clearance still needs verification':`Mean slope ${c.slope_deg.toFixed(1)}° · parcel availability unverified`);
 return {candidate:c,excluded,label:excluded?(c.exclusion_reasons?.[0]||'Does not meet current requirements'):`${c.score.toFixed(1)} / 100 · passes current filters`,facts};
}

/** Present measured decisions, ending on the top-ranked site. Never invent stops. */
export function buildSearchReview(result:Result):SearchReview {
 const valid=(c:Candidate)=>Number.isFinite(c.longitude)&&Number.isFinite(c.latitude)&&!c.exclusion_reasons?.includes('Outside analysis boundary');
 const candidates=result.candidates.filter(valid);
 const leader=candidates[0];const stops:ReviewStop[]=[];const used=new Set<string>(leader?[leader.site_id]:[]);
 const kind=(c:Candidate)=>c.surface_type||c.technology;
 const alternatives=candidates.filter(c=>!used.has(c.site_id));
 const add=(c:Candidate,excluded=false)=>{if(!used.has(c.site_id)){stops.push(reviewStop(c,excluded));used.add(c.site_id);}};
 // Prefer different technologies/surface types over repeated adjacent rooftops.
 for(const c of alternatives){if(stops.length>=18)break;if(kind(c)!==kind(leader)&&!stops.some(s=>kind(s.candidate)===kind(c)))add(c);}
 for(const c of alternatives){if(stops.length>=18)break;add(c);}
 const rejectedOptions=result.excluded.filter(c=>valid(c)&&!used.has(c.site_id)&&(c.exclusion_reasons?.length||0)>0);
 const rejected=rejectedOptions.find(c=>c.technology==='wind'&&c.operating_evidence?.applied)||rejectedOptions[0];
 if(rejected)add(rejected,true);
 const limit=leader?19:20;
 for(const c of rejectedOptions){if(stops.length>=limit)break;add(c,true);}
 for(const c of alternatives){if(stops.length>=limit)break;add(c);}
 if(leader)stops.push({...reviewStop(leader),label:`Leading option · ${leader.score.toFixed(1)} / 100`});

 return {id:result.run_id,stops};
}
