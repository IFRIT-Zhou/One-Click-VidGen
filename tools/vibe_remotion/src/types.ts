export type Item = {
  id: string;
  title: string;
  body?: string;
  value?: string;
  unit?: string;
  note?: string;
  asset_id?: string;
  at?: number;
};

export type Stage = {
  id: string;
  layout: 'findings' | 'comparison' | 'process' | 'evidence';
  start: number;
  end: number;
  headline: string;
  eyebrow?: string;
  footer?: string;
  visual?: {asset_id:string;placement?:'left'|'right';treatment?:'contain'|'cutout';caption?:string};
  items: Item[];
};

export type MotionPlan = {
  version: 'ocv-motion-v1';
  canvas: [number, number];
  theme: 'paper' | 'midnight' | 'clinical';
  title: string;
  subtitle?: string;
  accent?: string;
  palette?: Partial<Record<'background'|'ink'|'muted'|'surface'|'line'|'accent',string>>;
  background?: {kind:'solid'|'soft_gradient'|'technical_grid'|'paper'};
  stages: Stage[];
};

export type Props = {plan: MotionPlan; assets: Record<string, string>; duration: number; fps: number};
