# flake8: noqa: E501
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from sqlalchemy.orm import sessionmaker

from config.settings import Settings


async def build_and_start_web_app(
    dp: Dispatcher,
    bot: Bot,
    settings: Settings,
    async_session_factory: sessionmaker,
):
    app = web.Application()
    app["bot"] = bot
    app["dp"] = dp
    app["settings"] = settings
    app["async_session_factory"] = async_session_factory
    # Inject shared instances used by webhook handlers
    app["i18n"] = dp.get("i18n_instance")
    for key in (
        "yookassa_service",
        "subscription_service",
        "referral_service",
        "panel_service",
        "stars_service",
        "cryptopay_service",
        "tribute_service",
        "panel_webhook_service",
    ):
        # Access dispatcher workflow_data directly to avoid sequence protocol issues
        if hasattr(dp, "workflow_data") and key in dp.workflow_data:  # type: ignore
            app[key] = dp.workflow_data[key]  # type: ignore

    setup_application(app, dp, bot=bot)

    telegram_uses_webhook_mode = bool(settings.WEBHOOK_BASE_URL)

    if telegram_uses_webhook_mode:
        telegram_webhook_path = f"/{settings.BOT_TOKEN}"
        app.router.add_post(telegram_webhook_path,
                            SimpleRequestHandler(dispatcher=dp, bot=bot))
        logging.info(
            f"Telegram webhook route configured at: [POST] {telegram_webhook_path} "
            "(relative to base URL)"
        )

    # --- Health and diagnostics endpoints ---
    async def health_handler(_: web.Request) -> web.Response:
        return web.Response(status=200, text="ok")

    async def miniapp_ping_handler(_: web.Request) -> web.Response:
        return web.Response(status=200, text="miniapp-ok")

    async def miniapp_sub_handler(request: web.Request) -> web.StreamResponse:
        """Resolve current user's subscription link on the panel and redirect there.
        Accepts Telegram WebApp initData via header 'X-Telegram-Init-Data' or
        query 'tgWebAppData'. Falls back to query 'user_id' for manual tests.
        """
        # settings_local: Settings = request.app["settings"]  # not used
        panel_service = request.app.get("panel_service")
        async_session_factory_local: sessionmaker = request.app["async_session_factory"]

        # 1) Extract Telegram user_id
        tg_user_id: int | None = None
        init_data = request.headers.get(
            "X-Telegram-Init-Data") or request.query.get("tgWebAppData")
        if init_data:
            try:
                from urllib.parse import parse_qsl
                import json as _json
                parsed = dict(parse_qsl(init_data, keep_blank_values=True))
                if "user" in parsed and parsed["user"]:
                    user_obj = _json.loads(parsed["user"])
                    if isinstance(user_obj, dict) and "id" in user_obj:
                        # best-effort; signature validation can be added later
                        tg_user_id = int(user_obj["id"])
            except Exception:
                pass

        if tg_user_id is None:
            user_id_query = request.query.get("user_id")
            if user_id_query and user_id_query.isdigit():
                tg_user_id = int(user_id_query)

        if tg_user_id is None:
            return web.Response(status=400, text="Missing Telegram initData or user_id")

        # 2) Lookup active subscription and build link
        try:
            from db.dal import subscription_dal
            async with async_session_factory_local() as db_session:
                local_sub = await subscription_dal.get_active_subscription_by_user_id(
                    db_session, tg_user_id
                )
                if not local_sub:
                    return web.Response(
                        status=200,
                        text="Subscription is not active. Open the bot to purchase.",
                    )

                sub_uuid = getattr(local_sub, "panel_subscription_uuid", None)
                if not sub_uuid:
                    return web.Response(status=200, text="Subscription link is not available yet. Try again later.")

                if not panel_service:
                    return web.Response(
                        status=503, text="Service unavailable. Please try again later."
                    )

                link = await panel_service.get_subscription_link(sub_uuid)
                if not link:
                    return web.Response(
                        status=502, text="Failed to resolve subscription link."
                    )

                # 3) Redirect to panel subscription page
                raise web.HTTPFound(location=link)
        except web.HTTPFound:
            raise
        except Exception:
            logging.exception("miniapp_sub_handler failed")
            return web.Response(status=500, text="Internal error. Please try again later.")

    app.router.add_get("/healthz", health_handler)
    app.router.add_get("/miniapp/ping", miniapp_ping_handler)
    # Support both with and without trailing slash for Telegram WebView peculiarities
    app.router.add_get("/miniapp/sub", miniapp_sub_handler)
    app.router.add_get("/miniapp/sub/", miniapp_sub_handler)

    from bot.handlers.user.payment import yookassa_webhook_route
    from bot.services.tribute_service import tribute_webhook_route
    from bot.services.crypto_pay_service import cryptopay_webhook_route
    from bot.services.panel_webhook_service import panel_webhook_route

    tribute_path = settings.tribute_webhook_path
    if tribute_path.startswith("/"):
        app.router.add_post(tribute_path, tribute_webhook_route)
        logging.info(
            f"Tribute webhook route configured at: [POST] {tribute_path}")

    cp_path = settings.cryptopay_webhook_path
    if cp_path.startswith("/"):
        app.router.add_post(cp_path, cryptopay_webhook_route)
        logging.info(
            f"CryptoPay webhook route configured at: [POST] {cp_path}")

    # YooKassa webhook (register only when base URL present and path configured)
    yk_path = settings.yookassa_webhook_path
    if settings.WEBHOOK_BASE_URL and yk_path and yk_path.startswith("/"):
        app.router.add_post(yk_path, yookassa_webhook_route)
        logging.info(f"YooKassa webhook route configured at: [POST] {yk_path}")

    panel_path = settings.panel_webhook_path
    if panel_path.startswith("/"):
        app.router.add_post(panel_path, panel_webhook_route)
        logging.info(f"Panel webhook route configured at: [POST] {panel_path}")

    web_app_runner = web.AppRunner(app)
    await web_app_runner.setup()
    site = web.TCPSite(
        web_app_runner,
        host=settings.WEB_SERVER_HOST,
        port=settings.WEB_SERVER_PORT,
    )

    await site.start()
    logging.info(
        f"AIOHTTP server started on http://{settings.WEB_SERVER_HOST}:{settings.WEB_SERVER_PORT}"
    )

    # Run until cancelled
    await asyncio.Event().wait()
