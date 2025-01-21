from pydantic import BaseModel


class User(BaseModel):
    user_id: str
    username: str


async def get_current_user():
    # XXX TODO
    return None
