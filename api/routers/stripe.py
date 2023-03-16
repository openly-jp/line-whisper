from fastapi import APIRouter, Depends, HTTPException, Request, Response, BackgroundTasks
import stripe
import os
from supabase import create_client
from linebot.models import TextSendMessage
from linebot import LineBotApi

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET_KEY = os.getenv("STRIPE_WEBHOOK_SECRET_KEY")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

LINEAPI_ACCESS_TOKEN = os.getenv("LINEAPI_ACCESS_TOKEN")
LINEAPI_SECRET = os.getenv("LINEAPI_SECRET")
line_bot_api_client = LineBotApi(LINEAPI_ACCESS_TOKEN)

router = APIRouter(
    prefix="/stripe",
    tags=["stripe"],
    responses={404: {"description": "Not found"}},
)

@router.post("/checkout")
async def checkout(request: Request):
    body = await request.json()
    user_id = body["user_id"]
    price_id = body["price_id"]
    # データベースからcutomer_idを取得
    # 存在しない場合は新規作成
    data = supabase_client.table('user_info').select('stripe_customer_id').filter('id', 'eq', user_id).execute().data
    # check if data is null
    if len(data) == 0 or data[0]["stripe_customer_id"] is None or data[0]["stripe_customer_id"] == "":
        customer = stripe.Customer.create(
            name=user_id,
        )
        supabase_client.table('user_info').upsert({'id': user_id, 'stripe_customer_id': customer.id}).execute()
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

@router.get("/products")
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

@router.post("/callback")
async def callback(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(
            body, signature, STRIPE_WEBHOOK_SECRET_KEY
        )
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        return HTTPException(status_code=401, detail="Invalid signature")
    except ValueError as e:
        # Invalid payload
        raise HTTPException(status_code=400, detail="Invalid payload")
    # execute postprocess on another process
    background_tasks.add_task(stripe_callback_postprocess, event)
    #stripe_callback_postprocess(event)
    # return 200 status code
    return Response(status_code=200)

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
        data = supabase_client.table('user_info').select("remaining_sec").filter('id', 'eq', line_id).execute().data
        if len(data) == 0:
            # TODO: 実際にはデフォルト値である300sを追加するべき
            supabase_client.table('user_info').insert({'id': line_id, 'stripe_customer_id': customer_id, 'remaining_sec': product_min * 60}).execute()
        else:
            old_remaining_sec = data[0]['remaining_sec']
            remaining_sec = old_remaining_sec + product_min * 60
            supabase_client.table('user_info').update({'remaining_sec': remaining_sec}).filter('id', 'eq', line_id).execute()
        line_bot_api_client.push_message(line_id, TextSendMessage(text=f"文字起こし時間が{product_min}秒追加されたぞ!"))
    elif event.type == 'payment_intent.payment_failed':
        # TODO: これはカードが不正なときなども発生する。そのため、これは使わないほうがいい
        line_bot_api_client.push_message(line_id, TextSendMessage(text=f"支払いに失敗したようじゃ...再度お試しくだされ！"))
    elif event.type == 'payment_intent.cancelled':
        line_bot_api_client.push_message(line_id, TextSendMessage(text=f"支払いがキャンセルされたようじゃ...再度お試しくだされ！"))
    else:
        raise Exception("unknown event type")