export async function startVoice(onText:(text:string)=>void,onError:(message:string)=>void):Promise<()=>void> {
 const stream=await navigator.mediaDevices.getUserMedia({audio:true});
 const context=new AudioContext({sampleRate:24000});
 try { await context.audioWorklet.addModule('/audio-worklet.js'); await context.resume(); }
 catch(error){stream.getTracks().forEach(t=>t.stop());await context.close();throw error;}
 const source=context.createMediaStreamSource(stream);const processor=new AudioWorkletNode(context,'pcm-recorder');
 const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/voice`);
 let stopped=false;let recording=false;
 const timeout=setTimeout(()=>{onError('OpenAI voice session timed out. Please try again.');stop();},60000);
 const stop=()=>{if(stopped)return;stopped=true;clearTimeout(timeout);processor.disconnect();source.disconnect();stream.getTracks().forEach(t=>t.stop());void context.close();socket.close();};
 processor.port.onmessage=event=>{
  if(socket.readyState!==WebSocket.OPEN)return;
  const samples=new Int16Array(event.data);const bytes=new Uint8Array(samples.buffer);let binary='';
  for(const byte of bytes)binary+=String.fromCharCode(byte);
  socket.send(JSON.stringify({type:'input_audio_buffer.append',audio:btoa(binary)}));
 };
 socket.onmessage=({data})=>{
  const event=JSON.parse(data);
  // Wait until OpenAI accepts the PCM transcription configuration before streaming audio.
  if(event.type==='session.updated'&&!recording&&!stopped){recording=true;source.connect(processor);processor.connect(context.destination);}
  if(event.type==='conversation.item.input_audio_transcription.completed'){
   if(event.transcript?.trim())onText(event.transcript);else onError('No speech was detected. Please try again.');stop();
  }
  if(event.type==='error'){onError(event.message||event.error?.message||'Voice connection failed.');stop();}
 };
 socket.onerror=()=>{onError('Could not connect to OpenAI voice input.');stop();};
 socket.onclose=()=>{if(!stopped){onError('Voice session ended.');stop();}};
 return stop;
}
