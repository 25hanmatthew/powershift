import { Activity, Database } from 'lucide-react';
import type { Candidate, OperatingPlant, Result } from './types';
import './operating-decisions.css';

function PlantEvidence({plant:p}:{plant:OperatingPlant}) {
 return <details className="operating-plant"><summary><span>{p.name}<small>{p.distance_km.toFixed(1)} km from search center · {p.months}/12 months</small></span><strong>{p.flagged_months}<small>review months</small></strong></summary>
  <p>{p.flagged_months} months reported output more than 10 capacity-factor points below the weather-adjusted estimate. {p.complete?'All 12 months pass the weather-coverage and baseline-history checks.':'Incomplete year; excluded from the ranking factor and investigation queue.'}</p>
  <dl><div><dt>2019–22 seasonal reference</dt><dd>{(p.baseline_mwh/1000).toFixed(2)} GWh</dd></div><div><dt>2024 weather-adjusted estimate</dt><dd>{(p.weather_mwh/1000).toFixed(2)} GWh</dd></div><div><dt>2024 reported generation</dt><dd>{(p.actual_mwh/1000).toFixed(2)} GWh</dd></div></dl>
  <p>Matched ISD stations: {p.stations.map(s=>`${s.name} (${s.distance_km.toFixed(1)} km from plant)`).join('; ')}. Station qualification can vary by month.</p>
 </details>;
}

export function CandidateOperatingNote({candidate:c}:{candidate:Candidate}) {
 const e=c.operating_evidence;
 if(!e)return null;
 return <details className="candidate-operating-note"><summary><Activity size={13}/>{e.applied?`ISD + PUDL: −${Math.max(0,(c.score_before_operating??0)-(c.score??c.score_before_operating??0)).toFixed(2)} score points`:'ISD + PUDL: no score adjustment'}</summary>
  <p>{e.applied?`${e.complete_plants} nearby plants show a mean ${(100*(e.weather_downside_share||0)).toFixed(1)}% historical weather-downside share. Resource score ${e.resource_before.toFixed(1)} → ${e.resource_after.toFixed(1)} before applying your energy-output weight.`:e.available?'Regional wind-risk preference is switched off.':e.reason}</p>
  <p>2024 observations within 100 km of this candidate. This is a regional risk preference, not a validated forecast for this site. Generation and financial estimates retain their physical assumptions.</p>
 </details>;
}

export default function OperatingDecisions({result,enabled,onChange}:{result:Result;enabled:boolean;onChange:(value:boolean)=>void}) {
 const e=result.operating_evidence;
 if(!e)return null;
 const winds=result.candidates.filter(c=>c.technology==='wind');
 const adjusted=winds.filter(c=>c.operating_evidence?.applied);
 return <section className="operating-decisions" aria-label="ISD and PUDL decision evidence">
  <span className="regional-eyebrow"><Database size={12}/> LEARN FROM EXISTING GENERATION</span>
  <h3>{e.investigations.length?'Investigate existing supply alongside new sites':'Historical evidence in this recommendation'}</h3>
  <p>{e.plant_count?`${e.plant_count} studied wind plants within 100 km of this search. ${e.complete_plants} have a complete, quality-qualified 2024 record.`:e.reason}</p>
  {e.investigations.length>0&&<div className="operating-action"><b>Recommended next action</b><p>Request operating records for {e.investigations.map(p=>p.name).join(', ')}. Repeated shortfalls remain after accounting for weather. Check outages, curtailment and reporting before assuming a need for replacement generation.</p><small>Investigation only. No recoverable energy or capacity is credited to your target.</small></div>}
  {e.plant_count>0&&<>
   <div className="operating-policy"><label htmlFor="operating-risk">Account for regional wind downside<small>{adjusted.length?`${adjusted.length} eligible wind scores adjusted`:winds.length?'No qualified adjustment for these wind sites':'No eligible wind sites; solar scores unchanged'}</small></label><input id="operating-risk" type="checkbox" checked={enabled} onChange={ev=>onChange(ev.target.checked)}/></div>
   {adjusted.length>0&&<div className="operating-score-changes">{adjusted.slice(0,3).map(c=><div key={c.id}><span>{c.name}</span><b>{c.score_before_operating?.toFixed(1)} → {c.score.toFixed(1)}</b></div>)}</div>}
   <details className="operating-method"><summary>How these sources change the decision</summary>
    <p>PUDL supplies reported generation, capacity and each plant’s seasonal reference. ISD weather feeds the frozen weather-adjustment model. For each complete plant-year, we sum only months when the weather estimate falls below its seasonal reference, then divide by annual reference generation.</p>
    <p>For each wind candidate, we average that share equally across qualified plants within 100 km. Wind resource score × (1 − downside share), followed by your usual priority weights. At least 3 spatially distinct plants with 12 qualified months are required. Missing evidence applies no bonus or penalty.</p>
    <p>This transparent screening preference has not been validated as a new-site predictor. A single historical year does not establish long-term risk. Solar scores, modeled energy and cash flow are unchanged. Investigations require at least 3 months more than 10 capacity-factor points below the weather model; this is a review threshold, not a fault diagnosis.</p>
    {e.available&&<p>At the search center: {(100*(e.weather_downside_share||0)).toFixed(1)}% mean downside across {e.complete_plants} complete plant-years. Candidate-specific neighborhoods can differ.</p>}
    <div>{e.plants.map(p=><PlantEvidence key={p.id} plant={p}/>)}</div>
    <a href="/research/wind-insights/">Model validation and source methodology →</a>
   </details>
  </>}
 </section>;
}
