import type { Map as MapInstance } from 'maplibre-gl';

/** Accelerated mouse pan, native touch/wheel gestures, and middle-button orbit. */
export function installMapMouseControls(map:MapInstance,onStart:()=>void){
 const canvas=map.getCanvas();
 map.dragRotate.disable();
 map.dragPan.enable();
 let drag:{id:number;button:number;x:number;y:number;lastX:number;lastY:number;moved:boolean;bearing:number;pitch:number;cursor:string}|null=null;
 let suppressClick=false;
 const finish=()=>{
  if(!drag)return;
  const {id,cursor,button,moved}=drag;if(button===0&&moved)suppressClick=true;drag=null;canvas.style.cursor=cursor;
  if(canvas.hasPointerCapture(id))canvas.releasePointerCapture(id);
 };
 const down=(event:PointerEvent)=>{
  if((event.button!==0&&event.button!==1)||event.pointerType==='touch'||drag)return;
  suppressClick=false;
  event.preventDefault();event.stopPropagation();onStart();map.stop();
  drag={id:event.pointerId,button:event.button,x:event.clientX,y:event.clientY,lastX:event.clientX,lastY:event.clientY,moved:false,bearing:map.getBearing(),pitch:map.getPitch(),cursor:canvas.style.cursor};
  canvas.style.cursor='grabbing';canvas.setPointerCapture(event.pointerId);
 };
 const move=(event:PointerEvent)=>{
  if(!drag||event.pointerId!==drag.id)return;
  if(!(event.buttons&(drag.button===0?1:4))){finish();return;}
  event.preventDefault();event.stopPropagation();
  if(drag.button===0){
   if(!drag.moved&&Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<3)return;
   drag.moved=true;
   map.panBy([(drag.lastX-event.clientX)*2,(drag.lastY-event.clientY)*2],{duration:0});
   drag.lastX=event.clientX;drag.lastY=event.clientY;
   return;
  }
  map.jumpTo({bearing:drag.bearing+(event.clientX-drag.x)*.4,
   pitch:Math.max(map.getMinPitch(),Math.min(map.getMaxPitch(),drag.pitch-(event.clientY-drag.y)*.3))});
 };
 const up=(event:PointerEvent)=>{if(drag&&event.pointerId===drag.id&&(event.button===drag.button||event.type!=='pointerup'))finish();};
 // Prevent browser auto-scroll and middle-button link behavior without consuming wheel events.
 const suppress=(event:MouseEvent)=>{if(event.button===1||(event.type==='mousedown'&&event.button===0)){event.preventDefault();event.stopPropagation();}};
 const click=(event:MouseEvent)=>{if(suppressClick){suppressClick=false;event.preventDefault();event.stopImmediatePropagation();}};
 canvas.addEventListener('click',click,{capture:true});
 canvas.addEventListener('pointerdown',down,{capture:true});
 canvas.addEventListener('pointermove',move,{capture:true});
 canvas.addEventListener('pointerup',up,{capture:true});
 canvas.addEventListener('pointercancel',up,{capture:true});
 canvas.addEventListener('lostpointercapture',up,{capture:true});
 canvas.addEventListener('mousedown',suppress,{capture:true});
 canvas.addEventListener('auxclick',suppress,{capture:true});
 window.addEventListener('blur',finish);
 return ()=>{
  finish();window.removeEventListener('blur',finish);
  canvas.removeEventListener('click',click,{capture:true});
  canvas.removeEventListener('pointerdown',down,{capture:true});canvas.removeEventListener('pointermove',move,{capture:true});
  canvas.removeEventListener('pointerup',up,{capture:true});canvas.removeEventListener('pointercancel',up,{capture:true});
  canvas.removeEventListener('lostpointercapture',up,{capture:true});canvas.removeEventListener('mousedown',suppress,{capture:true});canvas.removeEventListener('auxclick',suppress,{capture:true});
 };
}
