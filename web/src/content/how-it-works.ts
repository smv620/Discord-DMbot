// The words for the "How DMbot works" page (#2527), made from Part 1 of docs/how-it-works.md (Part 2 of
// that file is for builders and stays off the site). The page says plainly what is live: MapBot is
// "coming later" and the picture reader is "built, and off until it is switched on", and neither is shown
// as working before it is. Keep "DMbot helps. The DM decides." and "only people who said yes" as they are.

export const howTitle = "How DMbot works";
export const howDescription =
  "A plain tour of DMbot's small helpers, what each one does, and who gets to see what. DMbot helps. The DM decides.";

export const howIntro = {
  heading: "The big idea",
  lead: "DMbot is a quiet helper for the Dungeon Master (DM). It listens to your game, remembers your story, and whispers tips to the DM. It has one golden rule:",
  rule: "DMbot helps. The DM decides.",
  ruleMore:
    "DMbot never makes up story and never makes a ruling. It only suggests, points to where a rule comes from, and remembers what happened.",
  team: "DMbot is not one big brain. It is a team of small helpers, and each one has one job. DMbot itself is the conductor: it hands each piece of the game to the helper that needs it, and it makes sure every helper follows the four rules below.",
};

export const fourRules = {
  heading: "Four rules every helper follows",
  rules: [
    {
      icon: "🙋",
      name: "Only people who said yes",
      text: 'DMbot only listens to people who pressed "yes". Bots are never recorded. Anyone can open the 🗂️ Menu and press Stop recording me, or say "Hey DMbot, off the record", at any time.',
    },
    { icon: "👑", name: "The DM decides", text: "Helpers suggest. Nothing goes into the story until the DM says yes." },
    {
      icon: "🤫",
      name: "Secrets stay with the DM",
      text: "Tips, secret names and plot notes go only to the DM's private screen. Players never get a secret from DMbot.",
    },
    {
      icon: "🏰",
      name: "Every campaign is its own world",
      text: "One campaign's story never leaks into another campaign, or into another Discord server.",
    },
  ],
};

export interface Helper {
  icon: string;
  name: string;
  /** A word in brackets after the name, like "(the Writer)". */
  aka?: string;
  job: string;
  /** Said when the helper is not working yet, so it is never shown as live before it is. */
  state?: string;
}

export const team = {
  heading: "Meet the team",
  lead: "Two helpers are not working yet: MapBot (coming later) and the picture reader (built, and off until it is switched on).",
  groups: [
    {
      title: "While you play",
      helpers: [
        { icon: "👂", name: "Ears", job: "Listens in the game's voice channel, only to people who said yes." },
        { icon: "✍️", name: "The scribe", job: "Writes down what each person says. That is the transcript." },
        { icon: "🧹", name: "The Cleaner", job: 'Fixes names the scribe heard wrong ("Tavern" → "Tavin").' },
        { icon: "🚦", name: "The game-or-not check", job: 'Sets aside talk that isn\'t the game ("who wants pizza?").' },
        {
          icon: "🏷️",
          name: "The who's-talking tag",
          job: "Marks each line: a hero, an NPC the DM is voicing, or the DM telling the story.",
        },
        {
          icon: "📏",
          name: "The rules helper",
          job: "Spots a rule moment and gives the DM a short tip, with where the rule comes from and how sure it is.",
        },
        { icon: "⏳", name: "TimeBot", job: "Keeps the game clock: rests, travel, spells running out, dawn and dusk." },
        {
          icon: "⚔️",
          name: "FightBot",
          job: 'In a fight, sends each player a private reminder of their own character\'s tricks ("You still have 2 spell slots").',
        },
        {
          icon: "🗣️",
          name: '"Hey DMbot"',
          job: 'Answers quick questions. The DM gets a private answer. A player\'s rules question is answered in the campaign\'s chat. Anything that needs the DM gets "That\'s one for your DM."',
        },
        {
          icon: "🖼️",
          name: "The picture reader",
          job: "Describes a picture someone posts in the game's chat, so the transcript can say what it showed.",
          state: "Built, and off until it is switched on.",
        },
      ] satisfies Helper[],
    },
    {
      title: "The story keepers",
      helpers: [
        {
          icon: "📚",
          name: "The Librarian",
          aka: "EntityBot",
          job: "The only helper allowed to write in the Story Memory. Others bring it ideas. It writes a fact down only when the DM says yes.",
        },
        {
          icon: "🧠",
          name: "The Story Memory",
          job: "Everything the campaign remembers: people, places, things, and how they connect. On the website it is the Story Graph. It keeps two views: The truth (what the DM knows) and The party (what the players know).",
        },
        {
          icon: "👥",
          name: "The NPC tracker",
          job: "Remembers the people you meet, who they work for, and who they love or hate.",
        },
        {
          icon: "🧭",
          name: "PlotBot",
          job: "The story's best friend. Keeps track of plot threads, promises and clues, notices when something doesn't fit, and checks every recap.",
        },
        {
          icon: "🗺️",
          name: "MapBot",
          job: "Will draw simple maps from facts the DM has confirmed.",
          state: "Coming later.",
        },
      ] satisfies Helper[],
    },
    {
      title: "After the game",
      helpers: [
        {
          icon: "🗂️",
          name: "The story check",
          job: "Tells the DM what to look at: secrets someone said out loud (with ↩️ Recant), new names, and things to check.",
        },
        {
          icon: "✂️",
          name: "The splitter",
          job: 'Finds where table talk, catch-up and "last time…" end and the real game begins.',
        },
        {
          icon: "✍️",
          name: "RecapBot",
          aka: "the Writer",
          job: "Writes the story of the session, using only what really happened.",
        },
        {
          icon: "🔍",
          name: "The checkers",
          job: "Read each draft. PlotBot checks the story, the fact checker checks every line really happened, the why checker checks no one's reasons were made up, and the style checker checks it reads well.",
        },
        { icon: "🎙️", name: "The narrator", job: "Reads the recap aloud with music, if the DM wants it." },
      ] satisfies Helper[],
    },
  ],
};

export type ChartGroup = "table" | "listen" | "think" | "private" | "chat" | "memory" | "dm" | "doc" | "out";

export interface ChartBox {
  group: ChartGroup;
  text: string;
}

/** A row of boxes that happen side by side; an arrow leads from one stage to the next. */
export interface ChartStage {
  boxes: ChartBox[];
  /** Said on the arrow into this stage, when it needs a word. */
  arrow?: string;
}

export interface Chart {
  id: string;
  heading: string;
  lead: string;
  /** Heading above the steps-as-a-list for screen readers (and anyone who prefers a list). */
  listHeading: string;
  lanes: { title: string; stages: ChartStage[] }[];
  /** The same steps in words, in order. */
  steps: string[];
  legend: { group: ChartGroup; text: string }[];
}

const b = (group: ChartGroup, text: string): ChartBox => ({ group, text });

export const duringChart: Chart = {
  id: "during-the-game",
  heading: "How it all flows: during the game",
  lead: "Follow the arrows from the top. Purple boxes are the helpers that think about the game. The gold crown is always the end of the line: the DM decides.",
  listHeading: "Read this chart as a list of steps",
  lanes: [
    {
      title: "From the table to DMbot",
      stages: [
        { boxes: [b("table", "🎲 Everyone talks at the table (only people who said yes)")] },
        { boxes: [b("listen", "👂 Ears listen")] },
        { boxes: [b("listen", "✍️ The scribe writes it down")] },
        { boxes: [b("listen", "🧹 The Cleaner fixes names")] },
        { boxes: [b("listen", "🚦 Game or not? Pizza talk is set aside")] },
        { boxes: [b("listen", "🏷️ Who's talking? A hero, an NPC, or the storyteller")] },
        { boxes: [b("think", "🎩 DMbot, the conductor")] },
      ],
    },
    {
      title: "What the helpers do with it",
      stages: [
        {
          boxes: [
            b("think", "📏 Rules helper"),
            b("think", "⏳ TimeBot"),
            b("think", "🧭 PlotBot and 👥 NPC tracker"),
            b("think", "⚔️ FightBot"),
            b("think", '🗣️ "Hey DMbot"'),
          ],
        },
        {
          boxes: [
            b("private", "🛡️ The DM's private screen (rules, time and plot)"),
            b("private", "📩 A private reminder to that player only (FightBot)"),
            b("chat", "💬 A short answer in the campaign's chat: rules, never secrets (Hey DMbot)"),
          ],
        },
        { boxes: [b("dm", "👑 The DM decides")] },
        { boxes: [b("memory", "📚 The Librarian")], arrow: "✅ Yes, remember it" },
        { boxes: [b("memory", "🧠 Story Memory: The truth · The party")] },
      ],
    },
  ],
  steps: [
    "Everyone at the table talks. Only people who said yes are heard.",
    "Ears listen in the voice channel.",
    "The scribe writes down what was said.",
    "The Cleaner fixes names that were heard wrong.",
    "The game-or-not check sets aside talk that isn't the game, like pizza talk.",
    "The who's-talking tag marks each line as a hero, an NPC, or the storyteller.",
    "DMbot, the conductor, hands the lines to the helpers who need them.",
    "The rules helper, TimeBot, PlotBot with the NPC tracker, FightBot and \"Hey DMbot\" each do their one job.",
    "Tips from the rules helper, TimeBot and PlotBot go to the DM's private screen.",
    "FightBot sends a private reminder to that player only.",
    "\"Hey DMbot\" gives a short answer. It is about rules and never about secrets.",
    "The DM decides what to do with a tip.",
    "If the DM says yes, remember it, the Librarian writes it in the Story Memory (The truth and The party).",
    "The Story Memory tells the Cleaner the names and tells PlotBot the story, for next time.",
  ],
  legend: [
    { group: "table", text: "The table" },
    { group: "listen", text: "Listening and writing down" },
    { group: "think", text: "Helpers that think about the game" },
    { group: "private", text: "Private to the DM or one player" },
    { group: "chat", text: "Posted in the campaign's chat" },
    { group: "memory", text: "Story Memory" },
    { group: "dm", text: "The DM decides" },
  ],
};

export const afterChart: Chart = {
  id: "after-the-game",
  heading: "How it all flows: after the game",
  lead: "When the game ends, DMbot writes the recap and tidies the Story Memory. Nothing is shared until the DM says so.",
  listHeading: "Read this chart as a list of steps",
  lanes: [
    {
      title: "Tidying the Story Memory",
      stages: [
        { boxes: [b("doc", "📄 The session's transcript")] },
        { boxes: [b("think", "🗂️ The story check: secrets said out loud, new names, things to check")] },
        { boxes: [b("dm", "👑 The DM answers: ✅ Remember · 🤫 Secret · ❌ Not true · ↩️ Recant")] },
        { boxes: [b("memory", "📚 The Librarian updates the 🧠 Story Memory")] },
      ],
    },
    {
      title: "Writing the recap",
      stages: [
        { boxes: [b("doc", "📄 The session's transcript")] },
        { boxes: [b("think", "✂️ The splitter finds where the real game starts")] },
        { boxes: [b("think", "🎬 The beats: what happened, in order")] },
        { boxes: [b("think", "🧭 PlotBot marks what matters, and why")] },
        { boxes: [b("think", "✍️ RecapBot writes a draft")] },
        {
          boxes: [b("think", "🔍 Do the checkers agree? PlotBot · fact checker · why checker · style checker")],
          arrow: "Not yet: RecapBot gets the notes and tries again (up to 4 times)",
        },
        { boxes: [b("dm", "👑 The DM reads the recap")], arrow: "Yes" },
        {
          boxes: [
            b("out", "✅ Post it as text"),
            b("out", "🎙️ Make the narration and music, then check it"),
            b("out", "📋 Keep it, don't post"),
          ],
        },
      ],
    },
  ],
  steps: [
    "The session's transcript is the starting point for two jobs.",
    "First job, tidying the Story Memory: the story check finds secrets said out loud, new names and things to check.",
    "The DM answers each one: Remember, Secret, Not true, or Recant.",
    "The Librarian updates the Story Memory with what the DM said yes to.",
    "Second job, writing the recap: the splitter finds where the real game starts.",
    "The beats are made: what happened, in order.",
    "PlotBot marks what matters, and why.",
    "RecapBot writes a draft.",
    "The checkers read it: PlotBot, the fact checker, the why checker and the style checker. If they don't agree yet, they say what to fix and RecapBot tries again, up to 4 tries.",
    "When they agree, the DM reads the recap.",
    "The DM picks one: post it as text, make the narration and music (then check it), or keep it without posting.",
  ],
  legend: [
    { group: "doc", text: "The transcript" },
    { group: "think", text: "Helpers that think about the game" },
    { group: "memory", text: "Story Memory" },
    { group: "dm", text: "The DM decides" },
    { group: "out", text: "What happens to the recap" },
  ],
};

export const whoSees = {
  heading: "Who gets to see what",
  columns: ["", "The DM", "Players"],
  rows: [
    {
      what: "The transcript",
      dm: "✅ Yes",
      players: "✅ Yes. Everyone in the server can read it. Only people who said yes are in it.",
    },
    {
      what: "Tips, secret names, plot notes (the DM screen)",
      dm: "✅ Yes",
      players: "🚫 No, only if the DM lets them peek.",
    },
    { what: "FightBot's reminder", dm: "No: it is private to the player", players: "✅ Yes, only the player it's about." },
    {
      what: "An answer to a rules question",
      dm: "✅ Yes",
      players: "✅ Yes, in the campaign's chat. Never a secret, never a ruling.",
    },
    { what: "The recap", dm: "✅ Yes, reads it first", players: "✅ Yes, only after the DM posts it." },
    {
      what: "The Story Memory",
      dm: "✅ Yes: The truth",
      players: "✅ Yes: The party (what the players already know).",
    },
  ],
};

export const brains = {
  heading: "Different helpers, different-sized brains",
  lead: "Some jobs are easy and some are hard, so DMbot uses different-sized AI brains:",
  items: [
    {
      icon: "⚡",
      name: "The quick brain",
      text: 'for small, fast jobs: fixing names, game-or-not, quick rules tips, the first "Hey DMbot" answer, FightBot.',
    },
    {
      icon: "🧐",
      name: "The careful brain",
      text: "for jobs that need more thought: finding new names, house rules, reading character sheets.",
    },
    { icon: "🔭", name: "The deep brain", text: "is for the hardest thinking, which is PlotBot's job." },
    { icon: "🖋️", name: "The storyteller brain", text: "for writing the recap." },
  ],
  close: "Small jobs use the small brain, so DMbot stays quick at the table and doesn't cost more than it needs to.",
};

export const moreQuestions = {
  heading: "Still have a question?",
  textBefore: "Send it to us on the",
  helloLink: "Say hello",
  textMid: "page. It has a short form, and we read every message. You can also read and ask in the public",
  qaLink: "Questions and answers",
  textAfter: "where anyone can see the answers, so don't put private details there.",
};

