# Source Project Notes

These notes combine:

- the existing local Sora2 frontend project
- the current Comfly browser docs that now expose Google-Veo endpoints

## Old project flow that is still worth keeping

The older Sora2 page already had a good pre-generation workflow:

1. upload image
2. analyze image with a multimodal chat model
3. generate 5 ecommerce prompt candidates
4. let the user choose the best prompt
5. submit the chosen prompt to the video backend
6. poll until the final video URL is ready

This flow is still useful in the Veo version.

## Confirmed image-generation reference flow from the local project

The local `nanobanna2` page already implements the image generation pattern we should reuse for storyboard images:

- default image model: `nano-banana-2`
- optional higher-quality variants:
  - `nano-banana-2-2k`
  - `nano-banana-2-4k`
- endpoint: `POST /v1/images/generations`
- request body shape:
  - `model`
  - `prompt`
  - `aspect_ratio`
  - `image` as an array of uploaded reference image URLs

This is the correct reference path for:

- main character reference image generation
- storyboard image generation

So the Python pipeline should default to `nano-banana-2`, not `gpt-4o-image`.

## What changed in the Veo integration

The video task API is no longer the old Sora2 style:

- old style used `POST /v1/videos`
- current Google-Veo docs show `POST /v2/videos/generations`
- task query now uses `GET /v2/videos/generations/{task_id}`

## Confirmed Google-Veo submit fields from the browser docs

- `prompt`
- `model`
- `images`
- `aspect_ratio`
- `enhance_prompt`

The visible docs page for Veo image-to-video does not show:

- `seconds`
- `size`
- `input_reference`

So those old Sora2 fields must not be carried over blindly.

## Confirmed model values from the browser docs

- `veo3.1`
- `veo3.1-pro`
- `veo3.1-components`
- `veo3-pro-frames`
- `veo3-fast-frames`
- `veo2-fast-frames`
- `veo2-fast-components`

## Important doc behavior

- If `aspect_ratio` is missing, the backend infers it from the image when possible, otherwise defaults to landscape.
- The docs explicitly say Veo supports English prompts. If the user writes Chinese prompts and wants automatic conversion, `enhance_prompt` can be enabled.

## Confirmed query response shape

The visible example for `GET /v2/videos/generations/{task_id}` includes:

- `task_id`
- `platform`
- `action`
- `status`
- `fail_reason`
- `submit_time`
- `start_time`
- `finish_time`
- `progress`
- `data.output`

Status enum shown in docs:

- `NOT_START`
- `IN_PROGRESS`
- `SUCCESS`
- `FAILURE`

## Important migration note

The user explicitly confirmed that in the real runtime environment the usable model is:

- `veo3.1-fast`

The browser docs page did not show `veo3.1-fast` as a visible enum value and instead showed `veo3.1`.

For this workspace, the migration should follow the user-confirmed runtime value first:

- use `veo3.1-fast` as the default submit model
- keep `veo3.1` only as a docs-reference discrepancy note

## Recommendation

For OpenClaw and reusable skill logic:

- keep the old prompt-generation stage
- submit Veo jobs with documented `v2` JSON payloads
- use `images` instead of old file-form submission fields
- poll `v2/videos/generations/{task_id}`
- default the model to `veo3.1-fast`
- normalize the output to `status`, `task_id`, `progress`, `fail_reason`, and final `mp4url = data.output`
