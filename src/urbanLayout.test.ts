import {describe,it,expect} from 'vitest';
import {buildLayout,fits} from './siteConcept';
import type {Candidate} from './types';
const c={technology:'solar',capacity_mw:1,surface_type:'parking_canopy',building_height_m:0,geometry:{type:'Polygon',coordinates:[[[-119.88,39.61],[-119.878,39.611],[-119.879,39.612],[-119.881,39.611],[-119.88,39.61]]]}} as Candidate;
describe('urban construction layout',()=>{
 it('groups canopies into complete bays with drive aisles and stays inside the site',()=>{
  const layout=buildLayout(c,3.2);expect(layout.points.length).toBeGreaterThan(32);expect(layout.points.length%32).toBe(0);
  expect(layout.capacityMW).toBeLessThanOrEqual(c.capacity_mw);expect(Math.abs(layout.orientation!)).toBeGreaterThan(.1);
  for(const bay of layout.bays!){expect(bay.indices.length).toBe(32);for(const i of bay.indices)expect(fits(layout.points[i],layout.rings,2+Math.hypot(1.3,2.4)/2)).toBe(true);}
  const angle=layout.orientation!,levels=[...new Set(layout.bays!.map(b=>Math.round((-b.center[0]*Math.sin(angle)+b.center[1]*Math.cos(angle))*1000)/1000))].sort((a,b)=>a-b);
  for(let i=1;i<levels.length;i++)expect(levels[i]-levels[i-1]-layout.bays![0].depth).toBeGreaterThanOrEqual(5.99);
 });
 it('does not build a partial canopy or invent capacity on a tiny site',()=>{
  const layout=buildLayout({...c,capacity_mw:.01},3.2);expect(layout.points).toHaveLength(0);expect(layout.capacityMW).toBe(0);
 });
 it('keeps roof modules within the screening capacity and uses low tilt',()=>{
  const layout=buildLayout({...c,surface_type:'rooftop',building_height_m:12},3.2);
  expect(layout.tilt).toBe(10);expect(layout.capacityMW).toBeLessThanOrEqual(c.capacity_mw);expect(layout.moduleCount).toBe(layout.points.length);
 });
});
