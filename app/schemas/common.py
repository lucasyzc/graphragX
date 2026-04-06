from typing import Literal

from pydantic import BaseModel

Role = Literal["viewer", "editor", "admin"]


class MessageResponse(BaseModel):
    message: str
