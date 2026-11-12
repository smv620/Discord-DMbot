// What the website's search bar knows about each page (#3855): the page's address, its title, one plain line saying what it is
// for, and a hand-kept list of the words people use for it ("a summary of episodes" finds recaps because "summary" and
// "episode" are listed there; spelling alone cannot). Everything is matched in the browser; nothing here is sent anywhere.
// A page you must sign in for still has a link: the account pages already send a visitor to sign in first. Keep each list to
// words people really type; a word on two pages makes both match, so a word goes on the page it points to most. A new page
// gets an entry here (web/test/search.test.ts checks every public page and every account page has one).
export interface SearchEntry {
  /** The page's address, with a section after `#` when the entry is one section of a page. */
  path: string;
  title: string;
  /** What the page is for, in one short plain line. */
  about: string;
  /** Words and short phrases people use for it. */
  words: string[];
}

export const searchIndex: SearchEntry[] = [
  { path: "/", title: "DMbot", about: "A quiet helper for the game master of a tabletop game.", words: ["home", "start", "what is dmbot", "about", "front page", "welcome"] },
  { path: "/how-it-works", title: "How it works", about: "What DMbot does at the table, step by step.", words: ["how does it work", "explain", "what does it do", "listen", "voice channel", "advice", "dm screen", "overview"] },
  { path: "/pricing", title: "Prices", about: "The plans and what each one costs.", words: ["price", "prices", "pricing", "cost", "how much", "pay", "payment", "plan", "plans", "free", "trial", "subscription", "subscribe", "billing", "refund", "cheap"] },
  { path: "/pricing#questions", title: "Questions and answers", about: "Short answers to what people ask before they start.", words: ["faq", "q&a", "questions", "common questions", "can i", "do i need"] },
  { path: "/install", title: "Add to Discord", about: "Add DMbot to your Discord server.", words: ["install", "add", "add to discord", "invite", "get started", "set up", "setup", "begin", "bot link"] },
  { path: "/help", title: "How to use DMbot", about: "Every step for game masters and players, in plain words.", words: ["help", "how to", "guide", "instructions", "manual", "tutorial", "support", "stuck", "problem", "not working"] },
  { path: "/help#dm-heading", title: "Help for the game master", about: "What the game master does, step by step.", words: ["game master", "dm help", "run a game", "campaign setup", "dungeon master"] },
  { path: "/help#player-heading", title: "Help for players", about: "What players need to know, including how to stop being recorded.", words: ["player", "players", "consent", "stop recording me", "stop recording", "opt out", "recording", "privacy of voice", "agree to be recorded", "delete my voice"] },
  { path: "/help#account-heading", title: "Help with your account", about: "Signing in, your account and your plan.", words: ["sign in", "login", "log in", "account help", "password", "email change", "delete account"] },
  { path: "/help/questions", title: "Quick answers", about: "What DMbot knows when you ask it, in a few words each.", words: ["quick answers", "questions", "faq", "what does dmbot know", "ask dmbot"] },
  { path: "/help#emails-heading", title: "Help with emails from DMbot", about: "Which emails DMbot sends and how to stop them.", words: ["email", "emails", "unsubscribe", "stop emails", "newsletter", "notifications"] },
  { path: "/help/snags", title: "If something doesn't work", about: "What to try when something goes wrong.", words: ["broken", "error", "doesn't work", "fix", "bug", "wrong", "failed", "snag"] },
  { path: "/players", title: "For players", about: "What DMbot does and does not do with a player's voice.", words: ["player", "players", "my voice", "who hears", "safe", "private", "what is recorded", "consent"] },
  { path: "/whats-new", title: "What's new", about: "The latest changes to DMbot.", words: ["new", "latest", "changes", "changelog", "update", "updates", "release", "added"] },
  { path: "/whats-new/older", title: "Older changes", about: "Everything that changed in DMbot before the latest.", words: ["older", "earlier", "past", "changelog", "history", "changes"] },
  { path: "/hello", title: "Say hello", about: "Write to the people who make DMbot.", words: ["contact", "write to us", "message us", "feedback", "email us", "get in touch", "hello", "suggestion"] },
  { path: "/legal/terms", title: "Terms of use", about: "The rules for using DMbot.", words: ["terms", "terms of service", "rules of use", "legal", "agreement", "conditions"] },
  { path: "/legal/privacy", title: "Privacy", about: "What DMbot keeps, why, and for how long.", words: ["privacy", "data", "personal data", "gdpr", "what do you store", "keep my data", "delete my data", "cookies"] },
  { path: "/legal/refunds", title: "Refunds", about: "When you can get your money back.", words: ["refund", "refunds", "money back", "cancel", "cancel subscription", "charge"] },
  { path: "/credits", title: "Credits", about: "Who and what DMbot is built with.", words: ["credits", "thanks", "licence", "license", "open source", "attribution", "made by"] },
  { path: "/account", title: "My account", about: "Your campaigns, plan and settings.", words: ["my account", "dashboard", "my campaigns", "profile", "settings", "plan", "sign in", "log in"] },
  { path: "/account/campaigns", title: "Campaign", about: "One campaign's settings, backup and players.", words: ["campaign", "campaign settings", "backup", "download backup", "delete campaign", "move campaign", "hand over", "give campaign"] },
  { path: "/account/recaps", title: "Your recaps", about: "The written story of each session.", words: ["recap", "recaps", "summary", "summaries", "episode", "episodes", "session summary", "previously", "story so far", "what happened", "narrated", "audio recap", "listen to recap"] },
  { path: "/account/recap-style", title: "Recap style", about: "How your recaps are written and what they must include.", words: ["recap style", "tone", "voice of recap", "narrator", "length", "write differently", "must include"] },
  { path: "/account/names", title: "Names", about: "The people, places and things DMbot knows, and how to say them.", words: ["names", "npc list", "characters", "people", "places", "spelling", "pronounce", "pronunciation", "how to say", "rename", "merge names", "same as", "who is"] },
  { path: "/account/npc", title: "NPC", about: "What the players know about one non-player character.", words: ["npc", "non-player character", "villain", "notes on a character", "character page"] },
  { path: "/account/clarify", title: "Clear up one name", about: "Answer what DMbot wants to know about a single name.", words: ["clear up a name", "who is this", "unclear name", "clarify"] },
  { path: "/account/questions", title: "Questions for you", about: "Everything DMbot is waiting for you to answer.", words: ["questions for me", "waiting for me", "to do", "needs answer", "inbox", "pending", "check names", "decisions"] },
  { path: "/account/web", title: "Story Graph", about: "The web of people, places and how they connect.", words: ["graph", "story graph", "connections", "relationships", "who knows who", "map of the story", "web", "network", "family tree"] },
  { path: "/account/timeline", title: "Story timeline", about: "The story in the order it happened.", words: ["timeline", "calendar", "in order", "dates", "when did", "chronology", "history"] },
  { path: "/account/transcripts", title: "Session transcripts", about: "What was said at each session, as written text.", words: ["transcript", "transcripts", "what was said", "written record", "download transcript", "raw text", "session text"] },
  { path: "/account/import", title: "Import sessions", about: "Bring in sessions you played before DMbot.", words: ["import", "old sessions", "past sessions", "upload transcript", "earlier games", "bring in", "previous sessions"] },
  { path: "/account/lore", title: "Upload lore", about: "Give DMbot your world's background notes.", words: ["lore", "upload lore", "world notes", "background", "backstory", "setting notes", "adventure notes", "upload document"] },
  { path: "/account/house-rules", title: "House rules", about: "Your own rules for the table.", words: ["house rules", "my rules", "custom rules", "rule changes", "rulings", "rules document"] },
  { path: "/account/homebrew", title: "Homebrew", about: "Your own monsters, spells and items.", words: ["homebrew", "custom monsters", "custom spells", "my own items"] },
  { path: "/account/sharing", title: "Shared rules", about: "Share your rules with another campaign, or follow someone's.", words: ["share rules", "shared rules", "follow rules", "ruleset", "rule set", "publish rules", "share code"] },
  { path: "/account/pictures", title: "Pictures", about: "Pictures for your names and scenes.", words: ["pictures", "images", "portraits", "art", "icons", "avatar", "drawing"] },
  { path: "/account/restore", title: "Load a backup", about: "Bring a saved campaign back.", words: ["restore", "load backup", "restore backup", "bring back", "recover", "undo delete", "lost campaign"] },
];
