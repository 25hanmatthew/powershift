"""Run once after configuring services; persist real flagship measurements for offline fallback."""
import asyncio
import json
from backend.main import perform
from backend.models import Plan

async def main():
    import uuid
    queue=asyncio.Queue();run_id=uuid.uuid4().hex
    task=asyncio.create_task(perform(run_id,Plan(mode='live',ab_test=True),queue))
    while True:
        item=await queue.get()
        if item is None: break
        if item['type']=='result':
            result=item['data']
            print(json.dumps({'run_id':run_id,'mode':result['mode'],'capacity_mw':result['portfolio']['capacity_mw'],
                'cache_hit':result['cache_hit'],'stale':result.get('stale',False),'analysis_timestamp':result['analysis_timestamp']}))
            if result.get('stale'): raise SystemExit('No fresh precomputation: a service failed and stale cached data was returned.')
        elif item['type']=='error':
            print(item['message']);raise SystemExit(1)
        else: print(item['detail'])
    await task

if __name__=='__main__': asyncio.run(main())
