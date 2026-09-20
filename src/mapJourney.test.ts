import { describe,it,expect } from 'vitest';
import { searchBounds,surveyStops } from './mapJourney';
import { REGIONS } from './types';
import type { Region,Polygon } from './types';

describe('map search journey',()=>{
 it.each(Object.keys(REGIONS) as Region[])('keeps three distinct stops inside %s',region=>{
  const [w,s,e,n]=REGIONS[region].bounds;const stops=surveyStops(region,null);
  expect(stops).toHaveLength(3);expect(new Set(stops.map(p=>p.join(','))).size).toBe(3);
  for(const [x,y] of stops){expect(x).toBeGreaterThan(w);expect(x).toBeLessThan(e);expect(y).toBeGreaterThan(s);expect(y).toBeLessThan(n);}
 });
 it('does not fly outside a triangular custom boundary',()=>{
  const polygon:Polygon={type:'Polygon',coordinates:[[[-123,37.3],[-117,37.3],[-123,41.5],[-123,37.3]]]};
  expect(searchBounds('california-nevada',polygon)).toEqual(REGIONS['california-nevada'].bounds);
  const stops=surveyStops('california-nevada',polygon);expect(stops.length).toBeGreaterThan(0);
  for(const [x,y] of stops){expect((x+123)/6+(y-37.3)/4.2).toBeLessThan(1);}
 });
 it('does not fly to a custom polygon outside the supported search region',()=>{
  const polygon:Polygon={type:'Polygon',coordinates:[[[0,0],[10,0],[10,.01],[.01,.01],[.01,10],[0,10],[0,0]]]};
  expect(surveyStops('sacramento',polygon)).toEqual([]);
 });
 it('clips custom bounds to the region used by the analysis',()=>{
  const polygon:Polygon={type:'Polygon',coordinates:[[[-125,36],[-120,36],[-120,40],[-125,40],[-125,36]]]};
  expect(searchBounds('california-nevada',polygon)).toEqual([-123,37.3,-120,40]);
 });
});
