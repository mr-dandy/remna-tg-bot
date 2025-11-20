import logging
from aiogram import Router, F, types, Bot
from typing import Optional, Union
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from bot.services.referral_service import ReferralService


from bot.middlewares.i18n import JsonI18n

router = Router(name="user_referral_router")


async def referral_command_handler(event: Union[types.Message,
                                                types.CallbackQuery],
                                   settings: Settings, i18n_data: dict,
                                   referral_service: ReferralService, bot: Bot,
                                   session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")

    target_message_obj = (
        event.message if isinstance(event, types.CallbackQuery) else event
    )
    if not target_message_obj:
        logging.error(
            "Target message is None in referral_command_handler (possibly from callback without message)."
        )
        if isinstance(event, types.CallbackQuery):
            await event.answer("Error displaying referral info.",
                               show_alert=True)
        return

    if not i18n or not referral_service:
        logging.error(
            "Dependencies (i18n or ReferralService) missing in referral_command_handler"
        )
        await target_message_obj.answer(
            "Service error. Please try again later.")
        if isinstance(event, types.CallbackQuery):
            await event.answer()
        return

    def _(key, **kwargs):
        return i18n.gettext(current_lang, key, **kwargs)

    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username
    except Exception as e_bot_info:
        logging.error(
            f"Failed to get bot info for referral link: {e_bot_info}"
        )
        await target_message_obj.answer(_("error_generating_referral_link"))
        if isinstance(event, types.CallbackQuery):
            await event.answer()
        return

    if not bot_username:
        logging.error("Bot username is None, cannot generate referral link.")
        await target_message_obj.answer(_("error_generating_referral_link"))
        if isinstance(event, types.CallbackQuery):
            await event.answer()
        return

    inviter_user_id = event.from_user.id
    referral_link = referral_service.generate_referral_link(
        bot_username, inviter_user_id)

    bonus_info_parts = []
    per_friend_bonus = getattr(
        settings, "REFERRAL_DAYS_PER_SUCCESSFUL_FRIEND", None)
    use_per_friend_program = isinstance(
        per_friend_bonus, int) and per_friend_bonus > 0

    if use_per_friend_program:
        bonus_info_parts.append(
            _("referral_bonus_base_line", days=per_friend_bonus))
        tier_counts = getattr(settings, "referral_friend_tiers", None)
        if not tier_counts:
            tier_counts = [1, 6, 12]
        for tier_count in tier_counts:
            bonus_info_parts.append(
                _("referral_bonus_per_friend_tier",
                  friends=tier_count,
                  days=tier_count * per_friend_bonus))
    elif settings.subscription_options:

        for months_period_key, _price in sorted(
                settings.subscription_options.items()):

            inv_bonus = settings.referral_bonus_inviter.get(months_period_key)
            ref_bonus = settings.referral_bonus_referee.get(months_period_key)
            if inv_bonus is not None or ref_bonus is not None:
                bonus_info_parts.append(
                    _("referral_bonus_per_period",
                      months=months_period_key,
                      inviter_bonus_days=inv_bonus
                      if inv_bonus is not None else _("no_bonus_placeholder"),
                      referee_bonus_days=ref_bonus
                      if ref_bonus is not None else _("no_bonus_placeholder")))

    bonus_details_str = "\n".join(bonus_info_parts) if bonus_info_parts else _(
        "referral_no_bonuses_configured")

    # Get referral statistics
    referral_stats = await referral_service.get_referral_stats(session, inviter_user_id)

    text = _(
        "referral_program_info_new",
        referral_link=referral_link,
        bonus_details=bonus_details_str,
        invited_count=referral_stats["invited_count"],
        purchased_count=referral_stats["purchased_count"],
    )

    from bot.keyboards.inline.user_keyboards import get_referral_link_keyboard
    # Build share URLs for web-app share sheet (Telegram supports opening external share sheets in web).
    share_bonus_days = per_friend_bonus if use_per_friend_program else settings.referral_bonus_inviter.get(
        1, 0)
    share_text = i18n.gettext(
        current_lang,
        "referral_friend_message",
        referral_link=referral_link,
        bonus_days=share_bonus_days,
    )
    import urllib.parse as _url
    encoded_text = _url.quote(share_text)
    # Universal web share options (will open platform share sheet in mobile browsers)
    web_share = (
        f"https://t.me/share/url?url={_url.quote(referral_link)}&text={encoded_text}"
    )
    reply_markup_val = get_referral_link_keyboard(
        current_lang, i18n, share_url=web_share
    )

    if isinstance(event, types.Message):
        await event.answer(text,
                           reply_markup=reply_markup_val,
                           disable_web_page_preview=True)
    elif isinstance(event, types.CallbackQuery) and event.message:
        try:
            await event.message.edit_text(text,
                                          reply_markup=reply_markup_val,
                                          disable_web_page_preview=True)
        except Exception as e_edit:
            logging.warning(
                f"Failed to edit message for referral info: {e_edit}. Sending new one."
            )
            await event.message.answer(text,
                                       reply_markup=reply_markup_val,
                                       disable_web_page_preview=True)
        await event.answer()


@router.callback_query(F.data.startswith("referral_action:"))
async def referral_action_handler(callback: types.CallbackQuery, settings: Settings,
                                  i18n_data: dict, referral_service: ReferralService,
                                  bot: Bot, session: AsyncSession):
    action = callback.data.split(":")[1]
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n = i18n_data.get("i18n_instance")

    def _(key, **kwargs):
        return i18n.gettext(current_lang, key, **kwargs)

    if action == "share_message":
        try:
            bot_info = await bot.get_me()
            bot_username = bot_info.username
            if not bot_username:
                await callback.answer(
                    "Ошибка получения имени бота", show_alert=True
                )
                return

            inviter_user_id = callback.from_user.id
            referral_link = referral_service.generate_referral_link(
                bot_username, inviter_user_id)

            per_friend_bonus_cb = getattr(
                settings, "REFERRAL_DAYS_PER_SUCCESSFUL_FRIEND", None)
            share_bonus_days_cb = per_friend_bonus_cb if isinstance(
                per_friend_bonus_cb, int) and per_friend_bonus_cb > 0 else settings.referral_bonus_inviter.get(1, 0)

            friend_message = _(
                "referral_friend_message",
                referral_link=referral_link,
                bonus_days=share_bonus_days_cb
            )
            await callback.message.answer(
                friend_message, disable_web_page_preview=True
            )

        except Exception as e:
            logging.error(f"Error in referral share message: {e}")
            await callback.answer("Произошла ошибка", show_alert=True)
    await callback.answer()
