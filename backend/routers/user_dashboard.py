from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta

from db.session import get_db
from models.csvfile import CSVFile
from models.user import User
from schemas.dashboard_schema import DashboardResponse
from core.auth import get_current_user

router = APIRouter(prefix="/user-dashboard", tags=["dashboard"])

@router.get("/stats", response_model=DashboardResponse)
async def get_user_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1, description="Page number, starts at 1"),
    per_page: int = Query(10, ge=1, description="Items per page (default 10)")
):
    # Accept either ORM User or token payload dict from auth dependency
    if isinstance(current_user, dict):
        user_id = current_user.get("id") or current_user.get("user_id")
        username = current_user.get("username") or current_user.get("sub")
    else:
        user_id = getattr(current_user, "id", None)
        username = getattr(current_user, "username", None)

    # fallback: if we only have username, look up the user to get id
    if not user_id and username:
        user_obj = db.query(User).filter(User.username == username).first()
        if user_obj:
            user_id = user_obj.id
            username = user_obj.username

    if not user_id:
        raise HTTPException(status_code=401, detail="Unable to identify user")

    # Base query filtered by user_id
    base_query = db.query(CSVFile).filter(CSVFile.user_id == user_id)

    # Pagination
    total_files = base_query.count()
    total_pages = max(1, (total_files + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page

    # Last downloaded file
    last_download = (
        base_query.filter(CSVFile.last_downloaded_at.isnot(None))
        .order_by(CSVFile.last_downloaded_at.desc())
        .first()
    )

    # Recent uploads (paginated)
    recent_files = (
        base_query.order_by(CSVFile.created_at.desc())
        .offset(offset)
        .limit(per_page)
        .all()
    )

    # Uploads in last 7 days
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    recent_count = base_query.filter(CSVFile.created_at >= seven_days_ago).count()

    # Total downloads across user's files
    total_downloads = (
        base_query.with_entities(func.coalesce(func.sum(CSVFile.download_count), 0)).scalar() or 0
    )

    return {
        "username": username,
        "file_stats": {
            "last_download": {
                "filename": last_download.filename,
                "downloaded_at": last_download.last_downloaded_at,
                "download_count": last_download.download_count,
                "id":last_download.id
            } if last_download else None,
            "download_count": int(total_downloads),
            "recent_uploads": [
                {
                    "filename": f.filename,
                    "created_at": f.created_at,
                    "status": f.status,
                    "id":f.id
                } for f in recent_files
            ],
            "uploads_last_7days": recent_count,
            
        }
    }





