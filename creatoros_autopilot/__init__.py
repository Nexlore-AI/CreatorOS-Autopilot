# SPDX-License-Identifier: AGPL-3.0-or-later
"""CreatorOS Autopilot — research, write, voice, render and publish short videos.

Open source under AGPL-3.0-or-later. The pieces:

    core/       the job: spec, ledger, schedule, settings, lock, media
    render/     the renderer and its styles
    providers/  llm, tts, research and publish backends
    quality/    the gates a script must pass before it ships
    agents/     research and scripting
    cli/        the `creatoros` command
    runtime/    the only OS-specific code
"""
__version__ = "0.1.0"
__all__ = ["__version__"]
