import httpx
from fastapi import APIRouter, Request
from core.logger import write_log

router = APIRouter()

@router.post("/api/test-uscall")
async def test_uscall(request: Request):
    data = await request.json()
    host = data.get("host")
    token = data.get("token")

    if not host or not token:
        return {"success": False}

    try:
        url = f"https://{host}/api/extenstatus?token={token}&tipo=all"

        async with httpx.AsyncClient(verify=False, timeout=5) as client:
            response = await client.get(url)

        if response.status_code == 200:
            write_log("INFO", "uscall", "Connection test successful")
            return {"success": True}
        else:
            write_log("ERROR", "uscall", f"Connection failed - status {response.status_code}")
            return {"success": False}

    except Exception as e:
        write_log("ERROR", "uscall", f"Connection error: {str(e)}")
        return {"success": False}