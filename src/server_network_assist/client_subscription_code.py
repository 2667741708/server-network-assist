"""Versioned subscription codes. Encoding is not encryption or authorization."""
import base64
import binascii
import json
import re

PREFIX = 'PURE1-'
MAX_INPUT_LENGTH = 8192


def decode_subscription_input(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= MAX_INPUT_LENGTH:
        raise ValueError('订阅地址或订阅码为空或过长')
    value = value.strip()
    if not value.startswith(PREFIX):
        return value
    encoded = value[len(PREFIX):]
    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', encoded):
            raise ValueError()
        payload = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        if base64.urlsafe_b64encode(payload).decode().rstrip('=') != encoded:
            raise ValueError()
        record = json.loads(payload.decode('utf-8'))
        if not isinstance(record, dict) or set(record) != {'v', 'url'} or type(record['v']) is not int or record['v'] != 1:
            raise ValueError()
        if not isinstance(record['url'], str) or not record['url'].startswith(('https://', 'http://')):
            raise ValueError()
        return record['url']
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise ValueError('订阅码无效或版本不受支持，请向管理员重新获取；未更换当前订阅') from None


def encode_subscription_code(value):
    from .client_online import parse_enrollment_url
    # Validate through exactly the same endpoint and token policy as enrollment.
    url = decode_subscription_input(value)
    parse_enrollment_url(url)
    payload = json.dumps({'v': 1, 'url': url}, ensure_ascii=True, separators=(',', ':')).encode()
    code = PREFIX + base64.urlsafe_b64encode(payload).decode().rstrip('=')
    if len(code) > MAX_INPUT_LENGTH:
        raise ValueError('订阅地址过长，不能转换为订阅码')
    return code
