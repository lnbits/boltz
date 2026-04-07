"""boltz_client helpers"""

from httpx import AsyncClient


async def req_wrap(funcname, *args, **kwargs) -> dict:
    """request wrapper for httpx"""
    async with AsyncClient(follow_redirects=True) as client:
        func = getattr(client, funcname)
        res = await func(*args, timeout=30, **kwargs)
        res.raise_for_status()
        return (
            res.json()
            if kwargs["headers"]["Content-Type"] == "application/json"
            else {"text": res.text}
        )
