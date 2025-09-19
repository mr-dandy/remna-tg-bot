# flake8: noqa: E501
"""Payment handlers for subscriptions."""
import logging
from urllib.parse import urlparse, urlunparse
from urllib.parse import parse_qsl, urlencode
from aiogram import Router, F, types
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from bot.keyboards.inline.user_keyboards import get_payment_method_keyboard, get_payment_url_keyboard
from bot.services.yookassa_service import YooKassaService
from bot.services.crypto_pay_service import CryptoPayService
from bot.services.stars_service import StarsService
from bot.middlewares.i18n import JsonI18n
from db.dal import payment_dal, user_billing_dal

router = Router(name="user_subscription_payments_router")


@router.callback_query(F.data.startswith("subscribe_period:"))
async def select_subscription_period_callback_handler(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(
        current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        try:
            await callback.answer(get_text("error_occurred_try_again"), show_alert=True)
        except Exception:
            pass
        return

    try:
        months = int(callback.data.split(":")[-1])
    except (ValueError, IndexError):
        logging.error(
            f"Invalid subscription period in callback_data: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    price_rub = settings.subscription_options.get(months)
    if price_rub is None:
        logging.error(
            f"Price not found for {months} months subscription period in settings.subscription_options."
        )
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    currency_symbol_val = settings.DEFAULT_CURRENCY_SYMBOL
    text_content = get_text("choose_payment_method")
    # Для Tribute используем донат-ссылки из настроек (как у bedolaga)
    tribute_url = settings.tribute_payment_links.get(months)
    if not tribute_url and getattr(settings, 'TRIBUTE_DONATE_LINK', None):
        tribute_url = settings.TRIBUTE_DONATE_LINK

    # аккуратно подставляем сумму (в минорных единицах) и идентификатор пользователя
    if tribute_url:
        try:
            user_id_val = callback.from_user.id
        except Exception:
            user_id_val = None
        try:
            amount_minor = int(float(price_rub) * 100)
        except Exception:
            amount_minor = None

        try:
            parsed = urlparse(tribute_url)
            query = dict(parse_qsl(parsed.query))
            if user_id_val is not None and 'telegram_user_id' not in query:
                query['telegram_user_id'] = str(user_id_val)
            if amount_minor is not None and 'amount' not in query:
                query['amount'] = str(amount_minor)
            if 'period' not in query:
                query['period'] = str(months)
            new_query = urlencode(query, doseq=True)
            tribute_url = urlunparse(parsed._replace(query=new_query))
        except Exception:
            # Если не удалось распарсить/склеить URL — оставляем исходный
            pass
    stars_price = settings.stars_subscription_options.get(months)
    reply_markup = get_payment_method_keyboard(
        months,
        price_rub,
        tribute_url,
        stars_price,
        currency_symbol_val,
        current_lang,
        i18n,
        settings,
    )

    try:
        await callback.message.edit_text(text_content, reply_markup=reply_markup)
    except Exception as e_edit:
        logging.warning(
            f"Edit message for payment method selection failed: {e_edit}. Sending new one."
        )
        await callback.message.answer(text_content, reply_markup=reply_markup)
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("pay_yk:"))
async def pay_yk_callback_handler(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, yookassa_service: YooKassaService, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(
        current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        try:
            await callback.answer(get_text("error_occurred_try_again"), show_alert=True)
        except Exception:
            pass
        return


@router.callback_query(F.data.startswith("pay_tribute:"))
async def pay_tribute_callback_handler(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    session: AsyncSession,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = (lambda key, **kwargs: i18n.gettext(current_lang,
                key, **kwargs) if i18n else key)

    if not i18n or not callback.message:
        try:
            await callback.answer(get_text("error_occurred_try_again"), show_alert=True)
        except Exception:
            pass
        return

    try:
        _, data_payload = callback.data.split(":", 1)
        months_str, price_str = data_payload.split(":")
        months = int(months_str)
        price_rub = float(price_str)
    except (ValueError, IndexError):
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    # Сформировать ссылку Tribute (донат) с префиллом суммы, user_id и периода
    tribute_url = settings.tribute_payment_links.get(months)
    if not tribute_url and getattr(settings, 'TRIBUTE_DONATE_LINK', None):
        tribute_url = settings.TRIBUTE_DONATE_LINK
    if tribute_url:
        try:
            user_id_val = callback.from_user.id
        except Exception:
            user_id_val = None
        try:
            amount_minor = int(float(price_rub) * 100)
        except Exception:
            amount_minor = None

        try:
            parsed = urlparse(tribute_url)
            query = dict(parse_qsl(parsed.query))
            if user_id_val is not None and 'telegram_user_id' not in query:
                query['telegram_user_id'] = str(user_id_val)
            if amount_minor is not None and 'amount' not in query:
                query['amount'] = str(amount_minor)
            if 'period' not in query:
                query['period'] = str(months)
            new_query = urlencode(query, doseq=True)
            tribute_url = urlunparse(parsed._replace(query=new_query))
        except Exception:
            pass

    # Текст-инструкция
    instruction_text = (
        "💳 <b>Оплата банковской картой</b>\n\n"
        "• Введите сумму кратную тарифу который, вы хотите подключить\n"
        "• Выберите \"Перейти к оплате\"\n\n"
        "• 🚨 НЕ ОТПРАВЛЯЙТЕ ПЛАТЕЖ АНОНИМНО!"
    )

    # Показать кнопку перехода
    if tribute_url:
        try:
            await callback.message.edit_text(
                instruction_text,
                reply_markup=get_payment_url_keyboard(
                    tribute_url, current_lang, i18n),
                parse_mode="HTML",
                disable_web_page_preview=False,
            )
        except Exception:
            try:
                await callback.message.answer(
                    instruction_text,
                    reply_markup=get_payment_url_keyboard(
                        tribute_url, current_lang, i18n),
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                )
            except Exception:
                pass
        try:
            await callback.answer()
        except Exception:
            pass
        return

    try:
        await callback.answer(get_text("payment_service_unavailable_alert"), show_alert=True)
    except Exception:
        pass


@router.callback_query(F.data.startswith("pay_crypto:"))
async def pay_crypto_callback_handler(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    session: AsyncSession,
    cryptopay_service: CryptoPayService,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = (lambda key, **kwargs: i18n.gettext(current_lang,
                key, **kwargs) if i18n else key)

    if not i18n or not callback.message:
        try:
            await callback.answer(get_text("error_occurred_try_again"), show_alert=True)
        except Exception:
            pass
        return

    if not cryptopay_service or not getattr(cryptopay_service, "configured", False):
        try:
            await callback.answer(get_text("payment_service_unavailable_alert"), show_alert=True)
        except Exception:
            pass
        return

    try:
        _, data_payload = callback.data.split(":", 1)
        months_str, price_str = data_payload.split(":")
        months = int(months_str)
        price_amount = float(price_str)
    except (ValueError, IndexError):
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    user_id = callback.from_user.id
    payment_description = get_text(
        "payment_description_subscription", months=months)

    invoice_url = await cryptopay_service.create_invoice(
        session=session,
        user_id=user_id,
        months=months,
        amount=price_amount,
        description=payment_description,
    )

    if invoice_url:
        try:
            await callback.message.edit_text(
                get_text(key="payment_link_message", months=months),
                reply_markup=get_payment_url_keyboard(
                    invoice_url, current_lang, i18n),
                disable_web_page_preview=False,
            )
        except Exception:
            try:
                await callback.message.answer(
                    get_text(key="payment_link_message", months=months),
                    reply_markup=get_payment_url_keyboard(
                        invoice_url, current_lang, i18n),
                    disable_web_page_preview=False,
                )
            except Exception:
                pass
        try:
            await callback.answer()
        except Exception:
            pass
        return

    try:
        await callback.answer(get_text("error_payment_gateway"), show_alert=True)
    except Exception:
        pass


@router.callback_query(F.data.startswith("pay_stars:"))
async def pay_stars_callback_handler(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    session: AsyncSession,
    stars_service: StarsService,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = (lambda key, **kwargs: i18n.gettext(current_lang,
                key, **kwargs) if i18n else key)

    if not i18n or not callback.message:
        try:
            await callback.answer(get_text("error_occurred_try_again"), show_alert=True)
        except Exception:
            pass
        return

    if not settings.STARS_ENABLED:
        try:
            await callback.answer(get_text("payment_service_unavailable_alert"), show_alert=True)
        except Exception:
            pass
        return

    try:
        _, data_payload = callback.data.split(":", 1)
        months_str, stars_price_str = data_payload.split(":")
        months = int(months_str)
        stars_price = int(stars_price_str)
    except (ValueError, IndexError):
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    user_id = callback.from_user.id
    payment_description = get_text(
        "payment_description_subscription", months=months)

    payment_db_id = await stars_service.create_invoice(
        session=session,
        user_id=user_id,
        months=months,
        stars_price=stars_price,
        description=payment_description,
    )

    if payment_db_id:
        try:
            await callback.answer()
        except Exception:
            pass
        return

    try:
        await callback.answer(get_text("error_payment_gateway"), show_alert=True)
    except Exception:
        pass


@router.pre_checkout_query()
async def handle_pre_checkout_query(query: types.PreCheckoutQuery):
    try:
        await query.answer(ok=True)
    except Exception:
        # Nothing else to do here; Telegram will show an error if not answered
        pass


@router.message(F.successful_payment)
async def handle_successful_stars_payment(
    message: types.Message,
    settings: Settings,
    i18n_data: dict,
    session: AsyncSession,
    stars_service: StarsService,
):
    payload = (message.successful_payment.invoice_payload
               if message and message.successful_payment else "")
    try:
        payment_db_id_str, months_str = (payload or "").split(":", 1)
        payment_db_id = int(payment_db_id_str)
        months = int(months_str)
    except Exception:
        return

    stars_amount = int(
        message.successful_payment.total_amount) if message.successful_payment else 0
    await stars_service.process_successful_payment(
        session=session,
        message=message,
        payment_db_id=payment_db_id,
        months=months,
        stars_amount=stars_amount,
        i18n_data=i18n_data,
    )
