// 两代舞台主题：v1 = 2022-23 客厅 + 紫色几何舞台 + 矩形小窗；v2 = 2025- 深色玻璃 + 圆形头像
export type Theme = {
  name: "v1-purple" | "v2-glass";
  stageBg: string;            // 舞台底色（CSS background）
  card: React.CSSProperties;  // 图表卡片
  cardInk: string; cardMute: string;
  accent: string; accent2: string; hot: string; good: string; bad: string;
  pip: "rect" | "round";
  termBg: string; termInk: string;
  font: string; display: string; brush: string; mono: string;
};

const fonts = {
  font: '"PingFang SC","Hiragino Sans GB","Noto Sans SC",sans-serif',
  display: '"PingFang SC","Hiragino Sans GB",sans-serif',
  brush: '"Kaiti SC","STKaiti","KaiTi",serif',
  mono: '"SF Mono","JetBrains Mono",Menlo,monospace',
};

export const THEMES: Record<string, Theme> = {
  "v1-purple": {
    name: "v1-purple",
    stageBg: "conic-gradient(from 210deg at 68% 42%, #6A35E8, #3A2BB8 25%, #8F44F0 50%, #4E2FD0 75%, #6A35E8)",
    card: { background: "#FFFFFF", borderRadius: 18, boxShadow: "0 18px 50px rgba(20,10,60,.35)" },
    cardInk: "#1C1A33", cardMute: "#7C7898",
    accent: "#5B3FE0", accent2: "#8A6CFF", hot: "#D2453D", good: "#1F9D62", bad: "#D2453D",
    pip: "rect", termBg: "rgba(24,22,48,.92)", termInk: "#FFFFFF", ...fonts,
  },
  "v2-glass": {
    name: "v2-glass",
    stageBg: "radial-gradient(circle at 28% 30%, #1F3E48 0%, #0E1C22 55%, #070D10 100%)",
    card: { background: "linear-gradient(160deg, rgba(255,255,255,.10), rgba(255,255,255,.04))", border: "1.5px solid rgba(255,255,255,.18)",
            borderRadius: 26, boxShadow: "0 30px 80px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.15)", backdropFilter: "blur(18px)" },
    cardInk: "#EEF6F7", cardMute: "#8FA7AD",
    accent: "#3CC8C1", accent2: "#2C7BE5", hot: "#FFB547", good: "#4CD38F", bad: "#FF6B5E",
    pip: "round", termBg: "rgba(10,18,22,.88)", termInk: "#FFFFFF", ...fonts,
  },
};
