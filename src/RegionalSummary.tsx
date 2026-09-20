import { Database, ScanLine } from 'lucide-react';
import type { Result } from './types';

export default function RegionalSummary({result}:{result:Result}) {
 const summary=result.urban_summary!;
 const recommendation=summary.recommended_approach;
 return <section className="regional-summary" aria-label="Regional energy comparison">
  <span className="regional-eyebrow">THE REGIONAL PICTURE</span>
  <h3>{recommendation?`Start with ${recommendation.toLowerCase()}`:'No approaches passed screening'}</h3>
  <p>{recommendation?'These approaches have the highest-scoring sites under the initial priorities. The shortlist balances the viable alternatives.':'Review the exclusions before choosing a site.'}</p>
  <div className="regional-counts"><span><ScanLine size={13}/>{summary.urban_surfaces} surfaces + {summary.land_cells} land cells</span><span><Database size={13}/>{result.datasets.length} data layers</span></div>
  <div className="approach-list">{summary.comparison?.map(row=><details key={row.id} className={row.eligible?'':'approach-unavailable'}>
   <summary><span>{row.label}<small>{row.eligible} / {row.screened} pass</small></span><strong>{row.best_score==null?'None':row.best_score.toFixed(1)}<small>{row.best_score==null?'eligible':'best score'}</small></strong></summary>
   <p>{row.screened===0?'No mapped opportunities were available in this sample.':row.exclusions.length?row.exclusions.map(e=>`${e.count} ${e.reason.toLowerCase()}`).join('; ')+'. A site can fail multiple checks.':'All sampled options passed the screening filters.'}</p>
  </details>)}</div>
  <details className="regional-method"><summary>Scope, sources & assumptions</summary><p>{result.explanation}</p><p>{result.datasets.map(d=>d.name).join(' · ')}</p><p>{summary.omitted.toLocaleString()} smaller mapped surfaces were outside the 500-surface screening cap. Urban solar uses one regional irradiance estimate; land cells use location-specific resource data. The land sample is not continuous coverage. Wind and night-light averages cover 2024; land cover is from 2021.</p><p>Site economics, adjustable layouts and builders are available after selecting a location. Cost and financing are not part of the screening score.</p><p><a href="/research/wind-insights/">Explore the ISD + PUDL wind research →</a><br/>Historical plant-performance findings, separate from this site ranking.</p></details>
 </section>;
}
