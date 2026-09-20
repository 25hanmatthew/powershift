export async function readAssistantEvents(response:Response,onEvent:(event:unknown)=>void){
 if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:'The assistant is unavailable. Please retry.');}
 if(!response.body)throw new Error('The assistant connection did not open.');
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
 const consume=()=>{let end:number;while((end=buffer.indexOf('\n\n'))!==-1){const frame=buffer.slice(0,end);buffer=buffer.slice(end+2);const data=frame.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');if(data)onEvent(JSON.parse(data));}};
 try{while(true){const {done,value}=await reader.read();buffer+=done?decoder.decode():decoder.decode(value,{stream:true});buffer=buffer.replace(/\r\n/g,'\n');consume();if(done)break;}}finally{reader.releaseLock();}
}
