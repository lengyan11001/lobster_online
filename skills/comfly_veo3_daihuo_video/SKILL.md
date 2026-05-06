---
name: comfly_veo3_daihuo_video
description: Generate ecommerce promo videos from product images through ai.comfly.chat Google-Veo endpoints. Use when the user wants VEO3 or VEO3.1 image-to-video generation, especially with the user-confirmed `veo3.1-fast` model alias, plus 9:16 short-form带货视频, async task submission, task polling, and prompt generation from product images for OpenClaw.
metadata: {"openclaw":{"emoji":"video","homepage":"https://ai.comfly.chat/api-set"}}
---

# Comfly Google-Veo 带货视频

Use this skill when the user wants to turn product images into ecommerce promo videos through `ai.comfly.chat`, especially for Google Veo models such as `veo3.1-fast`, `veo3.1-pro`, `veo3.1-components`, `veo3-pro-frames`, and `veo3-fast-frames`.

This skill is based on two confirmed sources:

- the existing local Sora2 frontend project, which already had a good prompt-generation flow
- the current browser-visible Comfly docs, which now expose Google-Veo endpoints under `v2`

## Confirmed API pattern

- Upload image if needed: `POST /v1/files`
- Generate prompt candidates from image: `POST /v1/chat/completions`
- Submit Veo image-to-video task: `POST /v2/videos/generations`
- Query Veo task: `GET /v2/videos/generations/{task_id}`

## Confirmed request model values from the current docs

- `veo3.1`
- `veo3.1-pro`
- `veo3.1-components`
- `veo3-pro-frames`
- `veo3-fast-frames`
- `veo2-fast-frames`
- `veo2-fast-components`

Important:

- The browser-visible docs page showed `veo3.1`, but the user explicitly confirmed that the actually usable model in this environment is `veo3.1-fast`.
- In this skill, prefer the user-confirmed runtime value `veo3.1-fast` for real submissions.
- Keep `veo3.1` only as a documentation reference, not as the default submit value here.

## Confirmed request fields for `POST /v2/videos/generations`

- `prompt`: required
- `model`: required
- `images`: required for image-to-video docs page
- `aspect_ratio`: optional, supports `9:16` and `16:9`
- `enhance_prompt`: optional

The docs explicitly say:

- if `aspect_ratio` is omitted, the backend tries to infer it from the reference image and otherwise defaults to landscape
- Veo only supports English prompts, so if the user provides Chinese prompts and wants auto-translation, set `enhance_prompt` to `true`

## Confirmed task status values for `GET /v2/videos/generations/{task_id}`

- `NOT_START`
- `IN_PROGRESS`
- `SUCCESS`
- `FAILURE`

The successful response example contains:

- `task_id`
- `status`
- `progress`
- `fail_reason`
- `data.output` as the final mp4 URL

## Preferred workflow

1. Build or choose the prompt.

- If the user only gives a product image, first generate 5 prompt candidates from the image.
- For ecommerce use, make the prompts conversion-oriented:
  - product clearly visible
  - real usage scenario
  - camera movement and product close-ups
  - no subtitle overlay
  - no sticker overlay
  - no watermark

2. Prepare the image URL.

- If the user gives a local image path, upload it through:

```powershell
powershell -ExecutionPolicy Bypass -File "{baseDir}\scripts\comfly-video.ps1" -Action upload-image -ImagePath "<absolute-image-path>"
```

- Use the returned URL in the `images` array.

3. Submit the Veo generation task.

- For Chinese prompts, keep `enhance_prompt` off by default in the packaged pipeline; enable it only for explicit single-shot translation/debugging, because provider-side expansion can introduce unwanted captions or on-screen text.
- For short-form ecommerce video, default `aspect_ratio` to `9:16`.
- Run:

```powershell
powershell -ExecutionPolicy Bypass -File "{baseDir}\scripts\comfly-video.ps1" -Action submit-video -Model "veo3.1-fast" -Prompt "<prompt>" -ImagePath "<absolute-image-path>" -AspectRatio "9:16" -EnhancePrompt true
```

4. Poll the task.

```powershell
powershell -ExecutionPolicy Bypass -File "{baseDir}\scripts\comfly-video.ps1" -Action poll-video -TaskId "<task-id>" -PollIntervalSeconds 12 -MaxPollCount 50
```

- If status becomes `SUCCESS`, read `data.output` as the final video URL.
- If status becomes `FAILURE`, surface `fail_reason`.

## Ecommerce defaults

- platform intent: `douyin`
- output ratio: `9:16`
- prompt language from user: Chinese is acceptable
- model submission behavior: default `enhance_prompt=false` for the pipeline to preserve strict no-subtitle/no-text instructions
- strongest default model choice in this environment: `veo3.1-fast`
- quality-first option: `veo3.1-pro`
- multi-image reference option: `veo3.1-components`

## Prompt-generation guidance

The old Sora2 project already solved a useful subproblem: turning one product image into several strong ecommerce prompt candidates before the actual video call.

Keep that pattern.

- Upload image
- Analyze image with a vision-capable chat model
- Produce 5 candidate prompts
- Let the user choose one, unless they explicitly ask you to auto-pick

## Duration caveat

The visible Google-Veo docs page confirms `prompt`, `model`, `images`, `aspect_ratio`, and `enhance_prompt`, but it does not show a direct `seconds` parameter on the image-to-video endpoint.

Do not invent a duration parameter.

If the user insists on exact `30-60` seconds:

- say that Comfly's current Veo endpoint docs do not expose a direct duration field on this page
- submit with the documented fields only
- if the product needs a longer ad, propose:
  - multiple clips
  - post-edit stitching
  - another provider if exact duration control is required

## Safety rules

- Never hardcode API keys into committed files.
- If the user pasted a real key into chat or code, recommend rotating it because it is now exposed.
- Prefer documented model names from the current browser-visible docs over guessed aliases.

## OpenClaw config example

```json
{
  "skills": {
    "entries": {
      "comfly_veo3_daihuo_video": {
        "env": {
          "COMFLY_API_BASE": "https://ai.comfly.chat",
          "COMFLY_API_KEY": "sk-xxxx",
          "COMFLY_VIDEO_MODEL": "veo3.1-fast"
        }
      }
    }
  }
}
```

## Source notes

If you need migration details or the old frontend behavior, read:

- `{baseDir}\references\source_project_notes.md`

## Python pipeline

There is also a Python runtime-compatible pipeline at:

- `{baseDir}\scripts\comfly_storyboard_pipeline.py`

It implements this flow:

1. upload product image
2. use `gemini-2.5-pro` to analyze the product image and generate:
   - product summary
   - main character definition
   - 6 storyboard plans by default
3. generate one consistent main character reference image with `nano-banana-2`
4. generate 6 storyboard images in parallel by default, each using:
   - product image
   - character image
   - shot prompt
5. submit 6 Veo video tasks in parallel by default
6. poll 6 Veo video tasks in parallel by default

The Python pipeline defaults to `veo3.1-fast`.
The storyboard and character image generation defaults to `nano-banana-2`, matching the existing `nanobanna2` reference project flow.
It now also writes step-by-step debug artifacts under `runs/` and retries upload, analysis, image generation, video submit, and video generation failures.
It also returns a `usage` summary for the current run:

- successful analysis call = `1` point
- successful image generation call = `2` points
- successful video generation result = `2` points

Failed calls do not count toward the returned points total.

## Bundled FFmpeg

This skill now prefers FFmpeg binaries packaged inside the skill directory before falling back to the system PATH.

Current Windows bundle path:

- `{baseDir}\tools\ffmpeg\windows\ffmpeg.exe`
- `{baseDir}\tools\ffmpeg\windows\ffprobe.exe`
- `{baseDir}\tools\ffmpeg\windows\*.dll` for the required FFmpeg shared libraries

That means Windows users can use the merge step without installing FFmpeg separately, as long as these bundled binaries and DLLs are shipped together with the skill.

## Locale behavior

The Python pipeline now supports locale-aware prompt planning through these optional inputs:

- `platform`
- `country`
- `language`

Rules:

- If the user does not specify `platform` or `country`, default to mainland China domestic ecommerce style and Simplified Chinese copy.
- If the user specifies `tk` or `tiktok` but no country, default to English copy and a global TikTok creator persona.
- If the user specifies a `country`, prioritize that country's main consumer language and localized character style.
- The character's face, styling, daily environment, naming style, and vibe should change with the selected locale, not stay fixed as a China-market persona.
- For compatibility, the JSON field names such as `title_cn` and `hook_line_cn` remain unchanged, but their text content should follow the resolved local language instead of always being Chinese.
