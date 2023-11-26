""" boltz_client helpers """

import httpx


def req_wrap(funcname, *args, **kwargs) -> dict:
    """request wrapper for httpx"""
    func = getattr(httpx, funcname)
    res = func(*args, timeout=30, **kwargs)
    res.raise_for_status()
    return (
        res.json()
        if kwargs["headers"]["Content-Type"] == "application/json"
        else {"text": res.text}
    )
