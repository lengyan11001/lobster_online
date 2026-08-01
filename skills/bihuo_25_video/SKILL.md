---
name: bihuo_25_video
description: Create videos with multimodal references, first and last frames, video editing, or video extension in the 必火2.5 workbench.
metadata: {"openclaw":{"emoji":"video"}}
---

# 必火2.5

Use the dedicated workbench for these video tasks:

- Reference generation with images, videos, and audio in one request.
- First and last frame generation with exactly two frame images.
- Editing one existing video.
- Extending one existing video by a specified duration.

The workbench uploads source media to the user's asset library, submits an
asynchronous task through the managed server, resumes polling with the real
task ID after refresh, and saves the completed video back to the asset library.

Do not ask users for provider credentials or provider-specific model names.
