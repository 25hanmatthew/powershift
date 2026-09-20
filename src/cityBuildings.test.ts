import {describe,it,expect} from 'vitest';
import {buildingFilter,containsPoint} from './cityBuildings';
import {CITY_PLAN} from './types';import type {Candidate,Polygon} from './types';
const geometry:Polygon={type:'Polygon',coordinates:[[[0,0],[10,0],[10,10],[0,10],[0,0]],[[4,4],[6,4],[6,6],[4,6],[4,4]]]};
const feature=(id:number,w:number,s:number,e:number,n:number)=>({id,geometry:{type:'Polygon' as const,coordinates:[[[w,s],[e,s],[e,n],[w,n],[w,s]]]}});
describe('surrounding city buildings',()=>{
 it('keeps courtyards and adjacent buildings outside the selected shell',()=>{
  expect(containsPoint([2,2],geometry)).toBe(true);expect(containsPoint([5,5],geometry)).toBe(false);expect(containsPoint([12,2],geometry)).toBe(false);
 });
 it('replaces the selected building and its parts without hiding neighboring context',()=>{
  const site={id:'osm-way-123-0',surface_type:'rooftop',geometry} as Candidate;
  const filter=JSON.stringify(buildingFilter([feature(1230,0,0,10,10),feature(9870,1,1,3,3),feature(5550,11,0,15,5),feature(6660,4.2,4.2,5.8,5.8)],site));
  expect(filter).toContain('[1230,9870]');expect(filter).not.toContain('5550');expect(filter).not.toContain('6660');
 });
 it('retains context buildings when a parking canopy or no project is shown',()=>{
  const features=[feature(1230,1,1,3,3)];
  expect(buildingFilter(features,{id:'osm-way-123-0',surface_type:'parking_canopy',geometry} as Candidate)).toEqual(buildingFilter(features));
 });
 it('starts with a live Sacramento solar target on existing surfaces',()=>{
  expect(CITY_PLAN.region).toBe('sacramento');expect(CITY_PLAN.mode).toBe('live');expect(CITY_PLAN.target_mw).toBe(1);expect(CITY_PLAN.constraints.zero_new_land).toBe(true);
 });
});
