/** @jsxImportSource preact */
// One player for a recap's voice and music (#2017): a single Play and seek bar drives both, with an
// on/off switch and a volume for each. The two sound files play in step (recap-sync.ts), as in the
// test review page, so "voice without music" is one tap, not two players kept together by hand.
import { useEffect, useRef, useState } from "preact/hooks";

import { panelWords } from "../content/recap-panel";
import { clock, needsFollow, seekTarget } from "./recap-sync";

interface Props {
  voice?: string | undefined;
  music?: string | undefined;
  /** A file would not load: the page may read fresh links once (they last 15 minutes). */
  onFail: () => void;
}

export default function RecapPlayer({ voice, music, onFail }: Props) {
  const voiceEl = useRef<HTMLAudioElement>(null);
  const musicEl = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [at, setAt] = useState(0);
  const [end, setEnd] = useState(0);
  const [voiceOn, setVoiceOn] = useState(true);
  const [musicOn, setMusicOn] = useState(true);
  const [voiceVol, setVoiceVol] = useState(1);
  const [musicVol, setMusicVol] = useState(1);
  const [trouble, setTrouble] = useState(false);
  const dragging = useRef(false);
  const reported = useRef(new Set<string>());
  const links = `${voice}|${music}`;

  const all = (): HTMLAudioElement[] =>
    [voiceEl.current, musicEl.current].filter((a): a is HTMLAudioElement => a !== null);
  // The voice leads: the music is a bed under it. With no voice file the music leads.
  const lead = (): HTMLAudioElement | null => voiceEl.current ?? musicEl.current;

  const follow = (): void => {
    const l = lead();
    if (!l) return;
    for (const a of all()) {
      if (a !== l && needsFollow(l.currentTime, a.currentTime)) a.currentTime = l.currentTime;
    }
  };
  const show = (): void => {
    const l = lead();
    if (!l) return;
    setEnd(l.duration || Math.max(0, ...all().map((a) => a.duration || 0)));
    if (!dragging.current) setAt(l.currentTime);
  };
  const stop = (): void => {
    all().forEach((a) => a.pause());
    setPlaying(false);
  };
  // A play() cut short by our own pause (the follower waiting for a buffering lead, or Pause right
  // after Play) rejects with AbortError: that is not a file that won't play.
  const cannotPlay = (error?: unknown): void => {
    if (error instanceof DOMException && error.name === "AbortError") return;
    stop();
    setTrouble(true);
  };
  const go = (): void => {
    follow();
    setTrouble(false);
    for (const a of all()) {
      const tried = a.play() as Promise<void> | undefined;
      tried?.catch(cannotPlay);
    }
    setPlaying(true);
  };

  // Muting and volume sit on the elements themselves, so one switch never touches the other file.
  useEffect(() => {
    if (voiceEl.current) {
      voiceEl.current.muted = !voiceOn;
      voiceEl.current.volume = voiceVol;
    }
  }, [voiceOn, voiceVol, voice]);
  useEffect(() => {
    if (musicEl.current) {
      musicEl.current.muted = !musicOn;
      musicEl.current.volume = musicVol;
    }
  }, [musicOn, musicVol, music]);

  // New links (after the one-time refresh) load from the start; nothing plays on stale ones.
  const loaded = useRef(`${voice}|${music}`);
  useEffect(() => {
    const now = `${voice}|${music}`;
    if (loaded.current === now) return; // the first run is the page opening, not new links
    loaded.current = now;
    setPlaying(false);
    setAt(0);
    setEnd(0);
    setTrouble(false);
  }, [voice, music]);

  // Drift is checked a few times a second while playing, not only on the lead's own updates.
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(follow, 250);
    return () => clearInterval(timer);
  }, [playing]);

  if (!voice && !music) return null;

  const l = (): HTMLAudioElement | null => lead();
  const onLeadWaiting = (): void => {
    // While the lead is buffering, the other file waits for it.
    all().forEach((a) => a !== l() && a.pause());
  };
  const onLeadPlaying = (): void => {
    follow();
    for (const a of all()) {
      if (a !== l()) (a.play() as Promise<void> | undefined)?.catch(cannotPlay);
    }
  };
  const onLeadEnded = (): void => {
    stop();
    all().forEach((a) => {
      a.currentTime = 0;
    });
    setAt(0);
  };
  const seek = (value: number): void => {
    const target = seekTarget(value, l()?.duration ?? Infinity);
    all().forEach((a) => {
      a.currentTime = target;
    });
    setAt(target);
  };

  // The lead gets the events that move the page; the other file only follows.
  const leadProps = {
    onTimeUpdate: () => {
      follow();
      show();
    },
    onWaiting: onLeadWaiting,
    onPlaying: onLeadPlaying,
    onEnded: onLeadEnded,
    // A pause or play from outside the page (a media key, a headset) moves the button too.
    onPause: () => {
      setPlaying(false);
      all().forEach((a) => a !== l() && a.pause());
    },
    onPlay: () => setPlaying(true),
  };
  const failedFor = reported.current;
  const fail = (): void => {
    stop();
    if (failedFor.has(links)) return; // two files failing together ask for fresh links once
    failedFor.add(links);
    onFail();
  };
  const common = {
    preload: "none" as const,
    onError: fail,
    onLoadedMetadata: show,
  };
  const leadIsVoice = Boolean(voice);

  return (
    <div class="recap-player">
      <p class="muted small">{panelWords.lead}</p>
      {voice && (
        <audio
          ref={voiceEl}
          src={voice}
          aria-label={panelWords.voice}
          {...common}
          {...(leadIsVoice ? leadProps : {})}
        />
      )}
      {music && (
        <audio
          ref={musicEl}
          src={music}
          aria-label={panelWords.music}
          {...common}
          {...(!leadIsVoice ? leadProps : {})}
        />
      )}
      <div class="row">
        <button
          type="button"
          class="button secondary"
          onClick={() => (playing ? stop() : go())}
        >
          {playing ? panelWords.pause : panelWords.play}
        </button>
        <input
          type="range"
          class="seek"
          min={0}
          max={end || 0}
          step={0.1}
          disabled={!end}
          value={at}
          aria-label={panelWords.position}
          aria-valuetext={`${clock(at)} of ${clock(end)}`}
          onPointerDown={() => (dragging.current = true)}
          onPointerUp={() => (dragging.current = false)}
          onPointerCancel={() => (dragging.current = false)}
          onChange={() => (dragging.current = false)}
          onInput={(e) => seek(parseFloat((e.currentTarget as HTMLInputElement).value))}
        />
        <span class="small" aria-hidden="true">
          {end ? `${clock(at)} / ${clock(end)}` : clock(at)}
        </span>
      </div>
      <div class="mix-controls">
        {voice && (
          <div class="mix-line">
            <label class="switch-label">
              <input
                type="checkbox"
                role="switch"
                class="switch"
                checked={voiceOn}
                onChange={(e) => setVoiceOn((e.currentTarget as HTMLInputElement).checked)}
              />{" "}
              {panelWords.voice}{" "}
              <span class="small" aria-hidden="true">
                {voiceOn ? panelWords.switchOn : panelWords.switchOff}
              </span>
            </label>
            <label class="volume">
              <span class="small" aria-hidden="true">
                {panelWords.volume}
              </span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={voiceVol}
                disabled={!voiceOn}
                aria-label={panelWords.voiceVolume}
                onInput={(e) => setVoiceVol(parseFloat((e.currentTarget as HTMLInputElement).value))}
              />
            </label>
          </div>
        )}
        {music && (
          <div class="mix-line">
            <label class="switch-label">
              <input
                type="checkbox"
                role="switch"
                class="switch"
                checked={musicOn}
                onChange={(e) => setMusicOn((e.currentTarget as HTMLInputElement).checked)}
              />{" "}
              {panelWords.music}{" "}
              <span class="small" aria-hidden="true">
                {musicOn ? panelWords.switchOn : panelWords.switchOff}
              </span>
            </label>
            <label class="volume">
              <span class="small" aria-hidden="true">
                {panelWords.volume}
              </span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={musicVol}
                disabled={!musicOn}
                aria-label={panelWords.musicVolume}
                onInput={(e) => setMusicVol(parseFloat((e.currentTarget as HTMLInputElement).value))}
              />
            </label>
          </div>
        )}
      </div>
      {trouble && (
        <p class="warn" role="alert">
          {panelWords.wontPlay}
        </p>
      )}
    </div>
  );
}
