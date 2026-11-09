// The words of "Make the script" on a session's Script tab (#3899). Plain words. The cost line is the one Discord's question
// gives, sent by the API as it is; a script is only ever made when the game master presses the button (it uses the AI allowance).
export const makeScriptWords = {
  lead: "DMbot has not made a script of this session. It only does when you ask, because it uses your AI allowance.",
  button: "Make the script",
  making: "Making the script…",
  stage: "Making the script",
  quiet: "Still working. You can leave this page: the script will be here when it is done.",
  done: "The script is ready.",
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
