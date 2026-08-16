import os
import io
import json
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent
)
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

app = FastAPI()

# ----------------- ตั้งค่า LINE API -----------------
CHANNEL_ACCESS_TOKEN = os.getenv(
    "CHANNEL_ACCESS_TOKEN",
    "ayO0FVOcJraznf53veVRi1I7eojB9HRTPVAbdas3qkfjA2kWYpWG9c0m77mHYuz16SAOkQqOO/WOx29vcq18iyDwTZr0OzgQ5f9TGAhwggfJwEI59RcU8PNhf8L3wbx4o527eHyZJ9wF9Dc9OgKlZgdB04t89/1O/w1cDnyilFU="
)
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "7400f8927603c23a4ad12f81556c73d8")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ----------------- ตั้งค่า Google Drive API -----------------
ROOT_FOLDER_ID = os.getenv("ROOT_FOLDER_ID", "1M8ho1aDYD3t6q0UPd63Ud7K0RUBndtkW")

SCOPES = ['https://www.googleapis.com/auth/drive']

# ดึง Path โฟลเดอร์ปัจจุบันที่ไฟล์โปรเจกต์นี้ตั้งอยู่
BASE_DIR = Path(__file__).resolve().parent
KEY_FILE_PATH = BASE_DIR / "service_account.json"

# รองรับทั้งแบบ Environment Variable (บนเซิร์ฟเวอร์ Cloud) และไฟล์ .json ในเครื่อง
service_account_env = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
if service_account_env:
    creds_dict = json.loads(service_account_env)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
else:
    creds = Credentials.from_service_account_file(str(KEY_FILE_PATH), scopes=SCOPES)

drive_service = build('drive', 'v3', credentials=creds)

# บันทึกสถานะโฟลเดอร์ที่ผู้ใช้แต่ละคนเลือก {user_id: "ชื่อโฟลเดอร์"}
user_company_state = {}


def get_or_create_folder(folder_name: str, parent_id: str) -> str:
    """ค้นหาโฟลเดอร์ย่อย หากยังไม่มีจะทำการสร้างขึ้นใหม่บน Google Drive"""
    query = (
        f"name = '{folder_name}' and '{parent_id}' in parents and "
        f"mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    results = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    items = results.get('files', [])

    if items:
        return items[0]['id']

    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id]
    }
    folder = drive_service.files().create(body=file_metadata, fields='id').execute()
    return folder.get('id')


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


# 1. ดักรับข้อความพิมพ์ชื่อบริษัท
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
    reply_text = f"เลือกบันทึกที่: [{folder_name}] เรียบร้อยครับ\nสามารถส่งรูปภาพเข้ามาได้เลย"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )


# 2. ดักรับรูปภาพแล้วอัปโหลดขึ้น Google Drive ตามโฟลเดอร์บริษัทและวันที่
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event: MessageEvent):
    user_id = event.source.user_id
    message_id = event.message.id
    timestamp = datetime.fromtimestamp(event.timestamp / 1000.0)
    date_str = timestamp.strftime("%Y-%m-%d")

    current_company = user_company_state.get(user_id, "บริษัท C")

    # 1. ตรวจสอบ/สร้างโฟลเดอร์ชื่อบริษัท
    company_folder_id = get_or_create_folder(current_company, ROOT_FOLDER_ID)
    # 2. ตรวจสอบ/สร้างโฟลเดอร์วันที่ภายในโฟลเดอร์บริษัท
    date_folder_id = get_or_create_folder(date_str, company_folder_id)

    # 3. ดาวน์โหลดรูปภาพจากเซิร์ฟเวอร์ LINE
    with ApiClient(configuration) as api_client:
        blob_api = MessagingApiBlob(api_client)
        image_content = blob_api.get_message_content(message_id)

        # 4. อัปโหลดรูปภาพขึ้น Google Drive
        file_metadata = {
            'name': f"{message_id}.jpg",
            'parents': [date_folder_id]
        }
        media = MediaIoBaseUpload(io.BytesIO(image_content), mimetype='image/jpeg')
        drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        # 5. ตอบกลับยืนยันใน LINE
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=f"อัปโหลดรูปภาพลง Google Drive\n[{current_company} / {date_str}] เรียบร้อยแล้ว")]
            )
        )