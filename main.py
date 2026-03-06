#!/usr/bin/env python3
import asyncio
import os
from dotenv import load_dotenv
from daemon.event_loop import ForgeEventLoop
from web.server import create_app
import uvicorn

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

PORT = int(os.getenv('WEB_PORT', '7860'))

async def main():
    fe = ForgeEventLoop()
    app = create_app()
    # write debug marker
    open('/tmp/forge_startup.log','a').write('created app\n')
    config = uvicorn.Config(app, host='0.0.0.0', port=PORT, log_level='info')
    server = uvicorn.Server(config)
    open('/tmp/forge_startup.log','a').write('configured server\n')
    # Start both tasks concurrently
    task_fe = asyncio.create_task(fe.start())
    open('/tmp/forge_startup.log','a').write('started fe task\n')
    task_uvicorn = asyncio.create_task(server.serve())
    open('/tmp/forge_startup.log','a').write('started uvicorn task\n')
    await asyncio.gather(task_fe, task_uvicorn)

if __name__ == '__main__':
    asyncio.run(main())
