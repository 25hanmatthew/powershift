import {describe,it,expect,vi,afterEach} from 'vitest';
import type {Map as MapInstance} from 'maplibre-gl';
import {installMapMouseControls} from './mapMouseControls';
function setup(){
 const win=new EventTarget();vi.stubGlobal('window',win);
 const canvas=Object.assign(new EventTarget(),{style:{cursor:'grab'},setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()});
 const map={getCanvas:()=>canvas,dragRotate:{disable:vi.fn()},dragPan:{enable:vi.fn()},stop:vi.fn(),getBearing:()=>10,getPitch:()=>50,getMinPitch:()=>0,getMaxPitch:()=>80,jumpTo:vi.fn(),panBy:vi.fn()};
 const start=vi.fn(),dispose=installMapMouseControls(map as unknown as MapInstance,start);
 const send=(type:string,props:Record<string,number|string>={})=>{const e=new Event(type,{cancelable:true});Object.assign(e,{pointerId:1,button:1,buttons:4,clientX:100,clientY:100,...props});canvas.dispatchEvent(e);return e;};
 return {win,canvas,map,start,dispose,send};
}
afterEach(()=>vi.unstubAllGlobals());
describe('map mouse controls',()=>{
 it('keeps wheel zoom native and orbits with middle drag',()=>{
  const {map,send,start,dispose}=setup();expect(map.dragPan.enable).toHaveBeenCalled();expect(map.dragRotate.disable).toHaveBeenCalled();
  expect(send('wheel').defaultPrevented).toBe(false);
  expect(send('pointerdown').defaultPrevented).toBe(true);send('pointermove',{clientX:150,clientY:80});
  expect(start).toHaveBeenCalledTimes(1);expect(map.jumpTo).toHaveBeenLastCalledWith({bearing:30,pitch:56});
  expect(send('mousedown').defaultPrevented).toBe(true);expect(send('auxclick').defaultPrevented).toBe(true);dispose();
 });
 it('pans twice the drag distance, suppresses drag clicks, and preserves simple clicks',()=>{
  const {map,send,dispose}=setup();
  send('pointerdown',{button:0,buttons:1});send('pointermove',{button:0,buttons:1,clientX:102});
  expect(map.panBy).not.toHaveBeenCalled();
  send('pointermove',{button:0,buttons:1,clientX:150,clientY:80});
  expect(map.panBy).toHaveBeenLastCalledWith([-100,40],{duration:0});
  send('pointermove',{button:0,buttons:1,clientX:160,clientY:90});
  expect(map.panBy).toHaveBeenLastCalledWith([-20,-20],{duration:0});
  expect(map.jumpTo).not.toHaveBeenCalled();send('pointerup',{button:0,buttons:0});
  expect(send('click',{button:0}).defaultPrevented).toBe(true);
  send('pointerdown',{button:0,buttons:1});send('pointerup',{button:0,buttons:0});
  expect(send('click',{button:0}).defaultPrevented).toBe(false);dispose();
 });
 it('leaves touch panning to the native handler',()=>{
  const {map,send,dispose}=setup();
  expect(send('pointerdown',{button:0,buttons:1,pointerType:'touch'}).defaultPrevented).toBe(false);
  send('pointermove',{button:0,buttons:1,pointerType:'touch',clientX:150});
  expect(map.panBy).not.toHaveBeenCalled();dispose();
 });
 it('clamps tilt and releases capture and cursor on release or lost focus',()=>{
  const {map,send,canvas,win,dispose}=setup();send('pointerdown');send('pointermove',{clientY:-1000});expect(map.jumpTo).toHaveBeenLastCalledWith({bearing:10,pitch:80});
  send('pointermove',{clientY:1000});expect(map.jumpTo).toHaveBeenLastCalledWith({bearing:10,pitch:0});
  send('pointerup');expect(canvas.releasePointerCapture).toHaveBeenCalledWith(1);expect(canvas.style.cursor).toBe('grab');
  map.jumpTo.mockClear();send('pointermove');expect(map.jumpTo).not.toHaveBeenCalled();
  send('pointerdown');win.dispatchEvent(new Event('blur'));send('pointermove');expect(map.jumpTo).not.toHaveBeenCalled();dispose();
 });
 it('cleans up listeners and ends dragging if the wheel button is no longer held',()=>{
  const {map,send,canvas,dispose}=setup();send('pointerdown');send('pointermove',{buttons:0});expect(canvas.style.cursor).toBe('grab');expect(map.jumpTo).not.toHaveBeenCalled();
  dispose();expect(send('pointerdown').defaultPrevented).toBe(false);send('pointermove');expect(map.jumpTo).not.toHaveBeenCalled();
 });
});
