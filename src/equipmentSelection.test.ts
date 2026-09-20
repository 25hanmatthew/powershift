import {describe,it,expect} from 'vitest';
import * as THREE from 'three';
import {pickEquipment,resolveEquipmentHit} from './equipmentSelection';
import type {SiteLayout} from './siteConcept';

const layout={points:[[0,0],[10,0]],technology:'solar'} as SiteLayout;
const hit=(object:THREE.Object3D,point:THREE.Vector3,instanceId?:number)=>({object,point,instanceId,distance:1} as THREE.Intersection);

describe('individual equipment selection',()=>{
 it('selects the correct module and transform from an instanced, rotated roof array',()=>{
  const modules=new THREE.InstancedMesh(new THREE.BoxGeometry(1.3,.045,2.4),new THREE.MeshBasicMaterial(),2);
  modules.userData.equipment={kind:'module'};
  const transform=new THREE.Matrix4().makeRotationY(.7);transform.setPosition(10,12,3);modules.setMatrixAt(1,transform);
  const result=resolveEquipmentHit(hit(modules,new THREE.Vector3(10,12,3),1),layout)!;
  expect(result.id).toBe('solar-1');expect(result.position).toEqual([10,12,3]);expect(result.tableIndex).toBeUndefined();
  expect(result.matrix[0]).toBeCloseTo(Math.cos(.7));
 });
 it('resolves distinct panels at both ends of a tilted rural table',()=>{
  const tables=new THREE.InstancedMesh(new THREE.BoxGeometry(26,.16,4.8),new THREE.MeshBasicMaterial(),2);
  tables.userData.equipment={kind:'table'};
  const transform=new THREE.Matrix4().makeRotationX(.4);transform.setPosition(10,4,-8);tables.setMatrixAt(1,transform);
  const first=resolveEquipmentHit(hit(tables,new THREE.Vector3(-12.9,.08,-2.3).applyMatrix4(transform),1),layout)!;
  const last=resolveEquipmentHit(hit(tables,new THREE.Vector3(12.9,.08,2.3).applyMatrix4(transform),1),layout)!;
  expect(first.index).toBe(40);expect(last.index).toBe(79);expect(last.tableIndex).toBe(1);
  const center=new THREE.Vector3(12.35,0,1.2).applyMatrix4(transform);
  last.position.forEach((v,i)=>expect(v).toBeCloseTo(center.toArray()[i]));
 });
 it('maps a moving turbine blade to its parent turbine',()=>{
  const rotor=new THREE.Group();rotor.userData.equipment={kind:'turbine',index:1};rotor.userData.equipmentBase=40;
  const blade=new THREE.Mesh(new THREE.BoxGeometry(1,80,1));rotor.add(blade);rotor.rotation.z=1.8;rotor.updateMatrixWorld(true);
  const result=resolveEquipmentHit(hit(blade,new THREE.Vector3()),layout)!;
  expect(result.id).toBe('wind-1');expect(result.position).toEqual([10,140,0]);expect(result.size).toEqual([170,200,24]);
 });
 it('uses the combined projection to pick visible equipment and respects occluding geometry',()=>{
  const camera=new THREE.PerspectiveCamera(60,1,.1,100);camera.updateProjectionMatrix();
  const group=new THREE.Group(),panel=new THREE.Mesh(new THREE.BoxGeometry(2,2,1),new THREE.MeshBasicMaterial());
  panel.position.z=-6;panel.userData.equipment={kind:'module',index:0};group.add(panel);
  const pick=()=>pickEquipment(group,camera.projectionMatrixInverse,{x:200,y:200},{width:400,height:400},layout);
  expect(pick()?.id).toBe('solar-0');
  expect(pickEquipment(group,camera.projectionMatrixInverse,{x:0,y:0},{width:400,height:400},layout)).toBeNull();
  const building=new THREE.Mesh(new THREE.BoxGeometry(3,3,1),new THREE.MeshBasicMaterial());building.position.z=-3;group.add(building);
  expect(pick()).toBeNull();building.userData.ignoreEquipmentPicking=true;expect(pick()?.id).toBe('solar-0');
 });
});
