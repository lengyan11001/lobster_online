# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

## Lobster Publishing Orchestration

Use this as the local playbook when the user asks OpenClaw to generate, edit, or publish content through Lobster MCP.

- Existing asset publish: `list_publish_accounts` if account is ambiguous, then `publish_content` with the existing `asset_id`. Do not call generation tools first.
- Generate then publish: call the generation capability, wait for the real terminal result, then publish the returned `saved_assets[0].asset_id`.
- Task polling: SuTui image/video tasks use `task.get_result`; Comfly `video_` tasks use `comfly.daihuo` `poll_video`; full TVC pipeline jobs use `comfly.daihuo.pipeline` polling/terminal result.
- Account/platform checks: use `list_publish_accounts`; use account IDs from tool results when possible.
- Xiaohongshu needs title plus description or tags. Douyin/Toutiao can rely on backend AI copy fill when the user did not provide copy.
- Never claim success before the publishing/generation tool returns success. On failure, quote the returned reason and stop unless the user asks to retry.
