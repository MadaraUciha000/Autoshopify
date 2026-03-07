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

# Unicode bold text mapping for better compatibility
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

def format_proxy(proxy_str):
    """Format proxy string to httpx compatible format"""
    if not proxy_str:
        return None
    
    proxy_str = proxy_str.strip()
    if not proxy_str:
        return None

    # If already a URL
    if proxy_str.startswith(('http://', 'https://', 'socks4://', 'socks5://')):
        return proxy_str

    parts = proxy_str.split(':')
    
    # IP:PORT (2 parts)
    if len(parts) == 2:
        return f"http://{proxy_str}"
    
    # IP:PORT:USER:PASS (4 parts)
    if len(parts) == 4:
        ip, port, user, pw = parts
        return f"http://{user}:{pw}@{ip}:{port}"
    
    # HOST:PORT:USER:PASS (4 parts with hostname)
    if len(parts) == 4 and not parts[0].replace('.', '').isdigit():
        host, port, user, pw = parts
        return f"http://{user}:{pw}@{host}:{port}"
    
    # user:pass@host:port format
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
    """Mask credit card number for display (show last 4 only)"""
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
            # Try products.json first
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
                    # Sort by price and return cheapest
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
    
    result = {
        "status": "Decline",
        "site": "Dead",
        "amount": "$0.00",
        "response": "Unknown Error",
        "proxy": "Dead",
        "time": "0s",
        "card": "Unknown"
    }
    
    # Parse CC
    try:
        parts = cc.split('|')
        if len(parts) < 4:
            result["response"] = "Invalid CC Format (use cc|mm|yy|cvv)"
            result["time"] = f"{time.time() - start_time:.2f}s"
            result["card"] = cc
            return result
        cc_num, mon, year, cvv = parts[0], parts[1], parts[2], parts[3]
        
        # Store full card for response
        result["card"] = cc
        
        # Handle 2-digit year
        if len(year) == 2:
            year = f"20{year}"
    except Exception:
        result["response"] = "Invalid CC Format"
        result["time"] = f"{time.time() - start_time:.2f}s"
        result["card"] = cc
        return result
    
    # Format site URL
    if not site.startswith(('http://', 'https://')):
        site = f"https://{site}"
    site = site.rstrip('/')
    
    # Format proxy
    proxy_url = None
    try:
        if proxy_str:
            proxy_url = format_proxy(proxy_str)
            result["proxy"] = "Working" if proxy_str else "None"
        else:
            result["proxy"] = "None"
    except ValueError as e:
        result["response"] = str(e)
        result["proxy"] = "Invalid Format"
        result["time"] = f"{time.time() - start_time:.2f}s"
        result["card"] = cc
        return result
    
    # Create httpx client with proxy
    client_args = {
        'follow_redirects': True,
        'timeout': HTTP_TIMEOUT,
        'verify': False
    }
    
    if proxy_url:
        client_args['proxies'] = proxy_url
    
    shop = ShopifyAuto(proxy=proxy_url)
    
    # Process checkout with proper retry logic
    try:
        async with httpx.AsyncClient(**client_args) as client:
            # STEP 1: Get cheapest product
            print(f"{BOLD_MAP['CHECK']} Finding cheapest product...")
            product = await shop.get_cheapest_product(client, site)
            
            if not product:
                result["response"] = "No products found"
                result["site"] = "Dead"
                result["time"] = f"{time.time() - start_time:.2f}s"
                return result
            
            variant_id = product['variant_id']
            price = product['price_str']
            product_handle = product.get('handle')
            
            result["site"] = "Working"
            result["amount"] = f"${price}"
            
            # STEP 2: Add to cart
            print(f"{BOLD_MAP['CHECK']} Adding to cart...")
            
            # Visit product page to get cookies
            if product_handle:
                await client.get(f"{site}/products/{product_handle}", headers=shop.get_headers())
            
            # Add to cart
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
                result["response"] = f"Failed to add to cart: {add_response.status_code}"
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
            
            # Visit checkout
            await client.get(f"{site}/checkout", headers=checkout_headers)
            
            # POST to cart to proceed
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
            
            # Extract tokens from HTML
            session_token_match = re.search(
                r'name="serialized-sessionToken"\s+content="&quot;([^"]+)&quot;"', 
                response_text
            )
            session_token = session_token_match.group(1) if session_token_match else None
            
            queue_token = find_between(response_text, 'queueToken&quot;:&quot;', '&quot;')
            stable_id = find_between(response_text, 'stableId&quot;:&quot;', '&quot;')
            payment_method_id = find_between(response_text, 'paymentMethodIdentifier&quot;:&quot;', '&quot;')
            
            if not all([session_token, queue_token, stable_id, payment_method_id]):
                result["response"] = "Failed to extract checkout tokens"
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
                result["response"] = "Tokenization Failed"
                result["time"] = f"{time.time() - start_time:.2f}s"
                return result
            
            # STEP 5: Submit GraphQL payment with proper retry logic
            print(f"{BOLD_MAP['CHECK']} Submitting payment...")
            
            graphql_url = f"{site}/checkouts/unstable/graphql"
            
            # FIXED: Proper retry logic for soft errors
            retry_count = 0
            max_soft_retries = 3
            soft_error_retried = False
            
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
                
                # Generate random page ID
                page_id = f"{random.randint(10000000, 99999999):08x}-{random.randint(1000, 9999):04X}-{random.randint(1000, 9999):04X}-{random.randint(1000, 9999):04X}-{random.randint(100000000000, 999999999999):012X}"
                
                graphql_payload = {
                    'query': 'mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!,$metafields:[MetafieldInput!],$postPurchaseInquiryResult:PostPurchaseInquiryResultCode,$analytics:AnalyticsInput){submitForCompletion(input:$input attemptToken:$attemptToken metafields:$metafields postPurchaseInquiryResult:$postPurchaseInquiryResult analytics:$analytics){...on SubmitSuccess{receipt{...ReceiptDetails __typename}__typename}...on SubmitAlreadyAccepted{receipt{...ReceiptDetails __typename}__typename}...on SubmitFailed{reason __typename}...on SubmitRejected{errors{...on NegotiationError{code localizedMessage __typename}__typename}__typename}...on Throttled{pollAfter pollUrl queueToken __typename}...on CheckpointDenied{redirectUrl __typename}...on SubmittedForCompletion{receipt{...ReceiptDetails __typename}__typename}__typename}}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token __typename}...on ProcessingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id __typename}...on FailedReceipt{id processingError{...on PaymentFailed{code messageUntranslated __typename}__typename}__typename}__typename}',
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
                                            'variantId': f'gid://shopify/SuccessfuluctVariant/{variant_id}',
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
                    
                    # Check for errors
                    completion = result_data.get('data', {}).get('submitForCompletion', {})
                    
                    # Extract receipt ID if present
                    receipt_id = None
                    if completion.get('receipt'):
                        receipt_id = completion['receipt'].get('id')
                    
                    # Check for errors
                    errors = completion.get('errors', [])
                    if errors:
                        error_codes = [e.get('code') for e in errors if 'code' in e]
                        
                        # FIXED: Soft errors we can retry - now properly handles all 3 attempts
                        soft_errors = ['TAX_NEW_TAX_MUST_BE_ACCEPTED', 'WAITING_PENDING_TERMS']
                        only_soft = all(code in soft_errors for code in error_codes)
                        
                        if only_soft and retry_count < max_soft_retries:
                            print(f"   {BOLD_MAP['WARN']} Soft errors detected: {', '.join(error_codes)}")
                            print(f"   {BOLD_MAP['CHECK']} Retrying ({retry_count}/{max_soft_retries})...")
                            soft_error_retried = True
                            await asyncio.sleep(2)  # Wait before retry
                            continue  # Go to next retry attempt
                        else:
                            result["response"] = f"{', '.join(error_codes)}"
                            result["status"] = BOLD_MAP['DECLINED']
                            result["time"] = f"{time.time() - start_time:.2f}s"
                            return result
                    
                    # Check for explicit failure
                    if completion.get('reason'):
                        result["response"] = f"Failed: {completion['reason']}"
                        result["status"] = BOLD_MAP['DECLINED']
                        result["time"] = f"{time.time() - start_time:.2f}s"
                        return result
                    
                    # If we got a receipt ID, poll for result
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
                                    result["response"] = "OTP_REQUIRED"
                                    result["time"] = f"{time.time() - start_time:.2f}s"
                                    return result
                                elif typename == 'FailedReceipt':
                                    error = receipt.get('processingError', {})
                                    code = error.get('code', 'Unknown')
                                    result["response"] = f"{code}"
                                    result["status"] = BOLD_MAP['DECLINED']
                                    result["time"] = f"{time.time() - start_time:.2f}s"
                                    return result
                        
                        result["response"] = "Polling timeout"
                        result["status"] = BOLD_MAP['ERROR']
                        result["time"] = f"{time.time() - start_time:.2f}s"
                        return result
                    
                    # If we got here with no errors but no receipt, assume success
                    result["status"] = BOLD_MAP['SUCCESS']
                    result["response"] = "Payment processed"
                    result["time"] = f"{time.time() - start_time:.2f}s"
                    return result
                    
                else:
                    result["response"] = f"GraphQL HTTP {graphql_response.status_code}"
                    result["status"] = BOLD_MAP['ERROR']
                    result["time"] = f"{time.time() - start_time:.2f}s"
                    return result
            
            # If we exhausted retries
            if soft_error_retried:
                result["response"] = "Max soft error retries exceeded"
            else:
                result["response"] = "Max retries exceeded"
            result["status"] = BOLD_MAP['ERROR']
            result["time"] = f"{time.time() - start_time:.2f}s"
            return result
            
    except httpx.ProxyError:
        result["proxy"] = "Proxy Error"
        result["response"] = "Proxy connection failed"
        result["status"] = BOLD_MAP['ERROR']
        result["time"] = f"{time.time() - start_time:.2f}s"
        result["card"] = cc
        return result
    except httpx.ConnectTimeout:
        result["proxy"] = "Proxy Timeout" if proxy_str else "Dead"
        result["response"] = "Connection Timeout"
        result["status"] = BOLD_MAP['ERROR']
        result["time"] = f"{time.time() - start_time:.2f}s"
        result["card"] = cc
        return result
    except Exception as e:
        result["response"] = f"Error: {str(e)}"
        result["status"] = BOLD_MAP['ERROR']
        result["time"] = f"{time.time() - start_time:.2f}s"
        result["card"] = cc
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
                "status": "𝗗𝗘𝗖𝗟𝗜𝗡𝗘𝗗 ❌",
                "site": "Unknown",
                "amount": "$0.00",
                "response": "Invalid Key",
                "proxy": "Unknown",
                "time": "0s",
                "card": cc if cc else "Unknown"
            })
        
        if not cc or not site:
            return jsonify({
                "status": "𝗗𝗘𝗖𝗟𝗜𝗡𝗘𝗗 ❌",
                "site": "Unknown",
                "amount": "$0.00",
                "response": "Missing CC or Site",
                "proxy": "Unknown",
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
            "status": "Error",
            "proxy": "Error",
            "site": "Error",
            "amount": "Error",
            "response": str(e),
            "time": "0s",
            "card": "Unknown"
        })

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "running"})

if __name__ == "__main__":

    app.run(host='0.0.0.0', port=5001, debug=False)
