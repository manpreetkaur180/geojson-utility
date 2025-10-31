from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Response, Request
from sqlalchemy.orm import Session
from core.auth import get_current_user
from db.session import get_db, Base, engine, SessionLocal
from fastapi.responses import StreamingResponse, JSONResponse
from models.csvfile import CSVFile
from models.user import User
from core.sse_manager import sse_manager
from core.validation_helpers import validate_csv_row
import pandas as pd
import io
import os
import json
import http.client
from urllib.parse import urlencode
import logging
from typing import Optional
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.limiter import limiter
from core.lepton_usage import LeptonTokenService
import threading
import re
from core.security import verify_password

load_dotenv()

# Ensure the csv_files and csv_rows tables exist
Base.metadata.create_all(bind=engine)

# Logger setup
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

class LeptonMapsClient:
    HOST = "api.leptonmaps.com"
    PATH = "/v1/geojson/catchment"

    def __init__(self, api_key: str):
        self.headers = {
            "x-api-key": api_key,
            "Accept": "application/json"
        }
        self.conn = http.client.HTTPSConnection(self.HOST)

    def get_catchment_geojson(self, latitude: float, longitude: float, catchment_type: str, accuracy_time_based: str = "HIGH", drive_distance: Optional[int] = None, drive_time: Optional[int] = None, departure_time: Optional[str] = None) -> dict:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "catchment_type": catchment_type,
            "accuracy_time_based": accuracy_time_based
        }
        if drive_distance is not None:
            params["drive_distance"] = drive_distance
        if drive_time is not None:
            params["drive_time"] = drive_time
        if departure_time is not None:
            params["departure_time"] = departure_time
        query_string = urlencode(params)
        full_path = f"{self.PATH}?{query_string}"
        logger.info(f"Requesting catchment: {full_path}")
        try:
            self.conn.request("GET", full_path, headers=self.headers)
            resp = self.conn.getresponse()
            body = resp.read()
            body_text = body.decode()
            if resp.status == 401:
                logger.error("HTTP 401: Unauthorized - Lepton Maps API key is invalid or expired")
                raise Exception("Lepton Maps API: Unauthorized (HTTP 401). Your API key is invalid or expired.")
            if resp.status == 403:
                logger.error("HTTP 403: Forbidden - Lepton Maps API key is not allowed")
                raise Exception("Lepton Maps API: Forbidden (HTTP 403). Your API key does not have access.")
            if resp.status == 402:
                logger.error("HTTP 402: Not enough credits on Lepton Maps API")
                raise Exception("Lepton Maps API: Not enough credits (HTTP 402). Please check your API quota or upgrade your plan.")
            if resp.status != 200:
                logger.error(f"HTTP {resp.status}: {body_text}")
                raise Exception(f"Lepton Maps API: Unexpected status {resp.status}: {body_text}")
            geojson = json.loads(body)
            logger.info("Successfully fetched catchment GeoJSON")
            return geojson
        except Exception as e:
            logger.exception("Failed to fetch catchment")
            raise

    def extract_polygon_geojson(self, geojson: dict) -> dict:
        features = geojson.get("features", [])
        if not features:
            raise ValueError("No features found in GeoJSON response")
        geom = features[0].get("geometry", {})
        coords = geom.get("coordinates")
        if not coords or not isinstance(coords, list):
            raise ValueError("Invalid or missing coordinates in geometry")
        outer_ring = coords[0]
        geojson_polygon = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [outer_ring]
                    },
                    "properties": {}
                }
            ]
        }
        return geojson_polygon

router = APIRouter(prefix="/catchment", tags=["catchment"])

@router.get("/sample-csv")
def get_sample_csv():
    output = io.StringIO()
    SAMPLE_CSV_ROWS = {
        'snp_id': ['snp_1.com', 'snp_2.com'],
        'provider_id': ['provider1', 'provider2'],
        'location_id': ['L1', 'L2'],
        'location_gps': ['28.5065162,77.073938', '30.7135305,76.7454157'],
        'drive_distance': [500.5, ''],
        'drive_time': ['', 20.5]
    }
    sample_df = pd.DataFrame(SAMPLE_CSV_ROWS)
    sample_df.to_csv(output, index=False)
    output.seek(0)
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=sample_catchment.csv"})

@router.post("/bulk")
@limiter.limit("10/minute")
async def bulk_process_catchments(request: Request, file: UploadFile = File(...), current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    # File size limit (10MB)
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="CSV file too large (max 2MB)")
    if not file.filename or not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV with a valid filename")
    if not content:
        raise HTTPException(status_code=400, detail="CSV file is empty.")
    # Row count limit (1000 rows)
    try:
        df = pd.read_csv(io.StringIO(content.decode('utf-8')))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse CSV: {str(e)}")
    if len(df) > 1000:
        raise HTTPException(status_code=400, detail="CSV file has too many rows (max 1000)")
    # Check for required columns
    required_columns = {'snp_id', 'provider_id', 'location_id', 'location_gps', 'drive_distance', 'drive_time'}
    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        # Save CSV with errors column
        df['errors'] = [f"Missing columns: {', '.join(missing_columns)}"] * len(df)
        output = io.StringIO()
        df.to_csv(output, index=False)
        output.seek(0)
        csv_file.file_content = output.getvalue().encode('utf-8')
        csv_file.status = 'failed'
        csv_file.error = f"Missing columns: {', '.join(missing_columns)}"
        session.commit()
        return
    # Check for duplicate rows
    if df.duplicated().any():
        raise HTTPException(status_code=400, detail="CSV file contains duplicate rows.")
    # Check for duplicate location_id
    if df['location_id'].duplicated().any():
        dups = df[df['location_id'].duplicated(keep=False)]['location_id'].tolist()
        raise HTTPException(status_code=400, detail=f"CSV file contains duplicate location_id values: {set(dups)}")
    username = current_user.get('username')
    user_id = current_user.get('user_id')
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID not found in authentication")
    
    # Get user's token status
    user_token_status = LeptonTokenService.get_token_status(user_id, db)
    csv_row_count = len(df)
    
    new_csv = CSVFile(filename=file.filename, file_content=content, username=username, user_id=user_id, status='pending')
    db.add(new_csv)
    db.commit()
    db.refresh(new_csv)
    csv_id = new_csv.id
    def process_csv_in_background(csv_id, content, username, user_id):
        logger.info(f"Starting background processing thread for CSV {csv_id}")
        session = None
        try:
            session = SessionLocal()
            logger.info(f"Database session created for CSV {csv_id}")
            csv_file = session.query(CSVFile).filter(CSVFile.id == csv_id).first()
            if not csv_file:
                return
            csv_file.status = 'processing'
            df = pd.read_csv(io.StringIO(content.decode('utf-8')))
            total_rows = len(df)
            
            # Store initial processing metrics
            csv_file.total_rows = total_rows
            csv_file.processing_started_at = datetime.now(timezone.utc)
            session.commit()
            
            # PostgreSQL trigger will automatically broadcast start event when status changes to 'processing'
            logger.info(f"CSV {csv_id} marked as processing - PostgreSQL trigger will broadcast start event")
            errors_per_row = [''] * len(df)
            geojson_results = [None] * len(df)
            required_columns = {'snp_id', 'provider_id', 'location_id', 'location_gps', 'drive_distance', 'drive_time'}
            missing_columns = required_columns - set(df.columns)
            if missing_columns:
                # Save CSV with errors column
                df['errors'] = [f"Missing columns: {', '.join(missing_columns)}"] * len(df)
                output = io.StringIO()
                df.to_csv(output, index=False)
                output.seek(0)
                csv_file.file_content = output.getvalue().encode('utf-8')
                csv_file.status = 'failed'
                csv_file.error = f"Missing columns: {', '.join(missing_columns)}"
                session.commit()
                
                # PostgreSQL trigger will automatically broadcast completion event
                logger.info(f"CSV {csv_id} marked as failed due to missing columns - PostgreSQL trigger will broadcast completion event")
                return
            logger.info(f"Column validation passed for CSV {csv_id}, starting API processing")
            api_key = os.environ.get("LEPTON_API_KEY")
            if not api_key:
                # Save CSV with errors column
                df['errors'] = ["LEPTON_API_KEY not set"] * len(df)
                output = io.StringIO()
                df.to_csv(output, index=False)
                output.seek(0)
                csv_file.file_content = output.getvalue().encode('utf-8')
                csv_file.status = 'failed'
                csv_file.error = "LEPTON_API_KEY not set"
                session.commit()
                
                # PostgreSQL trigger will automatically broadcast completion event
                logger.info(f"CSV {csv_id} marked as failed due to missing API key - PostgreSQL trigger will broadcast completion event")
                return
            def process_row(idx, row):
                # Create thread-local database session for thread safety
                thread_session = SessionLocal()
                api_call_made = False
                
                try:
                    logger.info(f"Processing row {idx+1} for CSV {csv_id}")
                    print(f"DEBUG: Processing row {idx+1} for CSV {csv_id}")  # Explicit stdout
                    
                    # Use helper function for validation
                    row_errors, use_drive_distance, drive_distance_val, drive_time_val, lat, lon = validate_csv_row(row)
                    print(f"DEBUG: Row {idx+1} validation - errors: {row_errors}, use_drive_distance: {use_drive_distance}")  # Debug validation
                    
                    geojson_str = '{}'
                    if not row_errors and lat is not None and lon is not None:
                        if not LeptonTokenService.check_user_has_tokens(user_id, thread_session):
                            row_errors.append("Your token allocation has been exhausted")
                        else:
                            try:
                                # Check for mock mode
                                if os.environ.get("MOCK_MODE") == "true":
                                    mock_geojson = {
                                        "type": "FeatureCollection",
                                        "features": [{
                                            "type": "Feature",
                                            "geometry": {
                                                "type": "Polygon",
                                                "coordinates": [[
                                                    [lon - 0.01, lat - 0.01],
                                                    [lon + 0.01, lat - 0.01],
                                                    [lon + 0.01, lat + 0.01],
                                                    [lon - 0.01, lat + 0.01],
                                                    [lon - 0.01, lat - 0.01]
                                                ]]
                                            },
                                            "properties": {}
                                        }]
                                    }
                                    geojson_str = json.dumps(mock_geojson)
                                    api_call_made = False # No real API call
                                else:
                                    # Step 2: Make Lepton API call
                                    client = LeptonMapsClient(api_key=api_key)
                                    api_call_made = True
                                    if use_drive_distance and drive_distance_val is not None:
                                       geojson = client.get_catchment_geojson(latitude=lat, longitude=lon, catchment_type='DRIVE_DISTANCE', drive_distance=drive_distance_val)
                                    elif drive_time_val is not None:
                                       geojson = client.get_catchment_geojson(latitude=lat, longitude=lon, catchment_type='DRIVE_TIME', drive_time=drive_time_val)
                                    else:
                                       row_errors.append("Either drive_distance or drive_time must be provided and valid.")
                                       return idx, geojson_str, row_errors, False

                                    # Step 3: API call succeeded - now consume token
                                    if LeptonTokenService.consume_token_after_success(user_id, thread_session):
                                           polygon_geojson = client.extract_polygon_geojson(geojson)
                                           geojson_str = json.dumps(polygon_geojson)
                                    else:
                                        # Race condition: tokens exhausted between check and consumption
                                        row_errors.append("Your token allocation has been exhausted")
                            except Exception as e:
                                # Step 4: API call failed - don't consume token
                                logger.error(f"GeoJSON error for row {idx+1}: {str(e)}")
                                # Distinguish between different error types
                                error_str = str(e)
                                if "HTTP 402" in error_str or "Not enough credits" in error_str:
                                    # Real Lepton API exhaustion
                                    row_errors.append("Lepton Maps API: Not enough credits (HTTP 402). Please check your API quota or upgrade your plan.")
                                elif "HTTP 401" in error_str:
                                    row_errors.append("Lepton Maps API: Unauthorized (HTTP 401). Your API key is invalid or expired.")
                                elif "HTTP 403" in error_str:
                                    row_errors.append("Lepton Maps API: Forbidden (HTTP 403). Your API key does not have access.")
                                else:
                                    row_errors.append(f"GeoJSON error: {str(e)}")
                                return idx, geojson_str, row_errors, api_call_made
                    
                    logger.info(f"Row {idx+1} processed successfully for CSV {csv_id}")
                    
                    # Return with API call status
                    return idx, geojson_str, row_errors, api_call_made
                    
                except Exception as row_error:
                    logger.error(f"Error processing row {idx+1} for CSV {csv_id}: {str(row_error)}")
                    return idx, '{}', [f"Row processing error: {str(row_error)}"], api_call_made
                    
                finally:
                    # Always close thread-local session
                    try:
                        thread_session.close()
                    except Exception as close_error:
                        logger.error(f"Failed to close thread session for row {idx+1}, CSV {csv_id}: {close_error}")
            # Progress tracking variables
            completed_count = 0
            failed_count = 0
            api_calls_made = 0
            progress_lock = threading.Lock()
            
            def update_progress():
                nonlocal completed_count, failed_count, api_calls_made
                with progress_lock:
                    completed_count += 1
                    # Progress updates are now handled by PostgreSQL triggers when database is updated
                    logger.debug(f"Progress: {completed_count}/{total_rows} rows completed, {failed_count} failed")
            
            logger.info(f"Starting ThreadPoolExecutor for CSV {csv_id} with {total_rows} rows")
            print(f"DEBUG: Starting ThreadPoolExecutor for CSV {csv_id} with {total_rows} rows")
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(process_row, idx, row) for idx, row in df.iterrows()]
                for future in as_completed(futures):
                    idx, geojson_str, row_errors, api_call_made = future.result()
                    geojson_results[idx] = geojson_str
                    errors_per_row[idx] = '; '.join(row_errors)
                    
                    # Track failed rows and API calls
                    with progress_lock:
                        if row_errors:
                            failed_count += 1
                        if api_call_made:
                            api_calls_made += 1
                    
                    # Update progress
                    update_progress()
            logger.info(f"ThreadPoolExecutor completed for CSV {csv_id}, processed {completed_count} rows")
            print(f"DEBUG: ThreadPoolExecutor completed for CSV {csv_id}, processed {completed_count} rows")
            df['geojson'] = geojson_results
            df['errors'] = errors_per_row
            output = io.StringIO()
            df.to_csv(output, index=False)
            output.seek(0)
            processed_content = output.getvalue().encode('utf-8')
            csv_file.file_content = processed_content

            # Store final processing metrics
            processing_end_time = datetime.now(timezone.utc)
            
            # Handle timezone conversion for duration calculation
            try:
                start_time = csv_file.processing_started_at
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)
                processing_duration = (processing_end_time - start_time).total_seconds()
                csv_file.processing_duration_seconds = int(processing_duration)
            except Exception as duration_error:
                logger.error(f"Failed to calculate processing duration for CSV {csv_id}: {duration_error}")
                csv_file.processing_duration_seconds = None
            
            csv_file.successful_rows = completed_count - failed_count
            csv_file.failed_rows = failed_count
            csv_file.processing_completed_at = processing_end_time
            csv_file.lepton_api_calls_made = api_calls_made
            csv_file.tokens_consumed = api_calls_made  # Each successful API call consumes 1 token
            
            # Determine CSV status based on error types
            has_token_exhaustion = any("Your token allocation has been exhausted" in error for error in errors_per_row if error)
            has_lepton_api_credits = any("Lepton Maps API: Not enough credits" in error for error in errors_per_row if error)
            has_other_errors = any(error and "Your token allocation has been exhausted" not in error and "Lepton Maps API: Not enough credits" not in error for error in errors_per_row)
            
            if has_token_exhaustion and not has_other_errors and not has_lepton_api_credits:
                csv_file.status = 'partial'
                csv_file.error = 'Token allocation exhausted during processing'
            elif has_lepton_api_credits:
                csv_file.status = 'failed'
                csv_file.error = 'Lepton API credits exhausted'
            elif any(errors_per_row):
                csv_file.status = 'failed' 
                csv_file.error = 'Some rows failed, see errors column'
            else:
                csv_file.status = 'done'
                csv_file.error = None
            session.commit()
            logger.info(f"CSV {csv_id} processing completed with status: {csv_file.status}")
            print(f"DEBUG: CSV {csv_id} processing completed with status: {csv_file.status}")
            
            # PostgreSQL trigger will automatically broadcast completion event when status is updated
            logger.info(f"CSV {csv_id} marked as {csv_file.status} - PostgreSQL trigger will broadcast completion event")
        except Exception as e:
            logger.error(f"Error processing file: {str(e)}")
            csv_file = session.query(CSVFile).filter(CSVFile.id == csv_id).first()
            if csv_file:
                # Store processing metrics even in case of failure
                if csv_file.processing_started_at:
                    processing_end_time = datetime.now(timezone.utc)
                    
                    # Handle timezone conversion for duration calculation
                    try:
                        start_time = csv_file.processing_started_at
                        if start_time.tzinfo is None:
                            start_time = start_time.replace(tzinfo=timezone.utc)
                        processing_duration = (processing_end_time - start_time).total_seconds()
                        csv_file.processing_duration_seconds = int(processing_duration)
                    except Exception as duration_error:
                        logger.error(f"Failed to calculate processing duration for CSV {csv_id}: {duration_error}")
                        csv_file.processing_duration_seconds = None
                    
                    csv_file.processing_completed_at = processing_end_time
                
                # Store available metrics from local variables
                if 'completed_count' in locals():
                    csv_file.successful_rows = locals().get('completed_count', 0) - locals().get('failed_count', 0)
                    csv_file.failed_rows = locals().get('failed_count', 0)
                if 'api_calls_made' in locals():
                    csv_file.lepton_api_calls_made = locals().get('api_calls_made', 0)
                    csv_file.tokens_consumed = locals().get('api_calls_made', 0)
                
                csv_file.status = 'failed'
                csv_file.error = str(e)
                # Try to save whatever DataFrame is available
                try:
                    if 'df' in locals() and isinstance(df, pd.DataFrame):
                        # If geojson_results and errors_per_row exist, add them
                        if 'geojson_results' in locals() and len(geojson_results) == len(df):
                            df['geojson'] = geojson_results
                        if 'errors_per_row' in locals() and len(errors_per_row) == len(df):
                            df['errors'] = errors_per_row
                        output = io.StringIO()
                        df.to_csv(output, index=False)
                        output.seek(0)
                        csv_file.file_content = output.getvalue().encode('utf-8')
                except Exception as inner:
                    logger.error(f"Failed to save partial CSV on error: {inner}")
                session.commit()
                logger.info(f"CSV {csv_id} marked as failed due to exception: {str(e)}")
                
                # PostgreSQL trigger will automatically broadcast completion event when status is updated
                logger.info(f"CSV {csv_id} marked as failed - PostgreSQL trigger will broadcast completion event")
        except Exception as top_level_error:
            logger.error(f"Top-level exception in background processing for CSV {csv_id}: {str(top_level_error)}", exc_info=True)
            
            # Try to mark CSV as failed if session is available
            if session:
                try:
                    csv_file = session.query(CSVFile).filter(CSVFile.id == csv_id).first()
                    if csv_file:
                        csv_file.status = 'failed'
                        csv_file.error = f"Background processing failed: {str(top_level_error)}"
                        csv_file.processing_completed_at = datetime.now(timezone.utc)
                        session.commit()
                        
                        # PostgreSQL trigger will automatically broadcast completion event when status is updated
                        logger.info(f"CSV {csv_id} marked as failed (top-level) - PostgreSQL trigger will broadcast completion event")
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup after top-level error for CSV {csv_id}: {cleanup_error}")
        finally:
            if session:
                try:
                    session.close()
                    logger.info(f"Database session closed for CSV {csv_id}")
                except Exception as close_error:
                    logger.error(f"Failed to close database session for CSV {csv_id}: {close_error}")
            logger.info(f"Background processing thread completed for CSV {csv_id}")
    threading.Thread(target=process_csv_in_background, args=(csv_id, content, username, user_id)).start()
    
    return {
        "csv_id": csv_id, 
        "status": "pending",
        "token_info": {
            "available": user_token_status["remaining"],
            "total_rows": csv_row_count,
            "estimated_processed": min(user_token_status["remaining"], csv_row_count)
        }
    }

@router.get("/csv-status/{csv_id}")
def get_csv_status(csv_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    csv_file = db.query(CSVFile).filter(CSVFile.id == csv_id).first()
    if not csv_file:
        logger.info(f"Status check for CSV id={csv_id}: NOT FOUND")
        raise HTTPException(status_code=404, detail="CSV file not found")
    response = {"csv_id": csv_file.id, "status": csv_file.status}
    if csv_file.status == 'failed' and hasattr(csv_file, 'error') and csv_file.error:
        response["error"] = csv_file.error
    logger.info(f"Status check for CSV id={csv_id}: {response}")
    return response
@router.get("/csv-status-stream/{csv_id}")
async def stream_csv_status(csv_id: int, request: Request, hashed_token: str, username: str, db: Session = Depends(get_db)):
    """Stream real-time CSV processing status via Server-Sent Events with PostgreSQL notifications"""
    
    # Authenticate user by checking the hashed_token
    users = db.query(User).all()
    authenticated_user = None
    for user in users:
        if user.token and verify_password(user.token, hashed_token):
            authenticated_user = user
            break
    
    if not authenticated_user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Verify that the provided username matches the authenticated user
    if authenticated_user.username != username:
        raise HTTPException(status_code=403, detail="Username does not match token owner")

    # Verify CSV exists and user has access
    csv_file = db.query(CSVFile).filter(CSVFile.id == csv_id).first()
    if not csv_file:
        raise HTTPException(status_code=404, detail="CSV file not found")
    
    if csv_file.user_id != authenticated_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Subscribe to events for this CSV
    event_queue = await sse_manager.subscribe(csv_id)
    
    async def event_stream():
        try:
            # Send initial status
            initial_data = {
                "type": "init",
                "csv_id": csv_id,
                "status": csv_file.status,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            if csv_file.error:
                initial_data["error"] = csv_file.error
            if csv_file.successful_rows is not None:
                initial_data["successful_rows"] = csv_file.successful_rows
            if csv_file.failed_rows is not None:
                initial_data["failed_rows"] = csv_file.failed_rows
            if csv_file.total_rows is not None:
                initial_data["total_rows"] = csv_file.total_rows
            
            yield f"data: {json.dumps(initial_data)}\n\n"
            
            # If already completed, close connection immediately
            if csv_file.status in ['done', 'failed', 'partial']:
                logger.info(f"CSV {csv_id} already completed with status {csv_file.status}, closing SSE stream")
                return
            
            # Listen for PostgreSQL notifications
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info(f"Client disconnected for CSV {csv_id}")
                    break
                
                try:
                    # Wait for PostgreSQL notification with timeout for heartbeat
                    event_data = await asyncio.wait_for(event_queue.get(), timeout=30.0)
                    yield event_data
                    
                    # Check if processing is complete
                    try:
                        if event_data.startswith("data: "):
                            json_str = event_data[6:].strip()
                            event_obj = json.loads(json_str)
                            if event_obj.get("type") == "complete":
                                logger.info(f"Processing complete for CSV {csv_id}, closing SSE stream")
                                break
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.warning(f"Failed to parse event data for completion check: {e}")
                        continue
                        
                except asyncio.TimeoutError:
                    # Send heartbeat - PostgreSQL listener handles all status updates
                    heartbeat_data = {
                        "type": "heartbeat",
                        "csv_id": csv_id,
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                    yield f"data: {json.dumps(heartbeat_data)}\n\n"
                    logger.debug(f"Sent heartbeat for CSV {csv_id}")
                    
        except asyncio.CancelledError:
            logger.info(f"SSE connection cancelled for CSV {csv_id}")
        except Exception as e:
            logger.error(f"Error in SSE stream for CSV {csv_id}: {e}")
        finally:
            # Cleanup subscription
            await sse_manager.unsubscribe(csv_id, event_queue)
            logger.info(f"Cleaned up SSE subscription for CSV {csv_id}")
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.get("/csv/{csv_id}")
def get_csv_file(csv_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    csv_file = db.query(CSVFile).filter(CSVFile.id == csv_id).first()
    if not csv_file:
        raise HTTPException(status_code=404, detail="CSV file not found")
    if csv_file.status in ["pending", "processing"]:
        raise HTTPException(status_code=400, detail="CSV file is not ready yet. Current status: {}".format(csv_file.status))
    
    # Track download metrics
    user_id = current_user.get('user_id')
    download_time = datetime.now(timezone.utc)
    
    # Update CSV file download tracking
    csv_file.download_count += 1
    csv_file.last_downloaded_at = download_time
    if csv_file.first_downloaded_at is None:
        csv_file.first_downloaded_at = download_time
    
    # Update user download tracking
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.total_csvs_downloaded += 1
        user.last_csv_download_at = download_time
    
    db.commit()
    
    logger.info(f"CSV {csv_id} downloaded by user {user_id}. Total downloads: {csv_file.download_count}")
    
    return Response(
        content=csv_file.file_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={csv_file.filename}"}
    )

@router.get("/csvs")
def list_csvs(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    csvs = db.query(CSVFile).filter(CSVFile.user_id == current_user.get('user_id')).all()
    result = [
        {
            "id": csv.id,
            "filename": csv.filename,
            "username": csv.username,
            "user_id": csv.user_id,
            "created_at": csv.created_at.isoformat() if csv.created_at else None
        }
        for csv in csvs
    ]
    return JSONResponse(content=result) 