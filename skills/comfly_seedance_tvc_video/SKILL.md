---
name: comfly_seedance_tvc_video
description: Generate 20-second storyboard-driven promo videos by first creating two polished 10-second storyboard board images with gpt-image-2 (with automatic fallback to nano-banana-2 when gpt-image-2 fails) and then turning those boards into two Seedance clips with `doubao-seedance-2-0-fast-260128`. Use when the user provides product references plus a storyboard-board style target and wants a premium TVC workflow for beauty, skincare, FMCG, tea, and other brand-heavy commercial videos.
metadata: {"openclaw":{"emoji":"video","homepage":"https://ai.comfly.chat"}}
---

# Comfly Seedance TVC

Use this skill when the user wants a product to become:

- two polished storyboard board images
- each board covering about 10 seconds
- each board containing segment design + Chinese voice-over / copy
- two corresponding videos
- optionally merged into one 20-second ad

This skill is intentionally different from `comfly_veo3_daihuo_video`:

- it accepts multiple reference images
- it produces two complete storyboard board images rather than many single-frame stills
- each board is designed as a 10-second segment with Chinese copy and narration guidance
- it uses `gpt-image-2` for the board images by default, and automatically falls back to `nano-banana-2` if `gpt-image-2` exhausts its retries (e.g. the upstream `image=[url]` reference path is unstable)
- it uses `doubao-seedance-2-0-fast-260128` on the Seedance official-format endpoint for lower-cost testing

## Confirmed API pattern

- Upload local images when needed: `POST /v1/files`
- Analyze reference storyboard image: `POST /v1/chat/completions`
- Render per-shot storyboard stills: `POST /v1/images/generations`
- Submit Seedance task: `POST /seedance/v3/contents/generations/tasks`
- Query Seedance task: `GET /seedance/v3/contents/generations/tasks/{task_id}`

## Default model choices

- analysis model: `gpt-4.1-mini`
- storyboard board image model: `gpt-image-2` (primary), `nano-banana-2` (fallback)
- video model: `doubao-seedance-2-0-fast-260128`

## Workflow

1. Upload the primary reference image plus any extra product / packaging / style / scene references.
2. Analyze them into structured board JSON:
   - product / brand consistency notes
   - overall visual style
   - exactly two storyboard boards
   - each board covering about 10 seconds
   - Chinese voice-over text for that 10-second segment
   - English board-image prompt
   - English Seedance motion prompt
3. Render two polished storyboard board images with `gpt-image-2` (auto fallback to `nano-banana-2` on failure).
4. Submit two Seedance tasks in parallel:
   - board image as `first_frame`
   - all uploaded references as `reference_image`
5. Poll until both complete.
6. Optionally merge the two clips into a 20-second ad.

## Quality rules

- Each generated image is one complete 10-second storyboard board, not one single micro-shot.
- The board image should include time segmentation, sub-panels, Chinese notes, and Chinese voice-over/copy blocks.
- Use multiple references to keep product identity stable.
- The two videos should be launched in parallel.
- Testing stage defaults to the fast Seedance model; later it can switch to the higher-quality non-fast variant.

## Pipeline entry

The Python runtime entry is:

- `{baseDir}\scripts\comfly_seedance_storyboard_pipeline.py`

It accepts:

- `reference_image`
- `reference_images`
- `apikey`
- `base_url`
- `task_text`
- `platform`
- `country`
- `language`
- `analysis_model`
- `image_model`
- `image_model_fallback`
- `video_model`
- `storyboard_count` / `segment_count`
- `segment_duration_seconds`
- `total_duration_seconds`
- `merge_clips`

## Important notes

- This skill assumes the uploaded image is a storyboard board or visual shot brief, not just a plain product photo.
- The Seedance task body follows the official-format style:
  - `model`
  - `content`
  - `ratio`
  - `duration`
  - `generate_audio`
  - `watermark`
- For image-to-video, the generated still is sent as `role: "first_frame"`.
- The original storyboard image is sent as `role: "reference_image"` to help preserve the intended product identity and art direction.

## Safety

- Never hardcode API keys into committed files.
- If the user pasted a real key into chat, recommend rotating it after testing.
