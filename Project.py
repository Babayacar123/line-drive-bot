import base64
from datetime import datetime
import json
import os
from fastapi import FastAPI, HTTPException, Request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    ImageMessageContent,
    MessageEvent,
    TextMessageContent,
)
import requests

app = FastAPI()

# ----------------- ตั้งค่า LINE API -----------------
CHANNEL_ACCESS_TOKEN = os.getenv(
    "CHANNEL_ACCESS_TOKEN",
    "ayO0FVOcJraznf53veVRi1I7eojB9HRTPVAbdas3qkfjA2kWYpWG9c0m77mHYuz16SAOkQqOO/WOx29vcq18iyDwTZr0OzgQ5f9TGAhwggfJwEI59RcU8PNhf8L3wbx4o527eHyZJ9wF9Dc9OgKlZgdB04t89/1O/w1cDnyilFU=",
)
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "7400f8927603c23a4ad12f81556c73d8")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ----------------- ตั้งค่า Google Drive -----------------
ROOT_FOLDER_ID = os.getenv(
    "ROOT_FOLDER_ID", "1mkIk_ErkO-kjENeZd8mqngHfbt4bm49j"
)
GAS_WEBAPP_URL = os.getenv("GAS_WEBAPP_URL", "")

user_company_state = {}


@app.get("/")
def health_check():
    return {"status": "LINE Bot is running"}


@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    body_str = body.decode("utf-8")

    try:
        handler.handle(body_str, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"


# 1. รับข้อความเลือกบริษัท
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event: MessageEvent):
    user_id = event.source.user_id
    text_input = event.message.text.strip().lower()

    if text_input in ["a", "บริษัท a", "บริษัทa"]:
        folder_name = "บริษัท A"
    elif text_input in ["b", "บริษัท b", "บริษัทb"]:
        folder_name = "บริษัท B"
    else:
        folder_name = "บริษัท C"

    user_company_state[user_id] = folder_name
    reply_text = (
        f"เลือกบันทึกที่: [{folder_name}] เรียบร้อยครับ\nสามารถส่งรูปภาพเข้ามาได้เลย"
    )

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )


# 2. รับรูปภาพแล้วส่งเข้า Google Drive
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event: MessageEvent):
    user_id = event.source.user_id
    message_id = event.message.id
    timestamp = datetime.fromtimestamp(event.timestamp / 1000.0)
    date_str = timestamp.strftime("%Y-%m-%d")

    current_company = user_company_state.get(user_id, "บริษัท C")

    try:
        # 1. ดึงภาพจาก LINE
        with ApiClient(configuration) as api_client:
            blob_api = MessagingApiBlob(api_client)
            image_content = blob_api.get_message_content(message_id)

        # 2. แปลงเป็น Base64
        base64_image = base64.b64encode(image_content).decode("utf-8")

        # 3. ส่งข้อมูลให้ Apps Script
        payload = {
            "root_folder_id": ROOT_FOLDER_ID,
            "company_name": current_company,
            "date_str": date_str,
            "file_name": f"{message_id}.jpg",
            "image_base64": base64_image,
        }

        # ยิง request พร้อมรองรับ Redirect และส่งแบบ JSON string ตรงๆ
        response = requests.post(
            GAS_WEBAPP_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            allow_redirects=True,
            timeout=30,
        )

        res_json = response.json()

        if res_json.get("status") != "success":
            raise Exception(res_json.get("message", "Upload failed"))

        # 4. แจ้งเตือนสำเร็จ
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(
                            text=(
                                "อัปโหลดรูปภาพลง Google Drive\n"
                                f"[{current_company} / {date_str}] เรียบร้อยแล้ว"
                            )
                        )
                    ],
                )
            )
    except Exception as e:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"เกิดข้อผิดพลาดในการบันทึก: {e}")],
                )
            )