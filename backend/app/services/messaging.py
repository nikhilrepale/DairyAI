import os, requests
from urllib.parse import quote

class MessagingAdapter:
    def send(self, phone, message): raise NotImplementedError

class ClickToChatAdapter(MessagingAdapter):
    """Adapter pattern: free demo path opens a prefilled WhatsApp chat; user presses Send."""
    def send(self, phone, message):
        digits="".join(ch for ch in (phone or "") if ch.isdigit())
        if not digits: raise ValueError("Customer phone number is missing")
        if len(digits)==10: digits="91"+digits
        return {"mode":"click_to_chat","sent":False,"requires_user_send":True,
                "url":f"https://wa.me/{digits}?text={quote(message)}"}

class MetaCloudAdapter(MessagingAdapter):
    """Adapter for official WhatsApp Cloud API. Template and pricing rules apply."""
    def send(self, phone, message):
        token=os.getenv("WHATSAPP_ACCESS_TOKEN")
        number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        version=os.getenv("WHATSAPP_API_VERSION","v23.0")
        if not token or not number_id: raise RuntimeError("WhatsApp Cloud API credentials missing")
        digits="".join(ch for ch in phone if ch.isdigit())
        url=f"https://graph.facebook.com/{version}/{number_id}/messages"
        payload={"messaging_product":"whatsapp","to":digits,"type":"text","text":{"body":message}}
        r=requests.post(url,json=payload,headers={"Authorization":f"Bearer {token}"},timeout=30)
        r.raise_for_status()
        return {"mode":"cloud_api","sent":True,"response":r.json()}

class MessagingFactory:
    @staticmethod
    def create():
        return MetaCloudAdapter() if os.getenv("WHATSAPP_MODE")=="cloud_api" else ClickToChatAdapter()
