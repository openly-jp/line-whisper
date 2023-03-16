from fastapi import FastAPI, Response, Depends
from starlette.middleware.cors import CORSMiddleware
from routers import line, stripe



app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
app.include_router(line.router)
app.include_router(stripe.router)

@app.get("/health")
async def health():
    return Response(status_code=200)