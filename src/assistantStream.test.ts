import {describe,it,expect} from 'vitest';
import {readAssistantEvents} from './assistantStream';

describe('assistant response stream',()=>{
 it('reassembles split frames and UTF-8 characters while ignoring heartbeats',async()=>{
  const bytes=new TextEncoder().encode(': heartbeat\n\ndata: {"type":"status","message":"Looking…"}\n\ndata: {"type":"done","answer":"✓"}\n\n');
  const stream=new ReadableStream({start(c){for(let i=0;i<bytes.length;i+=3)c.enqueue(bytes.slice(i,i+3));c.close();}});
  const events:unknown[]=[];await readAssistantEvents(new Response(stream),e=>events.push(e));
  expect(events).toEqual([{type:'status',message:'Looking…'},{type:'done',answer:'✓'}]);
 });
 it('reports server errors instead of treating an unavailable service as an answer',async()=>{
  await expect(readAssistantEvents(new Response(JSON.stringify({detail:'OpenAI is not configured.'}),{status:503}),()=>{})).rejects.toThrow('OpenAI is not configured.');
 });
 it('propagates error events from the consumer',async()=>{
  await expect(readAssistantEvents(new Response('data: {"type":"error","message":"Failed"}\n\n'),()=>{throw new Error('Failed');})).rejects.toThrow('Failed');
 });
});
