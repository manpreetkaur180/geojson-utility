from dotenv import load_dotenv
load_dotenv()  # Load .env before anything else. Do not call load_dotenv() elsewhere.

from fastapi import FastAPI
from db.session import engine, Base, DATABASE_URL, SessionLocal
from routers import users, catchment
from models import user, csvfile
from db.triggers import setup_postgresql_triggers, check_triggers_exist
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware import Middleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import os
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from fastapi import Request
from fastapi.responses import JSONResponse
from core.limiter import limiter
from routers import user_dashboard



class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Strict-Transport-Security'] = 'max-age=63072000; includeSubDomains; preload'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'"
        return response

# Only show docs in development
ENV = os.environ.get('ENV', 'production')
docs_url = "/swagger-docs" if ENV == "development" else None
redoc_url = None
openapi_url = "/openapi.json" if ENV == "development" else None

# Fetch CORS and rate limit settings from environment
CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '*')
if CORS_ORIGINS == '*':
    allowed_origins = ["*"]
else:
    allowed_origins = [origin.strip() for origin in CORS_ORIGINS.split(',') if origin.strip()]
RATE_LIMIT = os.environ.get('RATE_LIMIT', '100/minute')

app = FastAPI(docs_url=docs_url, redoc_url=redoc_url, openapi_url=openapi_url)
app.include_router(user_dashboard.router, prefix="")
# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create tables for all models
Base.metadata.create_all(bind=engine)

# Setup PostgreSQL triggers for CSV status notifications
try:
    db = SessionLocal()
    if not check_triggers_exist(db):
        setup_postgresql_triggers(db)
    db.close()
except Exception as e:
    print(f"Warning: Failed to setup PostgreSQL triggers: {e}")
    print("SSE notifications may not work properly")

limiter = Limiter(key_func=get_remote_address, default_limits=[RATE_LIMIT])
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."}
    )

# Include routers
app.include_router(users.router)
app.include_router(catchment.router)

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"message": "Welcome to the GeoJSON backend API"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)