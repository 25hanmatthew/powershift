import {expect,it} from 'vitest';
import {markerOffsets} from './markerLayout';

it('separates the overlapping Sacramento sites 2, 3 and 6',()=>{
 const points=[{x:541,y:322},{x:541,y:323},{x:526,y:324}];
 const offsets=markerOffsets(points);
 const centers=points.map((p,i)=>({x:p.x+offsets[i][0],y:p.y+offsets[i][1]}));
 for(let i=0;i<centers.length;i++)for(let j=0;j<i;j++)expect(Math.hypot(centers[i].x-centers[j].x,centers[i].y-centers[j].y)).toBeGreaterThanOrEqual(36);
 expect(points[0]).toEqual({x:541,y:322});
});
it('restores exact positions when zooming separates sites',()=>{
 expect(markerOffsets([{x:0,y:0},{x:100,y:0},{x:200,y:0}])).toEqual([[0,0],[0,0],[0,0]]);
});
it('keeps every marker in a dense group distinct',()=>{
 const offsets=markerOffsets(Array.from({length:20},()=>({x:200,y:200})));
 expect(new Set(offsets.map(p=>p.join(','))).size).toBe(20);
 for(let i=0;i<offsets.length;i++)for(let j=0;j<i;j++)expect(Math.hypot(offsets[i][0]-offsets[j][0],offsets[i][1]-offsets[j][1])).toBeGreaterThanOrEqual(36);
});
