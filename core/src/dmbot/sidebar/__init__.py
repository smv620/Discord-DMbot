"""The DM sidebar's answer engine (#934; docs/PLAN.md, "DM sidebar"): the DM asks a question,
DMbot answers in as few words as it can. `answer.py` is the one call; `brevity.py` holds the
length and padding rules (in code, not only in the prompt); `context.py` builds what the AI
is told, from this one campaign's data. Voice memos and the "hold on, I need to find…"
trigger are #935; they call `Sidebar.answer`.
"""
