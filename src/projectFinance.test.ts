import { describe,it,expect } from 'vitest';
import { calculateFinance,defaultFinance } from './projectFinance';
import { buildLayout,EQUIPMENT,fits } from './siteConcept';
import type { Candidate } from './types';

function site(technology:'solar'|'wind',capacity=100):Candidate {
 return {technology,capacity_mw:capacity,annual_gwh:250,geometry:{type:'Polygon',coordinates:[[[-120.01,38.99],[-119.99,38.99],[-119.99,39.01],[-120.01,39.01],[-120.01,38.99]]]}} as Candidate;
}
describe('dimensioned site layout',()=>{
 it.each(['solar','wind'] as const)('fits %s equipment inside the footprint and under screening capacity',technology=>{
  const c=site(technology),layout=buildLayout(c);
  expect(layout.points.length).toBeGreaterThan(0);expect(layout.capacityMW).toBeLessThanOrEqual(c.capacity_mw);
  const margin=technology==='solar'?20+Math.hypot(26,4.8)/2:105;
  for(const point of layout.points)expect(fits(point,layout.rings,margin)).toBe(true);
  expect(layout.capacityMW).toBeCloseTo(layout.points.length*layout.unitMW);
 });
 it('reconciles PV module rating, DC capacity and AC capacity',()=>{
  const l=buildLayout(site('solar',46));
  expect(l.moduleCount).toBe(l.points.length*40);
  expect(l.capacityMW).toBeCloseTo(l.moduleCount*EQUIPMENT.moduleW/1e6/1.3);
 });
 it('reduces buildable capacity at wider spacing when footprint-limited',()=>{
  expect(buildLayout(site('solar',1000),22).capacityMW).toBeLessThan(buildLayout(site('solar',1000),8).capacityMW);
  expect(buildLayout(site('wind',1000),12,8).capacityMW).toBeLessThan(buildLayout(site('wind',1000),12,4).capacityMW);
 });
 it('does not put equipment in a footprint hole',()=>{
  const c=site('solar');c.geometry.coordinates.push([[-120.004,38.996],[-119.996,38.996],[-119.996,39.004],[-120.004,39.004],[-120.004,38.996]]);
  const l=buildLayout(c);for(const point of l.points)expect(fits(point,l.rings,33.3)).toBe(true);
 });
});
describe('project financial scenario',()=>{
 const input={...defaultFinance('solar'),capexPerKW:1000,omPerKW:10,pricePerMWh:100,discountPct:0,years:10,degradationPct:0,contingencyPct:0,extraMillions:0,curtailmentPct:0};
 it('matches hand-calculated cash flow, NPV and lifetime energy cost',()=>{
  const result=calculateFinance({capacity_mw:10,annual_gwh:20},{capacityMW:5},input);
  expect(result.investment).toBe(5e6);expect(result.firstMWh).toBe(10000);expect(result.annualRevenue).toBe(1e6);expect(result.om).toBe(50000);
  expect(result.npv).toBe(4.25e6);expect(result.payback).toBeCloseTo(5e6/950000);
  expect(result.lcoe).toBeCloseTo((5e6+500000+250000)/100000);
 });
 it('never reports a payback for a losing scenario',()=>{
  const result=calculateFinance({capacity_mw:10,annual_gwh:20},{capacityMW:5},{...input,pricePerMWh:0});
  expect(result.payback).toBeNull();expect(result.npv).toBeLessThan(0);
 });
 it('distinguishes zero upfront investment from a layout with no equipment',()=>{
  const free={...input,capexPerKW:0};
  expect(calculateFinance({capacity_mw:10,annual_gwh:20},{capacityMW:5},free).payback).toBe(0);
  const empty=calculateFinance({capacity_mw:10,annual_gwh:20},{capacityMW:0},input);
  expect(empty.payback).toBeNull();expect(empty.lcoe).toBeNull();expect(empty.firstMWh).toBe(0);
 });
 it('uses additional curtailment, degradation and a real discount rate consistently',()=>{
  const result=calculateFinance({capacity_mw:10,annual_gwh:20},{capacityMW:5},{...input,years:2,curtailmentPct:10,degradationPct:1,discountPct:5});
  expect(result.firstMWh).toBe(9000);expect(result.cashflows[2].energyMWh).toBe(8910);
  expect(result.npv).toBeCloseTo(-5e6+(900000-50000)/1.05+(891000-50000-250000)/1.05**2);
 });
 it('uses buildable capacity, not requested capacity, for both costs and energy',()=>{
  const a=calculateFinance({capacity_mw:10,annual_gwh:20},{capacityMW:5},input),b=calculateFinance({capacity_mw:10,annual_gwh:20},{capacityMW:10},input);
  expect(a.investment*2).toBe(b.investment);expect(a.firstMWh*2).toBe(b.firstMWh);
 });
 it('rejects nonfinite assumptions and fractional project lives',()=>{
  for(const change of [{years:5.5},{pricePerMWh:NaN},{curtailmentPct:110}])expect(()=>calculateFinance({capacity_mw:10,annual_gwh:20},{capacityMW:5},{...input,...change})).toThrow();
 });
});

describe('urban rooftop scenarios',()=>{
 it('fits individual modules around a courtyard and caps capacity',()=>{
  const c=site('solar',.1);c.surface_type='rooftop';c.building_height_m=12;
  c.geometry.coordinates=[[[-120.0005,38.9995],[-119.9995,38.9995],[-119.9995,39.0005],[-120.0005,39.0005],[-120.0005,38.9995]],[[-120.0001,38.9999],[-119.9999,38.9999],[-119.9999,39.0001],[-120.0001,39.0001],[-120.0001,38.9999]]];
  const l=buildLayout(c,3.2);expect(l.moduleCount).toBe(200);expect(l.buildingHeight).toBe(12);
  expect(l.capacityMW).toBeCloseTo(l.moduleCount*.00065/1.3);
  for(const point of l.points)expect(fits(point,l.rings,2+Math.hypot(1.3,2.4)/2)).toBe(true);
 });
 it('uses distinct urban cost scenarios without changing ground solar defaults',()=>{
  expect(defaultFinance('solar','rooftop').capexPerKW).toBe(3000);
  expect(defaultFinance('solar','parking_deck').capexPerKW).toBe(4000);
  expect(defaultFinance('solar').capexPerKW).toBe(1865);
 });
});
