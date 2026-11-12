from modelryx.core.auth import Principal, create_access_token

AUTH = {
    "Authorization": "Bearer "
    + create_access_token(
        Principal(
            subject="local-development-user",
            display_name="Local Engineer",
            role="administrator",
        )
    )
}
