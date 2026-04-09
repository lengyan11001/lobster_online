Bundled FFmpeg

This skill can use FFmpeg binaries placed inside this folder before falling back to the system PATH.

Current Windows bundle layout:

- `tools/ffmpeg/windows/ffmpeg.exe`
- `tools/ffmpeg/windows/ffprobe.exe`
- `tools/ffmpeg/windows/avcodec-58.dll`
- `tools/ffmpeg/windows/avdevice-58.dll`
- `tools/ffmpeg/windows/avfilter-7.dll`
- `tools/ffmpeg/windows/avformat-58.dll`
- `tools/ffmpeg/windows/avutil-56.dll`
- `tools/ffmpeg/windows/postproc-55.dll`
- `tools/ffmpeg/windows/swresample-3.dll`
- `tools/ffmpeg/windows/swscale-5.dll`

Notes:

- This package uses a self-contained Windows FFmpeg shared build, so the required FFmpeg DLLs must ship together with the `.exe` files.
- This makes the skill easier to move between Windows machines without asking users to install FFmpeg separately.
- For macOS or Linux, add the corresponding platform binaries under this folder and extend packaging as needed.
- Keep FFmpeg, FFprobe, and the bundled DLLs from the same build.
