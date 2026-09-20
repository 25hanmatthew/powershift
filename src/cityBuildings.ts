import type { Candidate, Polygon } from './types';
import type { FillExtrusionLayerSpecification, FilterSpecification } from 'maplibre-gl';

export const CITY_BOUNDS:[number,number,number,number]=[-121.505,38.575,-121.485,38.59];
export function containsPoint(p:number[],polygon:Polygon){
 const inside=(ring:number[][])=>{let yes=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){
  const a=ring[i],b=ring[j];if((a[1]>p[1])!==(b[1]>p[1])&&p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])yes=!yes;
 }return yes;};
 return inside(polygon.coordinates[0])&&!polygon.coordinates.slice(1).some(inside);
}
export const buildingLayer:FillExtrusionLayerSpecification={
 id:'city-buildings',type:'fill-extrusion',source:'city-context','source-layer':'building',minzoom:13,
 filter:['!=',['get','hide_3d'],true],
 paint:{'fill-extrusion-color':['interpolate',['linear'],['coalesce',['get','render_height'],8],0,'#afb8b2',30,'#c7cec8',100,'#e3e6d9'],
  'fill-extrusion-height':['max',['coalesce',['get','render_height'],8],['+',['coalesce',['get','render_min_height'],0],.5]],
  'fill-extrusion-base':['coalesce',['get','render_min_height'],0],
  'fill-extrusion-opacity':.94,'fill-extrusion-vertical-gradient':true}
};
// Hide the existing building/parts only when our selected project supplies its own shell.
// Match OSM IDs where present, then handle independently mapped parts by footprint containment.
export function buildingFilter(features:{id?:string|number;geometry:GeoJSON.Geometry}[],site?:Candidate|null):FilterSpecification {
 const hidden:(string|number)[]=[];
 if(site?.surface_type&&site.surface_type!=='parking_canopy'){
  const osmId=Number(site.id.split('-')[2]);
  for(const feature of features){
   if(feature.id===undefined)continue;
   const polygons=feature.geometry.type==='Polygon'?[feature.geometry.coordinates]:feature.geometry.type==='MultiPolygon'?feature.geometry.coordinates:[];
   const belongs=polygons.some(rings=>{
    const ring=rings[0];const x=ring.map(p=>p[0]),y=ring.map(p=>p[1]);
    return containsPoint([(Math.min(...x)+Math.max(...x))/2,(Math.min(...y)+Math.max(...y))/2],site.geometry);
   });
   if(Math.floor(Number(feature.id)/10)===osmId||belongs)hidden.push(feature.id);
  }
 }
 return ['all',['!=',['get','hide_3d'],true],['!', ['in',['id'],['literal',[...new Set(hidden)]]]]];
}
