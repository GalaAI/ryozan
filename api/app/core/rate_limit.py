from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def get_rate_limit_key(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        client_ip = forwarded_for.split(",", 1)[0].strip()
        if client_ip:
            return client_ip

    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip

    return get_remote_address(request)


# A portal page load makes 20 to 30 API reads, so the defaults leave room for a person
# clicking through several screens a minute. Sign-in, sign-up and other sensitive
# routes carry their own stricter @limiter.limit decorators.
limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=["300/minute"],
    application_limits=["600/minute"],
)
