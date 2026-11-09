import React from 'react';
import {Composition, getInputProps, registerRoot} from 'remotion';
import {MotionScene} from './scene';
import {ShotcraftScene} from './ShotcraftScene';
import {LayeredScene} from './LayeredScene';
import type {Props} from './types';
import type {ShotcraftProps} from './lib/shotcraft-types';
import type {LayeredProps} from './lib/layered-types';

const Root: React.FC = () => {
  const raw = getInputProps() as Props | ShotcraftProps | LayeredProps;
  if(raw.plan.version==='ocv-layered-v1'){
    const input=raw as LayeredProps;
    return <Composition id="OCVVibe" component={LayeredScene}
      width={input.plan.canvas[0]} height={input.plan.canvas[1]}
      fps={input.fps} durationInFrames={Math.max(1,Math.round(input.duration*input.fps))}
      defaultProps={input}/>;
  }
  if(raw.plan.version==='ocv-shotcraft-v1'){
    const input=raw as ShotcraftProps;
    return <Composition id="OCVVibe" component={ShotcraftScene}
      width={input.plan.canvas[0]} height={input.plan.canvas[1]}
      fps={input.fps} durationInFrames={Math.max(1,Math.round(input.duration*input.fps))}
      defaultProps={input}/>;
  }
  const input = raw as Props;
  return <Composition id="OCVVibe" component={MotionScene}
    width={input.plan.canvas[0]} height={input.plan.canvas[1]}
    fps={input.fps} durationInFrames={Math.max(1,Math.round(input.duration * input.fps))}
    defaultProps={input}/>;
};

registerRoot(Root);
