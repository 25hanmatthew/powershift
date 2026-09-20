import { describe, it, expect } from 'vitest';
import { rerank } from './ranking';
import { DEFAULT_PLAN } from './types';
import type { Candidate, Result } from './types';

const fixture=(id:string,overrides:Partial<Candidate>={}):Candidate=>({id,site_id:id,name:id,technology:'solar',longitude:-120,latitude:39,geometry:{type:'Polygon',coordinates:[]},capacity_mw:80,resource_value:5.8,resource_unit:'kWh/m²/day',grid_distance_km:3,slope_deg:4,protected_overlap_pct:0,developed_pct:10,natural_pct:20,developed_surface_verified:false,annual_gwh:130,components:{resource:80,environment:90,grid:75,buildability:84,reuse:10},confidence:'Demonstration',provenance:'synthetic',evidence_ids:[],limitations:[],vintage:'Fixture',land_cover:'fixture',score:0,rank:0,selected:false,...overrides});
const source=(candidates:Candidate[])=>({candidates,excluded:[],selected_ids:[],plan:DEFAULT_PLAN} as unknown as Result);

describe('instant ranking',()=>{
 it('preserves regional resource and overlapping-surface exclusions when filters change',()=>{
  const input=source([fixture('safe'),fixture('overlap',{screening_reasons:['Overlaps a screened urban surface']}),fixture('calm',{technology:'wind',screening_reasons:['Mean wind below 5.8 m/s']})]);
  const result=rerank(input,{...DEFAULT_PLAN,constraints:{...DEFAULT_PLAN.constraints,exclude_protected:false,max_grid_km:100}});
  expect(result.candidates.map(c=>c.id)).toEqual(['safe']);
  expect(result.excluded).toHaveLength(2);
  expect(result.portfolio.capacity_mw).toBe(80);
 });
 it('keeps legacy results usable with missing or null ML fields',()=>{
  const legacy=fixture('legacy');const nullable=fixture('legacy',{ml_enabled:false,ml_corrected_expected_cf:null,ml_confidence:null});
  const a=rerank(source([legacy]),DEFAULT_PLAN);const b=rerank(source([nullable]),{...DEFAULT_PLAN,historical_intelligence:true});
  expect(a.portfolio).toEqual(b.portfolio);expect(a.selected_ids).toEqual(b.selected_ids);
 });
 it('restores the exact base ranking and generation when historical intelligence is off',()=>{
  const wind=fixture('wind',{technology:'wind',ml_enabled:true,ml_confidence:'MEDIUM',ml_corrected_expected_cf:.1});
  const solar=fixture('solar',{components:{...wind.components,resource:60}});
  const plan={...DEFAULT_PLAN,target_mw:80,weights:{resource:100,environment:0,grid:0,buildability:0,reuse:0}};
  const original=rerank(source([wind,solar]),plan);
  const adjusted=rerank(source([wind,solar]),{...plan,historical_intelligence:true});
  expect(original.selected_ids).toEqual(['wind']);expect(adjusted.selected_ids).toEqual(['solar']);
  const restored=rerank(adjusted,plan);
  expect(restored.portfolio).toEqual(original.portfolio);expect(restored.selected_ids).toEqual(original.selected_ids);
  expect(restored.candidates.find(c=>c.id==='wind')?.ml_enabled).toBe(false);
 });
 it('cannot use low-confidence ML to improve ranking or bypass protected land',()=>{
  const low=fixture('low',{technology:'wind',ml_enabled:true,ml_confidence:'LOW',ml_corrected_expected_cf:.99});
  const protectedSite=fixture('protected',{technology:'wind',protected_overlap_pct:1,ml_enabled:true,ml_confidence:'HIGH',ml_corrected_expected_cf:.99});
  const result=rerank(source([low,protectedSite]),{...DEFAULT_PLAN,historical_intelligence:true});
  expect(result.candidates[0].components.resource).toBe(low.components.resource);
  expect(result.candidates[0].annual_gwh).toBe(low.annual_gwh);
  expect(result.excluded[0].exclusion_reasons).toContain('Protected land');
 });
 it('excludes protected footprints before weighting',()=>{
  const result=rerank(source([fixture('safe'),fixture('protected',{protected_overlap_pct:1})]),DEFAULT_PLAN);
  expect(result.candidates.map(c=>c.id)).toEqual(['safe']);expect(result.excluded[0].exclusion_reasons).toContain('Protected land');
 });
 it('does not count two technologies on the same footprint',()=>{
  const result=rerank(source([fixture('solar'),fixture('wind',{site_id:'solar',technology:'wind'})]),DEFAULT_PLAN);
  expect(result.portfolio.capacity_mw).toBe(80);expect(result.portfolio.shortfall_mw).toBe(170);
 });
 it('allows changing an existing hard constraint without losing physical data',()=>{
  const first=rerank(source([fixture('a'),fixture('b',{grid_distance_km:27})]),DEFAULT_PLAN);
  const second=rerank(first,{...DEFAULT_PLAN,constraints:{...DEFAULT_PLAN.constraints,max_grid_km:50}});
  expect(first.candidates).toHaveLength(1);expect(second.candidates).toHaveLength(2);expect(second.candidates.find(c=>c.id==='b')?.resource_value).toBe(5.8);
 });
 it('reranks physical measurements without mutating the cached input',()=>{
  const input=source([fixture('resource',{components:{resource:100,environment:20,grid:80,buildability:80,reuse:0}}),fixture('environment')]);
  const before=JSON.stringify(input);
  const result=rerank(input,{...DEFAULT_PLAN,weights:{resource:100,environment:0,grid:0,buildability:0,reuse:0}});
  expect(result.candidates[0].id).toBe('resource');expect(JSON.stringify(input)).toBe(before);
 });
 it('handles all-zero weights and capacity shortfalls',()=>{
  const result=rerank(source([fixture('a')]),{...DEFAULT_PLAN,weights:{resource:0,environment:0,grid:0,buildability:0,reuse:0}});
  expect(Number.isFinite(result.candidates[0].score)).toBe(true);expect(result.portfolio.target_met).toBe(false);
 });
 it('preserves geographic exclusions when priorities change',()=>{
  const input=source([]);input.excluded=[fixture('outside',{exclusion_reasons:['Outside analysis boundary']})];
  expect(rerank(input,DEFAULT_PLAN).candidates).toHaveLength(0);
 });
 it('never treats a built-up pixel as a verified rooftop',()=>{
  const result=rerank(source([fixture('built',{developed_pct:100})]),{...DEFAULT_PLAN,constraints:{...DEFAULT_PLAN.constraints,zero_new_land:true}});
  expect(result.portfolio.capacity_mw).toBe(0);
 });
});
