import { describe,it,expect } from 'vitest';
import { bridge,flagged,fleetTotals,distanceKm } from './fleet';
import type { FleetMonth,FleetPlant } from './fleet';
const row={month:1,actual_mwh:200,baseline_mwh:500,weather_mwh:250,actual_cf:.2,baseline_cf:.5,weather_cf:.25} as FleetMonth;
describe('historical fleet interpretation',()=>{
 it('reconciles seasonal expectation, weather adjustment and residual to reported energy',()=>{
  const b=bridge(row);expect(b.baseline+b.weather+b.residual).toBe(b.actual);
  expect(b.weather).toBe(-250);expect(b.residual).toBe(-50);
 });
 it('changes review flags when weather accounts for a historical shortfall',()=>{
  expect(flagged(row,false)).toBe(true);expect(flagged(row,true)).toBe(false);
 });
 it('does not turn missing observations into zero generation or extra flags',()=>{
  const plants=[{months:[row]},{months:[]}] as FleetPlant[];
  const total=fleetTotals(plants,1,false);
  expect(total).toEqual({observed:1,missing:1,flagged:1,actual_mwh:200,flagged_gap_mwh:300});
  expect(fleetTotals(plants,2).observed).toBe(0);
 });
 it('keeps the local operating-fleet scope geographically bounded',()=>{
  expect(distanceKm([-121.49,38.58],[-121.769,38.116])).toBeCloseTo(57,0);
  expect(distanceKm([-121.49,38.58],[-100,35])).toBeGreaterThan(100);
 });
});
