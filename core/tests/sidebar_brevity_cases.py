"""Real table questions for the DM sidebar's brevity rules (#934; owner, 2026-10-09).

Each case: the question a DM might ask at the table, what a fake AI replies (the replies are
in order; a case with two replies is one where the first breaks a rule and the engine asks
again), and what the final answer must satisfy. CI runs them with a fake AI client
(`tests/test_sidebar_answer.py`); `scripts/sidebar_brevity_check.py` runs the same questions
against the real model on the server and prints the answers for Supervisor to read.
"""

from __future__ import annotations

from dataclasses import dataclass


def reply(
    answer: str,
    source: str = "none",
    sure: str = "sure",
    in_game: str = "yes",
    on_topic: str = "yes",
) -> str:
    return (
        f"ANSWER: {answer}\nSOURCE: {source}\nSURE: {sure}\n"
        f"IN_GAME: {in_game}\nON_TOPIC: {on_topic}"
    )


@dataclass(frozen=True)
class Case:
    question: str
    replies: tuple[str, ...]  # what the fake AI says, one per call
    must_any: tuple[str, ...]  # at least one of these (any case) is in the answer
    yes_no: bool = False  # the answer starts with Yes or No
    calls: int = 1  # how many AI calls the engine should make
    in_game: bool = True


CASES: tuple[Case, ...] = (
    Case(
        "do you need line of sight for fireball",
        (reply("No. Its origin is a point you choose within range.", "SRD 5.2.1 p. 131"),),
        ("no", "point you choose"),
        yes_no=True,
    ),
    Case(
        "hold on, I need to find if you need line of sight for fireball",
        (reply("No. It starts at a point you choose within range.", "SRD 5.2.1 p. 131"),),
        ("point you choose",),
        yes_no=True,
    ),
    Case(
        "how much damage does fireball do",
        (reply("8d6 fire damage, half on a successful save.", "SRD 5.2.1 p. 131"),),
        ("8d6",),
    ),
    Case(  # greeting and offer are stripped in code: no second call needed
        "does fireball set things on fire",
        (
            reply(
                "Great question! Yes, flammable objects in the area that aren't worn or "
                "carried start burning. Let me know if you want the full spell text.",
                "SRD 5.2.1 p. 131",
            ),
        ),
        ("burning",),
        yes_no=True,
    ),
    Case(  # no Yes/No first: asked once more
        "can fireball hit someone behind a wall",
        (
            reply("Fireball spreads around corners, so it can.", "SRD 5.2.1 p. 131"),
            reply("Yes. It spreads around corners.", "SRD 5.2.1 p. 131"),
        ),
        ("around corners",),
        yes_no=True,
        calls=2,
    ),
    Case(
        "can a goblin be grappled while it's prone",
        (
            reply(
                "The Grappled condition and the Prone condition can both apply to a creature "
                "at the same time, because neither one ends the other. A grappled creature's "
                "speed is 0, and a prone creature can only crawl or stand up. So yes.",
                "SRD 5.2.1 p. 182",
            ),
            reply(
                "Yes. A creature can be Grappled and Prone at once; its speed is 0 while grappled.",
                "SRD 5.2.1 p. 182",
            ),
        ),
        ("yes",),
        yes_no=True,
        calls=2,
    ),
    Case(
        "what does the grappled condition do",
        (
            reply(
                "A grappled creature's speed is 0 and it has disadvantage on attacks against "
                "anyone but the grappler. The condition ends if the grappler is incapacitated "
                "or the creature is moved out of reach. It also affects carrying capacity in "
                "some situations and interacts with many other rules in many ways.",
                "SRD 5.2.1 p. 182",
            ),
            reply(
                "A grappled creature's speed is 0 and it has disadvantage on attacks against "
                "anyone but the grappler. The condition ends if the grappler is incapacitated "
                "or the creature is moved out of reach. It also affects carrying capacity too.",
                "SRD 5.2.1 p. 182",
            ),
        ),
        ("speed is 0",),
        calls=2,
    ),
    Case(
        "is there a rule for a critical hit on a spell attack",
        (reply("The free rules don't say. Your call.", "none", "not sure"),),
        ("your call",),
        yes_no=True,
    ),
    Case(
        "what's my house rule for criticals",
        (reply("House rule 3: criticals deal max damage plus the roll.", "House rule 3"),),
        ("house rule 3",),
    ),
    Case(
        "what do I know about Belleros",
        (reply("Belleros is an NPC and the hooded stranger's friend.", "campaign names"),),
        ("belleros",),
    ),
    Case(
        "who does Belleros work for",
        (reply("I don't have that. Your call.", "none", "not sure"),),
        ("your call",),
    ),
    Case(
        "how do I stop DMbot recording me",
        (
            reply(
                "Press Menu in DMbot's private message, then Stop recording me.",
                "DMbot help",
                in_game="no",
            ),
        ),
        ("stop recording me",),
        in_game=False,
    ),
    Case(
        "can players read the transcript",
        (reply("Yes. Anyone in the server can read and download it.", "DMbot help", in_game="no"),),
        ("anyone in the server",),
        yes_no=True,
        in_game=False,
    ),
    Case(
        "is the 2014 goblin different",
        (reply("Yes. It is now the Goblin Warrior in the newer rules.", "SRD 5.2.1 p. 290"),),
        ("goblin warrior",),
        yes_no=True,
    ),
    Case(
        "what's the best pizza topping",
        (reply("Pepperoni.", on_topic="no"),),
        ("only help with the game",),
        in_game=False,
    ),
    Case(
        "write me a poem about my dog",
        (reply("Woof.", on_topic="no", in_game="no"),),
        ("only help with the game",),
        in_game=False,
    ),
    Case(
        "should I let the rogue sneak attack with a spell",
        (
            reply(
                "Your call. Sneak Attack needs a weapon attack in the free rules.",
                "SRD 5.2.1",
                "not sure",
            ),
        ),
        ("your call",),
    ),
)
