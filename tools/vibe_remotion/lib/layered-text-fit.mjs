// Bounded, deterministic font-only repair. Never rewrite text or move its box.
// Keep compact statistics and their units together. Explicit line breaks and
// prose retain the author's wrapping; this never rewrites their content.
export function isCompactNumericLabel(text){
 return typeof text==='string'&&!/[\r\n]/.test(text)&&text.length<=24&&
  /^[<>≤≥≈±+−-]?\s*\d[\d.,]*\s*(?:[%％]|[a-zA-Zμµ℃°²³/\u4e00-\u9fff]{1,6})?$/.test(text);
}

export function fitLayeredText(preferred,minimum,fits){
  if(fits(preferred))return preferred;
  if(minimum===undefined||!fits(minimum))return null;
  let low=minimum,high=preferred;
  for(let i=0;i<12;i++){
    const mid=(low+high)/2;
    if(fits(mid))low=mid;else high=mid;
  }
 return Math.max(minimum,Math.floor(low*100)/100);
}
