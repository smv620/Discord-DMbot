"""0612_web_feature_settings

The website turns a campaign's "Rules cards" and "Ask me before adding" on or off (#5155, parity
gap of #3663): `rules_cards` and `ask_before_adding` join the settings the website can queue,
'on' or 'off', like `map_checks` (0538).
"""

from __future__ import annotations

# The settings queue (0197, last changed by 0538) lists its settings twice, in the table's check
# and in the door's own allow-list and value checks. Both are read back from the database as they
# are and each anchor must be found exactly the number of times given, so a drift stops the
# migration. The bot applies each choice through the call Discord's button makes
# (`DMBot.set_rules_cards`, `campaigns.set_ask_before_adding`). No row changes.
_SWAPS = (
    (
        "'dndbeyond_invite', 'rulesets', 'share_improve', 'map_checks') THEN",
        "'dndbeyond_invite', 'rulesets', 'share_improve', 'map_checks', 'rules_cards',\n"
        "           'ask_before_adding') THEN",
        1,
    ),
    (
        "OR (st = 'map_checks' AND val NOT IN ('on', 'off')) THEN",
        "OR (st = 'map_checks' AND val NOT IN ('on', 'off'))\n"
        "           OR (st IN ('rules_cards', 'ask_before_adding')\n"
        "               AND val NOT IN ('on', 'off')) THEN",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _SWAPS
)

SQL = (
    r"""
    DO $do$
    DECLARE
        d text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'setting_actions_setting_check'
              AND conrelid = 'setting_actions'::regclass;
        IF (length(d) - length(replace(d, '])', ''))) <> 2 THEN
            RAISE EXCEPTION 'the setting list did not have the expected shape';
        END IF;
        ALTER TABLE setting_actions DROP CONSTRAINT setting_actions_setting_check;
        EXECUTE 'ALTER TABLE setting_actions ADD CONSTRAINT setting_actions_setting_check '
            || replace(d, '])', ', ''rules_cards''::text, ''ask_before_adding''::text])');
    END
    $do$;

    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;

    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_setting_add';"""
    + _STEPS
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
