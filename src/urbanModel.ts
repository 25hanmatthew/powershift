import * as THREE from 'three';
import {architecturalMeshes,facadeParts} from './buildingArchitecture';
import type { SiteLayout } from './siteConcept';

export function buildUrbanModel(layout:SiteLayout,elevation:(x:number,z:number)=>number){
 const group=new THREE.Group(),ground=elevation(0,0),height=layout.buildingHeight||0;
 const canopy=layout.surfaceType!=='rooftop',groundParking=layout.surfaceType==='parking_canopy';
 const roofBase=ground+(groundParking?0:height),angle=layout.orientation||0,tilt=(layout.tilt||10)*Math.PI/180;
 const cs=Math.cos(angle),sn=Math.sin(angle);
 const world=(x:number,z:number,center:[number,number]):[number,number]=>[center[0]+x*cs-z*sn,center[1]+x*sn+z*cs];
 // Ground parking retains the aerial imagery and its visible circulation lanes.
 if(!groundParking){
  const shape=new THREE.Shape(layout.rings[0].map(([x,z])=>new THREE.Vector2(x,-z)));
  layout.rings.slice(1).forEach(r=>shape.holes.push(new THREE.Path(r.map(([x,z])=>new THREE.Vector2(x,-z)))));
  const building=new THREE.Mesh(new THREE.ExtrudeGeometry(shape,{depth:height,bevelEnabled:false}),[
   new THREE.MeshStandardMaterial({color:'#949b99',roughness:.96}),new THREE.MeshStandardMaterial({color:'#bbb9b0',roughness:.9})]);
  building.rotation.x=-Math.PI/2;building.position.y=ground;building.castShadow=true;building.receiveShadow=true;group.add(building);
  group.add(architecturalMeshes(facadeParts(layout.rings,ground,height,6000)));
 }
 const canvas=document.createElement('canvas');canvas.width=256;canvas.height=512;const ctx=canvas.getContext('2d')!;
 ctx.fillStyle='#101d2b';ctx.fillRect(0,0,256,512);
 // Low-contrast half-cell pattern, with a thin aluminum edge instead of a bright wire grid.
 for(let r=0;r<12;r++)for(let c=0;c<6;c++){
  ctx.fillStyle=(r+c)%4===0?'#192b3d':'#172638';ctx.fillRect(4+c*41.4,5+r*41.7,40,40.2);
 }
 ctx.strokeStyle='#46515a';ctx.lineWidth=2;ctx.strokeRect(1,1,254,510);
 const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;texture.anisotropy=8;
 const edge=new THREE.MeshStandardMaterial({color:'#303b43',roughness:.6,metalness:.55});
 const glass=new THREE.MeshStandardMaterial({map:texture,roughness:.34,metalness:.15});
 const steel=new THREE.MeshStandardMaterial({color:'#687780',metalness:.65,roughness:.48});
 const panels=new THREE.InstancedMesh(new THREE.BoxGeometry(1.3,.045,2.4),[edge,edge,glass,edge,edge,edge],layout.points.length);
 panels.name='PV modules';panels.userData.equipment={kind:'module'};
 const parts:{w:number;h:number;d:number;x:number;y:number;z:number;tilt?:number}[]=[];
 const transform=new THREE.Object3D();
 for(const bay of layout.bays||[]){
  const corners=[[-bay.width/2,-bay.depth/2],[bay.width/2,-bay.depth/2],[-bay.width/2,bay.depth/2],[bay.width/2,bay.depth/2]].map(([x,z])=>world(x,z,bay.center));
  const base=groundParking?Math.max(...corners.map(([x,z])=>elevation(x,z))):roofBase;
  const middle=base+(canopy?4.85+Math.sin(tilt)*bay.depth/2:.09+Math.sin(tilt)*1.2);
  for(const i of bay.indices){
   const [x,z]=layout.points[i],localZ=-(x-bay.center[0])*sn+(z-bay.center[1])*cs;
   const y=middle-(canopy?localZ*Math.sin(tilt):0);
   transform.rotation.set(tilt,-angle,0,'YXZ');transform.position.set(x,y,z);transform.scale.set(1,1,1);transform.updateMatrix();panels.setMatrixAt(i,transform.matrix);
   if(!canopy){
    for(const dz of [-.8,.8]){const p=world(0,dz,[x,z]);const h=y-base-dz*Math.sin(tilt)-.07;parts.push({w:1.1,h:.08,d:.08,x:p[0],y:y-dz*Math.sin(tilt)-.07,z:p[1]});
     for(const dx of [-.45,.45]){const q=world(dx,dz,[x,z]);parts.push({w:.08,h:Math.max(.1,h),d:.08,x:q[0],y:base+h/2,z:q[1]});}}
   }
  }
  if(canopy){
   // Shared structural bays with four columns and cross beams; no pole under every module.
   for(const x of [-bay.width*.36,bay.width*.36]){
    for(const z of [-bay.depth*.32,bay.depth*.32]){const p=world(x,z,bay.center),floor=groundParking?elevation(...p):base,h=middle-z*Math.sin(tilt)-.25-floor;
     parts.push({w:.18,h,d:.18,x:p[0],y:floor+h/2,z:p[1]});}
    const p=world(x,0,bay.center);parts.push({w:.16,h:.28,d:bay.depth,x:p[0],y:middle-.2,z:p[1],tilt});
   }
   for(const z of [-bay.depth*.32,bay.depth*.32]){const p=world(0,z,bay.center);parts.push({w:bay.width,h:.2,d:.14,x:p[0],y:middle-z*Math.sin(tilt)-.15,z:p[1]});}
  }
 }
 const structure=new THREE.InstancedMesh(new THREE.BoxGeometry(1,1,1),steel,parts.length);structure.name='Shared mounting structure';
 parts.forEach((p,i)=>{transform.position.set(p.x,p.y,p.z);transform.rotation.set(p.tilt||0,-angle,0,'YXZ');transform.scale.set(p.w,p.h,p.d);transform.updateMatrix();structure.setMatrixAt(i,transform.matrix);});
 panels.castShadow=true;panels.receiveShadow=true;structure.castShadow=true;group.add(panels,structure);
 group.traverse(o=>o.frustumCulled=false);
 return {group,rotors:[] as THREE.Group[],dispose(){const geometries=new Set<THREE.BufferGeometry>(),materials=new Set<THREE.Material>();group.traverse(o=>{if(o instanceof THREE.Mesh){geometries.add(o.geometry);(Array.isArray(o.material)?o.material:[o.material]).forEach(m=>materials.add(m));}});geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());texture.dispose();}};
}
