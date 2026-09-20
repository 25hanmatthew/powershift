class PCMRecorder extends AudioWorkletProcessor {
 constructor(){super();this.buffer=new Int16Array(2400);this.offset=0;}
 process(inputs){const channel=inputs[0]?.[0];if(channel){for(const value of channel){this.buffer[this.offset++]=Math.max(-1,Math.min(1,value))*32767;if(this.offset===this.buffer.length){this.port.postMessage(this.buffer.buffer);this.buffer=new Int16Array(2400);this.offset=0;}}}return true;}
}
registerProcessor('pcm-recorder',PCMRecorder);
