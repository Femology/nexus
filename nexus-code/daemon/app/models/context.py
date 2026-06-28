from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any
from datetime import datetime

from .request import NexusPayload

class ToolLoopState(BaseModel):
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    tool_results: List[Dict[str, Any]] = Field(default_factory=list)
    iteration_count: int = 0

class SessionObject(BaseModel):
    session_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_active: datetime = Field(default_factory=datetime.utcnow)
    conversation_summary: str = ""
    turn_count: int = 0
    memory_graph_partition_id: str = ""
    model_alias: str
    active_tool_loop: Optional[ToolLoopState] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class CacheResult(BaseModel):
    hit: bool
    tier: str
    response: str

class RequestContext(BaseModel):
    payload: NexusPayload
    api_key: str
    session: SessionObject
    cache_result: Optional[CacheResult] = None
    memory_context: str = ""
    memory_nodes_count: int = 0
    filtered_context: Optional[Dict[str, Any]] = None
    assembled_prompt: Optional[list] = None
    compressed_prompt: Optional['CompressedPrompt'] = None
    pre_compression_tokens: int = 0
    post_compression_tokens: int = 0
    provider_response: Optional[dict] = None

class NodeSummary(BaseModel):
    id: str
    type: str
    text: str
    score: float

class MemorySnippetBundle(BaseModel):
    text: str
    node_count: int
    nodes: List[NodeSummary]

class FilteredContextBundle(BaseModel):
    active_file: Optional[Dict[str, Any]] = None
    selection: Optional[Dict[str, Any]] = None
    open_tabs: List[Dict[str, Any]] = Field(default_factory=list)
    workspace_structure: Optional[str] = None
    git_diff: Optional[str] = None
    diagnostics: Optional[str] = None
    terminal_snapshot: Optional[str] = None

class TaggedPrompt(BaseModel):
    messages: List[Dict[str, Any]]
    segment_tags: Dict[int, str]
    pre_token_count: int

class CompressedPrompt(BaseModel):
    messages: List[Dict[str, Any]]
    post_compression_tokens: int
    compression_ratio: float
