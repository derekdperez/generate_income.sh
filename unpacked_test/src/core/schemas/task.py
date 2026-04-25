from uuid import UUID, uuid4
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
import datetime

class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    plugin: str
    target: str
    config: Dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    worker_id: Optional[str] = None
    lease_expires: Optional[datetime.datetime] = None