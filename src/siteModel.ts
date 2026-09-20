import * as THREE from 'three';
import { EQUIPMENT,fits } from './siteConcept';
import type { SiteLayout } from './siteConcept';
import { buildUrbanModel } from './urbanModel';

export function buildSiteModel(layout:SiteLayout,elevation:(x:number,z:number)=>number){
 if(layout.surfaceType)return buildUrbanModel(layout,elevation);
 const scene=new THREE.Group();
 const hasCompound=fits([0,0],layout.rings,45);
 const compoundHeights=[[-38,-28],[38,-28],[38,28],[-38,28],[0,0]].map(([x,z])=>elevation(x,z));
 const compoundBase=Math.max(...compoundHeights),compoundDepth=Math.max(1,compoundBase-Math.min(...compoundHeights)+1);
 const surface=(x:number,z:number)=>hasCompound&&Math.abs(x)<40&&Math.abs(z)<30?compoundBase:elevation(x,z);
  const steel=new THREE.MeshStandardMaterial({color:'#d5e3de',metalness:.45,roughness:.4});
  const concrete=new THREE.MeshStandardMaterial({color:'#b8b6a7',roughness:1});
  const green=new THREE.MeshStandardMaterial({color:'#577663',roughness:.65});
  const roadMat=new THREE.MeshStandardMaterial({color:'#b9af95',roughness:1});
  const panelCanvas=document.createElement('canvas');panelCanvas.width=2048;panelCanvas.height=384;
  const context=panelCanvas.getContext('2d')!;context.fillStyle='#a2b7bf';context.fillRect(0,0,2048,384);
  for(let row=0;row<2;row++)for(let col=0;col<20;col++){
   const x=col*102.4+3,y=row*192+3;context.fillStyle=(row+col)%3?'#153d5a':'#194965';context.fillRect(x,y,97,186);
   context.strokeStyle='#7598b455';context.lineWidth=1;
   for(let line=1;line<7;line++){context.beginPath();context.moveTo(x,y+line*26);context.lineTo(x+97,y+line*26);context.stroke();}
   context.beginPath();context.moveTo(x+48,y);context.lineTo(x+48,y+186);context.stroke();
  }
  const panelTexture=new THREE.CanvasTexture(panelCanvas);panelTexture.colorSpace=THREE.SRGBColorSpace;panelTexture.anisotropy=8;
  const panelMat=new THREE.MeshStandardMaterial({map:panelTexture,metalness:.1,roughness:.55,color:'#e9f4ff'});
  const box=(width:number,height:number,depth:number,x:number,y:number,z:number,material:THREE.Material)=>{
   const mesh=new THREE.Mesh(new THREE.BoxGeometry(width,height,depth),material);mesh.position.set(x,y+surface(x,z),z);mesh.castShadow=true;mesh.receiveShadow=true;scene.add(mesh);return mesh;
  };
  // Two planned access corridors, clipped to the footprint; not existing roads.
  const roadSegments:{x:number;z:number;vertical:boolean}[]=[];
  for(let x=-layout.width/2+10;x<layout.width/2-10;x+=10)if(fits([x,0],layout.rings,6))roadSegments.push({x,z:0,vertical:false});
  for(let z=-layout.depth/2+10;z<layout.depth/2-10;z+=10)if(fits([0,z],layout.rings,6))roadSegments.push({x:0,z,vertical:true});
  const roadVertices:number[]=[];
  roadSegments.forEach(r=>{
   const corners=r.vertical?[[r.x-3,r.z-5],[r.x+3,r.z-5],[r.x+3,r.z+5],[r.x-3,r.z+5]]:[[r.x-5,r.z-3],[r.x+5,r.z-3],[r.x+5,r.z+3],[r.x-5,r.z+3]];
   for(const i of [0,2,1,0,3,2]){const [x,z]=corners[i];roadVertices.push(x,elevation(x,z)+.1,z);}
  });
  const roadGeometry=new THREE.BufferGeometry();roadGeometry.setAttribute('position',new THREE.Float32BufferAttribute(roadVertices,3));roadGeometry.computeVertexNormals();
  const roads=new THREE.Mesh(roadGeometry,roadMat);roads.receiveShadow=true;scene.add(roads);
  const rotors:THREE.Group[]=[];
  if(layout.technology==='solar'){
   const count=layout.points.length;
   const panels=new THREE.InstancedMesh(new THREE.BoxGeometry(EQUIPMENT.tableWidth,.16,EQUIPMENT.tableDepth),panelMat,count);
   panels.userData.equipment={kind:'table'};
   const frames=new THREE.InstancedMesh(new THREE.BoxGeometry(EQUIPMENT.tableWidth+.15,.2,EQUIPMENT.tableDepth+.1),steel,count);
   const posts=new THREE.InstancedMesh(new THREE.CylinderGeometry(.1,.12,2.6,5),steel,count*4);
   const transform=new THREE.Object3D();
   layout.points.forEach(([x,z],i)=>{
    const base=elevation(x,z),roll=Math.atan2(elevation(x+13,z)-elevation(x-13,z),26);
    transform.scale.set(1,1,1);transform.position.set(x,2.9+base,z);transform.rotation.set(THREE.MathUtils.degToRad(EQUIPMENT.tilt),0,roll);transform.updateMatrix();panels.setMatrixAt(i,transform.matrix);
    transform.position.y=2.77+elevation(x,z);transform.updateMatrix();frames.setMatrixAt(i,transform.matrix);
    transform.rotation.set(0,0,0);
    [-10,-3.3,3.3,10].forEach((offset,j)=>{const ground=elevation(x+offset,z),length=Math.max(.5,base+2.65+Math.sin(roll)*offset-ground);transform.scale.set(1,length/2.6,1);transform.position.set(x+offset,ground+length/2,z);transform.updateMatrix();posts.setMatrixAt(i*4+j,transform.matrix);});
   });
   panels.castShadow=true;panels.receiveShadow=true;frames.castShadow=true;posts.castShadow=true;scene.add(frames,panels,posts);
   layout.points.filter((_,i)=>i%120===0).forEach(([x,z])=>{box(3,2.2,1.8,x,1.1,z+4.6,green);box(4,.15,3,x,.1,z+4.6,concrete);});
  }else{
   const towerGeometry=new THREE.CylinderGeometry(1.9,3.4,EQUIPMENT.hubHeight,14);
   const bladeShape=new THREE.Shape();bladeShape.moveTo(-1.2,3);bladeShape.bezierCurveTo(-6,13,-4,35,-.2,85);bladeShape.bezierCurveTo(1.3,61,3.5,25,2,7);bladeShape.lineTo(1,3);bladeShape.closePath();
   const bladeGeometry=new THREE.ExtrudeGeometry(bladeShape,{depth:.65,bevelEnabled:true,bevelThickness:.18,bevelSize:.15,bevelSegments:1,steps:1});
   const bladeMat=new THREE.MeshStandardMaterial({color:'#eef2e9',roughness:.42,metalness:.12});
   layout.points.forEach(([x,z],i)=>{
    const tag=(object:THREE.Object3D)=>{object.userData.equipment={kind:'turbine',index:i};object.userData.equipmentBase=elevation(x,z);return object;};
    const foundation=new THREE.Mesh(new THREE.CylinderGeometry(10,11,1,24),concrete);foundation.position.set(x,.5+elevation(x,z),z);foundation.receiveShadow=true;tag(foundation);scene.add(foundation);
    const tower=new THREE.Mesh(towerGeometry,steel);tower.position.set(x,EQUIPMENT.hubHeight/2+elevation(x,z),z);tower.castShadow=true;tag(tower);scene.add(tower);
    tag(box(5.5,5.4,13,x,EQUIPMENT.hubHeight,z,bladeMat));
    const rotor=new THREE.Group();rotor.position.set(x,EQUIPMENT.hubHeight+elevation(x,z),z+7.5);rotor.rotation.z=i*.63;
    const hub=new THREE.Mesh(new THREE.SphereGeometry(2.7,12,12),bladeMat);rotor.add(hub);
    for(let j=0;j<3;j++){const blade=new THREE.Mesh(bladeGeometry,bladeMat);blade.rotation.z=j*Math.PI*2/3;blade.castShadow=true;rotor.add(blade);}
    tag(rotor);scene.add(rotor);rotors.push(rotor);
    box(22,.1,16,x,.1,z,roadMat);
   });
  }
  if(hasCompound){
   box(75,compoundDepth,55,0,.3-compoundDepth/2,0,concrete);box(15,5,9,-18,2.8,-12,green);
   box(15.5,.3,9.5,-18,5.45,-12,steel);box(1.3,2.5,.15,-20,1.55,-7.45,steel);
   for(let x=-23;x<-12;x+=2)box(1.2,1,.12,x,3.3,-7.44,steel);
   for(let i=0;i<3;i++){
    box(7,4,6,7+i*10,2.5,-9,green);
    for(let fin=0;fin<12;fin++){box(.13,3.5,.7,4+i*10+fin*.55,2.5,-12.2,steel);box(.13,3.5,.7,4+i*10+fin*.55,2.5,-5.8,steel);}
    for(let j=0;j<3;j++){const insulator=new THREE.Mesh(new THREE.CylinderGeometry(.3,.5,2.4,12),steel);insulator.position.set(5+i*10+j*1.8,5.3+compoundBase,-9);scene.add(insulator);}
   }
   for(let x=-35;x<=35;x+=10){box(.12,3,.12,x,1.8,-25,steel);box(.12,3,.12,x,1.8,25,steel);}
   for(let z=-25;z<=25;z+=10){box(.12,3,.12,-35,1.8,z,steel);box(.12,3,.12,35,1.8,z,steel);}
   box(70,.07,.07,0,3,-25,steel);box(70,.07,.07,0,3,25,steel);box(.07,.07,50,-35,3,0,steel);box(.07,.07,50,35,3,0,steel);
  }

 scene.traverse(object=>{object.frustumCulled=false;});
 return {group:scene,rotors,dispose(){
  const geometries=new Set<THREE.BufferGeometry>(),materials=new Set<THREE.Material>();
  scene.traverse(object=>{if(object instanceof THREE.Mesh){geometries.add(object.geometry);(Array.isArray(object.material)?object.material:[object.material]).forEach(m=>materials.add(m));}});
  geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());panelTexture.dispose();
 }};
}
