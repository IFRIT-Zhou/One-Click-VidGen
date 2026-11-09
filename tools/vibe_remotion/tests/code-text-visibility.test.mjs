import test from 'node:test';
import assert from 'node:assert/strict';
import {textColorAlpha,textPaintVisible} from '../lib/code-text-visibility.mjs';
import {safeStyle} from '../lib/code-render-contract.mjs';

const transparent={color:'rgba(0, 0, 0, 0)',webkitTextFillColor:'rgba(0, 0, 0, 0)',backgroundImage:'none',backgroundClip:'border-box'};
const gradient={...transparent,backgroundClip:'text',backgroundImage:'linear-gradient(180deg, rgb(183, 242, 248) 0%, rgb(95, 224, 239) 55%, rgb(60, 199, 232) 100%)'};

test('ordinary computed text colors retain visibility while alpha-zero remains hidden',()=>{
  assert.equal(textPaintVisible({color:'rgb(255, 255, 255)'}),true);
  assert.equal(textPaintVisible(transparent),false);
  assert.equal(textPaintVisible({color:'rgba(255, 255, 255, 0.01)'}),false);
  assert.equal(textColorAlpha('#1234'),0x44/255);
  assert.equal(textColorAlpha('#12345680'),0x80/255);
  assert.equal(textColorAlpha('rgb(255 255 255 / 50%)'),.5);
});

test('transparent fill is visible only with a correctly clipped, painted CSS gradient',()=>{
  assert.equal(textPaintVisible(gradient),true);
  assert.equal(textPaintVisible({...gradient,backgroundImage:'linear-gradient(180deg,#b7f2f8 0%,#5fe0ef 55%,#3cc7e8 100%)'}),true);
  assert.equal(textPaintVisible({...gradient,backgroundClip:'border-box'}),false);
  assert.equal(textPaintVisible({...gradient,backgroundImage:'none'}),false);
  assert.equal(textPaintVisible({...gradient,backgroundClip:'',webkitBackgroundClip:'text'}),true);
});

test('fully transparent and nearly transparent gradients cannot satisfy visibility',()=>{
  for(const backgroundImage of [
    'linear-gradient(180deg, transparent 0%, transparent 100%)',
    'linear-gradient(#ffffff00, #00ffff00)',
    'radial-gradient(ellipse at center, rgba(255,255,255,0) 0%, rgba(255,255,255,0) 100%)',
    'linear-gradient(rgb(255 255 255 / 1%), rgba(255,255,255,0.02))',
    'linear-gradient(rgba(0,0,0,0) 0%, rgba(0,0,0,0) 100%, rgba(0,0,0,1) 200%)',
    'linear-gradient(#ffffff 0%,transparent 100%)',
  ])assert.equal(textPaintVisible({...gradient,backgroundImage}),false,backgroundImage);
});

test('WebkitTextFillColor is the effective glyph fill, not the fallback color',()=>{
  assert.equal(textPaintVisible({...transparent,color:'rgb(255,255,255)'}),false);
  assert.equal(textPaintVisible({...transparent,webkitTextFillColor:'rgb(20,60,90)'}),true);
  assert.equal(textPaintVisible({...gradient,color:'rgb(255,255,255)'}),true);
  assert.equal(textPaintVisible({color:'rgb(255,255,255)',webkitTextFillColor:'currentcolor'}),true);
});

test('clipped ancestor gradient may paint transparent child spans without changing visibility gates',()=>{
  assert.equal(textPaintVisible(transparent,[gradient]),true);
  assert.equal(textPaintVisible(transparent,[{...gradient,backgroundClip:'border-box'}]),false);
  // This function reports paint only; display, ancestors' opacity, and geometry
  // are independently required by CodeScene.appearance before visible=true.
  assert.equal(textPaintVisible({...transparent,backgroundImage:'none'},[]),false);
});

test('multiple layers remain unverified because a top layer can obscure glyph paint',()=>{
  const backgroundImage='linear-gradient(#ffffff,#00ffff), linear-gradient(transparent,transparent)';
  assert.equal(textPaintVisible({...gradient,backgroundImage,backgroundClip:'text, border-box'}),false);
  assert.equal(textPaintVisible({...gradient,backgroundImage,backgroundClip:'border-box, text'}),false);
});

test('resource loads, CSS escapes and unknown color syntax never create visibility evidence',()=>{
  for(const backgroundImage of [
    'url(https://example.invalid/image.png)',
    'image-set("//example.invalid/x" 1x)',
    'linear-gradient(#fff,#fff),url(file:///private.png)',
    'linear-gradient(var(--color),#fff)',
    'linear-gradient(rgb(from red r g b),#fff)',
    'linear-gradient(color(display-p3 1 0 0),color(display-p3 0 1 0))',
    'linear-gradient(oklab(50% 0.1 0.2),oklab(80% 0.1 0.2))',
    'linear-gradient(u\\72l("//example.invalid/x"),#fff)',
    'linear-gradient(#fff,#fff) trailing-junk',
  ])assert.equal(textPaintVisible({...gradient,backgroundImage}),false,backgroundImage);
  assert.equal(textPaintVisible({...gradient,color:'unrecognized-color',webkitTextFillColor:''}),false);
  assert.throws(()=>safeStyle({backgroundImage:'image-set("//example.invalid/x" 1x)'}),/Unsafe/);
});
