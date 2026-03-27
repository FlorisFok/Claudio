#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path


SAMPLE_RATE = 44_100
MASTER_VOLUME = 0.72


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    CYAN = "\033[38;5;117m"
    TEAL = "\033[38;5;37m"
    AMBER = "\033[38;5;214m"
    ORANGE = "\033[38;5;208m"
    GREEN = "\033[38;5;114m"
    RED = "\033[38;5;203m"
    MAGENTA = "\033[38;5;176m"
    GRAY = "\033[38;5;243m"
    WHITE = "\033[38;5;255m"


DIRECTION_COLORS: dict[str, str] = {
    "top-left": C.CYAN,
    "top-right": C.TEAL,
    "bottom-left": C.AMBER,
    "bottom-right": C.ORANGE,
}


def log_cue(position: str, cue_label: str, extra: str = "") -> None:
    color = DIRECTION_COLORS.get(position, C.WHITE)
    suffix = f"  {C.DIM}{C.GRAY}{extra}{C.RESET}" if extra else ""
    print(
        f"  {color}{C.BOLD}◈{C.RESET}  "
        f"{color}{position:<14}{C.RESET}"
        f"{C.DIM}│{C.RESET}  "
        f"{C.WHITE}{C.BOLD}{cue_label}{C.RESET}"
        f"{suffix}"
    )


def log_info(msg: str) -> None:
    print(f"  {C.DIM}{C.GRAY}▸ {msg}{C.RESET}")


def log_warn(msg: str) -> None:
    print(f"  {C.AMBER}⚠  {msg}{C.RESET}", file=sys.stderr)


def log_error(msg: str) -> None:
    print(f"  {C.RED}✖  {msg}{C.RESET}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Audio presets
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DirectionPreset:
    label: str
    pan: float
    base_freq: float
    brightness: float
    echo_delay_ms: float
    echo_decay: float
    itd_ms: float
    color: str


@dataclass(frozen=True)
class CuePreset:
    label: str
    hint: str
    duration: float
    pulse_count: int
    pulse_gap_ms: float
    attack_ms: float
    decay_rate: float
    pitch_sweep: float
    brightness_shift: float
    harmonic_ratio: float
    tremolo_hz: float
    tremolo_depth: float
    echo_tone_scale: float
    echo_decay_scale: float
    level: float


DIRECTIONS = [
    DirectionPreset(
        label="Top Left",
        pan=-0.92,
        base_freq=980.0,
        brightness=0.62,
        echo_delay_ms=28.0,
        echo_decay=0.24,
        itd_ms=0.34,
        color="#8ecae6",
    ),
    DirectionPreset(
        label="Top Right",
        pan=0.92,
        base_freq=980.0,
        brightness=0.62,
        echo_delay_ms=28.0,
        echo_decay=0.24,
        itd_ms=0.34,
        color="#219ebc",
    ),
    DirectionPreset(
        label="Bottom Left",
        pan=-0.92,
        base_freq=620.0,
        brightness=0.24,
        echo_delay_ms=58.0,
        echo_decay=0.38,
        itd_ms=0.34,
        color="#ffb703",
    ),
    DirectionPreset(
        label="Bottom Right",
        pan=0.92,
        base_freq=620.0,
        brightness=0.24,
        echo_delay_ms=58.0,
        echo_decay=0.38,
        itd_ms=0.34,
        color="#fb8500",
    ),
]

STARTUP_CUE = CuePreset(
    label="Startup",
    hint="Quick chime confirming launch",
    duration=0.28,
    pulse_count=1,
    pulse_gap_ms=0.0,
    attack_ms=4.0,
    decay_rate=14.0,
    pitch_sweep=0.30,
    brightness_shift=0.10,
    harmonic_ratio=2.0,
    tremolo_hz=0.0,
    tremolo_depth=0.0,
    echo_tone_scale=0.80,
    echo_decay_scale=0.40,
    level=0.65,
)

CUES = [
    CuePreset(
        label="Input Needed",
        hint="Two quick calls for attention",
        duration=0.46,
        pulse_count=2,
        pulse_gap_ms=0.12 * 1000.0,
        attack_ms=8.0,
        decay_rate=10.5,
        pitch_sweep=0.12,
        brightness_shift=0.18,
        harmonic_ratio=2.8,
        tremolo_hz=6.0,
        tremolo_depth=0.08,
        echo_tone_scale=0.84,
        echo_decay_scale=0.85,
        level=0.90,
    ),
    CuePreset(
        label="Approval",
        hint="Warm rising confirmation",
        duration=0.42,
        pulse_count=1,
        pulse_gap_ms=0.0,
        attack_ms=18.0,
        decay_rate=6.8,
        pitch_sweep=0.22,
        brightness_shift=0.05,
        harmonic_ratio=1.9,
        tremolo_hz=3.5,
        tremolo_depth=0.04,
        echo_tone_scale=0.72,
        echo_decay_scale=0.68,
        level=0.82,
    ),
    CuePreset(
        label="Done",
        hint="Short resolved finish",
        duration=0.40,
        pulse_count=3,
        pulse_gap_ms=0.07 * 1000.0,
        attack_ms=6.0,
        decay_rate=13.0,
        pitch_sweep=-0.07,
        brightness_shift=0.25,
        harmonic_ratio=3.15,
        tremolo_hz=0.0,
        tremolo_depth=0.0,
        echo_tone_scale=0.92,
        echo_decay_scale=0.58,
        level=0.78,
    ),
]

POSITION_MAP: dict[str, DirectionPreset] = {
    "top-left": DIRECTIONS[0],
    "top-right": DIRECTIONS[1],
    "bottom-left": DIRECTIONS[2],
    "bottom-right": DIRECTIONS[3],
}

CUE_MAP: dict[str, CuePreset] = {cue.label.lower().replace(" ", "-"): cue for cue in CUES}


# ---------------------------------------------------------------------------
# Audio synthesis
# ---------------------------------------------------------------------------

def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def build_ping(direction: DirectionPreset, cue: CuePreset) -> list[tuple[int, int]]:
    total_samples = int(SAMPLE_RATE * cue.duration)
    echo_samples = int((direction.echo_delay_ms / 1000.0) * SAMPLE_RATE)
    itd_samples = max(1, int((direction.itd_ms / 1000.0) * SAMPLE_RATE))
    pulse_gap = cue.pulse_gap_ms / 1000.0
    pulse_span = cue.duration / max(1, cue.pulse_count)
    mono_samples: list[float] = []

    left_gain = math.sqrt((1.0 - direction.pan) / 2.0)
    right_gain = math.sqrt((1.0 + direction.pan) / 2.0)
    brightness = clamp(direction.brightness + cue.brightness_shift, 0.08, 0.92)

    for index in range(total_samples):
        t = index / SAMPLE_RATE
        tone = 0.0

        for pulse_index in range(cue.pulse_count):
            pulse_start = pulse_index * pulse_gap
            pulse_time = t - pulse_start
            if pulse_time < 0.0 or pulse_time > pulse_span:
                continue

            attack = min(1.0, pulse_time / max(0.001, cue.attack_ms / 1000.0))
            decay = math.exp(-cue.decay_rate * pulse_time)
            envelope = attack * decay

            pulse_progress = clamp(pulse_time / max(0.001, pulse_span), 0.0, 1.0)
            shaped_freq = direction.base_freq * (1.0 + (cue.pitch_sweep * pulse_progress))
            modulation = 1.0
            if cue.tremolo_hz > 0.0 and cue.tremolo_depth > 0.0:
                modulation += cue.tremolo_depth * math.sin(
                    2.0 * math.pi * cue.tremolo_hz * pulse_time
                )

            fundamental = math.sin(2.0 * math.pi * shaped_freq * pulse_time)
            overtone = math.sin(
                2.0 * math.pi * shaped_freq * cue.harmonic_ratio * pulse_time
            )
            pulse_tone = (fundamental * (1.0 - brightness)) + (overtone * brightness)
            tone += pulse_tone * envelope * modulation

        echo = 0.0
        if index >= echo_samples:
            echo_time = (index - echo_samples) / SAMPLE_RATE
            echo = (
                math.sin(
                    2.0 * math.pi * direction.base_freq * cue.echo_tone_scale * echo_time
                )
                * math.exp(-(8.0 / max(0.25, cue.echo_decay_scale)) * echo_time)
                * direction.echo_decay
                * cue.echo_decay_scale
            )

        mono_samples.append((tone + echo) * cue.level)

    frames: list[tuple[int, int]] = []
    for index, _ in enumerate(mono_samples):
        left_index = index
        right_index = index
        if direction.pan < 0:
            right_index = max(0, index - itd_samples)
        elif direction.pan > 0:
            left_index = max(0, index - itd_samples)

        left_sample = mono_samples[left_index] * left_gain
        right_sample = mono_samples[right_index] * right_gain

        left_pcm = max(-32767, min(32767, int(left_sample * 32767 * MASTER_VOLUME)))
        right_pcm = max(-32767, min(32767, int(right_sample * 32767 * MASTER_VOLUME)))
        frames.append((left_pcm, right_pcm))

    return frames


def write_wave_file(frames: list[tuple[int, int]]) -> Path:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as handle:
        output_path = Path(handle.name)

    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        payload = b"".join(struct.pack("<hh", left, right) for left, right in frames)
        wav_file.writeframes(payload)

    return output_path


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

def play_sync(direction: DirectionPreset, cue: CuePreset) -> None:
    if shutil.which("afplay") is None:
        log_error("macOS 'afplay' not found — cannot play audio")
        sys.exit(1)

    wave_path = write_wave_file(build_ping(direction, cue))
    try:
        subprocess.run(["afplay", str(wave_path)], check=False)
    finally:
        wave_path.unlink(missing_ok=True)


def resolve_position() -> str:
    pos = os.environ.get("CLAUDE_AUDIO_POSITION", "").strip().lower()
    if pos not in POSITION_MAP:
        if pos:
            log_warn(f"Unknown CLAUDE_AUDIO_POSITION={pos!r}, falling back to top-left")
        else:
            log_warn("CLAUDE_AUDIO_POSITION not set, falling back to top-left")
        return "top-left"
    return pos


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_hook(event: str) -> None:
    """Called by Claude Code hooks — reads JSON from stdin, plays appropriate cue."""
    position = resolve_position()
    direction = POSITION_MAP[position]

    raw = sys.stdin.read()
    payload: dict = {}
    if raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            pass

    session_id = payload.get("session_id", "")
    extra = f"session {session_id[:12]}" if session_id else ""

    if event == "Stop":
        cue = CUE_MAP["done"]
        log_cue(position, cue.label, extra)
        play_sync(direction, cue)

    elif event == "Notification":
        notification_type = payload.get("notification_type", "")
        if notification_type in ("permission_prompt", "idle_prompt", "elicitation_dialog"):
            cue = CUE_MAP["input-needed"]
            log_cue(position, cue.label, f"{notification_type}  {extra}".strip())
            play_sync(direction, cue)
        else:
            log_info(f"ignoring notification type: {notification_type or '(empty)'}")


def cmd_play(position: str, cue_name: str) -> None:
    """Play a specific direction + cue combo."""
    if position not in POSITION_MAP:
        log_error(f"Unknown position: {position!r}")
        log_info(f"Choose from: {', '.join(POSITION_MAP)}")
        sys.exit(1)

    if cue_name not in CUE_MAP:
        log_error(f"Unknown cue: {cue_name!r}")
        log_info(f"Choose from: {', '.join(CUE_MAP)}")
        sys.exit(1)

    direction = POSITION_MAP[position]
    cue = CUE_MAP[cue_name]
    log_cue(position, cue.label)
    play_sync(direction, cue)


def cmd_demo() -> None:
    """Play every cue × direction combination."""
    print()
    print(f"  {C.WHITE}{C.BOLD}Directional Audio Cues — Full Demo{C.RESET}")
    print(f"  {C.DIM}{C.GRAY}Use headphones for clearest left/right placement{C.RESET}")
    print()

    for cue in CUES:
        print(f"  {C.MAGENTA}{C.BOLD}▪ {cue.label}{C.RESET}  {C.DIM}{cue.hint}{C.RESET}")
        for pos_name, direction in POSITION_MAP.items():
            log_cue(pos_name, cue.label)
            play_sync(direction, cue)
            time.sleep(0.18)
        print()
        time.sleep(0.25)

    print(f"  {C.GREEN}{C.BOLD}✔  Demo complete{C.RESET}")
    print()


def cmd_launch(work_dir: str | None, prompts: list[str] | None, terminal_app: str = "auto") -> None:
    """Open terminal windows in a 2x2 grid, each with CLAUDE_AUDIO_POSITION set."""
    if terminal_app == "auto":
        terminal_app = _detect_terminal_app()
        log_info(f"auto-detected terminal: {terminal_app}")

    cwd = work_dir or os.getcwd()
    all_positions = list(POSITION_MAP.keys())

    if prompts:
        if len(prompts) > 4:
            log_error(f"At most 4 prompts allowed (got {len(prompts)})")
            sys.exit(1)
        slots = all_positions[: len(prompts)]
    else:
        slots = all_positions

    app_label = {"iterm2": "iTerm2", "terminal": "Terminal"}.get(terminal_app, terminal_app)

    print()
    print(f"  {C.WHITE}{C.BOLD}Launching {len(slots)} Claude Code window{'s' if len(slots) != 1 else ''}{C.RESET}  {C.DIM}{C.GRAY}via {app_label}{C.RESET}")
    print(f"  {C.DIM}{C.GRAY}Working directory: {cwd}{C.RESET}")
    print()

    _setup_hooks(cwd)

    shell_lines: list[str] = []
    for i, pos in enumerate(slots):
        export_cmd = f"export CLAUDE_AUDIO_POSITION={pos}"
        cd_cmd = f"cd {_applescript_escape(cwd)}"
        if prompts:
            run_cmd = f"claude {_applescript_escape(prompts[i])}"
        else:
            run_cmd = "claude"
        combined = f"{cd_cmd} && {export_cmd} && clear && {run_cmd}"
        shell_lines.append(combined)

    applescript = _build_grid_applescript(slots, shell_lines, terminal_app)
    result = subprocess.run(
        ["osascript", "-e", applescript],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        log_error(f"Failed to launch {app_label} windows")
        if result.stderr.strip():
            log_info(result.stderr.strip())
        sys.exit(1)

    for i, pos in enumerate(slots):
        color = DIRECTION_COLORS[pos]
        if prompts:
            truncated = prompts[i] if len(prompts[i]) <= 40 else prompts[i][:37] + "..."
            print(
                f"    {color}{C.BOLD}◈  {pos:<14}{C.RESET} "
                f"{C.DIM}│{C.RESET}  {C.GREEN}running{C.RESET}  "
                f"{C.DIM}{C.GRAY}{truncated}{C.RESET}"
            )
        else:
            print(
                f"    {color}{C.BOLD}◈  {pos:<14}{C.RESET} "
                f"{C.DIM}│{C.RESET}  {C.GREEN}running{C.RESET}"
            )

    print()
    print(f"  {C.DIM}{C.GRAY}Claude Code is running in {len(slots)} window{'s' if len(slots) != 1 else ''}.{C.RESET}")
    print()

    for pos in slots:
        direction = POSITION_MAP[pos]
        play_sync(direction, STARTUP_CUE)
        time.sleep(0.08)


def _applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "'\\''")


def _app_exists(app_name: str) -> bool:
    """Check whether a macOS .app bundle is installed in common locations."""
    search_dirs = [
        "/Applications",
        "/System/Applications",
        os.path.expanduser("~/Applications"),
    ]
    return any(os.path.isdir(os.path.join(d, f"{app_name}.app")) for d in search_dirs)


def _detect_terminal_app() -> str:
    """Return 'iterm2' if iTerm2 is installed, otherwise fall back to 'terminal'."""
    if _app_exists("iTerm"):
        return "iterm2"
    return "terminal"


def _get_screen_size() -> tuple[int, int]:
    """Get screen resolution via AppKit (fast, no Finder dependency)."""
    result = subprocess.run(
        [
            "python3", "-c",
            "from AppKit import NSScreen; f=NSScreen.mainScreen().frame(); print(int(f.size.width), int(f.size.height))",
        ],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        parts = result.stdout.strip().split()
        return int(parts[0]), int(parts[1])
    log_warn("Could not detect screen size, using 1920x1080")
    return 1920, 1080


def _grid_bounds(w: int, h: int) -> list[tuple[int, int, int, int]]:
    half_w, half_h = w // 2, h // 2
    menu_bar = 25
    return [
        (0, menu_bar, half_w, half_h),       # top-left
        (half_w, menu_bar, w, half_h),        # top-right
        (0, half_h, half_w, h),               # bottom-left
        (half_w, half_h, w, h),               # bottom-right
    ]


def _build_grid_applescript_iterm2(positions: list[str], commands: list[str]) -> str:
    """Build AppleScript that creates iTerm2 windows in a 2×2 grid."""
    w, h = _get_screen_size()
    bounds = _grid_bounds(w, h)

    lines = [
        'tell application "iTerm2"',
        "  activate",
    ]

    for i, (pos, cmd) in enumerate(zip(positions, commands)):
        x1, y1, x2, y2 = bounds[i]
        escaped_cmd = cmd.replace('"', '\\"')
        lines.append(f'  set newWindow to (create window with default profile)')
        lines.append(f'  set bounds of newWindow to {{{x1}, {y1}, {x2}, {y2}}}')
        lines.append(f'  tell current session of newWindow')
        lines.append(f'    write text "{escaped_cmd}"')
        lines.append(f'  end tell')

    lines.append("end tell")
    return "\n".join(lines)


def _build_grid_applescript_terminal_app(positions: list[str], commands: list[str]) -> str:
    """Build AppleScript that creates Terminal.app windows in a 2×2 grid."""
    w, h = _get_screen_size()
    bounds = _grid_bounds(w, h)

    lines = [
        'tell application "Terminal"',
        "  activate",
    ]

    for i, (pos, cmd) in enumerate(zip(positions, commands)):
        x1, y1, x2, y2 = bounds[i]
        escaped_cmd = cmd.replace('"', '\\"')
        # Each bare `do script` opens a new window; the delay lets it appear
        # before we move it so `front window` refers to the right one.
        lines.append(f'  do script "{escaped_cmd}"')
        lines.append(f'  delay 0.4')
        lines.append(f'  set bounds of front window to {{{x1}, {y1}, {x2}, {y2}}}')

    lines.append("end tell")
    return "\n".join(lines)


def _setup_hooks(cwd: str) -> None:
    """Write or merge Claudio hooks into <cwd>/.claude/settings.json."""
    claude_dir = Path(cwd) / ".claude"
    settings_path = claude_dir / "settings.json"

    claude_dir.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            log_warn(f"Could not parse {settings_path}, overwriting hooks section only")

    hooks: dict = existing.setdefault("hooks", {})

    claudio_hooks = {
        "Stop":        "claudio hook Stop",
        "Notification": "claudio hook Notification",
    }

    for event, command in claudio_hooks.items():
        entry = {"hooks": [{"type": "command", "command": command}]}
        event_list: list = hooks.setdefault(event, [])
        already_present = any(
            any(h.get("command") == command for h in block.get("hooks", []))
            for block in event_list
        )
        if not already_present:
            event_list.append(entry)

    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    log_info(f"hooks written → {settings_path}")


def _build_grid_applescript(positions: list[str], commands: list[str], terminal_app: str) -> str:
    if terminal_app == "terminal":
        return _build_grid_applescript_terminal_app(positions, commands)
    return _build_grid_applescript_iterm2(positions, commands)


def cmd_list() -> None:
    """Print available positions and cues."""
    print()
    print(f"  {C.WHITE}{C.BOLD}Positions{C.RESET}  {C.DIM}(2×2 grid){C.RESET}")
    for pos_name in POSITION_MAP:
        color = DIRECTION_COLORS[pos_name]
        print(f"    {color}◈  {pos_name}{C.RESET}")
    print()
    print(f"  {C.WHITE}{C.BOLD}Cues{C.RESET}")
    for key, cue in CUE_MAP.items():
        print(f"    {C.WHITE}{key:<16}{C.RESET}{C.DIM}{cue.hint}{C.RESET}")
    print()
    print(f"  {C.WHITE}{C.BOLD}Environment{C.RESET}")
    current = os.environ.get("CLAUDE_AUDIO_POSITION", "")
    if current:
        color = DIRECTION_COLORS.get(current, C.WHITE)
        print(f"    {C.DIM}CLAUDE_AUDIO_POSITION={C.RESET}{color}{current}{C.RESET}")
    else:
        print(f"    {C.DIM}CLAUDE_AUDIO_POSITION  {C.AMBER}(not set){C.RESET}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claudio",
        description="Directional audio cues for Claude Code terminals",
    )
    sub = parser.add_subparsers(dest="command")

    hook_parser = sub.add_parser(
        "hook",
        help="Claude Code hook handler (reads JSON from stdin)",
    )
    hook_parser.add_argument(
        "event",
        choices=["Stop", "Notification"],
        help="Hook event name",
    )

    play_parser = sub.add_parser("play", help="Play a specific cue")
    play_parser.add_argument(
        "position",
        choices=list(POSITION_MAP),
        help="Grid position",
    )
    play_parser.add_argument(
        "cue",
        choices=list(CUE_MAP),
        help="Cue name",
    )

    sub.add_parser("demo", help="Play all cue/direction combos")
    sub.add_parser("list", help="Show available positions and cues")

    launch_parser = sub.add_parser(
        "launch",
        help="Open up to 4 terminal windows in a 2x2 grid with audio positions set",
    )
    launch_parser.add_argument(
        "-d", "--dir",
        default=None,
        help="Working directory for all windows (default: cwd)",
    )
    launch_parser.add_argument(
        "-p", "--prompt",
        nargs="+",
        default=None,
        metavar="PROMPT",
        help="Prompt(s) for claude — one per window, opens only as many windows as prompts given (max 4)",
    )
    launch_parser.add_argument(
        "-t", "--terminal",
        choices=["auto", "iterm2", "terminal"],
        default="auto",
        metavar="APP",
        help="Terminal app to use: auto (default), iterm2, or terminal (macOS Terminal.app)",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "hook":
        cmd_hook(args.event)
    elif args.command == "play":
        cmd_play(args.position, args.cue)
    elif args.command == "demo":
        cmd_demo()
    elif args.command == "list":
        cmd_list()
    elif args.command == "launch":
        cmd_launch(args.dir, args.prompt or None, args.terminal)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
