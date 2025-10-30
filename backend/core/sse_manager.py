import asyncio
import json
import logging
from typing import Dict, Set, Optional, Any
from datetime import datetime
import weakref
import threading
import select
import psycopg2
import psycopg2.extensions
import os

logger = logging.getLogger(__name__)


class SSEEventManager:
    """Manages Server-Sent Events for CSV processing status updates with PostgreSQL LISTEN/NOTIFY"""
    
    def __init__(self):
        self._subscribers: Dict[int, Set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        self._pg_connection = None
        self._pg_listener_thread = None
        self._loop = None
        self._shutdown_event = threading.Event()
        self._start_postgresql_listener()
    
    def _start_postgresql_listener(self):
        """Start PostgreSQL LISTEN connection in background thread"""
        try:
            # Get database URL - use same logic as main app (db/session.py)
            database_url = os.getenv('DATABASE_URL')
            if not database_url:
                # Build from individual env vars like main app does
                db_username = os.getenv("DB_USERNAME", "postgres")
                db_password = os.getenv("DB_PASSWORD", "postgres") 
                db_host = os.getenv("DB_HOST", "localhost")
                db_port = os.getenv("DB_PORT", "5432")
                db_name = os.getenv("DB_NAME", "mydb")
                database_url = f"postgresql://{db_username}:{db_password}@{db_host}:{db_port}/{db_name}"
            
            print(f"DEBUG: SSE Manager starting PostgreSQL listener")
            print(f"DEBUG: DB_HOST={os.getenv('DB_HOST')}, DB_USERNAME={os.getenv('DB_USERNAME')}")
            print(f"DEBUG: Constructed DATABASE_URL: {database_url}")
            logger.info(f"SSE Manager starting PostgreSQL listener with URL: {database_url}")
            logger.info(f"Environment: DB_HOST={os.getenv('DB_HOST')}, DB_USERNAME={os.getenv('DB_USERNAME')}")
            
            def postgresql_listener():
                """Background thread that listens for PostgreSQL notifications"""
                try:
                    # Connect to PostgreSQL
                    print("DEBUG: Attempting PostgreSQL connection...")
                    conn = psycopg2.connect(database_url)
                    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
                    cursor = conn.cursor()
                    
                    # Test connection and verify database
                    cursor.execute("SELECT current_database(), current_user, version();")
                    db_info = cursor.fetchone()
                    print(f"DEBUG: Connected to database: {db_info[0]}, user: {db_info[1]}")
                    logger.info(f"PostgreSQL connection established - Database: {db_info[0]}, User: {db_info[1]}")
                    
                    # Start listening to csv_status_change channel
                    cursor.execute("LISTEN csv_status_change;")
                    print("DEBUG: PostgreSQL LISTEN command executed successfully")
                    logger.info("PostgreSQL LISTEN connection established for csv_status_change")
                    
                    # Test that we can receive notifications by sending a test one
                    test_payload = '{"test": "sse_manager_connection_test", "timestamp": "' + datetime.now().isoformat() + '"}'
                    cursor.execute("SELECT pg_notify('csv_status_change', %s);", (test_payload,))
                    print("DEBUG: Sent test notification to verify LISTEN works")
                    
                    self._pg_connection = conn
                    
                    # Listen for notifications
                    while not self._shutdown_event.is_set():
                        if select.select([conn], [], [], 1) == ([], [], []):
                            continue  # Timeout, check shutdown event
                        
                        conn.poll()
                        while conn.notifies:
                            notify = conn.notifies.pop(0)
                            print(f"DEBUG: Received PostgreSQL notification: {notify.payload}")
                            logger.info(f"PostgreSQL notification received: {notify.payload}")
                            try:
                                # Handle the notification asynchronously
                                self._handle_pg_notification(notify.payload)
                            except Exception as e:
                                logger.error(f"Error handling PostgreSQL notification: {e}")
                                print(f"DEBUG: Error handling notification: {e}")
                                
                except Exception as e:
                    logger.error(f"PostgreSQL listener error: {e}")
                finally:
                    if self._pg_connection:
                        try:
                            self._pg_connection.close()
                        except:
                            pass
                    logger.info("PostgreSQL listener thread stopped")
            
            # Start the listener thread
            self._pg_listener_thread = threading.Thread(target=postgresql_listener, daemon=True)
            self._pg_listener_thread.start()
            
        except Exception as e:
            logger.error(f"Failed to start PostgreSQL listener: {e}")
    
    def _handle_pg_notification(self, payload: str):
        """Handle PostgreSQL notification and route to appropriate subscribers"""
        try:
            # Parse the notification payload
            data = json.loads(payload)
            csv_id = data.get('csv_id')
            
            if csv_id and csv_id in self._subscribers:
                # Format as SSE message
                event_data = {
                    "type": data.get('event_type', 'update'),
                    "csv_id": csv_id,
                    "status": data.get('status'),
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }
                
                # Add optional fields
                if data.get('error'):
                    event_data["error"] = data.get('error')
                if data.get('successful_rows') is not None:
                    event_data["successful_rows"] = data.get('successful_rows')
                if data.get('failed_rows') is not None:
                    event_data["failed_rows"] = data.get('failed_rows')
                if data.get('total_rows') is not None:
                    event_data["total_rows"] = data.get('total_rows')
                
                message = f"data: {json.dumps(event_data)}\n\n"
                
                # Send to all subscribers for this CSV (thread-safe)
                subscribers_copy = self._subscribers.get(csv_id, set()).copy()
                dead_queues = []
                
                for queue in subscribers_copy:
                    try:
                        # Use put_nowait to avoid blocking the listener thread
                        queue.put_nowait(message)
                    except asyncio.QueueFull:
                        logger.warning(f"Queue full for CSV {csv_id}, dropping event")
                        dead_queues.append(queue)
                    except Exception as e:
                        logger.error(f"Error sending event to subscriber: {e}")
                        dead_queues.append(queue)
                
                # Clean up dead queues (will be handled by main thread)
                if dead_queues:
                    asyncio.create_task(self._cleanup_dead_queues(csv_id, dead_queues))
                
                logger.info(f"PostgreSQL notification sent to {len(subscribers_copy) - len(dead_queues)} subscribers for CSV {csv_id}")
                
        except Exception as e:
            logger.error(f"Error handling PostgreSQL notification: {e}")
    
    async def _cleanup_dead_queues(self, csv_id: int, dead_queues: list):
        """Clean up dead queues asynchronously"""
        try:
            async with self._lock:
                if csv_id in self._subscribers:
                    for queue in dead_queues:
                        self._subscribers[csv_id].discard(queue)
                    if not self._subscribers[csv_id]:
                        del self._subscribers[csv_id]
        except Exception as e:
            logger.error(f"Error cleaning up dead queues: {e}")
    
    async def subscribe(self, csv_id: int) -> asyncio.Queue:
        """Subscribe to events for a specific CSV ID"""
        async with self._lock:
            if csv_id not in self._subscribers:
                self._subscribers[csv_id] = set()
            
            queue = asyncio.Queue(maxsize=100)
            self._subscribers[csv_id].add(queue)
            logger.info(f"New subscriber for CSV {csv_id}. Total subscribers: {len(self._subscribers[csv_id])}")
            return queue
    
    async def unsubscribe(self, csv_id: int, queue: asyncio.Queue):
        """Unsubscribe from events for a specific CSV ID"""
        async with self._lock:
            if csv_id in self._subscribers and queue in self._subscribers[csv_id]:
                self._subscribers[csv_id].discard(queue)
                if not self._subscribers[csv_id]:
                    del self._subscribers[csv_id]
                logger.info(f"Unsubscribed from CSV {csv_id}")
    
    def broadcast_sync(self, csv_id: int, event_type: str, data: Dict[str, Any]):
        """Thread-safe method to broadcast events from sync context"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._broadcast_async(csv_id, event_type, data))
            else:
                loop.run_until_complete(self._broadcast_async(csv_id, event_type, data))
        except RuntimeError:
            # No event loop in current thread, create a new one
            try:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(self._run_in_new_loop, csv_id, event_type, data)
                    future.result(timeout=1)
            except Exception as e:
                logger.error(f"Failed to broadcast event: {e}")
    
    def _run_in_new_loop(self, csv_id: int, event_type: str, data: Dict[str, Any]):
        """Run broadcast in a new event loop"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._broadcast_async(csv_id, event_type, data))
        finally:
            loop.close()
    
    async def _broadcast_async(self, csv_id: int, event_type: str, data: Dict[str, Any]):
        """Internal method to broadcast events"""
        if csv_id not in self._subscribers:
            return
        
        event_data = {
            "type": event_type,
            "csv_id": csv_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            **data
        }
        
        message = f"data: {json.dumps(event_data)}\n\n"
        
        # Get copy of subscribers to avoid modification during iteration
        async with self._lock:
            subscribers = self._subscribers.get(csv_id, set()).copy()
        
        # Send to all subscribers
        dead_queues = []
        for queue in subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning(f"Queue full for CSV {csv_id}, dropping event")
                dead_queues.append(queue)
            except Exception as e:
                logger.error(f"Error sending event to subscriber: {e}")
                dead_queues.append(queue)
        
        # Clean up dead queues
        if dead_queues:
            async with self._lock:
                for queue in dead_queues:
                    self._subscribers.get(csv_id, set()).discard(queue)
        
        logger.debug(f"Broadcasted {event_type} event to {len(subscribers) - len(dead_queues)} subscribers for CSV {csv_id}")
    
    async def send_heartbeat(self, csv_id: int):
        """Send heartbeat to keep connections alive"""
        await self._broadcast_async(csv_id, "heartbeat", {})
    
    def broadcast_start(self, csv_id: int, total_rows: int):
        """Broadcast processing start event"""
        self.broadcast_sync(csv_id, "start", {
            "total_rows": total_rows,
            "status": "processing"
        })
    
    def broadcast_progress(self, csv_id: int, completed: int, total: int, failed: int = 0):
        """Broadcast progress update event"""
        percentage = round((completed / total) * 100, 1) if total > 0 else 0
        self.broadcast_sync(csv_id, "progress", {
            "completed": completed,
            "total": total,
            "failed": failed,
            "percentage": percentage
        })
    
    def broadcast_complete(self, csv_id: int, status: str, error: Optional[str] = None):
        """Broadcast processing complete event"""
        data = {"status": status}
        if error:
            data["error"] = error
        self.broadcast_sync(csv_id, "complete", data)
    
    def get_subscriber_count(self, csv_id: int) -> int:
        """Get number of subscribers for a CSV ID"""
        return len(self._subscribers.get(csv_id, set()))
    
    def shutdown(self):
        """Shutdown the PostgreSQL listener and clean up resources"""
        logger.info("Shutting down SSE Event Manager")
        self._shutdown_event.set()
        
        if self._pg_listener_thread and self._pg_listener_thread.is_alive():
            self._pg_listener_thread.join(timeout=5)
        
        if self._pg_connection:
            try:
                self._pg_connection.close()
            except:
                pass


# Global SSE manager instance
sse_manager = SSEEventManager()