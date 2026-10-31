import React from "react";
import { AbsoluteFill, Audio, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import type { Timeline } from "./lib/types";
import { THEMES } from "./lib/theme";
import { Host } from "./components/Host";
import { StageBg, StageLayer } from "./components/Stage";
import { Asset, OverlayLayer } from "./components/Overlays";
import { Badge, Chapter, Questions, Subtitles } from "./components/Misc";

// 图层顺序：舞台底 → 舞台卡片 → 主播（可缩放）→ B-roll 全屏 → 叠加卡片 → 梗图 → 章节卡 → 字幕 → 角标
export const Episode: React.FC<{ tl: Timeline; badge?: string }> = ({ tl, badge }) => {
  const f = useCurrentFrame(); const { fps } = useVideoConfig(); const t = f / fps;
  const theme = THEMES[tl.meta.theme] ?? THEMES["v2-glass"];
  const beat = tl.beats.find((b) => t >= b.start && t < b.end) ?? tl.beats[tl.beats.length - 1];
  const showBroll = beat.kind !== "chapter" && beat.host === "off" && beat.bg;
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <StageBg theme={theme} />
      <StageLayer beat={beat} theme={theme} tl={tl} />
      <Host tl={tl} theme={theme} />
      {showBroll ? <AbsoluteFill><Asset id={beat.bg} tl={tl} kb /></AbsoluteFill> : null}
      <OverlayLayer tl={tl} theme={theme} filter={(o) => o.type !== "meme"} />
      {beat.overlays.filter((o) => o.type === "questions").map((o, i) => <Questions key={i} o={o} theme={theme} />)}
      <OverlayLayer tl={tl} theme={theme} filter={(o) => o.type === "meme"} />
      {beat.kind === "chapter" ? <Chapter beat={beat} tl={tl} theme={theme} /> : null}
      <Subtitles tl={tl} theme={theme} />
      <Badge text={badge} theme={theme} />
      <Sound tl={tl} />
    </AbsoluteFill>
  );
};

// 声音：旁白逐拍摆放；BGM 循环、有人声时自动压低；音效按时间点
const Sound: React.FC<{ tl: Timeline }> = ({ tl }) => {
  const { fps, durationInFrames } = useVideoConfig();
  const speaking = (fr: number) => tl.audio.some((a) => fr / fps >= a.start && fr / fps < a.start + (a as any).dur);
  return (
    <>
      {tl.audio.map((a, i) => (
        <Sequence key={`v${i}`} from={Math.round(a.start * fps)} layout="none"><Audio src={staticFile(a.src)} volume={a.volume ?? 1} /></Sequence>
      ))}
      {tl.meta.bgm ? (
        <Audio src={staticFile(tl.meta.bgm)} loop
               volume={(fr) => { const base = tl.meta.bgmVolume ?? 0.16; const v = speaking(fr) ? base : base * 2.2;
                                 return v * interpolate(fr, [0, 20, durationInFrames - 45, durationInFrames], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }); }} />
      ) : null}
      {tl.sfx.map((s, i) => (
        <Sequence key={`s${i}`} from={Math.max(0, Math.round(s.t * fps))} layout="none"><Audio src={staticFile(s.src)} volume={s.volume ?? 0.5} /></Sequence>
      ))}
    </>
  );
};
