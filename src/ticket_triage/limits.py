"""HTTP 入站上限。网关与应用必须同口径，见 docs/GATEWAY.md。"""

# 与 deploy/nginx 的 client_max_body_size、应用 middleware 一致。
MAX_BODY_BYTES = 64_000
