"""The account helpers moved to dmbot.accounts (#2547): the website's callers keep importing them
from here, so every public name is re-exported with the `as` form (mypy strict needs it)."""

from dmbot.accounts import LINK_WORDS as LINK_WORDS
from dmbot.accounts import NAME_MAX as NAME_MAX
from dmbot.accounts import UNLINK_WORDS as UNLINK_WORDS
from dmbot.accounts import LinkResult as LinkResult
from dmbot.accounts import Login as Login
from dmbot.accounts import account_email as account_email
from dmbot.accounts import account_of as account_of
from dmbot.accounts import active_subscription as active_subscription
from dmbot.accounts import customer_id as customer_id
from dmbot.accounts import delete_person as delete_person
from dmbot.accounts import dmbot_id as dmbot_id
from dmbot.accounts import link_discord_id as link_discord_id
from dmbot.accounts import link_install as link_install
from dmbot.accounts import logins_of as logins_of
from dmbot.accounts import payment_email as payment_email
from dmbot.accounts import record_install as record_install
from dmbot.accounts import remember_display_name as remember_display_name
from dmbot.accounts import unlink_discord_id as unlink_discord_id
from dmbot.accounts import user_of_dmbot_id as user_of_dmbot_id
