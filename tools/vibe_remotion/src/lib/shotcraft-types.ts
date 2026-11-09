export type Palette=Record<'background'|'ink'|'muted'|'surface'|'line'|'accent',string>;
export type ShotcraftItem={id:string;title:string;body?:string;asset_id?:string;motif:'document'|'book'|'inspection'|'refinement'|'none'};
export type ShotcraftRecipe='spotlight-hero-card'|'word-relay-filmstrip'|'outro-group-photo-launch'|'card-flip-reveal'|'carousel-3d'|'card-stack'|'timeline-travel'|'document-typewriter-reveal'|'marker-underline-title'|'quad-split-parallel-scenes'|'scanline-annotate-focus'|'title-demote-to-label';
export type ShotcraftStage={id:string;recipe:ShotcraftRecipe;start:number;end:number;eyebrow?:string;headline:string;body?:string;footer?:string;items:ShotcraftItem[]};
export type ShotcraftPlan={version:'ocv-shotcraft-v1';canvas:[number,number];duration:number;fps:30;palette:Palette;background:{kind:'solid'|'soft_gradient'|'technical_grid'|'paper'};typography:'sans'|'serif';stages:ShotcraftStage[]};
export type ShotcraftProps={plan:ShotcraftPlan;assets:Record<string,string>;duration:number;fps:30};
