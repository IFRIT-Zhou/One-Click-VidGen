import assets from './palette-assets.json';

// Bounded cache: color-picker changes must not retain an unlimited number of SVGs.
const cache = new Map<string,string>();
export const paletteAsset = (colors: Record<string,string>, name: string): string | undefined => {
  const clip = assets.clips[name as keyof typeof assets.clips];
  if (!clip) return undefined;
  const keys = ['page','surface','field','text','muted','accent','border'];
  const values = keys.map(k => /^#[0-9a-f]{6}$/i.test(colors[k]) ? colors[k] : '#ffffff');
  const kraft = colors.id === 'vintage-kraft';
  const key = name + values.join('') + (kraft ? ':kraft' : '');
  if (cache.has(key)) return cache.get(key);
  const style = keys.map((k,i) => `--${k}:${values[i]}`).join(';');
  const paperStyle = kraft ? '<style>.project-card,.paper-card,.metadata{border-radius:3px!important;box-shadow:inset 0 0 24px rgba(92,55,23,.09)}.card-tags span,.chip,.status{border-radius:2px!important}</style>' : '';
  const markup = assets.pages[clip.kind as keyof typeof assets.pages] + paperStyle;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${clip.w*2}" height="${clip.h*2}" viewBox="${clip.x} ${clip.y} ${clip.w} ${clip.h}"><foreignObject x="0" y="0" width="1920" height="5000"><div xmlns="http://www.w3.org/1999/xhtml" style="${style};width:1920px"><style>${clip.empty ? '.project-card{visibility:hidden!important}' : ''}</style>${markup}</div></foreignObject></svg>`;
  // Fixed seed keeps the fine grain attached to the paper as the camera moves.
  const grain = `<defs><filter id="kraft-grain" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".72" numOctaves="3" seed="17"/><feColorMatrix type="saturate" values="0"/></filter></defs><rect x="${clip.x}" y="${clip.y}" width="${clip.w}" height="${clip.h}" filter="url(#kraft-grain)" opacity=".18" style="mix-blend-mode:multiply"/>`;
  const uri = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(kraft ? svg.replace(/<\/svg>$/, grain + '</svg>') : svg)
    .replace(/\(/g, '%28').replace(/\)/g, '%29').replace(/'/g, '%27');
  if (cache.size >= 128) cache.delete(cache.keys().next().value!);
  cache.set(key,uri);
  return uri;
};
