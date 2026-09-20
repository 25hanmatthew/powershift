import {describe,it,expect,vi} from 'vitest';
import * as THREE from 'three';
import type {Map as MapInstance} from 'maplibre-gl';
import type {SiteLayout} from './siteConcept';
import {createSiteLayer} from './mapSiteLayer';
import {buildSiteModel} from './siteModel';

vi.mock('./siteModel',()=>({buildSiteModel:vi.fn(()=>({group:new THREE.Group(),rotors:[],dispose:vi.fn()}))}));
vi.mock('three',async importOriginal=>{
 const actual=await importOriginal<typeof import('three')>();
 return {...actual,WebGLRenderer:class {
  shadowMap={enabled:false,type:0};autoClear=false;outputColorSpace='';toneMapping=0;toneMappingExposure=1;
  resetState=vi.fn();setRenderTargetFramebuffer=vi.fn();setRenderTarget=vi.fn();render=vi.fn();dispose=vi.fn();
 }};
});

describe('persistent map model',()=>{
 it('swaps geometry without replacing the layer, keeps lighting independent, and preserves the map framebuffer',()=>{
  const layout={technology:'solar',width:50,depth:50,center:[-121,38],points:[[0,0]],rowPitch:3.2} as SiteLayout;
  const model=createSiteLayer(layout,vi.fn());model.setMotion(false);
  const map={getCanvas:()=>({width:800,height:600}),on:vi.fn(),off:vi.fn(),triggerRepaint:vi.fn(),
   queryTerrainElevation:()=>20,isSourceLoaded:()=>true,getTerrain:()=>({}),
   transform:{getMatrixForModel:()=>new THREE.Matrix4().toArray()}};
  const framebuffer={};const gl={FRAMEBUFFER_BINDING:1,VIEWPORT:2,FRAMEBUFFER:3,getParameter:(p:number)=>p===1?framebuffer:[0,0,800,600],bindFramebuffer:vi.fn(),viewport:vi.fn()};
  model.layer.onAdd!(map as unknown as MapInstance,gl as unknown as WebGL2RenderingContext);
  const first=vi.mocked(buildSiteModel).mock.results[0].value;
  expect(buildSiteModel).toHaveBeenCalledTimes(1);
  model.setLayout({...layout,rowPitch:4,points:[[0,0],[5,5]]});
  expect(first.dispose).not.toHaveBeenCalled();
  const render=()=>model.layer.render(gl as unknown as WebGL2RenderingContext,{defaultProjectionData:{mainMatrix:new THREE.Matrix4().toArray()}} as never);
  render();expect(buildSiteModel).toHaveBeenCalledTimes(2);expect(first.dispose).toHaveBeenCalledOnce();
  model.setSun(9);render();model.setSun(17);render();
  expect(buildSiteModel).toHaveBeenCalledTimes(2);
  expect(gl.bindFramebuffer).toHaveBeenLastCalledWith(3,framebuffer);
  expect(gl.viewport).toHaveBeenLastCalledWith(0,0,800,600);
  model.layer.onRemove!(map as unknown as MapInstance,gl as unknown as WebGL2RenderingContext);
  expect(vi.mocked(buildSiteModel).mock.results[1].value.dispose).toHaveBeenCalledOnce();
 });
});
