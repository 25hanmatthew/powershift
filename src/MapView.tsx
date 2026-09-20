import { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import type { GeoJSONSource, Map as MapInstance } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { Check, LocateFixed, Minus, Plus, Scan, Satellite, X, Mountain, Compass, Building2 } from 'lucide-react';
import type { Candidate, Polygon, Region, Result } from './types';
import type { EquipmentSelection } from './equipmentSelection';
import type { SiteLayout } from './siteConcept';
import type { createSiteLayer } from './mapSiteLayer';
import { REGIONS } from './types';
import { searchBounds } from './mapJourney';
import { REVIEW_STOP_MS } from './searchReview';
import type { SearchReview } from './searchReview';
import { buildingLayer,buildingFilter,containsPoint } from './cityBuildings';
import { installMapMouseControls } from './mapMouseControls';

interface Props { selectedEquipment:EquipmentSelection|null;onEquipmentSelect:(equipment:EquipmentSelection|null)=>void;city?:Result['city']; siteLayout:SiteLayout|null;modelView:'overview'|'equipment'|'plan';modelSun:number;modelMotion:boolean;candidates:Candidate[];excluded:Candidate[];selectedId:string|null;onSelect:(id:string)=>void;region:Region;polygon:Polygon|null;onPolygon:(polygon:Polygon|null)=>void;showExcluded:boolean;review:SearchReview|null;onReviewStep:(index:number)=>void;onReviewComplete:()=>void;busy:boolean;resultKey:string }
export default function MapView({selectedEquipment,onEquipmentSelect,city,siteLayout,modelView,modelSun,modelMotion,candidates,excluded,selectedId,onSelect,region,polygon,onPolygon,showExcluded,review,onReviewStep,onReviewComplete,busy,resultKey}:Props) {
 const host=useRef<HTMLDivElement>(null);const map=useRef<MapInstance|null>(null);const markers=useRef<maplibregl.Marker[]>([]);
 const [compact,setCompact]=useState(window.innerWidth<721);
 useEffect(()=>{const resize=()=>setCompact(window.innerWidth<721);window.addEventListener('resize',resize);return()=>window.removeEventListener('resize',resize);},[]);
 const equipmentSelectRef=useRef(onEquipmentSelect);equipmentSelectRef.current=onEquipmentSelect;
 const layerRef=useRef<ReturnType<typeof createSiteLayer>|null>(null);
 const modelSettings=useRef({siteLayout,modelSun,modelMotion});modelSettings.current={siteLayout,modelSun,modelMotion};
 const [buildings,setBuildings]=useState(true);const [buildingStatus,setBuildingStatus]=useState('Loading 3D buildings…');
 const [terrain,setTerrain]=useState(true);const [,setTerrainStatus]=useState('Loading terrain elevation…');
 const [ready,setReady]=useState(false);const [drawing,setDrawing]=useState(false);const [points,setPoints]=useState<number[][]>([]);const [mapError,setMapError]=useState(false);
 const drawingRef=useRef(false); const selectRef=useRef(onSelect); selectRef.current=onSelect;
 const [viewZoom,setViewZoom]=useState(14);const [journeyPhase,setJourneyPhase]=useState('idle');const [viewCenter,setViewCenter]=useState([-120,39.4]);
 const fittedRegion=useRef(region);
 const timers=useRef<ReturnType<typeof setTimeout>[]>([]);const flightActive=useRef(false);const reviewedResult=useRef('');
 const reviewCallbacks=useRef({onReviewStep,onReviewComplete});reviewCallbacks.current={onReviewStep,onReviewComplete};

 const reduced=()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 const clearTimers=()=>{timers.current.forEach(clearTimeout);timers.current=[];};
 const clearReview=()=>{
  const source=map.current?.getSource('review-site') as GeoJSONSource|undefined;
  source?.setData({type:'FeatureCollection',features:[]});
 };
 const cancelFlight=()=>{const active=flightActive.current;clearTimers();clearReview();flightActive.current=false;map.current?.stop();setJourneyPhase('idle');if(active)reviewCallbacks.current.onReviewComplete();};
 const padding=()=>window.innerWidth<721?{top:150,bottom:210,left:50,right:55}:{top:165,bottom:190,left:100,right:100};
 useEffect(()=>{
  if(!host.current)return;
  let instance:MapInstance;
  try {instance=new maplibregl.Map({container:host.current,bounds:searchBounds(region,polygon),fitBoundsOptions:{padding:padding(),duration:0},pitch:50,bearing:-18,minZoom:3,maxZoom:22,maxPitch:80,canvasContextAttributes:{antialias:true},attributionControl:false,
   style:{version:8,light:{anchor:'viewport',color:'#fff6df',intensity:.42,position:[1.5,210,35]},sources:{
    'city-context':{type:'vector',url:'https://tiles.openfreemap.org/planet'},
    satellite:{type:'raster',tiles:['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],tileSize:256,attribution:'Imagery © Esri, Maxar, Earthstar Geographics'},
   },layers:[{id:'base',type:'background',paint:{'background-color':'#132227'}},
    {id:'satellite',type:'raster',source:'satellite',paint:{'raster-saturation':-.68,'raster-brightness-max':.68,'raster-contrast':.12}}]}});
  } catch {setMapError(true);return;}
  map.current=instance;
  // Cover neighborhood-to-building distances with fewer wheel turns.
  instance.scrollZoom.setWheelZoomRate(1 / 60);
  instance.scrollZoom.setZoomRate(1 / 25);
  const removeMouseControls=installMapMouseControls(instance,cancelFlight);
  instance.on('moveend',()=>{setViewZoom(instance.getZoom());const center=instance.getCenter();setViewCenter([center.lng,center.lat]);});
  // Native gestures already interrupt camera flights; stop() here cancels the gesture itself.
  const manual=(event:{originalEvent?:unknown})=>{if(event.originalEvent){const active=flightActive.current;clearTimers();clearReview();flightActive.current=false;setJourneyPhase('idle');if(active)reviewCallbacks.current.onReviewComplete();}};
  instance.on('dragstart',manual);instance.on('zoomstart',manual);instance.on('rotatestart',manual);
  instance.addControl(new maplibregl.AttributionControl({compact:true}),'bottom-right');
  instance.on('style.load',()=>{
   instance.addSource('terrain-dem',{type:'raster-dem',url:'https://tiles.mapterhorn.com/tilejson.json',encoding:'terrarium',tileSize:512,maxzoom:12});
   instance.addSource('footprints',{type:'geojson',attribution:'Urban footprints © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',data:{type:'FeatureCollection',features:[]}});
   instance.addLayer({id:'footprints-fill',type:'fill',source:'footprints',paint:{'fill-color':['get','color'],'fill-opacity':.2}});
   instance.addLayer({id:'footprints-line',type:'line',source:'footprints',paint:{'line-color':['get','color'],'line-width':1.5}});
   instance.addLayer(buildingLayer);
   instance.addSource('review-site',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
   instance.addLayer({id:'review-site-fill',type:'fill',source:'review-site',paint:{'fill-color':['get','color'],'fill-opacity':.28}});
   instance.addLayer({id:'review-site-line',type:'line',source:'review-site',paint:{'line-color':['get','color'],'line-width':3}});
   instance.addSource('boundary',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
   instance.addLayer({id:'boundary-fill',type:'fill',source:'boundary',paint:{'fill-color':'#c0ed91','fill-opacity':.04}});
   instance.addLayer({id:'boundary-line',type:'line',source:'boundary',paint:{'line-color':'#c0ed91','line-width':1.5,'line-dasharray':[3,3]}});
   setReady(true);
  });
  const buildingReady=()=>{if(instance.getSource('city-context')&&instance.isSourceLoaded('city-context'))setBuildingStatus('');};
  instance.on('sourcedata',event=>{if(event.sourceId==='city-context')buildingReady();});instance.on('idle',buildingReady);
  instance.on('error',event=>{if('sourceId' in event&&event.sourceId==='city-context')setBuildingStatus('Building tiles unavailable · retry by panning');if('sourceId' in event&&event.sourceId==='terrain-dem')setTerrainStatus('Terrain tile unavailable · elevation incomplete');});
  instance.on('click',e=>{
   if(drawingRef.current){setPoints(old=>[...old,[e.lngLat.lng,e.lngLat.lat]]);return;}
   const equipment=layerRef.current?.pick(e.point);if(!equipment)return;
   equipmentSelectRef.current(equipment);layerRef.current?.setSelection(equipment);
   const layout=modelSettings.current.siteLayout;if(!layout)return;
   const [x,y,z]=equipment.position;const center:[number,number]=[layout.center[0]+x/(111320*Math.cos(layout.center[1]*Math.PI/180)),layout.center[1]-z/111320];
   cancelFlight();instance.setCenterClampedToGround(false);instance.setCenterElevation(y);
   instance.flyTo({center,elevation:y,freezeElevation:true,zoom:equipment.kind==='solar'?21.5:17,pitch:equipment.kind==='solar'?50:55,bearing:instance.getBearing(),padding:{top:0,bottom:0,left:0,right:0},offset:[0,35],duration:reduced()?0:1100});
  });
  const observer=new ResizeObserver(()=>instance.resize());observer.observe(host.current);
  return ()=>{clearTimers();removeMouseControls();observer.disconnect();markers.current.forEach(m=>m.remove());instance.remove();map.current=null;};
 },[]);
 useEffect(()=>{
  const instance=map.current;if(!ready||!instance||!buildings||viewZoom<17)return;
  let cancelled=false;
  import('./cityDetailLayer').then(({createCityDetailLayer})=>{if(!cancelled)instance.addLayer(createCityDetailLayer());}).catch(()=>{});
  return()=>{cancelled=true;if(map.current===instance&&instance.getLayer('city-building-details'))instance.removeLayer('city-building-details');};
 },[ready,buildings,viewZoom>=17]);
 useEffect(()=>{drawingRef.current=drawing;if(map.current)map.current.getCanvas().style.cursor=drawing?'crosshair':'';},[drawing]);
 const fit=()=>{equipmentSelectRef.current(null);cancelFlight();const [w,s,e,n]=searchBounds(region,polygon);map.current?.fitBounds([[w,s],[e,n]],{padding:padding(),pitch:buildings?50:0,bearing:0,duration:reduced()?0:1100});};
 useEffect(()=>{if(ready&&!flightActive.current&&region!==fittedRegion.current){fittedRegion.current=region;fit();}},[region,ready]);
 useEffect(()=>{if(ready&&city&&!selectedId&&!busy&&reviewedResult.current!==resultKey)fit();},[resultKey,ready,busy]);
 useEffect(()=>{
  const instance=map.current;if(!review||!ready||!instance)return;
  clearTimers();instance.stop();setDrawing(false);flightActive.current=true;reviewedResult.current=review.id;
  if(reduced()||!review.stops.length){flightActive.current=false;reviewCallbacks.current.onReviewComplete();return;}
  setJourneyPhase('reviewing');
  const visit=(index:number)=>{
   const stop=review.stops[index];const c=stop.candidate;
   reviewCallbacks.current.onReviewStep(index);clearReview();
   (instance.getSource('review-site') as GeoJSONSource)?.setData({type:'FeatureCollection',features:[{type:'Feature',geometry:c.geometry as GeoJSON.Polygon,properties:{color:stop.excluded?'#deb67e':'#c6ed8c'}}]});
   instance.flyTo({center:[c.longitude,c.latitude],zoom:c.surface_type?16:11.6,pitch:c.surface_type?40:30,bearing:[-16,12,-8,0][index%4],padding:{top:0,bottom:0,left:0,right:0},offset:[0,0],duration:350,essential:false});
  };
  visit(0);
  review.stops.slice(1).forEach((_,index)=>timers.current.push(setTimeout(()=>visit(index+1),(index+1)*REVIEW_STOP_MS)));
  timers.current.push(setTimeout(()=>{
   flightActive.current=false;clearReview();setJourneyPhase('idle');
   const locations=[...candidates,...(showExcluded?excluded:[]),...review.stops.map(stop=>stop.candidate)];
   const bounds=new maplibregl.LngLatBounds();
   for(const location of locations){
    if(Number.isFinite(location.longitude)&&Number.isFinite(location.latitude))bounds.extend([location.longitude,location.latitude]);
   }
   if(!bounds.isEmpty())instance.fitBounds(bounds,{padding:window.innerWidth<721?{top:95,bottom:65,left:45,right:45}:{top:125,bottom:95,left:85,right:85},maxZoom:15,pitch:0,bearing:0,duration:reduced()?0:1100});
   reviewCallbacks.current.onReviewComplete();
  },review.stops.length*REVIEW_STOP_MS));
  return()=>{clearTimers();clearReview();if(flightActive.current)instance.stop();flightActive.current=false;setJourneyPhase('idle');};
 },[review?.id,ready]);
 useEffect(()=>{if(mapError&&review)reviewCallbacks.current.onReviewComplete();},[mapError,review?.id]);
 useEffect(()=>{
  if(!selectedId||!ready||!map.current||siteLayout)return;
  const candidate=[...candidates,...excluded].find(c=>c.id===selectedId);if(!candidate)return;
  cancelFlight();map.current.flyTo({center:[candidate.longitude,candidate.latitude],zoom:candidate.surface_type?17:10,pitch:candidate.surface_type?55:25,bearing:0,duration:reduced()?0:1200,essential:false});
 },[selectedId,ready]);
 useEffect(()=>{
  const instance=map.current;if(!ready||!instance)return;
  markers.current.forEach(m=>m.remove());
  const visible=[...candidates,...(showExcluded?excluded:[])];
  markers.current=visible.filter(c=>!siteLayout||c.id!==selectedId).map(c=>{
   const isExcluded=excluded.some(x=>x.id===c.id);const button=document.createElement('button');
   button.type='button';button.className=`map-pin ${c.surface_type?'urban-pin':''} ${c.rank<=12?'priority-pin':''} ${c.technology} ${c.selected?'in-portfolio':''} ${c.id===selectedId?'active':''} ${isExcluded?'excluded':''}`;
   button.setAttribute('aria-label',`${c.name}, ${isExcluded?'excluded':`rank ${c.rank}`}, ${c.capacity_mw} MW`);
   button.innerHTML=`<span>${isExcluded?'×':c.rank}</span>`;
   button.addEventListener('click',event=>{if(drawingRef.current)return;event.stopPropagation();cancelFlight();selectRef.current(c.id);});
   return new maplibregl.Marker({element:button}).setLngLat([c.longitude,c.latitude]).addTo(instance);
  });
  (instance.getSource('footprints') as GeoJSONSource)?.setData({type:'FeatureCollection',features:visible.map(c=>({type:'Feature',geometry:c.geometry as GeoJSON.Polygon,properties:{color:c.technology==='solar'?'#c4ec83':'#70c8e7'}}))});
 },[candidates,excluded,selectedId,ready,showExcluded,Boolean(siteLayout)]);
 useEffect(()=>{
  if(!map.current||!ready)return;
  if(city&&!drawing){(map.current.getSource('boundary') as GeoJSONSource)?.setData({type:'FeatureCollection',features:[{type:'Feature',properties:{},geometry:city.geometry}]});return;}
  const coords=drawing&&points.length>=3?[...points,points[0]]:polygon?.coordinates[0];
  (map.current.getSource('boundary') as GeoJSONSource)?.setData({type:'FeatureCollection',features:coords?[{type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[coords]}}]:[]});
 },[polygon,points,drawing,ready,city]);
 useEffect(()=>{
  if(!ready||!map.current)return;
  const enabled=Boolean(terrain||siteLayout);
  map.current.setTerrain(enabled?{source:'terrain-dem',exaggeration:1}:null);
  map.current.setSky({'sky-color':'#8bb8d5','horizon-color':'#d4e0db','fog-color':'#d4e0db','sky-horizon-blend':.8,'horizon-fog-blend':.4,'fog-ground-blend':enabled?.15:0,'atmosphere-blend':enabled?.6:0});
 },[terrain,Boolean(siteLayout),ready]);
 useEffect(()=>{
  const instance=map.current;if(!ready||!instance)return;
  const active=Boolean(siteLayout);if(active)setTerrain(true);
  instance.setPaintProperty('satellite','raster-saturation',active?-.08:-.68);
  instance.setPaintProperty('satellite','raster-brightness-max',active?1:.68);
  instance.setPaintProperty('footprints-fill','fill-opacity',active?0:.2);
  instance.setPaintProperty('footprints-line','line-opacity',active?.28:1);
  if(!siteLayout)return;
  let cancelled=false;
  setTerrainStatus('Loading terrain elevation…');
  import('./mapSiteLayer').then(({createSiteLayer})=>{
   if(cancelled||!modelSettings.current.siteLayout)return;const current=modelSettings.current;
   const layer=createSiteLayer(current.siteLayout!,setTerrainStatus);layerRef.current=layer;
   instance.addLayer(layer.layer);layer.setMotion(current.modelMotion);layer.setSun(current.modelSun);
  }).catch(()=>{if(!cancelled)setTerrainStatus('3D model could not load');});
  return()=>{cancelled=true;if(map.current===instance&&instance.getLayer('energy-site-model'))instance.removeLayer('energy-site-model');layerRef.current=null;};
 },[Boolean(siteLayout),selectedId,ready]);
 useEffect(()=>{if(siteLayout)layerRef.current?.setLayout(siteLayout);},[siteLayout]);
 useEffect(()=>{
  layerRef.current?.setSelection(selectedEquipment);
  const instance=map.current;if(!selectedEquipment&&instance&&!instance.getCenterClampedToGround()){instance.setCenterClampedToGround(true);instance.triggerRepaint();}
 },[selectedEquipment]);
 useEffect(()=>{equipmentSelectRef.current(null);},[modelView]);
 useEffect(()=>{
  const instance=map.current;if(!ready||!instance)return;
  instance.setLayoutProperty('city-buildings','visibility',buildings?'visible':'none');
  const site=siteLayout?[...candidates,...excluded].find(c=>c.id===selectedId):null;
  let lastFilter='';const update=()=>{const filter=buildingFilter(instance.querySourceFeatures('city-context',{sourceLayer:'building'}),site);const key=JSON.stringify(filter);if(key!==lastFilter){lastFilter=key;instance.setFilter('city-buildings',filter);}};
  update();const loaded=(event:{sourceId?:string;isSourceLoaded?:boolean})=>{if(event.sourceId==='city-context'&&event.isSourceLoaded)update();};
  instance.on('sourcedata',loaded);return()=>{instance.off('sourcedata',loaded);};
 },[ready,buildings,Boolean(siteLayout),selectedId,candidates,excluded]);
 useEffect(()=>{
  const instance=map.current;if(!ready||!instance)return;
  const click=(event:maplibregl.MapLayerMouseEvent)=>{if(drawingRef.current||siteLayout)return;const match=candidates.find(c=>c.surface_type&&containsPoint([event.lngLat.lng,event.lngLat.lat],c.geometry));if(match)selectRef.current(match.id);};
  instance.on('click','city-buildings',click);return()=>{instance.off('click','city-buildings',click);};
 },[ready,candidates,siteLayout]);
 useEffect(()=>{layerRef.current?.setSun(modelSun);},[modelSun]);
 useEffect(()=>{layerRef.current?.setMotion(modelMotion);},[modelMotion]);
 useEffect(()=>{
  const instance=map.current;if(!instance||!ready||!siteLayout)return;
  cancelFlight();const mobile=compact;
  const offset:[number,number]=mobile?[0,30]:[0,70];
  if(modelView==='equipment'){
   const p=siteLayout.technology==='solar'&&!siteLayout.surfaceType?[...siteLayout.points].sort((a,b)=>Math.hypot(a[0]-200,a[1]-200)-Math.hypot(b[0]-200,b[1]-200))[0]||[0,0]:siteLayout.points[0]||[0,0];
   const center:[number,number]=[siteLayout.center[0]+p[0]/(111320*Math.cos(siteLayout.center[1]*Math.PI/180)),siteLayout.center[1]-p[1]/111320];
   instance.flyTo({center,zoom:siteLayout.technology==='solar'?(siteLayout.surfaceType?(mobile?19.4:20):(mobile?18.2:18.7)):(mobile?15.8:16.3),pitch:65,bearing:-28,padding:{top:0,bottom:0,left:0,right:0},offset,duration:reduced()?0:1800});
  }else{
   const [w,s,e,n]=siteLayout.bounds;
   instance.fitBounds([[w,s],[e,n]],{padding:mobile?{top:80,bottom:35,left:25,right:55}:{top:110,bottom:50,left:50,right:80},maxZoom:siteLayout.surfaceType?19:15.5,pitch:modelView==='plan'?0:60,bearing:modelView==='plan'?0:-25,duration:reduced()?0:1800});
  }
 },[siteLayout?.center[0],siteLayout?.center[1],Boolean(siteLayout),modelView,ready,compact]);
 return <div className={`map-stage ${viewZoom<16?'city-overview':''} ${journeyPhase==='reviewing'?'is-reviewing':''} ${siteLayout?'has-site-model':''}`} data-journey={journeyPhase}>
  <div ref={host} className="map-canvas" aria-label="Interactive energy candidate map"/>
  <div className="map-vignette"/>
  {mapError&&<div className="map-fallback"><Satellite size={32}/><p>Interactive map unavailable</p><small>Your browser needs WebGL. Ranked sites and evidence remain available below.</small></div>}
  <div className="map-topline"><span className="map-eyebrow"><span className="live-dot"/> {city?.scope==='regional'?'REGIONAL ENERGY WORKSPACE':'CITY ENERGY WORKSPACE'}</span><span className="coordinate-label">{Math.abs(viewCenter[1]).toFixed(2)}° {viewCenter[1]>=0?'N':'S'} / {Math.abs(viewCenter[0]).toFixed(2)}° {viewCenter[0]>=0?'E':'W'}</span></div>
  <div className="map-title"><span>FROM ORBIT TO OPPORTUNITY</span><h2>{city?.name||REGIONS[region].short}</h2><p>{busy?'Exploring the search area…':city?.scope==='regional'?`Renewable opportunities within ${city.radius_km} km.`:'Renewable opportunities inside city limits.'}</p></div>
  {buildings&&buildingStatus.includes('unavailable')&&<div className="city-building-status"><Building2 size={13}/><span>{buildingStatus}</span></div>}
  <div className="map-tools">
   <button title="3D buildings" aria-label="3D buildings" aria-pressed={buildings} onClick={()=>{setBuildings(!buildings);if(!buildings)map.current?.easeTo({pitch:55,duration:reduced()?0:700});}}><Building2 size={18}/></button>
   <button title="3D terrain" aria-label="3D terrain" aria-pressed={Boolean(terrain||siteLayout)} disabled={Boolean(siteLayout)} onClick={()=>{setTerrain(!terrain);map.current?.easeTo({pitch:terrain?0:60,duration:reduced()?0:900});}}><Mountain size={18}/></button>
   <button title="Reset north" aria-label="Reset north" onClick={()=>map.current?.easeTo({bearing:0,duration:reduced()?0:500})}><Compass size={18}/></button>
   <button title="Zoom in" aria-label="Zoom in" onClick={()=>{cancelFlight();map.current?.zoomTo(Math.min(22,map.current.getZoom()+2),{duration:220});}}><Plus size={18}/></button>
   <button title="Zoom out" aria-label="Zoom out" onClick={()=>{cancelFlight();map.current?.zoomTo(Math.max(3,map.current.getZoom()-2),{duration:220});}}><Minus size={18}/></button>
   <span/>
   <button title="Fit region" aria-label="Fit region" onClick={fit}><LocateFixed size={18}/></button>
  </div>
  {drawing&&<div className="draw-instructions"><Scan size={17}/><span>Click the map to draw a boundary · {points.length} points</span><button disabled={points.length<3} onClick={()=>{onPolygon({type:'Polygon',coordinates:[[...points,points[0]]]});setDrawing(false);}}><Check size={16}/> Use boundary</button><button aria-label="Cancel drawing" onClick={()=>{setDrawing(false);setPoints([]);}}><X size={16}/></button></div>}
  <div className="map-bottom"><div className="map-legend"><span><i className="solar-dot"/>Solar</span><span><i className="wind-dot"/>Wind</span><span><i className="selected-dot"/>Portfolio site</span></div></div>
 </div>;
}
