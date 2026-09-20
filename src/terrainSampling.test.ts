import {describe,it,expect,vi} from 'vitest';
import type { Map as MapInstance } from 'maplibre-gl';
import {terrainSampler} from './terrainSampling';

describe('batched terrain sampling',()=>{
 it('calculates coverage once, preserves coordinates and refreshes the cache between batches',()=>{
  const getElevation=vi.fn((p:{lng:number;lat:number},zoom:number)=>p.lng+p.lat+zoom);
  const terrain={tileManager:{minzoom:0,maxzoom:12},getElevationForLngLatZoom:getElevation};
  const coveringTiles=vi.fn(()=>[{canonical:{z:10}},{canonical:{z:14}}]);
  const map={terrain,coveringTiles} as unknown as MapInstance;
  const sample=terrainSampler(map);
  expect(sample(-121.82,38.14)).toBeCloseTo(-71.68);
  expect(sample(-121.82,38.14)).toBeCloseTo(-71.68);
  expect(getElevation).toHaveBeenCalledTimes(1);expect(coveringTiles).toHaveBeenCalledTimes(1);
  expect(getElevation.mock.calls[0][1]).toBe(12);
  terrainSampler(map)(-121.82,38.14);expect(getElevation).toHaveBeenCalledTimes(2);
 });
 it('uses the normal API when terrain is disabled or the optimized API is unavailable',()=>{
  const queryTerrainElevation=vi.fn(()=>null);
  const sample=terrainSampler({queryTerrainElevation} as unknown as MapInstance);
  expect(sample(-120,38)).toBeNull();expect(queryTerrainElevation).toHaveBeenCalledWith([-120,38]);
 });
});
