import * as THREE from 'three';

export type ArchitecturalPart={x:number;y:number;z:number;w:number;h:number;d:number;angle:number;glass:boolean;tone:number};
/** Illustrative facade detail only. Footprints and shell heights remain authoritative. */
export function facadeParts(rings:number[][][],base:number,height:number,budget=3000):ArchitecturalPart[]{
 const parts:ArchitecturalPart[]=[];
 if(height<2||!Number.isFinite(height+base))return parts;
 const levels=Math.min(40,Math.max(1,Math.floor(height/3.4))),floor=height/levels;
 for(const [ringIndex,ring] of rings.entries()){
  const area=ring.reduce((a,p,i)=>{const q=ring[(i+1)%ring.length];return a+p[0]*q[1]-q[0]*p[1];},0);
  const side=(area>=0?1:-1)*(ringIndex? -1:1);
  for(let i=0;i<ring.length;i++){
   const a=ring[i],b=ring[(i+1)%ring.length],dx=b[0]-a[0],dz=b[1]-a[1],length=Math.hypot(dx,dz);
   if(length<.5)continue;
   const nx=dz/length*side,nz=-dx/length*side,angle=-Math.atan2(dz,dx),mx=(a[0]+b[0])/2,mz=(a[1]+b[1])/2;
   const put=(part:Omit<ArchitecturalPart,'angle'|'tone'>,tone=0)=>{if(parts.length<budget)parts.push({...part,angle,tone});};
   put({x:mx,y:base+height+.16,z:mz,w:length,h:.32,d:.28,glass:false});
   put({x:mx+nx*.05,y:base+.25,z:mz+nz*.05,w:length,h:.45,d:.16,glass:false});
   for(let f=1;f<levels;f++)put({x:mx+nx*.06,y:base+f*floor,z:mz+nz*.06,w:length,h:.13,d:.17,glass:false});
   const bays=Math.min(60,Math.floor((length-1.4)/3));
   for(let f=0;f<levels;f++)for(let bay=0;bay<bays;bay++){
    const t=(bay+.5)/bays,x=a[0]+dx*t,z=a[1]+dz*t,y=base+f*floor+floor*.55;
    const width=Math.min(1.65,length/bays*.55),h=Math.min(1.65,floor*.48);
    put({x:x+nx*.04,y,z:z+nz*.04,w:width+.15,h:h+.15,d:.14,glass:false});
    put({x:x+nx*.13,y,z:z+nz*.13,w:width,h,d:.045,glass:true},(bay*7+f*3+i)%5);
   }
  }
 }
 return parts;
}

export function architecturalMeshes(parts:ArchitecturalPart[]){
 const group=new THREE.Group();group.name='Illustrative architectural details';
 const transform=new THREE.Object3D();
 for(const glass of [false,true]){
  const entries=parts.filter(p=>p.glass===glass);if(!entries.length)continue;
  const material=new THREE.MeshStandardMaterial(glass?{color:'#9bb8c4',roughness:.3,metalness:.25}:{color:'#c6c9c3',roughness:.86});
  const mesh=new THREE.InstancedMesh(new THREE.BoxGeometry(1,1,1),material,entries.length);
  mesh.name=glass?'Facade glazing':'Window frames, floor bands and parapets';
  entries.forEach((p,i)=>{transform.position.set(p.x,p.y,p.z);transform.rotation.set(0,p.angle,0);transform.scale.set(p.w,p.h,p.d);transform.updateMatrix();mesh.setMatrixAt(i,transform.matrix);
   if(glass)mesh.setColorAt(i,new THREE.Color(['#557586','#78909a','#3e596a','#66808b','#465d68'][p.tone%5]));
  });
  mesh.receiveShadow=true;mesh.frustumCulled=false;group.add(mesh);
 }
 return group;
}
export function disposeArchitecture(group:THREE.Group){group.traverse(o=>{if(o instanceof THREE.Mesh){o.geometry.dispose();(Array.isArray(o.material)?o.material:[o.material]).forEach(m=>m.dispose());}});}
