from pydantic import BaseModel


class User(BaseModel):
    user_id: str
    username: str


def get_current_user():
    # XXX TODO
    async def _authenticate():
        return None

    return _authenticate
