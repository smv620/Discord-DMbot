# Attribution: SCOWL (Spell Checker Oriented Word Lists)

`english_words.txt.gz` is built from SCOWL by Kevin Atkinson, release 2020.12.07
(<http://wordlist.aspell.net/>, downloaded from
<https://downloads.sourceforge.net/wordlist/scowl-2020.12.07.tar.gz>, SHA-256
`5587667caa20c4891390c2d42dbb4d5c4c3f41bee77af1457ece3ba23fb859cc`). Its copyright and
permission notice, and the notices of the lists it draws on, are in `SCOWL-Copyright.txt`, word
for word, as SCOWL asks.

## What is here, and what was changed

The English, American and British word lists of every size up to 60, merged. Only entries made of
the lower-case letters a to z are kept (no proper nouns, abbreviations, contractions or accented
words): 79,649 words, sorted, one a line. `python -m dmbot.devtools.english_words` makes the same
file from the release.

## What DMbot uses it for

Only to tell whether a lower-case word is an ordinary English word, so the Transcript Cleaner never
gives one capitals because a name is spelled the same (#5154). It is never sent to an AI.
