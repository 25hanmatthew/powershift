import * as THREE from 'three';
import { EQUIPMENT } from './siteConcept';
import type { SiteLayout } from './siteConcept';

export interface EquipmentSelection {
 id:string;kind:'solar'|'wind';index:number;tableIndex?:number;
 position:[number,number,number];matrix:number[];size:[number,number,number];
}

// Tables are rendered in one draw call; resolve their 20 × 2 module grid in local space.
export function resolveEquipmentHit(hit:THREE.Intersection,layout:SiteLayout):EquipmentSelection|null {
 let object:THREE.Object3D|null=hit.object;
 while(object&&!object.userData.equipment)object=object.parent;
 if(!object)return null;
 const tag=object.userData.equipment as {kind:'module'|'table'|'turbine';index?:number};
 const index=tag.index??hit.instanceId;
 if(index===undefined||!layout.points[index])return null;
 let matrix=object.matrixWorld.clone();
 if(object instanceof THREE.InstancedMesh){const instance=new THREE.Matrix4();object.getMatrixAt(index,instance);matrix.multiply(instance);}
 let moduleIndex=index,tableIndex:number|undefined,size:[number,number,number]=[1.3,.1,2.4];
 if(tag.kind==='table'){
  const local=hit.point.clone().applyMatrix4(matrix.clone().invert());
  const col=Math.max(0,Math.min(19,Math.floor((local.x+EQUIPMENT.tableWidth/2)/1.3)));
  const row=Math.max(0,Math.min(1,Math.floor((local.z+EQUIPMENT.tableDepth/2)/2.4)));
  matrix.multiply(new THREE.Matrix4().makeTranslation(-12.35+col*1.3,0,-1.2+row*2.4));
  moduleIndex=index*EQUIPMENT.modulesPerTable+row*20+col;tableIndex=index;size=[1.3,.2,2.4];
 }else if(tag.kind==='turbine'){
  // All tower, hub and blade clicks refer to the same turbine.
  matrix=new THREE.Matrix4().makeTranslation(layout.points[index][0],taggedBase(object)+EQUIPMENT.hubHeight/2+EQUIPMENT.rotorDiameter/4,layout.points[index][1]);
  size=[EQUIPMENT.rotorDiameter,EQUIPMENT.hubHeight+EQUIPMENT.rotorDiameter/2,24];
 }
 const position=new THREE.Vector3().setFromMatrixPosition(matrix).toArray() as [number,number,number];
 const kind=tag.kind==='turbine'?'wind':'solar';
 return {id:`${kind}-${moduleIndex}`,kind,index:moduleIndex,tableIndex,position,matrix:matrix.toArray(),size};
}

function taggedBase(object:THREE.Object3D){return Number(object.userData.equipmentBase)||0;}

export function pickEquipment(group:THREE.Group,inverseProjection:THREE.Matrix4,point:{x:number;y:number},viewport:{width:number;height:number},layout:SiteLayout){
 if(!viewport.width||!viewport.height)return null;
 const x=point.x/viewport.width*2-1,y=1-point.y/viewport.height*2;
 const near=new THREE.Vector3(x,y,-1).applyMatrix4(inverseProjection);
 const far=new THREE.Vector3(x,y,1).applyMatrix4(inverseProjection);
 const ray=new THREE.Raycaster(near,far.sub(near).normalize());
 group.updateMatrixWorld(true);
 // Respect building/structure occlusion, but ignore the transparent shadow receiver.
 const hit=ray.intersectObject(group,true).find(h=>!h.object.userData.ignoreEquipmentPicking);
 return hit?resolveEquipmentHit(hit,layout):null;
}
