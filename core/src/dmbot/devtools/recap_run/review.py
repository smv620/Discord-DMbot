"""The files a recap test run leaves for the owner to review (#2011): `recap.txt` (the recap with
its cues, the same without them, the housekeeping and the music prompts), `report.txt` (what the
run printed) and `review.html`, one page with no network that plays the voice and the music
together or one at a time. `recap_run --review DIR` rebuilds the page from a folder, with no paid
call.

Pure text and files: nothing here calls a service. Everything the page shows is escaped, and the
page loads nothing from outside (no address, no outside script), so it opens by double-click.

A recap that names a secret is never written (the caller checks, as it does before printing).
"""

from __future__ import annotations

import contextlib
import html
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dmbot.recaps.audio.files import FILE_NAMES, MIX, MUSIC, VOICE
from dmbot.recaps.text import CUE, PERFORMANCE, strip_cues

RECAP_FILE, REPORT_FILE, PAGE_FILE = "recap.txt", "report.txt", "review.html"
NOT_SAVED = "text not saved in this run"
_SECTION = re.compile(r"^== (.+) ==$")
_TAGS_LINE = re.compile(r"^(?:DMbot recap test\. )?Voice mood tags: (on|off)", re.MULTILINE)
_CUES_HEAD = re.compile(r"^Cues \(\d+\):$")
_HEADING = re.compile(r"^\*\*Session Summary: (.+)\*\*$")
S_TITLE, S_CUES, S_PLAIN = "Title", "Summary, with the cues", "Summary, cues removed"
S_HOUSE, S_MUSIC = "Mechanical Housekeeping", "Music prompts"
S_CONFIRM = "Facts to be confirmed"
_NO_CUES = "As the table reads it (no cues):"


@dataclass(slots=True)
class Review:
    """What the page shows. An empty `with_cues` means the text was not saved."""

    title: str = ""
    with_cues: str = ""
    plain: str = ""
    housekeeping: list[str] = field(default_factory=list)
    to_confirm: list[str] = field(default_factory=list)  # the DM's copy only (#2027)
    music: list[str] = field(default_factory=list)
    voice_tags: bool | None = None  # None: the folder does not say
    names_given: bool = False  # a names file was given, so names are worth listening for
    cue_map: list[str] = field(default_factory=list)
    report: str = ""


def private_dir(folder: Path) -> None:
    """The folder only this user can open (the owner's recap is private)."""
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)


def write_private(path: Path, text: str) -> None:
    """The file is made readable by this user only from the start, and never through a link."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    path.chmod(0o600)  # (a file that was already there keeps its old mode until now)


def clear_old(folder: Path) -> None:
    """The files a run writes, removed before a new run writes them, so a page never shows the
    new recap above an earlier run's sound (or the other way round). Only these names go."""
    names = (RECAP_FILE, REPORT_FILE, PAGE_FILE, *FILE_NAMES.values())
    for name in names:
        with contextlib.suppress(OSError):
            (folder / name).unlink()


def recap_text(
    title: str,
    summary: str,
    housekeeping: list[str],
    music: list[str],
    *,
    voice_tags: bool,
    names_given: bool = False,
    to_confirm: Sequence[str] = (),
) -> str:
    """`recap.txt`: the recap as the AI wrote it, the same as players read it, and the rest."""
    tags = "on" if voice_tags else "off"
    parts = [
        f"DMbot recap test. Voice mood tags: {tags} (DMBOT_RECAP_VOICE_TAGS)\n"
        f"Names file: {'given' if names_given else 'none'}",
        f"== {S_TITLE} ==\n{title}",
        f"== {S_CUES} ==\n{summary}",
        f"== {S_PLAIN} ==\n{strip_cues(summary)}",
        f"== {S_HOUSE} ==\n" + "\n".join(f"- {b}" for b in housekeeping),
        f"== {S_MUSIC} ==\n" + "\n".join(f"{n}. {m}" for n, m in enumerate(music, start=1)),
    ]
    if to_confirm:  # the DM's copy ends with the names to confirm; never narrated or posted
        parts.append(f"== {S_CONFIRM} ==\n" + "\n".join(f"- {n}" for n in to_confirm))
    return "\n\n".join(parts) + "\n"


def _sections(text: str) -> tuple[str, dict[str, str]]:
    """(the lines before the first heading, each `== Name ==` section's text)."""
    head: list[str] = []
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        match = _SECTION.match(line)
        if match:
            current = found.setdefault(match.group(1), [])
        elif current is None:
            head.append(line)
        else:
            current.append(line)
    return "\n".join(head), {k: "\n".join(v).strip() for k, v in found.items()}


def _bullets(text: str, marker: re.Pattern[str]) -> list[str]:
    return [marker.sub("", line).strip() for line in text.splitlines() if line.strip()]


def _from_report(report: str, review: Review) -> None:
    """The recap and the cue map out of a printed report (a folder made before `recap.txt`)."""
    lines = report.splitlines()
    for line in lines:
        match = _TAGS_LINE.match(line)
        if match and review.voice_tags is None:
            review.voice_tags = match.group(1) == "on"
    for i, line in enumerate(lines):
        if _CUES_HEAD.match(line):
            review.cue_map = [ln.strip() for ln in lines[i + 1 :] if ln.startswith("  ")]
            break
    if review.with_cues or _NO_CUES not in lines:
        return
    at = lines.index(_NO_CUES)
    # The printed recap is: a blank, the heading, a blank, the summary, a blank, then the marker.
    top = max((i for i in range(at) if _HEADING.match(lines[i])), default=None)
    if top is None:
        return
    title = _HEADING.match(lines[top])
    review.title = re.sub(r" \(Day \d+\)$", "", title.group(1)) if title else ""
    review.with_cues = "\n".join(lines[top + 2 : at]).strip()
    rest = lines[at + 1 :]
    end = next((i for i, ln in enumerate(rest) if ln == "Housekeeping:"), len(rest))
    review.plain = "\n".join(rest[:end]).strip()
    bullets: list[str] = []
    prompts: list[str] = []
    confirm: list[str] = []
    into: list[str] | None = None
    for ln in rest[end:]:
        if ln == "Housekeeping:":
            into = bullets
        elif ln == "Music prompts:":
            into = prompts
        elif ln == "Facts to be confirmed:":
            into = confirm
        elif ln in ("Checks:", "") or ln.startswith(("Grader:", "Calls:")):
            if ln != "":
                into = None
        elif into is not None:
            into.append(ln)
    review.housekeeping = [re.sub(r"^- ", "", b) for b in bullets]
    review.music = [re.sub(r"^\d+\. ", "", m) for m in prompts]
    review.to_confirm = [re.sub(r"^- ", "", n) for n in confirm]


def read_folder(folder: Path) -> Review:
    """The review of a folder: `recap.txt` first, else the recap in a saved `report.txt`."""
    review = Review()
    recap = folder / RECAP_FILE
    if recap.is_file():
        head, parts = _sections(recap.read_text(encoding="utf-8"))
        match = _TAGS_LINE.search(head)
        review.voice_tags = None if match is None else match.group(1) == "on"
        review.names_given = "Names file: given" in head
        review.title = parts.get(S_TITLE, "")
        review.with_cues = parts.get(S_CUES, "")
        review.plain = parts.get(S_PLAIN, "")
        review.housekeeping = _bullets(parts.get(S_HOUSE, ""), re.compile(r"^- "))
        review.music = _bullets(parts.get(S_MUSIC, ""), re.compile(r"^\d+\. "))
        review.to_confirm = _bullets(parts.get(S_CONFIRM, ""), re.compile(r"^- "))
    report = folder / REPORT_FILE
    if report.is_file():
        review.report = report.read_text(encoding="utf-8")
        _from_report(review.report, review)
    return review


# --- the page ------------------------------------------------------------------------------

_E = html.escape


def _cue_hue(name: str) -> int:
    """The same mood always gets the same colour (a stable hash, not Python's per-run one)."""
    return sum(ord(c) * (i + 3) for i, c in enumerate(name.lower())) % 360


def tagged_html(text: str) -> str:
    """The summary with each cue as a coloured tag. A mood cue 《Tense》 is a coloured chip, a
    performance cue ((pause)) a grey one; the words around them are escaped."""
    out: list[str] = []
    at = 0
    for match in sorted(
        [*CUE.finditer(text), *PERFORMANCE.finditer(text)], key=lambda m: m.start()
    ):
        if match.start() < at:  # a cue inside another one: the outer one already covers it
            continue
        out.append(_E(text[at : match.start()]))
        cue = match.group()
        if cue.startswith("《"):
            name = cue[1:-1]
            out.append(
                f'<span class="cue mood" style="--hue:{_cue_hue(name)}" '
                f'title="A mood starts here">{_E(cue)}</span>'
            )
        else:
            out.append(
                f'<span class="cue perf" title="A pause or a change of pace">{_E(cue)}</span>'
            )
        at = match.end()
    out.append(_E(text[at:]))
    return "".join(out).replace("\n\n", "</p><p>").replace("\n", "<br>")


def _paragraphs(text: str) -> str:
    return "<p>" + _E(text).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"


_PAGE_CSS = """
:root{--bg:#fbfaf7;--fg:#1f2328;--muted:#5b6470;--card:#fff;--line:#d9d4c7;--accent:#2f5d9e;
--ok:#1f7a4d}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#e8e6e1;--muted:#a3a9b3;--card:#1f2228;
--line:#33373f;--accent:#8db4ee;--ok:#6fcf9b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.55 Georgia,'Times New Roman',serif}
main{max-width:46rem;margin:0 auto;padding:1.25rem 16px 4rem}
h1{font-size:1.6rem;margin:.2rem 0}h2{font-size:1.2rem;margin:2rem 0 .5rem}
.note{color:var(--muted);font-size:.95rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem 1.1rem}
.cue{display:inline-block;padding:0 .45em;border-radius:1em;font:600 .8rem/1.5 system-ui,sans-serif;
vertical-align:.1em}
.mood{background:hsl(var(--hue) 70% 85%);color:hsl(var(--hue) 55% 22%);
border:1px solid hsl(var(--hue) 50% 60%)}
.perf{background:#e7e7e7;color:#444;border:1px dashed #999}
@media (prefers-color-scheme:dark){.mood{background:hsl(var(--hue) 40% 24%);
color:hsl(var(--hue) 80% 85%)}.perf{background:#32353b;color:#ddd}}
button,input,label{font:inherit}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:.5rem 1.4rem;
min-height:44px;cursor:pointer;font-weight:600}
.pick{min-height:44px;padding:.2rem .6rem;border:1px solid var(--line);border-radius:8px}
.pick input{width:1.3rem;height:1.3rem}
.mix{margin-top:.8rem}
button:focus-visible,input:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.row{display:flex;flex-wrap:wrap;gap:.8rem 1.5rem;align-items:center;margin:.7rem 0}
.row label{display:flex;gap:.45rem;align-items:center}
input[type=range]{accent-color:var(--accent)}
#seek{width:100%}
audio{width:100%;margin-top:.4rem}
ul.listen{padding-left:1.2rem}ul.listen li{margin:.35rem 0}
pre{white-space:pre-wrap;word-break:break-word;font:.85rem/1.4 ui-monospace,monospace}
details summary{cursor:pointer;color:var(--accent);margin:.5rem 0}
.tags{display:inline-block;padding:.1rem .6rem;border-radius:1em;border:1px solid var(--line);
font:600 .85rem system-ui,sans-serif}
"""

_PAGE_JS = """
(function(){
  var v=document.getElementById('voice'), b=document.getElementById('bed');
  var play=document.getElementById('play'), seek=document.getElementById('seek');
  var clock=document.getElementById('clock'), note=document.getElementById('trouble');
  var all=[v,b].filter(Boolean);
  if(!v&&!b){return}
  var lead=v||b;
  function fmt(s){s=Math.max(0,Math.floor(s||0));return Math.floor(s/60)+':'+('0'+(s%60)).slice(-2)}
  function total(){return Math.max.apply(null,all.map(function(a){return a.duration||0}))}
  var dragging=false;
  function show(){
    var end=lead.duration||total()||0;
    seek.max=end;
    if(!dragging){seek.value=lead.currentTime}
    clock.textContent=fmt(lead.currentTime)+' / '+fmt(end);
  }
  function follow(){
    all.forEach(function(a){
      if(a!==lead&&Math.abs(a.currentTime-lead.currentTime)>0.15){a.currentTime=lead.currentTime}
    });
  }
  function trouble(){
    all.forEach(function(a){a.pause()});
    play.textContent='Play';
    note.textContent='The sound could not play. Check that the sound files are in this folder.';
  }
  function go(){
    follow(); note.textContent='';
    all.forEach(function(a){
      var tried=a.play();
      if(tried&&tried.catch){tried.catch(trouble)}
    });
    play.textContent='Pause';
  }
  function stop(){all.forEach(function(a){a.pause()});play.textContent='Play'}
  play.addEventListener('click',function(){lead.paused?go():stop()});
  seek.addEventListener('pointerdown',function(){dragging=true});
  seek.addEventListener('pointerup',function(){dragging=false});
  seek.addEventListener('input',function(){
    var at=Math.min(parseFloat(seek.value),(lead.duration||Infinity)-0.05);
    all.forEach(function(a){a.currentTime=at});show();
  });
  all.forEach(function(a){a.addEventListener('error',trouble)});
  lead.addEventListener('waiting',function(){
    all.forEach(function(a){if(a!==lead){a.pause()}});
  });
  lead.addEventListener('playing',function(){
    all.forEach(function(a){if(a!==lead){follow();a.play().catch(trouble)}});
  });
  lead.addEventListener('timeupdate',function(){follow();show()});
  setInterval(function(){if(!lead.paused){follow()}},250);
  all.forEach(function(a){a.addEventListener('loadedmetadata',show)});
  lead.addEventListener('ended',function(){stop();all.forEach(function(a){a.currentTime=0});show()});
  function wire(box,vol,el){
    if(!el){return}
    box.addEventListener('change',function(){el.muted=!box.checked});
    vol.addEventListener('input',function(){el.volume=parseFloat(vol.value)});
    el.muted=!box.checked; el.volume=parseFloat(vol.value);
  }
  wire(document.getElementById('voice-on'),document.getElementById('voice-vol'),v);
  wire(document.getElementById('bed-on'),document.getElementById('bed-vol'),b);
  show();
})();
"""


def _listen_list(voice_tags: bool | None, with_names: bool) -> str:
    items = [
        "The voice changes where a coloured tag starts.",
        "The music has no singing or words.",
        "You can always hear the voice over the music.",
        "Voice and music start and end together.",
    ]
    if voice_tags is False:
        items[0] = "Mood tags are off, so the voice should sound even all the way through."
    if with_names:
        items.append("Names are said the way your table says them.")
    return "".join(f"<li>{_E(i)}</li>" for i in items)


# What each voice tag means, in plain words (the run prints "《Tense》 -> [urgently]").
_PLAIN_TAG = {
    "short pause": "a short pause",
    "long pause": "a long pause",
    "whispers": "the voice whispers",
    "sighs": "the voice sighs",
}


def cue_meaning(line: str) -> str:
    """One cue-map line ("《Tense》 -> [urgently]") as chips and plain words."""
    cue, arrow, result = line.strip().partition(" -> ")
    if not arrow:
        return _E(line.strip())
    chip = tagged_html(cue)
    if result == "music only":
        return f"{chip} no voice change (it only changes the music)"
    tag = result.strip("[]")
    return f"{chip} {_E(_PLAIN_TAG.get(tag, 'the voice sounds ' + tag))}"


def _player(have: dict[str, bool]) -> str:
    """The voice and the music as one player (play, pause, seek, each on or off) and the mix."""
    if not (have[VOICE] or have[MUSIC]):
        return '<p class="note">No sound was made for this test. Run it again with sound on.</p>'
    voice = f'<audio id="voice" src="{_E(FILE_NAMES[VOICE])}" preload="auto"></audio>'
    bed = f'<audio id="bed" src="{_E(FILE_NAMES[MUSIC])}" preload="auto"></audio>'
    off = {k: "" if have[k] else " disabled" for k in (VOICE, MUSIC)}
    missing = (
        ""
        if all(have[k] for k in (VOICE, MUSIC))
        else '<p class="note">One of the two sound files is not in this folder.</p>'
    )
    mixed = (
        '<p class="note">Both mixed is what the bot would send as one recording.</p>'
        f'<audio controls preload="none" title="Both mixed" src="{_E(FILE_NAMES[MIX])}"></audio>'
        if have[MIX]
        else ""
    )
    return f"""<div class="card" id="together">
{voice if have[VOICE] else ""}{bed if have[MUSIC] else ""}
<div class="row"><button id="play" type="button">Play</button>
<span id="clock" aria-live="off">0:00 / 0:00</span>
<span id="trouble" class="note" role="status"></span></div>
<input id="seek" type="range" min="0" max="0" step="0.1" value="0" aria-label="Where you are">
<div class="row">
<label class="pick"><input id="voice-on" type="checkbox" checked{off[VOICE]}>
<span>Voice on</span></label>
<label class="pick"><input id="bed-on" type="checkbox" checked{off[MUSIC]}>
<span>Music on</span></label>
</div>
<p class="note"><b>Voice only</b> is the speaking. <b>Music and ambience</b> is the music
and the background sounds, like wind. Turn Voice or Music off to hear the other alone.</p>
{missing}
<details><summary>Volume</summary>
<div class="row">
<label>Voice volume
<input id="voice-vol" type="range" min="0" max="1" step="0.05" value="1"></label>
<label>Music volume
<input id="bed-vol" type="range" min="0" max="1" step="0.05" value="0.8"></label>
</div></details>
<div class="mix"><b>Both mixed</b>{mixed}</div>
</div>"""


def page(review: Review, folder: Path, *, with_names: bool = False) -> str:
    """The whole review page for what is in `folder`: the player first, then the recap."""
    have = {kind: (folder / FILE_NAMES[kind]).is_file() for kind in (VOICE, MUSIC, MIX)}
    if review.voice_tags is None:
        tags = "Not recorded whether mood tags were on."
    elif review.voice_tags:
        tags = "Mood tags: on. The voice acts them out."
    else:
        tags = "Mood tags: off. The voice reads evenly."
    title = review.title or "Recap test"
    saved = f'<p class="note">{_E(NOT_SAVED)}.</p>'
    if review.with_cues:
        recap = f'<div class="card"><p>{tagged_html(review.with_cues)}</p></div>'
        plain = _paragraphs(review.plain or strip_cues(review.with_cues))
        house = (
            "<ul>" + "".join(f"<li>{_E(b)}</li>" for b in review.housekeeping) + "</ul>"
            if review.housekeeping
            else '<p class="note">None.</p>'
        )
        music = (
            "<ol>" + "".join(f"<li>{_E(m)}</li>" for m in review.music) + "</ol>"
            if review.music
            else '<p class="note">None.</p>'
        )
    else:
        recap = plain = house = music = saved
    confirm = (
        "<h2>Facts to be confirmed</h2>"
        '<p class="note">Names the recap used that DMbot does not know yet. For the DM only.</p>'
        '<div class="card"><ul>'
        + "".join(f"<li>{_E(n)}</li>" for n in review.to_confirm)
        + "</ul></div>"
        if review.to_confirm
        else ""
    )
    meanings = (
        '<h2>How each tag changes the voice</h2><div class="card"><ul>'
        + "".join(f"<li>{cue_meaning(c)}</li>" for c in review.cue_map)
        + "</ul></div>"
        if review.cue_map
        else ""
    )
    report = (
        "<details><summary>Technical details (not needed)</summary>"
        f"<pre>{_E(review.report)}</pre></details>"
        if review.report
        else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recap test: {_E(title)}</title><style>{_PAGE_CSS}</style></head>
<body><main>
<h1>{_E(title)}</h1>
<p><span class="tags">{_E(tags)}</span></p>
<p class="note">This is a test of the recap. Nothing was posted or sent.
It holds your campaign's story, so keep this folder private.</p>
<h2>Listen</h2>
{_player(have)}
<h2>What to listen for</h2>
<ul class="listen">{_listen_list(review.voice_tags, with_names)}</ul>
<h2>The recap, with mood tags</h2>
<p class="note">A coloured tag shows where a mood starts.
A grey tag is a pause or a change of pace.</p>
{recap}
{meanings}
<h2>The recap without the tags</h2>
<div class="card">{plain}</div>
<h2>Game bookkeeping kept out of the story</h2>
<p class="note">Dice and rules lines.</p>
<div class="card">{house}</div>
<h2>What the music was asked for</h2>
<div class="card">{music}</div>
{confirm}
{report}
</main>
<script>{_PAGE_JS}</script>
</body></html>
"""


def write_page(folder: Path) -> Path:
    """Rebuild `review.html` in `folder` from the files in it (no paid call)."""
    private_dir(folder)
    review = read_folder(folder)
    target = folder / PAGE_FILE
    write_private(target, page(review, folder, with_names=review.names_given))
    return target


def count_lines(job: Any, away_found: int | None) -> list[str]:
    """The away check's and the beat sheet's counts (#2738), numbers only: how many notes are left
    for the DM, how many of the first finds the writer's fix cleared, how many readings; how many
    windows of the transcript, parts of the opening and beats. Never a word of the draft or the
    transcript, so two variants can be compared. `away_found`: what the first reading found (None
    when not reported: then nothing is claimed fixed)."""
    from dmbot.recaps import away, critics
    from dmbot.recaps import split as sp

    loop = critics.loop_from_json(job.critics)
    left = sum(1 for n in loop.notes if away.is_note(n))
    found = left if away_found is None else away_found
    split = sp.from_json(job.split)
    return [
        f"Away: notes {left}, fixed {max(found - left, 0)}, rounds {loop.round}",
        f"Beats: windows {job.window_count}, parts {len(split.parts) if split else 0}, "
        f"beats {len(job.beats)}",
    ]
