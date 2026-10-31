import React from "react";
import { AbsoluteFill, Img, OffthreadVideo, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import type { Beat, HostState, Timeline } from "../lib/types";
import type { Theme } from "../lib/theme";

// 主播层：一个容器在「全屏 / 缩小 / 侧移 / 小窗 / 隐藏」几何之间弹簧插值，
// 里面的画面（整帧主播）按同一个缩放比放置；小窗为圆形时把脸对到圆心。
type Geo = { x: number; y: number; w: number; h: number; r: number; k: number; ox: number; oy: number; o: number };

function geoFor(state: HostState, theme: Theme, W: number, H: number, face: [number, number]): Geo {
  const full = { x: 0, y: 0, w: W, h: H, r: 0, k: 1, ox: 0, oy: 0, o: 1 };
  const rect = (x: number, y: number, w: number, r: number, o = 1) => ({ x, y, w, h: (w * H) / W, r, k: w / W, ox: 0, oy: 0, o });
  switch (state) {
    case "full": return full;
    case "shrink": return rect(W * 0.09, H * 0.055, W * 0.82, 26);
    case "side": return rect(W * 0.035, H * 0.2, W * 0.6, 22);
    case "off":
    case "pip": {
      if (theme.pip === "rect") return rect(W * 0.03, H - (W * 0.25 * H) / W - H * 0.07, W * 0.25, 14, state === "off" ? 0 : 1);
      const d = H * 0.23, k = 0.44;            // 圆形头像：直径 d，内容缩放 k，脸对准圆心
      return { x: W * 0.035, y: H - d - H * 0.07, w: d, h: d, r: d / 2, k,
               ox: d / 2 - face[0] * W * k, oy: d / 2 - face[1] * H * k + d * 0.12, o: state === "off" ? 0 : 1 };
    }
  }
}

const lerp = (a: number, b: number, p: number) => a + (b - a) * p;

export const Host: React.FC<{ tl: Timeline; theme: Theme }> = ({ tl, theme }) => {
  const frame = useCurrentFrame();
  const { fps, width: W, height: H } = useVideoConfig();
  const t = frame / fps;
  const beats = tl.beats;
  const i = Math.max(0, beats.findIndex((b) => t >= b.start && t < b.end));
  const cur: Beat = beats[i] ?? beats[beats.length - 1];
  const prev: Beat | undefined = beats[i - 1];
  const face = tl.meta.host.face;
  const a = geoFor(prev?.host ?? cur.host, theme, W, H, face);
  const b = geoFor(cur.host, theme, W, H, face);
  const p = spring({ frame: Math.round((t - cur.start) * fps), fps, config: { damping: 16, stiffness: 170, mass: 0.7 } });
  const g: Geo = { x: lerp(a.x, b.x, p), y: lerp(a.y, b.y, p), w: lerp(a.w, b.w, p), h: lerp(a.h, b.h, p), r: lerp(a.r, b.r, p),
                   k: lerp(a.k, b.k, p), ox: lerp(a.ox, b.ox, p), oy: lerp(a.oy, b.oy, p), o: lerp(a.o, b.o, p) };
  // 推近：只在全屏时生效，硬切进入（盖住录音跳切的接缝），绕脸部放大
  const punch = cur.host === "full" && cur.punch ? cur.punch : 1;
  const ringW = theme.pip === "round" ? interpolate(g.r / Math.max(1, g.w), [0, 0.5], [0, 5], { extrapolateRight: "clamp" }) : g.r > 2 ? 4 : 0;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={{ position: "absolute", left: g.x, top: g.y, width: g.w, height: g.h, borderRadius: g.r, overflow: "hidden", opacity: g.o,
                    boxShadow: g.r > 2 ? `0 0 0 ${ringW}px rgba(255,255,255,.95), 0 20px 60px rgba(0,0,0,.35)` : "none" }}>
        <div style={{ position: "absolute", left: g.ox, top: g.oy, width: W * g.k, height: H * g.k,
                      transform: `scale(${punch})`, transformOrigin: `${face[0] * 100}% ${face[1] * 100}%` }}>
          <HostFrame tl={tl} />
        </div>
      </div>
    </AbsoluteFill>
  );
};

// 主播画面：按 hostClips 时间表放口型视频；没有口型片段时退回定妆照（样片阶段）
const HostFrame: React.FC<{ tl: Timeline }> = ({ tl }) => {
  const { fps } = useVideoConfig();
  const fill: React.CSSProperties = { width: "100%", height: "100%", objectFit: "cover" };
  return (
    <AbsoluteFill>
      {tl.meta.host.kind === "image" ? <Img src={staticFile(tl.meta.host.src)} style={fill} /> : null}
      {tl.hostClips.map((c, n) => (
        <Sequence key={n} from={Math.round(c.start * fps)} durationInFrames={Math.max(1, Math.round((c.end - c.start) * fps))} layout="none">
          <AbsoluteFill>
            <OffthreadVideo src={staticFile(c.src)} muted style={fill} startFrom={Math.max(0, Math.round((c.offset ?? 0) * fps))} />
          </AbsoluteFill>
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
