from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from memory.database import db
from datetime import datetime
import asyncio

router = APIRouter()

@router.post('/chat')
async def chat_endpoint(request: Request):
    body = await request.json()
    message = body.get('message','')
    if not message:
        return JSONResponse({'error':'no message'}, status_code=400)
    db.log_message('user','henry',message)
    # signal wake
    db.set_state('wake_signal', datetime.utcnow().isoformat())
    # simple blocking wait for Henry response message
    for _ in range(15):
        row = db.conn.execute("SELECT * FROM messages WHERE sender='henry' ORDER BY created_at DESC LIMIT 1").fetchone()
        if row:
            return JSONResponse({'response': row['content']})
        await asyncio.sleep(1)
    return JSONResponse({'response':'(no response yet)'})

@router.get('/chat/stream')
async def chat_stream():
    async def event_generator():
        last_id = None
        while True:
            row = db.conn.execute("SELECT * FROM messages WHERE sender='henry' ORDER BY created_at DESC LIMIT 1").fetchone()
            if row and row['id'] != last_id:
                last_id = row['id']
                yield f"data: {row['content']}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(event_generator(), media_type='text/event-stream')
