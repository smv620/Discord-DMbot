"""The recap audio's parts that need no provider (#1413; the calls themselves are #1383).

Pure functions from a finished recap's text to the requests the audio step will send: the
narration script and its cue map (`script`), the pronunciation rules (`names`), the music
composition plan and the ambience requests (`music`) and the ffmpeg mix (`mix`). No network, no
key, no database: everything here is testable without a provider.
"""
