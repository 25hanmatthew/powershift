import {describe,it,expect} from 'vitest';
import {facadeParts,architecturalMeshes,disposeArchitecture} from './buildingArchitecture';
import * as THREE from 'three';
const square=[[0,0],[20,0],[20,20],[0,20],[0,0]];
describe('architectural detail geometry',()=>{
 it('places glazing outside walls and keeps roof trim above the shell',()=>{
  const parts=facadeParts([square],10,10);
  const glass=parts.filter(p=>p.glass);
  expect(glass.length).toBeGreaterThan(0);
  expect(glass.every(p=>p.x<0||p.x>20||p.z<0||p.z>20)).toBe(true);
  expect(glass.every(p=>p.y>10&&p.y<20)).toBe(true);
  expect(parts.some(p=>!p.glass&&p.y>20)).toBe(true);
 });
 it('handles reversed footprints and courtyard walls without filling the courtyard',()=>{
  const courtyard=[[6,6],[14,6],[14,14],[6,14],[6,6]];
  const parts=facadeParts([[...square].reverse(),courtyard],0,8);
  expect(parts.filter(p=>p.glass&&p.x>6&&p.x<14&&p.z>6&&p.z<14).length).toBeGreaterThan(0);
  expect(parts.every(p=>p.d<.3)).toBe(true);
 });
 it('caps geometry and batches all details into two instanced draw calls',()=>{
  const parts=facadeParts([square],0,200,120);
  expect(parts.length).toBe(120);
  const group=architecturalMeshes(parts);
  expect(group.children.length).toBe(2);
  expect(group.children.every(m=>m instanceof THREE.InstancedMesh)).toBe(true);
  expect(facadeParts([square],0,NaN)).toEqual([]);
  disposeArchitecture(group);
 });
});
