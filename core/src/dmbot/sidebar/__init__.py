"""The DM sidebar (docs/PLAN.md, "DM sidebar"): quick, very short answers for the DM.

The answer engine is #934: `answer.py` is the one call; `brevity.py` holds the length and
padding rules (in code, not only in the prompt); `context.py` builds what the AI is told,
from this one campaign's data. The ways in are #935: `service.py` (voice messages and typed
questions in the DM's chat, and the "hold on, I need to find…" trigger at the table), `ask.py`
(the trigger phrases) and `memo.py` (reading a voice message).
"""
