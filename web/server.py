from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
import os
import asyncio

# Load .env before importing modules that read environment on import
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

from daemon.event_loop import ForgeEventLoop
from .api import chat, status, health, logs, projects
from .api import llm_metrics


def create_app():
    app = FastAPI()

    @app.on_event('startup')
    async def _startup():
        # Start the Forge background event loop in the same process so systemd-managed uvicorn runs both web + agents
        app.state.forge_loop = ForgeEventLoop()
        app.state._fe_task = asyncio.create_task(app.state.forge_loop.start())
        # start projects IDF refresher background task (compute global IDF periodically)
        try:
            import web.api.projects as projects
            # run an immediate refresh in executor so IDF cache is populated on startup
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, projects.recompute_idf_internal)
            app.state._idf_task = asyncio.create_task(projects.async_idf_refresher(600))
        except Exception:
            pass

    @app.on_event('shutdown')
    async def _shutdown():
        try:
            app.state.forge_loop.stop()
            if hasattr(app.state, '_fe_task'):
                app.state._fe_task.cancel()
                try:
                    await app.state._fe_task
                except Exception:
                    pass
            # cancel IDF refresher if running
            if hasattr(app.state, '_idf_task'):
                app.state._idf_task.cancel()
                try:
                    await app.state._idf_task
                except Exception:
                    pass
        except Exception:
            pass

    @app.get('/', response_class=HTMLResponse)
    async def index(request: Request):
        from pathlib import Path
        static = Path(os.path.join(os.path.dirname(__file__), 'static', 'index.html')).read_text()
        # Ensure the index is not cached by clients so UI changes are visible immediately
        headers = {
            'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
            'Pragma': 'no-cache'
        }
        return HTMLResponse(content=static, headers=headers)

    app.include_router(chat.router, prefix='/api')
    app.include_router(status.router, prefix='/api')
    app.include_router(health.router, prefix='/api')
    app.include_router(logs.router, prefix='/api')
    app.include_router(projects.router, prefix='/api')
    app.include_router(llm_metrics.router, prefix='/api')
    return app
