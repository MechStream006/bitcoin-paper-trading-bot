# Alertmanager secrets directory

This directory is bind-mounted read-only into the alertmanager container at
`/etc/alertmanager/secrets/`. It is intentionally almost empty in git.

Before starting the stack, create:

```
telegram_bot_token
```

containing **only** the raw bot token (the same one already in the bot's own
`.env` as `TELEGRAM_BOT_TOKEN` -- the same bot can post from multiple
processes). No quotes, no `KEY=value` wrapper, no trailing config -- just the
token string, optionally with a trailing newline.

```
echo -n "123456:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" > telegram_bot_token
chmod 600 telegram_bot_token
```

This file is gitignored (`deploy/compose/alertmanager/secrets/*`, this
README excepted). If it is missing, alertmanager fails to start -- that is
the intended fail-closed behavior, not a bug.
