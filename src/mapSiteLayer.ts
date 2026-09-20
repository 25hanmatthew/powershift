import * as THREE from 'three';
import type { CustomLayerInterface,Map as MapInstance,MapSourceDataEvent } from 'maplibre-gl';
import { buildSiteModel } from './siteModel';
import type { SiteLayout } from './siteConcept';
import { terrainSampler } from './terrainSampling';

export function siteLngLat(layout:SiteLayout,x:number,z:number):[number,number]{
 return [layout.center[0]+x/(111320*Math.cos(layout.center[1]*Math.PI/180)),layout.center[1]-z/111320];
}
export function createSiteLayer(layout:SiteLayout,onTerrain:(message:string)=>void){
 let map:MapInstance,renderer:THREE.WebGLRenderer,model:ReturnType<typeof buildSiteModel>|undefined;
 const scene=new THREE.Scene(),camera=new THREE.Camera();
 scene.add(new THREE.HemisphereLight('#e8f4ff','#727759',2.1));
 const sun=new THREE.DirectionalLight('#fff2d5',3);scene.add(sun);
 const span=Math.max(layout.width,layout.depth,300);
 const mapTarget=new THREE.WebGLRenderTarget(1,1);
 let layoutDirty=false;
 sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);sun.shadow.camera.left=-span*.65;sun.shadow.camera.right=span*.65;
 sun.shadow.camera.top=span*.65;sun.shadow.camera.bottom=-span*.65;sun.shadow.camera.far=span*6;sun.shadow.normalBias=.7;sun.shadow.bias=-.00003;
 let moving=true,hour=13,previous=0,disposed=false,timer:ReturnType<typeof setTimeout>|undefined,fallbackTimer:ReturnType<typeof setTimeout>|undefined;
 const started=Date.now();
 let lastHeights:number[]=[];
 const updateElevation=()=>{
  if(disposed)return;
  const sample=terrainSampler(map);
  const height=(x:number,z:number)=>sample(...siteLngLat(layout,x,z));
  const heights=layout.points.map(([x,z])=>height(x,z));
  const ready=heights.length>0&&map.isSourceLoaded('terrain-dem')&&heights.every(h=>h!==null);
  onTerrain(ready?'Terrain anchored · real elevation':map.getTerrain()&&Date.now()-started<20000?'Loading terrain elevation…':'Terrain incomplete · missing heights use flat placement');
  // Retain established elevations while map tiles are being replaced.
  if(model&&!ready&&!layoutDirty)return;
  if(model&&!layoutDirty&&heights.length===lastHeights.length&&heights.every((h,i)=>Math.abs((h??0)-(lastHeights[i]??0))<.2))return;
  layoutDirty=false;
  lastHeights=heights.map(h=>h??0);
  const oldModel=model;
  model=buildSiteModel(layout,(x,z)=>(height(x,z)??0)+.35);
  // Transparent receiver follows the DEM; the satellite basemap remains visible underneath.
  const shadowGeometry=new THREE.PlaneGeometry(layout.width,layout.depth,80,80);shadowGeometry.rotateX(-Math.PI/2);
  const vertices=shadowGeometry.attributes.position;
  for(let i=0;i<vertices.count;i++)vertices.setY(i,(height(vertices.getX(i),vertices.getZ(i))??0)+.5);
  shadowGeometry.computeVertexNormals();
  const shadowGround=new THREE.Mesh(shadowGeometry,new THREE.ShadowMaterial({opacity:.24,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1}));
  shadowGround.receiveShadow=true;shadowGround.frustumCulled=false;model.group.add(shadowGround);
  scene.add(model.group);if(oldModel){scene.remove(oldModel.group);oldModel.dispose();}map.triggerRepaint();
 };
 const sourceUpdate=(event:MapSourceDataEvent)=>{if(event.sourceId==='terrain-dem'){clearTimeout(timer);timer=setTimeout(updateElevation,250);}};
 const layer:CustomLayerInterface={id:'energy-site-model',type:'custom',renderingMode:'3d',
  onAdd(instance,gl){
   map=instance;renderer=new THREE.WebGLRenderer({canvas:map.getCanvas(),context:gl as WebGL2RenderingContext,antialias:true});
   renderer.autoClear=false;renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1;
   renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFShadowMap;
   map.on('sourcedata',sourceUpdate);map.on('moveend',updateElevation);updateElevation();fallbackTimer=setTimeout(updateElevation,20500);
  },
  render(gl,args){
   if(layoutDirty)updateElevation();
   // The map owns the camera, terrain, canvas and depth buffer. No second viewport.
   const projection=new THREE.Matrix4().fromArray(args.defaultProjectionData.mainMatrix);
   const anchor=new THREE.Matrix4().fromArray(map.transform.getMatrixForModel(layout.center,0));
   camera.projectionMatrix.copy(projection.multiply(anchor));camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
   const a=(hour-6)/12*Math.PI;sun.position.set(Math.cos(a)*span,Math.max(.13,Math.sin(a))*span,span*.45);
   const now=performance.now(),delta=Math.min((now-previous)/1000,.06);previous=now;
   if(moving&&!matchMedia('(prefers-reduced-motion: reduce)').matches)model?.rotors.forEach(r=>r.rotation.z-=delta*.45);
   // Shadow passes must return to MapLibre's framebuffer, including terrain renders.
   const framebuffer=gl.getParameter(gl.FRAMEBUFFER_BINDING) as WebGLFramebuffer|null;
   const viewport=gl.getParameter(gl.VIEWPORT) as Int32Array;
   renderer.resetState();
   mapTarget.viewport.set(viewport[0],viewport[1],viewport[2],viewport[3]);
   mapTarget.scissor.copy(mapTarget.viewport);mapTarget.width=viewport[2];mapTarget.height=viewport[3];
   // Three exposes external framebuffer support; its bundled typings omit this method.
   (renderer as THREE.WebGLRenderer & {setRenderTargetFramebuffer:(target:THREE.WebGLRenderTarget,buffer:WebGLFramebuffer|null)=>void}).setRenderTargetFramebuffer(mapTarget,framebuffer);renderer.setRenderTarget(mapTarget);
   renderer.render(scene,camera);renderer.resetState();
   gl.bindFramebuffer(gl.FRAMEBUFFER,framebuffer);gl.viewport(viewport[0],viewport[1],viewport[2],viewport[3]);
   if(layout.technology==='wind'&&moving&&map.getZoom()>10&&!document.hidden&&!matchMedia('(prefers-reduced-motion: reduce)').matches)map.triggerRepaint();
  },
  onRemove(){disposed=true;clearTimeout(timer);clearTimeout(fallbackTimer);map.off('sourcedata',sourceUpdate);map.off('moveend',updateElevation);model?.dispose();sun.shadow.dispose();renderer?.dispose();}
 };
 return {layer,setLayout(value:SiteLayout){if(value===layout)return;layout=value;layoutDirty=true;map?.triggerRepaint();},setMotion(value:boolean){moving=value;map?.triggerRepaint();},setSun(value:number){hour=value;map?.triggerRepaint();}};
}
