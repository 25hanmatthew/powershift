import { REGIONS } from './types';
import type { Polygon, Region } from './types';

export function searchBounds(region:Region,polygon:Polygon|null):[number,number,number,number] {
 const ring=polygon?.coordinates[0];
 if(!ring?.length)return REGIONS[region].bounds;
 const [rw,rs,re,rn]=REGIONS[region].bounds;
 const bounds:[number,number,number,number]=[Math.max(rw,Math.min(...ring.map(p=>p[0]))),Math.max(rs,Math.min(...ring.map(p=>p[1]))),Math.min(re,Math.max(...ring.map(p=>p[0]))),Math.min(rn,Math.max(...ring.map(p=>p[1])))];
 return bounds[0]<bounds[2]&&bounds[1]<bounds[3]?bounds:REGIONS[region].bounds;
}
function inside(point:number[],ring:number[][]){
 let yes=false;
 for(let i=0,j=ring.length-1;i<ring.length;j=i++){
  const [x,y]=ring[i],[a,b]=ring[j];
  if((y>point[1])!==(b>point[1])&&point[0]<(a-x)*(point[1]-y)/(b-y)+x)yes=!yes;
 }
 return yes;
}
export function surveyStops(region:Region,polygon:Polygon|null):[number,number][] {
 const [w,s,e,n]=searchBounds(region,polygon);
 const options=[[.24,.65],[.7,.72],[.58,.3],[.35,.4],[.5,.5],[.8,.2],[.2,.2]];
 const points=options.map(([x,y])=>[w+(e-w)*x,s+(n-s)*y] as [number,number]);
 return points.filter(point=>!polygon||inside(point,polygon.coordinates[0])).slice(0,3);
}
