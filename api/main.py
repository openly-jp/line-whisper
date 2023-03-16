from fastapi import FastAPI, Request, HTTPException, Response, BackgroundTasks
from starlette.middleware.cors import CORSMiddleware
import time
import stripe
import math
import os
import requests
from supabase import create_client, Client
from pydub import AudioSegment
from linebot import (
    LineBotApi, WebhookHandler, WebhookParser
)
from linebot.exceptions import (
    InvalidSignatureError
)

from linebot.models import (
    MessageEvent, FileMessage, TextSendMessage, AudioMessage, VideoMessage
)
import openai

from errors import TranscriptionFailureError, FileSizeError, FileExtensionError, FileCorruptionError, UsageLimitError

LINEAPI_ACCESS_TOKEN = os.getenv("LINEAPI_ACCESS_TOKEN")
LINEAPI_SECRET = os.getenv("LINEAPI_SECRET")
line_bot_api = LineBotApi(LINEAPI_ACCESS_TOKEN)
parser = WebhookParser(LINEAPI_SECRET)
handler = WebhookHandler(LINEAPI_SECRET)

openai.organization = os.getenv("OPENAI_ORGANIZATION")
openai.api_key = os.getenv("OPENAI_API_KEY")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET_KEY = os.getenv("STRIPE_WEBHOOK_SECRET_KEY")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

ACCEPT_FILE_EXTENSIONS = ["m4a", "mp3", "mp4", "wav"]
CONTENT_TYPE_EXTENSION_MAP = {
    "audio/aac": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mpeg3": "mp3",
    "video/mp4": "mp4",
    "video/mpeg4": "mp4",
    "audio/wav": "wav",
}
LIMITATION_SEC = int(os.getenv("LIMITATION_SEC"))
LIMITATION_FILE_SIZE_MB = int(os.getenv("LIMITATION_FILE_SIZE_MB"))
LIMITATION_FILE_SIZE = LIMITATION_FILE_SIZE_MB * 1024 * 1024

@app.post("/callback")
async def handle_request(request: Request):
    body = (await request.body()).decode("utf-8")
    signature = request.headers.get("X-Line-Signature", "")
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature. Please check your channel access token/channel secret.")

@app.get("/health")
async def health():
    return "ok"

def stripe_callback_postprocess(event):
    # TODO: rollback処理の追加
    if event.type == 'payment_intent.succeeded':
        # get session_id
        session = stripe.checkout.Session.list(payment_intent=event.data.object.id).data[0]
        session_id = session.id
        # get customer_id
        customer_id = session.customer
        # get line_id
        customer = stripe.Customer.retrieve(customer_id)
        line_id = customer.name
        # create expanded request to get line_items
        expanded_session = stripe.checkout.Session.retrieve(session_id, expand=['line_items'])
        line_item = expanded_session.line_items.data[0]
        # get product_id
        product_id = stripe.Price.retrieve(line_item.price.id).product
        # get product
        product = stripe.Product.retrieve(product_id)
        # get time of product
        product_min = int(product.metadata["min"])
        
        # update user_info
        # TODO: データベースをロックしなければ、書き換え中に認識タスクを実行される可能性があり、不正使用につながる
        data = supabase.table('user_info').select("remaining_sec").filter('id', 'eq', line_id).execute().data
        if len(data) == 0:
            # TODO: 実際にはデフォルト値である300sを追加するべき
            supabase.table('user_info').insert({'id': line_id, 'stripe_customer_id': customer_id, 'remaining_sec': product_min * 60}).execute()
        else:
            old_remaining_sec = data[0]['remaining_sec']
            remaining_sec = old_remaining_sec + product_min * 60
            supabase.table('user_info').update({'remaining_sec': remaining_sec}).filter('id', 'eq', line_id).execute()
        line_bot_api.push_message(line_id, TextSendMessage(text=f"文字起こし時間が{product_min}秒追加されたぞ!"))
        print("payment process done")
    elif event.type == 'payment_intent.payment_failed':
        # TODO: これはカードが不正なときなども発生する。そのため、これは使わないほうがいい
        line_bot_api.push_message(line_id, TextSendMessage(text=f"支払いに失敗したようじゃ...再度お試しくだされ！"))
        print("payment failed")
    elif event.type == 'payment_intent.cancelled':
        line_bot_api.push_message(line_id, TextSendMessage(text=f"支払いがキャンセルされたようじゃ...再度お試しくだされ！"))
        print("payment cancelled")
    else:
        raise Exception("unknown event type")

@app.post("/stripe_callback")
async def handle_stripe_callback(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(
            body, signature, STRIPE_WEBHOOK_SECRET_KEY
        )
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        return Response(status_code=401)
    except ValueError as e:
        # Invalid payload
        raise Response(status_code=400)
    # execute postprocess on another process
    background_tasks.add_task(stripe_callback_postprocess, event)
    #stripe_callback_postprocess(event)
    # return 200 status code
    return Response(status_code=200)

# TODO: CSRF対策
@app.post("/checkout")
async def get_checkout_url(request: Request):
    body = await request.json()
    user_id = body["user_id"]
    price_id = body["price_id"]
    # データベースからcutomer_idを取得
    # 存在しない場合は新規作成
    data = supabase.table('user_info').select('stripe_customer_id').filter('id', 'eq', user_id).execute().data
    # check if data is null
    if len(data) == 0 or data[0]["stripe_customer_id"] is None or data[0]["stripe_customer_id"] == "":
        customer = stripe.Customer.create(
            name=user_id,
        )
        supabase.table('user_info').upsert({'id': user_id, 'stripe_customer_id': customer.id}).execute()
        print("new customer created")
    else:
        customer = stripe.Customer.retrieve(data[0]["stripe_customer_id"])
    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{
            'price': price_id,
            'quantity': 1,
        }],
        mode='payment',
        customer=customer.id,
        success_url=os.getenv("SUCCESS_URL"),
        cancel_url=os.getenv("CANCEL_URL"),
    )
    # redirect to checkout
    return {"url": session.url}

@app.get("/products")
async def get_all_products():
    products = stripe.Product.list()
    prices = stripe.Price.list()
    result_products = []
    for product in products["data"]:
        result_product = {}
        result_product["id"] = product["id"]
        result_product["name"] = product["name"]
        result_product["description"] = product["description"]
        result_product["images"] = product["images"]
        result_product["price"] = None
        # 各商品につき価格が一つであると仮定している
        for price in prices["data"]:
            if price["product"] == product["id"]:
                result_product["price"] = {}
                result_product["price"]["id"] = price["id"]
                result_product["price"]["unit_amount"] = price["unit_amount"]
                break
        result_products.append(result_product)

    return {"products": result_products}

@handler.add(MessageEvent, message=FileMessage)
def handle_audio_file(event):
    message_id = event.message.id
    message_content = line_bot_api.get_message_content(message_id)
    handle_message_content(event, message_content)

@handler.add(MessageEvent, message=AudioMessage)
def handle_audio_message(event):
    message_id = event.message.id
    CONTENT_READY_URL = f"https://api-data.line.me/v2/bot/message/{message_id}/content/transcoding"
    headers = {'Authorization': f'Bearer {LINEAPI_ACCESS_TOKEN}'}

    status = requests.get(CONTENT_READY_URL, headers=headers).json().get("status", "")
    while status == "processing":
        time.sleep(1)
        status = requests.get(CONTENT_READY_URL, headers=headers).json().get("status", "")
    if status == "succeeded":
        message_content = line_bot_api.get_message_content(message_id)
        handle_message_content(event, message_content)
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="音声ファイルの取得に失敗したのじゃ・・・"))

@handler.add(MessageEvent, message=VideoMessage)
def handle_video_message(event):
    message_id = event.message.id
    CONTENT_READY_URL = f"https://api-data.line.me/v2/bot/message/{message_id}/content/transcoding"
    headers = {'Authorization': f'Bearer {LINEAPI_ACCESS_TOKEN}'}

    status = requests.get(CONTENT_READY_URL, headers=headers).json().get("status", "")
    while status == "processing":
        time.sleep(1)
        status = requests.get(CONTENT_READY_URL, headers=headers).json().get("status", "")
    if status == "succeeded":
        message_content = line_bot_api.get_message_content(message_id)
        handle_message_content(event, message_content)
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="動画ファイルの取得に失敗したのじゃ・・・"))


@handler.default()
def default(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="音声ファイルを送信するのじゃ！"))

def handle_message_content(event, message_content):
    #matched_extension = re.search(r'\b(?:%s)\b' % '|'.join(ACCEPT_FILE_EXTENSIONS), message_content.content_type)
    matched_extension = CONTENT_TYPE_EXTENSION_MAP.get(message_content.content_type, "")
    if matched_extension:
        file_id = event.message.id
        audio_file_path = f'/audio/{file_id}.{matched_extension}'
        total_file_size = 0
        with open(audio_file_path, 'wb') as fd:
            # TODO: ファイル書き込みを行わずに、直接openai.Audio.transcribeに渡す
            for chunk in message_content.iter_content():
                # check file size is under 25MB
                if total_file_size > LIMITATION_FILE_SIZE:
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text=f"ファイルサイズが大きすぎるようじゃ・・・\n{LIMITATION_FILE_SIZE_MB}MB以下のファイルを送信するのじゃ！"))
                    return
                fd.write(chunk)
                total_file_size += len(chunk)
        try:
            user_id = event.source.user_id
            text, additional_comment = transcribe(audio_file_path, matched_extension, user_id)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=text + "\n\n" + additional_comment if additional_comment else text))
        except TranscriptionFailureError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="書き起こしに失敗したのじゃ・・・もう一度お試しくだされ！"))
        except FileSizeError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"ファイルサイズが大きすぎるみたいじゃ・・・\n{LIMITATION_FILE_SIZE_MB}MB以下のファイルを送信するのじゃ！"))
        except FileCorruptionError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="ファイルが壊れているみたいじゃ・・・"))
        except UsageLimitError as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="利用制限時間を超えちゃうようじゃ・・・\n残りの利用可能時間は" + get_remaining_time_text(e.remaining_sec) + "らしいぞ！"))
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="予期せぬエラーが発生したのじゃ・・・"))
        finally:
            os.remove(audio_file_path)
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="対応していないファイル形式のようじゃの・・・\n以下のファイル形式で試すのじゃ!\n" + ", ".join(ACCEPT_FILE_EXTENSIONS)))


def transcribe(audio_file_path, extension, user_id):
    try:
        audio = AudioSegment.from_file(audio_file_path, extension)
    except Exception as e:
        raise FileCorruptionError
    additional_comment = None
    duration_sec = audio.duration_seconds
    data = supabase.table('user_info').select('usage_sec').filter('id', 'eq', user_id).execute().data
    if len(data) == 0:
        old_usage_sec = 0
    else:
        old_usage_sec = data[0]['usage_sec']
    usage_sec = duration_sec + old_usage_sec
    supabase.table('user_info').upsert({'id': user_id, 'usage_sec': LIMITATION_SEC if usage_sec > LIMITATION_SEC else usage_sec}).execute()

    if usage_sec > LIMITATION_SEC:
        remaining_sec = LIMITATION_SEC - old_usage_sec
        if remaining_sec < 1:
            raise UsageLimitError(remaining_sec=0)
        else:
            # transcribe only remaining time
            # PyDub handles time in milliseconds
            audio = audio[:math.floor(remaining_sec * 1000)]
            # PyDub cannot export m4a file so convert it to mp3
            audio.export(audio_file_path, format=extension if extension != "m4a" else "mp3")
            additional_comment = "利用制限時間を超えたようじゃ、冒頭の" + get_remaining_time_text(remaining_sec) + "だけ書き起こしたぞ！"
    audio_file= open(audio_file_path, "rb")
    try:
        # TODO: 以下の部分を非同期に行うことで他のユーザーのリクエストを処理できるようにする
        transcript = openai.Audio.transcribe("whisper-1", audio_file, language="ja")
        text = transcript.get("text", "")
        return text, additional_comment
    except Exception as e:
        supabase.table('user_info').upsert({'id': user_id, 'usage_sec': old_usage_sec}).execute()
        raise TranscriptionFailureError
    finally:
        audio_file.close()

def get_remaining_time_text(remaining_sec):
    if remaining_sec < 60:
        remaining_time_text = str(int(remaining_sec)) + "秒"
    else:
        remaining_time_text = str(int(remaining_sec // 60)) + "分"
    return remaining_time_text