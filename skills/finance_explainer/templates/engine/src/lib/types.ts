// timeline.json 由 scripts/build_timeline.py 生成：所有时间都已换算成秒（相对整片开头）
export type HostState = "full" | "shrink" | "side" | "pip" | "off";
export type Overlay = { type: string; t: number; t2?: number; [k: string]: any };
export type Beat = {
  id: string; kind?: "chapter"; start: number; end: number;
  host: HostState; punch?: number; bg?: string; stage?: any;
  overlays: Overlay[]; cues: Record<string, number>; end_card?: boolean;
  title?: string; subtitle?: string;
};
export type SubLine = { text: string; start: number; end: number };
export type Timeline = {
  meta: { title: string; channel: string; theme: "v1-purple" | "v2-glass"; fps: number; width: number; height: number; duration: number;
          host: { src: string; kind: "video" | "image"; face: [number, number]; offset?: number }; bgm?: string; bgmVolume?: number };
  beats: Beat[]; subs: SubLine[];
  audio: { src: string; start: number; volume?: number }[];
  hostClips: { src: string; start: number; end: number; offset?: number }[];
  sfx: { src: string; t: number; volume?: number }[];
  assets: Record<string, { src: string; kind: "video" | "image"; label?: string }>;
};
