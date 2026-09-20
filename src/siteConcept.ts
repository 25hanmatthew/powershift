import type { Candidate } from './types';

export interface SiteLayout {
 surfaceType?:Candidate['surface_type'];buildingHeight?:number;orientation?:number;tilt?:number;bays?:{center:[number,number];width:number;depth:number;indices:number[]}[];
 technology:'solar'|'wind';points:[number,number][];rings:[number,number][][];
 width:number;depth:number;areaHa:number;capacityMW:number;requestedMW:number;
 unitMW:number;moduleCount:number;rowPitch:number;spacingX:number;spacingZ:number;
 bounds:[number,number,number,number];center:[number,number];
}
export const EQUIPMENT={moduleW:650,modulesPerTable:40,tableWidth:26,tableDepth:4.8,tilt:25,dcAc:1.3,turbineMW:6,rotorDiameter:170,hubHeight:115};
function inside(p:[number,number],ring:[number,number][]){
 let value=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){
  const [x,y]=ring[i],[a,b]=ring[j];if((y>p[1])!==(b>p[1])&&p[0]<(a-x)*(p[1]-y)/(b-y)+x)value=!value;
 }return value;
}
function edgeDistance(p:[number,number],a:[number,number],b:[number,number]){
 const dx=b[0]-a[0],dy=b[1]-a[1];const t=Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy||1)));
 return Math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy);
}
export function fits(p:[number,number],rings:[number,number][][],clearance:number){
 if(!inside(p,rings[0])||rings.slice(1).some(r=>inside(p,r)))return false;
 return rings.every(r=>r.every((a,i)=>edgeDistance(p,a,r[(i+1)%r.length])>=clearance));
}
export function buildLayout(candidate:Candidate,rowPitch=12,windSpacing=5):SiteLayout {
 const coordinates=candidate.geometry.coordinates;
 const outer=coordinates[0];const bounds:[number,number,number,number]=[Math.min(...outer.map(p=>p[0])),Math.min(...outer.map(p=>p[1])),Math.max(...outer.map(p=>p[0])),Math.max(...outer.map(p=>p[1]))];
 const center:[number,number]=[(bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2];
 const scaleX=111320*Math.cos(center[1]*Math.PI/180),scaleZ=111320;
 const rings=coordinates.map(r=>r.map(p=>[(p[0]-center[0])*scaleX,-(p[1]-center[1])*scaleZ] as [number,number]));
 const width=(bounds[2]-bounds[0])*scaleX,depth=(bounds[3]-bounds[1])*scaleZ;
 const ringArea=(r:[number,number][])=>Math.abs(r.reduce((n,p,i)=>{const q=r[(i+1)%r.length];return n+p[0]*q[1]-q[0]*p[1];},0))/2;
 const areaHa=(ringArea(rings[0])-rings.slice(1).reduce((n,r)=>n+ringArea(r),0))/10000;
 const solar=candidate.technology==='solar';const urban=Boolean(candidate.surface_type);
 if(urban)return buildUrbanLayout(candidate,rings,bounds,center,width,depth,areaHa,rowPitch);
 const unitMW=urban?.0005:solar?EQUIPMENT.moduleW*EQUIPMENT.modulesPerTable/1e6/EQUIPMENT.dcAc:EQUIPMENT.turbineMW;
 const spacingX=urban?1.6:solar?30:EQUIPMENT.rotorDiameter*windSpacing;
 const spacingZ=urban?Math.max(2.8,rowPitch):solar?Math.max(8,rowPitch):EQUIPMENT.rotorDiameter*(windSpacing+2);
 const margin=urban?2+Math.hypot(1.3,2.4)/2:solar?20+Math.hypot(EQUIPMENT.tableWidth,EQUIPMENT.tableDepth)/2:EQUIPMENT.rotorDiameter/2+20;
 const possible:[number,number][]=[];
 // Whole units only: capacity is never inflated beyond the site's screening estimate.
 const wanted=Math.max(0,Math.floor(candidate.capacity_mw/unitMW+1e-8));
 if(width>0&&depth>0&&width*depth<1e9){
  for(let z=-depth/2+margin;z<depth/2-margin;z+=spacingZ){
   for(let x=-width/2+margin;x<width/2-margin;x+=spacingX){
    const p:[number,number]=[x,z];
    if(!urban&&solar&&(Math.abs(x)<EQUIPMENT.tableWidth/2+4||Math.abs(z)<EQUIPMENT.tableDepth/2+4))continue;
    if(!urban&&Math.abs(x)<60&&Math.abs(z)<50)continue; // Central infrastructure reservation.
    if(fits(p,rings,margin))possible.push(p);
   }
  }
 }
 // Compact construction blocks around the site's center, deterministic across renders.
 possible.sort((a,b)=>Math.max(Math.abs(a[0]),Math.abs(a[1]))-Math.max(Math.abs(b[0]),Math.abs(b[1]))||a[1]-b[1]||a[0]-b[0]);
 const points=possible.slice(0,Math.min(wanted,20000));
 return {technology:candidate.technology,points,rings,width,depth,areaHa,capacityMW:points.length*unitMW,requestedMW:candidate.capacity_mw,
         unitMW,moduleCount:urban?points.length:solar?points.length*EQUIPMENT.modulesPerTable:0,surfaceType:candidate.surface_type,buildingHeight:candidate.building_height_m,rowPitch:spacingZ,spacingX,spacingZ,bounds,center};
}

// Arrays follow the longest mapped edge; aisle and mounting dimensions are concept assumptions.
function buildUrbanLayout(c:Candidate,rings:[number,number][][],bounds:SiteLayout['bounds'],center:[number,number],width:number,depth:number,areaHa:number,rowPitch:number):SiteLayout {
 const canopy=c.surface_type!=='rooftop';
 const edges=rings[0].slice(1).map((p,i)=>({dx:p[0]-rings[0][i][0],dz:p[1]-rings[0][i][1]}));
 const edge=edges.sort((a,b)=>Math.hypot(b.dx,b.dz)-Math.hypot(a.dx,a.dz))[0];
 const angle=edge?Math.atan2(edge.dz,edge.dx):0,cs=Math.cos(angle),sn=Math.sin(angle);
 const world=(x:number,z:number):[number,number]=>[x*cs-z*sn,x*sn+z*cs];
 const local=rings[0].map(([x,z])=>[x*cs+z*sn,-x*sn+z*cs]);
 const minX=Math.min(...local.map(p=>p[0])),maxX=Math.max(...local.map(p=>p[0]));
 const minZ=Math.min(...local.map(p=>p[1])),maxZ=Math.max(...local.map(p=>p[1]));
 const cols=canopy?8:6,rows=canopy?4:2,px=1.34,pz=canopy?2.44:Math.max(2.65,rowPitch);
 const bw=(cols-1)*px+1.3,bd=(rows-1)*pz+2.4;
 const aisle=canopy?6:1.5,stepX=bw+1.5,stepZ=bd+aisle;
 const wanted=Math.floor(c.capacity_mw/.0005+1e-8),points:[number,number][]=[],bays:NonNullable<SiteLayout['bays']>=[];
 const blocks:{x:number;z:number}[]=[];
 for(let z=minZ+bd/2+3.5;z<maxZ-bd/2-3.5;z+=stepZ)
  for(let x=minX+bw/2+3.5;x<maxX-bw/2-3.5;x+=stepX)blocks.push({x,z});
 blocks.sort((a,b)=>Math.hypot(a.x,a.z)-Math.hypot(b.x,b.z));
 for(const b of blocks){
  const modules:[number,number][]=[];
  for(let r=0;r<rows;r++)for(let col=0;col<cols;col++)modules.push(world(b.x+(col-(cols-1)/2)*px,b.z+(r-(rows-1)/2)*pz));
  if(!modules.every(p=>fits(p,rings,2+Math.hypot(1.3,2.4)/2)))continue;
  if(canopy&&points.length+modules.length>wanted)continue;
  const indices:number[]=[];
  for(const p of modules){if(points.length>=Math.min(wanted,20000))break;indices.push(points.length);points.push(p);}
  if(indices.length)bays.push({center:world(b.x,b.z),width:bw,depth:bd,indices});
  if(points.length>=Math.min(wanted,20000))break;
 }
 return {technology:'solar',surfaceType:c.surface_type,buildingHeight:c.building_height_m,orientation:angle,tilt:canopy?5:10,bays,
  points,rings,width,depth,areaHa,capacityMW:points.length*.0005,requestedMW:c.capacity_mw,unitMW:.0005,moduleCount:points.length,
  rowPitch:pz,spacingX:px,spacingZ:pz,bounds,center};
}
