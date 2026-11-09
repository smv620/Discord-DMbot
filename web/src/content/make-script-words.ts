// The words of "Make the script" on a session's Script tab (#3899). Plain words. The cost line is the one Discord's question
// gives, sent by the API as it is. Since #3901 a new session gets its script automatically; this button is for the sessions
// that have none (made before then, or while scripts were off), and it uses the AI allowance, so it is the game master's press.
export const makeScriptWords = {
  lead: "This session has no script yet. Making one uses some of your AI allowance.",
  button: "Make the script",
  making: "Making the script…",
  stage: "Making the script",
  quiet: "Still working. You can leave this page: the script will be here when it is done.",
  already: "A script was already made for this session.",
  plan: "Your plan doesn't include scripts.",
  off: "Scripts are switched off right now.",
  problems: {
    "plan-needed": "Your plan doesn't include scripts.",
    "script-off": "Scripts are switched off right now.",
    "script-already": "A script was already made for this session.",
    "not-allowed": "Only a game master can make the script.",
    "still-waiting": "DMbot is still making it. Check back in a few minutes.",
  } as Record<string, string>,
  failed: "Couldn't make the script. Try again in a minute.",
} as const;
