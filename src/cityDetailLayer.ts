import * as THREE from 'three';
import type {CustomLayerInterface,Map as MapInstance} from 'maplibre-gl';
import {architecturalMeshes,disposeArchitecture,facadeParts} from './buildingArchitecture';
import type {ArchitecturalPart} from './buildingArchitecture';
import {terrainSampler} from './terrainSampling';

export function createCityDetailLayer(){
 let map:MapInstance,renderer:THREE.WebGLRenderer,details:THREE.Group|undefined;
 let center:[number,number]=[0,0],signature='',disposed=false,timer:ReturnType<typeof setTimeout>|undefined;
 const scene=new THREE.Scene(),camera=new THREE.Camera();
 scene.add(new THREE.HemisphereLight('#e4eef7','#7b7c68',2.4));
 const light=new THREE.DirectionalLight('#fff3de',2);light.position.set(-300,600,400);scene.add(light);
 const update=()=>{
  if(disposed)return;
  if(map.getZoom()<17){if(details){scene.remove(details);disposeArchitecture(details);details=undefined;signature='';map.triggerRepaint();}return;}
  const c=map.getCenter(),unique=new Set<string>();
  const buildings=map.queryRenderedFeatures(undefined,{layers:['city-buildings']}).flatMap(f=>{
   const key=String(f.id??JSON.stringify(f.geometry));if(unique.has(key))return [];unique.add(key);
   const height=Number(f.properties.render_height??8),base=Number(f.properties.render_min_height??0);
   if(!Number.isFinite(height+base)||height<=base)return [];
   const polys=f.geometry.type==='Polygon'?[f.geometry.coordinates]:f.geometry.type==='MultiPolygon'?f.geometry.coordinates:[];
   return polys.map(rings=>({key,rings,height,base,distance:Math.hypot((rings[0][0][0]-c.lng)*Math.cos(c.lat*Math.PI/180),rings[0][0][1]-c.lat)}));
  }).filter(b=>b.distance<.007).sort((a,b)=>a.distance-b.distance).slice(0,70);
  const key=buildings.map(b=>`${b.key}:${b.height}:${b.base}`).sort().join('|');
  if(key===signature)return;signature=key;center=[c.lng,c.lat];
  const sample=terrainSampler(map),parts:ArchitecturalPart[]=[];
  const scale=111320*Math.cos(center[1]*Math.PI/180);
  for(const building of buildings){
   if(parts.length>=16000)break;
   const rings=building.rings.map(r=>r.map(p=>[(p[0]-center[0])*scale,(center[1]-p[1])*111320]));
   const first=building.rings[0][0];const ground=sample(first[0],first[1])??0;
   parts.push(...facadeParts(rings,ground+building.base,building.height-building.base,Math.min(2200,16000-parts.length)));
  }
  const next=architecturalMeshes(parts);scene.add(next);if(details){scene.remove(details);disposeArchitecture(details);}details=next;map.triggerRepaint();
 };
 const schedule=()=>{clearTimeout(timer);timer=setTimeout(update,180);};
 const source=(e:{sourceId?:string;isSourceLoaded?:boolean})=>{if((e.sourceId==='city-context'||e.sourceId==='terrain-dem')&&e.isSourceLoaded){if(e.sourceId==='terrain-dem')signature='';schedule();}};
 const layer:CustomLayerInterface={id:'city-building-details',type:'custom',renderingMode:'3d',
  onAdd(instance,gl){map=instance;renderer=new THREE.WebGLRenderer({canvas:map.getCanvas(),context:gl as WebGL2RenderingContext});renderer.autoClear=false;renderer.outputColorSpace=THREE.SRGBColorSpace;
   map.on('moveend',schedule);map.on('sourcedata',source);map.on('idle',schedule);schedule();},
  render(gl,args){if(map.getZoom()<17||!details)return;
   const framebuffer=gl.getParameter(gl.FRAMEBUFFER_BINDING),viewport=gl.getParameter(gl.VIEWPORT) as Int32Array;
   camera.projectionMatrix.fromArray(args.defaultProjectionData.mainMatrix).multiply(new THREE.Matrix4().fromArray(map.transform.getMatrixForModel(center,0)));
   camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
   renderer.resetState();renderer.setViewport(viewport[0],viewport[1],viewport[2],viewport[3]);renderer.render(scene,camera);renderer.resetState();
   gl.bindFramebuffer(gl.FRAMEBUFFER,framebuffer);gl.viewport(viewport[0],viewport[1],viewport[2],viewport[3]);
  },
  onRemove(){disposed=true;clearTimeout(timer);map.off('moveend',schedule);map.off('sourcedata',source);map.off('idle',schedule);if(details)disposeArchitecture(details);renderer.dispose();}
 };
 return layer;
}
