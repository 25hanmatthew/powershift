import type { Candidate, Plan, Result, Verification, WeightKey } from './types';

export function rerank(source: Result, plan: Plan): Result {
 const keys = Object.keys(plan.weights) as WeightKey[];
 const total = keys.reduce((n,k)=>n+plan.weights[k],0);
 const candidates: Candidate[] = [], excluded: Candidate[] = [];
 for(const raw of [...source.candidates,...source.excluded]) {
  const c = {...raw,components:{...raw.components}}; const reasons: string[] = [];
  c.base_resource_score=raw.base_resource_score??raw.components.resource;c.base_annual_gwh=raw.base_annual_gwh??raw.annual_gwh;
  c.components.resource=c.base_resource_score;c.annual_gwh=c.base_annual_gwh;
  if(!plan.historical_intelligence){c.ml_enabled=false;c.ml_confidence=null;c.ml_fallback_reason=null;}
  if(plan.historical_intelligence&&c.ml_enabled&&['HIGH','MEDIUM'].includes(c.ml_confidence||'')&&c.technology==='wind'&&c.ml_corrected_expected_cf!=null&&Number.isFinite(c.ml_corrected_expected_cf)&&c.ml_corrected_expected_cf>=0&&c.ml_corrected_expected_cf<=1){
   c.components.resource=Math.round(Math.max(0,Math.min(100,(c.ml_corrected_expected_cf/.075-1)*20))*100)/100;
   c.annual_gwh=Math.round(c.capacity_mw*8760*c.ml_corrected_expected_cf/100)/10;
  }
  if(raw.exclusion_reasons?.includes('Outside analysis boundary')) reasons.push('Outside analysis boundary');
  if(plan.technology!=='auto' && c.technology!==plan.technology) reasons.push('Technology filter');
  if(plan.constraints.exclude_protected && c.protected_overlap_pct>0) reasons.push('Protected land');
  if(c.grid_distance_km>plan.constraints.max_grid_km) reasons.push('Grid distance');
  if(!c.surface_type&&c.slope_deg>plan.constraints.max_slope_deg) reasons.push('Slope limit');
  if(plan.constraints.zero_new_land && (!c.developed_surface_verified || c.technology!=='solar')) reasons.push('No verified developed footprint');
  if(c.capacity_mw<plan.constraints.min_capacity_mw) reasons.push('Minimum capacity');
  if(c.excluded_land_cover) reasons.push('Incompatible land cover');
  if(reasons.length) {excluded.push({...c,selected:false,exclusion_reasons:reasons});continue;}
  c.score = Math.round(keys.reduce((n,k)=>n+c.components[k]*(total?plan.weights[k]:1),0)/(total||keys.length)*100)/100;
  c.exclusion_reasons=[];candidates.push(c);
 }
 candidates.sort((a,b)=>b.score-a.score || a.id.localeCompare(b.id));
 let capacity=0;const sites=new Set<string>(); const selected_ids: string[]=[];
 candidates.forEach((c,i)=>{
  c.rank=i+1;c.selected=false;
  if(capacity<plan.target_mw && !sites.has(c.site_id)) {c.selected=true;capacity+=c.capacity_mw;sites.add(c.site_id);selected_ids.push(c.id);}
 });
 const chosen=candidates.filter(c=>c.selected);
 const sum=(fn:(c:Candidate)=>number)=>chosen.reduce((n,c)=>n+fn(c),0);
 const precision=chosen.some(c=>c.surface_type)?1000:10;const round=(n:number)=>Math.round(n*precision)/precision;
 const portfolio={capacity_mw:round(capacity),target_mw:plan.target_mw,target_met:capacity>=plan.target_mw,shortfall_mw:round(Math.max(0,plan.target_mw-capacity)),site_count:chosen.length,
 solar_mw:round(sum(c=>c.technology==='solar'?c.capacity_mw:0)),wind_mw:round(sum(c=>c.technology==='wind'?c.capacity_mw:0)),annual_gwh:round(sum(c=>c.annual_gwh)),mean_score:round(sum(c=>c.score)/(chosen.length||1)),mean_grid_km:round(sum(c=>c.grid_distance_km)/(chosen.length||1))};
 const verification:Verification[]=[
  {label:'Capacity target',passed:portfolio.target_met,detail:`${portfolio.capacity_mw} / ${plan.target_mw} MW`},
  {label:'Geographic boundary',passed:true,detail:'All selected footprints inside analysis boundary'},
  {label:'Protected land',passed:chosen.every(c=>c.protected_overlap_pct===0),detail:plan.constraints.exclude_protected?'Hard exclusion enabled':'Exclusion disabled by user'},
  {label:'Grid proximity',passed:chosen.every(c=>c.grid_distance_km<=plan.constraints.max_grid_km),detail:`Within ${plan.constraints.max_grid_km} km`},
  chosen.some(c=>c.surface_type)?{label:'Structural suitability',passed:false,detail:'Roof load, pitch, shading and parking clearance need site verification'}:{label:'Terrain',passed:chosen.every(c=>c.slope_deg<=plan.constraints.max_slope_deg),detail:`Slope ≤ ${plan.constraints.max_slope_deg}°`},
 ];
 return {...source,candidates,excluded,selected_ids,portfolio,verification,plan};
}
