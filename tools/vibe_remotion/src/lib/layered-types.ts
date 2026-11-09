export type Layer = {
  id:string;type:'image'|'text'|'rect'|'ellipse'|'line'|'polyline';role:'background'|'content';
  box:[number,number,number,number];at:number;end:number;enter:number;exit:number;
  motion:'none'|'fade'|'slide_left'|'slide_right'|'slide_up'|'scale'|'grow'|'draw';
  rotation:number;opacity:number;shadow:'none'|'soft';
  asset_id?:string;fit?:'contain'|'cover';edge_fade_right?:number;text?:string;font_size?:number;bold?:boolean;
  align?:'left'|'center'|'right';fill?:string;line_height?:number;letter_spacing?:number;font_family?:'sans'|'serif';
  readability_halo?:{color:string;opacity:number};
  min_font_size?:number;valign?:'top'|'middle'|'bottom';gradient?:{color:string;angle:number};glow?:number;
  stroke?:string;stroke_width?:number;radius?:number;points?:[number,number][];
};
export type LayeredPlan = {version:'ocv-layered-v1';canvas:[number,number];background:string;fade_in:0;fade_out:0;elements:Layer[]};
export type LayeredProps = {plan:LayeredPlan;assets:Record<string,string>;duration:number;fps:number};
