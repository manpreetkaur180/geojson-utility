from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from sqlalchemy.orm import Session
from schemas.user import UserCreate, UserRead
from schemas.token import Token
from crud.user import get_user_by_username, create_user, delete_user_by_username
from core.auth import create_access_token, get_current_user
from db.session import get_db
from core.limiter import limiter
from core.lepton_usage import LeptonTokenService
from fastapi.security import HTTPAuthorizationCredentials
from typing import Optional
from models.user import User
import os
from core.security import get_password_hash
router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register")
async def register(
    request: Request, 
    user: UserCreate, 
    db: Session = Depends(get_db),
    geojson_utility_key: str = Header(..., alias="geojson-utility-key")
):
    # Validate API key
    required_key = os.environ.get('GEOJSON_UTILITY_KEY')
    if not required_key:
        raise HTTPException(status_code=500, detail="Server configuration error")
    
    if geojson_utility_key != required_key:
        # Log failed registration attempt for security monitoring
        print(f"[SECURITY] Failed registration attempt with invalid key from {request.client.host if request.client else 'unknown'}")
        raise HTTPException(status_code=403, detail="Invalid or missing registration key")
    
    # Check if user already exists
    db_user = get_user_by_username(db, user.username)
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    # Create user and generate token
    created_user = create_user(db, user.username, user.password)
    access_token, jti = create_access_token(data={"sub": created_user.username})
    
    # Save the token in the User table
    created_user.token = access_token
    db.add(created_user)
    db.commit()
    
    # Log successful registration
    print(f"[INFO] New user registered: {user.username}")
    
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, db: Session = Depends(get_db)):
    # Accept JWT token in the request body as {"token": "..."}
    try:
        body = await request.json()
        token = body.get("token")
    except Exception:
        raise HTTPException(status_code=400, detail="Token required in request body.")
    if not token:
        raise HTTPException(status_code=400, detail="Token required in request body.")
    # Check if the token exists in the User table
    user = db.query(User).filter(User.token == token).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    hashed_token = get_password_hash(token)
    return {"username": user.username,"hashed_token": hashed_token}


@router.get("/token-status")
def get_user_token_status(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get user's current token allocation and usage"""
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    status = LeptonTokenService.get_token_status(user_id, db)
    return {
        "user_id": user_id,
        "username": current_user.get("username"),
        "tokens": status
    }

@router.post("/delete-user")
def delete_user(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        username = current_user['username']
        delete_user_by_username(db, username)
        return {"msg": "User and all info deleted successfully"}
    except Exception:
        raise HTTPException(status_code=500, detail="Error deleting user") 