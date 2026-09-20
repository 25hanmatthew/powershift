import { describe,it,expect } from 'vitest';
import { buildSearchReview,reviewStop } from './searchReview';
import type { Candidate,Result } from './types';

const site=(id:string,extra:Partial<Candidate>={}):Candidate=>({id,site_id:id,name:id,longitude:-121.4,latitude:38.6,technology:'solar',surface_type:'rooftop',surface_area_m2:500,grid_distance_km:2,protected_overlap_pct:0,resource_value:5.5,resource_unit:'kWh/m²/day',slope_deg:2,score:80,exclusion_reasons:[],...extra} as Candidate);
const result=(candidates:Candidate[],excluded:Candidate[]=[])=>({run_id:'current-search',candidates,excluded} as Result);
describe('measured search review',()=>{
 it('shows different viable approaches, a real rejection, then the actual leader',()=>{
  const tour=buildSearchReview(result([site('winner',{surface_type:'parking_canopy',score:90}),site('other-parking',{surface_type:'parking_canopy'}),site('roof'),site('land',{surface_type:undefined})],[site('protected',{exclusion_reasons:['Protected land'],protected_overlap_pct:3})]));
  expect(tour.stops.map(s=>s.candidate.id)).toEqual(['roof','land','other-parking','protected','winner']);
  expect(tour.stops[3].excluded).toBe(true);expect(tour.stops[3].facts).toContain('Protected land');
  expect(tour.stops[4].label).toBe('Leading option · 90.0 / 100');
 });
 it('visits up to twenty distinct locations while keeping the leader last',()=>{
  const options=Array.from({length:30},(_,i)=>site(`site-${i}`));
  const tour=buildSearchReview(result(options,[site('rejected',{exclusion_reasons:['Protected land']})]));
  expect(tour.stops).toHaveLength(20);
  expect(new Set(tour.stops.map(s=>s.candidate.site_id)).size).toBe(20);
  expect(tour.stops.at(-1)?.candidate.id).toBe('site-0');
  expect(tour.stops.some(s=>s.excluded)).toBe(true);
 });
 it('fills the tour from real screened sites when the shortlist is small',()=>{
  const rejected=Array.from({length:30},(_,i)=>site(`excluded-${i}`,{exclusion_reasons:['Grid distance']}));
  const tour=buildSearchReview(result([site('winner'),site('runner-up')],rejected));
  expect(tour.stops).toHaveLength(20);
  expect(tour.stops.filter(s=>s.excluded)).toHaveLength(18);
  expect(tour.stops.at(-1)?.candidate.id).toBe('winner');
  expect(buildSearchReview(result([],rejected)).stops).toHaveLength(20);
  expect(buildSearchReview(result(Array.from({length:30},(_,i)=>site(`eligible-${i}`)))) .stops).toHaveLength(20);
 });
 it('never shows an out-of-boundary site or counts alternate technologies twice',()=>{
  const tour=buildSearchReview(result([site('winner'),site('wind-on-winner',{site_id:'winner',technology:'wind'}),site('second')],[site('outside',{exclusion_reasons:['Outside analysis boundary']})]));
  expect(tour.stops.map(s=>s.candidate.id)).toEqual(['second','winner']);
 });
 it('handles no matches and all-excluded results without inventing a winner',()=>{
  expect(buildSearchReview(result([])).stops).toEqual([]);
  const tour=buildSearchReview(result([],[site('blocked',{exclusion_reasons:['Grid distance']})]));
  expect(tour.stops).toHaveLength(1);expect(tour.stops[0].excluded).toBe(true);
  expect(tour.stops[0].label).not.toContain('Leading');
 });
 it('uses actual source metrics and preserves screening limitations',()=>{
  const stop=reviewStop(site('roof'));
  expect(stop.facts.some(f=>f.includes('2.0 km'))).toBe(true);
  expect(stop.facts.some(f=>f.includes('still needs verification'))).toBe(true);
  expect(stop.facts.join(' ')).not.toContain('engineered');
 });
 it('keeps a later search confined to its own returned candidates',()=>{
  buildSearchReview(result([site('Sacramento')]));
  const b=site('Boston',{longitude:-71.06,latitude:42.36});
  expect(buildSearchReview(result([b])).stops.map(s=>s.candidate)).toEqual([b]);
 });
});
