import { describe,it,expect } from 'vitest';
import { siteLngLat } from './mapSiteLayer';
import { buildLayout } from './siteConcept';
import type { Candidate } from './types';

describe('site placement on the geographic map',()=>{
 const candidate={technology:'solar',capacity_mw:30,geometry:{type:'Polygon',coordinates:[[[-122.01,37.99],[-121.99,37.99],[-121.99,38.01],[-122.01,38.01],[-122.01,37.99]]]}} as Candidate;
 const layout=buildLayout(candidate);
 it('maps the local origin and opposite footprint corners to the real coordinates',()=>{
  expect(siteLngLat(layout,0,0)).toEqual([-122,38]);
  const nw=siteLngLat(layout,-layout.width/2,-layout.depth/2),se=siteLngLat(layout,layout.width/2,layout.depth/2);
  expect(nw[0]).toBeCloseTo(-122.01,7);expect(nw[1]).toBeCloseTo(38.01,7);
  expect(se[0]).toBeCloseTo(-121.99,7);expect(se[1]).toBeCloseTo(37.99,7);
 });
 it('preserves east/south orientation and puts all equipment within the geographic bounds',()=>{
  for(const [x,z] of layout.points){const [lng,lat]=siteLngLat(layout,x,z);
   expect(lng).toBeGreaterThan(layout.bounds[0]);expect(lng).toBeLessThan(layout.bounds[2]);
   expect(lat).toBeGreaterThan(layout.bounds[1]);expect(lat).toBeLessThan(layout.bounds[3]);
  }
  expect(siteLngLat(layout,100,0)[0]).toBeGreaterThan(-122);
  expect(siteLngLat(layout,0,100)[1]).toBeLessThan(38);
 });
});
