import base64
import json


def make_jwt(claims: dict) -> str:
    def b64u(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64u({"alg": "none", "typ": "JWT"})
    payload = b64u(claims)
    return f"{header}.{payload}.sig"
