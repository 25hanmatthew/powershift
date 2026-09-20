import { LngLat } from 'maplibre-gl';
import type { Map as MapInstance } from 'maplibre-gl';

// Match MapLibre's queryTerrainElevation algorithm, calculating visible tile coverage
// once per batch instead of once for every panel support, road vertex and shadow vertex.
export function terrainSampler(map:MapInstance){
 const terrain=map.terrain;
 if(!terrain?.getElevationForLngLatZoom)return (lng:number,lat:number)=>map.queryTerrainElevation([lng,lat]);
 const {maxzoom,minzoom}=terrain.tileManager;
 const options={maxzoom,minzoom,tileSize:512,terrain};
 const tiles=map.coveringTiles(options);
 const zoom=tiles.reduce((max,tile)=>Math.max(max,Math.min(tile.canonical.z,maxzoom)),0);
 const cache=new Map<string,number>();
 return (lng:number,lat:number)=>{
  const key=`${lng},${lat}`;let value=cache.get(key);
  if(value===undefined){value=terrain.getElevationForLngLatZoom(new LngLat(lng,lat),zoom);cache.set(key,value);}
  return value;
 };
}
