from fastapi import FastAPI, Response, Depends
from starlette.middleware.cors import CORSMiddleware
from routers import line
import tempfile



app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
app.include_router(line.router)

@app.get("/health")
async def health():
    try:
        with tempfile.TemporaryFile() as fp:
            fp = tempfile.TemporaryFile()
            fp.write(b'health check')
    except Exception as e:
        return Response(status_code=503)
    return Response(status_code=200)