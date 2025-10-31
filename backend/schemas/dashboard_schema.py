from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

class LastDownloadInfo(BaseModel):
    filename: str
    downloaded_at: Optional[datetime]
    download_count: int
    id:int

class RecentUploadInfo(BaseModel):
    filename: str
    created_at: datetime
    status: Optional[str]
    id:int

class FileStats(BaseModel):
    last_download: Optional[LastDownloadInfo]
    download_count: int
    recent_uploads: List[RecentUploadInfo]
    uploads_last_7days: int

class DashboardResponse(BaseModel):
    username: str
    file_stats: FileStats
