from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, TypedDict

class AgentState(TypedDict):
    """The state of the agent."""
    messages: Annotated[list[BaseMessage], add_messages]
    feature_description: str
    final_report: Optional[Dict[str, Any]]
