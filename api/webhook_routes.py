from fastapi import APIRouter
from datetime import datetime
from services.monitor_service import send_webhook, load_config

router = APIRouter(prefix="/api/webhooks")


@router.post("/test/{event_type}")
def test_webhook(event_type: str):

    config = load_config()
    webhook_cfg = config.get("webhooks", {}).get(event_type)

    if not webhook_cfg:
        return {"error": "Webhook type not configured"}

    payload = {
        "test": True,
        "message": "Webhook TEST triggered manually from dashboard",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sample_data": {
            "device": "3660",
            "status": "online",
            "latency": 2
        }
    }

    send_webhook(event_type, payload)

    return {"status": "Webhook test sent"}