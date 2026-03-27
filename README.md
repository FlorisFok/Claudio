# Claudio — Directional Audio Cues for Claude Code

Know which terminal needs your attention — without looking at the screen.

Claudio plays short stereo pings tied to where each [Claude Code](https://docs.anthropic.com/en/docs/claude-code) window sits on your screen. When a task finishes or needs input, you hear a sound from that direction.

```
┌─────────────┬─────────────┐
│  top-left   │  top-right  │
├─────────────┼─────────────┤
│ bottom-left │bottom-right │
└─────────────┴─────────────┘
```

> Use headphones for the clearest left/right placement.

## Prerequisites

- macOS (uses `afplay` and `osascript`)
- Python 3.10+
- [iTerm2](https://iterm2.com) for `claudio launch`
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI (`claude`) on your PATH

## Install

```bash
# Install via pip (editable mode):
pip install -e .

# Or install directly from GitHub (HTTPS):
pip install git+https://github.com/FlorisFok/Claudio.git

# Or via SSH, if you have access:
pip install git+ssh://git@github.com/FlorisFok/Claudio.git
```

## Hook setup

`claudio launch` automatically writes the three hooks into `.claude/settings.json` in your working directory (creating it if needed, and merging safely if it already exists).

You can also set them manually:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "claudio hook Stop" }
        ]
      }
    ],
    "Notification": [
      {
        "hooks": [
          { "type": "command", "command": "claudio hook Notification" }
        ]
      }
    ]
  }
}
```

Then open your terminals with `claudio launch` so each window has its audio position set.

## Launch

```bash
claudio launch                                    # 4 iTerm2 windows in a 2×2 grid
claudio launch -p "fix the tests" "add logging"   # 2 windows, each starting claude with a prompt
claudio launch -d ~/my-project                    # custom working directory
claudio launch --terminal iterm2
```

`launch` sets `CLAUDE_AUDIO_POSITION` in each window automatically. Without `launch`, set it yourself:

```bash
export CLAUDE_AUDIO_POSITION=top-left
claude
```

## Commands

| Command | Description |
|---|---|
| `claudio launch` | Open up to 4 iTerm2 windows in a 2×2 grid |
| `claudio hook Stop` | Hook handler — reads Claude Code JSON from stdin |
| `claudio hook Notification` | Hook handler for notification events |
| `claudio demo` | Play every cue × position combo |
| `claudio play <position> <cue>` | Play a specific cue manually |
| `claudio list` | Show available positions, cues, and current env var |

## Cues

| Cue | Trigger | Sound |
|---|---|---|
| `input-needed` | Permission prompt, idle prompt, elicitation dialog | Two quick calls |
| `done` | Task finished (`Stop` event) | Short resolved finish |

## License

MIT — see [LICENSE](LICENSE).
