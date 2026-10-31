import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import type { Beat, Timeline } from "../lib/types";
import type { Theme } from "../lib/theme";

// 舞台内容：主播缩成小窗后，右侧大卡片里放的图表 / 文件 / 竞猜 / 步骤
const useT = () => { const f = useCurrentFrame(); const { fps } = useVideoConfig(); return { f, fps, t: f / fps }; };
const pop = (t: number, at: number, fps: number) => spring({ frame: Math.round((t - at) * fps), fps, config: { damping: 15, stiffness: 160 } });

export const StageBg: React.FC<{ theme: Theme }> = ({ theme }) => {
  const { f } = useT();
  return (
    <AbsoluteFill style={{ background: theme.stageBg, overflow: "hidden" }}>
      {theme.name === "v1-purple"
        ? [0, 1, 2, 3, 4, 5].map((i) => (   // v1：漂移的几何三角
            <div key={i} style={{ position: "absolute", left: `${(i * 19 + 7) % 100}%`, top: `${(i * 37 + (f * 0.04 * (i % 2 ? 1 : -1))) % 110 - 5}%`,
              width: 0, height: 0, borderLeft: "70px solid transparent", borderRight: "70px solid transparent",
              borderBottom: `120px solid rgba(${i % 2 ? "255,255,255" : "120,200,255"},.10)`, transform: `rotate(${i * 40 + f * 0.1}deg)` }} />))
        : [0, 1, 2].map((i) => (            // v2：缓慢漂移的玻璃光斑
            <div key={i} style={{ position: "absolute", width: 700, height: 700, borderRadius: "50%",
              left: 200 + i * 520 + Math.sin(f / 90 + i) * 60, top: 120 + (i % 2) * 380 + Math.cos(f / 110 + i) * 50,
              background: `radial-gradient(circle, ${["rgba(60,200,193,.20)", "rgba(44,123,229,.16)", "rgba(255,181,71,.10)"][i]}, transparent 65%)` }} />))}
    </AbsoluteFill>
  );
};

const Card: React.FC<{ theme: Theme; title?: string; source?: string; inP: number; children: React.ReactNode }> = ({ theme, title, source, inP, children }) => (
  <div style={{ position: "absolute", left: 560, top: 90, width: 1290, height: 860, ...theme.card, padding: "54px 70px", fontFamily: theme.font,
                transform: `translateY(${(1 - inP) * 60}px) scale(${0.96 + inP * 0.04})`, opacity: inP, display: "flex", flexDirection: "column" }}>
    {title ? <div style={{ alignSelf: "center", background: theme.name === "v1-purple" ? theme.accent : "rgba(60,200,193,.16)", color: theme.name === "v1-purple" ? "#fff" : theme.accent,
                           border: theme.name === "v1-purple" ? "none" : `1.5px solid ${theme.accent}`, fontSize: 38, fontWeight: 800, padding: "10px 30px", borderRadius: 12 }}>{title}</div> : null}
    <div style={{ flex: 1, position: "relative", marginTop: 30 }}>{children}</div>
    {source ? <div style={{ fontSize: 22, color: theme.cardMute, textAlign: "right" }}>数据来源：{source}</div> : null}
  </div>
);

const Bars: React.FC<{ s: any; theme: Theme; inP: number }> = ({ s, theme, inP }) => {
  const { t, fps } = useT();
  const max = Math.max(...s.bars.map((b: any) => b.value)) * 1.12;
  const n = s.bars.length; const colW = Math.min(300, 900 / n);
  return (
    <Card theme={theme} title={s.title} source={s.source} inP={inP}>
      <div style={{ position: "absolute", left: 0, right: 0, bottom: 90, top: 30, display: "flex", justifyContent: "center", alignItems: "flex-end", gap: Math.max(60, 500 / n) }}>
        {s.bars.map((b: any, i: number) => {
          const p = b.t != null ? interpolate(t - b.t, [0, 0.7], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: (x) => 1 - Math.pow(1 - x, 3) }) : inP;
          const h = (b.value / max) * 560 * p;
          const color = b.hot ? theme.hot : theme.accent;
          return (
            <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", width: colW }}>
              <div style={{ fontFamily: theme.mono, fontSize: 52, fontWeight: 800, color: b.hot ? theme.hot : theme.cardInk, opacity: p, marginBottom: 10, whiteSpace: "nowrap" }}>
                {(b.value * p).toLocaleString("en-US", { maximumFractionDigits: b.value % 1 ? 1 : 0 })}{b.unit ?? ""}</div>
              <div style={{ width: colW * 0.7, height: h, borderRadius: "10px 10px 0 0", background: `linear-gradient(180deg, ${color}, ${color}AA)`,
                            boxShadow: b.hot ? `0 0 40px ${theme.hot}55` : "none" }} />
              <div style={{ position: "absolute", bottom: 0, fontSize: 30, color: theme.cardInk, fontWeight: 700, textAlign: "center", whiteSpace: "pre-line", width: colW + 80,
                            transform: "translateY(80px)" }}>{b.label}</div>
            </div>
          );
        })}
      </div>
      {s.callout && t >= s.callout.t ? <Callout theme={theme} text={s.callout.text} p={pop(t, s.callout.t, fps)} /> : null}
    </Card>
  );
};

const Callout: React.FC<{ theme: Theme; text: string; p: number }> = ({ theme, text, p }) => (
  <div style={{ position: "absolute", right: 0, top: 10, background: theme.hot, color: "#1a1a1a", fontWeight: 900, fontSize: 36, padding: "12px 24px", borderRadius: 14,
                transform: `scale(${0.6 + p * 0.4}) rotate(${(1 - p) * -8 + 2}deg)`, opacity: p, boxShadow: "0 12px 30px rgba(0,0,0,.3)" }}>{text}</div>
);

const Line: React.FC<{ s: any; theme: Theme; inP: number }> = ({ s, theme, inP }) => {
  const { t, fps } = useT();
  const pts = s.points; const W = 1100, H = 520;
  const ys = pts.map((p: any) => p.y); const maxY = Math.max(...ys) * 1.15;
  const xy = pts.map((p: any, i: number) => [40 + (i / (pts.length - 1)) * (W - 80), H - (p.y / maxY) * (H - 40)]);
  const reveal = (s.reveal ?? []).map((r: any) => r.t ?? r);
  const prog = reveal.length ? interpolate(t, [reveal[0], (reveal[reveal.length - 1] ?? reveal[0]) + 0.8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : inP;
  const d = xy.map((q: number[], i: number) => `${i ? "L" : "M"}${q[0]},${q[1]}`).join(" ");
  return (
    <Card theme={theme} title={s.title} source={s.source} inP={inP}>
      <svg viewBox={`0 0 ${W} ${H + 60}`} style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
        <path d={d} fill="none" stroke={theme.accent} strokeWidth={8} strokeLinecap="round" strokeLinejoin="round" pathLength={1} strokeDasharray={1} strokeDashoffset={1 - prog} />
        {xy.map((q: number[], i: number) => (i / (pts.length - 1) <= prog + 0.001 ? (
          <g key={i}><circle cx={q[0]} cy={q[1]} r={12} fill={i === pts.length - 1 ? theme.hot : theme.accent} />
            <text x={q[0]} y={q[1] - 26} fill={theme.cardInk} fontSize={34} fontWeight={800} textAnchor="middle" fontFamily={theme.mono}>{pts[i].y}</text></g>) : null))}
        {pts.map((p: any, i: number) => <text key={i} x={xy[i][0]} y={H + 50} fill={theme.cardMute} fontSize={28} textAnchor="middle" fontFamily={theme.font}>{p.x}</text>)}
      </svg>
      {s.callout && t >= s.callout.t ? <Callout theme={theme} text={s.callout.text} p={pop(t, s.callout.t, fps)} /> : null}
    </Card>
  );
};

const Steps: React.FC<{ s: any; theme: Theme; inP: number }> = ({ s, theme, inP }) => {
  const { t, fps } = useT();
  return (
    <Card theme={theme} title={s.title} source={s.source} inP={inP}>
      <div style={{ display: "flex", flexDirection: "column", gap: 24, justifyContent: "center", height: "100%" }}>
        {s.items.map((it: any, i: number) => {
          const p = it.t != null ? pop(t, it.t, fps) : inP; if (p <= 0.001) return <div key={i} style={{ height: 110 }} />;
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 28, transform: `translateX(${(1 - p) * 120}px)`, opacity: p,
                                  background: theme.name === "v1-purple" ? "#F4F2FF" : "rgba(255,255,255,.06)", borderRadius: 16, padding: "20px 30px" }}>
              <div style={{ width: 60, height: 60, borderRadius: 30, background: theme.accent, color: "#fff", display: "grid", placeItems: "center", fontSize: 32, fontWeight: 900, flexShrink: 0 }}>{i + 1}</div>
              <div><div style={{ fontSize: 44, fontWeight: 800, color: theme.cardInk }}>{it.text}</div>
                {it.sub ? <div style={{ fontSize: 28, color: theme.cardMute, marginTop: 4 }}>{it.sub}</div> : null}</div>
            </div>);
        })}
      </div>
    </Card>
  );
};

const Doc: React.FC<{ s: any; theme: Theme; inP: number }> = ({ s, theme, inP }) => {
  const { t } = useT();
  return (
    <div style={{ position: "absolute", left: 600, top: 150, width: 1200, minHeight: 600, background: "#FBFBF6", borderRadius: 6, boxShadow: "0 30px 80px rgba(0,0,0,.45)",
                  padding: "70px 80px", transform: `translateY(${(1 - inP) * 80}px) rotate(${(1 - inP) * 2 - 0.6}deg)`, opacity: inP, fontFamily: '"Songti SC",Georgia,serif', color: "#222" }}>
      <div style={{ fontSize: 24, color: "#888", fontFamily: theme.font }}>{s.source}</div>
      <div style={{ fontSize: 46, fontWeight: 700, margin: "18px 0 40px", borderBottom: "2px solid #222", paddingBottom: 20, fontFamily: "Georgia,serif" }}>{s.title}</div>
      {s.lines.map((l: any, i: number) => {
        const hp = interpolate(t - (l.t ?? 0), [0.15, 0.6], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        return (
          <div key={i} style={{ fontSize: 54, lineHeight: 1.6, marginBottom: 34, opacity: t >= (l.t ?? 0) - 0.3 ? 1 : 0.18 }}>
            {l.text}<span style={{ fontWeight: 800, backgroundImage: "linear-gradient(#FFE27A,#FFE27A)", backgroundRepeat: "no-repeat",
                                   backgroundSize: `${hp * 100}% 70%`, backgroundPosition: "0 80%", padding: "0 4px" }}>{l.hl}</span>
          </div>);
      })}
    </div>
  );
};

const Quiz: React.FC<{ s: any; theme: Theme; inP: number }> = ({ s, theme, inP }) => {
  const { t, fps } = useT();
  const shown = t >= s.revealT; const p = shown ? pop(t, s.revealT, fps) : 0;
  return (
    <Card theme={theme} inP={inP}>
      <div style={{ textAlign: "center", fontSize: 52, fontWeight: 800, color: theme.cardInk, marginTop: 60 }}>{s.question}</div>
      <div style={{ textAlign: "center", marginTop: 70, fontFamily: theme.mono, fontWeight: 900, fontSize: 280, color: shown ? theme.hot : theme.cardMute,
                    transform: `scale(${shown ? 0.6 + p * 0.4 : 1})` }}>{shown ? s.answer : "?"}</div>
      {s.note ? <div style={{ textAlign: "center", fontSize: 28, color: theme.cardMute }}>{s.note}</div> : null}
    </Card>
  );
};

export const StageLayer: React.FC<{ beat: Beat; theme: Theme; tl: Timeline }> = ({ beat, theme }) => {
  const { f, fps } = useT();
  const s = beat.stage; if (!s) return null;
  const inP = spring({ frame: f - Math.round((beat.start + 0.08) * fps), fps, config: { damping: 16, stiffness: 150 } });
  const C: any = ({ bars: Bars, line: Line, steps: Steps, doc: Doc, quiz: Quiz } as Record<string, any>)[s.type];
  return C ? <C s={s} theme={theme} inP={inP} /> : null;
};
