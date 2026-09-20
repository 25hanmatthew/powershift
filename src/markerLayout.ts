type Point={x:number;y:number};

/** Keep close markers separately clickable without changing their coordinates. */
export function markerOffsets(points:Point[],gap=36):[number,number][] {
 const placed:Point[]=[];
 return points.map(point=>{
  let offset:[number,number]=[0,0];
  const available=(dx:number,dy:number)=>placed.every(p=>Math.hypot(p.x-point.x-dx,p.y-point.y-dy)>=gap);
  if(!available(0,0)){
   search:for(let ring=1;ring<=points.length;ring++){
    const slots=ring*8;
    for(let i=0;i<slots;i++){
     const angle=i*2*Math.PI/slots;
     const dx=Math.round(Math.cos(angle)*ring*gap),dy=Math.round(Math.sin(angle)*ring*gap);
     if(available(dx,dy)){offset=[dx,dy];break search;}
    }
   }
  }
  placed.push({x:point.x+offset[0],y:point.y+offset[1]});return offset;
 });
}
